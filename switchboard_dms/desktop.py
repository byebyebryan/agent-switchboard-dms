"""Local desktop execution for validated Switchboard presentation plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence
from .bridge import (
    DEFAULT_MAX_SESSIONS,
    DEFAULT_TIMEOUT_MS,
    MAX_TIMEOUT_MS,
    MIN_TIMEOUT_MS,
    run_bridge,
)
from .process import ProcessRunError, ProcessOutput, run_process

ACTION_VERSION = 4
MAX_ACTION_BYTES = 16 * 1024
MAX_DESKTOP_TOKEN_LENGTH = 2048
MAX_WINDOW_HOST_LENGTH = 256
APP_ID_PREFIX = "com.agent_switchboard.surface.s"

ProcessRunner = Callable[..., ProcessOutput]
DetachedLauncher = Callable[[Sequence[str]], None]


class DesktopActionError(RuntimeError):
    """A validated plan could not be completed on the local desktop."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[:160]
        self.retryable = retryable


def _bounded_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("expected a bounded non-empty string")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError("control characters are not allowed")
    return value


def desktop_app_id(desktop_token: str, host_id: str) -> str:
    """Derive a valid, opaque Wayland application ID from a desktop token."""

    token = _bounded_text(desktop_token, maximum=MAX_DESKTOP_TOKEN_LENGTH)
    host = _bounded_text(host_id, maximum=36)
    digest = hashlib.sha256(f"{host}\0{token}".encode("utf-8")).hexdigest()
    return f"{APP_ID_PREFIX}{digest[:32]}"


def _window_id(window: object) -> int | None:
    if not isinstance(window, dict):
        return None
    value = window.get("id")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _title_matches(window: object, workspace_id: str | None, window_host: str) -> bool:
    if not isinstance(window, dict) or not workspace_id:
        return False
    title = window.get("title")
    if not isinstance(title, str):
        return False
    short_host = window_host.split(".", 1)[0]
    return title.startswith(f"{workspace_id}:") and title.casefold().endswith(
        f" @ {short_host}".casefold()
    )


def matching_niri_window_id(
    windows: object,
    *,
    app_id: str | None,
    workspace_id: str | None,
    window_host: str,
) -> int | None:
    """Choose one unambiguous managed niri window."""

    if not isinstance(windows, list):
        return None
    valid = [window for window in windows if _window_id(window) is not None]
    app_matches = (
        [window for window in valid if window.get("app_id") == app_id]
        if app_id is not None
        else []
    )
    if len(app_matches) == 1:
        return _window_id(app_matches[0])
    candidates = app_matches if app_matches else valid
    title_matches = [
        window
        for window in candidates
        if _title_matches(window, workspace_id, window_host)
    ]
    if len(title_matches) != 1:
        return None
    return _window_id(title_matches[0])


