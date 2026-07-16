"""Bounded, shell-free subprocess execution for the Switchboard bridge."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import IO, Any, Sequence, cast

from .protocol import MAX_JSON_BYTES

MAX_STDOUT_BYTES = MAX_JSON_BYTES + 1
MAX_STDERR_BYTES = 64 * 1024
READ_CHUNK_BYTES = 64 * 1024
REAP_TIMEOUT_SECONDS = 1


@dataclass(frozen=True, slots=True)
class ProcessOutput:
    stdout: bytes
    stderr: bytes
    exit_code: int


class ProcessRunError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _close_stream(selector: selectors.BaseSelector, stream: IO[Any]) -> None:
    try:
        selector.unregister(stream)
    except (KeyError, OSError, ValueError):
        pass
    try:
        stream.close()
    except OSError:
        pass


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    """Best-effort group termination with two bounded reap attempts."""

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        process.wait(timeout=REAP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=REAP_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass
    except OSError:
        pass


def _spawn(argv: Sequence[str]) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
    except FileNotFoundError as error:
        raise ProcessRunError(
            "executable_not_found",
            "The configured swbctl executable was not found.",
            retryable=False,
        ) from error
    except PermissionError as error:
        raise ProcessRunError(
            "executable_permission_denied",
            "The configured swbctl executable is not executable.",
            retryable=False,
        ) from error
    except OSError as error:
        raise ProcessRunError(
            "executable_start_failed",
            "The configured swbctl executable could not be started.",
            retryable=True,
        ) from error


def run_process(argv: Sequence[str], *, timeout_ms: int) -> ProcessOutput:
    """Run one argv without a shell while bounding both output streams."""

    process = _spawn(argv)
    selector: selectors.BaseSelector | None = None
    result: ProcessOutput | None = None
    primary_error: BaseException | None = None
    try:
        stdout_stream = process.stdout
        stderr_stream = process.stderr
        if stdout_stream is None or stderr_stream is None:
            raise ProcessRunError(
                "executable_start_failed",
                "The configured swbctl executable could not be started.",
                retryable=True,
            )

        stdout = bytearray()
        stderr = bytearray()
        exit_code: int | None = None
        failure: ProcessRunError | None = None
        selector = selectors.DefaultSelector()
        deadline = time.monotonic() + timeout_ms / 1000
        streams: tuple[tuple[IO[Any], str], ...] = (
            (stdout_stream, "stdout"),
            (stderr_stream, "stderr"),
        )

        for stream, name in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = ProcessRunError(
                    "process_timeout",
                    "swbctl did not finish before the configured timeout.",
                    retryable=True,
                )
                break

            events = selector.select(remaining)
            if not events:
                continue

            for key, _mask in events:
                stream = cast(IO[Any], key.fileobj)
                target = stdout if key.data == "stdout" else stderr
                limit = MAX_STDOUT_BYTES if key.data == "stdout" else MAX_STDERR_BYTES
                while True:
                    try:
                        remaining_budget = limit - len(target)
                        read_size = min(READ_CHUNK_BYTES, remaining_budget + 1)
                        chunk = os.read(stream.fileno(), read_size)
                    except BlockingIOError:
                        break
                    if not chunk:
                        _close_stream(selector, stream)
                        break
                    target.extend(chunk)
                    if len(target) > limit:
                        if key.data == "stdout":
                            failure = ProcessRunError(
                                "stdout_overflow",
                                "swbctl stdout exceeded the bridge limit.",
                                retryable=False,
                            )
                        else:
                            failure = ProcessRunError(
                                "stderr_overflow",
                                "swbctl stderr exceeded the bridge limit.",
                                retryable=False,
                            )
                        break
                if failure is not None:
                    break
            if failure is not None:
                break

        if failure is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = ProcessRunError(
                    "process_timeout",
                    "swbctl did not finish before the configured timeout.",
                    retryable=True,
                )
            else:
                try:
                    exit_code = process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    failure = ProcessRunError(
                        "process_timeout",
                        "swbctl did not finish before the configured timeout.",
                        retryable=True,
                    )

        if failure is not None:
            raise failure

        if exit_code is None:
            raise RuntimeError("swbctl completed without an exit status")
        result = ProcessOutput(bytes(stdout), bytes(stderr), exit_code)
    except BaseException as error:
        primary_error = error
    finally:
        if result is None:
            _kill_process_group(process)
        try:
            if selector is not None:
                try:
                    for stream in (process.stdout, process.stderr):
                        if stream is not None:
                            _close_stream(selector, stream)
                finally:
                    selector.close()
            else:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
        except BaseException:
            if result is not None:
                _kill_process_group(process)
                raise

    if primary_error is not None:
        raise primary_error.with_traceback(primary_error.__traceback__)

    if result is None:
        raise AssertionError("run_process exited without a result or exception")
    return result
