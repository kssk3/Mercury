from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from test_run_config import make_fixture, write_config

from agent_harness.admission import AdmissionRecord
from agent_harness.agent import AgentTurnResult, CapturedText, TurnBudgets
from agent_harness.cli import main
from agent_harness.context import ContextPacket
from agent_harness.contract import TaskContract
from agent_harness.inspection import summarize_execution
from agent_harness.lock import RepositoryLock
from agent_harness.policy import VerificationPolicy
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
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
    summary = summarize_execution(state_dir)
    assert summary["recorded_status"] == outcome["status"]
    assert summary["diagnostics"] == []
    assert main(["status", "--state-dir", str(state_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["recorded_status"] == outcome["status"]
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


@pytest.mark.parametrize("phase", ["mismatch", "before_git", "under_lease"])
def test_initial_head_reports_observation_not_expectation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    actual = data["expected_head"]
    calls = 0

    def git(config: object, *arguments: str) -> bytes:
        nonlocal calls
        if phase == "before_git":
            raise ValueError("no observation")
        if arguments == ("rev-parse", "HEAD"):
            calls += 1
            return (("b" * 40 if calls == 2 else actual) + "\n").encode()
        return b""

    monkeypatch.setattr("agent_harness.task_run._git", git)
    if phase == "mismatch":
        config = replace(config, expected_head="a" * 40)
    outcome, code = execute_run(config)
    assert code in (1, 2)
    assert outcome["status"] != "pass"
    assert outcome["initial_head"] == (None if phase == "before_git" else actual)


class _DigestAlias(str):
    pass


@pytest.mark.parametrize(
    "digest",
    ["/outside", "../escape", "a" * 63, "A" * 64, None, _DigestAlias("a" * 64)],
)
def test_direct_digest_rejected_before_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, digest: object
) -> None:
    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    agent = FakeAgent()
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: agent)
    if digest == "/outside":
        digest = str(tmp_path / "escaped")
    config = replace(config, config_sha256=cast(str, digest))
    outcome, code = execute_run(config)
    assert code == 2
    assert outcome["state_dir"] is None
    assert not config.state_root.exists()
    assert agent.preparations == 0


def test_direct_state_root_in_other_worktree_has_no_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    other = tmp_path / "other"
    other.mkdir()
    _, other_data = make_fixture(other)
    state_root = Path(other_data["repository"]) / "evidence"
    agent = FakeAgent()
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: agent)
    outcome, code = execute_run(replace(config, state_root=state_root))
    assert code == 2
    assert outcome["status"] == "blocked"
    assert not state_root.exists()
    assert agent.preparations == 0


def test_direct_api_rejects_env_before_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    command = ("/usr/bin/env", f"PATH={config.repository}/bin", "checker")
    config = replace(
        config,
        policy=VerificationPolicy((command,), ".", 2),
        criterion_commands={"value correct": [list(command)]},
    )
    agent = FakeAgent()
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: agent)
    outcome, code = execute_run(config)
    assert code == 2
    assert outcome["status"] == "blocked"
    assert not config.state_root.exists()
    assert agent.preparations == 0


@pytest.mark.parametrize("backend", ["claud", "", "CODEX"])
def test_direct_backend_rejected_before_state_or_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    agent = FakeAgent()
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: agent)
    outcome, code = execute_run(replace(config, backend=backend))
    assert (code, outcome["status"], outcome["reason"]) == (
        2,
        "blocked",
        "preflight_failed",
    )
    assert not config.state_root.exists()
    assert agent.preparations == 0


