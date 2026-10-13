from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from typing import Literal, cast

import pytest

from agent_harness.execution import (
    ExecutionEvent,
    ExecutionEvidence,
    ExecutionState,
    transition_execution,
)
from agent_harness.final import TechnicalDecisionKind


def test_admitted_new_execution_becomes_ready() -> None:
    result = transition_execution(
        ExecutionState.NEW, ExecutionEvent.ADMIT, ExecutionEvidence(admitted=True)
    )

    assert result.accepted is True
    assert result.original_state is ExecutionState.NEW
    assert result.next_state is ExecutionState.READY
    assert result.rejection_reason is None


def test_successful_sequence_requires_distinct_verification() -> None:
    current = ExecutionState.NEW
    steps = (
        (ExecutionEvent.ADMIT, ExecutionEvidence(admitted=True), ExecutionState.READY),
        (ExecutionEvent.START, None, ExecutionState.RUNNING),
        (
            ExecutionEvent.PROCESS_RESULT,
            ExecutionEvidence(process_status="successful_exit", exit_code=0),
            ExecutionState.PROCESS_SUCCEEDED,
        ),
        (
            ExecutionEvent.SCOPE_RESULT,
            ExecutionEvidence(scope_clear=True),
            ExecutionState.SCOPE_CLEAR,
        ),
        (
            ExecutionEvent.OUTPUT_RESULT,
            ExecutionEvidence(output_stored=True),
            ExecutionState.OUTPUT_STORED,
        ),
        (ExecutionEvent.BEGIN_VERIFICATION, None, ExecutionState.VERIFYING),
        (
            ExecutionEvent.VERIFICATION_RESULT,
            ExecutionEvidence(technical_decision=TechnicalDecisionKind.PASS),
            ExecutionState.PASSED,
        ),
    )
    for event, evidence, expected in steps:
        result = transition_execution(current, event, evidence)
        assert result.accepted is True
        assert result.original_state is current
        assert result.next_state is expected
        assert result.rejection_reason is None
        current = expected


@pytest.mark.parametrize(
    "status,code",
    [
        ("nonzero_exit", 1),
        ("nonzero_exit", -9),
        ("timeout", None),
        ("timeout", 0),
        ("timeout", -9),
        ("launch_error", None),
    ],
)
def test_failed_process_ends_execution(status: str, code: int | None) -> None:
    evidence = ExecutionEvidence(
        process_status=cast(
            Literal["successful_exit", "nonzero_exit", "timeout", "launch_error"],
            status,
        ),
        exit_code=code,
    )
    result = transition_execution(
        ExecutionState.RUNNING, ExecutionEvent.PROCESS_RESULT, evidence
    )
    assert result.accepted is True
    assert result.next_state is ExecutionState.FAILED
    assert result.original_state is ExecutionState.RUNNING
    assert result.rejection_reason is None


@pytest.mark.parametrize(
    "state,event,evidence,expected",
    [
        (
            ExecutionState.PROCESS_SUCCEEDED,
            ExecutionEvent.SCOPE_RESULT,
            ExecutionEvidence(scope_clear=False),
            ExecutionState.BLOCKED,
        ),
        (
            ExecutionState.SCOPE_CLEAR,
            ExecutionEvent.OUTPUT_RESULT,
            ExecutionEvidence(output_stored=False),
            ExecutionState.FAILED,
        ),
        (
            ExecutionState.VERIFYING,
            ExecutionEvent.VERIFICATION_RESULT,
            ExecutionEvidence(technical_decision=TechnicalDecisionKind.FAIL),
            ExecutionState.FAILED,
        ),
        (
            ExecutionState.VERIFYING,
            ExecutionEvent.VERIFICATION_RESULT,
            ExecutionEvidence(technical_decision=TechnicalDecisionKind.BLOCKED),
            ExecutionState.BLOCKED,
        ),
    ],
)
def test_negative_stage_facts_end_execution(
    state: ExecutionState,
    event: ExecutionEvent,
    evidence: ExecutionEvidence,
    expected: ExecutionState,
) -> None:
    result = transition_execution(state, event, evidence)
    assert result.accepted is True
    assert result.original_state is state
    assert result.next_state is expected
    assert result.rejection_reason is None


NONTERMINAL = (
    ExecutionState.NEW,
    ExecutionState.READY,
    ExecutionState.RUNNING,
    ExecutionState.PROCESS_SUCCEEDED,
    ExecutionState.SCOPE_CLEAR,
    ExecutionState.OUTPUT_STORED,
    ExecutionState.VERIFYING,
)
TERMINAL = (
    ExecutionState.PASSED,
    ExecutionState.FAILED,
    ExecutionState.BLOCKED,
    ExecutionState.STOPPED,
)


