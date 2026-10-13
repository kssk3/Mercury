from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent_harness.baseline import VerificationBaseline
from agent_harness.development import DevelopmentVerificationResult
from agent_harness.evidence import EvidenceReport, build_evidence_report
from agent_harness.final import (
    TechnicalDecision,
    TechnicalDecisionKind,
    decide_technical_outcome,
)
from agent_harness.integrity import (
    ProtectedInputCheck,
    ProtectedInputState,
    capture_protected_directories,
    capture_protected_inputs,
    verify_protected_directories,
    verify_protected_inputs,
)
from agent_harness.policy import VerificationPolicy
from agent_harness.runner import CommandResult, ControlledCommandRunner


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


def test_report_retains_clean_evidence_and_d4_pass(tmp_path: Path) -> None:
    command = ("tests",)
    verification = profile((result(command, 0),), (result(command, 0),))
    decision = decide_technical_outcome(verification)
    checks = (
        ProtectedInputCheck("tests/test_acceptance.py", ProtectedInputState.UNCHANGED),
    )

    report = build_evidence_report(
        tmp_path, "candidate-123", verification, decision, checks
    )

    assert report == EvidenceReport(
        repository_root=tmp_path.resolve(),
        target_revision="candidate-123",
        profile=verification,
        technical_decision=decision,
        protected_input_checks=checks,
        protected_input_violations=(),
        overall_kind=TechnicalDecisionKind.PASS,
    )
    assert report.profile is verification
    assert report.technical_decision is decision
    with pytest.raises(FrozenInstanceError):
        report.target_revision = "replacement"  # type: ignore[misc]


@pytest.mark.parametrize(
    "verification",
    [
        profile((result(("new-failure",), 0),), (result(("new-failure",), 1),)),
        profile((result(("known-failure",), 1),), (result(("known-failure",), 1),)),
    ],
)
def test_report_preserves_non_passing_d4_kind_when_inputs_are_clean(
    tmp_path: Path,
    verification: DevelopmentVerificationResult,
) -> None:
    decision = decide_technical_outcome(verification)

    report = build_evidence_report(
        tmp_path,
        "candidate-123",
        verification,
        decision,
        (ProtectedInputCheck("tests/test_safety.py", ProtectedInputState.UNCHANGED),),
    )

    assert report.overall_kind is decision.kind
    assert report.protected_input_violations == ()


def test_report_fails_for_ordered_protected_input_violations(tmp_path: Path) -> None:
    command = ("known-failure",)
    verification = profile((result(command, 1),), (result(command, 1),))
    decision = decide_technical_outcome(verification)
    checks = (
        ProtectedInputCheck("policy.toml", ProtectedInputState.UNCHANGED),
        ProtectedInputCheck("tests/test_changed.py", ProtectedInputState.MODIFIED),
        ProtectedInputCheck("tests/test_missing.py", ProtectedInputState.MISSING),
        ProtectedInputCheck("tests/test_link.py", ProtectedInputState.UNVERIFIABLE),
    )

    report = build_evidence_report(
        tmp_path, "candidate-123", verification, decision, checks
    )

    assert report.technical_decision is decision
    assert report.overall_kind is TechnicalDecisionKind.FAIL
    assert report.protected_input_violations == checks[1:]


def test_report_rejects_ambiguous_or_mismatched_evidence(tmp_path: Path) -> None:
    command = ("tests",)
    verification = profile((result(command, 0),), (result(command, 0),))
    decision = decide_technical_outcome(verification)
    duplicate_checks = (
        ProtectedInputCheck("tests/test_safety.py", ProtectedInputState.UNCHANGED),
        ProtectedInputCheck("tests/test_safety.py", ProtectedInputState.MODIFIED),
    )
    unknown_state = (
        ProtectedInputCheck("tests/test_safety.py", "unknown"),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError):
        build_evidence_report(tmp_path, " ", verification, decision, ())
    with pytest.raises(ValueError):
        build_evidence_report(
            tmp_path,
            "candidate-123",
            verification,
            object(),  # type: ignore[arg-type]
            (),
        )
    with pytest.raises(ValueError):
        build_evidence_report(
            tmp_path,
            "candidate-123",
            verification,
            TechnicalDecision(TechnicalDecisionKind.FAIL, (command,)),
            (),
        )
    with pytest.raises(ValueError):
        build_evidence_report(
            tmp_path, "candidate-123", verification, decision, duplicate_checks
        )
    with pytest.raises(ValueError):
        build_evidence_report(
            tmp_path, "candidate-123", verification, decision, unknown_state
        )


