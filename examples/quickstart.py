"""Disposable single-agent API demo; no native agent, model, or network call."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agent_harness.adapters.agents.codex import CodexCLIAdapter
from agent_harness.admission import AdmissionRecord, admit, record_admission
from agent_harness.context import ContextPacket, build_context_packet
from agent_harness.contract import TaskContract
from agent_harness.journal import EventJournal
from agent_harness.lock import RepositoryLock
from agent_harness.loop import run_loop
from agent_harness.policy import VerificationPolicy
from agent_harness.ports.agent import AgentTurnResult, CapturedText, TurnBudgets
from agent_harness.retry import RetryLimits
from agent_harness.state import CurrentStateStore

MARKER = "fixture-private-marker"
CHECK = (
    "from pathlib import Path\n"
    "assert Path('value.txt').read_text(encoding='utf-8') == 'after\\n'\n"
)


class SimulatedCodexAdapter(CodexCLIAdapter):
    """Test fixture only: replace the native invocation with one local write."""

    def __init__(self) -> None:
        super().__init__("simulated-not-executed")

    def run(
        self,
        contract: TaskContract,
        admission: AdmissionRecord,
        context: ContextPacket,
        *,
        budgets: TurnBudgets,
        sandbox: Literal["read-only", "workspace-write"] = "workspace-write",
    ) -> AgentTurnResult:
        admit(contract, admission)
        if sandbox != "workspace-write":
            raise ValueError("the fixture requires workspace-write")
        root = Path(context.repository_root)
        (root / "value.txt").write_text("after\n", encoding="utf-8")
        final = f"Simulated fixture edit complete. {MARKER}"
        if len(final.encode("utf-8")) > budgets.final_bytes:
            raise ValueError("fixture final output exceeds its budget")
        empty = CapturedText("", 0, False)
        return AgentTurnResult(
            argv=("simulated-adapter",),
            repository_root=str(root),
            process_status="successful_exit",
            exit_code=0,
            duration_seconds=0.0,
            stdout=empty,
            stderr=empty,
            final_message=CapturedText(final, len(final.encode("utf-8")), False),
            final_output_status="available",
        )


def git(root: Path, *arguments: str) -> None:
    """Configure only this disposable repository; do not inherit Git hooks."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
    )
    subprocess.run(
        (
            "git",
            "-c",
            f"core.hooksPath={root.parent / 'empty-hooks'}",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Fixture User",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(root),
            *arguments,
        ),
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def redact(text: str) -> str:
    """Only a synthetic marker policy; real callers need their own policy."""
    return text.replace(MARKER, "[REDACTED]")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--approve-demo",
        action="store_true",
        help=(
            "admit only the disposable task: change value.txt to after, protect "
            "check.py, and require its unchanged check to pass"
        ),
    )
    parser.add_argument("--fixture", choices=("value", "cart"), default="value")
    parser.add_argument(
        "--scenario",
        choices=("correct", "regression", "scope_escape", "incomplete"),
        default="correct",
    )
    parser.add_argument("--evidence-parent", type=Path)
    arguments = parser.parse_args()
    if not arguments.approve_demo:
        parser.error("read docs/quickstart.md, then pass --approve-demo to admit it")

    if arguments.fixture == "cart":
        if arguments.evidence_parent is None:
            parser.error(
                "cart runs require --evidence-parent in an existing private directory"
            )
        return cart_main(arguments.evidence_parent, arguments.scenario)
    if arguments.evidence_parent is not None or arguments.scenario != "correct":
        parser.error("--evidence-parent and --scenario require --fixture cart")

    with tempfile.TemporaryDirectory(prefix="harness-demo-") as directory:
        scratch = Path(directory).resolve()
        root = scratch / "repository"
        state_directory = scratch / "state"
        root.mkdir()
        (scratch / "empty-hooks").mkdir()
        (root / "value.txt").write_text("before\n", encoding="utf-8")
        (root / "check.py").write_text(CHECK, encoding="utf-8")
        git(root, "init", "--template=", "--initial-branch=demo")
        git(root, "add", "value.txt", "check.py")
        git(root, "commit", "-m", "Create disposable fixture")

        criterion = "value.txt contains after and check.py remains unchanged"
        contract = TaskContract(
            goal="Change the disposable fixture value from before to after",
            allowed_paths=("value.txt",),
            protected_paths=("check.py",),
            completion_criteria=(criterion,),
        )
        admission = record_admission(
            contract,
            approver="demo-human",
            approved_at=datetime.now(UTC).isoformat(),
        )
        command = (sys.executable, "check.py")
        policy = VerificationPolicy((command,), ".", 2)
        context = build_context_packet(
            root, source_paths=("value.txt",), budget_bytes=4096
        )

        # Every caller on this repository must share this lock and state location.
        lock = RepositoryLock(state_directory, timeout_seconds=120)
        lock.acquire()
        try:
            result = run_loop(
                SimulatedCodexAdapter(),
                contract,
                admission,
                context,
                policy=policy,
                criterion_commands={criterion: (command,)},
                journal=EventJournal(state_directory),
                state_store=CurrentStateStore(state_directory),
                budgets=TurnBudgets(2, 8192, 256, 256, 256),
                limits=RetryLimits(0, 1, 4, 30),
                redactor=redact,
                max_output_bytes=256,
            )
        finally:
            lock.release()

        outputs = [
            path.read_text(encoding="utf-8")
            for path in (state_directory / "outputs").glob("*.txt")
        ]
        redaction_checked = (
            bool(outputs)
            and all(MARKER not in output for output in outputs)
            and any("[REDACTED]" in output for output in outputs)
        )
        criteria_passed = len(result.criteria) == 1 and all(
            decision.passed for decision in result.criteria
        )
        print(
            json.dumps(
                {
                    "mode": "simulated",
                    "status": result.status,
                    "reason": result.reason,
                    "command_boundaries": result.usage.commands,
                    "criteria_passed": criteria_passed,
                    "redaction_checked": redaction_checked,
                },
                sort_keys=True,
            )
        )
        return int(
            result.status != "pass" or not criteria_passed or not redaction_checked
        )


