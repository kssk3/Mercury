from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_run_config import make_fixture, write_config

from agent_harness.cli import main


def test_run_requires_explicit_approval_before_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, data = make_fixture(tmp_path)
    assert main(["run", "--config", str(path)]) == 2
    captured = capsys.readouterr()
    assert "invalid_run_config" in captured.err
    assert not Path(data["state_root"]).exists()


def test_run_dispatch_json_without_raw_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_task_run import FakeAgent

    path, data = make_fixture(tmp_path)
    digest = write_config(path, data)
    monkeypatch.setattr("agent_harness.task_run._adapter", lambda config: FakeAgent())
    assert (
        main(
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
        == 0
    )
    captured = capsys.readouterr()
    outcome = json.loads(captured.out)
    assert outcome["status"] == "pass"
    assert outcome["criteria_passed"] is True
    assert "secret-output" not in captured.out
    assert captured.err == ""
