import argparse
import json
import unittest
from pathlib import Path
from unittest import mock

from switchboard_dms import bridge
from switchboard_dms.process import ProcessOutput, ProcessRunError


FIXTURE = Path(__file__).parent / "fixtures" / "navigator-state-v1.json"


class BridgeV1Tests(unittest.TestCase):
    def test_retained_and_refresh_argv_are_fixed(self) -> None:
        for refresh, expected in (
            (False, ["/opt/swbctl", "state", "navigator", "--json"]),
            (True, ["/opt/swbctl", "state", "navigator", "--refresh", "--json"]),
        ):
            with (
                self.subTest(refresh=refresh),
                mock.patch.object(
                    bridge,
                    "run_process",
                    return_value=ProcessOutput(FIXTURE.read_bytes(), b"private", 0),
                ) as runner,
            ):
                payload = bridge.run(
                    argparse.Namespace(
                        swbctl="/opt/swbctl", timeout_ms=4321, refresh=refresh
                    )
                )
                runner.assert_called_once_with(expected, timeout_ms=4321)
                envelope = json.loads(payload)
                self.assertEqual(envelope["bridgeVersion"], 1)
                self.assertTrue(envelope["ok"])
                self.assertEqual(envelope["model"]["modelVersion"], 1)

    def test_nonzero_and_process_failures_are_bounded(self) -> None:
        with mock.patch.object(
            bridge, "run_process", return_value=ProcessOutput(b"private", b"secret", 2)
        ):
            with self.assertRaises(ProcessRunError) as caught:
                bridge.run(
                    argparse.Namespace(swbctl="swbctl", timeout_ms=1000, refresh=False)
                )
        self.assertEqual(caught.exception.code, "core_incompatible_generation")
        self.assertNotIn("private", caught.exception.message)

    def test_argument_bounds(self) -> None:
        with self.assertRaises(SystemExit):
            bridge.parser().parse_args(["--timeout-ms", "99"])
        with self.assertRaises(SystemExit):
            bridge.parser().parse_args(["--swbctl", "bad\x00name"])


if __name__ == "__main__":
    unittest.main()
