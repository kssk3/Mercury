import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

from agent_harness.journal import EventJournal
from agent_harness.state import CurrentStateStore


def reconcile(root: Path, journal: EventJournal, store: CurrentStateStore) -> Any:
    assert importlib.util.find_spec("agent_harness.startup") is not None, (
        "R1 reconciliation behavior is missing"
    )
    from agent_harness.startup import reconcile_startup

    return reconcile_startup(
        root, journal=journal, state_store=store, expected_contract_sha256="a" * 64
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = (tmp_path / "repo").resolve()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, timeout=10)
    (root / "user.txt").write_text("existing dirty work")
    return root


def test_fresh_dirty_repository_is_read_only_and_accounting_unknown(
    repository: Path, tmp_path: Path
) -> None:
    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")
    before = (repository / "user.txt").read_bytes()
    result = reconcile(repository, journal, store)
    assert result.status == "fresh"
    assert result.usage is None
    assert result == reconcile(repository, journal, store)
    assert not journal.path.parent.exists()
    assert (repository / "user.txt").read_bytes() == before
    with pytest.raises(FrozenInstanceError):
        result.status = "completed"


def test_intent_without_after_is_interrupted(repository: Path, tmp_path: Path) -> None:
    from dataclasses import asdict

    from agent_harness.turn import RepositoryObservation

    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")
    before = RepositoryObservation.capture(repository)
    journal.append(
        {
            "event": "turn_intent",
            "attempt_id": "known",
            "repository_root": str(repository),
            "repository_id": before.repository_id,
            "contract_sha256": "a" * 64,
            "context_sha256": "b" * 64,
            "sandbox": "workspace-write",
            "budgets": {
                "timeout_seconds": 2,
                "input_bytes": 100,
                "stdout_bytes": 100,
                "stderr_bytes": 100,
                "final_bytes": 100,
            },
            "before": asdict(before),
        }
    )
    result = reconcile(repository, journal, store)
    assert result.status == "interrupted"
    assert result.attempt_ids == ("known",)
    assert result.usage is None


def test_missing_history_with_summary_is_unknown(
    repository: Path, tmp_path: Path
) -> None:
    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")
    store.write({"loop_summary": {"event": "loop_outcome", "status": "pass"}})
    assert reconcile(repository, journal, store).status == "unknown"


@pytest.mark.parametrize("contents", [b"{private-secret", b"{}", b"[]\n"])
def test_invalid_journal_fails_closed(
    repository: Path, tmp_path: Path, contents: bytes
) -> None:
    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")
    journal.path.parent.mkdir()
    journal.path.write_bytes(contents)
    result = reconcile(repository, journal, store)
    assert result.status == "unknown"
    assert "private-secret" not in repr(result)
    assert journal.path.read_bytes() == contents


