import { createStore } from "zustand/vanilla";

import type { ScenarioId } from "./demo-evidence";
export type { ScenarioId } from "./demo-evidence";
export type TechnicalResult = "PASS" | "FAIL" | "UNKNOWN" | "OUT OF SCOPE";
export const steps = ["작업과 범위", "에이전트 변경", "변경 범위 관찰", "선택한 검사", "근거 확인"] as const;
export const scenarios: Record<ScenarioId, { label: string; summary: string; result: TechnicalResult; conclusion: string }> = {
  correct: { label: "정상 수정", summary: "할인과 배송비 검사 통과", result: "PASS", conclusion: "요구한 할인 수정과 기존 배송비 동작을 검사가 뒷받침합니다. 변경 내용과 검사 충분성은 사람이 검토합니다." },
  regression: { label: "회귀 발생", summary: "음수는 고쳤지만 배송비가 3,000원 늘어남", result: "FAIL", conclusion: "할인 검사는 통과했지만 기존 배송비 검사가 실패했습니다. Mercury는 재시도 없이 사람의 판단을 요청한 상태입니다." },
  scope_escape: { label: "범위 이탈", summary: "승인하지 않은 배송비 정책 변경", result: "OUT OF SCOPE", conclusion: "보호 대상 shipping.py 변경을 관찰하여 개발·최종 검사를 진행하지 않았습니다. 정책 변경은 별도 승인이 필요합니다." },
  incomplete: { label: "검증 불완전", summary: "개발 검사 이후 최종 검증 직전 중단", result: "UNKNOWN", conclusion: "개발 검사가 통과했어도 최종 검사는 실행되지 않았습니다. 중단된 실행을 완료로 판단할 수 없습니다." },
};

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