def focus_existing_window(
    plan: Mapping[str, object],
    *,
    host_id: str,
    window_host: str,
    timeout_ms: int,
    runner: ProcessRunner = run_process,
    environ: Mapping[str, str] = os.environ,
    which: Callable[[str], str | None] = shutil.which,
) -> bool:
    """Focus the plan's managed window, returning false on any safe miss."""

    niri = which("niri")
    if niri is None or not environ.get("NIRI_SOCKET"):
        return False
    token = plan.get("desktopToken")
    try:
        app_id = desktop_app_id(token, host_id) if isinstance(token, str) else None
        workspace = plan.get("workspaceId")
        workspace_id = (
            _bounded_text(workspace, maximum=1024)
            if isinstance(workspace, str)
            else None
        )
        host = _bounded_text(window_host, maximum=MAX_WINDOW_HOST_LENGTH)
        output = runner([niri, "msg", "--json", "windows"], timeout_ms=timeout_ms)
        if output.exit_code != 0 or output.stderr:
            return False
        windows = json.loads(output.stdout)
        window_id = matching_niri_window_id(
            windows,
            app_id=app_id,
            workspace_id=workspace_id,
            window_host=host,
        )
        if window_id is None:
            return False
        focused = runner(
            [niri, "msg", "action", "focus-window", "--id", str(window_id)],
            timeout_ms=timeout_ms,
        )
        return focused.exit_code == 0 and not focused.stdout and not focused.stderr
    except (ProcessRunError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False


def terminal_launch_argv(
    *,
    systemd_run: str,
    terminal: str,
    swbctl: str,
    surface_id: str,
    desktop_token: str,
    host_id: str,
) -> list[str]:
    return [
        systemd_run,
        "--user",
        "--scope",
        "--collect",
        "--quiet",
        "--",
        terminal,
        f"--class={desktop_app_id(desktop_token, host_id)}",
        "-e",
        swbctl,
        "attach-surface",
        surface_id,
        "--host",
        host_id,
    ]


def launch_detached(argv: Sequence[str]) -> None:
    try:
        subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            start_new_session=True,
        )
    except FileNotFoundError as error:
        raise DesktopActionError(
            "desktop_executable_not_found",
            "A required desktop executable was not found.",
            retryable=False,
        ) from error
    except PermissionError as error:
        raise DesktopActionError(
            "desktop_executable_permission_denied",
            "A required desktop executable is not executable.",
            retryable=False,
        ) from error
    except OSError as error:
        raise DesktopActionError(
            "desktop_launch_failed",
            "The terminal could not be started.",
            retryable=True,
        ) from error


def _bridge_error(response: Mapping[str, object]) -> DesktopActionError:
    error = response.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        retryable = error.get("retryable")
        if isinstance(code, str) and isinstance(message, str):
            return DesktopActionError(code, message, retryable=retryable is True)
    return DesktopActionError(
        "desktop_bridge_invalid",
        "The desktop helper received an invalid bridge response.",
        retryable=False,
    )


def _blocked_error(plan: Mapping[str, object]) -> DesktopActionError:
    error = plan.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        retryable = error.get("retryable")
        if isinstance(code, str) and isinstance(message, str):
            return DesktopActionError(code, message, retryable=retryable is True)
    return DesktopActionError(
        "desktop_plan_invalid",
        "The presentation plan could not be executed.",
        retryable=False,
    )


def _prepared_plan(
    *,
    swbctl: str,
    host_id: str,
    session_key: str | None,
    task_id: str | None,
    create_task: bool,
    reopen_task: bool,
    project_id: str | None,
    title: str | None,
    checkout_id: str | None,
    provider: str | None,
    history: bool,
    request_id: str,
    timeout_ms: int,
    can_focus_desktop: bool,
) -> dict[str, object]:
    response = run_bridge(
        executable=swbctl,
        refresh=False,
        timeout_ms=timeout_ms,
        max_sessions=DEFAULT_MAX_SESSIONS,
        prepare_open=session_key,
        prepare_task=task_id,
        create_task=create_task,
        reopen_task=reopen_task,
        prepare_history=(project_id if history else None),
        project_id=(project_id if create_task else None),
        task_title=title,
        checkout_id=checkout_id,
        provider=provider,
        request_id=request_id,
        prepare_can_focus_desktop=can_focus_desktop,
        prepare_can_launch_terminal=True,
        action_host_id=host_id,
    )
    if response.get("ok") is not True:
        raise _bridge_error(response)
    plan = response.get("plan")
    if not isinstance(plan, dict):
        raise DesktopActionError(
            "desktop_plan_invalid",
            "The presentation plan could not be executed.",
            retryable=False,
        )
    if plan.get("kind") == "blocked":
        raise _blocked_error(plan)
    if plan.get("hostId") != host_id:
        raise DesktopActionError(
            "desktop_plan_host_mismatch",
            "The presentation plan belongs to another host.",
            retryable=False,
        )
    return plan


