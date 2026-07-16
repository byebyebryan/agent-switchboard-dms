"""Stable JSON bridge between DMS and the public ``swbctl`` snapshot CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Sequence

from .process import ProcessRunError, run_process
from .protocol import MAX_JSON_BYTES, MAX_MODEL_SESSIONS, ProtocolError, parse_snapshot

BRIDGE_VERSION = 1
DEFAULT_TIMEOUT_MS = 10_000
MIN_TIMEOUT_MS = 100
MAX_TIMEOUT_MS = 60_000
DEFAULT_MAX_SESSIONS = MAX_MODEL_SESSIONS
MAX_BRIDGE_BYTES = MAX_JSON_BYTES
_INTERNAL_ERROR_PAYLOAD = (
    b'{"bridgeVersion":1,"error":{"code":"bridge_internal_error",'
    b'"message":"The bridge encountered an internal error.",'
    b'"retryable":false},"ok":false}\n'
)


@dataclass(frozen=True, slots=True)
class BridgeError:
    code: str
    message: str
    retryable: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


def snapshot_argv(executable: str, *, refresh: bool) -> list[str]:
    if refresh:
        return [executable, "snapshot", "--reconcile", "full", "--json"]
    return [executable, "snapshot", "--json"]


def _failure(error: BridgeError) -> dict[str, object]:
    return {
        "bridgeVersion": BRIDGE_VERSION,
        "ok": False,
        "error": error.to_dict(),
    }


def _success(model: object) -> dict[str, object]:
    return {
        "bridgeVersion": BRIDGE_VERSION,
        "ok": True,
        "model": model,
    }


def _json_syntax_is_valid(text: str) -> bool:
    def reject_constant(_value: str) -> None:
        raise ValueError

    try:
        json.loads(text, parse_constant=reject_constant)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return False
    return True


def _snapshot_payload(stdout: bytes) -> bytes:
    """Return the JSON bytes from the bridge's strict single-record framing.

    ``swbctl`` may omit its final LF or emit exactly one. JSON whitespace is
    deliberately not accepted outside the document because accepting it would
    make the stdout byte limit and one-record framing ambiguous.
    """

    if stdout.endswith(b"\n"):
        payload = stdout[:-1]
    else:
        payload = stdout
    json_whitespace = b" \t\r\n"
    if not payload or payload[:1] in json_whitespace or payload[-1:] in json_whitespace:
        raise ProcessRunError(
            "snapshot_invalid_json",
            "swbctl stdout was not one JSON document.",
            retryable=False,
        )
    if len(payload) > MAX_JSON_BYTES:
        raise ProcessRunError(
            "stdout_overflow",
            "swbctl stdout exceeded the bridge limit.",
            retryable=False,
        )
    return payload


def run_bridge(
    *,
    executable: str,
    refresh: bool,
    timeout_ms: int,
    max_sessions: int,
) -> dict[str, object]:
    try:
        output = run_process(
            snapshot_argv(executable, refresh=refresh),
            timeout_ms=timeout_ms,
        )
        if output.exit_code != 0:
            return _failure(
                BridgeError(
                    "swbctl_nonzero_exit",
                    f"swbctl exited with status {output.exit_code}.",
                    True,
                )
            )

        payload = _snapshot_payload(output.stdout)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return _failure(
                BridgeError(
                    "snapshot_invalid_utf8",
                    "swbctl stdout was not valid UTF-8.",
                    False,
                )
            )
        if not _json_syntax_is_valid(text):
            return _failure(
                BridgeError(
                    "snapshot_invalid_json",
                    "swbctl stdout was not valid JSON.",
                    False,
                )
            )
        try:
            model = parse_snapshot(text, max_sessions=max_sessions)
        except ProtocolError:
            return _failure(
                BridgeError(
                    "snapshot_invalid_protocol",
                    "swbctl stdout was not a compatible Snapshot v1 document.",
                    False,
                )
            )
        return _success(model.to_dict())
    except ProcessRunError as error:
        return _failure(BridgeError(error.code, error.message, error.retryable))


def serialize_response(response: dict[str, object]) -> tuple[int, bytes]:
    try:
        encoded = json.dumps(
            response,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        response = _failure(
            BridgeError(
                "bridge_serialization_failed",
                "The bridge could not serialize its response.",
                False,
            )
        )
        encoded = json.dumps(
            response,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    if len(encoded) + 1 > MAX_BRIDGE_BYTES:
        response = _failure(
            BridgeError(
                "bridge_output_overflow",
                "The bridge response exceeded the output limit.",
                False,
            )
        )
        encoded = json.dumps(
            response,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    return (0 if response.get("ok") is True else 1, encoded + b"\n")


def _bounded_integer(name: str, minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value, 10)
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from error
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"{name} must be between {minimum} and {maximum}"
            )
        return number

    return parse


def _executable(value: str) -> str:
    if not value or "\x00" in value:
        raise argparse.ArgumentTypeError("--swbctl must be one executable token")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="switchboard-bridge",
        description="Read and validate one bounded Switchboard snapshot.",
    )
    parser.add_argument("--swbctl", default="swbctl", type=_executable)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--timeout-ms",
        default=DEFAULT_TIMEOUT_MS,
        type=_bounded_integer("--timeout-ms", MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
    )
    parser.add_argument(
        "--max-sessions",
        default=DEFAULT_MAX_SESSIONS,
        type=_bounded_integer("--max-sessions", 1, MAX_MODEL_SESSIONS),
    )
    return parser


def _internal_failure() -> dict[str, object]:
    return _failure(
        BridgeError(
            "bridge_internal_error",
            "The bridge encountered an internal error.",
            False,
        )
    )


def _silence_stdout() -> None:
    """Prevent interpreter shutdown from repeating a failed stdout flush."""

    try:
        stdout_descriptor = sys.stdout.buffer.fileno()
        null_descriptor = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null_descriptor, stdout_descriptor)
        finally:
            os.close(null_descriptor)
    except Exception:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        response = run_bridge(
            executable=args.swbctl,
            refresh=args.refresh,
            timeout_ms=args.timeout_ms,
            max_sessions=args.max_sessions,
        )
    except Exception:
        response = _internal_failure()
    try:
        exit_code, payload = serialize_response(response)
    except Exception:
        exit_code, payload = 1, _INTERNAL_ERROR_PAYLOAD
    try:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
    except Exception:
        _silence_stdout()
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
