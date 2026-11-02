"""A valid large candidate remains readable through the historical projection."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

import pytest
import test_attempt

from agent_harness.attempt import AttemptJournal
from agent_harness.inspection import summarize_execution
from agent_harness.journal import EventJournal
from agent_harness.state import CurrentStateStore
from agent_harness.turn import (
    FileFingerprint,
    RepositoryObservation,
    compare_observations,
)

repository = test_attempt.repository
repository_seed = test_attempt.repository_seed


def _produce(
    root: Path, directory: Path, count: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    observation = replace(
        RepositoryObservation.capture(root),
        files=tuple(
            FileFingerprint(
                f"file-{index:05d}.py", "regular", "a" * 64, 1, False, 0o644
            )
            for index in range(count)
        ),
        status=(),
    )
    journal = EventJournal(directory)
    store = CurrentStateStore(directory)
    identity = {
        "attempt_id": "large-candidate",
        "repository_root": str(root),
        "repository_id": observation.repository_id,
        "contract_sha256": "b" * 64,
        "context_sha256": "c" * 64,
        "before_head": observation.head,
    }
    delta = asdict(compare_observations(observation, observation))
    capture = {"retained_bytes": 0, "truncated": False}
    process = {
        "process_status": "successful_exit",
        "exit_code": 0,
        "duration_seconds": 0.1,
        "stdout": capture,
        "stderr": capture,
        "final_message": capture,
        "final_output_status": "available",
    }
    journal.append({"event": "loop_start", "repository_id": observation.repository_id})
    journal.append(
        {
            **identity,
            "event": "turn_intent",
            "before": asdict(observation),
            "sandbox": "workspace-write",
            "budgets": {
                "timeout_seconds": 2,
                "input_bytes": 100,
                "stdout_bytes": 100,
                "stderr_bytes": 100,
                "final_bytes": 100,
            },
        }
    )
    journal.append(
        {
            "event": "turn_observation",
            "attempt_id": identity["attempt_id"],
            "after": asdict(observation),
            "process": process,
            "delta": delta,
        }
    )
    journal.append(
        {
            "event": "scope_decision",
            "attempt_id": identity["attempt_id"],
            "repository_id": observation.repository_id,
            "proceed": True,
            "delta": delta,
            "out_of_scope_paths": [],
            "protected_paths": [],
            "unexplained_status_change": False,
        }
    )
    output = {
        "identifier": "e" * 64,
        "byte_count": 0,
        "truncated": False,
        "captured_bytes": 0,
        "capture_truncated": False,
    }
    journal.append(
        {
            "event": "native_output_stored",
            "attempt_id": identity["attempt_id"],
            "repository_id": observation.repository_id,
            "stdout": output,
            "stderr": output,
            "final_message": output,
        }
    )
    attempts = AttemptJournal(root, journal=journal, state_store=store)
    verification = attempts.record_verification(
        "large-candidate",
        test_attempt.report(root, observation.head),
        refresh=True,
        final_observation=observation,
        verification_sha256="d" * 64,
    )
    outcome = {
        "event": "loop_outcome",
        "status": "pass",
        "reason": "final_verification",
        "commands": 2,
        "elapsed_seconds": 0.5,
        "elapsed_boundary": "before_outcome_persistence",
        "attempt_ids": ["large-candidate"],
    }
    journal.append(outcome)
    current = store.read()
    assert current is not None
    store.write({**current, "loop_summary": outcome})
    summary = summarize_execution(directory)
    facts = {
        "inventory_count": count,
        "current_bytes": store.path.stat().st_size,
        "journal_bytes": journal.path.stat().st_size,
        "verification_schema": verification["schema_version"],
        "summary": summary,
        "expected_binding": cast(dict[str, Any], verification["review_binding"])[
            "binding"
        ],
    }
    (directory / "reproduction-facts.json").write_text(json.dumps(facts, indent=2))
    print(json.dumps(facts, sort_keys=True))
    return summary, facts


def test_large_valid_candidate_keeps_historical_status_and_binding(
    repository: Path,
    tmp_path: Path,
) -> None:
    """Within journal bounds, inventory size must not hide valid saved evidence."""
    small, _ = _produce(repository, tmp_path / "small-state", 2)
    assert small["recorded_status"] == "pass"
    assert small["review_binding_status"] == "recorded"
    large, facts = _produce(repository, tmp_path / "large-state", 8000)
    assert facts["current_bytes"] <= 1024 * 1024
    assert facts["journal_bytes"] < 8 * 1024 * 1024
    assert large["recorded_status"] == "pass"
    assert large["review_binding_status"] == "recorded"
    assert large["review_binding"] == facts["expected_binding"]


def test_legacy_full_projection_retains_bound_identity(
    repository: Path, tmp_path: Path
) -> None:
    directory = tmp_path / "legacy-state"
    original, _ = _produce(repository, directory, 2)
    journal, store = EventJournal(directory), CurrentStateStore(directory)
    entries = AttemptJournal(repository, journal=journal, state_store=store).read()
    current = store.read()
    assert current is not None
    summary = cast(dict[str, Any], current["attempt_summary"])
    summary["schema_version"] = 1
    summary["active_attempt"] = entries[-1]
    store.write(current)
    before = store.path.read_bytes(), journal.path.read_bytes()
    loaded = summarize_execution(directory)
    assert loaded["recorded_status"] == "pass"
    assert loaded["review_binding"] == original["review_binding"]
    assert before == (store.path.read_bytes(), journal.path.read_bytes())


@pytest.mark.parametrize(
    "corruption",
    [
        "current_binding",
        "candidate",
        "journal_binding",
        "journal_digest",
        "wrong_attempt",
        "missing_binding",
        "missing_journal",
        "corrupt_journal",
        "incomplete",
        "compact_as_legacy",
        "full_as_compact",
    ],
)
def test_compact_projection_never_clears_inconsistent_evidence(
    repository: Path,
    tmp_path: Path,
    corruption: str,
) -> None:
    import hashlib

    directory = tmp_path / "corrupt-state"
    _produce(repository, directory, 2)
    journal, store = EventJournal(directory), CurrentStateStore(directory)
    current = store.read()
    assert current is not None
    summary = cast(dict[str, Any], current["attempt_summary"])
    compact = summary["active_attempt"]["verification"]["review_binding"]
    events = journal.read()
    verification = cast(
        dict[str, Any], next(e for e in events if e["event"] == "attempt_verification")
    )
    if corruption == "current_binding":
        compact["candidate_sha256"] = "f" * 64
    elif corruption == "journal_digest":
        summary["journal"]["sha256"] = "f" * 64
    elif corruption == "compact_as_legacy":
        summary["schema_version"] = 1
    elif corruption == "full_as_compact":
        summary["active_attempt"]["verification"]["review_binding"] = verification[
            "review_binding"
        ]
    elif corruption == "missing_journal":
        journal.path.unlink()
    elif corruption == "corrupt_journal":
        journal.path.write_text("{invalid\n")
    else:
        if corruption == "candidate":
            verification["review_binding"]["candidate"]["files"][0]["sha256"] = "f" * 64
        elif corruption == "journal_binding":
            cast(dict[str, Any], verification["review_binding"])["binding"][
                "evidence_sha256"
            ] = "f" * 64
        elif corruption == "wrong_attempt":
            verification["attempt_id"] = "another-attempt"
        elif corruption == "missing_binding":
            del verification["review_binding"]
        elif corruption == "incomplete":
            events.pop()
        raw = b"".join(json.dumps(event).encode() + b"\n" for event in events)
        journal.path.write_bytes(raw)
        summary["journal"]["sha256"] = hashlib.sha256(raw).hexdigest()
    store.write(current)
    before = (
        store.path.read_bytes(),
        journal.path.read_bytes() if journal.path.exists() else None,
    )
    loaded = summarize_execution(directory)
    assert loaded["recorded_status"] == "unknown"
    assert loaded["review_binding"] is None
    assert loaded["diagnostics"]
    assert before == (
        store.path.read_bytes(),
        journal.path.read_bytes() if journal.path.exists() else None,
    )


@pytest.mark.parametrize("status", ["fail", "blocked", "stopped", "unknown"])
def test_compact_projection_withholds_binding_for_nonpass_outcome(
    repository: Path,
    tmp_path: Path,
    status: str,
) -> None:
    directory = tmp_path / "nonpass-state"
    _produce(repository, directory, 2)
    journal, store = EventJournal(directory), CurrentStateStore(directory)
    events = journal.read()
    events[-1]["status"] = status
    journal.path.write_text("".join(json.dumps(event) + "\n" for event in events))
    attempts = AttemptJournal(repository, journal=journal, state_store=store)
    attempts.refresh()
    current = store.read()
    assert current is not None
    current["loop_summary"] = events[-1]
    store.write(current)
    loaded = summarize_execution(directory)
    assert loaded["recorded_status"] == status
    assert loaded["review_binding"] is None
    assert loaded["review_binding_status"] == "unavailable"
