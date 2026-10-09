from __future__ import annotations

import os
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agent_harness.contract import TaskContract
from agent_harness.journal import EventJournal

if TYPE_CHECKING:
    from agent_harness.scope import ScopedTurnResult
from agent_harness.turn import RepositoryObservation


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
    root = (tmp_path / "repository").resolve()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / "base.txt").write_bytes(b"initial")
    git(root, "add", ".")
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


def contract(*allowed: str, protected: tuple[str, ...] = ()) -> TaskContract:
    return TaskContract("Scope only", allowed, protected, ("Separately verify",))


def test_unchanged_dirty_baseline_is_clear_and_decision_is_immutable(
    repository: Path,
) -> None:
    from agent_harness.scope import evaluate_scope

    (repository / "base.txt").write_bytes(b"already dirty")
    (repository / "untracked").write_bytes(b"unchanged")
    git(repository, "add", "base.txt")
    before = RepositoryObservation.capture(repository)
    decision = evaluate_scope(
        contract(), before, RepositoryObservation.capture(repository)
    )
    assert decision.proceed
    assert not decision.out_of_scope_paths and not decision.protected_paths
    with pytest.raises(FrozenInstanceError):
        decision.out_of_scope_paths = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    "path,allowed,protected,clear,is_protected",
    [
        ("src/file", ("src",), (), True, False),
        ("src", ("src",), (), True, False),
        ("src-other", ("src",), (), False, False),
        ("SRC/file", ("src",), (), False, False),
        ("src/file", ("src/*",), (), False, False),
        ("src/*", ("src/*",), (), True, False),
        ("guard/File", (), ("GUARD",), False, True),
        ("guard-other", ("guard-other",), ("guard",), True, False),
        ("guard", (), ("GUARD",), False, True),
        ("한글\nfile", ("한글\nfile",), (), True, False),
    ],
)
def test_literal_boundaries_and_protected_casefold(
    repository: Path,
    path: str,
    allowed: tuple[str, ...],
    protected: tuple[str, ...],
    clear: bool,
    is_protected: bool,
) -> None:
    from agent_harness.scope import evaluate_scope

    before = RepositoryObservation.capture(repository)
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"added")
    decision = evaluate_scope(
        contract(*allowed, protected=protected),
        before,
        RepositoryObservation.capture(repository),
    )
    assert decision.proceed is clear
    assert decision.protected_paths == ((path,) if is_protected else ())
    assert decision.out_of_scope_paths == (() if clear or is_protected else (path,))


def test_dirty_rename_delete_mode_and_link_net_effects_are_checked(
    repository: Path,
) -> None:
    from agent_harness.scope import evaluate_scope

    for name in ("rename", "deleted", "mode", "same"):
        (repository / name).write_bytes(b"original")
    (repository / "link").symlink_to("old-target")
    git(repository, "add", ".")
    (repository / "base.txt").write_bytes(b"preexisting dirty")
    (repository / "untracked").write_bytes(b"before")
    before = RepositoryObservation.capture(repository)
    (repository / "base.txt").write_bytes(b"additional dirty")
    (repository / "untracked").write_bytes(b"after")
    (repository / "rename").rename(repository / "renamed")
    (repository / "deleted").unlink()
    (repository / "mode").chmod(0o755)
    (repository / "link").unlink()
    (repository / "link").symlink_to("new-target")
    (repository / "same").unlink()
    (repository / "same").write_bytes(b"original")
    decision = evaluate_scope(
        contract("renamed", "deleted", "mode"),
        before,
        RepositoryObservation.capture(repository),
    )
    assert not decision.proceed
    assert set(decision.out_of_scope_paths) == {
        "base.txt",
        "untracked",
        "rename",
        "link",
    }
    assert decision.delta.added == ("renamed",)
    assert set(decision.delta.modified) == {"base.txt", "untracked", "mode", "link"}
    assert decision.delta.deleted == ("deleted", "rename")


