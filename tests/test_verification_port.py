"""Contracts for the verification output port and public compatibility aliases."""

from importlib import import_module
from pathlib import Path

import pytest

from agent_harness.baseline import VerificationBaseline
from agent_harness.development import run_development_profile
from agent_harness.policy import VerificationPolicy
from agent_harness.ports.verification import (
    CommandResult,
    VerificationExecutable,
    VerificationExecutables,
    VerificationExecutor,
)


def test_public_runner_aliases_keep_identity() -> None:
    old = import_module("agent_harness.runner")
    port = import_module("agent_harness.ports.verification")
    local = import_module("agent_harness.adapters.verification.local")
    shared = import_module("agent_harness.adapters._executables")
    for name in ("CommandResult", "VerificationExecutable", "VerificationExecutables"):
        assert getattr(old, name) is getattr(port, name)
    assert old.ControlledCommandRunner is local.ControlledCommandRunner
    assert old.bind_verification_executables is shared.bind_verification_executables


class RecordingExecutor:
    """Trusted test double for application ordering, not process safety evidence."""

    def __init__(self) -> None:
        self.selected: VerificationExecutables | None = None
        self.used: list[VerificationExecutables | None] = []

    def __bool__(self) -> bool:
        return False

    def bind(
        self, policy: VerificationPolicy, repository: Path | str
    ) -> VerificationExecutables:
        self.selected = VerificationExecutables(
            str(Path(repository).resolve()),
            str((Path(repository) / policy.working_directory).resolve()),
            tuple(
                VerificationExecutable(command, None, None, None, None)
                for command in policy.commands
            ),
        )
        return self.selected

    def run(
        self,
        policy: VerificationPolicy,
        repository: Path | str,
        *,
        selections: VerificationExecutables | None = None,
    ) -> tuple[CommandResult, ...]:
        self.used.append(selections)
        return tuple(
            CommandResult(
                command,
                0 if (Path(repository) / "value").read_text() == "new" else 1,
                "",
                "",
                0.01,
                False,
            )
            for command in policy.commands
        )


def test_development_uses_falsy_injected_executor_and_supplied_selection(
    tmp_path: Path,
) -> None:
    (tmp_path / "value").write_text("new")
    policy = VerificationPolicy((("synthetic-check",),), ".", 2)
    executor: VerificationExecutor = RecordingExecutor()
    selected = executor.bind(policy, tmp_path)
    baseline = VerificationBaseline.capture(
        (CommandResult(policy.commands[0], 1, "", "", 0.1, False),)
    )
    result = run_development_profile(
        policy, tmp_path, baseline, selections=selected, verification_executor=executor
    )
    assert result.results[0].exit_code == 0
    assert isinstance(executor, RecordingExecutor)
    assert executor.used == [selected]
    assert executor.used[0] is selected


@pytest.mark.parametrize("resume", [False, True])
def test_loop_preserves_selection_order_and_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume: bool
) -> None:
    from test_loop import _repository

    from agent_harness.admission import record_admission
    from agent_harness.context import ContextPacket
    from agent_harness.contract import TaskContract
    from agent_harness.journal import EventJournal
    from agent_harness.loop import continue_verification, run_loop
    from agent_harness.ports.agent import AgentTurnResult, CapturedText, TurnBudgets
    from agent_harness.retry import RetryLimits
    from agent_harness.state import CurrentStateStore

    root = _repository(tmp_path)
    executor = RecordingExecutor()
    preparations: list[str] = []

    class Agent:
        def prepare(self, repository_root: Path) -> None:
            assert executor.selected is not None
            preparations.append("prepare")

        def run(
            self,
            contract: TaskContract,
            admission: object,
            context: ContextPacket,
            *,
            budgets: TurnBudgets,
            sandbox: str = "workspace-write",
        ) -> AgentTurnResult:
            (root / "value").write_text("new")
            preparations.append("run")
            return AgentTurnResult(
                ("synthetic-agent",),
                str(root),
                "successful_exit",
                0,
                0.01,
                CapturedText("", 0, False),
                CapturedText("", 0, False),
                CapturedText("done", 4, False),
                "available",
            )

    task = TaskContract("change value", ("value",), (), ("new value",))
    command = ("synthetic-check",)
    journal = EventJournal(tmp_path / "state")
    store = CurrentStateStore(tmp_path / "state")
    options = dict(
        policy=VerificationPolicy((command,), ".", 2),
        criterion_commands={"new value": (command,)},
        journal=journal,
        state_store=store,
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
        limits=RetryLimits(0, 2, 7, 50),
        redactor=lambda text: text,
        max_output_bytes=128,
        verification_executor=executor,
    )
    args = (
        Agent(),
        task,
        record_admission(task, approver="human", approved_at="2026-11-12T00:00:00Z"),
        ContextPacket(str(root), 4096, ()),
    )
    original_write = store.write

    class Interrupted(BaseException):
        pass

    def interrupt(value: dict[str, object]) -> None:
        original_write(value)
        if journal.read()[-1].get("pending_phase") == "final":
            raise Interrupted

    if resume:
        monkeypatch.setattr(store, "write", interrupt)
        with pytest.raises(Interrupted):
            run_loop(*args, **options, checkpoint_verification=True)  # type: ignore[arg-type]
        assert preparations == ["prepare", "run"]
        assert executor.selected is not None
        assert all(item is executor.selected for item in executor.used)
        executor.used.clear()
        monkeypatch.setattr(store, "write", original_write)
        result = continue_verification(*args, **options)  # type: ignore[arg-type]
    else:
        result = run_loop(*args, **options)  # type: ignore[arg-type]
    assert result.status == "pass"
    assert preparations == ["prepare", "run"]
    assert len(executor.used) == (1 if resume else 3)
    assert executor.selected is not None
    assert all(item is executor.selected for item in executor.used)


