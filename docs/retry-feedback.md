# Bounded retry feedback

The Python `run_loop` API supplies observed failure facts to the next turn only
when its existing retry policy permits that turn. It keeps the original task,
acceptance commands, protected inputs, baseline and aggregate limits. Success
still requires development verification and a distinct final verification.

This is local implementation capability, not a claim of live-model reliability
or delivery to main. The public execution CLI defaults to one turn with no retry;
explicit bounded retry configuration is described in
[run preview](run-preview.md#explicit-bounded-retries).

## Use the existing API

Start with an ordinary `ContextPacket` and explicitly admitted `RetryLimits`.
For example, `RetryLimits(1, 2, 7, 90)` permits at most one retry, stops at two
equivalent failures, reserves at most seven outer command boundaries, and has a
90-second loop allowance. Native process/input/output budgets remain separate.
The caller must hold the existing repository lease and authorize the task and
allowance; creating limits is not approval.

After an eligible failure, `context.retry_feedback` contains:

- `assessment`: the actual preceding attempt ID, observed revision and classified
  failure findings, including attribution and failed command hashes.
- `commands`: the matching, unchanged admitted checker argv. Native process
  failures have no failed verification command and receive an empty tuple.

Both native adapters serialize this field within their existing untrusted
repository-context envelope. A custom adapter can inspect the field directly.
It is evidence for diagnosis, not an instruction to expand scope, change tests,
or acquire permission. No raw checker output, agent text or generated diagnosis
is forwarded. Command arguments can themselves contain sensitive information;
the caller owns their selection for the admitted task.

The latest assessment replaces earlier feedback rather than accumulating prompt
history. Original selected source text remains unchanged. The complete packet
must fit `ContextPacket.budget_bytes` and the complete native input envelope must
fit `TurnBudgets.input_bytes`. If it cannot, the loop stops with
`blocked / retry_feedback_unavailable` before another intent or preparation.

The next saved turn intent hashes the actual complete context, including its
feedback and source attempt identity. Existing attempt records retain the
underlying process/verification facts. No new journal schema or raw command-text
persistence is introduced. Context without feedback preserves its previous JSON
encoding and verification-checkpoint binding. A fresh loop refuses caller-supplied
retry feedback; it derives feedback from its own observed attempts.

## Reproduce a correction without a model

From the repository root, using existing development dependencies:

```sh
.venv/bin/python -m pytest -q 'tests/test_retry_feedback.py::test_feedback_drives_correction_without_changing_oracle[correct-pass-final_verification-2]'
```

This uses a disposable repository and a deterministic fake adapter. The baseline
passes, the first turn introduces a regression, and the checker fails. The second
turn repairs the value only when it receives feedback. The test requires the
unchanged checker, correctly attributed failure, six aggregate command boundaries
and passing development plus final verification. It proves feedback plumbing and
objective gating; it does not prove autonomous diagnosis by a real model.

## Stops that remain in force

Unknown attribution, scope/protected-input violations and uncertain outcomes
still stop or escalate. In particular, an unresolved failure already present in
the original baseline does not become an automatically retryable regression.
Repeated failures and exhausted command/time/retry limits still stop the task.
Verification-only continuation does not invoke the agent, and checkpoint mode
does not enable retries. No automatic review, publication or native-session
resume is added. A future baseline-defect repair policy needs separate admission.
