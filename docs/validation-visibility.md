# Historical validation visibility

Use the installed CLI to explain one existing execution without starting work:

```sh
.venv/bin/local-harness status --state-dir /absolute/path/to/external-state --explain
```

The existing default `status` JSON and exit semantics are preserved. The optional
view adds an `explanation` object to the same historical summary. It reads one
bounded, validated current/journal byte pair; it does not re-open those files to
compose another view. A file changed after capture does not replace the captured
bytes. This is a point-in-time read, not an OS sandbox or an authenticity proof.
The existing 1 MiB current, 8 MiB journal and 10,000-event bounds still apply.

Supply the original configuration to resolve criteria to saved command hashes:

```sh
.venv/bin/local-harness status --state-dir /absolute/path/to/external-state \
  --explain --config /absolute/path/to/original-run.json \
  --expected-revision FULL_RECORDED_REVISION \
  --expected-candidate-sha256 RECORDED_CONTENT_SHA256
```

Configuration bytes are bounded to 64 KiB. Their raw SHA-256 must match the saved
run binding. The task contract hash must match the attempts before the existing
pure contract, verification-policy and criterion-mapping validators connect
criteria to exact commands. This is a historical subset inspection, not full
execution preflight or configuration approval; repositories, checkers, native
executables and context files need not remain available. No command argv or
native output payload is emitted. A missing, changed, invalid or unbound config
leaves coverage unavailable and preserves the recorded execution conclusion.

`--config` and `--expected-*` require `--explain`. Expected revision/content values
are caller-provided comparison context. A match does not capture or verify the
current workspace. A mismatch or unavailable identity prevents evidence readiness;
it never changes a historical pass into a current verified pass. Older records
without a content binding remain explicitly unavailable.

| Field | Meaning |
| --- | --- |
| `recorded_status` / `recorded_reason` | Existing validated execution outcome |
| `explanation.conclusion` | Fixed conclusion: recorded non-pass, evidence incomplete, or review pending |
| `evidence_readiness` | Historical inputs ready for manual review, or incomplete; never approval |
| `blockers` / `next` | Evidence gaps and next caller action |
| `observed_at` / `inspector_version` | Inspection timestamp and installed package version, not execution time |
| `comparison` | Context-only revision/content comparison |
| `config` | Config binding status and available original-byte digest/path |
| `criteria` | Pure criterion-command mapping and each attempt's saved command outcomes |
| `attempts` | Separate attempt identities, native process metadata, checker result, command hashes and zero-based journal event indices |
| `review` | `assess_review` exists as a pure API but is implemented and unwired here |
| `approval` | Manual and not recorded; no inferred reviewer or merge approval |

Criterion saved outcomes distinguish pass, fail, timeout, unknown and unverified
coverage. They describe saved command metadata, not a new semantic proof of the
criterion. Outcomes from different attempts never combine into passing coverage.
Native process success is separate from checker success and evidence completeness.
Attempt details validate exposed observation/process/verification identities and
metadata, but do not replay all unexposed journal-event semantics. Their evidence
state remains recorded metadata, not authentication of past execution.
An execution can have recorded pass while its explanation is evidence incomplete.

Readable historical failures and evidence gaps keep exit code 0, like default
status. Invalid/unreadable history emits unknown with diagnostics and exit code 1;
CLI syntax errors use exit code 2. The explanation never creates a lock, journal,
receipt, retry, review, approval or merge. It does not verify output payloads,
authenticate records, invoke recovery, capture a repository or start subprocesses.

`inspector_version` identifies the installed package version, not uncommitted
implementation bytes. The caller must retain a separate frozen candidate manifest
and range diff when exact implementation provenance is needed; the read API does
not query Git or manufacture that evidence.

## Standalone Korean HTML report

Export the same explanation snapshot explicitly to stdout:

```sh
.venv/bin/local-harness status --state-dir /absolute/path/to/external-state \
  --explain --config /absolute/path/to/original-run.json --format html > report.html
```

The shell redirection is the caller's explicit file write. Mercury does not create
an output file, service or server. Open the local file in a browser. `--format html`
requires `--explain`; the default format remains JSON, including exit semantics.
The HTML path calls `explain_execution` exactly once, and the pure
`agent_harness.html_report.render_execution_html` consumes that same dictionary.
It does not re-read state/config, compute a new conclusion/time, or add authority.

The Korean report separates historical outcome, evidence readiness, blockers and
next actor/action. Native disclosures show criteria, individual attempts, evidence,
comparison/config bindings and limits. Long identifiers wrap on narrow screens;
keyboard focus and touch-sized disclosure targets support local reading. The
inspection timestamp is formatted from the saved value in KST; package version
still does not identify exact source bytes. These design choices are not a complete
accessibility conformance claim.

The original dictionary is retained as escaped JSON under a disclosure for precise
semantic comparison. All dynamic text is escaped. The self-contained page has no
JavaScript or remote resources, and its CSP forbids scripts, networking and external
assets. Evidence paths are selectable text, not execution links. There are no
execution, retry, approval or merge controls. Missing/corrupt/stale facts and manual/
unwired review states retain their original meaning. This local report is not a
new dashboard application or a publication/hosting workflow.

CI's existing Linux Pytest command uses `pytest -v -rs` to show each node outcome
and skip reasons. Only log verbosity changes; permissions, jobs, enforcement,
test selection and approval authority remain unchanged. New hosted results are
still required for a subsequently authorized publication.
