from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_harness.development import DevelopmentVerificationResult


class TechnicalDecisionKind(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class TechnicalDecision:
    kind: TechnicalDecisionKind
    reason_commands: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reason_commands",
            tuple(tuple(command) for command in self.reason_commands),
        )


def decide_technical_outcome(
    profile: DevelopmentVerificationResult,
) -> TechnicalDecision:
    new_failure_commands = tuple(
        result.command for result in profile.delta.new_failures
    )
    if new_failure_commands:
        return TechnicalDecision(TechnicalDecisionKind.FAIL, new_failure_commands)

    preexisting_failure_commands = tuple(
        result.command for result in profile.delta.preexisting_failures
    )
    if preexisting_failure_commands:
        return TechnicalDecision(
            TechnicalDecisionKind.BLOCKED,
            preexisting_failure_commands,
        )

    return TechnicalDecision(TechnicalDecisionKind.PASS, ())
