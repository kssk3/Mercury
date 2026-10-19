from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_harness.run_config import load_run_config


def make_fixture(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "value.txt").write_text("before")
    (root / "check.py").write_text(
        "from pathlib import Path\nassert Path('value.txt').read_text() == 'after'\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for args in [
        ["init", "--quiet"],
        ["add", "."],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
    ]:
        subprocess.run(
            ["/usr/bin/git", "-C", str(root), *args],
            env=env,
            check=True,
            capture_output=True,
            timeout=10,
        )
    head = subprocess.run(
        ["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    command = [sys.executable, "-I", "check.py"]
    data: dict[str, Any] = {
        "schema_version": 1,
        "repository": str(root.resolve()),
        "expected_head": head,
        "state_root": str((tmp_path / "state").resolve()),
        "backend": "claude",
        "executable": sys.executable,
        "contract": {
            "goal": "change value",
            "allowed_paths": ["value.txt"],
            "protected_paths": ["check.py"],
            "completion_criteria": ["value correct"],
        },
        "verification": {
            "commands": [command],
            "working_directory": ".",
            "timeout_seconds": 2,
        },
        "criterion_commands": {"value correct": [command]},
        "context_paths": ["value.txt", "check.py"],
        "budgets": {
            "timeout_seconds": 2,
            "input_bytes": 16000,
            "stdout_bytes": 2000,
            "stderr_bytes": 2000,
            "final_bytes": 2000,
        },
        "total_timeout_seconds": 40,
    }
    path = tmp_path / "run.json"
    path.write_text(json.dumps(data))
    return path, data


def write_config(path: Path, data: dict[str, Any]) -> str:
    path.write_text(json.dumps(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_valid_config_is_pure_and_binds_exact_bytes(tmp_path: Path) -> None:
    path, data = make_fixture(tmp_path)
    digest = write_config(path, data)
    config = load_run_config(path, digest, "human")
    assert config.config_sha256 == digest
    assert config.limits.max_retries == 0
    assert config.limits.max_commands == 4
    assert not Path(data["state_root"]).exists()


@pytest.mark.parametrize(
    "approval,approver",
    [("", "human"), ("0" * 64, "human"), (None, "human"), ("valid", "")],
)
def test_approval_rejected_without_effects(
    tmp_path: Path, approval: str | None, approver: str
) -> None:
    path, data = make_fixture(tmp_path)
    digest = write_config(path, data)
    with pytest.raises(ValueError, match="invalid_run_config"):
        load_run_config(path, digest if approval == "valid" else approval, approver)
    assert not Path(data["state_root"]).exists()


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "version_bool",
        "backend",
        "timeout_bool",
        "bytes_huge",
        "total_small",
        "command_string",
        "context_outside",
        "criterion_missing",
        "missing_protected",
        "checker_unprotected",
        "state_overlap",
        "state_parent",
        "executable_inside",
        "relative_root",
    ],
)
def test_schema_and_path_preflight(tmp_path: Path, change: str) -> None:
    path, data = make_fixture(tmp_path)
    if change == "unknown":
        data["secret-setting"] = "never echo this"
    elif change == "version_bool":
        data["schema_version"] = True
    elif change == "backend":
        data["backend"] = "arbitrary"
    elif change == "timeout_bool":
        data["budgets"]["timeout_seconds"] = True
    elif change == "bytes_huge":
        data["budgets"]["final_bytes"] = 2**30
    elif change == "total_small":
        data["total_timeout_seconds"] = 1
    elif change == "command_string":
        data["verification"]["commands"] = ["echo unsafe"]
    elif change == "context_outside":
        data["context_paths"] = ["other.txt"]
    elif change == "criterion_missing":
        data["criterion_commands"] = {}
    elif change == "missing_protected":
        data["contract"]["protected_paths"] = ["missing.py"]
    elif change == "checker_unprotected":
        data["contract"]["protected_paths"] = []
    elif change == "state_overlap":
        data["state_root"] = data["repository"] + "/state"
    elif change == "state_parent":
        data["state_root"] = str(tmp_path.resolve())
    elif change == "executable_inside":
        data["executable"] = data["repository"] + "/check.py"
    elif change == "relative_root":
        data["repository"] = "repository"
    digest = write_config(path, data)
    with pytest.raises(ValueError, match="^invalid_run_config$"):
        load_run_config(path, digest, "human")
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize(
    "kind",
    ["duplicate", "nan", "deep", "oversize", "utf8", "symlink", "hardlink", "fifo"],
)
def test_unsafe_config_read_is_bounded(tmp_path: Path, kind: str) -> None:
    path, _ = make_fixture(tmp_path)
    if kind == "duplicate":
        path.write_text('{"schema_version":1,"schema_version":1}')
    elif kind == "nan":
        path.write_text('{"schema_version":NaN}')
    elif kind == "deep":
        path.write_text("[" * 2000 + "0" + "]" * 2000)
    elif kind == "oversize":
        path.write_bytes(b" " * 65537)
    elif kind == "utf8":
        path.write_bytes(b"\xff")
    elif kind == "symlink":
        target = path.with_name("target.json")
        path.rename(target)
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(path, path.with_name("hardlink.json"))
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    digest = (
        "0" * 64 if kind == "fifo" else hashlib.sha256(path.read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="^invalid_run_config$"):
        load_run_config(path, digest, "human")


@pytest.mark.parametrize("alias", [False, True])
def test_env_dispatcher_cannot_select_mutable_checker(
    tmp_path: Path, alias: bool
) -> None:
    """Delegated env programs are unsupported, including resolved aliases."""
    path, data = make_fixture(tmp_path)
    executable = Path("/usr/bin/env")
    if alias:
        executable = tmp_path / "dispatcher"
        executable.symlink_to("/usr/bin/env")
    command = [str(executable), f"PATH={data['repository']}/bin", "checker"]
    data["verification"]["commands"] = [command]
    data["criterion_commands"] = {"value correct": [command]}
    with pytest.raises(ValueError, match="invalid_run_config"):
        load_run_config(path, write_config(path, data), "human")
    assert not Path(data["state_root"]).exists()
