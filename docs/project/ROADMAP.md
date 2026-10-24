# Mercury roadmap

Documentation date: 2026-10-24. Product snapshot:
[main f6343844](https://github.com/kssk3/Mercury/commit/f63438440f77c3eca177399bc5c7d0bcbd985a5d).
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
| Pure review-result contract | [PR #13](https://github.com/kssk3/Mercury/pull/13): frozen bindings/receipts and assessment of supplied facts. No automatic loop, CLI or GitHub gate integration. | [Review contract](../review-contract.md) |

The existing automatic
[main CI run 39924711186](https://github.com/kssk3/Mercury/actions/runs/39924711186)
completed successfully for this exact snapshot. This documentation reuses that
evidence; it neither reruns CI nor claims every later backend correction has
been exercised with a real model. Historical live evidence remains bound to its
actual revision, executable, environment and task.

## Local and held work

| State | Work | Boundary |
| --- | --- | --- |
| LOCAL | Overview/quickstart alignment and this roadmap/architecture update | Unpublished documentation in the working tree; no product behavior change. |
| LOCAL | Personal skill and current-record maintenance outside the shipped package | Authoring-environment work, not a portable Mercury feature or proof that hooks fire. Private records do not belong in this public repository. |
| HELD | [PR #12](https://github.com/kssk3/Mercury/pull/12), recorded candidate 79e7d4f3 | Status-detail/profile Git-limit work is excluded from the pinned main capabilities. Native completion and internal task/error evidence were not established in the retained investigation. The underlying service cause remains UNKNOWN; no current live state is asserted here. |

Scoped runtime/example AGENTS guidance is locally complete in
[src/agent_harness/AGENTS.md](../../src/agent_harness/AGENTS.md) and
[examples/AGENTS.md](../../examples/AGENTS.md). These guides add folder-specific
navigation under the existing [R1-R4](../../AGENTS.md#code-review-rules);
they introduce no hook registrations.

## Proposed next product outcomes

### P1: Bind execution results to the actual review target

The current [review contract](../review-contract.md#trusted-caller-responsibilities)
expects trusted identities and evidence from its caller. The next bounded
outcome should establish that producer/consumer connection using existing run,
evidence and review components.

Before implementation, define the admitted candidate snapshot/diff semantics,
including relevant uncommitted content; the attempt and contract identity;
attributable verification evidence; serialization; and invalidation when inputs
change. Cover production, persistence, read-only presentation and supported
recovery/continuation consumers together. Missing or mismatched evidence remains
unknown or invalid. Existing receipt shape validation does not authenticate a
reviewer, a person's approval or the referenced evidence.

Completion means that the specified execution/candidate/evidence relationship
is represented and checked without turning review clearance into permission,
delivery or a replacement for technical PASS. It does not require a second
runner, automatic reviewer dispatch or a GitHub integration. Exact files,
affected checks and remaining review/publication allowance need separate
admission; no execution or verification budget is allocated here.

### P2: Present binding and review evidence read-only

After P1, consider exposing its established facts through existing inspection
and review facilities. Keep recorded outcomes separate from current workspace
verification and approval. Resolve any overlap with held PR #12 before admitting
new status/detail work; do not silently reproduce or resume that candidate.

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
