# Project workflow

This is the repository lifecycle authority. Use it with the development checks
in [CONTRIBUTING.md](../../CONTRIBUTING.md). Keep each change bounded to the
requested result, necessary paths, acceptance criteria, and delivery endpoint.
Record whether commit, push, PR creation, and merge are actually authorized;
review approval alone never supplies missing human authority.

## Review an existing PR

1. Use the existing PR. Record its target branch, immutable base SHA, current
   full head SHA, and the diff between them. Read the relevant requirements,
   code, tests, and earlier findings before judging the change.
2. Review read-only by default. Do not create a branch, worktree, or another PR
   merely to review. A read-only snapshot of existing content is sufficient
   when needed. Any necessary isolation beyond that requires a concrete reason
   and explicit user approval before creating it.
3. Keep reviewers independent from implementation. Give a reviewer the scoped
   requirements, exact base/head diff, verification evidence, and prior finding
   dispositions. Ask for findings and evidence, not edits or merge actions.
4. Resolve material findings before merge. Record each as accepted and repaired,
   rejected with evidence, or deferred with explicit user approval. An unresolved
   blocking finding prevents merge; silence or a label is not an approval.

## Repair and verify the final change

Repair accepted findings on the existing PR branch. Preserve unrelated changes
and user files; do not create a replacement branch or PR just for repairs.
For behavior changes, state the behavioral contract, reproduce the failure with
an appropriate test, implement the fix, then refactor with passing checks.

Bind review and test evidence to the final current head and applicable base.
After a source change, repeat independent review against the final diff and run
applicable checks. Use the actual CI classification and inspect completed check
results; a successful push is not verification. Record required local platform
checks separately from hosted CI.

For documentation-only changes, check links, executable command consistency,
and claims proportionally. Reuse earlier source/test evidence only when the
relevant bytes, base, and environment remain applicable and record that basis.
A new head still needs its applicable CI checks. A changed base or head requires
reassessment of the diff and evidence before approval or merge.

## Record review approval

The `codex-review` label routes review; it does not prove that review passed.
Native Codex review uses the configured subscription integration; this workflow
does not require an API-billed review action. Read the actual review result and
resolve findings before recording approval.

Where the manual `codex-review` status workflow is configured, new commits leave
review pending. The authorized confirmer must provide the exact full reviewed
head SHA and explicitly confirm the completed review before marking it approved.
Do not infer approval from missing comments, labels, test success, or an old SHA.
Confirm the PR still has that head immediately before proceeding.

These instructions describe the required process, not proof of server-side
branch protection. Verify repository enforcement separately; pending protection
setup remains pending until applied and checked. Do not bypass a platform
rejection, use an administrator override, or change settings to force a merge.

## Deliver to main

The authorized local lead performs the merge only after findings are resolved,
applicable final-head checks and independent review pass, and the user's merge
authority covers this PR. Reviewers and hosted jobs do not grant merge authority.
If authority is missing, present the verified result and request it; if already
given, continue through the authorized delivery instead of stopping at push.

Immediately before merging, refresh the PR base/head and gate results. If they
changed, reassess and complete the affected gates first. After merge, confirm the
PR's merged state and fetch remote `main`. Verify that the merge commit belongs
to remote main history; for squash/rebase, verify the platform-recorded resulting
commit and final content rather than assuming the old PR head is an ancestor.
Check the applicable main CI and requested outcome on that actual main revision.
Report technical verification separately from confirmed delivery.

## Clean up owned temporary work

Delete an owned PR branch only after merged-main ancestry/content verification,
with no dependent work or unique commits remaining. Check both remote and local
branch identity and worktree use. Preserve dirty files, unrelated branches, and
other people's work; do not force-delete or reset them to complete cleanup.

Handle owned temporary validation PRs, branches, and artifacts separately: verify
they are no longer needed or depended on, close test PRs without merging test
content, and safely delete only their owned disposable resources. Report any
retained resource and reason. Stop when the authorized outcome, applicable main
checks, truthful records, and safe cleanup are complete.
