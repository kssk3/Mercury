# One real python_study integration

This bounded example applies one approved addition-CLI task to a retained,
separate Git copy of python_study revision
`f990af301da73f66c0482911a6cefca2234ce09c`. It never includes the source
worktree's uncommitted or untracked files. The source remains untouched.
Review [the example](../examples/python_study_example.py) before approval.
This is a known-repository example, not a general repository importer or a
new `run` command. Existing [native configuration requirements](codex-quickstart.md)
apply unchanged; configured model and reasoning effort remain selected.

Only `chapter09/03.py` may change during the native task. Exactly two integer
arguments must print their sum and newline to stdout and exit zero without
stderr. Invalid argument counts or noninteger input must emit nonempty stderr
guidance, no stdout or traceback, and exit two. No exact guidance wording is
required. The seven protected cases cover positive, negative and zero sums,
zero/one/three arguments, and a noninteger.

## Preparation and execution

Use explicit absolute paths for the source, fresh copy, fresh external evidence,
trusted check Python and trusted native Codex. From Mercury's root:

```sh
.venv/bin/python examples/python_study_example.py --approve-python-study --prepare-copy --source /absolute/python_study --working-copy /absolute/owned-copy --evidence-directory /absolute/private-evidence --check-python /absolute/python3.13
```

The opt-in precedes any copy or native call. Preparation checks the exact source
revision and copies committed content with `git clone --no-local --no-hardlinks`.
It uses fresh Git metadata, disables inherited system/global Git configuration
and hooks, removes the copied remote, checks the original tree, and commits only
the new protected checker. Source and prepared SHAs are recorded separately.
Copied native settings, agent instruction files, and nonregular tree entries
are refused. Existing user settings are not rewritten. Preparation does not
run the target or a model. Existing output paths are refused instead of reused.

After inspecting preparation and checking the external supervisor, execute once:

```sh
.venv/bin/python examples/python_study_example.py --approve-python-study --source /absolute/python_study --working-copy /absolute/owned-copy --evidence-directory /absolute/private-evidence --check-python /absolute/python3.13 --codex-executable /absolute/trusted/codex
```

The one model-free metadata probe retains the existing ten-second configuration
gate. Unsupported provider/authentication environment overrides are refused,
not silently removed. The real attempt has 120 seconds and no retry or resume.
The protected stdlib checker runs each CLI subprocess with a one-second timeout;
one required command runs for baseline, development and final, each with ten
seconds. Exit one from the baseline means only the new invalid-input contract
failed while all three normal sums passed. Environment/timeout/normal-case
failures use exit three and cannot establish the expected baseline. Native
success text cannot establish correctness or authorize protected changes.

The loop uses `RetryLimits(0, 1, 4, 200)`, a 300-second lease released in `finally`,
an 8,192-byte source/checker context, a 16,384-byte prompt envelope, and bounded
65,536/16,384/8,192-byte native stdout/stderr/final retention. Native internal
commands, requests, tokens and cost are not capped by these counts. Model-free
metadata does not prove which model served every internal request.

The command itself is not an outer watchdog. Live validation requires the
approved external supervisor with an actual 240-second execution limit and
at most 20 seconds of cleanup. Because the adapter starts new sessions, killing
only the caller's process group is insufficient. The supervisor observes owned
descendants and terminates their observed groups; snapshot discovery cannot
promise to find every short-lived escaped descendant. This is not a sandbox
against a hostile native executable.

## Retained evidence

The working copy remains on success or failure for human diff review. External
private evidence holds preparation provenance, `summary.json`, `target.patch`,
`private-native.json` and `state/`. Native output may contain sensitive data;
do not publish these private receipts. The summary contains phase facts and
read-only historical status, not raw native text. The [status CLI](quickstart.md)
reads recorded history; it does not validate the live workspace or grant approval.

Success requires actual loop/criterion PASS, clear scope and protected inputs,
and the expected baseline contract failure. Any failed required gate stops the
work; do not retry automatically. The example never commits the native change,
pushes, opens a PR, merges, installs dependencies, logs in or modifies user rules.
Any later publication or integration requires separate human authority.