def test_loop_interrupted_before_first_attempt(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.turn import RepositoryObservation

    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")
    journal.append(
        {
            "event": "loop_start",
            "repository_id": RepositoryObservation.capture(repository).repository_id,
        }
    )
    result = reconcile(repository, journal, store)
    assert result.status == "interrupted"
    assert result.attempt_ids == ()
    assert result.usage is None


def build_completed_loop(
    repository: Path, tmp_path: Path, mode: object
) -> tuple[Path, EventJournal, CurrentStateStore, str]:
    import hashlib
    import sys

    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.admission import record_admission
    from agent_harness.context import ContextPacket
    from agent_harness.contract import TaskContract
    from agent_harness.loop import run_loop
    from agent_harness.policy import VerificationPolicy
    from agent_harness.retry import RetryLimits

    # Control only this temporary repository, before producer/seed Git writes.
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "--local",
            "maintenance.auto",
            "false",
        ],
        check=True,
        timeout=10,
    )
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True, timeout=10)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=f@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        check=True,
        timeout=10,
    )
    executable = tmp_path / "native"
    executable.write_text(
        f"#!{sys.executable}\nimport pathlib,sys\nsys.stdin.buffer.read()\npathlib.Path('user.txt').write_text('done')\npathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('done')\n"
    )
    if mode is True or mode == "human":
        counter = tmp_path / "native-count"
        script = executable.read_text()
        script = script.replace(
            "import pathlib,sys\n",
            f"import pathlib,sys\np=pathlib.Path({str(counter)!r})\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n",
        )
        script += "sys.exit(1 if len(p.read_text())==1 else 0)\n"
        executable.write_text(script)
    executable.chmod(0o700)
    task = TaskContract("change value", ("user.txt",), (), ("value updated",))
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; assert Path('user.txt').read_text()=='done'",
    )
    if mode == "verification":
        counter = tmp_path / "verification-count"
        command = (
            sys.executable,
            "-c",
            f"from pathlib import Path; p=Path({str(counter)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x'); assert len(p.read_text())!=2",
        )
    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")
    result = run_loop(
        CodexCLIAdapter(executable),
        task,
        record_admission(task, approver="human", approved_at="2026-10-06T00:00:00Z"),
        ContextPacket(str(repository), 4096, ()),
        policy=VerificationPolicy((command,), ".", 3),
        criterion_commands={"value updated": (command,)},
        journal=journal,
        state_store=store,
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
        limits=RetryLimits(0 if mode == "human" else 1, 2, 10, 50),
        redactor=lambda text: text,
        max_output_bytes=128,
        checkpoint_verification=mode == "checkpoint",
    )
    assert result.status == ("human" if mode == "human" else "pass")
    return (
        repository,
        journal,
        store,
        hashlib.sha256(task.to_json().encode()).hexdigest(),
    )


@pytest.fixture(scope="module")
def loop_seeds() -> dict[object, tuple[Path, bytes, bytes, str]]:
    # Only serialized records are reused; mutable repositories belong to each test.
    return {}


@pytest.fixture
def completed_loop(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    request: pytest.FixtureRequest,
    loop_seeds: dict[object, tuple[Path, bytes, bytes, str]],
) -> tuple[Path, EventJournal, CurrentStateStore, str]:
    mode = getattr(request, "param", False)
    real_producer_tests = {
        "test_actual_h6_pass_prefix_is_completed_read_only",
        "test_retry_completion_preserves_earlier_failure",
        "test_actual_verification_failure_retry_remains_supported",
    }
    if request.node.originalname in real_producer_tests:
        root = (tmp_path / "repo").resolve()
        subprocess.run(["git", "init", "--quiet", str(root)], check=True, timeout=10)
        (root / "user.txt").write_text("existing dirty work")
        return build_completed_loop(root, tmp_path, mode)
    if mode not in loop_seeds:
        seed_path = tmp_path_factory.mktemp("loop-seed")
        seed_root = seed_path / "repo"
        subprocess.run(
            ["git", "init", "--quiet", str(seed_root)], check=True, timeout=10
        )
        (seed_root / "user.txt").write_text("existing dirty work")
        root, journal, store, digest = build_completed_loop(seed_root, seed_path, mode)
        loop_seeds[mode] = (
            root,
            journal.path.read_bytes(),
            store.path.read_bytes(),
            digest,
        )
    seed_root, raw_events, raw_state, digest = loop_seeds[mode]
    root = (tmp_path / "repo").resolve()
    shutil.copytree(seed_root, root)
    old_id = hashlib.sha256(os.fsencode(seed_root)).hexdigest()
    new_id = hashlib.sha256(os.fsencode(root)).hexdigest()

    def rebind(raw: bytes) -> Any:
        return json.loads(
            raw.decode()
            .replace(str(seed_root.parent), str(tmp_path))
            .replace(old_id, new_id)
        )

    events = [rebind(line) for line in raw_events.splitlines()]
    journal, store = (
        EventJournal(tmp_path / "external"),
        CurrentStateStore(tmp_path / "external"),
    )
    store.write(rebind(raw_state))
    fixture = root, journal, store, digest
    # H6 hashes the H2 prefix before appending its terminal outcome.
    rewrite_runtime_events(fixture, events)
    return fixture


