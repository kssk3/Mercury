# A disposable real Codex example

This example calls your trusted native Codex CLI once to fix a discount
calculator in an owned temporary Git repository. It does not target Mercury or
accept an arbitrary user repository. Review [the code](../examples/codex_quickstart.py)
before approving it. The [offline simulator](quickstart.md) remains available
without an account or model call.

Use macOS/POSIX, Git, Python 3.12+, the installed Mercury package, and a trusted
Codex CLI already signed in using ChatGPT. From the repository root, prepare the
package as described in the [README](../README.en.md), then run:

```sh
.venv/bin/python examples/codex_quickstart.py --approve-codex-demo
```

Without `--approve-codex-demo`, the example exits before fixture creation or
native invocation. The flag approves this disposable task only. The example
checks `codex login status` as non-model metadata, refuses nonempty
`OPENAI_API_KEY` or `CODEX_API_KEY`, and requires saved ChatGPT authentication.
It never changes authentication, user configuration, or rules and never falls
back to another billing method. Existing settings and rules remain active.
`--codex-executable /absolute/trusted/codex` selects an external trusted program;
normal tests use a fake executable and never call a real model.

The fixture begins with a deliberately wrong discount formula. The protected
`check.py` checks `discount(100, 10) == 90`, `discount(200, 25) == 150`, and
`discount(0, 10) == 0`. Only `calculator.py` is allowed to change. Mercury runs
one required command, the current Python interpreter with `-B check.py`, before
the edit, after it, and again for distinct final verification. `-B` avoids a
workspace bytecode cache changing the observed scope. Scope, protection, checks,
and the mapped completion criterion determine success; exit zero and a model
message do not establish it. Initial FAIL is expected; successful development
and final checks must be PASS. Exit code zero means the loop and criterion pass;
other results return nonzero and retain their evidence.

## Budgets and limits

There is at most one native exec attempt, with no retry or resume. The native
turn has a 120-second timeout. Each check has a 5-second timeout. The loop has
four outer command boundaries and a 180-second reservation limit via
`RetryLimits(0, 1, 4, 180)`. The caller holds a 260-second lease and releases it
in `finally`. These values do not cap the native agent's internal commands,
model requests, tokens, or dollar cost, and do not guarantee a whole-process
observation deadline. An external watchdog may be appropriate for live
validation; it is not implemented by this example.

Context is limited to 4,096 bytes and the complete prompt envelope to 16,384
bytes. Retained native stdout/stderr/final text have limits of 65,536/16,384/8,192
bytes; state output storage has a 65,536-byte limit. Summary retention flags
report actual truncation. Native JSON token usage is reported when available;
otherwise it is `UNKNOWN`. No price or hard token cap is inferred. Saved
model/effort values are selective top-level configuration metadata; native model
events, when available, provide additional provenance. Configuration metadata
cannot prove which model served every request.

## Evidence and cleanup

Each run creates a fresh private evidence child directory outside the fixture,
with permissions 0700. By default it is under the OS temporary directory. To
retain it in another existing private directory, pass:

```sh
.venv/bin/python examples/codex_quickstart.py --approve-codex-demo --evidence-parent /absolute/private/directory
```

The printed JSON and `summary.json` contain status/reason, elapsed time,
configuration provenance, native process status/usage/retention, criteria,
changed paths, protection/scope facts, and baseline/development/final results.
Unavailable facts stay `UNKNOWN`. The external `state/` holds journal and current
state; native text is replaced with a fixed omission notice in stored state.
`private-native.json` retains bounded raw native stdout/stderr/final text for
private diagnosis. It may contain sensitive data. It is not a public safe
summary or a general secret detector. Auth output and keys are not stored.
Keep private receipts outside source control and inspect them before sharing.

The owned fixture repository is removed on normal completion and handled
exceptions. External evidence remains on both success and failure for review;
remove only that owned child when it is no longer needed. Existing parent
files and directories remain. Forced termination can leave temporary resources.
The task prompt discourages other agents/services/installs and confines intent
to the fixture, but does not establish filesystem confinement or prevent all
external writes. Ignored files and Git internals remain outside aggregate file
observation, as described in the [project limits](../README.en.md).
