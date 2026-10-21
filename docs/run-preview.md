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
and fixed one-turn/no-retry limits. It always reports `approval_required: true`
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
full current commit hash using a trusted Git executable. Replace every sample
path below and the all-zero `expected_head` with those reviewed values, then save
this JSON at your external configuration path:

```json
{
  "schema_version": 1,
  "repository": "/work/addition",
  "expected_head": "0000000000000000000000000000000000000000",
  "state_root": "/work/addition-state",
  "backend": "claude",
  "executable": "/trusted/bin/claude",
  "contract": {
    "goal": "Fix add.mjs to add two numbers; preserve check.mjs",
    "allowed_paths": ["add.mjs"],
    "protected_paths": ["check.mjs"],
    "completion_criteria": ["addition handles positive, negative and zero inputs"]
  },
  "verification": {
    "commands": [["/trusted/bin/node", "check.mjs"]],
    "working_directory": ".",
    "timeout_seconds": 5
  },
  "criterion_commands": {
    "addition handles positive, negative and zero inputs": [["/trusted/bin/node", "check.mjs"]]
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

Preview this file using the first command. Review the exact protected checker,
allowed edit, criterion mapping, native executable, expected commit and budgets.
Only after separate authorization, run the second command with the reviewed
digest. This requests one Claude turn, zero retries, a 90-second native timeout
and a 120-second loop budget; it does not install dependencies. Authentication
remains external. The Claude backend uses restricted settings sources and does
not inherit user/project hooks or model preferences; trusted managed policy and
external authentication remain applicable. Retain the external state and inspect
the JSON outcome and historical status before deciding on further work.
