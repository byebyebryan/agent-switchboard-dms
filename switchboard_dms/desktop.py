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

ACTION_VERSION = 1
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


def desktop_app_id(desktop_token: str) -> str:
    """Derive a valid, opaque Wayland application ID from a desktop token."""

    token = _bounded_text(desktop_token, maximum=MAX_DESKTOP_TOKEN_LENGTH)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
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
        app_id = desktop_app_id(token) if isinstance(token, str) else None
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
) -> list[str]:
    return [
        systemd_run,
        "--user",
        "--scope",
        "--collect",
        "--quiet",
        "--",
        terminal,
        f"--class={desktop_app_id(desktop_token)}",
        "-e",
        swbctl,
        "attach-surface",
        surface_id,
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
    session_key: str | None,
    project_id: str | None,
    location_id: str | None,
    provider: str | None,
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
        prepare_new=project_id,
        location_id=location_id,
        provider=provider,
        request_id=request_id,
        prepare_can_focus_desktop=can_focus_desktop,
        prepare_can_launch_terminal=True,
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
    return plan


def _attach(
    plan: Mapping[str, object],
    *,
    swbctl: str,
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
        )
    )
    return {"kind": "launched", "surfaceId": surface_id}


def _open_target(
    *,
    swbctl: str,
    terminal: str,
    session_key: str | None,
    project_id: str | None,
    location_id: str | None,
    provider: str | None,
    window_host: str,
    timeout_ms: int,
    request_id: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    launcher: DetachedLauncher = launch_detached,
) -> dict[str, object]:
    """Prepare and execute one validated local presentation target."""

    if (session_key is None) == (project_id is None):
        raise ValueError("exactly one session or project target is required")
    project_arguments = (project_id, location_id, provider)
    if any(value is not None for value in project_arguments) and not all(
        value is not None for value in project_arguments
    ):
        raise ValueError(
            "project, location, and provider targets must be supplied together"
        )

    request = request_id or str(uuid.uuid4())
    plan = _prepared_plan(
        swbctl=swbctl,
        session_key=session_key,
        project_id=project_id,
        location_id=location_id,
        provider=provider,
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
        )
        if selected.get("ok") is not True:
            raise _bridge_error(selected)
        if focus_existing_window(
            plan, window_host=window_host, timeout_ms=timeout_ms, which=which
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
            plan, window_host=window_host, timeout_ms=timeout_ms, which=which
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
        session_key=session_key,
        project_id=project_id,
        location_id=location_id,
        provider=provider,
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
        terminal=terminal,
        which=which,
        launcher=launcher,
    )


def open_session(
    *,
    swbctl: str,
    terminal: str,
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
        session_key=session_key,
        project_id=None,
        location_id=None,
        provider=None,
        window_host=window_host,
        timeout_ms=timeout_ms,
        request_id=request_id,
        which=which,
        launcher=launcher,
    )


def open_project(
    *,
    swbctl: str,
    terminal: str,
    project_id: str,
    location_id: str,
    provider: str,
    window_host: str,
    timeout_ms: int,
    request_id: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    launcher: DetachedLauncher = launch_detached,
) -> dict[str, object]:
    """Prepare and execute one new local provider project-session action."""

    return _open_target(
        swbctl=swbctl,
        terminal=terminal,
        session_key=None,
        project_id=project_id,
        location_id=location_id,
        provider=provider,
        window_host=window_host,
        timeout_ms=timeout_ms,
        request_id=request_id,
        which=which,
        launcher=launcher,
    )


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="switchboard-open",
        description="Open one validated local Switchboard presentation plan.",
    )
    parser.add_argument("--swbctl", default="swbctl", type=_executable)
    parser.add_argument("--terminal", default="ghostty", type=_executable)
    parser.add_argument("--window-host", required=True, type=_window_host)
    parser.add_argument(
        "--timeout-ms",
        default=DEFAULT_TIMEOUT_MS,
        type=_bounded_integer("--timeout-ms", MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
    )
    parser.add_argument("--project", type=_uuid)
    parser.add_argument("--location", type=_uuid)
    parser.add_argument("--provider", choices=("codex", "claude"))
    parser.add_argument("session_key", nargs="?")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_arguments = (args.project, args.location, args.provider)
    if any(value is not None for value in project_arguments) and not all(
        value is not None for value in project_arguments
    ):
        parser.error("--project, --location, and --provider must be supplied together")
    if (args.session_key is None) == (args.project is None):
        parser.error("supply one session key or one project/location/provider target")
    try:
        if args.session_key is not None:
            action = open_session(
                swbctl=args.swbctl,
                terminal=args.terminal,
                session_key=args.session_key,
                window_host=args.window_host,
                timeout_ms=args.timeout_ms,
            )
        else:
            action = open_project(
                swbctl=args.swbctl,
                terminal=args.terminal,
                project_id=args.project,
                location_id=args.location,
                provider=args.provider,
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