def test_completed_loop_seed_copy_avoids_automatic_maintenance(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    request: pytest.FixtureRequest,
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

    fixture = cast(Any, completed_loop).__wrapped__(
        tmp_path, tmp_path_factory, request, {}
    )
    assert inspect_loop(fixture).status == "completed"

    events = [json.loads(line) for line in trace.read_text().splitlines()]
    launches = [
        event["argv"]
        for event in events
        if event.get("event") == "child_start"
        and "--auto" in event.get("argv", [])
        and any(command in event["argv"] for command in ("maintenance", "gc"))
    ]
    assert launches == [], "fixture Git writes must not launch concurrent housekeeping"


def inspect_loop(fixture: tuple[Path, EventJournal, CurrentStateStore, str]) -> Any:
    from agent_harness.startup import reconcile_startup

    root, journal, store, digest = fixture
    return reconcile_startup(
        root, journal=journal, state_store=store, expected_contract_sha256=digest
    )


def test_actual_h6_pass_prefix_is_completed_read_only(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
) -> None:
    root, journal, store, _ = completed_loop
    before = (
        journal.path.read_bytes(),
        store.path.read_bytes(),
        (root / "user.txt").read_bytes(),
    )
    result = inspect_loop(completed_loop)
    assert result.status == "completed"
    assert result.loop_status == "pass"
    assert result.usage is not None and result.usage[0] == 4
    assert result == inspect_loop(completed_loop)
    assert before == (
        journal.path.read_bytes(),
        store.path.read_bytes(),
        (root / "user.txt").read_bytes(),
    )


def test_completed_loop_changed_workspace_is_interrupted(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
) -> None:
    (completed_loop[0] / "user.txt").write_text("later user edit")
    result = inspect_loop(completed_loop)
    assert result.status == "interrupted"
    assert result.workspace_delta.modified == ("user.txt",)


@pytest.mark.parametrize(
    "field,value",
    [
        ("commands", True),
        ("elapsed_seconds", float("nan")),
        ("elapsed_boundary", "other"),
    ],
)
def test_invalid_loop_accounting_is_unknown(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
    field: str,
    value: object,
) -> None:
    import json

    _, journal, store, _ = completed_loop
    events = journal.read()
    events[-1][field] = value
    journal.path.write_text("".join(json.dumps(event) + "\n" for event in events))
    current = store.read()
    assert current is not None
    current["loop_summary"] = events[-1]
    store.write(current)
    result = inspect_loop(completed_loop)
    assert result.status == "unknown"
    assert result.usage is None


def test_empty_h2_summary_remains_fresh(repository: Path, tmp_path: Path) -> None:
    from agent_harness.attempt import AttemptJournal

    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")
    AttemptJournal(repository, journal=journal, state_store=store).refresh()
    assert reconcile(repository, journal, store).status == "fresh"


@pytest.mark.parametrize("change", ["missing", "stale", "contradiction", "no_outcome"])
def test_summary_consistency_never_invents_completion(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str], change: str
) -> None:
    _, journal, store, _ = completed_loop
    current = store.read()
    assert current is not None
    if change == "missing":
        del current["attempt_summary"]
    elif change == "stale":
        import hashlib

        current["attempt_summary"] = {
            "schema_version": 1,
            "attempt_count": 0,
            "journal": {
                "path": str(journal.path),
                "sha256": hashlib.sha256(b"").hexdigest(),
            },
            "active_attempt": None,
        }
    elif change == "contradiction":
        current["attempt_summary"]["attempt_count"] = 999  # type: ignore[index]
    else:
        del current["loop_summary"]
        journal.path.write_bytes(
            b"".join(journal.path.read_bytes().splitlines(keepends=True)[:-1])
        )
        current["attempt_summary"]["attempt_count"] = 999  # type: ignore[index]
    store.write(current)
    result = inspect_loop(completed_loop)
    assert result.status == (
        "interrupted" if change in {"missing", "stale"} else "unknown"
    )
    if change in {"missing", "stale"}:
        assert result.diagnostics == ("summary_" + change,)


