'use strict';
// Focused offline regressions for UI-01..04 / Q-01: composer admission,
// confirmed rejection vs ambiguous reconciliation, and event-driven tool rows.
// Production scripts run in the shared browser double; no network or credentials.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {browser, flush} = require('./frontend-harness.cjs');

function input(b) { return b.get('user-input'); }
function sendBtn(b) { return b.get('send-btn'); }
function messages(b) { return b.get('chat-container').querySelectorAll('.message'); }
function posts(b) {
  return b.requests.filter(r => r.opts && r.opts.method === 'POST' && r.url.includes('/turn-queue'));
}
// refreshTaskComposer only mounts its live hint when the input has a parent.
function mounted(b) {
  const host = b.get('composer');
  if (input(b).parentNode !== host) host.appendChild(input(b));
  return b;
}
function refresh(b) { b.ctx.refreshTaskComposer(); }
function hint(b) { refresh(b); return b.get('composer-task-status'); }
function press(b, over) {
  const state = {prevented: 0};
  const event = Object.assign({
    key: 'Enter', shiftKey: false, isComposing: false, keyCode: 13, repeat: false,
    preventDefault() { state.prevented += 1; },
  }, over || {});
  input(b).fire('keydown', event);
  return state;
}
function entry(over) {
  return Object.assign({
    session_id: 'session', client_request_id: '', queue_seq: 1, state: 'queued',
    active_child_job_id: '', accepted_at: 't0', dispatch_at: null, started_at: null,
    finished_at: null, updated_at: 't0', history_saved: false, clear_reason: '',
    job_id: 'root-1', queue_position: 1, query: '问题', attachments: [],
  }, over || {});
}
function rail() {
  return {pending: [], drops: [], addPending(id) { this.pending.push(id); },
    dropPending(id) { this.drops.push(id); }, reload() {}, sync() {}};
}

test('send button is hidden until the composer has text', () => {
  const b = mounted(browser());
  b.ctx._selectedAttachments = [];
  b.ctx._selectedAccountFileRefs = [];
  input(b).value = '';
  refresh(b);
  assert.equal(sendBtn(b).style.display, 'none');
  input(b).value = '   ';
  refresh(b);
  assert.equal(sendBtn(b).style.display, 'none', 'whitespace alone does not offer send');
  input(b).value = '正文';
  refresh(b);
  assert.equal(sendBtn(b).style.display, 'flex');
  input(b).value = '';
  refresh(b);
  assert.equal(sendBtn(b).style.display, 'none', 'clearing text hides send again');
  b.ctx._selectedAttachments = [{name: 'a.pdf', type: 'application/pdf'}];
  refresh(b);
  assert.equal(sendBtn(b).style.display, 'none', 'attachments alone do not offer send');
});

test('Enter suppresses the default newline and sends exactly once', async () => {
  const b = mounted(browser());
  const bctx = b;
  bctx.ctx._selectedAttachments = [];
  bctx.ctx._selectedAccountFileRefs = [];
  input(b).value = '核对资料';
  const state = press(b);
  await flush();
  assert.equal(state.prevented, 1);
  assert.equal(posts(b).length, 1);
  assert.equal(JSON.parse(posts(b)[0].opts.body).query, '核对资料');
});

test('Shift+Enter, IME composition, keyCode 229 and repeated Enter never send', async () => {
  const cases = [
    {name: 'Shift+Enter', over: {shiftKey: true}, prevented: 0},
    {name: 'composition', over: {isComposing: true}, prevented: 0},
    {name: 'keyCode 229', over: {keyCode: 229}, prevented: 0},
    {name: 'repeat', over: {repeat: true}, prevented: 1},
  ];
  for (const item of cases) {
    const b = mounted(browser());
    b.ctx._selectedAttachments = [];
    b.ctx._selectedAccountFileRefs = [];
    input(b).value = '中文输入';
    const state = press(b, item.over);
    await flush();
    assert.equal(posts(b).length, 0, item.name + ' must not send');
    assert.equal(state.prevented, item.prevented, item.name + ' default handling');
  }
});

test('whitespace-only Enter does not send and keeps the typed value', async () => {
  const b = mounted(browser());
  b.ctx._selectedAttachments = [];
  b.ctx._selectedAccountFileRefs = [];
  input(b).value = '   ';
  const state = press(b);
  await flush();
  assert.equal(state.prevented, 1);
  assert.equal(posts(b).length, 0);
  assert.equal(input(b).value, '   ');
});

