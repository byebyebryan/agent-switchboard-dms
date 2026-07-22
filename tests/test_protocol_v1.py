import json
import unittest
from pathlib import Path

from switchboard_dms.protocol import ProtocolError, parse_directive, parse_navigator


FIXTURES = Path(__file__).parent / "fixtures"
HOST = "11111111-1111-4111-8111-111111111111"
REQUEST = "66666666-6666-4666-8666-666666666666"


class ProtocolV1Tests(unittest.TestCase):
    def navigator(self) -> bytes:
        return (FIXTURES / "navigator-state-v1.json").read_bytes()

    def test_projects_navigator_without_reinterpreting_agent_state(self) -> None:
        model = parse_navigator(self.navigator()).to_dict()
        self.assertEqual(model["modelVersion"], 1)
        self.assertEqual(model["sourceNavigatorVersion"], 1)
        self.assertEqual(model["views"][0]["title"], "Implement Phase 6E")
        self.assertEqual(model["views"][0]["activity"], "ready")
        self.assertEqual(model["views"][0]["mode"], "direct")
        self.assertEqual(model["projects"][0]["name"], "Switchboard")
        self.assertEqual(model["recoveries"][0]["actionability"], "open_view")
        encoded = json.dumps(model).casefold()
        for forbidden in (
            "sessionkey",
            "checkoutid",
            "repositoryid",
            "tmuxserver",
            "paneid",
            "desktopToken".casefold(),
        ):
            self.assertNotIn(forbidden, encoded)

    def test_rejects_old_generation_and_sensitive_future_fields(self) -> None:
        old = {
            "fleetVersion": 1,
            "schemaVersion": 2,
            "protocolVersion": 2,
        }
        with self.assertRaises(ProtocolError) as caught:
            parse_navigator(json.dumps(old, separators=(",", ":")).encode())
        self.assertEqual(caught.exception.code, "navigator_incompatible_generation")

        source = json.loads(self.navigator())
        source["checkoutPath"] = "/private"
        with self.assertRaises(ProtocolError) as caught:
            parse_navigator(json.dumps(source, separators=(",", ":")).encode())
        self.assertEqual(caught.exception.code, "navigator_invalid_protocol")

    def test_identity_order_and_references_fail_closed(self) -> None:
        source = json.loads(self.navigator())
        source["projects"][0]["viewId"] = "77777777-7777-4777-8777-777777777777"
        with self.assertRaisesRegex(ProtocolError, "project view reference"):
            parse_navigator(json.dumps(source, separators=(",", ":")).encode())

        source = json.loads(self.navigator())
        source["hosts"].append(
            {
                **source["hosts"][0],
                "hostId": "00000000-0000-4000-8000-000000000001",
                "generationId": "00000000-0000-4000-8000-000000000002",
                "displayName": "snap",
                "isLocal": False,
            }
        )
        with self.assertRaisesRegex(ProtocolError, "canonically ordered"):
            parse_navigator(json.dumps(source, separators=(",", ":")).encode())

    def test_focus_and_attach_directives_are_exact(self) -> None:
        focus = parse_directive(
            (FIXTURES / "directive-focus-v1.json").read_bytes(),
            host_id=HOST,
            request_id=REQUEST,
        )
        self.assertEqual(focus.value["kind"], "focus")
        self.assertNotIn("leaseExpiresAt", focus.value)

        attach = parse_directive(
            (FIXTURES / "directive-attach-v1.json").read_bytes(),
            host_id=HOST,
            request_id=REQUEST,
        )
        self.assertEqual(attach.value["kind"], "attach")
        self.assertEqual(attach.value["leaseExpiresAt"], 2000)

    def test_directive_identity_and_field_shape_fail_closed(self) -> None:
        source = json.loads((FIXTURES / "directive-focus-v1.json").read_bytes())
        source["requestId"] = "77777777-7777-4777-8777-777777777777"
        with self.assertRaises(ProtocolError) as caught:
            parse_directive(
                json.dumps(source, separators=(",", ":")).encode(),
                host_id=HOST,
                request_id=REQUEST,
            )
        self.assertEqual(caught.exception.code, "directive_identity_mismatch")

        source = json.loads((FIXTURES / "directive-attach-v1.json").read_bytes())
        source.pop("leaseExpiresAt")
        with self.assertRaises(ProtocolError) as caught:
            parse_directive(
                json.dumps(source, separators=(",", ":")).encode(),
                host_id=HOST,
                request_id=REQUEST,
            )
        self.assertEqual(caught.exception.code, "directive_invalid_protocol")


if __name__ == "__main__":
    unittest.main()
