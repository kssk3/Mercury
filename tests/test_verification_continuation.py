"""Behavioral contracts for safe verification continuation evidence."""

import json
from pathlib import Path
from typing import cast

import pytest


def test_checkpoint_results_restore_positionally_without_persisting_secrets() -> None:
    from agent_harness.runner import CommandResult
    from agent_harness.verification_checkpoint import restore_results, safe_results

    command = ("checker", "private-argv-canary")
    results = (
        CommandResult(command, 1, "private-stdout", "private-stderr", 0.25, False),
        CommandResult(command, 0, "", "", 0.5, False),
    )
    metadata = safe_results(results)
    persisted = json.dumps(metadata)
    assert "private-" not in persisted
    restored = restore_results(metadata, (command, command))
    assert [(r.command, r.exit_code, r.duration_seconds) for r in restored] == [
        (command, 1, 0.25),
        (command, 0, 0.5),
    ]
    assert all(r.stdout == "" and r.stderr == "" for r in restored)


def test_checkpoint_rejects_unproven_launch_and_conflicting_timeout() -> None:
    import pytest

    from agent_harness.runner import CommandResult
    from agent_harness.verification_checkpoint import restore_results, safe_results

    command = ("checker",)
    for exit_code, timed_out in ((None, False), (0, True)):
        result = CommandResult(command, exit_code, "", "", 0.1, timed_out)
        with pytest.raises(ValueError, match="^invalid_verification_checkpoint$"):
            safe_results((result,))
        metadata = safe_results((CommandResult(command, 0, "", "", 0.1, False),))
        metadata[0].update(exit_code=exit_code, timed_out=timed_out)
        with pytest.raises(ValueError, match="^invalid_verification_checkpoint$"):
            restore_results(metadata, (command,))


def test_continuation_elapsed_includes_offline_time_and_rejects_bad_clocks() -> None:
    import pytest

    from agent_harness.verification_checkpoint import carried_elapsed

    assert carried_elapsed(7.5, 100.0, 120.0) == 27.5
    for saved, checkpoint, now in (
        (-1, 100, 120),
        (True, 100, 120),
        (1, 100, 99),
        (1, float("nan"), 120),
        (1, 100, float("inf")),
        (1e308, 0, 1e308),
    ):
        with pytest.raises(ValueError, match="^invalid_verification_checkpoint$"):
            carried_elapsed(saved, checkpoint, now)


def test_restoration_rejects_changed_order_extra_fields_and_invalid_duration() -> None:
    import copy

    import pytest

    from agent_harness.runner import CommandResult
    from agent_harness.verification_checkpoint import restore_results, safe_results

    commands = (("first",), ("second",))
    metadata = safe_results(
        tuple(CommandResult(command, 0, "", "", 0.1, False) for command in commands)
    )
    with pytest.raises(ValueError, match="^invalid_verification_checkpoint$"):
        restore_results(metadata, tuple(reversed(commands)))
    with pytest.raises(ValueError, match="^invalid_verification_checkpoint$"):
        restore_results(metadata[:1], commands)
    for field, value in (
        ("duration_seconds", -1),
        ("duration_seconds", float("nan")),
        ("duration_seconds", True),
        ("exit_code", True),
        ("timed_out", 0),
        ("command_sha256", "bad"),
        ("stdout", "private-output"),
    ):
        corrupt = copy.deepcopy(metadata)
        corrupt[0][field] = value
        with pytest.raises(ValueError, match="^invalid_verification_checkpoint$"):
            restore_results(corrupt, commands)