def _attach(
    plan: Mapping[str, object],
    *,
    swbctl: str,
    host_id: str,
    terminal: str,
    which: Callable[[str], str | None],
    launcher: DetachedLauncher,
) -> dict[str, object]:
    surface_id = plan.get("surfaceId")
    desktop_token = plan.get("desktopToken")
    if not isinstance(surface_id, str) or not isinstance(desktop_token, str):
        raise DesktopActionError(
            "desktop_plan_invalid",
            "The attach plan omitted a required desktop locator.",
            retryable=False,
        )
    systemd_run = which("systemd-run")
    terminal_path = which(terminal)
    if systemd_run is None or terminal_path is None:
        raise DesktopActionError(
            "desktop_executable_not_found",
            "A required desktop executable was not found.",
            retryable=False,
        )
    launcher(
        terminal_launch_argv(
            systemd_run=systemd_run,
            terminal=terminal_path,
            swbctl=swbctl,
            surface_id=surface_id,
            desktop_token=desktop_token,
            host_id=host_id,
        )
    )
    return {"kind": "launched", "surfaceId": surface_id}


def _open_target(
    *,
    swbctl: str,
    terminal: str,
    host_id: str,
    session_key: str | None,
    task_id: str | None,
    create_task: bool,
    reopen_task: bool,
    project_id: str | None,
    title: str | None,
    checkout_id: str | None,
    provider: str | None,
    history: bool,
    window_host: str,
    timeout_ms: int,
    request_id: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    launcher: DetachedLauncher = launch_detached,
) -> dict[str, object]:
    """Prepare and execute one validated local presentation target."""

    target_count = sum((session_key is not None, task_id is not None, history))
    if target_count != 1:
        raise ValueError("exactly one session, task, or history target is required")
    if create_task and (
        task_id is None or project_id is None or title is None or provider is None
    ):
        raise ValueError("new task targets require project, title, and provider")
    if history and (project_id is None or provider is not None):
        raise ValueError("history targets require only project and optional checkout")

    request = request_id or str(uuid.uuid4())
    plan = _prepared_plan(
        swbctl=swbctl,
        host_id=host_id,
        session_key=session_key,
        task_id=task_id,
        create_task=create_task,
        reopen_task=reopen_task,
        project_id=project_id,
        title=title,
        checkout_id=checkout_id,
        provider=provider,
        history=history,
        request_id=request,
        timeout_ms=timeout_ms,
        can_focus_desktop=True,
    )
    kind = plan.get("kind")
    surface_id = plan.get("surfaceId")
    if kind == "attach":
        return _attach(
            plan,
            swbctl=swbctl,
            host_id=host_id,
            terminal=terminal,
            which=which,
            launcher=launcher,
        )
    if kind == "switch":
        client = plan.get("tmuxClient")
        if not isinstance(surface_id, str) or not isinstance(client, str):
            raise DesktopActionError(
                "desktop_plan_invalid",
                "The switch plan omitted a required surface locator.",
                retryable=False,
            )
        selected = run_bridge(
            executable=swbctl,
            refresh=False,
            timeout_ms=timeout_ms,
            max_sessions=DEFAULT_MAX_SESSIONS,
            select_surface=surface_id,
            tmux_client=client,
            action_host_id=host_id,
        )
        if selected.get("ok") is not True:
            raise _bridge_error(selected)
        if focus_existing_window(
            plan,
            host_id=host_id,
            window_host=window_host,
            timeout_ms=timeout_ms,
            which=which,
        ):
            return {"kind": "switched", "surfaceId": surface_id}
    elif kind == "focus":
        if not isinstance(surface_id, str):
            raise DesktopActionError(
                "desktop_plan_invalid",
                "The focus plan omitted a required surface locator.",
                retryable=False,
            )
        if focus_existing_window(
            plan,
            host_id=host_id,
            window_host=window_host,
            timeout_ms=timeout_ms,
            which=which,
        ):
            return {"kind": "focused", "surfaceId": surface_id}
    else:
        raise DesktopActionError(
            "desktop_plan_invalid",
            "The presentation plan kind is not executable.",
            retryable=False,
        )

    fallback = _prepared_plan(
        swbctl=swbctl,
        host_id=host_id,
        session_key=session_key,
        task_id=task_id,
        create_task=create_task,
        reopen_task=reopen_task,
        project_id=project_id,
        title=title,
        checkout_id=checkout_id,
        provider=provider,
        history=history,
        request_id=request,
        timeout_ms=timeout_ms,
        can_focus_desktop=False,
    )
    if fallback.get("kind") != "attach":
        raise DesktopActionError(
            "desktop_fallback_invalid",
            "Switchboard did not return an attach fallback after focus failed.",
            retryable=True,
        )
    return _attach(
        fallback,
        swbctl=swbctl,
        host_id=host_id,
        terminal=terminal,
        which=which,
        launcher=launcher,
    )


