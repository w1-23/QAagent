'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const harness = {exports: {}};
vm.runInNewContext(readFileSync(join(__dirname, 'frontend-isolation.test.cjs'), 'utf8') + '\nmodule.exports = {browser, flush, Node};', {
  module: harness, __dirname, console, AbortController, DOMException, TextDecoder, TextEncoder, URLSearchParams,
  require(name) { return name === 'node:test' ? {test() {}} : require(name); }
}, {filename: 'frontend-isolation.test.cjs'});
const {browser, flush, Node} = harness.exports;
function setup() {
  const b = browser('app.js'); b.ctx.currentAccount = {user_id: 'A', username: 'A'}; b.ctx.sessionId = 'A-session'; return b;
}
const plain = value => JSON.parse(JSON.stringify(value));
function protocol(b, callbacks = {}) {
  const store = b.ctx.CitrusTaskState.create(callbacks);
  const transport = b.ctx.CitrusTaskTransport.create({store, fetch: b.ctx.accountFetch, owns: () => b.ctx.sessionId === 'A-session', ...callbacks});
  return {store, transport};
}
function frame(seq, event, data = {}) { return {seq, event, data: {...data, seq}}; }
function page(events, extra = {}) { return {protocol: 'citrus-task/1', job_id: 'j', events, has_more: false, ...extra}; }

test('ordered reducer buffers gaps, deduplicates live/replay and trusts only canonical terminal', () => {
  const b = setup(), received = [];
  const {store} = protocol(b, {onEvent: (type, data) => received.push([type, data.seq])});
  store.receive('tool_result', {seq: 3, status: 'complete'}, '3');
  store.receive('job', {seq: 1, job_id: 'j'}, '1');
  assert.equal(store.state.cursor, 1); assert.equal(store.hasGap(), true);
  store.receive('tool_call_start', {seq: 2}, '2');
  store.receive('tool_result', {seq: 3, status: 'complete'}, '3');
  assert.deepEqual(received, [['job', 1], ['tool_call_start', 2], ['tool_result', 3]]);
  assert.equal(store.state.display_status, '');
  store.receive('done', {seq: 4, turn_status: 'completed', turn_envelope: {display_status: 'partial'}}, '4');
  assert.equal(store.state.display_status, 'partial');
  assert.equal(store.receive('text', {seq: 5}, '6'), false);
  assert.equal(store.receive('text', {seq: 5, job_id: 'other'}, '5'), false);
  assert.equal(store.state.cursor, 4);
});

test('SSE parser handles split frames, CRLF, numeric ids and multiline JSON', () => {
  const b = setup(), seen = [], input = b.ctx.CitrusTaskTransport.parser((...args) => seen.push(plain(args)));
  input.push(': ping\r\nid: 1\r\nevent: task_up');
  input.push('date\r\ndata: {"seq":1,\r\ndata: "version":2}\r\n\r\n');
  input.push('event: done\ndata: {broken}\n\nevent: heartbeat\ndata: {}', true);
  assert.deepEqual(seen, [['task_update', {seq: 1, version: 2}, '1'], ['heartbeat', {}, '']]);
});

test('replay pages consume every ordered event and never skip to an advertised cursor', async () => {
  const b = setup(), seen = [], {store, transport} = protocol(b, {onEvent: (_, data) => seen.push(data.seq)});
  store.receive('job', {job_id: 'j', seq: 1}); store.receive('text', {seq: 4});
  const replay = transport.replay('j');
  assert.equal(b.requests.at(-1).url, '/jobs/j/events?after=1&limit=200');
  b.reply(b.requests.at(-1), page([frame(2, 'answer_start'), frame(3, 'text')], {has_more: true, next_cursor: 999})); await flush();
  assert.equal(b.requests.at(-1).url, '/jobs/j/events?after=4&limit=200');
  b.reply(b.requests.at(-1), page([frame(4, 'text'), frame(5, 'done', {turn_envelope: {display_status: 'failed'}})])); await replay;
  assert.deepEqual(seen, [1, 2, 3, 4, 5]); assert.equal(store.state.cursor, 5); assert.equal(store.state.display_status, 'failed');
});

