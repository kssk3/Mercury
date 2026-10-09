from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import pytest

from agent_harness.journal import EventJournal
from agent_harness.state import CurrentStateStore
from agent_harness.turn import RepositoryObservation


@pytest.fixture(scope="module")
def repository_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = (tmp_path_factory.mktemp("attempt-repository") / "repository").resolve()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    # A copied seed must not outlive a detached Git housekeeping writer.
    subprocess.run(
        ["git", "-C", str(root), "config", "--local", "maintenance.auto", "false"],
        check=True,
        timeout=10,
    )
    (root / "base.txt").write_text("baseline")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        check=True,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        },
    )
    return root


@pytest.fixture
def repository(tmp_path: Path, repository_seed: Path) -> Path:
    root = (tmp_path / "repository").resolve()
    shutil.copytree(repository_seed, root)
    return root


def test_repository_seed_copy_avoids_automatic_maintenance(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    trace = tmp_path / "git-trace.jsonl"
    original_run = subprocess.run

    def traced_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        # Instrument the real subprocess; fixture commit filters inherited GIT_*.
        if args[0][0] == "git":
            kwargs["env"] = {
                **kwargs.get("env", os.environ),
                "GIT_TRACE2_EVENT": str(trace),
            }
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", traced_run)

    seed = cast(Any, repository_seed).__wrapped__(tmp_path_factory)
    clone = tmp_path / "clone"
    shutil.copytree(seed, clone)
    assert RepositoryObservation.capture(clone).head is not None

    events = [json.loads(line) for line in trace.read_text().splitlines()]
    launches = [
        event["argv"]
        for event in events
        if event.get("event") == "child_start"
        and "--auto" in event.get("argv", [])
        and any(command in event["argv"] for command in ("maintenance", "gc"))
    ]
    assert launches == [], "fixture Git writes must not launch concurrent housekeeping"


def api(repository: Path, journal: EventJournal, store: CurrentStateStore) -> Any:
    assert importlib.util.find_spec("agent_harness.attempt") is not None, (
        "H2 must provide the additive AttemptJournal API"
    )
    from agent_harness.attempt import AttemptJournal

    return AttemptJournal(repository, journal=journal, state_store=store)


def intent(repository: Path, attempt_id: str = "attempt-one") -> dict[str, object]:
    snapshot = RepositoryObservation.capture(repository)
    return {
        "event": "turn_intent",
        "attempt_id": attempt_id,
        "repository_root": str(repository),
        "repository_id": snapshot.repository_id,
        "contract_sha256": "a" * 64,
        "context_sha256": "b" * 64,
        "sandbox": "workspace-write",
        "budgets": {
            "timeout_seconds": 2,
            "input_bytes": 16000,
            "stdout_bytes": 128,
            "stderr_bytes": 128,
            "final_bytes": 128,
        },
        "before": asdict(snapshot),
    }


def test_read_and_refresh_intents_preserve_order_missing_phases_and_user_state(
    repository: Path,
    tmp_path: Path,
) -> None:
    journal = EventJournal(tmp_path / "state")
    store = CurrentStateStore(tmp_path / "state")
    attempts = api(repository, journal, store)
    assert attempts.read() == ()
    assert not journal.path.parent.exists()
    journal.append({"event": "legacy", "text": "unrelated"})
    journal.append(intent(repository, "old"))
    journal.append(intent(repository, "new"))
    before = journal.path.read_bytes()
    store.write({"user": {"nested": [1, 2]}, "status": "held"})
    entries = attempts.read()
    assert [entry["attempt_id"] for entry in entries] == ["old", "new"]
    assert entries[-1]["before_head"] == RepositoryObservation.capture(repository).head
    for phase in ("observation", "scope", "outputs", "verification"):
        assert entries[-1][phase] is None
    summary = attempts.refresh()
    assert summary["schema_version"] == 1
    assert summary["attempt_count"] == 2
    assert summary["active_attempt"] == entries[-1]
    assert summary["journal"] == {
        "path": str(journal.path),
        "sha256": hashlib.sha256(before).hexdigest(),
    }
    assert store.read() == {
        "user": {"nested": [1, 2]},
        "status": "held",
        "attempt_summary": summary,
    }
    assert journal.path.read_bytes() == before
    assert "unrelated" not in json.dumps(summary)


def build_complete_events(
    repository: Path, tmp_path: Path, exit_code: int
) -> list[dict[str, Any]]:
    import sys

    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.admission import record_admission
    from agent_harness.context import build_context_packet
    from agent_harness.contract import TaskContract
    from agent_harness.native_output import run_stored_scoped_turn

    executable = tmp_path / "fake-codex"
    executable.write_text(
        f"#!{sys.executable}\nimport sys, pathlib\nsys.stdin.buffer.read()\n"
        "pathlib.Path('base.txt').write_text('controlled change')\n"
        "print('RAW-SENTINEL')\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('RAW-SENTINEL')\n"
        f"sys.exit({exit_code})\n"
    )
    executable.chmod(0o700)
    task = TaskContract("PRIVATE-PROMPT", ("base.txt",), (), ("Separate verification",))
    journal = EventJournal(tmp_path / "fixture-state")
    run_stored_scoped_turn(
        CodexCLIAdapter(executable),
        task,
        record_admission(task, approver="human", approved_at="2026-10-02T00:00:00Z"),
        build_context_packet(repository, source_paths=["base.txt"], budget_bytes=4096),
        journal=journal,
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
        redactor=lambda text: text.replace("RAW-SENTINEL", "[removed]"),
        max_output_bytes=128,
    )
    return journal.read()


@pytest.fixture(scope="module")
def event_seeds() -> dict[int, tuple[Path, bytes]]:
    return {}


@pytest.fixture
def complete_events(
    repository: Path,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    request: pytest.FixtureRequest,
    repository_seed: Path,
    event_seeds: dict[int, tuple[Path, bytes]],
) -> list[dict[str, Any]]:
    exit_code = getattr(request, "param", 0)
    if (
        request.node.originalname
        == "test_real_f2_f4_metadata_correlates_without_inventing_verification"
    ):
        return build_complete_events(repository, tmp_path, exit_code)
    if exit_code not in event_seeds:
        seed_path = tmp_path_factory.mktemp("attempt-events")
        seed_root = seed_path / "repository"
        shutil.copytree(repository_seed, seed_root)
        events = build_complete_events(seed_root, seed_path, exit_code)
        event_seeds[exit_code] = seed_root, json.dumps(events).encode()
    seed_root, raw = event_seeds[exit_code]
    # Keep the real producer's final filesystem observation on an independent clone.
    (repository / "base.txt").write_bytes((seed_root / "base.txt").read_bytes())
    old_id = hashlib.sha256(os.fsencode(seed_root)).hexdigest()
    new_id = hashlib.sha256(os.fsencode(repository)).hexdigest()
    return cast(
        list[dict[str, Any]],
        json.loads(
            raw.decode()
            .replace(str(seed_root.parent), str(tmp_path))
            .replace(old_id, new_id)
        ),
    )


def write_events(journal: EventJournal, events: list[dict[str, Any]]) -> None:
    for event in events:
        journal.append(event)


def test_real_f2_f4_metadata_correlates_without_inventing_verification(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
) -> None:
    journal = EventJournal(tmp_path / "state")
    write_events(journal, complete_events)
    attempts = api(repository, journal, CurrentStateStore(tmp_path / "state"))
    entry = attempts.read()[0]
    assert entry["attempt_id"] == complete_events[0]["attempt_id"]
    assert entry["after_head"] == RepositoryObservation.capture(repository).head
    assert entry["observation"]["status"] == "observed"
    assert entry["observation"]["process"]["process_status"] == "successful_exit"
    assert entry["scope"]["proceed"] is True
    assert (
        entry["outputs"]["stdout"]["identifier"]
        == complete_events[3]["stdout"]["identifier"]
    )
    assert entry["verification"] is None
    from agent_harness.failure import classify_attempt_failure

    journal_before = journal.path.read_bytes()
    assessment = classify_attempt_failure(entry)
    assert assessment.attempt_id == entry["attempt_id"]
    assert assessment.revision == entry["after_head"]
    assert {finding.reason for finding in assessment.findings} == {
        "missing_verification"
    }
    assert journal.path.read_bytes() == journal_before
    serialized = json.dumps(attempts.refresh())
    for raw in ("RAW-SENTINEL", "PRIVATE-PROMPT", "[removed]", "successful task"):
        assert raw not in serialized


def test_incomplete_and_failed_observation_have_explicit_missing_later_phases(
    repository: Path,
    tmp_path: Path,
) -> None:
    journal = EventJournal(tmp_path / "state")
    journal.append(intent(repository, "incomplete"))
    journal.append(intent(repository, "failed"))
    journal.append(
        {
            "event": "turn_observation_failed",
            "attempt_id": "failed",
            "classification": "adapter_raised",
        }
    )
    entries = api(repository, journal, CurrentStateStore(tmp_path / "state")).read()
    assert entries[0]["observation"] is None
    assert entries[1]["observation"] == {
        "status": "failed",
        "classification": "adapter_raised",
        "process": None,
    }
    assert all(entries[1][key] is None for key in ("scope", "outputs", "verification"))


@pytest.mark.parametrize(
    "invalid",
    [
        "duplicate_intent",
        "duplicate_observation",
        "contradictory_observation",
        "duplicate_scope",
        "duplicate_outputs",
        "orphan",
        "scope_before_observation",
        "output_before_scope",
        "wrong_root",
        "wrong_repository_id",
        "wrong_contract",
        "wrong_context",
        "wrong_before",
        "wrong_after",
        "missing_process",
        "invalid_process",
        "missing_delta",
        "wrong_scope_id",
        "wrong_scope_delta",
        "wrong_proceed",
        "unsafe_output_id",
        "missing_output_metadata",
        "unknown_failure",
    ],
)
def test_invalid_recognized_history_rejects_without_rewriting(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
    invalid: str,
) -> None:
    from agent_harness.attempt import AttemptJournalError

    events: list[dict[str, Any]] = json.loads(json.dumps(complete_events))
    if invalid.startswith("duplicate_"):
        index = {
            "duplicate_intent": 0,
            "duplicate_observation": 1,
            "duplicate_scope": 2,
            "duplicate_outputs": 3,
        }[invalid]
        events.append(events[index])
    elif invalid == "contradictory_observation":
        events.append(
            {
                "event": "turn_observation_failed",
                "attempt_id": events[0]["attempt_id"],
                "classification": "adapter_raised",
            }
        )
    elif invalid == "orphan":
        events[1]["attempt_id"] = "orphan"
    elif invalid == "scope_before_observation":
        events[1], events[2] = events[2], events[1]
    elif invalid == "output_before_scope":
        events[2], events[3] = events[3], events[2]
    elif invalid == "wrong_root":
        events[0]["repository_root"] = str(tmp_path)
    elif invalid == "wrong_repository_id":
        events[0]["repository_id"] = "c" * 64
    elif invalid == "wrong_contract":
        events[0]["contract_sha256"] = "bad"
    elif invalid == "wrong_context":
        events[0]["context_sha256"] = None
    elif invalid == "wrong_before":
        events[0]["before"]["repository_root"] = str(tmp_path)
    elif invalid == "wrong_after":
        events[1]["after"]["repository_id"] = "c" * 64
    elif invalid == "missing_process":
        del events[1]["process"]
    elif invalid == "invalid_process":
        events[1]["process"]["exit_code"] = True
    elif invalid == "missing_delta":
        del events[1]["delta"]
    elif invalid == "wrong_scope_id":
        events[2]["repository_id"] = "c" * 64
    elif invalid == "wrong_scope_delta":
        events[2]["delta"]["modified"] = ["elsewhere"]
    elif invalid == "wrong_proceed":
        events[2]["proceed"] = False
    elif invalid == "unsafe_output_id":
        events[3]["stdout"]["identifier"] = "../outside"
    elif invalid == "missing_output_metadata":
        del events[3]["stderr"]["truncated"]
    elif invalid == "unknown_failure":
        events = [
            events[0],
            {
                "event": "turn_observation_failed",
                "attempt_id": events[0]["attempt_id"],
                "classification": "PRIVATE-ERROR",
            },
        ]
    journal = EventJournal(tmp_path / "state")
    write_events(journal, events)
    before = journal.path.read_bytes()
    with pytest.raises(AttemptJournalError) as raised:
        api(repository, journal, CurrentStateStore(tmp_path / "state")).read()
    assert raised.value.code == "invalid_history"
    assert str(raised.value) == "invalid_history"
    assert not raised.value.journal_recorded
    assert journal.path.read_bytes() == before


def report(repository: Path, revision: str | None, kind: str = "pass") -> Any:
    from agent_harness.baseline import VerificationBaseline
    from agent_harness.development import DevelopmentVerificationResult
    from agent_harness.evidence import build_evidence_report
    from agent_harness.final import decide_technical_outcome
    from agent_harness.runner import CommandResult

    assert revision is not None
    baseline_result = CommandResult(
        ("checker", "PRIVATE-ARGV"),
        1 if kind == "blocked" else 0,
        "PRIVATE-STDOUT",
        "PRIVATE-STDERR",
        0.1,
        False,
    )
    result = CommandResult(
        baseline_result.command,
        0 if kind == "pass" else 1,
        "PRIVATE-STDOUT",
        "PRIVATE-STDERR",
        0.2,
        False,
    )
    baseline = VerificationBaseline.capture((baseline_result,))
    profile = DevelopmentVerificationResult(
        baseline, (result,), baseline.compare((result,))
    )
    return build_evidence_report(
        repository, revision, profile, decide_technical_outcome(profile), ()
    )


@pytest.mark.parametrize("kind", ["pass", "fail", "blocked"])
def test_verification_appends_compact_metadata_and_preserves_latest_intent(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
    kind: str,
) -> None:
    journal = EventJournal(tmp_path / "state")
    store = CurrentStateStore(tmp_path / "state")
    write_events(journal, complete_events)
    old_id = complete_events[0]["attempt_id"]
    journal.append(intent(repository, "latest"))
    before = journal.path.read_bytes()
    attempts = api(repository, journal, store)
    entry = attempts.record_verification(
        old_id,
        report(repository, RepositoryObservation.capture(repository).head, kind),
        refresh=True,
    )
    assert journal.path.read_bytes().startswith(before)
    assert entry["event"] == "attempt_verification"
    assert entry["schema_version"] == 1
    assert entry["overall_kind"] == kind
    assert entry["technical_kind"] == kind
    assert entry["target_revision"] == complete_events[1]["after"]["head"]
    expected_digest = hashlib.sha256(
        json.dumps(
            ["checker", "PRIVATE-ARGV"], separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()
    assert entry["commands"] == [
        {
            "command_sha256": expected_digest,
            "exit_code": 0 if kind == "pass" else 1,
            "timed_out": False,
        }
    ]
    entries = attempts.read()
    assert entries[0]["verification"]["overall_kind"] == kind
    assert entries[1]["verification"] is None
    assert (
        cast(Any, store.read())["attempt_summary"]["active_attempt"]["attempt_id"]
        == "latest"
    )
    assert cast(Any, store.read())["attempt_summary"]["attempt_count"] == 2
    serialized = json.dumps(entry) + json.dumps(entries) + store.path.read_text()
    for raw in (
        "PRIVATE-ARGV",
        "PRIVATE-STDOUT",
        "PRIVATE-STDERR",
        "PRIVATE-PROMPT",
        "[removed]",
    ):
        assert raw not in serialized
    from agent_harness.attempt import AttemptJournalError

    with pytest.raises(AttemptJournalError) as raised:
        attempts.record_verification(
            old_id, report(repository, RepositoryObservation.capture(repository).head)
        )
    assert raised.value.code == "duplicate_verification"
    assert len(journal.read()) == len(complete_events) + 2


@pytest.mark.parametrize(
    "invalid",
    [
        "wrong_revision",
        "wrong_repository",
        "incomplete",
        "failed",
        "unborn",
        "unknown_attempt",
    ],
)
def test_verification_identity_rejects_before_any_persistence(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
    invalid: str,
) -> None:
    from agent_harness.attempt import AttemptJournalError

    journal = EventJournal(tmp_path / "state")
    events = json.loads(json.dumps(complete_events))
    attempt_id = events[0]["attempt_id"]
    revision = events[1]["after"]["head"]
    evidence_root = repository
    if invalid == "incomplete":
        events = events[:1]
    elif invalid == "failed":
        events = [
            events[0],
            {
                "event": "turn_observation_failed",
                "attempt_id": attempt_id,
                "classification": "adapter_raised",
            },
        ]
    elif invalid == "unborn":
        events = events[:2]
        events[0]["before"]["head"] = None
        events[1]["after"]["head"] = None
    elif invalid == "wrong_revision":
        revision = "c" * 40
    elif invalid == "wrong_repository":
        evidence_root = tmp_path
    elif invalid == "unknown_attempt":
        attempt_id = "other"
    write_events(journal, events)
    store = CurrentStateStore(tmp_path / "state")
    store.write({"user": "preserve"})
    before = journal.path.read_bytes(), store.path.read_bytes()
    with pytest.raises(AttemptJournalError) as raised:
        api(repository, journal, store).record_verification(
            attempt_id, report(evidence_root, revision), refresh=True
        )
    assert raised.value.code in {
        "verification_identity_mismatch",
        "attempt_not_observed",
        "unknown_attempt",
    }
    assert (journal.path.read_bytes(), store.path.read_bytes()) == before


def test_empty_explicit_refresh_writes_only_current_summary(
    repository: Path, tmp_path: Path
) -> None:
    journal = EventJournal(tmp_path / "state")
    store = CurrentStateStore(tmp_path / "state")
    summary = api(repository, journal, store).refresh()
    assert summary["attempt_count"] == 0
    assert summary["active_attempt"] is None
    assert summary["journal"]["sha256"] == hashlib.sha256(b"").hexdigest()
    assert cast(Any, store.read())["attempt_summary"] == summary
    assert not journal.path.exists()


@pytest.mark.parametrize(
    "hazard",
    [
        "inside",
        "relative",
        "noncanonical",
        "parent_symlink",
        "ancestor_file",
        "journal_symlink",
        "journal_fifo",
        "journal_directory",
        "partial",
        "corrupt",
        "different_store",
        "current_symlink",
        "current_fifo",
        "current_directory",
    ],
)
def test_unsafe_persistence_rejects_before_refresh_or_verification_write(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
    hazard: str,
) -> None:
    from agent_harness.attempt import AttemptJournalError

    state = tmp_path / "state"
    state.mkdir()
    journal = EventJournal(state)
    store = CurrentStateStore(state)
    write_events(journal, complete_events)
    untouched = repository / "base.txt"
    original = untouched.read_bytes()
    if hazard == "inside":
        journal = EventJournal(repository / "state")
        store = CurrentStateStore(repository / "state")
    elif hazard == "relative":
        journal = EventJournal("h2-relative-state")
        store = CurrentStateStore("h2-relative-state")
    elif hazard == "noncanonical":
        journal = EventJournal(state / ".." / "state")
        store = CurrentStateStore(state / ".." / "state")
    elif hazard == "parent_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(state, target_is_directory=True)
        journal = EventJournal(alias)
        store = CurrentStateStore(alias)
    elif hazard == "ancestor_file":
        blocked = tmp_path / "file"
        blocked.write_text("preserve")
        journal = EventJournal(blocked / "state")
        store = CurrentStateStore(blocked / "state")
    elif hazard.startswith("journal_"):
        journal.path.unlink()
        if hazard == "journal_symlink":
            journal.path.symlink_to(untouched)
        elif hazard == "journal_fifo":
            os.mkfifo(journal.path)
        else:
            journal.path.mkdir()
    elif hazard == "partial":
        journal.path.write_bytes(journal.path.read_bytes().rstrip(b"\n"))
    elif hazard == "corrupt":
        journal.path.write_text("not-json\n")
    elif hazard == "different_store":
        store = CurrentStateStore(tmp_path / "other")
    elif hazard == "current_symlink":
        store.path.symlink_to(untouched)
    elif hazard == "current_fifo":
        os.mkfifo(store.path)
    elif hazard == "current_directory":
        store.path.mkdir()
    attempts = api(repository, journal, store)
    with pytest.raises(AttemptJournalError) as raised:
        attempts.refresh()
    assert raised.value.code == "unsafe_persistence"
    assert not raised.value.journal_recorded
    with pytest.raises(AttemptJournalError):
        attempts.record_verification(
            complete_events[0]["attempt_id"],
            report(repository, RepositoryObservation.capture(repository).head),
        )
    assert untouched.read_bytes() == original
    assert not (repository / "state").exists()
    assert not (tmp_path / "other").exists()


@pytest.mark.parametrize("boundary", ["append", "summary"])
def test_persistence_failure_exposes_completed_append_and_refresh_recovers(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
    boundary: str,
) -> None:
    from collections.abc import Mapping

    from agent_harness.attempt import AttemptJournalError

    class FailingJournal(EventJournal):
        def append(self, event: Mapping[str, object]) -> None:
            if event.get("event") == "attempt_verification":
                raise OSError("PRIVATE-ERROR")
            super().append(event)

    class FailingStore(CurrentStateStore):
        def write(self, state: Mapping[str, object]) -> None:
            raise OSError("PRIVATE-ERROR")

    state = tmp_path / "state"
    journal = FailingJournal(state) if boundary == "append" else EventJournal(state)
    store = FailingStore(state) if boundary == "summary" else CurrentStateStore(state)
    write_events(journal, complete_events)
    CurrentStateStore(state).write({"user": "preserve", "attempt_summary": "previous"})
    previous = journal.path.read_bytes(), store.path.read_bytes()
    attempts = api(repository, journal, store)
    with pytest.raises(AttemptJournalError) as raised:
        attempts.record_verification(
            complete_events[0]["attempt_id"],
            report(repository, RepositoryObservation.capture(repository).head),
            refresh=True,
        )
    assert raised.value.code == (
        "verification_append_failed" if boundary == "append" else "summary_write_failed"
    )
    assert raised.value.journal_recorded is (boundary == "summary")
    assert str(raised.value) == raised.value.code
    assert store.path.read_bytes() == previous[1]
    if boundary == "append":
        assert journal.path.read_bytes() == previous[0]
    else:
        assert journal.path.read_bytes().startswith(previous[0])
        assert journal.read()[-1]["event"] == "attempt_verification"
        recovered = api(repository, journal, CurrentStateStore(state)).refresh()
        assert recovered["active_attempt"]["verification"]["overall_kind"] == "pass"
        assert cast(Any, CurrentStateStore(state).read())["user"] == "preserve"


def test_unverifiable_protected_evidence_keeps_technical_pass_separate_from_overall_fail(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
) -> None:
    from agent_harness.evidence import build_evidence_report
    from agent_harness.integrity import ProtectedInputCheck, ProtectedInputState

    journal = EventJournal(tmp_path / "state")
    write_events(journal, complete_events)
    base = report(repository, RepositoryObservation.capture(repository).head)
    evidence = build_evidence_report(
        repository,
        base.target_revision,
        base.profile,
        base.technical_decision,
        (ProtectedInputCheck("PRIVATE-PATH", ProtectedInputState.UNVERIFIABLE),),
    )
    entry = api(
        repository, journal, CurrentStateStore(tmp_path / "state")
    ).record_verification(complete_events[0]["attempt_id"], evidence)
    assert entry["technical_kind"] == "pass"
    assert entry["overall_kind"] == "fail"
    assert entry["protected_inputs"] == [
        {
            "path_sha256": hashlib.sha256(b"PRIVATE-PATH").hexdigest(),
            "state": "unverifiable",
        }
    ]
    assert "PRIVATE-PATH" not in journal.path.read_text()


@pytest.mark.parametrize(
    "invalid", ["capture_count", "capture_truncated", "final_presence"]
)
def test_contradictory_output_records_reject(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
    invalid: str,
) -> None:
    from agent_harness.attempt import AttemptJournalError

    events = json.loads(json.dumps(complete_events))
    if invalid == "capture_count":
        events[3]["stdout"]["captured_bytes"] += 1
    elif invalid == "capture_truncated":
        events[3]["stdout"]["capture_truncated"] = True
    else:
        events[3]["final_message"] = None
    journal = EventJournal(tmp_path / "state")
    write_events(journal, events)
    with pytest.raises(AttemptJournalError, match="invalid_history"):
        api(repository, journal, CurrentStateStore(tmp_path / "state")).read()


def test_refresh_returns_same_json_metadata_that_current_store_persists(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
) -> None:
    journal = EventJournal(tmp_path / "state")
    store = CurrentStateStore(tmp_path / "state")
    write_events(journal, complete_events)
    summary = api(repository, journal, store).refresh()
    assert cast(Any, store.read())["attempt_summary"] == summary


@pytest.mark.parametrize(
    "invalid",
    [
        "version",
        "identity",
        "revision",
        "command_digest",
        "timeout",
        "exit_code",
        "overall",
        "protected_state",
        "output_reference",
        "duplicate",
        "orphan",
    ],
)
def test_malformed_verification_history_rejects(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
    invalid: str,
) -> None:
    from agent_harness.attempt import AttemptJournalError

    journal = EventJournal(tmp_path / "state")
    write_events(journal, complete_events)
    entry = api(
        repository, journal, CurrentStateStore(tmp_path / "state")
    ).record_verification(
        complete_events[0]["attempt_id"],
        report(repository, RepositoryObservation.capture(repository).head),
    )
    journal.path.unlink()
    event = json.loads(json.dumps(entry))
    if invalid == "version":
        event["schema_version"] = True
    elif invalid == "identity":
        event["contract_sha256"] = "c" * 64
    elif invalid == "revision":
        event["target_revision"] = "c" * 40
    elif invalid == "command_digest":
        event["commands"][0]["command_sha256"] = "PRIVATE-ARGV"
    elif invalid == "timeout":
        event["commands"][0]["timed_out"] = "false"
    elif invalid == "exit_code":
        event["commands"][0]["exit_code"] = True
    elif invalid == "overall":
        event["overall_kind"] = "fail"
    elif invalid == "protected_state":
        event["protected_inputs"] = [{"path_sha256": "c" * 64, "state": "private"}]
    elif invalid == "output_reference":
        event["output_identifiers"]["stdout"] = "c" * 64
    elif invalid == "orphan":
        event["attempt_id"] = "orphan"
    events = [*complete_events, event]
    if invalid == "duplicate":
        events.append(event)
    write_events(journal, events)
    with pytest.raises(AttemptJournalError, match="invalid_history"):
        api(repository, journal, CurrentStateStore(tmp_path / "state")).read()


@pytest.mark.parametrize("complete_events", [7], indirect=True)
def test_real_failed_process_still_has_scope_and_output_facts(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
) -> None:
    journal = EventJournal(tmp_path / "state")
    write_events(journal, complete_events)
    entry = api(repository, journal, CurrentStateStore(tmp_path / "state")).read()[0]
    assert entry["observation"]["process"]["process_status"] == "nonzero_exit"
    assert entry["observation"]["process"]["exit_code"] == 7
    assert entry["scope"]["proceed"] is True
    assert entry["outputs"]["stdout"]["identifier"] is not None
    assert entry["verification"] is None


@pytest.mark.parametrize("invalid", ["unchanged_path", "overlapping_paths"])
def test_scope_attribution_cannot_contradict_observed_delta(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
    invalid: str,
) -> None:
    from agent_harness.attempt import AttemptJournalError

    events = json.loads(json.dumps(complete_events))
    events[2]["out_of_scope_paths"] = (
        ["never-changed"] if invalid == "unchanged_path" else ["base.txt"]
    )
    if invalid == "overlapping_paths":
        events[2]["protected_paths"] = ["base.txt"]
    events[2]["proceed"] = False
    journal = EventJournal(tmp_path / "state")
    write_events(journal, events)
    with pytest.raises(AttemptJournalError, match="invalid_history"):
        api(repository, journal, CurrentStateStore(tmp_path / "state")).read()


def test_current_path_is_revalidated_after_state_read_before_replacement(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
) -> None:
    from agent_harness.attempt import AttemptJournalError

    class RedirectingStore(CurrentStateStore):
        def read(self) -> dict[str, object] | None:
            original = super().read()
            self.path.unlink()
            self.path.symlink_to(repository / "base.txt")
            return original

    state = tmp_path / "state"
    journal = EventJournal(state)
    write_events(journal, complete_events)
    CurrentStateStore(state).write({"user": "preserve"})
    before = (repository / "base.txt").read_bytes()
    with pytest.raises(AttemptJournalError, match="summary_write_failed") as raised:
        api(repository, journal, RedirectingStore(state)).record_verification(
            complete_events[0]["attempt_id"],
            report(repository, RepositoryObservation.capture(repository).head),
            refresh=True,
        )
    assert raised.value.journal_recorded
    assert journal.read()[-1]["event"] == "attempt_verification"
    assert (repository / "base.txt").read_bytes() == before


@pytest.mark.parametrize("mode", ["sleep", "held_pipes"])
def test_real_timeout_stored_turn_metadata_remains_readable(
    repository: Path, tmp_path: Path, mode: str
) -> None:
    import sys

    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.admission import record_admission
    from agent_harness.context import build_context_packet
    from agent_harness.contract import TaskContract
    from agent_harness.native_output import run_stored_scoped_turn

    executable = tmp_path / "controlled-timeout"
    body = f"#!{sys.executable}\nimport os, sys, time\nsys.stdin.buffer.read()\n"
    if mode == "held_pipes":
        body += "if os.fork() != 0:\n    sys.exit(0)\n"
    body += "time.sleep(30)\n"
    executable.write_text(body)
    executable.chmod(0o700)
    task = TaskContract("controlled timeout", (), (), ("Separate verification",))
    journal = EventJournal(tmp_path / "state")
    run_stored_scoped_turn(
        CodexCLIAdapter(executable),
        task,
        record_admission(task, approver="human", approved_at="2026-10-02T00:00:00Z"),
        build_context_packet(repository, source_paths=[], budget_bytes=4096),
        journal=journal,
        budgets=TurnBudgets(1, 16000, 128, 128, 128),
        redactor=lambda text: text,
        max_output_bytes=128,
    )
    observed = journal.read()[1]["process"]
    assert isinstance(observed, dict)
    assert observed["process_status"] == "timeout"
    assert type(observed["exit_code"]) is int
    if mode == "held_pipes":
        assert observed["exit_code"] == 0
    else:
        assert observed["exit_code"] != 0
    before = journal.path.read_bytes()
    store = CurrentStateStore(tmp_path / "state")
    attempts = api(repository, journal, store)
    entry = attempts.read()[0]
    assert entry["observation"]["process"]["process_status"] == "timeout"
    assert entry["observation"]["process"]["exit_code"] == observed["exit_code"]
    assert entry["scope"]["proceed"] is True
    assert entry["outputs"] is not None
    assert entry["verification"] is None
    summary = attempts.refresh()
    assert summary["active_attempt"] == entry
    assert cast(Any, store.read())["attempt_summary"] == summary
    assert journal.path.read_bytes() == before


def test_timeout_with_unavailable_exit_code_remains_readable(
    repository: Path, tmp_path: Path, complete_events: list[dict[str, Any]]
) -> None:
    complete_events[1]["process"]["process_status"] = "timeout"
    complete_events[1]["process"]["exit_code"] = None
    journal = EventJournal(tmp_path / "state")
    write_events(journal, complete_events)
    entry = api(repository, journal, CurrentStateStore(tmp_path / "state")).read()[0]
    assert entry["observation"]["process"]["process_status"] == "timeout"
    assert entry["observation"]["process"]["exit_code"] is None


@pytest.mark.parametrize(
    "status,exit_code",
    [("successful_exit", 7), ("nonzero_exit", 0), ("launch_error", 0)],
)
def test_contradictory_exit_status_still_rejects_history(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
    status: str,
    exit_code: int,
) -> None:
    from agent_harness.attempt import AttemptJournalError

    complete_events[1]["process"]["process_status"] = status
    complete_events[1]["process"]["exit_code"] = exit_code
    journal = EventJournal(tmp_path / "state")
    write_events(journal, complete_events)
    with pytest.raises(AttemptJournalError, match="invalid_history") as raised:
        api(repository, journal, CurrentStateStore(tmp_path / "state")).read()
    assert not raised.value.journal_recorded


@pytest.mark.parametrize("field", ["duration_seconds", "timeout_seconds"])
@pytest.mark.parametrize("operation", ["read", "record_verification", "refresh"])
def test_unrepresentable_numeric_history_uses_fixed_error_without_writes(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
    field: str,
    operation: str,
) -> None:
    from agent_harness.attempt import AttemptJournalError

    if field == "duration_seconds":
        complete_events[1]["process"][field] = 10**400
    else:
        complete_events[0]["budgets"][field] = 10**400
    journal = EventJournal(tmp_path / "state")
    write_events(journal, complete_events)
    store = CurrentStateStore(tmp_path / "state")
    store.write({"user": "preserved", "attempt_summary": {"previous": "retained"}})
    before_journal = journal.path.read_bytes()
    before_current = store.path.read_bytes()
    attempts = api(repository, journal, store)
    with pytest.raises(AttemptJournalError) as raised:
        if operation == "record_verification":
            attempts.record_verification(
                complete_events[0]["attempt_id"],
                report(repository, RepositoryObservation.capture(repository).head),
            )
        else:
            getattr(attempts, operation)()
    expected_code = (
        "summary_write_failed" if operation == "refresh" else "invalid_history"
    )
    assert raised.value.code == expected_code
    assert str(raised.value) == expected_code
    assert raised.value.journal_recorded is False
    assert journal.path.read_bytes() == before_journal
    assert store.path.read_bytes() == before_current


def test_h3_composes_with_real_h2_printable_unicode_attempt_identity(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.failure import classify_attempt_failure

    journal = EventJournal(tmp_path / "unicode-state")
    journal.append(intent(repository, "시도 1"))
    projection = api(
        repository, journal, CurrentStateStore(tmp_path / "unicode-state")
    ).read()[0]
    assessment = classify_attempt_failure(projection)
    assert assessment.attempt_id == "시도 1"
    assert {finding.reason for finding in assessment.findings} == {
        "missing_observation",
        "missing_scope",
        "missing_output",
        "missing_verification",
    }


@pytest.mark.parametrize("complete_events", [1], indirect=True)
@pytest.mark.parametrize("stopping", [False, True])
def test_h4_retry_composes_with_real_h2_and_h3_without_persistence_mutation(
    repository: Path,
    tmp_path: Path,
    complete_events: list[dict[str, Any]],
    stopping: bool,
) -> None:
    from agent_harness.failure import classify_attempt_failure
    from agent_harness.retry import RetryLimits, RetryUsage, evaluate_retry

    if stopping:
        scope = next(
            event for event in complete_events if event["event"] == "scope_decision"
        )
        scope["protected_paths"] = ["base.txt"]
        scope["proceed"] = False
    journal = EventJournal(tmp_path / "h4-state")
    store = CurrentStateStore(tmp_path / "h4-state")
    write_events(journal, complete_events)
    store.write({"user": "preserved"})
    before = journal.path.read_bytes(), store.path.read_bytes()
    projection = api(repository, journal, store).read()[-1]
    assessment = classify_attempt_failure(projection)
    assert any(finding.kind == "native" for finding in assessment.findings)
    assert any(finding.kind == "scope" for finding in assessment.findings) is stopping
    decision = evaluate_retry(
        [assessment],
        limits=RetryLimits(2, 3, 10, 20),
        usage=RetryUsage(1, 1),
        next_commands=1,
        next_seconds=1,
    )
    assert decision.retry is (not stopping)
    assert decision.reason == ("unsafe_failure" if stopping else "retry_allowed")
    assert decision.completed_retries == 0
    assert decision.repeated_failures == 1
    assert (journal.path.read_bytes(), store.path.read_bytes()) == before


@pytest.mark.parametrize("mode", [0o600, 0o666, 0o755, 0o777])
def test_actual_observation_json_roundtrip_retains_permissions(
    repository: Path, mode: int
) -> None:
    from agent_harness.attempt import _observation

    (repository / "base.txt").chmod(mode)
    snapshot = RepositoryObservation.capture(repository)
    decoded = _observation(json.loads(json.dumps(asdict(snapshot))))

    assert decoded == snapshot
    assert decoded.files[0].mode == mode


def test_legacy_observation_does_not_invent_permissions(repository: Path) -> None:
    from agent_harness.attempt import _observation

    payload = asdict(RepositoryObservation.capture(repository))
    for entry in payload["files"]:
        entry.pop("mode")
    decoded = _observation(json.loads(json.dumps(payload)))

    assert all(entry.mode is None for entry in decoded.files)
