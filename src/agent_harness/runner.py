from __future__ import annotations

import os
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


class ControlledCommandRunner:
    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic

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
        stdout: bytes | None
        stderr: bytes | None
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except (subprocess.TimeoutExpired, OverflowError) as failure:
            timed_out = isinstance(failure, subprocess.TimeoutExpired)
            # The leader may have exited while descendants still hold the pipes.
            # Its isolated group must be killed even in that case.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired as error:
                # communicate's retry includes earlier bytes; do not concatenate.
                stdout, stderr = error.stdout, error.stderr
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    # Keep the cleanup bounded even if the OS cannot reap yet.
                    pass
            if not timed_out:
                # Input-calculation failure remains an error after bounded cleanup.
                raise

        return CommandResult(
            command=command,
            exit_code=None if timed_out else process.returncode,
            stdout=_as_text(stdout),
            stderr=_as_text(stderr),
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