@pytest.mark.parametrize("phase", ["baseline", "verification", "storage", "context"])
def test_under_lease_validation_remains_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    import agent_harness.task_run as task_run

    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    agent = FakeAgent()
    monkeypatch.setattr(task_run, "_adapter", lambda config: agent)
    if phase == "baseline":
        original_git = task_run._git
        heads = 0

        def git(config: object, *arguments: str) -> bytes:
            nonlocal heads
            if arguments == ("rev-parse", "HEAD"):
                heads += 1
                if heads == 2:
                    return b"b" * 40 + b"\n"
            return original_git(config, *arguments)  # type: ignore[arg-type]

        monkeypatch.setattr(task_run, "_git", git)
    elif phase == "context":

        def context(*args: object, **kwargs: object) -> ContextPacket:
            raise ValueError("context rejected")

        monkeypatch.setattr(task_run, "build_context_packet", context)
    else:
        name = (
            "validate_run_verification"
            if phase == "verification"
            else "validate_runtime_state_directory"
        )
        original = getattr(task_run, name)
        calls = 0

        def validate(*args: object, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            if calls == (2 if phase == "verification" else 5):
                raise ValueError("admission rejected")
            original(*args, **kwargs)

        monkeypatch.setattr(task_run, name, validate)
    outcome, code = execute_run(config)
    assert (code, outcome["status"], outcome["reason"]) == (
        2,
        "blocked",
        "preflight_failed",
    )
    assert outcome["initial_head"] == config.expected_head
    assert agent.preparations == 0
    assert not Path(str(outcome["state_dir"])).exists()


@pytest.mark.parametrize(
    "failure", ["release", "finalization", "interrupted-finalization"]
)
def test_wrapper_terminal_failure_never_leaves_readable_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from agent_harness.state import CurrentStateStore

    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: FakeAgent())
    if failure == "release":
        original_release = RepositoryLock.release

        def release(self: RepositoryLock) -> None:
            original_release(self)
            raise ValueError("release failed")

        monkeypatch.setattr(RepositoryLock, "release", release)
    else:
        original_write = CurrentStateStore.write

        def write(self: CurrentStateStore, state: object) -> None:
            if isinstance(state, dict) and "run_result" in state:
                if failure == "interrupted-finalization":
                    raise KeyboardInterrupt
                raise OSError("finalization failed")
            original_write(self, state)  # type: ignore[arg-type]

        monkeypatch.setattr(CurrentStateStore, "write", write)
    outcome, code = execute_run(config)
    summary = summarize_execution(Path(str(outcome["state_dir"])))
    assert code == 1
    assert outcome["status"] == "unknown"
    assert summary["recorded_status"] == "unknown"
    if failure == "release":
        assert summary["recorded_reason"] == outcome["reason"] == "lock_release_failed"
    else:
        assert summary["diagnostics"]


@pytest.mark.parametrize("arguments", [["-c", "pass"], ["-I", "new_checker.py"]])
def test_direct_api_rejects_unbound_interpreter_before_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    import sys

    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    command = (sys.executable, *arguments)
    config = replace(
        config,
        policy=VerificationPolicy((command,), ".", 2),
        criterion_commands={"value correct": [list(command)]},
    )
    agent = FakeAgent()
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: agent)
    outcome, code = execute_run(config)
    assert (code, outcome["status"]) == (2, "blocked")
    assert not config.state_root.exists()
    assert agent.preparations == 0


@pytest.mark.parametrize("error", [OSError, RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("release_failed", [False, True])
def test_post_commit_error_preserves_observed_terminal_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: type[BaseException],
    release_failed: bool,
) -> None:
    from agent_harness.state import CurrentStateStore

    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: FakeAgent())
    original_write = CurrentStateStore.write

    def write(self: CurrentStateStore, state: object) -> None:
        original_write(self, state)  # type: ignore[arg-type]
        if isinstance(state, dict) and "run_result" in state:
            raise error("post-commit interruption")

    monkeypatch.setattr(CurrentStateStore, "write", write)
    if release_failed:
        original_release = RepositoryLock.release

        def release(self: RepositoryLock) -> None:
            original_release(self)
            raise OSError("release failed")

        monkeypatch.setattr(RepositoryLock, "release", release)
    outcome, code = execute_run(config)
    expected = (
        ("unknown", "lock_release_failed", 1)
        if release_failed
        else ("pass", "final_verification", 0)
    )
    assert (outcome["status"], outcome["reason"], code) == expected
    state_dir = Path(str(outcome["state_dir"]))
    summary = summarize_execution(state_dir)
    assert summary["recorded_status"] == outcome["status"]
    assert summary["recorded_reason"] == outcome["reason"]
    assert summary["diagnostics"] == []
    assert main(["status", "--state-dir", str(state_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["recorded_status"] == outcome["status"]


@pytest.mark.parametrize("readback", ["mismatched", "unreadable"])
def test_post_commit_error_without_matching_evidence_stays_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, readback: str
) -> None:
    from agent_harness.state import CurrentStateStore

    path, data = make_fixture(tmp_path)
    config = load_run_config(path, write_config(path, data), "human")
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: FakeAgent())
    original_write = CurrentStateStore.write

    def write(self: CurrentStateStore, state: object) -> None:
        original_write(self, state)  # type: ignore[arg-type]
        if isinstance(state, dict) and "run_result" in state:
            saved = dict(state, unrelated_metadata=True)
            self.path.write_text(
                json.dumps(saved) if readback == "mismatched" else "not JSON"
            )
            raise KeyboardInterrupt

    monkeypatch.setattr(CurrentStateStore, "write", write)
    outcome, code = execute_run(config)
    assert (outcome["status"], outcome["reason"], code) == (
        "unknown",
        "finalization_failed",
        1,
    )
