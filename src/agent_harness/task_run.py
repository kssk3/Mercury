"""Fresh explicitly approved runs over the existing loop and repository lease.

Callers must share state_root: leases in different roots do not coordinate.
Empty run directories count as history. Captured output content is omitted from
saved evidence by default; structured status remains, reducing diagnostics.
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime

from agent_harness.adapter import CodexCLIAdapter
from agent_harness.admission import record_admission
from agent_harness.agent import AgentAdapter
from agent_harness.claude_adapter import ClaudeCLIAdapter
from agent_harness.context import build_context_packet
from agent_harness.journal import EventJournal
from agent_harness.lock import RepositoryLock
from agent_harness.loop import run_loop
from agent_harness.repository import (
    git_executable,
    git_read_environment,
    resolve_repository_state_location,
    validate_runtime_state_directory,
)
from agent_harness.run_config import RunConfig, validate_run_verification
from agent_harness.state import CurrentStateStore

# Lease expiry uses the observer's horizon, so every CLI invocation must share
# the maximum admitted loop budget (240s) plus 60s setup/cleanup margin. API
# callers sharing this state root need a compatible horizon/heartbeat policy.
_CLI_LEASE_SECONDS = 300


def _adapter(config: RunConfig) -> AgentAdapter:
    if config.backend == "codex":
        return CodexCLIAdapter(config.executable)
    return ClaudeCLIAdapter(config.executable)


def _git(config: RunConfig, *arguments: str) -> bytes:
    result = subprocess.run(
        [
            git_executable(),
            "--no-replace-objects",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(config.repository),
            *arguments,
        ],
        env=git_read_environment(),
        capture_output=True,
        timeout=10,
        check=True,
    )
    return result.stdout


def _baseline(config: RunConfig, outcome: dict[str, object]) -> None:
    observed = _git(config, "rev-parse", "HEAD").decode().strip()
    if outcome["initial_head"] is None:
        outcome["initial_head"] = observed
    if observed != config.expected_head:
        raise ValueError("baseline_mismatch")
    if _git(config, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise ValueError("workspace_not_clean")


def _omit_output(text: str) -> str:
    return "[captured output omitted]"


def execute_run(config: RunConfig) -> tuple[dict[str, object], int]:
    """Run once; interruption/persistence/lease errors cannot produce PASS."""
    outcome: dict[str, object] = {
        "schema_version": 1,
        "status": "blocked",
        "reason": "preflight_failed",
        "backend": config.backend,
        "repository": str(config.repository),
        "initial_head": None,
        "config_sha256": config.config_sha256,
        "state_dir": None,
        "attempts": 0,
        "command_boundaries": 0,
        "criteria_passed": False,
    }
    lock: RepositoryLock | None = None
    held = False
    code = 2
    try:
        if (
            type(config.config_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", config.config_sha256) is None
        ):
            raise ValueError("invalid configuration digest")
        validate_runtime_state_directory(config.state_root, config.repository)
        validate_runtime_state_directory(config.state_root)
        location = resolve_repository_state_location(
            config.repository, config.state_root
        )
        if location.repository_root != config.repository:
            raise ValueError("repository_rebound")
        runs_directory = location.state_directory / "runs"
        run_directory = runs_directory / config.config_sha256
        if run_directory.resolve().parent != runs_directory.resolve():
            raise ValueError("run directory escaped evidence root")
        validate_runtime_state_directory(run_directory, config.repository)
        validate_runtime_state_directory(run_directory)
        validate_run_verification(config.repository, config.contract, config.policy)
        outcome["state_dir"] = str(run_directory)
        if run_directory.exists():
            raise ValueError("existing_history")
        _baseline(config, outcome)
        lock = RepositoryLock(
            location.state_directory, timeout_seconds=_CLI_LEASE_SECONDS
        )
        lock.acquire()
        held = True
        code = 1
        _baseline(config, outcome)
        validate_runtime_state_directory(run_directory, config.repository)
        validate_runtime_state_directory(run_directory)
        validate_run_verification(config.repository, config.contract, config.policy)
        # Reserve serialized-contract/envelope overhead conservatively.
        context_budget = (
            config.budgets.input_bytes
            - len(config.contract.to_json().encode("utf-8"))
            - 1024
        )
        context = build_context_packet(
            config.repository,
            source_paths=config.context_paths,
            budget_bytes=context_budget,
        )
        run_directory.mkdir(parents=True, exist_ok=False)
        journal = EventJournal(run_directory)
        journal.append(
            {
                "event": "run_binding",
                "schema_version": 1,
                "initial_head": outcome["initial_head"],
                "config_sha256": config.config_sha256,
                "backend": config.backend,
            }
        )
        admission = record_admission(
            config.contract,
            approver=config.approver,
            approved_at=datetime.now(UTC).isoformat(),
        )
        result = run_loop(
            _adapter(config),
            config.contract,
            admission,
            context,
            policy=config.policy,
            criterion_commands=config.criterion_commands,
            journal=journal,
            state_store=CurrentStateStore(run_directory),
            budgets=config.budgets,
            limits=config.limits,
            redactor=_omit_output,
            max_output_bytes=1024,
        )
        criteria_passed = bool(result.criteria) and all(
            item.passed for item in result.criteria
        )
        outcome.update(
            status=result.status,
            reason=result.reason,
            attempts=len(result.attempt_ids),
            command_boundaries=result.usage.commands,
            criteria_passed=criteria_passed,
        )
        code = 0 if result.status == "pass" and criteria_passed else 1
    except KeyboardInterrupt:
        outcome.update(status="unknown", reason="interrupted")
        code = 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Never leak exception/config/credential strings into stdout/stderr.
        outcome.update(
            status="blocked" if code == 2 else "unknown",
            reason="preflight_failed" if code == 2 else "execution_failed",
        )
    finally:
        if held and lock is not None:
            try:
                lock.release()
            except (OSError, ValueError, RuntimeError):
                outcome.update(status="unknown", reason="lock_release_failed")
                code = 1
    return outcome, code
