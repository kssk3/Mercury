from __future__ import annotations

import importlib
import importlib.util
from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest

from agent_harness.contract import TaskContract
from agent_harness.failure import FailureAssessment, FailureFinding
from agent_harness.policy import VerificationPolicy
from agent_harness.retry import RetryLimits, RetryUsage, evaluate_retry


def api() -> Any:
    assert importlib.util.find_spec("agent_harness.escalation") is not None, (
        "H5 API missing"
    )
    return importlib.import_module("agent_harness.escalation")


@pytest.mark.parametrize(
    "kind,reason,action",
    [
        ("native", "native_nonzero", "retry"),
        ("unknown", "missing_scope", "human"),
        ("scope", "scope_violation", "human"),
        ("observation", "observation_failed", "human"),
        (None, None, "verify"),
    ],
)
def test_escalation_composes_h4_without_mutation(
    kind: Any, reason: Any, action: str
) -> None:
    findings = () if kind is None else (FailureFinding(kind, reason),)
    history = [FailureAssessment("attempt", None, findings)]
    arguments: dict[str, Any] = dict(
        limits=RetryLimits(3, 3, 10, 20),
        usage=RetryUsage(1, 1),
        next_commands=1,
        next_seconds=1,
    )
    result = api().evaluate_escalation(history, **arguments)
    assert result.action == action
    assert result.retry_decision == evaluate_retry(history, **arguments)
    assert result.attempt_ids == ("attempt",)
    assert history == [FailureAssessment("attempt", None, findings)]
    with pytest.raises(FrozenInstanceError):
        result.action = "pass"


@pytest.mark.parametrize(
    "history,overrides,reason",
    [
        ([], {}, "invalid_history"),
        (
            [FailureAssessment("same", None, ()), FailureAssessment("same", None, ())],
            {},
            "invalid_history",
        ),
        (
            [FailureAssessment("one", None, ())],
            {"next_commands": True},
            "invalid_input",
        ),
        (
            [
                FailureAssessment(
                    "one", None, (FailureFinding("native", "native_nonzero"),)
                )
            ],
            {"limits": RetryLimits(0, 3, 10, 20)},
            "retry_budget",
        ),
        (
            [
                FailureAssessment(
                    "one", None, (FailureFinding("native", "native_nonzero"),)
                )
            ],
            {"usage": RetryUsage(10, 1)},
            "command_budget",
        ),
        (
            [
                FailureAssessment(
                    "one", None, (FailureFinding("native", "native_nonzero"),)
                )
            ],
            {"usage": RetryUsage(1, 20)},
            "time_budget",
        ),
    ],
)
def test_escalation_fails_closed(
    history: object, overrides: dict[str, Any], reason: str
) -> None:
    arguments: dict[str, Any] = dict(
        limits=RetryLimits(3, 3, 10, 20),
        usage=RetryUsage(1, 1),
        next_commands=1,
        next_seconds=1,
    )
    arguments.update(overrides)
    result = api().evaluate_escalation(history, **arguments)
    assert (result.action, result.reason) == ("human", reason)
    if reason.startswith("invalid"):
        assert result.attempt_ids == ()


def request(**overrides: Any) -> Any:
    contract = TaskContract("Task", (), (), ("tests",))
    policy = VerificationPolicy((("pytest",),), ".", 60)
    values = dict(
        original=contract,
        proposed=replace(contract, goal="Changed"),
        original_policy=policy,
        proposed_policy=policy,
        requested_at="2026-10-06T00:00:00+00:00",
    )
    values.update(overrides)
    return api().AmendmentRequest(**values)


