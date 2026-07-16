from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path
from uuid import UUID

from switchboard_dms.protocol import (
    MAX_JSON_ARRAY_ITEMS,
    MAX_JSON_BYTES,
    MAX_JSON_DEPTH,
    MAX_JSON_OBJECT_KEYS,
    MAX_JSON_STRING_LENGTH,
    MAX_MODEL_DEGRADED_REASONS,
    MAX_MODEL_ERRORS,
    MAX_MODEL_FEATURES,
    MAX_MODEL_SESSION_BYTES,
    MAX_MODEL_WARNINGS,
    ProtocolError,
    SnapshotModel,
    parse_presentation_plan,
    parse_snapshot,
)


FIXTURE = Path(__file__).parent / "fixtures" / "snapshot-v1.json"
PLAN_FIXTURE = Path(__file__).parent / "fixtures" / "presentation-plan-v1.json"


def fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def cloned_session(number: int, *, recency: int) -> dict[str, object]:
    value = copy.deepcopy(fixture()["sessions"][0])
    provider_id = str(UUID(int=number))
    host_id = value["hostId"]
    value.update(
        sessionKey=f"{host_id}:codex:{provider_id}",
        providerSessionId=provider_id,
        name=f"session-{number}",
        firstObservedAt=recency - 1,
        lastObservedAt=recency,
        lastActivityAt=recency,
    )
    value.pop("surfaceId", None)
    return value


class PresentationPlanTests(unittest.TestCase):
    def test_switch_fixture_is_validated_and_projected(self) -> None:
        plan = parse_presentation_plan(PLAN_FIXTURE.read_bytes())

        self.assertEqual(plan["kind"], "switch")
        self.assertEqual(plan["surfaceId"], "33333333-3333-4333-8333-333333333333")
        self.assertEqual(plan["tmuxClient"], "/dev/pts/7")
        self.assertEqual(plan["leaseExpiresAt"], 1784142030000)

    def test_blocked_plan_requires_only_a_structured_error(self) -> None:
        value = {
            "schemaVersion": 1,
            "protocolVersion": 1,
            "plan": {
                "kind": "blocked",
                "hostId": "11111111-1111-4111-8111-111111111111",
                "error": {
                    "code": "unmanaged_surface",
                    "message": "The live session is not routable.",
                    "scope": "session",
                    "retryable": False,
                    "observedAt": 1,
                },
            },
        }

        plan = parse_presentation_plan(encode(value))

        self.assertEqual(plan["kind"], "blocked")
        self.assertEqual(plan["error"]["code"], "unmanaged_surface")

        value["plan"]["surfaceId"] = "33333333-3333-4333-8333-333333333333"
        with self.assertRaisesRegex(ProtocolError, "surface locators"):
            parse_presentation_plan(encode(value))

    def test_executable_plan_shapes_are_strict(self) -> None:
        value = json.loads(PLAN_FIXTURE.read_text(encoding="utf-8"))
        value["plan"].pop("tmuxClient")
        with self.assertRaisesRegex(
            ProtocolError, "requires tmuxTarget and tmuxClient"
        ):
            parse_presentation_plan(encode(value))

        value = json.loads(PLAN_FIXTURE.read_text(encoding="utf-8"))
        value["plan"]["promptText"] = "must not cross the boundary"
        with self.assertRaisesRegex(ProtocolError, "forbidden"):
            parse_presentation_plan(encode(value))


