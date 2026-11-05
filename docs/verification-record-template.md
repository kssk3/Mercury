# Verification current record

This is a blank, manually maintained template, not proof of an execution.
Replace placeholders only with observed facts. Use UNKNOWN or NOT_RUN when no
evidence exists. This record does not replace the project's lifecycle authority.

## Current outcome

- Outcome/check ID: `<stable ID>`
- Updated date and timezone: `<date; human timezone; preserve receipt UTC times>`
- State: `<verified locally / held / failed / unknown / delivered>`
- Requested behavior and acceptance criterion: `<observable result>`
- Application revision and candidate content identity: `<SHA/digest or unavailable>`
- Mercury revision and implementation manifest: `<versioned source identity>`
- Execution outcome: `<process state / exit / timeout; evidence link>`
- Verification outcome: `<PASS / FAIL / SKIP / TIMEOUT / UNKNOWN; evidence link>`
- Delivery outcome: `<not published / PR only / delivered; exact revision and proof>`

## Verification evidence

| Check and criterion | Actor / tool | Target / environment | Outcome | Evidence |
| --- | --- | --- | --- | --- |
| `<check ID and guaranteed behavior>` | `<observed actor/tool; not inferred model>` | `<candidate and environment identity>` | `<process and assertions separately>` | `<original receipt, complete bundle and artifact>` |

- Fresh checks: `<exact command reference, cwd, relevant overrides, timeout, result>`
- Reused checks: `<original revision/digest, reason still applicable, limitations>`
- Original bundle: `<intent/process/receipt/result.xml links; keep siblings intact>`
- Readable view: `<HTML and standalone explanation JSON links>`
- View generator: `<exact renderer revision/content manifest; optional view digest>`
- UI evidence, if applicable: `<viewport, state, candidate and screenshot; else N/A>`
- Review, if applicable: `<independent report, scope, candidate and actual counters; else N/A>`

Counts describe the linked run. Do not add reused, overlapping or different
attempts into a new passing run. A screenshot is not assertion coverage.

## Decisions and remaining gate

- Decision and observable basis: `<evidence that changed the choice>`
- Alternative and selection rationale: `<relevant tradeoff or N/A>`
- Confirmed cause or result: `<supported facts>`
- Assumptions and unverified risks: `<unknowns; missing evidence is not zero>`
- Preserved failure/recovery evidence: `<links or N/A>`
- Next smallest action and authority: `<specific gate and allowed actions>`

## Significant outcomes

| Date / timezone | Outcome ID | Result / decision | Evidence and next gate |
| --- | --- | --- | --- |
| `<date>` | `<ID>` | `<material feature, failure, decision or handoff>` | `<links>` |

Keep one current record. Preserve the prior view before replacing audit/recovery
facts. Link immutable evidence; do not append every tool call or create empty
daily notes. A file's age alone is not permission to remove it.
