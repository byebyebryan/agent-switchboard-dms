"""Exact niri/Ghostty presentation for PresentationDirective v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final
from uuid import UUID, uuid4

from .process import ProcessOutput, ProcessRunError, run_process
from .protocol import (
    Directive,
    ProtocolError,
    parse_core_error,
    parse_directive,
)

ACTION_VERSION: Final = 1
APP_ID_PREFIX: Final = "com.agent_switchboard.view.v"
ProcessRunner = Callable[..., ProcessOutput]
Launcher = Callable[[Sequence[str]], None]


class DesktopError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _bounded(value: str, label: str, maximum: int = 4096) -> str:
    if not value or "\x00" in value or len(value.encode()) > maximum:
        raise argparse.ArgumentTypeError(f"{label} is invalid")
    return value


def _uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("identifier is not a UUID") from error
    if parsed.int == 0 or str(parsed) != value:
        raise argparse.ArgumentTypeError("identifier is not a canonical UUID")
    return value


def _timeout(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be an integer") from error
    if not 100 <= result <= 60_000:
        raise argparse.ArgumentTypeError("timeout is outside bounds")
    return result


def desktop_app_id(desktop_token: str, host_id: str) -> str:
    token = _bounded(desktop_token, "desktop token", 256)
    host = _uuid(host_id)
    digest = hashlib.sha256(f"{host}\0{token}".encode()).hexdigest()
    return f"{APP_ID_PREFIX}{digest[:32]}"


def matching_window_ids(windows: object, app_id: str) -> tuple[int, ...]:
    if not isinstance(windows, list) or len(windows) > 20_000:
        raise DesktopError(
            "niri_windows_invalid", "niri returned an invalid window list."
        )
    matches: list[int] = []
    for window in windows:
        if not isinstance(window, dict) or window.get("app_id") != app_id:
            continue
        window_id = window.get("id")
        if (
            isinstance(window_id, bool)
            or not isinstance(window_id, int)
            or window_id < 0
        ):
            raise DesktopError(
                "niri_windows_invalid", "niri returned an invalid matching window."
            )
        matches.append(window_id)
    return tuple(sorted(matches))


def _windows(
    app_id: str,
    *,
    timeout_ms: int,
    runner: ProcessRunner,
    environment: Mapping[str, str],
    which: Callable[[str], str | None],
) -> tuple[str, tuple[int, ...]]:
    niri = which("niri")
    if niri is None or not environment.get("NIRI_SOCKET"):
        return "", ()
    output = runner([niri, "msg", "--json", "windows"], timeout_ms=timeout_ms)
    if output.exit_code != 0:
        raise DesktopError(
            "niri_query_failed", "niri could not list windows.", retryable=True
        )
    try:
        windows = json.loads(output.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DesktopError(
            "niri_windows_invalid", "niri returned invalid window JSON."
        ) from error
    return niri, matching_window_ids(windows, app_id)


def _focus(
    niri: str, window_id: int, *, timeout_ms: int, runner: ProcessRunner
) -> None:
    result = runner(
        [niri, "msg", "action", "focus-window", "--id", str(window_id)],
        timeout_ms=timeout_ms,
    )
    if result.exit_code != 0:
        raise DesktopError(
            "niri_focus_failed",
            "niri could not focus the Switchboard window.",
            retryable=True,
        )


def _directive(
    swbctl: str,
    host_id: str,
    target_kind: str,
    target_id: str,
    request_id: str,
    *,
    can_focus: bool,
    timeout_ms: int,
    runner: ProcessRunner,
) -> Directive:
    action = "recover" if target_kind == "recovery" else "open"
    argv = [swbctl, "view", action, "--host", host_id]
    argv.extend([f"--{target_kind}", target_id])
    argv.extend(
        [
            "--request-id",
            request_id,
            "--can-focus-desktop" if can_focus else "--no-focus-desktop",
            "--can-launch-terminal",
            "--json",
        ]
    )
    output = runner(argv, timeout_ms=timeout_ms)
    if output.exit_code != 0:
        if output.stdout:
            try:
                error = parse_core_error(output.stdout)
            except ProtocolError:
                pass
            else:
                raise DesktopError(
                    error["code"],
                    error["message"],
                    retryable=error["retryable"],
                )
        raise DesktopError(
            "core_incompatible_generation",
            "DMS 0.5 requires core 0.3 PresentationDirective v1.",
            retryable=False,
        )
    return parse_directive(output.stdout, host_id=host_id, request_id=request_id)


def terminal_launch_argv(
    *,
    systemd_run: str,
    terminal: str,
    swbctl: str,
    directive: Directive,
) -> list[str]:
    value = directive.value
    return [
        systemd_run,
        "--user",
        "--scope",
        "--collect",
        "--quiet",
        "--",
        terminal,
        f"--class={desktop_app_id(value['desktopToken'], value['hostId'])}",
        "-e",
        swbctl,
        "view",
        "attach",
        "--host",
        value["hostId"],
        "--view",
        value["viewId"],
        "--request-id",
        value["requestId"],
    ]


def launch_detached(argv: Sequence[str]) -> None:
    subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def _resolve(executable: str, which: Callable[[str], str | None]) -> str:
    if os.path.isabs(executable):
        if os.path.isfile(executable) and os.access(executable, os.X_OK):
            return executable
        raise DesktopError(
            "desktop_executable_not_found", "A desktop executable was not found."
        )
    found = which(executable)
    if found is None:
        raise DesktopError(
            "desktop_executable_not_found", "A desktop executable was not found."
        )
    return found


def _attach(
    directive: Directive,
    *,
    swbctl: str,
    terminal: str,
    which: Callable[[str], str | None],
    launcher: Launcher,
) -> dict[str, Any]:
    if directive.value["kind"] != "attach":
        raise DesktopError(
            "directive_invalid_protocol", "Attach requires an attach directive."
        )
    systemd_run = _resolve("systemd-run", which)
    terminal_path = _resolve(terminal, which)
    launcher(
        terminal_launch_argv(
            systemd_run=systemd_run,
            terminal=terminal_path,
            swbctl=swbctl,
            directive=directive,
        )
    )
    return {
        "kind": "launched",
        "hostId": directive.value["hostId"],
        "viewId": directive.value["viewId"],
        "requestId": directive.value["requestId"],
    }


def present(
    *,
    swbctl: str,
    terminal: str,
    host_id: str,
    target_kind: str,
    target_id: str,
    request_id: str,
    timeout_ms: int,
    runner: ProcessRunner = run_process,
    environment: Mapping[str, str] = os.environ,
    which: Callable[[str], str | None] = shutil.which,
    launcher: Launcher = launch_detached,
) -> dict[str, Any]:
    directive = _directive(
        swbctl,
        host_id,
        target_kind,
        target_id,
        request_id,
        can_focus=True,
        timeout_ms=timeout_ms,
        runner=runner,
    )
    if directive.value["kind"] == "blocked":
        error = directive.value["error"]
        raise DesktopError(
            error["code"], error["message"], retryable=error["retryable"]
        )
    if directive.value["kind"] == "attach":
        return _attach(
            directive, swbctl=swbctl, terminal=terminal, which=which, launcher=launcher
        )
    app_id = desktop_app_id(directive.value["desktopToken"], host_id)
    niri, matches = _windows(
        app_id,
        timeout_ms=timeout_ms,
        runner=runner,
        environment=environment,
        which=which,
    )
    if len(matches) > 1:
        raise DesktopError(
            "ambiguous_desktop_windows",
            "More than one canonical Switchboard window matches this view.",
        )
    if len(matches) == 1:
        _focus(niri, matches[0], timeout_ms=timeout_ms, runner=runner)
        return {
            "kind": "focused",
            "hostId": host_id,
            "viewId": directive.value["viewId"],
            "requestId": request_id,
        }
    fallback = _directive(
        swbctl,
        host_id,
        target_kind,
        target_id,
        request_id,
        can_focus=False,
        timeout_ms=timeout_ms,
        runner=runner,
    )
    if fallback.value["kind"] == "blocked":
        error = fallback.value["error"]
        raise DesktopError(
            error["code"], error["message"], retryable=error["retryable"]
        )
    if (
        fallback.value["kind"] != "attach"
        or fallback.value.get("viewId") != directive.value["viewId"]
        or fallback.value.get("desktopToken") != directive.value["desktopToken"]
    ):
        raise DesktopError(
            "desktop_fallback_invalid",
            "Focus miss did not return the same view's attach lease.",
        )
    return _attach(
        fallback, swbctl=swbctl, terminal=terminal, which=which, launcher=launcher
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="switchboard-open")
    result.add_argument(
        "--swbctl", default="swbctl", type=lambda value: _bounded(value, "swbctl")
    )
    result.add_argument(
        "--terminal", default="ghostty", type=lambda value: _bounded(value, "terminal")
    )
    result.add_argument("--timeout-ms", default=10_000, type=_timeout)
    result.add_argument("--host", required=True, type=_uuid)
    targets = result.add_mutually_exclusive_group(required=True)
    targets.add_argument("--view", type=_uuid)
    targets.add_argument("--project", type=_uuid)
    targets.add_argument("--recovery", type=_uuid)
    result.add_argument("--request-id", type=_uuid)
    return result


def _output(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            {"actionVersion": ACTION_VERSION, "ok": True, "action": value},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )


def _failure_output(code: str, message: str, *, retryable: bool) -> bytes:
    return (
        json.dumps(
            {
                "actionVersion": ACTION_VERSION,
                "ok": False,
                "error": {
                    "code": str(code)[:64],
                    "message": str(message)[:1024],
                    "retryable": bool(retryable),
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    target_kind = next(
        name
        for name in ("view", "project", "recovery")
        if getattr(arguments, name) is not None
    )
    try:
        action = present(
            swbctl=arguments.swbctl,
            terminal=arguments.terminal,
            host_id=arguments.host,
            target_kind=target_kind,
            target_id=getattr(arguments, target_kind),
            request_id=arguments.request_id or str(uuid4()),
            timeout_ms=arguments.timeout_ms,
        )
        output = _output(action)
        exit_code = 0
    except (DesktopError, ProcessRunError, ProtocolError) as error:
        output = _failure_output(
            error.code, error.message, retryable=getattr(error, "retryable", False)
        )
        exit_code = 1
    except Exception:
        output = _failure_output(
            "desktop_internal_error",
            "The Switchboard desktop action failed unexpectedly.",
            retryable=True,
        )
        exit_code = 1
    os.write(sys.stdout.fileno(), output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