test('replay refuses a missing event rather than advancing the cursor', async () => {
  const b = setup(), {store, transport} = protocol(b);
  store.receive('job', {seq: 1, job_id: 'j'});
  const replay = transport.replay('j'); b.reply(b.requests.at(-1), page([frame(3, 'text')], {next_cursor: 3}));
  await assert.rejects(replay, /不连续/); assert.equal(store.state.cursor, 1);
});

test('late replay JSON and snapshots cannot cross the account/session boundary', async () => {
  const b = setup(), {store, transport} = protocol(b);
  const replay = transport.replay('j'); let deliver;
  b.requests.at(-1).resolve({ok: true, json: () => new Promise(resolve => { deliver = resolve; })}); await flush();
  b.ctx.advanceAuthEpoch(); b.ctx.sessionId = 'B-session';
  deliver(page([frame(1, 'job', {job_id: 'j'})], {terminal_snapshot: {display_status: 'completed'}})); await replay;
  assert.equal(store.state.cursor, 0); assert.equal(store.state.display_status, '');
});

test('task update reducer tracks accepted/applied versions and rejects stale metadata', () => {
  const b = setup(), {store} = protocol(b);
  store.receive('job', {seq: 1, job_id: 'j'});
  store.receive('task_update', {seq: 2, version: 2, status: 'pending', instruction: 'test instruction'});
  assert.equal(store.state.task_version, 2); assert.equal(store.state.pending_updates.length, 1);
  assert.equal(store.hydrate({job_id: 'j', event_cursor: 1, task_version: 1, pending_updates: []}), false);
  assert.equal(store.state.pending_updates.length, 1);
  store.receive('task_update', {seq: 3, version: 2, status: 'applied'});
  assert.equal(store.state.applied_version, 2); assert.equal(store.state.pending_updates.length, 0);
});

test('update UI sends CAS version, preserves conflicting draft and refreshes state', async () => {
  const b = setup(), {store, transport} = protocol(b), container = new Node();
  store.hydrate({job_id: 'j', status: 'running', task_version: 2});
  b.ctx.CitrusTaskControls.create({container, store, transport, owns: () => b.ctx.sessionId === 'A-session'});
  const input = container.querySelector('textarea'), form = container.querySelector('form'); input.value = 'keep this adjustment';
  const submitting = form.fire('submit', {preventDefault() {}})[0];
  const update = b.requests.at(-1); assert.equal(update.url, '/api/v2/chat/jobs/j/updates');
  assert.deepEqual(JSON.parse(update.opts.body), {expected_version: 2, instruction: 'keep this adjustment'});
  b.reply(update, {detail: {code: 'version_conflict'}}, 409); await flush();
  assert.equal(b.requests.at(-1).url, '/jobs/j');
  b.reply(b.requests.at(-1), {job_id: 'j', status: 'running', task_version: 3}); await submitting;
  assert.equal(input.value, 'keep this adjustment'); assert.match(container.textContent, /已更新/);
  assert.equal(store.state.task_version, 3);
});

test('service restart marks an accepted update unapplied and clears its waiting notice', async () => {
  const b = setup(), {store, transport} = protocol(b), container = new Node();
  store.hydrate({job_id: 'j', status: 'running', task_version: 1});
  b.ctx.CitrusTaskControls.create({container, store, transport, owns: () => true});
  container.querySelector('textarea').value = 'test adjustment';
  const submitting = container.querySelector('form').fire('submit', {preventDefault() {}})[0];
  b.reply(b.requests.at(-1), {job_id: 'j', version: 2, status: 'pending'}); await flush();
  b.reply(b.requests.at(-1), {job_id: 'j', status: 'running', task_version: 2, pending_updates: [{version: 2, status: 'pending'}]}); await submitting;
  assert.match(container.textContent, /等待后续步骤应用/);
  store.receive('task_update', {seq: 1, version: 2, status: 'not_applied', reason: 'service_restart'});
  assert.equal(store.state.pending_updates.length, 0); assert.equal(store.state.applied_version, 0);
  assert.match(container.textContent, /第 2 版调整未应用（服务重启）/);
  assert.doesNotMatch(container.textContent, /等待后续步骤应用|条调整待应用|已应用第 2/);
  // A late acknowledgment/metadata read cannot resurrect the discarded update.
  store.hydrate({job_id: 'j', task_version: 2, pending_updates: [{version: 2, status: 'pending'}]});
  assert.equal(store.state.pending_updates.length, 0);
  const restored = new Node();
  b.ctx.CitrusTaskControls.create({container: restored, store, transport, owns: () => true});
  assert.match(restored.textContent, /第 2 版调整未应用（服务重启）/);
  store.clear(); assert.equal(store.state.not_applied_updates.length, 0);
});

