"""R4: real process death at persisted H6 boundaries, not power-loss durability.

Contracts precede execution: unfinished evidence cannot imply completion or
execution authority; explicit R2 recovery restores only the admitted delta.
Test-only subclasses stop AFTER real writes, without product fault hooks.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

import pytest

from agent_harness.adapter import CodexCLIAdapter, CodexTurnResult, TurnBudgets
from agent_harness.admission import AdmissionRecord, record_admission
from agent_harness.context import ContextPacket
from agent_harness.contract import TaskContract
from agent_harness.interruption import human_gate, report_interruption
from agent_harness.journal import EventJournal
from agent_harness.loop import run_loop
from agent_harness.policy import VerificationPolicy
from agent_harness.recovery import capture_patch, recover_patch, seal_patch
from agent_harness.retry import RetryLimits
from agent_harness.startup import reconcile_startup
from agent_harness.state import CurrentStateStore

BOUNDARIES = (
    "loop_start",
    "turn_intent",
    "native_mutation",
    "turn_observation",
    "attempt_verification",
    "loop_outcome",
    "loop_summary",
)


def _contract() -> TaskContract:
    return TaskContract("update value", ("value",), (), ("value updated",))


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        timeout=10,
    ).stdout


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def _line(channel: socket.socket) -> str:
    data = bytearray()
    deadline = time.monotonic() + 10
    while not data.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("owned child handshake exceeded 10 seconds")
        channel.settimeout(remaining)
        part = channel.recv(1)
        if not part:
            raise AssertionError("owned child closed handshake before boundary")
        data.extend(part)
        assert len(data) <= 4096, "unbounded handshake"
    return data.decode().rstrip("\n")


def _child(directory: str, boundary: str, descriptor: int) -> None:
    """Run real H6; pause only once the selected real operation has finished."""
    base = Path(directory)
    channel = socket.socket(fileno=descriptor)
    channel.settimeout(10)

    def pause(observed: str) -> None:
        if observed != boundary:
            return
        channel.sendall((json.dumps({"boundary": observed}) + "\n").encode())
        assert _line(channel) == "ACK " + observed
        channel.sendall(b"ACKNOWLEDGED\n")
        # Parent kills this owned session after acknowledgment; timeout fails closed.
        assert _line(channel) == "never release"

    class Journal(EventJournal):
        def append(self, event: Mapping[str, object]) -> None:
            super().append(event)
            pause(str(event.get("event")))

    class Store(CurrentStateStore):
        def write(self, state: Mapping[str, object]) -> None:
            super().write(state)
            if "loop_summary" in state:
                pause("loop_summary")

    class Adapter(CodexCLIAdapter):
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
                contract,
                admission,
                context,
                budgets=budgets,
                sandbox=sandbox,
            )
            # Adapter has reaped its separate-session native process. Workspace
            # mutation exists, but run_observed_turn has not captured the after image.
            assert result.exit_code == 0
            pause("native_mutation")
            return result

    task = _contract()
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; assert Path('value').read_text() == 'agent'",
    )
    result = run_loop(
        Adapter(base / "native"),
        task,
        record_admission(task, approver="fixture", approved_at="2026-10-07T00:00:00Z"),
        ContextPacket(str(base / "repo"), 4096, ()),
        policy=VerificationPolicy((command,), ".", 2),
        criterion_commands={"value updated": (command,)},
        journal=Journal(base / "external"),
        state_store=Store(base / "external"),
        budgets=TurnBudgets(3, 16000, 128, 128, 128),
        limits=RetryLimits(0, 1, 8, 40),
        redactor=lambda text: text,
        max_output_bytes=128,
    )
    raise AssertionError(f"boundary was not reached: {result.status}")


@pytest.fixture(scope="module")
def repository_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("kill-point-seed") / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "--local", "maintenance.auto", "false")
    for name in ("value", "user-dirty", "user-staged"):
        (root / name).write_text("committed")
    _git(root, "add", ".")
    _git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=f@invalid",
        "commit",
        "-qm",
        "fixture",
    )
    (root / "value").write_text("user baseline")
    (root / "user-dirty").write_text("dirty user work")
    (root / "user-staged").write_text("staged user work")
    _git(root, "add", "user-staged")
    (root / "user-untracked").write_text("untracked user work")
    return root


def _kill_at(directory: Path, boundary: str) -> int:
    parent, child = socket.socketpair()
    parent.settimeout(10)
    process: subprocess.Popen[bytes] | None = None
    with (directory / "child.log").open("wb") as log:
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.path.insert(0, sys.argv[1]); "
                    "from test_recovery_kill_points import _child; "
                    "_child(sys.argv[2], sys.argv[3], int(sys.argv[4]))",
                    str(Path(__file__).parent),
                    str(directory),
                    boundary,
                    str(child.fileno()),
                ],
                pass_fds=(child.fileno(),),
                start_new_session=True,
                stdout=log,
                stderr=log,
            )
            child.close()
            assert json.loads(_line(parent)) == {"boundary": boundary}
            # Inspect actual surviving persistence before acknowledging the cut.
            journal = EventJournal(directory / "external")
            events = journal.read()
            if boundary == "loop_summary":
                state = CurrentStateStore(directory / "external").read()
                assert state is not None and state["loop_summary"] == events[-1]
                assert events[-1]["status"] == "pass"
            elif boundary == "native_mutation":
                assert events[-1]["event"] == "turn_intent"
                assert (directory / "repo/value").read_text() == "agent"
            else:
                assert events[-1]["event"] == boundary
            parent.sendall(("ACK " + boundary + "\n").encode())
            assert _line(parent) == "ACKNOWLEDGED"
            assert os.getpgid(process.pid) == process.pid
            os.killpg(process.pid, signal.SIGKILL)
            assert process.wait(timeout=10) == -signal.SIGKILL
            return process.returncode
        finally:
            parent.close()
            child.close()
            if process is not None:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=10)


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_real_kill_reconciles_without_automatic_execution(
    tmp_path: Path,
    repository_seed: Path,
    boundary: str,
    record_property: Callable[[str, object], None],
) -> None:
    # R4-A1..A4: real death, surviving evidence, read-only actions, exact recovery.
    directory = tmp_path.resolve()
    root = directory / "repo"
    shutil.copytree(repository_seed, root)
    initial = _snapshot(root)
    head = _git(root, "rev-parse", "HEAD")
    index = (root / ".git/index").read_bytes()
    launches = directory / "native-launches"
    executable = directory / "native"
    executable.write_text(
        f"#!{sys.executable}\nimport pathlib,sys\nsys.stdin.buffer.read()\n"
        f"p=pathlib.Path({str(launches)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
        "pathlib.Path('value').write_text('agent')\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('done')\n"
    )
    executable.chmod(0o700)
    task = _contract()
    before = capture_patch(root, task, "trusted-r4-recovery", ("value",))
    code = _kill_at(directory, boundary)
    record_property("kill_boundary", boundary)
    record_property("owned_child_exit", code)
    record_property("owned_child_reaped", True)
    assert _git(root, "rev-parse", "HEAD") == head
    assert (root / ".git/index").read_bytes() == index
    for name in ("user-dirty", "user-staged", "user-untracked"):
        assert (root / name).read_bytes() == initial[name]
    journal, store = (
        EventJournal(directory / "external"),
        CurrentStateStore(directory / "external"),
    )
    digest = hashlib.sha256(task.to_json().encode()).hexdigest()
    surviving = _snapshot(directory)
    startup = reconcile_startup(
        root, journal=journal, state_store=store, expected_contract_sha256=digest
    )
    report = report_interruption(
        root, journal=journal, state_store=store, expected_contract_sha256=digest
    )
    assert report.evidence == startup
    if boundary == "loop_summary":
        assert startup.status == "completed"
    else:
        assert startup.status == "interrupted"
    identities = tuple(
        str(event["attempt_id"])
        for event in journal.read()
        if event.get("event") == "turn_intent"
    )
    assert startup.attempt_ids == identities
    if boundary == "native_mutation":
        assert startup.workspace_delta is not None
        assert startup.workspace_delta.modified == ("value",)
    for action in ("stop", "inspect", "new_attempt", "recover_patch"):
        decision = human_gate(
            report, {"report_sha256": report.report_sha256, "action": action}
        )
        assert not decision.execution_authorized
        assert decision.status == (
            action if action in {"stop", "inspect"} else "requested"
        )
    assert _snapshot(directory) == surviving
    record_property("startup_status", startup.status)
    record_property(
        "native_launches", launches.read_text() if launches.exists() else ""
    )
    if boundary == "native_mutation":
        # Caller supplies trusted snapshots explicitly; the R3 request above made
        # no write and did not invoke recovery or a second native turn.
        after = capture_patch(root, task, "trusted-r4-recovery", ("value",))
        reference = seal_patch(before, after, directory / "exact-patch")
        (root / "value").write_text("later user conflict")
        conflict = _snapshot(directory)
        with pytest.raises(ValueError, match="conflict"):
            recover_patch(root, task, "trusted-r4-recovery", reference)
        assert _snapshot(directory) == conflict
        (root / "value").write_text("agent")
        assert (
            recover_patch(root, task, "trusted-r4-recovery", reference).status
            == "restored"
        )
        assert _snapshot(root) == initial
        assert launches.read_text() == "x"
        record_property("explicit_recovery", "restored; target-conflict no-write")
