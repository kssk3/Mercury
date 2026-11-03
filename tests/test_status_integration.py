"""Details extend the current historical reader without changing existing modes."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path
from typing import Any

import pytest
import test_attempt
from test_inspection import write_history
from test_large_review_projection import _produce
from test_status_details import history, save_history

from agent_harness.cli import main
from agent_harness.inspection import summarize_execution

repository = test_attempt.repository
repository_seed = test_attempt.repository_seed


def test_details_preserve_current_basic_projection(tmp_path: Path) -> None:
    write_history(tmp_path)
    baseline = summarize_execution(tmp_path)
    result = summarize_execution(tmp_path, details=True)
    assert {key: value for key, value in result.items() if key != "details"} == baseline
    assert isinstance(result["details"], dict)


@pytest.mark.parametrize(
    "options",
    [
        ["--details", "--explain"],
        ["--details", "--format", "html"],
        ["--details", "--config", "absent.json"],
        ["--details", "--expected-revision", "a" * 40],
        ["--details", "--expected-candidate-sha256", "b" * 64],
    ],
)
def test_details_reject_incompatible_modes(tmp_path: Path, options: list[str]) -> None:
    with pytest.raises(SystemExit) as failure:
        main(["status", "--state-dir", str(tmp_path), *options])
    assert failure.value.code == 2


def test_details_cli_missing_input_preserves_basic_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["status", "--state-dir", str(tmp_path), "--details"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["recorded_status"] == "unknown"
    assert result["diagnostics"] == ["missing_input"]
    assert result["details"]["diagnostics"]


@pytest.mark.parametrize("legacy", [False, True])
def test_details_preserve_compact_and_full_review_bindings(
    repository: Path, tmp_path: Path, legacy: bool
) -> None:
    from agent_harness.attempt import AttemptJournal
    from agent_harness.journal import EventJournal
    from agent_harness.state import CurrentStateStore

    directory = tmp_path / "state"
    _produce(repository, directory, 8000 if not legacy else 2)
    current = CurrentStateStore(directory)
    saved: Any = current.read()
    if legacy:
        saved["attempt_summary"]["schema_version"] = 1
        saved["attempt_summary"]["active_attempt"] = AttemptJournal(
            repository, journal=EventJournal(directory), state_store=current
        ).read()[-1]
        current.write(saved)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    baseline = summarize_execution(directory)
    result: Any = summarize_execution(directory, details=True)
    assert {key: value for key, value in result.items() if key != "details"} == baseline
    assert result["recorded_status"] == "pass"
    assert result["review_binding_status"] == "recorded"
    assert result["details"]["diagnostics"] == []
    assert result["details"]["verification"]["coverage"] == "active_attempt_only"
    assert current.path.stat().st_size <= 1024 * 1024
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


@pytest.mark.parametrize(
    "reason", ["lock_release_failed", "execution_failed", "interrupted"]
)
def test_details_keep_wrapper_unknown_outcomes(tmp_path: Path, reason: str) -> None:
    state, events = history(tmp_path)
    events.insert(
        0,
        {
            "event": "run_binding",
            "schema_version": 2,
            "initial_head": "c" * 40,
            "config_sha256": "a" * 64,
            "backend": "codex",
        },
    )
    state["run_result"] = {"status": "unknown", "reason": reason}
    save_history(tmp_path, state, events)
    result: Any = summarize_execution(tmp_path, details=True)
    assert result["recorded_status"] == "unknown"
    assert result["recorded_reason"] == reason
    assert result["review_binding"] is None
    assert result["details"]["diagnostics"] == []


def test_details_read_exactly_one_pair_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_harness import inspection

    history(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    original = inspection._read_pair
    reads: list[Path | str] = []

    def snapshot(path: Path | str) -> tuple[Path, bytes, bytes]:
        reads.append(path)
        return original(path)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("historical reader executed a writer or process")

    with monkeypatch.context() as patch:
        patch.setattr(inspection, "_read_pair", snapshot)
        patch.setattr(subprocess, "Popen", forbidden)
        patch.setattr(Path, "write_text", forbidden)
        patch.setattr(Path, "write_bytes", forbidden)
        assert summarize_execution(tmp_path, details=True)["recorded_status"] == "pass"
    assert reads == [tmp_path]
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize("damage", ["saved", "missing", "corrupt"])
def test_installed_cli_modes_preserve_saved_outcome_and_inputs(
    tmp_path: Path, damage: str
) -> None:
    if damage != "missing":
        history(tmp_path)
    if damage == "corrupt":
        (tmp_path / "current.json").write_text("{")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    executable = Path(sys.executable).parent / "local-harness"
    results = []
    for options in (
        [],
        ["--details"],
        ["--explain"],
        ["--explain", "--format", "html"],
    ):
        result = subprocess.run(
            [str(executable), "status", "--state-dir", str(tmp_path), *options],
            timeout=15,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == (0 if damage == "saved" else 1), result.stderr
        if "html" in options:
            from test_html_report import parse_report

            _, snapshot = parse_report(result.stdout)
        else:
            snapshot = json.loads(result.stdout)
        results.append(snapshot)
    baseline = results[0]
    assert {k: v for k, v in results[1].items() if k != "details"} == baseline
    for snapshot in results[2:]:
        assert {k: v for k, v in snapshot.items() if k != "explanation"} == baseline
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_verified_runtime_exports_details_from_complete_source_inventory(
    tmp_path: Path,
) -> None:
    from test_verified_runtime import SCRIPT, bundle, digest, dispatch

    root, manifest, _ = bundle(tmp_path)
    shutil.rmtree(root / "src")
    shutil.copytree(
        SCRIPT.parents[1] / "src",
        root / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "AGENTS.md"),
    )
    installed = distribution("agent-harness")
    metadata = (
        root / "src" / f"agent_harness-{installed.version}.dist-info" / "METADATA"
    )
    metadata.parent.mkdir()
    metadata.write_text(installed.read_text("METADATA") or "")
    frozen = json.loads(manifest.read_text())
    frozen["files"] = {
        str(p.relative_to(root / "src")): digest(p)
        for p in sorted((root / "src").rglob("*"))
        if p.is_file()
    }
    manifest.write_text(json.dumps(frozen))
    state = tmp_path / "saved-state"
    history(state)
    before = {p.name: p.read_bytes() for p in state.iterdir()}
    result = dispatch(
        root, manifest, arguments=("status", "--state-dir", str(state), "--details")
    )
    assert result.returncode == 0, result.stderr
    projected = json.loads(result.stdout)
    assert projected["recorded_status"] == "pass"
    assert projected["details"]["diagnostics"] == []
    assert before == {p.name: p.read_bytes() for p in state.iterdir()}
