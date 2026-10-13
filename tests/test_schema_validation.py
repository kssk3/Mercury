"""Real-process contracts for the read-only project-state development gate."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tests/fixtures/project-state-v1.json"
SCHEMA = ROOT / "schemas/project-state-v1.schema.json"


def run_gate(
    *arguments: str, cwd: Path = ROOT, state: Path | None = STATE
) -> subprocess.CompletedProcess[str]:
    state_arguments = ["--state", str(state)] if state is not None else []
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_harness.schema_validation",
            *state_arguments,
            *arguments,
        ],
        cwd=cwd,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"}
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_gate_accepts_synthetic_fixture_without_writes() -> None:
    before = STATE.read_bytes()

    result = run_gate()

    assert result.returncode == 0, result.stderr
    assert "검증 통과" in result.stdout
    assert result.stderr == ""
    assert STATE.read_bytes() == before


def test_default_cli_requires_current_state_in_working_directory(
    tmp_path: Path,
) -> None:
    result = run_gate("--schema", str(SCHEMA), cwd=tmp_path, state=None)

    assert_rejected(result, ".project-state/current.json")
    assert not (tmp_path / ".project-state").exists()


def test_default_cli_accepts_disposable_current_state_without_writes(
    tmp_path: Path,
) -> None:
    current = tmp_path / ".project-state/current.json"
    current.parent.mkdir()
    current.write_bytes(STATE.read_bytes())
    schema = tmp_path / "schemas/project-state-v1.schema.json"
    schema.parent.mkdir()
    schema.write_bytes(SCHEMA.read_bytes())
    before = current.read_bytes(), schema.read_bytes()

    result = run_gate(cwd=tmp_path, state=None)

    assert result.returncode == 0, result.stderr
    assert "검증 통과: .project-state/current.json" in result.stdout
    assert result.stderr == ""
    assert (current.read_bytes(), schema.read_bytes()) == before


@pytest.fixture
def state() -> dict[str, Any]:
    value: dict[str, Any] = json.loads(STATE.read_text(encoding="utf-8"))
    return value


def assert_rejected(result: subprocess.CompletedProcess[str], field: str) -> None:
    assert result.returncode != 0
    assert result.stdout == ""
    assert "검증 실패" in result.stderr
    assert field in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("schema_version", "1"),
        ("project", " "),
        ("phase", "UNKNOWN"),
        ("current_unit.id", ""),
        ("current_unit.implementation_status", "COMPLETE"),
        ("current_unit.immutable_base", "abc"),
        ("current_unit.immutable_base", "a" * 40 + "\n"),
        ("observed_at", "2026-09-30\n"),
        ("last_delivered_runtime.merged_at", "2026-09-30T05:13:22Z\n"),
        ("delivery.state", "SUCCESS"),
        ("next_unit.status", "UNKNOWN"),
        ("last_delivered_runtime.pr", "17"),
        ("last_delivered_runtime.pr", 0),
        ("last_delivered_runtime.main_run_id", -1),
        ("last_delivered_runtime.head", "x" * 40),
        ("last_delivered_runtime.merge_commit", "a" * 39),
        ("last_delivered_runtime.merged_at", "yesterday"),
        ("last_delivered_runtime.static-checks", "PASSED"),
        ("last_delivered_runtime.tests-macos", False),
        ("last_delivered_runtime.full_tests", "194"),
        ("historical_d7_candidate_checks.focused_tests", -1),
        ("historical_e1_candidate_checks.review_tool_calls", True),
        ("role_policy.observed_runtime_model", 42),
        ("role_policy.implementation", False),
    ],
)
def test_gate_rejects_invalid_known_field_without_writes(
    tmp_path: Path, state: dict[str, Any], field: str, value: object
) -> None:
    keys = field.split(".")
    parent = state
    for key in keys[:-1]:
        parent = parent[key]
    parent[keys[-1]] = value
    candidate = tmp_path / "invalid state.json"
    candidate.write_text(json.dumps(state), encoding="utf-8")
    before = candidate.read_bytes(), SCHEMA.read_bytes()

    result = run_gate("--state", str(candidate))

    assert_rejected(result, field)
    assert str(candidate) in result.stderr
    assert (candidate.read_bytes(), SCHEMA.read_bytes()) == before


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "current_unit.id",
        "project",
        "repository",
        "phase",
        "workflow",
        "policy_decision",
        "current_unit",
        "delivery",
        "next_unit",
        "current_unit.name",
        "current_unit.implementation_status",
        "current_unit.plan_file",
        "current_unit.report_file",
        "current_unit.immutable_base",
        "current_unit.next_gate",
        "delivery.state",
        "delivery.repository",
        "delivery.base",
        "delivery.head_branch",
        "next_unit.id",
        "next_unit.status",
        "next_unit.scope",
        "last_delivered_ci.unit",
        "last_delivered_ci.pr",
        "last_delivered_ci.head",
        "last_delivered_ci.merge_commit",
        "last_delivered_ci.merged_at",
        "last_delivered_ci.main_run_id",
        "last_delivered_ci.static-checks",
        "last_delivered_ci.tests-macos",
    ],
)
def test_gate_rejects_missing_required_field(
    tmp_path: Path, state: dict[str, Any], field: str
) -> None:
    if field == "last_delivered_ci.tests-macos":
        # Exercise the legacy schema independently of the latest delivered policy.
        state["last_delivered_ci"] = {
            "unit": "legacy-ci",
            "pr": 18,
            "head": "a" * 40,
            "merge_commit": "b" * 40,
            "merged_at": "2026-09-30T05:24:36Z",
            "main_run_id": 123,
            "static-checks": "SUCCESS",
            "tests-macos": "SUCCESS",
        }
    keys = field.split(".")
    parent = state
    for key in keys[:-1]:
        parent = parent[key]
    del parent[keys[-1]]
    candidate = tmp_path / "missing.json"
    candidate.write_text(json.dumps(state), encoding="utf-8")

    result = run_gate("--state", str(candidate))

    assert_rejected(result, keys[-1])


def test_version_one_allows_unknown_extensions_at_each_object_level(
    tmp_path: Path, state: dict[str, Any]
) -> None:
    state["future_extension"] = {"arbitrary": [1, None]}
    state["current_unit"]["future_extension"] = True
    state["last_delivered_runtime"]["future_extension"] = ["value"]
    candidate = tmp_path / "extension.json"
    candidate.write_text(json.dumps(state), encoding="utf-8")
    before = candidate.read_bytes()

    result = run_gate("--state", str(candidate), "--schema", str(SCHEMA))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert candidate.read_bytes() == before


@pytest.mark.parametrize("option", ["--state", "--schema"])
@pytest.mark.parametrize(
    "text", ['{"broken":', '{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}']
)
def test_gate_rejects_non_json_without_traceback(
    tmp_path: Path, option: str, text: str
) -> None:
    candidate = tmp_path / "non-json.json"
    candidate.write_text(text, encoding="utf-8")
    before = candidate.read_bytes()

    result = run_gate(option, str(candidate))

    assert_rejected(result, str(candidate))
    assert candidate.read_bytes() == before


@pytest.mark.parametrize("option", ["--state", "--schema"])
@pytest.mark.parametrize("kind", ["missing", "directory", "non-utf8"])
def test_gate_rejects_unreadable_input(tmp_path: Path, option: str, kind: str) -> None:
    candidate = tmp_path / kind
    if kind == "directory":
        candidate.mkdir()
    elif kind == "non-utf8":
        candidate.write_bytes(b"\xff")

    result = run_gate(option, str(candidate))

    assert_rejected(result, str(candidate))
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "unknown"},
        {"required": "field"},
        {"$ref": "#/$defs/missing"},
        {"$schema": "https://json-schema.org/draft-07/schema#"},
        {"$ref": "https://example.invalid/schema.json"},
        {"$dynamicRef": "https://example.invalid/schema.json"},
        {"$defs": {"unused": {"$ref": "file:///tmp/sg1-other.json"}}},
        {"$defs": {"nested": {"$schema": "https://example.invalid/meta"}}},
    ],
)
def test_gate_rejects_invalid_or_external_schema(
    tmp_path: Path, schema: dict[str, Any]
) -> None:
    candidate = tmp_path / "invalid-schema.json"
    candidate.write_text(json.dumps(schema), encoding="utf-8")
    before = candidate.read_bytes(), STATE.read_bytes()

    result = run_gate("--schema", str(candidate))

    assert_rejected(result, str(candidate))
    assert (candidate.read_bytes(), STATE.read_bytes()) == before


def test_gate_accepts_local_fragment_reference(tmp_path: Path) -> None:
    candidate = tmp_path / "local-schema.json"
    candidate.write_text(
        json.dumps(
            {
                "$defs": {"state": {"type": "object"}},
                "$ref": "#/$defs/state",
            }
        ),
        encoding="utf-8",
    )

    result = run_gate("--schema", str(candidate))

    assert result.returncode == 0, result.stderr


def test_external_schema_reference_never_opens_network_connection(
    tmp_path: Path,
) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.1)
        port = listener.getsockname()[1]
        candidate = tmp_path / "remote-schema.json"
        candidate.write_text(
            json.dumps({"$ref": f"http://127.0.0.1:{port}/schema.json"}),
            encoding="utf-8",
        )

        result = run_gate("--schema", str(candidate))

        assert_rejected(result, str(candidate))
        with pytest.raises(TimeoutError):
            listener.accept()


@pytest.mark.parametrize(
    ("keyword", "target"),
    [
        ("$ref", "x"),
        ("$ref", 42),
        ("$ref", None),
        ("$ref", []),
        ("$ref", {"type": "unknown"}),
        ("$ref", {"required": "field"}),
        ("$dynamicRef", "x"),
    ],
)
def test_gate_rejects_invalid_local_reference_target_without_traceback_or_writes(
    tmp_path: Path, keyword: str, target: object
) -> None:
    candidate = tmp_path / "invalid-local-target.json"
    candidate.write_text(
        json.dumps({"extension": target, keyword: "#/extension"}),
        encoding="utf-8",
    )
    before = candidate.read_bytes(), STATE.read_bytes()

    result = run_gate("--schema", str(candidate))

    assert_rejected(result, "#/extension")
    assert str(candidate) in result.stderr
    assert (candidate.read_bytes(), STATE.read_bytes()) == before


def test_gate_rejects_reference_to_annotation_without_traceback(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "review-nonschema-ref.json"
    candidate.write_text(
        json.dumps({"title": "x", "$ref": "#/title"}), encoding="utf-8"
    )

    result = run_gate("--schema", str(candidate))

    assert_rejected(result, "#/title")
    assert str(candidate) in result.stderr


@pytest.mark.parametrize(
    "schema",
    [
        {"extension": {"$ref": "#/title"}, "title": "x", "$ref": "#/extension"},
        {"title": "x", "properties": {"absent": {"$ref": "#/title"}}},
        {
            "$defs": {
                "nested": {
                    "$id": "nested",
                    "extension": {"type": "unknown"},
                    "$ref": "#/extension",
                }
            },
        },
    ],
)
def test_gate_rejects_invalid_nested_local_reference_target(
    tmp_path: Path, schema: dict[str, Any]
) -> None:
    candidate = tmp_path / "nested-invalid-reference.json"
    candidate.write_text(json.dumps(schema), encoding="utf-8")

    result = run_gate("--schema", str(candidate))

    assert_rejected(result, str(candidate))
    assert "$ref" in result.stderr


@pytest.mark.parametrize(
    "schema",
    [
        {"extension": True, "$ref": "#/extension"},
        {"extension": {"type": "object"}, "$ref": "#/extension"},
        {"$defs": {"state": {"$anchor": "state", "type": "object"}}, "$ref": "#state"},
        {
            "$defs": {"state": {"$dynamicAnchor": "state", "type": "object"}},
            "$dynamicRef": "#state",
        },
        {
            "$id": "https://example.invalid/root",
            "properties": {
                "current_unit": {
                    "$id": "unit",
                    "$defs": {"unit": {"type": "object"}},
                    "$ref": "#/$defs/unit",
                }
            },
        },
    ],
)
def test_gate_preserves_valid_local_reference_targets(
    tmp_path: Path, schema: dict[str, Any]
) -> None:
    candidate = tmp_path / "valid-local-reference.json"
    candidate.write_text(json.dumps(schema), encoding="utf-8")
    before = candidate.read_bytes(), STATE.read_bytes()

    result = run_gate("--schema", str(candidate))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert (candidate.read_bytes(), STATE.read_bytes()) == before


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
@pytest.mark.parametrize("intermediate", [None, 42, True, "text"])
def test_gate_rejects_scalar_local_pointer_traversal_without_traceback_or_writes(
    tmp_path: Path, keyword: str, intermediate: object
) -> None:
    candidate = tmp_path / "review-scalar-traversal.json"
    candidate.write_text(
        json.dumps({"extension": intermediate, keyword: "#/extension/foo"}),
        encoding="utf-8",
    )
    before = candidate.read_bytes(), STATE.read_bytes()

    result = run_gate("--schema", str(candidate))

    assert_rejected(result, "#/extension/foo")
    assert str(candidate) in result.stderr
    assert "유효하지 않은 스키마" in result.stderr
    assert (candidate.read_bytes(), STATE.read_bytes()) == before


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
def test_gate_preserves_valid_local_pointer_through_array(
    tmp_path: Path, keyword: str
) -> None:
    candidate = tmp_path / "array-local-pointer.json"
    candidate.write_text(
        json.dumps({"extension": [{"type": "object"}], keyword: "#/extension/0"}),
        encoding="utf-8",
    )
    before = candidate.read_bytes(), STATE.read_bytes()

    result = run_gate("--schema", str(candidate))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert (candidate.read_bytes(), STATE.read_bytes()) == before


@pytest.fixture
def linux_receipt() -> dict[str, Any]:
    evidence = {
        "platform": "Darwin",
        "revision": "a" * 40,
        "report_file": "artifacts/ci-report.json",
        "observed_at": "2026-10-01T07:00:00Z",
        "command": "uv run --no-sync pytest",
        "result": "SUCCESS",
    }
    return {
        "unit": "CI3",
        "pr": 25,
        "head": "a" * 40,
        "merge_commit": "b" * 40,
        "merged_at": "2026-10-01T07:00:00Z",
        "main_run_id": 123,
        "static-checks": "SUCCESS",
        "ci_policy": "linux-local-macos-v1",
        "record_only": False,
        "tests-linux": "SUCCESS",
        "macos_verification": {
            "status": "SUCCESS",
            "candidate": {**evidence, "base": "c" * 40},
            "main": {**evidence, "revision": "b" * 40},
        },
    }


def receipt_gate(
    tmp_path: Path, state: dict[str, Any], receipt: dict[str, Any]
) -> subprocess.CompletedProcess[str]:
    state["last_delivered_ci"] = receipt
    candidate = tmp_path / "linux-receipt.json"
    candidate.write_text(json.dumps(state), encoding="utf-8")
    return run_gate("--state", str(candidate))


@pytest.mark.parametrize("record_only", [False, True])
def test_new_policy_accepts_complete_receipt_without_legacy_macos_job(
    tmp_path: Path,
    state: dict[str, Any],
    linux_receipt: dict[str, Any],
    record_only: bool,
) -> None:
    if record_only:
        linux_receipt.update(record_only=True, **{"tests-linux": "SKIPPED"})
        linux_receipt["macos_verification"] = {
            "status": "NOT_REQUIRED",
            "reason": "Complete record-only diff classification",
        }
    result = receipt_gate(tmp_path, state, linux_receipt)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ci_policy", "unknown"),
        ("ci_policy", None),
        ("record_only", "false"),
        ("tests-linux", "SKIPPED"),
        ("tests-linux", "FAILURE"),
        ("static-checks", "FAILURE"),
        ("tests-macos", "SUCCESS"),
        ("macos_verification.status", "NOT_REQUIRED"),
        ("macos_verification.candidate.platform", "Linux"),
        ("macos_verification.main.platform", "darwin"),
        ("macos_verification.candidate.revision", "a" * 39),
        ("macos_verification.candidate.base", "a" * 40 + "\n"),
        ("macos_verification.main.observed_at", "yesterday"),
        ("macos_verification.main.report_file", " "),
        ("macos_verification.candidate.command", " "),
        ("macos_verification.main.result", "FAILURE"),
    ],
)
def test_new_policy_rejects_contradictory_or_malformed_evidence(
    tmp_path: Path,
    state: dict[str, Any],
    linux_receipt: dict[str, Any],
    field: str,
    value: object,
) -> None:
    parent = linux_receipt
    keys = field.split(".")
    for key in keys[:-1]:
        parent = parent[key]
    parent[keys[-1]] = value
    result = receipt_gate(tmp_path, state, linux_receipt)
    assert_rejected(result, keys[-1])


@pytest.mark.parametrize(
    "field",
    [
        "record_only",
        "tests-linux",
        "macos_verification",
        "macos_verification.candidate",
        "macos_verification.main",
        "macos_verification.candidate.base",
        "macos_verification.main.revision",
        "macos_verification.main.platform",
        "macos_verification.main.report_file",
        "macos_verification.main.observed_at",
        "macos_verification.main.command",
        "macos_verification.main.result",
    ],
)
def test_new_policy_rejects_missing_required_evidence(
    tmp_path: Path,
    state: dict[str, Any],
    linux_receipt: dict[str, Any],
    field: str,
) -> None:
    parent = linux_receipt
    keys = field.split(".")
    for key in keys[:-1]:
        parent = parent[key]
    del parent[keys[-1]]
    assert_rejected(receipt_gate(tmp_path, state, linux_receipt), keys[-1])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tests-linux", "SUCCESS"),
        ("record_only", False),
        ("macos_verification.status", "SUCCESS"),
        ("macos_verification.reason", " "),
    ],
)
def test_record_only_receipt_rejects_full_or_unexplained_outcomes(
    tmp_path: Path,
    state: dict[str, Any],
    linux_receipt: dict[str, Any],
    field: str,
    value: object,
) -> None:
    linux_receipt.update(record_only=True, **{"tests-linux": "SKIPPED"})
    linux_receipt["macos_verification"] = {
        "status": "NOT_REQUIRED",
        "reason": "Validated record-only paths",
    }
    parent = linux_receipt
    keys = field.split(".")
    for key in keys[:-1]:
        parent = parent[key]
    parent[keys[-1]] = value
    assert_rejected(receipt_gate(tmp_path, state, linux_receipt), keys[-1])