@pytest.mark.parametrize("completed_loop", [True], indirect=True)
def test_retry_completion_preserves_earlier_failure(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
) -> None:
    result = inspect_loop(completed_loop)
    assert result.status == "completed"
    assert len(result.attempt_ids) == 2
    assert any(
        finding.reason == "native_nonzero" for finding in result.failures[0].findings
    )
    assert result.accounting_complete
    assert result.usage is not None and result.usage[0] >= 5


def test_h2_pass_without_terminal_loop_cannot_complete(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
) -> None:
    _, journal, store, _ = completed_loop
    journal.path.write_bytes(
        b"".join(journal.path.read_bytes().splitlines(keepends=True)[:-1])
    )
    current = store.read()
    assert current is not None
    del current["loop_summary"]
    store.write(current)
    assert inspect_loop(completed_loop).status == "interrupted"


def test_journal_change_during_projection_is_unknown(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_harness.attempt import AttemptJournal

    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")
    original = AttemptJournal.read

    def changed_read(self: AttemptJournal) -> tuple[dict[str, object], ...]:
        entries = original(self)
        journal.append({"event": "external_change"})
        return entries

    monkeypatch.setattr(AttemptJournal, "read", changed_read)
    result = reconcile(repository, journal, store)
    assert result.status == "unknown"
    assert result.diagnostics == ("inputs_changed",)


@pytest.mark.parametrize(
    "event,status",
    [
        ("caller_note", "completed"),
        ("loop_start", "unknown"),
        ("scope_decision", "unknown"),
    ],
)
def test_events_after_terminal_preserve_only_unrelated_history(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
    event: str,
    status: str,
) -> None:
    _, journal, store, _ = completed_loop
    journal.append({"event": event, "text": "private-note"})
    before = journal.path.read_bytes(), store.path.read_bytes()
    assert inspect_loop(completed_loop).status == status
    assert before == (journal.path.read_bytes(), store.path.read_bytes())


def test_command_count_below_proven_launches_is_unknown(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
) -> None:
    import hashlib
    import json

    _, journal, store, _ = completed_loop
    events = journal.read()
    events[-1]["commands"] = 0
    prefix = b"".join(journal.path.read_bytes().splitlines(keepends=True)[:-1])
    journal.path.write_bytes(prefix + (json.dumps(events[-1]) + "\n").encode())
    current = store.read()
    assert current is not None
    current["loop_summary"] = events[-1]
    current["attempt_summary"]["journal"]["sha256"] = hashlib.sha256(prefix).hexdigest()  # type: ignore[index]
    store.write(current)
    assert inspect_loop(completed_loop).status == "unknown"


@pytest.mark.parametrize(
    "target",
    ["journal_symlink", "current_symlink", "journal_fifo", "current_directory"],
)
def test_unsafe_persistence_fails_closed(
    repository: Path, tmp_path: Path, target: str
) -> None:
    import os

    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")
    journal.path.parent.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("{}\n")
    if target == "journal_symlink":
        journal.path.symlink_to(outside)
    elif target == "current_symlink":
        store.path.symlink_to(outside)
    elif target == "journal_fifo":
        os.mkfifo(journal.path)
    else:
        store.path.mkdir()
    assert reconcile(repository, journal, store).status == "unknown"
    assert outside.read_text() == "{}\n"


@pytest.mark.parametrize("target", ["current", "workspace"])
def test_current_and_workspace_changes_during_projection_are_unknown(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    from agent_harness.attempt import AttemptJournal

    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")
    original = AttemptJournal.read

    def changed_read(self: AttemptJournal) -> tuple[dict[str, object], ...]:
        entries = original(self)
        if target == "current":
            store.write({"caller": "changed"})
        else:
            (repository / "user.txt").write_text("changed during read")
        return entries

    monkeypatch.setattr(AttemptJournal, "read", changed_read)
    assert reconcile(repository, journal, store).status == "unknown"


@pytest.mark.parametrize("completed_loop", [True], indirect=True)
def test_bad_summary_preserves_safe_failure_and_loop_evidence(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
) -> None:
    _, _, store, _ = completed_loop
    current = store.read()
    assert current is not None
    current["attempt_summary"]["attempt_count"] = 999  # type: ignore[index]
    store.write(current)
    result = inspect_loop(completed_loop)
    assert result.status == "unknown"
    assert result.loop_status == "pass"
    assert result.usage is not None
    assert any(
        finding.reason == "native_nonzero" for finding in result.failures[0].findings
    )


@pytest.mark.parametrize(
    "fault",
    [
        "foreign_loop",
        "duplicate_intent",
        "scope_before_observation",
        "contract",
        "root",
    ],
)
def test_foreign_duplicate_out_of_order_history_fails_closed(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str], fault: str
) -> None:
    import json

    _, journal, _, _ = completed_loop
    events = journal.read()
    if fault == "foreign_loop":
        events[0]["repository_id"] = "f" * 64
    elif fault == "duplicate_intent":
        events.insert(2, dict(events[1]))
    elif fault == "scope_before_observation":
        observation = next(
            index
            for index, event in enumerate(events)
            if event["event"] == "turn_observation"
        )
        scope = next(
            index
            for index, event in enumerate(events)
            if event["event"] == "scope_decision"
        )
        events[observation], events[scope] = events[scope], events[observation]
    elif fault == "contract":
        events[1]["contract_sha256"] = "f" * 64
    else:
        events[1]["repository_root"] = "/foreign/private-secret"
    journal.path.write_text("".join(json.dumps(event) + "\n" for event in events))
    result = inspect_loop(completed_loop)
    assert result.status == "unknown"
    assert "private-secret" not in repr(result)


def test_unreadable_current_returns_fixed_unknown(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")

    def unreadable(self: CurrentStateStore) -> dict[str, object] | None:
        raise PermissionError("private-secret")

    monkeypatch.setattr(CurrentStateStore, "read", unreadable)
    result = reconcile(repository, journal, store)
    assert result.status == "unknown"
    assert "private-secret" not in repr(result)


def test_unknown_loop_usage_is_explicit_lower_bound(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
) -> None:
    import json

    _, journal, store, _ = completed_loop
    events = journal.read()
    events[-1]["status"] = "unknown"
    events[-1]["reason"] = "native_launch_accounting"
    prefix = b"".join(journal.path.read_bytes().splitlines(keepends=True)[:-1])
    journal.path.write_bytes(prefix + (json.dumps(events[-1]) + "\n").encode())
    current = store.read()
    assert current is not None
    current["loop_summary"] = events[-1]
    store.write(current)
    result = inspect_loop(completed_loop)
    assert result.loop_status == "unknown"
    assert result.usage is not None
    assert not result.accounting_complete
    assert result.status != "completed"


def test_orphan_attempt_terminal_is_not_fresh(repository: Path, tmp_path: Path) -> None:
    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")
    journal.append(
        {"event": "loop_attempt_terminal", "attempt_id": "foreign", "state": "failed"}
    )
    assert reconcile(repository, journal, store).status == "unknown"


@pytest.mark.parametrize("completed_loop", [True], indirect=True)
@pytest.mark.parametrize("fault", ["foreign", "duplicate", "impossible"])
def test_attempt_terminal_identity_order_and_state_are_validated(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str], fault: str
) -> None:
    _, journal, _, _ = completed_loop
    events = journal.read()
    index = next(
        index
        for index, event in enumerate(events)
        if event["event"] == "loop_attempt_terminal"
    )
    if fault == "foreign":
        events[index]["attempt_id"] = "foreign"
    elif fault == "duplicate":
        events.insert(index, dict(events[index]))
    else:
        events[index]["state"] = "passed"
    rewrite_runtime_events(completed_loop, events)
    assert inspect_loop(completed_loop).status == "unknown"


def rewrite_runtime_events(
    fixture: tuple[Path, EventJournal, CurrentStateStore, str],
    events: list[dict[str, object]],
) -> None:
    import json

    from agent_harness.attempt import AttemptJournal

    root, journal, store, _ = fixture
    outcome = events[-1]
    assert outcome["event"] == "loop_outcome"
    journal.path.write_text("".join(json.dumps(event) + "\n" for event in events[:-1]))
    AttemptJournal(root, journal=journal, state_store=store).refresh()
    journal.append(outcome)
    current = store.read()
    assert current is not None
    current["loop_summary"] = outcome
    store.write(current)


def test_unfinished_prior_intent_then_pass_never_completes(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
) -> None:
    _, journal, _, _ = completed_loop
    events = journal.read()
    earlier = dict(events[1])
    earlier["attempt_id"] = "unfinished-prior"
    events.insert(1, earlier)
    events[-1]["attempt_ids"] = ["unfinished-prior"] + events[-1]["attempt_ids"]  # type: ignore[operator]
    rewrite_runtime_events(completed_loop, events)
    result = inspect_loop(completed_loop)
    assert result.status == "unknown"
    assert "unfinished-prior" in result.attempt_ids


@pytest.mark.parametrize("completed_loop", [True], indirect=True)
@pytest.mark.parametrize("suffix", ["partial", "foreign_row"])
def test_valid_prefix_failure_survives_invalid_suffix(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str], suffix: str
) -> None:
    _, journal, _, _ = completed_loop
    valid_ids = inspect_loop(completed_loop).attempt_ids
    if suffix == "partial":
        with journal.path.open("ab") as stream:
            stream.write(b"{private-invalid")
    else:
        journal.append({"event": "scope_decision", "attempt_id": "foreign"})
    before = journal.path.read_bytes()
    result = inspect_loop(completed_loop)
    assert result.status == "unknown"
    assert result.attempt_ids == valid_ids
    assert any(
        finding.reason == "native_nonzero" for finding in result.failures[0].findings
    )
    assert result.usage is None and not result.accounting_complete
    assert journal.path.read_bytes() == before
    assert "private-invalid" not in repr(result)


