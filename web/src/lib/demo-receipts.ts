// Build-time validation stays on the server; no Node APIs reach the client bundle.
import { createHash } from "node:crypto";
import correct from "../../public/demo/cart/correct.json";
import regression from "../../public/demo/cart/regression.json";
import scopeEscape from "../../public/demo/cart/scope_escape.json";
import incomplete from "../../public/demo/cart/incomplete.json";
import { parseDemoReceipt, snapshotBytes, type DemoReceipt, type ScenarioId } from "./demo-evidence";

export function loadDemoReceipts(): Record<ScenarioId, DemoReceipt> {
  const entries = Object.entries({ correct, regression, scope_escape: scopeEscape, incomplete }).map(([id, input]) => {
    const receipt = parseDemoReceipt(input);
    if (receipt.scenarioId !== id || receipt.sourceMode !== "recorded_simulated") throw new Error("Mismatched demo receipt source");
    for (const [files, expected] of [[receipt.diff.beforeFiles, receipt.fixtureSha256], [receipt.diff.afterFiles, receipt.candidateSha256]] as const) {
      const actual = createHash("sha256").update(snapshotBytes(files)).digest("hex");
      if (actual !== expected) throw new Error("Mismatched cart snapshot digest");
    }
    return [id, receipt];
  });
  return Object.fromEntries(entries) as Record<ScenarioId, DemoReceipt>;
}
