'use strict';

function dispatchNumber(context) {
  if (context.actor !== 'kssk3' || context.repo.owner !== 'kssk3'
      || context.ref !== 'refs/heads/main') {
    throw new Error('kssk3 소유자만 main에서 리뷰 상태를 변경할 수 있습니다.');
  }
  const value = context.payload.inputs?.['pr-number'];
  if (!/^[1-9][0-9]*$/.test(value ?? '') || !Number.isSafeInteger(Number(value))) {
    throw new Error('PR 번호는 양의 정수여야 합니다.');
  }
  return Number(value);
}

function planGate(context, pr) {
  if (context.eventName === 'workflow_dispatch') {
    const number = dispatchNumber(context);
    const inputs = context.payload.inputs;
    const decision = inputs.decision;
    if (!['pending', 'approved', 'changes_requested'].includes(decision)) {
      throw new Error('허용되지 않은 리뷰 상태입니다.');
    }
    if (pr.number !== number || pr.state !== 'open' || pr.base.ref !== 'main') {
      throw new Error('지정한 PR은 열린 main 대상 PR이어야 합니다.');
    }
    if (decision !== 'pending') {
      const reviewed = inputs['reviewed-sha'] ?? '';
      if (!/^[a-f0-9]{40}$/i.test(reviewed) || reviewed.toLowerCase() !== pr.head.sha.toLowerCase()) {
        throw new Error('리뷰한 전체 SHA가 현재 PR HEAD와 일치해야 합니다.');
      }
    }
    if (decision === 'approved'
        && (pr.draft || ![true, 'true'].includes(inputs.confirmation))) {
      throw new Error('초안 PR은 승인할 수 없으며 Codex 리뷰 완료와 지적 해결 확인이 필요합니다.');
    }
    const state = { pending: 'pending', approved: 'success', changes_requested: 'failure' }[decision];
    return { action: 'status', state, sha: pr.head.sha };
  }
  const payload = context.payload;
  const relevant = ['opened', 'reopened', 'synchronize', 'ready_for_review'].includes(payload.action)
    || (payload.action === 'edited' && Object.hasOwn(payload.changes ?? {}, 'base'));
  if (!relevant || pr.state !== 'open'
      || payload.pull_request.head.sha !== pr.head.sha
      || payload.pull_request.base.ref !== pr.base.ref) {
    return { action: 'skip' };
  }
  return { action: 'status', state: 'pending', sha: pr.head.sha };
}

async function runGate({ github, context, core }) {
  const number = context.eventName === 'workflow_dispatch'
    ? dispatchNumber(context) : context.payload.number;
  const { data: pr } = await github.rest.pulls.get({ ...context.repo, pull_number: number });
  let plan;
  try {
    plan = planGate(context, pr);
  } catch (error) {
    await writeSummary(core, pr, `요청을 거절했습니다: ${error.message}`);
    throw error;
  }
  const descriptions = {
    pending: 'Codex 리뷰 완료 후 소유자의 현재 커밋 확인을 기다립니다.',
    success: 'kssk3 소유자가 이 커밋의 Codex 리뷰 완료와 지적 해결을 확인했습니다.',
    failure: '소유자가 이 커밋에 수정이 필요함을 확인했습니다.',
  };
  if (plan.action === 'status') {
    await github.rest.issues.addLabels({ ...context.repo, issue_number: number, labels: ['codex-review'] });
    await github.rest.repos.createCommitStatus({
      ...context.repo,
      sha: plan.sha,
      context: 'codex-review',
      state: plan.state,
      description: descriptions[plan.state],
    });
  }
  const result = plan.action === 'skip'
    ? '오래된 이벤트 또는 리뷰와 무관한 수정이므로 상태를 변경하지 않았습니다.'
    : descriptions[plan.state];
  await writeSummary(core, pr, result);
  return plan;
}

async function writeSummary(core, pr, result) {
  await core.summary.addRaw(
    `## Codex 리뷰 확인\n\nPR: [#${pr.number}](${pr.html_url})\n\n현재 HEAD SHA: \`${pr.head.sha}\`\n\n${result}\n\n`
    + '승인은 네이티브 Codex의 자동 판정이 아니라 소유자의 리뷰 완료 확인입니다. 새 커밋은 다시 확인해야 합니다.\n',
  ).write();
}

module.exports = { planGate, runGate };
