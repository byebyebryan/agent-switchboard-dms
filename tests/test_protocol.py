import copy
import json
import math
from pathlib import Path
import unittest

from switchboard_dms.protocol import (
    MAX_JSON_BYTES,
    MAX_MODEL_PROJECTS,
    MAX_MODEL_SESSIONS,
    ProtocolError,
    parse_fleet,
    parse_presentation_plan,
    parse_session_action,
    parse_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "tests" / "fixtures" / "snapshot-v2.json"
PLAN = ROOT / "tests" / "fixtures" / "presentation-plan-v2.json"
V1 = ROOT / "tests" / "fixtures" / "snapshot-v1-mixed.json"
HOST_ID = "11111111-1111-4111-8111-111111111111"
PROJECT_ID = "22222222-2222-4222-8222-222222222222"
TASK_ID = "88888888-8888-4888-8888-888888888888"
CHECKOUT_ID = "44444444-4444-4444-8444-444444444444"
CLAUDE_KEY = f"{HOST_ID}:claude:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
REMOTE_HOST_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def fixture() -> dict[str, object]:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def fleet_fixture() -> dict[str, object]:
    local = fixture()
    remote = json.loads(encode(local).replace(HOST_ID, REMOTE_HOST_ID))
    remote["host"]["displayName"] = "snap"
    remote["generatedAt"] -= 10
    return {
        "schemaVersion": 2,
        "protocolVersion": 2,
        "fleetVersion": 1,
        "generatedAt": local["generatedAt"] + 1,
        "localHostId": HOST_ID,
        "hosts": [
            {
                "source": "local",
                "remoteName": None,
                "hostId": HOST_ID,
                "displayName": local["host"]["displayName"],
                "reachability": "online",
                "snapshotObservedAt": local["generatedAt"],
                "snapshotReceivedAt": local["generatedAt"] + 1,
                "lastAttemptAt": local["generatedAt"] + 1,
                "stale": False,
                "error": None,
                "snapshot": local,
            },
            {
                "source": "remote",
                "remoteName": "snap",
                "hostId": REMOTE_HOST_ID,
                "displayName": "snap",
                "reachability": "offline",
                "snapshotObservedAt": remote["generatedAt"],
                "snapshotReceivedAt": local["generatedAt"],
                "lastAttemptAt": local["generatedAt"] + 1,
                "stale": True,
                "error": {
                    "code": "ssh_failed",
                    "message": "The remote host is unavailable.",
                    "retryable": True,
                },
                "snapshot": remote,
            },
        ],
    }


class FleetProjectionTests(unittest.TestCase):
    def test_fleet_merges_projects_and_qualifies_host_owned_rows(self) -> None:
        model = parse_fleet(encode(fleet_fixture())).to_dict()
        self.assertEqual(model["modelVersion"], 4)
        self.assertEqual(model["sourceFleetVersion"], 1)
        self.assertEqual(len(model["hosts"]), 2)
        self.assertEqual(len(model["projects"]), 1)
        self.assertEqual(len(model["projects"][0]["routes"]), 2)
        self.assertEqual(
            {(task["hostId"], task["taskId"]) for task in model["tasks"]},
            {
                (HOST_ID, TASK_ID),
                (REMOTE_HOST_ID, TASK_ID),
                (HOST_ID, "99999999-9999-4999-8999-999999999999"),
                (REMOTE_HOST_ID, "99999999-9999-4999-8999-999999999999"),
            },
        )
        remote_task = next(
            task for task in model["tasks"] if task["hostId"] == REMOTE_HOST_ID
        )
        self.assertEqual(remote_task["hostReachability"], "offline")
        self.assertTrue(remote_task["hostStale"])
        self.assertIn("ssh_failed", {row["code"] for row in model["warnings"]})

    def test_fleet_accepts_never_seen_remote_without_inventing_rows(self) -> None:
        value = fleet_fixture()
        value["hosts"][1] = {
            "source": "remote",
            "remoteName": "snap",
            "hostId": None,
            "displayName": "snap",
            "reachability": "unknown",
            "snapshotObservedAt": None,
            "snapshotReceivedAt": None,
            "lastAttemptAt": None,
            "stale": True,
            "error": None,
            "snapshot": None,
        }
        model = parse_fleet(encode(value)).to_dict()
        self.assertEqual(len(model["hosts"]), 2)
        self.assertEqual(len(model["tasks"]), 2)
        self.assertFalse(any(task["hostId"] is None for task in model["tasks"]))

    def test_fleet_rejects_version_order_and_snapshot_identity_conflicts(self) -> None:
        for mutate in (
            lambda value: value.__setitem__("fleetVersion", 2),
            lambda value: value["hosts"].reverse(),
            lambda value: value["hosts"][1].__setitem__("hostId", HOST_ID),
            lambda value: value["hosts"][1].__setitem__(
                "snapshotObservedAt", value["hosts"][1]["snapshotObservedAt"] + 1
            ),
        ):
            value = fleet_fixture()
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(ProtocolError):
                parse_fleet(encode(value))

    def test_fleet_model_is_revalidated_after_routing_mutation(self) -> None:
        mutations = (
            lambda value: value["tasks"][0].__setitem__(
                "hostDisplayName", "wrong-host"
            ),
            lambda value: value["projects"][0]["routes"][0].__setitem__(
                "reachability", "offline"
            ),
            lambda value: value["warnings"].append({"message": "unroutable"}),
        )
        for mutate in mutations:
            model = parse_fleet(encode(fleet_fixture()))
            mutate(model.value)
            with self.subTest(mutate=mutate), self.assertRaises(ProtocolError):
                model.to_dict()


class PresentationPlanTests(unittest.TestCase):
    def test_switch_fixture_is_validated_and_projected(self) -> None:
        plan = parse_presentation_plan(PLAN.read_bytes())
        self.assertEqual(plan["kind"], "switch")
        self.assertEqual(plan["surfaceId"], "33333333-3333-4333-8333-333333333333")
        self.assertNotIn("futureField", plan)

    def test_v1_and_invalid_plan_shapes_are_rejected(self) -> None:
        value = json.loads(PLAN.read_text(encoding="utf-8"))
        value["schemaVersion"] = 1
        with self.assertRaisesRegex(ProtocolError, "expected 2"):
            parse_presentation_plan(encode(value))
        value["schemaVersion"] = 2
        del value["plan"]["tmuxClient"]
        with self.assertRaises(ProtocolError):
            parse_presentation_plan(encode(value))

    def test_blocked_plan_requires_only_a_structured_error(self) -> None:
        value = {
            "schemaVersion": 2,
            "protocolVersion": 2,
            "plan": {
                "kind": "blocked",
                "hostId": HOST_ID,
                "error": {
                    "code": "task_closed",
                    "message": "The task is closed.",
                    "scope": "project",
                    "retryable": False,
                    "observedAt": 100,
                },
            },
        }
        self.assertEqual(parse_presentation_plan(encode(value))["kind"], "blocked")
        value["plan"]["surfaceId"] = "33333333-3333-4333-8333-333333333333"
        with self.assertRaises(ProtocolError):
            parse_presentation_plan(encode(value))


class SessionActionTests(unittest.TestCase):
    def test_stop_action_v2_is_validated(self) -> None:
        value = {
            "schemaVersion": 2,
            "protocolVersion": 2,
            "action": {
                "kind": "stop",
                "status": "stopped",
                "hostId": HOST_ID,
                "sessionKey": CLAUDE_KEY,
            },
        }
        self.assertEqual(parse_session_action(encode(value))["status"], "stopped")
        value["action"]["sessionKey"] = value["action"]["sessionKey"].replace(
            ":claude:", ":codex:"
        )
        with self.assertRaises(ProtocolError):
            parse_session_action(encode(value))


class SnapshotProjectionTests(unittest.TestCase):
    def test_fixture_projects_task_first_model_v3(self) -> None:
        model = parse_snapshot(SNAPSHOT.read_bytes())
        value = model.to_dict()
        self.assertEqual(value["modelVersion"], 3)
        self.assertEqual(value["sourceSchemaVersion"], 2)
        self.assertEqual(value["sourceProtocolVersion"], 2)
        self.assertEqual(value["projects"][0]["defaultCheckoutId"], CHECKOUT_ID)
        self.assertEqual(
            [task["status"] for task in value["tasks"]], ["open", "closed"]
        )
        self.assertEqual(value["tasks"][0]["title"], "Refine the task picker")
        self.assertEqual(value["tasks"][0]["provider"], "codex")
        self.assertEqual(len(value["inboxSessions"]), 1)
        self.assertEqual(value["inboxSessions"][0]["provider"], "claude")
        self.assertTrue(value["inboxSessions"][0]["canStop"])

    def test_paths_and_git_private_identity_do_not_cross_model_boundary(self) -> None:
        value = fixture()
        value["checkouts"][0]["gitCommonDir"] = "/private/common"
        with self.assertRaisesRegex(ProtocolError, "sensitive"):
            parse_snapshot(encode(value))
        model_text = encode(parse_snapshot(SNAPSHOT.read_bytes()).to_dict())
        self.assertNotIn("/work/agent-switchboard", model_text)
        self.assertNotIn("gitCommonDir", model_text)

    def test_no_task_is_inferred_for_inbox_session(self) -> None:
        model = parse_snapshot(SNAPSHOT.read_bytes()).to_dict()
        self.assertEqual(model["inboxSessions"][0]["sessionKey"], CLAUDE_KEY)
        self.assertTrue(all(task["taskId"] != CLAUDE_KEY for task in model["tasks"]))

    def test_inbox_limit_is_structural_and_honest(self) -> None:
        value = fixture()
        template = copy.deepcopy(value["sessions"][1])
        template_surface = copy.deepcopy(value["surfaces"][1])
        for ordinal in range(1, 4):
            provider_id = f"00000000-0000-4000-8000-{ordinal:012d}"
            surface_id = f"10000000-0000-4000-8000-{ordinal:012d}"
            launch_id = f"20000000-0000-4000-8000-{ordinal:012d}"
            session = copy.deepcopy(template)
            session["providerSessionId"] = provider_id
            session["sessionKey"] = f"{HOST_ID}:claude:{provider_id}"
            session["surfaceId"] = surface_id
            session["lastObservedAt"] += ordinal
            surface = copy.deepcopy(template_surface)
            surface["surfaceId"] = surface_id
            surface["currentSessionKey"] = session["sessionKey"]
            surface["launchId"] = launch_id
            value["sessions"].append(session)
            value["surfaces"].append(surface)
        model = parse_snapshot(encode(value), max_sessions=2).to_dict()
        self.assertEqual(len(model["inboxSessions"]), 2)
        self.assertTrue(model["truncation"]["inboxTruncated"])
        self.assertEqual(model["warnings"][-1]["code"], "model_inbox_truncated")

    def test_degraded_capability_and_error_become_bounded_warnings(self) -> None:
        value = fixture()
        capability = value["capabilities"][0]
        capability["available"] = False
        capability["degradedReasons"] = [
            {
                "code": "version_untested",
                "message": "The installed version is outside the certified range.",
                "retryable": False,
            }
        ]
        value["errors"] = [
            {
                "code": "checkout_missing",
                "message": "The checkout is unavailable.",
                "scope": "project",
                "hostId": HOST_ID,
                "projectId": PROJECT_ID,
                "retryable": True,
                "observedAt": value["generatedAt"],
            }
        ]
        model = parse_snapshot(encode(value)).to_dict()
        self.assertEqual(model["capabilities"][0]["status"], "degraded")
        self.assertEqual(
            [warning["source"] for warning in model["warnings"]],
            ["capability", "error"],
        )

    def test_large_diagnostic_set_is_truncated_before_whole_model_limit(self) -> None:
        value = fixture()
        value["errors"] = [
            {
                "code": f"diagnostic_{ordinal}",
                "message": "x" * 4096,
                "scope": "host",
                "hostId": HOST_ID,
                "retryable": False,
                "observedAt": value["generatedAt"],
            }
            for ordinal in range(200)
        ]
        model = parse_snapshot(encode(value)).to_dict()
        self.assertLess(len(encode(model).encode("utf-8")), 4 * 1024 * 1024)
        self.assertEqual(model["warnings"][-1]["code"], "model_diagnostics_truncated")

    def test_large_project_set_is_structurally_bounded(self) -> None:
        value = fixture()
        template = copy.deepcopy(value["projects"][0])
        for ordinal in range(MAX_MODEL_PROJECTS + 10):
            project = copy.deepcopy(template)
            project["projectId"] = f"30000000-0000-4000-8000-{ordinal:012d}"
            project["name"] = f"Project {ordinal}"
            value["projects"].append(project)
        model = parse_snapshot(encode(value)).to_dict()
        self.assertEqual(len(model["projects"]), MAX_MODEL_PROJECTS)
        self.assertIn(
            "model_projects_truncated",
            {warning["code"] for warning in model["warnings"]},
        )

    def test_snapshot_v1_is_explicitly_incompatible(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "expected 2"):
            parse_snapshot(V1.read_bytes())


class SnapshotInvariantTests(unittest.TestCase):
    def assert_invalid(self, value: object, pattern: str | None = None) -> None:
        context = (
            self.assertRaisesRegex(ProtocolError, pattern)
            if pattern
            else self.assertRaises(ProtocolError)
        )
        with context:
            parse_snapshot(encode(value))

    def test_required_v2_collections_and_versions_are_strict(self) -> None:
        for collection in ("projectRepositories", "repositories", "checkouts", "tasks"):
            value = fixture()
            del value[collection]
            with self.subTest(collection=collection):
                self.assert_invalid(value, "required")
        value = fixture()
        value["protocolVersion"] = 1
        self.assert_invalid(value, "expected 2")

    def test_repository_membership_and_checkout_references_are_checked(self) -> None:
        value = fixture()
        value["projectRepositories"][0]["repositoryId"] = (
            "00000000-0000-4000-8000-000000000001"
        )
        self.assert_invalid(value, "unknown identity")
        value = fixture()
        value["checkouts"][0]["repositoryId"] = "00000000-0000-4000-8000-000000000001"
        self.assert_invalid(value, "not in repositories")

    def test_task_session_and_surface_backreferences_are_checked(self) -> None:
        value = fixture()
        value["tasks"][0]["currentSessionKey"] = CLAUDE_KEY
        self.assert_invalid(value, "backreference")
        value = fixture()
        value["sessions"][0]["taskId"] = None
        self.assert_invalid(value, "backreference")
        value = fixture()
        value["surfaces"][0]["currentSessionKey"] = CLAUDE_KEY
        self.assert_invalid(value, "host/provider")

    def test_uuid_status_timestamp_and_duplicate_identity_are_checked(self) -> None:
        value = fixture()
        value["tasks"][0]["taskId"] = "not-a-uuid"
        self.assert_invalid(value, "UUID")
        value = fixture()
        value["tasks"][0]["status"] = "paused"
        self.assert_invalid(value, "unsupported")
        value = fixture()
        value["sessions"][0]["lastObservedAt"] = (
            value["sessions"][0]["firstObservedAt"] - 1
        )
        self.assert_invalid(value, "reversed")
        value = fixture()
        value["tasks"].append(copy.deepcopy(value["tasks"][0]))
        self.assert_invalid(value, "duplicate taskId")

    def test_nil_identity_and_error_routing_disagreement_are_rejected(self) -> None:
        value = fixture()
        value["host"]["hostId"] = "00000000-0000-0000-0000-000000000000"
        self.assert_invalid(value, "nil UUID")
        value = fixture()
        value["errors"] = [
            {
                "code": "provider_mismatch",
                "message": "The route is inconsistent.",
                "scope": "session",
                "hostId": HOST_ID,
                "provider": "codex",
                "sessionKey": CLAUDE_KEY,
                "retryable": False,
                "observedAt": value["generatedAt"],
            }
        ]
        self.assert_invalid(value, "session/provider disagree")


class SnapshotBoundaryTests(unittest.TestCase):
    def test_size_depth_control_and_nonfinite_bounds(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_snapshot("{" + '"x":"' + "x" * MAX_JSON_BYTES + '"}')
        value = fixture()
        cursor = value
        for _ in range(40):
            cursor["future"] = {}
            cursor = cursor["future"]
        self.assert_invalid(value, "depth")
        value = fixture()
        for key in (
            "futurePrompt",
            "rawConversationDump",
            "futureResponseBody",
            "providerToken",
        ):
            candidate = copy.deepcopy(value)
            candidate[key] = "secret"
            with self.subTest(key=key):
                self.assert_invalid(candidate, "sensitive")
        value = fixture()
        value["generatedAt"] = math.inf
        with self.assertRaises(ValueError):
            encode(value)

    def assert_invalid(self, value: object, pattern: str) -> None:
        with self.assertRaisesRegex(ProtocolError, pattern):
            parse_snapshot(encode(value))

    def test_model_session_limit_is_strict(self) -> None:
        for limit in (0, MAX_MODEL_SESSIONS + 1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                parse_snapshot(SNAPSHOT.read_bytes(), max_sessions=limit)

    def test_final_model_is_revalidated_after_mutation(self) -> None:
        model = parse_snapshot(SNAPSHOT.read_bytes())
        model.tasks[0]["status"] = "paused"
        with self.assertRaises(ProtocolError):
            model.to_dict()
        model = parse_snapshot(SNAPSHOT.read_bytes())
        model.inbox_sessions[0]["sessionKey"] = "bad"
        with self.assertRaises(ProtocolError):
            model.to_dict()
        model = parse_snapshot(SNAPSHOT.read_bytes())
        model.warnings.append(
            {
                "source": "model",
                "code": "unsafe",
                "message": "unsafe",
                "retryable": False,
                "extra": "not public",
            }
        )
        with self.assertRaises(ProtocolError):
            model.to_dict()
        model = parse_snapshot(SNAPSHOT.read_bytes())
        model.truncation["emittedTaskCount"] += 1
        with self.assertRaises(ProtocolError):
            model.to_dict()


if __name__ == "__main__":
    unittest.main()
