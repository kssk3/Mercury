import { createStore } from "zustand/vanilla";

import type { DemoCheck, DemoReceipt, ScenarioId } from "./demo-evidence";
export type { ScenarioId } from "./demo-evidence";
export type TechnicalResult = "PASS" | "FAIL" | "UNKNOWN" | "OUT OF SCOPE";
export const steps = ["작업과 범위", "에이전트 변경", "변경 범위 관찰", "선택한 검사", "근거 확인"] as const;
export const scenarios: Record<ScenarioId, { label: string; summary: string; result: TechnicalResult }> = {
  correct: { label: "정상 수정", summary: "할인과 배송비 검사 통과", result: "PASS" },
  regression: { label: "회귀 발생", summary: "음수는 고쳤지만 배송비가 3,000원 늘어남", result: "FAIL" },
  scope_escape: { label: "범위 이탈", summary: "승인하지 않은 배송비 정책 변경", result: "OUT OF SCOPE" },
  incomplete: { label: "검증 불완전", summary: "개발 검사 이후 최종 검증 직전 중단", result: "UNKNOWN" },
};

export function checkLabel(check: DemoCheck): string {
  if (check.availability === "not_run") return "NOT RUN";
  if (check.availability !== "observed" || check.exitCode === null || check.timedOut !== false) return "UNKNOWN";
  return check.exitCode === 0 ? "PASS" : "FAIL";
}

export function receiptOutcome(receipt: DemoReceipt): TechnicalResult {
  if (receipt.scope.availability === "observed" && (receipt.scope.proceed === false || receipt.scope.violations.length > 0)) return "OUT OF SCOPE";
  const checks = receipt.checks.filter(c => c.phase !== "baseline");
  if (checks.some(c => checkLabel(c) === "FAIL")) return "FAIL";
  const complete = ["development", "final"].every(phase => {
    const rows = checks.filter(c => c.phase === phase);
    return rows.length === 2 && rows.every(c => checkLabel(c) === "PASS");
  });
  if (receipt.recordedStatus === "pass" && receipt.scope.availability === "observed" && receipt.scope.proceed === true && complete) return "PASS";
  return "UNKNOWN";
}

export function summarizeReceipt(receipt: DemoReceipt): { result: TechnicalResult; conclusion: string; evidence: string[]; nextDecision: string } {
  const result = receiptOutcome(receipt);
  const checks = receipt.checks.filter(c => c.phase !== "baseline");
  const phaseCount = (phase: "development" | "final") => {
    const rows = checks.filter(c => c.phase === phase);
    return `${rows.filter(c => checkLabel(c) === "PASS").length}/${rows.length} 통과`;
  };
  const notRun = checks.filter(c => checkLabel(c) === "NOT RUN").length;
  const unknown = checks.filter(c => checkLabel(c) === "UNKNOWN").length;
  const evidence = [`개발 검사 ${phaseCount("development")} · 최종 검사 ${phaseCount("final")}${notRun ? ` · 미실행 ${notRun}개` : ""}${unknown ? ` · 확인 불가 ${unknown}개` : ""}`];
  const failed = checks.filter(c => checkLabel(c) === "FAIL");
  const mismatch = failed.flatMap(c => (c.cases ?? []).filter(row => row.expected !== row.actual).map(row => ({ commandId: c.commandId, ...row })))[0];
  if (mismatch) evidence.unshift(`${mismatch.commandId === "shipping" ? "배송비" : "할인"} 검사: 예상 ${mismatch.expected.toLocaleString("ko-KR")}원 → 실제 ${mismatch.actual.toLocaleString("ko-KR")}원`);
  else if (failed.length) evidence.unshift(`${[...new Set(failed.map(c => c.commandId === "shipping" ? "배송비" : "할인"))].join("·")} 검사 실패`);
  const scope = receipt.scope;
  evidence.push(scope.availability !== "observed" ? "변경 범위 확인 불가" : scope.violations.length ? `범위·보호 위반: ${scope.violations.join(", ")}` : scope.proceed === true ? "관측된 범위·보호 위반 없음" : "범위 관찰에서 진행 허용을 확인하지 못함");
  if (result === "PASS") return { result, evidence, conclusion: "선택한 검사가 수정 결과를 뒷받침합니다.", nextDecision: "변경 코드와 검사 충분성을 사람이 검토한 뒤 승인 여부를 판단하세요." };
  if (result === "FAIL") return { result, evidence, conclusion: "수정 후 검사에서 회귀가 발견됐습니다.", nextDecision: failed.some(c => c.commandId === "shipping") ? "기존 배송비 정책을 유지하도록 수정을 검토하고, 필요한 검사를 다시 확인하세요." : "실패한 검사의 기대값과 수정 코드를 검토하고, 필요한 검사를 다시 확인하세요." };
  if (result === "OUT OF SCOPE") return { result, evidence, conclusion: "승인한 변경 범위를 벗어났습니다.", nextDecision: "보호 파일 변경과 범위 관찰을 검토하고, 원래 범위로 수정할지 별도 승인을 받을지 판단하세요." };
  return { result, evidence, conclusion: "아직 완료로 판단할 수 없습니다.", nextDecision: "미실행·확인 불가 근거와 중단 상태를 검토하고, 승인된 절차에 따라 최종 검증 근거를 확인하세요." };
}

export interface DemoState {
  scenario: ScenarioId;
  step: number;
  next: () => void;
  previous: () => void;
  reset: () => void;
  selectScenario: (scenario: ScenarioId) => void;
}

export const createDemoStore = () => createStore<DemoState>((set) => ({
  scenario: "correct",
  step: 0,
  next: () => set((state) => ({ step: Math.min(state.step + 1, steps.length - 1) })),
  previous: () => set((state) => ({ step: Math.max(state.step - 1, 0) })),
  reset: () => set({ step: 0 }),
  selectScenario: (scenario) => set({ scenario, step: 0 }),
}));

export function getVisibleResult(state: Pick<DemoState, "step" | "scenario">): TechnicalResult | null {
  return state.step === steps.length - 1 ? scenarios[state.scenario].result : null;
}
