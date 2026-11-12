import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { parseDemoReceipt, snapshotBytes, type DemoReceipt } from "../src/lib/demo-evidence";
import * as demo from "../src/lib/demo";

const load = (id: string): unknown => JSON.parse(readFileSync(new URL(`../public/demo/cart/${id}.json`, import.meta.url), "utf8"));
const hash = (value: string) => createHash("sha256").update(value).digest("hex");
test("four recorded receipts bind exact before and after snapshots", () => {
  for (const id of ["correct", "regression", "scope_escape", "incomplete"]) {
    const receipt = parseDemoReceipt(load(id));
    assert.equal(receipt.scenarioId, id);
    assert.equal(receipt.sourceMode, "recorded_simulated");
    assert.equal(hash(snapshotBytes(receipt.diff.beforeFiles)), receipt.fixtureSha256);
    assert.equal(hash(snapshotBytes(receipt.diff.afterFiles)), receipt.candidateSha256);
  }
});
test("incomplete final and scope-stop development never become successful checks", () => {
  const interrupted = parseDemoReceipt(load("incomplete"));
  assert.equal(interrupted.recordedStatus, "unknown");
  assert.equal(interrupted.recordedReason, "interrupted");
  assert(interrupted.checks.filter(r => r.phase === "final").every(r => r.availability === "not_run" && r.exitCode === null));
  const scope = parseDemoReceipt(load("scope_escape"));
  assert.equal(scope.scope.proceed, false);
  assert(scope.checks.filter(r => r.phase !== "baseline").every(r => r.availability === "not_run"));
});
test("schema rejects unsafe paths, unknown fields, unsupported mode and invented final PASS", () => {
  const original = load("incomplete") as DemoReceipt;
  for (const change of [
    (r: DemoReceipt) => { r.schemaVersion = 2 as 1; },
    (r: DemoReceipt) => { Object.assign(r, { unexpected: "private" }); },
    (r: DemoReceipt) => { r.sourceMode = "invented" as DemoReceipt["sourceMode"]; },
    (r: DemoReceipt) => { r.diff.afterFiles[0].path = "/private/path"; },
    (r: DemoReceipt) => { r.checks[4].exitCode = 0; },
  ]) {
    const value = structuredClone(original); change(value);
    assert.throws(() => parseDemoReceipt(value));
  }
});
test("Python and Node share a literal UTF-8 compact snapshot serialization", () => {
  assert.equal(snapshotBytes([{ path: "a.py", content: "한글\n" }]), '[["a.py","한글\\n"]]');
  assert.equal(hash(snapshotBytes([{ path: "a.py", content: "한글\n" }])), "5b61a6f73dd261cea2b459c9cf15e668c186303829a36dae531dca63845f682a");
});
test("observed final results require an explicit timeout boolean", () => {
  const receipt = load("correct") as DemoReceipt;
  const final = receipt.checks.find(row => row.phase === "final" && row.availability === "observed")!;
  assert.equal(final.exitCode, 0);
  final.timedOut = null;
  assert.throws(() => parseDemoReceipt(receipt));
});

// Contract: summary conclusions and next decisions follow observed receipt facts.
// Exact layout is covered by browser acceptance, not these projection tests.
test("result summaries explain four recorded outcomes using their observed evidence", () => {
  assert.equal(typeof demo.summarizeReceipt, "function", "receipt summary projection must exist");
  const correct = demo.summarizeReceipt(parseDemoReceipt(load("correct")));
  assert.equal(correct.result, "PASS");
  assert.match(correct.conclusion, /검사/);
  assert.match(correct.evidence.join(" "), /개발 검사 2\/2 통과.*최종 검사 2\/2 통과/);
  assert.match(correct.nextDecision, /사람.*검토/);
  const regression = demo.summarizeReceipt(parseDemoReceipt(load("regression")));
  assert.equal(regression.result, "FAIL");
  assert.match(regression.evidence.join(" "), /예상 40,000원 → 실제 43,000원/);
  assert.match(regression.nextDecision, /배송비/);
  const scope = demo.summarizeReceipt(parseDemoReceipt(load("scope_escape")));
  assert.equal(scope.result, "OUT OF SCOPE");
  assert.match(scope.evidence.join(" "), /shipping\.py/);
  assert.match(scope.evidence.join(" "), /미실행 4개/);
  assert.match(scope.nextDecision, /별도 승인/);
  const incomplete = demo.summarizeReceipt(parseDemoReceipt(load("incomplete")));
  assert.equal(incomplete.result, "UNKNOWN");
  assert.match(incomplete.evidence.join(" "), /개발 검사 2\/2 통과.*최종 검사 0\/2 통과.*미실행 2개/);
  assert.match(incomplete.nextDecision, /최종 검증/);
});

test("summary amounts and outcomes are observations rather than scenario-name assertions", () => {
  const receipt = parseDemoReceipt(load("regression"));
  const observed = receipt.checks.find(c => c.phase === "development" && c.commandId === "shipping")!;
  observed.cases = [{ subtotal: 60000, coupon: 20000, expected: 40000, actual: 44000 }];
  assert.match(demo.summarizeReceipt(receipt).evidence.join(" "), /실제 44,000원/);
  for (const change of [
    (r: DemoReceipt) => { r.checks.find(c => c.phase === "final")!.availability = "unavailable"; },
    (r: DemoReceipt) => { r.checks.find(c => c.phase === "final")!.timedOut = true; },
    (r: DemoReceipt) => { r.checks.find(c => c.phase === "final")!.timedOut = null; },
    (r: DemoReceipt) => { r.scope = { ...r.scope, availability: "unavailable", proceed: null, changed: [], violations: [] }; },
    (r: DemoReceipt) => { r.recordedStatus = "unknown"; },
  ]) {
    const changed = parseDemoReceipt(load("correct")); change(changed);
    const summary = demo.summarizeReceipt(changed);
    assert.equal(summary.result, "UNKNOWN");
    assert.doesNotMatch(summary.conclusion, /검사가 수정 결과를 뒷받침/);
  }
});