@pytest.mark.parametrize(
    "contract_changes,policy_changes",
    [
        ({"goal": "New"}, {}),
        ({"allowed_paths": ("src",)}, {}),
        ({"protected_paths": ("secret",)}, {}),
        ({"completion_criteria": ("lint",)}, {}),
        ({}, {"commands": (("ruff", "check"),)}),
        ({}, {"working_directory": "src"}),
        ({}, {"timeout_seconds": 30}),
    ],
)
def test_each_material_field_needs_exact_fresh_admission(
    contract_changes: dict[str, Any], policy_changes: dict[str, Any]
) -> None:
    module = api()
    base = request()
    req = request(
        proposed=replace(base.original, **contract_changes),
        proposed_policy=replace(base.original_policy, **policy_changes),
    )
    denied = module.evaluate_amendment(req)
    assert not denied.accepted
    assert denied.active_contract == req.original
    assert denied.active_policy == req.original_policy
    record = module.record_readmission(
        req, approver="synthetic-approver", approved_at=req.requested_at
    )
    accepted = module.evaluate_amendment(req, record)
    assert accepted.accepted
    assert accepted.active_contract == req.proposed
    assert accepted.active_policy == req.proposed_policy
    with pytest.raises(FrozenInstanceError):
        record.proposal_id = "other"


@pytest.mark.parametrize("change", ["baseline", "request", "proposal", "policy"])
def test_approval_cannot_cross_proposals(change: str) -> None:
    module = api()
    req = request()
    record = module.record_readmission(
        req, approver="synthetic-approver", approved_at="2026-10-06T01:00:00+00:00"
    )
    changes = {
        "baseline": dict(original=replace(req.original, goal="Different baseline")),
        "request": dict(requested_at="2026-10-06T00:01:00+00:00"),
        "proposal": dict(proposed=replace(req.proposed, goal="Different proposal")),
        "policy": dict(
            proposed_policy=replace(req.proposed_policy, timeout_seconds=31)
        ),
    }
    different = replace(req, **changes[change])
    result = module.evaluate_amendment(different, record)
    assert not result.accepted
    assert result.active_contract == different.original
    assert result.active_policy == different.original_policy


def test_stale_approval_and_malformed_request_never_activate() -> None:
    module = api()
    req = request()
    with pytest.raises(ValueError, match="^invalid_approval$"):
        module.record_readmission(
            req, approver="synthetic-approver", approved_at="2026-10-05T00:00:00+00:00"
        )
    invalid_requests: tuple[object, ...] = (None, {}, "private input")
    for value in invalid_requests:
        result = module.evaluate_amendment(value)
        assert (
            result.accepted,
            result.reason,
            result.active_contract,
            result.active_policy,
        ) == (False, "invalid_request", None, None)
    for timestamp in ("bad", "2026-10-06T00:00:00"):
        with pytest.raises(ValueError, match="^invalid_request$"):
            request(requested_at=timestamp)
    result = module.evaluate_amendment(req, {})
    assert not result.accepted
    assert result.active_contract == req.original


def test_no_change_uses_original_without_approval() -> None:
    req = request()
    req = replace(req, proposed=req.original)
    result = api().evaluate_amendment(req)
    assert (result.accepted, result.reason) == (True, "no_change")
    assert result.active_contract is req.original
    assert result.active_policy is req.original_policy


@pytest.mark.parametrize(
    "finding,action,reason",
    [
        (
            FailureFinding("verification", "verification_nonzero", "new", "a" * 64),
            "retry",
            "retry_allowed",
        ),
        (
            FailureFinding("verification", "verification_nonzero", "unknown", "a" * 64),
            "human",
            "unsafe_failure",
        ),
        (
            FailureFinding("protected_input", "protected_input_violation"),
            "human",
            "unsafe_failure",
        ),
    ],
)
def test_real_h4_failure_attribution_survives_escalation(
    finding: FailureFinding, action: str, reason: str
) -> None:
    history = [FailureAssessment("current", None, (finding,))]
    result = api().evaluate_escalation(
        history,
        limits=RetryLimits(3, 3, 10, 20),
        usage=RetryUsage(1, 1),
        next_commands=1,
        next_seconds=1,
    )
    assert (result.action, result.reason) == (action, reason)
    assert result.attempt_ids == ("current",)


def test_earlier_unsafe_history_remains_a_hard_stop_after_clean_attempt() -> None:
    history = [
        FailureAssessment("old", None, (FailureFinding("unknown", "missing_scope"),)),
        FailureAssessment("clean", None, ()),
    ]
    result = api().evaluate_escalation(
        history,
        limits=RetryLimits(3, 3, 10, 20),
        usage=RetryUsage(1, 1),
        next_commands=1,
        next_seconds=1,
    )
    assert (result.action, result.reason, result.attempt_ids) == (
        "human",
        "unsafe_failure",
        ("old", "clean"),
    )


