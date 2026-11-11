# Preview a run before approving execution

Use the actual run configuration to inspect its settings before authorizing a
native agent:

```sh
uv run --no-sync local-harness run --preview --config /absolute/external/run.json
```

The command prints one JSON object. It includes the configuration's absolute
path and SHA256 of the exact bytes read, repository, configured `expected_head`,
backend, executable, state root, contract, verifier commands and working
directory, criterion mapping, context paths, byte/time budgets, total timeout,
and effective turn/retry/command limits. Configurations without `retry` retain
one turn and zero retries. It always reports `approval_required: true`
and `execution_performed: false`. `expected_head` is the requested baseline, not
an observation of the current HEAD. No success or authentication claim is made.

Unlike the pure-string `plan` command, preview reads a bounded configuration
file, performs read-only Git root queries, and inspects paths and protected
verifier inputs. It shares the execution configuration parser, including schema,
contract, path separation, verifier binding and budget validation. Configuration
files must be canonical absolute paths outside the repository and state root;
symlinks, hard links, non-regular files, duplicate JSON keys and files exceeding
65,536 bytes are rejected. Invalid configurations and preview combined with
either approval flag return exit code 2 with only
`local-harness: invalid_run_config` on stderr.

Preview does not create state, locks or journals, run checkers, prepare adapters,
or launch native processes. It does not import the run execution entry point.
Actual HEAD matching, repository cleanliness, executable readiness and applicable
native/authentication checks remain execution-time gates. Passing preview does
not establish that a subsequent run will pass those gates.

After inspecting all settings, obtain separate authorization for the actual run.
Use the digest from the reviewed output and identify its approver explicitly:

```sh
uv run --no-sync local-harness run --config /absolute/external/run.json \
  --approve-config-sha256 REVIEWED_64_CHARACTER_SHA256 --approver human-name
```

Do not automatically pipe preview's digest into execution. Preview grants no
approval and cannot be combined with `--approver` or `--approve-config-sha256`.
The execution loader reads the configuration again and rejects changed bytes;
repository files, executable targets and external settings remain mutable. Path
checks and snapshots are point-in-time observations, not an OS sandbox or
protection against concurrent changes. The Claude adapter's configured USD 0.25
CLI cap is not measured billing or a guaranteed invoice maximum.

## Explicit bounded retries

Retries are opt-in within the existing schema version 1. Omit `retry` to preserve
one turn, zero retries and the existing preview shape. To authorize at most one
retry, add this exact object to the configuration before preview and approval:

```json
"retry": {"max_retries": 1, "max_same_failure": 2}
```

Both fields are required integers: `max_retries` is 0 through 3 and
`max_same_failure` is 1 through `max_retries + 1`. Booleans, fractions, extra
fields and other values are rejected. A repetition limit of 1 deliberately stops
at the first failure; use 2 to permit a retry after the first matching failure.
The opt-in preview includes `max_same_failure` alongside effective `max_retries`,
`max_turns` and `max_commands`; per-turn budgets and total timeout remain visible.
Changing these bytes invalidates the old approval digest. No new CLI flag is
needed, and an allowed retry count is a ceiling, not a guarantee of another turn.

For `T = max_retries + 1` and `C` verification commands, command reservation is
`T + (2*T + 1)*C`: one original baseline plus development and distinct final
checks for each admitted turn, and one native boundary per turn. This is a
conservative upper bound; failed development checks can skip final checks.
Required aggregate seconds are
`T*(native_timeout + 2) + (2*T + 1)*C*(checker_timeout + 2) + 5`.
Insufficient total budget is rejected before launch. The existing 120-second
native and 240-second total caps remain; retries never expand them implicitly.
For example, one retry, one checker, a 30-second native timeout, a 2-second
checker timeout and a 90-second total allowance fit the required 89 seconds.
The 90-second native timeout in the one-shot example below does not fit one
retry within its existing 120-second total; reduce per-turn timeouts or choose
an explicitly reviewed total within the cap.

Execution reuses the existing loop, admission, config revalidation, original
baseline, checker argv, protected inputs, scope observation and saved history.
Only policy-permitted observed failures receive bounded
[retry feedback](retry-feedback.md) on the next turn. Pre-existing or unknown
failures, unsafe scope/protected-input changes, uncertain outcomes, repeated
failures and exhausted budgets still stop or escalate. Passing requires both
development verification and a separate final verification. Preview remains
read-only, and execution still requires separate authorization of exact bytes.

## Minimal execution and local completion

Use this sequence for a separately admitted task in a prepared repository. It
composes the existing CLI and caller responsibilities; it adds no execution
command, JSON field, automatic overall verdict, or review gate.

