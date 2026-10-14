"""One explicitly approved Codex task in an owned disposable Git fixture."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agent_harness.adapter import CodexCLIAdapter, CodexTurnResult, TurnBudgets
from agent_harness.admission import AdmissionRecord, record_admission
from agent_harness.context import ContextPacket, build_context_packet
from agent_harness.contract import TaskContract
from agent_harness.journal import EventJournal
from agent_harness.lock import RepositoryLock
from agent_harness.loop import run_loop
from agent_harness.policy import VerificationPolicy
from agent_harness.retry import RetryLimits
from agent_harness.state import CurrentStateStore

BUG = "def discount(price, percent):\n    return price * (1 - percent / 50)\n"
CHECK = (
    "from calculator import discount\n"
    "assert discount(100, 10) == 90\n"
    "assert discount(200, 25) == 150\n"
    "assert discount(0, 10) == 0\n"
)


def git(root: Path, *args: str) -> None:
    """Only fixture-local initialization, with external hooks/config disabled."""
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0"
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
            *args,
        ),
        env=env,
        check=True,
        capture_output=True,
        timeout=10,
    )


def configuration() -> dict[str, object]:
    """Report selected non-secret settings without modifying config/auth/rules."""
    config = (
        Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    )
    if not config.exists():
        return {"model": "UNKNOWN", "effort": "UNKNOWN", "provenance": "CLI defaults"}
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    return {
        "model": data.get("model", "UNKNOWN"),
        "effort": data.get("model_reasoning_effort", "UNKNOWN"),
        "provenance": "saved top-level config; native events authoritative when available",
    }


class RecordingAdapter(CodexCLIAdapter):
    """Retain private native receipts outside the fixture; never print raw text."""

    def __init__(self, executable: str, evidence: Path) -> None:
        super().__init__(executable)
        self.evidence = evidence
        self.result: CodexTurnResult | None = None
        self.attempts = 0

    def run(
        self,
        contract: TaskContract,
        admission: AdmissionRecord,
        context: ContextPacket,
        *,
        budgets: TurnBudgets,
        sandbox: Literal["read-only", "workspace-write"] = "workspace-write",
    ) -> CodexTurnResult:
        if self.attempts:
            raise RuntimeError("one native attempt only; retry/resume refused")
        self.attempts += 1
        self.result = super().run(
            contract, admission, context, budgets=budgets, sandbox=sandbox
        )
        (self.evidence / "private-native.json").write_text(
            json.dumps(asdict(self.result), ensure_ascii=True),
            encoding="utf-8",
        )
        return self.result

    def facts(self) -> dict[str, object]:
        if self.result is None:
            return {"attempts": self.attempts, "status": "UNKNOWN", "usage": "UNKNOWN"}
        usage: object = "UNKNOWN"
        observed_model: object = "UNKNOWN"
        for line in self.result.stdout.text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "turn.completed" and isinstance(
                event.get("usage"), dict
            ):
                raw = event["usage"]
                usage = {
                    key: raw[key]
                    for key in ("input_tokens", "cached_input_tokens", "output_tokens")
                    if type(raw.get(key)) is int and raw[key] >= 0
                } or "UNKNOWN"
            if isinstance(event.get("model"), str):
                observed_model = event["model"]
        return {
            "attempts": self.attempts,
            "status": self.result.process_status,
            "exit_code": self.result.exit_code,
            "elapsed_seconds": self.result.duration_seconds,
            "usage": usage,
            "observed_model": observed_model,
            "retention": {
                key: {"bytes": value.retained_bytes, "truncated": value.truncated}
                for key, value in (
                    ("stdout", self.result.stdout),
                    ("stderr", self.result.stderr),
                    ("final", self.result.final_message),
                )
                if value is not None
            },
            "final_output_status": self.result.final_output_status,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-codex-demo", action="store_true")
    parser.add_argument(
        "--codex-executable", default="codex", help="trusted external executable"
    )
    parser.add_argument(
        "--evidence-parent",
        type=Path,
        default=Path(tempfile.gettempdir()),
        help="existing directory outside the fixture; owned child receipts retained",
    )
    args = parser.parse_args(argv)
    if not args.approve_codex_demo:
        parser.error("read docs/codex-quickstart.md and pass --approve-codex-demo")
    if any(os.environ.get(key) for key in ("OPENAI_API_KEY", "CODEX_API_KEY")):
        parser.error(
            "remove API-key overrides; this example requires saved ChatGPT login"
        )
    executable = shutil.which(args.codex_executable)
    if executable is None:
        parser.error("trusted Codex executable unavailable")
    # Auth metadata only; do not store or display raw auth output.
    try:
        auth = subprocess.run(
            (executable, "login", "status"), capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        parser.error(
            "ChatGPT authentication preflight UNKNOWN; native task not started"
        )
    if auth.returncode != 0 or "chatgpt" not in (auth.stdout + auth.stderr).lower():
        parser.error("saved ChatGPT authentication required; no billing fallback")
    selected_config = configuration()
    evidence = Path(
        tempfile.mkdtemp(prefix="mercury-codex-", dir=args.evidence_parent)
    ).resolve()
    evidence.chmod(0o700)
    started = time.monotonic()
    summary: dict[str, object] = {
        "mode": "native-codex",
        "status": "unknown",
        "reason": "not_completed",
        "configuration": selected_config,
        "authentication": "saved ChatGPT login",
        "evidence_directory": str(evidence),
        "private_raw_evidence": "private-native.json",
        "baseline": "UNKNOWN",
        "development": "UNKNOWN",
        "final": "UNKNOWN",
        "criteria_passed": False,
        "scope_clear": False,
        "protection_clear": False,
        "changed_paths": [],
        "native": {"attempts": 0, "usage": "UNKNOWN"},
    }
    adapter = RecordingAdapter(executable, evidence)
    try:
        with tempfile.TemporaryDirectory(prefix="mercury-discount-") as directory:
            scratch = Path(directory).resolve()
            root = scratch / "repository"
            root.mkdir()
            (scratch / "empty-hooks").mkdir()
            (root / "calculator.py").write_text(BUG, encoding="utf-8")
            (root / "check.py").write_text(CHECK, encoding="utf-8")
            git(root, "init", "--template=", "--initial-branch=demo")
            git(root, "add", "calculator.py", "check.py")
            git(root, "commit", "-m", "Create disposable discount fixture")
            criterion = "discount(100,10)=90, discount(200,25)=150, discount(0,10)=0"
            contract = TaskContract(
                goal="Fix discount in calculator.py only. Work only in this disposable fixture. "
                "Do not use extra agents, services, network, or installs. Do not edit check.py "
                "or Git metadata; do not commit. Return a brief result.",
                allowed_paths=("calculator.py",),
                protected_paths=("check.py",),
                completion_criteria=(criterion,),
            )
            admission = record_admission(
                contract,
                approver="demo-human",
                approved_at=datetime.now(UTC).isoformat(),
            )
            command = (sys.executable, "-B", "check.py")
            policy = VerificationPolicy((command,), ".", 5)
            context = build_context_packet(
                root, source_paths=("calculator.py", "check.py"), budget_bytes=4096
            )
            state = evidence / "state"
            lock = RepositoryLock(state, timeout_seconds=260)
            journal = EventJournal(state)
            lock.acquire()
            try:
                result = run_loop(
                    adapter,
                    contract,
                    admission,
                    context,
                    policy=policy,
                    criterion_commands={criterion: (command,)},
                    journal=journal,
                    state_store=CurrentStateStore(state),
                    budgets=TurnBudgets(120, 16384, 65536, 16384, 8192),
                    limits=RetryLimits(0, 1, 4, 180),
                    redactor=lambda text: "[private native text omitted from state]",
                    max_output_bytes=65536,
                    checkpoint_verification=True,
                )
            finally:
                lock.release()
            summary.update(
                status=result.status,
                reason=result.reason,
                command_boundaries=result.usage.commands,
                criteria_passed=bool(result.criteria)
                and all(item.passed for item in result.criteria),
            )
            if result.report is not None:
                summary["baseline"] = (
                    "FAIL"
                    if any(
                        item.exit_code != 0 or item.timed_out
                        for item in result.report.profile.baseline.results
                    )
                    else "PASS"
                )
                summary[
                    "protection_clear"
                ] = not result.report.protected_input_violations
                summary["final"] = result.report.overall_kind.value.upper()
            for event in journal.read():
                if (
                    event.get("event") == "verification_checkpoint"
                    and event.get("pending_phase") == "development"
                ):
                    baseline = event.get("baseline")
                    if isinstance(baseline, list):
                        summary["baseline"] = (
                            "FAIL"
                            if any(item.get("exit_code") != 0 for item in baseline)
                            else "PASS"
                        )
                if (
                    event.get("event") == "verification_phase_intent"
                    and event.get("phase") == "final"
                ):
                    summary["development"] = "PASS"
                if event.get("event") in {"scope_decision", "callback_scope_decision"}:
                    summary["scope_clear"] = event.get("proceed", False)
                    delta = event.get("delta")
                    if isinstance(delta, dict):
                        summary["changed_paths"] = sorted(
                            set(
                                delta.get("added", [])
                                + delta.get("modified", [])
                                + delta.get("deleted", [])
                            )
                        )
        summary["fixture_removed"] = True
    except Exception as error:
        summary.update(status="unknown", reason=type(error).__name__)
    finally:
        summary["native"] = adapter.facts()
        summary["elapsed_seconds"] = time.monotonic() - started
        (evidence / "summary.json").write_text(
            json.dumps(summary, sort_keys=True, indent=2), encoding="utf-8"
        )
    print(json.dumps(summary, sort_keys=True))
    return int(summary["status"] != "pass" or summary["criteria_passed"] is not True)


if __name__ == "__main__":
    raise SystemExit(main())
