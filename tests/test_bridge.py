import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import textwrap
import unittest

from switchboard_dms.bridge import (
    MAX_BRIDGE_BYTES,
    prepare_history_argv,
    prepare_open_argv,
    prepare_task_argv,
    serialize_response,
    snapshot_argv,
    stop_session_argv,
)


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "switchboard-bridge"
SNAPSHOT = ROOT / "tests" / "fixtures" / "snapshot-v2.json"
V1 = ROOT / "tests" / "fixtures" / "snapshot-v1-mixed.json"
PLAN = ROOT / "tests" / "fixtures" / "presentation-plan-v2.json"
HOST_ID = "11111111-1111-4111-8111-111111111111"
PROJECT_ID = "22222222-2222-4222-8222-222222222222"
CHECKOUT_ID = "44444444-4444-4444-8444-444444444444"
TASK_ID = "88888888-8888-4888-8888-888888888888"
REQUEST_ID = "77777777-7777-4777-8777-777777777777"
SESSION_KEY = f"{HOST_ID}:codex:55555555-5555-4555-8555-555555555555"
CLAUDE_KEY = f"{HOST_ID}:claude:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SURFACE_ID = "33333333-3333-4333-8333-333333333333"


class BridgeArgumentTests(unittest.TestCase):
    def test_snapshot_argv_is_exact(self) -> None:
        self.assertEqual(
            snapshot_argv("swbctl", refresh=False), ["swbctl", "snapshot", "--json"]
        )
        self.assertEqual(
            snapshot_argv("swbctl", refresh=True),
            ["swbctl", "snapshot", "--reconcile", "full", "--json"],
        )

    def test_prepare_open_argv_is_exact(self) -> None:
        self.assertEqual(
            prepare_open_argv("swbctl", session_key=SESSION_KEY, request_id=REQUEST_ID),
            [
                "swbctl",
                "prepare-open",
                SESSION_KEY,
                "--request-id",
                REQUEST_ID,
                "--can-focus-desktop",
                "--can-launch-terminal",
                "--json",
            ],
        )

    def test_prepare_existing_and_create_task_argv_are_exact(self) -> None:
        self.assertEqual(
            prepare_task_argv("swbctl", task_id=TASK_ID, request_id=REQUEST_ID),
            [
                "swbctl",
                "prepare-task",
                TASK_ID,
                "--request-id",
                REQUEST_ID,
                "--can-focus-desktop",
                "--can-launch-terminal",
                "--json",
            ],
        )
        self.assertEqual(
            prepare_task_argv(
                "swbctl",
                task_id=TASK_ID,
                create=True,
                project_id=PROJECT_ID,
                title="Fix picker layout",
                checkout_id=CHECKOUT_ID,
                provider="claude",
                request_id=REQUEST_ID,
            ),
            [
                "swbctl",
                "prepare-task",
                TASK_ID,
                "--create",
                "--project",
                PROJECT_ID,
                "--title",
                "Fix picker layout",
                "--checkout",
                CHECKOUT_ID,
                "--provider",
                "claude",
                "--request-id",
                REQUEST_ID,
                "--can-focus-desktop",
                "--can-launch-terminal",
                "--json",
            ],
        )

    def test_prepare_history_and_stop_argv_are_exact(self) -> None:
        self.assertEqual(
            prepare_history_argv(
                "swbctl",
                project_id=PROJECT_ID,
                checkout_id=CHECKOUT_ID,
                request_id=REQUEST_ID,
            ),
            [
                "swbctl",
                "prepare-history",
                "--project",
                PROJECT_ID,
                "--checkout",
                CHECKOUT_ID,
                "--request-id",
                REQUEST_ID,
                "--can-focus-desktop",
                "--can-launch-terminal",
                "--json",
            ],
        )
        self.assertEqual(
            stop_session_argv("swbctl", session_key=CLAUDE_KEY),
            ["swbctl", "stop-session", CLAUDE_KEY, "--json"],
        )


class BridgeCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def executable(self, body: str, *, name: str = "fake-swbctl") -> Path:
        path = self.temp / name
        path.write_text(
            "#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8"
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def fixture_executable(self, *, name: str = "fake-swbctl") -> Path:
        return self.executable(
            """
            import os
            from pathlib import Path
            import sys
            if os.environ.get("FAKE_ARGUMENTS"):
                Path(os.environ["FAKE_ARGUMENTS"]).write_text("\\n".join(sys.argv[1:]), encoding="utf-8")
            sys.stdout.buffer.write(Path(os.environ["FAKE_OUTPUT"]).read_bytes())
            """,
            name=name,
        )

    def run_bridge(
        self,
        executable: Path | str,
        *arguments: str,
        output: Path = SNAPSHOT,
        timeout: float = 5,
        extra: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        environment = os.environ.copy()
        environment["FAKE_OUTPUT"] = str(output)
        if extra:
            environment.update(extra)
        return subprocess.run(
            [str(BRIDGE), "--swbctl", str(executable), *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def payload(self, result: subprocess.CompletedProcess[bytes]) -> dict[str, object]:
        self.assertEqual(result.stderr, b"")
        self.assertTrue(result.stdout.endswith(b"\n"))
        self.assertEqual(result.stdout.count(b"\n"), 1)
        value = json.loads(result.stdout)
        canonical = (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            + b"\n"
        )
        self.assertEqual(result.stdout, canonical)
        self.assertLessEqual(len(result.stdout), MAX_BRIDGE_BYTES)
        return value

    def assert_error(
        self, result: subprocess.CompletedProcess[bytes], code: str
    ) -> dict[str, object]:
        value = self.payload(result)
        self.assertEqual(result.returncode, 1)
        self.assertIs(value["ok"], False)
        self.assertEqual(value["error"]["code"], code)
        return value

    def test_snapshot_success_is_model_v3_and_shell_free(self) -> None:
        arguments = self.temp / "arguments"
        marker = self.temp / "shell-used"
        executable = self.fixture_executable(name="fake swbctl; touch shell-used")
        result = self.run_bridge(executable, extra={"FAKE_ARGUMENTS": str(arguments)})
        value = self.payload(result)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(value["bridgeVersion"], 2)
        self.assertEqual(value["model"]["modelVersion"], 3)
        self.assertEqual(len(value["model"]["tasks"]), 2)
        self.assertEqual(len(value["model"]["inboxSessions"]), 1)
        self.assertEqual(arguments.read_text(encoding="utf-8"), "snapshot\n--json")
        self.assertFalse(marker.exists())

    def test_refresh_and_create_task_cli_forward_exact_argv(self) -> None:
        arguments = self.temp / "arguments"
        executable = self.fixture_executable()
        refreshed = self.run_bridge(
            executable, "--refresh", extra={"FAKE_ARGUMENTS": str(arguments)}
        )
        self.assertEqual(refreshed.returncode, 0)
        self.assertEqual(
            arguments.read_text(encoding="utf-8"), "snapshot\n--reconcile\nfull\n--json"
        )
        created = self.run_bridge(
            executable,
            "--prepare-task",
            TASK_ID,
            "--create-task",
            "--project",
            PROJECT_ID,
            "--title",
            "Fix picker layout",
            "--checkout",
            CHECKOUT_ID,
            "--provider",
            "claude",
            "--request-id",
            REQUEST_ID,
            output=PLAN,
            extra={"FAKE_ARGUMENTS": str(arguments)},
        )
        value = self.payload(created)
        self.assertEqual(value["plan"]["kind"], "switch")
        self.assertEqual(
            arguments.read_text(encoding="utf-8"),
            "\n".join(
                [
                    "prepare-task",
                    TASK_ID,
                    "--create",
                    "--project",
                    PROJECT_ID,
                    "--title",
                    "Fix picker layout",
                    "--checkout",
                    CHECKOUT_ID,
                    "--provider",
                    "claude",
                    "--request-id",
                    REQUEST_ID,
                    "--can-focus-desktop",
                    "--can-launch-terminal",
                    "--json",
                ]
            ),
        )

    def test_existing_task_open_history_and_inbox_prepare_are_supported(self) -> None:
        executable = self.fixture_executable()
        for arguments in (
            ("--prepare-task", TASK_ID, "--request-id", REQUEST_ID),
            (
                "--prepare-history",
                PROJECT_ID,
                "--checkout",
                CHECKOUT_ID,
                "--request-id",
                REQUEST_ID,
            ),
            ("--prepare-open", SESSION_KEY, "--request-id", REQUEST_ID),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_bridge(executable, *arguments, output=PLAN)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(self.payload(result)["plan"]["kind"], "switch")

    def test_stop_and_select_actions_are_projected(self) -> None:
        stop_output = self.temp / "stop.json"
        stop_output.write_text(
            encode(
                {
                    "schemaVersion": 2,
                    "protocolVersion": 2,
                    "action": {
                        "kind": "stop",
                        "status": "stopped",
                        "hostId": HOST_ID,
                        "sessionKey": CLAUDE_KEY,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        stopped = self.run_bridge(
            self.fixture_executable(), "--stop-session", CLAUDE_KEY, output=stop_output
        )
        self.assertEqual(self.payload(stopped)["action"]["status"], "stopped")
        silent = self.executable("import sys\nsys.exit(0)\n")
        selected = self.run_bridge(
            silent, "--select-surface", SURFACE_ID, "--tmux-client", "/dev/pts/7"
        )
        self.assertEqual(
            self.payload(selected)["action"],
            {"kind": "selected", "surfaceId": SURFACE_ID},
        )

    def test_v1_invalid_utf8_nonzero_timeout_and_overflow_are_bounded(self) -> None:
        executable = self.fixture_executable()
        self.assert_error(
            self.run_bridge(executable, output=V1), "snapshot_invalid_protocol"
        )
        invalid = self.temp / "invalid"
        invalid.write_bytes(b"\xff")
        self.assert_error(
            self.run_bridge(executable, output=invalid), "snapshot_invalid_utf8"
        )
        nonzero = self.executable(
            "import sys\nsys.stderr.write('private')\nsys.exit(9)\n"
        )
        value = self.assert_error(self.run_bridge(nonzero), "swbctl_nonzero_exit")
        self.assertNotIn("private", json.dumps(value))
        sleepy = self.executable("import time\ntime.sleep(5)\n")
        self.assert_error(
            self.run_bridge(sleepy, "--timeout-ms", "100", timeout=2), "process_timeout"
        )
        noisy = self.executable(
            f"import sys\nsys.stdout.buffer.write(b'x' * {MAX_BRIDGE_BYTES + 1})\n"
        )
        self.assert_error(self.run_bridge(noisy), "stdout_overflow")

    def test_argument_combinations_are_argparse_errors(self) -> None:
        cases = (
            ("--max-sessions", "0"),
            ("--prepare-task", TASK_ID),
            ("--create-task",),
            ("--prepare-task", TASK_ID, "--create-task", "--request-id", REQUEST_ID),
            (
                "--prepare-history",
                PROJECT_ID,
                "--title",
                "ignored",
                "--request-id",
                REQUEST_ID,
            ),
            ("--project", PROJECT_ID),
            ("--stop-session", SESSION_KEY),
        )
        for arguments in cases:
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


def encode(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


class BridgeSerializationTests(unittest.TestCase):
    def test_overflow_and_serialization_failures_are_managed(self) -> None:
        exit_code, output = serialize_response(
            {"bridgeVersion": 2, "ok": True, "model": {"value": "x" * MAX_BRIDGE_BYTES}}
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(output)["error"]["code"], "bridge_output_overflow")
        exit_code, output = serialize_response(
            {"bridgeVersion": 2, "ok": True, "model": {"bad": object()}}
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            json.loads(output)["error"]["code"], "bridge_serialization_failed"
        )


class RuntimeBoundaryTests(unittest.TestCase):
    def test_runtime_imports_no_core_or_private_storage_modules(self) -> None:
        runtime_files = sorted((ROOT / "switchboard_dms").rglob("*.py")) + [BRIDGE]
        for path in runtime_files:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                for module in (
                    "agent_switchboard",
                    "sqlite3",
                    "switchboard.storage",
                    "switchboard.providers",
                ):
                    self.assertNotIn(f"import {module}", text)
                    self.assertNotIn(f"from {module}", text)

    def test_bridge_has_no_provider_git_shell_or_desktop_ownership(self) -> None:
        text = "\n".join(
            (ROOT / name).read_text(encoding="utf-8").casefold()
            for name in (
                "switchboard-bridge",
                "switchboard_dms/bridge.py",
                "switchboard_dms/process.py",
            )
        )
        for forbidden in (
            "registry.sqlite",
            "subprocess git",
            "niri",
            "ghostty",
            "codex app-server",
            "shell=true",
            "shlex",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
