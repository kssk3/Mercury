from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
from agent_harness.admission import record_admission
from agent_harness.context import ContextPacket
from agent_harness.contract import TaskContract
from agent_harness.journal import EventJournal
from agent_harness.native_output import NativeOutputError, run_stored_scoped_turn
from agent_harness.turn import RepositoryObservation


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = (tmp_path / "repo").resolve()
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=10)
    (root / "foo").write_text("initial")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, timeout=10)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=f@invalid",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
        timeout=10,
    )
    return root


def executable(tmp_path: Path) -> Path:
    path = tmp_path / "native"
    path.write_text(
        f"#!{sys.executable}\nimport sys,pathlib\nsys.stdin.buffer.read()\n"
        "print('raw')\npathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('final')\n"
    )
    path.chmod(0o700)
    return path


@pytest.mark.parametrize("raises", [False, True])
def test_redactor_scope_evidence_includes_callback_effects(
    repository: Path, tmp_path: Path, raises: bool
) -> None:
    from agent_harness.scope import evaluate_scope

    contract = TaskContract("Task", (), (), ("verified",))
    journal = EventJournal(tmp_path / "state")

    def callback(text: str) -> str:
        (repository / "foo").write_text("callback change")
        if raises:
            raise ValueError("callback failure")
        return text.replace("raw", "safe")

    args = (
        CodexCLIAdapter(executable(tmp_path)),
        contract,
        record_admission(
            contract, approver="human", approved_at="2026-10-10T00:00:00Z"
        ),
        ContextPacket(str(repository), 4096, ()),
    )
    if raises:
        with pytest.raises(NativeOutputError) as error:
            run_stored_scoped_turn(
                *args,
                journal=journal,
                budgets=TurnBudgets(2, 16000, 128, 128, 128),
                redactor=callback,
                max_output_bytes=128,
            )
        scoped = error.value.scoped_turn
        assert error.value.code == "redaction_failed"
    else:
        result = run_stored_scoped_turn(
            *args,
            journal=journal,
            budgets=TurnBudgets(2, 16000, 128, 128, 128),
            redactor=callback,
            max_output_bytes=128,
        )
        scoped = result.scoped_turn
        assert result.stdout.path.read_text() == "safe\n"
    assert scoped.observed_turn.after == RepositoryObservation.capture(repository)
    assert scoped.decision == evaluate_scope(
        contract, scoped.observed_turn.before, scoped.observed_turn.after
    )
    assert not scoped.decision.proceed
    assert scoped.decision.out_of_scope_paths == ("foo",)
    events = journal.read()
    observations = [event for event in events if event["event"] == "turn_observation"]
    decisions = [event for event in events if event["event"] == "scope_decision"]
    assert len(observations) == len(decisions) == 1
    assert (
        observations[0]["attempt_id"]
        == decisions[0]["attempt_id"]
        == scoped.observed_turn.attempt_id
    )
    corrected = [
        event for event in events if event["event"] == "callback_scope_decision"
    ]
    assert len(corrected) == 1 and corrected[0]["proceed"] is False


