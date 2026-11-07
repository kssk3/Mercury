# H1: Separate execution, verification and stopping reasons

Historical scope: the active-outcome references below describe the original
2026-11-02 hold. This note preserves that held proposal and grants no current
implementation authority.

Status: HELD_BY_USER as of 2026-11-02; implementation and tests NOT_STARTED.
RED preparation from 2026-11-01 is preserved below for a future explicit resume.
The active outcome is the Mercury-native frontend rebuild, not H1 implementation.
No code changes, native execution, new runtime, commit or publication are
authorized by this document. [WORKFLOW.md](WORKFLOW.md) is the lifecycle authority.
W1/W3/W6 are recorded there; this document is not another workflow.

## User outcome and behavioral contract

A caller can tell what the native process did, what the required checks proved,
and why the harness stopped, without treating a policy label as a root cause.

- Behavior: project supported recorded facts into separate execution,
  verification and stopping-reason fields in live-result and historical views.
- Guarantees: preserve evidence identity, original outcomes, missing/unknown
  distinctions, existing exit semantics and read-only historical inspection.
- Not guaranteed: diagnose omitted native messages, authenticate historical
  evidence, verify the current workspace, or infer UI quality from unit tests.

The motivating recorded F3-A01-R2 outcome was native exit 0 in 59.708 seconds,
missing required output, harness `human` / `unsafe_failure`, and existing
171-test/type PASS. The new 12 tests did not exist and were not run. The separate
FILE-EDIT-PROBE-01 then observed a successful file edit in 16.507 seconds despite
three initially unclassified error events. Neither fact identifies the app
attempt's underlying cause. These are reused historical observations, not fresh
checks or proof that every error event is harmless.

## Proposed result contract

For the first RED contract, use an additive `result_breakdown` object with its
own `schema_version: 1` on both `summarize_execution` and `execute_run` outputs.
Preserve all existing fields and their meanings, including the top-level schema
version. This is a projection-only addition, not a persisted record format.
The following broader meanings remain the H1 outcome scope.

| Dimension | Required distinction | Evidence constraint |
| --- | --- | --- |
| Execution | successful exit, failed exit, timeout, launch failure, not started, unknown | Use actual process observations; keep native and wrapper exits distinct. A successful process is not task PASS. |
| Verification | passed, failed, not run, unknown; applicable phase and criteria | Preserve baseline, development and final checks separately. A baseline failure must not become a new implementation failure. |
| Stop reason | original loop/wrapper policy code and a fixed explanation | `unsafe_failure` and `retry_budget` describe stopping policy, not the native root cause. Preserve unknown codes conservatively. |
| Supporting facts | scope/protected-input result and explicitly required output facts | Allowed paths are not required outputs. Only a recorded contract/checker finding establishes missing required output. |
| Uncertainty | what is not established and which evidence is unavailable | Missing, omitted, truncated, malformed and absent observations are not interchangeable. Do not invent unavailable distinctions. |

Preserve current `status`, `reason`, `criteria_passed` and command exit semantics.
Do not silently reinterpret existing fields or overwrite persisted records.
Prefer an additive bounded presentation over a stored-schema migration. If
existing evidence cannot support a dimension, report UNKNOWN with its supported
reason. A checker text message is not automatically a structured trusted finding.

Every result must remain attributable to its run/attempt and available recorded
identity. A later caller verification must be labeled separately with its own
candidate/environment evidence; it cannot rewrite a timed-out native attempt.
Historical inspection continues to report `historical_only=true` and
`current_workspace_verified=false`.

## First implementation boundary, not execution approval

Proposed owners to inspect and admit together:

| Responsibility | Candidate write set | Required coverage |
| --- | --- | --- |
| Read-only projection of existing validated observations | `src/agent_harness/inspection.py` | Supported legacy/direct-loop/wrapper histories; UNKNOWN for inconsistent records. |
| Live CLI result presentation after terminal finalization | `src/agent_harness/task_run.py` | Same meanings as historical view; release/finalization errors cannot become PASS. |
| Behavioral regression tests | New `tests/test_h1_result_separation.py` only | Reuse existing fixture helpers read-only; preserve all existing tests, including dirty review-binding tests. |
| User-facing output contract | Existing CLI documentation for the affected result | Explain policy vs diagnosis and compatibility; no new dashboard. |

