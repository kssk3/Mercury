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
native invocation. The flag approves this disposable task only. Before creating
the fixture or evidence directory, the example uses one model-free
`codex --strict-config app-server` process to read effective configuration,
admin requirements, and account metadata (`config/read`,
`configRequirements/read`, and `account/read` with `refreshToken: false`).
It requires an explicit effective `openai` provider and a saved ChatGPT account.
It refuses custom provider definitions, selected or legacy profiles, nonstandard
endpoints/authentication, custom project-root markers, and managed requirements.
Missing, oversized, malformed, unreadable, warned-about, or unavailable
configuration is a refusal, rather than permission to assume defaults. This
deliberately narrow example does not resolve or support every Codex configuration.

Nonempty `OPENAI_*`, `CHATGPT_*`, and `CODEX_*` environment variables are refused,
except an absolute `CODEX_HOME`. This includes API keys, endpoint overrides,
external access tokens, and profile overrides; an arbitrary custom provider's
key is excluded by refusing its provider definition. The metadata process runs
from the filesystem root, where a project config is refused, and the execution
fixture has a fresh Git root without project configuration. The trusted CLI,
configuration, account and environment must remain stable during the run.

The example does not initiate login/logout or rewrite user configuration or
rules. The native CLI may refresh and persist credentials during normal use;
authentication bytes are not promised to remain unchanged. Existing accepted
settings and rules remain active. These checks do not create a new isolation
mechanism, and the example provides no billing fallback. See the official
[app-server protocol](https://learn.chatgpt.com/docs/app-server),
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference),
and [credential behavior](https://learn.chatgpt.com/docs/auth#login-caching).
`--codex-executable /absolute/trusted/codex` selects an external trusted program;
normal tests use a fake executable and never call a real model.

The fixture begins with a deliberately wrong discount formula. The protected
`check.py` checks `discount(100, 10) == 90`, `discount(200, 25) == 150`, and
`discount(0, 10) == 0`. Only `calculator.py` is allowed to change. Mercury runs
one required command, the current Python interpreter with `-B check.py`, before
the edit, after it, and again for distinct final verification. Explicit failure
checks remain active when Python optimization is enabled. `-B` avoids a
workspace bytecode cache changing the observed scope. Scope, protection, checks,
and the mapped completion criterion determine success; exit zero and a model
message do not establish it. Initial FAIL is expected; successful development
and final checks must be PASS. Exit code zero means the loop and criterion pass;
other results return nonzero and retain their evidence.

## Budgets and limits

There is at most one native exec attempt, with no retry or resume. The native
turn has a 120-second timeout. The separate metadata probe has one 10-second
deadline for the handshake and reads, a 262,144-byte response cap, and a
2-second reap limit after its process group is killed. No thread or turn is
started by that probe, and its raw responses are discarded. Any stderr output,
unexpected message, incomplete response or nonzero probe exit causes refusal.
The saved user configuration is also limited to 262,144 bytes. Each check has a
5-second timeout. The loop has
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
otherwise it is `UNKNOWN`. No price or hard token cap is inferred. The selected
model/effort values come from effective configuration metadata; native model
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
state. A failed development check is attributed to development, with final
remaining `UNKNOWN`. Starting final verification alone cannot establish a final
result; its completed report must be recorded and match the returned report.
Timed-out checks remain `UNKNOWN`. Native failures and interruptions
leave phases without attributable reports `UNKNOWN`. The external journal's
checkpoints and completed verification events supply these phase facts. Native
text is replaced with a fixed omission notice in stored state.
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