def open_session(
    *,
    swbctl: str,
    terminal: str,
    host_id: str,
    session_key: str,
    window_host: str,
    timeout_ms: int,
    request_id: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    launcher: DetachedLauncher = launch_detached,
) -> dict[str, object]:
    """Prepare and execute one existing local provider session action."""

    return _open_target(
        swbctl=swbctl,
        terminal=terminal,
        host_id=host_id,
        session_key=session_key,
        task_id=None,
        create_task=False,
        reopen_task=False,
        project_id=None,
        title=None,
        checkout_id=None,
        provider=None,
        history=False,
        window_host=window_host,
        timeout_ms=timeout_ms,
        request_id=request_id,
        which=which,
        launcher=launcher,
    )


def open_task(
    *,
    swbctl: str,
    terminal: str,
    host_id: str,
    task_id: str,
    window_host: str,
    timeout_ms: int,
    provider: str | None = None,
    create: bool = False,
    reopen: bool = False,
    project_id: str | None = None,
    title: str | None = None,
    checkout_id: str | None = None,
    request_id: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    launcher: DetachedLauncher = launch_detached,
) -> dict[str, object]:
    """Prepare and execute one existing or atomically-created task action."""

    return _open_target(
        swbctl=swbctl,
        terminal=terminal,
        host_id=host_id,
        session_key=None,
        task_id=task_id,
        create_task=create,
        reopen_task=reopen,
        project_id=project_id,
        title=title,
        checkout_id=checkout_id,
        provider=provider,
        history=False,
        window_host=window_host,
        timeout_ms=timeout_ms,
        request_id=request_id,
        which=which,
        launcher=launcher,
    )


def open_history(
    *,
    swbctl: str,
    terminal: str,
    host_id: str,
    project_id: str,
    checkout_id: str | None,
    window_host: str,
    timeout_ms: int,
    request_id: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    launcher: DetachedLauncher = launch_detached,
) -> dict[str, object]:
    """Open Claude's native history picker in one managed project surface."""

    return _open_target(
        swbctl=swbctl,
        terminal=terminal,
        host_id=host_id,
        session_key=None,
        task_id=None,
        create_task=False,
        reopen_task=False,
        project_id=project_id,
        title=None,
        checkout_id=checkout_id,
        provider=None,
        history=True,
        window_host=window_host,
        timeout_ms=timeout_ms,
        request_id=request_id,
        which=which,
        launcher=launcher,
    )


def stop_session(
    *,
    swbctl: str,
    host_id: str,
    session_key: str,
    timeout_ms: int,
) -> dict[str, object]:
    """Stop one core-revalidated launch-owned managed runtime."""

    response = run_bridge(
        executable=swbctl,
        refresh=False,
        timeout_ms=timeout_ms,
        max_sessions=DEFAULT_MAX_SESSIONS,
        stop_session=session_key,
        action_host_id=host_id,
    )
    if response.get("ok") is not True:
        raise _bridge_error(response)
    action = response.get("action")
    if not isinstance(action, dict):
        raise DesktopActionError(
            "desktop_action_invalid",
            "The stop action response was invalid.",
            retryable=False,
        )
    if action.get("status") == "blocked":
        raise _blocked_error(action)
    if action.get("kind") != "stop" or action.get("status") not in {
        "stopped",
        "already_stopped",
    }:
        raise DesktopActionError(
            "desktop_action_invalid",
            "The stop action response was invalid.",
            retryable=False,
        )
    return {"kind": "stopped", "status": action["status"]}


