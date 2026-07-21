"""Local project-catalog window management for the DMS launcher."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NoReturn

from .bridge import DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS, MIN_TIMEOUT_MS
from .process import ProcessOutput, ProcessRunError, run_process

BRIDGE_VERSION = 3
MANAGER_APP_ID = "com.agent_switchboard.projects"
MAX_EXECUTABLE_LENGTH = 4096
MAX_ERROR_MESSAGE_LENGTH = 160
POLL_SECONDS = 0.1
WINDOW_START_SECONDS = 5.0

ProcessRunner = Callable[..., ProcessOutput]
TerminalLauncher = Callable[[Sequence[str]], subprocess.Popen[bytes]]


class ProjectManagerError(RuntimeError):
    """The local project manager could not be opened or refreshed safely."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[:MAX_ERROR_MESSAGE_LENGTH]
        self.retryable = retryable


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ProjectManagerError(
            "project_manager_arguments_invalid",
            "The project manager received invalid arguments.",
            retryable=False,
        )


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
    if (
        not value
        or len(value) > MAX_EXECUTABLE_LENGTH
        or "\x00" in value
        or any(character in "\r\n" for character in value)
    ):
        raise argparse.ArgumentTypeError("expected one bounded executable token")
    return value


def _uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a canonical non-nil UUID") from error
    if parsed.int == 0 or str(parsed) != value:
        raise argparse.ArgumentTypeError("expected a canonical non-nil UUID")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="switchboard-projects",
        description="Open the local Agent Switchboard project catalog manager.",
        add_help=False,
    )
    parser.add_argument("--swbctl", default="swbctl", type=_executable)
    parser.add_argument("--terminal", default="ghostty", type=_executable)
    parser.add_argument(
        "--timeout-ms",
        default=DEFAULT_TIMEOUT_MS,
        type=_bounded_integer("--timeout-ms", MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
    )
    parser.add_argument("--project", type=_uuid)
    parser.add_argument("--add-project", action="store_true")
    return parser


def project_tui_argv(
    *, swbctl: str, project_id: str | None, add_project: bool
) -> list[str]:
    argv = [swbctl, "tui", "--view", "projects"]
    if project_id is not None:
        argv.extend(("--project", project_id))
    if add_project:
        argv.append("--add-project")
    return argv


def terminal_argv(
    *, terminal: str, swbctl: str, project_id: str | None, add_project: bool
) -> list[str]:
    return [
        terminal,
        f"--class={MANAGER_APP_ID}",
        "-e",
        *project_tui_argv(
            swbctl=swbctl, project_id=project_id, add_project=add_project
        ),
    ]


def manager_window_id(windows: object) -> int | None:
    if not isinstance(windows, list):
        raise ProjectManagerError(
            "project_manager_windows_invalid",
            "The desktop returned an invalid window list.",
            retryable=True,
        )
    matches: list[int] = []
    for window in windows:
        if not isinstance(window, dict) or window.get("app_id") != MANAGER_APP_ID:
            continue
        window_id = window.get("id")
        if (
            isinstance(window_id, bool)
            or not isinstance(window_id, int)
            or window_id < 0
        ):
            continue
        matches.append(window_id)
    if len(matches) > 1:
        raise ProjectManagerError(
            "project_manager_window_ambiguous",
            "More than one project manager window is open.",
            retryable=False,
        )
    return matches[0] if matches else None


def _run_niri(
    argv: Sequence[str], *, timeout_ms: int, runner: ProcessRunner
) -> ProcessOutput:
    try:
        result = runner(argv, timeout_ms=timeout_ms)
    except ProcessRunError as error:
        raise ProjectManagerError(
            "project_manager_window_query_failed",
            "The desktop window manager could not be queried.",
            retryable=error.retryable,
        ) from error
    if result.exit_code != 0 or result.stderr:
        raise ProjectManagerError(
            "project_manager_window_query_failed",
            "The desktop window manager could not be queried.",
            retryable=True,
        )
    return result


def _read_window_id(niri: str, *, timeout_ms: int, runner: ProcessRunner) -> int | None:
    result = _run_niri(
        [niri, "msg", "--json", "windows"], timeout_ms=timeout_ms, runner=runner
    )
    try:
        windows = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectManagerError(
            "project_manager_windows_invalid",
            "The desktop returned an invalid window list.",
            retryable=True,
        ) from error
    return manager_window_id(windows)


def _focus_window(
    niri: str, window_id: int, *, timeout_ms: int, runner: ProcessRunner
) -> None:
    _run_niri(
        [niri, "msg", "action", "focus-window", "--id", str(window_id)],
        timeout_ms=timeout_ms,
        runner=runner,
    )


def launch_terminal(argv: Sequence[str]) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            start_new_session=True,
        )
    except FileNotFoundError as error:
        raise ProjectManagerError(
            "project_manager_terminal_not_found",
            "The configured terminal executable was not found.",
            retryable=False,
        ) from error
    except PermissionError as error:
        raise ProjectManagerError(
            "project_manager_terminal_permission_denied",
            "The configured terminal executable is not executable.",
            retryable=False,
        ) from error
    except OSError as error:
        raise ProjectManagerError(
            "project_manager_terminal_start_failed",
            "The project manager terminal could not be started.",
            retryable=True,
        ) from error


