import subprocess
import sys
import time
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent_harness.contract import TaskContract
from agent_harness.policy import VerificationPolicy


def contract(criteria: tuple[str, ...] = ("unit behavior", "lint")) -> TaskContract:
    return TaskContract("Improve behavior", ("src",), (), criteria)


def policy() -> VerificationPolicy:
    return VerificationPolicy((("python", "check.py"), ("ruff", "check")), ".", 3)


def test_exact_criterion_mapping_returns_immutable_bindings() -> None:
    from agent_harness.loop import bind_completion_criteria

    bindings = bind_completion_criteria(
        contract(),
        policy(),
        {"unit behavior": (("python", "check.py"),), "lint": (("ruff", "check"),)},
    )
    assert {item.criterion: item.commands for item in bindings} == {
        "unit behavior": (("python", "check.py"),),
        "lint": (("ruff", "check"),),
    }
    with pytest.raises(FrozenInstanceError):
        bindings[0].criterion = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "mapping",
    [
        {},
        {"unit behavior": (("python", "check.py"),)},
        {"unit behavior": (("python", "check.py"),), "lint": ()},
        {"unit behavior": (("python", "check.py"),), "lint": (("private-secret",),)},
        {
            "unit behavior": (("python", "check.py"),),
            "lint": (("ruff", "check"),),
            "extra": (("ruff", "check"),),
        },
    ],
)
def test_invalid_criterion_mapping_uses_fixed_diagnostic(mapping: object) -> None:
    from agent_harness.loop import bind_completion_criteria

    with pytest.raises(ValueError, match="^invalid_criterion_mapping$"):
        bind_completion_criteria(contract(), policy(), mapping)


def test_duplicate_contract_criteria_are_rejected() -> None:
    from agent_harness.loop import bind_completion_criteria

    with pytest.raises(ValueError, match="^invalid_criterion_mapping$"):
        bind_completion_criteria(
            contract(("lint", "lint")), policy(), {"lint": (("ruff", "check"),)}
        )


