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

The CLI offers only `--help`, `--version`, and `profile [path]`, with no task-run
command. Runtime multi-agent orchestration and messenger control are outside
this preview. See [development checks](../CONTRIBUTING.md).