@pytest.mark.parametrize("completed_loop", ["verification"], indirect=True)
def test_actual_verification_failure_retry_remains_supported(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
) -> None:
    result = inspect_loop(completed_loop)
    assert result.status == "completed"
    assert len(result.attempt_ids) == 2
    assert any(
        finding.reason == "verification_nonzero"
        for finding in result.failures[0].findings
    )


@pytest.mark.parametrize("completed_loop", [True], indirect=True)
def test_h2_valid_post_outcome_intent_cannot_contaminate_runtime_prefix(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
) -> None:
    from agent_harness.attempt import AttemptJournal

    root, journal, store, _ = completed_loop
    prefix = inspect_loop(completed_loop)
    intent = dict(
        next(event for event in journal.read() if event["event"] == "turn_intent")
    )
    intent["attempt_id"] = "rejected-suffix-intent"
    journal.append(intent)
    assert (
        AttemptJournal(root, journal=journal, state_store=store).read()[-1][
            "attempt_id"
        ]
        == "rejected-suffix-intent"
    )
    before = journal.path.read_bytes(), store.path.read_bytes()
    result = inspect_loop(completed_loop)
    assert result.status == "unknown"
    assert result.attempt_ids == prefix.attempt_ids
    assert result.failures == prefix.failures
    assert "rejected-suffix-intent" not in result.attempt_ids
    assert result.usage is None and not result.accounting_complete
    assert before == (journal.path.read_bytes(), store.path.read_bytes())


