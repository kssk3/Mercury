"use client";

import { useState } from "react";
import { useStore } from "zustand";
import { ArrowLeft, ArrowRight, Check, FileText, FlaskConical, RotateCcw } from "lucide-react";
import { createDemoStore, scenarios, steps, type ScenarioId } from "@/lib/demo";
import type { DemoCheck, DemoReceipt } from "@/lib/demo-evidence";

const panels = ["Diff", "Checks", "Scope", "Evidence"] as const;
const descriptions = [
  "음수 할인 금액을 고치되, 할인 전 합계로 배송비를 정하는 기존 정책은 유지합니다.",
  "정해진 후보 변경을 시뮬레이션으로 적용했습니다. 실제 AI 모델은 호출하지 않았습니다.",
  "Mercury가 허용 경로와 보호 입력의 변경을 관찰했습니다. OS 샌드박스나 자동 롤백은 아닙니다.",
  "보호된 두 검사 명령의 실제 관측 결과입니다. 실행되지 않은 단계는 NOT RUN으로 남습니다.",
  "실행 상태, 검사, 범위와 남은 불확실성을 함께 검토합니다. 기술 결과는 승인과 별개입니다.",
];
const phaseNames = { baseline: "기준선", development: "개발", final: "최종" };
function checkLabel(check: DemoCheck): string {
  if (check.availability === "not_run") return "NOT RUN";
  if (check.availability === "unavailable" || check.exitCode === null || check.timedOut) return "UNKNOWN";
  return check.exitCode === 0 ? "PASS" : "FAIL";
}
function outcome(receipt: DemoReceipt): string {
  if (receipt.scope.availability === "observed" && receipt.scope.proceed === false) return "OUT OF SCOPE";
  if (receipt.checks.some(c => c.phase !== "baseline" && checkLabel(c) === "FAIL")) return "FAIL";
  if (receipt.recordedStatus === "pass" && receipt.checks.filter(c => c.phase === "final").every(c => checkLabel(c) === "PASS")) return "PASS";
  return "UNKNOWN";
}

