from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import Literal, cast

import pytest

from agent_harness.adapter import CapturedText, CodexTurnResult, TurnBudgets
from agent_harness.admission import AdmissionRecord, record_admission
from agent_harness.context import ContextPacket
from agent_harness.contract import TaskContract
from agent_harness.journal import EventJournal
from agent_harness.loop import run_loop
from agent_harness.policy import VerificationPolicy
from agent_harness.retry import RetryLimits
from agent_harness.state import CurrentStateStore
from agent_harness.turn import TurnObservationError, run_observed_turn


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], env=env, check=True)
    root = root.resolve()
    (root / "value").write_text("old")
    for args in [
        ["add", "value"],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
    ]:
        subprocess.run(["/usr/bin/git", "-C", str(root), *args], env=env, check=True)
    return root


def inputs(root: Path) -> tuple[TaskContract, AdmissionRecord, ContextPacket]:
    task = TaskContract("change value", ("value",), (), ("new value",))
    return (
        task,
        record_admission(task, approver="human", approved_at="2026-10-17T00:00:00Z"),
        ContextPacket(str(root), 4096, ()),
    )


class StructuralAdapter:
    """Trusted synthetic backend; deliberately does not inherit the Codex adapter."""

    def __init__(self) -> None:
        self.prepared: list[Path] = []
        self.sandboxes: list[str] = []
        self.refuse = False
        self.malformed = ""
        self.status: Literal["successful_exit", "timeout", "launch_error"] = (
            "successful_exit"
        )

    def prepare(self, repository_root: Path) -> None:
        if self.refuse:
            raise ValueError("prepare_refused")
        self.prepared.append(repository_root)

    def run(
        self,
        contract: TaskContract,
        admission: AdmissionRecord,
        context: ContextPacket,
        *,
        budgets: TurnBudgets,
        sandbox: Literal["read-only", "workspace-write"] = "workspace-write",
    ) -> CodexTurnResult:
        self.sandboxes.append(sandbox)
        root = Path(context.repository_root)
        if self.status == "successful_exit" and sandbox == "workspace-write":
            (root / "value").write_text("new")
        text = CapturedText("private payload", 15, False)
        result = CodexTurnResult(
            ("synthetic-agent",),
            str(root),
            self.status,
            0
            if self.status == "successful_exit"
            else None
            if self.status == "launch_error"
            else -9,
            0.01,
            text,
            text,
            text,
            "available",
        )
        if self.malformed == "type":
            return cast(CodexTurnResult, "private payload")
        if self.malformed == "root":
            return replace(result, repository_root=str(root.parent))
        if self.malformed == "capture":
            return replace(result, stdout=CapturedText("private payload", -1, False))
        return result


def test_structural_adapter_runs_observed_turn(
    repository: Path, tmp_path: Path
) -> None:
    adapter = StructuralAdapter()
    journal = EventJournal(tmp_path / "state")
    result = None
    try:
        result = run_observed_turn(
            adapter,
            *inputs(repository),
            journal=journal,
            budgets=TurnBudgets(2, 16000, 128, 128, 128),
        )
    except TypeError as error:
        assert "CodexCLIAdapter" in str(error)
    assert result is not None, (
        "structural adapter must be admitted without Codex inheritance"
    )
    assert result.delta.modified == ("value",)
    assert adapter.prepared == [repository]
    assert [event["event"] for event in journal.read()] == [
        "turn_intent",
        "turn_observation",
    ]
    assert "private payload" not in journal.path.read_text()


def test_structural_adapter_completes_verified_loop(
    repository: Path, tmp_path: Path
) -> None:
    adapter = StructuralAdapter()
    launches = tmp_path / "checks"
    command = (
        sys.executable,
        "-c",
        f"from pathlib import Path; p=Path({str(launches)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x'); assert Path('value').read_text()=='new'",
    )
    journal = EventJournal(tmp_path / "state")
    result = run_loop(
        adapter,
        *inputs(repository),
        policy=VerificationPolicy((command,), ".", 2),
        criterion_commands={"new value": (command,)},
        journal=journal,
        state_store=CurrentStateStore(tmp_path / "state"),
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
        limits=RetryLimits(0, 1, 4, 50),
        redactor=lambda text: text.replace("private", "redacted"),
        max_output_bytes=128,
    )
    assert result.status == "pass", (
        "structural backend must reach independent final verification"
    )
    assert launches.read_text() == "xxx"  # baseline, development, distinct final
    assert result.usage.commands == 4
    assert len(adapter.sandboxes) == 1
    assert adapter.prepared == [repository]
    assert any(event["event"] == "scope_decision" for event in journal.read())
    assert any(event["event"] == "native_output_stored" for event in journal.read())
    assert result.criteria and all(item.passed for item in result.criteria)


