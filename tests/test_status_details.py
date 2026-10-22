"""Opt-in historical detail projections never imply current execution proof."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from test_inspection import write_history

from agent_harness.cli import main
from agent_harness.inspection import summarize_execution


def save_history(directory: Path, state: dict[str, Any], events: list[Any]) -> None:
    raw = b"".join(json.dumps(event).encode() + b"\n" for event in events)
    state["attempt_summary"]["journal"]["sha256"] = hashlib.sha256(raw).hexdigest()
    (directory / "events.jsonl").write_bytes(raw)
    (directory / "current.json").write_text(json.dumps(state))


def history(directory: Path) -> tuple[dict[str, Any], list[Any]]:
    state = write_history(directory)
    events = [
        json.loads(line)
        for line in (directory / "events.jsonl").read_text().splitlines()
    ]
    return state, events


def details(directory: Path) -> dict[str, Any]:
    return cast(dict[str, Any], summarize_execution(directory, details=True)["details"])


def callback_history(directory: Path) -> tuple[dict[str, Any], list[Any]]:
    state, events = history(directory)
    observation = copy.deepcopy(events[2])
    observation["event"] = "callback_observation"
    observation["delta"]["modified"] = ["src/changed.py"]
    observation["delta"]["status_changed"] = True
    scope = copy.deepcopy(events[3])
    scope["event"] = "callback_scope_decision"
    scope["delta"] = copy.deepcopy(observation["delta"])
    scope["protected_paths"] = ["src/changed.py"]
    scope["proceed"] = False
    state["attempt_summary"]["active_attempt"]["scope"] = {
        key: value
        for key, value in scope.items()
        if key not in {"event", "attempt_id", "repository_id"}
    }
    events[4:4] = [observation, scope]
    save_history(directory, state, events)
    return state, events


def test_details_preserve_default_summary(tmp_path: Path) -> None:
    write_history(tmp_path)
    basic = summarize_execution(tmp_path)
    detailed = summarize_execution(tmp_path, details=True)
    assert "details" not in basic
    assert {key: value for key, value in detailed.items() if key != "details"} == basic
    assert detailed["historical_only"] is True
    assert detailed["current_workspace_verified"] is False


def test_details_project_recorded_metadata_without_private_fields(
    tmp_path: Path,
) -> None:
    state, events = history(tmp_path)
    active = state["attempt_summary"]["active_attempt"]
    for verification in (active["verification"], events[-2]):
        verification["commands"][0]["argv"] = ["PRIVATE-MARKER"]
        verification["protected_inputs"] = [
            {"path_sha256": "f" * 64, "state": "unchanged", "private": "PRIVATE-MARKER"}
        ]
    save_history(tmp_path, state, events)
    result = details(tmp_path)
    assert result["schema_version"] == 1
    assert result["process"] == {
        "process_status": "successful_exit",
        "exit_code": 0,
        "duration_seconds": 0.1,
    }
    assert result["scope"]["proceed"] is True
    assert result["scope"]["delta"]["added"] == []
    assert result["verification"] == {
        "coverage": "active_attempt_only",
        "technical_kind": "pass",
        "overall_kind": "pass",
        "commands": [{"command_sha256": "e" * 64, "exit_code": 0, "timed_out": False}],
        "baseline_commands": [
            {"command_sha256": "e" * 64, "exit_code": 1, "timed_out": False}
        ],
        "protected_inputs": [{"path_sha256": "f" * 64, "state": "unchanged"}],
    }
    assert result["usage"] == {
        "loop_command_boundaries": 4,
        "command_boundary_semantics": "recorded_loop_total",
        "requested_attempt_count": 1,
        "requested_attempt_semantics": "recorded_turn_intents",
        "native_launch_count": None,
        "native_launch_count_status": "unknown",
        "active_attempt_verification_command_count": 1,
        "elapsed_seconds": 0.5,
        "elapsed_boundary": "before_outcome_persistence",
    }
    assert result["outputs"]["channels"]["stdout"] == {
        "identifier": "d" * 64,
        "byte_count": 12,
        "captured_bytes": 12,
        "truncated": False,
        "capture_truncated": False,
    }
    assert result["outputs"]["bodies_read"] is False
    assert result["outputs"]["retention"] == "unknown"
    assert result["outputs"]["content_availability"] == "unknown"
    assert result["diagnostics"] == []
    assert "PRIVATE-MARKER" not in json.dumps(result)


@pytest.mark.parametrize(
    ("field", "value"),
    [("duration_seconds", -1), ("process_status", "nonzero_exit"), ("exit_code", True)],
)
def test_details_invalid_process_is_unknown(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = history(tmp_path)
    state["attempt_summary"]["active_attempt"]["observation"]["process"][field] = value
    events[2]["process"][field] = value
    save_history(tmp_path, state, events)
    result = summarize_execution(tmp_path, details=True)
    assert result["recorded_status"] == "pass"
    assert cast(dict[str, Any], result["details"])["process"] is None
    assert (
        "process_unavailable" in cast(dict[str, Any], result["details"])["diagnostics"]
    )


def test_details_adapter_failure_does_not_invent_process_or_launch(
    tmp_path: Path,
) -> None:
    state, events = history(tmp_path)
    active = state["attempt_summary"]["active_attempt"]
    active.update(
        after_head=None,
        observation={
            "status": "failed",
            "classification": "adapter_raised",
            "process": None,
        },
        scope=None,
        verification=None,
        outputs=None,
    )
    events[2:-1] = [
        {
            "event": "turn_observation_failed",
            "attempt_id": "attempt-one",
            "classification": "adapter_raised",
        }
    ]
    state["loop_summary"].update(status="unknown", reason="adapter_failed")
    events[-1] = state["loop_summary"]
    save_history(tmp_path, state, events)
    result = details(tmp_path)
    assert result["process"] is None
    assert result["scope"] is None
    assert result["verification"] is None
    assert result["outputs"] is None
    assert result["usage"]["requested_attempt_count"] == 1
    assert result["usage"]["native_launch_count"] is None


def test_details_follow_final_callback_scope(tmp_path: Path) -> None:
    callback_history(tmp_path)
    result = details(tmp_path)
    assert result["scope"]["delta"]["modified"] == ["src/changed.py"]
    assert result["scope"]["protected_paths"] == ["src/changed.py"]
    assert result["scope"]["proceed"] is False


@pytest.mark.parametrize("invalid", ["path", "flag", "decision"])
def test_details_reject_invalid_scope(tmp_path: Path, invalid: str) -> None:
    state, events = history(tmp_path)
    scope = state["attempt_summary"]["active_attempt"]["scope"]
    if invalid == "path":
        scope["delta"]["added"] = ["../private"]
    elif invalid == "flag":
        scope["delta"]["head_changed"] = 1
    else:
        scope["proceed"] = False
    events[3].update(scope)
    events[2]["delta"] = scope["delta"]
    save_history(tmp_path, state, events)
    result = details(tmp_path)
    assert result["scope"] is None
    assert "scope_unavailable" in result["diagnostics"]


def test_details_missing_final_callback_scope_is_unknown(tmp_path: Path) -> None:
    state, events = callback_history(tmp_path)
    del events[5]
    save_history(tmp_path, state, events)
    result = details(tmp_path)
    assert result["scope"] is None
    assert "scope_unavailable" in result["diagnostics"]


def test_details_read_only_pair_without_output_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_history(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    original_open = os.open
    opened: list[str] = []

    def guarded_open(
        path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if not flags & os.O_DIRECTORY:
            assert path in {"current.json", "events.jsonl"}
            opened.append(path)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def forbid(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("historical details attempted execution or an extra file read")

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", guarded_open)
        patch.setattr(subprocess, "Popen", forbid)
        patch.setattr(Path, "read_bytes", forbid)
        patch.setattr(Path, "read_text", forbid)
        assert details(tmp_path)["process"]["exit_code"] == 0
    assert opened == ["current.json", "events.jsonl"]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_details_cannot_promote_incomplete_core(tmp_path: Path) -> None:
    write_history(tmp_path)
    path = tmp_path / "events.jsonl"
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    result = summarize_execution(tmp_path, details=True)
    assert result["recorded_status"] == "unknown"
    assert result["diagnostics"] == ["incomplete_history"]
    projection = cast(dict[str, Any], result["details"])
    assert all(
        projection[key] is None
        for key in ("process", "scope", "verification", "usage", "outputs")
    )
    assert projection["diagnostics"] == ["core_history_unavailable"]


def test_details_cli_exit_depends_on_core_diagnostics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state, events = history(tmp_path)
    del events[3]
    save_history(tmp_path, state, events)
    assert main(["status", "--state-dir", str(tmp_path), "--details"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["recorded_status"] == "pass"
    assert "scope_unavailable" in result["details"]["diagnostics"]
    assert main(["status", "--state-dir", str(tmp_path / "missing"), "--details"]) == 1
    assert json.loads(capsys.readouterr().out)["recorded_status"] == "unknown"


@pytest.mark.parametrize(
    ("first", "second"),
    [("added", "modified"), ("added", "deleted"), ("modified", "deleted")],
    ids=["added-modified", "added-deleted", "modified-deleted"],
)
def test_details_reject_cross_category_path_overlap(
    tmp_path: Path, first: str, second: str
) -> None:
    state, events = history(tmp_path)
    scope = state["attempt_summary"]["active_attempt"]["scope"]
    scope["delta"][first] = ["src/shared.py"]
    scope["delta"][second] = ["src/shared.py"]
    scope["delta"]["status_changed"] = True
    events[3].update(copy.deepcopy(scope))
    events[2]["delta"] = copy.deepcopy(scope["delta"])
    save_history(tmp_path, state, events)
    result = summarize_execution(tmp_path, details=True)
    assert result["recorded_status"] == "pass"
    assert result["recorded_reason"] == "final_verification"
    assert result["diagnostics"] == []
    projection = cast(dict[str, Any], result["details"])
    assert projection["scope"] is None
    assert projection["diagnostics"] == ["scope_unavailable"]


def test_details_accept_disjoint_change_categories(tmp_path: Path) -> None:
    state, events = history(tmp_path)
    scope = state["attempt_summary"]["active_attempt"]["scope"]
    scope["delta"].update(
        added=["src/z-added.py", "src/a-added.py"],
        modified=["src/modified.py"],
        deleted=["src/deleted.py"],
        status_changed=True,
    )
    events[3].update(copy.deepcopy(scope))
    events[2]["delta"] = copy.deepcopy(scope["delta"])
    save_history(tmp_path, state, events)
    result = details(tmp_path)
    assert result["scope"] == scope
    assert result["diagnostics"] == []
