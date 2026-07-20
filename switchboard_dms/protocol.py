"""Bounded Fleet/Snapshot validation and DMS-facing model projection.

This module deliberately duplicates only Agent Switchboard's public JSON
contract.  It never imports core internals, reads the registry, invokes Git, or
parses provider-owned data.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import UUID

SCHEMA_VERSION = 2
PROTOCOL_VERSION = 2
MODEL_VERSION = 3
FLEET_VERSION = 1
FLEET_MODEL_VERSION = 4
MAX_FLEET_HOSTS = 33

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_STRING_LENGTH = 64 * 1024
MAX_JSON_ARRAY_ITEMS = 100_000
MAX_JSON_OBJECT_KEYS = 256
MAX_SNAPSHOT_RECORDS = 100_000
MAX_MODEL_SESSIONS = 1_000
MAX_MODEL_TASKS = 1_000
MAX_MODEL_PROJECTS = 1_000
MAX_MODEL_BYTES = 4 * 1024 * 1024
MAX_MODEL_PROJECT_BYTES = 512 * 1024
MAX_MODEL_TASK_BYTES = 1280 * 1024
MAX_MODEL_INBOX_BYTES = 768 * 1024
MAX_MODEL_WARNING_BYTES = 512 * 1024
MAX_MODEL_WARNINGS = 256

_PROVIDERS = frozenset({"codex", "claude"})
_TRANSPORTS = frozenset({"tmux"})
_REPOSITORY_KINDS = frozenset({"git", "directory"})
_CHECKOUT_KINDS = frozenset({"main", "worktree", "directory"})
_TASK_STATUSES = frozenset({"open", "closed"})
_RUNTIME_PRESENCE = frozenset({"live", "stopped", "unknown"})
_RESUMABILITY = frozenset({"resumable", "missing", "unknown"})
_ACTIVITY = frozenset({"working", "needs_input", "ready", "completed", "unknown"})
_ACTIVITY_REASON = frozenset(
    {
        "permission",
        "question",
        "elicitation",
        "turn_complete",
        "provider_complete",
        "error",
        "unknown",
    }
)
_ATTACHMENT = frozenset({"attached", "detached", "none", "unknown"})
_STATE_CONFIDENCE = frozenset({"confirmed", "inferred", "unknown"})
_SURFACE_ROLES = frozenset({"session", "provider_manager"})
_BINDING_CONFIDENCE = frozenset({"confirmed", "unknown"})
_ERROR_SCOPES = frozenset(
    {"host", "project", "provider", "session", "launch", "surface"}
)
_PRESENTATION_PLAN_KINDS = frozenset({"focus", "switch", "attach", "blocked"})
_SESSION_ACTION_STATUSES = frozenset({"stopped", "already_stopped", "blocked"})

_KEY_NORMALIZER = re.compile(r"[^a-z0-9]")
_SENSITIVE_KEY_PARTS = (
    "accesskey",
    "apikey",
    "argv",
    "authorization",
    "cookie",
    "credential",
    "environment",
    "history",
    "input",
    "modelresponse",
    "password",
    "passphrase",
    "privatekey",
    "prompt",
    "refreshtoken",
    "accesstoken",
    "authtoken",
    "secret",
    "toolresult",
)
_SENSITIVE_KEYS = frozenset(
    {
        "body",
        "content",
        "conversation",
        "absolutegitdir",
        "gitadministrativedir",
        "gitcommondir",
        "hookpayload",
        "messages",
        "modeloutput",
        "output",
        "payload",
        "prompt",
        "providerpayload",
        "rawpayload",
        "requestpayload",
        "responsepayload",
        "stderr",
        "stdin",
        "stdout",
        "systemprompt",
        "tooloutput",
        "transcript",
        "userprompt",
        "worktreeadmindir",
    }
)
_SAFE_DETAIL_FIELDS = frozenset(
    {
        "capability",
        "emittedCount",
        "fallback",
        "latency",
        "payloadHash",
        "projectId",
        "repositoryId",
        "retainedCount",
    }
)


class ProtocolError(ValueError):
    """A public Switchboard document is malformed, unsafe, or incompatible."""


def _reject_constant(value: str) -> None:
    raise ProtocolError(f"non-finite JSON number {value!r} is not supported")


def _normalized_key(value: str) -> str:
    return _KEY_NORMALIZER.sub("", unicodedata.normalize("NFKC", value).casefold())


def _reject_sensitive_key(value: str, path: str) -> None:
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ProtocolError(f"{path} contains a terminal control in an object key")
    normalized = _normalized_key(value)
    if not normalized or len(value) > 256:
        raise ProtocolError(f"{path} contains an invalid object key")
    if normalized in _SENSITIVE_KEYS or any(
        part in normalized for part in _SENSITIVE_KEY_PARTS
    ):
        raise ProtocolError(f"{path} contains sensitive field {value!r}")
    if "prompt" in normalized or "transcript" in normalized:
        raise ProtocolError(f"{path} contains sensitive field {value!r}")
    if any(
        part in normalized
        for part in ("conversation", "messages", "output", "response", "result")
    ):
        raise ProtocolError(f"{path} contains sensitive field {value!r}")
    if normalized.startswith("raw"):
        raise ProtocolError(f"{path} contains sensitive field {value!r}")
    if "payload" in normalized and not normalized.endswith("payloadhash"):
        raise ProtocolError(f"{path} contains sensitive field {value!r}")
    if "token" in normalized and normalized != "desktoptoken":
        raise ProtocolError(f"{path} contains sensitive field {value!r}")


def _validate_json_tree(value: object, path: str = "envelope", depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ProtocolError(f"{path} exceeds the JSON depth limit")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if value < 0 or value > 2**63 - 1:
            raise ProtocolError(f"{path} integer is outside the supported range")
        return
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0:
            raise ProtocolError(f"{path} number is outside the supported range")
        return
    if isinstance(value, str):
        if len(value) > MAX_JSON_STRING_LENGTH:
            raise ProtocolError(f"{path} string exceeds the supported limit")
        controls = [char for char in value if unicodedata.category(char) == "Cc"]
        if controls and not (
            path.endswith(".purpose") and all(char in "\n\t" for char in controls)
        ):
            raise ProtocolError(f"{path} contains terminal control characters")
        return
    if isinstance(value, list):
        if len(value) > MAX_JSON_ARRAY_ITEMS:
            raise ProtocolError(f"{path} contains too many array items")
        for index, item in enumerate(value):
            _validate_json_tree(item, f"{path}[{index}]", depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_JSON_OBJECT_KEYS:
            raise ProtocolError(f"{path} contains too many object keys")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError(f"{path} contains a non-string key")
            _reject_sensitive_key(key, path)
            _validate_json_tree(item, f"{path}.{key}", depth + 1)
        return
    raise ProtocolError(f"{path} contains unsupported JSON data")


def _decode(raw: str | bytes | bytearray) -> dict[str, Any]:
    try:
        size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
    except (AttributeError, UnicodeEncodeError) as exc:
        raise ProtocolError("protocol message must be UTF-8 JSON") from exc
    if size > MAX_JSON_BYTES:
        raise ProtocolError(f"protocol message exceeds the {MAX_JSON_BYTES}-byte limit")
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    _validate_json_tree(value)
    return _object(value, "envelope")


def _object(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{path} must be an object")
    return value


def _array(
    value: object, path: str, *, maximum: int = MAX_JSON_ARRAY_ITEMS
) -> list[Any]:
    if not isinstance(value, list):
        raise ProtocolError(f"{path} must be an array")
    if len(value) > maximum:
        raise ProtocolError(f"{path} contains too many records")
    return value


def _required(table: dict[str, Any], key: str, path: str) -> Any:
    if key not in table:
        raise ProtocolError(f"{path}.{key} is required")
    return table[key]


def _string(value: object, path: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ProtocolError(f"{path} must be a nonempty bounded string")
    return value


def _optional_string(
    table: dict[str, Any], key: str, path: str, *, maximum: int
) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    return _string(value, f"{path}.{key}", maximum=maximum)


def _integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(f"{path} must be a nonnegative integer")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolError(f"{path} must be a boolean")
    return value


def _enum(value: object, path: str, allowed: frozenset[str]) -> str:
    result = _string(value, path, maximum=64)
    if result not in allowed:
        raise ProtocolError(f"{path} contains an unsupported value")
    return result


def _uuid(value: object, path: str) -> str:
    text = _string(value, path, maximum=64)
    try:
        parsed = UUID(text)
    except ValueError as exc:
        raise ProtocolError(f"{path} must be a UUID") from exc
    if parsed.int == 0:
        raise ProtocolError(f"{path} must not be a nil UUID")
    return str(parsed)


def _optional_uuid(table: dict[str, Any], key: str, path: str) -> str | None:
    value = table.get(key)
    return None if value is None else _uuid(value, f"{path}.{key}")


def _provider(value: object, path: str) -> str:
    return _enum(value, path, _PROVIDERS)


def _session_key(value: object, path: str) -> tuple[str, str, str, str]:
    text = _string(value, path, maximum=256)
    parts = text.split(":")
    if len(parts) != 3:
        raise ProtocolError(f"{path} must be a canonical session key")
    host_id = _uuid(parts[0], f"{path}.hostId")
    provider = _provider(parts[1], f"{path}.provider")
    provider_id = _uuid(parts[2], f"{path}.providerSessionId")
    return f"{host_id}:{provider}:{provider_id}", host_id, provider, provider_id


def _versions(table: dict[str, Any]) -> None:
    schema = _integer(
        _required(table, "schemaVersion", "envelope"), "envelope.schemaVersion"
    )
    protocol = _integer(
        _required(table, "protocolVersion", "envelope"), "envelope.protocolVersion"
    )
    if schema != SCHEMA_VERSION:
        raise ProtocolError(
            f"schema version {schema} is not supported; expected {SCHEMA_VERSION}"
        )
    if protocol != PROTOCOL_VERSION:
        raise ProtocolError(
            f"protocol version {protocol} is not supported; expected {PROTOCOL_VERSION}"
        )


def _string_array(
    value: object, path: str, *, maximum: int, maximum_string: int
) -> list[str]:
    items = _array(value, path, maximum=maximum)
    return [
        _string(item, f"{path}[{index}]", maximum=maximum_string)
        for index, item in enumerate(items)
    ]


def _details(value: object, path: str) -> dict[str, Any]:
    table = _object(value, path)
    if not set(table).issubset(_SAFE_DETAIL_FIELDS):
        raise ProtocolError(f"{path} contains unsupported detail fields")
    result: dict[str, Any] = {}
    for key, value in table.items():
        item_path = f"{path}.{key}"
        if key in {"projectId", "repositoryId"}:
            result[key] = _uuid(value, item_path)
        elif key in {"emittedCount", "retainedCount"}:
            result[key] = _integer(value, item_path)
        elif key == "latency":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
            ):
                raise ProtocolError(f"{item_path} must be a nonnegative number")
            result[key] = value
        elif key == "payloadHash":
            text = _string(value, item_path, maximum=128)
            if not re.fullmatch(r"[0-9a-f]{64}", text):
                raise ProtocolError(f"{item_path} must be a SHA-256 digest")
            result[key] = text
        else:
            result[key] = _string(value, item_path, maximum=256)
    if (
        "emittedCount" in result
        and "retainedCount" in result
        and result["emittedCount"] > result["retainedCount"]
    ):
        raise ProtocolError(f"{path}.emittedCount must not exceed retainedCount")
    return result


def _host_record(value: object) -> dict[str, Any]:
    table = _object(value, "envelope.host")
    return {
        "hostId": _uuid(
            _required(table, "hostId", "envelope.host"), "envelope.host.hostId"
        ),
        "displayName": _string(
            _required(table, "displayName", "envelope.host"),
            "envelope.host.displayName",
            maximum=256,
        ),
    }


def _project_record(value: object, path: str) -> dict[str, Any]:
    table = _object(value, path)
    result: dict[str, Any] = {
        "projectId": _uuid(_required(table, "projectId", path), f"{path}.projectId"),
        "name": _string(_required(table, "name", path), f"{path}.name", maximum=256),
    }
    result["aliases"] = (
        _string_array(
            table["aliases"], f"{path}.aliases", maximum=128, maximum_string=128
        )
        if "aliases" in table
        else []
    )
    result["defaultProvider"] = (
        None
        if table.get("defaultProvider") is None
        else _provider(table["defaultProvider"], f"{path}.defaultProvider")
    )
    result["defaultTransport"] = (
        "tmux"
        if table.get("defaultTransport") is None
        else _enum(table["defaultTransport"], f"{path}.defaultTransport", _TRANSPORTS)
    )
    result["declared"] = (
        True
        if "declared" not in table
        else _boolean(table["declared"], f"{path}.declared")
    )
    return result


def _membership_record(value: object, path: str) -> dict[str, Any]:
    table = _object(value, path)
    return {
        "projectId": _uuid(_required(table, "projectId", path), f"{path}.projectId"),
        "repositoryId": _uuid(
            _required(table, "repositoryId", path), f"{path}.repositoryId"
        ),
        "isPrimary": _boolean(_required(table, "isPrimary", path), f"{path}.isPrimary"),
    }


def _repository_record(value: object, path: str) -> dict[str, Any]:
    table = _object(value, path)
    return {
        "repositoryId": _uuid(
            _required(table, "repositoryId", path), f"{path}.repositoryId"
        ),
        "name": _string(_required(table, "name", path), f"{path}.name", maximum=256),
        "kind": _enum(
            _required(table, "kind", path), f"{path}.kind", _REPOSITORY_KINDS
        ),
        "contextSources": (
            _string_array(
                table["contextSources"],
                f"{path}.contextSources",
                maximum=256,
                maximum_string=1024,
            )
            if "contextSources" in table
            else []
        ),
    }


def _checkout_record(value: object, path: str, host_id: str) -> dict[str, Any]:
    table = _object(value, path)
    record_host = _uuid(_required(table, "hostId", path), f"{path}.hostId")
    if record_host != host_id:
        raise ProtocolError(f"{path}.hostId does not match envelope.host.hostId")
    return {
        "checkoutId": _uuid(_required(table, "checkoutId", path), f"{path}.checkoutId"),
        "repositoryId": _uuid(
            _required(table, "repositoryId", path), f"{path}.repositoryId"
        ),
        "hostId": record_host,
        "path": _string(_required(table, "path", path), f"{path}.path", maximum=4096),
        "kind": _enum(_required(table, "kind", path), f"{path}.kind", _CHECKOUT_KINDS),
        "displayName": _optional_string(table, "displayName", path, maximum=256),
        "branch": _optional_string(table, "branch", path, maximum=1024),
        "providerOverride": (
            None
            if table.get("providerOverride") is None
            else _provider(table["providerOverride"], f"{path}.providerOverride")
        ),
        "transportOverride": (
            None
            if table.get("transportOverride") is None
            else _enum(
                table["transportOverride"], f"{path}.transportOverride", _TRANSPORTS
            )
        ),
        "isDefault": False
        if "isDefault" not in table
        else _boolean(table["isDefault"], f"{path}.isDefault"),
        "declared": False
        if "declared" not in table
        else _boolean(table["declared"], f"{path}.declared"),
        "present": True
        if "present" not in table
        else _boolean(table["present"], f"{path}.present"),
    }


def _task_record(value: object, path: str, host_id: str) -> dict[str, Any]:
    table = _object(value, path)
    record_host = _uuid(_required(table, "hostId", path), f"{path}.hostId")
    if record_host != host_id:
        raise ProtocolError(f"{path}.hostId does not match envelope.host.hostId")
    status = _enum(_required(table, "status", path), f"{path}.status", _TASK_STATUSES)
    closed_at = (
        None
        if table.get("closedAt") is None
        else _integer(table["closedAt"], f"{path}.closedAt")
    )
    if (status == "closed") != (closed_at is not None):
        raise ProtocolError(f"{path}.closedAt disagrees with task status")
    current = table.get("currentSessionKey")
    return {
        "taskId": _uuid(_required(table, "taskId", path), f"{path}.taskId"),
        "hostId": record_host,
        "projectId": _uuid(_required(table, "projectId", path), f"{path}.projectId"),
        "checkoutId": _optional_uuid(table, "checkoutId", path),
        "title": _string(_required(table, "title", path), f"{path}.title", maximum=256),
        "purpose": _optional_string(table, "purpose", path, maximum=4096),
        "preferredProvider": (
            None
            if table.get("preferredProvider") is None
            else _provider(table["preferredProvider"], f"{path}.preferredProvider")
        ),
        "status": status,
        "pinned": _boolean(_required(table, "pinned", path), f"{path}.pinned"),
        "currentSessionKey": None
        if current is None
        else _session_key(current, f"{path}.currentSessionKey")[0],
        "createdAt": _integer(_required(table, "createdAt", path), f"{path}.createdAt"),
        "updatedAt": _integer(_required(table, "updatedAt", path), f"{path}.updatedAt"),
        "closedAt": closed_at,
    }


def _session_record(value: object, path: str, host_id: str) -> dict[str, Any]:
    table = _object(value, path)
    session_key, key_host, key_provider, key_provider_id = _session_key(
        _required(table, "sessionKey", path), f"{path}.sessionKey"
    )
    record_host = _uuid(_required(table, "hostId", path), f"{path}.hostId")
    provider = _provider(_required(table, "provider", path), f"{path}.provider")
    provider_id = _uuid(
        _required(table, "providerSessionId", path), f"{path}.providerSessionId"
    )
    if record_host != host_id or key_host != host_id:
        raise ProtocolError(f"{path} belongs to a different host")
    if provider != key_provider or provider_id != key_provider_id:
        raise ProtocolError(f"{path} identity fields disagree with sessionKey")
    first = _integer(
        _required(table, "firstObservedAt", path), f"{path}.firstObservedAt"
    )
    last = _integer(_required(table, "lastObservedAt", path), f"{path}.lastObservedAt")
    if last < first:
        raise ProtocolError(f"{path} observation timestamps are reversed")
    result: dict[str, Any] = {
        "sessionKey": session_key,
        "hostId": record_host,
        "provider": provider,
        "providerSessionId": provider_id,
        "projectId": _optional_uuid(table, "projectId", path),
        "taskId": _optional_uuid(table, "taskId", path),
        "checkoutId": _optional_uuid(table, "checkoutId", path),
        "name": _optional_string(table, "name", path, maximum=512),
        "purpose": _optional_string(table, "purpose", path, maximum=4096),
        "firstObservedAt": first,
        "lastObservedAt": last,
        "metadataSource": _string(
            _required(table, "metadataSource", path),
            f"{path}.metadataSource",
            maximum=64,
        ),
        "runtimePresence": _enum(
            _required(table, "runtimePresence", path),
            f"{path}.runtimePresence",
            _RUNTIME_PRESENCE,
        ),
        "resumability": _enum(
            _required(table, "resumability", path),
            f"{path}.resumability",
            _RESUMABILITY,
        ),
        "activity": _enum(
            _required(table, "activity", path), f"{path}.activity", _ACTIVITY
        ),
        "activityReason": _enum(
            _required(table, "activityReason", path),
            f"{path}.activityReason",
            _ACTIVITY_REASON,
        ),
        "attachment": _enum(
            _required(table, "attachment", path), f"{path}.attachment", _ATTACHMENT
        ),
        "stateConfidence": _enum(
            _required(table, "stateConfidence", path),
            f"{path}.stateConfidence",
            _STATE_CONFIDENCE,
        ),
        "surfaceId": _optional_uuid(table, "surfaceId", path),
        "pinned": False
        if "pinned" not in table
        else _boolean(table["pinned"], f"{path}.pinned"),
    }
    for key in (
        "createdAt",
        "providerUpdatedAt",
        "lastActivityAt",
        "stateObservedAt",
        "wrappedAt",
    ):
        result[key] = (
            None if table.get(key) is None else _integer(table[key], f"{path}.{key}")
        )
    return result


def _runtime_record(value: object, path: str, host_id: str) -> dict[str, Any]:
    table = _object(value, path)
    record_host = _uuid(_required(table, "hostId", path), f"{path}.hostId")
    provider = _provider(_required(table, "provider", path), f"{path}.provider")
    if record_host != host_id:
        raise ProtocolError(f"{path}.hostId does not match envelope.host.hostId")
    session_key = None
    if table.get("sessionKey") is not None:
        session_key, key_host, key_provider, _ = _session_key(
            table["sessionKey"], f"{path}.sessionKey"
        )
        if key_host != host_id or key_provider != provider:
            raise ProtocolError(f"{path}.sessionKey does not match host/provider")
    result: dict[str, Any] = {
        "hostId": record_host,
        "provider": provider,
        "sessionKey": session_key,
        "runtimePresence": _enum(
            _required(table, "runtimePresence", path),
            f"{path}.runtimePresence",
            _RUNTIME_PRESENCE,
        ),
        "resumability": _enum(
            _required(table, "resumability", path),
            f"{path}.resumability",
            _RESUMABILITY,
        ),
        "activity": _enum(
            _required(table, "activity", path), f"{path}.activity", _ACTIVITY
        ),
        "activityReason": _enum(
            _required(table, "activityReason", path),
            f"{path}.activityReason",
            _ACTIVITY_REASON,
        ),
        "attachment": _enum(
            _required(table, "attachment", path), f"{path}.attachment", _ATTACHMENT
        ),
        "observedAt": _integer(
            _required(table, "observedAt", path), f"{path}.observedAt"
        ),
    }
    if table.get("pid") is not None:
        pid = _integer(table["pid"], f"{path}.pid")
        if pid == 0:
            raise ProtocolError(f"{path}.pid must be positive")
        result["pid"] = pid
    return result


def _surface_record(value: object, path: str, host_id: str) -> dict[str, Any]:
    table = _object(value, path)
    record_host = _uuid(_required(table, "hostId", path), f"{path}.hostId")
    provider = _provider(_required(table, "provider", path), f"{path}.provider")
    if record_host != host_id:
        raise ProtocolError(f"{path}.hostId does not match envelope.host.hostId")
    current = None
    if table.get("currentSessionKey") is not None:
        current, key_host, key_provider, _ = _session_key(
            table["currentSessionKey"], f"{path}.currentSessionKey"
        )
        if key_host != host_id or key_provider != provider:
            raise ProtocolError(
                f"{path}.currentSessionKey does not match host/provider"
            )
    created_at = _integer(_required(table, "createdAt", path), f"{path}.createdAt")
    last_observed_at = _integer(
        _required(table, "lastObservedAt", path), f"{path}.lastObservedAt"
    )
    if last_observed_at < created_at:
        raise ProtocolError(f"{path} observation timestamps are reversed")
    retired_at = (
        None
        if table.get("retiredAt") is None
        else _integer(table["retiredAt"], f"{path}.retiredAt")
    )
    if retired_at is not None and not created_at <= retired_at <= last_observed_at:
        raise ProtocolError(f"{path}.retiredAt is outside the observation lifetime")
    result = {
        "surfaceId": _uuid(_required(table, "surfaceId", path), f"{path}.surfaceId"),
        "hostId": record_host,
        "provider": provider,
        "transport": _enum(
            _required(table, "transport", path), f"{path}.transport", _TRANSPORTS
        ),
        "transportLocator": _string(
            _required(table, "transportLocator", path),
            f"{path}.transportLocator",
            maximum=4096,
        ),
        "role": _enum(_required(table, "role", path), f"{path}.role", _SURFACE_ROLES),
        "currentSessionKey": current,
        "bindingConfidence": _enum(
            _required(table, "bindingConfidence", path),
            f"{path}.bindingConfidence",
            _BINDING_CONFIDENCE,
        ),
        "launchId": _optional_uuid(table, "launchId", path),
        "createdAt": created_at,
        "lastObservedAt": last_observed_at,
        "clientAttached": _boolean(
            _required(table, "clientAttached", path), f"{path}.clientAttached"
        ),
        "retiredAt": retired_at,
    }
    if retired_at is not None and (
        current is not None
        or result["bindingConfidence"] != "unknown"
        or result["clientAttached"]
    ):
        raise ProtocolError(f"{path} retired surface is still bound or attached")
    return result


def _degradation(value: object, path: str) -> dict[str, Any]:
    table = _object(value, path)
    result: dict[str, Any] = {
        "code": _string(_required(table, "code", path), f"{path}.code", maximum=128),
        "message": _string(
            _required(table, "message", path), f"{path}.message", maximum=2048
        ),
        "retryable": _boolean(_required(table, "retryable", path), f"{path}.retryable"),
    }
    if table.get("feature") is not None:
        result["feature"] = _string(table["feature"], f"{path}.feature", maximum=256)
    if table.get("details") is not None:
        result["details"] = _details(table["details"], f"{path}.details")
    return result


def _capability_record(value: object, path: str) -> dict[str, Any]:
    table = _object(value, path)
    contract = _object(
        _required(table, "testedContractRange", path), f"{path}.testedContractRange"
    )
    reasons = [
        _degradation(item, f"{path}.degradedReasons[{index}]")
        for index, item in enumerate(
            _array(
                _required(table, "degradedReasons", path),
                f"{path}.degradedReasons",
                maximum=256,
            )
        )
    ]
    available = _boolean(_required(table, "available", path), f"{path}.available")
    if not available and not reasons:
        raise ProtocolError(f"{path}.degradedReasons must explain unavailable provider")
    return {
        "provider": _provider(_required(table, "provider", path), f"{path}.provider"),
        "available": available,
        "providerVersion": _optional_string(
            table, "providerVersion", path, maximum=256
        ),
        "testedContractRange": {
            "minimum": _string(
                _required(contract, "minimum", f"{path}.testedContractRange"),
                f"{path}.testedContractRange.minimum",
                maximum=256,
            ),
            "maximum": _string(
                _required(contract, "maximum", f"{path}.testedContractRange"),
                f"{path}.testedContractRange.maximum",
                maximum=256,
            ),
        },
        "features": _string_array(
            _required(table, "features", path),
            f"{path}.features",
            maximum=256,
            maximum_string=256,
        ),
        "degradedReasons": reasons,
    }


def _error_record(value: object, path: str) -> dict[str, Any]:
    table = _object(value, path)
    result: dict[str, Any] = {
        "code": _string(_required(table, "code", path), f"{path}.code", maximum=128),
        "message": _string(
            _required(table, "message", path), f"{path}.message", maximum=4096
        ),
        "scope": _enum(_required(table, "scope", path), f"{path}.scope", _ERROR_SCOPES),
        "retryable": _boolean(_required(table, "retryable", path), f"{path}.retryable"),
        "observedAt": _integer(
            _required(table, "observedAt", path), f"{path}.observedAt"
        ),
    }
    for key in (
        "hostId",
        "projectId",
        "repositoryId",
        "checkoutId",
        "taskId",
        "launchId",
        "surfaceId",
    ):
        if table.get(key) is not None:
            result[key] = _uuid(table[key], f"{path}.{key}")
    if table.get("provider") is not None:
        result["provider"] = _provider(table["provider"], f"{path}.provider")
    if table.get("sessionKey") is not None:
        result["sessionKey"] = _session_key(table["sessionKey"], f"{path}.sessionKey")[
            0
        ]
    if table.get("details") is not None:
        result["details"] = _details(table["details"], f"{path}.details")
    return result


def parse_presentation_plan(raw: str | bytes | bytearray) -> dict[str, Any]:
    """Validate and project one public PresentationPlan v2 envelope."""

    table = _decode(raw)
    _versions(table)
    path = "envelope.plan"
    source = _object(_required(table, "plan", "envelope"), path)
    kind = _enum(
        _required(source, "kind", path), f"{path}.kind", _PRESENTATION_PLAN_KINDS
    )
    result: dict[str, Any] = {
        "kind": kind,
        "hostId": _uuid(_required(source, "hostId", path), f"{path}.hostId"),
    }
    if source.get("surfaceId") is not None:
        result["surfaceId"] = _uuid(source["surfaceId"], f"{path}.surfaceId")
    for key, maximum in (
        ("workspaceId", 1024),
        ("tmuxTarget", 2048),
        ("tmuxClient", 1024),
        ("desktopToken", 2048),
    ):
        if source.get(key) is not None:
            result[key] = _string(source[key], f"{path}.{key}", maximum=maximum)
    if source.get("leaseExpiresAt") is not None:
        result["leaseExpiresAt"] = _integer(
            source["leaseExpiresAt"], f"{path}.leaseExpiresAt"
        )
    if source.get("error") is not None:
        result["error"] = _error_record(source["error"], f"{path}.error")
    locators = (
        "surfaceId",
        "workspaceId",
        "tmuxTarget",
        "tmuxClient",
        "desktopToken",
        "leaseExpiresAt",
    )
    if kind == "blocked":
        if "error" not in result or any(field in result for field in locators):
            raise ProtocolError(f"{path} blocked plan shape is invalid")
        return result
    if "error" in result or "surfaceId" not in result:
        raise ProtocolError(f"{path} executable plan shape is invalid")
    if kind == "focus":
        if "desktopToken" not in result or any(
            field in result for field in ("tmuxTarget", "tmuxClient", "leaseExpiresAt")
        ):
            raise ProtocolError(f"{path} focus plan shape is invalid")
    elif kind == "switch":
        if "tmuxTarget" not in result or "tmuxClient" not in result:
            raise ProtocolError(f"{path} switch plan shape is invalid")
    elif kind == "attach":
        if "tmuxTarget" not in result or "tmuxClient" in result:
            raise ProtocolError(f"{path} attach plan shape is invalid")
    return result


def parse_session_action(raw: str | bytes | bytearray) -> dict[str, Any]:
    """Validate and project one public Claude stop-action v2 envelope."""

    table = _decode(raw)
    _versions(table)
    path = "envelope.action"
    source = _object(_required(table, "action", "envelope"), path)
    if _string(_required(source, "kind", path), f"{path}.kind", maximum=16) != "stop":
        raise ProtocolError(f"{path}.kind is unsupported")
    status = _enum(
        _required(source, "status", path), f"{path}.status", _SESSION_ACTION_STATUSES
    )
    host_id = _uuid(_required(source, "hostId", path), f"{path}.hostId")
    session_key, key_host, provider, _ = _session_key(
        _required(source, "sessionKey", path), f"{path}.sessionKey"
    )
    if key_host != host_id or provider != "claude":
        raise ProtocolError(f"{path} is not a host-local Claude identity")
    result: dict[str, Any] = {
        "kind": "stop",
        "status": status,
        "hostId": host_id,
        "sessionKey": session_key,
    }
    if source.get("error") is not None:
        result["error"] = _error_record(source["error"], f"{path}.error")
    if status == "blocked" and "error" not in result:
        raise ProtocolError(f"{path} blocked action requires an error")
    if status != "blocked" and "error" in result:
        raise ProtocolError(f"{path} successful action contains an error")
    return result


def _unique(records: Iterable[dict[str, Any]], key: str, collection: str) -> set[str]:
    values = [str(record[key]) for record in records]
    if len(values) != len(set(values)):
        raise ProtocolError(f"envelope.{collection} contains duplicate {key} values")
    return set(values)


def _validated_snapshot(raw: str | bytes | bytearray) -> dict[str, Any]:
    table = _decode(raw)
    _versions(table)
    generated_at = _integer(
        _required(table, "generatedAt", "envelope"), "envelope.generatedAt"
    )
    host = _host_record(_required(table, "host", "envelope"))
    host_id = host["hostId"]

    def records(name: str) -> list[Any]:
        return _array(
            _required(table, name, "envelope"),
            f"envelope.{name}",
            maximum=MAX_SNAPSHOT_RECORDS,
        )

    projects = [
        _project_record(item, f"envelope.projects[{index}]")
        for index, item in enumerate(records("projects"))
    ]
    memberships = [
        _membership_record(item, f"envelope.projectRepositories[{index}]")
        for index, item in enumerate(records("projectRepositories"))
    ]
    repositories = [
        _repository_record(item, f"envelope.repositories[{index}]")
        for index, item in enumerate(records("repositories"))
    ]
    checkouts = [
        _checkout_record(item, f"envelope.checkouts[{index}]", host_id)
        for index, item in enumerate(records("checkouts"))
    ]
    tasks = [
        _task_record(item, f"envelope.tasks[{index}]", host_id)
        for index, item in enumerate(records("tasks"))
    ]
    sessions = [
        _session_record(item, f"envelope.sessions[{index}]", host_id)
        for index, item in enumerate(records("sessions"))
    ]
    runtimes = [
        _runtime_record(item, f"envelope.runtimes[{index}]", host_id)
        for index, item in enumerate(records("runtimes"))
    ]
    surfaces = [
        _surface_record(item, f"envelope.surfaces[{index}]", host_id)
        for index, item in enumerate(records("surfaces"))
    ]
    capabilities = [
        _capability_record(item, f"envelope.capabilities[{index}]")
        for index, item in enumerate(records("capabilities"))
    ]
    errors = [
        _error_record(item, f"envelope.errors[{index}]")
        for index, item in enumerate(records("errors"))
    ]

    project_ids = _unique(projects, "projectId", "projects")
    repository_ids = _unique(repositories, "repositoryId", "repositories")
    _unique(checkouts, "checkoutId", "checkouts")
    _unique(tasks, "taskId", "tasks")
    session_keys = _unique(sessions, "sessionKey", "sessions")
    surface_ids = _unique(surfaces, "surfaceId", "surfaces")
    providers = [capability["provider"] for capability in capabilities]
    if len(providers) != len(set(providers)):
        raise ProtocolError("envelope contains duplicate provider capabilities")

    membership_keys = {
        (item["projectId"], item["repositoryId"]) for item in memberships
    }
    if len(membership_keys) != len(memberships):
        raise ProtocolError(
            "envelope contains duplicate project repository memberships"
        )
    primary_counts: dict[str, int] = {}
    for index, membership in enumerate(memberships):
        if (
            membership["projectId"] not in project_ids
            or membership["repositoryId"] not in repository_ids
        ):
            raise ProtocolError(
                f"envelope.projectRepositories[{index}] references unknown identity"
            )
        primary_counts[membership["projectId"]] = primary_counts.get(
            membership["projectId"], 0
        ) + int(membership["isPrimary"])
    for project_id in project_ids:
        if (
            any(key[0] == project_id for key in membership_keys)
            and primary_counts.get(project_id) != 1
        ):
            raise ProtocolError(
                f"envelope project {project_id} requires one primary repository"
            )

    checkouts_by_id = {item["checkoutId"]: item for item in checkouts}
    for index, checkout in enumerate(checkouts):
        if checkout["repositoryId"] not in repository_ids:
            raise ProtocolError(
                f"envelope.checkouts[{index}].repositoryId is not in repositories"
            )
    tasks_by_id = {item["taskId"]: item for item in tasks}
    for index, task in enumerate(tasks):
        if task["projectId"] not in project_ids:
            raise ProtocolError(f"envelope.tasks[{index}].projectId is not in projects")
        checkout = checkouts_by_id.get(task["checkoutId"])
        if task["checkoutId"] is not None and (
            checkout is None
            or (task["projectId"], checkout["repositoryId"]) not in membership_keys
        ):
            raise ProtocolError(f"envelope.tasks[{index}] checkout/project disagree")

    sessions_by_key = {item["sessionKey"]: item for item in sessions}
    for index, session in enumerate(sessions):
        project_id = session["projectId"]
        checkout = checkouts_by_id.get(session["checkoutId"])
        task = tasks_by_id.get(session["taskId"])
        if project_id is not None and project_id not in project_ids:
            raise ProtocolError(
                f"envelope.sessions[{index}].projectId is not in projects"
            )
        if session["checkoutId"] is not None and (
            checkout is None
            or project_id is None
            or (project_id, checkout["repositoryId"]) not in membership_keys
        ):
            raise ProtocolError(f"envelope.sessions[{index}] checkout/project disagree")
        if session["taskId"] is not None and (
            task is None
            or task["projectId"] != project_id
            or task["checkoutId"] != session["checkoutId"]
        ):
            raise ProtocolError(f"envelope.sessions[{index}] task context disagrees")
        if session["surfaceId"] is not None and session["surfaceId"] not in surface_ids:
            raise ProtocolError(
                f"envelope.sessions[{index}].surfaceId is not in surfaces"
            )
    for index, task in enumerate(tasks):
        current = task["currentSessionKey"]
        if current is not None and (
            current not in session_keys
            or sessions_by_key[current]["taskId"] != task["taskId"]
        ):
            raise ProtocolError(
                f"envelope.tasks[{index}] current session backreference disagrees"
            )
    for collection_name, collection, key in (
        ("runtimes", runtimes, "sessionKey"),
        ("surfaces", surfaces, "currentSessionKey"),
    ):
        for index, record in enumerate(collection):
            if record[key] is not None and record[key] not in session_keys:
                raise ProtocolError(
                    f"envelope.{collection_name}[{index}].{key} is not in sessions"
                )
    surfaces_by_id = {item["surfaceId"]: item for item in surfaces}
    for index, session in enumerate(sessions):
        surface_id = session["surfaceId"]
        if (
            surface_id is not None
            and surfaces_by_id[surface_id]["currentSessionKey"] != session["sessionKey"]
        ):
            raise ProtocolError(
                f"envelope.sessions[{index}] surface binding is inconsistent"
            )
    for index, surface in enumerate(surfaces):
        current = surface["currentSessionKey"]
        if (
            current is not None
            and sessions_by_key[current]["surfaceId"] != surface["surfaceId"]
        ):
            raise ProtocolError(
                f"envelope.surfaces[{index}] session binding is inconsistent"
            )
    for index, error in enumerate(errors):
        if error.get("hostId") not in {None, host_id}:
            raise ProtocolError(
                f"envelope.errors[{index}].hostId belongs to another host"
            )
        if error.get("sessionKey") is not None:
            _, error_host, error_provider, _ = _session_key(
                error["sessionKey"], f"envelope.errors[{index}].sessionKey"
            )
            if error_host != host_id:
                raise ProtocolError(
                    f"envelope.errors[{index}].sessionKey belongs to another host"
                )
            if error.get("provider") not in {None, error_provider}:
                raise ProtocolError(
                    f"envelope.errors[{index}] session/provider disagree"
                )

    return {
        "generatedAt": generated_at,
        "host": host,
        "projects": projects,
        "projectRepositories": memberships,
        "repositories": repositories,
        "checkouts": checkouts,
        "tasks": tasks,
        "sessions": sessions,
        "runtimes": runtimes,
        "surfaces": surfaces,
        "capabilities": capabilities,
        "errors": errors,
    }


def _recency(session: dict[str, Any]) -> int:
    for key in ("lastActivityAt", "providerUpdatedAt", "createdAt", "lastObservedAt"):
        if session.get(key) is not None:
            return int(session[key])
    raise AssertionError("validated sessions contain lastObservedAt")


def _can_stop(session: dict[str, Any], surfaces: dict[str, dict[str, Any]]) -> bool:
    surface = surfaces.get(session["surfaceId"])
    return bool(
        session["provider"] == "claude"
        and session["runtimePresence"] == "live"
        and surface is not None
        and surface["transport"] == "tmux"
        and surface["role"] == "session"
        and surface["currentSessionKey"] == session["sessionKey"]
        and surface["bindingConfidence"] == "confirmed"
        and surface["launchId"] is not None
        and surface["retiredAt"] is None
    )


def _session_state(
    session: dict[str, Any], surfaces: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    return {
        "provider": session["provider"],
        "sessionKey": session["sessionKey"],
        "runtimePresence": session["runtimePresence"],
        "resumability": session["resumability"],
        "activity": session["activity"],
        "activityReason": session["activityReason"],
        "attachment": session["attachment"],
        "stateConfidence": session["stateConfidence"],
        "recencyAt": _recency(session),
        "canStop": _can_stop(session, surfaces),
    }


def _bounded(
    items: list[dict[str, Any]], *, count: int, byte_limit: int
) -> tuple[list[dict[str, Any]], bool]:
    result: list[dict[str, Any]] = []
    for item in items[:count]:
        candidate = result + [item]
        if (
            len(
                json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            > byte_limit
        ):
            break
        result.append(item)
    return result, len(result) != len(items)


def _adapt_capabilities(source: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_provider = {item["provider"]: item for item in source}
    result: list[dict[str, Any]] = []
    for provider in ("codex", "claude"):
        capability = by_provider.get(provider)
        if capability is None:
            result.append(
                {
                    "provider": provider,
                    "status": "neutral",
                    "available": None,
                    "features": [],
                    "degradedReasons": [],
                }
            )
        else:
            result.append(
                {
                    "provider": provider,
                    "status": "available"
                    if capability["available"] and not capability["degradedReasons"]
                    else "degraded",
                    "available": capability["available"],
                    "features": capability["features"][:64],
                    "degradedReasons": capability["degradedReasons"][:64],
                }
            )
    return result


def _validate_model(value: object) -> dict[str, Any]:
    _validate_json_tree(value, "model")
    table = _object(value, "model")
    required = {
        "modelVersion",
        "sourceSchemaVersion",
        "sourceProtocolVersion",
        "generatedAt",
        "host",
        "projects",
        "tasks",
        "inboxSessions",
        "capabilities",
        "warnings",
        "truncation",
    }
    if set(table) != required:
        raise ProtocolError("model contains missing or unknown fields")
    for key, expected in (
        ("modelVersion", MODEL_VERSION),
        ("sourceSchemaVersion", SCHEMA_VERSION),
        ("sourceProtocolVersion", PROTOCOL_VERSION),
    ):
        if _integer(table[key], f"model.{key}") != expected:
            raise ProtocolError(f"model.{key} is incompatible")
    _integer(table["generatedAt"], "model.generatedAt")
    host = _object(table["host"], "model.host")
    host_id = _uuid(_required(host, "hostId", "model.host"), "model.host.hostId")
    _string(
        _required(host, "displayName", "model.host"),
        "model.host.displayName",
        maximum=256,
    )
    projects = _array(table["projects"], "model.projects", maximum=MAX_MODEL_PROJECTS)
    tasks = _array(table["tasks"], "model.tasks", maximum=MAX_MODEL_TASKS)
    inbox = _array(
        table["inboxSessions"], "model.inboxSessions", maximum=MAX_MODEL_SESSIONS
    )
    _array(table["capabilities"], "model.capabilities", maximum=2)
    _array(table["warnings"], "model.warnings", maximum=MAX_MODEL_WARNINGS)
    _object(table["truncation"], "model.truncation")
    project_ids: set[str] = set()
    for index, item in enumerate(projects):
        row = _object(item, f"model.projects[{index}]")
        if set(row) != {
            "projectId",
            "name",
            "repositoryName",
            "defaultProvider",
            "defaultCheckoutId",
        }:
            raise ProtocolError("model project contains missing or unknown fields")
        project_id = _uuid(
            _required(row, "projectId", f"model.projects[{index}]"),
            f"model.projects[{index}].projectId",
        )
        if project_id in project_ids:
            raise ProtocolError("model.projects contains duplicate projectId values")
        project_ids.add(project_id)
        _string(
            _required(row, "name", f"model.projects[{index}]"),
            f"model.projects[{index}].name",
            maximum=256,
        )
        _optional_string(row, "repositoryName", f"model.projects[{index}]", maximum=256)
        _provider(
            _required(row, "defaultProvider", f"model.projects[{index}]"),
            f"model.projects[{index}].defaultProvider",
        )
        _optional_uuid(row, "defaultCheckoutId", f"model.projects[{index}]")
    task_ids: set[str] = set()
    for index, item in enumerate(tasks):
        row = _object(item, f"model.tasks[{index}]")
        expected_task_fields = {
            "taskId",
            "projectId",
            "projectName",
            "checkoutId",
            "checkoutName",
            "checkoutKind",
            "checkoutBranch",
            "checkoutIsDefault",
            "title",
            "purpose",
            "preferredProvider",
            "status",
            "pinned",
            "currentSessionKey",
            "createdAt",
            "updatedAt",
            "closedAt",
            "provider",
            "runtimePresence",
            "resumability",
            "activity",
            "activityReason",
            "attachment",
            "stateConfidence",
            "recencyAt",
            "canStop",
        }
        if set(row) != expected_task_fields:
            raise ProtocolError("model task contains missing or unknown fields")
        task_id = _uuid(
            _required(row, "taskId", f"model.tasks[{index}]"),
            f"model.tasks[{index}].taskId",
        )
        if (
            task_id in task_ids
            or _uuid(
                _required(row, "projectId", f"model.tasks[{index}]"),
                f"model.tasks[{index}].projectId",
            )
            not in project_ids
        ):
            raise ProtocolError("model task identities are inconsistent")
        task_ids.add(task_id)
        _string(
            _required(row, "title", f"model.tasks[{index}]"),
            f"model.tasks[{index}].title",
            maximum=256,
        )
        _string(
            _required(row, "projectName", f"model.tasks[{index}]"),
            f"model.tasks[{index}].projectName",
            maximum=256,
        )
        _optional_uuid(row, "checkoutId", f"model.tasks[{index}]")
        _optional_string(row, "checkoutName", f"model.tasks[{index}]", maximum=256)
        if row["checkoutKind"] is not None:
            _enum(
                row["checkoutKind"],
                f"model.tasks[{index}].checkoutKind",
                _CHECKOUT_KINDS,
            )
        _optional_string(row, "checkoutBranch", f"model.tasks[{index}]", maximum=1024)
        _boolean(row["checkoutIsDefault"], f"model.tasks[{index}].checkoutIsDefault")
        _optional_string(row, "purpose", f"model.tasks[{index}]", maximum=4096)
        if row["preferredProvider"] is not None:
            _provider(
                row["preferredProvider"], f"model.tasks[{index}].preferredProvider"
            )
        status = _enum(
            _required(row, "status", f"model.tasks[{index}]"),
            f"model.tasks[{index}].status",
            _TASK_STATUSES,
        )
        _boolean(row["pinned"], f"model.tasks[{index}].pinned")
        if row["currentSessionKey"] is not None:
            _, current_host, current_provider, _ = _session_key(
                row["currentSessionKey"], f"model.tasks[{index}].currentSessionKey"
            )
            if current_host != host_id or row["provider"] != current_provider:
                raise ProtocolError("model task current session identity disagrees")
        elif row["provider"] is not None:
            raise ProtocolError("model task provider requires a current session")
        _integer(row["createdAt"], f"model.tasks[{index}].createdAt")
        _integer(row["updatedAt"], f"model.tasks[{index}].updatedAt")
        if row["closedAt"] is not None:
            _integer(row["closedAt"], f"model.tasks[{index}].closedAt")
        if (status == "closed") != (row["closedAt"] is not None):
            raise ProtocolError("model task closedAt disagrees with status")
        if row["provider"] is not None:
            _provider(row["provider"], f"model.tasks[{index}].provider")
        _enum(
            row["runtimePresence"],
            f"model.tasks[{index}].runtimePresence",
            _RUNTIME_PRESENCE,
        )
        _enum(row["resumability"], f"model.tasks[{index}].resumability", _RESUMABILITY)
        _enum(row["activity"], f"model.tasks[{index}].activity", _ACTIVITY)
        _enum(
            row["activityReason"],
            f"model.tasks[{index}].activityReason",
            _ACTIVITY_REASON,
        )
        _enum(row["attachment"], f"model.tasks[{index}].attachment", _ATTACHMENT)
        _enum(
            row["stateConfidence"],
            f"model.tasks[{index}].stateConfidence",
            _STATE_CONFIDENCE,
        )
        _integer(row["recencyAt"], f"model.tasks[{index}].recencyAt")
        _boolean(row["canStop"], f"model.tasks[{index}].canStop")
        if row["canStop"] and (
            row["provider"] != "claude" or row["runtimePresence"] != "live"
        ):
            raise ProtocolError("model task stop capability is inconsistent")
    session_keys: set[str] = set()
    for index, item in enumerate(inbox):
        row = _object(item, f"model.inboxSessions[{index}]")
        expected_inbox_fields = {
            "sessionKey",
            "providerSessionId",
            "provider",
            "projectId",
            "projectName",
            "checkoutId",
            "checkoutName",
            "name",
            "runtimePresence",
            "resumability",
            "activity",
            "activityReason",
            "attachment",
            "stateConfidence",
            "recencyAt",
            "canStop",
        }
        if set(row) != expected_inbox_fields:
            raise ProtocolError(
                "model Inbox session contains missing or unknown fields"
            )
        key, key_host, key_provider, key_provider_id = _session_key(
            _required(row, "sessionKey", f"model.inboxSessions[{index}]"),
            f"model.inboxSessions[{index}].sessionKey",
        )
        if key_host != host_id or key in session_keys:
            raise ProtocolError("model Inbox session identities are inconsistent")
        session_keys.add(key)
        provider_id = _uuid(
            row["providerSessionId"], f"model.inboxSessions[{index}].providerSessionId"
        )
        provider = _provider(row["provider"], f"model.inboxSessions[{index}].provider")
        if provider != key_provider or provider_id != key_provider_id:
            raise ProtocolError("model Inbox session identity fields disagree")
        _optional_uuid(row, "projectId", f"model.inboxSessions[{index}]")
        _optional_string(
            row, "projectName", f"model.inboxSessions[{index}]", maximum=256
        )
        _optional_uuid(row, "checkoutId", f"model.inboxSessions[{index}]")
        _optional_string(
            row, "checkoutName", f"model.inboxSessions[{index}]", maximum=256
        )
        _optional_string(row, "name", f"model.inboxSessions[{index}]", maximum=512)
        _enum(
            row["runtimePresence"],
            f"model.inboxSessions[{index}].runtimePresence",
            _RUNTIME_PRESENCE,
        )
        _enum(
            row["resumability"],
            f"model.inboxSessions[{index}].resumability",
            _RESUMABILITY,
        )
        _enum(row["activity"], f"model.inboxSessions[{index}].activity", _ACTIVITY)
        _enum(
            row["activityReason"],
            f"model.inboxSessions[{index}].activityReason",
            _ACTIVITY_REASON,
        )
        _enum(
            row["attachment"], f"model.inboxSessions[{index}].attachment", _ATTACHMENT
        )
        _enum(
            row["stateConfidence"],
            f"model.inboxSessions[{index}].stateConfidence",
            _STATE_CONFIDENCE,
        )
        _integer(row["recencyAt"], f"model.inboxSessions[{index}].recencyAt")
        _boolean(row["canStop"], f"model.inboxSessions[{index}].canStop")
        if row["canStop"] and (
            provider != "claude" or row["runtimePresence"] != "live"
        ):
            raise ProtocolError("model Inbox stop capability is inconsistent")
    capabilities = _array(table["capabilities"], "model.capabilities", maximum=2)
    if len(capabilities) != 2:
        raise ProtocolError("model capabilities must contain both providers")
    for index, provider in enumerate(("codex", "claude")):
        capability = _object(capabilities[index], f"model.capabilities[{index}]")
        if (
            set(capability)
            != {"provider", "status", "available", "features", "degradedReasons"}
            or capability["provider"] != provider
        ):
            raise ProtocolError("model capability order or fields are invalid")
        status = _enum(
            capability["status"],
            f"model.capabilities[{index}].status",
            frozenset({"available", "degraded", "neutral"}),
        )
        available = capability["available"]
        if available is not None:
            _boolean(available, f"model.capabilities[{index}].available")
        features = _array(
            capability["features"], f"model.capabilities[{index}].features", maximum=64
        )
        for feature_index, feature in enumerate(features):
            _string(
                feature,
                f"model.capabilities[{index}].features[{feature_index}]",
                maximum=256,
            )
        reasons = _array(
            capability["degradedReasons"],
            f"model.capabilities[{index}].degradedReasons",
            maximum=64,
        )
        for reason_index, reason in enumerate(reasons):
            reason_path = f"model.capabilities[{index}].degradedReasons[{reason_index}]"
            reason_table = _object(reason, reason_path)
            if not set(reason_table).issubset(
                {"code", "message", "retryable", "feature", "details"}
            ):
                raise ProtocolError("model capability reason fields are invalid")
            _degradation(reason_table, reason_path)
        expected_status = (
            "neutral"
            if available is None
            else "available"
            if available and not reasons
            else "degraded"
        )
        if status != expected_status:
            raise ProtocolError("model capability status is inconsistent")
    warnings = _array(table["warnings"], "model.warnings", maximum=MAX_MODEL_WARNINGS)
    for index, warning in enumerate(warnings):
        path = f"model.warnings[{index}]"
        row = _object(warning, path)
        if not set(row).issubset(
            {"source", "provider", "code", "message", "retryable"}
        ) or not {"source", "code", "message", "retryable"}.issubset(row):
            raise ProtocolError("model warning fields are invalid")
        _enum(
            row["source"],
            f"{path}.source",
            frozenset({"capability", "error", "model"}),
        )
        _string(row["code"], f"{path}.code", maximum=128)
        _string(row["message"], f"{path}.message", maximum=4096)
        _boolean(row["retryable"], f"{path}.retryable")
        if row.get("provider") is not None:
            _provider(row["provider"], f"{path}.provider")
    truncation = _object(table["truncation"], "model.truncation")
    expected_truncation = {
        "sourceTaskCount",
        "emittedTaskCount",
        "tasksTruncated",
        "sourceInboxCount",
        "emittedInboxCount",
        "inboxTruncated",
        "sessionLimit",
    }
    if set(truncation) != expected_truncation:
        raise ProtocolError("model truncation fields are invalid")
    for key in (
        "sourceTaskCount",
        "emittedTaskCount",
        "sourceInboxCount",
        "emittedInboxCount",
        "sessionLimit",
    ):
        _integer(truncation[key], f"model.truncation.{key}")
    for key in ("tasksTruncated", "inboxTruncated"):
        _boolean(truncation[key], f"model.truncation.{key}")
    if (
        truncation["emittedTaskCount"] != len(tasks)
        or truncation["emittedInboxCount"] != len(inbox)
        or truncation["emittedTaskCount"] > truncation["sourceTaskCount"]
        or truncation["emittedInboxCount"] > truncation["sourceInboxCount"]
        or truncation["emittedInboxCount"] > truncation["sessionLimit"]
        or truncation["tasksTruncated"]
        != (truncation["emittedTaskCount"] < truncation["sourceTaskCount"])
        or truncation["inboxTruncated"]
        != (truncation["emittedInboxCount"] < truncation["sourceInboxCount"])
    ):
        raise ProtocolError("model truncation counts are inconsistent")
    encoded = json.dumps(
        table, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_MODEL_BYTES:
        raise ProtocolError("model exceeds the encoded byte limit")
    return table


@dataclass(slots=True)
class SnapshotModel:
    generated_at: int
    host: dict[str, Any]
    projects: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    inbox_sessions: list[dict[str, Any]]
    capabilities: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    truncation: dict[str, Any]

    @property
    def sessions(self) -> tuple[dict[str, Any], ...]:
        """Compatibility accessor for callers that only need Inbox sessions."""

        return tuple(self.inbox_sessions)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "modelVersion": MODEL_VERSION,
            "sourceSchemaVersion": SCHEMA_VERSION,
            "sourceProtocolVersion": PROTOCOL_VERSION,
            "generatedAt": self.generated_at,
            "host": deepcopy(self.host),
            "projects": deepcopy(self.projects),
            "tasks": deepcopy(self.tasks),
            "inboxSessions": deepcopy(self.inbox_sessions),
            "capabilities": deepcopy(self.capabilities),
            "warnings": deepcopy(self.warnings),
            "truncation": deepcopy(self.truncation),
        }
        return deepcopy(_validate_model(value))


def parse_snapshot(
    raw: str | bytes | bytearray, *, max_sessions: int = MAX_MODEL_SESSIONS
) -> SnapshotModel:
    if (
        isinstance(max_sessions, bool)
        or not isinstance(max_sessions, int)
        or not 1 <= max_sessions <= MAX_MODEL_SESSIONS
    ):
        raise ValueError(f"max_sessions must be between 1 and {MAX_MODEL_SESSIONS}")
    snapshot = _validated_snapshot(raw)
    projects_by_id = {item["projectId"]: item for item in snapshot["projects"]}
    repositories_by_id = {
        item["repositoryId"]: item for item in snapshot["repositories"]
    }
    checkouts_by_id = {item["checkoutId"]: item for item in snapshot["checkouts"]}
    surfaces_by_id = {item["surfaceId"]: item for item in snapshot["surfaces"]}
    sessions_by_key = {item["sessionKey"]: item for item in snapshot["sessions"]}
    primary_repository: dict[str, str] = {
        item["projectId"]: item["repositoryId"]
        for item in snapshot["projectRepositories"]
        if item["isPrimary"]
    }
    default_checkout: dict[str, dict[str, Any]] = {}
    for project_id, repository_id in primary_repository.items():
        candidates = [
            checkout
            for checkout in snapshot["checkouts"]
            if checkout["repositoryId"] == repository_id
            and checkout["isDefault"]
            and checkout["declared"]
            and checkout["present"]
        ]
        if candidates:
            default_checkout[project_id] = sorted(
                candidates, key=lambda item: item["checkoutId"]
            )[0]

    all_project_rows: list[dict[str, Any]] = []
    for project in snapshot["projects"]:
        if not project["declared"]:
            continue
        checkout = default_checkout.get(project["projectId"])
        repository = repositories_by_id.get(
            primary_repository.get(project["projectId"], "")
        )
        all_project_rows.append(
            {
                "projectId": project["projectId"],
                "name": project["name"],
                "repositoryName": None if repository is None else repository["name"],
                "defaultProvider": project["defaultProvider"] or "codex",
                "defaultCheckoutId": None
                if checkout is None
                else checkout["checkoutId"],
            }
        )
    all_project_rows.sort(key=lambda item: (item["name"].casefold(), item["projectId"]))

    task_rows: list[dict[str, Any]] = []
    for task in snapshot["tasks"]:
        project = projects_by_id[task["projectId"]]
        checkout = checkouts_by_id.get(task["checkoutId"])
        current = sessions_by_key.get(task["currentSessionKey"])
        state = None if current is None else _session_state(current, surfaces_by_id)
        row = {
            "taskId": task["taskId"],
            "projectId": task["projectId"],
            "projectName": project["name"],
            "checkoutId": task["checkoutId"],
            "checkoutName": None if checkout is None else checkout["displayName"],
            "checkoutKind": None if checkout is None else checkout["kind"],
            "checkoutBranch": None if checkout is None else checkout["branch"],
            "checkoutIsDefault": False if checkout is None else checkout["isDefault"],
            "title": task["title"],
            "purpose": task["purpose"],
            "preferredProvider": task["preferredProvider"],
            "status": task["status"],
            "pinned": task["pinned"],
            "currentSessionKey": task["currentSessionKey"],
            "createdAt": task["createdAt"],
            "updatedAt": task["updatedAt"],
            "closedAt": task["closedAt"],
            "provider": None if state is None else state["provider"],
            "runtimePresence": "unknown" if state is None else state["runtimePresence"],
            "resumability": "unknown" if state is None else state["resumability"],
            "activity": "unknown" if state is None else state["activity"],
            "activityReason": "unknown" if state is None else state["activityReason"],
            "attachment": "unknown" if state is None else state["attachment"],
            "stateConfidence": "unknown" if state is None else state["stateConfidence"],
            "recencyAt": task["updatedAt"] if state is None else state["recencyAt"],
            "canStop": False if state is None else state["canStop"],
        }
        task_rows.append(row)
    task_rows.sort(
        key=lambda item: (
            item["status"] == "closed",
            not item["pinned"],
            -item["recencyAt"],
            item["taskId"],
        )
    )
    task_rows, tasks_truncated = _bounded(
        task_rows, count=MAX_MODEL_TASKS, byte_limit=MAX_MODEL_TASK_BYTES
    )
    project_rows_by_id = {item["projectId"]: item for item in all_project_rows}
    required_project_ids = list(
        dict.fromkeys(
            item["projectId"]
            for item in task_rows
            if item["projectId"] in project_rows_by_id
        )
    )
    required_project_id_set = set(required_project_ids)
    project_candidates = [
        project_rows_by_id[project_id] for project_id in required_project_ids
    ] + [
        item
        for item in all_project_rows
        if item["projectId"] not in required_project_id_set
    ]
    project_rows, projects_truncated = _bounded(
        project_candidates,
        count=MAX_MODEL_PROJECTS,
        byte_limit=MAX_MODEL_PROJECT_BYTES,
    )
    emitted_project_ids = {item["projectId"] for item in project_rows}
    retained_task_rows = [
        item for item in task_rows if item["projectId"] in emitted_project_ids
    ]
    tasks_truncated = tasks_truncated or len(retained_task_rows) != len(task_rows)
    task_rows = retained_task_rows
    project_rows.sort(key=lambda item: (item["name"].casefold(), item["projectId"]))

    inbox_rows: list[dict[str, Any]] = []
    for session in snapshot["sessions"]:
        if session["taskId"] is not None:
            continue
        project = projects_by_id.get(session["projectId"])
        checkout = checkouts_by_id.get(session["checkoutId"])
        row = {
            "sessionKey": session["sessionKey"],
            "providerSessionId": session["providerSessionId"],
            "provider": session["provider"],
            "projectId": session["projectId"],
            "projectName": None if project is None else project["name"],
            "checkoutId": session["checkoutId"],
            "checkoutName": None if checkout is None else checkout["displayName"],
            "name": session["name"],
            **_session_state(session, surfaces_by_id),
        }
        inbox_rows.append(row)
    inbox_rows.sort(key=lambda item: (-item["recencyAt"], item["sessionKey"]))
    inbox_rows, inbox_truncated = _bounded(
        inbox_rows, count=max_sessions, byte_limit=MAX_MODEL_INBOX_BYTES
    )

    warnings: list[dict[str, Any]] = []
    for capability in snapshot["capabilities"]:
        for reason in capability["degradedReasons"]:
            warnings.append(
                {
                    "source": "capability",
                    "provider": capability["provider"],
                    "code": reason["code"],
                    "message": reason["message"],
                    "retryable": reason["retryable"],
                }
            )
    for error in snapshot["errors"]:
        warnings.append(
            {
                "source": "error",
                "code": error["code"],
                "message": error["message"],
                "retryable": error["retryable"],
                "provider": error.get("provider"),
            }
        )
    warnings, diagnostics_truncated = _bounded(
        warnings,
        count=MAX_MODEL_WARNINGS - 4,
        byte_limit=MAX_MODEL_WARNING_BYTES,
    )
    if diagnostics_truncated:
        warnings.append(
            {
                "source": "model",
                "code": "model_diagnostics_truncated",
                "message": "The frontend model omitted diagnostics to remain bounded.",
                "retryable": False,
            }
        )
    if projects_truncated:
        warnings.append(
            {
                "source": "model",
                "code": "model_projects_truncated",
                "message": "The frontend model omitted projects to remain bounded.",
                "retryable": False,
            }
        )
    if tasks_truncated:
        warnings.append(
            {
                "source": "model",
                "code": "model_tasks_truncated",
                "message": "The frontend model omitted tasks to remain bounded.",
                "retryable": False,
            }
        )
    if inbox_truncated:
        warnings.append(
            {
                "source": "model",
                "code": "model_inbox_truncated",
                "message": "The frontend model omitted Inbox sessions to remain bounded.",
                "retryable": False,
            }
        )

    model = SnapshotModel(
        generated_at=snapshot["generatedAt"],
        host=snapshot["host"],
        projects=project_rows,
        tasks=task_rows,
        inbox_sessions=inbox_rows,
        capabilities=_adapt_capabilities(snapshot["capabilities"]),
        warnings=warnings,
        truncation={
            "sourceTaskCount": len(snapshot["tasks"]),
            "emittedTaskCount": len(task_rows),
            "tasksTruncated": tasks_truncated,
            "sourceInboxCount": sum(
                session["taskId"] is None for session in snapshot["sessions"]
            ),
            "emittedInboxCount": len(inbox_rows),
            "inboxTruncated": inbox_truncated,
            "sessionLimit": max_sessions,
        },
    )
    model.to_dict()
    return model


_FLEET_MODEL_TASK_FIELDS = {
    "taskId",
    "projectId",
    "projectName",
    "checkoutId",
    "checkoutName",
    "checkoutKind",
    "checkoutBranch",
    "checkoutIsDefault",
    "title",
    "purpose",
    "preferredProvider",
    "status",
    "pinned",
    "currentSessionKey",
    "createdAt",
    "updatedAt",
    "closedAt",
    "provider",
    "runtimePresence",
    "resumability",
    "activity",
    "activityReason",
    "attachment",
    "stateConfidence",
    "recencyAt",
    "canStop",
    "hostId",
    "hostDisplayName",
    "isLocal",
    "hostReachability",
    "hostStale",
}
_FLEET_MODEL_INBOX_FIELDS = {
    "sessionKey",
    "providerSessionId",
    "provider",
    "projectId",
    "projectName",
    "checkoutId",
    "checkoutName",
    "name",
    "runtimePresence",
    "resumability",
    "activity",
    "activityReason",
    "attachment",
    "stateConfidence",
    "recencyAt",
    "canStop",
    "hostId",
    "hostDisplayName",
    "isLocal",
    "hostReachability",
    "hostStale",
}


def _fleet_error(value: object, path: str) -> dict[str, Any]:
    table = _object(value, path)
    return {
        "code": _string(_required(table, "code", path), f"{path}.code", maximum=128),
        "message": _string(
            _required(table, "message", path), f"{path}.message", maximum=2048
        ),
        "retryable": _boolean(_required(table, "retryable", path), f"{path}.retryable"),
    }


def _optional_integer_field(table: dict[str, Any], key: str, path: str) -> int | None:
    value = _required(table, key, path)
    return None if value is None else _integer(value, f"{path}.{key}")


def _validated_fleet(raw: str | bytes | bytearray) -> dict[str, Any]:
    table = _decode(raw)
    _versions(table)
    if (
        _integer(_required(table, "fleetVersion", "envelope"), "envelope.fleetVersion")
        != FLEET_VERSION
    ):
        raise ProtocolError(f"envelope.fleetVersion expected {FLEET_VERSION}")
    local_host_id = _uuid(
        _required(table, "localHostId", "envelope"), "envelope.localHostId"
    )
    hosts_source = _array(
        _required(table, "hosts", "envelope"),
        "envelope.hosts",
        maximum=MAX_FLEET_HOSTS,
    )
    if not hosts_source:
        raise ProtocolError("envelope.hosts requires one local entry")
    hosts: list[dict[str, Any]] = []
    aliases: list[str] = []
    host_ids: set[str] = set()
    for index, value in enumerate(hosts_source):
        path = f"envelope.hosts[{index}]"
        source = _object(value, path)
        source_kind = _enum(
            _required(source, "source", path),
            f"{path}.source",
            frozenset({"local", "remote"}),
        )
        remote_name = _optional_string(source, "remoteName", path, maximum=128)
        raw_host_id = _required(source, "hostId", path)
        host_id = None if raw_host_id is None else _uuid(raw_host_id, f"{path}.hostId")
        display_name = _string(
            _required(source, "displayName", path),
            f"{path}.displayName",
            maximum=256,
        )
        reachability = _enum(
            _required(source, "reachability", path),
            f"{path}.reachability",
            frozenset({"online", "offline", "unknown"}),
        )
        observed_at = _optional_integer_field(source, "snapshotObservedAt", path)
        received_at = _optional_integer_field(source, "snapshotReceivedAt", path)
        last_attempt_at = _optional_integer_field(source, "lastAttemptAt", path)
        stale = _boolean(_required(source, "stale", path), f"{path}.stale")
        raw_error = _required(source, "error", path)
        error = None if raw_error is None else _fleet_error(raw_error, f"{path}.error")
        raw_snapshot = _required(source, "snapshot", path)
        snapshot = None
        snapshot_model = None
        if raw_snapshot is not None:
            snapshot_raw = json.dumps(
                raw_snapshot,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            snapshot = _validated_snapshot(snapshot_raw)
            snapshot_model = parse_snapshot(
                snapshot_raw, max_sessions=MAX_MODEL_SESSIONS
            ).to_dict()
        if source_kind == "local":
            if index != 0 or remote_name is not None:
                raise ProtocolError("envelope local host ordering is invalid")
            if host_id != local_host_id or snapshot is None:
                raise ProtocolError("envelope local host identity is invalid")
            if reachability != "online" or stale or error is not None:
                raise ProtocolError("envelope local host must be healthy")
        else:
            if index == 0 or remote_name is None:
                raise ProtocolError("envelope remote host routing is invalid")
            aliases.append(remote_name)
        if host_id is not None:
            if host_id in host_ids:
                raise ProtocolError("envelope contains duplicate host identities")
            host_ids.add(host_id)
        if snapshot is None:
            if observed_at is not None or received_at is not None:
                raise ProtocolError("envelope snapshot timestamps require a snapshot")
            if reachability == "online":
                raise ProtocolError("envelope online host requires a snapshot")
        else:
            if (
                snapshot["host"]["hostId"] != host_id
                or snapshot["host"]["displayName"] != display_name
                or snapshot["generatedAt"] != observed_at
                or received_at is None
            ):
                raise ProtocolError("envelope host snapshot identity is inconsistent")
        if reachability == "online" and error is not None:
            raise ProtocolError("envelope online host cannot contain an error")
        if reachability == "offline" and error is None:
            raise ProtocolError("envelope offline host requires an error")
        hosts.append(
            {
                "source": source_kind,
                "remoteName": remote_name,
                "hostId": host_id,
                "displayName": display_name,
                "reachability": reachability,
                "snapshotObservedAt": observed_at,
                "snapshotReceivedAt": received_at,
                "lastAttemptAt": last_attempt_at,
                "stale": stale,
                "error": error,
                "snapshot": snapshot,
                "snapshotModel": snapshot_model,
            }
        )
    if aliases != sorted(aliases) or len(aliases) != len(set(aliases)):
        raise ProtocolError("envelope remote aliases are not unique and ordered")
    return {
        "generatedAt": _integer(
            _required(table, "generatedAt", "envelope"), "envelope.generatedAt"
        ),
        "localHostId": local_host_id,
        "hosts": hosts,
    }


def _validate_fleet_model(value: object) -> dict[str, Any]:
    _validate_json_tree(value, "model")
    table = _object(value, "model")
    required = {
        "modelVersion",
        "sourceSchemaVersion",
        "sourceProtocolVersion",
        "sourceFleetVersion",
        "generatedAt",
        "localHostId",
        "hosts",
        "projects",
        "tasks",
        "inboxSessions",
        "warnings",
        "truncation",
    }
    if set(table) != required:
        raise ProtocolError("fleet model contains missing or unknown fields")
    for key, expected in (
        ("modelVersion", FLEET_MODEL_VERSION),
        ("sourceSchemaVersion", SCHEMA_VERSION),
        ("sourceProtocolVersion", PROTOCOL_VERSION),
        ("sourceFleetVersion", FLEET_VERSION),
    ):
        if _integer(table[key], f"model.{key}") != expected:
            raise ProtocolError(f"model.{key} is incompatible")
    _integer(table["generatedAt"], "model.generatedAt")
    local_host_id = _uuid(table["localHostId"], "model.localHostId")
    hosts = _array(table["hosts"], "model.hosts", maximum=MAX_FLEET_HOSTS)
    host_rows: dict[str, dict[str, Any]] = {}
    local_count = 0
    for index, value in enumerate(hosts):
        path = f"model.hosts[{index}]"
        row = _object(value, path)
        if set(row) != {
            "source",
            "remoteName",
            "hostId",
            "displayName",
            "reachability",
            "stale",
            "hasSnapshot",
            "error",
        }:
            raise ProtocolError("fleet model host fields are invalid")
        source = _enum(row["source"], f"{path}.source", frozenset({"local", "remote"}))
        host_id = (
            None if row["hostId"] is None else _uuid(row["hostId"], f"{path}.hostId")
        )
        remote_name = _optional_string(row, "remoteName", path, maximum=128)
        _string(row["displayName"], f"{path}.displayName", maximum=256)
        reachability = _enum(
            row["reachability"],
            f"{path}.reachability",
            frozenset({"online", "offline", "unknown"}),
        )
        stale = _boolean(row["stale"], f"{path}.stale")
        has_snapshot = _boolean(row["hasSnapshot"], f"{path}.hasSnapshot")
        has_error = row["error"] is not None
        if has_error:
            _fleet_error(row["error"], f"{path}.error")
        if source == "local":
            local_count += 1
            if (
                index != 0
                or remote_name is not None
                or host_id != local_host_id
                or not has_snapshot
                or reachability != "online"
                or stale
                or has_error
            ):
                raise ProtocolError("fleet model local host identity is invalid")
        elif index == 0 or remote_name is None:
            raise ProtocolError("fleet model remote host routing is invalid")
        if host_id is None and has_snapshot:
            raise ProtocolError("fleet model unknown host cannot contain a snapshot")
        if reachability == "online" and (not has_snapshot or has_error):
            raise ProtocolError("fleet model online host state is invalid")
        if reachability == "offline" and not has_error:
            raise ProtocolError("fleet model offline host requires an error")
        if host_id is not None:
            if host_id in host_rows:
                raise ProtocolError("fleet model contains duplicate host identities")
            host_rows[host_id] = row
    if local_count != 1 or local_host_id not in host_rows:
        raise ProtocolError("fleet model omitted the local host")
    projects = _array(table["projects"], "model.projects", maximum=MAX_MODEL_PROJECTS)
    project_ids: set[str] = set()
    project_rows: dict[str, dict[str, Any]] = {}
    project_route_hosts: dict[str, set[str]] = {}
    for index, value in enumerate(projects):
        path = f"model.projects[{index}]"
        row = _object(value, path)
        if set(row) != {"projectId", "name", "repositoryName", "routes"}:
            raise ProtocolError("fleet model project fields are invalid")
        project_id = _uuid(row["projectId"], f"{path}.projectId")
        if project_id in project_ids:
            raise ProtocolError("fleet model contains duplicate projects")
        project_ids.add(project_id)
        project_rows[project_id] = row
        _string(row["name"], f"{path}.name", maximum=256)
        _optional_string(row, "repositoryName", path, maximum=256)
        routes = _array(row["routes"], f"{path}.routes", maximum=MAX_FLEET_HOSTS)
        if not routes:
            raise ProtocolError("fleet model project requires one host route")
        route_hosts: set[str] = set()
        for route_index, route_value in enumerate(routes):
            route_path = f"{path}.routes[{route_index}]"
            route = _object(route_value, route_path)
            if set(route) != {
                "hostId",
                "hostDisplayName",
                "isLocal",
                "defaultProvider",
                "defaultCheckoutId",
                "reachability",
                "stale",
            }:
                raise ProtocolError("fleet model project route fields are invalid")
            route_host = _uuid(route["hostId"], f"{route_path}.hostId")
            host = host_rows.get(route_host)
            if host is None or route_host in route_hosts:
                raise ProtocolError("fleet model project route identity is invalid")
            route_hosts.add(route_host)
            host_display_name = _string(
                route["hostDisplayName"],
                f"{route_path}.hostDisplayName",
                maximum=256,
            )
            is_local = _boolean(route["isLocal"], f"{route_path}.isLocal")
            _provider(route["defaultProvider"], f"{route_path}.defaultProvider")
            _optional_uuid(route, "defaultCheckoutId", route_path)
            reachability = _enum(
                route["reachability"],
                f"{route_path}.reachability",
                frozenset({"online", "offline", "unknown"}),
            )
            stale = _boolean(route["stale"], f"{route_path}.stale")
            if (
                host_display_name != host["displayName"]
                or is_local != (host["source"] == "local")
                or reachability != host["reachability"]
                or stale != host["stale"]
            ):
                raise ProtocolError("fleet model project route disagrees with its host")
        project_route_hosts[project_id] = route_hosts
    tasks = _array(table["tasks"], "model.tasks", maximum=MAX_MODEL_TASKS)
    task_ids: set[tuple[str, str]] = set()
    assigned_session_keys: set[str] = set()
    for index, value in enumerate(tasks):
        path = f"model.tasks[{index}]"
        row = _object(value, path)
        if set(row) != _FLEET_MODEL_TASK_FIELDS:
            raise ProtocolError("fleet model task fields are invalid")
        host_id = _uuid(row["hostId"], f"{path}.hostId")
        identity = (host_id, _uuid(row["taskId"], f"{path}.taskId"))
        host = host_rows.get(host_id)
        if identity in task_ids or host is None:
            raise ProtocolError("fleet model task identity is invalid")
        task_ids.add(identity)
        project_id = _uuid(row["projectId"], f"{path}.projectId")
        if (
            project_id not in project_ids
            or host_id not in project_route_hosts[project_id]
        ):
            raise ProtocolError("fleet model task project is invalid")
        if (
            _string(row["projectName"], f"{path}.projectName", maximum=256)
            != project_rows[project_id]["name"]
        ):
            raise ProtocolError("fleet model task project name is inconsistent")
        _string(row["title"], f"{path}.title", maximum=256)
        _optional_uuid(row, "checkoutId", path)
        _optional_string(row, "checkoutName", path, maximum=256)
        if row["checkoutKind"] is not None:
            _enum(row["checkoutKind"], f"{path}.checkoutKind", _CHECKOUT_KINDS)
        _optional_string(row, "checkoutBranch", path, maximum=1024)
        _optional_string(row, "purpose", path, maximum=4096)
        if row["preferredProvider"] is not None:
            _provider(row["preferredProvider"], f"{path}.preferredProvider")
        status = _enum(row["status"], f"{path}.status", _TASK_STATUSES)
        if row["provider"] is not None:
            _provider(row["provider"], f"{path}.provider")
        if (status == "closed") != (row["closedAt"] is not None):
            raise ProtocolError("fleet model task status is inconsistent")
        if row["currentSessionKey"] is not None:
            session_key, key_host, key_provider, _ = _session_key(
                row["currentSessionKey"], f"{path}.currentSessionKey"
            )
            if (
                key_host != host_id
                or key_provider != row["provider"]
                or session_key in assigned_session_keys
            ):
                raise ProtocolError("fleet model task session identity is invalid")
            assigned_session_keys.add(session_key)
        elif row["provider"] is not None:
            raise ProtocolError("fleet model task provider requires a current session")
        for field, allowed in (
            ("runtimePresence", _RUNTIME_PRESENCE),
            ("resumability", _RESUMABILITY),
            ("activity", _ACTIVITY),
            ("activityReason", _ACTIVITY_REASON),
            ("attachment", _ATTACHMENT),
            ("stateConfidence", _STATE_CONFIDENCE),
            ("hostReachability", frozenset({"online", "offline", "unknown"})),
        ):
            _enum(row[field], f"{path}.{field}", allowed)
        for field in ("pinned", "checkoutIsDefault", "canStop", "isLocal", "hostStale"):
            _boolean(row[field], f"{path}.{field}")
        for field in ("createdAt", "updatedAt", "recencyAt"):
            _integer(row[field], f"{path}.{field}")
        if row["closedAt"] is not None:
            _integer(row["closedAt"], f"{path}.closedAt")
        host_display_name = _string(
            row["hostDisplayName"], f"{path}.hostDisplayName", maximum=256
        )
        if (
            host_display_name != host["displayName"]
            or row["isLocal"] != (host["source"] == "local")
            or row["hostReachability"] != host["reachability"]
            or row["hostStale"] != host["stale"]
        ):
            raise ProtocolError("fleet model task routing disagrees with its host")
        if row["canStop"] and (
            row["provider"] != "claude" or row["runtimePresence"] != "live"
        ):
            raise ProtocolError("fleet model task stop capability is inconsistent")
    inbox = _array(
        table["inboxSessions"], "model.inboxSessions", maximum=MAX_MODEL_SESSIONS
    )
    session_keys: set[str] = set()
    for index, value in enumerate(inbox):
        path = f"model.inboxSessions[{index}]"
        row = _object(value, path)
        if set(row) != _FLEET_MODEL_INBOX_FIELDS:
            raise ProtocolError("fleet model Inbox fields are invalid")
        key, key_host, provider, provider_id = _session_key(
            row["sessionKey"], f"{path}.sessionKey"
        )
        host_id = _uuid(row["hostId"], f"{path}.hostId")
        host = host_rows.get(host_id)
        if (
            key in session_keys
            or key in assigned_session_keys
            or key_host != host_id
            or host is None
        ):
            raise ProtocolError("fleet model Inbox identity is invalid")
        session_keys.add(key)
        if provider != row["provider"] or provider_id != row["providerSessionId"]:
            raise ProtocolError("fleet model Inbox provider identity is invalid")
        project_id = _optional_uuid(row, "projectId", path)
        project_name = _optional_string(row, "projectName", path, maximum=256)
        if project_id is not None:
            if project_name is None:
                raise ProtocolError("fleet model Inbox project name is missing")
            if project_id in project_rows and (
                host_id not in project_route_hosts[project_id]
                or project_name != project_rows[project_id]["name"]
            ):
                raise ProtocolError("fleet model Inbox project is invalid")
        if project_id is None and project_name is not None:
            raise ProtocolError("fleet model Inbox project name requires a project")
        _optional_uuid(row, "checkoutId", path)
        _optional_string(row, "checkoutName", path, maximum=256)
        _optional_string(row, "name", path, maximum=512)
        for field, allowed in (
            ("runtimePresence", _RUNTIME_PRESENCE),
            ("resumability", _RESUMABILITY),
            ("activity", _ACTIVITY),
            ("activityReason", _ACTIVITY_REASON),
            ("attachment", _ATTACHMENT),
            ("stateConfidence", _STATE_CONFIDENCE),
            ("hostReachability", frozenset({"online", "offline", "unknown"})),
        ):
            _enum(row[field], f"{path}.{field}", allowed)
        _integer(row["recencyAt"], f"{path}.recencyAt")
        for field in ("canStop", "isLocal", "hostStale"):
            _boolean(row[field], f"{path}.{field}")
        host_display_name = _string(
            row["hostDisplayName"], f"{path}.hostDisplayName", maximum=256
        )
        if (
            host_display_name != host["displayName"]
            or row["isLocal"] != (host["source"] == "local")
            or row["hostReachability"] != host["reachability"]
            or row["hostStale"] != host["stale"]
        ):
            raise ProtocolError("fleet model Inbox routing disagrees with its host")
        if row["canStop"] and (
            provider != "claude" or row["runtimePresence"] != "live"
        ):
            raise ProtocolError("fleet model Inbox stop capability is inconsistent")
    warnings = _array(table["warnings"], "model.warnings", maximum=MAX_MODEL_WARNINGS)
    for index, value in enumerate(warnings):
        path = f"model.warnings[{index}]"
        row = _object(value, path)
        if not set(row).issubset(
            {"hostId", "source", "provider", "code", "message", "retryable"}
        ) or not {"hostId", "source", "code", "message", "retryable"}.issubset(row):
            raise ProtocolError("fleet model warning fields are invalid")
        if (
            row["hostId"] is not None
            and _uuid(row["hostId"], f"{path}.hostId") not in host_rows
        ):
            raise ProtocolError("fleet model warning host is invalid")
        _enum(
            row["source"],
            f"{path}.source",
            frozenset({"capability", "error", "fleet", "model"}),
        )
        if row.get("provider") is not None:
            _provider(row["provider"], f"{path}.provider")
        _string(row["code"], f"{path}.code", maximum=128)
        _string(row["message"], f"{path}.message", maximum=4096)
        _boolean(row["retryable"], f"{path}.retryable")
    truncation = _object(table["truncation"], "model.truncation")
    expected_truncation = {
        "sourceHostCount",
        "emittedHostCount",
        "sourceTaskCount",
        "emittedTaskCount",
        "tasksTruncated",
        "sourceInboxCount",
        "emittedInboxCount",
        "inboxTruncated",
        "sessionLimit",
    }
    if set(truncation) != expected_truncation:
        raise ProtocolError("fleet model truncation fields are invalid")
    for field in expected_truncation - {"tasksTruncated", "inboxTruncated"}:
        _integer(truncation[field], f"model.truncation.{field}")
    _boolean(truncation["tasksTruncated"], "model.truncation.tasksTruncated")
    _boolean(truncation["inboxTruncated"], "model.truncation.inboxTruncated")
    if (
        truncation["emittedHostCount"] != len(hosts)
        or truncation["emittedTaskCount"] != len(tasks)
        or truncation["emittedInboxCount"] != len(inbox)
        or truncation["sourceHostCount"] < truncation["emittedHostCount"]
        or truncation["sourceTaskCount"] < truncation["emittedTaskCount"]
        or truncation["sourceInboxCount"] < truncation["emittedInboxCount"]
        or not 1 <= truncation["sessionLimit"] <= MAX_MODEL_SESSIONS
    ):
        raise ProtocolError("fleet model truncation counts are inconsistent")
    encoded = json.dumps(
        table, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_MODEL_BYTES:
        raise ProtocolError("fleet model exceeds the encoded byte limit")
    return table


@dataclass(slots=True)
class FleetModel:
    value: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(_validate_fleet_model(deepcopy(self.value)))


def parse_fleet(
    raw: str | bytes | bytearray, *, max_sessions: int = MAX_MODEL_SESSIONS
) -> FleetModel:
    if (
        isinstance(max_sessions, bool)
        or not isinstance(max_sessions, int)
        or not 1 <= max_sessions <= MAX_MODEL_SESSIONS
    ):
        raise ValueError(f"max_sessions must be between 1 and {MAX_MODEL_SESSIONS}")
    fleet = _validated_fleet(raw)
    host_rows: list[dict[str, Any]] = []
    project_rows: dict[str, dict[str, Any]] = {}
    task_rows: list[dict[str, Any]] = []
    inbox_rows: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    source_task_count = 0
    source_inbox_count = 0
    source_tasks_truncated = False
    source_inbox_truncated = False
    for host in fleet["hosts"]:
        host_id = host["hostId"]
        is_local = host["source"] == "local"
        host_rows.append(
            {
                "source": host["source"],
                "remoteName": host["remoteName"],
                "hostId": host_id,
                "displayName": host["displayName"],
                "reachability": host["reachability"],
                "stale": host["stale"],
                "hasSnapshot": host["snapshot"] is not None,
                "error": host["error"],
            }
        )
        if host["error"] is not None:
            warnings.append(
                {
                    "hostId": host_id,
                    "source": "fleet",
                    "code": host["error"]["code"],
                    "message": host["error"]["message"],
                    "retryable": host["error"]["retryable"],
                }
            )
        if host["snapshot"] is None:
            continue
        assert isinstance(host_id, str)
        snapshot_model = host["snapshotModel"]
        assert isinstance(snapshot_model, dict)
        routing = {
            "hostId": host_id,
            "hostDisplayName": host["displayName"],
            "isLocal": is_local,
            "hostReachability": host["reachability"],
            "hostStale": host["stale"],
        }
        for project in snapshot_model["projects"]:
            row = project_rows.setdefault(
                project["projectId"],
                {
                    "projectId": project["projectId"],
                    "name": project["name"],
                    "repositoryName": project["repositoryName"],
                    "routes": [],
                },
            )
            row["routes"].append(
                {
                    "hostId": host_id,
                    "hostDisplayName": host["displayName"],
                    "isLocal": is_local,
                    "defaultProvider": project["defaultProvider"],
                    "defaultCheckoutId": project["defaultCheckoutId"],
                    "reachability": host["reachability"],
                    "stale": host["stale"],
                }
            )
        task_rows.extend({**task, **routing} for task in snapshot_model["tasks"])
        inbox_rows.extend(
            {**session, **routing} for session in snapshot_model["inboxSessions"]
        )
        source_task_count += snapshot_model["truncation"]["sourceTaskCount"]
        source_inbox_count += snapshot_model["truncation"]["sourceInboxCount"]
        source_tasks_truncated = (
            source_tasks_truncated or snapshot_model["truncation"]["tasksTruncated"]
        )
        source_inbox_truncated = (
            source_inbox_truncated or snapshot_model["truncation"]["inboxTruncated"]
        )
        warnings.extend(
            {"hostId": host_id, **warning} for warning in snapshot_model["warnings"]
        )
    task_rows.sort(
        key=lambda item: (
            item["status"] == "closed",
            not item["pinned"],
            -item["recencyAt"],
            item["hostId"],
            item["taskId"],
        )
    )
    task_rows, tasks_truncated = _bounded(
        task_rows, count=MAX_MODEL_TASKS, byte_limit=MAX_MODEL_TASK_BYTES
    )
    inbox_rows.sort(key=lambda item: (-item["recencyAt"], item["sessionKey"]))
    inbox_rows, inbox_truncated = _bounded(
        inbox_rows, count=max_sessions, byte_limit=MAX_MODEL_INBOX_BYTES
    )
    projects = sorted(
        project_rows.values(),
        key=lambda item: (item["name"].casefold(), item["projectId"]),
    )
    for project in projects:
        project["routes"].sort(key=lambda item: (not item["isLocal"], item["hostId"]))
    projects, projects_truncated = _bounded(
        projects, count=MAX_MODEL_PROJECTS, byte_limit=MAX_MODEL_PROJECT_BYTES
    )
    emitted_projects = {project["projectId"] for project in projects}
    retained_tasks = [
        task for task in task_rows if task["projectId"] in emitted_projects
    ]
    tasks_truncated = tasks_truncated or len(retained_tasks) != len(task_rows)
    task_rows = retained_tasks
    warnings, warnings_truncated = _bounded(
        warnings,
        count=MAX_MODEL_WARNINGS - 3,
        byte_limit=MAX_MODEL_WARNING_BYTES,
    )
    for truncated, code, message in (
        (
            projects_truncated,
            "model_projects_truncated",
            "The fleet model omitted projects to remain bounded.",
        ),
        (
            tasks_truncated or source_tasks_truncated,
            "model_tasks_truncated",
            "The fleet model omitted tasks to remain bounded.",
        ),
        (
            inbox_truncated or source_inbox_truncated,
            "model_inbox_truncated",
            "The fleet model omitted Inbox sessions to remain bounded.",
        ),
        (
            warnings_truncated,
            "model_diagnostics_truncated",
            "The fleet model omitted diagnostics to remain bounded.",
        ),
    ):
        if truncated and len(warnings) < MAX_MODEL_WARNINGS:
            warnings.append(
                {
                    "hostId": None,
                    "source": "model",
                    "code": code,
                    "message": message,
                    "retryable": False,
                }
            )
    value = {
        "modelVersion": FLEET_MODEL_VERSION,
        "sourceSchemaVersion": SCHEMA_VERSION,
        "sourceProtocolVersion": PROTOCOL_VERSION,
        "sourceFleetVersion": FLEET_VERSION,
        "generatedAt": fleet["generatedAt"],
        "localHostId": fleet["localHostId"],
        "hosts": host_rows,
        "projects": projects,
        "tasks": task_rows,
        "inboxSessions": inbox_rows,
        "warnings": warnings,
        "truncation": {
            "sourceHostCount": len(fleet["hosts"]),
            "emittedHostCount": len(host_rows),
            "sourceTaskCount": source_task_count,
            "emittedTaskCount": len(task_rows),
            "tasksTruncated": tasks_truncated or source_tasks_truncated,
            "sourceInboxCount": source_inbox_count,
            "emittedInboxCount": len(inbox_rows),
            "inboxTruncated": inbox_truncated or source_inbox_truncated,
            "sessionLimit": max_sessions,
        },
    }
    model = FleetModel(value)
    model.to_dict()
    return model
