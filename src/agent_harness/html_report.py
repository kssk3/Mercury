"""Standalone presentation of an already captured explanation; no new facts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from html import escape
from typing import cast

_LABELS = {
    "harness_journal": "Mercury 저장 저널",
    "harness_checker": "Mercury 저장 검사기 메타데이터",
    "operator_supplied_config": "호출자가 제공한 원래 설정",
    "not_ingested": "자동 수집하지 않음 · 결과 알 수 없음",
    "captured": "이번 판독에서 캡처한 바이트",
    "saved_process_metadata_only": "저장된 프로세스 메타데이터만 있음",
    "captured_bytes": "캡처한 바이트의 SHA-256 · 진위 인증 아님",
    "recorded_identifier_payload_not_read": "내용을 읽지 않은 출력 식별값",
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
nav{display:flex;flex-wrap:wrap;gap:.5rem;margin:1rem 0 0}nav a{display:inline-flex;align-items:center;min-height:44px;padding:.35rem .7rem;border:1px solid var(--border);border-radius:.5rem}
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
a{color:var(--accent);text-underline-offset:.2em}a:focus-visible,summary:focus-visible{outline:3px solid #0e5f49;outline-offset:3px;border-radius:.5rem}
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


def _provenance_sections(value: object) -> str:
    provenance = _mapping(value)
    ownership = _mapping(provenance.get("ownership"))
    availability = _mapping(provenance.get("record_availability"))
    execution = _mapping(provenance.get("execution"))
    owner = (
        '<div class="grid provenance-owner-facts">'
        + _facts(
            [
                ("예상 기록 출처 · 관찰 여부와 별개", ownership.get("saved_execution")),
                ("실행 프로세스 기록의 관찰 상태", availability.get("saved_execution")),
                ("예상 검사 결과 출처", ownership.get("saved_checks")),
                ("검사 결과 기록의 관찰 상태", availability.get("saved_checks")),
            ]
        )
        + _facts(
            [
                ("외부 운영자 검사", ownership.get("external_operator_checks")),
                ("독립 검토 영수증", ownership.get("independent_reviews")),
                (
                    "설정된 백엔드 · 실제 실행 증명 아님",
                    execution.get("configured_backend"),
                ),
                ("실행 증거 범위", execution.get("receipt_state")),
                (
                    "확인된 네이티브 실행 횟수 · 미기록은 0이 아님",
                    execution.get("native_launch_count"),
                ),
            ]
        )
        + "</div>"
        + _details(
            "추가 실행 식별 정보 · 기록 없음은 알 수 없음",
            _facts(
                [
                    ("실제 모델 식별값", execution.get("model_identity")),
                    ("실행 시작 시각", execution.get("started_at")),
                    ("실행 종료 시각", execution.get("ended_at")),
                    ("실행 환경 식별값", execution.get("environment_identity")),
                    ("도구 사용 증거", execution.get("tool_use")),
                ]
            ),
        )
    )
    targets = []
    for raw in _items(provenance.get("targets")):
        row = _mapping(raw)
        indices = [
            index
            for index in _items(row.get("journal_event_indices"))
            if type(index) is int and index >= 0
        ]
        links = " · ".join(
            f'<a href="#journal-event-{index}">저널 항목 {index}</a>'
            for index in indices
        )
        targets.append(
            '<article class="record"><h3>'
            + _value(row.get("attempt_id"))
            + "</h3>"
            + _facts(
                [
                    ("저장소 경로", row.get("repository_root")),
                    ("저장소 식별값", row.get("repository_id")),
                    ("시작 HEAD", row.get("before_head")),
                    ("검사 대상 리비전", row.get("target_revision")),
                    ("후보 내용 SHA-256 · 저장된 식별값", row.get("candidate_sha256")),
                    ("계약 SHA-256", row.get("contract_sha256")),
                    ("문맥 SHA-256", row.get("context_sha256")),
                    ("호출자 기대값 비교 · 현재 검증 아님", row.get("comparison")),
                    (
                        "저장된 프로세스 상태 · 검사 결과와 별개",
                        row.get("native_process"),
                    ),
                    ("저장된 검사 결과", row.get("checker_result")),
                    ("이 시도에 속한 정확한 명령 해시와 결과", row.get("commands")),
                ]
            )
            + "<p>"
            + (links or "연결된 저널 항목 없음")
            + "</p></article>"
        )
    cards = []
    titles = {
        "current": "현재 상태 바이트 · current.json",
        "journal": "저널 바이트 · events.jsonl",
        "config": "설정 바이트",
        "outputs": "내용을 읽지 않은 출력 식별값",
    }
    for key, title in titles.items():
        card = next(
            (
                _mapping(raw)
                for raw in _items(provenance.get("evidence"))
                if _mapping(raw).get("id") == key
            ),
            {},
        )
        body = _facts(
            [
                ("출처", card.get("origin")),
                ("증거 상태", card.get("state")),
                ("경로 · 선택 가능한 텍스트", card.get("path")),
                ("SHA-256", card.get("sha256")),
                ("해시의 의미", card.get("digest_kind")),
                ("설정 연결 상태", card.get("binding_state")),
                ("저장된 출력 식별값 · 내용 미검증", card.get("identifiers")),
            ]
        )
        if key == "journal":
            for raw in _items(provenance.get("targets")):
                row = _mapping(raw)
                for index in _items(row.get("journal_event_indices")):
                    if type(index) is int and index >= 0:
                        body += f'<p id="journal-event-{index}">저널 항목 {index} · 시도 {_value(row.get("attempt_id"))} · 0부터 시작하는 위치, 위 저널 바이트에 속함</p>'
        cards.append(
            f'<article class="record" id="evidence-{key}"><h3>{title}</h3>{body}</article>'
        )
    navigation = (
        "<p>"
        + " · ".join(
            f'<a href="#evidence-{key}">{title}</a>' for key, title in titles.items()
        )
        + "</p>"
    )
    return (
        '<section class="panel" aria-labelledby="provenance-owner"><h2 id="provenance-owner">검증 주체와 실행 출처</h2>'
        + owner
        + '<p class="muted">백엔드 설정이나 정상 종료는 실제 모델 호출·도구 사용·검사 통과의 증명이 아닙니다. 외부 검사와 독립 검토는 이 보고서에 자동 수집되지 않습니다.</p></section>'
        + '<section class="panel" aria-labelledby="verification-target"><h2 id="verification-target">검증 대상</h2>'
        + ("".join(targets) or "<p>검증 대상을 연결할 유효한 기록 없음</p>")
        + '<p class="muted">시도별 결과를 합치지 않습니다. 경로·해시의 일치도 현재 작업 공간을 새로 검증하지 않습니다.</p></section>'
        + '<section class="panel" aria-labelledby="provenance-evidence"><h2 id="provenance-evidence">연결된 증거</h2>'
        + navigation
        + "".join(cards)
        + '<p class="muted">내부 링크는 이 페이지의 캡처 정보로 이동합니다. 파일 경로나 출력 URI를 열지 않습니다. 출력 식별값의 원본 내용은 읽거나 검증하지 않았습니다.</p></section>'
    )


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
    external_link = (
        '<a href="#external-checks">외부 검사 영수증</a>'
        if explanation.get("external_checks")
        else ""
    )
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
패키지 버전은 정확한 소스 식별값이 아닙니다.</p>
<nav aria-label="보고서 내부 탐색"><a href="#provenance-owner">검증 주체와 실행 출처</a><a href="#verification-target">검증 대상</a><a href="#provenance-evidence">연결된 증거</a>{external_link}</nav></header>
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
{_provenance_sections(explanation.get("provenance"))}
{_external_sections(explanation.get("external_checks"))}
<section aria-labelledby="evidence"><h2 id="evidence">상세 근거</h2>{sections}</section>
<footer>저장된 명령 결과는 기준의 의미적 증명이 아닙니다. 원본 JSON과 기록의 식별값을 함께 확인하세요.</footer>
</main></body></html>"""


