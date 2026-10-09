from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from agent_harness.adapter import CodexCLIAdapter, CodexTurnResult, TurnBudgets
from agent_harness.admission import AdmissionRecord, record_admission
from agent_harness.context import ContextPacket, build_context_packet
from agent_harness.contract import TaskContract
from agent_harness.journal import EventJournal

if TYPE_CHECKING:
    from agent_harness.turn import ObservedTurnResult


def as_mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=True,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        },
    ).stdout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    root = root.resolve()
    (root / "base.txt").write_bytes(b"initial")
    git(root, "add", "base.txt")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    return root


def test_snapshot_fingerprints_content_modes_links_missing_and_dirty_baseline(
    repository: Path,
) -> None:
    from agent_harness.turn import RepositoryObservation

    (repository / ".gitignore").write_text("ignored/\n")
    git(repository, "add", ".gitignore")
    (repository / "base.txt").write_bytes(b"already dirty")
    (repository / "untracked 한글\nfile").write_bytes(b"new")
    (repository / "executable").write_bytes(b"#!/bin/sh\n")
    (repository / "executable").chmod(0o755)
    (repository / "link").symlink_to("outside-target")
    (repository / "deleted.txt").write_bytes(b"gone")
    git(repository, "add", "deleted.txt")
    (repository / "deleted.txt").unlink()
    (repository / "ignored").mkdir()
    (repository / "ignored" / "secret").write_text("excluded")

    snapshot = RepositoryObservation.capture(repository)
    assert snapshot == RepositoryObservation.capture(repository)
    assert snapshot.repository_root == str(repository)
    assert snapshot.repository_id == hashlib.sha256(os.fsencode(repository)).hexdigest()
    assert snapshot.head == git(repository, "rev-parse", "HEAD").decode().strip()
    assert len(snapshot.index_sha256) == 64
    files = {entry.path: entry for entry in snapshot.files}
    assert tuple(files) == tuple(sorted(files, key=os.fsencode))
    assert "ignored/secret" not in files
    assert all(".git/" not in path for path in files)
    assert files["base.txt"].sha256 == hashlib.sha256(b"already dirty").hexdigest()
    assert files["base.txt"].size_bytes == len(b"already dirty")
    assert files["executable"].executable
    assert files["link"].kind == "symlink"
    assert files["link"].sha256 == hashlib.sha256(b"outside-target").hexdigest()
    assert files["deleted.txt"].kind == "missing"
    assert files["deleted.txt"].sha256 is None
    assert any(
        entry.path == "base.txt" and entry.status == " M" for entry in snapshot.status
    )
    assert any(
        entry.path == "deleted.txt" and entry.status == "AD"
        for entry in snapshot.status
    )
    assert any(entry.status == "??" for entry in snapshot.status)
    with pytest.raises(FrozenInstanceError):
        snapshot.head = None  # type: ignore[misc]


def test_delta_distinguishes_dirty_modification_add_delete_rename_and_modes(
    repository: Path,
) -> None:
    from agent_harness.turn import RepositoryObservation, compare_observations

    for name in ("delete", "rename", "mode", "same"):
        (repository / name).write_bytes(b"original")
    git(repository, "add", ".")
    (repository / "base.txt").write_bytes(b"preexisting dirty")
    (repository / "preexisting-untracked").write_bytes(b"before")
    before = RepositoryObservation.capture(repository)
    (repository / "base.txt").write_bytes(b"additional dirty change")
    (repository / "preexisting-untracked").write_bytes(b"after")
    (repository / "delete").unlink()
    (repository / "rename").rename(repository / "renamed")
    (repository / "mode").chmod(0o755)
    (repository / "same").unlink()
    (repository / "same").write_bytes(b"original")
    (repository / "added").write_bytes(b"new")
    after = RepositoryObservation.capture(repository)
    delta = compare_observations(before, after)
    assert set(delta.added) == {"added", "renamed"}
    assert set(delta.modified) == {"base.txt", "preexisting-untracked", "mode"}
    assert set(delta.deleted) == {"delete", "rename"}
    assert not delta.index_changed
    assert not delta.head_changed
    assert delta.status_changed
    assert before.files != after.files


def test_staging_and_commit_are_observed_without_file_byte_change(
    repository: Path,
) -> None:
    from agent_harness.turn import RepositoryObservation, compare_observations

    (repository / "base.txt").write_bytes(b"dirty")
    before = RepositoryObservation.capture(repository)
    git(repository, "add", "base.txt")
    staged = RepositoryObservation.capture(repository)
    delta = compare_observations(before, staged)
    assert not delta.added and not delta.modified and not delta.deleted
    assert delta.index_changed and delta.status_changed and not delta.head_changed
    git(
        repository,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "staged",
    )
    committed = RepositoryObservation.capture(repository)
    delta = compare_observations(staged, committed)
    assert delta.head_changed and delta.status_changed and not delta.index_changed
    assert not delta.added and not delta.modified and not delta.deleted


def test_index_hash_ignores_stat_cache_refresh(repository: Path) -> None:
    from agent_harness.turn import RepositoryObservation

    before = RepositoryObservation.capture(repository)
    os.utime(repository / "base.txt", (100, 100))
    git(repository, "update-index", "--refresh")
    after = RepositoryObservation.capture(repository)
    assert before == after