Reuse validated observations and existing failure classification. Assess unchanged
`failure.py`, `retry.py`, `loop.py`, `attempt.py`, `journal.py`, `state.py`,
`startup.py`, `verification_checkpoint.py`, CLI routing and schema validation as
consumers/contracts. Do not modify these just to make a label more convenient.
A required persisted-shape change exceeds this first presentation scope and needs
explicit producer/serializer/reader/recovery admission before implementation.

Current local Mercury HEAD is `eff5708c05b586be41f047e46b18d079f6849dbe`;
the working tree contains unrelated diagnostic changes, including inspection.
HEAD alone is not an immutable implementation baseline for this dirty candidate.
The 2026-11-01 RED preparation binds the 129-file content snapshot, dirty patch,
Git index hash and held candidate range in the local evidence ledger. Its
baseline manifest SHA256 is
`2b8d4127a7ee2eaa5f57d89fbdfd2b8dfc6dbded53df11315d75dada8ba00989`;
the archive SHA256 is
`815eb83b778f31e474a55d1149933cdfb105636d1e05c991a8b953cbac227b8c`.
This is a content-addressed preparation snapshot, not a new Git commit, an OS
write barrier or verified code. The plan's subsequent documentation-only update
is recorded as a separate overlay; source/test/dependency bytes remain bound to
the snapshot. Drift in those inputs requires renewed admission, not rebasing the
baseline silently. Original records remain unchanged.

The branch is `codex/p1-review-evidence`. HEAD already contains P1 review-binding
work, and the dirty candidate adds compact/full review-projection compatibility.
Preserve both; H1 does not establish their completion or publication status.
The earlier roadmap's pinned-main snapshot is not this local candidate's identity.
No remote PR status, remote-main state or independent code review was refreshed.

## Held work overlap: resolved boundary for this preparation

The local held candidate is `79e7d4f353f36a39a4339b26fd7d84881012304e`,
compared from common ancestor `ec83497690b42bd3aea89bb8ed5e76736cf18da9`.
The full eight-file change list and overlapping inspection/CLI code were examined,
not merely the final four-file commit summary.

| Held work | H1 disposition |
| --- | --- |
| `status --details`, process/verification projection in `inspection.py` | Semantic overlap confirmed. Do not cherry-pick the candidate or add a parallel detailed-status reader. H1 owns the three-result breakdown on the current reader; any later details integration must consume the same interpretation. |
| Scope/delta, output captures, usage counters and detailed diagnostics | Excluded from the first RED/GREEN write set. Reuse their validation lessons, not unadmitted feature code. |
| `profile.py` Git timeout, output limits and cancellation cleanup | No H1 dependency; remain held and protected. |
| `tests/test_status_details.py`, profile-limit tests, CLI details flag | Not imported or recreated. No flag or profile behavior changes in H1. |
| Current review-binding and compact/full projection changes | Preserve exact current bytes in RED and semantics in GREEN. Held code predates these contracts and is not a replacement baseline. |

This resolves the design overlap without resuming PR #12. It does not establish
the held candidate's current remote status or authorize its integration.

## Prepared first RED unit

Unit: H1-RED-01. Current endpoint: documentation and evidence only.
Future RED write set: new `tests/test_h1_result_separation.py` only. Every existing
source, test, dependency file, runtime and failure record is protected. No null
implementation, stub API, package edit or test-helper refactor is needed.
The two existing APIs are importable; assert the new field with `.get()` and an
explicit mapping assertion so missing behavior produces AssertionError rather
than KeyError, TypeError or ImportError. Do not write those tests in this request.