def cart_main(parent: Path, scenario: str) -> int:
    from cart_fixture import (
        CartExecutor,
        SimulatedCartAdapter,
        build_cart_fixture,
        snapshots,
    )

    from agent_harness.explanation import explain_execution
    from agent_harness.inspection import summarize_execution

    parent = parent.resolve(strict=True)
    source = Path(__file__).resolve().parents[1]
    if (
        not parent.is_dir()
        or parent == source
        or source in parent.parents
        or parent.stat().st_mode & 0o077
    ):
        raise ValueError("evidence parent must be private and outside source")
    evidence = Path(tempfile.mkdtemp(prefix="cart-", dir=parent))
    state = evidence / "state"
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0"
    )
    capture: dict[str, object] = {
        "scenarioId": scenario,
        "sourceMode": "recorded_simulated",
        "runId": evidence.name,
        "capturedAt": datetime.now(UTC).isoformat(),
        "mercuryRevision": subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip(),
    }
    try:
        with tempfile.TemporaryDirectory(prefix="mercury-cart-") as directory:
            scratch = Path(directory).resolve()
            root = scratch / "repository"
            root.mkdir()
            (scratch / "empty-hooks").mkdir()
            build_cart_fixture(root)
            capture["beforeFiles"] = snapshots(root)
            git(root, "init", "--template=", "--initial-branch=demo")
            git(root, "add", ".")
            git(root, "commit", "-m", "Create disposable cart fixture")
            criteria = (
                "Clamp merchandise without changing ordinary discount",
                "Preserve original-subtotal shipping policy",
            )
            contract = TaskContract(
                "Fix negative cart merchandise; preserve shipping",
                ("cart.py",),
                ("shipping.py", "check_discount.py", "check_shipping.py"),
                criteria,
            )
            admission = record_admission(
                contract,
                approver="offline-demo-human",
                approved_at=datetime.now(UTC).isoformat(),
            )
            commands = (
                (sys.executable, "-B", "check_discount.py"),
                (sys.executable, "-B", "check_shipping.py"),
            )
            policy = VerificationPolicy(commands, ".", 3)
            executor = CartExecutor(state, scenario)
            lock = RepositoryLock(state, timeout_seconds=120)
            lock.acquire()
            try:
                result = run_loop(
                    SimulatedCartAdapter(scenario),
                    contract,
                    admission,
                    build_context_packet(
                        root, source_paths=("cart.py", "shipping.py"), budget_bytes=4096
                    ),
                    policy=policy,
                    criterion_commands=dict(
                        zip(criteria, ((commands[0],), (commands[1],)), strict=True)
                    ),
                    journal=EventJournal(state),
                    state_store=CurrentStateStore(state),
                    budgets=TurnBudgets(2, 8192, 1024, 1024, 1024),
                    limits=RetryLimits(0, 1, 8, 60),
                    redactor=lambda text: text,
                    max_output_bytes=8192,
                    checkpoint_verification=True,
                    verification_executor=executor,
                )
                capture.update(
                    recordedStatus=result.status, recordedReason=result.reason
                )
            finally:
                capture["afterFiles"] = snapshots(root)
                capture["checks"] = executor.rows
                lock.release()
            capture["summary"] = summarize_execution(state, details=True)
            capture["explanation"] = explain_execution(state)
            capture["contract"] = {
                "allowed": list(contract.allowed_paths),
                "protected": list(contract.protected_paths),
            }
    finally:
        (evidence / "capture.json").write_text(
            json.dumps(capture, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "evidence_directory": str(evidence),
                "status": capture.get("recordedStatus"),
                "reason": capture.get("recordedReason"),
            }
        )
    )
    return int(capture.get("recordedStatus") != "pass")


if __name__ == "__main__":
    raise SystemExit(main())
