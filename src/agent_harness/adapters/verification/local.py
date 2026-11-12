"""Bounded local command execution with trusted executable proofs."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from agent_harness.adapters._executables import (
    _check_verification_directory,
    _validate_executable_proofs,
    _verification_boundaries,
    bind_verification_executables,
)
from agent_harness.policy import VerificationPolicy
from agent_harness.ports.verification import (
    CommandResult,
    VerificationExecutable,
    VerificationExecutables,
)
from agent_harness.repository import git_read_environment

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

    def bind(
        self, policy: VerificationPolicy, repository: Path | str
    ) -> VerificationExecutables:
        return bind_verification_executables(policy, repository)

    def run(
        self,
        policy: VerificationPolicy,
        repository: Path | str,
        *,
        selections: VerificationExecutables | None = None,
    ) -> tuple[CommandResult, ...]:
        repository_root = Path(repository).resolve()
        working_directory = (repository_root / policy.working_directory).resolve()
        if selections is not None:
            _check_verification_directory(
                working_directory,
                repository_root,
                _verification_boundaries(repository_root),
            )
        selected = selections or bind_verification_executables(policy, repository_root)
        if selected.repository != str(
            repository_root
        ) or selected.working_directory != str(working_directory):
            raise ValueError(
                "verification selection does not match repository or directory"
            )
        by_command = {item.command: item for item in selected.executables}
        if any(command not in by_command for command in policy.commands):
            raise ValueError("verification selection does not match commands")
        environment = git_read_environment()
        for name, value in selected.resolver_environment:
            if value is None:
                environment.pop(name, None)
            else:
                environment[name] = value
        # Node startup flags can preload mutable code independently of argv or
        # NODE_PATH. Never admit them through the inherited verification environment.
        environment.pop("NODE_OPTIONS", None)
        return tuple(
            self._run_command(
                command,
                working_directory,
                policy.timeout_seconds,
                by_command[command],
                environment,
                selected.interpreter_proofs,
            )
            for command in policy.commands
        )

    def _run_command(
        self,
        command: tuple[str, ...],
        working_directory: Path,
        timeout_seconds: int,
        selection: VerificationExecutable,
        environment: dict[str, str],
        interpreter_proofs: tuple[tuple[str, str], ...],
    ) -> CommandResult:
        started_at = self._monotonic()
        try:
            _validate_executable_proofs(selection, interpreter_proofs)
            process = subprocess.Popen(
                (selection.selected or command[0], *command[1:]),
                cwd=working_directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=environment,
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
        except BaseException as failure:
            timed_out = isinstance(failure, subprocess.TimeoutExpired)
            # The leader may have exited while descendants still hold the pipes.
            # Its isolated group must be killed even in that case.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                _collect_output(process, stdout, stderr, 1)
            except BaseException:
                # Cleanup failures must not replace the original collection error.
                # Escaped descendants can retain pipes; reaping stays bounded.
                try:
                    process.wait(timeout=1)
                except BaseException:
                    pass
            if not timed_out:
                # Every non-timeout failure propagates after bounded cleanup.
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
