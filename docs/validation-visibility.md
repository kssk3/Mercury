# Historical validation visibility

For a repeatable handoff in another project, see [the reporting procedure](#reuse-in-another-project)
and the reusable [current record template](verification-record-template.md).

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
The explicit external-receipt option below additionally validates its fixed JUnit
artifact; this does not verify historical native output payloads.

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

## Provenance projection and internal evidence navigation

Only `status --explain` adds `explanation.provenance`. The default status schema,
historical conclusion, readiness, comparison and exit behavior remain unchanged.
The projection uses the same bounded validated state/journal pair and already
captured configuration receipt. It does not acquire any additional files.

The ownership section identifies saved Mercury journal/process metadata and
Mercury checker metadata separately from external operator checks and independent
reviews. These ownership values name expected sources; separate
`record_availability` fields identify whether saved process/checker metadata was
actually observed. A target alone does not establish a process receipt.
Independent reviews remain unconnected. External command receipts are ingested
only when explicitly supplied, as described below. Configured backend names
are configuration facts, not proof of native launch, real model identity or tool
use. Native launch count, model identity, execution timestamps and environment
identity remain null because the saved contract does not establish those facts.
The report inspection timestamp is never substituted for an execution timestamp.

Targets retain each attempt's repository root/id, before and target revision,
recorded candidate digest where available, contract/context digests, process
metadata, checker result and exact command hashes/results. Caller comparison
applies to the active target only; earlier attempts retain no requested comparison.
No results are combined across attempts. None establishes current verification.
Legacy missing content identity remains null; invalid histories expose no targets.

Evidence cards identify captured current/journal/config paths and SHA-256 digests.
A captured but mismatched configuration remains captured evidence with its failed
binding visible, not evidence readiness. Invalid histories expose unavailable
cards rather than partial unvalidated facts. Output identifiers are explicitly
recorded identifiers whose payloads were not read, unlike digests computed from
captured input bytes. Neither kind authenticates the source.

The Korean standalone report prominently displays ownership, verification targets
and evidence. Header navigation reaches each section directly. Ownership facts
use two columns on desktop and one column on mobile; secondary unavailable
execution identity fields remain available in a native disclosure. Fixed internal anchors connect cards and zero-based journal event
indices to their exact saved attempt. Every arbitrary path/URI remains escaped,
selectable text. Rendering performs no I/O, adds no resources, and preserves the
exact supplied snapshot in its existing escaped JSON disclosure. Links and native
disclosures have visible keyboard focus; long values wrap within the existing
16px responsive layout. This is navigation within one report, not an artifact
exporter.

## Explicit external command receipts

The trusted Python caller may collect **one already-authorized check command**:

```python
from pathlib import Path
from agent_harness.external_checks import collect_external_check
from agent_harness.policy import VerificationPolicy

repository = Path("/absolute/path/to/repository")
bundle = Path("/absolute/path/to/external-evidence/fresh-check")
policy = VerificationPolicy(
    (("/absolute/path/to/external-environment/bin/python", "-m", "pytest",
      "tests/test_example.py", "--junitxml=" + str(bundle / "result.xml")),),
    working_directory=".", timeout_seconds=60,
)
receipt = collect_external_check(
    repository, policy, bundle,
    actor="Local operator", check_label="Example unit checks",
    criterion="Expected example behavior",
    safe_command_display="python -m pytest tests/test_example.py --junitxml=[bundle]/result.xml",
    protected_paths=(repository / "protected-input",),
)
```

The parent must exist; the bundle must be fresh, exclusive, outside the repository,
its actual Git metadata and explicit protected paths, with no symlink ancestors.
The collector rejects a terminal repository symlink and any repository, bundle
or protected path combining symlink components with parent traversal before
normalization, executable binding or bundle creation. Ordinary unambiguous parent
traversal remains supported. These point-in-time guards do not confine concurrent
filesystem changes.
The check owns `result.xml`, which must not exist before launch. There is no new
execution CLI. The caller owns authorization, command arguments, privacy review of
the display string, checker environment and declared protected paths. The existing
trusted executable binder and controlled runner retain executable proofs, loader
isolation, bounded output capture and no retry. This is not process confinement.

`intent.json` precedes launch. `process.json` preserves the observed UTC times,
monotonic duration and process outcome before result/target observation and terminal
`receipt.json`. Interruption or failed terminal persistence leaves the intent and
any completed process record in place. No reader starts recovery or runs a command;
an incomplete receipt is unavailable. Failed writes are not successful collection.

Receipts retain command SHA-256 for the actual selected executable plus arguments,
selected tool path/hash, cwd, before/after repository observations and candidate
digests, and explicit protected-entry fingerprints (including ignored content
under those entries). Raw argv, stdout/stderr and environment values are not
persisted. The display is explicitly caller-provided, not proof of exact arguments.
Hashes detect byte differences under trusted acquisition; they do not authenticate
the operator or establish human approval. Files and XML reads are bounded to4MiB;
explicit protected observations are bounded to10,000entries per path.

The strict JUnit subset counts testcase outcomes and checks declared aggregate
counts. It rejects duplicate cases, contradictory/nested outcomes, DTD/entities,
malformed XML and unsupported structures. Pytest JUnit is supported; the Vitest
JUnit shape has synthetic compatibility coverage only. No real Vitest execution is
claimed. Counts do not prove the criterion's semantics. Exit0with missing/invalid
XML isUNKNOWN; failing assertions areFAIL; any skipped case preventsPASS; timeout
remainsTIMEOUT even with partial passing XML. Process and test results remain
separate. A changing candidate/protected target prevents an applicable success.

The **original XML remains checker-owned** and can contain private test names,
messages or captured output. It is not copied into receipt/HTML. Only normalized
counts, fixed artifact filename, hash and its local path are projected. Restrict
access to the entire bundle as appropriate before sharing; no scrubbed export or
portable packaging is implemented.

Connect receipts to the same historical report without changing its conclusions:

```sh
local-harness status --state-dir /absolute/path/to/external-state --explain \
  --external-check /absolute/path/to/external-evidence/fresh-check/receipt.json \
  --format html > report.html
```

`--external-check` is repeatable (up to32read records) and requires `--explain`.
The reader validates fixed `intent.json`, `process.json`, `receipt.json` and
`result.xml` siblings without following paths stored inside metadata. It refuses
changed, missing, truncated, oversized, symlinked or hardlinked inputs. An invalid
receipt is projected as unavailable/UNKNOWN, while historical status/diagnostics,
readiness and exit behavior remain unchanged. Without this option the explanation
schema remains unchanged and external ownership stays `not_ingested`.

Historical applicability requires unchanged before/after content and protected
entries, matching repository root, revision and saved candidate digest. A different
target is marked mismatch; missing historical binding is unavailable. Matching is
historical identity comparison, never current freshness, native launch, review or
approval. Different commands/attempts are never aggregated into one passing run.
The pure Korean renderer adds external cards and a fixed internal header link only
when records were supplied, escapes every value, and performs no reads or execution.

## Read-only walkthrough from a Mercury checkout

Use an already installed Mercury environment and an existing state directory
from a previously admitted execution. This walkthrough does not create an
execution, run a checker, collect a new receipt or start a native agent. The
commands below are examples, not results from a new verification run.

From the Mercury checkout, replace both absolute placeholder paths with the
repository and state that belong to that saved execution:

```sh
mercury_cli="$PWD/.venv/bin/local-harness"
task_repository=/absolute/path/to/app
saved_state=/absolute/path/to/app-evidence/native-state

"$mercury_cli" status --state-dir "$saved_state"
"$mercury_cli" status --state-dir "$saved_state" --details
"$mercury_cli" status --state-dir "$saved_state" --explain
```

The first command shows the recorded summary. `--details` adds saved process,
scope and verification metadata; `--explain` presents evidence readiness,
blockers and the next actor/action. These modes are alternatives and cannot be
combined. The reader does not establish that the current repository still
matches the record. Add `--config` or expected identities only when you retain
the corresponding original inputs; see the comparison contract above.

A readable recorded failure still exits 0. Invalid/unreadable native history
remains UNKNOWN and exits 1; invalid CLI usage exits 2. Read the recorded outcome
and evidence fields rather than interpreting exit 0 as a passing task. To print
standalone HTML to stdout instead of JSON, use:

```sh
"$mercury_cli" status --state-dir "$saved_state" --explain --format html
```

For files that can be opened together, select a new report destination outside
the repository, state and protected inputs. Its immediate parent must already
exist. This optional command reads the original evidence and explicitly writes
only the new bundle; it is not a filesystem-read-only operation:

```sh
fresh_view=/absolute/path/to/app-evidence/views/new-read-only-report
"$mercury_cli" report --state-dir "$saved_state" \
  --repository "$task_repository" --output-dir "$fresh_view"
```

The resulting `explanation.json`, `report.html` and `record.md` share one captured
explanation. Open `report.html` locally; the page needs no server or network.
Two separate `status` calls can have different capture times. Export completion
also does not imply PASS; use the [report exit-code contract](#explicit-fresh-report-bundle).
If receipt bundles already exist, the explicit `--external-check` option can
include them without collection. No receipt path is inferred or searched here.
Original local paths can remain in all views, so review contents before sharing.

## Reuse in another project

Use a Mercury checkout that contains the external-receipt feature, with its locked
environment installed as described in [the quickstart](quickstart.md). A PR or an
older installation does not establish that a user has this implementation. Keep
the exact Mercury revision and, for uncommitted code, a separate content manifest.
The Python package version alone cannot identify an uncommitted implementation.
These instructions and the template are repository files; the current wheel
packages `src/agent_harness`, not the repository documentation.

### Collect once, then read the same evidence

1. Name the project revision/content, behavioral criterion, operator and authorized
   check. Collect it with the Python API above. Each check uses a fresh bundle;
   reporting does not authorize a command, retry or native agent execution.
2. Retain the complete original check bundle and existing Mercury state. Keep
   evidence outside the checked repository, Git metadata and protected inputs.
   For example, use sibling `app/` and `app-evidence/` directories. State and check
   receipt paths still identify the original local locations; copying files to
   another machine is not a supported portable migration.
3. Capture one explanation dictionary, save it as JSON and render that exact
   dictionary as HTML. The example below only reads existing execution/check
   records. Replace the paths and use the Python environment for the selected
   Mercury checkout. The parent `views` directory must already exist; `check-001`
   must be a new name outside the project and protected locations.

```python
import json
from pathlib import Path

from agent_harness.explanation import explain_execution
from agent_harness.html_report import render_execution_html

snapshot = explain_execution(
    "/absolute/path/to/app-evidence/native-state",
    external_checks=(
        "/absolute/path/to/app-evidence/checks/check-001/receipt.json",
    ),
)
view = Path("/absolute/path/to/app-evidence/views/check-001")
view.mkdir()
with (view / "explanation.json").open("x", encoding="utf-8") as stream:
    json.dump(snapshot, stream, ensure_ascii=False, indent=2)
with (view / "report.html").open("x", encoding="utf-8") as stream:
    stream.write(render_execution_html(snapshot))
```

This is caller-owned sample code, not an atomic export transaction. The explicit
`report` command below adds bounded fresh-output validation. If a later write fails, retain the original records
and any completed view; use a new view name for a later attempt. Output-path
selection remains the caller's responsibility. No evidence files are moved.

The existing CLI can instead print JSON or HTML with `status --explain`. Two
independent CLI calls capture two independent inspection snapshots. To reproduce
one HTML view exactly, retain its standalone JSON and the exact renderer source;
read that JSON and call `render_execution_html` without calling `explain_execution`
again. Keep any view's digest alongside its generator identity when exact bytes
matter. The renderer does not calculate a new inspection time.

### Maintain one current Markdown record

Copy the [record template](verification-record-template.md) to a local `current.md`
outside the application and shared product patch. Fill it from the saved facts;
Mercury does not fill it automatically. Link to the original bundle, standalone
snapshot and HTML instead of copying raw logs or manually aggregating test totals.
Use an outcome/check ID as well as a date. Add a dated entry for a feature result,
material failure, decision or handoff, rather than creating a note every day.
Preserve the previous view before replacing facts needed for recovery or audit.

Keep execution, verification and delivery separate. A process exiting zero does
not establish passing assertions. A readable `status` command exiting zero does
not establish verification success; its exit code reports history readability.
An external PASS does not change native UNKNOWN, establish current applicability,
or count as an independent review, approval, publication or merge. Inspect the
reported outcome, test counts, subject/protected changes and applicability.

### Share the feature, retain each user's records locally

| Material | Shared product change | Local project record |
| --- | --- | --- |
| Collector, readers and HTML renderer | Yes, after the applicable delivery gates | Installed selected version |
| This guide and blank Markdown template | Yes | Optional local copy |
| Original state, intent/process/receipt, raw XML | No automatic upload | Preserve complete attributable bundles |
| Current Markdown, snapshots and generated HTML | No automatic upload | One current view plus linked evidence |
| Screenshots | Only deliberately selected, privacy-reviewed examples | Viewport/state evidence when UI inspection is needed |

The same HTML can be opened without a server, JavaScript or remote assets. It can
still expose project paths, operator labels and identifiers. Raw JUnit may also
contain test names, messages or captured output. Review access and content before
sharing any record; the implementation is not a scrubbed exporter. A static HTML
file is a captured view, not a live dashboard or proof that adjacent files remain
unchanged. Hashes link bytes; they do not authenticate authors.

Retain unique snapshots, original failures and referenced evidence. HTML is
regenerable only while its standalone JSON and exact generator remain available.
Keep screenshots for a relevant UI state or defect; routine checks do not require
them. Age alone does not make a file disposable. Automatic collection at task end,
current-Markdown updates, screenshots and cleanup are not implemented.

## Explicit fresh report bundle

Create three files from one capture of already saved records:

```sh
local-harness report --state-dir /absolute/path/to/app-evidence/native-state \
  --repository /absolute/path/to/app \
  --external-check /absolute/path/to/app-evidence/checks/check-001/receipt.json \
  --protect /absolute/path/to/app-evidence/current.md \
  --output-dir /absolute/path/to/app-evidence/views/new-report
```

The output's immediate parent must exist and the output directory must be new.
The command writes exactly `explanation.json`, `report.html` and `record.md`.
One `explain_execution` capture feeds all three; rendering saved JSON with the
same renderer reproduces the HTML without re-reading history. The English
Markdown scaffold projects observed process/assertion/applicability facts and
escapes opaque recorded text. Non-identifier values use literal code spans with
delimiters longer than any embedded backtick run, keeping URL/email text as data.
The displayed source representation retains escaping; exact original values
remain in the lossless JSON. No specific Markdown viewer is executed by export.
Only fixed sibling links are generated. No passing total combines checks or
attempts. The record includes package version and an actual renderer-file SHA-256
where readable; neither establishes clean Git provenance or author authentication.

`--external-check` is repeatable up to 32 paths. `--protect` is repeatable for
caller-declared entries. Optional `--config`, `--expected-revision` and
`--expected-candidate-sha256` retain the existing historical comparison semantics.
The explicit repository selects output protection; it does not establish that
all supplied historical records describe that repository or are currently fresh.

Before writing, the command resolves the actual Git working-tree root and checks
its actual Git/common metadata, input state directory, supplied config, full
external-receipt parent bundles and explicit protected paths. Output overlap in
either direction, symlink ancestors, existing destinations and invalid parents
are refused. Input/output selections combining `..` with a symlink component are
rejected before lexical normalization can hide the actual selected location;
ordinary relative paths without that ambiguity remain allowed.
The selected repository itself must not be a terminal symlink; choose its actual
working-tree path instead. This report-only refusal does not change shared
repository resolver behavior.
It reuses trusted bounded Git queries only for these boundaries;
no native/checker command, network, retry, approval or recovery is triggered.
Path checks are point-in-time checks, not concurrent-path confinement.

The directory and fixed files are created exclusively. If a later write fails,
completed partial files remain, originals stay in place, and no completion
summary is printed. There is no rollback, automatic retry, atomic bundle
transaction or power-loss durability claim. Use a new destination for a later
attempt. Existing current Markdown is never updated or promoted.

| CLI exit | Meaning |
| --- | --- |
| `0` | Bundle completed and original history was readable; a recorded failure can still have this code. |
| `1` | Bundle completed with native-history diagnostics/UNKNOWN; external PASS cannot promote it. |
| `2` | Invalid selection or failed generation; no completion summary. Partial output may remain. |

The JSON stdout summary describes generation, not test success. Review actual
saved execution/assertion outcomes in the bundle. Manual rationale, independent
review, approval and delivery remain explicitly unrecorded; the exporter does
not invent them. Original paths and operator identifiers can remain sensitive;
this is a local projection, not a scrubbed or portable sharing package.
