"""Pure human escalation and exact proposal readmission; no execution authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from agent_harness.admission import AdmissionRecord, admit, record_admission
from agent_harness.amendment import AmendmentMateriality, classify_amendment
from agent_harness.contract import TaskContract
from agent_harness.failure import FailureAssessment
from agent_harness.policy import VerificationPolicy
from agent_harness.retry import RetryDecision, RetryLimits, RetryUsage, evaluate_retry


@dataclass(frozen=True)
class EscalationDecision:
    action: Literal["retry", "verify", "human"]
    reason: str
    attempt_ids: tuple[str, ...]
    retry_decision: RetryDecision


def evaluate_escalation(
    history: object,
    *,
    limits: RetryLimits,
    usage: RetryUsage,
    next_commands: int,
    next_seconds: float,
) -> EscalationDecision:
    decision = evaluate_retry(
        history,
        limits=limits,
        usage=usage,
        next_commands=next_commands,
        next_seconds=next_seconds,
    )
    identities: tuple[str, ...] = ()
    if decision.reason not in ("invalid_input", "invalid_history"):
        assessments = cast(
            list[FailureAssessment] | tuple[FailureAssessment, ...], history
        )
        identities = tuple(cast(str, item.attempt_id) for item in assessments)
    action: Literal["retry", "verify", "human"] = "human"
    if decision.reason == "retry_allowed":
        action = "retry"
    elif decision.reason == "no_failure":
        action = "verify"
    return EscalationDecision(action, decision.reason, identities, decision)


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid_request")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("invalid_request")
    return result


def _contract(value: object) -> TaskContract:
    if not isinstance(value, TaskContract):
        raise ValueError("invalid_request")
    # Reconstruct ordinary snapshots to reuse validation without mutating inputs.
    validated = TaskContract(
        value.goal,
        value.allowed_paths,
        value.protected_paths,
        value.completion_criteria,
    )
    if validated != value:
        raise ValueError("invalid_request")
    validated.to_json()
    return value


def _policy(value: object) -> VerificationPolicy:
    if not isinstance(value, VerificationPolicy):
        raise ValueError("invalid_request")
    validated = VerificationPolicy(
        value.commands, value.working_directory, value.timeout_seconds
    )
    if validated != value:
        raise ValueError("invalid_request")
    return value


@dataclass(frozen=True)
class AmendmentRequest:
    original: TaskContract
    proposed: TaskContract
    original_policy: VerificationPolicy
    proposed_policy: VerificationPolicy
    requested_at: str

    def __post_init__(self) -> None:
        try:
            _contract(self.original)
            _contract(self.proposed)
            _policy(self.original_policy)
            _policy(self.proposed_policy)
            _time(self.requested_at)
        except (TypeError, ValueError, AttributeError, OverflowError):
            raise ValueError("invalid_request") from None

    @property
    def proposal_id(self) -> str:
        self.__post_init__()
        snapshots = {
            "original": json.loads(self.original.to_json()),
            "proposed": json.loads(self.proposed.to_json()),
            "original_policy": _policy_data(self.original_policy),
            "proposed_policy": _policy_data(self.proposed_policy),
            "requested_at": self.requested_at,
        }
        canonical = json.dumps(snapshots, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def _policy_data(policy: VerificationPolicy) -> dict[str, object]:
    return dict(
        commands=policy.commands,
        working_directory=policy.working_directory,
        timeout_seconds=policy.timeout_seconds,
    )


@dataclass(frozen=True)
class ReadmissionRecord:
    admission: AdmissionRecord
    policy: VerificationPolicy
    proposal_id: str

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.admission, AdmissionRecord):
                raise ValueError("invalid_approval")
            self.admission.__post_init__()
            _policy(self.policy)
            if (
                not isinstance(self.proposal_id, str)
                or len(self.proposal_id) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in self.proposal_id
                )
            ):
                raise ValueError("invalid_approval")
        except (TypeError, ValueError, AttributeError, OverflowError):
            raise ValueError("invalid_approval") from None


def record_readmission(
    request: AmendmentRequest,
    *,
    approver: str,
    approved_at: str,
) -> ReadmissionRecord:
    try:
        if not isinstance(request, AmendmentRequest):
            raise ValueError("invalid_request")
        request.__post_init__()
    except (TypeError, ValueError, AttributeError, OverflowError):
        raise ValueError("invalid_request") from None
    try:
        if _time(approved_at) < _time(request.requested_at):
            raise ValueError("invalid_approval")
        admission = record_admission(
            request.proposed, approver=approver, approved_at=approved_at
        )
        return ReadmissionRecord(
            admission, request.proposed_policy, request.proposal_id
        )
    except (TypeError, ValueError, AttributeError, OverflowError):
        raise ValueError("invalid_approval") from None


@dataclass(frozen=True)
class AmendmentDecision:
    accepted: bool
    reason: str
    active_contract: TaskContract | None
    active_policy: VerificationPolicy | None


def evaluate_amendment(request: object, record: object = None) -> AmendmentDecision:
    try:
        if not isinstance(request, AmendmentRequest):
            raise ValueError("invalid_request")
        request.__post_init__()
    except (TypeError, ValueError, AttributeError, OverflowError):
        return AmendmentDecision(False, "invalid_request", None, None)
    if (
        classify_amendment(
            request.original,
            request.proposed,
            request.original_policy,
            request.proposed_policy,
        )
        is AmendmentMateriality.NO_CHANGE
    ):
        return AmendmentDecision(
            True, "no_change", request.original, request.original_policy
        )

    def retain_original(reason: str) -> AmendmentDecision:
        return AmendmentDecision(
            False, reason, request.original, request.original_policy
        )

    reason = "readmission_required" if record is None else "invalid_approval"
    try:
        if not isinstance(record, ReadmissionRecord):
            raise ValueError("invalid_approval")
        record.__post_init__()
        if (
            record.proposal_id != request.proposal_id
            or record.policy != request.proposed_policy
            or _time(record.admission.approved_at) < _time(request.requested_at)
        ):
            raise ValueError("invalid_approval")
        admit(request.proposed, record.admission)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return retain_original(reason)
    return AmendmentDecision(
        True, "readmitted", request.proposed, request.proposed_policy
    )
