"""Bounded NavigatorState v1 to entry-model v1 bridge."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from .process import ProcessRunError, run_process
from .protocol import ProtocolError, failure_envelope, parse_navigator, success_envelope


def _executable(value: str) -> str:
    if not value or "\x00" in value or len(value.encode()) > 4096:
        raise argparse.ArgumentTypeError("executable is invalid")
    return value


def _timeout(value: str) -> int:
    try:
        timeout = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be an integer") from error
    if not 100 <= timeout <= 60_000:
        raise argparse.ArgumentTypeError("timeout is outside bounds")
    return timeout


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="switchboard-bridge")
    result.add_argument("--swbctl", default="swbctl", type=_executable)
    result.add_argument("--timeout-ms", default=10_000, type=_timeout)
    result.add_argument("--refresh", action="store_true")
    return result


def run(arguments: argparse.Namespace) -> bytes:
    argv = [arguments.swbctl, "state", "navigator"]
    if arguments.refresh:
        argv.append("--refresh")
    argv.append("--json")
    result = run_process(argv, timeout_ms=arguments.timeout_ms)
    if result.exit_code != 0:
        raise ProcessRunError(
            "core_incompatible_generation",
            "DMS 0.5 requires core 0.3 NavigatorState v1.",
            retryable=False,
        )
    return success_envelope(parse_navigator(result.stdout))


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        output = run(arguments)
    except (ProtocolError, ProcessRunError) as error:
        output = failure_envelope(
            error.code,
            error.message,
            retryable=getattr(error, "retryable", False),
        )
        exit_code = 1
    except Exception:
        output = failure_envelope(
            "bridge_internal_error",
            "The Switchboard bridge failed unexpectedly.",
            retryable=True,
        )
        exit_code = 1
    else:
        exit_code = 0
    os.write(sys.stdout.fileno(), output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