def close_task(
    *,
    swbctl: str,
    host_id: str,
    task_id: str,
    timeout_ms: int,
) -> dict[str, object]:
    """Close one task and project its independent runtime-cleanup result."""

    response = run_bridge(
        executable=swbctl,
        refresh=False,
        timeout_ms=timeout_ms,
        max_sessions=DEFAULT_MAX_SESSIONS,
        close_task=task_id,
        action_host_id=host_id,
    )
    if response.get("ok") is not True:
        raise _bridge_error(response)
    action = response.get("action")
    if not isinstance(action, dict):
        raise DesktopActionError(
            "desktop_action_invalid",
            "The task close response was invalid.",
            retryable=False,
        )
    if action.get("status") == "blocked":
        raise _blocked_error(action)
    if (
        action.get("kind") != "close"
        or action.get("status") not in {"closed", "already_closed"}
        or action.get("hostId") != host_id
        or action.get("taskId") != task_id
        or action.get("runtimeDisposition")
        not in {"no_session", "already_stopped", "stopped", "retained", "unknown"}
    ):
        raise DesktopActionError(
            "desktop_action_invalid",
            "The task close response was invalid.",
            retryable=False,
        )
    result: dict[str, object] = {
        "kind": "closed",
        "status": action["status"],
        "taskId": task_id,
        "runtimeDisposition": action["runtimeDisposition"],
    }
    warning = action.get("warning")
    if warning is not None:
        if not isinstance(warning, dict) or not all(
            isinstance(warning.get(field), expected)
            for field, expected in (
                ("code", str),
                ("message", str),
                ("retryable", bool),
            )
        ):
            raise DesktopActionError(
                "desktop_action_invalid",
                "The task close warning was invalid.",
                retryable=False,
            )
        result["warning"] = {
            "code": warning["code"],
            "message": warning["message"],
            "retryable": warning["retryable"],
        }
    return result


def _success(action: dict[str, object]) -> dict[str, object]:
    return {"actionVersion": ACTION_VERSION, "ok": True, "action": action}


def _failure(error: DesktopActionError) -> dict[str, object]:
    return {
        "actionVersion": ACTION_VERSION,
        "ok": False,
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
        },
    }


