from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_harness.baseline import VerificationBaseline, VerificationDelta
from agent_harness.policy import VerificationPolicy
from agent_harness.runner import CommandResult, ControlledCommandRunner


@dataclass(frozen=True)
class DevelopmentVerificationResult:
    baseline: VerificationBaseline
    results: tuple[CommandResult, ...]
    delta: VerificationDelta

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))


def run_development_profile(
    policy: VerificationPolicy,
    repository: Path | str,
    baseline: VerificationBaseline,
) -> DevelopmentVerificationResult:
    results = ControlledCommandRunner().run(policy, repository)
    return DevelopmentVerificationResult(
        baseline=baseline,
        results=results,
        delta=baseline.compare(results),
    )
