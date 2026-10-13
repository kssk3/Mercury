from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from agent_harness.development import DevelopmentVerificationResult
from agent_harness.final import (
    TechnicalDecision,
    TechnicalDecisionKind,
    decide_technical_outcome,
)
from agent_harness.integrity import ProtectedInputCheck, ProtectedInputState


@dataclass(frozen=True)
class EvidenceReport:
    repository_root: Path
    target_revision: str
    profile: DevelopmentVerificationResult
    technical_decision: TechnicalDecision
    protected_input_checks: tuple[ProtectedInputCheck, ...]
    protected_input_violations: tuple[ProtectedInputCheck, ...]
    overall_kind: TechnicalDecisionKind

    def __post_init__(self) -> None:
        _require_target_revision(self.target_revision)
        if not isinstance(self.profile, DevelopmentVerificationResult):
            raise ValueError("profile must be a DevelopmentVerificationResult")
        if not isinstance(self.technical_decision, TechnicalDecision):
            raise ValueError("technical decision must be a TechnicalDecision")
        if self.technical_decision != decide_technical_outcome(self.profile):
            raise ValueError("technical decision must match the supplied profile")

        checks = _normalize_protected_input_checks(self.protected_input_checks)
        violations = _violations(checks)
        overall_kind = (
            TechnicalDecisionKind.FAIL if violations else self.technical_decision.kind
        )
        if self.protected_input_violations != violations:
            raise ValueError(
                "protected input violations must match the supplied checks"
            )
        if self.overall_kind is not overall_kind:
            raise ValueError("overall kind must match the supplied evidence")

        object.__setattr__(
            self,
            "repository_root",
            Path(self.repository_root).expanduser().resolve(),
        )
        object.__setattr__(self, "protected_input_checks", checks)


def build_evidence_report(
    repository_root: Path | str,
    target_revision: str,
    profile: DevelopmentVerificationResult,
    technical_decision: TechnicalDecision,
    protected_input_checks: Iterable[ProtectedInputCheck],
) -> EvidenceReport:
    if not isinstance(technical_decision, TechnicalDecision):
        raise ValueError("technical decision must be a TechnicalDecision")
    checks = _normalize_protected_input_checks(protected_input_checks)
    violations = _violations(checks)
    overall_kind = TechnicalDecisionKind.FAIL if violations else technical_decision.kind
    return EvidenceReport(
        repository_root=Path(repository_root),
        target_revision=target_revision,
        profile=profile,
        technical_decision=technical_decision,
        protected_input_checks=checks,
        protected_input_violations=violations,
        overall_kind=overall_kind,
    )


def _require_target_revision(target_revision: object) -> None:
    if (
        not isinstance(target_revision, str)
        or not target_revision.strip()
        or target_revision != target_revision.strip()
    ):
        raise ValueError("target revision must be a nonblank string without whitespace")


def _normalize_protected_input_checks(
    checks: Iterable[ProtectedInputCheck],
) -> tuple[ProtectedInputCheck, ...]:
    if isinstance(checks, ProtectedInputCheck):
        raise ValueError("protected input checks must be a collection")
    normalized = tuple(checks)
    if not all(isinstance(check, ProtectedInputCheck) for check in normalized):
        raise ValueError("protected input checks must be ProtectedInputCheck values")
    paths = tuple(check.path for check in normalized)
    if not all(paths) or len(set(paths)) != len(paths):
        raise ValueError("protected input checks must use unique nonblank paths")
    if not all(isinstance(check.state, ProtectedInputState) for check in normalized):
        raise ValueError("protected input checks must have recognized states")
    return normalized


def _violations(
    checks: tuple[ProtectedInputCheck, ...],
) -> tuple[ProtectedInputCheck, ...]:
    return tuple(
        check for check in checks if check.state is not ProtectedInputState.UNCHANGED
    )
