"use client";

import { useState } from "react";
import { useStore } from "zustand";
import { ArrowLeft, ArrowRight, Check, FileText, FlaskConical, RotateCcw, ShieldCheck, Terminal, CircleHelp, X } from "lucide-react";
import { createDemoStore, getVisibleResult, scenarios, steps, type ScenarioId } from "@/lib/demo";

const stepDescriptions = [
  "사람이 작업, 허용 파일, 보호할 검사와 완료 기준을 먼저 정합니다.",
  "선택한 에이전트가 코드를 바꿉니다. 변경했다는 사실만으로 완료가 되지는 않습니다.",
  "Mercury는 실제 바뀐 경로와 보호 입력을 관찰합니다. 관찰은 운영체제 샌드박스를 뜻하지 않습니다.",
  "사람이 선택한 명령의 관측 결과를 모읍니다. 검사의 의미와 충분성은 사람이 결정합니다.",
  "변경 범위와 검사 근거를 함께 읽습니다. 기술 결과는 리뷰 승인이나 머지 승인이 아닙니다.",
];
const stepIcons = [FileText, Terminal, ShieldCheck, FlaskConical, FileText];

export function Demo() {
  const [store] = useState(createDemoStore);
  const state = useStore(store);
  const scenario = scenarios[state.scenario];
  const result = getVisibleResult(state);
  const Icon = stepIcons[state.step];

  return (
    <section className="demo-section" id="demo" aria-label="클릭형 실행 데모">
      <div className="section-heading">
        <div><h2>완료라는 말 다음에,<br />무엇을 확인할까요?</h2><p>같은 작업, 다른 근거. 세 가지 결과를 직접 살펴보세요.</p></div>
        <span className="demo-label"><FlaskConical size={16} aria-hidden="true" /> 클릭형 데모</span>
      </div>
      <div className="demo-notice"><span className="notice-dot" /><p><strong>브라우저 안에서만 동작하는 합성 데이터입니다.</strong><span> 실제 파일·에이전트·검사 명령을 실행하지 않습니다.</span></p></div>
      <div className="scenario-picker" role="group" aria-label="데모 시나리오">
        {(Object.keys(scenarios) as ScenarioId[]).map((id) => <button key={id} aria-pressed={state.scenario === id} onClick={() => state.selectScenario(id)}>{scenarios[id].label}</button>)}
      </div>
      <div className="demo-workspace">
        <div className="demo-steps">
          <p className="workspace-label">하나의 변경을 따라가기</p>
          <ol>{steps.map((step, i) => <li key={step} aria-current={i === state.step ? "step" : undefined} className={i < state.step ? "completed" : ""}><span className="step-marker">{i < state.step ? <Check size={16} aria-hidden="true" /> : i + 1}</span><span>{step}</span>{i === state.step && <span className="current-label">현재</span>}</li>)}</ol>
          <p className="step-hint">작업의 시작부터 근거 확인까지<br />다음 단계로 이동해 보세요.</p>
        </div>
        <div className="demo-detail" aria-live="polite" aria-atomic="true">
          <div className="detail-topline"><span><Icon size={18} aria-hidden="true" /> {steps[state.step]}</span><span>{state.step + 1} / 5</span></div>
          <h3>{["무엇을, 어디까지 바꿀지 정합니다.", "에이전트의 변경이 생겼습니다.", "바뀐 경로를 확인합니다.", "검사 결과를 실제로 확인합니다.", "관측된 근거로 결과를 읽습니다."][state.step]}</h3>
          <p className="detail-description">{stepDescriptions[state.step]}</p>
          <div className="evidence-panel">
            {state.step === 0 && <><div className="file-caption"><FileText size={16} aria-hidden="true" /> 합성 작업 계약</div><dl className="contract-list"><div><dt>작업</dt><dd>value.txt의 값을 before에서 after로 변경</dd></div><div><dt>허용 파일</dt><dd><code>value.txt</code></dd></div><div><dt>보호 입력</dt><dd><code>check.py</code></dd></div><div><dt>완료 기준</dt><dd>변경 범위 일치 + 선택한 검사 통과</dd></div></dl></>}
            {state.step === 1 && <><div className="file-caption"><FileText size={16} aria-hidden="true" /> value.txt · 합성 변경</div><pre className="diff"><span className="removed">− before</span>{"\n"}<span className="added">+ {scenario.value}</span></pre><p className="panel-note">변경된 내용이 기대와 일치하는지는 다음 근거로 확인합니다.</p></>}
            {state.step === 2 && <><div className="file-caption"><ShieldCheck size={16} aria-hidden="true" /> 변경 경로 관찰 · 합성 기록</div><ul className="observation-list"><li><Check size={17} aria-hidden="true" /> <code>value.txt</code><span>허용 범위 안의 변경</span></li><li><Check size={17} aria-hidden="true" /> <code>check.py</code><span>보호 입력 변경 없음</span></li></ul><p className="panel-note">이 예시의 관찰 결과입니다. 모든 파일 접근을 차단하는 기능은 아닙니다.</p></>}
            {state.step === 3 && <><div className="file-caption"><Terminal size={16} aria-hidden="true" /> 선택한 검사 · 합성 출력</div><pre className="check-output">$ python check.py{"\n\n"}{scenario.check}</pre><p className="panel-note">화면의 명령과 출력은 설명용 데이터이며 실행되지 않습니다.</p></>}
            {state.step === 4 && <div className={`result-content result-${state.scenario}`}><div className="result-icon">{result === "PASS" ? <Check aria-hidden="true" /> : result === "FAIL" ? <X aria-hidden="true" /> : <CircleHelp aria-hidden="true" />}</div><span className="result-value" data-testid="technical-result">{result}</span><p>{scenario.conclusion}</p><span className="result-footnote">합성 기술 결과 · 리뷰 및 머지 승인과 별개</span></div>}
          </div>
        </div>
      </div>
      <div className="demo-controls"><button className="reset-button" onClick={state.reset}><RotateCcw size={16} aria-hidden="true" /> 처음부터</button><div><button className="secondary-button" onClick={state.previous} disabled={state.step === 0}><ArrowLeft size={16} aria-hidden="true" /> 이전 단계</button><button className="primary-button" onClick={state.next} disabled={state.step === 4}>다음 단계 <ArrowRight size={16} aria-hidden="true" /></button></div></div>
    </section>
  );
}
