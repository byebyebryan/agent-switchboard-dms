"""Bounded Snapshot v1 validation and DMS-facing projection.

This module deliberately duplicates only the public JSON contract.  It never
imports Agent Switchboard or reads its private registry.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import UUID

SCHEMA_VERSION = 1
PROTOCOL_VERSION = 1
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_STRING_LENGTH = 64 * 1024
MAX_JSON_ARRAY_ITEMS = 100_000
MAX_JSON_OBJECT_KEYS = 256
MAX_SNAPSHOT_RECORDS = 100_000
MAX_MODEL_SESSIONS = 1_000
MAX_MODEL_SESSION_BYTES = 4 * 1024 * 1024
MAX_MODEL_FEATURES = 64
MAX_MODEL_FEATURE_BYTES = 64 * 1024
MAX_MODEL_DEGRADED_REASONS = 64
MAX_MODEL_DEGRADED_REASON_BYTES = 512 * 1024
MAX_MODEL_ERRORS = 128
MAX_MODEL_WARNINGS = 256
MAX_MODEL_WARNING_BYTES = 1024 * 1024
_MODEL_DIAGNOSTICS_MESSAGE = "The frontend model omitted diagnostics to remain bounded."
_MODEL_SESSIONS_MESSAGE = "The frontend model omitted sessions to remain bounded."

_PROVIDERS = frozenset({"codex", "claude"})
_TRANSPORTS = frozenset({"tmux"})
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
    "refreshtoken",
    "accesstoken",
    "authtoken",
    "secret",
    "toolresult",
)
_SENSITIVE_KEYS = frozenset(
    {
        "argv",
        "body",
        "content",
        "conversation",
        "conversationhistory",
        "cookie",
        "environment",
        "hookpayload",
        "messages",
        "modeloutput",
        "output",
        "payload",
        "prompt",
        "prompts",
        "prompttext",
        "providerargv",
        "providerpayload",
        "rawpayload",
        "rawprompt",
        "requestpayload",
        "responsepayload",
        "secret",
        "secrets",
        "setcookie",
        "stderr",
        "stdin",
        "stdout",
        "systemprompt",
        "tooloutput",
        "transcript",
        "transcriptbody",
        "transcripts",
        "userprompt",
    }
)
_KEY_NORMALIZER = re.compile(r"[^a-z0-9]")
_DETAIL_STRING_FIELDS = frozenset({"capability", "fallback"})
_DETAIL_INTEGER_FIELDS = frozenset({"emittedCount", "retainedCount"})
_DETAIL_NUMBER_FIELDS = frozenset({"latency"})
_DETAIL_HASH_FIELDS = frozenset({"payloadHash"})
_DETAIL_FIELDS = (
    _DETAIL_STRING_FIELDS
    | _DETAIL_INTEGER_FIELDS
    | _DETAIL_NUMBER_FIELDS
    | _DETAIL_HASH_FIELDS
)


class ProtocolError(ValueError):
    """A Snapshot v1 document is malformed, unsafe, or incompatible."""


def _reject_constant(value: str) -> None:
    raise ProtocolError(f"non-finite JSON number {value!r} is not supported")


def _normalized_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _KEY_NORMALIZER.sub("", normalized)


def _reject_sensitive_key(value: str, path: str) -> None:
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ProtocolError(f"{path} contains a terminal control in an object key")
    normalized = _normalized_key(value)
    if not normalized or len(value) > 256:
        raise ProtocolError(f"{path} contains an invalid object key")
    if normalized in _SENSITIVE_KEYS or any(
        part in normalized for part in _SENSITIVE_KEY_PARTS
    ):
        raise ProtocolError(f"{path} contains forbidden sensitive field {value!r}")
    if "prompt" in normalized or "transcript" in normalized:
        raise ProtocolError(f"{path} contains forbidden content field {value!r}")
    if any(
        part in normalized
        for part in ("conversation", "messages", "output", "response", "result")
    ):
        raise ProtocolError(f"{path} contains forbidden content field {value!r}")
    if normalized.startswith("raw"):
        raise ProtocolError(f"{path} contains forbidden raw field {value!r}")
    if "payload" in normalized and not normalized.endswith("payloadhash"):
        raise ProtocolError(f"{path} contains forbidden payload field {value!r}")
    if "token" in normalized and normalized != "desktoptoken":
        raise ProtocolError(f"{path} contains forbidden token field {value!r}")


def _validate_json_tree(
    value: object, path: str = "envelope", *, depth: int = 0
) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ProtocolError(f"{path} exceeds maximum JSON nesting depth")
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError(f"{path} contains a non-finite number")
        return
    if isinstance(value, str):
        if len(value) > MAX_JSON_STRING_LENGTH:
            raise ProtocolError(f"{path} contains an oversized string")
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ProtocolError(f"{path} contains terminal control characters")
        return
    if isinstance(value, list):
        if len(value) > MAX_JSON_ARRAY_ITEMS:
            raise ProtocolError(f"{path} contains too many array items")
        for index, item in enumerate(value):
            _validate_json_tree(item, f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        if len(value) > MAX_JSON_OBJECT_KEYS:
            raise ProtocolError(f"{path} contains too many object keys")
        for key, item in value.items():
            _reject_sensitive_key(key, path)
            _validate_json_tree(item, f"{path}.{key}", depth=depth + 1)
        return
    raise ProtocolError(f"{path} contains a non-JSON value")


def _decode(raw: str | bytes | bytearray) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            size = len(raw.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ProtocolError("snapshot must be UTF-8 JSON") from error
    elif isinstance(raw, (bytes, bytearray)):
        size = len(raw)
    else:
        raise ProtocolError("snapshot must be UTF-8 JSON")
    if size > MAX_JSON_BYTES:
        raise ProtocolError(f"snapshot exceeds the {MAX_JSON_BYTES}-byte limit")
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except ProtocolError:
        raise
    except (
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        RecursionError,
    ) as error:
        raise ProtocolError(f"invalid JSON: {error}") from error
    _validate_json_tree(value)
    return _object(value, "envelope")


def _object(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
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
        raise ProtocolError(f"{path} must be a non-empty bounded string")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ProtocolError(f"{path} contains terminal control characters")
    return value


def _optional_string(
    table: dict[str, Any], key: str, path: str, *, maximum: int
) -> str | None:
    value = table.get(key)
    return None if value is None else _string(value, f"{path}.{key}", maximum=maximum)


def _integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(f"{path} must be a non-negative integer")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolError(f"{path} must be boolean")
    return value


def _enum(value: object, path: str, allowed: frozenset[str]) -> str:
    text = _string(value, path, maximum=128)
    if text not in allowed:
        raise ProtocolError(f"{path} has unsupported value {text!r}")
    return text


def _provider(value: object, path: str) -> str:
    return _enum(value, path, _PROVIDERS)


def _uuid(value: object, path: str) -> str:
    text = _string(value, path, maximum=64)
    try:
        parsed = UUID(text)
    except ValueError as error:
        raise ProtocolError(f"{path} must be a UUID") from error
    if parsed.int == 0:
        raise ProtocolError(f"{path} must not be a nil UUID")
    return str(parsed)


def _optional_uuid(table: dict[str, Any], key: str, path: str) -> str | None:
    value = table.get(key)
    return None if value is None else _uuid(value, f"{path}.{key}")


def _session_key(value: object, path: str) -> tuple[str, str, str, str]:
    text = _string(value, path, maximum=512)
    parts = text.split(":")
    if len(parts) != 3:
        raise ProtocolError(f"{path} must contain host, provider, and UUID")
    host_id = _uuid(parts[0], f"{path}.host")
    provider = _provider(parts[1], f"{path}.provider")
    provider_session_id = _uuid(parts[2], f"{path}.providerSessionId")
    canonical = f"{host_id}:{provider}:{provider_session_id}"
    return canonical, host_id, provider, provider_session_id


def _hash(value: object, path: str) -> str:
    text = _string(value, path, maximum=64)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ProtocolError(f"{path} must be a lowercase SHA-256 digest")
    return text


def _string_array(
    value: object,
    path: str,
    *,
    maximum_items: int = 10_000,
    maximum_string: int = 4096,
) -> list[str]:
    items = _array(value, path, maximum=maximum_items)
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        text = _string(item, f"{path}[{index}]", maximum=maximum_string)
        if text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _details(value: object, path: str) -> dict[str, Any]:
    table = _object(value, path)
    unknown = set(table) - _DETAIL_FIELDS
    if unknown:
        raise ProtocolError(f"{path} contains unsupported retained detail fields")
    result: dict[str, Any] = {}
    for key, value in table.items():
        field_path = f"{path}.{key}"
        if key in _DETAIL_STRING_FIELDS:
            result[key] = _string(value, field_path, maximum=512)
        elif key in _DETAIL_INTEGER_FIELDS:
            result[key] = _integer(value, field_path)
        elif key in _DETAIL_NUMBER_FIELDS:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ProtocolError(f"{field_path} must be a non-negative number")
            result[key] = value
        else:
            result[key] = _hash(value, field_path)
    if (
        "emittedCount" in result
        and "retainedCount" in result
        and result["emittedCount"] > result["retainedCount"]
    ):
        raise ProtocolError(f"{path}.emittedCount must not exceed retainedCount")
    return result


def _bounded_encoded_items(
    items: list[Any], *, count_limit: int, byte_limit: int
) -> list[Any]:
    """Select items in order within explicit count and canonical byte bounds."""

    selected: list[Any] = []
    encoded_bytes = 2
    for item in items:
        if len(selected) >= count_limit:
            break
        encoded = json.dumps(
            item,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        separator_bytes = 1 if selected else 0
        if encoded_bytes + separator_bytes + len(encoded) > byte_limit:
            continue
        selected.append(item)
        encoded_bytes += separator_bytes + len(encoded)
    return selected


def _truncation_summary(
    *, source_count: int, emitted_count: int, limit: int, byte_limit: int
) -> dict[str, Any]:
    return {
        "truncated": emitted_count < source_count,
        "sourceCount": source_count,
        "emittedCount": emitted_count,
        "limit": limit,
        "byteLimit": byte_limit,
    }


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
    if "aliases" in table:
        result["aliases"] = _string_array(
            table["aliases"], f"{path}.aliases", maximum_string=128
        )
    if "defaultProvider" in table:
        result["defaultProvider"] = (
            None
            if table["defaultProvider"] is None
            else _provider(table["defaultProvider"], f"{path}.defaultProvider")
        )
    if "defaultTransport" in table:
        result["defaultTransport"] = _enum(
            table["defaultTransport"], f"{path}.defaultTransport", _TRANSPORTS
        )
    if "contextSources" in table:
        result["contextSources"] = _string_array(
            table["contextSources"],
            f"{path}.contextSources",
            maximum_string=1024,
        )
    if "declared" in table:
        result["declared"] = _boolean(table["declared"], f"{path}.declared")
    for key in ("createdAt", "updatedAt"):
        if key in table:
            result[key] = (
                None if table[key] is None else _integer(table[key], f"{path}.{key}")
            )
    return result


def _location_record(value: object, path: str, host_id: str) -> dict[str, Any]:
    table = _object(value, path)
    record_host_id = _uuid(_required(table, "hostId", path), f"{path}.hostId")
    if record_host_id != host_id:
        raise ProtocolError(f"{path}.hostId does not match envelope.host.hostId")
    result: dict[str, Any] = {
        "locationId": _uuid(_required(table, "locationId", path), f"{path}.locationId"),
        "projectId": _uuid(_required(table, "projectId", path), f"{path}.projectId"),
        "hostId": record_host_id,
        "path": _string(_required(table, "path", path), f"{path}.path", maximum=4096),
    }
    for key, maximum in (("displayName", 256), ("repositoryIdentity", 2048)):
        if key in table:
            result[key] = _optional_string(table, key, path, maximum=maximum)
    if "providerOverride" in table:
        result["providerOverride"] = (
            None
            if table["providerOverride"] is None
            else _provider(table["providerOverride"], f"{path}.providerOverride")
        )
    if "transportOverride" in table:
        result["transportOverride"] = (
            None
            if table["transportOverride"] is None
            else _enum(
                table["transportOverride"], f"{path}.transportOverride", _TRANSPORTS
            )
        )
    for key in ("isDefault", "declared"):
        if key in table:
            result[key] = _boolean(table[key], f"{path}.{key}")
    for key in ("lastObservedAt", "createdAt", "updatedAt"):
        if key in table:
            result[key] = (
                None if table[key] is None else _integer(table[key], f"{path}.{key}")
            )
    return result


def _runtime_locator(value: object, path: str) -> dict[str, Any]:
    table = _object(value, path)
    result: dict[str, Any] = {}
    if "pid" in table:
        if table["pid"] is None:
            result["pid"] = None
        else:
            pid = _integer(table["pid"], f"{path}.pid")
            if pid == 0:
                raise ProtocolError(f"{path}.pid must be a positive integer")
            result["pid"] = pid
    for key in ("providerRuntimeId", "tmuxSession", "tmuxWindow", "tmuxPane"):
        if key in table:
            result[key] = _optional_string(table, key, path, maximum=1024)
    if "observedAt" in table:
        result["observedAt"] = (
            None
            if table["observedAt"] is None
            else _integer(table["observedAt"], f"{path}.observedAt")
        )
    return result


def _session_record(value: object, path: str, host_id: str) -> dict[str, Any]:
    table = _object(value, path)
    session_key, key_host, key_provider, key_provider_id = _session_key(
        _required(table, "sessionKey", path), f"{path}.sessionKey"
    )
    record_host = _uuid(_required(table, "hostId", path), f"{path}.hostId")
    provider = _provider(_required(table, "provider", path), f"{path}.provider")
    provider_session_id = _uuid(
        _required(table, "providerSessionId", path), f"{path}.providerSessionId"
    )
    if record_host != host_id or key_host != host_id:
        raise ProtocolError(f"{path} belongs to a different host")
    if provider != key_provider or provider_session_id != key_provider_id:
        raise ProtocolError(f"{path} identity fields disagree with sessionKey")
    first_observed_at = _integer(
        _required(table, "firstObservedAt", path), f"{path}.firstObservedAt"
    )
    last_observed_at = _integer(
        _required(table, "lastObservedAt", path), f"{path}.lastObservedAt"
    )
    if last_observed_at < first_observed_at:
        raise ProtocolError(f"{path} observation timestamps are reversed")
    result: dict[str, Any] = {
        "sessionKey": session_key,
        "hostId": record_host,
        "provider": provider,
        "providerSessionId": provider_session_id,
        "firstObservedAt": first_observed_at,
        "lastObservedAt": last_observed_at,
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
    }
    for key in (
        "projectId",
        "locationId",
        "surfaceId",
        "latestHandoffId",
        "continuedFromHandoffId",
    ):
        if key in table:
            result[key] = _optional_uuid(table, key, path)
    for key, maximum in (("name", 512), ("purpose", 4096), ("cwd", 4096)):
        if key in table:
            result[key] = _optional_string(table, key, path, maximum=maximum)
    for key in (
        "createdAt",
        "providerUpdatedAt",
        "lastActivityAt",
        "stateObservedAt",
        "wrappedAt",
    ):
        if key in table:
            result[key] = (
                None if table[key] is None else _integer(table[key], f"{path}.{key}")
            )
    if "runtimeLocator" in table:
        result["runtimeLocator"] = (
            None
            if table["runtimeLocator"] is None
            else _runtime_locator(table["runtimeLocator"], f"{path}.runtimeLocator")
        )
    if "pinned" in table:
        result["pinned"] = _boolean(table["pinned"], f"{path}.pinned")
    return result


def _runtime_record(value: object, path: str, host_id: str) -> dict[str, Any]:
    table = _object(value, path)
    record_host = _uuid(_required(table, "hostId", path), f"{path}.hostId")
    provider = _provider(_required(table, "provider", path), f"{path}.provider")
    if record_host != host_id:
        raise ProtocolError(f"{path}.hostId does not match envelope.host.hostId")
    result: dict[str, Any] = {
        "hostId": record_host,
        "provider": provider,
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
    if "sessionKey" in table:
        if table["sessionKey"] is None:
            result["sessionKey"] = None
        else:
            session_key, key_host, key_provider, _ = _session_key(
                table["sessionKey"], f"{path}.sessionKey"
            )
            if key_host != host_id or key_provider != provider:
                raise ProtocolError(f"{path}.sessionKey does not match host/provider")
            result["sessionKey"] = session_key
    if "launchId" in table:
        result["launchId"] = _optional_uuid(table, "launchId", path)
    for key in ("observationId", "observationKey", "source", "providerRuntimeId"):
        if key in table:
            result[key] = _optional_string(table, key, path, maximum=256)
    if "sourcePriority" in table:
        result["sourcePriority"] = _integer(
            table["sourcePriority"], f"{path}.sourcePriority"
        )
    if "pid" in table:
        if table["pid"] is None:
            result["pid"] = None
        else:
            pid = _integer(table["pid"], f"{path}.pid")
            if pid == 0:
                raise ProtocolError(f"{path}.pid must be a positive integer")
            result["pid"] = pid
    for key in ("tmuxSession", "tmuxWindow", "tmuxPane"):
        if key in table:
            result[key] = _optional_string(table, key, path, maximum=256)
    if "receivedAt" in table:
        result["receivedAt"] = (
            None
            if table["receivedAt"] is None
            else _integer(table["receivedAt"], f"{path}.receivedAt")
        )
    if "payloadHash" in table:
        result["payloadHash"] = (
            None
            if table["payloadHash"] is None
            else _hash(table["payloadHash"], f"{path}.payloadHash")
        )
    return result


def _surface_record(value: object, path: str, host_id: str) -> dict[str, Any]:
    table = _object(value, path)
    record_host = _uuid(_required(table, "hostId", path), f"{path}.hostId")
    provider = _provider(_required(table, "provider", path), f"{path}.provider")
    if record_host != host_id:
        raise ProtocolError(f"{path}.hostId does not match envelope.host.hostId")
    role = _enum(_required(table, "role", path), f"{path}.role", _SURFACE_ROLES)
    binding = _enum(
        _required(table, "bindingConfidence", path),
        f"{path}.bindingConfidence",
        _BINDING_CONFIDENCE,
    )
    created_at = _integer(_required(table, "createdAt", path), f"{path}.createdAt")
    last_observed_at = _integer(
        _required(table, "lastObservedAt", path), f"{path}.lastObservedAt"
    )
    if last_observed_at < created_at:
        raise ProtocolError(f"{path} observation timestamps are reversed")
    result: dict[str, Any] = {
        "surfaceId": _uuid(_required(table, "surfaceId", path), f"{path}.surfaceId"),
        "hostId": record_host,
        "provider": provider,
        "transport": _enum(
            _required(table, "transport", path), f"{path}.transport", _TRANSPORTS
        ),
        "transportLocator": _string(
            _required(table, "transportLocator", path),
            f"{path}.transportLocator",
            maximum=1024,
        ),
        "role": role,
        "bindingConfidence": binding,
        "createdAt": created_at,
        "lastObservedAt": last_observed_at,
        "clientAttached": _boolean(
            _required(table, "clientAttached", path), f"{path}.clientAttached"
        ),
    }
    if role == "provider_manager" and binding != "unknown":
        raise ProtocolError(
            f"{path}.bindingConfidence must be unknown for provider_manager"
        )
    if "currentSessionKey" in table:
        if table["currentSessionKey"] is None:
            result["currentSessionKey"] = None
        else:
            session_key, key_host, key_provider, _ = _session_key(
                table["currentSessionKey"], f"{path}.currentSessionKey"
            )
            if key_host != host_id or key_provider != provider:
                raise ProtocolError(
                    f"{path}.currentSessionKey does not match host/provider"
                )
            if role == "provider_manager":
                raise ProtocolError(
                    f"{path}.currentSessionKey is invalid for provider_manager"
                )
            result["currentSessionKey"] = session_key
    if binding == "confirmed" and result.get("currentSessionKey") is None:
        raise ProtocolError(
            f"{path}.bindingConfidence confirmed requires currentSessionKey"
        )
    if "launchId" in table:
        result["launchId"] = _optional_uuid(table, "launchId", path)
    if "workspaceId" in table:
        result["workspaceId"] = _optional_string(
            table, "workspaceId", path, maximum=256
        )
    if "retiredAt" in table:
        result["retiredAt"] = (
            None
            if table["retiredAt"] is None
            else _integer(table["retiredAt"], f"{path}.retiredAt")
        )
    retired_at = result.get("retiredAt")
    if retired_at is not None and not created_at <= retired_at <= last_observed_at:
        raise ProtocolError(f"{path}.retiredAt is outside the observation lifetime")
    if retired_at is not None and (
        result.get("currentSessionKey") is not None
        or binding != "unknown"
        or result["clientAttached"] is not False
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
    contract_path = f"{path}.testedContractRange"
    contract = _object(_required(table, "testedContractRange", path), contract_path)
    features = _string_array(
        _required(table, "features", path), f"{path}.features", maximum_string=256
    )
    raw_reasons = _array(
        _required(table, "degradedReasons", path), f"{path}.degradedReasons"
    )
    reasons = [
        _degradation(item, f"{path}.degradedReasons[{index}]")
        for index, item in enumerate(raw_reasons)
    ]
    available = _boolean(_required(table, "available", path), f"{path}.available")
    if not available and not reasons:
        raise ProtocolError(f"{path}.degradedReasons must explain unavailable provider")
    result: dict[str, Any] = {
        "provider": _provider(_required(table, "provider", path), f"{path}.provider"),
        "available": available,
        "testedContractRange": {
            "minimum": _string(
                _required(contract, "minimum", contract_path),
                f"{contract_path}.minimum",
                maximum=256,
            ),
            "maximum": _string(
                _required(contract, "maximum", contract_path),
                f"{contract_path}.maximum",
                maximum=256,
            ),
        },
        "features": features,
        "degradedReasons": reasons,
    }
    if table.get("providerVersion") is not None:
        result["providerVersion"] = _string(
            table["providerVersion"], f"{path}.providerVersion", maximum=256
        )
    if table.get("schemaFingerprint") is not None:
        result["schemaFingerprint"] = _hash(
            table["schemaFingerprint"], f"{path}.schemaFingerprint"
        )
    return result


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
    if table.get("hostId") is not None:
        result["hostId"] = _uuid(table["hostId"], f"{path}.hostId")
    if table.get("provider") is not None:
        result["provider"] = _provider(table["provider"], f"{path}.provider")
    if table.get("sessionKey") is not None:
        session_key, key_host, key_provider, _ = _session_key(
            table["sessionKey"], f"{path}.sessionKey"
        )
        if "hostId" in result and result["hostId"] != key_host:
            raise ProtocolError(f"{path} session/host routing fields disagree")
        if "provider" in result and result["provider"] != key_provider:
            raise ProtocolError(f"{path} session/provider routing fields disagree")
        result["sessionKey"] = session_key
    if table.get("details") is not None:
        result["details"] = _details(table["details"], f"{path}.details")
    return result


def _unique(records: list[dict[str, Any]], key: str, collection: str) -> set[str]:
    values = [str(record[key]) for record in records]
    if len(values) != len(set(values)):
        raise ProtocolError(f"envelope.{collection} contains duplicate {key} values")
    return set(values)


def _validated_snapshot(raw: str | bytes | bytearray) -> dict[str, Any]:
    table = _decode(raw)
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
    locations = [
        _location_record(item, f"envelope.locations[{index}]", host_id)
        for index, item in enumerate(records("locations"))
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
        for index, item in enumerate(
            _array(
                _required(table, "capabilities", "envelope"),
                "envelope.capabilities",
            )
        )
    ]
    errors = [
        _error_record(item, f"envelope.errors[{index}]")
        for index, item in enumerate(
            _array(_required(table, "errors", "envelope"), "envelope.errors")
        )
    ]

    project_ids = _unique(projects, "projectId", "projects")
    _unique(locations, "locationId", "locations")
    session_keys = _unique(sessions, "sessionKey", "sessions")
    surface_ids = _unique(surfaces, "surfaceId", "surfaces")
    providers = [capability["provider"] for capability in capabilities]
    if len(providers) != len(set(providers)):
        raise ProtocolError("envelope contains duplicate provider capabilities")

    locations_by_id = {location["locationId"]: location for location in locations}
    for index, location in enumerate(locations):
        if location["projectId"] not in project_ids:
            raise ProtocolError(
                f"envelope.locations[{index}].projectId is not in projects"
            )

    for index, session in enumerate(sessions):
        project_id = session.get("projectId")
        location_id = session.get("locationId")
        if project_id is not None and project_id not in project_ids:
            raise ProtocolError(
                f"envelope.sessions[{index}].projectId is not in projects"
            )
        if location_id is not None:
            location = locations_by_id.get(location_id)
            if location is None:
                raise ProtocolError(
                    f"envelope.sessions[{index}].locationId is not in locations"
                )
            if project_id is None or location["projectId"] != project_id:
                raise ProtocolError(
                    f"envelope.sessions[{index}] location/project disagree"
                )
        surface_id = session.get("surfaceId")
        if surface_id is not None and surface_id not in surface_ids:
            raise ProtocolError(
                f"envelope.sessions[{index}].surfaceId is not in surfaces"
            )

    for collection, collection_name, key in (
        (runtimes, "runtimes", "sessionKey"),
        (surfaces, "surfaces", "currentSessionKey"),
    ):
        for index, record in enumerate(collection):
            session_key = record.get(key)
            if session_key is not None and session_key not in session_keys:
                raise ProtocolError(
                    f"envelope.{collection_name}[{index}].{key} is not in sessions"
                )

    sessions_by_key = {session["sessionKey"]: session for session in sessions}
    surfaces_by_id = {surface["surfaceId"]: surface for surface in surfaces}
    for index, session in enumerate(sessions):
        surface_id = session.get("surfaceId")
        if surface_id is None:
            continue
        if surfaces_by_id[surface_id].get("currentSessionKey") != session["sessionKey"]:
            raise ProtocolError(
                f"envelope.sessions[{index}] surface binding is inconsistent"
            )
    for index, surface in enumerate(surfaces):
        session_key = surface.get("currentSessionKey")
        if session_key is None:
            continue
        if sessions_by_key[session_key].get("surfaceId") != surface["surfaceId"]:
            raise ProtocolError(
                f"envelope.surfaces[{index}] session binding is inconsistent"
            )

    for index, error in enumerate(errors):
        if error.get("hostId") is not None and error["hostId"] != host_id:
            raise ProtocolError(
                f"envelope.errors[{index}].hostId belongs to another host"
            )
        if error.get("sessionKey") is not None:
            _, key_host, key_provider, _ = _session_key(
                error["sessionKey"], f"envelope.errors[{index}].sessionKey"
            )
            if key_host != host_id:
                raise ProtocolError(
                    f"envelope.errors[{index}].sessionKey belongs to another host"
                )
            if error.get("provider") is not None and error["provider"] != key_provider:
                raise ProtocolError(
                    f"envelope.errors[{index}] session/provider disagree"
                )

    return {
        "schemaVersion": schema,
        "protocolVersion": protocol,
        "generatedAt": generated_at,
        "host": host,
        "projects": projects,
        "locations": locations,
        "sessions": sessions,
        "runtimes": runtimes,
        "surfaces": surfaces,
        "capabilities": capabilities,
        "errors": errors,
    }


def _recency(session: dict[str, Any]) -> int:
    for key in ("lastActivityAt", "providerUpdatedAt", "createdAt", "lastObservedAt"):
        value = session.get(key)
        if value is not None:
            return int(value)
    raise AssertionError("validated sessions always contain lastObservedAt")


def _project_session(
    session: dict[str, Any],
    projects: dict[str, dict[str, Any]],
    locations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    project_id = session.get("projectId")
    location_id = session.get("locationId")
    project = projects.get(project_id) if isinstance(project_id, str) else None
    location = locations.get(location_id) if isinstance(location_id, str) else None
    return {
        "sessionKey": session["sessionKey"],
        "hostId": session["hostId"],
        "provider": "codex",
        "providerSessionId": session["providerSessionId"],
        "projectId": session.get("projectId"),
        "projectName": None if project is None else project["name"],
        "locationId": session.get("locationId"),
        "locationName": None if location is None else location.get("displayName"),
        "name": session.get("name"),
        "purpose": session.get("purpose"),
        "cwd": session.get("cwd"),
        "firstObservedAt": session["firstObservedAt"],
        "lastObservedAt": session["lastObservedAt"],
        "createdAt": session.get("createdAt"),
        "providerUpdatedAt": session.get("providerUpdatedAt"),
        "lastActivityAt": session.get("lastActivityAt"),
        "stateObservedAt": session.get("stateObservedAt"),
        "wrappedAt": session.get("wrappedAt"),
        "recencyAt": _recency(session),
        "metadataSource": session["metadataSource"],
        "runtimePresence": session["runtimePresence"],
        "resumability": session["resumability"],
        "activity": session["activity"],
        "activityReason": session["activityReason"],
        "attachment": session["attachment"],
        "stateConfidence": session["stateConfidence"],
        "pinned": session.get("pinned", False),
    }


def _adapt_capability(
    capabilities: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    capability = next(
        (item for item in capabilities if item["provider"] == "codex"), None
    )
    if capability is None:
        return (
            {
                "provider": "codex",
                "status": "neutral",
                "available": None,
                "features": [],
                "degradedReasons": [],
            },
            {
                "features": _truncation_summary(
                    source_count=0,
                    emitted_count=0,
                    limit=MAX_MODEL_FEATURES,
                    byte_limit=MAX_MODEL_FEATURE_BYTES,
                ),
                "degradedReasons": _truncation_summary(
                    source_count=0,
                    emitted_count=0,
                    limit=MAX_MODEL_DEGRADED_REASONS,
                    byte_limit=MAX_MODEL_DEGRADED_REASON_BYTES,
                ),
            },
        )
    result = deepcopy(capability)
    features = _bounded_encoded_items(
        capability["features"],
        count_limit=MAX_MODEL_FEATURES,
        byte_limit=MAX_MODEL_FEATURE_BYTES,
    )
    reasons = _bounded_encoded_items(
        capability["degradedReasons"],
        count_limit=MAX_MODEL_DEGRADED_REASONS,
        byte_limit=MAX_MODEL_DEGRADED_REASON_BYTES,
    )
    result["features"] = features
    result["degradedReasons"] = reasons
    result["status"] = (
        "degraded"
        if not capability["available"] or capability["degradedReasons"]
        else "available"
    )
    return (
        result,
        {
            "features": _truncation_summary(
                source_count=len(capability["features"]),
                emitted_count=len(features),
                limit=MAX_MODEL_FEATURES,
                byte_limit=MAX_MODEL_FEATURE_BYTES,
            ),
            "degradedReasons": _truncation_summary(
                source_count=len(capability["degradedReasons"]),
                emitted_count=len(reasons),
                limit=MAX_MODEL_DEGRADED_REASONS,
                byte_limit=MAX_MODEL_DEGRADED_REASON_BYTES,
            ),
        },
    )


def _relevant_error(error: dict[str, Any]) -> bool:
    provider = error.get("provider")
    if provider is not None:
        return provider == "codex"
    session_key = error.get("sessionKey")
    if session_key is not None:
        return _session_key(session_key, "model.error.sessionKey")[2] == "codex"
    return True


def _diagnostics(
    capability: dict[str, Any],
    errors: list[dict[str, Any]],
    truncation: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    for reason in capability["degradedReasons"]:
        warning: dict[str, Any] = {
            "source": "capability",
            "code": reason["code"],
            "retryable": reason["retryable"],
        }
        if "feature" in reason:
            warning["feature"] = reason["feature"]
        candidates.append(warning)
    relevant_errors = [error for error in errors if _relevant_error(error)]
    for error in relevant_errors[:MAX_MODEL_ERRORS]:
        warning = deepcopy(error)
        warning["source"] = "error"
        candidates.append(warning)
    warnings = _bounded_encoded_items(
        candidates,
        count_limit=MAX_MODEL_WARNINGS - 2,
        byte_limit=MAX_MODEL_WARNING_BYTES - 16 * 1024,
    )
    emitted_errors = sum(warning["source"] == "error" for warning in warnings)
    diagnostic_truncation = {
        **deepcopy(truncation),
        "errors": _truncation_summary(
            source_count=len(relevant_errors),
            emitted_count=emitted_errors,
            limit=MAX_MODEL_ERRORS,
            byte_limit=MAX_MODEL_WARNING_BYTES - 16 * 1024,
        ),
        "warnings": _truncation_summary(
            source_count=(
                truncation["degradedReasons"]["sourceCount"] + len(relevant_errors)
            ),
            emitted_count=len(warnings),
            limit=MAX_MODEL_WARNINGS - 2,
            byte_limit=MAX_MODEL_WARNING_BYTES - 16 * 1024,
        ),
    }
    if any(summary["truncated"] for summary in diagnostic_truncation.values()):
        warnings.append(
            {
                "source": "model",
                "code": "model_diagnostics_truncated",
                "message": _MODEL_DIAGNOSTICS_MESSAGE,
                "retryable": False,
                "counts": deepcopy(diagnostic_truncation),
            }
        )
    return warnings, diagnostic_truncation


def _exact_object(
    value: object,
    path: str,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    table = _object(value, path)
    unknown = set(table) - required - optional
    if unknown:
        raise ProtocolError(f"{path} contains unsupported fields")
    missing = required - set(table)
    if missing:
        raise ProtocolError(f"{path} is missing required fields")
    return table


def _canonical_uuid(value: object, path: str) -> str:
    canonical = _uuid(value, path)
    if value != canonical:
        raise ProtocolError(f"{path} must use canonical UUID spelling")
    return canonical


def _canonical_session_key(value: object, path: str) -> tuple[str, str, str, str]:
    canonical, host_id, provider, provider_session_id = _session_key(value, path)
    if value != canonical:
        raise ProtocolError(f"{path} must use canonical identity spelling")
    return canonical, host_id, provider, provider_session_id


def _nullable_string(value: object, path: str, *, maximum: int) -> str | None:
    return None if value is None else _string(value, path, maximum=maximum)


def _nullable_uuid(value: object, path: str) -> str | None:
    return None if value is None else _canonical_uuid(value, path)


def _nullable_integer(value: object, path: str) -> int | None:
    return None if value is None else _integer(value, path)


def _validate_encoded_limit(value: object, path: str, maximum: int) -> None:
    try:
        size = len(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ProtocolError(f"{path} contains invalid JSON") from error
    if size > maximum:
        raise ProtocolError(f"{path} exceeds its {maximum}-byte limit")


def _validate_model_host(value: object) -> str:
    path = "model.host"
    table = _exact_object(
        value,
        path,
        required=frozenset({"hostId", "displayName"}),
    )
    host_id = _canonical_uuid(table["hostId"], f"{path}.hostId")
    _string(table["displayName"], f"{path}.displayName", maximum=256)
    return host_id


_MODEL_SESSION_FIELDS = frozenset(
    {
        "sessionKey",
        "hostId",
        "provider",
        "providerSessionId",
        "projectId",
        "projectName",
        "locationId",
        "locationName",
        "name",
        "purpose",
        "cwd",
        "firstObservedAt",
        "lastObservedAt",
        "createdAt",
        "providerUpdatedAt",
        "lastActivityAt",
        "stateObservedAt",
        "wrappedAt",
        "recencyAt",
        "metadataSource",
        "runtimePresence",
        "resumability",
        "activity",
        "activityReason",
        "attachment",
        "stateConfidence",
        "pinned",
    }
)


def _validate_model_session(value: object, path: str, host_id: str) -> None:
    table = _exact_object(value, path, required=_MODEL_SESSION_FIELDS)
    session_key, key_host, key_provider, key_provider_id = _canonical_session_key(
        table["sessionKey"], f"{path}.sessionKey"
    )
    record_host = _canonical_uuid(table["hostId"], f"{path}.hostId")
    provider = _string(table["provider"], f"{path}.provider", maximum=16)
    provider_session_id = _canonical_uuid(
        table["providerSessionId"], f"{path}.providerSessionId"
    )
    if (
        record_host != host_id
        or key_host != host_id
        or provider != "codex"
        or key_provider != "codex"
        or provider_session_id != key_provider_id
    ):
        raise ProtocolError(f"{path} is not a canonical Codex identity")
    if session_key != f"{host_id}:codex:{provider_session_id}":
        raise ProtocolError(f"{path}.sessionKey identity fields disagree")

    _nullable_uuid(table["projectId"], f"{path}.projectId")
    _nullable_string(table["projectName"], f"{path}.projectName", maximum=256)
    _nullable_uuid(table["locationId"], f"{path}.locationId")
    _nullable_string(table["locationName"], f"{path}.locationName", maximum=256)
    _nullable_string(table["name"], f"{path}.name", maximum=512)
    _nullable_string(table["purpose"], f"{path}.purpose", maximum=4096)
    _nullable_string(table["cwd"], f"{path}.cwd", maximum=4096)
    project_id_present = table["projectId"] is not None
    project_name_present = table["projectName"] is not None
    location_id_present = table["locationId"] is not None
    location_name_present = table["locationName"] is not None
    if project_id_present != project_name_present:
        raise ProtocolError(f"{path} project identity and name are inconsistent")
    if location_name_present and not location_id_present:
        raise ProtocolError(f"{path}.locationName requires locationId")
    if location_id_present and not project_id_present:
        raise ProtocolError(f"{path}.locationId requires projectId")

    first_observed_at = _integer(table["firstObservedAt"], f"{path}.firstObservedAt")
    last_observed_at = _integer(table["lastObservedAt"], f"{path}.lastObservedAt")
    if last_observed_at < first_observed_at:
        raise ProtocolError(f"{path} observation timestamps are reversed")
    optional_times = {
        key: _nullable_integer(table[key], f"{path}.{key}")
        for key in (
            "createdAt",
            "providerUpdatedAt",
            "lastActivityAt",
            "stateObservedAt",
            "wrappedAt",
        )
    }
    recency_at = _integer(table["recencyAt"], f"{path}.recencyAt")
    expected_recency = next(
        (
            optional_times[key]
            for key in ("lastActivityAt", "providerUpdatedAt", "createdAt")
            if optional_times[key] is not None
        ),
        last_observed_at,
    )
    if recency_at != expected_recency:
        raise ProtocolError(f"{path}.recencyAt is inconsistent with session timestamps")

    _string(table["metadataSource"], f"{path}.metadataSource", maximum=64)
    _enum(table["runtimePresence"], f"{path}.runtimePresence", _RUNTIME_PRESENCE)
    _enum(table["resumability"], f"{path}.resumability", _RESUMABILITY)
    _enum(table["activity"], f"{path}.activity", _ACTIVITY)
    _enum(table["activityReason"], f"{path}.activityReason", _ACTIVITY_REASON)
    _enum(table["attachment"], f"{path}.attachment", _ATTACHMENT)
    _enum(table["stateConfidence"], f"{path}.stateConfidence", _STATE_CONFIDENCE)
    _boolean(table["pinned"], f"{path}.pinned")


def _validate_model_degradation(value: object, path: str) -> None:
    table = _exact_object(
        value,
        path,
        required=frozenset({"code", "message", "retryable"}),
        optional=frozenset({"feature", "details"}),
    )
    _string(table["code"], f"{path}.code", maximum=128)
    _string(table["message"], f"{path}.message", maximum=2048)
    _boolean(table["retryable"], f"{path}.retryable")
    if "feature" in table:
        _string(table["feature"], f"{path}.feature", maximum=256)
    if "details" in table:
        _details(table["details"], f"{path}.details")


def _validate_model_capability(value: object) -> dict[str, Any]:
    path = "model.codexCapability"
    table = _exact_object(
        value,
        path,
        required=frozenset(
            {"provider", "status", "available", "features", "degradedReasons"}
        ),
        optional=frozenset(
            {"testedContractRange", "providerVersion", "schemaFingerprint"}
        ),
    )
    if table["provider"] != "codex":
        raise ProtocolError(f"{path}.provider must be codex")
    status = _enum(
        table["status"],
        f"{path}.status",
        frozenset({"neutral", "available", "degraded"}),
    )
    features = _array(table["features"], f"{path}.features", maximum=MAX_MODEL_FEATURES)
    validated_features = [
        _string(item, f"{path}.features[{index}]", maximum=256)
        for index, item in enumerate(features)
    ]
    if len(validated_features) != len(set(validated_features)):
        raise ProtocolError(f"{path}.features contains duplicates")
    _validate_encoded_limit(features, f"{path}.features", MAX_MODEL_FEATURE_BYTES)

    reasons = _array(
        table["degradedReasons"],
        f"{path}.degradedReasons",
        maximum=MAX_MODEL_DEGRADED_REASONS,
    )
    for index, reason in enumerate(reasons):
        _validate_model_degradation(reason, f"{path}.degradedReasons[{index}]")
    _validate_encoded_limit(
        reasons, f"{path}.degradedReasons", MAX_MODEL_DEGRADED_REASON_BYTES
    )

    available = table["available"]
    if available is None:
        if (
            status != "neutral"
            or features
            or reasons
            or set(table)
            != {"provider", "status", "available", "features", "degradedReasons"}
        ):
            raise ProtocolError(f"{path} neutral capability fields are inconsistent")
        return table

    available = _boolean(available, f"{path}.available")
    if "testedContractRange" not in table:
        raise ProtocolError(f"{path}.testedContractRange is required")
    contract_path = f"{path}.testedContractRange"
    contract = _exact_object(
        table["testedContractRange"],
        contract_path,
        required=frozenset({"minimum", "maximum"}),
    )
    _string(contract["minimum"], f"{contract_path}.minimum", maximum=256)
    _string(contract["maximum"], f"{contract_path}.maximum", maximum=256)
    if "providerVersion" in table:
        _string(table["providerVersion"], f"{path}.providerVersion", maximum=256)
    if "schemaFingerprint" in table:
        _hash(table["schemaFingerprint"], f"{path}.schemaFingerprint")
    expected_status = "degraded" if not available or reasons else "available"
    if status != expected_status:
        raise ProtocolError(f"{path}.status is inconsistent with capability state")
    if not available and not reasons:
        raise ProtocolError(f"{path}.degradedReasons must explain unavailable provider")
    return table


def _validate_truncation_summary(
    value: object,
    path: str,
    *,
    expected_limit: int,
    expected_byte_limit: int,
    expected_emitted_count: int | None = None,
    maximum_source_count: int = MAX_JSON_ARRAY_ITEMS,
) -> dict[str, Any]:
    table = _exact_object(
        value,
        path,
        required=frozenset(
            {"truncated", "sourceCount", "emittedCount", "limit", "byteLimit"}
        ),
    )
    truncated = _boolean(table["truncated"], f"{path}.truncated")
    source_count = _integer(table["sourceCount"], f"{path}.sourceCount")
    emitted_count = _integer(table["emittedCount"], f"{path}.emittedCount")
    limit = _integer(table["limit"], f"{path}.limit")
    byte_limit = _integer(table["byteLimit"], f"{path}.byteLimit")
    if limit != expected_limit or byte_limit != expected_byte_limit:
        raise ProtocolError(f"{path} contains incompatible limits")
    if expected_emitted_count is not None and emitted_count != expected_emitted_count:
        raise ProtocolError(f"{path}.emittedCount is inconsistent with the model")
    if emitted_count > limit or source_count < emitted_count:
        raise ProtocolError(f"{path} contains inconsistent counts")
    if source_count > 0 and emitted_count == 0:
        raise ProtocolError(f"{path} cannot omit every bounded source item")
    if source_count > maximum_source_count:
        raise ProtocolError(f"{path}.sourceCount exceeds the source collection limit")
    if truncated is not (emitted_count < source_count):
        raise ProtocolError(f"{path}.truncated is inconsistent with its counts")
    return table


def _validate_error_warning(value: object, path: str, host_id: str) -> None:
    table = _exact_object(
        value,
        path,
        required=frozenset(
            {"source", "code", "message", "scope", "retryable", "observedAt"}
        ),
        optional=frozenset({"hostId", "provider", "sessionKey", "details"}),
    )
    _string(table["code"], f"{path}.code", maximum=128)
    _string(table["message"], f"{path}.message", maximum=4096)
    _enum(table["scope"], f"{path}.scope", _ERROR_SCOPES)
    _boolean(table["retryable"], f"{path}.retryable")
    _integer(table["observedAt"], f"{path}.observedAt")
    if (
        "hostId" in table
        and _canonical_uuid(table["hostId"], f"{path}.hostId") != host_id
    ):
        raise ProtocolError(f"{path}.hostId belongs to another host")
    if "provider" in table and table["provider"] != "codex":
        raise ProtocolError(f"{path}.provider must be codex")
    if "sessionKey" in table:
        _, key_host, key_provider, _ = _canonical_session_key(
            table["sessionKey"], f"{path}.sessionKey"
        )
        if key_host != host_id or key_provider != "codex":
            raise ProtocolError(
                f"{path}.sessionKey is not a Codex identity on this host"
            )
    if "details" in table:
        _details(table["details"], f"{path}.details")


def _validate_model_warnings(
    warnings: list[Any],
    *,
    host_id: str,
    capability: dict[str, Any],
    diagnostic_truncation: dict[str, dict[str, Any]],
    session_truncation: dict[str, Any],
) -> None:
    expected_capability_warnings: list[dict[str, Any]] = []
    for reason in capability["degradedReasons"]:
        warning: dict[str, Any] = {
            "source": "capability",
            "code": reason["code"],
            "retryable": reason["retryable"],
        }
        if "feature" in reason:
            warning["feature"] = reason["feature"]
        expected_capability_warnings.append(warning)

    capability_warnings: list[dict[str, Any]] = []
    error_count = 0
    model_warnings: list[dict[str, Any]] = []
    phase = 0
    for index, item in enumerate(warnings):
        path = f"model.warnings[{index}]"
        table = _object(item, path)
        source = table.get("source")
        if source == "capability":
            if phase != 0:
                raise ProtocolError("model.warnings sources are not in canonical order")
            table = _exact_object(
                table,
                path,
                required=frozenset({"source", "code", "retryable"}),
                optional=frozenset({"feature"}),
            )
            _string(table["code"], f"{path}.code", maximum=128)
            _boolean(table["retryable"], f"{path}.retryable")
            if "feature" in table:
                _string(table["feature"], f"{path}.feature", maximum=256)
            capability_warnings.append(table)
        elif source == "error":
            if phase > 1:
                raise ProtocolError("model.warnings sources are not in canonical order")
            phase = 1
            _validate_error_warning(table, path, host_id)
            error_count += 1
        elif source == "model":
            phase = 2
            model_warnings.append(table)
        else:
            raise ProtocolError(f"{path}.source has unsupported value")

    if capability_warnings != expected_capability_warnings:
        raise ProtocolError("model capability warnings are inconsistent")
    if error_count > MAX_MODEL_ERRORS:
        raise ProtocolError("model contains too many error warnings")

    summaries = diagnostic_truncation
    if summaries["features"]["emittedCount"] != len(capability["features"]):
        raise ProtocolError("model feature truncation count is inconsistent")
    if summaries["degradedReasons"]["emittedCount"] != len(
        capability["degradedReasons"]
    ):
        raise ProtocolError("model degradation truncation count is inconsistent")
    if summaries["errors"]["emittedCount"] != error_count:
        raise ProtocolError("model error truncation count is inconsistent")
    core_warning_count = len(capability_warnings) + error_count
    if summaries["warnings"]["emittedCount"] != core_warning_count:
        raise ProtocolError("model warning truncation count is inconsistent")
    if summaries["warnings"]["sourceCount"] != (
        summaries["degradedReasons"]["sourceCount"] + summaries["errors"]["sourceCount"]
    ):
        raise ProtocolError("model warning source count is inconsistent")

    expected_model_warnings: list[dict[str, Any]] = []
    if any(summary["truncated"] for summary in summaries.values()):
        expected_model_warnings.append(
            {
                "source": "model",
                "code": "model_diagnostics_truncated",
                "message": _MODEL_DIAGNOSTICS_MESSAGE,
                "retryable": False,
                "counts": deepcopy(summaries),
            }
        )
    if session_truncation["truncated"]:
        expected_model_warnings.append(
            {
                "source": "model",
                "code": "model_sessions_truncated",
                "message": _MODEL_SESSIONS_MESSAGE,
                "retryable": False,
                "details": {
                    "emittedCount": session_truncation["emittedCount"],
                    "retainedCount": session_truncation["sourceCount"],
                },
                "limit": session_truncation["limit"],
                "byteLimit": session_truncation["byteLimit"],
            }
        )
    if model_warnings != expected_model_warnings:
        raise ProtocolError("model truncation warnings are inconsistent")


def _validate_snapshot_model(value: object) -> None:
    path = "model"
    table = _exact_object(
        value,
        path,
        required=frozenset(
            {
                "modelVersion",
                "sourceSchemaVersion",
                "sourceProtocolVersion",
                "generatedAt",
                "host",
                "sessions",
                "codexCapability",
                "warnings",
                "diagnosticTruncation",
                "truncation",
            }
        ),
    )
    for key, expected in (
        ("modelVersion", 1),
        ("sourceSchemaVersion", SCHEMA_VERSION),
        ("sourceProtocolVersion", PROTOCOL_VERSION),
    ):
        if _integer(table[key], f"{path}.{key}") != expected:
            raise ProtocolError(f"{path}.{key} is incompatible")
    _integer(table["generatedAt"], f"{path}.generatedAt")
    host_id = _validate_model_host(table["host"])

    sessions = _array(table["sessions"], f"{path}.sessions", maximum=MAX_MODEL_SESSIONS)
    for index, session in enumerate(sessions):
        _validate_model_session(session, f"{path}.sessions[{index}]", host_id)
    session_keys = [session["sessionKey"] for session in sessions]
    if len(session_keys) != len(set(session_keys)):
        raise ProtocolError("model.sessions contains duplicate sessionKey values")
    expected_order = sorted(
        sessions, key=lambda session: (-session["recencyAt"], session["sessionKey"])
    )
    if sessions != expected_order:
        raise ProtocolError("model.sessions are not in canonical recency order")
    _validate_encoded_limit(sessions, f"{path}.sessions", MAX_MODEL_SESSION_BYTES)

    capability = _validate_model_capability(table["codexCapability"])
    warnings = _array(table["warnings"], f"{path}.warnings", maximum=MAX_MODEL_WARNINGS)
    _validate_encoded_limit(warnings, f"{path}.warnings", MAX_MODEL_WARNING_BYTES)

    diagnostic_table = _exact_object(
        table["diagnosticTruncation"],
        f"{path}.diagnosticTruncation",
        required=frozenset({"features", "degradedReasons", "errors", "warnings"}),
    )
    diagnostic_truncation = {
        "features": _validate_truncation_summary(
            diagnostic_table["features"],
            f"{path}.diagnosticTruncation.features",
            expected_limit=MAX_MODEL_FEATURES,
            expected_byte_limit=MAX_MODEL_FEATURE_BYTES,
        ),
        "degradedReasons": _validate_truncation_summary(
            diagnostic_table["degradedReasons"],
            f"{path}.diagnosticTruncation.degradedReasons",
            expected_limit=MAX_MODEL_DEGRADED_REASONS,
            expected_byte_limit=MAX_MODEL_DEGRADED_REASON_BYTES,
        ),
        "errors": _validate_truncation_summary(
            diagnostic_table["errors"],
            f"{path}.diagnosticTruncation.errors",
            expected_limit=MAX_MODEL_ERRORS,
            expected_byte_limit=MAX_MODEL_WARNING_BYTES - 16 * 1024,
            maximum_source_count=MAX_SNAPSHOT_RECORDS,
        ),
        "warnings": _validate_truncation_summary(
            diagnostic_table["warnings"],
            f"{path}.diagnosticTruncation.warnings",
            expected_limit=MAX_MODEL_WARNINGS - 2,
            expected_byte_limit=MAX_MODEL_WARNING_BYTES - 16 * 1024,
            maximum_source_count=MAX_JSON_ARRAY_ITEMS * 2,
        ),
    }
    if capability["available"] is None:
        for name in ("features", "degradedReasons"):
            summary = diagnostic_truncation[name]
            if summary["sourceCount"] != 0 or summary["emittedCount"] != 0:
                raise ProtocolError(
                    "model neutral capability cannot claim source diagnostics"
                )

    truncation_table = _object(table["truncation"], f"{path}.truncation")
    session_limit = _integer(
        _required(truncation_table, "limit", f"{path}.truncation"),
        f"{path}.truncation.limit",
    )
    if not 1 <= session_limit <= MAX_MODEL_SESSIONS:
        raise ProtocolError("model.truncation.limit is outside the model bounds")
    session_truncation = _validate_truncation_summary(
        truncation_table,
        f"{path}.truncation",
        expected_limit=session_limit,
        expected_byte_limit=MAX_MODEL_SESSION_BYTES,
        expected_emitted_count=len(sessions),
        maximum_source_count=MAX_SNAPSHOT_RECORDS,
    )
    _validate_model_warnings(
        warnings,
        host_id=host_id,
        capability=capability,
        diagnostic_truncation=diagnostic_truncation,
        session_truncation=session_truncation,
    )


def _model_collection(value: object, path: str) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise ProtocolError(f"{path} must be an array collection")
    return deepcopy(list(value))


@dataclass(slots=True)
class SnapshotModel:
    """Small, deterministic, Codex-only model safe for a DMS bridge."""

    generated_at: int
    host: dict[str, Any]
    sessions: tuple[dict[str, Any], ...]
    codex_capability: dict[str, Any]
    warnings: tuple[dict[str, Any], ...]
    diagnostic_truncation: dict[str, dict[str, Any]]
    source_session_count: int
    session_limit: int

    @property
    def truncated(self) -> bool:
        return len(self.sessions) < self.source_session_count

    def to_dict(self) -> dict[str, Any]:
        session_truncation = _truncation_summary(
            source_count=self.source_session_count,
            emitted_count=len(self.sessions),
            limit=self.session_limit,
            byte_limit=MAX_MODEL_SESSION_BYTES,
        )
        value: dict[str, Any] = {
            "modelVersion": 1,
            "sourceSchemaVersion": SCHEMA_VERSION,
            "sourceProtocolVersion": PROTOCOL_VERSION,
            "generatedAt": self.generated_at,
            "host": deepcopy(self.host),
            "sessions": _model_collection(self.sessions, "model.sessions"),
            "codexCapability": deepcopy(self.codex_capability),
            "warnings": _model_collection(self.warnings, "model.warnings"),
            "diagnosticTruncation": deepcopy(self.diagnostic_truncation),
            "truncation": session_truncation,
        }
        _validate_json_tree(value, "model")
        _validate_snapshot_model(value)
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError) as error:
            raise ProtocolError("projected model contains invalid JSON") from error
        if len(encoded) > MAX_JSON_BYTES:
            raise ProtocolError(
                f"projected model exceeds the {MAX_JSON_BYTES}-byte limit"
            )
        return value

    def to_json(self) -> str:
        value = self.to_dict()
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError) as error:
            raise ProtocolError("projected model contains invalid JSON") from error
        return encoded.decode("utf-8")


def _build_model(
    snapshot: dict[str, Any],
    sessions: list[dict[str, Any]],
    *,
    source_count: int,
    limit: int,
) -> SnapshotModel:
    capability, capability_truncation = _adapt_capability(snapshot["capabilities"])
    warnings, diagnostic_truncation = _diagnostics(
        capability, snapshot["errors"], capability_truncation
    )
    if len(sessions) < source_count:
        warnings.append(
            {
                "source": "model",
                "code": "model_sessions_truncated",
                "message": _MODEL_SESSIONS_MESSAGE,
                "retryable": False,
                "details": {
                    "emittedCount": len(sessions),
                    "retainedCount": source_count,
                },
                "limit": limit,
                "byteLimit": MAX_MODEL_SESSION_BYTES,
            }
        )
    return SnapshotModel(
        generated_at=snapshot["generatedAt"],
        host=deepcopy(snapshot["host"]),
        sessions=tuple(deepcopy(sessions)),
        codex_capability=capability,
        warnings=tuple(warnings),
        diagnostic_truncation=diagnostic_truncation,
        source_session_count=source_count,
        session_limit=limit,
    )


def parse_snapshot(
    raw: str | bytes | bytearray, *, max_sessions: int = MAX_MODEL_SESSIONS
) -> SnapshotModel:
    """Validate Snapshot v1 and project a bounded deterministic Codex model."""

    if (
        isinstance(max_sessions, bool)
        or not isinstance(max_sessions, int)
        or not 1 <= max_sessions <= MAX_MODEL_SESSIONS
    ):
        raise ValueError(f"max_sessions must be between 1 and {MAX_MODEL_SESSIONS}")
    snapshot = _validated_snapshot(raw)
    projects = {project["projectId"]: project for project in snapshot["projects"]}
    locations = {location["locationId"]: location for location in snapshot["locations"]}
    projected = [
        _project_session(session, projects, locations)
        for session in snapshot["sessions"]
        if session["provider"] == "codex"
    ]
    projected.sort(key=lambda session: (-session["recencyAt"], session["sessionKey"]))
    source_count = len(projected)
    selected = _bounded_encoded_items(
        projected,
        count_limit=max_sessions,
        byte_limit=MAX_MODEL_SESSION_BYTES,
    )
    model = _build_model(
        snapshot,
        selected,
        source_count=source_count,
        limit=max_sessions,
    )
    model.to_json()
    return model
