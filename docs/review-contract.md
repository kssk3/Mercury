# Review receipt contract

`agent_harness.review` provides frozen review records and the pure
`assess_review(report, expected, receipt)` function. It is a Python API foundation.
It is not integrated into the run loop, CLI or GitHub and does not automatically
enforce a review gate. The existing [project workflow](project/WORKFLOW.md)
continues to govern the actual development lifecycle.

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
