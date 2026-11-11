"""Approved CLI retries reuse the existing loop's offline safety boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from test_run_config import make_fixture, write_config
from test_task_run import FakeAgent

from agent_harness.cli import main
from agent_harness.context import ContextPacket
from agent_harness.diagnostics import DiagnosticsObserver
from agent_harness.inspection import summarize_execution
from agent_harness.journal import EventJournal
from agent_harness.loop import run_loop
from agent_harness.ports.agent import AgentTurnResult
from agent_harness.run_config import load_run_config
from agent_harness.task_run import execute_run


class CorrectionAgent(FakeAgent):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode
        self.contexts: list[ContextPacket] = []

    def run(self, *args: Any, **kwargs: Any) -> AgentTurnResult:
        context: ContextPacket = args[2]
        self.contexts.append(context)
        result = super().run(*args, **kwargs)
        root = Path(context.repository_root)
        if context.retry_feedback is None or self.mode == "repeated":
            (root / "value.txt").write_text("broken")
        if self.mode == "scope":
            (root / "unexpected.txt").write_text("outside admitted scope")
        if self.mode == "uncertain":
            raise RuntimeError("unobserved native result")
        return replace(
            result, diagnostics=DiagnosticsObserver(0.0, 0.0).finish(1.0, 1.0)
        )


def retry_fixture(tmp_path: Path, mode: str) -> tuple[Path, dict[str, Any], bytes]:
    path, data = make_fixture(tmp_path)
    root = Path(data["repository"])
    checker = root / "check.py"
    counter = tmp_path / "checker-count"
    checker.write_text(
        "from pathlib import Path\n"
        f"counter = Path({str(counter)!r})\n"
        "counter.write_text(counter.read_text() + 'x' if counter.exists() else 'x')\n"
        + (
            "assert Path('value.txt').read_text() == 'after'\n"
            if mode == "preexisting"
            else "assert Path('value.txt').read_text() in ('before', 'after')\n"
        )
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for arguments in (
        ["add", "."],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=f@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "freeze checker",
        ],
    ):
        subprocess.run(
            ["/usr/bin/git", "-C", str(root), *arguments],
            env=env,
            check=True,
            capture_output=True,
            timeout=10,
        )
    data["expected_head"] = subprocess.run(
        ["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    if mode != "default":
        data["retry"] = {
            "max_retries": 0 if mode == "zero" else 2 if mode == "repeated" else 1,
            "max_same_failure": 1 if mode in ("zero", "same_failure") else 2,
        }
    data["total_timeout_seconds"] = 80
    return path, data, checker.read_bytes()


@pytest.mark.parametrize(
    "mode,status,reason,turns",
    [
        ("correct", "pass", "final_verification", 2),
        ("default", "human", "retry_budget", 1),
        ("zero", "human", "retry_budget", 1),
        ("same_failure", "human", "same_failure", 1),
        ("repeated", "human", "same_failure", 2),
        ("preexisting", "human", "unsafe_failure", 1),
        ("scope", "human", "unsafe_failure", 1),
        ("protected", "human", "unsafe_failure", 1),
        ("uncertain", "unknown", "native_launch_unknown", 1),
    ],
)
def test_approved_cli_retry_preserves_policy_and_historical_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    status: str,
    reason: str,
    turns: int,
) -> None:
    path, data, frozen_checker = retry_fixture(tmp_path, mode)
    digest = write_config(path, data)
    agent = CorrectionAgent(mode)
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: agent)
    code = main(
        [
            "run",
            "--config",
            str(path),
            "--approve-config-sha256",
            digest,
            "--approver",
            "human",
        ]
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert code == (0 if status == "pass" else 1)
    outcome = json.loads(captured.out)
    assert (outcome["status"], outcome["reason"]) == (status, reason)
    assert len(agent.contexts) == turns
    assert agent.preparations == turns
    assert outcome["config_sha256"] == digest
    assert outcome["initial_head"] == data["expected_head"]
    if mode != "protected":
        assert (Path(data["repository"]) / "check.py").read_bytes() == frozen_checker
    state_dir = Path(outcome["state_dir"])
    summary = summarize_execution(state_dir)
    assert summary["recorded_status"] == status
    assert summary["recorded_reason"] == reason
    events = EventJournal(state_dir).read()
    assert events[0]["config_sha256"] == digest
    observations = [e for e in events if e["event"] == "turn_observation"]
    if mode != "uncertain":
        assert len(observations) == turns
        for observation in observations:
            diagnostic = observation["diagnostics"]
            assert isinstance(diagnostic, dict)
            assert diagnostic["clock"]["status"] == "consistent"
    assert agent.contexts[0].retry_feedback is None
    if mode != "correct":
        return
    feedback = agent.contexts[1].retry_feedback
    assert feedback is not None
    command = tuple(data["verification"]["commands"][0])
    assert feedback.commands == (command,)
    assert [
        (finding.kind, finding.attribution) for finding in feedback.assessment.findings
    ] == [("verification", "new")]
    intents = [event for event in events if event["event"] == "turn_intent"]
    assert feedback.assessment.attempt_id == intents[0]["attempt_id"]
    assert intents[0]["contract_sha256"] == intents[1]["contract_sha256"]
    assert (
        intents[1]["context_sha256"]
        == hashlib.sha256(agent.contexts[1].to_json().encode()).hexdigest()
    )
    assert agent.contexts[0].sources == agent.contexts[1].sources
    assert "secret-output" not in agent.contexts[1].to_json()
    assert (tmp_path / "checker-count").read_text() == "xxxx"
    assert outcome["criteria_passed"] is True
    assert outcome["command_boundaries"] == 6
    assert [
        event["technical_kind"]
        for event in events
        if event["event"] == "attempt_verification"
    ] == ["fail", "pass"]
    assert main(["status", "--state-dir", str(state_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["recorded_status"] == "pass"


@pytest.mark.parametrize(
    "change", ["retry_bytes", "retry_limits", "same_failure", "commands", "total"]
)
def test_retry_approval_is_revalidated_before_any_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    path, data, _ = retry_fixture(tmp_path, "correct")
    config = load_run_config(path, write_config(path, data), "human")
    if change == "retry_bytes":
        data["retry"]["max_same_failure"] = 1
        write_config(path, data)
    else:
        changes = {
            "retry_limits": {"max_retries": 2},
            "same_failure": {"max_same_failure": 1},
            "commands": {"max_commands": 6},
            "total": {"max_seconds": 79},
        }
        config = replace(config, limits=replace(config.limits, **changes[change]))
    agent = CorrectionAgent("correct")
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: agent)
    outcome, code = execute_run(config)
    assert (code, outcome["status"], outcome["reason"]) == (
        2,
        "blocked",
        "preflight_failed",
    )
    assert agent.preparations == 0
    assert not config.state_root.exists()


def test_cli_retry_stops_when_actual_elapsed_time_exhausts_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, data, _ = retry_fixture(tmp_path, "correct")
    config = load_run_config(path, write_config(path, data), "human")
    clock = [0.0]

    class ElapsedAgent(CorrectionAgent):
        def run(self, *args: Any, **kwargs: Any) -> AgentTurnResult:
            result = super().run(*args, **kwargs)
            clock[0] = 75.0
            return result

    agent = ElapsedAgent("correct")
    monkeypatch.setattr(
        "agent_harness.task_run.run_loop", partial(run_loop, monotonic=lambda: clock[0])
    )
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: agent)
    outcome, code = execute_run(config)
    assert (code, outcome["status"], outcome["reason"]) == (1, "human", "time_budget")
    assert len(agent.contexts) == 1
    assert agent.preparations == 1