export function Demo({ receipts }: { receipts: Record<ScenarioId, DemoReceipt> }) {
  const [store] = useState(createDemoStore);
  const state = useStore(store);
  const [panel, setPanel] = useState<typeof panels[number]>("Diff");
  const scenario = scenarios[state.scenario];
  const receipt = receipts[state.scenario];
  const result = outcome(receipt);
  const basePath = process.env.NEXT_PUBLIC_MERCURY_BASE_PATH ?? "";
  const changedFiles = receipt.diff.afterFiles.filter((file, i) => file.content !== receipt.diff.beforeFiles[i].content);
  return <section className="demo-section" id="demo" aria-label="클릭형 실행 데모">
    <div className="section-heading"><div><h2>같은 할인 버그,<br />네 가지 다른 판단.</h2><p>수정 코드부터 실제 검사와 실행 근거까지 비교하세요.</p></div><span className="demo-label"><FlaskConical size={16} aria-hidden="true" /> 실행 근거 탐색</span></div>
    <div className="demo-notice"><span className="notice-dot" /><p><strong>Mercury 실제 실행 · 수정은 시뮬레이션</strong><span> 저장된 실행 기록을 탐색합니다. 브라우저에서 명령이나 AI를 실행하지 않습니다.</span></p></div>
    <div className="cart-task"><div><span className="workspace-label">작업 요청</span><h3>쿠폰이 상품 금액보다 커도 결제 금액이 음수가 되지 않게 수정해주세요.</h3><p>할인 전 합계 50,000원 이상 무료 배송, 그 외 3,000원. 배송비 정책은 그대로 유지합니다.</p></div><dl><div><dt>수정 허용</dt><dd><code>cart.py</code></dd></div><div><dt>보호 대상</dt><dd><code>shipping.py</code> · 두 검사 파일</dd></div><div><dt>핵심 기대값</dt><dd>10,000 − 15,000 + 배송비 → <strong>3,000원</strong><br />60,000 − 20,000 + 무료 배송 → <strong>40,000원</strong></dd></div></dl></div>
    <div className="cart-scenarios" role="group" aria-label="데모 시나리오">{(Object.keys(scenarios) as ScenarioId[]).map(id => <button key={id} aria-pressed={state.scenario === id} onClick={() => state.selectScenario(id)}><strong>{scenarios[id].label}</strong><span>{scenarios[id].summary}</span><code>{outcome(receipts[id])}</code></button>)}</div>
    <div className="cart-inspector">
      <div className="cart-main"><div className="evidence-tabs" role="group" aria-label="근거 보기">{panels.map(name => <button key={name} aria-pressed={panel === name} onClick={() => setPanel(name)}>{name}</button>)}</div>
        <div className="cart-panel" aria-label={`${panel} 근거`}>
          {panel === "Diff" && <><h3>실제로 바뀐 코드</h3><p className="panel-intro">실행 전·후 스냅샷 비교입니다. Mercury의 범위 관찰은 Scope에서 별도로 확인합니다.</p>{changedFiles.map(file => <article className="cart-file" key={file.path}><h4><FileText size={16} aria-hidden="true" /> {file.path}</h4><div className="cart-code-pair"><div><span>변경 전</span><pre className="removed"><code>{receipt.diff.beforeFiles.find(f => f.path === file.path)?.content}</code></pre></div><div><span>변경 후</span><pre className="added"><code>{file.content}</code></pre></div></div></article>)}</>}
          {panel === "Checks" && <><h3>할인은 고쳤어도 배송비는?</h3><p className="panel-intro">기준선 할인 실패는 알려진 버그입니다. 개발·최종 단계에서 두 검사가 어떻게 달라졌는지 보세요.</p><div className="table-scroll"><table className="cart-checks"><caption className="sr-only">실제 실행된 검사 결과</caption><thead><tr><th>단계</th><th>명령</th><th>관측 결과</th><th>exit</th></tr></thead><tbody>{receipt.checks.map(c => <tr key={`${c.phase}/${c.commandId}`}><td>{phaseNames[c.phase]}</td><td><code>python -B check_{c.commandId}.py</code></td><td><strong className={`check-${checkLabel(c).toLowerCase().replace(" ", "-")}`}>{checkLabel(c)}</strong></td><td>{c.exitCode ?? "—"}</td></tr>)}</tbody></table></div><div className="case-results">{receipt.checks.filter(c => c.phase === "development" && c.cases !== null).map(c => <div key={c.commandId}><h4>{c.commandId === "discount" ? "할인" : "배송비"} 검사에서 관측한 값</h4>{c.cases?.map(row => <p key={`${row.subtotal}/${row.coupon}`}>상품 {row.subtotal.toLocaleString("ko-KR")}원 · 쿠폰 {row.coupon.toLocaleString("ko-KR")}원<br /><span>예상 {row.expected.toLocaleString("ko-KR")}원 → 실제 <strong>{row.actual.toLocaleString("ko-KR")}원</strong></span></p>)}</div>)}</div></>}
          {panel === "Scope" && <><h3>승인한 변경 안에 있나요?</h3><dl className="cart-facts"><div><dt>관찰 상태</dt><dd>{receipt.scope.availability}</dd></div><div><dt>허용 파일</dt><dd>{receipt.scope.allowed.join(", ")}</dd></div><div><dt>보호 입력</dt><dd>{receipt.scope.protected.join(", ")}</dd></div><div><dt>실제 변경</dt><dd>{receipt.scope.availability === "observed" ? receipt.scope.changed.join(", ") || "없음" : "확인 불가"}</dd></div><div><dt>범위·보호 위반</dt><dd>{receipt.scope.availability === "observed" ? receipt.scope.violations.join(", ") || "관측된 위반 없음" : "확인 불가"}</dd></div><div><dt>진행 허용 관찰</dt><dd>{receipt.scope.proceed === null ? "확인 불가" : String(receipt.scope.proceed)}</dd></div></dl><p className="panel-note">위반을 관찰하면 후속 검사가 실행되지 않을 수 있습니다. 실행 차단은 OS 수준 격리를 의미하지 않습니다.</p></>}
          {panel === "Evidence" && <><h3>이 판단은 어떤 기록에서 왔나요?</h3><dl className="cart-facts"><div><dt>출처</dt><dd>{receipt.sourceMode === "recorded_simulated" ? "Mercury 실제 실행 · 수정은 시뮬레이션" : "네이티브 모델 실행"}</dd></div><div><dt>실행 ID</dt><dd data-testid="run-id">{receipt.runId}</dd></div><div><dt>기록 시각</dt><dd>{receipt.capturedAt}</dd></div><div><dt>Mercury 코어 기준</dt><dd><code>{receipt.mercuryRevision}</code></dd></div><div><dt>Fixture SHA-256</dt><dd><code>{receipt.fixtureSha256}</code></dd></div><div><dt>Candidate SHA-256</dt><dd><code>{receipt.candidateSha256}</code></dd></div><div><dt>기록 진단</dt><dd>{receipt.diagnostics.join(", ") || "진단 없음"}</dd></div></dl><p className="panel-note">코어 기준 SHA는 실행한 Mercury의 Git 기준입니다. 예제 변경은 미커밋 상태일 수 있습니다. 해시는 내용 일치를 확인하며 실행의 진위나 현재 작업 폴더를 증명하지 않습니다.</p><p className="panel-note">체커 값은 실제 executor 캡처, 범위와 상태는 Mercury 기록 조회에서 왔습니다. 실행 설정 바인딩과 리뷰 승인은 제공하지 않습니다.</p><a className="text-link" href={`${basePath}/demo/cart/${state.scenario}.json`} download>공개 실행 근거 JSON 다운로드 <ArrowRight size={16} aria-hidden="true" /></a><p className="panel-note"><a href="https://github.com/kssk3/Mercury/blob/main/docs/quickstart.md#recorded-cart-scenarios">동일한 예제 재현 방법</a> · <a href="https://github.com/kssk3/Mercury/blob/main/docs/codex-quickstart.md">별도 실제 Codex 할인 예제</a></p></>}
        </div>
      </div>
      <aside className="cart-verdict" aria-label="선택한 시나리오의 판단"><span className="workspace-label">{scenario.label}</span><strong className="cart-outcome" data-testid="evidence-outcome">{result}</strong><p>{scenario.conclusion}</p><dl><div><dt>기록된 실행 상태</dt><dd data-testid="recorded-status">{receipt.recordedStatus}</dd></div><div><dt>기록된 이유</dt><dd>{receipt.recordedReason ?? "확인 불가"}</dd></div></dl><p className="verdict-note">기술 결과 ≠ 리뷰·머지 승인</p></aside>
    </div>
    <div className="cart-journey"><ol>{steps.map((step, i) => <li key={step} aria-current={i === state.step ? "step" : undefined}><span>{i < state.step ? <Check size={14} aria-hidden="true" /> : i + 1}</span>{step}</li>)}</ol><div aria-live="polite"><strong>{state.step + 1} / 5</strong><p>{descriptions[state.step]}</p>{state.step === 4 && <span data-testid="technical-result">{result}</span>}</div></div>
    <div className="demo-controls"><button className="reset-button" onClick={state.reset}><RotateCcw size={16} aria-hidden="true" /> 처음부터</button><div><button className="secondary-button" onClick={state.previous} disabled={state.step === 0}><ArrowLeft size={16} aria-hidden="true" /> 이전 단계</button><button className="primary-button" onClick={state.next} disabled={state.step === 4}>다음 단계 <ArrowRight size={16} aria-hidden="true" /></button></div></div>
  </section>;
}
