"""Immutable caller-supplied review claims and pure technical/review assessment.

The caller establishes canonical roots, content/evidence digests, reviewer
independence, resolution correctness and human approval. References are opaque:
this module never reads them, hashes files or grants execution/delivery authority.
"""

from dataclasses import dataclass
from pathlib import PurePath
from typing import Literal

from agent_harness.evidence import EvidenceReport
from agent_harness.final import TechnicalDecisionKind


def _require_text(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("identities and references must be nonblank plain strings")
    return value


def _require_hex(value: object, lengths: tuple[int, ...]) -> None:
    text = _require_text(value)
    if len(text) not in lengths or any(char not in "0123456789abcdef" for char in text):
        raise ValueError("digest or revision must be full lowercase hexadecimal")


@dataclass(frozen=True)
class ReviewBinding:
    """Caller-established identity; candidate_sha256 covers content, not just HEAD."""

    repository_root: str
    target_revision: str
    attempt_id: str
    contract_sha256: str
    candidate_sha256: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        root = PurePath(_require_text(self.repository_root))
        if (
            not root.is_absolute()
            or root.anchor == "//"
            or ".." in root.parts
            or str(root) != self.repository_root
        ):
            raise ValueError("repository root must already be canonical and absolute")
        _require_hex(self.target_revision, (40, 64))
        _require_text(self.attempt_id)
        for digest in (
            self.contract_sha256,
            self.candidate_sha256,
            self.evidence_sha256,
        ):
            _require_hex(digest, (64,))


@dataclass(frozen=True)
class ReviewFinding:
    """A resolution claim, not verification that a repair or approval occurred."""

    finding_id: str
    blocking: bool
    disposition: Literal["unresolved", "repaired", "rejected", "deferred"]
    evidence_reference: str | None = None
    human_approval_reference: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.finding_id)
        if type(self.blocking) is not bool:
            raise ValueError("blocking must be an exact bool")
        if type(self.disposition) is not str or self.disposition not in (
            "unresolved",
            "repaired",
            "rejected",
            "deferred",
        ):
            raise ValueError("finding disposition must be recognized plain text")
        if self.disposition == "unresolved":
            if self.evidence_reference is not None:
                raise ValueError("unresolved findings cannot claim resolution evidence")
        else:
            _require_text(self.evidence_reference)
        if self.blocking and self.disposition == "deferred":
            _require_text(self.human_approval_reference)
        elif self.human_approval_reference is not None:
            raise ValueError(
                "human approval applies only to deferred blocking findings"
            )


@dataclass(frozen=True)
class ReviewReceipt:
    """Immutable report claim; malformed fields raise ValueError at construction."""

    binding: ReviewBinding
    reviewer_id: str
    report_reference: str
    state: Literal["completed", "running", "failed", "cancelled"]
    findings: tuple[ReviewFinding, ...] = ()

    def __post_init__(self) -> None:
        if type(self.binding) is not ReviewBinding:
            raise ValueError("receipt binding must be a ReviewBinding")
        _require_text(self.reviewer_id)
        _require_text(self.report_reference)
        if type(self.state) is not str or self.state not in (
            "completed",
            "running",
            "failed",
            "cancelled",
        ):
            raise ValueError("review state must be recognized plain text")
        if type(self.findings) is not tuple or any(
            type(finding) is not ReviewFinding for finding in self.findings
        ):
            raise ValueError("findings must be a tuple of ReviewFinding records")
        ids = tuple(finding.finding_id for finding in self.findings)
        if len(set(ids)) != len(ids):
            raise ValueError("finding IDs must be unique within a receipt")


@dataclass(frozen=True)
class ReviewAssessment:
    """Conditions satisfied is a fact comparison, never permission or delivery."""

    technical_kind: TechnicalDecisionKind
    review_kind: Literal["clear", "changes_requested", "incomplete", "invalid"]
    reason: str
    conditions_satisfied: bool


def assess_review(
    report: EvidenceReport,
    expected: ReviewBinding,
    receipt: ReviewReceipt | None,
) -> ReviewAssessment:
    """Compare already-established facts without I/O, preparation or persistence.

    Invalid report/expected record types raise ValueError. Receipt absence is
    incomplete; a foreign receipt type or any identity mismatch is invalid.
    Precedence is evidence binding, absence, receipt type/binding, completion,
    blocking findings, then technical eligibility. All reasons are fixed text.
    """
    if type(report) is not EvidenceReport or type(expected) is not ReviewBinding:
        raise ValueError("assessment requires EvidenceReport and ReviewBinding records")
    technical = report.overall_kind
    if (
        str(report.repository_root) != expected.repository_root
        or report.target_revision != expected.target_revision
    ):
        return ReviewAssessment(
            technical, "invalid", "evidence_binding_mismatch", False
        )
    if receipt is None:
        return ReviewAssessment(technical, "incomplete", "review_missing", False)
    if type(receipt) is not ReviewReceipt:
        return ReviewAssessment(technical, "invalid", "invalid_receipt", False)
    if receipt.binding != expected:
        return ReviewAssessment(technical, "invalid", "receipt_binding_mismatch", False)
    if receipt.state != "completed":
        return ReviewAssessment(technical, "incomplete", "review_incomplete", False)
    if any(
        finding.blocking and finding.disposition == "unresolved"
        for finding in receipt.findings
    ):
        return ReviewAssessment(
            technical, "changes_requested", "blocking_findings", False
        )
    if technical is not TechnicalDecisionKind.PASS:
        return ReviewAssessment(technical, "clear", "technical_not_pass", False)
    return ReviewAssessment(technical, "clear", "review_clear", True)