def test_loop_cannot_pass_after_outside_callback_mutation(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.loop import run_loop
    from agent_harness.policy import VerificationPolicy
    from agent_harness.retry import RetryLimits
    from agent_harness.state import CurrentStateStore

    contract = TaskContract("Task", (), (), ("verified",))
    command = (sys.executable, "-c", "pass")

    def callback(text: str) -> str:
        (repository / "foo").write_text("callback change")
        return text

    result = run_loop(
        CodexCLIAdapter(executable(tmp_path)),
        contract,
        record_admission(
            contract, approver="human", approved_at="2026-10-10T00:00:00Z"
        ),
        ContextPacket(str(repository), 4096, ()),
        policy=VerificationPolicy((command,), ".", 2),
        criterion_commands={"verified": (command,)},
        journal=EventJournal(tmp_path / "state"),
        state_store=CurrentStateStore(tmp_path / "state"),
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
        limits=RetryLimits(0, 1, 8, 30),
        redactor=callback,
        max_output_bytes=128,
    )
    assert result.status != "pass"


def test_case_sensitive_ignorecase_inventory_retains_collision(
    repository: Path,
) -> None:
    from agent_harness.scope import evaluate_scope

    sibling = repository / "FOO"
    if sibling.exists():
        pytest.skip("requires a case-sensitive filesystem; hosted Linux runs this case")
    subprocess.run(
        ["git", "-C", str(repository), "config", "core.ignorecase", "true"],
        check=True,
        timeout=10,
    )
    before = RepositoryObservation.capture(repository)
    sibling.write_text("outside")
    after = RepositoryObservation.capture(repository)
    assert "FOO" in {entry.path for entry in after.files}
    decision = evaluate_scope(
        TaskContract("Task", ("foo",), (), ("verified",)), before, after
    )
    assert not decision.proceed
    assert decision.out_of_scope_paths == ("FOO",)


def test_allowed_callback_completion_reconciles_authoritative_boundary(
    repository: Path, tmp_path: Path
) -> None:
    import hashlib

    from agent_harness.interruption import report_interruption
    from agent_harness.loop import run_loop
    from agent_harness.policy import VerificationPolicy
    from agent_harness.retry import RetryLimits
    from agent_harness.startup import reconcile_startup
    from agent_harness.state import CurrentStateStore

    contract = TaskContract("Task", ("foo",), (), ("verified",))
    command = (sys.executable, "-c", "pass")
    journal = EventJournal(tmp_path / "state")
    state = CurrentStateStore(tmp_path / "state")

    def callback(text: str) -> str:
        (repository / "foo").write_text("accepted callback")
        return text

    result = run_loop(
        CodexCLIAdapter(executable(tmp_path)),
        contract,
        record_admission(
            contract, approver="human", approved_at="2026-10-10T00:00:00Z"
        ),
        ContextPacket(str(repository), 4096, ()),
        policy=VerificationPolicy((command,), ".", 2),
        criterion_commands={"verified": (command,)},
        journal=journal,
        state_store=state,
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
        limits=RetryLimits(0, 1, 8, 30),
        redactor=callback,
        max_output_bytes=128,
        checkpoint_verification=True,
    )
    assert result.status == "pass"
    digest = hashlib.sha256(contract.to_json().encode()).hexdigest()
    assert (
        reconcile_startup(
            repository,
            journal=journal,
            state_store=state,
            expected_contract_sha256=digest,
        ).status
        == "completed"
    )
    assert (
        report_interruption(
            repository,
            journal=journal,
            state_store=state,
            expected_contract_sha256=digest,
        ).evidence.status
        == "completed"
    )
    (repository / "foo").write_text("later edit")
    changed = reconcile_startup(
        repository, journal=journal, state_store=state, expected_contract_sha256=digest
    )
    assert changed.status == "interrupted"
    assert changed.workspace_delta is not None and changed.workspace_delta.modified == (
        "foo",
    )


@pytest.mark.parametrize("mutation", ["fifo", "git_missing"])
@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt, SystemExit])
def test_unobservable_callback_preserves_original_failure_and_identity(
    repository: Path, tmp_path: Path, mutation: str, error_type: type[BaseException]
) -> None:
    import os

    from agent_harness.attempt import AttemptJournal
    from agent_harness.state import CurrentStateStore

    original = error_type("caller failure")
    journal = EventJournal(tmp_path / "state")
    state = CurrentStateStore(tmp_path / "state")
    contract = TaskContract("Task", ("foo",), (), ("verified",))

    def callback(text: str) -> str:
        if mutation == "fifo":
            (repository / "foo").unlink()
            os.mkfifo(repository / "foo")
        else:
            (repository / ".git").rename(repository / "removed-git")
        raise original

    expected = NativeOutputError if error_type is ValueError else error_type
    with pytest.raises(expected) as error:
        run_stored_scoped_turn(
            CodexCLIAdapter(executable(tmp_path)),
            contract,
            record_admission(
                contract, approver="human", approved_at="2026-10-10T00:00:00Z"
            ),
            ContextPacket(str(repository), 4096, ()),
            journal=journal,
            budgets=TurnBudgets(2, 16000, 128, 128, 128),
            redactor=callback,
            max_output_bytes=128,
        )
    events = journal.read()
    identity = events[0]["attempt_id"]
    if isinstance(error.value, NativeOutputError):
        assert error.value.code == "redaction_failed"
        assert error.value.scoped_turn.observed_turn.attempt_id == identity
    else:
        assert error.value is original
    failed = [
        event for event in events if event["event"] == "callback_observation_failed"
    ]
    assert len(failed) == 1 and failed[0]["attempt_id"] == identity
    assert "after" not in failed[0]
    if mutation == "git_missing":
        (repository / "removed-git").rename(repository / ".git")
    entry = AttemptJournal(repository, journal=journal, state_store=state).read()[0]
    assert entry["attempt_id"] == identity
    assert entry["observation"]["status"] == "failed"  # type: ignore[index]
    assert entry["scope"] is None and entry["after_head"] is None


