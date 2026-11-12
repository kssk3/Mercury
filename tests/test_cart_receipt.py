"""Public cart receipts are bounded projections, never raw run-directory exports."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from test_cart_demo import invoke


def exporter() -> Any:
    folder = Path(__file__).resolve().parents[1] / "examples"
    sys.path.insert(0, str(folder))
    try:
        spec = importlib.util.spec_from_file_location(
            "cart_receipt", folder / "cart_receipt.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def test_export_is_public_and_hashes_link_snapshots(tmp_path: Path) -> None:
    _, run = invoke(tmp_path / "private", "correct")
    output = tmp_path / "public.json"
    exporter().export_cart_receipt(run, output)
    raw = output.read_text()
    receipt = json.loads(raw)
    assert receipt["schemaVersion"] == 1
    assert receipt["sourceMode"] == "recorded_simulated"
    assert receipt["recordedStatus"] == "pass"
    assert str(tmp_path) not in raw
    assert "repository_root" not in raw
    assert receipt["fixtureSha256"] != receipt["candidateSha256"]
    with pytest.raises((ValueError, FileExistsError)):
        exporter().export_cart_receipt(run, output)


def test_invalid_terminal_never_reuses_pass_checks(tmp_path: Path) -> None:
    _, run = invoke(tmp_path / "private", "correct")
    (run / "state/current.json").write_text("{}")
    capture_path = run / "capture.json"
    capture = json.loads(capture_path.read_text())
    capture.update(recordedStatus="unknown", recordedReason=None)
    capture_path.write_text(json.dumps(capture))
    output = tmp_path / "unknown.json"
    exporter().export_cart_receipt(run, output)
    receipt = json.loads(output.read_text())
    assert receipt["recordedStatus"] == "unknown"
    assert all(
        r["availability"] == "unavailable" and r["exitCode"] is None
        for r in receipt["checks"]
    )
    assert receipt["scope"]["availability"] == "unavailable"


@pytest.mark.parametrize(
    "field,value",
    [("sourceMode", "recorded_native"), ("unexpected", "PRIVATE"), ("beforeFiles", [])],
)
def test_reject_untrusted_capture(tmp_path: Path, field: str, value: object) -> None:
    _, run = invoke(tmp_path / "private", "correct")
    path = run / "capture.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        exporter().export_cart_receipt(run, tmp_path / "invalid.json")


def test_literal_utf8_hash_vector() -> None:
    assert (
        exporter().digest([{"path": "a.py", "content": "한글\n"}])
        == "5b61a6f73dd261cea2b459c9cf15e668c186303829a36dae531dca63845f682a"
    )


def test_public_output_cannot_write_arbitrary_source(tmp_path: Path) -> None:
    _, run = invoke(tmp_path / "private", "correct")
    source = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError):
        exporter().export_cart_receipt(run, source / "forbidden-cart-output.json")
    assert not (source / "forbidden-cart-output.json").exists()


@pytest.mark.parametrize("scenario", ["incomplete", "scope_escape"])
def test_export_preserves_detailed_verification_diagnostic(
    tmp_path: Path, scenario: str
) -> None:
    _, run = invoke(tmp_path / "private", scenario)
    output = tmp_path / "public.json"
    exporter().export_cart_receipt(run, output)
    receipt = json.loads(output.read_text())
    assert receipt["diagnostics"] == ["verification_unavailable"]
    expected_phases = (
        {"final"} if scenario == "incomplete" else {"development", "final"}
    )
    assert all(
        row["availability"] == "not_run"
        for row in receipt["checks"]
        if row["phase"] in expected_phases
    )


@pytest.mark.parametrize(
    "diagnostics", [["invented_diagnostic"], [None], "verification_unavailable"]
)
def test_export_rejects_unknown_or_malformed_detail_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, diagnostics: object
) -> None:
    module = exporter()
    private = tmp_path / "private"
    private.mkdir()
    capture = dict.fromkeys(module.CAPTURE_KEYS)
    capture.update(
        scenarioId="correct",
        sourceMode="recorded_simulated",
        runId="cart-test",
        capturedAt="2026-11-12T06:00:00+00:00",
        mercuryRevision="0" * 40,
        beforeFiles=module.expected_files(None),
        afterFiles=module.expected_files("correct"),
        checks=[],
        recordedStatus="pass",
        recordedReason="final_verification",
    )
    (private / "capture.json").write_text(json.dumps(capture))
    monkeypatch.setattr(
        module,
        "summarize_execution",
        lambda *args, **kwargs: {
            "recorded_status": "pass",
            "recorded_reason": "final_verification",
            "diagnostics": [],
            "details": {"diagnostics": diagnostics},
        },
    )
    with pytest.raises(ValueError, match="unsupported diagnostic"):
        module.export_cart_receipt(private, tmp_path / "public.json")


def test_diagnostics_union_is_sorted_and_deduplicated() -> None:
    assert exporter().public_diagnostics(
        ["scope_unavailable", "input_limit"],
        ["verification_unavailable", "input_limit"],
    ) == ["input_limit", "scope_unavailable", "verification_unavailable"]