test('attachment-only send shows a visible hint and preserves the attachment', async () => {
  const b = mounted(browser());
  b.ctx._selectedAttachments = [{name: 'a.pdf', type: 'application/pdf'}];
  b.ctx._selectedAccountFileRefs = [];
  input(b).value = '';
  const notice = hint(b);
  assert.equal(notice.textContent, '已选择附件；请填写正文后再发送。');
  assert.equal(notice.hidden, false);
  await b.ctx.sendMsg();
  await flush();
  assert.equal(posts(b).length, 0);
  assert.equal(b.ctx._selectedAttachments.length, 1, 'attachment selection survives');
  assert.match(hint(b).textContent, /已选择附件；请填写正文后再发送，附件会保留。/);
});

test('a confirmed rejection removes only this optimistic turn and restores an untouched draft', async () => {
  const b = mounted(browser());
  b.ctx._selectedAttachments = [];
  b.ctx._selectedAccountFileRefs = [];
  b.ctx.turnRail = rail();
  input(b).value = '会被拒绝';
  await b.ctx.sendMsg();
  await flush();
  const post = posts(b)[0];
  const body = JSON.parse(post.opts.body);
  assert.equal(messages(b).length, 2, 'optimistic user and bot rows exist while admitting');
  assert.deepEqual(b.ctx.turnRail.pending, [body.client_request_id]);
  b.reply(post, {detail: {code: 'invalid_query', message: '内容无效'}}, 400);
  await flush();
  assert.equal(messages(b).length, 0, 'both optimistic bubbles are removed');
  assert.equal(input(b).value, '会被拒绝', 'untouched draft is restored');
  assert.deepEqual(b.ctx.turnRail.drops, [body.client_request_id]);
  assert.equal(b.ctx.turnRail.pending.length, 1);
});

test('a confirmed rejection never overwrites a concurrently edited draft', async () => {
  const b = mounted(browser());
  b.ctx._selectedAttachments = [];
  b.ctx._selectedAccountFileRefs = [];
  b.ctx.turnRail = rail();
  input(b).value = '原问题';
  await b.ctx.sendMsg();
  await flush();
  const post = posts(b)[0];
  input(b).value = '用户正在编辑的新草稿';
  b.reply(post, {detail: {code: 'invalid_query', message: '内容无效'}}, 400);
  await flush();
  assert.equal(input(b).value, '用户正在编辑的新草稿');
  assert.equal(messages(b).length, 0);
});

for (const [status, error] of [[502, null], [500, {detail: {code: 'internal_error', message: 'upstream failed'}}], [408, null], [202, {unexpected: true}]]) {
  test(`ambiguous admission HTTP ${status} keeps the optimistic turn and reconciles the original ID`, async () => {
    const b = mounted(browser());
    b.ctx._selectedAttachments = [];
    b.ctx._selectedAccountFileRefs = [];
    b.ctx.turnRail = rail();
    input(b).value = '网络不确定';
    await b.ctx.sendMsg();
    await flush();
    const post = posts(b)[0];
    const body = JSON.parse(post.opts.body);
    b.reply(post, error, status);
    await flush();
    assert.equal(messages(b).length, 2, 'ambiguous outcome keeps the optimistic turn');
    assert.deepEqual(b.ctx.turnRail.drops, [], 'pending rail entry is preserved');
    const lookup = '/api/v2/session/session/task-by-request/' + body.client_request_id;
    const read = b.requests.filter(r => r.url === lookup).pop();
    assert.ok(read, 'reconciles by the original client_request_id');
    const botContent = messages(b)[1].querySelector('.content');
    assert.equal(botContent.dataset.requestId, body.client_request_id);
    b.reply(read, entry({client_request_id: body.client_request_id, job_id: 'root-7'}));
    await flush();
    assert.equal(posts(b).length, 1, 'reconciliation never re-posts the turn');
    assert.match(b.get('chat-container').querySelector('.turn-queue-state').textContent, /排队中/);
  });

}

test('rejection of an older message does not restore it over a newer send', async () => {
  const b = mounted(browser());
  b.ctx._selectedAttachments = [];
  b.ctx._selectedAccountFileRefs = [];
  input(b).value = '第一条消息';
  b.ctx.sendMsg();
  await flush();
  input(b).value = '第二条消息';
  b.ctx.sendMsg();
  await flush();
  assert.equal(messages(b).length, 4);
  const newer = messages(b).slice(2);
  b.reply(posts(b)[0], {detail: {code: 'invalid_query'}}, 400);
  await flush();
  assert.equal(input(b).value, '');
  assert.equal(messages(b).length, 2);
  assert.equal(messages(b)[0], newer[0], 'newer user bubble remains');
  assert.equal(messages(b)[1], newer[1], 'newer assistant bubble remains');
  assert.equal(JSON.parse(posts(b)[1].opts.body).query, '第二条消息');
  assert.equal(posts(b).length, 2);
});

