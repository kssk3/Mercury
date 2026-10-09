from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_harness.policy import VerificationPolicy


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool


_TRUNCATION_MARKER = b"\n[output truncated]\n"


class ControlledCommandRunner:
    """Capture at most max_output_bytes per stream, including truncation notice."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        max_output_bytes: int = 1024 * 1024,
    ) -> None:
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or max_output_bytes < len(_TRUNCATION_MARKER)
        ):
            raise ValueError(
                f"max_output_bytes must be an integer >= {len(_TRUNCATION_MARKER)}"
            )
        self._monotonic = monotonic
        self._max_output_bytes = max_output_bytes

    def run(
        self, policy: VerificationPolicy, repository: Path | str
    ) -> tuple[CommandResult, ...]:
        repository_root = Path(repository).resolve()
        working_directory = (repository_root / policy.working_directory).resolve()
        if not working_directory.is_relative_to(repository_root):
            raise ValueError("policy working directory must stay inside the repository")

        return tuple(
            self._run_command(command, working_directory, policy.timeout_seconds)
            for command in policy.commands
        )

    def _run_command(
        self, command: tuple[str, ...], working_directory: Path, timeout_seconds: int
    ) -> CommandResult:
        started_at = self._monotonic()
        try:
            process = subprocess.Popen(
                command,
                cwd=working_directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as error:
            return CommandResult(
                command=command,
                exit_code=None,
                stdout="",
                stderr=str(error),
                duration_seconds=self._monotonic() - started_at,
                timed_out=False,
            )

        timed_out = False
        stdout = _OutputBuffer(self._max_output_bytes)
        stderr = _OutputBuffer(self._max_output_bytes)
        try:
            _collect_output(process, stdout, stderr, timeout_seconds)
        except (subprocess.TimeoutExpired, OverflowError) as failure:
            timed_out = isinstance(failure, subprocess.TimeoutExpired)
            # The leader may have exited while descendants still hold the pipes.
            # Its isolated group must be killed even in that case.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                _collect_output(process, stdout, stderr, 1)
            except subprocess.TimeoutExpired:
                # An escaped descendant can retain a pipe; cleanup stays bounded.
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            if not timed_out:
                # Input-calculation failure remains an error after bounded cleanup.
                raise
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

        return CommandResult(
            command=command,
            exit_code=None if timed_out else process.returncode,
            stdout=stdout.text(),
            stderr=stderr.text(),
            duration_seconds=self._monotonic() - started_at,
            timed_out=timed_out,
        )


def _as_text(output: str | bytes | None) -> str:
    """Preserve bytes via UTF-8; encode with surrogateescape to recover them."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="surrogateescape")
    return output


class _OutputBuffer:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    def append(self, chunk: bytes) -> None:
        remaining = self.limit - len(self.data)
        self.data.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self.truncated = True

    def text(self) -> str:
        if self.truncated:
            prefix = self.data[: self.limit - len(_TRUNCATION_MARKER)]
            return _as_text(bytes(prefix) + _TRUNCATION_MARKER)
        return _as_text(bytes(self.data))


def _collect_output(
    process: subprocess.Popen[bytes],
    stdout: _OutputBuffer,
    stderr: _OutputBuffer,
    timeout: int,
) -> None:
    # Calculating this deadline deliberately preserves huge-timeout OverflowError.
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        for stream, output in ((process.stdout, stdout), (process.stderr, stderr)):
            if stream is not None and not stream.closed:
                selector.register(stream, selectors.EVENT_READ, output)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fd, 65536)
                if chunk:
                    key.data.append(chunk)
                else:
                    selector.unregister(key.fileobj)
        process.wait(timeout=max(0, deadline - time.monotonic()))