@pytest.mark.parametrize(
    "corruption",
    [
        "none",
        "completed_digest",
        "duration",
        "elapsed",
        "extra",
        "intent",
        "counter",
        "current",
        "reserved_summary",
        "reserved_summary_null",
        "runtime_summary",
        "wall",
        "native_failure",
        "intent_contract",
        "intent_context",
        "intent_budgets",
        "runtime_marker",
        "fresh_clock",
    ],
)
def test_development_checkpoint_continues_only_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    import sys

    import pytest
    from test_loop import _native_fixture, _repository

    from agent_harness import loop
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.admission import record_admission
    from agent_harness.context import ContextPacket
    from agent_harness.contract import TaskContract
    from agent_harness.journal import EventJournal
    from agent_harness.policy import VerificationPolicy
    from agent_harness.retry import RetryLimits
    from agent_harness.state import CurrentStateStore

    root = _repository(tmp_path)
    native = tmp_path / "native-count"
    launches = tmp_path / "verify-count"
    adapter = CodexCLIAdapter(_native_fixture(tmp_path, root, native, "pass", 0))
    task = TaskContract("change value", ("value",), (), ("new value",))
    command = (
        sys.executable,
        "-c",
        f"from pathlib import Path; p=Path({str(launches)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x'); assert Path('value').read_text()=='new'",
    )
    journal = EventJournal(tmp_path / "state")
    store = CurrentStateStore(tmp_path / "state")
    store.write({"caller": "preserved"})
    args = (
        adapter,
        task,
        record_admission(task, approver="human", approved_at="2026-10-06T00:00:00Z"),
        ContextPacket(str(root), 4096, ()),
    )
    if corruption in ("runtime_marker", "fresh_clock", "runtime_summary"):
        if corruption == "runtime_marker":
            command = (
                *command[:2],
                command[2]
                + f"; Path({str(journal.path)!r}).open('a').write('\\n') if len(p.read_text())==2 else None",
            )
        if corruption == "runtime_summary":
            command = (
                *command[:2],
                command[2]
                + f"; import json; q=Path({str(store.path)!r}); q.write_text(json.dumps(json.loads(q.read_text()) | {{'loop_summary': None}})) if len(p.read_text())==2 else None",
            )
        fresh = loop.run_loop(
            *args,
            policy=VerificationPolicy((command,), ".", 2),
            criterion_commands={"new value": (command,)},
            journal=journal,
            state_store=store,
            budgets=TurnBudgets(2, 16000, 128, 128, 128),
            limits=RetryLimits(0, 2, 4, 50),
            redactor=lambda x: x,
            max_output_bytes=128,
            checkpoint_verification=True,
            wall_clock=(lambda: float("nan"))
            if corruption == "fresh_clock"
            else __import__("time").time,
        )
        assert fresh.status in ("blocked", "unknown")
        assert (
            (not launches.exists())
            if corruption == "fresh_clock"
            else launches.read_text() == "xx"
        )
        return
    original = store.write

    class Interrupted(BaseException):
        pass

    def interrupt_at_boundary(value: dict[str, object]) -> None:
        original(value)
        if journal.read()[-1].get("pending_phase") == "final":
            raise Interrupted

    monkeypatch.setattr(store, "write", interrupt_at_boundary)
    with pytest.raises(Interrupted):
        loop.run_loop(
            *args,
            policy=VerificationPolicy((command,), ".", 2),
            criterion_commands={"new value": (command,)},
            journal=journal,
            state_store=store,
            budgets=TurnBudgets(2, 16000, 128, 128, 128),
            limits=RetryLimits(0, 2, 4, 50),
            redactor=lambda x: x,
            max_output_bytes=128,
            checkpoint_verification=True,
        )
    assert launches.read_text() == "xx"
    monkeypatch.setattr(store, "write", original)
    if corruption != "none":
        from agent_harness.attempt import AttemptJournal

        events = journal.read()
        row = events[-1]
        if corruption == "completed_digest":
            cast(list[dict[str, object]], row["completed_results"])[0][
                "command_sha256"
            ] = "a" * 64
        elif corruption == "duration":
            cast(list[dict[str, object]], row["completed_results"])[0][
                "duration_seconds"
            ] = -1
        elif corruption == "elapsed":
            row["elapsed_seconds"] = 0
        elif corruption == "wall":
            row["wall_seconds"] = 0
        elif corruption == "extra":
            row["private"] = "secret"
        elif corruption == "counter":
            row["commands"] = 1
        elif corruption == "intent":
            events.append(
                {
                    "event": "verification_phase_intent",
                    "attempt_id": row["attempt_id"],
                    "phase": "final",
                }
            )
        elif corruption in ("intent_contract", "intent_context", "intent_budgets"):
            for event in events:
                if event.get("event") == "turn_intent":
                    if corruption == "intent_budgets":
                        cast(dict[str, object], event["budgets"])["timeout_seconds"] = 3
                    else:
                        event[
                            "contract_sha256"
                            if corruption == "intent_contract"
                            else "context_sha256"
                        ] = "a" * 64
        elif corruption == "native_failure":
            for event in events:
                if event.get("event") == "turn_observation":
                    cast(dict[str, object], event["process"])["process_status"] = (
                        "nonzero_exit"
                    )
                    cast(dict[str, object], event["process"])["exit_code"] = 1
        journal.path.write_text("".join(json.dumps(event) + "\n" for event in events))
        AttemptJournal(root, journal=journal, state_store=store).refresh()
        if corruption == "current":
            original({"caller": "preserved"})
        if corruption in ("reserved_summary", "reserved_summary_null"):
            conflicting = store.read()
            assert conflicting is not None
            conflicting["loop_summary"] = (
                None if corruption == "reserved_summary_null" else {"status": "pass"}
            )
            original(conflicting)
    before_resume = (journal.path.read_bytes(), store.path.read_bytes())
    result = loop.continue_verification(
        *args,
        policy=VerificationPolicy((command,), ".", 2),
        criterion_commands={"new value": (command,)},
        journal=journal,
        state_store=store,
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
        limits=RetryLimits(0, 2, 4, 50),
        redactor=lambda x: x,
        max_output_bytes=128,
    )
    if corruption != "none":
        assert result.status == "blocked"
        assert launches.read_text() == "xx"
        assert native.read_text() == "x"
        if corruption in ("reserved_summary", "reserved_summary_null"):
            assert before_resume == (journal.path.read_bytes(), store.path.read_bytes())
        return
    assert result.status == "pass"
    assert result.usage.commands == 4
    assert launches.read_text() == "xxx"
    assert native.read_text() == "x"
    assert (store.read() or {})["caller"] == "preserved"
    assert (
        loop.continue_verification(
            *args,
            policy=VerificationPolicy((command,), ".", 2),
            criterion_commands={"new value": (command,)},
            journal=journal,
            state_store=store,
            budgets=TurnBudgets(2, 16000, 128, 128, 128),
            limits=RetryLimits(0, 2, 4, 50),
            redactor=lambda x: x,
            max_output_bytes=128,
        ).status
        == "blocked"
    )
    assert launches.read_text() == "xxx"


