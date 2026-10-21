from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import NoReturn

import pytest
from test_run_config import make_fixture, write_config

from agent_harness.cli import main


def test_preview_reports_exact_config_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path, data = make_fixture(tmp_path)
    raw = path.read_bytes() + b"\n "
    path.write_bytes(raw)
    before = {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("preview dispatched execution, adapter, or checker")

    monkeypatch.setattr("agent_harness.task_run.execute_run", forbidden)
    monkeypatch.setattr("agent_harness.task_run._adapter", forbidden)
    monkeypatch.setattr("agent_harness.runner.ControlledCommandRunner.run", forbidden)
    assert main(["run", "--preview", "--config", str(path)]) == 0
    captured = capsys.readouterr()
    preview = json.loads(captured.out)
    assert captured.err == ""
    assert preview == {
        **data,
        "config_path": str(path),
        "config_sha256": hashlib.sha256(raw).hexdigest(),
        "approval_required": True,
        "execution_performed": False,
        "limits": {"max_retries": 0, "max_turns": 1, "max_commands": 4},
    }
    assert {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    } == before
    assert not Path(data["state_root"]).exists()
    assert main(["run", "--config", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "local-harness: invalid_run_config\n"


@pytest.mark.parametrize(
    "flag,value", [("--approver", "human"), ("--approve-config-sha256", "0" * 64)]
)
def test_preview_rejects_approval_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: str, value: str
) -> None:
    path, data = make_fixture(tmp_path)
    assert main(["run", "--preview", "--config", str(path), flag, value]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "local-harness: invalid_run_config\n"
    assert not Path(data["state_root"]).exists()


@pytest.mark.parametrize("kind", ["duplicate", "symlink", "fifo", "checker", "budget"])
def test_preview_rejects_unsafe_config_with_fixed_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    path, data = make_fixture(tmp_path)
    if kind == "duplicate":
        path.write_text('{"private-sentinel":1,"private-sentinel":2}')
    elif kind == "symlink":
        target = path.with_name("private-sentinel.json")
        path.rename(target)
        path.symlink_to(target)
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif kind == "checker":
        data["contract"]["protected_paths"] = []
        write_config(path, data)
    else:
        data["budgets"]["timeout_seconds"] = True
        write_config(path, data)
    assert main(["run", "--preview", "--config", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "local-harness: invalid_run_config\n"
    assert not Path(data["state_root"]).exists()


def test_preview_digest_observes_changed_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, _ = make_fixture(tmp_path)
    assert main(["run", "--preview", "--config", str(path)]) == 0
    first = json.loads(capsys.readouterr().out)
    path.write_bytes(path.read_bytes() + b"\n")
    assert main(["run", "--preview", "--config", str(path)]) == 0
    second = json.loads(capsys.readouterr().out)
    assert first["config_sha256"] != second["config_sha256"]
    assert second["config_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_preview_does_not_claim_baseline_or_native_preflight(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, data = make_fixture(tmp_path)
    data["expected_head"] = "0" * 40
    data["executable"] = str(tmp_path / "missing-native")
    (Path(data["repository"]) / "value.txt").write_text("dirty")
    write_config(path, data)
    assert main(["run", "--preview", "--config", str(path)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["expected_head"] == "0" * 40
    assert preview["executable"] == data["executable"]
    assert preview["execution_performed"] is False
    assert preview["approval_required"] is True
    assert "status" not in preview
    assert not Path(data["state_root"]).exists()