def _stop_launched_terminal(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass


def open_project_manager(
    *,
    swbctl: str,
    terminal: str,
    project_id: str | None,
    add_project: bool,
    timeout_ms: int,
    runner: ProcessRunner = run_process,
    launcher: TerminalLauncher = launch_terminal,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] = os.environ,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Focus or open the singleton catalog window and wait for it to close."""

    if project_id is not None and add_project:
        raise ProjectManagerError(
            "project_manager_arguments_invalid",
            "A project and the add-project flow cannot be opened together.",
            retryable=False,
        )
    niri = which("niri")
    if niri is None or not environ.get("NIRI_SOCKET"):
        raise ProjectManagerError(
            "project_manager_desktop_unavailable",
            "The project manager requires the niri desktop session.",
            retryable=False,
        )

    existing = _read_window_id(niri, timeout_ms=timeout_ms, runner=runner)
    launched: subprocess.Popen[bytes] | None = None
    if existing is not None:
        _focus_window(niri, existing, timeout_ms=timeout_ms, runner=runner)
    else:
        launched = launcher(
            terminal_argv(
                terminal=terminal,
                swbctl=swbctl,
                project_id=project_id,
                add_project=add_project,
            )
        )
        startup_deadline = monotonic() + WINDOW_START_SECONDS
        while existing is None and monotonic() < startup_deadline:
            sleep(POLL_SECONDS)
            existing = _read_window_id(niri, timeout_ms=timeout_ms, runner=runner)
        if existing is None:
            _stop_launched_terminal(launched)
            raise ProjectManagerError(
                "project_manager_window_start_failed",
                "The project manager window did not appear.",
                retryable=True,
            )

    while _read_window_id(niri, timeout_ms=timeout_ms, runner=runner) is not None:
        sleep(POLL_SECONDS)


def refresh_bridge(
    *,
    bridge: str,
    swbctl: str,
    timeout_ms: int,
    runner: ProcessRunner = run_process,
) -> tuple[int, bytes]:
    try:
        result = runner(
            [bridge, "--swbctl", swbctl, "--timeout-ms", str(timeout_ms), "--refresh"],
            timeout_ms=timeout_ms + 2_000,
        )
    except ProcessRunError as error:
        raise ProjectManagerError(
            error.code,
            error.message,
            retryable=error.retryable,
        ) from error
    if result.stderr:
        raise ProjectManagerError(
            "project_manager_refresh_failed",
            "The catalog refresh returned unexpected diagnostics.",
            retryable=True,
        )
    try:
        response = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectManagerError(
            "project_manager_refresh_invalid",
            "The catalog refresh returned an invalid response.",
            retryable=True,
        ) from error
    if (
        not isinstance(response, dict)
        or response.get("bridgeVersion") != BRIDGE_VERSION
        or not isinstance(response.get("ok"), bool)
        or (response["ok"] and result.exit_code != 0)
        or (not response["ok"] and result.exit_code == 0)
    ):
        raise ProjectManagerError(
            "project_manager_refresh_invalid",
            "The catalog refresh returned an invalid response.",
            retryable=True,
        )
    return result.exit_code, result.stdout.rstrip() + b"\n"


def _failure(error: ProjectManagerError) -> dict[str, object]:
    return {
        "bridgeVersion": BRIDGE_VERSION,
        "ok": False,
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
        },
    }


def _serialize_failure(error: ProjectManagerError) -> bytes:
    return (
        json.dumps(
            _failure(error),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    bridge_path: str | None = None,
) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.project is not None and args.add_project:
            raise ProjectManagerError(
                "project_manager_arguments_invalid",
                "A project and the add-project flow cannot be opened together.",
                retryable=False,
            )
        open_project_manager(
            swbctl=args.swbctl,
            terminal=args.terminal,
            project_id=args.project,
            add_project=args.add_project,
            timeout_ms=args.timeout_ms,
        )
        bridge = bridge_path or str(
            Path(sys.argv[0]).resolve().with_name("switchboard-bridge")
        )
        exit_code, payload = refresh_bridge(
            bridge=bridge,
            swbctl=args.swbctl,
            timeout_ms=args.timeout_ms,
        )
    except ProjectManagerError as error:
        exit_code, payload = 1, _serialize_failure(error)
    except Exception:
        exit_code, payload = (
            1,
            _serialize_failure(
                ProjectManagerError(
                    "project_manager_internal_error",
                    "The project manager encountered an internal error.",
                    retryable=False,
                )
            ),
        )
    try:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
    except Exception:
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
