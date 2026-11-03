"""Read-only explanations preserve candidate, attempt and approval boundaries."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from test_inspection import write_history

from agent_harness.attempt import _command_digest
from agent_harness.cli import main
from agent_harness.contract import TaskContract
from agent_harness.explanation import explain_execution
from agent_harness.inspection import _read_pair, summarize_execution
from agent_harness.review_evidence import build_review_evidence
from agent_harness.turn import RepositoryObservation


def persist(
    directory: Path, state: dict[str, Any], events: list[dict[str, Any]]
) -> None:
    raw = b"".join(json.dumps(event).encode() + b"\n" for event in events)
    state["attempt_summary"]["journal"]["sha256"] = hashlib.sha256(raw).hexdigest()
    (directory / "events.jsonl").write_bytes(raw)
    (directory / "current.json").write_text(json.dumps(state))


def history(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state = write_history(directory)
    events = [
        json.loads(line)
        for line in (directory / "events.jsonl").read_bytes().splitlines()
    ]
    return state, events


def bound_config(directory: Path, *, content: bool = True) -> Path:
    state, events = history(directory)
    command = ("/recorded/checker", "PRIVATE-COMMAND")
    task = TaskContract("verify", ("value",), (), ("value verified",))
    config = {
        "schema_version": 1,
        "contract": asdict(task),
        "verification": {
            "commands": [command],
            "working_directory": ".",
            "timeout_seconds": 3,
        },
        "criterion_commands": {"value verified": [command]},
    }
    path = directory / "config.json"
    path.write_text(json.dumps(config))
    contract_digest = hashlib.sha256(task.to_json().encode()).hexdigest()
    for record in [
        *events,
        state["attempt_summary"]["active_attempt"],
        state["attempt_summary"]["active_attempt"]["verification"],
    ]:
        if "contract_sha256" in record:
            record["contract_sha256"] = contract_digest
        if record.get("event") == "attempt_verification":
            record["commands"][0]["command_sha256"] = _command_digest(command)
    if content:
        root = "/recorded/repository"
        repository_id = hashlib.sha256(root.encode()).hexdigest()
        observation = RepositoryObservation(
            root, repository_id, (), "c" * 40, "f" * 64, (), None
        )
        for record in [
            *events,
            state["attempt_summary"]["active_attempt"],
            state["attempt_summary"]["active_attempt"]["verification"],
        ]:
            if "repository_id" in record:
                record["repository_id"] = repository_id
            for key in ("before", "after"):
                if key in record:
                    record[key]["repository_id"] = repository_id
            if record.get("event") == "attempt_verification":
                record["schema_version"] = 2
                record["review_binding"] = build_review_evidence(
                    record, observation, "d" * 64
                )
    events.insert(
        0,
        {
            "event": "run_binding",
            "schema_version": 1,
            "initial_head": "c" * 40,
            "backend": "codex",
            "config_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    )
    persist(directory, state, events)
    return path


def test_explanation_separates_recorded_pass_from_missing_evidence(
    tmp_path: Path,
) -> None:
    write_history(tmp_path)
    result = explain_execution(tmp_path)
    detail = result["explanation"]
    assert result["recorded_status"] == "pass"
    assert isinstance(detail, dict)
    assert detail["conclusion"] == "evidence_incomplete"
    assert "candidate_content_unavailable" in detail["blockers"]
    assert detail["review"]["state"] == "implemented_but_unwired"
    assert detail["approval"]["state"] == "manual_not_recorded"


def test_bound_criteria_are_per_attempt_and_pending_manual_review(
    tmp_path: Path,
) -> None:
    config = bound_config(tmp_path)
    result: Any = explain_execution(tmp_path, config_path=config)
    detail = result["explanation"]
    assert detail["conclusion"] == "review_pending"
    assert detail["config"]["state"] == "bound"
    assert detail["next"] == {"actor": "caller", "action": "independent_review"}
    assert detail["criteria"][0]["attempts"][0]["saved_command_outcome"] == "pass"
    assert (
        detail["attempts"][0]["native_process"]["process_status"] == "successful_exit"
    )
    assert detail["attempts"][0]["candidate_binding"]["attempt_id"] == "attempt-one"
    assert result["current_workspace_verified"] is False
    assert "PRIVATE-COMMAND" not in json.dumps(result)
    assert "PRIVATE-MARKER" not in json.dumps(result)


@pytest.mark.parametrize(
    "field,expected", [("revision", "f" * 40), ("candidate_sha256", "f" * 64)]
)
def test_comparison_mismatch_never_certifies_freshness(
    tmp_path: Path, field: str, expected: str
) -> None:
    config = bound_config(tmp_path)
    kwargs = {"expected_" + field: expected}
    result: Any = explain_execution(tmp_path, config_path=config, **kwargs)
    assert result["recorded_status"] == "pass"
    assert result["explanation"]["comparison"][field] == "mismatch"
    assert result["explanation"]["conclusion"] == "evidence_incomplete"
    assert result["explanation"]["comparison"]["context_only"] is True


def test_matching_context_still_does_not_capture_current_workspace(
    tmp_path: Path,
) -> None:
    config = bound_config(tmp_path)
    original: Any = explain_execution(tmp_path, config_path=config)
    binding = original["review_binding"]
    result: Any = explain_execution(
        tmp_path,
        config_path=config,
        expected_revision=binding["target_revision"],
        expected_candidate_sha256=binding["candidate_sha256"],
    )
    assert result["explanation"]["comparison"]["revision"] == "match"
    assert result["explanation"]["comparison"]["candidate_sha256"] == "match"
    assert result["current_workspace_verified"] is False


@pytest.mark.parametrize(
    "mode",
    ["missing", "mismatch", "contract", "mapping", "oversized", "corrupt", "unbound"],
)
def test_config_failure_does_not_erase_recorded_execution(
    tmp_path: Path, mode: str
) -> None:
    config = bound_config(tmp_path)
    state = json.loads((tmp_path / "current.json").read_bytes())
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_bytes().splitlines()
    ]
    if mode == "missing":
        config.unlink()
    elif mode == "mismatch":
        config.write_text(config.read_text() + " ")
    elif mode == "oversized":
        config.write_bytes(b" " * 65537)
    elif mode == "unbound":
        events.pop(0)
        persist(tmp_path, state, events)
    else:
        data = json.loads(config.read_bytes())
        if mode == "contract":
            data["contract"]["goal"] = "different"
        if mode == "mapping":
            data["criterion_commands"] = {}
        config.write_text("{" if mode == "corrupt" else json.dumps(data))
        events[0]["config_sha256"] = hashlib.sha256(config.read_bytes()).hexdigest()
        persist(tmp_path, state, events)
    result: Any = explain_execution(tmp_path, config_path=config)
    assert result["recorded_status"] == "pass"
    assert result["explanation"]["evidence_readiness"] == "incomplete"
    assert result["explanation"]["criteria"] == []


@pytest.mark.parametrize(
    "mode", ["missing", "corrupt", "oversized", "inconsistent", "tampered"]
)
def test_invalid_history_remains_unknown(tmp_path: Path, mode: str) -> None:
    bound_config(tmp_path)
    current = tmp_path / "current.json"
    if mode == "missing":
        current.unlink()
    elif mode == "corrupt":
        current.write_text("{")
    elif mode == "oversized":
        current.write_bytes(b" " * (1024 * 1024 + 1))
    else:
        state = json.loads(current.read_bytes())
        events = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_bytes().splitlines()
        ]
        if mode == "inconsistent":
            state["loop_summary"]["status"] = "unknown"
        else:
            for record in [
                *events,
                state["attempt_summary"]["active_attempt"]["verification"],
            ]:
                if record.get("event") == "attempt_verification":
                    record["review_binding"]["binding"]["candidate_sha256"] = "f" * 64
        persist(tmp_path, state, events)
    result: Any = explain_execution(tmp_path)
    assert result["recorded_status"] == "unknown"
    assert result["diagnostics"]
    assert result["explanation"]["attempts"] == []


@pytest.mark.parametrize(
    "exit_code,timed_out,expected",
    [(1, False, "fail"), (None, True, "timeout"), (None, False, "unknown")],
)
def test_checker_outcome_is_separate_from_native_process(
    tmp_path: Path, exit_code: int | None, timed_out: bool, expected: str
) -> None:
    config = bound_config(tmp_path, content=False)
    state = json.loads((tmp_path / "current.json").read_bytes())
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_bytes().splitlines()
    ]
    for record in [*events, state["attempt_summary"]["active_attempt"]["verification"]]:
        if record.get("event") == "attempt_verification":
            record["commands"][0].update(exit_code=exit_code, timed_out=timed_out)
            record.update(technical_kind="fail", overall_kind="fail")
    events[-1]["status"] = "fail"
    state["loop_summary"]["status"] = "fail"
    persist(tmp_path, state, events)
    result: Any = explain_execution(tmp_path, config_path=config)
    assert result["recorded_status"] == "fail"
    detail = result["explanation"]
    assert (
        detail["attempts"][0]["native_process"]["process_status"] == "successful_exit"
    )
    assert detail["criteria"][0]["attempts"][0]["saved_command_outcome"] == expected


def test_mixed_attempts_never_union_command_coverage(tmp_path: Path) -> None:
    config = bound_config(tmp_path, content=False)
    state = json.loads((tmp_path / "current.json").read_bytes())
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_bytes().splitlines()
    ]
    original = copy.deepcopy(events[2:-1])
    for event in events[2:-1]:
        event["attempt_id"] = "older"
    for event in events[2:-1]:
        if event["event"] == "attempt_verification":
            event["commands"][0]["command_sha256"] = "f" * 64
    events[-1:-1] = original
    events[-1]["attempt_ids"] = ["older", "attempt-one"]
    state["loop_summary"]["attempt_ids"] = ["older", "attempt-one"]
    state["attempt_summary"]["attempt_count"] = 2
    persist(tmp_path, state, events)
    result: Any = explain_execution(tmp_path, config_path=config)
    decisions = result["explanation"]["criteria"][0]["attempts"]
    assert {d["attempt_id"]: d["saved_command_outcome"] for d in decisions} == {
        "older": "unverified",
        "attempt-one": "pass",
    }
    assert (
        result["explanation"]["attempts"][0]["commands"][0]["command_sha256"]
        == "f" * 64
    )


@pytest.mark.parametrize("damage", [False, True])
def test_read_only_single_snapshot_and_default_cli_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    damage: bool,
) -> None:
    config = bound_config(tmp_path)
    if damage:
        (tmp_path / "current.json").write_text("{")
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    baseline = summarize_execution(tmp_path)
    from agent_harness import explanation

    original = _read_pair
    reads = []

    def snapshot(path: Path | str) -> tuple[Path, bytes, bytes]:
        reads.append(path)
        return original(path)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("inspection invoked an executor or writer")

    monkeypatch.setattr(explanation, "_read_pair", snapshot)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    assert main(["status", "--state-dir", str(tmp_path)]) == (1 if damage else 0)
    assert json.loads(capsys.readouterr().out) == baseline
    assert main(
        ["status", "--state-dir", str(tmp_path), "--explain", "--config", str(config)]
    ) == (1 if damage else 0)
    result = json.loads(capsys.readouterr().out)
    assert result["recorded_status"] == baseline["recorded_status"]
    assert len(reads) == 1
    assert before == {p: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize("status", ["timeout", "unknown"])
def test_native_unknown_and_timeout_are_never_inferred_from_checker(
    tmp_path: Path, status: str
) -> None:
    state, events = history(tmp_path)
    observed = next(e for e in events if e["event"] == "turn_observation")
    active = state["attempt_summary"]["active_attempt"]
    if status == "timeout":
        observed["process"].update(process_status="timeout", exit_code=None)
        active["observation"]["process"] = observed["process"]
    else:
        observed.clear()
        observed.update(
            event="turn_observation_failed",
            attempt_id="attempt-one",
            classification="adapter_raised",
        )
        active["observation"] = {"status": "failed"}
        active["after_head"] = None
        active["verification"] = None
        events = [e for e in events if e["event"] != "attempt_verification"]
        state["loop_summary"]["status"] = "unknown"
        events[-1]["status"] = "unknown"
    persist(tmp_path, state, events)
    result: Any = explain_execution(tmp_path)
    assert (
        result["explanation"]["attempts"][0]["native_process"]["process_status"]
        == status
    )
    if status == "timeout":
        assert (
            result["explanation"]["attempts"][0]["checker_result"]["overall_kind"]
            == "pass"
        )
    else:
        assert result["explanation"]["attempts"][0]["checker_result"] is None


@pytest.mark.parametrize("expected", ["bad", "A" * 64, "", "x" * 40000])
def test_invalid_expected_identity_cannot_become_a_match(
    tmp_path: Path, expected: str
) -> None:
    write_history(tmp_path)
    result: Any = explain_execution(tmp_path, expected_candidate_sha256=expected)
    assert (
        result["explanation"]["comparison"]["candidate_sha256"]
        == "invalid_expected_value"
    )
    assert result["explanation"]["conclusion"] == "evidence_incomplete"


def test_summary_and_explanation_use_original_snapshot_when_disk_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = bound_config(tmp_path)
    from agent_harness import explanation

    original = _read_pair
    expected = hashlib.sha256((tmp_path / "current.json").read_bytes()).hexdigest()

    def snapshot(path: Path | str) -> tuple[Path, bytes, bytes]:
        result = original(path)
        (tmp_path / "current.json").write_text("{")
        return result

    monkeypatch.setattr(explanation, "_read_pair", snapshot)
    result: Any = explain_execution(tmp_path, config_path=config)
    assert result["recorded_status"] == "pass"
    assert result["explanation"]["config"]["state"] == "bound"
    assert result["evidence"]["current"]["sha256"] == expected


def test_duplicate_observation_is_not_promoted_into_an_explanation(
    tmp_path: Path,
) -> None:
    state, events = history(tmp_path)
    observation = copy.deepcopy(
        next(e for e in events if e["event"] == "turn_observation")
    )
    events.insert(3, observation)
    persist(tmp_path, state, events)
    result: Any = explain_execution(tmp_path)
    assert result["recorded_status"] == "unknown"
    assert result["explanation"]["attempts"] == []


def test_earlier_incomplete_attempt_rejects_object_identity(tmp_path: Path) -> None:
    state, events = history(tmp_path)
    earlier = copy.deepcopy(events[1:-1])
    earlier = [event for event in earlier if event["event"] != "attempt_verification"]
    malformed = {"private": "PRIVATE-IDENTITY-PAYLOAD"}
    for event in earlier:
        event["attempt_id"] = "older-incomplete"
        if "repository_root" in event:
            event["repository_root"] = malformed
        for key in ("before", "after"):
            if key in event:
                event[key]["repository_root"] = malformed
    events[1:1] = earlier
    events[-1]["attempt_ids"] = ["older-incomplete", "attempt-one"]
    state["loop_summary"]["attempt_ids"] = ["older-incomplete", "attempt-one"]
    state["attempt_summary"]["attempt_count"] = 2
    persist(tmp_path, state, events)
    result: Any = explain_execution(tmp_path)
    assert result["recorded_status"] == "unknown"
    assert result["explanation"]["attempts"] == []
    assert "PRIVATE-IDENTITY-PAYLOAD" not in json.dumps(result)


@pytest.mark.parametrize(
    "kind", ["turn_observation_failed", "callback_observation_failed"]
)
@pytest.mark.parametrize("payload", ["object", "revision", "absent"])
def test_failed_observation_never_supplies_candidate_from_extra_after(
    tmp_path: Path, kind: str, payload: str
) -> None:
    config = bound_config(tmp_path)
    state = json.loads((tmp_path / "current.json").read_bytes())
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_bytes().splitlines()
    ]
    intent = copy.deepcopy(next(e for e in events if e["event"] == "turn_intent"))
    observed = copy.deepcopy(
        next(e for e in events if e["event"] == "turn_observation")
    )
    scope = copy.deepcopy(next(e for e in events if e["event"] == "scope_decision"))
    for record in (intent, observed, scope):
        record["attempt_id"] = "older-failed"
    failed: dict[str, Any] = {
        "event": kind,
        "attempt_id": "older-failed",
        "classification": "adapter_raised"
        if kind == "turn_observation_failed"
        else "after_observation_failed",
    }
    earlier = [intent]
    if kind == "callback_observation_failed":
        earlier.extend([observed, scope])
        failed.update(
            repository_id=intent["repository_id"], process=observed["process"]
        )
    if payload != "absent":
        failed["after"] = {
            **observed["after"],
            "head": {"private": "PRIVATE-FAILED-AFTER"}
            if payload == "object"
            else "e" * 40,
        }
    earlier.append(failed)
    position = next(
        i for i, event in enumerate(events) if event["event"] == "turn_intent"
    )
    events[position:position] = earlier
    events[-1]["attempt_ids"] = ["older-failed", "attempt-one"]
    state["loop_summary"]["attempt_ids"] = ["older-failed", "attempt-one"]
    state["attempt_summary"]["attempt_count"] = 2
    persist(tmp_path, state, events)
    result: Any = explain_execution(tmp_path, config_path=config)
    assert result["recorded_status"] == "pass"
    attempts = {
        attempt["attempt_id"]: attempt for attempt in result["explanation"]["attempts"]
    }
    assert attempts["older-failed"]["target_revision"] is None
    assert attempts["older-failed"]["candidate_binding"] is None
    assert attempts["older-failed"]["checker_result"] is None
    assert attempts["older-failed"]["evidence_state"] == "unverified"
    assert attempts["attempt-one"]["target_revision"] == result["target_revision"]
    assert result["explanation"]["conclusion"] == "review_pending"
    assert "PRIVATE-FAILED-AFTER" not in json.dumps(result)