@pytest.mark.parametrize("signal", ["index", "head", "status"])
def test_git_only_signals_block_without_file_effects(
    repository: Path, signal: str
) -> None:
    from dataclasses import replace

    from agent_harness.scope import evaluate_scope
    from agent_harness.turn import GitStatusEntry

    (repository / "base.txt").write_bytes(b"dirty")
    before = RepositoryObservation.capture(repository)
    if signal == "index":
        git(repository, "add", "base.txt")
        after = RepositoryObservation.capture(repository)
    elif signal == "head":
        git(
            repository,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "--quiet",
            "-m",
            "agent commit",
        )
        after = RepositoryObservation.capture(repository)
    else:
        after = replace(before, status=(GitStatusEntry("base.txt", " D"),))
    decision = evaluate_scope(contract("base.txt"), before, after)
    assert not decision.proceed
    assert not decision.out_of_scope_paths and not decision.protected_paths
    assert (
        not decision.delta.added
        and not decision.delta.modified
        and not decision.delta.deleted
    )
    assert decision.delta.index_changed is (signal == "index")
    assert decision.delta.head_changed is (signal == "head")
    assert decision.unexplained_status_change is (signal == "status")


@pytest.mark.parametrize(
    "invalid",
    [
        "snapshot_type",
        "root",
        "identity",
        "other_root",
        "files_type",
        "file_type",
        "duplicate_file",
        "unsafe_path",
        "dotgit",
        "absolute_path",
        "kind",
        "digest",
        "size",
        "bool_size",
        "mode",
        "missing_metadata",
        "symlink_mode",
        "head",
        "index",
        "status_type",
        "status_entry",
        "duplicate_status",
        "status_path",
        "status_code",
        "status_missing_file",
        "contract_type",
        "contract_path",
        "contract_entry",
    ],
)
def test_invalid_inputs_never_clear(repository: Path, invalid: str) -> None:
    from dataclasses import replace

    from agent_harness.scope import evaluate_scope
    from agent_harness.turn import GitStatusEntry

    before = RepositoryObservation.capture(repository)
    after = before
    task = contract()
    entry = before.files[0]
    if invalid == "snapshot_type":
        after = None  # type: ignore[assignment]
    elif invalid == "root":
        after = replace(before, repository_root=str(repository) + "/../repository")
    elif invalid == "identity":
        after = replace(before, repository_id="a" * 64)
    elif invalid == "other_root":
        after = RepositoryObservation.capture(repository.parent / "repository")
        after = replace(after, repository_root=str(repository.parent))
    elif invalid == "files_type":
        after = replace(before, files=list(before.files))  # type: ignore[arg-type]
    elif invalid == "file_type":
        after = replace(before, files=(None,))  # type: ignore[arg-type]
    elif invalid == "duplicate_file":
        after = replace(before, files=(entry, entry))
    elif invalid in {"unsafe_path", "dotgit", "absolute_path"}:
        path = {
            "unsafe_path": "a/../b",
            "dotgit": ".GiT/config",
            "absolute_path": "/outside",
        }[invalid]
        after = replace(before, files=(replace(entry, path=path),))
    elif invalid == "kind":
        after = replace(before, files=(replace(entry, kind="directory"),))  # type: ignore[arg-type]
    elif invalid == "digest":
        after = replace(before, files=(replace(entry, sha256="not digest"),))
    elif invalid == "size":
        after = replace(before, files=(replace(entry, size_bytes=-1),))
    elif invalid == "bool_size":
        after = replace(before, files=(replace(entry, size_bytes=True),))
    elif invalid == "mode":
        after = replace(before, files=(replace(entry, executable=1),))  # type: ignore[arg-type]
    elif invalid == "missing_metadata":
        after = replace(before, files=(replace(entry, kind="missing"),))
    elif invalid == "symlink_mode":
        after = replace(before, files=(replace(entry, kind="symlink"),))
    elif invalid == "head":
        after = replace(before, head="malformed")
    elif invalid == "index":
        after = replace(before, index_sha256="malformed")
    elif invalid == "status_type":
        after = replace(before, status=[])  # type: ignore[arg-type]
    elif invalid == "status_entry":
        after = replace(before, status=(None,))  # type: ignore[arg-type]
    elif invalid == "duplicate_status":
        after = replace(before, status=(GitStatusEntry("base.txt", " M"),) * 2)
    elif invalid == "status_path":
        after = replace(before, status=(GitStatusEntry("../bad", " M"),))
    elif invalid == "status_code":
        after = replace(before, status=(GitStatusEntry("base.txt", "ZZ"),))
    elif invalid == "status_missing_file":
        after = replace(before, status=(GitStatusEntry("unknown", "??"),))
    elif invalid == "contract_type":
        task = None  # type: ignore[assignment]
    elif invalid == "contract_path":
        object.__setattr__(task, "allowed_paths", (".git/config",))
    else:
        object.__setattr__(task, "allowed_paths", (None,))
    with pytest.raises((TypeError, ValueError)):
        evaluate_scope(task, before, after)


