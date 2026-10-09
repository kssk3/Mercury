from __future__ import annotations

import copy
import importlib.util
from typing import Any

import pytest


def classify(value: object) -> Any:
    assert importlib.util.find_spec("agent_harness.failure") is not None, (
        "H3 classifier is missing"
    )
    from agent_harness.failure import classify_attempt_failure

    return classify_attempt_failure(value)


def attempt() -> dict[str, Any]:
    identity = {
        "attempt_id": "attempt-one",
        "repository_id": "a" * 64,
        "repository_root": "/fixture",
        "contract_sha256": "b" * 64,
        "context_sha256": "c" * 64,
        "before_head": "d" * 40,
    }
    return {
        **identity,
        "after_head": "d" * 40,
        "observation": {
            "status": "observed",
            "process": {
                "process_status": "successful_exit",
                "exit_code": 0,
                "final_output_status": "available",
            },
        },
        "scope": {
            "proceed": True,
            "out_of_scope_paths": [],
            "protected_paths": [],
            "unexplained_status_change": False,
            "delta": {"head_changed": False, "index_changed": False},
        },
        "outputs": {"stdout": {}, "stderr": {}, "final_message": {}},
        "verification": {
            **identity,
            "target_revision": "d" * 40,
            "technical_kind": "pass",
            "overall_kind": "pass",
            "commands": [
                {"command_sha256": "e" * 64, "exit_code": 0, "timed_out": False}
            ],
            "baseline_commands": [
                {"command_sha256": "e" * 64, "exit_code": 0, "timed_out": False}
            ],
            "protected_inputs": [],
        },
    }


def reasons(value: object) -> set[str]:
    return {finding.reason for finding in classify(value).findings}


def test_complete_evidence_returns_no_findings_without_mutating_input() -> None:
    value = attempt()
    original = copy.deepcopy(value)
    assert classify(value).findings == ()
    assert value == original


@pytest.mark.parametrize(
    "baseline,attribution", [(0, "new"), (1, "unknown"), (None, "unknown")]
)
def test_current_failure_cannot_be_masked_by_legacy_pass(
    baseline: int | None, attribution: str
) -> None:
    value = attempt()
    value["verification"]["commands"][0]["exit_code"] = 1
    value["verification"]["baseline_commands"][0]["exit_code"] = baseline
    findings = classify(value).findings
    failure = next(item for item in findings if item.reason == "verification_nonzero")
    assert failure.attribution == attribution
    assert failure.command_sha256 == "e" * 64


@pytest.mark.parametrize(
    "change", ["missing", "mismatch", "duplicate_baseline", "duplicate_current"]
)
def test_ambiguous_command_identity_never_proves_new(change: str) -> None:
    value = attempt()
    report = value["verification"]
    report["commands"][0]["exit_code"] = 1
    if change == "missing":
        report["baseline_commands"] = []
    elif change == "mismatch":
        report["baseline_commands"][0]["command_sha256"] = "f" * 64
    elif change == "duplicate_baseline":
        report["baseline_commands"] *= 2
    else:
        report["commands"] *= 2
    assert all(
        item.attribution == "unknown"
        for item in classify(value).findings
        if item.kind == "verification"
    )


def test_native_success_without_verification_remains_unknown() -> None:
    value = attempt()
    value["verification"] = None
    assert "missing_verification" in reasons(value)


def test_multiple_concrete_failures_are_preserved() -> None:
    value = attempt()
    value["observation"]["process"].update(process_status="timeout", exit_code=0)
    value["scope"].update(proceed=False, protected_paths=["secret"])
    value["verification"]["commands"][0]["timed_out"] = True
    value["verification"]["protected_inputs"] = [
        {"path_sha256": "f" * 64, "state": "modified"}
    ]
    assert {
        "native_timeout",
        "scope_violation",
        "verification_timeout",
        "protected_input_violation",
    } <= reasons(value)


def test_stopping_observation_failure_needs_no_later_phases() -> None:
    value = attempt()
    value.update(
        observation={
            "status": "failed",
            "classification": "adapter_raised",
            "process": None,
        },
        scope=None,
        outputs=None,
        verification=None,
    )
    assert reasons(value) == {"observation_failed"}


@pytest.mark.parametrize(
    "value", [None, [], {"secret": "RAW-SENTINEL"}, {"attempt_id": "RAW-SENTINEL"}]
)
def test_malformed_input_returns_fixed_unknown_without_echo(value: object) -> None:
    result = classify(value)
    assert {item.reason for item in result.findings} == {"invalid_metadata"}
    assert "RAW-SENTINEL" not in repr(result)


@pytest.mark.parametrize(
    "phase,key,bad",
    [
        ("process", "exit_code", True),
        ("scope", "proceed", 1),
        ("command", "timed_out", 0),
        ("command", "exit_code", False),
        ("process", "process_status", []),
    ],
)
def test_invalid_strict_metadata_fails_closed(
    phase: str, key: str, bad: object
) -> None:
    value = attempt()
    target = (
        value["observation"]["process"]
        if phase == "process"
        else value["scope"]
        if phase == "scope"
        else value["verification"]["commands"][0]
    )
    target[key] = bad
    assert reasons(value) == {"invalid_metadata"}


@pytest.mark.parametrize("outputs", [True, [], "RAW-SENTINEL"])
def test_non_mapping_output_evidence_returns_fixed_unknown(outputs: object) -> None:
    value = attempt()
    value["outputs"] = outputs
    assert reasons(value) == {"invalid_metadata"}


def test_contradictory_technical_label_cannot_hide_unknown() -> None:
    value = attempt()
    value["verification"]["technical_kind"] = "fail"
    assert "contradictory_verification" in reasons(value)


@pytest.mark.parametrize("identity", ["line\nbreak", "nul\0id", "\x1bescape"])
def test_control_character_identity_is_not_echoed(identity: str) -> None:
    value = attempt()
    value["attempt_id"] = identity
    value["verification"]["attempt_id"] = identity
    assessment = classify(value)
    assert assessment.attempt_id is None
    assert {finding.reason for finding in assessment.findings} == {"invalid_metadata"}


@pytest.mark.parametrize(
    "outputs",
    [
        {},
        {"stderr": {}, "final_message": {}},
        {"stdout": {}, "final_message": {}},
        {"stdout": {}, "stderr": {}},
        {"stdout": [], "stderr": {}, "final_message": {}},
        {"stdout": {}, "stderr": None, "final_message": {}},
        {"stdout": {}, "stderr": {}, "final_message": True},
    ],
)
def test_incomplete_output_categories_return_fixed_unknown(outputs: object) -> None:
    value = attempt()
    value["outputs"] = outputs
    assert reasons(value) == {"invalid_metadata"}


def test_unavailable_final_output_accepts_explicit_none_category() -> None:
    value = attempt()
    value["observation"]["process"].update(
        process_status="timeout", final_output_status="missing"
    )
    value["outputs"]["final_message"] = None
    assert reasons(value) == {"native_timeout"}


@pytest.mark.parametrize("status,message", [("available", None), ("missing", {})])
def test_final_output_presence_must_agree_with_native_status(
    status: str, message: object
) -> None:
    value = attempt()
    value["observation"]["process"]["final_output_status"] = status
    value["outputs"]["final_message"] = message
    assert "contradictory_output" in reasons(value)