Future GREEN source write set: `src/agent_harness/inspection.py` and
`src/agent_harness/task_run.py`; output-contract documentation may update the
existing `docs/quickstart.md` section without removing its unrelated edits.
Freeze the new RED test plus all existing tests. No `cli.py`, `profile.py`,
adapter, retry, state/journal, startup or recovery change is admitted. If those
become necessary, stop and present the concrete scope change.

The minimal output contract is:

```json
{
  "result_breakdown": {
    "schema_version": 1,
    "coverage": "active_attempt_only",
    "attempt_id": "recorded attempt identity",
    "execution": {"status": "successful_exit", "exit_code": 0},
    "verification": {"technical_kind": "fail", "overall_kind": "fail"},
    "stop_reason": {"code": "retry_budget", "role": "policy"}
  }
}
```

Use existing verification kinds verbatim, including `blocked` for a preexisting
failure. An absent verified record yields null verification, not PASS; unknown
execution must not be guessed from the wrapper exit. The first contract promises
no native duration, root-cause text, launch count or inferred missing-file reason.
The stop code preserves the original effective wrapper/loop reason. The `policy`
role applies to known stop-policy codes such as `retry_budget`/`unsafe_failure`;
unclassified reasons must remain `unknown`, not receive an invented diagnosis.
Invalid or inconsistent core history leaves the breakdown unavailable/null and
preserves the reader's existing UNKNOWN diagnostics. Validation of newly exposed
process fields must reject inconsistent status/exit combinations conservatively.

Live and historical results share the same recorded interpretation after existing
terminal finalization. Live wrapper exit1 and historical CLI exit0 can both be
correct: the former reports task failure, while the latter means the supported
historical record was readable. Neither exit code is the native process exit.

### RED cases and setup

1. Historical projection: use the existing `write_history` helper read-only.
   Produce a consistent synthetic history with native successful_exit/0,
   baseline checker exit0, current checker exit1, technical/overall fail and
   human/retry_budget. Rebind current/journal hashes after fixture construction.
   Verify old nonpass fields first, then require the new breakdown. Block process
   launch during the reader call and assert fixture bytes remain unchanged.
2. Live-to-history integration: reuse `make_fixture` and the existing fake-agent
   seam. Prepare a protected checker expecting `before` in the disposable fixture,
   commit that fixture and refresh its expected HEAD before configuration loading.
   The successful fake agent writes `after`; baseline passes and verification
   fails. Native-model calls remain forbidden. Require execution success,
   fail verification, unchanged task-nonpass exit and identical historical
   breakdown after finalization. Do not edit a protected checker after admission.
3. Existing-failure distinction: use the standard checker expecting `after` with
   a successful no-op fake agent. Both baseline and current check fail. Require
   blocked verification and preserved unsafe_failure, not a newly introduced
   failure or a permission diagnosis. Keep scope/protected inputs unchanged.

Expected first RED: these three semantic cases fail at the missing breakdown
assertion while their setup and existing-result assertions succeed. This is an
expectation from source inspection, NOT an executed result. Collect/import errors,
fixture admission failures, native launch, timeout, or a baseline mismatch are
not acceptable RED and must stop implementation handoff for diagnosis.

### Bounded check handoff

The local evidence packet records exact Python/Git paths and digests, working
directory, environment, command arrays, timeouts and relevant regression nodes.
Use the project venv Python with bytecode/cache writes disabled and an explicitly
bound import path; do not install dependencies or select a different executable
silently. The RED command is planned as pytest on the new test file, with a
120-second child-process timeout and zero automatic retries. Relevant existing
regressions are a separate bounded command. Module presence was inspected, but
pytest import, collection and tests were not executed in this preparation.

No implementation/review agent is dispatched here. Existing diagnostic review
usage2/2 and frontend review usage0/2 remain attached to their original outcomes;
neither supplies or resets the P1/H1 delivery allowance. Remaining H1/P1 aggregate
review/publication authority is UNKNOWN and must be reconciled before dispatching
that stage. This does not prevent completing the requested RED preparation.

## Explicit exclusions