class SnapshotProjectionTests(unittest.TestCase):
    def test_fixture_projects_one_bounded_codex_model(self) -> None:
        model = parse_snapshot(FIXTURE.read_bytes())

        self.assertIsInstance(model, SnapshotModel)
        self.assertEqual(len(model.sessions), 1)
        session = model.sessions[0]
        self.assertEqual(
            session["sessionKey"],
            fixture()["sessions"][0]["sessionKey"],
        )
        self.assertEqual(session["projectName"], "example")
        self.assertIsNone(session["locationName"])
        self.assertEqual(session["runtimePresence"], "live")
        self.assertEqual(session["activity"], "working")
        self.assertEqual(session["recencyAt"], session["lastObservedAt"])
        self.assertEqual(model.codex_capability["status"], "available")
        self.assertFalse(model.truncated)
        self.assertLessEqual(len(model.to_json().encode("utf-8")), 8 * 1024 * 1024)

    def test_safe_future_fields_are_ignored_in_the_model(self) -> None:
        value = fixture()
        value["futureSafeField"] = {"flag": True}
        value["sessions"][0]["futureSafeSessionField"] = "safe"

        serialized = parse_snapshot(encode(value)).to_json()

        self.assertNotIn("futureSafeField", serialized)
        self.assertNotIn("futureSafeSessionField", serialized)
        self.assertNotIn("futureEnvelopeField", serialized)

    def test_sensitive_future_field_is_rejected(self) -> None:
        value = fixture()
        value["sessions"][0]["promptText"] = "must not cross the boundary"

        with self.assertRaisesRegex(ProtocolError, "forbidden"):
            parse_snapshot(encode(value))

    def test_empty_capabilities_are_neutral(self) -> None:
        value = fixture()
        value["capabilities"] = []

        model = parse_snapshot(encode(value))

        self.assertEqual(model.codex_capability["status"], "neutral")
        self.assertIsNone(model.codex_capability["available"])
        self.assertEqual(model.warnings, ())

    def test_degraded_codex_capability_and_error_become_warnings(self) -> None:
        value = fixture()
        capability = value["capabilities"][0]
        capability["available"] = False
        capability["degradedReasons"] = [
            {
                "code": "provider_not_found",
                "message": "Codex is unavailable.",
                "retryable": True,
                "feature": "app_server_thread_list",
            }
        ]
        value["errors"] = [
            {
                "code": "provider_not_found",
                "message": "Codex is unavailable.",
                "scope": "provider",
                "hostId": value["host"]["hostId"],
                "provider": "codex",
                "retryable": True,
                "observedAt": value["generatedAt"],
            }
        ]

        model = parse_snapshot(encode(value))

        self.assertEqual(model.codex_capability["status"], "degraded")
        self.assertFalse(model.codex_capability["available"])
        self.assertEqual(
            [warning["source"] for warning in model.warnings],
            ["capability", "error"],
        )
        self.assertTrue(
            all(warning["code"] == "provider_not_found" for warning in model.warnings)
        )

    def test_sessions_are_recency_sorted_and_structurally_truncated(self) -> None:
        value = fixture()
        value["sessions"].extend(
            [
                cloned_session(2, recency=1_784_142_001_000),
                cloned_session(3, recency=1_784_142_002_000),
                cloned_session(4, recency=1_784_142_002_000),
            ]
        )

        model = parse_snapshot(encode(value), max_sessions=2)

        self.assertEqual(
            [session["providerSessionId"] for session in model.sessions],
            [str(UUID(int=3)), str(UUID(int=4))],
        )
        self.assertTrue(model.truncated)
        self.assertEqual(
            model.to_dict()["truncation"],
            {
                "truncated": True,
                "sourceCount": 4,
                "emittedCount": 2,
                "limit": 2,
                "byteLimit": MAX_MODEL_SESSION_BYTES,
            },
        )
        warning = model.warnings[-1]
        self.assertEqual(warning["code"], "model_sessions_truncated")
        self.assertEqual(warning["details"], {"emittedCount": 2, "retainedCount": 4})

    def test_non_codex_sessions_are_validated_but_not_projected(self) -> None:
        value = fixture()
        claude = cloned_session(8, recency=1_784_142_003_000)
        claude["provider"] = "claude"
        claude["sessionKey"] = (
            f"{claude['hostId']}:claude:{claude['providerSessionId']}"
        )
        value["sessions"].append(claude)

        model = parse_snapshot(encode(value))

        self.assertEqual(len(model.sessions), 1)
        self.assertEqual(model.sessions[0]["provider"], "codex")
        self.assertEqual(model.source_session_count, 1)

    def test_equivalent_uuid_spelling_is_canonicalized_across_references(self) -> None:
        value = fixture()
        provider_id = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
        canonical_id = provider_id.lower()
        host_id = value["host"]["hostId"]
        upper_key = f"{host_id}:codex:{provider_id}"
        canonical_key = f"{host_id}:codex:{canonical_id}"
        value["sessions"][0]["providerSessionId"] = provider_id
        value["sessions"][0]["sessionKey"] = upper_key
        value["runtimes"][0]["sessionKey"] = canonical_key
        value["surfaces"][0]["currentSessionKey"] = upper_key

        model = parse_snapshot(encode(value))

        self.assertEqual(model.sessions[0]["sessionKey"], canonical_key)
        self.assertEqual(model.sessions[0]["providerSessionId"], canonical_id)

    def test_duplicate_logical_session_keys_with_different_spelling_fail(self) -> None:
        value = fixture()
        provider_id = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
        host_id = value["host"]["hostId"]
        value["sessions"][0]["providerSessionId"] = provider_id
        value["sessions"][0]["sessionKey"] = f"{host_id}:codex:{provider_id}"
        value["runtimes"][0]["sessionKey"] = f"{host_id}:codex:{provider_id.lower()}"
        value["surfaces"][0]["currentSessionKey"] = (
            f"{host_id}:codex:{provider_id.lower()}"
        )
        duplicate = copy.deepcopy(value["sessions"][0])
        duplicate["providerSessionId"] = provider_id.lower()
        duplicate["sessionKey"] = f"{host_id}:codex:{provider_id.lower()}"
        duplicate.pop("surfaceId", None)
        value["sessions"].append(duplicate)

        with self.assertRaisesRegex(ProtocolError, "duplicate sessionKey"):
            parse_snapshot(encode(value))

    def test_feature_reason_and_error_diagnostics_are_independently_bounded(
        self,
    ) -> None:
        value = fixture()
        capability = value["capabilities"][0]
        capability["features"] = [
            f"feature_{index}" for index in range(MAX_MODEL_FEATURES + 5)
        ]
        capability["degradedReasons"] = [
            {
                "code": f"reason_{index}",
                "message": "Structured degradation.",
                "retryable": False,
            }
            for index in range(MAX_MODEL_DEGRADED_REASONS + 5)
        ]
        value["errors"] = [
            {
                "code": f"error_{index}",
                "message": "Structured provider failure.",
                "scope": "provider",
                "provider": "codex",
                "retryable": True,
                "observedAt": value["generatedAt"],
            }
            for index in range(MAX_MODEL_ERRORS + 5)
        ]

        model = parse_snapshot(encode(value))

        self.assertEqual(len(model.codex_capability["features"]), MAX_MODEL_FEATURES)
        self.assertEqual(
            len(model.codex_capability["degradedReasons"]),
            MAX_MODEL_DEGRADED_REASONS,
        )
        for name in ("features", "degradedReasons", "errors", "warnings"):
            self.assertTrue(model.diagnostic_truncation[name]["truncated"])
        self.assertLessEqual(len(model.warnings), MAX_MODEL_WARNINGS)
        self.assertIn(
            "model_diagnostics_truncated",
            {warning["code"] for warning in model.warnings},
        )

    def test_near_limit_error_collection_still_projects_a_bounded_model(self) -> None:
        value = fixture()
        error = {
            "code": "e",
            "message": "m",
            "scope": "host",
            "retryable": False,
            "observedAt": 0,
        }
        value["errors"] = [error] * 100_000
        raw = encode(value)
        self.assertLessEqual(len(raw.encode("utf-8")), MAX_JSON_BYTES)

        model = parse_snapshot(raw)

        self.assertEqual(model.diagnostic_truncation["errors"]["sourceCount"], 100_000)
        self.assertLessEqual(
            model.diagnostic_truncation["errors"]["emittedCount"], MAX_MODEL_ERRORS
        )
        self.assertTrue(model.diagnostic_truncation["errors"]["truncated"])
        self.assertLessEqual(len(model.warnings), MAX_MODEL_WARNINGS)
        self.assertLessEqual(len(model.to_json().encode("utf-8")), MAX_JSON_BYTES)

    def test_final_model_tree_is_revalidated_before_serialization(self) -> None:
        model = parse_snapshot(FIXTURE.read_bytes())
        model.host["promptText"] = "must not serialize"
        with self.assertRaisesRegex(ProtocolError, "forbidden"):
            model.to_dict()
        with self.assertRaisesRegex(ProtocolError, "forbidden"):
            model.to_json()

        model = parse_snapshot(FIXTURE.read_bytes())
        model.sessions[0]["recencyAt"] = math.inf
        with self.assertRaisesRegex(ProtocolError, "non-finite"):
            model.to_dict()
        with self.assertRaisesRegex(ProtocolError, "non-finite"):
            model.to_json()


