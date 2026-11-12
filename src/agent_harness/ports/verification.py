"""Consumer-owned verification execution contract and observed facts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent_harness.policy import VerificationPolicy


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool


@dataclass(frozen=True)
class VerificationExecutable:
    command: tuple[str, ...]
    selected: str | None
    resolved: str | None
    sha256: str | None
    launch_error: str | None
    identity: tuple[int, int, int] | None = None


@dataclass(frozen=True)
class VerificationExecutables:
    repository: str
    working_directory: str
    executables: tuple[VerificationExecutable, ...]
    resolver_environment: tuple[tuple[str, str | None], ...] = ()
    interpreter_proofs: tuple[tuple[str, str], ...] = ()


class VerificationExecutor(Protocol):
    """Trusted execution infrastructure; not a sandbox or outcome authority.

    Bind before writable work and reuse the selection where continuity is
    required. Implementations preserve proofs, execution limits and facts.
    """

    def bind(
        self, policy: VerificationPolicy, repository: Path | str
    ) -> VerificationExecutables: ...

    def run(
        self,
        policy: VerificationPolicy,
        repository: Path | str,
        *,
        selections: VerificationExecutables | None = None,
    ) -> tuple[CommandResult, ...]: ...