def serialize_response(response: Mapping[str, object]) -> tuple[int, bytes]:
    encoded = json.dumps(
        response,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) + 1 > MAX_ACTION_BYTES:
        response = _failure(
            DesktopActionError(
                "desktop_output_overflow",
                "The desktop helper response exceeded its output limit.",
                retryable=False,
            )
        )
        encoded = json.dumps(response, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
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
    if not value or "\x00" in value or len(value) > 4096:
        raise argparse.ArgumentTypeError("expected one bounded executable token")
    return value


def _window_host(value: str) -> str:
    try:
        return _bounded_text(value, maximum=MAX_WINDOW_HOST_LENGTH)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a bounded window host") from error


def _uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a canonical non-nil UUID") from error
    if parsed.int == 0 or str(parsed) != value:
        raise argparse.ArgumentTypeError("expected a canonical non-nil UUID")
    return value


def _session_key(value: str) -> str:
    parts = value.split(":")
    if len(parts) != 3 or parts[1] not in {"codex", "claude"}:
        raise argparse.ArgumentTypeError("expected a canonical local session key")
    _uuid(parts[0])
    _uuid(parts[2])
    if len(value) > 512:
        raise argparse.ArgumentTypeError("expected a canonical local session key")
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
        prog="switchboard-open",
        description="Open one validated local Switchboard presentation plan.",
    )
    parser.add_argument("--swbctl", default="swbctl", type=_executable)
    parser.add_argument("--terminal", default="ghostty", type=_executable)
    parser.add_argument("--window-host", required=True, type=_window_host)
    parser.add_argument("--host", dest="host_id", required=True, type=_uuid)
    parser.add_argument(
        "--timeout-ms",
        default=DEFAULT_TIMEOUT_MS,
        type=_bounded_integer("--timeout-ms", MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
    )
    parser.add_argument("--task", type=_uuid)
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--reopen", action="store_true")
    parser.add_argument("--project", type=_uuid)
    parser.add_argument("--title", type=_task_title)
    parser.add_argument("--checkout", type=_uuid)
    parser.add_argument("--provider", choices=("codex", "claude"))
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--stop", dest="stop_session", type=_session_key)
    parser.add_argument("--close-task", type=_uuid)
    parser.add_argument("session_key", nargs="?", type=_session_key)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    create_arguments = (args.project, args.title, args.provider)
    if args.history:
        if (
            args.project is None
            or args.task is not None
            or args.title is not None
            or args.provider is not None
            or args.create
        ):
            parser.error("--history requires --project and optional --checkout only")
    elif args.create and not all(value is not None for value in create_arguments):
        parser.error("--create requires --project, --title, and --provider")
    elif args.reopen and (args.task is None or args.create):
        parser.error("--reopen requires one existing --task")
    elif not args.create and any(
        value is not None for value in (args.project, args.title, args.checkout)
    ):
        parser.error("--project, --title, and --checkout require --create or --history")
    if args.provider is not None and args.task is None and not args.create:
        parser.error("--provider requires --task")
    target_count = sum(
        (
            args.session_key is not None,
            args.task is not None or args.create,
            args.history,
            args.stop_session is not None,
            args.close_task is not None,
        )
    )
    if target_count != 1:
        parser.error(
            "supply exactly one session, task, project, history, close, or stop target"
        )
    try:
        if args.close_task is not None:
            action = close_task(
                swbctl=args.swbctl,
                host_id=args.host_id,
                task_id=args.close_task,
                timeout_ms=args.timeout_ms,
            )
        elif args.stop_session is not None:
            action = stop_session(
                swbctl=args.swbctl,
                host_id=args.host_id,
                session_key=args.stop_session,
                timeout_ms=args.timeout_ms,
            )
        elif args.session_key is not None:
            action = open_session(
                swbctl=args.swbctl,
                terminal=args.terminal,
                host_id=args.host_id,
                session_key=args.session_key,
                window_host=args.window_host,
                timeout_ms=args.timeout_ms,
            )
        elif args.history:
            assert isinstance(args.project, str)
            action = open_history(
                swbctl=args.swbctl,
                terminal=args.terminal,
                host_id=args.host_id,
                project_id=args.project,
                checkout_id=args.checkout,
                window_host=args.window_host,
                timeout_ms=args.timeout_ms,
            )
        else:
            task_id = args.task if isinstance(args.task, str) else str(uuid.uuid4())
            action = open_task(
                swbctl=args.swbctl,
                terminal=args.terminal,
                host_id=args.host_id,
                task_id=task_id,
                provider=args.provider,
                create=args.create,
                reopen=args.reopen,
                project_id=args.project,
                title=args.title,
                checkout_id=args.checkout,
                window_host=args.window_host,
                timeout_ms=args.timeout_ms,
            )
        response = _success(action)
    except DesktopActionError as error:
        response = _failure(error)
    except Exception:
        response = _failure(
            DesktopActionError(
                "desktop_internal_error",
                "The desktop helper encountered an internal error.",
                retryable=False,
            )
        )
    exit_code, payload = serialize_response(response)
    try:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
    except Exception:
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
