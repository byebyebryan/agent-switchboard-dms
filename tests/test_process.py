import io
import json
import os
from pathlib import Path
import stat
import tempfile
import textwrap
import time
import unittest
from unittest import mock

import switchboard_dms.bridge as bridge_module
import switchboard_dms.process as process_module


class _FaultySelectSelector:
    def __init__(self, delegate):
        self.delegate = delegate

    def register(self, *args, **kwargs):
        return self.delegate.register(*args, **kwargs)

    def unregister(self, *args, **kwargs):
        return self.delegate.unregister(*args, **kwargs)

    def get_map(self):
        return self.delegate.get_map()

    def select(self, _timeout=None):
        raise OSError("injected selector.select failure")

    def close(self):
        return self.delegate.close()


class _FaultyCloseSelector(_FaultySelectSelector):
    def select(self, timeout=None):
        return self.delegate.select(timeout)

    def close(self):
        self.delegate.close()
        raise OSError("injected selector.close failure with private detail")


class _BinaryStdout:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()


class ProcessLifecycleFaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def executable(self, body: str) -> Path:
        path = self.temp / "faulty-swbctl"
        path.write_text(
            "#!/usr/bin/env python3\n" + textwrap.dedent(body),
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def assert_fault_reaps(self, executable: Path, fault_patch) -> None:
        captured = []
        original_spawn = process_module._spawn

        def capture_spawn(argv):
            process = original_spawn(argv)
            captured.append(process)
            return process

        with (
            mock.patch.object(process_module, "_spawn", side_effect=capture_spawn),
            fault_patch,
        ):
            with self.assertRaisesRegex(OSError, "injected"):
                process_module.run_process([str(executable)], timeout_ms=2_000)

        self.assertEqual(len(captured), 1)
        process = captured[0]
        self.assertIsNotNone(process.returncode)
        self.assertIsNotNone(process.poll())
        self.assertFalse(Path(f"/proc/{process.pid}").exists())

    def test_normal_completion_does_not_invoke_the_kill_guard(self) -> None:
        executable = self.executable(
            """
            import os
            os.write(1, b"complete")
            """
        )
        with mock.patch.object(
            process_module,
            "_kill_process_group",
            side_effect=AssertionError("normal completion invoked kill guard"),
        ) as kill_group:
            result = process_module.run_process([str(executable)], timeout_ms=2_000)

        self.assertEqual(result.stdout, b"complete")
        self.assertEqual(result.exit_code, 0)
        kill_group.assert_not_called()

    def test_default_selector_constructor_failure_reaps_child(self) -> None:
        executable = self.executable(
            """
            import time
            time.sleep(30)
            """
        )
        fault = mock.patch.object(
            process_module.selectors,
            "DefaultSelector",
            side_effect=OSError("injected DefaultSelector failure"),
        )

        self.assert_fault_reaps(executable, fault)

    def test_selector_select_failure_reaps_child(self) -> None:
        executable = self.executable(
            """
            import time
            time.sleep(30)
            """
        )
        original_selector = process_module.selectors.DefaultSelector

        def faulty_selector():
            return _FaultySelectSelector(original_selector())

        fault = mock.patch.object(
            process_module.selectors,
            "DefaultSelector",
            side_effect=faulty_selector,
        )

        self.assert_fault_reaps(executable, fault)

    def test_os_read_failure_reaps_child(self) -> None:
        executable = self.executable(
            """
            import os
            import time
            os.write(1, b"ready")
            time.sleep(30)
            """
        )
        captured = []
        original_spawn = process_module._spawn
        original_read = os.read

        def capture_spawn(argv):
            process = original_spawn(argv)
            captured.append(process)
            return process

        def injected_read(file_descriptor, size):
            if (
                captured
                and captured[0].stdout is not None
                and file_descriptor == captured[0].stdout.fileno()
            ):
                raise OSError("injected os.read failure")
            return original_read(file_descriptor, size)

        with (
            mock.patch.object(process_module, "_spawn", side_effect=capture_spawn),
            mock.patch.object(process_module.os, "read", side_effect=injected_read),
        ):
            with self.assertRaisesRegex(OSError, "injected"):
                process_module.run_process([str(executable)], timeout_ms=2_000)

        self.assertEqual(len(captured), 1)
        process = captured[0]
        self.assertIsNotNone(process.returncode)
        self.assertIsNotNone(process.poll())
        self.assertFalse(Path(f"/proc/{process.pid}").exists())

    def test_cleanup_failure_reaps_descendant_and_is_a_managed_error(self) -> None:
        child_pid_path = self.temp / "cleanup-child-pid"
        executable = self.executable(
            """
            import os
            from pathlib import Path
            import time

            child_pid = os.fork()
            if child_pid == 0:
                os.close(1)
                os.close(2)
                time.sleep(30)
                os._exit(0)
            Path(os.environ["FAULT_CHILD_PID"]).write_text(
                str(child_pid), encoding="ascii"
            )
            os.write(1, b"nominal parent completion")
            """
        )
        original_selector = process_module.selectors.DefaultSelector

        def faulty_selector():
            return _FaultyCloseSelector(original_selector())

        stdout = _BinaryStdout()
        stderr = io.StringIO()
        with (
            mock.patch.dict(
                process_module.os.environ,
                {"FAULT_CHILD_PID": str(child_pid_path)},
            ),
            mock.patch.object(
                process_module.selectors,
                "DefaultSelector",
                side_effect=faulty_selector,
            ),
            mock.patch.object(bridge_module.sys, "stdout", stdout),
            mock.patch.object(bridge_module.sys, "stderr", stderr),
        ):
            exit_code = bridge_module.main(["--swbctl", str(executable)])

        child_pid = int(child_pid_path.read_text(encoding="ascii"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self._process_is_running(child_pid):
            time.sleep(0.02)

        output = stdout.buffer.getvalue()
        payload = json.loads(output)
        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(payload["error"]["code"], "bridge_internal_error")
        self.assertNotIn("private detail", output.decode("utf-8"))
        self.assertFalse(self._process_is_running(child_pid))

    def assert_primary_process_error_survives_cleanup_fault(
        self,
        body: str,
        *,
        arguments: list[str],
        expected_code: str,
    ) -> None:
        child_pid_path = self.temp / f"{expected_code}-child-pid"
        executable = self.executable(body)
        original_selector = process_module.selectors.DefaultSelector

        def faulty_selector():
            return _FaultyCloseSelector(original_selector())

        stdout = _BinaryStdout()
        stderr = io.StringIO()
        with (
            mock.patch.dict(
                process_module.os.environ,
                {"FAULT_CHILD_PID": str(child_pid_path)},
            ),
            mock.patch.object(
                process_module.selectors,
                "DefaultSelector",
                side_effect=faulty_selector,
            ),
            mock.patch.object(bridge_module.sys, "stdout", stdout),
            mock.patch.object(bridge_module.sys, "stderr", stderr),
        ):
            exit_code = bridge_module.main(["--swbctl", str(executable), *arguments])

        child_pid = int(child_pid_path.read_text(encoding="ascii"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self._process_is_running(child_pid):
            time.sleep(0.02)

        output = stdout.buffer.getvalue()
        payload = json.loads(output)
        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(payload["error"]["code"], expected_code)
        self.assertNotIn("private detail", output.decode("utf-8"))
        self.assertFalse(self._process_is_running(child_pid))

    def test_timeout_classification_survives_cleanup_fault(self) -> None:
        self.assert_primary_process_error_survives_cleanup_fault(
            """
            import os
            from pathlib import Path
            import time

            child_pid = os.fork()
            if child_pid == 0:
                time.sleep(30)
                os._exit(0)
            Path(os.environ["FAULT_CHILD_PID"]).write_text(
                str(child_pid), encoding="ascii"
            )
            time.sleep(30)
            """,
            arguments=["--timeout-ms", "500"],
            expected_code="process_timeout",
        )

    def test_overflow_classification_survives_cleanup_fault(self) -> None:
        self.assert_primary_process_error_survives_cleanup_fault(
            """
            import os
            from pathlib import Path
            import time

            child_pid = os.fork()
            if child_pid == 0:
                time.sleep(30)
                os._exit(0)
            Path(os.environ["FAULT_CHILD_PID"]).write_text(
                str(child_pid), encoding="ascii"
            )
            os.write(1, b"x" * (8 * 1024 * 1024 + 2))
            """,
            arguments=[],
            expected_code="stdout_overflow",
        )

    @staticmethod
    def _process_is_running(pid: int) -> bool:
        try:
            fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
        except FileNotFoundError:
            return False
        return len(fields) > 2 and fields[2] != "Z"

    def test_unmanaged_process_fault_is_a_private_internal_bridge_error(self) -> None:
        stdout = _BinaryStdout()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                bridge_module,
                "run_process",
                side_effect=OSError("injected private process detail"),
            ),
            mock.patch.object(bridge_module.sys, "stdout", stdout),
            mock.patch.object(bridge_module.sys, "stderr", stderr),
        ):
            exit_code = bridge_module.main(["--swbctl", "fake-swbctl"])

        output = stdout.buffer.getvalue()
        payload = json.loads(output)
        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(output.count(b"\n"), 1)
        self.assertEqual(payload["error"]["code"], "bridge_internal_error")
        self.assertNotIn("private process detail", output.decode("utf-8"))

    def test_unmanaged_serialization_fault_uses_hardcoded_fallback(self) -> None:
        stdout = _BinaryStdout()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                bridge_module,
                "serialize_response",
                side_effect=RuntimeError("injected private serialization detail"),
            ),
            mock.patch.object(bridge_module.sys, "stdout", stdout),
            mock.patch.object(bridge_module.sys, "stderr", stderr),
        ):
            exit_code = bridge_module.main(["--swbctl", "missing-swbctl"])

        output = stdout.buffer.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(output, bridge_module._INTERNAL_ERROR_PAYLOAD)
        self.assertNotIn("private serialization detail", output.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
