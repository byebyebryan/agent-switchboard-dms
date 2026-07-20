"""Stable JSON bridge between DMS and the public ``swbctl`` Fleet/actions CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from dataclasses import dataclass
from typing import Sequence
from uuid import UUID

from .process import ProcessRunError, run_process
from .protocol import (
    MAX_JSON_BYTES,
    MAX_MODEL_SESSIONS,
    ProtocolError,
    parse_fleet,
    parse_presentation_plan,
    parse_session_action,
)

BRIDGE_VERSION = 3
DEFAULT_TIMEOUT_MS = 10_000
MIN_TIMEOUT_MS = 100
MAX_TIMEOUT_MS = 60_000
DEFAULT_MAX_SESSIONS = MAX_MODEL_SESSIONS
MAX_BRIDGE_BYTES = MAX_JSON_BYTES
_INTERNAL_ERROR_PAYLOAD = (
    b'{"bridgeVersion":3,"error":{"code":"bridge_internal_error",'
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


def fleet_argv(executable: str, *, refresh: bool) -> list[str]:
    if refresh:
        return [executable, "fleet", "--refresh", "--json"]
    return [executable, "fleet", "--json"]


def prepare_open_argv(
    executable: str,
    *,
    session_key: str,
    host_id: str,
    request_id: str,
    can_focus_desktop: bool = True,
    can_launch_terminal: bool = True,
) -> list[str]:
    argv = [
        executable,
        "prepare-open",
        session_key,
        "--host",
        host_id,
        "--request-id",
        request_id,
    ]
    if can_focus_desktop:
        argv.append("--can-focus-desktop")
    if can_launch_terminal:
        argv.append("--can-launch-terminal")
    argv.append("--json")
    return argv


def prepare_task_argv(
    executable: str,
    *,
    task_id: str,
    host_id: str,
    request_id: str,
    create: bool = False,
    project_id: str | None = None,
    title: str | None = None,
    checkout_id: str | None = None,
    provider: str | None = None,
    can_focus_desktop: bool = True,
    can_launch_terminal: bool = True,
) -> list[str]:
    argv = [
        executable,
        "prepare-task",
        task_id,
        "--host",
        host_id,
    ]
    if create:
        assert project_id is not None and title is not None and provider is not None
        argv.extend(
            [
                "--create",
                "--project",
                project_id,
                "--title",
                title,
            ]
        )
        if checkout_id is not None:
            argv.extend(["--checkout", checkout_id])
        argv.extend(["--provider", provider])
    elif provider is not None:
        argv.extend(["--provider", provider])
    argv.extend(
        [
            "--request-id",
            request_id,
        ]
    )
    if can_focus_desktop:
        argv.append("--can-focus-desktop")
    if can_launch_terminal:
        argv.append("--can-launch-terminal")
    argv.append("--json")
    return argv


def prepare_history_argv(
    executable: str,
    *,
    project_id: str,
    host_id: str,
    checkout_id: str | None,
    request_id: str,
    can_focus_desktop: bool = True,
    can_launch_terminal: bool = True,
) -> list[str]:
    argv = [
        executable,
        "prepare-history",
        "--project",
        project_id,
        "--host",
        host_id,
    ]
    if checkout_id is not None:
        argv.extend(["--checkout", checkout_id])
    argv.extend(["--request-id", request_id])
    if can_focus_desktop:
        argv.append("--can-focus-desktop")
    if can_launch_terminal:
        argv.append("--can-launch-terminal")
    argv.append("--json")
    return argv


def stop_session_argv(executable: str, *, session_key: str, host_id: str) -> list[str]:
    return [executable, "stop-session", session_key, "--host", host_id, "--json"]


def select_surface_argv(
    executable: str, *, surface_id: str, host_id: str, tmux_client: str
) -> list[str]:
    return [
        executable,
        "select-surface",
        surface_id,
        "--host",
        host_id,
        "--client",
        tmux_client,
    ]


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


def _plan_success(plan: dict[str, object]) -> dict[str, object]:
    return {
        "bridgeVersion": BRIDGE_VERSION,
        "ok": True,
        "plan": plan,
    }


def _action_success(surface_id: str) -> dict[str, object]:
    return {
        "bridgeVersion": BRIDGE_VERSION,
        "ok": True,
        "action": {"kind": "selected", "surfaceId": surface_id},
    }


def _json_syntax_is_valid(text: str) -> bool:
    def reject_constant(_value: str) -> None:
        raise ValueError

    try:
        json.loads(text, parse_constant=reject_constant)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return False
    return True


def _json_payload(stdout: bytes, *, invalid_code: str, invalid_message: str) -> bytes:
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
            invalid_code,
            invalid_message,
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
    prepare_open: str | None = None,
    prepare_task: str | None = None,
    create_task: bool = False,
    prepare_history: str | None = None,
    project_id: str | None = None,
    task_title: str | None = None,
    checkout_id: str | None = None,
    provider: str | None = None,
    request_id: str | None = None,
    prepare_can_focus_desktop: bool = True,
    prepare_can_launch_terminal: bool = True,
    select_surface: str | None = None,
    tmux_client: str | None = None,
    stop_session: str | None = None,
    action_host_id: str | None = None,
) -> dict[str, object]:
    try:
        if any(
            target is not None
            for target in (prepare_open, prepare_task, prepare_history)
        ):
            assert request_id is not None
            assert action_host_id is not None
            if prepare_open is not None:
                argv = prepare_open_argv(
                    executable,
                    session_key=prepare_open,
                    host_id=action_host_id,
                    request_id=request_id,
                    can_focus_desktop=prepare_can_focus_desktop,
                    can_launch_terminal=prepare_can_launch_terminal,
                )
            elif prepare_task is not None:
                argv = prepare_task_argv(
                    executable,
                    task_id=prepare_task,
                    host_id=action_host_id,
                    create=create_task,
                    project_id=project_id,
                    title=task_title,
                    checkout_id=checkout_id,
                    provider=provider,
                    request_id=request_id,
                    can_focus_desktop=prepare_can_focus_desktop,
                    can_launch_terminal=prepare_can_launch_terminal,
                )
            else:
                assert prepare_history is not None
                argv = prepare_history_argv(
                    executable,
                    project_id=prepare_history,
                    host_id=action_host_id,
                    checkout_id=checkout_id,
                    request_id=request_id,
                    can_focus_desktop=prepare_can_focus_desktop,
                    can_launch_terminal=prepare_can_launch_terminal,
                )
            output = run_process(
                argv,
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
            payload = _json_payload(
                output.stdout,
                invalid_code="plan_invalid_protocol",
                invalid_message="swbctl stdout was not one PresentationPlan document.",
            )
            try:
                plan = parse_presentation_plan(payload)
            except ProtocolError:
                return _failure(
                    BridgeError(
                        "plan_invalid_protocol",
                        "swbctl stdout was not a compatible PresentationPlan v2 document.",
                        False,
                    )
                )
            return _plan_success(plan)

        if stop_session is not None:
            assert action_host_id is not None
            output = run_process(
                stop_session_argv(
                    executable,
                    session_key=stop_session,
                    host_id=action_host_id,
                ),
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
            payload = _json_payload(
                output.stdout,
                invalid_code="action_invalid_protocol",
                invalid_message="swbctl stdout was not one SessionAction document.",
            )
            try:
                action = parse_session_action(payload)
            except ProtocolError:
                return _failure(
                    BridgeError(
                        "action_invalid_protocol",
                        "swbctl stdout was not a compatible session action.",
                        False,
                    )
                )
            return {
                "bridgeVersion": BRIDGE_VERSION,
                "ok": True,
                "action": action,
            }

        if select_surface is not None:
            assert tmux_client is not None and action_host_id is not None
            output = run_process(
                select_surface_argv(
                    executable,
                    surface_id=select_surface,
                    host_id=action_host_id,
                    tmux_client=tmux_client,
                ),
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
            if output.stdout:
                return _failure(
                    BridgeError(
                        "action_unexpected_output",
                        "swbctl returned unexpected action output.",
                        False,
                    )
                )
            return _action_success(select_surface)

        output = run_process(
            fleet_argv(executable, refresh=refresh),
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

        payload = _json_payload(
            output.stdout,
            invalid_code="fleet_invalid_json",
            invalid_message="swbctl stdout was not one Fleet document.",
        )
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return _failure(
                BridgeError(
                    "fleet_invalid_utf8",
                    "swbctl stdout was not valid UTF-8.",
                    False,
                )
            )
        if not _json_syntax_is_valid(text):
            return _failure(
                BridgeError(
                    "fleet_invalid_json",
                    "swbctl stdout was not valid JSON.",
                    False,
                )
            )
        try:
            model = parse_fleet(text, max_sessions=max_sessions)
        except ProtocolError:
            return _failure(
                BridgeError(
                    "fleet_invalid_protocol",
                    "swbctl stdout was not a compatible Fleet v1 document.",
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


def _uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a canonical non-nil UUID") from error
    if parsed.int == 0 or str(parsed) != value:
        raise argparse.ArgumentTypeError("expected a canonical non-nil UUID")
    return value


def _session_key(value: str) -> str:
    parts = value.split(":")
    if len(parts) != 3 or parts[1] not in {"codex", "claude"}:
        raise argparse.ArgumentTypeError("expected a canonical session key")
    _uuid(parts[0])
    _uuid(parts[2])
    if len(value) > 512:
        raise argparse.ArgumentTypeError("expected a canonical session key")
    return value


def _claude_session_key(value: str) -> str:
    parsed = _session_key(value)
    if parsed.split(":", 2)[1] != "claude":
        raise argparse.ArgumentTypeError("expected a canonical Claude session key")
    return parsed


def _tmux_client(value: str) -> str:
    if (
        not value
        or len(value) > 1024
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise argparse.ArgumentTypeError("expected a bounded tmux client ID")
    return value


def _task_title(value: str) -> str:
    normalized = " ".join(value.split())
    if (
        not normalized
        or len(normalized) > 256
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise argparse.ArgumentTypeError("expected a nonempty bounded task title")
    return normalized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="switchboard-bridge",
        description="Read and validate one bounded Switchboard fleet.",
    )
    parser.add_argument("--swbctl", default="swbctl", type=_executable)
    parser.add_argument("--refresh", action="store_true")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--prepare-open", type=_session_key)
    actions.add_argument("--prepare-task", type=_uuid)
    actions.add_argument("--prepare-history", type=_uuid)
    actions.add_argument("--select-surface", type=_uuid)
    actions.add_argument("--stop-session", type=_claude_session_key)
    parser.add_argument("--create-task", action="store_true")
    parser.add_argument("--project", type=_uuid)
    parser.add_argument("--title", type=_task_title)
    parser.add_argument("--checkout", type=_uuid)
    parser.add_argument("--provider", choices=("codex", "claude"))
    parser.add_argument("--host", type=_uuid)
    parser.add_argument("--request-id", type=_uuid)
    parser.add_argument("--tmux-client", type=_tmux_client)
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
    parser = build_parser()
    args = parser.parse_args(argv)
    preparing = any(
        target is not None
        for target in (args.prepare_open, args.prepare_task, args.prepare_history)
    )
    if preparing != (args.request_id is not None):
        parser.error("a prepare action and --request-id must be supplied together")
    acting = (
        preparing or args.select_surface is not None or args.stop_session is not None
    )
    if acting != (args.host is not None):
        parser.error("an owning-host action and --host must be supplied together")
    if args.create_task and args.prepare_task is None:
        parser.error("--create-task requires --prepare-task")
    if args.create_task and (
        args.project is None or args.title is None or args.provider is None
    ):
        parser.error("--create-task requires --project, --title, and --provider")
    if args.prepare_history is not None and args.provider is not None:
        parser.error("--provider does not apply to --prepare-history")
    if args.prepare_history is not None and (
        args.project is not None or args.title is not None
    ):
        parser.error("--prepare-history accepts only optional --checkout context")
    if (
        not args.create_task
        and args.prepare_history is None
        and (
            args.project is not None
            or args.title is not None
            or args.checkout is not None
        )
    ):
        parser.error(
            "--project, --title, and --checkout require a create or history action"
        )
    if args.prepare_task is None and args.provider is not None:
        parser.error("--provider requires --prepare-task")
    if (args.select_surface is None) != (args.tmux_client is None):
        parser.error("--select-surface and --tmux-client must be supplied together")
    if (
        preparing or args.select_surface is not None or args.stop_session is not None
    ) and args.refresh:
        parser.error("--refresh applies only to fleet reads")
    try:
        response = run_bridge(
            executable=args.swbctl,
            refresh=args.refresh,
            timeout_ms=args.timeout_ms,
            max_sessions=args.max_sessions,
            prepare_open=args.prepare_open,
            prepare_task=args.prepare_task,
            create_task=args.create_task,
            prepare_history=args.prepare_history,
            project_id=args.project,
            task_title=args.title,
            checkout_id=args.checkout,
            provider=args.provider,
            request_id=args.request_id,
            select_surface=args.select_surface,
            tmux_client=args.tmux_client,
            stop_session=args.stop_session,
            action_host_id=args.host,
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