- No new native execution, automatic retry/resume, timeout increase or rollback.
- No adapter/tool permission changes, raw-message retention or new diagnostic
  runtime. Better error capture is a later H2 outcome.
- No second state store, generic event parser, dashboard, configuration generator
  or new dependency. No mandatory-output inference from allowed paths.
- No frontend source/test changes, API integration or app delivery claim.
- No relaxation of protected paths, unknown handling or existing acceptance gates.

## Verification plan: one feature-level RED/GREEN outcome

First establish the public contract with a meaningful failing assertion for
successful native exit plus failed verification. Freeze the admitted tests for
GREEN, implement the smallest projection, then refactor only if needed. Add the
following cases because they exercise distinct failure modes, not to reach an
arbitrary test count. All checks below are PLANNED and have not run for H1.

| Case | Required assertion |
| --- | --- |
| Native exit 0, required output absent | Execution success stays visible; task/verification is nonpass; missing output only appears with attributable recorded evidence. |
| Native timeout with later caller PASS | Original timeout remains; separate later verification cannot change the original verdict. |
| Preflight rejection before launch | NOT_STARTED when supported; zero attempts; no fabricated native duration/output. |
| Existing baseline failure and unchanged candidate | Preserve baseline attribution and `unsafe_failure`; do not claim a newly introduced defect or scope violation. |
| Successful file-edit probe with initial error events | Preserve process/check PASS; unknown event causes do not automatically override it. |
| Protected input or scope violation | Show the concrete observed boundary failure separately from the stop policy; never PASS. |
| No verification record vs failed checker | NOT_RUN only when proved; otherwise UNKNOWN. Neither is PASS. |
| Legacy/partial/corrupt/mismatched history | Preserve supported legacy facts; inconsistent binding stays UNKNOWN without execution or recovery. |
| Wrapper release/finalization/interruption error | Preserve valid observed process facts where binding permits; do not hide incomplete terminal recording or report overall PASS. |
| Allowed but optional file absent | Do not label it missing required output. |
| Evidence provenance and privacy | No raw omitted text echoed; no current workspace or referenced output reads; old records are unchanged. |
| Live result versus historical read | Matching finalized evidence produces matching meanings; native and wrapper codes stay distinct. |

Use sanitized synthetic fixtures modeled on retained evidence. Original evidence
is read-only, never rewritten to make a test pass. Snapshot fixture bytes before
and after inspection. Monkeypatch execution/preparation/recovery entry points to
fail if a supposedly read-only reader calls them. Assertions target semantic
fields and fixed reasons, not incidental prose, timings, event order or counts.

Run scoped tests for the projection/live output first, then affected failure,
retry, persistence/recovery and serialization cases selected from the admitted
diff. Relevant existing tests include native success without verification,
nonpass historical outcomes, historical reads that never open referenced output,
and wrapper terminal failure that never leaves readable PASS.

Before independent review, execute applicable full candidate checks from
[CONTRIBUTING.md](../../CONTRIBUTING.md): pytest, schema validation, Ruff check
and format check, mypy, offline example and diff checks. Each command needs a
pinned executable/cwd/environment, explicit bounded deadline and recorded result;
split a suite into bounded documented subsets only when needed and retain total
coverage. No real native model is required to verify this presentation contract.
Report unavailable checks explicitly rather than treating fixtures as live proof.

UI/browser checks are not applicable to H1's Python CLI output. Inspect sample CLI
JSON for readability and compatibility instead. The separately requested frontend
server smoke check is operational evidence, not an H1 acceptance test.

## Completion and next gate

Planning is complete when scope, semantics, exclusions, evidence limits and
verification cases are reviewable. Implementation completion later requires
passing applicable checks and independent review within an explicitly available
budget; no current review allowance is reset. Publication and main delivery need
their existing separate authority.

Next smallest step: authorize writing/running the prepared test-only RED on the
bound candidate, verify no protected-input drift, then inspect actual failure
evidence. The native frontend configuration path is not required for these local
Python contract tests. No RED, GREEN or live native launch occurred in this turn.