def test_regular_to_symlink_and_restored_missing_are_scope_effects(
    repository: Path,
) -> None:
    from agent_harness.scope import evaluate_scope

    (repository / "restored").write_bytes(b"tracked")
    git(repository, "add", "restored")
    (repository / "restored").unlink()
    before = RepositoryObservation.capture(repository)
    (repository / "base.txt").unlink()
    (repository / "base.txt").symlink_to("outside-target")
    (repository / "restored").write_bytes(b"restored")
    decision = evaluate_scope(
        contract("restored"), before, RepositoryObservation.capture(repository)
    )
    assert decision.delta.added == ("restored",)
    assert decision.delta.modified == ("base.txt",)
    assert decision.out_of_scope_paths == ("base.txt",)
    assert not decision.proceed


def test_staged_deletion_status_may_reference_absent_inventory(
    repository: Path,
) -> None:
    from agent_harness.scope import evaluate_scope

    before = RepositoryObservation.capture(repository)
    git(repository, "rm", "--quiet", "base.txt")
    after = RepositoryObservation.capture(repository)
    decision = evaluate_scope(contract("base.txt"), before, after)
    assert decision.delta.deleted == ("base.txt",)
    assert decision.delta.index_changed
    assert not decision.proceed
    assert not decision.out_of_scope_paths


def fake(tmp_path: Path, body: str) -> Path:
    import sys

    executable = tmp_path / "fake-codex"
    executable.write_text(f"#!{sys.executable}\n" + body)
    executable.chmod(0o700)
    return executable


def invoke(
    repository: Path,
    executable: Path,
    journal: EventJournal,
    *,
    task: TaskContract | None = None,
) -> ScopedTurnResult:
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.admission import record_admission
    from agent_harness.context import build_context_packet
    from agent_harness.scope import run_scoped_turn

    task = task if task is not None else contract("base.txt", "added")
    return run_scoped_turn(
        CodexCLIAdapter(executable),
        task,
        record_admission(task, approver="human", approved_at="2026-10-02T00:00:00Z"),
        build_context_packet(repository, source_paths=["base.txt"], budget_bytes=4096),
        journal=journal,
        budgets=TurnBudgets(3, 16000, 128, 128, 128),
    )