def test_snapshot_supports_unborn_head_and_empty_index(tmp_path: Path) -> None:
    from agent_harness.turn import RepositoryObservation

    root = tmp_path / "unborn"
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / "new").write_bytes(b"new")
    snapshot = RepositoryObservation.capture(root)
    assert snapshot.head is None
    assert tuple(entry.path for entry in snapshot.files) == ("new",)
    assert snapshot.index_sha256 == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize(
    "kind", ["ancestor_symlink", "fifo", "tracked_directory", "submodule"]
)
def test_snapshot_rejects_unsupported_observation(
    repository: Path, tmp_path: Path, kind: str
) -> None:
    from agent_harness.turn import RepositoryObservation

    if kind == "ancestor_symlink":
        (repository / "directory").mkdir()
        (repository / "directory" / "tracked").write_bytes(b"old")
        git(repository, "add", "directory/tracked")
        (repository / "directory" / "tracked").unlink()
        (repository / "directory").rmdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "tracked").write_bytes(b"do not follow")
        (repository / "directory").symlink_to(outside, target_is_directory=True)
    elif kind == "fifo":
        os.mkfifo(repository / "special")
    elif kind == "tracked_directory":
        (repository / "base.txt").unlink()
        (repository / "base.txt").mkdir()
    else:
        head = git(repository, "rev-parse", "HEAD").decode().strip()
        git(repository, "update-index", "--add", "--cacheinfo", f"160000,{head},module")
    with pytest.raises(ValueError):
        RepositoryObservation.capture(repository)