@pytest.mark.parametrize(
    "scenario",
    [
        "pass",
        "redaction",
        "retry",
        "repeated",
        "scope_failed",
        "missing_output",
        "verifier_mutation",
        "final_failure",
        "launch_failure",
        "no_admission",
        "history",
        "corrupt",
        "command_budget",
        "time_budget",
        "missing_protected",
        "protected_mutation",
        "after_baseline_budget",
        "multi_mutation",
        "journal_failure",
        "current_failure",
        "observation_failure",
        "state_symlink",
        "protected_directory",
        "protected_symlink",
        "unreadable_protected",
        "policy_callback",
        "dev_current_corrupt",
        "dev_journal_partial",
        "dev_journal_lost",
        "dev_state_lost",
        "baseline_persistence_corrupt",
        "ignored_current_failure",
        "ignored_native",
        "ignored_failed_native",
        "ignored_compensation",
        "retry_command_budget",
        "retry_time_budget",
        "final_time_budget",
    ],
)
def test_real_native_turn_requires_distinct_final_verification(
    tmp_path: Path, scenario: str
) -> None:
    native_failures = {
        "redaction": -1,
        "retry": 1,
        "repeated": 2,
        "ignored_failed_native": 1,
        "retry_command_budget": 1,
        "retry_time_budget": 1,
    }.get(scenario, 0)

    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.admission import record_admission
    from agent_harness.context import ContextPacket
    from agent_harness.journal import EventJournal
    from agent_harness.loop import run_loop
    from agent_harness.retry import RetryLimits
    from agent_harness.state import CurrentStateStore

    root = _repository(tmp_path)
    launches = tmp_path / "verify-launches"
    native_launches = tmp_path / "native-launches"
    executable = _native_fixture(
        tmp_path, root, native_launches, scenario, native_failures
    )
    command = (
        sys.executable,
        "-c",
        f"from pathlib import Path; p=Path({str(launches)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x'); assert Path('value').read_text()=='new'",
    )
    if scenario in (
        "verifier_mutation",
        "final_failure",
        "multi_mutation",
        "ignored_compensation",
    ):
        action = (
            "Path('value').write_text('verification edit') if len(p.read_text())==2 else None"
            if scenario
            in ("verifier_mutation", "multi_mutation", "ignored_compensation")
            else "assert len(p.read_text()) != 3"
        )
        if scenario == "ignored_compensation":
            action = "Path('protected').write_text('changed') if len(p.read_text())==2 else None"
        command = (
            sys.executable,
            "-c",
            f"from pathlib import Path; p=Path({str(launches)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x'); {action}",
        )
    if scenario in (
        "dev_current_corrupt",
        "dev_journal_partial",
        "dev_journal_lost",
        "dev_state_lost",
        "baseline_persistence_corrupt",
    ):
        target = str(
            tmp_path
            / "state"
            / (
                "current.json"
                if scenario in ("dev_current_corrupt", "dev_state_lost")
                else "events.jsonl"
            )
        )
        action = (
            "q.unlink(); q.mkdir()"
            if scenario == "dev_current_corrupt"
            else (
                "q.open('a').write('partial')"
                if scenario in ("dev_journal_partial", "baseline_persistence_corrupt")
                else "q.unlink()"
            )
        )
        at = 1 if scenario == "baseline_persistence_corrupt" else 2
        command = (
            sys.executable,
            "-c",
            f"from pathlib import Path; p=Path({str(launches)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x'); q=Path({target!r});\nif len(p.read_text())=={at}: {action}",
        )
    policy_commands: tuple[tuple[str, ...], ...] = (
        (("/missing-h6-executable",), command)
        if scenario == "launch_failure"
        else (command,)
    )
    tail_launches = tmp_path / "tail-launches"
    if scenario in (
        "multi_mutation",
        "ignored_compensation",
        "baseline_persistence_corrupt",
    ):
        tail = (
            sys.executable,
            "-c",
            f"from pathlib import Path; p=Path({str(tail_launches)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x')",
        )
        if scenario == "ignored_compensation":
            tail = (
                tail[0],
                tail[1],
                tail[2] + "; Path('protected').write_text('original')",
            )
        policy_commands = (command, tail)
    task = TaskContract(
        "change value",
        ("value",),
        ("missing",)
        if scenario == "missing_protected"
        else (
            ("protected",)
            if scenario
            in (
                "protected_mutation",
                "protected_directory",
                "protected_symlink",
                "unreadable_protected",
                "ignored_current_failure",
                "ignored_native",
                "ignored_failed_native",
                "ignored_compensation",
            )
            else ()
        ),
        ("new value",),
    )
    journal = EventJournal(tmp_path / "state")
    state = CurrentStateStore(tmp_path / "state")
    state.write({"caller": "preserved"})
    if scenario == "state_symlink":
        (tmp_path / "state-alias").symlink_to(
            tmp_path / "state", target_is_directory=True
        )
        journal = EventJournal(tmp_path / "state-alias")
        state = CurrentStateStore(tmp_path / "state-alias")
    if scenario == "history":
        journal.append({"event": "loop_start"})
    if scenario == "corrupt":
        journal.path.write_text("{\n")

    verifier_policy = VerificationPolicy(policy_commands, ".", 2)

    def redact(text: str) -> str:
        if scenario == "policy_callback":
            object.__setattr__(
                verifier_policy, "commands", ((sys.executable, "-c", "pass"),)
            )
        if native_failures == -1:
            raise ValueError("private storage secret")
        return text

    def clock() -> float:
        if scenario == "retry_time_budget":
            return 39.0 if native_launches.exists() else 0.0
        if scenario == "final_time_budget":
            return 49.0 if launches.exists() and len(launches.read_text()) == 2 else 0.0
        return (
            48.0
            if scenario == "after_baseline_budget" and launches.exists()
            else (0.0 if scenario == "after_baseline_budget" else time.monotonic())
        )

    result = run_loop(
        CodexCLIAdapter(executable),
        task,
        None
        if scenario == "no_admission"
        else record_admission(
            task, approver="human", approved_at="2026-10-06T00:00:00Z"
        ),  # type: ignore[arg-type]
        ContextPacket(str(root), 4096, ()),
        policy=verifier_policy,
        criterion_commands={"new value": (command,)},
        journal=journal,
        state_store=state,
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
        limits=RetryLimits(
            0 if scenario == "final_failure" else 2,
            2,
            3
            if scenario == "command_budget"
            else (4 if scenario == "retry_command_budget" else 7),
            1 if scenario == "time_budget" else 50,
        ),
        redactor=redact,
        max_output_bytes=128,
        monotonic=clock,
    )
    if scenario in (
        "dev_current_corrupt",
        "dev_journal_partial",
        "dev_journal_lost",
        "dev_state_lost",
        "baseline_persistence_corrupt",
    ):
        assert result.status == "unknown"
        if scenario == "baseline_persistence_corrupt":
            assert (
                result.usage.commands == 1
                and not native_launches.exists()
                and not tail_launches.exists()
            )
        else:
            assert result.usage.commands == 3 and len(result.attempt_ids) == 1
            assert launches.read_text() == "xx"
        return
    if scenario in ("retry_command_budget", "retry_time_budget"):
        assert result.status == "human"
        assert result.reason == (
            "command_budget" if scenario == "retry_command_budget" else "time_budget"
        )
        assert result.usage.commands == 2 and len(result.attempt_ids) == 1
        assert launches.read_text() == "x"
        return
    if scenario == "final_time_budget":
        assert result.status == "stopped" and result.reason == "time_budget"
        assert result.usage.commands == 3 and launches.read_text() == "xx"
        assert not any(
            event["event"] == "attempt_verification" for event in journal.read()
        )
        return
    if scenario == "ignored_current_failure":
        assert result.status == "unknown"
        assert result.usage.commands == 2 and len(result.attempt_ids) == 1
        assert launches.read_text() == "x"
        return
    if scenario in ("ignored_native", "ignored_failed_native", "ignored_compensation"):
        assert result.status == "human" and result.reason == "unsafe_failure"
        assert len(result.attempt_ids) == 1
        if scenario == "ignored_compensation":
            assert tail_launches.read_text() == "x"
            assert launches.read_text() == "xx"
        else:
            assert launches.read_text() == "x" and result.usage.commands == 2
        assert (root / "protected").read_text() == "changed"
        return
    if scenario in ("journal_failure", "current_failure", "observation_failure"):
        assert result.status == "unknown"
        assert len(result.attempt_ids) == 1 and result.usage.commands == 2
        assert launches.read_text() == "x"
        return
    current = state.read()
    assert current is not None and current["caller"] == "preserved"
    if scenario in (
        "no_admission",
        "history",
        "corrupt",
        "command_budget",
        "time_budget",
        "missing_protected",
        "state_symlink",
        "protected_symlink",
        "unreadable_protected",
    ):
        assert result.status in ("blocked", "stopped")
        assert result.usage.commands == 0
        assert not launches.exists() and not native_launches.exists()
        return
    if scenario == "after_baseline_budget":
        assert result.status == "stopped" and result.reason == "time_budget"
        assert result.usage.commands == 1
        assert launches.read_text() == "x" and not native_launches.exists()
        return
    if scenario == "launch_failure":
        assert result.status == "unknown"
        assert result.usage.commands == 0
        assert not launches.exists() and not native_launches.exists()
        return
    if scenario in ("scope_failed", "protected_mutation", "protected_directory"):
        assert result.status == "human" and result.reason == "unsafe_failure"
        assert len(result.attempt_ids) == 1 and result.usage.commands == 2
        assert result.escalation is not None
        return
    if scenario == "missing_output":
        assert result.status == "human" and result.reason == "unsafe_failure"
        assert launches.read_text() == "x"
        return
    if scenario in ("verifier_mutation", "multi_mutation", "ignored_compensation"):
        if scenario == "multi_mutation":
            assert tail_launches.read_text() == "x"
        assert (
            result.status == "blocked"
            and result.reason == "verification_workspace_changed"
        )
        assert launches.read_text() == "xx"
        return
    if scenario == "final_failure":
        assert result.status == "human" and result.reason == "retry_budget"
        assert launches.read_text() == "xxx"
        assert len(result.attempt_ids) == 1
        return
    summary = current["loop_summary"]
    assert isinstance(summary, dict)
    assert summary["commands"] == result.usage.commands
    if native_failures == -1:
        assert result.status == "unknown"
        assert result.usage.commands == 2
        assert len(result.attempt_ids) == 1
        assert launches.read_text() == "x"
        assert "private storage secret" not in journal.path.read_text()
        return
    if native_failures == 2:
        assert result.status == "human"
        assert result.reason == "same_failure"
        assert result.usage.commands == 3
        assert len(result.attempt_ids) == 2
        assert launches.read_text() == "x"
        return
    assert result.status == "pass"
    assert launches.read_text() == "xxx"
    assert result.usage.commands == 4 + native_failures
    assert len(result.attempt_ids) == 1 + native_failures
    assert all(
        item.passed and item.attempt_id == result.attempt_ids[-1]
        for item in result.criteria
    )
    assert (
        sum(event["event"] == "attempt_verification" for event in journal.read()) == 1
    )
    current = state.read()
    assert current is not None and current["caller"] == "preserved"
    assert result.report is not None
    assert result.report.profile.baseline.results[0].exit_code != 0
    actual_head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()
    assert (
        result.report.repository_root == root
        and result.report.target_revision == actual_head
    )
    terminal = next(
        event for event in journal.read() if event["event"] == "attempt_verification"
    )
    assert (
        terminal["attempt_id"] == result.attempt_ids[-1]
        and terminal["target_revision"] == actual_head
    )
    assert all(
        item.commands == (command,) and item.criterion == "new value"
        for item in result.criteria
    )
    with pytest.raises(FrozenInstanceError):
        result.status = "changed"  # type: ignore[misc]