@pytest.mark.parametrize(
    "status", ["successful_exit", "nonzero_exit", "timeout", "launch_error"]
)
@pytest.mark.parametrize("violate", [False, True])
def test_scoped_turn_evaluates_every_returned_process_status_before_return(
    repository: Path, tmp_path: Path, status: str, violate: bool
) -> None:
    journal = EventJournal(tmp_path / "state")
    journal.append({"event": "previous"})
    body = "import pathlib, sys, time\nsys.stdin.buffer.read()\n"
    if status != "launch_error":
        body += "pathlib.Path('base.txt').write_bytes(b'SECRET MUTATION')\n"
        body += f"pathlib.Path({'outside' if violate else 'added'!r}).write_text('SECRET SOURCE')\n"
        body += "print('SECRET STDOUT')\nprint('SECRET STDERR', file=sys.stderr)\n"
        body += "pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('SECRET FINAL')\n"
    if status == "nonzero_exit":
        body += "sys.exit(7)\n"
    elif status == "timeout":
        body += "time.sleep(30)\n"
    executable = (
        tmp_path / "missing" if status == "launch_error" else fake(tmp_path, body)
    )
    result = invoke(repository, executable, journal)
    assert result.observed_turn.adapter_result.process_status == status
    assert result.decision.proceed is (not violate or status == "launch_error")
    assert result.decision.out_of_scope_paths == (
        ("outside",) if violate and status != "launch_error" else ()
    )
    events = journal.read()
    assert [event["event"] for event in events] == [
        "previous",
        "turn_intent",
        "turn_observation",
        "scope_decision",
    ]
    assert (
        events[-1]["attempt_id"]
        == events[-2]["attempt_id"]
        == result.observed_turn.attempt_id
    )
    assert events[-1]["proceed"] is result.decision.proceed
    assert events[-1]["repository_id"] == result.observed_turn.after.repository_id
    assert events[0] == {"event": "previous"}
    for secret in (
        "Scope only",
        "Separately verify",
        "SECRET SOURCE",
        "SECRET STDOUT",
        "SECRET STDERR",
        "SECRET FINAL",
        "SECRET MUTATION",
    ):
        assert secret not in journal.path.read_text()
    with pytest.raises(FrozenInstanceError):
        result.decision = result.decision  # type: ignore[misc]


