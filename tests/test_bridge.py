import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest

from switchboard_dms.bridge import (
    MAX_BRIDGE_BYTES,
    prepare_open_argv,
    serialize_response,
)


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "switchboard-bridge"
FIXTURE = ROOT / "tests" / "fixtures" / "snapshot-v1.json"
PLAN_FIXTURE = ROOT / "tests" / "fixtures" / "presentation-plan-v1.json"
SESSION_KEY = (
    "11111111-1111-4111-8111-111111111111:codex:22222222-2222-4222-8222-222222222222"
)
REQUEST_ID = "44444444-4444-4444-8444-444444444444"
SURFACE_ID = "33333333-3333-4333-8333-333333333333"


class BridgeCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def executable(self, body: str, *, name: str = "fake-swbctl") -> Path:
        path = self.temp / name
        path.write_text(
            "#!/usr/bin/env python3\n" + textwrap.dedent(body),
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def run_bridge(
        self,
        executable: Path | str,
        *arguments: str,
        environment: dict[str, str] | None = None,
        timeout: float = 5,
        cwd: Path = ROOT,
        bridge: Path = BRIDGE,
    ) -> subprocess.CompletedProcess[bytes]:
        process_environment = os.environ.copy()
        if environment:
            process_environment.update(environment)
        return subprocess.run(
            [str(bridge), "--swbctl", str(executable), *arguments],
            cwd=cwd,
            env=process_environment,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def payload(self, result: subprocess.CompletedProcess[bytes]) -> dict[str, object]:
        self.assertEqual(result.stderr, b"")
        self.assertTrue(result.stdout.endswith(b"\n"))
        self.assertEqual(result.stdout.count(b"\n"), 1)
        payload = json.loads(result.stdout)
        expected = (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        self.assertEqual(result.stdout, expected)
        self.assertLessEqual(len(result.stdout), MAX_BRIDGE_BYTES)
        return payload

    def fixture_executable(self, *, name: str = "fake-swbctl") -> Path:
        return self.executable(
            """
            import os
            from pathlib import Path
            import sys

            arguments_path = os.environ.get("FAKE_ARGUMENTS")
            if arguments_path:
                Path(arguments_path).write_text(
                    "\\n".join(sys.argv[1:]), encoding="utf-8"
                )
            sys.stdout.buffer.write(Path(os.environ["FAKE_SNAPSHOT"]).read_bytes())
            """,
            name=name,
        )

    def test_retained_argv_is_exact_and_never_uses_a_shell(self) -> None:
        arguments = self.temp / "arguments"
        marker = self.temp / "shell-was-used"
        executable = self.fixture_executable(name="fake swbctl; touch shell-was-used")

        result = self.run_bridge(
            executable,
            environment={
                "FAKE_ARGUMENTS": str(arguments),
                "FAKE_SNAPSHOT": str(FIXTURE),
            },
        )

        payload = self.payload(result)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(arguments.read_text(encoding="utf-8"), "snapshot\n--json")
        self.assertFalse(marker.exists())

    def test_refresh_argv_is_exact(self) -> None:
        arguments = self.temp / "arguments"
        executable = self.fixture_executable()

        result = self.run_bridge(
            executable,
            "--refresh",
            environment={
                "FAKE_ARGUMENTS": str(arguments),
                "FAKE_SNAPSHOT": str(FIXTURE),
            },
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            arguments.read_text(encoding="utf-8"),
            "snapshot\n--reconcile\nfull\n--json",
        )

    def test_prepare_open_argv_and_plan_envelope_are_exact(self) -> None:
        arguments = self.temp / "arguments"
        executable = self.fixture_executable()

        result = self.run_bridge(
            executable,
            "--prepare-open",
            SESSION_KEY,
            "--request-id",
            REQUEST_ID,
            environment={
                "FAKE_ARGUMENTS": str(arguments),
                "FAKE_SNAPSHOT": str(PLAN_FIXTURE),
            },
        )

        payload = self.payload(result)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(set(payload), {"bridgeVersion", "ok", "plan"})
        self.assertEqual(payload["plan"]["kind"], "switch")
        self.assertEqual(
            arguments.read_text(encoding="utf-8"),
            "\n".join(
                (
                    "prepare-open",
                    SESSION_KEY,
                    "--request-id",
                    REQUEST_ID,
                    "--can-focus-desktop",
                    "--can-launch-terminal",
                    "--json",
                )
            ),
        )

    def test_prepare_open_can_suppress_focus_without_suppressing_launch(self) -> None:
        self.assertEqual(
            prepare_open_argv(
                "swbctl",
                session_key=SESSION_KEY,
                request_id=REQUEST_ID,
                can_focus_desktop=False,
                can_launch_terminal=True,
            ),
            [
                "swbctl",
                "prepare-open",
                SESSION_KEY,
                "--request-id",
                REQUEST_ID,
                "--can-launch-terminal",
                "--json",
            ],
        )

    def test_select_surface_argv_is_exact_and_output_free(self) -> None:
        arguments = self.temp / "arguments"
        executable = self.executable(
            """
            import os
            from pathlib import Path
            import sys
            Path(os.environ["FAKE_ARGUMENTS"]).write_text(
                "\\n".join(sys.argv[1:]), encoding="utf-8"
            )
            """
        )

        result = self.run_bridge(
            executable,
            "--select-surface",
            SURFACE_ID,
            "--tmux-client",
            "/dev/pts/7",
            environment={"FAKE_ARGUMENTS": str(arguments)},
        )

        payload = self.payload(result)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            payload["action"], {"kind": "selected", "surfaceId": SURFACE_ID}
        )
        self.assertEqual(
            arguments.read_text(encoding="utf-8"),
            "\n".join(
                (
                    "select-surface",
                    SURFACE_ID,
                    "--client",
                    "/dev/pts/7",
                )
            ),
        )

    def test_prepare_rejects_an_invalid_plan_without_exposing_it(self) -> None:
        output = self.temp / "invalid-plan"
        output.write_text('{"promptText":"private"}\n', encoding="utf-8")
        executable = self.fixture_executable()

        result = self.run_bridge(
            executable,
            "--prepare-open",
            SESSION_KEY,
            "--request-id",
            REQUEST_ID,
            environment={"FAKE_SNAPSHOT": str(output)},
        )

        payload = self.assert_error(result, "plan_invalid_protocol", retryable=False)
        self.assertNotIn("private", json.dumps(payload))

    def test_fixture_success_has_the_exact_envelope(self) -> None:
        result = self.run_bridge(
            self.fixture_executable(),
            environment={"FAKE_SNAPSHOT": str(FIXTURE)},
        )

        payload = self.payload(result)
        self.assertEqual(set(payload), {"bridgeVersion", "ok", "model"})
        self.assertEqual(payload["bridgeVersion"], 1)
        self.assertIs(payload["ok"], True)
        model = payload["model"]
        self.assertEqual(model["modelVersion"], 1)
        self.assertEqual(len(model["sessions"]), 1)

    def test_entry_point_is_0755_and_works_outside_repo_via_path_or_symlink(
        self,
    ) -> None:
        self.assertEqual(stat.S_IMODE(BRIDGE.stat().st_mode), 0o755)
        outside = self.temp / "outside"
        outside.mkdir()
        executable = self.fixture_executable()
        environment = {"FAKE_SNAPSHOT": str(FIXTURE)}

        absolute = self.run_bridge(
            executable,
            environment=environment,
            cwd=outside,
            bridge=BRIDGE,
        )
        self.assertEqual(absolute.returncode, 0)
        self.assertIs(self.payload(absolute)["ok"], True)

        linked_bridge = self.temp / "linked-switchboard-bridge"
        linked_bridge.symlink_to(BRIDGE)
        linked = self.run_bridge(
            executable,
            environment=environment,
            cwd=outside,
            bridge=linked_bridge,
        )
        self.assertEqual(linked.returncode, 0)
        self.assertIs(self.payload(linked)["ok"], True)

    def test_neutral_and_degraded_snapshots_remain_successes(self) -> None:
        original = json.loads(FIXTURE.read_text(encoding="utf-8"))
        neutral = dict(original)
        neutral["capabilities"] = []
        degraded = json.loads(FIXTURE.read_text(encoding="utf-8"))
        degraded["capabilities"][0]["available"] = False
        degraded["capabilities"][0]["degradedReasons"] = [
            {
                "code": "provider_not_found",
                "message": "Codex is unavailable.",
                "retryable": True,
                "feature": "app_server_thread_list",
            }
        ]
        degraded["errors"] = [
            {
                "code": "provider_not_found",
                "message": "Codex is unavailable.",
                "scope": "provider",
                "hostId": degraded["host"]["hostId"],
                "provider": "codex",
                "retryable": True,
                "observedAt": degraded["generatedAt"],
            }
        ]
        executable = self.fixture_executable()

        for name, snapshot, status in (
            ("neutral", neutral, "neutral"),
            ("degraded", degraded, "degraded"),
        ):
            with self.subTest(name=name):
                path = self.temp / f"{name}.json"
                path.write_text(json.dumps(snapshot), encoding="utf-8")
                result = self.run_bridge(
                    executable, environment={"FAKE_SNAPSHOT": str(path)}
                )
                payload = self.payload(result)
                self.assertEqual(result.returncode, 0)
                self.assertIs(payload["ok"], True)
                self.assertEqual(payload["model"]["codexCapability"]["status"], status)

    def assert_error(
        self,
        result: subprocess.CompletedProcess[bytes],
        code: str,
        *,
        retryable: bool,
    ) -> dict[str, object]:
        payload = self.payload(result)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(set(payload), {"bridgeVersion", "ok", "error"})
        self.assertEqual(payload["bridgeVersion"], 1)
        self.assertIs(payload["ok"], False)
        error = payload["error"]
        self.assertEqual(set(error), {"code", "message", "retryable"})
        self.assertEqual(error["code"], code)
        self.assertIs(error["retryable"], retryable)
        self.assertIsInstance(error["message"], str)
        self.assertLessEqual(len(error["message"]), 160)
        return payload

    def test_missing_permission_denied_and_exec_format_failures(self) -> None:
        missing = self.run_bridge(self.temp / "missing")
        self.assert_error(missing, "executable_not_found", retryable=False)

        denied_path = self.temp / "denied"
        denied_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        denied_path.chmod(0o600)
        denied = self.run_bridge(denied_path)
        self.assert_error(denied, "executable_permission_denied", retryable=False)

        invalid_path = self.temp / "invalid-executable"
        invalid_path.write_text("not an executable format\n", encoding="utf-8")
        invalid_path.chmod(0o700)
        invalid = self.run_bridge(invalid_path)
        self.assert_error(invalid, "executable_start_failed", retryable=True)

    def test_nonzero_exit_does_not_expose_stderr(self) -> None:
        executable = self.executable(
            """
            import sys
            sys.stderr.write("private diagnostic that must not cross the bridge\\n")
            raise SystemExit(7)
            """
        )

        result = self.run_bridge(executable)

        payload = self.assert_error(result, "swbctl_nonzero_exit", retryable=True)
        self.assertNotIn("private diagnostic", result.stdout.decode("utf-8"))
        self.assertEqual(payload["error"]["message"], "swbctl exited with status 7.")

    def test_timeout_kills_the_child_process_group(self) -> None:
        child_pid = self.temp / "child-pid"
        executable = self.executable(
            """
            import os
            from pathlib import Path
            import subprocess
            import sys
            import time

            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"]
            )
            Path(os.environ["FAKE_CHILD_PID"]).write_text(
                str(child.pid), encoding="ascii"
            )
            time.sleep(30)
            """
        )

        result = self.run_bridge(
            executable,
            "--timeout-ms",
            "500",
            environment={"FAKE_CHILD_PID": str(child_pid)},
        )

        self.assert_error(result, "process_timeout", retryable=True)
        pid = int(child_pid.read_text(encoding="ascii"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self._process_is_running(pid):
            time.sleep(0.02)
        self.assertFalse(self._process_is_running(pid))

    def test_stdout_overflow_kills_descendants_in_the_process_group(self) -> None:
        child_pid = self.temp / "overflow-child-pid"
        executable = self.executable(
            """
            import os
            from pathlib import Path
            import subprocess
            import sys

            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"]
            )
            Path(os.environ["FAKE_CHILD_PID"]).write_text(
                str(child.pid), encoding="ascii"
            )
            os.write(1, b"x" * (8 * 1024 * 1024 + 2))
            """
        )

        result = self.run_bridge(
            executable,
            environment={"FAKE_CHILD_PID": str(child_pid)},
        )

        self.assert_error(result, "stdout_overflow", retryable=False)
        pid = int(child_pid.read_text(encoding="ascii"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self._process_is_running(pid):
            time.sleep(0.02)
        self.assertFalse(self._process_is_running(pid))

    @staticmethod
    def _process_is_running(pid: int) -> bool:
        stat_path = Path(f"/proc/{pid}/stat")
        try:
            fields = stat_path.read_text(encoding="ascii").split()
        except FileNotFoundError:
            return False
        return len(fields) > 2 and fields[2] != "Z"

    def test_stdout_and_stderr_overflow_are_bounded_without_deadlock(self) -> None:
        stdout_executable = self.executable(
            """
            import os
            os.write(1, b"x" * (8 * 1024 * 1024 + 2))
            """,
            name="stdout-overflow",
        )
        stderr_executable = self.executable(
            """
            import os
            os.write(2, b"x" * (64 * 1024 + 1))
            """,
            name="stderr-overflow",
        )

        started = time.monotonic()
        stdout_result = self.run_bridge(stdout_executable)
        stderr_result = self.run_bridge(stderr_executable)

        self.assert_error(stdout_result, "stdout_overflow", retryable=False)
        self.assert_error(stderr_result, "stderr_overflow", retryable=False)
        self.assertLess(time.monotonic() - started, 4)

    def test_stderr_at_the_limit_does_not_change_a_valid_success(self) -> None:
        executable = self.executable(
            """
            import os
            from pathlib import Path
            import sys

            os.write(2, b"x" * (64 * 1024))
            sys.stdout.buffer.write(Path(os.environ["FAKE_SNAPSHOT"]).read_bytes())
            """
        )

        result = self.run_bridge(
            executable, environment={"FAKE_SNAPSHOT": str(FIXTURE)}
        )

        self.assertEqual(result.returncode, 0)
        self.assertIs(self.payload(result)["ok"], True)

    def test_invalid_utf8_json_framing_and_protocol_are_distinct(self) -> None:
        output = self.temp / "output"
        executable = self.executable(
            """
            import os
            from pathlib import Path
            import sys
            sys.stdout.buffer.write(Path(os.environ["FAKE_OUTPUT"]).read_bytes())
            """
        )
        cases = (
            ("utf8", b"\xff\n", "snapshot_invalid_utf8"),
            ("json", b"{\n", "snapshot_invalid_json"),
            ("framing", b"{}\n\n", "snapshot_invalid_json"),
            ("protocol", b"{}\n", "snapshot_invalid_protocol"),
        )

        for name, raw, code in cases:
            with self.subTest(name=name):
                output.write_bytes(raw)
                result = self.run_bridge(
                    executable, environment={"FAKE_OUTPUT": str(output)}
                )
                self.assert_error(result, code, retryable=False)

    def test_snapshot_framing_rejects_all_outer_json_whitespace(self) -> None:
        output = self.temp / "output"
        executable = self.executable(
            """
            import os
            from pathlib import Path
            import sys
            sys.stdout.buffer.write(Path(os.environ["FAKE_OUTPUT"]).read_bytes())
            """
        )
        compact = FIXTURE.read_bytes().removesuffix(b"\n")
        cases = (
            ("leading-space", b" " + compact),
            ("leading-tab", b"\t" + compact),
            ("trailing-space", compact + b" "),
            ("trailing-tab", compact + b"\t"),
            ("lf-then-space", compact + b"\n "),
            ("lf-then-tab", compact + b"\n\t"),
            ("multiple-lf", compact + b"\n\n"),
            ("crlf", compact + b"\r\n"),
        )

        for name, raw in cases:
            with self.subTest(name=name):
                output.write_bytes(raw)
                result = self.run_bridge(
                    executable, environment={"FAKE_OUTPUT": str(output)}
                )
                self.assert_error(result, "snapshot_invalid_json", retryable=False)

        for name, raw in (
            ("no-final-lf", compact),
            ("one-final-lf", compact + b"\n"),
        ):
            with self.subTest(name=name):
                output.write_bytes(raw)
                result = self.run_bridge(
                    executable, environment={"FAKE_OUTPUT": str(output)}
                )
                self.assertEqual(result.returncode, 0)
                self.assertIs(self.payload(result)["ok"], True)

    def test_argument_bounds_remain_argparse_errors(self) -> None:
        for arguments in (
            ("--timeout-ms", "99"),
            ("--timeout-ms", "60001"),
            ("--max-sessions", "0"),
            ("--max-sessions", "1001"),
            ("--swbctl", ""),
        ):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [str(BRIDGE), *arguments],
                    cwd=ROOT,
                    capture_output=True,
                    timeout=2,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertNotEqual(result.stderr, b"")

    def test_broken_stdout_after_valid_arguments_is_silent_and_bounded(self) -> None:
        executable = self.executable(
            """
            from pathlib import Path
            import os
            import sys
            import time

            time.sleep(0.1)
            sys.stdout.buffer.write(Path(os.environ["FAKE_SNAPSHOT"]).read_bytes())
            """
        )
        environment = os.environ.copy()
        environment["FAKE_SNAPSHOT"] = str(FIXTURE)
        process = subprocess.Popen(
            [str(BRIDGE), "--swbctl", str(executable)],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertIsNotNone(process.stdout)
        self.assertIsNotNone(process.stderr)
        process.stdout.close()
        stderr = process.stderr.read()
        process.stderr.close()
        return_code = process.wait(timeout=5)

        self.assertEqual(return_code, 1)
        self.assertEqual(stderr, b"")


class BridgeSerializationTests(unittest.TestCase):
    def test_bridge_output_overflow_becomes_a_small_stable_failure(self) -> None:
        response = {
            "bridgeVersion": 1,
            "ok": True,
            "model": {"value": "x" * MAX_BRIDGE_BYTES},
        }

        exit_code, output = serialize_response(response)
        payload = json.loads(output)

        self.assertEqual(exit_code, 1)
        self.assertLessEqual(len(output), MAX_BRIDGE_BYTES)
        self.assertEqual(payload["error"]["code"], "bridge_output_overflow")

    def test_serialization_failure_is_managed(self) -> None:
        response = {"bridgeVersion": 1, "ok": True, "model": {"bad": object()}}

        exit_code, output = serialize_response(response)
        payload = json.loads(output)

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["error"]["code"], "bridge_serialization_failed")
        self.assertEqual(output.count(b"\n"), 1)


class RuntimeBoundaryTests(unittest.TestCase):
    def test_runtime_imports_no_core_or_private_storage_modules(self) -> None:
        runtime_files = sorted((ROOT / "switchboard_dms").rglob("*.py"))
        runtime_files.append(BRIDGE)
        forbidden_imports = (
            "agent_switchboard",
            "sqlite3",
            "switchboard.core",
            "switchboard.providers",
            "switchboard.storage",
        )
        for path in runtime_files:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                for module in forbidden_imports:
                    self.assertNotIn(f"import {module}", text)
                    self.assertNotIn(f"from {module}", text)

    def test_bridge_delegates_actions_without_provider_or_desktop_tools(self) -> None:
        text = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8").casefold()
            for relative in (
                "switchboard-bridge",
                "switchboard_dms/bridge.py",
                "switchboard_dms/process.py",
            )
        )
        for forbidden in (
            "registry.sqlite",
            "ssh",
            "niri",
            "ghostty",
            "codex app-server",
            "claude",
            "shell=true",
            "shlex",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
