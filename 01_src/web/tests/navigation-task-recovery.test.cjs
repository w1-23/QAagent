'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {browser, flush, reply, framesPage} = require('./chat-turn-fixture.cjs');
function render(b, turns) { b.context.renderHistoryMessages([], b.context.normalizeHistoryTurns(turns)); }
const active = (id = 'job', status = 'detached') => ({turn_request_id: id + '-request', job_id: id, job_status: status});
const events = (b, job) => b.requests.findLast(r => r.url.includes('/jobs/' + job + '/events?'));
const chats = b => b.requests.filter(r => r.url === '/api/v2/chat');

test('unsaved job restores a task card and same-job append/stop without a chat submission', async () => {
  const b = browser(); render(b, [active()]);
  assert.equal(b.get('chat-container').querySelectorAll('.bot').length, 1);
  assert.equal(b.get('chat-container').querySelectorAll('.user').length, 0);
  assert.match(b.get('chat-container').textContent, /任务在后台执行/);
  assert.equal(b.context.currentJobId, 'job');
  assert.equal(b.get('stop-btn').style.display, 'flex');
  const binding = b.context.activeTurnComposer;
  assert.equal(binding.controls.canUpdate(), true);
  b.get('user-input').value = '追加要求';
  const send = b.context.sendMsg(); await flush();
  const update = b.requests.find(r => r.url.includes('/jobs/job/updates'));
  assert.ok(update); reply(update, {accepted: true, version: 1}); await flush();
  // submitUpdate also refreshes status; resolve it without replacing this task.
  for (const request of b.requests.filter(r => r.url === '/jobs/job')) reply(request, {job_id: 'job', status: 'detached'});
  await flush();
  for (const request of b.requests.filter(r => r.url.includes('/jobs/job/events?'))) reply(request, framesPage('job', []));
  await send;
  assert.equal(chats(b).length, 0);
  b.context.stopCurrentTask();
  assert.ok(b.requests.some(r => r.url === '/api/v2/chat/jobs/job/cancel'));
  assert.equal(b.requests.filter(r => r.url === '/api/v2/chat/cancel').length, 0);
  assert.match(b.get('user-input').placeholder, /正在停止/);
  assert.equal(binding.store.state.display_status, '');
});

test('a newer terminal turn cannot steal controls from an earlier active task', () => {
  const b = browser(); render(b, [active('running'), {...active('completed', 'completed'), terminal_snapshot: {display_status: 'completed'}, answer: {content: '已完成'}}]);
  assert.equal(b.context.currentJobId, 'running');
  assert.equal(b.context.activeTurnComposer.store.state.job_id, 'running');
  b.context.stopCurrentTask();
  assert.ok(b.requests.some(r => r.url === '/api/v2/chat/jobs/running/cancel'));
});

test('queued job blocks a second chat while retaining exact cancellation', async () => {
  const b = browser(); render(b, [active('queued', 'queued')]);
  assert.equal(b.get('stop-btn').style.display, 'flex');
  b.get('user-input').value = '不得另起一轮'; await b.context.sendMsg();
  assert.equal(chats(b).length, 0);
  b.context.stopCurrentTask();
  assert.ok(b.requests.some(r => r.url === '/api/v2/chat/jobs/queued/cancel'));
});

test('rerender retires old observers and late replay cannot update replacement DOM', async () => {
  const b = browser(); render(b, [active('old')]);
  const oldBinding = b.context.activeTurnComposer, oldReplay = events(b, 'old');
  render(b, [active('new')]);
  assert.equal(oldBinding.owns(), false);
  assert.equal(b.context.historyTaskDisposers.length, 1);
  reply(oldReplay, framesPage('old', [[1, 'done', {answer: '迟到的旧答案', turn_envelope: {display_status: 'completed'}}]])); await flush();
  assert.doesNotMatch(b.get('chat-container').textContent, /迟到的旧答案/);
  assert.equal(b.context.currentJobId, 'new');
  assert.equal(b.requests.filter(r => r.url === '/jobs/old').length, 0);
});

test('canonical terminal releases restored stop controls and never sends a session cancel', async () => {
  const b = browser(); render(b, [active()]);
  reply(events(b, 'job'), framesPage('job', [[1, 'done', {answer: '真实终答', turn_envelope: {display_status: 'completed'}}]])); await flush();
  assert.match(b.get('chat-container').textContent, /真实终答/);
  assert.equal(b.get('stop-btn').style.display, 'none');
  assert.equal(b.context.currentJobId, '');
  b.context.stopCurrentTask();
  assert.equal(b.requests.filter(r => r.opts?.method === 'POST').length, 0);
});

test('session switch retires restored controls and late approval cannot appear in next session', async () => {
  const b = browser(); render(b, [active()]); const replay = events(b, 'job');
  b.context.switchSession('next');
  assert.equal(b.context.currentJobId, '');
  assert.equal(b.context.activeTurnComposer, null);
  assert.equal(b.context.historyTaskDisposers.length, 0);
  reply(replay, framesPage('job', [[1, 'permission_request', {approval_id: 'old-approval', tool_name: 'code_execute'}]])); await flush();
  assert.equal(b.document.body.querySelectorAll('.perm-card').length, 0);
  assert.equal(b.requests.filter(r => r.url.includes('/cancel')).length, 0);
});

test('late cancel acknowledgement cannot show a banner in a different session', async () => {
  const b = browser(); render(b, [active()]);
  const banners = []; b.context.showBanner = text => banners.push(text);
  b.context.stopCurrentTask();
  const cancel = b.requests.find(r => r.url === '/api/v2/chat/jobs/job/cancel');
  b.context.switchSession('next');
  reply(cancel, {accepted: false, note: 'not_in_this_process'}); await flush();
  assert.equal(banners.length, 0);
});

test('pending approvals and operation controls return through the original task snapshot', async () => {
  const b = browser(); render(b, [active()]);
  reply(events(b, 'job'), framesPage('job', [])); await flush();
  reply(b.requests.findLast(r => r.url === '/jobs/job'), {job_id: 'job', status: 'detached', approvals: [{approval_id: 'approval', tool_name: 'code_execute', status: 'pending'}]}); await flush();
  assert.equal(b.context._pendingPermission.job_id, 'job');
  assert.equal(b.context._pendingPermission.approval_id, 'approval');
  assert.equal(b.document.body.querySelectorAll('.perm-card').length, 1);
  assert.equal(chats(b).length, 0);
});
