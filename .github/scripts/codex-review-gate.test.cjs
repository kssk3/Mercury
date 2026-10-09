'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { planGate, runGate } = require('./codex-review-gate.cjs');

const HEAD = 'a'.repeat(40);
const OLD_HEAD = 'b'.repeat(40);

function pull(overrides = {}) {
  return {
    number: 12,
    state: 'open',
    draft: false,
    head: { sha: HEAD },
    base: { ref: 'main' },
    html_url: 'https://github.com/kssk3/Mercury/pull/12',
    ...overrides,
  };
}

function automatic(pr = pull(), action = 'opened') {
  return {
    eventName: 'pull_request_target',
    actor: 'contributor',
    ref: 'refs/heads/main',
    repo: { owner: 'kssk3', repo: 'Mercury' },
    payload: { action, number: pr.number, pull_request: pr },
  };
}

function manual(inputs = {}, overrides = {}) {
  return {
    eventName: 'workflow_dispatch',
    actor: 'kssk3',
    ref: 'refs/heads/main',
    repo: { owner: 'kssk3', repo: 'Mercury' },
    payload: { inputs: {
      'pr-number': '12',
      decision: 'approved',
      'reviewed-sha': HEAD,
      confirmation: 'true',
      ...inputs,
    } },
    ...overrides,
  };
}

test('a current PR event requires pending on the actual live head', () => {
  const result = planGate(automatic(), pull());
  assert.equal(result.action, 'status');
  assert.equal(result.state, 'pending');
  assert.equal(result.sha, HEAD);
});

test('all admitted automatic events reset the current head including drafts', () => {
  for (const action of ['opened', 'reopened', 'synchronize', 'ready_for_review']) {
    const pr = pull({ draft: true });
    assert.equal(planGate(automatic(pr, action), pr).state, 'pending', action);
  }
  const context = automatic(pull(), 'edited');
  context.payload.changes = { base: { ref: { from: 'develop' } } };
  assert.equal(planGate(context, pull()).state, 'pending');
});

test('obsolete heads or bases and unrelated edits cannot reset approval', () => {
  const cases = [
    [automatic(pull({ head: { sha: OLD_HEAD } })), pull()],
    [automatic(pull({ base: { ref: 'develop' } })), pull()],
    [automatic(pull(), 'edited'), pull()],
    [automatic(pull(), 'closed'), pull()],
    [automatic(), pull({ state: 'closed' })],
  ];
  for (const [context, pr] of cases) {
    assert.equal(planGate(context, pr).action, 'skip');
  }
});

test('owner confirmation approves only the specified current reviewed revision', () => {
  const result = planGate(manual(), pull());
  assert.equal(result.action, 'status');
  assert.equal(result.state, 'success');
  assert.equal(result.sha, HEAD);
});

const rejected = [
  ['wrong head', manual({ 'reviewed-sha': OLD_HEAD }), pull()],
  ['short SHA', manual({ 'reviewed-sha': 'abc123' }), pull()],
  ['nonhex SHA', manual({ 'reviewed-sha': 'z'.repeat(40) }), pull()],
  ['nonowner', manual({}, { actor: 'contributor' }), pull()],
  ['other repository owner', manual({}, { repo: { owner: 'someone', repo: 'Mercury' } }), pull()],
  ['unconfirmed', manual({ confirmation: 'false' }), pull()],
  ['missing confirmation', manual({ confirmation: undefined }), pull()],
  ['draft', manual(), pull({ draft: true })],
  ['closed', manual(), pull({ state: 'closed' })],
  ['wrong workflow ref', manual({}, { ref: 'refs/heads/feature' }), pull()],
  ['wrong base', manual(), pull({ base: { ref: 'develop' } })],
  ['zero PR', manual({ 'pr-number': '0' }), pull()],
  ['negative PR', manual({ 'pr-number': '-12' }), pull()],
  ['noninteger PR', manual({ 'pr-number': '12.5' }), pull()],
  ['unsafe integer PR', manual({ 'pr-number': '9007199254740993' }), pull()],
  ['unknown decision', manual({ decision: 'merge' }), pull()],
  ['different live PR', manual(), pull({ number: 13 })],
];
for (const [name, context, pr] of rejected) {
  test(`invalid approval rejects ${name} without an approval result`, () => {
    assert.throws(() => planGate(context, pr));
  });
}