test('selecting and removing an attachment returns focus to the composer', () => {
  const b = mounted(browser());
  b.ctx._selectedAttachments = [];
  b.ctx._selectedAccountFileRefs = [];
  let focused = 0;
  input(b).focus = () => { focused += 1; };
  const file = {name: 'note.pdf', type: 'application/pdf', size: 3};
  b.ctx.handleAttachmentSelection([file]);
  assert.equal(focused, 1, 'native file selection restores composer focus');
  assert.equal(b.ctx._selectedAttachments.length, 1);
  const remove = b.get('attachment-list').querySelector('.attachment-preview-remove');
  assert.ok(remove);
  remove.onclick();
  assert.equal(focused, 2, 'removing an attachment restores composer focus');
  assert.equal(b.ctx._selectedAttachments.length, 0);
});

test('a failed optimistic send revokes only the preview URL it created', async () => {
  const b = mounted(browser());
  b.ctx._selectedAccountFileRefs = [];
  b.ctx._selectedAttachments = [{name: 'first.png', type: 'image/png'}];
  input(b).value = '第一条图';
  b.ctx.sendMsg();
  await flush();
  b.readers[b.readers.length - 1].finish();
  await flush();
  const first = posts(b)[0];
  b.reply(first, entry({client_request_id: JSON.parse(first.opts.body).client_request_id}));
  await flush();
  assert.equal(b.ctx._sentAttachmentPreviewUrls.length, 1);
  const firstUrl = b.ctx._sentAttachmentPreviewUrls[0];
  b.ctx._selectedAttachments = [{name: 'second.png', type: 'image/png'}];
  input(b).value = '第二条图';
  b.ctx.sendMsg();
  await flush();
  b.readers[b.readers.length - 1].finish();
  await flush();
  const second = posts(b)[1];
  const secondUrl = b.ctx._sentAttachmentPreviewUrls[1];
  assert.notEqual(secondUrl, firstUrl);
  b.reply(second, {detail: {code: 'invalid_query', message: '内容无效'}}, 400);
  await flush();
  assert.deepEqual(b.revoked, [secondUrl], 'only the rejected row preview is revoked');
  assert.deepEqual([...b.ctx._sentAttachmentPreviewUrls], [firstUrl], 'earlier sent preview survives');
});

test('timeline keeps failed and retried attempts distinct, then freezes without hiding logs', () => {
  const b = browser();
  const view = b.ctx.CitrusTaskCards.createProcessView({
    container: b.get('process'), clean: value => String(value || ''),
    toolName: value => String(value || ''), statusText: value => String(value || ''),
    elapsed: ms => (Number(ms) / 1000).toFixed(1) + ' 秒',
  });
  view.action({tool_call_id: 't1', operation_id: 'op1', logical_key: 'op1', attempt: 1,
    status: 'failed', display_name: '资料检索', elapsed_ms: 1200});
  view.action({tool_call_id: 't2', operation_id: 'op1', logical_key: 'op1', attempt: 2,
    status: 'complete', display_name: '资料检索'});
  const rows = view.element.querySelectorAll('.term-line');
  assert.equal(rows.length, 2, 'one row per attempt');
  assert.equal(rows[0].dataset.attempt, '1');
  assert.equal(rows[1].dataset.attempt, '2');
  assert.match(rows[0].className, /is-failed/);
  assert.match(rows[1].className, /is-complete/);
  assert.match(rows[1].textContent, /第 2 次尝试/);
  const summary = view.element.querySelector('.research-action-summary');
  assert.match(summary.textContent, /使用了 1 个工具/);
  assert.match(summary.textContent, /1 次失败/);
  assert.match(summary.textContent, /共 2 次尝试/);
  view.action({tool_call_id: 't3', operation_id: 'op2', logical_key: 'op2', attempt: 1,
    status: 'running', display_name: '统计'});
  assert.match(summary.textContent, /执行中 · 统计.*2 个工具.*3 次尝试.*1 次失败/);
  assert.equal(summary.classList.contains('is-running'), true);
  view.pause(true);
  assert.equal(summary.classList.contains('is-running'), false);
  assert.match(summary.textContent, /2 个工具.*3 次尝试.*1 次失败/);
  view.pause(false);
  assert.equal(summary.classList.contains('is-running'), true);
  view.terminal('completed');
  assert.equal(view.element.classList.contains('is-terminal'), true);
  const frozen = view.element.querySelectorAll('.term-line');
  assert.equal(frozen.length, 3, 'terminal state keeps every log row');
  assert.match(frozen[2].className, /is-unconfirmed/);
});
