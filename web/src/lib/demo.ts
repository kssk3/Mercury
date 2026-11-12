import { createStore } from "zustand/vanilla";

export type ScenarioId = "success" | "failure" | "unknown";
export type TechnicalResult = "PASS" | "FAIL" | "UNKNOWN";
export const steps = ["작업과 범위", "에이전트 변경", "변경 범위 관찰", "선택한 검사", "근거 확인"] as const;

export const scenarios: Record<ScenarioId, { label: string; summary: string; result: TechnicalResult; value: string; check: string; conclusion: string }> = {
  success: {
    label: "검사 통과", summary: "선택한 검사가 모두 통과한 경우", result: "PASS", value: "after",
    check: "개발 검사: exit 0\n별도 최종 검사: exit 0\n보호된 check.py: 변경 없음",
    conclusion: "허용된 파일의 변경과 선택한 검사의 통과 근거가 있습니다. 이 근거를 바탕으로 사람이 변경 내용을 검토합니다.",
  },
  failure: {
    label: "검사 실패", summary: "변경은 있었지만 검사에서 실패한 경우", result: "FAIL", value: "aftr",
    check: "개발 검사: exit 1\nexpected 'after', got 'aftr'\n완료 기준: 충족하지 못함",
    conclusion: "에이전트가 작업을 마쳤다고 해도 선택한 검사에 실패하면 기술 결과는 실패입니다. 실패 근거를 확인하고 수정 여부를 결정합니다.",
  },
  unknown: {
    label: "확인 불가", summary: "최종 검사 근거가 완전하지 않은 경우", result: "UNKNOWN", value: "after",
    check: "개발 검사: exit 0\n별도 최종 검사: 종료 근거 없음\n관측 기록: 불완전",
    conclusion: "개발 검사가 통과했어도 최종 검사의 종료 근거가 없으면 성공으로 판단하지 않습니다. 불완전한 근거는 확인 불가로 남깁니다.",
  },
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
  scenario: "success",
  step: 0,
  next: () => set((state) => ({ step: Math.min(state.step + 1, steps.length - 1) })),
  previous: () => set((state) => ({ step: Math.max(state.step - 1, 0) })),
  reset: () => set({ step: 0 }),
  selectScenario: (scenario) => set({ scenario, step: 0 }),
}));

export function getVisibleResult(state: Pick<DemoState, "step" | "scenario">): TechnicalResult | null {
  return state.step === steps.length - 1 ? scenarios[state.scenario].result : null;
}
