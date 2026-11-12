export type ScenarioId = "correct" | "regression" | "scope_escape" | "incomplete";
export type Availability = "observed" | "not_run" | "unavailable";
export interface SnapshotFile { path: string; content: string }
export interface CheckCase { subtotal: number; coupon: number; expected: number; actual: number }
export interface DemoCheck { phase: "baseline" | "development" | "final"; commandId: "discount" | "shipping"; exitCode: number | null; timedOut: boolean | null; cases: CheckCase[] | null; availability: Availability }
export interface DemoReceipt {
  schemaVersion: 1; scenarioId: ScenarioId; sourceMode: "recorded_simulated" | "recorded_native";
  runId: string; capturedAt: string; mercuryRevision: string; fixtureSha256: string; candidateSha256: string;
  recordedStatus: string; recordedReason: string | null; diagnostics: string[]; checks: DemoCheck[];
  scope: { availability: Availability; allowed: string[]; protected: string[]; changed: string[]; violations: string[]; proceed: boolean | null };
  diff: { beforeFiles: SnapshotFile[]; afterFiles: SnapshotFile[] }; limitations: string[];
}
const paths = ["cart.py", "check_discount.py", "check_shipping.py", "shipping.py"];
const scenarios = ["correct", "regression", "scope_escape", "incomplete"];
function requireValue(condition: unknown): asserts condition { if (!condition) throw new Error("Invalid cart evidence receipt"); }
function object(value: unknown, keys: string[]): Record<string, unknown> {
  requireValue(typeof value === "object" && value !== null && !Array.isArray(value));
  const row = value as Record<string, unknown>;
  requireValue(Object.keys(row).sort().join() === [...keys].sort().join()); return row;
}
function strings(value: unknown): asserts value is string[] { requireValue(Array.isArray(value) && value.every(s => typeof s === "string" && s.length < 4096)); }
function pathList(value: unknown): void { strings(value); requireValue(value.every(p => paths.includes(p)) && new Set(value).size === value.length); }
export function snapshotBytes(files: SnapshotFile[]): string { return JSON.stringify(files.map(f => [f.path, f.content])); }
export function parseDemoReceipt(input: unknown): DemoReceipt {
  const row = object(input, ["schemaVersion", "scenarioId", "sourceMode", "runId", "capturedAt", "mercuryRevision", "fixtureSha256", "candidateSha256", "recordedStatus", "recordedReason", "diagnostics", "checks", "scope", "diff", "limitations"]);
  requireValue(new TextEncoder().encode(JSON.stringify(row)).length <= 131072);
  requireValue(row.schemaVersion === 1 && scenarios.includes(String(row.scenarioId)));
  requireValue(["recorded_simulated", "recorded_native"].includes(String(row.sourceMode)));
  requireValue(typeof row.runId === "string" && /^cart-[\w-]+$/.test(row.runId));
  requireValue(typeof row.capturedAt === "string" && /^\d{4}-\d\d-\d\dT[\d:.]+\+00:00$/.test(row.capturedAt));
  requireValue(typeof row.mercuryRevision === "string" && /^[a-f0-9]{40}$/.test(row.mercuryRevision));
  for (const key of ["fixtureSha256", "candidateSha256"]) requireValue(typeof row[key] === "string" && /^[a-f0-9]{64}$/.test(row[key] as string));
  requireValue(["pass", "fail", "blocked", "human", "unknown", "stopped"].includes(String(row.recordedStatus)));
  requireValue(row.recordedReason === null || typeof row.recordedReason === "string" && /^[a-z0-9_]+$/.test(row.recordedReason));
  strings(row.diagnostics); strings(row.limitations);
  requireValue(row.diagnostics.every(s => /^[a-z0-9_]+$/.test(s)));
  const diff = object(row.diff, ["beforeFiles", "afterFiles"]);
  for (const snapshot of [diff.beforeFiles, diff.afterFiles]) {
    requireValue(Array.isArray(snapshot) && snapshot.length === 4);
    snapshot.forEach((value, i) => { const f = object(value, ["path", "content"]); requireValue(f.path === paths[i] && typeof f.content === "string" && !f.content.includes("\r") && new TextEncoder().encode(f.content).length <= 16384); });
  }
  const scope = object(row.scope, ["availability", "allowed", "protected", "changed", "violations", "proceed"]);
  requireValue(["observed", "not_run", "unavailable"].includes(String(scope.availability)));
  for (const key of ["allowed", "protected", "changed", "violations"]) pathList(scope[key]);
  requireValue(scope.proceed === null || typeof scope.proceed === "boolean");
  if (scope.availability !== "observed") requireValue(scope.proceed === null && (scope.changed as string[]).length === 0 && (scope.violations as string[]).length === 0);
  requireValue(Array.isArray(row.checks) && row.checks.length === 6);
  const identities = new Set<string>();
  for (const value of row.checks) {
    const check = object(value, ["phase", "commandId", "exitCode", "timedOut", "cases", "availability"]);
    requireValue(["baseline", "development", "final"].includes(String(check.phase)) && ["discount", "shipping"].includes(String(check.commandId)));
    identities.add(`${check.phase}/${check.commandId}`);
    requireValue(["observed", "not_run", "unavailable"].includes(String(check.availability)));
    requireValue(check.exitCode === null || Number.isSafeInteger(check.exitCode));
    requireValue(check.timedOut === null || typeof check.timedOut === "boolean");
    if (check.availability === "observed") requireValue(typeof check.timedOut === "boolean");
    else requireValue(check.exitCode === null && check.timedOut === null && check.cases === null);
    if (check.cases !== null) {
      requireValue(check.availability === "observed" && Array.isArray(check.cases));
      for (const value of check.cases) {
        const c = object(value, ["subtotal", "coupon", "expected", "actual"]);
        requireValue(Object.values(c).every(v => Number.isSafeInteger(v) && Math.abs(v as number) <= 100000));
      }
    }
  }
  requireValue(identities.size === 6);
  return input as DemoReceipt;
}
