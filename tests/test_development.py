from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from agent_harness.baseline import VerificationBaseline, VerificationDelta
from agent_harness.development import (
    DevelopmentVerificationResult,
    run_development_profile,
)
from agent_harness.evidence import build_evidence_report
from agent_harness.final import TechnicalDecisionKind, decide_technical_outcome
from agent_harness.policy import VerificationPolicy
from agent_harness.runner import CommandResult


def result(command: tuple[str, ...], exit_code: int | None) -> CommandResult:
    return CommandResult(
        command=command,
        exit_code=exit_code,
        stdout="",
        stderr="",
        duration_seconds=0.1,
        timed_out=False,
    )


def test_development_profile_returns_ordered_results_and_baseline_delta(
    tmp_path: Path,
) -> None:
    failing = (sys.executable, "-c", "import sys; raise SystemExit(1)")
    succeeding = (sys.executable, "-c", "print('later command')")
    policy = VerificationPolicy((failing, succeeding), ".", 5)
    baseline = VerificationBaseline.capture((result(failing, 0), result(succeeding, 1)))

    profile = run_development_profile(policy, tmp_path, baseline)

    assert profile.baseline == baseline
    assert tuple(item.command for item in profile.results) == (failing, succeeding)
    assert tuple(item.exit_code for item in profile.results) == (1, 0)
    assert profile.delta.new_failures == (profile.results[0],)
    assert profile.delta.recovered == (profile.results[1],)
    with pytest.raises(FrozenInstanceError):
        profile.__setattr__("results", ())


def test_development_profile_exposes_baseline_command_mismatch(tmp_path: Path) -> None:
    command = (sys.executable, "-c", "print('candidate')")
    policy = VerificationPolicy((command,), ".", 5)
    baseline = VerificationBaseline.capture((result(("different",), 0),))

    with pytest.raises(ValueError, match="same command sequence"):
        run_development_profile(policy, tmp_path, baseline)


def test_direct_profile_construction_copies_caller_owned_results() -> None:
    command = ("tests",)
    baseline = VerificationBaseline.capture((result(command, 0),))
    caller_owned_results = [result(command, 0)]
    profile = DevelopmentVerificationResult(
        baseline=baseline,
        results=caller_owned_results,  # type: ignore[arg-type]
        delta=baseline.compare(caller_owned_results),
    )

    caller_owned_results.clear()

    assert profile.results == (result(command, 0),)


@pytest.mark.parametrize(
    ("previous_code", "current_code", "timed_out", "empty_delta"),
    [
        (0, 1, False, True),
        (0, 0, True, False),
        (1, 0, False, False),
    ],
    ids=[
        "failure-hidden-by-empty-delta",
        "timeout-hidden-by-stale-success",
        "stale-failure-after-recovery",
    ],
)
def test_direct_profile_rejects_inconsistent_delta(
    previous_code: int, current_code: int, timed_out: bool, empty_delta: bool
) -> None:
    command = ("tests",)
    baseline = VerificationBaseline.capture((result(command, previous_code),))
    current = (replace(result(command, current_code), timed_out=timed_out),)
    supplied_delta = (
        VerificationDelta((), (), (), ())
        if empty_delta
        else baseline.compare(baseline.results)
    )

    with pytest.raises(ValueError, match="delta"):
        DevelopmentVerificationResult(baseline, current, supplied_delta)


@pytest.mark.parametrize(
    ("previous_code", "current_code", "timed_out", "expected_kind"),
    [
        (0, 1, False, TechnicalDecisionKind.FAIL),
        (0, 0, True, TechnicalDecisionKind.FAIL),
        (1, 1, False, TechnicalDecisionKind.BLOCKED),
        (1, 0, False, TechnicalDecisionKind.PASS),
        (0, 0, False, TechnicalDecisionKind.PASS),
    ],
    ids=["new-failure", "timeout", "preexisting-failure", "recovered", "success"],
)
def test_consistent_direct_profile_preserves_downstream_outcome(
    tmp_path: Path,
    previous_code: int,
    current_code: int,
    timed_out: bool,
    expected_kind: TechnicalDecisionKind,
) -> None:
    command = ("tests",)
    baseline = VerificationBaseline.capture((result(command, previous_code),))
    current = (replace(result(command, current_code), timed_out=timed_out),)
    supplied_delta = baseline.compare(current)

    profile = DevelopmentVerificationResult(baseline, current, supplied_delta)
    decision = decide_technical_outcome(profile)
    report = build_evidence_report(tmp_path, "candidate", profile, decision, ())

    assert profile.delta is supplied_delta
    assert decision.kind is expected_kind
    assert report.overall_kind is expected_kind


def test_direct_profile_rejects_different_command_sequence() -> None:
    baseline = VerificationBaseline.capture((result(("baseline",), 0),))
    current = (result(("different",), 0),)

    with pytest.raises(ValueError, match="same command sequence"):
        DevelopmentVerificationResult(
            baseline, current, baseline.compare(baseline.results)
        )
