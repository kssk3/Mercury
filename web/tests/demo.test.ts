import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { parseDemoReceipt } from "../src/lib/demo-evidence";
import { checkLabel, createDemoStore, getVisibleResult, scenarioFromHash, summarizeReceipt } from "../src/lib/demo";

// These tests catch out-of-bounds navigation, stale result leakage, and shared instances.
test("starts at the first step with no premature technical result", () => {
  const store = createDemoStore();
  assert.equal(store.getState().step, 0);
  assert.equal(store.getState().scenario, "correct");
  assert.equal(getVisibleResult(store.getState()), null);
});
test("previous and next stop at the first and fifth steps", () => {
  const store = createDemoStore();
  store.getState().previous();
  assert.equal(store.getState().step, 0);
  for (let i = 0; i < 8; i++) store.getState().next();
  assert.equal(store.getState().step, 4);
  assert.equal(getVisibleResult(store.getState()), "PASS");
});
test("changing scenario resets progress and hides a previous success", () => {
  const store = createDemoStore();
  for (let i = 0; i < 4; i++) store.getState().next();
  store.getState().selectScenario("regression");
  assert.equal(store.getState().step, 0);
  assert.equal(getVisibleResult(store.getState()), null);
  for (let i = 0; i < 4; i++) store.getState().next();
  assert.equal(getVisibleResult(store.getState()), "FAIL");
});
test("missing terminal evidence stays unknown rather than becoming success", () => {
  const store = createDemoStore();
  store.getState().selectScenario("incomplete");
  for (let i = 0; i < 4; i++) store.getState().next();
  assert.equal(getVisibleResult(store.getState()), "UNKNOWN");
  store.getState().previous();
  assert.equal(getVisibleResult(store.getState()), null);
});
test("reset retains the chosen scenario but returns to the beginning", () => {
  const store = createDemoStore();
  store.getState().selectScenario("regression");
  store.getState().next();
  store.getState().reset();
  assert.equal(store.getState().scenario, "regression");
  assert.equal(store.getState().step, 0);
});
test("each demo has an independent initial state", () => {
  const first = createDemoStore();
  const second = createDemoStore();
  first.getState().selectScenario("incomplete");
  first.getState().next();
  assert.equal(second.getState().scenario, "correct");
  assert.equal(second.getState().step, 0);
});

// Contract: shared fragment links select only explicit demo scenarios.
test("scenario fragments are allowlisted and leave ordinary anchors alone", () => {
  assert.equal(typeof scenarioFromHash, "function");
  for (const [hash, scenario] of [["#demo-correct", "correct"], ["#demo-regression", "regression"], ["#demo-scope-escape", "scope_escape"], ["#demo-incomplete", "incomplete"]]) {
    assert.equal(scenarioFromHash(hash), scenario);
  }
  for (const hash of ["", "#demo", "#how-it-works", "#demo-unknown", "#demo-__proto__", "#demo-%72egression"]) {
    assert.equal(scenarioFromHash(hash), null);
  }
});

// Contract: missing execution must not read like failed checks, and actions follow observed outcomes.
const receiptFor = (id: string) => parseDemoReceipt(JSON.parse(readFileSync(new URL(`../public/demo/cart/${id}.json`, import.meta.url), "utf8")));
test("phase evidence distinguishes observed failure, unrun checks and unknown results", () => {
  const incomplete = summarizeReceipt(receiptFor("incomplete"));
  assert.match(incomplete.evidence.join(" "), /최종 검사: 미실행 2개/);
  assert.doesNotMatch(incomplete.evidence.join(" "), /최종 검사 0\/2 통과|실패/);
  const regression = summarizeReceipt(receiptFor("regression"));
  assert.match(regression.evidence.join(" "), /개발 검사: 통과 1개 · 실패 1개/);
  assert.match(regression.evidence.join(" "), /최종 검사: 미실행 2개/);
  const mixed = receiptFor("incomplete");
  Object.assign(mixed.checks.find(c => c.phase === "final")!, { availability: "observed", exitCode: 1, timedOut: false });
  assert.match(summarizeReceipt(mixed).evidence.join(" "), /최종 검사: 실패 1개 · 미실행 1개/);
  for (const [exitCode, timedOut] of [[0, true], [null, false], [0, null]] as const) {
    const unknown = receiptFor("correct");
    Object.assign(unknown.checks.find(c => c.phase === "final")!, { exitCode, timedOut });
    assert.equal(checkLabel(unknown.checks.find(c => c.phase === "final")!), "UNKNOWN");
    assert.equal(summarizeReceipt(unknown).result, "UNKNOWN");
    assert.match(summarizeReceipt(unknown).evidence.join(" "), /최종 검사: 통과 1개 · 확인 불가 1개/);
  }
  const missing = receiptFor("correct");
  missing.checks = missing.checks.filter(c => c.phase !== "final");
  assert.equal(summarizeReceipt(missing).result, "UNKNOWN");
  assert.match(summarizeReceipt(missing).evidence.join(" "), /최종 검사: 기록 없음 · 확인 불가/);
});
test("primary evidence follows receipt facts even when scenario names differ", () => {
  for (const [id, panel, label] of [["correct", "Checks", "통과한 검사 보기"], ["regression", "Checks", "실패한 검사 보기"], ["scope_escape", "Scope", "승인 범위와 변경 파일 비교하기"], ["incomplete", "Checks", "미실행·확인 불가 검사 보기"]]) {
    assert.deepEqual(summarizeReceipt(receiptFor(id)).evidenceAction, { panel, label });
  }
  for (const reason of ["scope", "recordedStatus"]) {
    const uncertain = receiptFor("correct");
    if (reason === "scope") uncertain.scope = { ...uncertain.scope, availability: "unavailable", proceed: null, changed: [], violations: [] };
    else uncertain.recordedStatus = "unknown";
    assert.equal(summarizeReceipt(uncertain).result, "UNKNOWN");
    assert.equal(summarizeReceipt(uncertain).evidenceAction.label, "검사 실행 상태 확인하기");
  }
  const renamed = receiptFor("scope_escape"); renamed.scenarioId = "correct";
  assert.equal(summarizeReceipt(renamed).evidenceAction.panel, "Scope");
});
