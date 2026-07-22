"""Strict public contracts for the Switchboard DMS 0.5 entry adapter."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

MAX_JSON_BYTES: Final = 4 * 1024 * 1024
MAX_OUTPUT_BYTES: Final = 4 * 1024 * 1024
MAX_STRING_BYTES: Final = 64 * 1024
MAX_COLLECTION: Final = 20_000
MAX_NODES: Final = 100_000
MAX_DEPTH: Final = 32
MODEL_VERSION: Final = 1
BRIDGE_VERSION: Final = 1
_UUID_FIELDS: Final = frozenset(
    {
        "generationId",
        "localHostId",
        "hostId",
        "viewId",
        "activeFrameId",
        "activeProjectId",
        "projectId",
        "entryFrameId",
        "frameId",
        "parentFrameId",
        "recoveryId",
        "requestId",
    }
)
_FORBIDDEN_KEYS: Final = (
    "path",
    "prompt",
    "transcript",
    "argv",
    "ssh",
    "tmux",
    "pane",
    "processid",
    "sessionkey",
    "sessionid",
    "checkoutid",
    "repositoryid",
    "capability",
)


class ProtocolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("invalid_json", "JSON object repeats a key.")
        result[key] = value
    return result


def _decode(raw: bytes, *, label: str) -> dict[str, Any]:
    if len(raw) > MAX_JSON_BYTES:
        raise ProtocolError(f"{label}_overflow", f"{label} exceeded the byte limit.")
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if not raw or raw[:1].isspace() or raw[-1:].isspace():
        raise ProtocolError(f"{label}_invalid_json", f"{label} framing is invalid.")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except UnicodeDecodeError as error:
        raise ProtocolError(
            f"{label}_invalid_utf8", f"{label} is not UTF-8."
        ) from error
    except (json.JSONDecodeError, ValueError) as error:
        raise ProtocolError(
            f"{label}_invalid_json", f"{label} is not one JSON document."
        ) from error
    if not isinstance(value, dict):
        raise ProtocolError(f"{label}_invalid_protocol", f"{label} must be one object.")
    _validate_json_tree(value, label)
    return value


def _validate_json_tree(value: Any, label: str) -> None:
    stack: list[tuple[Any, int, str | None]] = [(value, 0, None)]
    nodes = 0
    while stack:
        item, depth, key = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ProtocolError(f"{label}_invalid_protocol", f"{label} is too complex.")
        if key is not None and any(
            token in key.casefold() for token in _FORBIDDEN_KEYS
        ):
            raise ProtocolError(
                f"{label}_invalid_protocol", f"{label} contains a forbidden field."
            )
        if item is None or isinstance(item, (bool, str, int, float)):
            if isinstance(item, str) and len(item.encode("utf-8")) > MAX_STRING_BYTES:
                raise ProtocolError(
                    f"{label}_invalid_protocol", f"{label} contains oversized text."
                )
            if isinstance(item, float) and not math.isfinite(item):
                raise ProtocolError(
                    f"{label}_invalid_protocol",
                    f"{label} contains a non-finite number.",
                )
            if (
                isinstance(item, int)
                and not isinstance(item, bool)
                and abs(item) > 2**63 - 1
            ):
                raise ProtocolError(
                    f"{label}_invalid_protocol",
                    f"{label} contains an out-of-range integer.",
                )
            continue
        if isinstance(item, list):
            if len(item) > MAX_COLLECTION:
                raise ProtocolError(
                    f"{label}_invalid_protocol", f"{label} contains too many records."
                )
            stack.extend((child, depth + 1, None) for child in item)
            continue
        if isinstance(item, dict):
            if len(item) > MAX_COLLECTION:
                raise ProtocolError(
                    f"{label}_invalid_protocol", f"{label} contains too many fields."
                )
            stack.extend(
                (child, depth + 1, str(child_key)) for child_key, child in item.items()
            )
            continue
        raise ProtocolError(
            f"{label}_invalid_protocol", f"{label} contains unsupported JSON."
        )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("navigator_invalid_protocol", f"{label} must be an object.")
    return value


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProtocolError("navigator_invalid_protocol", f"{label} must be an array.")
    return value


def _required(value: dict[str, Any], fields: set[str], label: str) -> None:
    if not fields <= set(value):
        raise ProtocolError(
            "navigator_invalid_protocol", f"{label} is missing required fields."
        )


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(
            "navigator_invalid_protocol", f"{label} must be a nonnegative integer."
        )
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolError("navigator_invalid_protocol", f"{label} must be boolean.")
    return value


def _string(value: Any, label: str, *, maximum: int = 1024, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not empty and not value)
        or len(value.encode()) > maximum
        or "\x00" in value
    ):
        raise ProtocolError("navigator_invalid_protocol", f"{label} is invalid text.")
    return value


def _uuid(value: Any, label: str) -> str:
    text = _string(value, label, maximum=36)
    try:
        parsed = UUID(text)
    except ValueError as error:
        raise ProtocolError(
            "navigator_invalid_protocol", f"{label} is not a UUID."
        ) from error
    if parsed.int == 0 or str(parsed) != text:
        raise ProtocolError(
            "navigator_invalid_protocol", f"{label} is not a canonical UUID."
        )
    return text


def _optional_uuid(value: Any, label: str) -> str | None:
    return None if value is None else _uuid(value, label)


def _enum(value: Any, values: set[str], label: str) -> str:
    text = _string(value, label, maximum=64)
    if text not in values:
        raise ProtocolError("navigator_invalid_protocol", f"{label} is unsupported.")
    return text


def _failure(value: Any, label: str) -> dict[str, Any]:
    record = _object(value, label)
    _required(record, {"code", "message", "retryable"}, label)
    return {
        "code": _string(record["code"], f"{label}.code", maximum=64),
        "message": _string(record["message"], f"{label}.message", maximum=1024),
        "retryable": _boolean(record["retryable"], f"{label}.retryable"),
    }


def _host(value: Any, index: int) -> dict[str, Any]:
    label = f"hosts[{index}]"
    record = _object(value, label)
    fields = {
        "hostId",
        "generationId",
        "displayName",
        "isLocal",
        "reachability",
        "stale",
        "generatedAt",
        "activationState",
    }
    _required(record, fields, label)
    return {
        "hostId": _uuid(record["hostId"], f"{label}.hostId"),
        "generationId": _uuid(record["generationId"], f"{label}.generationId"),
        "displayName": _string(
            record["displayName"], f"{label}.displayName", maximum=256
        ),
        "isLocal": _boolean(record["isLocal"], f"{label}.isLocal"),
        "reachability": _enum(
            record["reachability"],
            {"online", "offline", "unknown"},
            f"{label}.reachability",
        ),
        "stale": _boolean(record["stale"], f"{label}.stale"),
        "generatedAt": _integer(record["generatedAt"], f"{label}.generatedAt"),
        "activationState": _enum(
            record["activationState"],
            {"cutover_staged", "committed"},
            f"{label}.activationState",
        ),
    }


def _view(value: Any, index: int) -> dict[str, Any]:
    label = f"views[{index}]"
    record = _object(value, label)
    fields = {
        "hostId",
        "viewId",
        "mode",
        "state",
        "revision",
        "activeFrameId",
        "activeProjectId",
        "title",
        "breadcrumb",
        "activity",
        "attention",
        "transitionState",
        "controlState",
        "lastActivityAt",
    }
    _required(record, fields, label)
    breadcrumb = [
        _string(item, f"{label}.breadcrumb", maximum=256)
        for item in _array(record["breadcrumb"], f"{label}.breadcrumb")
    ]
    if len(breadcrumb) > 32:
        raise ProtocolError(
            "navigator_invalid_protocol", "view breadcrumb is too deep."
        )
    return {
        "hostId": _uuid(record["hostId"], f"{label}.hostId"),
        "viewId": _uuid(record["viewId"], f"{label}.viewId"),
        "mode": _enum(record["mode"], {"navigator", "direct"}, f"{label}.mode"),
        "state": _enum(
            record["state"],
            {"ready", "transitioning", "degraded", "retired"},
            f"{label}.state",
        ),
        "revision": _integer(record["revision"], f"{label}.revision"),
        "activeFrameId": _optional_uuid(
            record["activeFrameId"], f"{label}.activeFrameId"
        ),
        "activeProjectId": _optional_uuid(
            record["activeProjectId"], f"{label}.activeProjectId"
        ),
        "title": _string(record["title"], f"{label}.title", maximum=256),
        "breadcrumb": breadcrumb,
        "activity": _enum(
            record["activity"],
            {"working", "needs_input", "ready", "unknown"},
            f"{label}.activity",
        ),
        "attention": _string(record["attention"], f"{label}.attention", maximum=32),
        "transitionState": None
        if record["transitionState"] is None
        else _string(record["transitionState"], f"{label}.transitionState", maximum=32),
        "controlState": None
        if record["controlState"] is None
        else _string(record["controlState"], f"{label}.controlState", maximum=32),
        "lastActivityAt": None
        if record["lastActivityAt"] is None
        else _integer(record["lastActivityAt"], f"{label}.lastActivityAt"),
    }


def _frame(value: Any, label: str) -> dict[str, Any]:
    record = _object(value, label)
    _required(
        record,
        {"frameId", "title", "role", "parentFrameId", "lifecycleState", "activity"},
        label,
    )
    return {
        "frameId": _uuid(record["frameId"], f"{label}.frameId"),
        "title": _string(record["title"], f"{label}.title", maximum=256),
        "role": _enum(record["role"], {"workspace", "task"}, f"{label}.role"),
        "parentFrameId": _optional_uuid(
            record["parentFrameId"], f"{label}.parentFrameId"
        ),
        "lifecycleState": _enum(
            record["lifecycleState"],
            {"open", "closing", "closed"},
            f"{label}.lifecycleState",
        ),
        "activity": _enum(
            record["activity"],
            {"working", "needs_input", "ready", "unknown"},
            f"{label}.activity",
        ),
    }


def _project(value: Any, index: int) -> dict[str, Any]:
    label = f"projects[{index}]"
    record = _object(value, label)
    _required(
        record,
        {"hostId", "projectId", "name", "viewId", "entryFrameId", "frames"},
        label,
    )
    frames = [
        _frame(item, f"{label}.frames[{offset}]")
        for offset, item in enumerate(_array(record["frames"], f"{label}.frames"))
    ]
    frame_ids = {item["frameId"] for item in frames}
    entry = _optional_uuid(record["entryFrameId"], f"{label}.entryFrameId")
    if entry is not None and entry not in frame_ids:
        raise ProtocolError(
            "navigator_invalid_protocol", "project entry frame is missing."
        )
    if any(
        item["parentFrameId"] is not None and item["parentFrameId"] not in frame_ids
        for item in frames
    ):
        raise ProtocolError(
            "navigator_invalid_protocol", "project frame parent is missing."
        )
    return {
        "hostId": _uuid(record["hostId"], f"{label}.hostId"),
        "projectId": _uuid(record["projectId"], f"{label}.projectId"),
        "name": _string(record["name"], f"{label}.name", maximum=256),
        "viewId": _optional_uuid(record["viewId"], f"{label}.viewId"),
        "entryFrameId": entry,
        "frames": frames,
    }


def _recovery(value: Any, index: int) -> dict[str, Any]:
    label = f"recoveries[{index}]"
    record = _object(value, label)
    fields = {
        "recoveryId",
        "hostId",
        "kind",
        "subjectType",
        "subjectId",
        "actionability",
        "state",
        "explanation",
        "createdAt",
        "updatedAt",
    }
    _required(record, fields, label)
    return {
        "recoveryId": _uuid(record["recoveryId"], f"{label}.recoveryId"),
        "hostId": _uuid(record["hostId"], f"{label}.hostId"),
        "kind": _string(record["kind"], f"{label}.kind", maximum=64),
        "subjectType": _string(
            record["subjectType"], f"{label}.subjectType", maximum=64
        ),
        "subjectId": _string(record["subjectId"], f"{label}.subjectId", maximum=512),
        "actionability": _enum(
            record["actionability"],
            {"safe_auto", "open_view", "manual"},
            f"{label}.actionability",
        ),
        "state": _enum(
            record["state"], {"open", "resolved", "dismissed"}, f"{label}.state"
        ),
        "explanation": _string(
            record["explanation"], f"{label}.explanation", maximum=1024
        ),
        "createdAt": _integer(record["createdAt"], f"{label}.createdAt"),
        "updatedAt": _integer(record["updatedAt"], f"{label}.updatedAt"),
    }


def _warning(value: Any, index: int) -> dict[str, Any]:
    label = f"warnings[{index}]"
    record = _object(value, label)
    _required(record, {"code", "message", "hostId", "subjectType", "subjectId"}, label)
    return {
        "code": _string(record["code"], f"{label}.code", maximum=64),
        "message": _string(record["message"], f"{label}.message", maximum=1024),
        "hostId": _optional_uuid(record["hostId"], f"{label}.hostId"),
        "subjectType": None
        if record["subjectType"] is None
        else _string(record["subjectType"], f"{label}.subjectType", maximum=64),
        "subjectId": None
        if record["subjectId"] is None
        else _string(record["subjectId"], f"{label}.subjectId", maximum=512),
    }


def _truncation(value: Any) -> dict[str, Any]:
    record = _object(value, "truncation")
    result: dict[str, Any] = {}
    for key, raw_counts in record.items():
        name = _string(key, "truncation key", maximum=128)
        counts = _object(raw_counts, f"truncation.{name}")
        _required(counts, {"retainedCount", "emittedCount"}, f"truncation.{name}")
        retained = _integer(counts["retainedCount"], f"truncation.{name}.retainedCount")
        emitted = _integer(counts["emittedCount"], f"truncation.{name}.emittedCount")
        if emitted > retained:
            raise ProtocolError(
                "navigator_invalid_protocol", "truncation counts are invalid."
            )
        result[name] = {"retainedCount": retained, "emittedCount": emitted}
    return dict(sorted(result.items()))


@dataclass(frozen=True, slots=True)
class EntryModel:
    value: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.to_json())

    def to_json(self) -> str:
        return json.dumps(
            self.value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )


def parse_navigator(raw: bytes) -> EntryModel:
    source = _decode(raw, label="navigator")
    observed_versions = (
        source.get("schemaVersion"),
        source.get("protocolVersion"),
        source.get("navigatorVersion"),
    )
    if observed_versions != (1, 1, 1):
        raise ProtocolError(
            "navigator_incompatible_generation",
            "DMS 0.5 requires NavigatorState v1 from core 0.3.",
        )
    required = {
        "schemaVersion",
        "protocolVersion",
        "navigatorVersion",
        "generationId",
        "generatedAt",
        "localHostId",
        "hosts",
        "views",
        "projects",
        "recoveries",
        "warnings",
        "truncation",
    }
    _required(source, required, "navigator")
    generation = _uuid(source["generationId"], "generationId")
    local_host = _uuid(source["localHostId"], "localHostId")
    generated_at = _integer(source["generatedAt"], "generatedAt")
    hosts = [
        _host(item, index)
        for index, item in enumerate(_array(source["hosts"], "hosts"))
    ]
    if hosts != sorted(hosts, key=lambda item: item["hostId"]):
        raise ProtocolError(
            "navigator_invalid_protocol", "hosts are not canonically ordered."
        )
    by_host = {item["hostId"]: item for item in hosts}
    if (
        len(by_host) != len(hosts)
        or local_host not in by_host
        or sum(item["isLocal"] for item in hosts) != 1
        or not by_host[local_host]["isLocal"]
        or by_host[local_host]["generationId"] != generation
    ):
        raise ProtocolError(
            "navigator_invalid_protocol", "local host identity is inconsistent."
        )
    views = [
        _view(item, index)
        for index, item in enumerate(_array(source["views"], "views"))
    ]
    projects = [
        _project(item, index)
        for index, item in enumerate(_array(source["projects"], "projects"))
    ]
    recoveries = [
        _recovery(item, index)
        for index, item in enumerate(_array(source["recoveries"], "recoveries"))
    ]
    warnings = [
        _warning(item, index)
        for index, item in enumerate(_array(source["warnings"], "warnings"))
    ]
    for collection, keys in (
        (views, ("hostId", "viewId")),
        (projects, ("hostId", "projectId")),
        (recoveries, ("hostId", "recoveryId")),
    ):
        if collection != sorted(
            collection, key=lambda item: tuple(item[key] for key in keys)
        ):
            raise ProtocolError(
                "navigator_invalid_protocol",
                "navigator rows are not canonically ordered.",
            )
        if any(item["hostId"] not in by_host for item in collection):
            raise ProtocolError(
                "navigator_invalid_protocol", "navigator row owner is missing."
            )
    by_view = {(item["hostId"], item["viewId"]) for item in views}
    if any(
        item["viewId"] is not None and (item["hostId"], item["viewId"]) not in by_view
        for item in projects
    ):
        raise ProtocolError(
            "navigator_invalid_protocol", "project view reference is invalid."
        )
    model = {
        "modelVersion": MODEL_VERSION,
        "sourceNavigatorVersion": 1,
        "sourceGenerationId": generation,
        "generatedAt": generated_at,
        "localHostId": local_host,
        "hosts": hosts,
        "views": views,
        "projects": projects,
        "recoveries": recoveries,
        "warnings": warnings,
        "truncation": _truncation(source["truncation"]),
    }
    return EntryModel(model)


@dataclass(frozen=True, slots=True)
class Directive:
    value: dict[str, Any]


def parse_directive(raw: bytes, *, host_id: str, request_id: str) -> Directive:
    source = _decode(raw, label="directive")
    _required(source, {"directiveVersion", "requestId", "hostId", "kind"}, "directive")
    if source["directiveVersion"] != 1:
        raise ProtocolError(
            "directive_incompatible_generation",
            "DMS 0.5 requires PresentationDirective v1.",
        )
    requested_host = _uuid(host_id, "requested host")
    requested_id = _uuid(request_id, "requested request")
    if (
        _uuid(source["hostId"], "directive.hostId") != requested_host
        or _uuid(source["requestId"], "directive.requestId") != requested_id
    ):
        raise ProtocolError(
            "directive_identity_mismatch",
            "Core returned a directive for another request or host.",
        )
    kind = _enum(source["kind"], {"focus", "attach", "blocked"}, "directive.kind")
    if kind == "blocked":
        if (
            any(
                source.get(key) is not None
                for key in ("viewId", "viewRevision", "desktopToken", "leaseExpiresAt")
            )
            or source.get("error") is None
        ):
            raise ProtocolError(
                "directive_invalid_protocol",
                "Blocked directive fields are inconsistent.",
            )
        value = {
            "directiveVersion": 1,
            "requestId": requested_id,
            "hostId": requested_host,
            "kind": kind,
            "error": _failure(source["error"], "directive.error"),
        }
    else:
        required = {"viewId", "viewRevision", "desktopToken"}
        _required(source, required, "directive")
        lease = source.get("leaseExpiresAt")
        if (kind == "attach") != (lease is not None) or source.get("error") is not None:
            raise ProtocolError(
                "directive_invalid_protocol",
                "Presentation directive fields are inconsistent.",
            )
        value = {
            "directiveVersion": 1,
            "requestId": requested_id,
            "hostId": requested_host,
            "kind": kind,
            "viewId": _uuid(source["viewId"], "directive.viewId"),
            "viewRevision": _integer(source["viewRevision"], "directive.viewRevision"),
            "desktopToken": _string(
                source["desktopToken"], "directive.desktopToken", maximum=256
            ),
        }
        if lease is not None:
            value["leaseExpiresAt"] = _integer(lease, "directive.leaseExpiresAt")
    return Directive(value)


def parse_core_error(raw: bytes) -> dict[str, Any]:
    source = _decode(raw, label="core_error")
    if set(source) != {"error"}:
        raise ProtocolError(
            "core_error_invalid_protocol", "Core returned an invalid error record."
        )
    error = _object(source["error"], "core_error.error")
    if set(error) != {"code", "message"}:
        raise ProtocolError(
            "core_error_invalid_protocol", "Core returned an invalid error record."
        )
    return {
        "code": _string(error["code"], "core_error.error.code", maximum=64),
        "message": _string(error["message"], "core_error.error.message", maximum=1024),
        "retryable": False,
    }


def success_envelope(model: EntryModel) -> bytes:
    payload = (
        json.dumps(
            {"bridgeVersion": BRIDGE_VERSION, "ok": True, "model": model.to_dict()},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    if len(payload) > MAX_OUTPUT_BYTES:
        raise ProtocolError(
            "bridge_output_overflow", "Bridge output exceeded the byte limit."
        )
    return payload


def failure_envelope(code: str, message: str, *, retryable: bool) -> bytes:
    payload = {
        "bridgeVersion": BRIDGE_VERSION,
        "ok": False,
        "error": {
            "code": _string(code, "error.code", maximum=64),
            "message": _string(message, "error.message", maximum=1024),
            "retryable": bool(retryable),
        },
    }
    return (
        json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        + b"\n"
    )


__all__ = [
    "BRIDGE_VERSION",
    "EntryModel",
    "MAX_JSON_BYTES",
    "ProtocolError",
    "Directive",
    "failure_envelope",
    "parse_directive",
    "parse_core_error",
    "parse_navigator",
    "success_envelope",
]