def test_snapshot_inherited_git_selectors_cannot_redirect_reads(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_harness.turn import RepositoryObservation

    before = RepositoryObservation.capture(repository)
    other = tmp_path / "other"
    subprocess.run(["git", "init", "--quiet", str(other)], check=True)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / "wrong-index"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    assert RepositoryObservation.capture(repository) == before


def test_symlink_target_and_large_file_digest_without_following(
    repository: Path,
    tmp_path: Path,
) -> None:
    from agent_harness.turn import RepositoryObservation, compare_observations

    secret = tmp_path / "secret"
    secret.write_bytes(b"PRIVATE SOURCE")
    (repository / "link").symlink_to(secret)
    payload = b"large content" * 100000
    (repository / "large").write_bytes(payload)
    before = RepositoryObservation.capture(repository)
    files = {entry.path: entry for entry in before.files}
    assert files["large"].size_bytes == len(payload)
    assert files["large"].sha256 == hashlib.sha256(payload).hexdigest()
    assert files["link"].sha256 == hashlib.sha256(os.fsencode(secret)).hexdigest()
    secret.write_bytes(b"different outside bytes")
    assert RepositoryObservation.capture(repository) == before
    (repository / "link").unlink()
    (repository / "link").symlink_to("different-target")
    delta = compare_observations(before, RepositoryObservation.capture(repository))
    assert delta.modified == ("link",)


def test_missing_tracked_ancestor_is_missing_not_untracked_loss(
    repository: Path,
) -> None:
    from agent_harness.turn import RepositoryObservation

    (repository / "directory").mkdir()
    (repository / "directory" / "tracked").write_bytes(b"tracked")
    git(repository, "add", ".")
    (repository / "directory" / "tracked").unlink()
    (repository / "directory").rmdir()
    files = {
        entry.path: entry for entry in RepositoryObservation.capture(repository).files
    }
    assert files["directory/tracked"].kind == "missing"


def fake(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-codex"
    executable.write_text(f"#!{sys.executable}\n" + body)
    executable.chmod(0o700)
    return executable


def inputs(repository: Path) -> tuple[TaskContract, AdmissionRecord, ContextPacket]:
    contract = TaskContract(
        "SECRET GOAL", ("base.txt", "added"), (), ("SECRET CRITERION",)
    )
    admission = record_admission(
        contract, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    context = build_context_packet(
        repository, source_paths=["base.txt"], budget_bytes=4096
    )
    return contract, admission, context


def invoke(
    repository: Path,
    executable: Path,
    state_directory: Path,
    *,
    budgets: TurnBudgets | None = None,
    journal: EventJournal | None = None,
    adapter: CodexCLIAdapter | None = None,
) -> ObservedTurnResult:
    from agent_harness.turn import run_observed_turn

    return run_observed_turn(
        adapter if adapter is not None else CodexCLIAdapter(executable),
        *inputs(repository),
        journal=journal if journal is not None else EventJournal(state_directory),
        budgets=budgets
        if budgets is not None
        else TurnBudgets(3, 16000, 128, 128, 128),
    )


def test_external_intent_is_closed_before_real_child_mutation_and_no_raw_payload_leaks(
    repository: Path,
    tmp_path: Path,
) -> None:
    from agent_harness.turn import compare_observations

    (repository / "base.txt").write_bytes(b"SECRET SOURCE already dirty")
    state = tmp_path / "state"
    journal = EventJournal(state)
    journal.append({"event": "previous", "number": 7})
    executable = fake(
        tmp_path,
        "import hashlib, json, pathlib, sys\n"
        "payload = sys.stdin.buffer.read()\n"
        f"events = [json.loads(line) for line in pathlib.Path({str(journal.path)!r}).read_text().splitlines()]\n"
        "assert events[-1]['event'] == 'turn_intent'\n"
        "assert events[-1]['before']['files'][0]['sha256'] == hashlib.sha256(pathlib.Path('base.txt').read_bytes()).hexdigest()\n"
        "pathlib.Path('base.txt').write_bytes(b'changed by turn')\n"
        "pathlib.Path('added').write_bytes(b'new file')\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('SECRET FINAL')\n"
        "print('SECRET STDOUT')\nprint('SECRET STDERR', file=sys.stderr)\n",
    )
    expected_context = inputs(repository)[2]
    result = invoke(repository, executable, state, journal=journal)
    assert result.adapter_result.process_status == "successful_exit"
    assert result.adapter_result.stdout.text == "SECRET STDOUT\n"
    assert result.delta.added == ("added",)
    assert result.delta.modified == ("base.txt",)
    assert result.delta == compare_observations(result.before, result.after)
    assert any(entry.path == "base.txt" for entry in result.before.status)
    events = journal.read()
    assert events[0] == {"event": "previous", "number": 7}
    assert len(events) == 3
    intent, observation = events[1:]
    assert intent["attempt_id"] == observation["attempt_id"] == result.attempt_id
    assert result.attempt_id
    assert intent["repository_root"] == str(repository)
    assert intent["repository_id"] == result.before.repository_id
    contract, _, _ = inputs(repository)
    assert (
        intent["contract_sha256"]
        == hashlib.sha256(contract.to_json().encode()).hexdigest()
    )
    assert (
        intent["context_sha256"]
        == hashlib.sha256(expected_context.to_json().encode()).hexdigest()
    )
    assert intent["sandbox"] == "workspace-write"
    assert as_mapping(intent["budgets"])["timeout_seconds"] == 3
    assert observation["event"] == "turn_observation"
    assert as_mapping(observation["process"])["stdout"] == {
        "retained_bytes": 14,
        "truncated": False,
    }
    assert as_mapping(observation["process"])["final_output_status"] == "available"
    assert as_mapping(observation["process"])["final_message"] == {
        "retained_bytes": 12,
        "truncated": False,
    }
    raw = journal.path.read_text()
    for secret in (
        "SECRET GOAL",
        "SECRET CRITERION",
        "SECRET SOURCE",
        "SECRET STDOUT",
        "SECRET STDERR",
        "SECRET FINAL",
        "changed by turn",
        "new file",
    ):
        assert secret not in raw


@pytest.mark.parametrize(
    "status", ["successful_exit", "nonzero_exit", "timeout", "launch_error"]
)
def test_every_returned_process_status_has_after_observation(
    repository: Path,
    tmp_path: Path,
    status: str,
) -> None:
    body = "import pathlib, sys, time\nsys.stdin.buffer.read()\npathlib.Path('base.txt').write_bytes(b'changed')\n"
    if status == "nonzero_exit":
        body += "sys.exit(7)\n"
    elif status == "timeout":
        body += "time.sleep(30)\n"
    executable = (
        fake(tmp_path, body) if status != "launch_error" else tmp_path / "missing"
    )
    state = tmp_path / "state"
    result = invoke(
        repository, executable, state, budgets=TurnBudgets(3, 16000, 100, 100, 100)
    )
    assert result.adapter_result.process_status == status
    assert result.delta.modified == (() if status == "launch_error" else ("base.txt",))
    events = EventJournal(state).read()
    assert len(events) == 2
    assert events[-1]["event"] == "turn_observation"
    assert as_mapping(events[-1]["process"])["process_status"] == status
    assert as_mapping(events[-1]["process"])["final_output_status"] == "missing"
    assert events[0]["attempt_id"] == events[1]["attempt_id"]


def test_attempts_are_fresh_and_existing_events_preserved(
    repository: Path, tmp_path: Path
) -> None:
    state = tmp_path / "state"
    executable = fake(tmp_path, "import sys\nsys.stdin.buffer.read()\n")
    first = invoke(repository, executable, state)
    second = invoke(repository, executable, state)
    assert first.attempt_id != second.attempt_id
    assert (
        not first.delta.added and not first.delta.modified and not first.delta.deleted
    )
    assert len(EventJournal(state).read()) == 4


@pytest.mark.parametrize(
    "history",
    [
        b"{",
        b"[]\n",
        b'{}\n{"partial":',
        b'{"valid_but_unclosed":true}',
        b"\xff\n",
        b"\n",
    ],
)
def test_corrupt_or_partial_history_blocks_invocation_without_altering_history(
    repository: Path,
    tmp_path: Path,
    history: bytes,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    journal = EventJournal(state)
    journal.path.write_bytes(history)
    marker = tmp_path / "ran"
    executable = fake(
        tmp_path, f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n"
    )
    with pytest.raises(ValueError):
        invoke(repository, executable, state)
    assert journal.path.read_bytes() == history
    assert not marker.exists()


@pytest.mark.parametrize(
    "location", ["inside", "parent_link", "file_link", "nonregular", "file_parent"]
)
def test_invalid_external_location_blocks_invocation(
    repository: Path,
    tmp_path: Path,
    location: str,
) -> None:
    state = tmp_path / "state"
    if location == "inside":
        state = repository / "state"
    elif location == "parent_link":
        inside = repository / "state"
        inside.mkdir()
        state.symlink_to(inside, target_is_directory=True)
    elif location == "file_link":
        state.mkdir()
        (state / "events.jsonl").symlink_to(repository / "base.txt")
    elif location == "nonregular":
        state.mkdir()
        os.mkfifo(state / "events.jsonl")
    else:
        state.write_text("not a directory")
    marker = tmp_path / "ran"
    executable = fake(
        tmp_path, f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n"
    )
    with pytest.raises(ValueError):
        invoke(repository, executable, state)
    assert not marker.exists()


@pytest.mark.parametrize(
    "invalid",
    [
        "admission",
        "context_root",
        "input_budget",
        "sandbox",
        "budget_type",
        "contract_type",
        "context_type",
        "pre_observation",
    ],
)
def test_invalid_admission_inputs_and_pre_observation_never_invoke(
    repository: Path,
    tmp_path: Path,
    invalid: str,
) -> None:
    from agent_harness.turn import run_observed_turn

    contract, admission, context = inputs(repository)
    budgets: object = TurnBudgets(3, 16000, 100, 100, 100)
    sandbox = "workspace-write"
    if invalid == "admission":
        admission = record_admission(
            TaskContract("different", (), (), ("done",)),
            approver="human",
            approved_at="2026-10-01T00:00:00Z",
        )
    elif invalid == "context_root":
        (repository / "child").mkdir()
        context = ContextPacket(str(repository / "child"), 4096, ())
    elif invalid == "input_budget":
        budgets = TurnBudgets(3, 1, 100, 100, 100)
    elif invalid == "sandbox":
        sandbox = "unsafe"
    elif invalid == "budget_type":
        budgets = None
    elif invalid == "contract_type":
        contract = object()  # type: ignore[assignment]
    elif invalid == "context_type":
        context = object()  # type: ignore[assignment]
    else:
        os.mkfifo(repository / "special")
    marker = tmp_path / "ran"
    executable = fake(
        tmp_path, f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n"
    )
    journal = EventJournal(tmp_path / "state")
    with pytest.raises((ValueError, TypeError)):
        run_observed_turn(
            CodexCLIAdapter(executable),
            contract,
            admission,
            context,
            journal=journal,
            budgets=budgets,  # type: ignore[arg-type]
            sandbox=sandbox,  # type: ignore[arg-type]
        )
    assert not marker.exists()
    assert not journal.path.exists()


@pytest.mark.parametrize("boundary", ["intent", "terminal"])
def test_journal_append_failure_has_safe_typed_error_and_never_retries(
    repository: Path,
    tmp_path: Path,
    boundary: str,
) -> None:
    from agent_harness.turn import TurnObservationError

    class RejectingJournal(EventJournal):
        def append(self, event: Mapping[str, object]) -> None:
            if boundary == "intent" or event["event"] != "turn_intent":
                raise OSError("SECRET APPEND ERROR")
            super().append(event)

    marker = tmp_path / "invocations"
    executable = fake(
        tmp_path,
        "import pathlib, sys\nsys.stdin.buffer.read()\n"
        f"path = pathlib.Path({str(marker)!r})\npath.write_text(path.read_text() + '1' if path.exists() else '1')\n"
        "pathlib.Path('base.txt').write_bytes(b'changed')\n",
    )
    journal = RejectingJournal(tmp_path / "state")
    with pytest.raises(TurnObservationError) as raised:
        invoke(repository, executable, tmp_path / "state", journal=journal)
    error = raised.value
    assert error.code == (
        "intent_append_failed" if boundary == "intent" else "terminal_append_failed"
    )
    assert not error.terminal_recorded
    assert "SECRET" not in str(error)
    if boundary == "intent":
        assert not marker.exists()
        assert error.adapter_result is None
        assert journal.read() == []
    else:
        assert marker.read_text() == "1"
        assert error.adapter_result is not None
        assert error.adapter_result.process_status == "successful_exit"
        assert len(journal.read()) == 1
        assert journal.read()[0]["attempt_id"] == error.attempt_id


@pytest.mark.parametrize("failure_append", [False, True])
def test_adapter_exception_records_fixed_failure_without_raw_exception(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_append: bool,
) -> None:
    from agent_harness.turn import TurnObservationError

    class Journal(EventJournal):
        def append(self, event: Mapping[str, object]) -> None:
            if failure_append and event["event"] != "turn_intent":
                raise OSError("SECRET JOURNAL ERROR")
            super().append(event)

    adapter = CodexCLIAdapter(tmp_path / "unused")

    def raises(*args: object, **kwargs: object) -> CodexTurnResult:
        raise RuntimeError("SECRET ADAPTER ERROR")

    monkeypatch.setattr(adapter, "run", raises)
    journal = Journal(tmp_path / "state")
    with pytest.raises(TurnObservationError) as raised:
        invoke(
            repository,
            tmp_path / "unused",
            tmp_path / "state",
            journal=journal,
            adapter=adapter,
        )
    error = raised.value
    assert error.code == "adapter_raised"
    assert error.terminal_recorded is not failure_append
    assert error.adapter_result is None
    events = journal.read()
    assert len(events) == (1 if failure_append else 2)
    if not failure_append:
        assert events[1] == {
            "event": "turn_observation_failed",
            "attempt_id": error.attempt_id,
            "classification": "adapter_raised",
        }
    assert "SECRET ADAPTER ERROR" not in journal.path.read_text()
    assert "SECRET" not in str(error)


def test_after_observation_failure_keeps_intent_and_returned_process_metadata(
    repository: Path,
    tmp_path: Path,
) -> None:
    from agent_harness.turn import TurnObservationError

    state = tmp_path / "state"
    executable = fake(
        tmp_path,
        "import pathlib, shutil, sys\nsys.stdin.buffer.read()\n"
        "pathlib.Path('base.txt').write_bytes(b'changed')\nshutil.rmtree('.git')\n"
        "print('SECRET STDOUT')\n",
    )
    with pytest.raises(TurnObservationError) as raised:
        invoke(repository, executable, state)
    error = raised.value
    assert error.code == "after_observation_failed"
    assert error.terminal_recorded
    assert error.adapter_result is not None
    assert error.adapter_result.stdout.text == "SECRET STDOUT\n"
    events = EventJournal(state).read()
    assert [entry["event"] for entry in events] == [
        "turn_intent",
        "turn_observation_failed",
    ]
    assert events[-1]["classification"] == "after_observation_failed"
    assert as_mapping(events[-1]["process"])["process_status"] == "successful_exit"
    assert "after" not in events[-1]
    assert "SECRET STDOUT" not in EventJournal(state).path.read_text()


def test_terminal_write_rejects_parent_symlink_redirect_to_repository(
    repository: Path,
    tmp_path: Path,
) -> None:
    from agent_harness.turn import TurnObservationError

    state = tmp_path / "state"
    old = tmp_path / "old-state"
    inside = repository / "inside"
    inside.mkdir()
    executable = fake(
        tmp_path,
        "import pathlib, sys\nsys.stdin.buffer.read()\n"
        f"pathlib.Path({str(state)!r}).rename({str(old)!r})\n"
        f"pathlib.Path({str(state)!r}).symlink_to({str(inside)!r}, target_is_directory=True)\n",
    )
    with pytest.raises(TurnObservationError) as raised:
        invoke(repository, executable, state)
    assert raised.value.code == "terminal_append_failed"
    assert not raised.value.terminal_recorded
    assert len(EventJournal(old).read()) == 1
    assert not (inside / "events.jsonl").exists()


def test_terminal_write_rejects_new_partial_history(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.turn import TurnObservationError

    state = tmp_path / "state"
    journal = EventJournal(state)
    executable = fake(
        tmp_path,
        "import pathlib, sys\nsys.stdin.buffer.read()\n"
        f"with pathlib.Path({str(journal.path)!r}).open('ab') as stream: stream.write(b'{{\"partial\":')\n",
    )
    with pytest.raises(TurnObservationError) as raised:
        invoke(repository, executable, state)
    assert raised.value.code == "terminal_append_failed"
    raw = journal.path.read_bytes()
    assert raw.endswith(b'{"partial":')
    assert len(raw.splitlines()) == 2
    assert json.loads(raw.splitlines()[0])["event"] == "turn_intent"


def test_journal_is_revalidated_between_pre_capture_and_intent(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_harness.turn import RepositoryObservation, TurnObservationError

    state = tmp_path / "state"
    inside = repository / "inside"
    inside.mkdir()
    original = RepositoryObservation.capture

    def capture(
        cls: type[RepositoryObservation], root: Path | str
    ) -> RepositoryObservation:
        snapshot = original(root)
        state.symlink_to(inside, target_is_directory=True)
        return snapshot

    monkeypatch.setattr(RepositoryObservation, "capture", classmethod(capture))
    marker = tmp_path / "ran"
    executable = fake(
        tmp_path, f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n"
    )
    with pytest.raises(TurnObservationError) as raised:
        invoke(repository, executable, state)
    assert raised.value.code == "intent_append_failed"
    assert not marker.exists()
    assert not (inside / "events.jsonl").exists()


def test_retained_size_and_truncation_metadata_excludes_flood_bytes(
    repository: Path,
    tmp_path: Path,
) -> None:
    executable = fake(
        tmp_path,
        "import os, pathlib, sys\nsys.stdin.buffer.read()\n"
        "os.write(1,b'x'*10000)\nos.write(2,b'y'*10000)\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_bytes(b'z'*10000)\n",
    )
    state = tmp_path / "state"
    result = invoke(
        repository, executable, state, budgets=TurnBudgets(3, 16000, 31, 37, 23)
    )
    process = as_mapping(EventJournal(state).read()[-1]["process"])
    assert process["stdout"] == {"retained_bytes": 31, "truncated": True}
    assert process["stderr"] == {"retained_bytes": 37, "truncated": True}
    assert process["final_message"] == {"retained_bytes": 23, "truncated": True}
    assert result.adapter_result.stdout.text == "x" * 31
    assert "x" * 31 not in EventJournal(state).path.read_text()


def test_read_only_sandbox_and_strict_utf8_validation(
    repository: Path,
    tmp_path: Path,
) -> None:
    from agent_harness.turn import run_observed_turn

    executable = fake(tmp_path, "import sys\nsys.stdin.buffer.read()\n")
    journal = EventJournal(tmp_path / "state")
    result = run_observed_turn(
        CodexCLIAdapter(executable),
        *inputs(repository),
        journal=journal,
        budgets=TurnBudgets(3, 16000, 100, 100, 100),
        sandbox="read-only",
    )
    assert not result.delta.modified
    assert journal.read()[0]["sandbox"] == "read-only"
    contract = TaskContract("invalid \ud800", (), (), ("done",))
    admission = record_admission(
        contract, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    before = journal.path.read_bytes()
    with pytest.raises(ValueError, match="UTF-8"):
        run_observed_turn(
            CodexCLIAdapter(executable),
            contract,
            admission,
            inputs(repository)[2],
            journal=journal,
            budgets=TurnBudgets(3, 16000, 100, 100, 100),
        )
    assert journal.path.read_bytes() == before


@pytest.mark.parametrize(
    "unsafe",
    [
        b".",
        b"..",
        b"../escape",
        b"/absolute",
        b".git/config",
        b"nested/.git/config",
        b"a//b",
        b"truncated-without-nul",
    ],
)
def test_unsafe_git_inventory_rejects_before_fingerprinting(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: bytes,
) -> None:
    import agent_harness.turn as turn

    original = turn._git

    def inventory(root: Path, *args: str) -> bytes:
        raw = original(root, *args)
        if args[:2] == ("ls-files", "--stage"):
            metadata = raw.partition(b"\t")[0]
            return (
                metadata
                + b"\t"
                + unsafe
                + (b"\0" if unsafe != b"truncated-without-nul" else b"")
            )
        return raw

    monkeypatch.setattr(turn, "_git", inventory)
    with pytest.raises(ValueError):
        turn.RepositoryObservation.capture(repository)


def test_ignored_special_files_are_excluded(repository: Path) -> None:
    from agent_harness.turn import RepositoryObservation

    (repository / ".gitignore").write_text("ignored/\nspecial\n")
    (repository / "ignored").mkdir()
    os.mkfifo(repository / "ignored" / "fifo")
    os.mkfifo(repository / "special")
    os.mkfifo(repository / ".git" / "unused-fifo")
    snapshot = RepositoryObservation.capture(repository)
    assert {entry.path for entry in snapshot.files} == {"base.txt", ".gitignore"}


def test_missing_restored_and_type_transition_are_net_file_changes(
    repository: Path,
) -> None:
    from agent_harness.turn import RepositoryObservation, compare_observations

    (repository / "base.txt").unlink()
    before = RepositoryObservation.capture(repository)
    (repository / "base.txt").write_bytes(b"initial")
    restored = RepositoryObservation.capture(repository)
    assert compare_observations(before, restored).added == ("base.txt",)
    (repository / "base.txt").unlink()
    (repository / "base.txt").symlink_to("initial")
    delta = compare_observations(restored, RepositoryObservation.capture(repository))
    assert delta.modified == ("base.txt",)
    assert not delta.added and not delta.deleted


@pytest.mark.parametrize("old_mode,new_mode", [(0o600, 0o666), (0o755, 0o777)])
def test_permission_only_changes_reach_scope(
    repository: Path, old_mode: int, new_mode: int
) -> None:
    from agent_harness.scope import evaluate_scope
    from agent_harness.turn import RepositoryObservation, compare_observations

    path = repository / "base.txt"
    path.chmod(old_mode)
    before = RepositoryObservation.capture(repository)
    path.chmod(new_mode)
    after = RepositoryObservation.capture(repository)
    assert before.status == after.status
    assert compare_observations(before, after).modified == ("base.txt",)
    decision = evaluate_scope(
        TaskContract("observe", (), ("base.txt",), ("done",)), before, after
    )
    assert decision.protected_paths == ("base.txt",)
    assert not decision.proceed


@pytest.mark.parametrize(
    "flags",
    [
        ("--skip-worktree",),
        ("--assume-unchanged",),
        ("--skip-worktree", "--assume-unchanged"),
    ],
)
def test_index_flags_change_observation_and_scope(
    repository: Path, flags: tuple[str, ...]
) -> None:
    from agent_harness.scope import evaluate_scope
    from agent_harness.turn import RepositoryObservation, compare_observations

    name = "tab\tnewline\n한글"
    (repository / name).write_bytes(b"content")
    git(repository, "add", "--", name)
    before = RepositoryObservation.capture(repository)
    previous = before
    for flag in flags:
        git(repository, "update-index", flag, "--", name)
        current = RepositoryObservation.capture(repository)
        assert current.index_sha256 != previous.index_sha256
        previous = current
    after = RepositoryObservation.capture(repository)
    assert before.status == after.status
    assert before.files == after.files
    assert compare_observations(before, after).index_changed
    assert not evaluate_scope(
        TaskContract("observe", (), (), ("done",)), before, after
    ).proceed
    for flag in reversed(flags):
        git(repository, "update-index", flag.replace("--", "--no-", 1), "--", name)
    assert RepositoryObservation.capture(repository) == before


@pytest.mark.parametrize("contents", [b"", b'{"event":"existing"}\n'])
def test_hardlinked_journal_rejected_before_turn(
    repository: Path, tmp_path: Path, contents: bytes
) -> None:
    target = repository / "external-target"
    target.write_bytes(contents)
    journal = EventJournal(tmp_path / "state")
    journal.path.parent.mkdir()
    os.link(target, journal.path)
    marker = tmp_path / "ran"
    executable = fake(
        tmp_path, f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n"
    )
    with pytest.raises(ValueError, match="link"):
        invoke(repository, executable, journal.path.parent)
    assert target.read_bytes() == contents
    assert not marker.exists()


@pytest.mark.parametrize("contents", [b"", b'{"event":"existing"}\n'])
def test_terminal_hardlink_refusal_is_typed_and_preserves_target(
    repository: Path, tmp_path: Path, contents: bytes
) -> None:
    from agent_harness.turn import TurnObservationError

    journal = EventJournal(tmp_path / "state")
    target = tmp_path / "target"
    target.write_bytes(contents)
    executable = fake(
        tmp_path,
        "import pathlib, os, sys\nsys.stdin.buffer.read()\n"
        f"pathlib.Path({str(journal.path)!r}).unlink()\n"
        f"os.link({str(target)!r}, {str(journal.path)!r})\n",
    )
    with pytest.raises(TurnObservationError) as raised:
        invoke(repository, executable, journal.path.parent)
    assert raised.value.code == "terminal_append_failed"
    assert not raised.value.terminal_recorded
    assert raised.value.adapter_result is not None
    assert target.read_bytes() == contents


@pytest.mark.parametrize("tracked", [True, False])
def test_same_bytes_and_mode_hardlink_cannot_produce_observation(
    repository: Path, tmp_path: Path, tracked: bool
) -> None:
    from agent_harness.turn import RepositoryObservation

    path = repository / ("base.txt" if tracked else "untracked")
    path.write_bytes(b"initial")
    path.chmod(0o600)
    before = RepositoryObservation.capture(repository)
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes())
    outside.chmod(0o600)
    path.unlink()
    os.link(outside, path)
    with pytest.raises(ValueError):
        RepositoryObservation.capture(repository)
    assert before.files
    assert outside.read_bytes() == b"initial"


def test_worktree_hardlink_refused_before_adapter_invocation(
    repository: Path, tmp_path: Path
) -> None:
    os.link(repository / "base.txt", tmp_path / "outside")
    marker = tmp_path / "ran"
    executable = fake(
        tmp_path, f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n"
    )
    journal = EventJournal(tmp_path / "state")
    with pytest.raises(ValueError):
        invoke(repository, executable, journal.path.parent)
    assert not marker.exists()
    assert not journal.path.exists()


def test_protected_hardlink_substitution_yields_typed_failure_not_scope_clear(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.scope import run_scoped_turn
    from agent_harness.turn import TurnObservationError

    target = repository / "base.txt"
    target.chmod(0o600)
    outside = tmp_path / "outside"
    outside.write_bytes(target.read_bytes())
    outside.chmod(0o600)
    executable = fake(
        tmp_path,
        "import os, pathlib, sys\nsys.stdin.buffer.read()\n"
        "pathlib.Path('base.txt').unlink()\n"
        f"os.link({str(outside)!r}, 'base.txt')\n",
    )
    policy = TaskContract("observe", ("added",), ("base.txt",), ("scope clear",))
    admission = record_admission(
        policy, approver="human", approved_at="2026-10-01T00:00:00Z"
    )
    context = build_context_packet(
        repository, source_paths=["base.txt"], budget_bytes=4096
    )
    journal = EventJournal(tmp_path / "state")
    with pytest.raises(TurnObservationError) as raised:
        run_scoped_turn(
            CodexCLIAdapter(executable),
            policy,
            admission,
            context,
            journal=journal,
            budgets=TurnBudgets(3, 16000, 128, 128, 128),
        )
    assert raised.value.code == "after_observation_failed"
    assert raised.value.terminal_recorded
    assert raised.value.adapter_result is not None
    assert raised.value.adapter_result.process_status == "successful_exit"
    assert [event["event"] for event in journal.read()] == [
        "turn_intent",
        "turn_observation_failed",
    ]
    assert target.read_bytes() == outside.read_bytes() == b"initial"


def test_observation_checks_opened_hardlink_and_closes_descriptor(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_harness.turn import RepositoryObservation

    target = repository / "base.txt"
    outside = tmp_path / "outside"
    outside.write_bytes(target.read_bytes())
    outside.chmod(target.stat().st_mode & 0o777)
    original = os.open
    opened: list[int] = []

    def substituted(
        path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if path == target:
            target.unlink()
            os.link(outside, target)
        descriptor = original(path, flags, mode, dir_fd=dir_fd)  # type: ignore[arg-type]
        if path == target:
            opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", substituted)
    with pytest.raises(ValueError):
        RepositoryObservation.capture(repository)
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


@pytest.mark.parametrize("change", ["switch", "detach"])
def test_same_commit_head_identity_change_is_not_scope_clear(
    repository: Path, change: str
) -> None:
    from agent_harness.scope import evaluate_scope
    from agent_harness.turn import RepositoryObservation, compare_observations

    before = RepositoryObservation.capture(repository)
    if change == "switch":
        git(repository, "switch", "-c", "other")
    else:
        git(repository, "switch", "--detach")
    after = RepositoryObservation.capture(repository)
    assert before.head == after.head
    assert compare_observations(before, after).head_changed
    assert not evaluate_scope(
        TaskContract("scope", (), (), ("verified",)), before, after
    ).proceed


@pytest.mark.parametrize("name", [".git", ".GIT"])
@pytest.mark.parametrize("kind", ["directory", "file", "symlink"])
def test_nested_git_entry_is_not_a_usable_observation(
    repository: Path, tmp_path: Path, name: str, kind: str
) -> None:
    from agent_harness.turn import RepositoryObservation

    parent = repository / "outside"
    parent.mkdir()
    entry = parent / name
    if kind == "directory":
        entry.mkdir()
        (entry / "payload").write_bytes(b"unobserved")
    elif kind == "file":
        entry.write_bytes(b"unobserved")
    else:
        entry.symlink_to(tmp_path / "external")
    with pytest.raises(ValueError):
        RepositoryObservation.capture(repository)


def test_linked_worktree_root_git_file_remains_observable(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.turn import RepositoryObservation

    linked = tmp_path / "linked"
    git(repository, "worktree", "add", "--detach", str(linked))
    assert (linked / ".git").is_file()
    observation = RepositoryObservation.capture(linked)
    assert observation.head == RepositoryObservation.capture(repository).head
    assert {entry.path for entry in observation.files} == {"base.txt"}


@pytest.mark.parametrize("location", ["exact", "ancestor", "descendant"])
def test_ignored_admitted_symlink_refused_before_native_write(
    repository: Path, tmp_path: Path, location: str
) -> None:
    from agent_harness.turn import RepositoryObservation, run_observed_turn

    (repository / ".gitignore").write_text("ignored/\n")
    ignored = repository / "ignored"
    ignored.mkdir()
    target = tmp_path / "external"
    if location == "ancestor":
        target.mkdir()
        victim = target / "payload"
        relative = "ignored/link/payload"
        allowed = relative
        link = ignored / "link"
    else:
        victim = target
        if location == "descendant":
            (ignored / "directory").mkdir()
            link = ignored / "directory/link"
            relative = "ignored/directory/link"
            allowed = "ignored/directory"
        else:
            link = ignored / "link"
            relative = "ignored/link"
            allowed = relative
    victim.write_bytes(b"original")
    link.symlink_to(target, target_is_directory=location == "ancestor")
    assert not any(
        entry.path.startswith("ignored/")
        for entry in RepositoryObservation.capture(repository).files
    )
    marker = tmp_path / "ran"
    executable = fake(
        tmp_path,
        "import pathlib, sys\nsys.stdin.buffer.read()\n"
        + f"pathlib.Path({str(marker)!r}).touch()\npathlib.Path({relative!r}).write_bytes(b'changed')\n",
    )
    task = TaskContract("admitted ignored path", (allowed,), (), ("verified",))
    admission = record_admission(
        task, approver="human", approved_at="2026-10-09T00:00:00Z"
    )
    context = build_context_packet(
        repository, source_paths=["base.txt"], budget_bytes=4096
    )
    journal = EventJournal(tmp_path / "state")
    with pytest.raises(ValueError, match="symlink"):
        run_observed_turn(
            CodexCLIAdapter(executable),
            task,
            admission,
            context,
            journal=journal,
            budgets=TurnBudgets(3, 16000, 128, 128, 128),
        )
    assert not marker.exists()
    assert victim.read_bytes() == b"original"
    assert not journal.path.exists()


@pytest.mark.parametrize("path_kind", ["missing", "ignored_regular"])
def test_allowed_path_inspection_preserves_missing_regular_and_unrelated_links(
    repository: Path, tmp_path: Path, path_kind: str
) -> None:
    from agent_harness.turn import run_observed_turn

    (repository / ".gitignore").write_text("ignored/\n")
    ignored = repository / "ignored"
    ignored.mkdir()
    external = tmp_path / "external"
    external.write_bytes(b"original")
    (ignored / "unrelated").symlink_to(external)
    allowed = "new/missing" if path_kind == "missing" else "ignored/regular"
    if path_kind == "ignored_regular":
        (repository / allowed).write_bytes(b"regular")
    marker = tmp_path / "ran"
    executable = fake(
        tmp_path,
        "import pathlib, sys\nsys.stdin.buffer.read()\n"
        + f"pathlib.Path({str(marker)!r}).touch()\n",
    )
    task = TaskContract("allowed inspection", (allowed,), (), ("verified",))
    result = run_observed_turn(
        CodexCLIAdapter(executable),
        task,
        record_admission(task, approver="human", approved_at="2026-10-09T00:00:00Z"),
        build_context_packet(repository, source_paths=["base.txt"], budget_bytes=4096),
        journal=EventJournal(tmp_path / "state"),
        budgets=TurnBudgets(3, 16000, 128, 128, 128),
    )
    assert result.adapter_result.process_status == "successful_exit"
    assert marker.exists()
    assert external.read_bytes() == b"original"