def test_legacy_types_keep_identity() -> None:
    from agent_harness.agent import AgentTurnResult
    from agent_harness.agent import CapturedText as CommonText
    from agent_harness.agent import TurnBudgets as CommonBudgets

    assert CodexTurnResult is AgentTurnResult
    assert CapturedText is CommonText
    assert TurnBudgets is CommonBudgets
    value = CodexTurnResult(
        (),
        "/fixture",
        "launch_error",
        None,
        0,
        CapturedText("", 0, False),
        CapturedText("", 0, False),
        None,
        "missing",
    )
    assert isinstance(value, AgentTurnResult)


def test_prepare_refusal_precedes_intent_and_run(
    repository: Path, tmp_path: Path
) -> None:
    adapter = StructuralAdapter()
    adapter.refuse = True
    journal = EventJournal(tmp_path / "state")
    with pytest.raises(ValueError, match="^prepare_refused$"):
        run_observed_turn(
            adapter,
            *inputs(repository),
            journal=journal,
            budgets=TurnBudgets(2, 16000, 128, 128, 128),
        )
    assert not journal.read() and not adapter.sandboxes
    assert (repository / "value").read_text() == "old"


@pytest.mark.parametrize("missing", ["prepare", "run"])
def test_incomplete_adapter_is_rejected_before_intent(
    repository: Path, tmp_path: Path, missing: str
) -> None:
    from agent_harness.agent import AgentAdapter

    class Incomplete:
        pass

    incomplete = Incomplete()
    calls: list[object] = []
    setattr(
        incomplete,
        "run" if missing == "prepare" else "prepare",
        lambda *args, **kwargs: calls.append(args),
    )
    journal = EventJournal(tmp_path / "state")
    with pytest.raises(TypeError, match="adapter"):
        run_observed_turn(
            cast(AgentAdapter, incomplete),
            *inputs(repository),
            journal=journal,
            budgets=TurnBudgets(2, 16000, 128, 128, 128),
        )
    assert not calls and not journal.read()


@pytest.mark.parametrize("malformed", ["type", "root", "capture"])
def test_malformed_result_is_unknown_without_payload_or_launch_claim(
    repository: Path, tmp_path: Path, malformed: str
) -> None:
    adapter = StructuralAdapter()
    adapter.malformed = malformed
    journal = EventJournal(tmp_path / "state")
    with pytest.raises(TurnObservationError) as raised:
        run_observed_turn(
            adapter,
            *inputs(repository),
            journal=journal,
            budgets=TurnBudgets(2, 16000, 128, 128, 128),
        )
    assert raised.value.code == "adapter_raised"
    assert raised.value.adapter_result is None and raised.value.terminal_recorded
    assert len(adapter.sandboxes) == 1
    events = journal.read()
    assert events[-1]["event"] == "turn_observation_failed"
    assert "process" not in events[-1]
    assert "private payload" not in journal.path.read_text()


def test_read_only_is_forwarded_through_storage(
    repository: Path, tmp_path: Path
) -> None:
    from agent_harness.native_output import run_stored_scoped_turn

    adapter = StructuralAdapter()
    result = run_stored_scoped_turn(
        adapter,
        *inputs(repository),
        journal=EventJournal(tmp_path / "state"),
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
        redactor=lambda text: text.replace("private", "redacted"),
        max_output_bytes=128,
        sandbox="read-only",
    )
    assert adapter.sandboxes == ["read-only"]
    assert (repository / "value").read_text() == "old"
    assert result.scoped_turn.decision.proceed
    assert result.final_message is not None


@pytest.mark.parametrize("status", ["timeout", "launch_error"])
def test_structural_process_failures_keep_observation_semantics(
    repository: Path, tmp_path: Path, status: Literal["timeout", "launch_error"]
) -> None:
    from agent_harness.scope import run_scoped_turn

    adapter = StructuralAdapter()
    adapter.status = status
    journal = EventJournal(tmp_path / "state")
    result = run_scoped_turn(
        adapter,
        *inputs(repository),
        journal=journal,
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
    )
    assert result.observed_turn.adapter_result.process_status == status
    assert result.decision.proceed
    assert result.observed_turn.after == result.observed_turn.before
    assert len(adapter.sandboxes) == 1
    assert [event["event"] for event in journal.read()] == [
        "turn_intent",
        "turn_observation",
        "scope_decision",
    ]


