from __future__ import annotations

import importlib
import importlib.util
from dataclasses import FrozenInstanceError, replace
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, cast

import pytest

from agent_harness.baseline import VerificationBaseline
from agent_harness.development import DevelopmentVerificationResult
from agent_harness.evidence import EvidenceReport, build_evidence_report
from agent_harness.final import TechnicalDecisionKind, decide_technical_outcome
from agent_harness.integrity import ProtectedInputCheck, ProtectedInputState
from agent_harness.runner import CommandResult

if TYPE_CHECKING:
    from agent_harness.review import ReviewBinding, ReviewReceipt


def _api() -> ModuleType:
    assert importlib.util.find_spec("agent_harness.review") is not None, (
        "the review receipt contract is not implemented"
    )
    return importlib.import_module("agent_harness.review")


def _binding() -> ReviewBinding:
    return cast(
        "ReviewBinding",
        _api().ReviewBinding(
            repository_root="/review-repository",
            target_revision="a" * 40,
            attempt_id="attempt-1",
            contract_sha256="b" * 64,
            candidate_sha256="c" * 64,
            evidence_sha256="d" * 64,
        ),
    )


def _receipt(state: str = "completed") -> ReviewReceipt:
    return cast(
        "ReviewReceipt",
        _api().ReviewReceipt(
            binding=_binding(),
            reviewer_id="independent-reviewer",
            report_reference="review-report",
            state=state,
            findings=(),
        ),
    )


def _evidence(kind: str = "pass") -> EvidenceReport:
    def command_result(exit_code: int) -> CommandResult:
        return CommandResult(("synthetic-check",), exit_code, "", "", 0.0, False)

    baseline = VerificationBaseline.capture(
        (command_result(1 if kind == "blocked" else 0),)
    )
    results = (command_result(1 if kind in {"fail", "blocked"} else 0),)
    profile = DevelopmentVerificationResult(
        baseline, results, baseline.compare(results)
    )
    checks = (
        (ProtectedInputCheck("protected.py", ProtectedInputState.MODIFIED),)
        if kind == "protected"
        else ()
    )
    return build_evidence_report(
        "/review-repository",
        "a" * 40,
        profile,
        decide_technical_outcome(profile),
        checks,
    )


def test_matching_completed_review_satisfies_only_technical_and_review_conditions() -> (
    None
):
    api = _api()
    assessment = api.assess_review(_evidence(), _binding(), _receipt())

    assert assessment.technical_kind is TechnicalDecisionKind.PASS
    assert assessment.review_kind == "clear"
    assert assessment.conditions_satisfied is True
    assert assessment.reason == "review_clear"


@pytest.mark.parametrize("state", [None, "running", "failed", "cancelled"])
def test_absent_or_noncompleted_review_never_clears(state: str | None) -> None:
    api = _api()
    receipt = None if state is None else _receipt(state)
    assessment = api.assess_review(_evidence(), _binding(), receipt)

    assert assessment.review_kind == "incomplete"
    assert assessment.conditions_satisfied is False
    assert assessment.reason == (
        "review_missing" if state is None else "review_incomplete"
    )


def test_every_binding_component_invalidates_foreign_or_stale_reviews() -> None:
    api = _api()
    changes = {
        "repository_root": "/other-repository",
        "target_revision": "e" * 40,
        "attempt_id": "attempt-2",
        "contract_sha256": "e" * 64,
        "candidate_sha256": "f" * 64,
        "evidence_sha256": "0" * 64,
    }
    for field, value in changes.items():
        stale = replace(_receipt(), binding=replace(_binding(), **{field: value}))
        assessment = api.assess_review(_evidence(), _binding(), stale)
        assert assessment.review_kind == "invalid", field
        assert assessment.conditions_satisfied is False, field
        assert assessment.reason == "receipt_binding_mismatch", field


def test_expected_binding_must_agree_with_actual_evidence_root_and_revision() -> None:
    api = _api()
    for evidence in (
        replace(_evidence(), repository_root=Path("/other-repository")),
        replace(_evidence(), target_revision="e" * 40),
    ):
        assessment = api.assess_review(evidence, _binding(), _receipt())
        assert assessment.review_kind == "invalid"
        assert assessment.conditions_satisfied is False
        assert assessment.reason == "evidence_binding_mismatch"


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("fail", TechnicalDecisionKind.FAIL),
        ("blocked", TechnicalDecisionKind.BLOCKED),
        ("protected", TechnicalDecisionKind.FAIL),
    ],
)
def test_clear_review_preserves_nonpassing_overall_technical_kind(
    kind: str, expected: TechnicalDecisionKind
) -> None:
    api = _api()
    evidence = _evidence(kind)
    if kind == "protected":
        assert evidence.technical_decision.kind is TechnicalDecisionKind.PASS
    assessment = api.assess_review(evidence, _binding(), _receipt())

    assert assessment.technical_kind is expected
    assert assessment.review_kind == "clear"
    assert assessment.conditions_satisfied is False
    assert assessment.reason == "technical_not_pass"