test('structured statistics/audit/figure cards use facts and refs without prose parsing', () => {
  const b = setup();
  for (const [tool, title] of [['statistics_test', '统计分析'], ['data_audit', '数据审查'], ['create_figure', '图件']]) {
    const container = new Node();
    b.ctx.CitrusTaskCards.render(container, {tool_name: tool, status: 'partial', outcome: {
      facts: {coverage: 'test scope', limitations: '<script>not markup</script>'}, result_refs: [{label: 'test reference', url: 'javascript:alert(1)'}]
    }});
    assert.match(container.textContent, new RegExp(title)); assert.match(container.textContent, /test scope/);
    assert.match(container.textContent, /<script>not markup<\/script>/); assert.equal(container.querySelectorAll('a').length, 0);
  }
  assert.equal(b.ctx.CitrusTaskCards.model({summary: 'said it succeeded and made a chart'}), null);
});

test('canonical permission rejection posts a decision without replaying chat', async () => {
  const b = setup(); b.ctx.showPermissionCard({job_id: 'j', approval_id: 'a', tool_name: 'write_local_file'});
  const card = b.document.body.querySelector('.perm-card'); assert.ok(card.querySelector('[data-decision="rejected"]'));
  const pending = b.ctx.rejectPermission('write_local_file'); const req = b.requests.at(-1);
  assert.equal(req.url, '/api/v2/chat/jobs/j/approvals/a'); assert.deepEqual(JSON.parse(req.opts.body), {decision: 'rejected'});
  b.reply(req, {status: 'rejected'}); await pending;
  assert.equal(b.ctx._pendingPermission, null); assert.equal(b.requests.filter(req => req.url === '/api/v2/chat').length, 0);
});

test('only server-declared interrupted local reads/computation expose recovery', () => {
  const b = setup(), {store} = protocol(b);
  store.hydrate({job_id: 'j', status: 'failed', terminal_snapshot: {display_status: 'failed'}, operations: [
    {operation_id: 'read', status: 'interrupted', recovery_mode: 'local_read', resumable: true},
    {operation_id: 'compute', status: 'interrupted', recovery_mode: 'pure_computation', resumable: true},
    {operation_id: 'write', status: 'interrupted', recovery_mode: 'never', resumable: true},
    {operation_id: 'unspecified', status: 'interrupted', resumable: true},
    {operation_id: 'done', status: 'complete', recovery_mode: 'local_read', resumable: true},
    {operation_id: 'unsafe', status: 'interrupted', recovery_mode: 'local_read', resumable: false},
  ]});
  assert.deepEqual(plain(b.ctx.CitrusTaskState.resumable(store.state).map(op => op.operation_id)), ['read', 'compute']);
  const container = new Node(); const {transport} = protocol(b);
  b.ctx.CitrusTaskControls.create({container, store, transport, owns: () => true});
  assert.equal(container.querySelector('.task-recovery').querySelectorAll('button').length, 2);
  assert.equal(container.querySelector('details').hidden, true);
});

test('one-step recovery leaves the original canonical task terminal untouched', async () => {
  const b = setup(), {store, transport} = protocol(b), container = new Node();
  store.hydrate({job_id: 'j', status: 'failed', terminal_snapshot: {display_status: 'partial'}, operations: [
    {operation_id: 'read', tool_name: 'read_account_file', status: 'interrupted', recovery_mode: 'local_read', resumable: true}
  ]});
  b.ctx.CitrusTaskControls.create({container, store, transport, owns: () => true});
  const resume = container.querySelector('.task-recovery').querySelector('button').onclick();
  const req = b.requests.at(-1); assert.equal(req.url, '/api/v2/chat/jobs/j/resume');
  assert.deepEqual(JSON.parse(req.opts.body), {operation_id: 'read'});
  b.reply(req, {job_id: 'j', operation_id: 'read', recovery: 'safe_step', operation_status: 'complete'}); await flush();
  b.reply(b.requests.at(-1), {job_id: 'j', status: 'failed', terminal_snapshot: {display_status: 'partial'}, operations: []}); await resume;
  assert.equal(store.state.display_status, 'partial'); assert.match(container.textContent, /整轮任务的原有状态保留/);
  assert.equal(container.querySelector('.task-recovery').querySelectorAll('button').length, 0);
});

