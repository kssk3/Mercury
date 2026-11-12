# Mercury architecture

The historical documentation baseline is 2026-11-08 at
[main d2216f7a](https://github.com/kssk3/Mercury/commit/d2216f7a54e4cddee8e48e63fb45b8f32466241c).
The earlier unpushed local integration commit `0b91f259` had the same product
source as that baseline. The current local map includes the 2026-11-11 agent
package refactor on inspected base `c567a3ef`; this does not claim delivery to
main. It describes existing module responsibilities and the local package
boundary, not a new lifecycle policy. [WORKFLOW.md](WORKFLOW.md) owns repository
lifecycle; [ROADMAP.md](ROADMAP.md) separates delivered, local, held and planned
outcomes.

## Product execution and authoring environment

Mercury's Python package starts an admitted task through a trusted Codex or
Claude CLI adapter, observes scope, executes chosen verification commands and
records evidence. Its implementation language does not restrict the checker to
Python. The caller supplies meaningful commands for the target language.

The tools used to develop Mercury have a separate control plane: personal
skills, authoring-app hooks, repository AGENTS, human decisions and GitHub
delivery checks. They are not automatically installed or enforced by Mercury's
product adapters. Separate Codex/Claude apps need no cmux handoff dependency.
An AGENTS file guides decisions; tool permissions and sandboxing have their own
actual enforcement boundaries.

## Main entry paths

```mermaid
flowchart TD
    C[Explicit configuration] --> P[Read-only run preview]
    P --> J[Settings and digest JSON]
    C --> A[Separately approved configuration bytes]
    A --> R[Execution wrapper: preflight and lease]
    R --> L[Existing bounded loop]
    L --> T[Observed turn: snapshot and saved intent]
    T --> N[Trusted agent adapter]
    N --> O[Observed result and scope evidence]
    O --> L
    L --> V[Admitted technical checks and final verification]
    V --> E[Technical evidence]
    E --> L
    L --> S[State and journal]
    R --> F[Post-release wrapper result]
    S --> H[Read-only historical inspection]
    F --> H
    V --> B[Eligible final candidate and evidence binding]
    B --> S
    B --> Q[Pure review assessment]
    U[Caller-supplied matching report and receipt] --> Q
    H --> X[JSON explanation or offline HTML]
    K[Existing external check receipts] --> X
    X --> G[Explicit fresh report bundle]
```

Execution produces recorded identities; the caller connects trusted evidence
and a receipt to the pure assessment API. The loop does not enforce receipt
assessment or dispatch a reviewer. A preview does not supply approval.
Historical inspection does not run recovery or verify the present workspace.
Report export writes a new projection of saved records; external check collection
is a separate, explicitly authorized Python API. Detailed contracts are in the
[quickstart responsibility map](../quickstart.md#product-contracts-and-implementation),
[run preview](../run-preview.md), [review contract](../review-contract.md) and
[validation visibility](../validation-visibility.md).

## Module responsibilities

Paths below are existing modules, not proposed services or agent roles. The
agent port is consumer-owned; task_run.py selects concrete implementations.
Legacy agent.py, adapter.py and claude_adapter.py imports remain aliases to
canonical definitions. The working-tree verification boundary similarly keeps
runner.py as public aliases; this snapshot does not claim that refactor is
delivered to main.

| Responsibility | Modules | Boundary |
| --- | --- | --- |
| CLI and repository inventory | [cli.py](../../src/agent_harness/cli.py), [profile.py](../../src/agent_harness/profile.py) | Route commands and collect filename-based Git information. Repository inventory is not technical verification. |
| Approved execution configuration | [run_config.py](../../src/agent_harness/run_config.py), [task_run.py](../../src/agent_harness/task_run.py) | Parse/preview bounded settings, including explicit retries; reload approved bytes, preflight repository/state/checkers, hold the lease, compose the loop and finalize the wrapper result. |
| Task and admission contracts | [contract.py](../../src/agent_harness/contract.py), [admission.py](../../src/agent_harness/admission.py), [amendment.py](../../src/agent_harness/amendment.py), [command_admission.py](../../src/agent_harness/command_admission.py) | Represent admitted scope, criteria and command/contract decisions. Recorded approval is not identity authentication. |
| Repository, workspace and protected inputs | [repository.py](../../src/agent_harness/repository.py), [workspace.py](../../src/agent_harness/workspace.py), [scope.py](../../src/agent_harness/scope.py), [integrity.py](../../src/agent_harness/integrity.py), [baseline.py](../../src/agent_harness/baseline.py) | Establish repository identity, path/scope observations and protected fingerprints without claiming hostile-process confinement. |
| Common and native adapter boundaries | [ports/agent.py](../../src/agent_harness/ports/agent.py), [adapters/agents/codex.py](../../src/agent_harness/adapters/agents/codex.py), [adapters/agents/claude.py](../../src/agent_harness/adapters/agents/claude.py), [adapters/agents/_native.py](../../src/agent_harness/adapters/agents/_native.py), [native_output.py](../../src/agent_harness/native_output.py) | Prepare/run a trusted backend, validate process/result facts and retain bounded native output. Task success remains a harness decision. |
| Bounded observations and source-verified launcher | [diagnostics.py](../../src/agent_harness/diagnostics.py), [verified_runtime.py](../../scripts/verified_runtime.py) | Record observed native milestones without inferring model latency/success; optionally verify a separately prepared frozen source inventory before import. Neither mechanism grants execution authority. |
| Context and stored text | [context.py](../../src/agent_harness/context.py), [output.py](../../src/agent_harness/output.py) | Select bounded context and apply the caller's output-storage/redaction policy. Raw adapter text can exist before redaction. |
| Technical verification | [ports/verification.py](../../src/agent_harness/ports/verification.py), [adapters/verification/local.py](../../src/agent_harness/adapters/verification/local.py), [adapters/_executables.py](../../src/agent_harness/adapters/_executables.py), [composition/verification.py](../../src/agent_harness/composition/verification.py), [policy.py](../../src/agent_harness/policy.py), [runner.py](../../src/agent_harness/runner.py), [development.py](../../src/agent_harness/development.py), [final.py](../../src/agent_harness/final.py), [evidence.py](../../src/agent_harness/evidence.py) | Bind required commands and trusted programs, collect bounded command results, decide technical outcomes and preserve protected-input failures. |
| Execution, retry and escalation decisions | [execution.py](../../src/agent_harness/execution.py), [retry.py](../../src/agent_harness/retry.py), [escalation.py](../../src/agent_harness/escalation.py) | Represent decisions over admitted execution facts/limits. In particular, execution.py makes pure state transitions; it does not execute or persist work. |
| Turn and loop composition | [turn.py](../../src/agent_harness/turn.py), [loop.py](../../src/agent_harness/loop.py) | Order snapshots, saved intent, preparation, execution, scope checks, verification, terminal records and supported explicit continuation. |
| Persistence and checkpoint facts | [state.py](../../src/agent_harness/state.py), [journal.py](../../src/agent_harness/journal.py), [attempt.py](../../src/agent_harness/attempt.py), [verification_checkpoint.py](../../src/agent_harness/verification_checkpoint.py) | Store current/journal/attempt projections and serialize supported checkpoint facts. A checkpoint alone does not authorize continuation. |
| Startup, recovery and failure evidence | [startup.py](../../src/agent_harness/startup.py), [recovery.py](../../src/agent_harness/recovery.py), [interruption.py](../../src/agent_harness/interruption.py), [failure.py](../../src/agent_harness/failure.py) | Reconcile or classify retained evidence and support explicitly requested file recovery. Reports do not resume native sessions or create completion authority. |
| Cooperative serialization | [lock.py](../../src/agent_harness/lock.py) | Repository lease and heartbeat; callers must coordinate on the same state location and compatible horizon. |
| Candidate/evidence identity production | [review_evidence.py](../../src/agent_harness/review_evidence.py) with loop.py and attempt.py | Build deterministic identities from supplied final-verification observations and safe metadata; validate recorded binding without reading the live workspace or authenticating references. |
| Historical inspection and explanation | [inspection.py](../../src/agent_harness/inspection.py), [explanation.py](../../src/agent_harness/explanation.py) | Read supported state/journal/wrapper bindings and project details or explanation from a bounded snapshot. No task, output payload, live-workspace verification or recovery is executed. |
| Offline presentation and explicit export | [html_report.py](../../src/agent_harness/html_report.py), [report.py](../../src/agent_harness/report.py) | Render a supplied explanation without I/O; export one capture to fresh JSON/HTML/Markdown files after bounded Git/path boundary checks. Failed exports retain partial files, with no retry or rollback. |
| External check collection and receipt reading | [external_checks.py](../../src/agent_harness/external_checks.py) | The trusted caller explicitly runs one admitted checker into a fresh protected-boundary-checked bundle. A separate reader validates fixed receipt/process/JUnit siblings without executing checks or observing the live target. |
| Review-result assessment | [review.py](../../src/agent_harness/review.py) | Validate receipt shape and compare caller-established binding/evidence. It performs no I/O, digest production, authentication or reviewer dispatch. |
| Development schema and package surface | [schema_validation.py](../../src/agent_harness/schema_validation.py), [__init__.py](../../src/agent_harness/__init__.py) | Development-only synthetic status validation and package version/surface; neither establishes delivery or replaces runtime evidence. |

## Verification execution boundary

`ports.verification` owns the execution protocol and unchanged result/proof
records. `adapters.verification.local.ControlledCommandRunner` selects and runs
trusted local programs; `adapters._executables` contains proof/path/environment
checks shared with native agent adapters. Selecting a program and validating it
again before launch remain separate steps. These checks are not an OS sandbox.

The loop binds at its existing pre-agent point and passes the same selection to
per-command baseline, development and final execution. Its scope observations,
retry/command budgets, persistence and technical decisions remain application
responsibilities. Verification-only continuation keeps its existing re-entry
rules and does not invoke agent preparation or execution. External receipt
collection still saves intent before launch and keeps its 65536-byte output cap.

Public loop/development/external-check entry points accept an optional trusted
executor. Their default composition calls, and task_run.py's explicit assembly,
are transitional composition boundaries; those modules are not wholly pure.
The internal `_run_loop` receives its executor explicitly. Old runner imports
alias canonical objects, while private monkeypatch paths follow definition
owners. Arbitrary dynamic imports and class introspection/pickle identity strings
are outside the static dependency and public import compatibility contract.

## Execution ordering and invariants

1. The caller chooses the task, protected checker, commands, budgets and actual
   authorization. The CLI's configuration digest binds bytes, not a person's
   identity. The Python API requires caller-owned admission and serialization.
2. The execution wrapper reloads approved configuration bytes and uses that
   independent snapshot. It checks baseline/protected inputs around acquisition
   of its repository lease before composing the loop.
3. An actual turn captures its before state and saves intent before adapter
   preparation. Preparation belongs to that turn, including observed mutations;
   compatibility checks and verification-only continuation do not prepare again.
4. Native process/result facts are validated before downstream recording. An
   output artifact or successful exit alone is not scope clearance or technical
   PASS. The loop runs admitted technical phases and maps every criterion to
   exact commands, including a distinct final verification.
5. When final-verification observations match, the loop records the candidate and
   verification binding. Supported readers require consistent terminal evidence;
   CLI histories also require successful post-release wrapper finalization before
   exposing the binding. A legacy history can retain technical PASS without one.
6. Current state, attempt projections and terminal journal records preserve
   observed outcomes. CLI wrapper records also need post-release finalization;
   incomplete or inconsistent supported records remain UNKNOWN to readers.

Preserve these existing boundaries through the cross-cutting
[R1-R4 review rules](../../AGENTS.md#code-review-rules). They are product
invariants, not new repair/review/publication allowances.

## Persistence and consumer ownership

When a future contract changes stored identity or event/result shape, trace the
existing producer and consumers rather than adding a parallel store or universal
parser. Distinct trust boundaries can require distinct validation.

| Stored contract | Producer/serialization owners | Consumers to assess together |
| --- | --- | --- |
| Turn intent, observed process/output and attempt phases | turn.py, loop.py, attempt.py, journal.py, state.py | startup.py reconciliation; interruption.py/failure.py projections; verification_checkpoint.py and loop.py continuation; inspection.py supported histories |
| Loop summary and CLI wrapper binding/finalization | loop.py and task_run.py with current/journal projections | inspection.py and supported startup/continuation logic; keep direct-loop/older record handling explicit |
| Sealed file-recovery evidence | recovery.py capture/seal and caller-retained trusted references | recovery.py loading/preflight/recovery; reconcile relevant live state separately before any continuation |
| Final candidate and verification binding | review_evidence.py builds identities; loop.py/attempt.py serialize full journal evidence and compact current projections | inspection.py and startup.py validate matching projections; loop.py verification-only continuation records its actual final phase without another adapter preparation |
| Review receipt assessment | review.py defines records; the trusted caller supplies matching evidence and an actual receipt | assess_review compares supplied facts without authentication, persistence, reviewer dispatch or an automatic runtime/CLI gate |
| Historical explanation and fresh report projection | inspection.py/explanation.py capture and validate one saved snapshot; html_report.py renders it; report.py exports fresh files | JSON, standalone HTML and record.md retain recorded failures/unknowns rather than creating verification, approval or delivery |
| External command receipt | external_checks.py saves intent before launch, observed process facts and terminal receipt after bounded target/result observation | Its reader validates fixed linked artifacts; explanation.py/report.py present applicability without rerunning commands; incomplete collection evidence remains retained and unavailable |

See the [delivered binding outcome](ROADMAP.md#p1-bind-execution-results-to-the-actual-review-target)
and [binding contract](../review-contract.md#execution-produced-binding) before
changing these records. Evidence reuse depends on relevant content, base, tool
and environment applicability under WORKFLOW, not merely an unchanged Git HEAD.

## Trust and capability limits

- Trusted native executables and declared interpreter proofs are checked through
  invocation/retry boundaries. Arbitrary plugins, runtime dependencies and
  native settings remain part of the admitted trust decision.
- Codex retains its native configuration. The Claude backend uses its mandatory
  restricted/safe-mode boundary and excludes user/project customizations; external
  authentication and managed policy remain trusted. See
  [backend limits](../quickstart.md#claude-code-backend-boundaries).
- Scope observations cover tracked and nonignored untracked paths. They do not
  cover every ignored entry or Git internal, and they are not an OS sandbox or
  atomic protection against concurrent changes. Protected inputs are explicit.
- Native/command timeouts and retained-byte budgets are separate from internal
  model turns, tokens and billing. Cooperative leases are not hostile-process
  locks; preflight/cleanup also have their own timing boundaries.
- Journal ordering and recovery evidence do not promise automatic rollback,
  automatic native-session resume or fsync/power-loss durability.
- A review receipt cannot authenticate its references or make technical failure
  pass. Technical verification, review clearance, execution permission and actual
  delivery remain separate. GitHub merge/status workflows govern development of
  this repository; they are not an automatic product integration.
- Historical reads and report-generation exit codes describe readability or
  generation, not historical task success. External receipts cannot promote an
  incomplete native history to PASS. Saved JSON/HTML can contain local paths and
  operator identifiers; export is not automatic privacy scrubbing or portability.

Tests and existing PASS evidence describe their recorded candidate/environment.
The linked [development checks](../../CONTRIBUTING.md) were not executed to
produce this documentation update. PR #12, #15 and #16 are included in this
snapshot; the held H1 plan and proposed P3 do not change its implementation.