def test_unresolved_blocking_findings_request_changes() -> None:
    api = _api()
    blocking = api.ReviewFinding("B1", True, "unresolved")
    advisory = api.ReviewFinding("N1", False, "unresolved")
    receipt = replace(_receipt(), findings=(blocking, advisory))
    assessment = api.assess_review(_evidence(), _binding(), receipt)

    assert assessment.review_kind == "changes_requested"
    assert assessment.conditions_satisfied is False
    assert assessment.reason == "blocking_findings"
    advisory_only = api.assess_review(
        _evidence(), _binding(), replace(receipt, findings=(advisory,))
    )
    assert advisory_only.review_kind == "clear"


def test_evidenced_resolutions_and_human_approved_blocking_deferral_can_clear() -> None:
    api = _api()
    findings = (
        api.ReviewFinding("B1", True, "repaired", "repair-evidence"),
        api.ReviewFinding("B2", True, "rejected", "rejection-evidence"),
        api.ReviewFinding(
            "B3", True, "deferred", "deferral-evidence", "human-approval"
        ),
        api.ReviewFinding("N1", False, "deferred", "follow-up-evidence"),
    )
    assessment = api.assess_review(
        _evidence(), _binding(), replace(_receipt(), findings=findings)
    )
    assert assessment.review_kind == "clear"
    assert assessment.conditions_satisfied is True


def test_binding_rejects_missing_malformed_or_nonplain_identities() -> None:
    _api()

    class StringClaim(StrEnum):
        ATTEMPT = "attempt-1"

    invalid: tuple[tuple[str, object], ...] = (
        ("repository_root", "relative/repository"),
        ("repository_root", "/repo/../other"),
        ("repository_root", "/repo/./child"),
        ("repository_root", "/repo/"),
        ("repository_root", "//repo"),
        ("repository_root", "/repo\x00"),
        ("target_revision", "HEAD"),
        ("target_revision", "a" * 39),
        ("target_revision", "G" * 40),
        ("attempt_id", ""),
        ("attempt_id", " attempt-1"),
        ("attempt_id", StringClaim.ATTEMPT),
        ("attempt_id", None),
        ("contract_sha256", "g" * 64),
        ("candidate_sha256", "A" * 64),
        ("evidence_sha256", "d" * 63),
    )
    for field, value in invalid:
        with pytest.raises(ValueError):
            replace(_binding(), **{field: value})  # type: ignore[arg-type]
    assert replace(_binding(), target_revision="a" * 64).target_revision == "a" * 64


def test_findings_reject_inconsistent_resolution_claims() -> None:
    api = _api()

    class DispositionClaim(StrEnum):
        REPAIRED = "repaired"

    invalid: tuple[tuple[object, ...], ...] = (
        ("", True, "unresolved"),
        ("B1", 1, "unresolved"),
        ("B1", True, "unknown"),
        ("B1", True, DispositionClaim.REPAIRED, "evidence"),
        ("B1", True, "repaired"),
        ("B1", True, "rejected", " "),
        ("B1", False, "deferred"),
        ("B1", True, "deferred", "evidence"),
        ("B1", True, "unresolved", "resolution-evidence"),
        ("B1", True, "repaired", "evidence", "human-approval"),
        ("B1", False, "deferred", "evidence", "human-approval"),
    )
    for values in invalid:
        with pytest.raises(ValueError):
            api.ReviewFinding(*values)


def test_receipts_reject_unknown_states_duplicate_findings_and_invalid_records() -> (
    None
):
    api = _api()
    finding = api.ReviewFinding("B1", True, "unresolved")

    class StateClaim(StrEnum):
        COMPLETED = "completed"

    invalid: tuple[tuple[str, object], ...] = (
        ("binding", object()),
        ("reviewer_id", " "),
        ("report_reference", ""),
        ("report_reference", "report\nreference"),
        ("state", "unknown"),
        ("state", StateClaim.COMPLETED),
        ("findings", [finding]),
        ("findings", (finding, finding)),
        ("findings", (object(),)),
    )
    for field, value in invalid:
        with pytest.raises(ValueError):
            replace(_receipt(), **{field: value})  # type: ignore[arg-type]
    assessment = api.assess_review(_evidence(), _binding(), object())
    assert assessment.review_kind == "invalid"
    assert assessment.conditions_satisfied is False
    with pytest.raises(ValueError):
        api.assess_review(object(), _binding(), _receipt())
    with pytest.raises(ValueError):
        api.assess_review(_evidence(), object(), _receipt())


def test_assessment_is_immutable_repeatable_and_does_not_resolve_or_read_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    binding, evidence, receipt = _binding(), _evidence(), _receipt()
    finding = api.ReviewFinding("N1", False, "unresolved")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("review assessment must not access the filesystem")

    with monkeypatch.context() as guard:
        for operation in ("resolve", "read_text", "write_text", "open"):
            guard.setattr(Path, operation, forbidden)
        assessment = api.assess_review(evidence, binding, receipt)
        assert assessment == api.assess_review(evidence, binding, receipt)
        assert replace(binding, attempt_id="attempt-2").attempt_id == "attempt-2"
    for record, field, value in (
        (binding, "attempt_id", "changed"),
        (receipt, "state", "running"),
        (finding, "blocking", True),
        (assessment, "conditions_satisfied", False),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, value)