@pytest.mark.parametrize("consumer", ["observed", "scoped", "loop"])
@pytest.mark.parametrize("protected", [False, True])
def test_prepare_mutation_is_observed(
    repository: Path, tmp_path: Path, consumer: str, protected: bool
) -> None:
    from agent_harness.scope import run_scoped_turn

    class Mutating(StructuralAdapter):
        def prepare(self, repository_root: Path) -> None:
            super().prepare(repository_root)
            (repository_root / "outside").write_text("changed")

    (repository / "outside").write_text("original")
    task = TaskContract(
        "change value", ("value",), ("outside",) if protected else (), ("new value",)
    )
    args = (
        Mutating(),
        task,
        record_admission(task, approver="human", approved_at="2026-10-18T00:00:00Z"),
        ContextPacket(str(repository), 4096, ()),
    )
    journal = EventJournal(tmp_path / "state")
    budgets = TurnBudgets(2, 16000, 128, 128, 128)
    if consumer == "observed":
        observed = run_observed_turn(*args, journal=journal, budgets=budgets)
        assert "outside" in observed.delta.modified
    elif consumer == "scoped":
        scoped = run_scoped_turn(*args, journal=journal, budgets=budgets)
        assert not scoped.decision.proceed
    else:
        command = (sys.executable, "-c", "assert True")
        result = run_loop(
            *args,
            policy=VerificationPolicy((command,), ".", 2),
            criterion_commands={"new value": (command,)},
            journal=journal,
            state_store=CurrentStateStore(tmp_path / "state"),
            budgets=budgets,
            limits=RetryLimits(0, 1, 4, 50),
            redactor=lambda text: text,
            max_output_bytes=128,
        )
        assert result.status != "pass"


def test_preflight_does_not_prepare(repository: Path, tmp_path: Path) -> None:
    from agent_harness.turn import _validate_turn

    adapter = StructuralAdapter()
    assert (
        _validate_turn(
            adapter,
            *inputs(repository),
            EventJournal(tmp_path / "state"),
            TurnBudgets(2, 16000, 128, 128, 128),
            "workspace-write",
        )
        == repository
    )
    assert adapter.prepared == []


@pytest.mark.parametrize("field", ["process_status", "final_output_status"])
@pytest.mark.parametrize("kind", ["enum", "impersonator"])
def test_status_requires_plain_string_before_persistence(
    repository: Path, tmp_path: Path, field: str, kind: str
) -> None:
    class Status(StrEnum):
        PROCESS = "successful_exit"
        FINAL = "available"

    class Impersonator:
        def __eq__(self, other: object) -> bool:
            return other == (
                "successful_exit" if field == "process_status" else "available"
            )

    class Malformed(StructuralAdapter):
        def run(
            self,
            contract: TaskContract,
            admission: AdmissionRecord,
            context: ContextPacket,
            *,
            budgets: TurnBudgets,
            sandbox: Literal["read-only", "workspace-write"] = "workspace-write",
        ) -> CodexTurnResult:
            result = super().run(
                contract, admission, context, budgets=budgets, sandbox=sandbox
            )
            value = (
                (Status.PROCESS if field == "process_status" else Status.FINAL)
                if kind == "enum"
                else Impersonator()
            )
            if field == "process_status":
                return replace(
                    result,
                    process_status=cast(
                        Literal[
                            "launch_error", "timeout", "nonzero_exit", "successful_exit"
                        ],
                        value,
                    ),
                )
            return replace(
                result,
                final_output_status=cast(
                    Literal["available", "missing", "unsafe", "unreadable"], value
                ),
            )

    journal = EventJournal(tmp_path / "state")
    with pytest.raises(TurnObservationError) as raised:
        run_observed_turn(
            Malformed(),
            *inputs(repository),
            journal=journal,
            budgets=TurnBudgets(2, 16000, 128, 128, 128),
        )
    assert raised.value.adapter_result is None
    assert journal.read()[-1]["event"] == "turn_observation_failed"
    assert "process" not in journal.read()[-1]


@pytest.mark.parametrize("consumer", ["observed", "scoped", "loop"])
def test_failed_prepare_cannot_publish_success(
    repository: Path, tmp_path: Path, consumer: str
) -> None:
    from agent_harness.scope import run_scoped_turn

    class Refusing(StructuralAdapter):
        def prepare(self, repository_root: Path) -> None:
            (repository_root / "outside").write_text("changed")
            raise ValueError("prepare_refused")

    adapter = Refusing()
    journal = EventJournal(tmp_path / "state")
    args = (adapter, *inputs(repository))
    budgets = TurnBudgets(2, 16000, 128, 128, 128)
    if consumer == "loop":
        command = (sys.executable, "-c", "assert True")
        result = run_loop(
            *args,
            policy=VerificationPolicy((command,), ".", 2),
            criterion_commands={"new value": (command,)},
            journal=journal,
            state_store=CurrentStateStore(tmp_path / "state"),
            budgets=budgets,
            limits=RetryLimits(0, 1, 4, 50),
            redactor=lambda text: text,
            max_output_bytes=128,
        )
        assert result.status != "pass"
    else:
        run = run_observed_turn if consumer == "observed" else run_scoped_turn
        with pytest.raises(ValueError, match="^prepare_refused$"):
            run(*args, journal=journal, budgets=budgets)
    assert not adapter.sandboxes
    assert not any(
        event["event"]
        in {"turn_intent", "turn_observation", "scope_decision", "native_output_stored"}
        for event in journal.read()
    )
