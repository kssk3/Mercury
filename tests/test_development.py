from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent_harness.baseline import VerificationBaseline
from agent_harness.development import (
    DevelopmentVerificationResult,
    run_development_profile,
)
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
