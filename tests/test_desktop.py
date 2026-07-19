import hashlib
import json
import os
import stat
import subprocess
import unittest
from pathlib import Path
from unittest.mock import call, patch

from switchboard_dms.desktop import (
    APP_ID_PREFIX,
    DesktopActionError,
    desktop_app_id,
    focus_existing_window,
    matching_niri_window_id,
    open_history,
    open_project,
    open_session,
    serialize_response,
    stop_session,
    terminal_launch_argv,
)
from switchboard_dms.process import ProcessOutput

ROOT = Path(__file__).resolve().parents[1]
OPENER = ROOT / "switchboard-open"
SESSION_KEY = (
    "11111111-1111-4111-8111-111111111111:codex:55555555-5555-4555-8555-555555555555"
)
REQUEST_ID = "77777777-7777-4777-8777-777777777777"
SURFACE_ID = "33333333-3333-4333-8333-333333333333"
PROJECT_ID = "22222222-2222-4222-8222-222222222222"
LOCATION_ID = "44444444-4444-4444-8444-444444444444"
TOKEN = f"surface:{SURFACE_ID}"


def plan(kind: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "kind": kind,
        "hostId": "11111111-1111-4111-8111-111111111111",
        "surfaceId": SURFACE_ID,
        "workspaceId": "as-codex-surface",
        "desktopToken": TOKEN,
    }
    value.update(overrides)
    return value


class DesktopIdentityTests(unittest.TestCase):
    def test_application_id_is_valid_deterministic_and_opaque(self) -> None:
        expected = APP_ID_PREFIX + hashlib.sha256(TOKEN.encode()).hexdigest()[:32]

        self.assertEqual(desktop_app_id(TOKEN), expected)
        self.assertRegex(expected, r"^[a-z][a-z0-9_.]+$")
        self.assertNotIn(SURFACE_ID, expected)
        with self.assertRaises(ValueError):
            desktop_app_id("bad\nvalue")

    def test_niri_match_prefers_exact_app_id_then_bounded_title_fallback(self) -> None:
        app_id = desktop_app_id(TOKEN)
        windows = [
            {"id": 1, "app_id": "com.example.other", "title": "other"},
            {
                "id": 2,
                "app_id": app_id,
                "title": "as-codex-surface:0 codex | project @ snap",
            },
        ]
        self.assertEqual(
            matching_niri_window_id(
                windows,
                app_id=app_id,
                workspace_id="as-codex-surface",
                window_host="snap.lan",
            ),
            2,
        )
        adopted = [
            {
                "id": 9,
                "app_id": "com.mitchellh.ghostty",
                "title": "as-codex-surface:0 codex | project @ snap",
            }
        ]
        self.assertEqual(
            matching_niri_window_id(
                adopted,
                app_id=app_id,
                workspace_id="as-codex-surface",
                window_host="snap.lan",
            ),
            9,
        )
        self.assertIsNone(
            matching_niri_window_id(
                adopted * 2,
                app_id=app_id,
                workspace_id="as-codex-surface",
                window_host="snap",
            )
        )

    def test_focus_uses_exact_shell_free_niri_argv(self) -> None:
        invocations: list[list[str]] = []

        def runner(argv: list[str], *, timeout_ms: int) -> ProcessOutput:
            self.assertEqual(timeout_ms, 500)
            invocations.append(argv)
            if "windows" in argv:
                payload = [
                    {
                        "id": 42,
                        "app_id": desktop_app_id(TOKEN),
                        "title": "managed",
                    }
                ]
                return ProcessOutput(json.dumps(payload).encode(), b"", 0)
            return ProcessOutput(b"", b"", 0)

        focused = focus_existing_window(
            plan("focus"),
            window_host="snap",
            timeout_ms=500,
            runner=runner,
            environ={"NIRI_SOCKET": "/run/user/1000/niri.sock"},
            which=lambda executable: f"/usr/bin/{executable}",
        )

        self.assertTrue(focused)
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
            ],
        )


