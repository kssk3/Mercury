# Review receipt contract

`agent_harness.review` provides frozen review records and the pure
`assess_review(report, expected, receipt)` function. It is a Python API foundation.
The loop records candidate/evidence identities through
`agent_harness.review_evidence`; historical inspection can present that binding.
Receipt assessment is not automatically enforced by the loop, CLI or GitHub.
The existing [project workflow](project/WORKFLOW.md) continues to govern the
actual development lifecycle.

## Trusted caller responsibilities

The caller supplies an existing trusted `EvidenceReport`, an expected
`ReviewBinding`, and a `ReviewReceipt` or `None`. The caller must establish:

- The admitted canonical repository root and full target Git revision.
- The attempt identity and the admitted contract's SHA-256 digest.
- A candidate SHA-256 digest representing the admitted snapshot or diff content,
  including relevant uncommitted content, rather than merely Git HEAD.
- A verification-evidence SHA-256 digest representing the supplied report and its
  relevant verification evidence, with appropriate provenance and freshness.
- Reviewer independence, report authenticity, finding dispositions and the
  correctness of resolution evidence. A reviewer ID alone proves none of these.
- Actual explicit human approval for deferred blocking findings. An approval
  reference records a claim; its presence does not authenticate that approval.

The module validates record shape and compares identities. It does not compute
digests, read report/evidence/approval references, authenticate claims or execute
their contents. It performs no filesystem resolution, preparation, process,
model or network calls, retries, persistence or finding-management workflow.

## Execution-produced binding

The bounded loop reuses the repository observations immediately before and after
its final verification phase. If they differ, it retains the existing blocked
outcome and does not record a final binding. A version-2 attempt verification
event stores a version-1 review-evidence record alongside the existing safe
command/protected-input/output metadata. Direct callers that omit final
observation inputs retain the legacy version-1 event contract.

The review-evidence record contains the observed candidate, the existing loop
verification identity, and a serialized `ReviewBinding`. Candidate SHA-256 covers
canonical sorted observed paths, file kinds, content hashes and permissions,
HEAD, index identity, status and symbolic HEAD identity. It includes tracked and
observed nonignored untracked content, including deletions; it does not expand
observation to all ignored files or Git internals. The contract hash remains the
hash of the admitted `TaskContract.to_json()` bytes.

Evidence SHA-256 covers that candidate identity and safe attempt verification
metadata: baseline/final command identities and process results, protected-input
facts, output identifiers and the loop verification identity. The latter binds
the existing admitted policy, executable selections, criterion mapping and loop
inputs. Raw command output is not added to these records. The deterministic
JSON encoding uses sorted keys, compact separators and an explicit version;
neither its resulting digest nor a later receipt is an input to its own hash.

`read_review_binding(verification, record)` recomputes and compares the stored
identities without resolving the current repository or opening output/report
references. Supply its returned binding, the trusted matching `EvidenceReport`
and an actual receipt to the unchanged `assess_review` API. Equality checks are
not authentication: a party able to replace every record can forge consistent
hashes. Direct producers still own actual final-phase provenance and admission.

Inspection exposes only a recorded binding after a consistent terminal PASS,
including the CLI wrapper's required post-release finalization. Legacy histories
retain their original technical outcome and report review binding unavailable;
missing or inconsistent version-2 binding records remain UNKNOWN. A wrapper
release/finalization failure withholds the binding rather than promoting UNKNOWN
to PASS. A successfully stored intermediate event alone is not a completed run.

Verification-only continuation records a binding only after its actual final
phase; it does not prepare or invoke an adapter again. Interrupted histories and
recovery evidence keep their existing handling. Historical binding output never
claims current workspace verification. A new admitted snapshot, attempt,
contract or verification evidence requires its own matching binding. Review
independence, report authenticity, finding dispositions and human approval remain
the caller responsibilities above. No reviewer launch or execution/merge
authority is added.

## Records and validation

`ReviewBinding` contains `repository_root`, `target_revision`, `attempt_id`,
`contract_sha256`, `candidate_sha256` and `evidence_sha256`. Roots must already use
absolute, lexically canonical current-platform path syntax: no parent traversal,
redundant separators, dot components or trailing separators. The root itself is
valid. The module does not resolve symlinks, aliases or filesystem case; the
caller must establish the canonical spelling before constructing the binding.
Git revisions are full lowercase hexadecimal SHA-1 or SHA-256 values (40 or 64
characters). Content/evidence/contract digests are 64 lowercase hexadecimal
characters. Identities and references must be plain, nonblank strings without
surrounding whitespace or ASCII control characters. String subclasses, including
string enums, are rejected at these boundaries.

`ReviewFinding` contains `finding_id`, an exact boolean `blocking`, and a plain
string `disposition`: `unresolved`, `repaired`, `rejected` or `deferred`.
Every resolved disposition requires `evidence_reference`; unresolved findings
cannot carry a resolution-evidence reference. A deferred blocking finding also
requires `human_approval_reference`. That approval field is reserved for deferred
blocking findings. These requirements validate consistency of the claims, not
their truth.

`ReviewReceipt` contains the binding, `reviewer_id`, `report_reference`, a plain
string state (`completed`, `running`, `failed` or `cancelled`), and an immutable
tuple of `ReviewFinding` records. Finding IDs must be unique within the receipt.
Empty findings are valid. All input records are frozen dataclasses; callers
create replacement records for changed facts rather than mutating existing ones.
Malformed records raise `ValueError` at construction. The module does not provide
a serializer, file loader or approval action.

## Assessment

`ReviewAssessment` is frozen and returns the original `report.overall_kind` as
`technical_kind`, a separate `review_kind`, a fixed diagnostic `reason`, and the
boolean `conditions_satisfied`. Using `overall_kind` preserves a protected-input
failure even when the report's underlying technical decision was PASS.

Assessment uses the following precedence; earlier mismatches cannot be hidden by
a completed receipt or a passing technical result:

| Supplied facts | Review kind | Reason | Conditions satisfied |
| --- | --- | --- | --- |
| Report root or revision differs from expected binding | `invalid` | `evidence_binding_mismatch` | False |
| Receipt absent | `incomplete` | `review_missing` | False |
| Receipt has an unsupported record type | `invalid` | `invalid_receipt` | False |
| Any receipt-binding component differs | `invalid` | `receipt_binding_mismatch` | False |
| Matching receipt is running, failed or cancelled | `incomplete` | `review_incomplete` | False |
| Completed receipt has an unresolved blocking finding | `changes_requested` | `blocking_findings` | False |
| Completed review otherwise clear; technical FAIL or BLOCKED | `clear` | `technical_not_pass` | False |
| Completed review clear and technical PASS | `clear` | `review_clear` | True |

Unsupported report or expected-binding record types raise `ValueError`. Root and
revision comparison uses the already-supplied report values, without resolving
filesystem paths. Every component of the expected binding must equal the receipt
binding. Thus a new attempt, changed contract, changed candidate content at the
same Git SHA, or changed verification evidence invalidates receipt reuse.
Nonblocking unresolved findings do not prevent clearance. Repaired or rejected
blocking findings with evidence, and deferred blocking findings with evidence
and an explicit human-approval reference, may permit clearance.

Technical PASS, review clearance, execution permission and actual delivery remain
separate facts. `conditions_satisfied=True` means only that the supplied technical
and review conditions agree. It grants no authority and proves no commit, push,
publication, merge or delivery. Callers retain all actual authorization and
integration gates and must not substitute unauthenticated references for them.
