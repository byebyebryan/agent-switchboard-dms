import io
import json
import unittest
from unittest import mock

from switchboard_dms.process import ProcessOutput
from switchboard_dms.projects import (
    MANAGER_APP_ID,
    ProjectManagerError,
    manager_window_id,
    open_project_manager,
    project_tui_argv,
    refresh_bridge,
    terminal_argv,
)

PROJECT_ID = "22222222-2222-4222-8222-222222222222"


class _Process:
    pid = 12345

    def poll(self):
        return None


class _BinaryStdout:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()


class ProjectManagerArgumentTests(unittest.TestCase):
    def test_tui_and_terminal_argv_are_fixed_and_shell_free(self) -> None:
        self.assertEqual(
            project_tui_argv(
                swbctl="/opt/swb ctl", project_id=PROJECT_ID, add_project=False
            ),
            [
                "/opt/swb ctl",
                "tui",
                "--view",
                "projects",
                "--project",
                PROJECT_ID,
            ],
        )
        self.assertEqual(
            terminal_argv(
                terminal="ghostty",
                swbctl="swbctl",
                project_id=None,
                add_project=True,
            ),
            [
                "ghostty",
                f"--class={MANAGER_APP_ID}",
                "-e",
                "swbctl",
                "tui",
                "--view",
                "projects",
                "--add-project",
            ],
        )

    def test_window_match_requires_one_exact_application_id(self) -> None:
        self.assertEqual(
            manager_window_id(
                [
                    {"id": 4, "app_id": "com.example.other"},
                    {"id": 8, "app_id": MANAGER_APP_ID},
                ]
            ),
            8,
        )
        self.assertIsNone(manager_window_id([]))
        with self.assertRaisesRegex(ProjectManagerError, "More than one"):
            manager_window_id(
                [
                    {"id": 8, "app_id": MANAGER_APP_ID},
                    {"id": 9, "app_id": MANAGER_APP_ID},
                ]
            )


class ProjectManagerWindowTests(unittest.TestCase):
    @staticmethod
    def _which(executable: str) -> str | None:
        return "/usr/bin/niri" if executable == "niri" else None

    def test_existing_manager_is_focused_and_observed_until_close(self) -> None:
        invocations: list[list[str]] = []
        window_reads = iter(
            [
                [{"id": 42, "app_id": MANAGER_APP_ID}],
                [],
            ]
        )

        def runner(argv, *, timeout_ms):
            self.assertEqual(timeout_ms, 500)
            invocations.append(list(argv))
            if "windows" in argv:
                return ProcessOutput(json.dumps(next(window_reads)).encode(), b"", 0)
            return ProcessOutput(b"", b"", 0)

        open_project_manager(
            swbctl="swbctl",
            terminal="ghostty",
            project_id=PROJECT_ID,
            add_project=False,
            timeout_ms=500,
            runner=runner,
            launcher=lambda _argv: self.fail("existing manager launched a terminal"),
            which=self._which,
            environ={"NIRI_SOCKET": "/run/user/1000/niri"},
            sleep=lambda _seconds: None,
        )

        self.assertEqual(
            invocations,
            [
                ["/usr/bin/niri", "msg", "--json", "windows"],
                [
                    "/usr/bin/niri",
                    "msg",
                    "action",
                    "focus-window",
                    "--id",
                    "42",
                ],
                ["/usr/bin/niri", "msg", "--json", "windows"],
            ],
        )

    def test_new_manager_uses_selected_project_and_waits_for_window_close(self) -> None:
        window_reads = iter(
            [
                [],
                [{"id": 42, "app_id": MANAGER_APP_ID}],
                [],
            ]
        )
        launched: list[list[str]] = []
        now = iter((0.0, 0.0, 0.1))

        def runner(argv, *, timeout_ms):
            self.assertEqual(timeout_ms, 500)
            return ProcessOutput(json.dumps(next(window_reads)).encode(), b"", 0)

        open_project_manager(
            swbctl="swbctl",
            terminal="ghostty",
            project_id=PROJECT_ID,
            add_project=False,
            timeout_ms=500,
            runner=runner,
            launcher=lambda argv: launched.append(list(argv)) or _Process(),
            which=self._which,
            environ={"NIRI_SOCKET": "/run/user/1000/niri"},
            monotonic=lambda: next(now),
            sleep=lambda _seconds: None,
        )

        self.assertEqual(
            launched,
            [
                [
                    "ghostty",
                    f"--class={MANAGER_APP_ID}",
                    "-e",
                    "swbctl",
                    "tui",
                    "--view",
                    "projects",
                    "--project",
                    PROJECT_ID,
                ]
            ],
        )

    def test_manager_requires_the_supported_desktop(self) -> None:
        with self.assertRaisesRegex(ProjectManagerError, "requires the niri"):
            open_project_manager(
                swbctl="swbctl",
                terminal="ghostty",
                project_id=None,
                add_project=False,
                timeout_ms=500,
                which=lambda _executable: None,
                environ={},
            )


class ProjectManagerRefreshTests(unittest.TestCase):
    def test_refresh_returns_one_exact_bridge_record(self) -> None:
        payload = b'{"bridgeVersion":3,"model":{},"ok":true}\n'
        invocations = []

        def runner(argv, *, timeout_ms):
            invocations.append((list(argv), timeout_ms))
            return ProcessOutput(payload, b"", 0)

        exit_code, output = refresh_bridge(
            bridge="/plugin/switchboard-bridge",
            swbctl="/opt/swbctl",
            timeout_ms=700,
            runner=runner,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(output, payload)
        self.assertEqual(
            invocations,
            [
                (
                    [
                        "/plugin/switchboard-bridge",
                        "--swbctl",
                        "/opt/swbctl",
                        "--timeout-ms",
                        "700",
                        "--refresh",
                    ],
                    2700,
                )
            ],
        )

    def test_refresh_rejects_diagnostics_and_invalid_envelopes(self) -> None:
        with self.assertRaisesRegex(ProjectManagerError, "diagnostics"):
            refresh_bridge(
                bridge="bridge",
                swbctl="swbctl",
                timeout_ms=500,
                runner=lambda *_args, **_kwargs: ProcessOutput(b"{}", b"bad", 1),
            )
        with self.assertRaisesRegex(ProjectManagerError, "invalid response"):
            refresh_bridge(
                bridge="bridge",
                swbctl="swbctl",
                timeout_ms=500,
                runner=lambda *_args, **_kwargs: ProcessOutput(b"{}", b"", 1),
            )

    def test_main_returns_structured_bridge_error_without_stderr(self) -> None:
        from switchboard_dms import projects

        stdout = _BinaryStdout()
        stderr = io.StringIO()
        with (
            mock.patch.object(projects.sys, "stdout", stdout),
            mock.patch.object(projects.sys, "stderr", stderr),
        ):
            exit_code = projects.main(
                ["--project", PROJECT_ID, "--add-project"],
                bridge_path="/plugin/switchboard-bridge",
            )

        response = json.loads(stdout.buffer.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(response["bridgeVersion"], 3)
        self.assertEqual(response["error"]["code"], "project_manager_arguments_invalid")


if __name__ == "__main__":
    unittest.main()