@pytest.mark.parametrize("boundary", ["append", "history", "symlink", "evaluation"])
def test_scope_terminal_failure_is_fixed_safe_nonproceed_and_preserves_f2(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    from collections.abc import Mapping

    from agent_harness import scope

    class BoundaryJournal(EventJournal):
        def append(self, event: Mapping[str, object]) -> None:
            if event["event"] == "scope_decision" and boundary == "append":
                raise OSError("SECRET EXCEPTION")
            super().append(event)
            if event["event"] == "turn_observation":
                if boundary == "history":
                    with self.path.open("a") as stream:
                        stream.write('{"partial":')
                elif boundary == "symlink":
                    state = self.path.parent
                    state.rename(tmp_path / "saved-state")
                    (repository / "redirect").mkdir()
                    state.symlink_to(repository / "redirect", target_is_directory=True)

    if boundary == "evaluation":

        def fail(*args: object) -> object:
            raise ValueError("SECRET EXCEPTION")

        monkeypatch.setattr(scope, "evaluate_scope", fail)
    journal = BoundaryJournal(tmp_path / "state")
    executable = fake(
        tmp_path,
        "import pathlib, sys\nsys.stdin.buffer.read()\npathlib.Path('base.txt').write_text('changed')\nprint('SECRET OUTPUT')\n",
    )
    with pytest.raises(scope.ScopeTurnError) as failure:
        invoke(repository, executable, journal)
    expected = (
        "evaluation_failed" if boundary == "evaluation" else "decision_append_failed"
    )
    assert failure.value.code == expected
    assert str(failure.value) == expected
    assert not failure.value.proceed
    assert failure.value.observed_turn.delta.modified == ("base.txt",)
    preserved = (
        tmp_path / "saved-state" / "events.jsonl"
        if boundary == "symlink"
        else journal.path
    )
    raw = preserved.read_text()
    assert '"turn_intent"' in raw and '"turn_observation"' in raw
    assert '"scope_decision"' not in raw
    assert "SECRET EXCEPTION" not in raw and "SECRET OUTPUT" not in raw
    assert not (repository / "redirect" / "events.jsonl").exists()


@pytest.mark.parametrize("failure", ["after_observation", "terminal_append"])
def test_incomplete_f2_observation_propagates_and_never_scope_clears(
    repository: Path, tmp_path: Path, failure: str
) -> None:
    from collections.abc import Mapping

    from agent_harness.turn import TurnObservationError

    class FailObservationJournal(EventJournal):
        def append(self, event: Mapping[str, object]) -> None:
            if event["event"] == "turn_observation" and failure == "terminal_append":
                raise OSError("SECRET EXCEPTION")
            super().append(event)

    body = "import os, sys\nsys.stdin.buffer.read()\n"
    if failure == "after_observation":
        body += "os.mkfifo('unsupported')\n"
    journal = FailObservationJournal(tmp_path / "state")
    with pytest.raises(TurnObservationError) as error:
        invoke(repository, fake(tmp_path, body), journal)
    assert error.value.code == (
        "after_observation_failed"
        if failure == "after_observation"
        else "terminal_append_failed"
    )
    assert all(event["event"] != "scope_decision" for event in journal.read())


def test_unsafe_contract_path_rejects_before_process_or_intent(
    repository: Path, tmp_path: Path
) -> None:
    task = contract(".git/config")
    marker = tmp_path / "ran"
    executable = fake(
        tmp_path, f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n"
    )
    journal = EventJournal(tmp_path / "state")
    with pytest.raises(ValueError):
        invoke(repository, executable, journal, task=task)
    assert not marker.exists() and not journal.path.exists()


def test_cached_deletion_can_have_two_distinct_statuses_for_one_path(
    repository: Path,
) -> None:
    from agent_harness.scope import evaluate_scope

    before = RepositoryObservation.capture(repository)
    git(repository, "rm", "--cached", "--quiet", "base.txt")
    after = RepositoryObservation.capture(repository)
    assert {entry.status for entry in after.status if entry.path == "base.txt"} == {
        "D ",
        "??",
    }
    decision = evaluate_scope(contract("base.txt"), before, after)
    assert decision.delta.index_changed
    assert not decision.delta.deleted and not decision.delta.modified
    assert not decision.proceed and not decision.out_of_scope_paths


@pytest.mark.parametrize(
    "field,value",
    [
        ("goal", 1),
        ("goal", " "),
        ("completion_criteria", ()),
        ("completion_criteria", (1,)),
        ("completion_criteria", ["done"]),
        ("allowed_paths", ["base.txt"]),
        ("protected_paths", ("base.txt",)),
    ],
)
def test_forged_invalid_contract_never_clear_or_launch(
    repository: Path, tmp_path: Path, field: str, value: object
) -> None:
    from agent_harness.scope import evaluate_scope

    task = contract("base.txt")
    object.__setattr__(task, field, value)
    before = RepositoryObservation.capture(repository)
    with pytest.raises((TypeError, ValueError)):
        evaluate_scope(task, before, before)
    marker = tmp_path / "ran"
    journal = EventJournal(tmp_path / "state")
    executable = fake(
        tmp_path, f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n"
    )
    with pytest.raises((TypeError, ValueError)):
        invoke(repository, executable, journal, task=task)
    assert not marker.exists() and not journal.path.exists()


@pytest.mark.parametrize(
    "mode,kind,executable",
    [
        (True, "regular", False),
        (False, "regular", False),
        (420.0, "regular", False),
        (-1, "regular", True),
        (0o10000, "regular", False),
        (0o644, "regular", True),
        (0o755, "regular", False),
        (0o644, "symlink", None),
        (0, "missing", None),
    ],
)
def test_supplied_permission_mode_must_be_consistent(
    repository: Path, mode: object, kind: str, executable: bool | None
) -> None:
    from dataclasses import replace
    from typing import Any, cast

    from agent_harness.scope import evaluate_scope

    snapshot = RepositoryObservation.capture(repository)
    entry = replace(
        snapshot.files[0],
        mode=cast(Any, mode),
        kind=cast(Any, kind),
        executable=executable,
    )
    if kind == "missing":
        entry = replace(entry, sha256=None, size_bytes=None)
    malformed = replace(snapshot, files=(entry,))

    with pytest.raises(ValueError, match="observation"):
        evaluate_scope(contract("base.txt"), malformed, malformed)


@pytest.mark.parametrize("mode", [None, 0, 0o600, 0o755, 0o7777])
def test_legacy_and_valid_permission_modes_remain_usable(
    repository: Path, mode: int | None
) -> None:
    from dataclasses import replace

    from agent_harness.scope import evaluate_scope

    snapshot = RepositoryObservation.capture(repository)
    entry = replace(
        snapshot.files[0], mode=mode, executable=bool(mode & 0o111) if mode else False
    )
    compatible = replace(snapshot, files=(entry,))

    assert evaluate_scope(contract(), compatible, compatible).proceed
