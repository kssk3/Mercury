import assert from "node:assert/strict";
import { test } from "node:test";
import { ESLint } from "eslint";
import ts from "typescript";
import { resolve } from "node:path";

const eslint = new ESLint();
const fixtures = [
  ["conditional hooks", 'import { useState } from "react"; export function Probe({ enabled }: { enabled: boolean }) { if (enabled) useState(0); return <div />; }', /react-hooks\/rules-of-hooks/],
  ["effect dependencies", 'import { useEffect } from "react"; export function Probe({ value }: { value: string }) { useEffect(() => { console.log(value); }, []); return <div />; }', /react-hooks\/exhaustive-deps/],
  ["image alternative text", 'export function Probe() { return <img src="sample.png" />; }', /jsx-a11y(?:-x)?\/alt-text/],
  ["invalid ARIA attributes", 'export function Probe() { return <div aria-not-a-property="x" />; }', /jsx-a11y(?:-x)?\/aria-props/],
  ["synchronous scripts", 'export function Probe() { return <script src="sample.js" />; }', /@next\/next\/no-sync-scripts/],
  ["explicit any", 'export function Probe(value: any) { return value; }', /@typescript-eslint\/no-explicit-any/],
  ["missing list keys", 'export function Probe() { return [1, 2].map(value => <span>{value}</span>); }', /(?:react\/jsx-key|@eslint-react\/no-missing-key)/],
  ["children passed as a prop", 'export function Probe() { return <div children="sample" />; }', /(?:react\/no-children-prop|@eslint-react\/jsx-no-children-prop)/],
  ["raw JSX comment text", 'export function Probe() { return <div>// sample</div>; }', /(?:react\/jsx-no-comment-textnodes|@eslint-react\/jsx-no-comment-textnodes)/],
  ["dangerous HTML with children", 'export function Probe() { return <div dangerouslySetInnerHTML={{ __html: "sample" }}>child</div>; }', /(?:react\/no-danger-with-children|@eslint-react\/dom[-/]no-dangerously-set-innerhtml-with-children)/],
];
for (const [name, code, expectedRule] of fixtures) {
  test(`lint rejects ${name}`, async () => {
    const [result] = await eslint.lintText(code, { filePath: "src/lint-probe.tsx" });
    assert.equal(result.fatalErrorCount, 0, JSON.stringify(result.messages));
    assert.ok(result.messages.some(message => expectedRule.test(message.ruleId ?? "")), JSON.stringify(result.messages));
  });
}

test("valid typed JSX remains accepted", async () => {
  const [result] = await eslint.lintText('export function Probe({ value }: { value: string }) { return <button type="button" aria-label="Sample">{value}</button>; }', { filePath: "src/lint-probe.tsx" });
  assert.deepEqual(result.messages, []);
});

// React 19 types replace legacy JSX/class checks that do not have a lint equivalent.
test("TypeScript rejects duplicate/undefined JSX and removed React APIs", () => {
  const filename = resolve("src/lint-type-probe.tsx");
  const code = `import { Component, createClass } from "react";
export class Probe extends Component { render() {} check() { this.isMounted(); } }
export const element = <div ref="legacy" id="a" id="b"><Missing /></div>;
export const legacy = createClass;`;
  const options = { noEmit: true, strict: true, jsx: ts.JsxEmit.ReactJSX, skipLibCheck: true, target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler };
  const host = ts.createCompilerHost(options);
  const original = host.getSourceFile.bind(host);
  host.getSourceFile = (path, languageVersion, onError, shouldCreateNewSourceFile) => path === filename
    ? ts.createSourceFile(path, code, languageVersion, true, ts.ScriptKind.TSX)
    : original(path, languageVersion, onError, shouldCreateNewSourceFile);
  const diagnostics = ts.getPreEmitDiagnostics(ts.createProgram([filename], options, host)).filter(d => d.file?.fileName === filename);
  const codes = diagnostics.map(d => d.code);
  for (const expected of [17001, 2304, 2305, 2322, 2339, 2416]) {
    assert.ok(codes.includes(expected), JSON.stringify(diagnostics.map(d => ({ code: d.code, message: ts.flattenDiagnosticMessageText(d.messageText, " ") }))));
  }
});
