"""Explicit external receipt projection must not change historical conclusions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_explanation import bound_config
from test_external_checks import collect

from agent_harness.cli import main
from agent_harness.explanation import explain_execution
from agent_harness.html_report import render_execution_html


def test_explicit_external_receipt_does_not_promote_historical_state(
    tmp_path: Path,
) -> None:
    bundle, _ = collect(tmp_path)
    baseline: Any = explain_execution(tmp_path / "missing")
    snapshot: Any = explain_execution(
        tmp_path / "missing", external_checks=(bundle / "receipt.json",)
    )
    assert (
        snapshot["explanation"]["conclusion"] == baseline["explanation"]["conclusion"]
    )
    assert snapshot["diagnostics"] == baseline["diagnostics"]
    checks = snapshot["explanation"]["external_checks"]
    assert checks[0]["outcome"] == "PASS"
    assert checks[0]["historical_applicability"] == "unavailable"
    assert checks[0]["origin"] == "external_command_receipt"
    html = render_execution_html(snapshot)
    assert 'href="#external-checks"' in html
    assert 'id="external-checks"' in html
    assert "python [redacted]" in html
    assert "PRIVATE-OUTPUT" not in html and "private-test" not in html


def test_invalid_external_receipt_is_unavailable_without_execution(
    tmp_path: Path,
) -> None:
    snapshot: Any = explain_execution(
        tmp_path, external_checks=(tmp_path / "receipt.json",)
    )
    assert snapshot["explanation"]["external_checks"][0]["state"] == "unavailable"


def test_different_target_remains_mismatch_and_renderer_is_pure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    bundle, _ = collect(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    config = bound_config(state)
    baseline: Any = explain_execution(state, config_path=config)
    snapshot: Any = explain_execution(
        state, config_path=config, external_checks=str(bundle / "receipt.json")
    )
    assert (
        snapshot["explanation"]["external_checks"][0]["historical_applicability"]
        == "mismatch"
    )
    for field in ("conclusion", "evidence_readiness", "blockers"):
        assert snapshot["explanation"][field] == baseline["explanation"][field]

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("renderer attempted I/O")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    snapshot["explanation"]["external_checks"][0]["caller_command_display"] = (
        '<script src="https://invalid.example/">'
    )
    html = render_execution_html(snapshot)
    assert "&lt;script" in html
    assert '<script src="https://invalid.example/">' not in html


def test_external_cli_requires_explain_and_keeps_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle, _ = collect(tmp_path)
    with pytest.raises(SystemExit) as error:
        main(
            [
                "status",
                "--state-dir",
                str(tmp_path),
                "--external-check",
                str(bundle / "receipt.json"),
            ]
        )
    assert error.value.code == 2
    capsys.readouterr()
    assert (
        main(
            [
                "status",
                "--state-dir",
                str(tmp_path),
                "--explain",
                "--external-check",
                str(bundle / "receipt.json"),
            ]
        )
        == 1
    )
    data = json.loads(capsys.readouterr().out)
    assert data["explanation"]["external_checks"][0]["outcome"] == "PASS"


@pytest.mark.parametrize(
    "counts,expected",
    [
        (
            {"tests": 7, "failures": 1, "errors": 1, "skipped": 2},
            "전체 7 · 통과 3 · 실패 1 · 오류 1 · 건너뜀 2",
        ),
        ({"tests": 1, "failures": 2, "errors": 0, "skipped": 0}, "개수 확인 불가"),
        ({"tests": 1, "failures": -1, "errors": 0, "skipped": 0}, "개수 확인 불가"),
    ],
)
def test_external_card_exposes_readable_results_before_evidence_details(
    tmp_path: Path, counts: dict[str, int], expected: str
) -> None:
    """Contract: visible summary separates process/result and validates derived counts.

    Full hashes and structured evidence remain available in native disclosures;
    this test does not prescribe pixel heights or browser-specific text wrapping.
    """
    snapshot: Any = explain_execution(tmp_path)
    snapshot["explanation"]["external_checks"] = [
        {
            "check_label": "실제 검사",
            "actor": "검사 운영자",
            "criterion": "판독 기준",
            "outcome": "TIMEOUT",
            "process": {"state": "TIMEOUT", "exit_code": None, "timed_out": True},
            "tests": {"state": "FAIL", "counts": counts},
            "after": {"candidate_sha256": "a" * 64},
            "historical_applicability": "mismatch",
            "subject_state": "changed",
            "working_directory": "/private/long/evidence/path",
        }
    ]
    html = render_execution_html(snapshot)
    section = html.split('id="external-checks"', 1)[1]
    summary = section.split("<article", 1)[1].split("<details>", 1)[0]
    assert expected in summary
    assert "프로세스" in summary and "TIMEOUT" in summary
    assert "JUnit" in summary and "FAIL" in summary
    assert "실제 검사" in summary and "검사 운영자" in summary
    assert "mismatch" in summary and "changed" in summary
    assert "/private/long/evidence/path" not in summary
    assert "<details><summary>실행 환경·시간·명령" in section
    assert "/private/long/evidence/path" in section
