# Single-agent API and CLI quickstart

This developer preview requires Python 3.12+, Git, and a local macOS environment.
Licensed under the [MIT License](../LICENSE).
The first example demonstrates API composition with a **simulated Codex
adapter**. It performs no native-agent, model, or network invocation.

This guide describes the current source tree. The last recorded delivered base,
as of 2026-10-19, is
[main `84367471`](https://github.com/kssk3/Mercury/commit/843674716483e828686d4a9f79f53e453046e1c3),
including the bounded user-repository example in
[PR #7](https://github.com/kssk3/Mercury/pull/7) and the common agent contract in
[PR #8](https://github.com/kssk3/Mercury/pull/8), followed by documentation in
[PR #9](https://github.com/kssk3/Mercury/pull/9). Its existing automatic
[main CI](https://github.com/kssk3/Mercury/actions/runs/39210422432) passed.
Claude integration, explicit task execution and the JavaScript example below
are subsequent local candidate features. Their documentation does not establish
publication or successful live execution; consult attributable candidate evidence.

## Install and run

From the repository root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/quickstart.py --approve-demo
```

Read [the example](../examples/quickstart.py) before passing `--approve-demo`.
That flag records your human admission of this exact disposable task: change
`value.txt` from `before` to `after`, allow only `value.txt`, protect `check.py`,
and require the unchanged check to pass. It grants no authority over an existing
repository. Omitting the flag exits before creating the fixture.

The script creates a temporary Git repository with a baseline commit and a
sibling state directory outside that repository. It holds `RepositoryLock`
through `run_loop` and releases it in `finally`. The lease expires after 120
seconds, beyond the demo's 30-second loop budget; longer callers must maintain
their lease heartbeat and serialize all operations on the same repository.

## What the example verifies

The mandatory command runs `check.py` using the current Python interpreter.
It fails on the initial value, then passes after the simulated adapter writes
the admitted value. The loop runs baseline, development, and a distinct final
verification, with the completion criterion mapped to that exact command.
`check.py` is a protected input, checked for changes throughout the loop.

The caller supplies a bounded context packet, turn/input/output byte limits,
a two-second verification timeout, no retries, four command boundaries, and
a 30-second loop budget. One boundary represents the simulated turn; the other
three are real local verification commands. These budgets do not measure or
control the internal commands of a real native agent.

The fixture redactor replaces one synthetic marker before returned text is
stored. The script checks the stored output for that replacement. This is an
illustration of a caller-defined policy, not a general secret detector; raw
adapter results can remain in memory. Both fixture and state are deleted on exit.

A successful run prints:

```json
{"command_boundaries": 4, "criteria_passed": true, "mode": "simulated", "reason": "final_verification", "redaction_checked": true, "status": "pass"}
```

The script returns zero only for that verified result. Other statuses and
reasons remain visible and return nonzero. Process exit, stored output, and
scope clearance alone are insufficient for technical PASS.

## Product contracts and implementation

Use this map when composing a caller around the existing APIs. The
[simulator composition](../examples/quickstart.py) shows how the inputs fit
into one run; the implementation and tests below describe each boundary.
The caller remains responsible for choosing meaningful tasks and checks.

| Boundary | Existing responsibility | Implementation and contract tests |
| --- | --- | --- |
| Task and human admission | The caller obtains actual approval and chooses scope and criteria. The admission record binds the exact contract; it does not authenticate a person's approval. | [TaskContract](../src/agent_harness/contract.py), [admission](../src/agent_harness/admission.py); [contract tests](../tests/test_contract.py), [admission tests](../tests/test_admission.py) |
| Scope and protected inputs | The caller selects allowed and protected paths. Snapshot comparison and protected fingerprints provide observations, without process confinement. | [scope](../src/agent_harness/scope.py), [integrity](../src/agent_harness/integrity.py); [scope tests](../tests/test_scope.py), [integrity tests](../tests/test_integrity.py) |
| Mandatory verification and completion | The caller freezes required commands and maps every criterion to exact commands. The loop requires distinct final verification and passing mapped criteria. | [VerificationPolicy](../src/agent_harness/policy.py), [bind_completion_criteria and run_loop](../src/agent_harness/loop.py); [policy tests](../tests/test_policy.py), [loop tests](../tests/test_loop.py) |
| State, journal, and startup | The caller supplies external storage. Current state and event history feed read-only startup reconciliation; these records carry evidence without a power-loss durability guarantee. | [state](../src/agent_harness/state.py), [journal](../src/agent_harness/journal.py), [startup](../src/agent_harness/startup.py); [state tests](../tests/test_state.py), [journal tests](../tests/test_journal.py), [startup tests](../tests/test_startup.py) |
| Explicit recovery | The caller retains trusted sealed-patch references for file recovery. Supported verification checkpoints can be continued explicitly; native sessions are not automatically resumed. | [patch recovery](../src/agent_harness/recovery.py), [continue_verification](../src/agent_harness/loop.py); [recovery tests](../tests/test_recovery.py), [continuation tests](../tests/test_verification_continuation.py) |
| Lease and outer budgets | The caller serializes repository work, holds and renews its lease, and supplies turn and retry limits. Outer command/time budgets do not cap native internal commands, tokens, or cost. | [RepositoryLock](../src/agent_harness/lock.py), [TurnBudgets](../src/agent_harness/adapter.py), [RetryLimits](../src/agent_harness/retry.py); [lock tests](../tests/test_lock.py), [retry tests](../tests/test_retry.py) |

The linked tests are navigation references to expected behavior. A link does
not establish that a test was executed or passed; execution evidence must
identify the actual check and result. For development commands, see
[CONTRIBUTING.md](../CONTRIBUTING.md).

For an existing native integration example, read the
[disposable real Codex guide](codex-quickstart.md). It documents a separate,
explicitly approved temporary-repository task, its prerequisites, budgets,
and retained evidence. Other repositories need an explicit run configuration
or Python caller integration.

The [python_study integration](python-study-example.md), delivered in PR #7,
adds one fixed addition-CLI task on a separate copy of an access-restricted
repository at a pinned revision. It is not a general repository importer.
Historical live Codex evidence applies to its recorded candidate, not a new live
execution of every subsequent correction.

### Common agent contract

PR #8 provides [AgentAdapter and AgentTurnResult](../src/agent_harness/agent.py)
for trusted backend implementations. Preparation belongs to an actual turn,
after its before snapshot and persisted intent; compatibility preflight and
verification-only continuation do not prepare. Backend results are validated
before downstream persistence, and scope observation and final verification
remain harness responsibilities. See the existing
[contract tests](../tests/test_agent_contract.py) for the supported behavior.

The original native backend is Codex; the additional
[Claude Code adapter](../src/agent_harness/claude_adapter.py) uses the same common
contract and executable-binding checks. Neither adapter supplies multi-agent
orchestration or a Gemini integration.
New backends still need their own trusted execution and configuration handling;
the interface itself supplies no confinement or task-success authority.

#### Connecting a backend

Implement the structural `AgentAdapter` protocol; subclassing `CodexCLIAdapter`
is not required. Keep native command/configuration handling in the adapter and
compose the existing harness APIs in the caller. Do not copy the loop's scope,
verification, or recovery decisions into the backend.

| Owner | Inputs and responsibility | Evidence or result |
| --- | --- | --- |
| Caller | Obtain actual human approval; construct `TaskContract`, matching admission, protected inputs, mandatory `VerificationPolicy`, exact criterion mappings, context and budgets. Hold the repository lease and choose external state/output storage and a redactor. | An admitted operation with explicit limits; the admission object itself does not authenticate approval. |
| Adapter `prepare(repository_root)` | Bind the trusted native executable and required preparation for this actual turn. The harness calls preparation after the before snapshot and persisted intent. | Preparation may succeed or raise; it must not claim a launch or verification result. |
| Adapter `run(...)` | Consume the admitted contract/context, honor applicable input/output/time limits and the requested mode, and invoke the selected backend. Reject unsupported modes rather than silently weakening them. | `AgentTurnResult` containing actual argv, canonical repository root, process status, exit code, duration and bounded captures. |
| Harness | Validate backend facts before downstream persistence, observe applicable scope/protected inputs, preserve attempt/recovery evidence, and run required and distinct final verification. | A loop outcome backed by observations and mapped verification results, not the agent's completion claim. |

The turn order is: caller admission and prerequisites → harness before snapshot
and saved intent → adapter preparation → native execution → result validation,
observation and recorded evidence → required verification and final verification.
Compatibility preflight and verification-only continuation do not prepare or
launch a new agent. Preparation is part of the observed turn, so preparation
changes cannot be hidden by taking the before snapshot afterward.

| Backend outcome | Required interpretation |
| --- | --- |
| Preparation raises | Do not call `run` or invent a successful observation; retain the saved intent/recovery evidence for reconciliation. |
| Process cannot start | Report `launch_error` with no exit code; do not count a completed native operation. |
| Process exceeds its limit or exits unsuccessfully | Preserve bounded partial captures and report `timeout` or `nonzero_exit` truthfully. |
| Process exits zero | Report `successful_exit`; this is process evidence, not technical PASS. |
| Final output is absent, unsafe or unreadable | Keep the corresponding `final_output_status` and no fabricated final message. |
| Result fields or capture byte counts are invalid | The harness rejects the result; do not persist unvalidated success metadata. |

`CapturedText` uses UTF-8 with `surrogateescape`; `retained_bytes` describes the
retained bytes, and `truncated` must describe actual truncation. Captures remain
untrusted and unredacted until the caller's output policy is applied. Neither the
protocol nor an adapter's mode name supplies filesystem confinement. Native
tools can have different permission systems; each concrete adapter must state
what it supports, what it refuses, and which native configuration remains active.

Use the [common contract tests](../tests/test_agent_contract.py) as behavioral
references for structural adapters, preparation failure, malformed results and
verification ownership. The [Codex adapter](../src/agent_harness/adapter.py)
illustrates executable binding and bounded native I/O, while the
[simulator](../examples/quickstart.py) illustrates caller composition. These
references do not establish compatibility with another CLI or authorize a live
model call. A new backend needs its own bounded conformance checks before a
separately scoped live integration example.

#### Claude Code backend boundaries

`ClaudeCLIAdapter(executable, max_budget_usd=0.25)` uses a caller-selected external
Claude Code executable. Its noninteractive JSON result is translated into the
common result contract; an error result, malformed JSON, truncated JSON or missing
final content cannot become an available final message. Timeouts retain bounded
partial output through the same process collector used by Codex.

The adapter sets both available and preapproved tools to `Read,Edit,Write,Glob,Grep`
for workspace-write mode, and `Read,Glob,Grep` for read-only mode. It denies
permission prompts, supplies an empty strict MCP configuration, disables slash
commands and session persistence, and does not enable permission bypass, shell,
subagent or network tools. Mandatory `--safe-mode` disables user/project/local
customizations, including hooks, plugins, CLAUDE.md and autoloaded extensions.
Mandatory `--restricted` confines built-in file tools to working directories
and loads only managed or explicitly supplied settings. Mercury supplies no
settings file: authentication remains external, but model preferences in excluded
user/project settings are not inherited. Managed policy hooks and the executable
itself remain trusted. The adapter removes inherited `NODE_OPTIONS` before every
Claude launch, including after preparation and on later turns, so Node preload
options cannot execute before those CLI controls. Other applicable resolver
checks and unrelated caller environment remain active.
These are native tool policies, not an OS sandbox, and working-directory access
is broader than the task's allowed/protected path subset. Unsupported CLIs fail
closed; Mercury never retries without either required mode.
No API key, login or settings change is performed by Mercury.

The USD option is a native CLI budget, not a measured invoice or a cap on the
number of internal model turns. `TurnBudgets` separately bounds the native
process duration and retained bytes. Fake-executable contract tests establish
the adapter boundary, not compatibility with every Claude version or provider.
Before live use, confirm the installed CLI supports these flags and has the
intended authentication. The implementation was prepared against Claude Code
2.1.304 combined restricted/safe-mode help and the official
[CLI reference](https://code.claude.com/docs/en/cli-reference); restricted mode
requires at least 2.1.248. Help confirms flag support, not real-engine path
confinement. Local Node startup regressions and fake-CLI argv tests are separate
from real Claude execution evidence.

## Using a real repository

The example's subclass replaces `CodexCLIAdapter.run` entirely. A real
integration must supply its own trusted executable/environment and genuinely
admitted task, verification policy, protection set, redactor, budgets, external
state, and caller-held lease. Codex inherits native configuration, authentication, rules and environment.
Claude uses the restricted/safe-mode boundary described above, retaining external
authentication and trusted managed policy rather than excluded settings. No credential setup or live execution
is part of this quickstart.

Allowed paths guide the agent; observations are point-in-time scope checks,
without hostile-process confinement. Ignored untracked files and Git internals
are outside aggregate file observations. Protected inputs are caller-selected.
State writes and journal ordering provide recovery evidence without an
fsync/power-loss durability guarantee. Explicit verification continuation is
available for supported checkpoints; automatic native-session resume is absent.

The CLI offers `--help`, `--version`, `profile [path]`, the read-only
`contract` and `plan` previews, the `status` record summary, and the explicitly
approved `run` command below. Runtime multi-agent orchestration and messenger control are
outside this preview.
See [development checks](../CONTRIBUTING.md).


### Preview a task contract

After installation, preview a contract without running a task:

```sh
.venv/bin/local-harness contract --goal "Fix the total" \
  --allow missing/cart.py --protect missing/test_cart.py \
  --criterion "The total is correct"
```

```json
{"allowed_paths":["missing/cart.py"],"completion_criteria":["The total is correct"],"goal":"Fix the total","protected_paths":["missing/test_cart.py"]}
```

`--goal` and at least one `--criterion` are required. Repeat `--allow`,
`--protect`, and `--criterion` for additional values; their order and spelling
are preserved. Omitted path flags produce empty arrays. The paths need not exist.
The existing TaskContract validation rejects blank goals/criteria, absolute or
parent-traversing paths, Git metadata, noncanonical paths, duplicate paths,
and overlapping allowed/protected scope. Invalid input returns exit 2 with an
error on stderr and no JSON; valid input returns exit 0 with one JSON line.

Preview only validates strings and prints the four-field contract. It does not
inspect or modify a repository, create admission/state/lock/baseline records,
or invoke Git, agents, models, network calls, or verification commands. A valid
preview is neither human approval nor evidence that a criterion passed.
Execution requires separate explicit run configuration and approval, or Python
caller integration.

### Preview a verification plan

Use `plan` to validate a contract, mandatory verification commands, and exact
criterion mappings together:

```sh
.venv/bin/local-harness plan --goal "Fix the total" \
  --allow missing/cart.py --protect missing/test_cart.py \
  --criterion "The total is correct" \
  --command '["python", "check.py"]' --timeout-seconds 10 \
  --criterion-map '{"The total is correct": [["python", "check.py"]]}'
```

```json
{"contract":{"allowed_paths":["missing/cart.py"],"completion_criteria":["The total is correct"],"goal":"Fix the total","protected_paths":["missing/test_cart.py"]},"criterion_bindings":[{"commands":[["python","check.py"]],"criterion":"The total is correct"}],"verification_policy":{"commands":[["python","check.py"]],"timeout_seconds":10,"working_directory":"."}}
```

The contract flags use the validation described above. At least one repeatable
`--command`, a positive integer `--timeout-seconds`, and `--criterion-map` are
required. Each command is a JSON array of nonempty string argv tokens, without
NUL characters; tokens retain their exact spelling and order. Shell syntax is
not interpreted. The optional `--working-directory` defaults to `.` and follows
[VerificationPolicy](../src/agent_harness/policy.py): it must be relative and
must not contain parent traversal, Git metadata, or NUL characters.

`--criterion-map` is a JSON object whose keys exactly match the declared
criteria. Each value is a nonempty list of command argv arrays matching
registered commands exactly. Missing, extra, or duplicate criteria, duplicate
JSON object keys, and unregistered commands are refused. The printed bindings
follow declared criterion order. Malformed or excessively nested JSON is also
refused. Valid input returns exit 0, no stderr, and one JSON line with
`contract`, `verification_policy`, and `criterion_bindings`; invalid input
returns exit 2 with a stderr diagnostic and no JSON stdout.

This preview only validates strings. It reads no input files or target
repository, creates no admission/state/journal/lock/baseline/output records,
and invokes no Git commands, verification commands, agents, models, or network
calls. Paths and executables need not exist. The JSON provides no human
approval, delivery evidence, technical PASS, authenticity, or executable-trust
claim. Executing a task requires the separate admission described below.

### Execute an explicit task

`run` composes the existing loop; it does not discover a task or choose tests.
Use a clean Git repository with a full pinned HEAD, a trusted external backend,
existing protected checker inputs, and canonical external configuration/state
paths. Every caller for the same repository must share `state_root` to share
its lease. All `run` invocations use the same 300-second lease horizon and renew
every 30 seconds throughout setup and execution. Renewal stops and joins before
release; an observed renewal failure prevents success and further native phases.
An already-running native phase is not forcibly interrupted by renewal failure.
If the renewal worker cannot stop within its bounded join, the result is UNKNOWN
and the lease is left in place rather than released beneath that worker. The existing lock
evaluates expiry using the acquiring caller's horizon; Python API callers sharing
this state must use the same horizon and maintain their heartbeat for longer
work. Different state roots do not coordinate. These are cooperative leases,
not an OS lock held for the lifetime of an arbitrary native process.

The following is a configuration template, not an executable task for Mercury.
Replace paths and HEAD with your reviewed target. `check.mjs` must already exist
and test the intended behavior; the goal and its command mapping must agree.

```json
{
  "schema_version": 1,
  "repository": "/absolute/path/to/repository",
  "expected_head": "REPLACE_WITH_FULL_GIT_HEAD",
  "state_root": "/absolute/path/to/external-state",
  "backend": "claude",
  "executable": "/absolute/path/to/claude",
  "contract": {
    "goal": "Fix add(a, b) in add.mjs",
    "allowed_paths": ["add.mjs"],
    "protected_paths": ["check.mjs"],
    "completion_criteria": ["Addition cases pass"]
  },
  "verification": {
    "commands": [["/absolute/path/to/node", "check.mjs"]],
    "working_directory": ".",
    "timeout_seconds": 5
  },
  "criterion_commands": {
    "Addition cases pass": [["/absolute/path/to/node", "check.mjs"]]
  },
  "context_paths": ["add.mjs", "check.mjs"],
  "budgets": {
    "timeout_seconds": 90,
    "input_bytes": 16384,
    "stdout_bytes": 65536,
    "stderr_bytes": 16384,
    "final_bytes": 8192
  },
  "total_timeout_seconds": 120
}
```

Save reviewed JSON outside both target repository and state root. Compute its
raw-byte SHA-256 with a trusted local hashing tool, inspect the exact contents,
then supply the digest and an approver label:

```sh
.venv/bin/local-harness run --config /absolute/path/to/run.json \
  --approve-config-sha256 REVIEWED_64_CHARACTER_SHA256 --approver YOUR_NAME
```

The hash binds the entire configuration, including commands, paths and budgets;
it is not identity authentication. Missing or mismatched approval is refused
before state writes or native execution. The direct Python execution entry point
reloads those approved bytes, compares every configuration field and uses the
fresh snapshot, including its independent criterion mapping. Changing a loaded
configuration requires newly approved bytes, rather than `dataclasses.replace`
with the old digest. JSON has a 64 KiB limit; duplicate or
unknown keys, symlink/hardlink configuration files, nonfinite limits and unsafe
path overlap are rejected. Backends are `codex` and `claude`; arbitrary flags,
shell command strings and model/auth overrides are not accepted.
Verification children discard inherited `NODE_OPTIONS`, including preload flags;
the existing resolver filters still constrain `NODE_PATH`. Unrelated caller
environment remains available. This does not change native backend configuration.

The wrapper rechecks HEAD and cleanliness under its repository lease. Both
preflight checks reject tracked skip-worktree/assume-unchanged index flags rather
than trusting a porcelain status that can hide modified checker contents. Each
configuration digest gets a fresh `runs/<digest>` evidence directory; existing
history is refused, including an empty directory. There is one native invocation
and no automatic retry. The loop reserves baseline, development and distinct
final verification; per-command/native limits are at most 120 seconds, total
loop time at most 240 seconds. Git preflight and setup are separate bounded
operations, so the loop limit is not an end-to-end wall-clock guarantee.

Output is one JSON summary with status/reason, initial HEAD, configuration hash,
state directory, attempts, command boundaries and criterion success. The
initial HEAD is the first Git HEAD observed during preflight, or `null` if
preflight stops before that observation; the expected HEAD remains an input.
Verifier commands must invoke their trusted program directly; recognized shell
and `env` dispatch, including resolved/same-file aliases, are unsupported.
Python and Node verifiers require an existing script operand; repository scripts
must be protected. Python accepts only `-I`, `-B`, `-E`, `-s`, `-S`, `-u`, `-O`,
`-OO`, and `-q` before that script; Node accepts only `--no-warnings` and
`--disable-proto=throw`. Either permits `--` before the script. Evaluation,
module and stdin forms are refused. This bounded check does not classify
arbitrary renamed/copied executables. Captured text is omitted from stored
output by default; structured evidence remains.
Exit 0 requires loop PASS and all mapped criteria. Invalid configuration or
preflight returns 2; execution failure/unknown returns 1. Lease-release failure
cannot return PASS. New wrapper records declare version-2 binding and remain
UNKNOWN to `status` until the post-release wrapper result is saved. Historical
version-1 binding and direct-loop records remain readable without that field.
No automatic rollback, session resume, publication or merge
is performed. Codex native configuration and Claude managed policy remain trusted;
Claude uses mandatory safe mode, restricted working-directory file tools and
managed/explicit settings only, and discards inherited `NODE_OPTIONS`. These limits do not provide OS confinement or prove that the chosen tests are sufficient.

### JavaScript integration example

[javascript_quickstart.py](../examples/javascript_quickstart.py) creates an owned
fresh Git repository with a deliberately incorrect `add.mjs` and a protected
`check.mjs`. It uses Node's built-in assertions for positive, negative and zero
addition cases, without npm or dependencies. The Python example creates the
same explicit configuration and invokes the `run` CLI above.

Read the example first. With existing Claude authentication and trusted absolute
executable paths, explicitly approve one native invocation:

```sh
.venv/bin/python examples/javascript_quickstart.py --approve-demo \
  --node /absolute/path/to/node --executable /absolute/path/to/claude \
  --evidence-dir /absolute/path/to/new-external-demo
```

The evidence directory must be new, outside existing repositories, and free of
symlink aliases. The original repositories are not copied or modified. Only
`add.mjs` may change; `check.mjs` is protected. Native execution has a 90-second
limit, each Node check 5 seconds, and the loop 120 seconds with zero retries.
Claude receives the adapter's USD 0.25 native budget option; this is not measured
billing evidence. Authentication stays external; restricted mode excludes
user/project settings, including their model preferences. The example
retains fixture, configuration and evidence on success or failure and does not
publish anything. Fake-executable tests and an actual native run are separate
evidence; the presence of this example does not claim a live run passed.

### Summarize recorded execution

The historical reader is available on
[main `34c7439e`](https://github.com/kssk3/Mercury/commit/34c7439ed3ad8eb6cf3be7483f0cbea5f7df162e)
through [PR #6](https://github.com/kssk3/Mercury/pull/6):

```sh
.venv/bin/local-harness status --state-dir /absolute/path/to/external-state
```

The required directory selects only `current.json` and `events.jsonl`. The reader
opens regular single-link files without following symlink leaves or ancestors,
and reads at most 1 MiB of current state and 8 MiB / 10,000 journal records. These
limits apply to this reader, without changing runtime persistence behavior.
It creates no directory, file or lock, reads no repository or referenced output
file, and invokes no Git, agent, network, verification or recovery action.

One JSON line has `schema_version: 1`, `historical_only: true`,
`current_workspace_verified: false`, `recorded_status`, `recorded_reason`,
`attempt_ids`, `target_revision`, `verification`, `evidence`, and `diagnostics`.
`verification` contains only recorded technical/overall kinds and command count,
or null. `evidence` contains current/journal paths and byte SHA-256 digests plus
output channel identifiers (digests), without stdout/stderr/native transcript
bodies. It does not copy arbitrary state/event fields. UNKNOWN input returns
null metadata and a fixed diagnostic, never malformed input text.

Supported histories use v1 `attempt_summary` with a journal path/hash and the
existing unversioned `loop_outcome` fields. The current loop summary must match
the terminal journal event. The attempt summary hash can cover the full journal
or its preterminal prefix: the producer refreshes attempts immediately before
appending the terminal outcome. Represented identities, revision, verification
and output identifiers must agree; incomplete or contradictory views remain
UNKNOWN. This is a limited historical binding check, not comprehensive semantic
validation of every event or authentication of recorded claims. File metadata
is checked across the pair observation; adversarial ABA isolation is not promised.

Valid supported readable records return exit 0, including recorded fail, blocked,
stopped, human, and unknown outcomes. Missing/malformed/unsupported/unsafe or
changed inputs return exit 1 and one JSON line with `recorded_status: "unknown"`.
Argument syntax errors, including missing `--state-dir`, retain argparse exit 2.
A recorded pass does not verify the current workspace, prove output authenticity,
obtain approval, permit a rerun, or authorize merge.