@pytest.mark.parametrize("state", NONTERMINAL)
@pytest.mark.parametrize(
    "event,expected",
    [
        (ExecutionEvent.FAIL, ExecutionState.FAILED),
        (ExecutionEvent.BLOCK, ExecutionState.BLOCKED),
        (ExecutionEvent.STOP, ExecutionState.STOPPED),
    ],
)
@pytest.mark.parametrize("evidence", [None, ExecutionEvidence()])
def test_caller_endings_from_every_active_stage(
    state: ExecutionState,
    event: ExecutionEvent,
    expected: ExecutionState,
    evidence: ExecutionEvidence | None,
) -> None:
    result = transition_execution(state, event, evidence)
    assert result.accepted is True
    assert result.original_state is state
    assert result.next_state is expected
    assert result.rejection_reason is None


def unchecked_facts(
    evidence: ExecutionEvidence, **changes: object
) -> ExecutionEvidence:
    """Exercise runtime validation with facts outside the static field types."""
    return cast(Callable[..., ExecutionEvidence], replace)(evidence, **changes)


def assert_rejected(
    state: object, event: object, evidence: object, reason: str
) -> None:
    result = transition_execution(state, event, evidence)
    assert result.accepted is False
    assert result.original_state is state
    assert result.next_state is state
    assert result.rejection_reason == reason
    assert result == transition_execution(state, event, evidence)


@pytest.mark.parametrize("state", TERMINAL)
@pytest.mark.parametrize("event", list(ExecutionEvent))
def test_terminal_states_reject_every_event(
    state: ExecutionState, event: ExecutionEvent
) -> None:
    assert_rejected(state, event, None, "terminal_state")


STAGE_INPUTS = (
    (ExecutionState.NEW, ExecutionEvent.ADMIT, ExecutionEvidence(admitted=True)),
    (ExecutionState.READY, ExecutionEvent.START, ExecutionEvidence()),
    (
        ExecutionState.RUNNING,
        ExecutionEvent.PROCESS_RESULT,
        ExecutionEvidence(process_status="successful_exit", exit_code=0),
    ),
    (
        ExecutionState.PROCESS_SUCCEEDED,
        ExecutionEvent.SCOPE_RESULT,
        ExecutionEvidence(scope_clear=True),
    ),
    (
        ExecutionState.SCOPE_CLEAR,
        ExecutionEvent.OUTPUT_RESULT,
        ExecutionEvidence(output_stored=True),
    ),
    (
        ExecutionState.OUTPUT_STORED,
        ExecutionEvent.BEGIN_VERIFICATION,
        ExecutionEvidence(),
    ),
    (
        ExecutionState.VERIFYING,
        ExecutionEvent.VERIFICATION_RESULT,
        ExecutionEvidence(technical_decision=TechnicalDecisionKind.PASS),
    ),
)


@pytest.mark.parametrize("state", NONTERMINAL)
@pytest.mark.parametrize("expected_state,event,evidence", STAGE_INPUTS)
def test_out_of_order_including_duplicates_and_skips_reject(
    state: ExecutionState,
    expected_state: ExecutionState,
    event: ExecutionEvent,
    evidence: ExecutionEvidence,
) -> None:
    if state is expected_state:
        assert transition_execution(state, event, evidence).accepted is True
    else:
        assert_rejected(state, event, evidence, "unexpected_event")


@pytest.mark.parametrize("state", [None, "new", "running", 0, True, [], {}])
def test_invalid_state_is_preserved_without_truthiness(state: object) -> None:
    assert_rejected(state, ExecutionEvent.STOP, None, "invalid_state")


@pytest.mark.parametrize("event", [None, "admit", "stop", 0, True, [], {}])
def test_invalid_event_rejects(event: object) -> None:
    assert_rejected(ExecutionState.NEW, event, None, "invalid_event")


@pytest.mark.parametrize(
    "evidence", [True, False, 0, "private evidence", [], {}, {"admitted": True}]
)
def test_invalid_evidence_record_rejects(evidence: object) -> None:
    assert_rejected(
        ExecutionState.NEW, ExecutionEvent.ADMIT, evidence, "invalid_evidence"
    )


@pytest.mark.parametrize("value", [None, False, 1, "true", [], {}])
def test_admission_requires_exact_true(value: object) -> None:
    assert_rejected(
        ExecutionState.NEW,
        ExecutionEvent.ADMIT,
        unchecked_facts(ExecutionEvidence(), admitted=value),
        "invalid_facts",
    )