@pytest.mark.parametrize("completed_loop", ["human"], indirect=True)
@pytest.mark.parametrize("suffix", ["intent", "observed_attempt"])
def test_nonpass_outcome_closes_all_later_runtime_prefix(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str], suffix: str
) -> None:
    from agent_harness.attempt import AttemptJournal

    root, journal, store, _ = completed_loop
    prefix = inspect_loop(completed_loop)
    assert prefix.loop_status == "human"
    original = journal.read()
    for event in original:
        if event["event"] not in {
            "turn_intent",
            "turn_observation",
            "scope_decision",
            "native_output_stored",
        }:
            continue
        copied = dict(event)
        copied["attempt_id"] = "invalid-after-human"
        journal.append(copied)
        if suffix == "intent":
            break
    assert (
        AttemptJournal(root, journal=journal, state_store=store).read()[-1][
            "attempt_id"
        ]
        == "invalid-after-human"
    )
    before = journal.path.read_bytes(), store.path.read_bytes()
    result = inspect_loop(completed_loop)
    assert result.status == "unknown"
    assert result.attempt_ids == prefix.attempt_ids
    assert result.failures == prefix.failures
    assert "invalid-after-human" not in result.attempt_ids
    assert result.usage is None and not result.accounting_complete
    assert before == (journal.path.read_bytes(), store.path.read_bytes())