def _repository(tmp_path: Path) -> Path:
    root = (tmp_path / "repository").resolve()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, timeout=5)
    (root / "value").write_text("old")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, timeout=5)
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
        timeout=5,
    )
    return root


def _native_fixture(
    tmp_path: Path,
    root: Path,
    native_launches: Path,
    scenario: str,
    native_failures: int,
) -> Path:
    executable = tmp_path / "native"
    executable.write_text(
        f"#!{sys.executable}\nimport sys,pathlib\nsys.stdin.read()\n"
        f"p=pathlib.Path({str(native_launches)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
        f"if len(p.read_text()) <= {native_failures}: sys.exit(1)\n"
        "pathlib.Path('value').write_text('new')\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('done')\n"
    )
    if scenario in (
        "ignored_native",
        "ignored_failed_native",
        "ignored_compensation",
        "ignored_current_failure",
    ):
        (root / ".gitignore").write_text("protected\n")
        (root / "protected").write_text("original")
        if scenario in (
            "ignored_native",
            "ignored_failed_native",
            "ignored_current_failure",
        ):
            executable.write_text(
                executable.read_text().replace(
                    "if len(p.read_text())",
                    "pathlib.Path('protected').write_text('changed')\nif len(p.read_text())",
                )
            )
    if scenario == "scope_failed":
        executable.write_text(
            f"#!{sys.executable}\nimport sys,pathlib\nsys.stdin.read()\npathlib.Path('unsafe').write_text('changed')\nsys.exit(1)\n"
        )
    if scenario == "missing_output":
        executable.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n")
    if scenario == "protected_mutation":
        (root / "protected").write_text("original")
        executable.write_text(
            executable.read_text() + "pathlib.Path('protected').write_text('changed')\n"
        )
    if scenario == "protected_directory":
        (root / "protected").mkdir()
        (root / "protected" / "base").write_text("original")
        executable.write_text(
            executable.read_text()
            + "pathlib.Path('protected/new').write_text('added')\n"
        )
    if scenario == "protected_symlink":
        (tmp_path / "outside").write_text("private")
        (root / "protected").symlink_to(tmp_path / "outside")
    if scenario == "unreadable_protected":
        (root / "protected").write_text("private")
        (root / "protected").chmod(0o000)
    if scenario == "journal_failure":
        executable.write_text(
            executable.read_text()
            + f"pathlib.Path({str(tmp_path / 'state' / 'events.jsonl')!r}).open('a').write('partial')\n"
        )
    if scenario in ("current_failure", "ignored_current_failure"):
        executable.write_text(
            executable.read_text()
            + f"p=pathlib.Path({str(tmp_path / 'state' / 'current.json')!r}); p.unlink(); p.mkdir()\n"
        )
    if scenario == "observation_failure":
        executable.write_text(executable.read_text() + "import os; os.mkfifo('fifo')\n")
    executable.chmod(0o700)
    return executable


