from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent_harness.baseline import VerificationBaseline
from agent_harness.development import DevelopmentVerificationResult
from agent_harness.final import TechnicalDecisionKind, decide_technical_outcome
from agent_harness.runner import CommandResult


def result(command: tuple[str, ...], exit_code: int) -> CommandResult:
    return CommandResult(
        command=command,
        exit_code=exit_code,
        stdout="",
        stderr="",
        duration_seconds=0.1,
        timed_out=False,
    )


def profile(
    baseline_results: tuple[CommandResult, ...],
    candidate_results: tuple[CommandResult, ...],
) -> DevelopmentVerificationResult:
    baseline = VerificationBaseline.capture(baseline_results)
    return DevelopmentVerificationResult(
        baseline=baseline,
        results=candidate_results,
        delta=baseline.compare(candidate_results),
    )


def test_decision_passes_when_d3_has_no_new_or_preexisting_failures() -> None:
    command = ("tests",)
    verification = profile((result(command, 0),), (result(command, 0),))

    decision = decide_technical_outcome(verification)

    assert decision.kind is TechnicalDecisionKind.PASS
    assert decision.reason_commands == ()


def test_decision_fails_for_new_failure_before_separate_preexisting_failure() -> None:
    changed_command = ("changed-tests",)
    baseline_command = ("known-failure",)
    verification = profile(
        (result(changed_command, 0), result(baseline_command, 1)),
        (result(changed_command, 1), result(baseline_command, 1)),
    )

    decision = decide_technical_outcome(verification)

    assert decision.kind is TechnicalDecisionKind.FAIL
    assert decision.reason_commands == (changed_command,)


def test_decision_blocks_remaining_preexisting_failures_in_d2_order() -> None:
    first = ("first-known-failure",)
    second = ("second-known-failure",)
    verification = profile(
        (result(first, 1), result(second, 1)),
        (result(first, 1), result(second, 1)),
    )

    decision = decide_technical_outcome(verification)

    assert decision.kind is TechnicalDecisionKind.BLOCKED
    assert decision.reason_commands == (first, second)


def test_decision_is_immutable() -> None:
    command = ("tests",)
    verification = profile((result(command, 0),), (result(command, 0),))

    decision = decide_technical_outcome(verification)

    with pytest.raises(FrozenInstanceError):
        decision.__setattr__("reason_commands", (("replacement",),))
