"""Standalone presentation of an already captured explanation; no new facts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from html import escape
from typing import cast

_LABELS = {
    "pass": "통과 · 과거 기록",
    "fail": "실패",
    "blocked": "차단",
    "stopped": "중단",
    "human": "사람의 판단 필요",
    "unknown": "알 수 없음",
    "review_pending": "검토 대기",
    "evidence_incomplete": "증거 부족",
    "recorded_pass": "기록상 통과",
    "recorded_fail": "기록상 실패",
    "recorded_blocked": "기록상 차단",
    "recorded_unknown": "기록상 알 수 없음",
    "ready_for_manual_review": "수동 검토 준비됨 · 승인 아님",
    "incomplete": "불충분",
    "caller": "호출자",
    "independent_review": "독립 검토 필요",
    "inspect_and_reconcile_saved_evidence": "저장된 증거 확인·대조",
    "implemented_but_unwired": "구현됨 · 자동 연결되지 않음",
    "manual_not_recorded": "수동 승인 · 기록되지 않음",
    "automatic_mapping": "명령에 자동 매핑 · 의미적 증명 아님",
    "unverified": "검증되지 않음",
    "recorded_metadata": "저장된 메타데이터",
    "bound": "원본 설정에 연결됨",
    "not_supplied": "제공되지 않음",
    "match": "일치 · 현재 작업 공간의 증명 아님",
    "mismatch": "불일치",
    "unavailable": "기록되지 않아 확인 불가",
    "not_requested": "비교 요청 없음",
    "successful_exit": "정상 종료 · 검사 통과와 별개",
    "timeout": "시간 초과",
    "config_not_supplied": "원래 설정이 제공되지 않음",
    "config_unreadable_or_invalid_config": "설정을 읽을 수 없거나 유효하지 않음",
    "config_digest_mismatch": "설정 바이트 해시 불일치",
    "config_contract_mismatch": "설정과 기록의 계약 해시 불일치",
    "config_unbound": "설정의 원본 연결 기록 없음",
    "revision_mismatch": "비교한 리비전과 저장된 리비전이 다름",
    "candidate_sha256_mismatch": "비교한 내용 해시와 저장된 값이 다름",
    "candidate_content_unavailable": "후보 내용 식별값이 기록되지 않음",
    "missing_input": "기록 파일이 없음",
    "invalid_record": "유효하지 않은 기록",
    "inconsistent_history": "기록 간 불일치",
    "incomplete_history": "완결되지 않은 기록",
    "input_limit": "기록 크기 또는 항목 수 한도 초과",
    "active_criteria_not_passed": "마지막 시도의 기준별 명령이 모두 통과하지 않음",
    "recorded_execution_not_passed": "저장된 실행 결과가 통과가 아님",
    "historical_metadata_only": "과거 메타데이터만 표시",
    "no_current_workspace_capture": "현재 작업 공간을 캡처하지 않음",
    "caller_comparison_is_not_freshness_proof": "호출자의 비교값은 최신성 증명이 아님",
    "no_authentication": "기록의 진위를 인증하지 않음",
    "no_output_payload_verification": "원본 출력 내용을 검증하지 않음",
    "no_review_receipt_integration": "검토 영수증 자동 연결 없음",
    "no_execution_or_merge_authority": "실행·병합 권한을 부여하지 않음",
    "no_cross_attempt_aggregation": "서로 다른 시도를 합쳐 통과로 만들지 않음",
    "point_in_time_read_not_os_sandbox": "한 시점의 읽기이며 운영체제 격리가 아님",
    "attempt_details_are_saved_metadata_not_full_execution_replay": "시도 상세는 저장된 메타데이터이며 전체 실행 재연 검증이 아님",
    "package_version_is_not_implementation_digest": "패키지 버전은 정확한 소스 식별값이 아님",
}

_STYLE = """
:root{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo',sans-serif;
font-size:16px;color:#172630;background:#f3f6f3;line-height:1.65;
--paper:#fff;--border:#c9d4cd;--muted:#46594d;--accent:#0e5f49}
*{box-sizing:border-box;min-width:0}body{margin:0}main{max-width:72rem;margin:auto;
padding:clamp(1rem,4vw,3rem)}header{border-left:4px solid var(--accent);padding-left:1rem;
margin-bottom:2rem}.eyebrow{color:var(--accent);font-weight:750;letter-spacing:.12em}
h1{font-size:clamp(1.7rem,5vw,2.6rem);line-height:1.25;margin:.35rem 0 1rem}
h2{font-size:1.2rem;line-height:1.4;margin:0 0 .75rem}h3{font-size:1rem;margin:0 0 .5rem}
p{margin:.5rem 0 1rem}.muted{color:var(--muted)}.grid{display:grid;gap:1rem}
.panel,details{background:var(--paper);border:1px solid var(--border);border-radius:.75rem}
.panel{padding:1.25rem;margin-bottom:1rem}.headline{font-size:1.3rem;font-weight:750;
margin:.5rem 0}.notice{background:#fff4d6;border-color:#b39045}.facts{margin:0}
.facts>div{margin-bottom:1rem}.facts>div:last-child{margin:0}dt{font-weight:700;
color:var(--muted)}dd{margin:.25rem 0 0}code,pre{font-family:ui-monospace,SFMono-Regular,
Consolas,monospace;font-size:1rem;line-height:1.65}code{color:inherit}pre{margin:0;
white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}
p,li,dd,code,h1,h2,h3,summary{overflow-wrap:anywhere;word-break:break-word}
ul{margin:.5rem 0;padding-left:1.35rem}li{margin:.45rem 0}details{margin:0 0 1rem}
summary{cursor:pointer;min-height:44px;padding:.9rem 1rem;font-size:1rem;font-weight:750}
summary:focus-visible{outline:3px solid #0e5f49;outline-offset:3px;border-radius:.5rem}
.detail-body{padding:0 1rem 1.25rem}.record{padding:1rem 0;border-top:1px solid var(--border)}
.record:first-child{border-top:0}footer{margin-top:2rem;color:var(--muted)}
@media(min-width:60rem){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media print{body{background:#fff}main{max-width:none}details{break-inside:avoid}}
"""


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _items(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    )


def _value(value: object) -> str:
    if value is None:
        return "기록 없음"
    return escape(value if isinstance(value, str) else _json(value), quote=True)


def _label(value: object) -> str:
    if isinstance(value, str) and value in _LABELS:
        return f"{_LABELS[value]} <code>({_value(value)})</code>"
    if isinstance(value, (dict, list)):
        return f"<pre>{_value(value)}</pre>"
    return _value(value)


def _facts(rows: list[tuple[str, object]]) -> str:
    return (
        '<dl class="facts">'
        + "".join(
            f"<div><dt>{escape(title)}</dt><dd>{_label(value)}</dd></div>"
            for title, value in rows
        )
        + "</dl>"
    )


def _list(value: object) -> str:
    items = _items(value)
    if not items:
        return '<p class="muted">기록된 항목 없음</p>'
    return "<ul>" + "".join(f"<li>{_label(item)}</li>" for item in items) + "</ul>"


def _details(title: str, body: str) -> str:
    return f'<details><summary>{escape(title)}</summary><div class="detail-body">{body}</div></details>'


def _record_details(value: object, *, criteria: bool) -> str:
    records = _items(value)
    if not records:
        return (
            '<p class="muted">연결된 기록 없음 · 실행되지 않았다는 뜻은 아닙니다.</p>'
        )
    output = []
    for raw in records:
        row = _mapping(raw)
        if criteria:
            title = row.get("criterion")
            facts = [
                ("명령 연결 방식", row.get("mode")),
                ("명령 해시", row.get("command_sha256")),
                ("시도별 저장된 명령 결과", row.get("attempts")),
            ]
        else:
            title = row.get("attempt_id")
            facts = [
                ("관찰된 후보 HEAD", row.get("target_revision")),
                ("증거 상태", row.get("evidence_state")),
                ("네이티브 프로세스 · 검사 결과와 별개", row.get("native_process")),
                ("검사 명령 결과", row.get("checker_result")),
                ("저장된 명령 해시와 결과", row.get("commands")),
                ("후보·증거 연결", row.get("candidate_binding")),
                ("저널 항목 위치 · 0부터 시작", row.get("journal_event_indices")),
            ]
        output.append(
            f'<article class="record"><h3>{_value(title)}</h3>{_facts(facts)}</article>'
        )
    return "".join(output)


def _observed_time(value: object) -> str:
    if isinstance(value, str):
        try:
            observed = datetime.fromisoformat(value)
            if observed.tzinfo is not None:
                return observed.astimezone(timezone(timedelta(hours=9))).strftime(
                    "%Y-%m-%d %H:%M:%S KST"
                )
        except ValueError:
            pass
    return _value(value)


def render_execution_html(snapshot: dict[str, object]) -> str:
    """Render only supplied facts. No reads, writes, execution or new conclusions."""
    explanation = _mapping(snapshot.get("explanation"))
    next_gate = _mapping(explanation.get("next"))
    review = _mapping(explanation.get("review"))
    approval = _mapping(explanation.get("approval"))
    binding = snapshot.get("review_binding")
    raw = escape(_json(snapshot), quote=True)
    sections = "".join(
        [
            _details(
                "기준별 명령과 시도 결과",
                _record_details(explanation.get("criteria"), criteria=True),
            ),
            _details(
                "시도별 기록 · 서로 합쳐 통과로 만들지 않음",
                _record_details(explanation.get("attempts"), criteria=False),
            ),
            _details(
                "후보 식별과 비교 맥락",
                _facts(
                    [
                        ("저장된 후보 HEAD", snapshot.get("target_revision")),
                        ("후보·증거 연결", binding),
                        (
                            "기대값 비교 · 현재 작업 공간 증명 아님",
                            explanation.get("comparison"),
                        ),
                    ]
                ),
            ),
            _details(
                "원래 설정과 연결 상태",
                _facts([("설정 바이트와 계약 연결", explanation.get("config"))]),
            ),
            _details(
                "원본 증거 경로와 해시",
                _facts(
                    [
                        (
                            "저장된 증거 참조 · 내용을 다시 읽지 않음",
                            snapshot.get("evidence"),
                        )
                    ]
                ),
            ),
            _details(
                "진단과 판독 한계",
                "<h3>고정 진단</h3>"
                + _list(snapshot.get("diagnostics"))
                + "<h3>이 보고서가 증명하지 않는 것</h3>"
                + _list(explanation.get("limits")),
            ),
            _details("정확한 원본 스냅샷 · JSON", f"<pre data-snapshot>{raw}</pre>"),
        ]
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; connect-src 'none'; img-src 'none'; base-uri 'none'; form-action 'none'">
<title>Mercury · 과거 실행 검증 보고서</title><style>{_STYLE}</style></head>
<body><main><header><p class="eyebrow">MERCURY / 검증 기록</p>
<h1>과거 실행 검증 보고서</h1><p>과거 기록입니다. 현재 작업 공간은 다시 검증하지 않았습니다.</p>
<p class="muted">판독 시각: {_observed_time(explanation.get("observed_at"))}<br>
검사기 패키지 버전: {_value(explanation.get("inspector_version"))}<br>
패키지 버전은 정확한 소스 식별값이 아닙니다.</p></header>
<section aria-labelledby="conclusion"><h2 id="conclusion">현재 판독 결론</h2>
<p class="headline">{_label(explanation.get("conclusion"))}</p>
<div class="grid"><article class="panel"><h2>저장된 실행 결과</h2>
{_facts([("과거 실행 상태", snapshot.get("recorded_status")), ("저장된 이유", snapshot.get("recorded_reason"))])}</article>
<article class="panel"><h2>증거 준비 상태</h2>
{_facts([("수동 검토를 위한 증거", explanation.get("evidence_readiness")), ("수동 승인", approval.get("state")), ("검토의 자동 연결", review.get("state"))])}</article></div></section>
<section class="panel notice" aria-labelledby="blockers"><h2 id="blockers">차단 요인</h2>{_list(explanation.get("blockers"))}</section>
<section class="panel" aria-labelledby="next"><h2 id="next">다음 담당자와 행동</h2>
{_facts([("담당자", next_gate.get("actor")), ("필요한 다음 행동", next_gate.get("action"))])}
<p class="muted">읽기 전용 보고서입니다. 실행·재시도·승인·병합을 시작하지 않습니다.</p></section>
<section aria-labelledby="evidence"><h2 id="evidence">상세 근거</h2>{sections}</section>
<footer>저장된 명령 결과는 기준의 의미적 증명이 아닙니다. 원본 JSON과 기록의 식별값을 함께 확인하세요.</footer>
</main></body></html>"""
