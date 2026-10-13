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

from agent_harness.adapter import (
    CapturedText,
    CodexCLIAdapter,
    CodexTurnResult,
    TurnBudgets,
)
from agent_harness.admission import AdmissionRecord, admit, record_admission
from agent_harness.context import ContextPacket, build_context_packet
from agent_harness.contract import TaskContract
from agent_harness.journal import EventJournal
from agent_harness.lock import RepositoryLock
from agent_harness.loop import run_loop
from agent_harness.policy import VerificationPolicy
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
    ) -> CodexTurnResult:
        admit(contract, admission)
        if sandbox != "workspace-write":
            raise ValueError("the fixture requires workspace-write")
        root = Path(context.repository_root)
        (root / "value.txt").write_text("after\n", encoding="utf-8")
        final = f"Simulated fixture edit complete. {MARKER}"
        if len(final.encode("utf-8")) > budgets.final_bytes:
            raise ValueError("fixture final output exceeds its budget")
        empty = CapturedText("", 0, False)
        return CodexTurnResult(
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
    if not parser.parse_args().approve_demo:
        parser.error("read docs/quickstart.md, then pass --approve-demo to admit it")

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


if __name__ == "__main__":
    raise SystemExit(main())
