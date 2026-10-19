from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_run_config import make_fixture, write_config

from agent_harness.admission import AdmissionRecord
from agent_harness.agent import AgentTurnResult, CapturedText, TurnBudgets
from agent_harness.context import ContextPacket
from agent_harness.contract import TaskContract
from agent_harness.lock import RepositoryLock
from agent_harness.repository import resolve_repository_state_location
from agent_harness.run_config import load_run_config
from agent_harness.task_run import execute_run


class FakeAgent:
    def __init__(self, mode: str = "pass") -> None:
        self.mode = mode
        self.preparations = 0

    def prepare(self, repository_root: Path) -> None:
        self.preparations += 1

    def run(
        self,
        contract: TaskContract,
        admission: AdmissionRecord,
        context: ContextPacket,
        *,
        budgets: TurnBudgets,
        sandbox: str = "workspace-write",
    ) -> AgentTurnResult:
        root = Path(context.repository_root)
        (root / "value.txt").write_text("after")
        if self.mode == "protected":
            (root / "check.py").write_text("pass\n")
        capture = CapturedText("secret-output", 13, False)
        return AgentTurnResult(
            ("fake",),
            str(root),
            "timeout"
            if self.mode == "timeout"
            else "nonzero_exit"
            if self.mode == "fail"
            else "successful_exit",
            1 if self.mode == "fail" else -9 if self.mode == "timeout" else 0,
            0.01,
            capture,
            capture,
            capture,
            "available",
        )


@pytest.mark.parametrize("mode", ["pass", "fail", "timeout", "protected"])
def test_real_loop_one_fake_turn_and_redacted_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    agent = FakeAgent(mode)
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: agent)
    outcome, code = execute_run(config)
    assert code == (0 if mode == "pass" else 1)
    assert (
        outcome["status"] == "pass" if mode == "pass" else outcome["status"] != "pass"
    )
    assert agent.preparations == 1
    assert outcome["config_sha256"] == config.config_sha256
    assert outcome["initial_head"] == data["expected_head"]
    state_dir = Path(str(outcome["state_dir"]))
    for artifact in state_dir.rglob("*"):
        if artifact.is_file():
            assert "secret-output" not in artifact.read_text()
    assert not (state_dir.parent.parent / "lock").exists()


@pytest.mark.parametrize("reason", ["head", "dirty", "held", "history"])
def test_preflight_rejects_before_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    path, data = make_fixture(tmp_path)
    if reason == "head":
        data["expected_head"] = "0" * 40
    config = load_run_config(path, write_config(path, data), "human")
    agent = FakeAgent()
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: agent)
    if reason == "dirty":
        (Path(data["repository"]) / "untracked").touch()
    location = resolve_repository_state_location(data["repository"], data["state_root"])
    lock = RepositoryLock(location.state_directory, timeout_seconds=1000)
    if reason == "held":
        lock.acquire()
    if reason == "history":
        prior = location.state_directory / "runs" / config.config_sha256
        prior.mkdir(parents=True)
    outcome, code = execute_run(config)
    if reason == "held":
        lock.release()
    assert code in (1, 2)
    assert outcome["status"] != "pass"
    assert agent.preparations == 0


def test_release_error_cannot_return_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: FakeAgent())
    original = RepositoryLock.release

    def release(self: RepositoryLock) -> None:
        original(self)
        raise ValueError("private exception text")

    monkeypatch.setattr(RepositoryLock, "release", release)
    outcome, code = execute_run(config)
    assert code == 1
    assert outcome["reason"] == "lock_release_failed"
    assert "private exception text" not in json.dumps(outcome)


@pytest.mark.parametrize("alias", ["state", "repository"])
def test_alias_introduced_after_approval_rejected_before_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str
) -> None:
    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    agent = FakeAgent()
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: agent)
    if alias == "state":
        target = tmp_path / "other-state"
        target.mkdir()
        Path(data["state_root"]).symlink_to(target, target_is_directory=True)
    else:
        original = Path(data["repository"])
        target = original.with_name("relocated-repository")
        original.rename(target)
        original.symlink_to(target, target_is_directory=True)
    outcome, code = execute_run(config)
    assert code == 2
    assert outcome["status"] == "blocked"
    assert agent.preparations == 0
    assert not (target / "repositories").exists()
    assert (tmp_path / "state").is_symlink() or not (tmp_path / "state").exists()


def test_short_run_cannot_expire_long_cli_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, data = make_fixture(tmp_path)
    data["budgets"]["timeout_seconds"] = 1
    data["verification"]["timeout_seconds"] = 1
    data["total_timeout_seconds"] = 20
    config = load_run_config(path, write_config(path, data), "human")
    location = resolve_repository_state_location(config.repository, config.state_root)
    holder = RepositoryLock(
        location.state_directory,
        timeout_seconds=300,
        now=lambda: 0,
        owner_token="long-cli-holder",
    )
    original_lease = holder.acquire()
    agent = FakeAgent()
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: agent)

    def observer_lock(
        state_directory: Path | str, *, timeout_seconds: float
    ) -> RepositoryLock:
        return RepositoryLock(
            state_directory,
            timeout_seconds=timeout_seconds,
            now=lambda: 90,
            owner_token="short-cli-observer",
        )

    monkeypatch.setattr("agent_harness.task_run.RepositoryLock", observer_lock)
    outcome, code = execute_run(config)
    assert code == 2
    assert outcome["status"] == "blocked"
    assert agent.preparations == 0
    lease = json.loads((holder.path / "lease.json").read_text())
    assert lease["owner_token"] == original_lease.owner_token
    assert lease["heartbeat_at"] == original_lease.heartbeat_at
    holder.release()
