"""Fresh explicitly approved runs over the existing loop and repository lease.

Callers must share state_root: leases in different roots do not coordinate.
Empty run directories count as history. Captured output content is omitted from
saved evidence by default; structured status remains, reducing diagnostics.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agent_harness.adapter import CodexCLIAdapter
from agent_harness.admission import AdmissionRecord, record_admission
from agent_harness.agent import AgentAdapter, AgentTurnResult, TurnBudgets
from agent_harness.claude_adapter import ClaudeCLIAdapter
from agent_harness.context import ContextPacket, build_context_packet
from agent_harness.contract import TaskContract
from agent_harness.journal import EventJournal
from agent_harness.lock import RepositoryLock
from agent_harness.loop import run_loop
from agent_harness.repository import (
    git_executable,
    git_read_environment,
    resolve_repository_state_location,
    validate_runtime_state_directory,
)
from agent_harness.run_config import (
    RunConfig,
    load_run_config,
    validate_run_verification,
)
from agent_harness.state import CurrentStateStore

# Lease expiry uses the observer's horizon, so every CLI invocation must share
# a common horizon, independently of the selected loop budget. Renewal covers
# setup and execution; API callers sharing this root need a compatible policy.
_CLI_LEASE_SECONDS = 300
_CLI_HEARTBEAT_SECONDS = 30.0


class _LeaseRenewal:
    """Keep one cooperative owner live; join before releasing its lease."""

    def __init__(self, lock: RepositoryLock) -> None:
        self.lock = lock
        self.stopped = threading.Event()
        self.failed = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.lock.heartbeat()
        self.thread = threading.Thread(
            target=self._renew, name="mercury-run-lease", daemon=True
        )
        self.thread.start()

    def _renew(self) -> None:
        try:
            while not self.stopped.wait(_CLI_HEARTBEAT_SECONDS):
                self.lock.heartbeat()
        except BaseException:
            # Exceptions belong to the owning caller, not the background thread.
            self.failed.set()

    def check(self) -> None:
        if self.failed.is_set():
            raise RuntimeError("repository lease renewal failed")

    def stop(self) -> None:
        self.stopped.set()
        if self.thread is not None and self.thread.ident is not None:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                raise RuntimeError("repository lease renewal did not stop")
        self.check()


class _LeaseGuardedAdapter:
    """Do not enter a new native phase after an observed renewal failure."""

    def __init__(self, adapter: AgentAdapter, renewal: _LeaseRenewal) -> None:
        self.adapter = adapter
        self.renewal = renewal

    def prepare(self, repository_root: Path) -> None:
        self.renewal.check()
        self.adapter.prepare(repository_root)

    def run(
        self,
        contract: TaskContract,
        admission: AdmissionRecord,
        context: ContextPacket,
        *,
        budgets: TurnBudgets,
        sandbox: Literal["read-only", "workspace-write"] = "workspace-write",
    ) -> AgentTurnResult:
        self.renewal.check()
        return self.adapter.run(
            contract, admission, context, budgets=budgets, sandbox=sandbox
        )


def _adapter(config: RunConfig) -> AgentAdapter:
    if config.backend == "codex":
        return CodexCLIAdapter(config.executable)
    if config.backend == "claude":
        return ClaudeCLIAdapter(config.executable)
    raise ValueError("unsupported backend")


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
    # Porcelain can hide changed tracked bytes behind these index flags.
    index = _git(config, "ls-files", "--cached", "-v", "-z")
    if any(entry[:1] == b"S" or entry[:1].islower() for entry in index.split(b"\0")):
        raise ValueError("unsupported_hidden_index_flags")
    if _git(config, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise ValueError("workspace_not_clean")


def _omit_output(text: str) -> str:
    return "[captured output omitted]"


def execute_run(config: RunConfig) -> tuple[dict[str, object], int]:
    """Run once; finalization errors require exact observed commit evidence."""
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
    renewal: _LeaseRenewal | None = None
    held = False
    store: CurrentStateStore | None = None
    code = 2
    try:
        if type(config.backend) is not str or config.backend not in {"codex", "claude"}:
            raise ValueError("unsupported backend")
        if (
            type(config.config_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", config.config_sha256) is None
        ):
            raise ValueError("invalid configuration digest")
        approved = load_run_config(
            config.config_path, config.config_sha256, config.approver
        )
        if config != approved:
            raise ValueError("configuration differs from approved bytes")
        # Nested caller-owned mappings can still mutate on a frozen dataclass.
        # Execute the fresh independent snapshot, never the compared caller object.
        config = approved
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
        renewal = _LeaseRenewal(lock)
        renewal.start()
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
        admission = record_admission(
            config.contract,
            approver=config.approver,
            approved_at=datetime.now(UTC).isoformat(),
        )
        renewal.check()
        code = 1
        run_directory.mkdir(parents=True, exist_ok=False)
        journal = EventJournal(run_directory)
        journal.append(
            {
                "event": "run_binding",
                "schema_version": 2,
                "initial_head": outcome["initial_head"],
                "config_sha256": config.config_sha256,
                "backend": config.backend,
            }
        )
        store = CurrentStateStore(run_directory)
        result = run_loop(
            _LeaseGuardedAdapter(_adapter(config), renewal),
            config.contract,
            admission,
            context,
            policy=config.policy,
            criterion_commands=config.criterion_commands,
            journal=journal,
            state_store=store,
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
        renewal.check()
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
        renewal_failed = False
        renewal_running = False
        if renewal is not None:
            try:
                renewal.stop()
            except (OSError, ValueError, RuntimeError, KeyboardInterrupt):
                renewal_failed = True
                renewal_running = (
                    renewal.thread is not None and renewal.thread.is_alive()
                )
                outcome.update(status="unknown", reason="execution_failed")
                code = 1
        if lock is not None:
            try:
                if renewal_running:
                    # Never release underneath an outstanding owner operation.
                    pass
                elif held and not renewal_failed:
                    lock.release()
                else:
                    # acquire may commit before returning or held is assigned.
                    lock.release_if_owned()
            except (OSError, ValueError, RuntimeError, KeyboardInterrupt):
                outcome.update(status="unknown", reason="lock_release_failed")
                code = 1
        # Version-2 binding declares finalization pending before the loop can
        # persist PASS. Missing finalization stays UNKNOWN even if this write fails.
        if store is not None:
            intended_state: str | None = None
            try:
                current = store.read()
                if current is None:
                    raise ValueError("missing loop state")
                current["run_result"] = {
                    "status": outcome["status"],
                    "reason": outcome["reason"],
                }
                intended_state = json.dumps(current, sort_keys=True)
                store.write(current)
            except (OSError, ValueError, RuntimeError, KeyboardInterrupt):
                # Atomic replacement can finish before cleanup is interrupted.
                # Preserve the selected outcome only if the entire intended
                # state is observed, including an already-selected UNKNOWN.
                committed = False
                if intended_state is not None:
                    try:
                        committed = (
                            json.dumps(store.read(), sort_keys=True) == intended_state
                        )
                    except (OSError, ValueError, RuntimeError, KeyboardInterrupt):
                        pass
                if not committed:
                    outcome.update(status="unknown", reason="finalization_failed")
                    code = 1
    return outcome, code