class SnapshotModelMutationTests(unittest.TestCase):
    def assert_serializers_reject(self, model: SnapshotModel, pattern: str) -> None:
        for serializer in (model.to_dict, model.to_json):
            with self.subTest(serializer=serializer.__name__):
                with self.assertRaisesRegex(ProtocolError, pattern):
                    serializer()

    @staticmethod
    def set_diagnostic_warning(model: SnapshotModel) -> None:
        model.warnings = (
            {
                "source": "model",
                "code": "model_diagnostics_truncated",
                "message": (
                    "The frontend model omitted diagnostics to remain bounded."
                ),
                "retryable": False,
                "counts": copy.deepcopy(model.diagnostic_truncation),
            },
        )

    def test_generated_at_is_retyped_after_mutation(self) -> None:
        model = parse_snapshot(FIXTURE.read_bytes())
        model.generated_at = True

        self.assert_serializers_reject(model, "non-negative integer")

    def test_source_counts_and_limits_are_revalidated_after_mutation(self) -> None:
        cases = (
            ("source-bool", "source_session_count", True),
            ("source-below-emitted", "source_session_count", 0),
            ("source-overflow", "source_session_count", 100_001),
            ("limit-bool", "session_limit", True),
            ("limit-zero", "session_limit", 0),
            ("limit-overflow", "session_limit", 1_001),
        )
        for name, attribute, value in cases:
            with self.subTest(name=name):
                model = parse_snapshot(FIXTURE.read_bytes())
                setattr(model, attribute, value)
                self.assert_serializers_reject(model, "integer|count|limit|bounds")

        for name, field, value in (
            ("diagnostic-source-bool", "sourceCount", True),
            ("diagnostic-source-underflow", "sourceCount", 0),
            ("diagnostic-limit", "limit", MAX_MODEL_FEATURES + 1),
            ("diagnostic-byte-limit", "byteLimit", 1),
        ):
            with self.subTest(name=name):
                model = parse_snapshot(FIXTURE.read_bytes())
                model.diagnostic_truncation["features"][field] = value
                self.assert_serializers_reject(
                    model, "integer|count|limit|inconsistent"
                )

    def test_codex_identity_and_status_are_revalidated_after_mutation(self) -> None:
        mutations = (
            lambda model: model.sessions[0].__setitem__("provider", "claude"),
            lambda model: model.sessions[0].__setitem__(
                "sessionKey", model.sessions[0]["sessionKey"].replace("codex", "claude")
            ),
            lambda model: model.codex_capability.__setitem__("provider", "claude"),
            lambda model: model.codex_capability.__setitem__("status", "neutral"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                model = parse_snapshot(FIXTURE.read_bytes())
                mutate(model)
                self.assert_serializers_reject(
                    model, "Codex|codex|provider|status|identity"
                )

    def test_project_and_location_relationships_are_revalidated(self) -> None:
        mutations = (
            {"projectId": None},
            {"projectName": None},
            {"locationId": None, "locationName": "impossible"},
            {"projectId": None, "projectName": None},
        )
        for index, updates in enumerate(mutations):
            with self.subTest(index=index):
                model = parse_snapshot(FIXTURE.read_bytes())
                model.sessions[0].update(updates)
                self.assert_serializers_reject(
                    model, "project|location|requires|inconsistent"
                )

    def test_location_id_with_optional_missing_display_name_remains_valid(self) -> None:
        model = parse_snapshot(FIXTURE.read_bytes())

        session = model.to_dict()["sessions"][0]

        self.assertIsNotNone(session["locationId"])
        self.assertIsNone(session["locationName"])

    def test_model_collection_count_limits_are_revalidated_after_mutation(self) -> None:
        model = parse_snapshot(FIXTURE.read_bytes())
        model.sessions = tuple(copy.deepcopy(model.sessions[0]) for _ in range(1_001))
        self.assert_serializers_reject(model, "too many records")

        model = parse_snapshot(FIXTURE.read_bytes())
        model.codex_capability["features"] = [
            f"feature_{index}" for index in range(MAX_MODEL_FEATURES + 1)
        ]
        self.assert_serializers_reject(model, "too many records")

        model = parse_snapshot(FIXTURE.read_bytes())
        model.warnings = tuple(
            {
                "source": "capability",
                "code": f"warning_{index}",
                "retryable": False,
            }
            for index in range(MAX_MODEL_WARNINGS + 1)
        )
        self.assert_serializers_reject(model, "too many records")

    def test_model_string_and_session_byte_limits_are_revalidated(self) -> None:
        model = parse_snapshot(FIXTURE.read_bytes())
        model.host["displayName"] = "x" * 257
        self.assert_serializers_reject(model, "bounded string")

        model = parse_snapshot(FIXTURE.read_bytes())
        base = model.sessions[0]
        sessions = []
        for index in range(1, 501):
            session = copy.deepcopy(base)
            provider_session_id = str(UUID(int=index))
            session["providerSessionId"] = provider_session_id
            session["sessionKey"] = f"{session['hostId']}:codex:{provider_session_id}"
            session["name"] = "n" * 512
            session["purpose"] = "p" * 4096
            session["cwd"] = "c" * 4096
            sessions.append(session)
        sessions.sort(key=lambda item: (-item["recencyAt"], item["sessionKey"]))
        model.sessions = tuple(sessions)
        model.source_session_count = len(sessions)
        model.session_limit = len(sessions)
        self.assertGreater(
            len(json.dumps(sessions, separators=(",", ":")).encode("utf-8")),
            MAX_MODEL_SESSION_BYTES,
        )
        self.assert_serializers_reject(model, "byte limit")

    def test_nonempty_bounded_sources_cannot_emit_zero_items(self) -> None:
        model = parse_snapshot(FIXTURE.read_bytes())
        model.sessions = ()
        model.warnings = (
            {
                "source": "model",
                "code": "model_sessions_truncated",
                "message": "The frontend model omitted sessions to remain bounded.",
                "retryable": False,
                "details": {"emittedCount": 0, "retainedCount": 1},
                "limit": model.session_limit,
                "byteLimit": MAX_MODEL_SESSION_BYTES,
            },
        )
        self.assert_serializers_reject(model, "cannot omit every")

        neutral = fixture()
        neutral["capabilities"] = []
        model = parse_snapshot(encode(neutral))
        feature_summary = model.diagnostic_truncation["features"]
        feature_summary.update(sourceCount=1, truncated=True)
        self.set_diagnostic_warning(model)
        self.assert_serializers_reject(model, "cannot omit every|neutral")

        for name in ("degradedReasons", "errors"):
            with self.subTest(name=name):
                model = parse_snapshot(FIXTURE.read_bytes())
                summary = model.diagnostic_truncation[name]
                summary.update(sourceCount=1, truncated=True)
                warning_summary = model.diagnostic_truncation["warnings"]
                warning_summary.update(sourceCount=1, truncated=True)
                self.set_diagnostic_warning(model)
                self.assert_serializers_reject(model, "cannot omit every")

    def test_unknown_and_private_model_fields_are_rejected_after_mutation(self) -> None:
        model = parse_snapshot(FIXTURE.read_bytes())
        model.host["futureHostField"] = "not allowlisted"
        self.assert_serializers_reject(model, "unsupported fields")

        model = parse_snapshot(FIXTURE.read_bytes())
        model.host["promptText"] = "must not serialize"
        self.assert_serializers_reject(model, "forbidden")

        model = parse_snapshot(FIXTURE.read_bytes())
        model.sessions[0]["futureSessionField"] = True
        self.assert_serializers_reject(model, "unsupported fields")


class SnapshotBoundaryTests(unittest.TestCase):
    def test_total_size_depth_string_array_object_and_number_bounds(self) -> None:
        with self.subTest(bound="bytes"):
            with self.assertRaisesRegex(ProtocolError, "byte limit"):
                parse_snapshot(b" " * (MAX_JSON_BYTES + 1))

        with self.subTest(bound="depth"):
            value = fixture()
            nested: dict[str, object] = {}
            value["futureTree"] = nested
            for _ in range(MAX_JSON_DEPTH + 1):
                child: dict[str, object] = {}
                nested["level"] = child
                nested = child
            with self.assertRaisesRegex(ProtocolError, "nesting depth"):
                parse_snapshot(encode(value))

        with self.subTest(bound="string"):
            value = fixture()
            value["futureText"] = "x" * (MAX_JSON_STRING_LENGTH + 1)
            with self.assertRaisesRegex(ProtocolError, "oversized string"):
                parse_snapshot(encode(value))

        with self.subTest(bound="array"):
            value = fixture()
            value["futureItems"] = [0] * (MAX_JSON_ARRAY_ITEMS + 1)
            with self.assertRaisesRegex(ProtocolError, "array items"):
                parse_snapshot(encode(value))

        with self.subTest(bound="object"):
            value = fixture()
            value["futureMap"] = {
                f"field{index}": index for index in range(MAX_JSON_OBJECT_KEYS + 1)
            }
            with self.assertRaisesRegex(ProtocolError, "object keys"):
                parse_snapshot(encode(value))

        with self.subTest(bound="finite"):
            value = fixture()
            value["futureNumber"] = math.nan
            with self.assertRaisesRegex(ProtocolError, "non-finite"):
                parse_snapshot(encode(value))

    def test_terminal_controls_are_rejected_even_in_safe_future_fields(self) -> None:
        value = fixture()
        value["futureText"] = "safe\x1bunsafe"

        with self.assertRaisesRegex(ProtocolError, "control"):
            parse_snapshot(encode(value))

    def test_model_session_limit_is_strict(self) -> None:
        raw = FIXTURE.read_bytes()
        for limit in (True, 0, 1_001, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(ValueError, "max_sessions"):
                    parse_snapshot(raw, max_sessions=limit)


class SnapshotMutationTests(unittest.TestCase):
    def assert_invalid(self, value: object, pattern: str) -> None:
        with self.assertRaisesRegex(ProtocolError, pattern):
            parse_snapshot(encode(value))

    def test_versions_and_required_collections_are_strict(self) -> None:
        for key in ("schemaVersion", "protocolVersion"):
            value = fixture()
            value[key] = 2
            with self.subTest(key=key):
                self.assert_invalid(value, "version")

        for key in (
            "projects",
            "locations",
            "sessions",
            "runtimes",
            "surfaces",
            "capabilities",
            "errors",
        ):
            value = fixture()
            del value[key]
            with self.subTest(key=key):
                self.assert_invalid(value, "required")

    def test_uuid_identity_and_cross_record_references_are_checked(self) -> None:
        value = fixture()
        value["host"]["hostId"] = "not-a-uuid"
        self.assert_invalid(value, "UUID")

        value = fixture()
        value["sessions"][0]["providerSessionId"] = str(UUID(int=99))
        self.assert_invalid(value, "identity fields")

        value = fixture()
        value["locations"][0]["projectId"] = str(UUID(int=98))
        self.assert_invalid(value, "not in projects")

        value = fixture()
        value["runtimes"][0]["sessionKey"] = (
            f"{value['host']['hostId']}:codex:{UUID(int=97)}"
        )
        self.assert_invalid(value, "not in sessions")

        value = fixture()
        value["surfaces"][0]["currentSessionKey"] = None
        value["surfaces"][0]["bindingConfidence"] = "unknown"
        self.assert_invalid(value, "surface binding is inconsistent")

    def test_status_and_timestamp_values_are_checked(self) -> None:
        value = fixture()
        value["sessions"][0]["activity"] = "idle"
        self.assert_invalid(value, "unsupported value")

        value = fixture()
        value["sessions"][0]["lastObservedAt"] = (
            value["sessions"][0]["firstObservedAt"] - 1
        )
        self.assert_invalid(value, "timestamps are reversed")

        value = fixture()
        value["surfaces"][0]["lastObservedAt"] = value["surfaces"][0]["createdAt"] - 1
        self.assert_invalid(value, "timestamps are reversed")

        value = fixture()
        value["generatedAt"] = True
        self.assert_invalid(value, "non-negative integer")

    def test_bounded_projection_strings_are_enforced(self) -> None:
        for field, maximum in (("name", 512), ("purpose", 4096), ("cwd", 4096)):
            value = fixture()
            value["sessions"][0][field] = "x" * (maximum + 1)
            with self.subTest(field=field):
                self.assert_invalid(value, "bounded string")

    def test_capability_and_error_details_are_typed_and_allowlisted(self) -> None:
        value = fixture()
        value["capabilities"][0]["available"] = False
        value["capabilities"][0]["degradedReasons"] = []
        self.assert_invalid(value, "must explain unavailable")

        base_error = {
            "code": "snapshot_sessions_truncated",
            "message": "The snapshot was truncated.",
            "scope": "host",
            "hostId": fixture()["host"]["hostId"],
            "retryable": False,
            "observedAt": fixture()["generatedAt"],
        }

        value = fixture()
        error = copy.deepcopy(base_error)
        error["details"] = {"arbitrary": "not allowed"}
        value["errors"] = [error]
        self.assert_invalid(value, "unsupported retained detail")

        value = fixture()
        error = copy.deepcopy(base_error)
        error["details"] = {"emittedCount": True, "retainedCount": 1}
        value["errors"] = [error]
        self.assert_invalid(value, "non-negative integer")

        value = fixture()
        error = copy.deepcopy(base_error)
        error["details"] = {"emittedCount": 2, "retainedCount": 1}
        value["errors"] = [error]
        self.assert_invalid(value, "must not exceed")

        value = fixture()
        value["capabilities"][0]["schemaFingerprint"] = "not-a-hash"
        self.assert_invalid(value, "SHA-256")

    def test_duplicate_capabilities_and_cross_host_errors_are_rejected(self) -> None:
        value = fixture()
        value["capabilities"].append(copy.deepcopy(value["capabilities"][0]))
        self.assert_invalid(value, "duplicate provider capabilities")

        value = fixture()
        value["errors"] = [
            {
                "code": "provider_unavailable",
                "message": "Unavailable.",
                "scope": "provider",
                "hostId": str(UUID(int=90)),
                "provider": "codex",
                "retryable": True,
                "observedAt": value["generatedAt"],
            }
        ]
        self.assert_invalid(value, "belongs to another host")


if __name__ == "__main__":
    unittest.main()