1. **Prepare the installed package and target.** Follow the existing
   [installation steps](quickstart.md#install-and-run). Install the package
   normally: copying its source directory alone can omit distribution metadata
   needed during import. Identify the installed revision or reviewed candidate
   and its content evidence. Prepare a clean, pinned working repository without
   discarding unrelated user changes or silently replacing its baseline.
2. **Choose the task and checks.** Use the existing
   [run configuration](quickstart.md#execute-an-explicit-task) and small example
   below. Select allowed edits, protected checker inputs, completion criteria,
   trusted executable paths, context and budgets. Keep configuration and state
   outside the target. Decide which checks belong to Mercury and which are
   required once at caller-owned completion before executing anything.
3. **Preview the exact configuration.** Use `run --preview` above and inspect its
   bytes, digest, paths, criteria, commands and limits. Preview performs no native
   execution or checker run and supplies no approval or technical PASS.
4. **Execute only the approved operation.** After actual authorization, use the
   existing `run --config ... --approve-config-sha256 ... --approver ...` form
   above. A valid configuration or spare numerical budget is not new permission.
   Wait for the native turn and terminal wrapper outcome; intermediate output,
   a file appearing, or a progress observation does not establish completion.
5. **Read Mercury's actual result.** Retain its configuration digest, repository
   identity, state directory, scope observations and verification evidence.
   Read the recorded status, reason and criterion result rather than relying on
   the agent's completion message. An unsuccessful or incomplete required outcome
   is not technical PASS. Apply the admitted stopping rule before starting any
   dependent check; do not continue a failed or unknown required stage automatically.
6. **Finish the admitted local checks.** Run any caller-owned required checks in
   their dependency order, or reuse applicable earlier evidence. Bind the final
   report to the current candidate and inspect its actual diff and scope. Keep
   current totals separate from historical attempts in one authoritative report.
   Use [WORKFLOW](project/WORKFLOW.md) for reuse, stopping, review and delivery
   decisions; this guide grants no additional attempt or integration authority.

### Use the selected runtime's supported budgets

The currently documented v1 CLI accepts native limits up to 120 seconds and
total loop limits up to 240 seconds. The small JavaScript recipe uses 90 and
120 seconds respectively. These are limits, not defaults for every development
task or an end-to-end wall-clock guarantee; preflight and caller checks have
their own timing boundaries.
The configuration schema version alone does not identify the installed
implementation or its supported budget limits.

A long-running frontend exercise used a separately reviewed local runtime with
expanded budgets. That observation does not establish delivery of those changes
on main. Do not copy its longer budgets into an older installed CLI or silently
extend an approved configuration. Identify the runtime and obtain the applicable
task and budget authorization first. A parent's observation window is separate
from the native execution deadline.

### Interpret each result at its own boundary

| Observation | What it establishes | What remains separate |
| --- | --- | --- |
| Successful preview | The supplied configuration passed preview validation; `execution_performed` is false. | Authorization, native execution and checker results. |
| Native `successful_exit` | The process exited successfully. | Final-output validity, protected inputs, scope and technical verification. |
| Run `status: pass` and `criteria_passed: true` | Mercury passed the registered contract and mapped verification criteria. | Required caller checks outside that configuration. |
| Overall local validation complete | The caller has applicable PASS evidence for all admitted required local checks and has inspected the current candidate's diff and scope. | Independent review clearance, publication, merge and delivery. |

Overall local completion is a caller decision recorded with evidence, not a new
CLI output field or a second review schema. A required FAIL, UNKNOWN or NOT_RUN
prevents that claim. Checks outside the admitted scope are not silently added as
new gates. Existing [review assessment](review-contract.md) remains distinct from
technical verification and from human permission to integrate work.

### Place checks deliberately

| Check | Existing execution boundary |
| --- | --- |
| Small behavioral comparison | Mercury's registered policy: baseline, development verification and distinct final verification. A failing baseline is comparison evidence, not implementation acceptance. |
| Required lint, types, build or browser checks | Caller-owned completion after the native outcome, once per admitted check or through applicable reuse. These are not automatically run by the CLI. |
| Previously passing checks | Reuse under WORKFLOW when relevant contents, base, tool and environment remain applicable; assess affected unchanged consumers. |
| Changed or failed checks | Renew only the admitted failed/affected checks, preserving their original evidence and attempt counts. |

For example, passing component tests in a frontend Mercury configuration does
not establish that lint, a production build or browser navigation passed. List
those additional required outcomes before the task starts and retain their
individual results before claiming overall local completion.

Do not append every expensive check to `verification.commands` merely to obtain
one broader PASS label: the existing loop reserves those commands across its
baseline, development and final phases. The v1 configuration has no separate
caller-completion command list or final-only stage. This recipe documents the
caller boundary; implementing such a feature needs a separate product contract.

### Keep a small completion record

Use the caller's existing report or ledger, with one entry per required check.
This is a record-content checklist, not a new machine-validated schema.

| Record | Required meaning |
| --- | --- |
| Check and owner | Its criterion, selected files/cases and Mercury or caller-owned phase. |
| Invocation | Exact trusted executable and arguments, working directory, relevant environment and prepared prerequisites. |
| Limits and expectation | Timeout, stopping condition and expected result; expected case selection where the test tool supports it. |
| Observed evidence | Actual outcome and original receipt/output reference, with the tested content identity. Distinguish command invocations from executed test cases. |
| Applicability | Current candidate identity, changed inputs and affected consumers; explain why earlier evidence applies or which check must be renewed. |

A Git HEAD alone does not identify an uncommitted candidate. Keep the applicable
content snapshot/diff with the report. Reuse a check for its relevant unchanged
inputs without presenting an old result as a new execution on every changed
file. Unknown facts stay UNKNOWN; a recorded timestamp is not an invented event
time, and missing evidence is not an unused zero counter.

Keep file edits, individual command execution and report generation as separate
operations rather than one combined string program. Prepare dependencies before
the admitted check. For a test command, a result such as `No tests found` does
not establish the intended case's PASS, even though a command was invoked.
Use the installed tool's selection semantics and preserve the original selector
failure; do not broaden to the full suite or retry automatically to hide it.
For non-test commands, use their admitted result contract rather than inventing
a test-case count. Follow the existing workflow and scoped authority when a
preparation error, failed check or timeout requires stopping.

## A small JavaScript repository onboarding recipe

Start with one disposable, committed repository and the existing
[JavaScript example](../examples/javascript_quickstart.py). Reuse its small
`add.mjs` and protected `check.mjs` contents, with no packages or framework. The
example's `--approve-demo` command prepares a fixture and immediately requests
execution; it is a live demo, not a preview-only setup command. For this recipe,
create the two files yourself from its `BUG` and `CHECK` constants and commit
them locally. Do not execute that demo just to produce preview input.

Choose trusted absolute Node and Claude executable paths outside the disposable
repository. Keep a new external state root and the configuration outside the
repository, with neither directory containing the other. Record the repository's
full current commit hash using a trusted Git executable.

For one checker command and one completion criterion, the following Python
recipe defines each once and reuses it in the existing configuration schema.
Adapt the goal, paths, checker arguments, criterion and limits to the admitted
task. Supply the reviewed full HEAD explicitly; this recipe never replaces it
with a live Git query. Prepare the configuration's parent directory separately.
Replace every sample path and the HEAD placeholder before running this code
with the trusted absolute Python 3.12+ interpreter from the prepared Mercury
installation, using `-I -B`, outside the target repository. This recipe imports
internal helpers from that reviewed installation; their names and behavior are
version-dependent. Use the implementation documented here, not an arbitrary
older installation or a copied source directory.

Preparation below performs bounded, read-only Git root and metadata queries
before creating the configuration. The helpers select the trusted system Git,
remove inherited `GIT_*` selectors, and limit each query to 10 seconds. A failed
query stops preparation before the write. These queries bind paths; they do not
read or replace the explicitly reviewed `expected_head`.

```python
import json
import re
from pathlib import Path

from agent_harness.repository import (
    _is_within_directory,
    resolve_repository_root,
    resolve_repository_state_location,
    validate_runtime_state_directory,
)
from agent_harness.run_config import _absolute

repository = Path("/work/addition")
state_root = Path("/work/addition-state")
config_path = Path("/work/configs/addition-run.json")
expected_head = "REPLACE_WITH_REVIEWED_FULL_GIT_HEAD"
criterion = "addition handles positive, negative and zero inputs"
command = ["/trusted/bin/node", "check.mjs"]

if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", expected_head) is None:
    raise ValueError("Supply the reviewed full Git HEAD")
for path in (repository, state_root, config_path):
    _absolute(str(path))
if not repository.is_dir() or not config_path.parent.is_dir():
    raise ValueError("Prepare the repository and configuration parent first")
if resolve_repository_root(repository) != repository:
    raise ValueError("Use the actual Git working-tree root")
if _is_within_directory(repository, state_root) or _is_within_directory(
    state_root, repository
):
    raise ValueError("Repository and state root must be separate")
if _is_within_directory(config_path, repository) or _is_within_directory(
    config_path, state_root
):
    raise ValueError("Configuration must be outside repository and state root")
resolve_repository_state_location(repository, state_root)
validate_runtime_state_directory(config_path.parent, repository)

config = {
    "schema_version": 1,
    "repository": str(repository),
    "expected_head": expected_head,
    "state_root": str(state_root),
    "backend": "claude",
    "executable": "/trusted/bin/claude",
    "contract": {
        "goal": "Fix add.mjs to add two numbers; preserve check.mjs",
        "allowed_paths": ["add.mjs"],
        "protected_paths": ["check.mjs"],
        "completion_criteria": [criterion],
    },
    "verification": {
        "commands": [command],
        "working_directory": ".",
        "timeout_seconds": 5,
    },
    "criterion_commands": {criterion: [command]},
    "context_paths": ["add.mjs", "check.mjs"],
    "budgets": {
        "timeout_seconds": 90,
        "input_bytes": 16384,
        "stdout_bytes": 65536,
        "stderr_bytes": 16384,
        "final_bytes": 8192,
    },
    "total_timeout_seconds": 120,
}
raw = json.dumps(config, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
with config_path.open("x", encoding="utf-8") as output:
    output.write(raw)
print(config_path)
```

The only write is creation of the requested configuration file. Exclusive
creation (`"x"`) fails if that path already exists, including a symlink or hard
link; choose a fresh path without overwriting the earlier configuration.
If writing fails, retain any partial file and inspect the error before choosing
another path. These path checks are point-in-time observations. The shared
helpers compare existing ancestor identities, including case aliases on
case-insensitive filesystems, without folding distinct case-sensitive names.
They also reject state and configuration storage inside the repository's actual
Git directory or common Git directory, including external metadata locations,
before opening the configuration. They do not prevent concurrent path changes.
The shared Mercury parser still validates the full configuration and protected
checker inputs during preview. The recipe does not create state, run a checker or
agent, grant approval, or invoke preview automatically.

Preview the created file separately using the first command, substituting its
absolute path. Review the exact protected checker,
allowed edit, criterion mapping, native executable, expected commit and budgets.
Only after separate authorization, run the second command with the reviewed
digest. This requests one Claude turn, zero retries, a 90-second native timeout
and a 120-second loop budget; it does not install dependencies. Authentication
remains external. The Claude backend uses restricted settings sources and does
not inherit user/project hooks or model preferences; trusted managed policy and
external authentication remain applicable. Retain the external state and inspect
the JSON outcome and historical status before deciding on further work.

## Execution diagnostics after an approved Codex turn

New Codex turns retain optional `diagnostics` on the saved `turn_observation`
event, including nonzero exits, timeouts and launch failures. This is separate
from the existing `process` metadata and CLI text omission. Older/custom adapters
may omit diagnostics; Claude keeps its existing behavior. Without diagnostics, the detailed
process milestones and filtered Codex event metadata described below are
unavailable. Historical inspection can still report a consistently recorded
terminal outcome and, for a consistent terminal PASS with a valid recorded
[review binding](review-contract.md), return that binding.

Milestones use monotonic elapsed seconds from launch start: process creation
returned, complete or incomplete stdin delivery, first/last received output per
stream, observed EOF, process exit, timeout and cleanup. Null means unobserved;
closing a pipe during cleanup does not establish EOF. Stdin completion means the
harness wrote all input bytes, not that the native agent accepted or processed
them. Cleanup completion means the bounded cleanup routine ended, not proof that
all descendants were recovered. These stages describe harness observations, not
native reasoning or service latency, authentication, model identity or billing.

Only fixed allowlisted Codex event metadata is kept with observation elapsed
seconds: event types, item kinds, status enums, bounded lexical error hints,
terminal command exit codes and bounded tool-limitation self-reports. See
[diagnostic interpretation limits](validation-visibility.md) for the newer hint
fields. Commands, arguments, prompts, reasoning/message text, URLs, paths,
session/item IDs, provider payloads and dynamic tool names are excluded. The stream parser retains at most 32 events and at most 16,384 pending
line bytes, independent of raw stdout capture limits. It drops invalid/unknown
lines and oversized lines, ignores unapproved fields, and counts dropped lines,
oversized lines and events beyond the retention cap. `events_truncated` identifies
event-cap loss. A valid bounded final fragment can be retained without a newline;
this does not invent an EOF observation. Reported native events remain untrusted
and establish neither approval nor technical PASS.

Clock diagnostics report `monotonic_elapsed_seconds`, `wall_span_seconds` and the
signed `discrepancy_seconds` (wall span minus monotonic elapsed). Absolute
discrepancy at most one second is `consistent`; larger positive or negative
values are `discrepant`. Missing/invalid spans are `unknown`. Clock status is
reporting only: existing timeout decisions still use the monotonic clock. It does
not repair earlier receipts or explain historical clock disagreements.
