'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');

// Reuse the existing browser doubles without registering its test suite. Both
// suites execute the entire production app, never an extracted/copied function.
const harness = {exports: {}};
vm.runInNewContext(readFileSync(join(__dirname, 'frontend-isolation.test.cjs'), 'utf8') +
  '\nmodule.exports = {browser, flush};', {
  module: harness, __dirname, console, AbortController, DOMException, TextDecoder,
  URLSearchParams, TextEncoder,
  require(name) { return name === 'node:test' ? {test() {}} : require(name); }
}, {filename: 'frontend-isolation.test.cjs'});
const {browser, flush} = harness.exports;

function setup() {
  const b = browser('app.js');
  b.ctx.currentAccount = {user_id: 'A', username: 'A'};
  b.ctx.sessionId = 'missing-session';
  b.ctx.localStorage.setItem('citrus_session_id_A', 'missing-session');
  b.get('user-input').value = 'keep my draft';
  const file = {name: 'local.png', type: 'image/png', size: 2};
  b.ctx.handleAttachmentSelection([file]);
  b.ctx._selectedAccountFileRefs.push({file_id: 'account-ref'});
  b.eval('_historyMsgs = [{message_id: 9, content:"old"}]; _historyHasMore = true;');
  b.get('chat-container').textContent = 'old history';
  let reloads = 0;
  b.ctx.turnRail = {reload() { reloads++; }};
  return Object.assign(b, {file, reloads: () => reloads});
}
function creations(b) { return b.requests.filter(r => r.url === '/api/v1/session/new'); }
function histories(b) { return b.requests.filter(r => r.url.includes('/messages?')); }
function assertComposer(b) {
  assert.equal(b.get('user-input').value, 'keep my draft');
  assert.equal(b.ctx._selectedAttachments[0], b.file);
  assert.equal(b.ctx._selectedAccountFileRefs[0].file_id, 'account-ref');
  assert.equal(b.ctx._attachmentPreviewUrls.length, 1);
  assert.equal(b.revoked.includes(b.ctx._attachmentPreviewUrls[0]), false);
}

test('ensureSession replaces a missing stored session once and preserves composer', async () => {
  const b = setup();
  const pending = b.ctx.ensureSession();
  assert.equal(histories(b).length, 1);
  b.reply(histories(b)[0], {detail: '会话不存在或无权访问。'}, 404);
  await flush();
  assert.equal(creations(b).length, 1);
  assert.equal(b.ctx.sessionId, null);
  assert.equal(b.ctx.localStorage.getItem('citrus_session_id_A'), null);
  assert.equal(b.get('chat-container').textContent, '');
  assertComposer(b);
  b.reply(creations(b)[0], {session_id: 'replacement'});
  await pending;
  assert.equal(b.ctx.sessionId, 'replacement');
  assert.equal(b.ctx.localStorage.getItem('citrus_session_id_A'), 'replacement');
  assert.equal(histories(b).length, 1);
  assert.equal(b.reloads(), 1);
  assertComposer(b);
});

for (const failure of [401, 403, 500, 503, 'network']) {
  test(`history ${failure} keeps the current session and draft`, async () => {
    const b = setup();
    const pending = b.ctx.ensureSession();
    if (failure === 'network') histories(b)[0].reject(new Error('offline'));
    else b.reply(histories(b)[0], {detail: 'failed'}, failure);
    await pending;
    assert.equal(b.ctx.sessionId, 'missing-session');
    assert.equal(b.ctx.localStorage.getItem('citrus_session_id_A'), 'missing-session');
    assert.equal(b.get('chat-container').textContent, 'old history');
    assert.equal(creations(b).length, 0);
    assertComposer(b);
  });
}

test('concurrent history failures share one replacement creation', async () => {
  const b = setup();
  const first = b.ctx.restoreSessionHistory();
  const second = b.ctx.loadEarlierHistory();
  b.reply(histories(b)[0], {}, 404);
  b.reply(histories(b)[1], {}, 404);
  await flush();
  const ensure = b.ctx.ensureSession();
  assert.equal(creations(b).length, 1);
  b.reply(creations(b)[0], {session_id: 'replacement'});
  await flush();
  // Explicit ensure validates the replacement once; its ordinary empty history
  // must leave the draft and local attachment selections intact.
  b.reply(histories(b).at(-1), {messages: [], turns: []});
  await Promise.all([first, second, ensure]);
  assert.equal(creations(b).length, 1);
  assertComposer(b);
});

for (const change of ['account', 'session']) {
  test(`late 404 cannot replace a different ${change}`, async () => {
    const b = setup();
    const pending = b.ctx.restoreSessionHistory();
    if (change === 'account') {
      b.ctx.advanceAuthEpoch();
      b.ctx.currentAccount = {user_id: 'B', username: 'B'};
    }
    b.ctx.sessionId = 'chosen-session';
    b.reply(histories(b)[0], {}, 404);
    await pending;
    assert.equal(b.ctx.sessionId, 'chosen-session');
    assert.equal(creations(b).length, 0);
    assertComposer(b);
  });

  test(`late creation JSON cannot overwrite a different ${change}`, async () => {
    const b = setup();
    const pending = b.ctx.restoreSessionHistory();
    b.reply(histories(b)[0], {}, 404);
    await flush();
    let deliver;
    creations(b)[0].resolve({ok: true, json: () => new Promise(resolve => { deliver = resolve; })});
    await flush();
    if (change === 'account') {
      b.ctx.advanceAuthEpoch();
      b.ctx.currentAccount = {user_id: 'B', username: 'B'};
    }
    b.ctx.sessionId = 'chosen-session';
    deliver({session_id: 'late-replacement'});
    await pending;
    assert.equal(b.ctx.sessionId, 'chosen-session');
    assert.notEqual(b.ctx.localStorage.getItem('citrus_session_id_B'), 'late-replacement');
    assertComposer(b);
  });
}

for (const failure of [404, 503, 'invalid', 'network']) {
  test(`replacement creation ${failure} does not loop or adopt an error body`, async () => {
    const b = setup();
    const pending = b.ctx.ensureSession();
    b.reply(histories(b)[0], {}, 404);
    await flush();
    if (failure === 'network') creations(b)[0].reject(new Error('offline'));
    else if (failure === 'invalid') b.reply(creations(b)[0], {detail: 'no session'});
    else b.reply(creations(b)[0], {session_id: 'must-not-adopt'}, failure);
    await pending;
    assert.equal(b.ctx.sessionId, null);
    assert.equal(b.ctx.localStorage.getItem('citrus_session_id_A'), null);
    assert.equal(creations(b).length, 1);
    assert.equal(histories(b).length, 1);
    assertComposer(b);
  });
}
