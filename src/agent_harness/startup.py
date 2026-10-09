"""Read-only startup evidence; caller owns B4 serialization and all authority."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from agent_harness.attempt import AttemptJournal, _mapping, _observation, _require
from agent_harness.failure import FailureAssessment, classify_attempt_failure
from agent_harness.journal import EventJournal
from agent_harness.retry import RetryUsage
from agent_harness.scope import _digest
from agent_harness.state import CurrentStateStore
from agent_harness.turn import (
    ObservationDelta,
    RepositoryObservation,
    compare_observations,
)
from agent_harness.verification_checkpoint import validate_boundaries


@dataclass(frozen=True)
class StartupDecision:
    status: str
    diagnostics: tuple[str, ...] = ()
    attempt_ids: tuple[str, ...] = ()
    usage: tuple[int, float] | None = None
    workspace_delta: ObservationDelta | None = None
    loop_status: str | None = None
    accounting_complete: bool = False
    failures: tuple[FailureAssessment, ...] = ()


_RUNTIME_KINDS = {
    "verification_checkpoint",
    "verification_phase_intent",
    "turn_intent",
    "turn_observation",
    "turn_observation_failed",
    "scope_decision",
    "native_output_stored",
    "attempt_verification",
    "loop_start",
    "loop_outcome",
    "loop_attempt_terminal",
}


def _validate_attempt_terminals(
    attempts: AttemptJournal, events: list[dict[str, object]]
) -> None:
    validate_boundaries(events)
    seen: set[str] = set()
    active_id: object = None
    in_loop = False
    outcome_seen = False
    for index, event in enumerate(events):
        kind = event.get("event")
        if kind in _RUNTIME_KINDS:
            _require(not outcome_seen)
        if kind == "loop_outcome":
            outcome_seen = True
        if kind == "loop_start":
            in_loop = True
        if kind == "turn_intent":
            if in_loop and active_id is not None:
                _require(active_id in seen)
            active_id = event.get("attempt_id")
        elif kind in _RUNTIME_KINDS - {"loop_start", "loop_outcome"}:
            _require(event.get("attempt_id") == active_id)
            if kind != "loop_attempt_terminal":
                _require(active_id not in seen)
        if kind != "loop_attempt_terminal":
            continue
        _require(any(row.get("event") == "loop_start" for row in events[:index]))
        _require(not any(row.get("event") == "loop_outcome" for row in events[:index]))
        prefix = attempts._project(events[:index])
        _require(bool(prefix))
        active = prefix[-1]
        identity = cast(str, active["attempt_id"])
        _require(event.get("attempt_id") == identity and identity not in seen)
        observed = _mapping(active["observation"])
        _require(observed.get("status") == "observed" and active["outputs"] is not None)
        process = _mapping(observed["process"])
        verification = active["verification"]
        state: object = None
        if process["process_status"] != "successful_exit":
            state = "failed"
        elif process["final_output_status"] != "available":
            state = "blocked"
        elif verification is not None:
            outcome = _mapping(verification)["overall_kind"]
            state = {"fail": "failed", "blocked": "blocked"}.get(cast(str, outcome))
        _require(state is not None and event.get("state") == state)
        seen.add(identity)


def _proven_launches(entries: tuple[dict[str, object], ...]) -> int:
    launches = 0
    baseline: object = None
    for entry in entries:
        observed = entry["observation"]
        if observed is not None:
            process = _mapping(observed).get("process")
            if (
                process is not None
                and _mapping(process)["process_status"] != "launch_error"
            ):
                launches += 1
        verification = entry["verification"]
        if verification is None:
            continue
        verified = _mapping(verification)
        commands = cast(list[dict[str, object]], verified["commands"])
        launches += sum(
            command["exit_code"] is not None or command["timed_out"] is True
            for command in commands
        )
        current_baseline = verified["baseline_commands"]
        if baseline is None:
            baseline = current_baseline
            launches += sum(
                command["exit_code"] is not None or command["timed_out"] is True
                for command in cast(list[dict[str, object]], baseline)
            )
        else:
            _require(baseline == current_baseline)
    return launches


def _safe_storage(attempts: AttemptJournal) -> None:
    _require(
        isinstance(attempts.journal, EventJournal)
        and isinstance(attempts.state_store, CurrentStateStore)
    )
    _require(attempts.journal.path.parent == attempts.state_store.path.parent)
    for path in (attempts.journal.path, attempts.state_store.path):
        _require(
            path.is_absolute()
            and path == path.resolve()
            and not path.is_relative_to(attempts.root)
        )
        for ancestor in path.parents:
            _require(
                not ancestor.is_symlink()
                and (not ancestor.exists() or ancestor.is_dir())
            )
        _require(not path.is_symlink())
        if path.exists():
            _require(stat.S_ISREG(path.lstat().st_mode))


def _bytes(path: Path) -> bytes | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode))
        return stream.read()


def _validated_prefix(
    attempts: AttemptJournal, raw: bytes, expected: str
) -> tuple[dict[str, object], ...]:
    events: list[dict[str, object]] = []
    validated: tuple[dict[str, object], ...] = ()
    for line in raw.splitlines(keepends=True):
        try:
            _require(line.endswith(b"\n"))
            event = _mapping(json.loads(line))
            candidate = events + [event]
            projected = attempts._project(candidate)
            _require(all(entry["contract_sha256"] == expected for entry in projected))
            _validate_attempt_terminals(attempts, candidate)
        except (ValueError, TypeError, KeyError, OverflowError, UnicodeError):
            break
        events = candidate
        validated = projected
    return validated


def _summary_state(
    current: dict[str, object],
    raw: bytes,
    events: list[dict[str, object]],
    attempts: AttemptJournal,
    terminal: dict[str, object] | None,
) -> str:
    if "attempt_summary" not in current:
        return "summary_missing"
    summary = _mapping(current["attempt_summary"])
    _require(
        type(summary.get("schema_version")) is int and summary["schema_version"] == 1
    )
    metadata = _mapping(summary.get("journal"))
    _require(
        metadata.get("path") == str(attempts.journal.path)
        and _digest(metadata.get("sha256"))
    )
    lines = raw.splitlines(keepends=True)
    # H6 refresh is immediately before the terminal outcome append.
    valid_latest = events.index(terminal) if terminal is not None else len(events)
    for count in range(len(events) + 1):
        prefix = b"".join(lines[:count])
        if metadata["sha256"] != hashlib.sha256(prefix).hexdigest():
            continue
        projected = attempts._project(events[:count])
        _require(
            type(summary.get("attempt_count")) is int
            and summary["attempt_count"] == len(projected)
        )
        _require(
            summary.get("active_attempt") == (projected[-1] if projected else None)
        )
        return "summary_current" if count == valid_latest else "summary_stale"
    raise ValueError("contradictory_summary")


def _decide(
    attempts: AttemptJournal,
    entries: tuple[dict[str, object], ...],
    events: list[dict[str, object]],
    current: dict[str, object],
    raw: bytes,
    live: RepositoryObservation,
) -> StartupDecision:
    _validate_attempt_terminals(attempts, events)
    identities = tuple(str(entry["attempt_id"]) for entry in entries)
    failures = tuple(classify_attempt_failure(entry) for entry in entries)
    base = StartupDecision("interrupted", attempt_ids=identities, failures=failures)
    observations = [
        event
        for event in events
        if event.get("event") in {"turn_intent", "turn_observation"}
    ]
    if observations:
        latest = observations[-1]
        observation = _observation(
            latest["after"]
            if latest["event"] == "turn_observation"
            else latest["before"]
        )
        base = replace(base, workspace_delta=compare_observations(observation, live))
    loops = [
        (index, event)
        for index, event in enumerate(events)
        if event.get("event") in {"loop_start", "loop_outcome"}
    ]
    terminal: dict[str, object] | None = None
    if loops:
        _require(
            loops[0][1].get("event") == "loop_start"
            and loops[0][1].get("repository_id") == live.repository_id
        )
        _require(len(loops) <= 2)
        intent_positions = [
            index
            for index, event in enumerate(events)
            if event.get("event") == "turn_intent"
        ]
        _require(all(index > loops[0][0] for index in intent_positions))
        if len(loops) == 2:
            index, terminal = loops[1]
            _require(
                terminal.get("event") == "loop_outcome"
                and not any(
                    event.get("event") in _RUNTIME_KINDS
                    for event in events[index + 1 :]
                )
            )
            _require(terminal.get("attempt_ids") == list(identities))
            _require(
                terminal.get("status")
                in {"pass", "fail", "blocked", "stopped", "human", "unknown"}
            )
            _require(
                isinstance(terminal.get("reason"), str) and bool(terminal["reason"])
            )
            _require(terminal.get("elapsed_boundary") == "before_outcome_persistence")
            usage = RetryUsage(
                cast(int, terminal.get("commands")),
                cast(float, terminal.get("elapsed_seconds")),
            )
            checkpoints = [
                event
                for event in events
                if event.get("event") == "verification_checkpoint"
            ]
            if checkpoints:
                _require(
                    all(
                        usage.elapsed_seconds >= cast(float, event["elapsed_seconds"])
                        for event in checkpoints
                    )
                )
            completed_development = sum(
                len(cast(list[object], event["completed_results"]))
                for event in checkpoints
            )
            checkpoint_floor = max(
                (cast(int, event["commands"]) for event in checkpoints), default=0
            )
            _require(
                usage.commands
                >= max(
                    checkpoint_floor, _proven_launches(entries) + completed_development
                )
            )
            base = replace(
                base,
                loop_status=cast(str, terminal["status"]),
                usage=(usage.commands, usage.elapsed_seconds),
                accounting_complete=terminal["status"] != "unknown",
            )
    if not entries and not loops:
        _require("loop_summary" not in current)
        if "attempt_summary" in current:
            _require(
                _summary_state(current, raw, events, attempts, None)
                == "summary_current"
            )
        return replace(base, status="fresh")
    if terminal is None:
        _require("loop_summary" not in current)
        summary = (
            _summary_state(current, raw, events, attempts, None)
            if "attempt_summary" in current
            else "summary_missing"
        )
        return replace(
            base,
            diagnostics=(
                "loop_without_outcome" if loops else "attempt_without_loop_outcome",
                summary,
            ),
        )
    try:
        summary = _summary_state(current, raw, events, attempts, terminal)
        if "loop_summary" not in current:
            return replace(base, diagnostics=("loop_summary_missing", summary))
        _require(current["loop_summary"] == terminal)
    except (KeyError, TypeError, ValueError):
        return replace(base, status="unknown", diagnostics=("contradictory_summary",))
    if summary != "summary_current":
        return replace(base, diagnostics=(summary,))
    if terminal["status"] != "pass":
        return replace(base, diagnostics=("recorded_nonpass",))
    _require(bool(entries))
    last = entries[-1]
    scope, verification = _mapping(last["scope"]), _mapping(last["verification"])
    _require(
        scope.get("proceed") is True
        and verification.get("overall_kind") == "pass"
        and verification.get("technical_kind") == "pass"
    )
    _require(bool(verification.get("commands")) and not failures[-1].findings)
    delta = base.workspace_delta
    if delta is None:
        raise ValueError("missing_after_observation")
    changed = any(
        (
            delta.added,
            delta.modified,
            delta.deleted,
            delta.head_changed,
            delta.index_changed,
            delta.status_changed,
        )
    )
    return replace(
        base,
        status="interrupted" if changed else "completed",
        diagnostics=("workspace_changed" if changed else "recorded_completion",),
    )


def reconcile_startup(
    repository: Path | str,
    *,
    journal: EventJournal,
    state_store: CurrentStateStore,
    expected_contract_sha256: str,
) -> StartupDecision:
    """Return historical evidence only, never resume/execute/PASS authority.

    Reads are point-in-time with observable-change checks, not an atomic snapshot
    or hostile-process/ABA isolation. Caller holds its repository B4 lease.
    """
    identities: tuple[str, ...] = ()
    safe_failures: tuple[FailureAssessment, ...] = ()
    try:
        if not _digest(expected_contract_sha256):
            return StartupDecision("unknown", ("invalid_expected_contract",))
        attempts = AttemptJournal(repository, journal=journal, state_store=state_store)
        _safe_storage(attempts)
        raw_journal, raw_current = _bytes(journal.path), _bytes(state_store.path)
        prefix = _validated_prefix(
            attempts, raw_journal or b"", expected_contract_sha256
        )
        identities = tuple(str(entry["attempt_id"]) for entry in prefix)
        safe_failures = tuple(classify_attempt_failure(entry) for entry in prefix)
        attempts._validate_persistence()
        live = RepositoryObservation.capture(attempts.root)
        entries = attempts.read()
        # H2 validity cannot replace the stricter R1 prefix used on rejection.
        events, current = journal.read(), state_store.read() or {}
        if any(
            entry["contract_sha256"] != expected_contract_sha256 for entry in entries
        ):
            result = StartupDecision("unknown", ("contract_mismatch",), identities)
        else:
            result = _decide(
                attempts, entries, events, current, raw_journal or b"", live
            )
        attempts._validate_persistence()
        if (
            raw_journal != _bytes(journal.path)
            or raw_current != _bytes(state_store.path)
            or live != RepositoryObservation.capture(attempts.root)
        ):
            return StartupDecision("unknown", ("inputs_changed",), identities)
        return result
    except Exception:
        return StartupDecision(
            "unknown",
            ("inputs_unreadable_or_invalid",),
            identities,
            failures=safe_failures,
        )