@pytest.mark.parametrize("completed_loop", ["human"], indirect=True)
def test_nonpass_outcome_keeps_unrelated_later_note(
    completed_loop: tuple[Path, EventJournal, CurrentStateStore, str],
) -> None:
    _, journal, store, _ = completed_loop
    prefix = inspect_loop(completed_loop)
    journal.append({"event": "caller_note", "text": "unrelated"})
    before = journal.path.read_bytes(), store.path.read_bytes()
    result = inspect_loop(completed_loop)
    assert result.status == "interrupted"
    assert result.loop_status == "human"
    assert (
        result.attempt_ids == prefix.attempt_ids and result.failures == prefix.failures
    )
    assert before == (journal.path.read_bytes(), store.path.read_bytes())


def test_orphan_verification_checkpoint_is_unknown(
    repository: Path, tmp_path: Path
) -> None:
    journal = EventJournal(tmp_path / "external")
    store = CurrentStateStore(tmp_path / "external")
    journal.append({"event": "verification_checkpoint", "pending_phase": "final"})
    assert reconcile(repository, journal, store).status == "unknown"


@pytest.mark.parametrize("corruption", ["missing_final_intent", "elapsed_reset"])
def test_checkpoint_terminal_requires_phase_intent_and_elapsed_floor(
    repository: Path, tmp_path: Path, corruption: str
) -> None:
    from agent_harness.turn import RepositoryObservation

    fixture = build_completed_loop(repository, tmp_path, "checkpoint")
    _, journal, store, _ = fixture
    assert inspect_loop(fixture).status == "completed"
    events = journal.read()
    if corruption == "missing_final_intent":
        events = [
            event
            for event in events
            if not (
                event.get("event") == "verification_phase_intent"
                and event.get("phase") == "final"
            )
        ]
    else:
        assert any(
            event.get("event") == "verification_checkpoint"
            and cast(float, event["elapsed_seconds"]) > 0
            for event in events
        )
        events[-1]["elapsed_seconds"] = 0
    # Repair the exact pre-outcome summary hash and terminal projection, so the
    # tested rejection comes from ordering/accounting rather than stale state.
    rewrite_runtime_events(fixture, events)
    before = (
        journal.path.read_bytes(),
        store.path.read_bytes(),
        RepositoryObservation.capture(repository),
    )
    assert inspect_loop(fixture).status == "unknown"
    assert before == (
        journal.path.read_bytes(),
        store.path.read_bytes(),
        RepositoryObservation.capture(repository),
    )