test('pending is allowed without attestation and changes requested blocks the reviewed head', () => {
  const pending = planGate(manual({ decision: 'pending', 'reviewed-sha': '', confirmation: 'false' }), pull({ draft: true }));
  assert.equal(pending.state, 'pending');
  assert.equal(pending.sha, HEAD);
  const changes = planGate(manual({ decision: 'changes_requested', confirmation: 'false' }), pull());
  assert.equal(changes.state, 'failure');
  assert.equal(changes.sha, HEAD);
  assert.throws(() => planGate(manual({ decision: 'changes_requested', 'reviewed-sha': OLD_HEAD }), pull()));
});

test('boolean confirmation and case-insensitive full SHA are accepted', () => {
  assert.equal(planGate(manual({ confirmation: true, 'reviewed-sha': HEAD.toUpperCase() }), pull()).state, 'success');
});

function repository(pr = pull()) {
  const labels = new Set(['bug', 'help wanted']);
  const statuses = new Map();
  let summary = '';
  return {
    labels,
    statuses,
    summary: () => summary,
    github: { rest: {
      pulls: { get: async () => ({ data: structuredClone(pr) }) },
      issues: { addLabels: async ({ issue_number, labels: added }) => {
        assert.equal(issue_number, pr.number);
        for (const label of added) labels.add(label);
      } },
      repos: { createCommitStatus: async (status) => {
        assert.equal(status.owner, 'kssk3');
        assert.equal(status.repo, 'Mercury');
        statuses.set(`${status.sha}:${status.context}`, status.state);
      } },
    } },
    core: { summary: {
      addRaw(text) { summary += text; return this; },
      async write() {},
    } },
  };
}

test('controller preserves labels and publishes explicit pending for a live fork or draft PR', async () => {
  const pr = pull({ draft: true, head: { sha: HEAD, repo: { fork: true } } });
  const store = repository(pr);
  await runGate({ github: store.github, core: store.core, context: automatic(pr) });
  assert.deepEqual(store.labels, new Set(['bug', 'help wanted', 'codex-review']));
  assert.equal(store.statuses.get(`${HEAD}:codex-review`), 'pending');
  assert.ok(store.summary().includes(pr.html_url));
  assert.ok(store.summary().includes(HEAD));
});

test('controller publishes owner success or changes requested for the exact live SHA', async () => {
  for (const [decision, expected] of [['approved', 'success'], ['changes_requested', 'failure']]) {
    const store = repository();
    await runGate({ github: store.github, core: store.core, context: manual({ decision }) });
    assert.equal(store.statuses.get(`${HEAD}:codex-review`), expected);
    assert.ok(store.summary().includes(HEAD));
  }
});

test('controller rejection leaves all commit statuses untouched', async () => {
  for (const [, context, pr] of rejected) {
    const store = repository(pr);
    await assert.rejects(runGate({ github: store.github, core: store.core, context }));
    assert.equal(store.statuses.size, 0);
  }
});

test('an obsolete automatic event cannot overwrite an approved newer live head', async () => {
  const store = repository();
  store.statuses.set(`${HEAD}:codex-review`, 'success');
  await runGate({ github: store.github, core: store.core, context: automatic(pull({ head: { sha: OLD_HEAD } })) });
  assert.equal(store.statuses.get(`${HEAD}:codex-review`), 'success');
  assert.equal(store.statuses.has(`${OLD_HEAD}:codex-review`), false);
});

test('a stale approval shows the live PR and exact SHA while publishing no status', async () => {
  const store = repository();
  await assert.rejects(runGate({ github: store.github, core: store.core, context: manual({ 'reviewed-sha': OLD_HEAD }) }));
  assert.equal(store.statuses.size, 0);
  assert.ok(store.summary().includes('https://github.com/kssk3/Mercury/pull/12'));
  assert.ok(store.summary().includes(HEAD));
});
