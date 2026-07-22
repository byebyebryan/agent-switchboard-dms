import json
import unittest
from pathlib import Path

from switchboard_dms.desktop import (
    DesktopError,
    desktop_app_id,
    matching_window_ids,
    present,
)
from switchboard_dms.process import ProcessOutput


FIXTURES = Path(__file__).parent / "fixtures"
HOST = "11111111-1111-4111-8111-111111111111"
VIEW = "22222222-2222-4222-8222-222222222222"
PROJECT = "44444444-4444-4444-8444-444444444444"
REQUEST = "66666666-6666-4666-8666-666666666666"


class QueueRunner:
    def __init__(self, outputs: list[ProcessOutput]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[list[str], int]] = []

    def __call__(self, argv, *, timeout_ms):
        self.calls.append((list(argv), timeout_ms))
        return self.outputs.pop(0)


def output(name: str) -> ProcessOutput:
    return ProcessOutput((FIXTURES / name).read_bytes(), b"", 0)


class DesktopV1Tests(unittest.TestCase):
    def which(self, name: str) -> str | None:
        return (
            f"/usr/bin/{name}" if name in {"niri", "systemd-run", "ghostty"} else None
        )

    def test_identity_is_opaque_and_host_qualified(self) -> None:
        first = desktop_app_id("opaque-view-token", HOST)
        second = desktop_app_id(
            "opaque-view-token", "77777777-7777-4777-8777-777777777777"
        )
        self.assertRegex(first, r"^com\.agent_switchboard\.view\.v[0-9a-f]{32}$")
        self.assertNotEqual(first, second)
        self.assertNotIn("opaque", first)

    def test_one_matching_window_focuses_without_fallback(self) -> None:
        app_id = desktop_app_id("opaque-view-token", HOST)
        runner = QueueRunner(
            [
                output("directive-focus-v1.json"),
                ProcessOutput(
                    json.dumps([{"id": 42, "app_id": app_id}]).encode(), b"", 0
                ),
                ProcessOutput(b"", b"", 0),
            ]
        )
        launched: list[list[str]] = []
        action = present(
            swbctl="/opt/swbctl",
            terminal="ghostty",
            host_id=HOST,
            target_kind="view",
            target_id=VIEW,
            request_id=REQUEST,
            timeout_ms=1000,
            runner=runner,
            environment={"NIRI_SOCKET": "socket"},
            which=self.which,
            launcher=lambda argv: launched.append(list(argv)),
        )
        self.assertEqual(action["kind"], "focused")
        self.assertEqual(launched, [])
        self.assertEqual(
            runner.calls[0][0],
            [
                "/opt/swbctl",
                "view",
                "open",
                "--host",
                HOST,
                "--view",
                VIEW,
                "--request-id",
                REQUEST,
                "--can-focus-desktop",
                "--can-launch-terminal",
                "--json",
            ],
        )
        self.assertEqual(
            runner.calls[-1][0],
            ["/usr/bin/niri", "msg", "action", "focus-window", "--id", "42"],
        )

    def test_focus_miss_reuses_request_and_launches_one_leased_attach(self) -> None:
        runner = QueueRunner(
            [
                output("directive-focus-v1.json"),
                ProcessOutput(b"[]", b"", 0),
                output("directive-attach-v1.json"),
            ]
        )
        launched: list[list[str]] = []
        action = present(
            swbctl="/opt/swbctl",
            terminal="ghostty",
            host_id=HOST,
            target_kind="project",
            target_id=PROJECT,
            request_id=REQUEST,
            timeout_ms=1000,
            runner=runner,
            environment={"NIRI_SOCKET": "socket"},
            which=self.which,
            launcher=lambda argv: launched.append(list(argv)),
        )
        self.assertEqual(action["kind"], "launched")
        directives = [
            call for call, _timeout in runner.calls if call[0] == "/opt/swbctl"
        ]
        self.assertEqual(len(directives), 2)
        self.assertEqual(
            directives[0][directives[0].index("--request-id") + 1], REQUEST
        )
        self.assertEqual(
            directives[1][directives[1].index("--request-id") + 1], REQUEST
        )
        self.assertIn("--can-focus-desktop", directives[0])
        self.assertIn("--no-focus-desktop", directives[1])
        self.assertEqual(len(launched), 1)
        self.assertEqual(
            launched[0][-9:],
            [
                "/opt/swbctl",
                "view",
                "attach",
                "--host",
                HOST,
                "--view",
                VIEW,
                "--request-id",
                REQUEST,
            ],
        )

    def test_ambiguous_windows_never_request_or_launch_fallback(self) -> None:
        app_id = desktop_app_id("opaque-view-token", HOST)
        runner = QueueRunner(
            [
                output("directive-focus-v1.json"),
                ProcessOutput(
                    json.dumps(
                        [
                            {"id": 1, "app_id": app_id},
                            {"id": 2, "app_id": app_id},
                        ]
                    ).encode(),
                    b"",
                    0,
                ),
            ]
        )
        launched: list[list[str]] = []
        with self.assertRaises(DesktopError) as caught:
            present(
                swbctl="/opt/swbctl",
                terminal="ghostty",
                host_id=HOST,
                target_kind="view",
                target_id=VIEW,
                request_id=REQUEST,
                timeout_ms=1000,
                runner=runner,
                environment={"NIRI_SOCKET": "socket"},
                which=self.which,
                launcher=lambda argv: launched.append(list(argv)),
            )
        self.assertEqual(caught.exception.code, "ambiguous_desktop_windows")
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(launched, [])

    def test_window_matcher_ignores_ordinary_tmux_clients(self) -> None:
        self.assertEqual(
            matching_window_ids(
                [
                    {"id": 9, "app_id": "com.example.terminal"},
                    {"id": 4, "app_id": "expected"},
                ],
                "expected",
            ),
            (4,),
        )

    def test_staged_core_error_is_preserved_and_never_launches(self) -> None:
        core_error = json.dumps(
            {
                "error": {
                    "code": "cutover_staged",
                    "message": "view open is blocked until cutover commit",
                }
            },
            separators=(",", ":"),
        ).encode()
        runner = QueueRunner([ProcessOutput(core_error, b"private", 2)])
        launched: list[list[str]] = []
        with self.assertRaises(DesktopError) as caught:
            present(
                swbctl="/opt/swbctl",
                terminal="ghostty",
                host_id=HOST,
                target_kind="project",
                target_id=PROJECT,
                request_id=REQUEST,
                timeout_ms=1000,
                runner=runner,
                environment={"NIRI_SOCKET": "socket"},
                which=self.which,
                launcher=lambda argv: launched.append(list(argv)),
            )
        self.assertEqual(caught.exception.code, "cutover_staged")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(launched, [])


if __name__ == "__main__":
    unittest.main()