def test_approval_does_not_reset_history_or_consumed_retry_budget() -> None:
    module = api()
    finding = FailureFinding("native", "native_nonzero")
    history = [
        FailureAssessment("one", None, (finding,)),
        FailureAssessment("two", None, (finding,)),
    ]
    limits, usage = RetryLimits(3, 2, 10, 20), RetryUsage(2, 2)
    before = tuple(history), limits, usage
    req = request()
    record = module.record_readmission(
        req, approver="synthetic-approver", approved_at=req.requested_at
    )
    approved = module.evaluate_amendment(req, record)
    assert approved.accepted
    result = module.evaluate_escalation(
        history, limits=limits, usage=usage, next_commands=1, next_seconds=1
    )
    assert (result.action, result.reason, result.retry_decision.completed_retries) == (
        "human",
        "same_failure",
        1,
    )
    assert (tuple(history), limits, usage) == before
    for value, field in ((req, "requested_at"), (approved, "accepted")):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, None)
    assert req.original == TaskContract("Task", (), (), ("tests",))
    assert req.original_policy == VerificationPolicy((("pytest",),), ".", 60)


@pytest.mark.parametrize("variant", ["stale", "contract", "policy", "proposal"])
def test_constructed_readmission_cannot_bypass_exact_fresh_binding(
    variant: str,
) -> None:
    from agent_harness.admission import record_admission

    module = api()
    req = request()
    admission = record_admission(
        req.proposed, approver="synthetic-approver", approved_at=req.requested_at
    )
    policy, proposal = req.proposed_policy, req.proposal_id
    if variant == "stale":
        admission = record_admission(
            req.proposed,
            approver="synthetic-approver",
            approved_at="2026-10-05T23:59:59+00:00",
        )
    elif variant == "contract":
        admission = record_admission(
            req.original, approver="synthetic-approver", approved_at=req.requested_at
        )
    elif variant == "policy":
        policy = replace(policy, timeout_seconds=30)
    else:
        proposal = "b" * 64
    record = module.ReadmissionRecord(admission, policy, proposal)
    result = module.evaluate_amendment(req, record)
    assert (result.accepted, result.reason) == (False, "invalid_approval")
    assert result.active_contract is req.original
    assert result.active_policy is req.original_policy


@pytest.mark.parametrize(
    "snapshot,field,value",
    [
        ("original", "goal", 7),
        ("proposed", "completion_criteria", None),
        ("original", "allowed_paths", ("../private",)),
        ("proposed", "protected_paths", (None,)),
        ("original_policy", "commands", (("pytest", None),)),
        ("proposed_policy", "working_directory", None),
        ("proposed_policy", "timeout_seconds", True),
    ],
)
def test_malformed_ordinary_snapshots_fail_closed_without_echo(
    snapshot: str, field: str, value: object
) -> None:
    module = api()
    req = request()
    malformed = replace(getattr(req, snapshot))
    object.__setattr__(malformed, field, value)
    with pytest.raises(ValueError, match="^invalid_request$"):
        replace(req, **{snapshot: malformed})
    object.__setattr__(req, snapshot, malformed)
    result = module.evaluate_amendment(req)
    assert (
        result.accepted,
        result.reason,
        result.active_contract,
        result.active_policy,
    ) == (False, "invalid_request", None, None)


@pytest.mark.parametrize(
    "approver,approved_at",
    [
        ("", "2026-10-06T00:00:00+00:00"),
        ("synthetic-approver", "2026-10-06T00:00:00"),
        ("synthetic-approver", "private"),
    ],
)
def test_recording_malformed_approval_uses_fixed_diagnostic(
    approver: str, approved_at: str
) -> None:
    with pytest.raises(ValueError, match="^invalid_approval$"):
        api().record_readmission(request(), approver=approver, approved_at=approved_at)