class DesktopActionTests(unittest.TestCase):
    @staticmethod
    def which(executable: str) -> str | None:
        return {
            "systemd-run": "/usr/bin/systemd-run",
            "ghostty": "/usr/bin/ghostty",
            "niri": "/usr/bin/niri",
        }.get(executable)

    def test_terminal_argv_is_scoped_fixed_and_core_owned(self) -> None:
        argv = terminal_launch_argv(
            systemd_run="/usr/bin/systemd-run",
            terminal="/usr/bin/ghostty",
            swbctl="/opt/swb ctl",
            surface_id=SURFACE_ID,
            desktop_token=TOKEN,
        )

        self.assertEqual(
            argv,
            [
                "/usr/bin/systemd-run",
                "--user",
                "--scope",
                "--collect",
                "--quiet",
                "--",
                "/usr/bin/ghostty",
                f"--class={desktop_app_id(TOKEN)}",
                "-e",
                "/opt/swb ctl",
                "attach-surface",
                SURFACE_ID,
            ],
        )
        self.assertNotIn("tmux", argv)

    @patch("switchboard_dms.desktop.focus_existing_window", return_value=True)
    @patch("switchboard_dms.desktop._prepared_plan")
    def test_focus_success_does_not_launch_terminal(self, prepared, focused) -> None:
        prepared.return_value = plan("focus")
        launched: list[list[str]] = []

        result = open_session(
            swbctl="swbctl",
            terminal="ghostty",
            session_key=SESSION_KEY,
            window_host="snap",
            timeout_ms=1000,
            request_id=REQUEST_ID,
            which=self.which,
            launcher=lambda argv: launched.append(list(argv)),
        )

        self.assertEqual(result, {"kind": "focused", "surfaceId": SURFACE_ID})
        self.assertEqual(launched, [])
        prepared.assert_called_once_with(
            swbctl="swbctl",
            session_key=SESSION_KEY,
            project_id=None,
            location_id=None,
            provider=None,
            history=False,
            request_id=REQUEST_ID,
            timeout_ms=1000,
            can_focus_desktop=True,
        )
        focused.assert_called_once()

    @patch("switchboard_dms.desktop.focus_existing_window", return_value=False)
    @patch("switchboard_dms.desktop._prepared_plan")
    def test_focus_miss_reuses_request_and_launches_attach_fallback(
        self, prepared, _focused
    ) -> None:
        prepared.side_effect = [
            plan("focus"),
            plan("attach", tmuxTarget='{"pane":"%9"}'),
        ]
        launched: list[list[str]] = []

        result = open_session(
            swbctl="/opt/swb ctl",
            terminal="ghostty",
            session_key=SESSION_KEY,
            window_host="snap",
            timeout_ms=1000,
            request_id=REQUEST_ID,
            which=self.which,
            launcher=lambda argv: launched.append(list(argv)),
        )

        self.assertEqual(result, {"kind": "launched", "surfaceId": SURFACE_ID})
        self.assertEqual(
            prepared.call_args_list,
            [
                call(
                    swbctl="/opt/swb ctl",
                    session_key=SESSION_KEY,
                    project_id=None,
                    location_id=None,
                    provider=None,
                    history=False,
                    request_id=REQUEST_ID,
                    timeout_ms=1000,
                    can_focus_desktop=True,
                ),
                call(
                    swbctl="/opt/swb ctl",
                    session_key=SESSION_KEY,
                    project_id=None,
                    location_id=None,
                    provider=None,
                    history=False,
                    request_id=REQUEST_ID,
                    timeout_ms=1000,
                    can_focus_desktop=False,
                ),
            ],
        )
        self.assertEqual(launched[0][-2:], ["attach-surface", SURFACE_ID])

    @patch("switchboard_dms.desktop._prepared_plan")
    def test_new_project_uses_only_stable_ids_and_shared_attach_path(
        self, prepared
    ) -> None:
        prepared.return_value = plan("attach", tmuxTarget='{"pane":"%9"}')
        launched: list[list[str]] = []

        result = open_project(
            swbctl="swbctl",
            terminal="ghostty",
            project_id=PROJECT_ID,
            location_id=LOCATION_ID,
            provider="claude",
            window_host="snap",
            timeout_ms=1000,
            request_id=REQUEST_ID,
            which=self.which,
            launcher=lambda argv: launched.append(list(argv)),
        )

        self.assertEqual(result, {"kind": "launched", "surfaceId": SURFACE_ID})
        prepared.assert_called_once_with(
            swbctl="swbctl",
            session_key=None,
            project_id=PROJECT_ID,
            location_id=LOCATION_ID,
            provider="claude",
            history=False,
            request_id=REQUEST_ID,
            timeout_ms=1000,
            can_focus_desktop=True,
        )
        self.assertEqual(launched[0][-2:], ["attach-surface", SURFACE_ID])

    @patch("switchboard_dms.desktop._prepared_plan")
    def test_history_uses_project_ids_and_shared_attach_path(self, prepared) -> None:
        prepared.return_value = plan("attach", tmuxTarget='{"pane":"%9"}')
        launched: list[list[str]] = []

        result = open_history(
            swbctl="swbctl",
            terminal="ghostty",
            project_id=PROJECT_ID,
            location_id=LOCATION_ID,
            window_host="snap",
            timeout_ms=1000,
            request_id=REQUEST_ID,
            which=self.which,
            launcher=lambda argv: launched.append(list(argv)),
        )

        self.assertEqual(result, {"kind": "launched", "surfaceId": SURFACE_ID})
        prepared.assert_called_once_with(
            swbctl="swbctl",
            session_key=None,
            project_id=PROJECT_ID,
            location_id=LOCATION_ID,
            provider=None,
            history=True,
            request_id=REQUEST_ID,
            timeout_ms=1000,
            can_focus_desktop=True,
        )

    @patch("switchboard_dms.desktop.run_bridge")
    def test_stop_returns_only_validated_core_action(self, bridge) -> None:
        bridge.return_value = {
            "bridgeVersion": 1,
            "ok": True,
            "action": {
                "kind": "stop",
                "status": "already_stopped",
                "sessionKey": SESSION_KEY.replace(":codex:", ":claude:"),
            },
        }

        result = stop_session(
            swbctl="swbctl",
            session_key=SESSION_KEY.replace(":codex:", ":claude:"),
            timeout_ms=1000,
        )

        self.assertEqual(result, {"kind": "stopped", "status": "already_stopped"})
        bridge.assert_called_once_with(
            executable="swbctl",
            refresh=False,
            timeout_ms=1000,
            max_sessions=1000,
            stop_session=SESSION_KEY.replace(":codex:", ":claude:"),
        )

    @patch("switchboard_dms.desktop.focus_existing_window", return_value=True)
    @patch("switchboard_dms.desktop.run_bridge")
    @patch("switchboard_dms.desktop._prepared_plan")
    def test_switch_selects_surface_before_focusing(
        self, prepared, bridge, focused
    ) -> None:
        prepared.return_value = plan("switch", tmuxClient="/dev/pts/7")
        bridge.return_value = {"bridgeVersion": 1, "ok": True, "action": {}}

        result = open_session(
            swbctl="swbctl",
            terminal="ghostty",
            session_key=SESSION_KEY,
            window_host="snap",
            timeout_ms=1000,
            request_id=REQUEST_ID,
            which=self.which,
        )

        self.assertEqual(result, {"kind": "switched", "surfaceId": SURFACE_ID})
        bridge.assert_called_once_with(
            executable="swbctl",
            refresh=False,
            timeout_ms=1000,
            max_sessions=1000,
            select_surface=SURFACE_ID,
            tmux_client="/dev/pts/7",
        )
        focused.assert_called_once()

    @patch("switchboard_dms.desktop.run_bridge")
    def test_blocked_plan_is_a_small_failure(self, bridge) -> None:
        bridge.return_value = {
            "bridgeVersion": 1,
            "ok": True,
            "plan": {
                "kind": "blocked",
                "hostId": "11111111-1111-4111-8111-111111111111",
                "error": {
                    "code": "unmanaged_surface",
                    "message": "This live runtime cannot be managed.",
                    "retryable": False,
                },
            },
        }

        with self.assertRaisesRegex(DesktopActionError, "cannot be managed") as raised:
            open_session(
                swbctl="swbctl",
                terminal="ghostty",
                session_key=SESSION_KEY,
                window_host="snap",
                timeout_ms=1000,
                request_id=REQUEST_ID,
                which=self.which,
            )
        self.assertEqual(raised.exception.code, "unmanaged_surface")

    def test_response_framing_is_one_bounded_json_record(self) -> None:
        exit_code, payload = serialize_response(
            {
                "actionVersion": 1,
                "ok": True,
                "action": {"kind": "focused", "surfaceId": SURFACE_ID},
            }
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload.count(b"\n"), 1)
        self.assertTrue(json.loads(payload)["ok"])

    def test_entrypoint_is_executable_and_argument_errors_do_not_emit_json(
        self,
    ) -> None:
        self.assertEqual(stat.S_IMODE(OPENER.stat().st_mode), 0o755)
        result = subprocess.run(
            [str(OPENER), "--window-host", "snap\nlan", SESSION_KEY],
            cwd=ROOT,
            capture_output=True,
            check=False,
            timeout=2,
            env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertNotEqual(result.stderr, b"")

        result = subprocess.run(
            [str(OPENER), "--stop", SESSION_KEY],
            cwd=ROOT,
            capture_output=True,
            check=False,
            timeout=2,
            env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertNotEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()
