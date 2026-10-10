# Project workflow

This is the repository lifecycle authority. Use it with the development checks
in [CONTRIBUTING.md](../../CONTRIBUTING.md). Admit the requested outcome,
necessary paths, acceptance criteria, immutable base and delivery endpoint.
Record actual authority for commit, push, PR publication and merge; a rule edit
or review approval grants none. Keep one active outcome unless the user names
an exception. Documentation corrections may have a publication-only endpoint;
main-targeted work continues through authorized merge and main verification.

## Review and repair an existing PR

Use the existing PR and branch. Record target/base SHA, full current head SHA,
range diff, relevant requirements and prior findings. Review read-only; create
no branch, worktree or replacement PR merely for review. Preserve user files
and unrelated changes. Batch relevant repairs on the existing branch, including
ownership of producers, serializers, readers, comparison and recovery consumers.
Assess affected unchanged consumers when a contract changes.

For behavior changes, state the behavioral contract, reproduce the failure with
an appropriate test, implement the smallest fix, then refactor with passing
checks. Documentation uses proportional link, command, claim and diff checks,
without artificial application tests.

## Verify, then review, then publish

Required applicable full candidate checks must reach terminal PASS before final
independent review, then authorized publication of that reviewed candidate.
FAIL, RUNNING and UNKNOWN do not satisfy gates. Keep final reviewers independent
from implementation; give them the scoped contract, immutable base, exact final
range diff, changed paths, verification evidence and prior finding dispositions.
Request reports only, without edits, tests, commits or merge actions. Main
inspection and passing tests do not constitute independent review.

Record every material finding as accepted-and-repaired, rejected with evidence,
or deferred with explicit user approval. An unresolved blocking finding prevents
merge. Batch accepted repairs within the remaining budget, pass affected full
checks and refresh final review before publishing. Avoid interim publication
merely to provoke another review. Required hosted checks triggered by publication
are subsequent exact-HEAD integration gates; a successful push is not their PASS.
Record local platform checks separately from hosted CI.

## Bound the whole PR or delivery outcome

Default aggregate: two substantive independent passes (initial plus one bounded
correction), at most one automatic post-review repair cycle and two planned
reviewed-candidate publications/native-head cycles. Track these dimensions
separately. Additional human-requested independent reviewers consume the pass
allowance unless a named exception explicitly grants more. Native code/security
review on one HEAD is one remote cycle, not two independent passes; targeted
TDD/regression tests are not final review passes.

Carry counters, original starts/deadlines, cumulative waits and prior evidence
across tasks, agents, commits, chats and continuation of the same outcome.
Renaming a unit, changing SHA or opening a replacement PR cannot reset them.
Existing counts remain attributable; unavailable counts are UNKNOWN, never zero.
At exhaustion report PARTIAL/BLOCKED/TIMEOUT/UNKNOWN, remaining findings and next
human decision. Do not automatically repair, review or publish again, waive a
defect or merge. A budget adjustment requires explicit scope and remaining
allowance; editing this rule cannot supply that authority.

Each lookup/status wait has a cumulative window no greater than 300 seconds,
including retries/polls; retain shorter timeouts and actual observed times
across continuation. Stop at expiry and do not restart the same lookup to extend
it. Prompt guidance does not enforce runtime termination or establish cost.

The explicitly admitted 2026-10-10 workflow migration permits one final
independent review, one correction/review only for accepted material findings,
and one documentation publication to the existing PR. Prior product cycles
remain disclosed historical overruns, not retroactively compliant. New limits
apply prospectively; resumed product delivery discloses prior counts and requires
explicit remaining allowance when exhausted. This migration does not resume or
merge product work.

## Reuse evidence truthfully

Reuse an attributable earlier result only when relevant content, comparison
base, tools and environment remain applicable. Record actual tested/reviewed
revision or content digests and differences. A documentation increment needs
fresh proportional checks; applicable underlying code evidence can be reused.
A mixed source PR still requires its complete-diff hosted CI; a final document
increment cannot classify the whole PR as documentation-only.

Changed relevant inputs require renewed affected checks/review; a changed base
requires impact assessment. New-HEAD statuses/approval must bind the exact full
SHA even when underlying content evidence is reusable. A newly reproduced defect
can invalidate approval even at an unchanged SHA.

Reuse one current report for status, counters, evidence and next gate. Preserve
immutable attributable per-run execution logs and review diff packets. Publish
short actual start/end results and meaningful blockers or requested statuses;
monitor completion internally with backoff. Unknown timing stays UNKNOWN.
A status question or dissatisfaction is not explicit cancellation.

## Record review approval

The `codex-review` label routes review; it does not prove approval. Native
review uses the configured subscription integration, without requiring an
API-billed action. Read actual results and resolve findings. Where the manual
`codex-review` status workflow is configured, new commits leave review pending.
The authorized confirmer supplies the exact full reviewed head SHA and confirms
the completed review before approving it. Silence, labels, test success and old
SHAs do not prove approval. Confirm the PR still has that head before proceeding.

These rules do not prove server-side protection. Verify enforcement separately;
pending setup stays pending. Do not bypass platform rejection, use administrator
overrides or change settings to force merge.

## Deliver to main and clean up

The designated local integration owner merges only after substantive findings
are resolved, applicable final-head checks and independent review pass, and
actual human merge authority covers the PR. Reviewers and hosted jobs grant no
merge authority. Hosted implementation ends at its admitted PR/evidence handoff;
it does not merge, enable auto-merge/merge queue, write protected integration
refs or delegate integration elsewhere. Missing authority/evidence stays pending.

Immediately before merge, refresh actual base/head, checks and approval. If
changed, complete affected gates within remaining budget first. After authorized
merge, verify the PR's merged state and fetch remote `main`. Verify the resulting
commit belongs to remote main history; for squash/rebase, use the platform's
resulting commit and final content rather than assuming the old head is an
ancestor. Check applicable main CI and the requested outcome on that revision.
Distinguish technical VERIFIED from observed DELIVERED.

Delete an owned PR branch only after main ancestry/content proof, with no
remaining dependencies or unique commits. Check remote/local branch identity
and worktree use. Preserve dirty files and other people's branches; do not
force-delete or reset them. Handle owned temporary validation resources
separately: verify they are disposable, close test PRs without merging test
content, and safely remove only owned unused resources. Report retained items
and reasons. Stop at the admitted outcome, applicable checks, truthful records
and safe cleanup; a publication-only endpoint does not authorize product merge.
