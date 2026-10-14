# Single-agent API quickstart

This developer preview requires Python 3.12+, Git, and a local macOS environment.
Licensed under the [MIT License](../LICENSE).
The example demonstrates API composition with a **simulated Codex
adapter**. It performs no native-agent, model, or network invocation.

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
and retained evidence. Arbitrary repositories still require caller integration.

## Using a real repository

The example's subclass replaces `CodexCLIAdapter.run` entirely. A real
integration must supply its own trusted executable/environment and genuinely
admitted task, verification policy, protection set, redactor, budgets, external
state, and caller-held lease. The real adapter inherits native configuration,
authentication, rules, and environment. No credential setup or live execution
is part of this quickstart.

Allowed paths guide the agent; observations are point-in-time scope checks,
without hostile-process confinement. Ignored untracked files and Git internals
are outside aggregate file observations. Protected inputs are caller-selected.
State writes and journal ordering provide recovery evidence without an
fsync/power-loss durability guarantee. Explicit verification continuation is
available for supported checkpoints; automatic native-session resume is absent.

The CLI offers `--help`, `--version`, `profile [path]`, and the read-only
`contract` preview below, with no task-run command. Runtime multi-agent orchestration and messenger control are outside
this preview. See [development checks](../CONTRIBUTING.md).


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
Arbitrary-repository execution still requires Python caller integration.
