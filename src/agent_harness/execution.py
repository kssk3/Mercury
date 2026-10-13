"""Pure execution-state decisions over caller-supplied facts.

No F1–F4 calls, I/O, retry or durable history. Successful process exit, scope
clearance and storage are separate from technical PASS. This path requires
storage; existing optional F4 storage behavior remains unchanged.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from agent_harness.final import TechnicalDecisionKind


class ExecutionState(StrEnum):
    NEW = "new"
    READY = "ready"
    RUNNING = "running"
    PROCESS_SUCCEEDED = "process_succeeded"
    SCOPE_CLEAR = "scope_clear"
    OUTPUT_STORED = "output_stored"
    VERIFYING = "verifying"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    STOPPED = "stopped"


class ExecutionEvent(StrEnum):
    ADMIT = "admit"
    START = "start"
    PROCESS_RESULT = "process_result"
    SCOPE_RESULT = "scope_result"
    OUTPUT_RESULT = "output_result"
    BEGIN_VERIFICATION = "begin_verification"
    VERIFICATION_RESULT = "verification_result"
    FAIL = "fail"
    BLOCK = "block"
    STOP = "stop"


@dataclass(frozen=True)
class ExecutionEvidence:
    """Optional event-specific facts; transition_execution validates at use."""

    admitted: bool | None = None
    process_status: (
        Literal["successful_exit", "nonzero_exit", "timeout", "launch_error"] | None
    ) = None
    exit_code: int | None = None
    scope_clear: bool | None = None
    output_stored: bool | None = None
    technical_decision: TechnicalDecisionKind | None = None


@dataclass(frozen=True)
class ExecutionTransition:
    """Immutable decision; rejected inputs retain the caller's state object."""

    original_state: object
    next_state: object
    accepted: bool
    rejection_reason: str | None


def transition_execution(
    current: object, event: object, evidence: object = None
) -> ExecutionTransition:
    """Decide one step without executing, persisting or authenticating facts.

    Rejection precedence: invalid_state, invalid_event, invalid_evidence
    (record type), terminal_state, unexpected_event, irrelevant_evidence,
    invalid_facts (missing, malformed or contradictory required facts).
    Reasons are fixed and never include caller evidence. None is empty evidence.
    Caller owns provenance and sequencing; PASSED is only technical success.
    """

    def reject(reason: str) -> ExecutionTransition:
        return ExecutionTransition(current, current, False, reason)

    if not isinstance(current, ExecutionState):
        return reject("invalid_state")
    if not isinstance(event, ExecutionEvent):
        return reject("invalid_event")
    if evidence is not None and not isinstance(evidence, ExecutionEvidence):
        return reject("invalid_evidence")
    if current in (
        ExecutionState.PASSED,
        ExecutionState.FAILED,
        ExecutionState.BLOCKED,
        ExecutionState.STOPPED,
    ):
        return reject("terminal_state")
    facts = evidence if isinstance(evidence, ExecutionEvidence) else ExecutionEvidence()
    stages = {
        ExecutionEvent.ADMIT: (ExecutionState.NEW, ("admitted",)),
        ExecutionEvent.START: (ExecutionState.READY, ()),
        ExecutionEvent.PROCESS_RESULT: (
            ExecutionState.RUNNING,
            ("process_status", "exit_code"),
        ),
        ExecutionEvent.SCOPE_RESULT: (
            ExecutionState.PROCESS_SUCCEEDED,
            ("scope_clear",),
        ),
        ExecutionEvent.OUTPUT_RESULT: (ExecutionState.SCOPE_CLEAR, ("output_stored",)),
        ExecutionEvent.BEGIN_VERIFICATION: (ExecutionState.OUTPUT_STORED, ()),
        ExecutionEvent.VERIFICATION_RESULT: (
            ExecutionState.VERIFYING,
            ("technical_decision",),
        ),
    }
    endings = {
        ExecutionEvent.FAIL: ExecutionState.FAILED,
        ExecutionEvent.BLOCK: ExecutionState.BLOCKED,
        ExecutionEvent.STOP: ExecutionState.STOPPED,
    }
    allowed: tuple[str, ...] = ()
    if event not in endings:
        expected, allowed = stages[event]
        if current is not expected:
            return reject("unexpected_event")
    for field in (
        "admitted",
        "process_status",
        "exit_code",
        "scope_clear",
        "output_stored",
        "technical_decision",
    ):
        if field not in allowed and getattr(facts, field) is not None:
            return reject("irrelevant_evidence")
    next_state = endings.get(event)
    if event is ExecutionEvent.ADMIT:
        if facts.admitted is not True:
            return reject("invalid_facts")
        next_state = ExecutionState.READY
    elif event is ExecutionEvent.START:
        next_state = ExecutionState.RUNNING
    elif event is ExecutionEvent.PROCESS_RESULT:
        status, code = facts.process_status, facts.exit_code
        if type(status) is not str:
            return reject("invalid_facts")
        if status == "successful_exit":
            if type(code) is not int or code != 0:
                return reject("invalid_facts")
            next_state = ExecutionState.PROCESS_SUCCEEDED
        elif status == "nonzero_exit":
            if type(code) is not int or code == 0:
                return reject("invalid_facts")
            next_state = ExecutionState.FAILED
        elif status == "timeout":
            if code is not None and type(code) is not int:
                return reject("invalid_facts")
            next_state = ExecutionState.FAILED
        elif status == "launch_error":
            if code is not None:
                return reject("invalid_facts")
            next_state = ExecutionState.FAILED
        else:
            return reject("invalid_facts")
    elif event is ExecutionEvent.SCOPE_RESULT:
        if type(facts.scope_clear) is not bool:
            return reject("invalid_facts")
        next_state = (
            ExecutionState.SCOPE_CLEAR if facts.scope_clear else ExecutionState.BLOCKED
        )
    elif event is ExecutionEvent.OUTPUT_RESULT:
        if type(facts.output_stored) is not bool:
            return reject("invalid_facts")
        next_state = (
            ExecutionState.OUTPUT_STORED
            if facts.output_stored
            else ExecutionState.FAILED
        )
    elif event is ExecutionEvent.BEGIN_VERIFICATION:
        next_state = ExecutionState.VERIFYING
    elif event is ExecutionEvent.VERIFICATION_RESULT:
        if not isinstance(facts.technical_decision, TechnicalDecisionKind):
            return reject("invalid_facts")
        next_state = {
            TechnicalDecisionKind.PASS: ExecutionState.PASSED,
            TechnicalDecisionKind.FAIL: ExecutionState.FAILED,
            TechnicalDecisionKind.BLOCKED: ExecutionState.BLOCKED,
        }[facts.technical_decision]
    return ExecutionTransition(current, next_state, True, None)