@pytest.mark.parametrize("boundary", ["development", "final"])
def test_killed_process_continues_in_fresh_interpreter(
    tmp_path: Path, boundary: str
) -> None:
    import hashlib
    import subprocess
    import sys
    import time

    from test_loop import _native_fixture, _repository

    from agent_harness.contract import TaskContract
    from agent_harness.journal import EventJournal
    from agent_harness.startup import reconcile_startup
    from agent_harness.state import CurrentStateStore
    from agent_harness.turn import RepositoryObservation

    root = _repository(tmp_path)
    (root / "protected").write_text("original-user-data")
    native = tmp_path / "native-count"
    executable = _native_fixture(tmp_path, root, native, "pass", 0)
    launches = tmp_path / "verify-count"
    marker = tmp_path / "ready"
    state = tmp_path / "state"
    driver = tmp_path / "driver.py"
    before = RepositoryObservation.capture(root)
    command = (
        sys.executable,
        "-c",
        f"from pathlib import Path; p=Path({str(launches)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x'); assert Path('value').read_text()=='new'",
    )
    driver.write_text(f"""
import json,sys,time
from pathlib import Path
from agent_harness.adapter import CodexCLIAdapter,TurnBudgets
from agent_harness.admission import record_admission
from agent_harness.context import ContextPacket
from agent_harness.contract import TaskContract
from agent_harness.journal import EventJournal
from agent_harness.state import CurrentStateStore
from agent_harness.policy import VerificationPolicy
from agent_harness.retry import RetryLimits
from agent_harness.loop import run_loop,continue_verification
root=Path({str(root)!r}); journal=EventJournal({str(state)!r}); store=CurrentStateStore({str(state)!r})
mode=sys.argv[1]
task=TaskContract('change value',('value',),('protected',),('new value',))
admission=record_admission(task,approver='other' if mode=='admission' else 'human',approved_at='2026-10-06T00:00:00Z')
context=ContextPacket(str(root),4097 if mode=='context' else 4096,())
command={command!r}
policy=VerificationPolicy((command,),'.',3 if mode=='policy' else 2)
limits=RetryLimits(0,2,5 if mode=='limits' else 4,100)
args=(CodexCLIAdapter(Path({str(executable)!r})),task,admission,context)
options=dict(policy=policy,criterion_commands={{'new value':(command,)}},journal=journal,state_store=store,budgets=TurnBudgets(2,16000,128,128,128),limits=limits,redactor=lambda x:x,max_output_bytes=128)
if mode=='pause':
    store.write({{'caller':'preserved'}})
    original=store.write
    def publish(value):
        original(value)
        if journal.read()[-1].get('pending_phase')=={boundary!r}:
            Path({str(marker)!r}).write_text('ready')
            while True: time.sleep(0.02)
    store.write=publish
    result=run_loop(*args,**options,checkpoint_verification=True)
else:
    result=continue_verification(*args,**options,wall_clock=(lambda:time.time()+200) if mode=='expired' else time.time)
print(json.dumps({{'status':result.status,'commands':result.usage.commands,'attempt_ids':result.attempt_ids}}))
""")
    child = subprocess.Popen(
        [sys.executable, str(driver), "pause"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                raise AssertionError(child.communicate(timeout=1))
            time.sleep(0.02)
        assert marker.exists()
        child.kill()
        child.communicate(timeout=5)
        assert child.returncode is not None and child.returncode < 0
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
    journal = EventJournal(state)
    store = CurrentStateStore(state)
    checkpoint = journal.read()[-1]
    checkpoint_count = launches.read_text()
    original_journal, original_current = (
        journal.path.read_bytes(),
        store.path.read_bytes(),
    )
    for mode in ("admission", "context", "policy", "limits", "expired"):
        rejected = subprocess.run(
            [sys.executable, str(driver), mode],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert json.loads(rejected.stdout)["status"] in ("blocked", "stopped")
        assert launches.read_text() == checkpoint_count
        assert journal.path.read_bytes() == original_journal
        assert store.path.read_bytes() == original_current
    for name in ("protected", "unrelated"):
        target = root / name
        previous = target.read_bytes() if target.exists() else None
        target.write_text("changed")
        rejected = subprocess.run(
            [sys.executable, str(driver), "resume"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert json.loads(rejected.stdout)["status"] == "blocked"
        assert launches.read_text() == checkpoint_count
        if previous is None:
            target.unlink()
        else:
            target.write_bytes(previous)
    result = subprocess.run(
        [sys.executable, str(driver), "resume"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    outcome = json.loads(result.stdout)
    assert outcome == {
        "status": "pass",
        "commands": 4,
        "attempt_ids": [checkpoint["attempt_id"]],
    }
    assert native.read_text() == "x"
    assert launches.read_text() == "xxx"
    after = RepositoryObservation.capture(root)
    assert before.head == after.head and before.index_sha256 == after.index_sha256
    assert (root / "protected").read_text() == "original-user-data"
    assert (store.read() or {})["caller"] == "preserved"
    task = TaskContract("change value", ("value",), ("protected",), ("new value",))
    startup = reconcile_startup(
        root,
        journal=journal,
        state_store=store,
        expected_contract_sha256=hashlib.sha256(task.to_json().encode()).hexdigest(),
    )
    assert startup.status == "completed"
    assert startup.usage is not None and startup.usage[0] == 4
    assert sum(e.get("event") == "attempt_verification" for e in journal.read()) == 1


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("changed_mode", [False, True])
def test_checkpoint_roundtrip_preserves_protected_permission_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool, changed_mode: bool
) -> None:
    import sys

    from test_loop import _native_fixture, _repository

    from agent_harness import loop
    from agent_harness.adapter import CodexCLIAdapter, TurnBudgets
    from agent_harness.admission import record_admission
    from agent_harness.context import ContextPacket
    from agent_harness.contract import TaskContract
    from agent_harness.journal import EventJournal
    from agent_harness.policy import VerificationPolicy
    from agent_harness.retry import RetryLimits
    from agent_harness.state import CurrentStateStore

    root = _repository(tmp_path)
    (root / ".git/info/exclude").write_text("protected/\n")
    (root / "protected").mkdir()
    target = root / "protected/private.dat"
    target.write_bytes(b"same contents")
    target.chmod(0o600)
    task = TaskContract(
        "change value",
        ("value",),
        ("protected" if directory else "protected/private.dat",),
        ("new value",),
    )
    native = tmp_path / "native-count"
    adapter = CodexCLIAdapter(_native_fixture(tmp_path, root, native, "pass", 0))
    command = (sys.executable, "-c", "assert True")
    journal = EventJournal(tmp_path / "state")
    store = CurrentStateStore(tmp_path / "state")
    args = (
        adapter,
        task,
        record_admission(task, approver="human", approved_at="2026-10-06T00:00:00Z"),
        ContextPacket(str(root), 4096, ()),
    )
    kwargs = dict(
        policy=VerificationPolicy((command,), ".", 2),
        criterion_commands={"new value": (command,)},
        journal=journal,
        state_store=store,
        budgets=TurnBudgets(2, 16000, 128, 128, 128),
        limits=RetryLimits(0, 2, 4, 50),
        redactor=lambda x: x,
        max_output_bytes=128,
    )
    original = store.write

    class Interrupted(BaseException):
        pass

    def interrupt(value: dict[str, object]) -> None:
        original(value)
        if journal.read()[-1].get("pending_phase") == "final":
            raise Interrupted

    monkeypatch.setattr(store, "write", interrupt)
    with pytest.raises(Interrupted):
        loop.run_loop(*args, **kwargs, checkpoint_verification=True)  # type: ignore[arg-type]
    checkpoint = journal.read()[-1]
    row = cast(
        list[dict[str, object]],
        checkpoint["protected_directories" if directory else "protected_files"],
    )[0]
    if directory:
        row = cast(list[dict[str, object]], row["files"])[0]
    assert row.get("mode") == 0o600
    monkeypatch.setattr(store, "write", original)
    if changed_mode:
        target.chmod(0o777)
    result = loop.continue_verification(*args, **kwargs)  # type: ignore[arg-type]
    if changed_mode:
        assert result.status != "pass", result.reason
        assert any(
            event.get("event") == "loop_protected_inputs"
            and any(
                row.get("state") == "modified"
                for row in cast(list[dict[str, object]], event["checks"])
            )
            for event in journal.read()
        ), result.reason
    else:
        assert result.status == "pass", result.reason
    assert native.read_text() == "x"
