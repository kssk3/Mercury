from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_harness.baseline import VerificationBaseline, VerificationDelta
from agent_harness.composition.verification import create_verification_executor
from agent_harness.policy import VerificationPolicy
from agent_harness.ports.verification import (
    CommandResult,
    VerificationExecutables,
    VerificationExecutor,
)


@dataclass(frozen=True)
class DevelopmentVerificationResult:
    baseline: VerificationBaseline
    results: tuple[CommandResult, ...]
    delta: VerificationDelta

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))
        if self.delta != self.baseline.compare(self.results):
            raise ValueError("delta must match the baseline comparison of results")


def run_development_profile(
    policy: VerificationPolicy,
    repository: Path | str,
    baseline: VerificationBaseline,
    *,
    selections: VerificationExecutables | None = None,
    verification_executor: VerificationExecutor | None = None,
) -> DevelopmentVerificationResult:
    """Reuse an explicit pre-baseline selection when continuity is required.

    Arbitrary caller-provided historical baseline results have no executable
    provenance; the default safely selects anew for this independent invocation.
    """
    if tuple(result.command for result in baseline.results) != policy.commands:
        raise ValueError("baseline must use the same command sequence")
    if verification_executor is None:
        verification_executor = create_verification_executor()
    results = verification_executor.run(policy, repository, selections=selections)
    return DevelopmentVerificationResult(
        baseline=baseline,
        results=results,
        delta=baseline.compare(results),
    )