@pytest.mark.parametrize(
    "status,code",
    [
        (None, None),
        (None, 0),
        ("unknown private text", 0),
        (True, 0),
        ([], 0),
        ({}, 0),
        ("successful_exit", None),
        ("successful_exit", 1),
        ("successful_exit", -1),
        ("successful_exit", False),
        ("successful_exit", 0.0),
        ("successful_exit", "0"),
        ("nonzero_exit", None),
        ("nonzero_exit", 0),
        ("nonzero_exit", True),
        ("nonzero_exit", 1.0),
        ("nonzero_exit", "1"),
        ("timeout", True),
        ("timeout", 0.0),
        ("timeout", "0"),
        ("timeout", []),
        ("launch_error", 0),
        ("launch_error", 1),
        ("launch_error", False),
    ],
)
def test_process_facts_must_be_recognized_and_consistent(
    status: object, code: object
) -> None:
    evidence = unchecked_facts(
        ExecutionEvidence(), process_status=status, exit_code=code
    )
    assert_rejected(
        ExecutionState.RUNNING, ExecutionEvent.PROCESS_RESULT, evidence, "invalid_facts"
    )


@pytest.mark.parametrize(
    "state,event,field",
    [
        (ExecutionState.PROCESS_SUCCEEDED, ExecutionEvent.SCOPE_RESULT, "scope_clear"),
        (ExecutionState.SCOPE_CLEAR, ExecutionEvent.OUTPUT_RESULT, "output_stored"),
    ],
)
@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_scope_and_storage_require_exact_booleans(
    state: ExecutionState, event: ExecutionEvent, field: str, value: object
) -> None:
    assert_rejected(
        state,
        event,
        unchecked_facts(ExecutionEvidence(), **{field: value}),
        "invalid_facts",
    )


@pytest.mark.parametrize("value", [None, "pass", "fail", "blocked", True, 0, [], {}])
def test_verification_requires_actual_decision_enum(value: object) -> None:
    assert_rejected(
        ExecutionState.VERIFYING,
        ExecutionEvent.VERIFICATION_RESULT,
        unchecked_facts(ExecutionEvidence(), technical_decision=value),
        "invalid_facts",
    )


@pytest.mark.parametrize(
    "state,event,evidence",
    STAGE_INPUTS
    + (
        (ExecutionState.RUNNING, ExecutionEvent.FAIL, ExecutionEvidence()),
        (ExecutionState.READY, ExecutionEvent.BLOCK, ExecutionEvidence()),
        (ExecutionState.NEW, ExecutionEvent.STOP, ExecutionEvidence()),
    ),
)
def test_all_irrelevant_fields_reject_even_falsey_facts(
    state: ExecutionState, event: ExecutionEvent, evidence: ExecutionEvidence
) -> None:
    required = {
        ExecutionEvent.ADMIT: {"admitted"},
        ExecutionEvent.PROCESS_RESULT: {"process_status", "exit_code"},
        ExecutionEvent.SCOPE_RESULT: {"scope_clear"},
        ExecutionEvent.OUTPUT_RESULT: {"output_stored"},
        ExecutionEvent.VERIFICATION_RESULT: {"technical_decision"},
    }.get(event, set())
    for field in (
        "admitted",
        "process_status",
        "exit_code",
        "scope_clear",
        "output_stored",
        "technical_decision",
    ):
        if field not in required:
            for value in (False, 0, "private evidence"):
                assert_rejected(
                    state,
                    event,
                    unchecked_facts(evidence, **{field: value}),
                    "irrelevant_evidence",
                )


def test_rejection_precedence_is_fixed_and_does_not_expose_evidence() -> None:
    assert_rejected("new", "admit", "secret", "invalid_state")
    assert_rejected(ExecutionState.PASSED, "stop", "secret", "invalid_event")
    assert_rejected(
        ExecutionState.PASSED, ExecutionEvent.STOP, "secret", "invalid_evidence"
    )
    assert_rejected(
        ExecutionState.PASSED,
        ExecutionEvent.ADMIT,
        ExecutionEvidence(admitted=False),
        "terminal_state",
    )
    assert_rejected(
        ExecutionState.READY,
        ExecutionEvent.ADMIT,
        ExecutionEvidence(scope_clear=False),
        "unexpected_event",
    )
    assert_rejected(
        ExecutionState.NEW,
        ExecutionEvent.ADMIT,
        ExecutionEvidence(admitted=False, scope_clear=False),
        "irrelevant_evidence",
    )


def test_evidence_and_transition_are_immutable_and_input_is_preserved() -> None:
    evidence = ExecutionEvidence(admitted=True)
    before = replace(evidence)
    result = transition_execution(ExecutionState.NEW, ExecutionEvent.ADMIT, evidence)
    assert evidence == before
    with pytest.raises(FrozenInstanceError):
        evidence.__setattr__("admitted", False)
    for field in ("original_state", "next_state", "accepted", "rejection_reason"):
        with pytest.raises(FrozenInstanceError):
            result.__setattr__(field, None)
    rejected = transition_execution(
        ExecutionState.READY, ExecutionEvent.ADMIT, evidence
    )
    assert rejected.accepted is False
    assert evidence == before