@pytest.mark.parametrize("boundary", ["protected_check", "recapture", "acceptance"])
@pytest.mark.parametrize("elapsed", [49.0, 50.0, 51.0])
def test_final_cleanup_obeys_elapsed_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, elapsed: float
) -> None:
    """Post-command checks and evidence acceptance count toward the loop limit."""
    from agent_harness import loop
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.admission import record_admission
    from agent_harness.attempt import AttemptJournal
    from agent_harness.context import ContextPacket
    from agent_harness.integrity import verify_protected_inputs
    from agent_harness.journal import EventJournal
    from agent_harness.retry import RetryLimits
    from agent_harness.state import CurrentStateStore
    from agent_harness.turn import RepositoryObservation

    root = _repository(tmp_path)
    launches = tmp_path / "verify-launches"
    executable = _native_fixture(
        tmp_path, root, tmp_path / "native-launches", "pass", 0
    )
    (root / "protected").write_text("original")
    command = (
        sys.executable,
        "-c",
        f"from pathlib import Path; p=Path({str(launches)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x'); assert Path('value').read_text()=='new'",
    )
    task = TaskContract("change value", ("value",), ("protected",), ("new value",))
    journal = EventJournal(tmp_path / "state")
    now = 0.0

    def final_command_finished() -> bool:
        return launches.exists() and launches.read_text() == "xxx"

    if boundary == "protected_check":
        original_check = verify_protected_inputs

        def slow_check(*args: object, **kwargs: object) -> object:
            nonlocal now
            result = original_check(*args, **kwargs)  # type: ignore[arg-type]
            if final_command_finished():
                now = elapsed
            return result

        monkeypatch.setattr(loop, "verify_protected_inputs", slow_check)
    elif boundary == "recapture":
        original_capture = RepositoryObservation.capture

        def slow_capture(*args: object, **kwargs: object) -> object:
            nonlocal now
            result = original_capture(*args, **kwargs)  # type: ignore[arg-type]
            if final_command_finished():
                now = elapsed
            return result

        monkeypatch.setattr(RepositoryObservation, "capture", slow_capture)
    else:
        original_record = AttemptJournal.record_verification

        def slow_record(*args: object, **kwargs: object) -> object:
            nonlocal now
            result = original_record(*args, **kwargs)  # type: ignore[arg-type]
            if final_command_finished():
                now = elapsed
            return result

        monkeypatch.setattr(AttemptJournal, "record_verification", slow_record)

    result = loop.run_loop(
        CodexCLIAdapter(executable),
        task,
        record_admission(task, approver="human", approved_at="2026-10-06T00:00:00Z"),
        ContextPacket(str(root), 4096, ()),
        policy=VerificationPolicy((command,), ".", 2),
        criterion_commands={"new value": (command,)},
        journal=journal,
        state_store=CurrentStateStore(tmp_path / "state"),
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
        limits=RetryLimits(2, 2, 7, 50),
        redactor=lambda text: text,
        max_output_bytes=128,
        monotonic=lambda: now,
    )
    assert launches.read_text() == "xxx"
    assert result.usage.commands == 4
    assert result.usage.elapsed_seconds == elapsed
    assert result.status == ("stopped" if elapsed > 50 else "pass")
    assert result.reason == ("time_budget" if elapsed > 50 else "final_verification")
    if elapsed <= 50:
        assert result.criteria and all(item.passed for item in result.criteria)
    else:
        assert not result.criteria
    assert journal.read()[-1]["status"] == result.status