test('production chat fills a sequence gap through replay and renders a structured tool card once', async () => {
  const b = setup(); b.get('user-input').value = 'test task'; await b.ctx.sendMsg(); await flush();
  const post = b.requests.find(req => req.url === '/api/v2/chat');
  const bytes = new TextEncoder().encode([
    'id: 1\nevent: job\ndata: {"seq":1,"job_id":"j"}\n\n',
    'id: 3\nevent: tool_result\ndata: {"seq":3,"tool_call_id":"tool-1","tool_name":"statistics_test","status":"partial","facts":{"coverage":"test scope"},"result_refs":[]}\n\n',
    'id: 3\nevent: tool_result\ndata: {"seq":3,"tool_call_id":"tool-1","status":"partial"}\n\n'
  ].join(''));
  let reads = 0;
  post.resolve({ok: true, body: {getReader: () => ({read: () => ++reads === 1 ? Promise.resolve({done: false, value: bytes}) : new Promise(() => {}), cancel: async () => {}})}});
  await flush();
  const replay = b.requests.find(req => req.url.includes('/events?after=1'));
  assert.ok(replay);
  b.reply(replay, page([frame(2, 'tool_call_start', {tool_call_id: 'tool-1', display_name: '统计分析'}), frame(3, 'tool_result', {
    tool_call_id: 'tool-1', tool_name: 'statistics_test', status: 'partial', facts: {coverage: 'test scope'}, result_refs: []
  })])); await flush();
  assert.equal(b.get('chat-container').querySelectorAll('.task-result').length, 1);
  assert.match(b.get('chat-container').textContent, /test scope/);
  assert.equal(b.requests.filter(req => req.url === '/api/v2/chat').length, 1);
  b.ctx.resetAccountState();
});

test('stopping a live task waits for its canonical cancellation terminal', async () => {
  const b = setup(); b.get('user-input').value = 'test task'; await b.ctx.sendMsg(); await flush();
  const post = b.requests.find(req => req.url === '/api/v2/chat'); let reads = 0;
  const bytes = new TextEncoder().encode('id: 1\nevent: job\ndata: {"seq":1,"job_id":"j"}\n\nid: 2\nevent: tool_call_start\ndata: {"seq":2,"tool_call_id":"t","display_name":"test step"}\n\n');
  post.resolve({ok: true, body: {getReader: () => ({read: () => ++reads === 1 ? Promise.resolve({done: false, value: bytes}) : new Promise(() => {}), cancel: async () => {}})}}); await flush();
  b.ctx.stopCurrentTask(); await flush();
  assert.match(b.get('chat-container').textContent, /已请求停止/);
  assert.equal(b.get('chat-container').querySelector('.tool-trace-label').textContent, '状态未确认');
  const cancel = b.requests.find(req => req.url === '/api/v2/chat/jobs/j/cancel'); assert.ok(cancel); b.reply(cancel, {accepted: true});
  const replay = b.requests.find(req => req.url.includes('/events?after=2')); assert.ok(replay);
  b.reply(replay, page([frame(3, 'done', {session_id: 'A-session', turn_status: 'cancelled', turn_envelope: {display_status: 'cancelled', history_saved: true}})])); await flush();
  assert.equal(b.get('chat-container').querySelector('.tool-trace-label').textContent, '已停止');
  assert.equal(b.get('send-btn').disabled, false);
  b.ctx.resetAccountState();
});

test('failed legacy rejection retains the permission decision for retry', async () => {
  const b = setup(); b.ctx.showPermissionCard({tool_name: 'write_local_file'});
  const pending = b.ctx.rejectPermission('write_local_file'); const req = b.requests.at(-1);
  assert.equal(req.url, '/api/v2/permission/reject'); b.reply(req, {}, 503); await pending;
  assert.equal(b.ctx._pendingPermission.tool_name, 'write_local_file');
});
