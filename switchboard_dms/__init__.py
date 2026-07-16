"""Public, frontend-owned Switchboard snapshot model."""

from .protocol import (
    MAX_JSON_BYTES,
    MAX_MODEL_SESSIONS,
    ProtocolError,
    SnapshotModel,
    parse_snapshot,
)

__all__ = [
    "MAX_JSON_BYTES",
    "MAX_MODEL_SESSIONS",
    "ProtocolError",
    "SnapshotModel",
    "parse_snapshot",
]
