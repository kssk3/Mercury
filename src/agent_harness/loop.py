"""Bounded loop composition; callers own serialization/the B4 repository lease."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import cast

from agent_harness.adapter import CodexCLIAdapter, CodexTurnResult, TurnBudgets
from agent_harness.admission import AdmissionRecord
from agent_harness.attempt import AttemptJournal, _observation
from agent_harness.baseline import VerificationBaseline
from agent_harness.context import ContextPacket
from agent_harness.contract import TaskContract
from agent_harness.development import DevelopmentVerificationResult
from agent_harness.escalation import EscalationDecision, evaluate_escalation
from agent_harness.evidence import EvidenceReport, build_evidence_report
from agent_harness.execution import (
    ExecutionEvent,
    ExecutionEvidence,
    ExecutionState,
    transition_execution,
)
from agent_harness.failure import (
    FailureAssessment,
    FailureFinding,
    classify_attempt_failure,
)
from agent_harness.final import TechnicalDecisionKind, decide_technical_outcome
from agent_harness.integrity import (
    ProtectedDirectoryFingerprint,
    ProtectedInputCheck,
    ProtectedInputFingerprint,
    capture_protected_directories,
    capture_protected_inputs,
    verify_protected_directories,
    verify_protected_inputs,
)
from agent_harness.journal import EventJournal
from agent_harness.native_output import (
    NativeOutputError,
    _validate_storage,
    run_stored_scoped_turn,
)
from agent_harness.policy import VerificationPolicy
from agent_harness.retry import RetryLimits, RetryUsage
from agent_harness.runner import CommandResult, ControlledCommandRunner
from agent_harness.scope import ScopeTurnError
from agent_harness.state import CurrentStateStore
from agent_harness.turn import (
    RepositoryObservation,
    TurnObservationError,
    _append,
    _validate_turn,
)
from agent_harness.verification_checkpoint import (
    carried_elapsed,
    restore_results,
    safe_results,
    validate_boundaries,
)


@dataclass(frozen=True)
class CriterionBinding:
    """Exact admitted commands, without a natural-language semantic proof."""

    criterion: str
    commands: tuple[tuple[str, ...], ...]


def bind_completion_criteria(
    contract: TaskContract, policy: VerificationPolicy, mapping: object
) -> tuple[CriterionBinding, ...]:
    """Freeze complete exact criterion mappings or raise a fixed diagnostic.

    This pure preflight performs no execution and grants no technical PASS.
    """
    try:
        if not isinstance(contract, TaskContract) or not isinstance(
            policy, VerificationPolicy
        ):
            raise ValueError
        criteria = contract.completion_criteria
        if len(criteria) != len(set(criteria)) or not isinstance(mapping, Mapping):
            raise ValueError
        if set(mapping) != set(criteria):
            raise ValueError
        bindings = []
        for criterion in criteria:
            bindings.append(
                CriterionBinding(
                    criterion, _freeze_commands(mapping[criterion], policy)
                )
            )
        return tuple(bindings)
    except Exception:
        raise ValueError("invalid_criterion_mapping") from None


def _freeze_commands(
    selected: object, policy: VerificationPolicy
) -> tuple[tuple[str, ...], ...]:
    if type(selected) not in (tuple, list) or not selected:
        raise ValueError
    commands = []
    for command in cast(list[object] | tuple[object, ...], selected):
        if type(command) not in (tuple, list):
            raise ValueError
        exact = tuple(cast(list[str] | tuple[str, ...], command))
        if exact not in policy.commands:
            raise ValueError
        commands.append(exact)
    return tuple(commands)


@dataclass(frozen=True)
class CriterionDecision:
    criterion: str
    commands: tuple[tuple[str, ...], ...]
    attempt_id: str
    passed: bool


@dataclass(frozen=True)
class LoopResult:
    status: str
    reason: str
    usage: RetryUsage
    attempt_ids: tuple[str, ...]
    criteria: tuple[CriterionDecision, ...]
    report: EvidenceReport | None
    escalation: EscalationDecision | None = None


def run_loop(
    adapter: CodexCLIAdapter,
    contract: TaskContract,
    admission: AdmissionRecord,
    context: ContextPacket,
    *,
    policy: VerificationPolicy,
    criterion_commands: object,
    journal: EventJournal,
    state_store: CurrentStateStore,
    budgets: TurnBudgets,
    limits: RetryLimits,
    redactor: Callable[[str], str],
    max_output_bytes: int,
    monotonic: Callable[[], float] = time.monotonic,
    checkpoint_verification: bool = False,
    wall_clock: Callable[[], float] = time.time,
) -> LoopResult:
    """Run a fresh task; optional checkpoints enable explicit continuation."""
    return _run_loop(
        adapter,
        contract,
        admission,
        context,
        policy=policy,
        criterion_commands=criterion_commands,
        journal=journal,
        state_store=state_store,
        budgets=budgets,
        limits=limits,
        redactor=redactor,
        max_output_bytes=max_output_bytes,
        monotonic=monotonic,
        checkpoint_verification=checkpoint_verification,
        wall_clock=wall_clock,
    )


def _run_loop(
    adapter: CodexCLIAdapter,
    contract: TaskContract,
    admission: AdmissionRecord,
    context: ContextPacket,
    *,
    policy: VerificationPolicy,
    criterion_commands: object,
    journal: EventJournal,
    state_store: CurrentStateStore,
    budgets: TurnBudgets,
    limits: RetryLimits,
    redactor: Callable[[str], str],
    max_output_bytes: int,
    monotonic: Callable[[], float] = time.monotonic,
    checkpoint_verification: bool = False,
    wall_clock: Callable[[], float] = time.time,
    _continuation: bool = False,
) -> LoopResult:
    """Execute a fresh serialized task; caller holds its B4 repository lease.

    Unknown observation/persistence stops rather than authorizing more work.
    UNKNOWN usage is a lower bound of launches proven by returned process facts.
    Captures are point-in-time evidence, not hostile-process confinement.
    """
    started = monotonic()
    resumed: dict[str, object] | None = None
    commands = 0
    identities: tuple[str, ...] = ()
    report: EvidenceReport | None = None
    criteria: tuple[CriterionDecision, ...] = ()
    escalation: EscalationDecision | None = None
    active = False
    persistence_uncertain = False
    known_history = b""
    journal_required = False
    current_required = False
    history: list[FailureAssessment] = []
    protected_failures: tuple[ProtectedInputCheck, ...] = ()

    def finish(status: str, reason: str) -> LoopResult:
        usage = RetryUsage(commands, monotonic() - started)
        if active and not persistence_uncertain:
            try:
                guard_persistence()
                summary: dict[str, object] = {
                    "event": "loop_outcome",
                    "status": status,
                    "reason": reason,
                    "commands": usage.commands,
                    "elapsed_seconds": usage.elapsed_seconds,
                    "elapsed_boundary": "before_outcome_persistence",
                    "attempt_ids": list(identities),
                }
                attempts.refresh()
                _append(journal, root, summary)
                current = state_store.read() or {}
                current["loop_summary"] = summary
                state_store.write(current)
            except Exception:
                status, reason = "unknown", "outcome_persistence_failed"
        return LoopResult(
            status,
            reason,
            RetryUsage(commands, monotonic() - started),
            identities,
            criteria,
            report,
            escalation,
        )

    def run_commands() -> tuple[CommandResult, ...]:
        nonlocal commands
        results: list[CommandResult] = []
        for command in policy.commands:
            guard_persistence()
            guard_protected()
            if commands + 1 > limits.max_commands:
                raise _LoopStop("stopped", "command_budget")
            if monotonic() - started + policy.timeout_seconds + 2 > limits.max_seconds:
                raise _LoopStop("stopped", "time_budget")
            single = VerificationPolicy(
                (command,), policy.working_directory, policy.timeout_seconds
            )
            command_before = RepositoryObservation.capture(root)
            result = ControlledCommandRunner().run(single, root)[0]
            if result.exit_code is None and not result.timed_out:
                raise _LoopStop("unknown", "command_launch_unknown")
            commands += 1
            results.append(result)
            guard_persistence()
            guard_protected()
            if command_before != RepositoryObservation.capture(root):
                raise _LoopStop("blocked", "verification_workspace_changed")
        return tuple(results)

    try:
        contract = TaskContract(
            contract.goal,
            contract.allowed_paths,
            contract.protected_paths,
            contract.completion_criteria,
        )
        policy = VerificationPolicy(
            policy.commands, policy.working_directory, policy.timeout_seconds
        )
        context = ContextPacket(
            context.repository_root, context.budget_bytes, context.sources
        )
        budgets = TurnBudgets(
            budgets.timeout_seconds,
            budgets.input_bytes,
            budgets.stdout_bytes,
            budgets.stderr_bytes,
            budgets.final_bytes,
        )
        limits = RetryLimits(
            limits.max_retries,
            limits.max_same_failure,
            limits.max_commands,
            limits.max_seconds,
        )
        if checkpoint_verification:
            carried_elapsed(0, wall_clock(), wall_clock())
        bindings = bind_completion_criteria(contract, policy, criterion_commands)
        root = _validate_turn(
            adapter, contract, admission, context, journal, budgets, "workspace-write"
        )
        _validate_storage(journal, root)
        if (
            not callable(redactor)
            or type(max_output_bytes) is not int
            or max_output_bytes <= 0
        ):
            return finish("blocked", "invalid_input")
        if not isinstance(limits, RetryLimits):
            return finish("blocked", "invalid_input")
        limits.__post_init__()
        policy.__post_init__()
        directory = (root / policy.working_directory).resolve(strict=True)
        if not directory.is_dir() or not directory.is_relative_to(root):
            return finish("blocked", "invalid_input")
        attempts = AttemptJournal(root, journal=journal, state_store=state_store)
        if not _continuation and (
            attempts.read()
            or any(
                event.get("event") in {"loop_start", "loop_outcome"}
                for event in journal.read()
            )
        ):
            return finish("blocked", "existing_history")
        binding = hashlib.sha256(
            json.dumps(
                [
                    asdict(contract),
                    asdict(admission),
                    asdict(context),
                    asdict(policy),
                    [asdict(item) for item in bindings],
                    asdict(budgets),
                    asdict(limits),
                    str(journal.path),
                    str(state_store.path),
                    max_output_bytes,
                ],
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
        if _continuation:
            events = journal.read()
            validate_boundaries(events)
            native_intents = [e for e in events if e.get("event") == "turn_intent"]
            if len(native_intents) != 1:
                raise ValueError
            native_intent = native_intents[0]
            if (
                native_intent.get("contract_sha256")
                != hashlib.sha256(contract.to_json().encode()).hexdigest()
                or native_intent.get("context_sha256")
                != hashlib.sha256(context.to_json().encode()).hexdigest()
                or native_intent.get("budgets") != asdict(budgets)
            ):
                raise ValueError
            entries = attempts.read()
            current = state_store.read() or {}
            if len(entries) != 1 or not events or "loop_summary" in current:
                raise ValueError
            resumed = events[-1]
            if (
                resumed.get("event") != "verification_checkpoint"
                or resumed.get("binding_sha256") != binding
                or resumed.get("pending_phase") not in ("development", "final")
                or any(
                    e.get("event")
                    in ("attempt_verification", "loop_outcome", "loop_attempt_terminal")
                    for e in events
                )
                or current.get("attempt_summary")
                != {
                    "schema_version": 1,
                    "attempt_count": 1,
                    "journal": {
                        "path": str(journal.path),
                        "sha256": hashlib.sha256(journal.path.read_bytes()).hexdigest(),
                    },
                    "active_attempt": entries[0],
                }
            ):
                raise ValueError
            if entries[0]["attempt_id"] != resumed["attempt_id"]:
                raise ValueError
            original = _observation(resumed["observation"])
            if RepositoryObservation.capture(root) != original:
                raise ValueError
            commands = cast(int, resumed["commands"])
            expected = 1 + len(policy.commands) * (
                2 if resumed["pending_phase"] == "final" else 1
            )
            if type(commands) is not int or commands != expected:
                raise ValueError
            started -= carried_elapsed(
                resumed["elapsed_seconds"], resumed["wall_seconds"], wall_clock()
            )
            identities = (cast(str, resumed["attempt_id"]),)
        state_store.read()
        current_required = state_store.path.exists()
        journal_required = journal.path.exists()
        known_history = journal.path.read_bytes() if journal_required else b""
        before = RepositoryObservation.capture(root)
        if before.head is None:
            return finish("blocked", "missing_revision")
        if resumed is None:
            files = tuple(
                path for path in contract.protected_paths if not (root / path).is_dir()
            )
            directories = tuple(
                path for path in contract.protected_paths if (root / path).is_dir()
            )
            file_inputs = capture_protected_inputs(root, files)
            directory_inputs = capture_protected_directories(root, directories)
        if resumed is not None:
            file_inputs = tuple(
                ProtectedInputFingerprint(**row)
                for row in cast(list[dict[str, str]], resumed["protected_files"])
            )
            directory_inputs = tuple(
                ProtectedDirectoryFingerprint(
                    cast(str, row["path"]),
                    tuple(
                        ProtectedInputFingerprint(**item)
                        for item in cast(list[dict[str, str]], row["files"])
                    ),
                )
                for row in cast(
                    list[dict[str, object]], resumed["protected_directories"]
                )
            )
            if (
                set(item.path for item in file_inputs)
                | set(item.path for item in directory_inputs)
            ) != set(contract.protected_paths):
                raise ValueError
        required_commands = (
            (1 + 3 * len(policy.commands))
            if resumed is None
            else commands
            + len(policy.commands)
            * (2 if resumed["pending_phase"] == "development" else 1)
        )
        required_seconds = (
            (budgets.timeout_seconds + 2 + 3 * _phase_seconds(policy))
            if resumed is None
            else _phase_seconds(policy)
            * (2 if resumed["pending_phase"] == "development" else 1)
        )
        if required_commands > limits.max_commands:
            return finish("stopped", "command_budget")
        if required_seconds > limits.max_seconds - (monotonic() - started):
            return finish("stopped", "time_budget")
    except Exception:
        return finish("blocked", "invalid_preflight")

    def guard_persistence() -> None:
        nonlocal \
            persistence_uncertain, \
            known_history, \
            journal_required, \
            current_required
        try:
            _validate_storage(journal, root)
            attempts.read()
            if checkpoint_verification:
                events = journal.read()
                validate_boundaries(events)
            current = state_store.read()
            if (
                checkpoint_verification
                and current is not None
                and "loop_summary" in current
                and not any(event.get("event") == "loop_outcome" for event in events)
            ):
                raise ValueError("conflicting_loop_summary")
            if current_required and current is None:
                raise ValueError("lost_current")
            if journal_required and not journal.path.exists():
                raise ValueError("lost_history")
            raw = journal.path.read_bytes() if journal.path.exists() else b""
            if not raw.startswith(known_history):
                raise ValueError("changed_history")
            known_history = raw
            journal_required = journal_required or journal.path.exists()
            current_required = current_required or current is not None
        except Exception:
            persistence_uncertain = True
            raise _LoopStop("unknown", "runtime_persistence_invalid") from None

    def checks() -> tuple[ProtectedInputCheck, ...]:
        return verify_protected_inputs(
            root, file_inputs
        ) + verify_protected_directories(root, directory_inputs)

    def guard_protected() -> None:
        nonlocal protected_failures
        bad = tuple(check for check in checks() if check.state.value != "unchanged")
        if bad:
            protected_failures = bad
            _append(
                journal,
                root,
                {
                    "event": "loop_protected_inputs",
                    "attempt_id": identities[-1] if identities else None,
                    "checks": [
                        {
                            "path_sha256": hashlib.sha256(
                                check.path.encode()
                            ).hexdigest(),
                            "state": check.state.value,
                        }
                        for check in bad
                    ],
                },
            )
            raise _LoopStop("blocked", "protected_input_changed")

    def assess_failure() -> EscalationDecision:
        assessment = classify_attempt_failure(attempts.read()[-1])
        additional = _protected_findings(protected_failures)
        history.append(
            FailureAssessment(
                assessment.attempt_id,
                assessment.revision,
                assessment.findings + additional,
            )
        )
        return evaluate_escalation(
            history,
            limits=limits,
            usage=RetryUsage(commands, monotonic() - started),
            next_commands=1 + 2 * len(policy.commands),
            next_seconds=budgets.timeout_seconds + 2 + 2 * _phase_seconds(policy),
        )

    def advance(
        state: ExecutionState,
        event: ExecutionEvent,
        facts: ExecutionEvidence | None = None,
    ) -> ExecutionState:
        step = transition_execution(state, event, facts)
        if not step.accepted:
            raise ValueError("invalid_transition")
        return cast(ExecutionState, step.next_state)

    def checkpoint(pending_phase: str) -> None:
        if len(identities) != 1:
            raise _LoopStop("blocked", "unsupported_checkpoint_history")
        guard_persistence()
        guard_protected()
        _append(
            journal,
            root,
            {
                "event": "verification_checkpoint",
                "version": 1,
                "attempt_id": identities[0],
                "pending_phase": pending_phase,
                "binding_sha256": binding,
                "baseline": safe_results(baseline.results),
                "completed_results": safe_results(report.profile.results)
                if report is not None and pending_phase == "final"
                else [],
                "protected_files": [asdict(item) for item in file_inputs],
                "protected_directories": [asdict(item) for item in directory_inputs],
                "observation": asdict(RepositoryObservation.capture(root)),
                "commands": commands,
                "elapsed_seconds": monotonic() - started,
                "wall_seconds": wall_clock(),
            },
        )
        attempts.refresh()

    try:
        active = True
        if resumed is None:
            _append(
                journal,
                root,
                {"event": "loop_start", "repository_id": before.repository_id},
            )
            baseline = VerificationBaseline.capture(run_commands())
            if before != RepositoryObservation.capture(root) or any(
                check.state.value != "unchanged" for check in checks()
            ):
                return finish("blocked", "verification_workspace_changed")
        else:
            baseline = VerificationBaseline.capture(
                restore_results(resumed["baseline"], policy.commands)
            )
            guard_protected()
        while True:
            guard_persistence()
            if resumed is None:
                if commands + 1 + 2 * len(policy.commands) > limits.max_commands:
                    return finish("stopped", "command_budget")
                if (
                    monotonic()
                    - started
                    + budgets.timeout_seconds
                    + 2
                    + 2 * _phase_seconds(policy)
                    > limits.max_seconds
                ):
                    return finish("stopped", "time_budget")
                state = advance(
                    ExecutionState.NEW,
                    ExecutionEvent.ADMIT,
                    ExecutionEvidence(admitted=True),
                )
                state = advance(state, ExecutionEvent.START)
                stored = run_stored_scoped_turn(
                    adapter,
                    contract,
                    admission,
                    context,
                    journal=journal,
                    budgets=budgets,
                    redactor=redactor,
                    max_output_bytes=max_output_bytes,
                )
                observed = stored.scoped_turn.observed_turn
                identities += (observed.attempt_id,)
                if observed.adapter_result.process_status == "launch_error":
                    return finish("unknown", "native_launch_accounting")
                commands += 1
                process = observed.adapter_result
                state = advance(
                    state,
                    ExecutionEvent.PROCESS_RESULT,
                    ExecutionEvidence(
                        process_status=process.process_status,
                        exit_code=process.exit_code,
                    ),
                )
                guard_persistence()
                guard_protected()
                attempts.refresh()
                if (
                    state is ExecutionState.FAILED
                    or process.final_output_status != "available"
                ):
                    if state is not ExecutionState.FAILED:
                        state = advance(state, ExecutionEvent.BLOCK)
                    continue_attempt = True
                else:
                    continue_attempt = False
                active_id = observed.attempt_id
                verification_revision = observed.after.head
            else:
                active_id = identities[0]
                verification_revision = before.head
                state = ExecutionState.VERIFYING
                continue_attempt = False
            if not continue_attempt:
                if verification_revision is None:
                    return finish("blocked", "missing_revision")
                if resumed is None:
                    state = advance(
                        state,
                        ExecutionEvent.SCOPE_RESULT,
                        ExecutionEvidence(
                            scope_clear=stored.scoped_turn.decision.proceed
                        ),
                    )
                    if state is ExecutionState.BLOCKED:
                        escalation = assess_failure()
                        return finish("human", escalation.reason)
                    state = advance(
                        state,
                        ExecutionEvent.OUTPUT_RESULT,
                        ExecutionEvidence(output_stored=True),
                    )
                    state = advance(state, ExecutionEvent.BEGIN_VERIFICATION)
                    if checkpoint_verification:
                        checkpoint("development")
                pending = (
                    ("final",)
                    if resumed is not None and resumed["pending_phase"] == "final"
                    else ("development", "final")
                )
                for phase in pending:
                    if (
                        monotonic() - started + _phase_seconds(policy)
                        > limits.max_seconds
                    ):
                        return finish("stopped", "time_budget")
                    if checkpoint_verification:
                        _append(
                            journal,
                            root,
                            {
                                "event": "verification_phase_intent",
                                "attempt_id": active_id,
                                "phase": phase,
                            },
                        )
                        attempts.refresh()
                    verification_before = RepositoryObservation.capture(root)
                    results = run_commands()
                    profile = DevelopmentVerificationResult(
                        baseline, results, baseline.compare(results)
                    )
                    report = build_evidence_report(
                        root,
                        verification_revision,
                        profile,
                        decide_technical_outcome(profile),
                        checks(),
                    )
                    if verification_before != RepositoryObservation.capture(root):
                        return finish("blocked", "verification_workspace_changed")
                    if (
                        report.overall_kind is not TechnicalDecisionKind.PASS
                        or phase == "final"
                    ):
                        attempts.record_verification(active_id, report, refresh=True)
                        state = advance(
                            state,
                            ExecutionEvent.VERIFICATION_RESULT,
                            ExecutionEvidence(technical_decision=report.overall_kind),
                        )
                        break
                    if checkpoint_verification:
                        checkpoint("final")
            if state is ExecutionState.PASSED:
                break
            _append(
                journal,
                root,
                {
                    "event": "loop_attempt_terminal",
                    "attempt_id": active_id,
                    "state": state.value,
                },
            )
            escalation = assess_failure()
            if checkpoint_verification or escalation.action != "retry":
                return finish("human", escalation.reason)
        assert report is not None
        passed = state is ExecutionState.PASSED
        criteria = tuple(
            CriterionDecision(
                binding.criterion,
                binding.commands,
                active_id,
                passed
                and all(
                    any(
                        result.command == command
                        and result.exit_code == 0
                        and not result.timed_out
                        for result in report.profile.results
                    )
                    for command in binding.commands
                ),
            )
            for binding in bindings
        )
        status = (
            "pass"
            if passed and all(item.passed for item in criteria)
            else report.overall_kind.value
        )
        return finish(
            status, "final_verification" if status == "pass" else "verification_failure"
        )
    except _LoopStop as error:
        try:
            if error.reason == "protected_input_changed" and identities:
                if state not in (
                    ExecutionState.FAILED,
                    ExecutionState.BLOCKED,
                    ExecutionState.STOPPED,
                ):
                    state = advance(state, ExecutionEvent.BLOCK)
                escalation = assess_failure()
                return finish("human", escalation.reason)
        except Exception:
            return finish("unknown", "protected_assessment_persistence_failed")
        return finish(error.status, error.reason)
    except NativeOutputError as error:
        partial = error.scoped_turn.observed_turn
        identities += (partial.attempt_id,)
        commands += _native_launches(partial.adapter_result)
        return finish("unknown", error.code)
    except ScopeTurnError as error:
        identities += (error.observed_turn.attempt_id,)
        commands += _native_launches(error.observed_turn.adapter_result)
        return finish("unknown", error.code)
    except TurnObservationError as error:
        identities += (error.attempt_id,)
        if error.adapter_result is not None:
            commands += _native_launches(error.adapter_result)
        return finish(
            "unknown",
            error.code if error.adapter_result is not None else "native_launch_unknown",
        )
    except Exception:
        return finish("unknown", "execution_or_persistence_failed")


def continue_verification(
    adapter: CodexCLIAdapter,
    contract: TaskContract,
    admission: AdmissionRecord,
    context: ContextPacket,
    *,
    policy: VerificationPolicy,
    criterion_commands: object,
    journal: EventJournal,
    state_store: CurrentStateStore,
    budgets: TurnBudgets,
    limits: RetryLimits,
    redactor: Callable[[str], str],
    max_output_bytes: int,
    monotonic: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
) -> LoopResult:
    """Explicit whole-phase continuation; caller holds its B4 repository lease.

    The adapter is validated for input compatibility but never invoked here.
    Historical baseline stdout/stderr are unavailable after restoration.
    """
    return _run_loop(
        adapter,
        contract,
        admission,
        context,
        policy=policy,
        criterion_commands=criterion_commands,
        journal=journal,
        state_store=state_store,
        budgets=budgets,
        limits=limits,
        redactor=redactor,
        max_output_bytes=max_output_bytes,
        monotonic=monotonic,
        checkpoint_verification=True,
        wall_clock=wall_clock,
        _continuation=True,
    )


def _phase_seconds(policy: VerificationPolicy) -> int:
    """Reserve each approved command timeout plus existing bounded cleanup."""
    return len(policy.commands) * (policy.timeout_seconds + 2)


def _native_launches(result: CodexTurnResult) -> int:
    """A returned launch_error proves no Popen launch; other statuses prove one."""
    return int(result.process_status != "launch_error")


class _LoopStop(Exception):
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _protected_findings(
    checks: tuple[ProtectedInputCheck, ...],
) -> tuple[FailureFinding, ...]:
    return tuple(
        FailureFinding("unknown", "protected_input_unverifiable")
        if check.state.value == "unverifiable"
        else FailureFinding("protected_input", "protected_input_violation")
        for check in checks
    )
