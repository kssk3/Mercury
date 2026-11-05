"""Synthetic saved records exercise provenance, never actual native execution.

Contract: expose only captured validated identities/results, separate ownership,
and navigate fixed internal evidence anchors. Backend configuration does not
prove launch counts, model identity, timestamps, or output payload acquisition.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from test_explanation import bound_config, history, persist
from test_html_report import parse_report

from agent_harness.cli import main
from agent_harness.explanation import explain_execution
from agent_harness.html_report import render_execution_html


def test_bound_provenance_identifies_captured_bytes_and_saved_attempt(
    tmp_path: Path,
) -> None:
    config = bound_config(tmp_path)
    snapshot: Any = explain_execution(tmp_path, config_path=config)
    assert "provenance" in snapshot["explanation"]
    provenance = snapshot["explanation"]["provenance"]
    assert provenance["ownership"] == {
        "saved_execution": "harness_journal",
        "saved_checks": "harness_checker",
        "external_operator_checks": "not_ingested",
        "independent_reviews": "not_ingested",
    }
    execution = provenance["execution"]
    assert execution["configured_backend"] == "codex"
    assert execution["native_launch_count"] is None
    assert execution["model_identity"] is None
    assert execution["started_at"] is None and execution["ended_at"] is None
    target = provenance["targets"][0]
    attempt = snapshot["explanation"]["attempts"][0]
    for key in (
        "attempt_id",
        "repository_root",
        "repository_id",
        "target_revision",
        "commands",
    ):
        assert target[key] == attempt[key]
    assert target["candidate_sha256"] == snapshot["review_binding"]["candidate_sha256"]
    assert target["current_workspace_verified"] is False
    cards = {card["id"]: card for card in provenance["evidence"]}
    for key, filename in (
        ("current", "current.json"),
        ("journal", "events.jsonl"),
        ("config", "config.json"),
    ):
        assert cards[key]["path"] == str(tmp_path / filename)
        assert (
            cards[key]["sha256"]
            == hashlib.sha256((tmp_path / filename).read_bytes()).hexdigest()
        )
        assert cards[key]["digest_kind"] == "captured_bytes"
    assert cards["outputs"]["digest_kind"] == "recorded_identifier_payload_not_read"
    assert target["journal_event_indices"] == attempt["journal_event_indices"]


@pytest.mark.parametrize("mode", ["legacy", "missing", "corrupt", "mismatch"])
def test_unavailable_provenance_never_becomes_current_verification(
    tmp_path: Path, mode: str
) -> None:
    config = bound_config(tmp_path, content=mode != "legacy")
    kwargs: dict[str, Any] = {"config_path": config}
    if mode == "missing":
        (tmp_path / "current.json").unlink()
    elif mode == "corrupt":
        (tmp_path / "events.jsonl").write_text("{\n")
    elif mode == "mismatch":
        kwargs["expected_revision"] = "f" * 40
    snapshot: Any = explain_execution(tmp_path, **kwargs)
    provenance = snapshot["explanation"]["provenance"]
    assert provenance["execution"]["native_launch_count"] is None
    assert snapshot["current_workspace_verified"] is False
    assert snapshot["explanation"]["evidence_readiness"] == "incomplete"
    if mode in {"missing", "corrupt"}:
        assert provenance["targets"] == []
        assert all(card["state"] == "unavailable" for card in provenance["evidence"])
        assert provenance["execution"]["configured_backend"] is None
    elif mode == "legacy":
        assert provenance["targets"][0]["candidate_sha256"] is None
    else:
        assert provenance["targets"][0]["comparison"]["revision"] == "mismatch"


@pytest.mark.parametrize("timed_out,exit_code", [(False, 1), (True, None)])
def test_saved_failed_checker_stays_separate_from_normal_process(
    tmp_path: Path, timed_out: bool, exit_code: int | None
) -> None:
    config = bound_config(tmp_path, content=False)
    state = json.loads((tmp_path / "current.json").read_bytes())
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_bytes().splitlines()
    ]
    for event in [*events, state["attempt_summary"]["active_attempt"]["verification"]]:
        if event.get("event") == "attempt_verification":
            event.update(technical_kind="fail", overall_kind="fail")
            event["commands"][0].update(exit_code=exit_code, timed_out=timed_out)
    events[-1]["status"] = state["loop_summary"]["status"] = "fail"
    persist(tmp_path, state, events)
    snapshot: Any = explain_execution(tmp_path, config_path=config)
    target = snapshot["explanation"]["provenance"]["targets"][0]
    assert target["native_process"]["process_status"] == "successful_exit"
    assert target["checker_result"]["overall_kind"] == "fail"
    assert target["commands"][0]["timed_out"] is timed_out
    assert target["commands"][0]["exit_code"] == exit_code


def test_provenance_keeps_separate_attempt_targets(tmp_path: Path) -> None:
    state, events = history(tmp_path)
    older = copy.deepcopy(events[1:-1])
    for event in older:
        event["attempt_id"] = "older"
        if event.get("event") == "attempt_verification":
            event["commands"][0]["command_sha256"] = "f" * 64
    events[1:1] = older
    state["loop_summary"]["attempt_ids"] = events[-1]["attempt_ids"] = [
        "older",
        "attempt-one",
    ]
    state["attempt_summary"]["attempt_count"] = 2
    persist(tmp_path, state, events)
    snapshot: Any = explain_execution(tmp_path)
    targets = {
        t["attempt_id"]: t for t in snapshot["explanation"]["provenance"]["targets"]
    }
    assert targets["older"]["commands"][0]["command_sha256"] == "f" * 64
    assert targets["attempt-one"]["commands"] != targets["older"]["commands"]
    assert set(targets["older"]["journal_event_indices"]).isdisjoint(
        targets["attempt-one"]["journal_event_indices"]
    )


def test_html_links_only_fixed_anchors_and_preserves_raw_projection(
    tmp_path: Path,
) -> None:
    config = bound_config(tmp_path)
    snapshot: Any = explain_execution(tmp_path, config_path=config)
    hostile = 'javascript:alert(1)"><img src="https://invalid.example/">'
    snapshot["explanation"]["provenance"]["evidence"][0]["path"] = hostile
    report = render_execution_html(snapshot)
    parsed, decoded = parse_report(report)
    assert decoded == snapshot
    ids = {attrs["id"] for _, attrs in parsed.tags if "id" in attrs}
    links = [attrs["href"] for tag, attrs in parsed.tags if tag == "a"]
    assert {"#evidence-current", "#evidence-journal", "#evidence-config"}.issubset(
        links
    )
    assert all(link and link.startswith("#") and link[1:] in ids for link in links)
    assert any(link and link.startswith("#journal-event-") for link in links)
    assert not any(tag in {"img", "script", "iframe"} for tag, _ in parsed.tags)
    assert hostile not in report
    assert "검증 주체와 실행 출처" in report and "검증 대상" in report
    assert "내용을 읽지 않은 출력 식별값" in report
    assert "a:focus-visible" in report


def test_actual_cli_adds_provenance_only_with_explain(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = bound_config(tmp_path)
    assert main(["status", "--state-dir", str(tmp_path)]) == 0
    assert "explanation" not in json.loads(capsys.readouterr().out)
    assert (
        main(
            [
                "status",
                "--state-dir",
                str(tmp_path),
                "--explain",
                "--config",
                str(config),
            ]
        )
        == 0
    )
    projected = json.loads(capsys.readouterr().out)
    assert (
        projected["explanation"]["provenance"]["ownership"]["saved_checks"]
        == "harness_checker"
    )


def test_unbound_saved_process_is_not_a_real_native_launch_receipt(
    tmp_path: Path,
) -> None:
    history(tmp_path)
    snapshot: Any = explain_execution(tmp_path)
    provenance = snapshot["explanation"]["provenance"]
    assert provenance["execution"]["configured_backend"] is None
    assert provenance["execution"]["native_launch_count"] is None
    assert provenance["execution"]["model_identity"] is None
    assert provenance["execution"]["tool_use"] == "unavailable"
    assert (
        provenance["targets"][0]["native_process"]["process_status"]
        == "successful_exit"
    )


def test_hostile_event_indices_cannot_create_arbitrary_links(tmp_path: Path) -> None:
    config = bound_config(tmp_path)
    snapshot: Any = explain_execution(tmp_path, config_path=config)
    malicious = '"><img src="https://invalid.example/">'
    snapshot["explanation"]["provenance"]["targets"][0]["journal_event_indices"] = [
        malicious,
        "javascript:alert(1)",
        True,
        -1,
        0,
    ]
    parsed, decoded = parse_report(render_execution_html(snapshot))
    assert decoded == snapshot
    links = [attrs["href"] for tag, attrs in parsed.tags if tag == "a"]
    assert "#journal-event-0" in links
    assert all(
        link
        and (
            link.startswith(("#evidence-", "#journal-event-0"))
            or link
            in {"#provenance-owner", "#verification-target", "#provenance-evidence"}
        )
        for link in links
    )
    assert not any(tag in {"img", "script"} for tag, _ in parsed.tags)
    assert not any(attrs.get("id") == malicious for _, attrs in parsed.tags)


@pytest.mark.parametrize("mode", ["missing", "adapter_raised"])
def test_absent_process_receipt_is_unavailable_even_with_target(
    tmp_path: Path, mode: str
) -> None:
    if mode == "adapter_raised":
        state, events = history(tmp_path)
        observed = next(
            event for event in events if event["event"] == "turn_observation"
        )
        observed.clear()
        observed.update(
            event="turn_observation_failed",
            attempt_id="attempt-one",
            classification="adapter_raised",
        )
        active = state["attempt_summary"]["active_attempt"]
        active.update(
            observation={"status": "failed"}, after_head=None, verification=None
        )
        events = [event for event in events if event["event"] != "attempt_verification"]
        events[-1]["status"] = state["loop_summary"]["status"] = "unknown"
        persist(tmp_path, state, events)
    snapshot: Any = explain_execution(tmp_path)
    provenance = snapshot["explanation"]["provenance"]
    assert provenance["execution"]["receipt_state"] == "unavailable"
    assert provenance["record_availability"] == {
        "saved_execution": "unavailable",
        "saved_checks": "unavailable",
    }
    report = render_execution_html(snapshot)
    assert "예상 기록 출처 · 관찰 여부와 별개" in report


def test_report_header_navigation_and_compact_ownership_are_accessible(
    tmp_path: Path,
) -> None:
    config = bound_config(tmp_path)
    snapshot: Any = explain_execution(tmp_path, config_path=config)
    report = render_execution_html(snapshot)
    parsed, decoded = parse_report(report)
    assert decoded == snapshot
    assert '<nav aria-label="보고서 내부 탐색">' in report
    header = report.split("</header>", 1)[0]
    for target in ("provenance-owner", "verification-target", "provenance-evidence"):
        assert f'href="#{target}"' in header
    assert 'class="grid provenance-owner-facts"' in report
    assert "<summary>추가 실행 식별 정보 · 기록 없음은 알 수 없음</summary>" in report
    owner = report.split('id="provenance-owner"', 1)[1].split("</section>", 1)[0]
    assert (
        "확인된 네이티브 실행 횟수 · 미기록은 0이 아님"
        in owner.split("<details>", 1)[0]
    )
    assert all(
        any(attrs.get("id") == target for _, attrs in parsed.tags)
        for target in ("provenance-owner", "verification-target", "provenance-evidence")
    )