@pytest.mark.parametrize("mutation", ["fifo", "git_missing"])
def test_unobservable_callback_loop_keeps_known_launch_accounting(
    repository: Path, tmp_path: Path, mutation: str
) -> None:
    import hashlib
    import os

    from agent_harness.loop import run_loop
    from agent_harness.policy import VerificationPolicy
    from agent_harness.retry import RetryLimits
    from agent_harness.startup import reconcile_startup
    from agent_harness.state import CurrentStateStore

    contract = TaskContract("Task", ("foo",), (), ("verified",))
    command = (sys.executable, "-c", "pass")
    journal = EventJournal(tmp_path / "state")
    state = CurrentStateStore(tmp_path / "state")

    def callback(text: str) -> str:
        if mutation == "fifo":
            (repository / "foo").unlink()
            os.mkfifo(repository / "foo")
        else:
            (repository / ".git").rename(repository / "removed-git")
        raise ValueError("callback failure")

    result = run_loop(
        CodexCLIAdapter(executable(tmp_path)),
        contract,
        record_admission(
            contract, approver="human", approved_at="2026-10-10T00:00:00Z"
        ),
        ContextPacket(str(repository), 4096, ()),
        policy=VerificationPolicy((command,), ".", 2),
        criterion_commands={"verified": (command,)},
        journal=journal,
        state_store=state,
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
        limits=RetryLimits(0, 1, 8, 30),
        redactor=callback,
        max_output_bytes=128,
    )
    events = journal.read()
    identity = next(
        event["attempt_id"] for event in events if event["event"] == "turn_intent"
    )
    assert result.status == "unknown" and result.reason == "redaction_failed"
    assert result.attempt_ids == (identity,) and result.usage.commands >= 1
    if mutation == "git_missing":
        (repository / "removed-git").rename(repository / ".git")
    decision = reconcile_startup(
        repository,
        journal=journal,
        state_store=state,
        expected_contract_sha256=hashlib.sha256(
            contract.to_json().encode()
        ).hexdigest(),
    )
    assert decision.status != "completed" and decision.attempt_ids == (identity,)
    assert decision.workspace_delta is None


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_callback_cancellation_survives_unobservable_workspace_and_journal_failure(
    repository: Path, tmp_path: Path, error_type: type[BaseException]
) -> None:
    import os

    contract = TaskContract("Task", ("foo",), (), ("verified",))
    journal = EventJournal(tmp_path / "state")
    original = error_type("cancel")

    def callback(text: str) -> str:
        (repository / "foo").unlink()
        os.mkfifo(repository / "foo")
        journal.path.write_text("incomplete journal")
        raise original

    with pytest.raises(error_type) as error:
        run_stored_scoped_turn(
            CodexCLIAdapter(executable(tmp_path)),
            contract,
            record_admission(
                contract, approver="human", approved_at="2026-10-10T00:00:00Z"
            ),
            ContextPacket(str(repository), 4096, ()),
            journal=journal,
            budgets=TurnBudgets(2, 16000, 128, 128, 128),
            redactor=callback,
            max_output_bytes=128,
        )
    assert error.value is original