def test_new_conftest_skip_forces_directory_report_fail_despite_pytest_exit_zero(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "tests"
    directory.mkdir()
    test_bytes = b"def test_acceptance(): assert True\n"
    protected_test = directory / "test_acceptance.py"
    protected_test.write_bytes(test_bytes)
    file_snapshot = capture_protected_inputs(tmp_path, ("tests/test_acceptance.py",))
    directory_snapshot = capture_protected_directories(tmp_path, ("tests",))
    command = (
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests",
    )
    policy = VerificationPolicy((command,), ".", 15)
    runner = ControlledCommandRunner()
    baseline = runner.run(policy, tmp_path)
    assert baseline[0].exit_code == 0
    assert "1 passed" in baseline[0].stdout
    clean_profile = profile(baseline, baseline)
    clean_report = build_evidence_report(
        tmp_path,
        "baseline",
        clean_profile,
        decide_technical_outcome(clean_profile),
        verify_protected_directories(tmp_path, directory_snapshot),
    )
    assert clean_report.overall_kind is TechnicalDecisionKind.PASS

    (directory / "conftest.py").write_text(
        "import pytest\n"
        "def pytest_collection_modifyitems(items):\n"
        "    for item in items:\n"
        "        item.add_marker(pytest.mark.skip(reason='new hook'))\n"
    )
    candidate = runner.run(policy, tmp_path)
    assert candidate[0].exit_code == 0
    assert "1 skipped" in candidate[0].stdout
    assert protected_test.read_bytes() == test_bytes
    verification = profile(baseline, candidate)
    decision = decide_technical_outcome(verification)
    legacy_report = build_evidence_report(
        tmp_path,
        "candidate",
        verification,
        decision,
        verify_protected_inputs(tmp_path, file_snapshot),
    )
    report = build_evidence_report(
        tmp_path,
        "candidate",
        verification,
        decision,
        verify_protected_directories(tmp_path, directory_snapshot),
    )

    assert legacy_report.overall_kind is TechnicalDecisionKind.PASS
    assert report.technical_decision.kind is TechnicalDecisionKind.PASS
    assert report.protected_input_violations == (
        ProtectedInputCheck("tests", ProtectedInputState.MODIFIED),
    )
    assert report.overall_kind is TechnicalDecisionKind.FAIL


@pytest.mark.parametrize(
    ("replacement", "state"),
    [
        ("missing", ProtectedInputState.MISSING),
        ("symlink", ProtectedInputState.UNVERIFIABLE),
    ],
)
def test_directory_missing_or_unsafe_evidence_forces_report_fail(
    tmp_path: Path, replacement: str, state: ProtectedInputState
) -> None:
    directory = tmp_path / "tests"
    directory.mkdir()
    captured = capture_protected_directories(tmp_path, ("tests",))
    directory.rmdir()
    if replacement == "symlink":
        directory.symlink_to("missing", target_is_directory=True)
    verification = profile((result(("tests",), 0),), (result(("tests",), 0),))
    report = build_evidence_report(
        tmp_path,
        "candidate",
        verification,
        decide_technical_outcome(verification),
        verify_protected_directories(tmp_path, captured),
    )

    assert report.protected_input_violations == (ProtectedInputCheck("tests", state),)
    assert report.overall_kind is TechnicalDecisionKind.FAIL
