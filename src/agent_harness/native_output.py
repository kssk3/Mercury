"""Optional persistence of one complete scoped turn's caller-redacted text."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from agent_harness.adapter import CapturedText, CodexCLIAdapter, TurnBudgets
from agent_harness.admission import AdmissionRecord
from agent_harness.context import ContextPacket
from agent_harness.contract import TaskContract
from agent_harness.journal import EventJournal
from agent_harness.output import OutputStore, StoredOutput
from agent_harness.repository import resolve_repository_root
from agent_harness.scope import ScopedTurnResult, evaluate_scope, run_scoped_turn
from agent_harness.turn import (
    RepositoryObservation,
    _append,
    _process_metadata,
    _validate_journal,
    compare_observations,
)


@dataclass(frozen=True)
class StoredScopedTurnResult:
    """Artifacts and the unchanged in-memory F3 result; no task-success inference."""

    scoped_turn: ScopedTurnResult
    stdout: StoredOutput
    stderr: StoredOutput
    final_message: StoredOutput | None


FailureCode = Literal[
    "redaction_failed",
    "encoding_failed",
    "storage_failed",
    "output_record_failed",
    "callback_observation_failed",
]


class NativeOutputError(RuntimeError):
    """Fixed persistence failure; raw scoped result is retained in memory only."""

    def __init__(self, code: FailureCode, scoped_turn: ScopedTurnResult) -> None:
        super().__init__(code)
        self.code = code
        self.scoped_turn = scoped_turn
        self.proceed = False


def _write(
    capture: CapturedText,
    scoped: ScopedTurnResult,
    store: OutputStore,
    redactor: Callable[[str], str],
    journal: EventJournal,
) -> StoredOutput:
    try:
        text = redactor(capture.text)
        if not isinstance(text, str):
            raise TypeError("redactor must return text")
    except Exception:
        raise NativeOutputError("redaction_failed", scoped) from None
    try:
        text.encode("utf-8")
    except UnicodeError:
        raise NativeOutputError("encoding_failed", scoped) from None
    try:
        _validate_storage(journal, Path(scoped.observed_turn.after.repository_root))
        return store.write(text)
    except Exception:
        raise NativeOutputError("storage_failed", scoped) from None


def _record_callback_failure(scoped: ScopedTurnResult, journal: EventJournal) -> None:
    observed = scoped.observed_turn
    try:
        _append(
            journal,
            Path(observed.after.repository_root),
            {
                "event": "callback_observation_failed",
                "attempt_id": observed.attempt_id,
                "repository_id": observed.after.repository_id,
                "classification": "after_observation_failed",
                "process": _process_metadata(observed.adapter_result),
            },
        )
    except BaseException:
        # Best effort only; journal failure cannot replace caller cancellation.
        pass


def _refresh(
    scoped: ScopedTurnResult, contract: TaskContract, journal: EventJournal
) -> ScopedTurnResult:
    try:
        observed = scoped.observed_turn
        after = RepositoryObservation.capture(observed.after.repository_root)
        if after == observed.after:
            return scoped
        updated = replace(
            observed, after=after, delta=compare_observations(observed.before, after)
        )
        decision = evaluate_scope(contract, updated.before, updated.after)
        scoped = ScopedTurnResult(updated, decision)
        root = Path(after.repository_root)
        try:
            _append(
                journal,
                root,
                {
                    "event": "callback_observation",
                    "attempt_id": updated.attempt_id,
                    "repository_id": after.repository_id,
                    "after": asdict(after),
                    "delta": asdict(updated.delta),
                    "process": _process_metadata(updated.adapter_result),
                },
            )
            _append(
                journal,
                root,
                {
                    "event": "callback_scope_decision",
                    "attempt_id": updated.attempt_id,
                    "repository_id": after.repository_id,
                    **asdict(decision),
                    "proceed": decision.proceed,
                },
            )
        except Exception:
            raise NativeOutputError("output_record_failed", scoped) from None
        return scoped

    except NativeOutputError:
        _record_callback_failure(scoped, journal)
        raise
    except Exception:
        _record_callback_failure(scoped, journal)
        raise NativeOutputError("callback_observation_failed", scoped) from None


def _refresh_preserving_failure(
    scoped: ScopedTurnResult, contract: TaskContract, journal: EventJournal
) -> ScopedTurnResult:
    try:
        return _refresh(scoped, contract, journal)
    except NativeOutputError:
        return scoped
    except BaseException:
        _record_callback_failure(scoped, journal)
        return scoped


def _metadata(output: StoredOutput, capture: CapturedText) -> dict[str, object]:
    return {
        "identifier": output.identifier,
        "byte_count": output.byte_count,
        "truncated": output.truncated,
        "captured_bytes": capture.retained_bytes,
        "capture_truncated": capture.truncated,
    }


def _validate_storage(journal: EventJournal, root: Path) -> None:
    _validate_journal(journal, root)
    outputs = journal.path.parent / "outputs"
    if outputs.is_symlink() or str(outputs) != str(outputs.resolve()):
        raise ValueError("outputs must be canonical and not a symlink")
    if outputs.is_relative_to(root) or outputs.resolve().is_relative_to(root.resolve()):
        raise ValueError("outputs must be outside the repository")
    if outputs.exists() and not outputs.is_dir():
        raise ValueError("outputs must be a directory")


def run_stored_scoped_turn(
    adapter: CodexCLIAdapter,
    contract: TaskContract,
    admission: AdmissionRecord,
    context: ContextPacket,
    *,
    journal: EventJournal,
    budgets: TurnBudgets,
    redactor: Callable[[str], str],
    max_output_bytes: int,
    sandbox: Literal["read-only", "workspace-write"] = "workspace-write",
) -> StoredScopedTurnResult:
    """Run F3 once, then persist all available caller-redacted returned channels.

    Caller owns serialization/B4 lock and redaction policy. Strict UTF-8 checks
    precede B5 byte bounding; F1's temporary raw final file remains unchanged.
    Complete process failures and scope violations are still stored. Successful
    persistence grants no scope clearance, task PASS or next-turn authority.
    Safe partial artifacts may remain after failure; there is no batch rollback.
    """
    if not callable(redactor):
        raise TypeError("redactor must be callable")
    if type(max_output_bytes) is not int or max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive integer bytes")
    if not isinstance(journal, EventJournal):
        raise TypeError("journal must be an EventJournal")
    root = resolve_repository_root(context.repository_root)
    _validate_storage(journal, root)
    scoped = run_scoped_turn(
        adapter,
        contract,
        admission,
        context,
        journal=journal,
        budgets=budgets,
        sandbox=sandbox,
    )
    result = scoped.observed_turn.adapter_result
    # Redaction is classified before B5; its identity callback sees only safe text.
    store = OutputStore(
        journal.path.parent, max_bytes=max_output_bytes, redactor=lambda text: text
    )
    try:
        stdout = _write(result.stdout, scoped, store, redactor, journal)
        stderr = _write(result.stderr, scoped, store, redactor, journal)
        final = (
            None
            if result.final_message is None
            else _write(result.final_message, scoped, store, redactor, journal)
        )
    except NativeOutputError as error:
        scoped = _refresh_preserving_failure(scoped, contract, journal)
        raise NativeOutputError(error.code, scoped) from None
    except BaseException:
        _refresh_preserving_failure(scoped, contract, journal)
        raise
    scoped = _refresh(scoped, contract, journal)
    event: dict[str, object] = {
        "event": "native_output_stored",
        "attempt_id": scoped.observed_turn.attempt_id,
        "repository_id": scoped.observed_turn.after.repository_id,
        "stdout": _metadata(stdout, result.stdout),
        "stderr": _metadata(stderr, result.stderr),
        "final_message": None
        if final is None or result.final_message is None
        else _metadata(final, result.final_message),
    }
    try:
        _validate_storage(journal, root)
        _append(journal, root, event)
    except Exception:
        raise NativeOutputError("output_record_failed", scoped) from None
    return StoredScopedTurnResult(scoped, stdout, stderr, final)