def _external_counts(value: object) -> str:
    counts = _mapping(value)
    keys = ("tests", "failures", "errors", "skipped")
    if any(
        type(counts.get(key)) is not int or cast(int, counts[key]) < 0 for key in keys
    ):
        return "개수 확인 불가"
    total, failures, errors, skipped = (cast(int, counts[key]) for key in keys)
    passed = total - failures - errors - skipped
    if passed < 0:
        return "개수 확인 불가"
    return f"전체 {total} · 통과 {passed} · 실패 {failures} · 오류 {errors} · 건너뜀 {skipped}"


def _external_sections(value: object) -> str:
    rows = _items(value)
    cards = []
    for index, raw in enumerate(rows, 1):
        row = _mapping(raw)
        before = _mapping(row.get("before"))
        after = _mapping(row.get("after"))
        observation = _mapping(after.get("observation"))
        process = _mapping(row.get("process"))
        tests = _mapping(row.get("tests"))
        digest = after.get("candidate_sha256")
        short_identity = digest[:16] + "…" if isinstance(digest, str) else None
        summary = (
            f'<p class="muted">기록 주체: {_value(row.get("actor"))} · '
            f"기준: {_value(row.get('criterion'))}</p>"
            + '<div class="grid">'
            + _facts(
                [
                    ("전체 검사 결과", row.get("outcome")),
                    ("프로세스 · 테스트 결과와 별개", process.get("state")),
                    ("JUnit 테스트 결과", tests.get("state")),
                ]
            )
            + _facts(
                [
                    ("정규화한 테스트 개수", _external_counts(tests.get("counts"))),
                    ("검사 후 후보 · 전체 해시는 증거 펼침", short_identity),
                    ("대상 변경 여부", row.get("subject_state")),
                    (
                        "과거 대상 적용 관계 · 최신성 증명 아님",
                        row.get("historical_applicability"),
                    ),
                ]
            )
            + "</div>"
        )
        environment = _facts(
            [
                ("실제 작업 디렉터리", row.get("working_directory")),
                ("선택 실행 파일·SHA-256", row.get("executable")),
                (
                    "호출자가 제공한 안전한 명령 표시 · 정확한 인자 증명 아님",
                    row.get("caller_command_display"),
                ),
                (
                    "실제 실행 인자 배열의 SHA-256 · 원문 미저장",
                    row.get("command_sha256"),
                ),
                ("실제 시작 UTC", row.get("started_at")),
                ("실제 종료 UTC", row.get("ended_at")),
                ("단조 시계 경과 초", row.get("duration_seconds")),
                ("시간 제한 초", row.get("timeout_seconds")),
            ]
        )
        evidence = _facts(
            [
                ("출처 · Mercury 네이티브 실행과 별개", row.get("origin")),
                ("판독 상태", row.get("state")),
                ("검사 대상 저장소", observation.get("repository_root")),
                ("검사 후 HEAD · 내용 해시와 별개", observation.get("head")),
                ("검사 전 후보 내용 해시", before.get("candidate_sha256")),
                ("검사 후 후보 내용 해시", digest),
                ("보호 경로 변경 여부", row.get("protected_state")),
                ("프로세스 원본 정규화 값", process),
                ("JUnit 개수·파일 해시 원본 정규화 값", tests),
                ("검사기 소유 원본 결과 파일 · 경로만 표시", row.get("artifact_path")),
                (
                    "영수증 경로·바이트 SHA-256 · 링크를 따라 읽지 않음",
                    row.get("receipt"),
                ),
                ("판독 실패 사유", row.get("reason")),
                ("수집기 한계", row.get("limits")),
            ]
        )
        cards.append(
            f'<article class="panel"><h3>외부 검사 {index} · {_value(row.get("check_label"))}</h3>'
            + summary
            + _details("실행 환경·시간·명령", environment)
            + _details("대상 식별·결과 파일·증거", evidence)
            + "</article>"
        )
    body = "".join(cards) if rows else _facts([("수집 상태", "not_ingested")])
    return (
        '<section aria-labelledby="external-checks"><h2 id="external-checks">외부 검사 영수증</h2>'
        "<p>명시적으로 연결한 외부 명령 기록입니다. 기록과 해시는 작성자나 실행 권한을 인증하지 않습니다. "
        "과거 결론·검토 준비 상태를 승격하지 않으며 서로 다른 시도를 합치지 않습니다. "
        "JUnit 원본은 검사기 소유이며 비공개 테스트·실패 내용이 포함될 수 있습니다. 보고서는 정규화한 개수와 해시만 표시합니다.</p>"
        + body
        + "</section>"
    )
