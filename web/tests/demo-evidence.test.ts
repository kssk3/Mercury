import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { parseDemoReceipt, snapshotBytes, type DemoReceipt } from "../src/lib/demo-evidence";

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