def test_legacy_class_method_patch_reaches_canonical_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_harness.adapters.verification.local import ControlledCommandRunner
    from agent_harness.runner import ControlledCommandRunner as LegacyRunner

    expected = (CommandResult(("synthetic-check",), 0, "", "", 0.0, False),)

    def run(
        self: ControlledCommandRunner,
        policy: VerificationPolicy,
        repository: Path | str,
        *,
        selections: VerificationExecutables | None = None,
    ) -> tuple[CommandResult, ...]:
        return expected

    monkeypatch.setattr(LegacyRunner, "run", run)
    assert (
        ControlledCommandRunner().run(
            VerificationPolicy((("synthetic-check",),), ".", 2), tmp_path
        )
        is expected
    )


def test_external_collection_injected_executor_observes_saved_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_loop import _repository

    from agent_harness import external_checks

    root = _repository(tmp_path)
    (root / "value").write_text("new")
    bundle = tmp_path / "receipt"

    class Executor(RecordingExecutor):
        def run(
            self,
            policy: VerificationPolicy,
            repository: Path | str,
            *,
            selections: VerificationExecutables | None = None,
        ) -> tuple[CommandResult, ...]:
            assert (bundle / "intent.json").is_file()
            assert selections is self.selected
            return super().run(policy, repository, selections=selections)

    def forbidden(*, max_output_bytes: int = 1048576) -> VerificationExecutor:
        raise AssertionError("injected executor must bypass default composition")

    monkeypatch.setattr(external_checks, "create_verification_executor", forbidden)
    executor = Executor()
    result = external_checks.collect_external_check(
        root,
        VerificationPolicy((("synthetic-check",),), ".", 2),
        bundle,
        actor="test",
        check_label="unit",
        criterion="synthetic",
        safe_command_display="synthetic",
        verification_executor=executor,
    )
    assert result["process"]["state"] == "EXITED"
    assert len(executor.used) == 1


def test_external_default_executor_retains_output_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    from test_loop import _repository

    from agent_harness import external_checks
    from agent_harness.adapters.verification.local import ControlledCommandRunner

    root = _repository(tmp_path)
    observed: list[int] = []
    original = ControlledCommandRunner.run

    def run(
        self: ControlledCommandRunner,
        policy: VerificationPolicy,
        repository: Path | str,
        *,
        selections: VerificationExecutables | None = None,
    ) -> tuple[CommandResult, ...]:
        results = original(self, policy, repository, selections=selections)
        observed.extend(
            len(item.stdout.encode("utf-8", errors="surrogateescape"))
            for item in results
        )
        return results

    monkeypatch.setattr(ControlledCommandRunner, "run", run)
    external_checks.collect_external_check(
        root,
        VerificationPolicy(((sys.executable, "-c", "print('x' * 70000)"),), ".", 2),
        tmp_path / "receipt",
        actor="test",
        check_label="unit",
        criterion="synthetic",
        safe_command_display="python [redacted]",
    )
    assert observed == [65536]
