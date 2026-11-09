# Mercury roadmap

Documentation date: 2026-11-08. Delivered product snapshot:
[main d2216f7a](https://github.com/kssk3/Mercury/commit/d2216f7a54e4cddee8e48e63fb45b8f32466241c).
This document describes delivered capability and proposed outcomes. It grants
no implementation, model execution, check, review, publication or merge authority.
[WORKFLOW.md](WORKFLOW.md) remains the sole repository lifecycle authority.
See [ARCHITECTURE.md](ARCHITECTURE.md) for current module ownership.

## Product purpose

Mercury is a lightweight local harness for a human-admitted coding task. A
caller chooses the repository, allowed/protected paths, acceptance criteria,
trusted agent and verification commands. Mercury observes bounded execution,
collects technical evidence and preserves supported recovery information.

The user entry point is an explicit configuration with
[`run --preview`](../run-preview.md) before separately approved execution, or
the [Python APIs](../quickstart.md#product-contracts-and-implementation).
The common adapter contract supports Codex and Claude CLI integrations. Python
is the harness implementation language; a checker may validate another language,
as the [JavaScript example](../quickstart.md#javascript-integration-example)
demonstrates. Meaningful checks and actual authorization remain caller decisions.

The goal is dependable execution and attributable evidence with less caller
effort. Generated code volume, agent count and new configuration are not success
criteria. General multi-agent management and automatic repository onboarding
are not current product capabilities.

## State definitions

| State | Meaning |
| --- | --- |
| DELIVERED_MAIN | Present in the pinned main snapshot, with existing delivery evidence. This does not establish a fresh live run of the current backend. |
| LOCAL | Work retained locally outside that delivered snapshot. Local edits alone do not imply completed tests, review or publication. |
| HELD | Work deliberately paused; its old commands, allowances and partial observations are not authority to resume. |
| PLANNED | A proposed outcome requiring its own scope, acceptance criteria and budget before implementation. |
| UNKNOWN | A fact is not established by available evidence. It is not PASS or an unused zero counter. |

## Delivered on the pinned main

| Capability | Delivery boundary and evidence | Usage |
| --- | --- | --- |
| Task/admission, scoped turns, protected inputs, required checks, evidence and explicit recovery | Existing single-agent Python APIs; callers supply the task, commands, external state and serialization. | [API responsibility map](../quickstart.md#product-contracts-and-implementation) |
| Offline simulator and disposable native Codex example | The simulator uses no native model. [PR #3](https://github.com/kssk3/Mercury/pull/3) delivered a separately approved fixed native example. | [Quickstart](../quickstart.md), [Codex example](../codex-quickstart.md) |
| Read-only contract, verification-plan and historical status CLI | [PR #4](https://github.com/kssk3/Mercury/pull/4), [PR #5](https://github.com/kssk3/Mercury/pull/5), [PR #6](https://github.com/kssk3/Mercury/pull/6). Preview/record output is not execution or approval. | [Contract](../quickstart.md#preview-a-task-contract), [plan](../quickstart.md#preview-a-verification-plan), [status](../quickstart.md#summarize-recorded-execution) |
| Fixed real-repository integration | [PR #7](https://github.com/kssk3/Mercury/pull/7): a pinned, access-restricted source and separate working copy for one addition-CLI task. | [Python study example](../python-study-example.md) |
| Common agent contract | [PR #8](https://github.com/kssk3/Mercury/pull/8): preparation, execution and result boundaries, preserving Codex compatibility aliases. | [AgentAdapter](../../src/agent_harness/agent.py) |
| Claude adapter, explicit run CLI and JavaScript example | [PR #10](https://github.com/kssk3/Mercury/pull/10): configuration-approved one-turn/no-retry CLI over the existing loop, plus the separate backend and example. | [Execution](../quickstart.md#execute-an-explicit-task), [backend limits](../quickstart.md#claude-code-backend-boundaries) |
| Read-only execution-settings preview | [PR #11](https://github.com/kssk3/Mercury/pull/11): inspect the actual configuration and digest without state creation, checkers or native execution. | [Run preview](../run-preview.md) |
| Pure review-result contract | [PR #13](https://github.com/kssk3/Mercury/pull/13): frozen bindings/receipts and assessment of supplied facts. Receipt assessment is not automatically enforced by the loop, CLI or GitHub. | [Review contract](../review-contract.md) |
| Execution-produced candidate/evidence binding | [PR #15](https://github.com/kssk3/Mercury/pull/15): final-verification identities, compact current projections, journal validation and supported startup/continuation consumers. This does not authenticate review or approval. | [Recorded binding](../review-contract.md#execution-produced-binding) |
| Bounded retry configuration and observed diagnostics | PR #15 includes explicit retry limits/feedback and bounded native diagnostics, plus source verification before import through an optional prepared launcher. Limits and source integrity do not authorize execution or prove model reliability. | [Retry settings](../run-preview.md#explicit-bounded-retries), [verified runtime](../verified-runtime.md) |
| Historical status details and bounded profile queries | [PR #12](https://github.com/kssk3/Mercury/pull/12), merged after reconciliation with current main: saved process/scope/verification metadata and bounded Git profiling. Details do not verify the live workspace. | [Status details](../status-details.md) |
| Historical validation explanation and offline HTML | PR #15: one bounded captured history supplies JSON explanation or standalone Korean HTML; comparison inputs remain caller context. | [Validation visibility](../validation-visibility.md) |
| External check receipts and fresh report bundles | [PR #16](https://github.com/kssk3/Mercury/pull/16): a separately authorized single-check Python collector, read-only receipt validation, and an explicit three-file export of existing records. Export does not run checks. | [Receipt collection](../validation-visibility.md#explicit-external-command-receipts), [report export](../validation-visibility.md#explicit-fresh-report-bundle) |

The pinned merge history establishes which implementation this map describes.
This update uses that local history and the current source/contracts; it does
not fetch remote status, rerun CI or claim a fresh native execution. Earlier CI
and live-run evidence remains bound to its actual revision, executable,
environment and task. A successful historical read or report export is not a
new technical PASS.

## Local and held work

| State | Work | Boundary |
| --- | --- | --- |
| LOCAL | Saved integration commit `0b91f259155124bad823ffb908aafd405ed60403` | Nine documentation/regression-test files are committed locally and unpushed. Product source matches the pinned main. Historical serial checks are not a fresh suite result; the two native-fixture parallel timeout cases remain unresolved and their investigation is stopped by the user. |
| LOCAL | This roadmap/architecture refresh and read-only usage walkthrough | Locally committed documentation; no product behavior change, native run or publication. |
| LOCAL | Personal skill and current-record maintenance outside the shipped package | Authoring-environment work, not a portable Mercury feature or proof that hooks fire. Private records do not belong in this public repository. |

Scoped runtime/example AGENTS guidance is available in
[src/agent_harness/AGENTS.md](../../src/agent_harness/AGENTS.md) and
[examples/AGENTS.md](../../examples/AGENTS.md). These guides add folder-specific
navigation under the existing [R1-R4](../../AGENTS.md#code-review-rules);
they introduce no hook registrations.

## Held product outcome

### H1: Explain execution, verification and stopping policy separately

The user's 2026-11-01 request prepared a bounded explanation improvement.
On 2026-11-02 the user explicitly HELD H1 to resume the frontend rebuild.
[H1 scope and verification plan](H1-result-separation-plan.md) is preserved and
unimplemented. Reuse recorded facts with explicit unknowns; preserve
existing exit/retry semantics and history. The plan predates the delivered P1/P2
and PR #12 work; its historical status references do not override the current
delivery table. Reassess overlap against the current implementation before any
separately admitted H1 work. W1/W3/W6 working practices live in WORKFLOW rather
than a second lifecycle document. No implementation or native execution is
authorized here.

## Completed follow-up outcomes

### P1: Bind execution results to the actual review target

State: DELIVERED_MAIN through PR #15. The loop records the final observed
candidate and verification identities; attempt serialization, compact current
state, journal inspection, startup reconciliation and verification-only
continuation preserve the connection. See the
[execution-produced binding contract](../review-contract.md#execution-produced-binding)
for its exact observation and size limits.

The caller still supplies a trusted matching report and receipt to the pure
assessment API. Shape/digest equality does not authenticate a reviewer, findings
or human approval. No reviewer dispatch, execution permission or automatic
GitHub gate follows from a recorded binding.

### P2: Present binding and review evidence read-only

State: DELIVERED_MAIN through PR #15, with PR #12 details and PR #16 reporting
extensions. Historical inspection exposes eligible recorded bindings and
validation explanations; offline HTML and report bundles present those facts.
Missing, inconsistent or legacy evidence retains its documented unavailable or
UNKNOWN state. Receipt assessment and actual independent review remain caller
responsibilities. This visibility does not verify the current workspace.

Start with the [read-only walkthrough](../validation-visibility.md#read-only-walkthrough-from-a-mercury-checkout).

## Proposed next product outcome

### P3: Reduce setup for one small real repository

Use the existing configuration/preview/run path to identify a concrete remaining
caller burden. Prefer a smaller guide or interface adjustment over a general
importer or new runtime. A live trial needs an explicit task, protected checker,
prepared environment, execution allowance and stopping condition.

## Deferred directions

- Additional backends after a demonstrated use case and compatibility contract.
- Model/skill comparisons using the same task, baseline, checks and budgets,
  with actual outcome and cost evidence.
- Neutral internal adapter helpers when repeated backend changes justify the
  extraction; current module size alone is insufficient.
- Recurring documentation automation, custom architecture linters or richer
  telemetry only when a repeated measurable need exceeds existing facilities.

Runtime multi-agent management, automatic native-session recovery, automatic
merge, permissive merge gates and broad package reorganization are not implied
next steps. New ideas are assessed against the product problem, existing
coverage, maintenance cost, trust boundaries and a smaller alternative.
