'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {browser, flush, reply} = require('./chat-turn-fixture.cjs');

function setup() {
  const b = browser();
  b.context.currentAccount = {user_id: 'A', username: 'A'};
  b.context.sessionId = 'missing-session';
  b.context.localStorage.setItem('citrus_session_id_A', 'missing-session');
  b.get('user-input').value = 'keep my draft';
  const file = {name: 'local.png', type: 'image/png', size: 2};
  b.context.handleAttachmentSelection([file]);
  b.context._selectedAccountFileRefs.push({file_id: 'account-ref'});
  b.context._historyMsgs = [{message_id: 9, content: 'old'}];
  b.context._historyHasMore = true;
  b.get('chat-container').textContent = 'old history';
  let reloads = 0;
  b.context.turnRail = {reload() { reloads++; }};
  return Object.assign(b, {file, reloads: () => reloads});
}
function creations(b) { return b.requests.filter(r => r.url === '/api/v1/session/new'); }
function histories(b) { return b.requests.filter(r => r.url.includes('/messages?')); }
function assertComposer(b) {
  assert.equal(b.get('user-input').value, 'keep my draft');
  assert.equal(b.context._selectedAttachments[0], b.file);
  assert.equal(b.context._selectedAccountFileRefs[0].file_id, 'account-ref');
  assert.equal(b.context._attachmentPreviewUrls.length, 1);
  assert.equal(b.revoked.includes(b.context._attachmentPreviewUrls[0]), false);
}

test('ensureSession replaces a missing stored session once and preserves composer', async () => {
  const b = setup();
  const pending = b.context.ensureSession();
  assert.equal(histories(b).length, 1);
  reply(histories(b)[0], {detail: '会话不存在或无权访问。'}, 404);
  await flush();
  assert.equal(creations(b).length, 1);
  assert.equal(b.context.sessionId, null);
  assert.equal(b.context.localStorage.getItem('citrus_session_id_A'), null);
  assert.equal(b.get('chat-container').textContent, '');
  assertComposer(b);
  reply(creations(b)[0], {session_id: 'replacement'});
  await pending;
  assert.equal(b.context.sessionId, 'replacement');
  assert.equal(b.context.localStorage.getItem('citrus_session_id_A'), 'replacement');
  assert.equal(histories(b).length, 1);
  assert.equal(b.reloads(), 1);
  assertComposer(b);
});

for (const failure of [401, 403, 500, 503, 'network']) {
  test(`history ${failure} keeps the current session and draft`, async () => {
    const b = setup();
    const pending = b.context.ensureSession();
    if (failure === 'network') histories(b)[0].reject(new Error('offline'));
    else reply(histories(b)[0], {detail: 'failed'}, failure);
    await pending;
    assert.equal(b.context.sessionId, 'missing-session');
    assert.equal(b.context.localStorage.getItem('citrus_session_id_A'), 'missing-session');
    const chat = b.get('chat-container');
    assert.match(chat.textContent, /^old history/);
    const retries = chat.querySelectorAll('.history-retry');
    assert.equal(retries.length, 1);
    assert.match(retries[0].textContent, /点击重试/);
    assert.equal(creations(b).length, 0);
    assertComposer(b);
  });
}

test('a fresh restore aborts older pagination and concurrent initialization shares the replacement', async () => {
  const b = setup();
  const pagination = b.context.loadEarlierHistory();
  const earlier = histories(b)[0];
  assert.match(earlier.url, /&before=9$/);
  const restore = b.context.restoreSessionHistory();
  assert.equal(earlier.opts.signal.aborted, true);
  const latest = histories(b)[1];
  assert.equal(await b.context.loadEarlierHistory(), false);
  assert.equal(histories(b).length, 2);
  reply(latest, {}, 404);
  await flush();
  assert.equal(creations(b).length, 1);
  const ensure = b.context.ensureSession();
  reply(earlier, {}, 404);
  assert.equal(await pagination, false);
  assert.equal(creations(b).length, 1);
  reply(creations(b)[0], {session_id: 'replacement'});
  await flush();
  assert.equal(histories(b).length, 3);
  const replacement = histories(b)[2];
  assert.match(replacement.url, /\/replacement\/messages\?/);
  reply(replacement, {messages: [], turns: []});
  await Promise.all([restore, ensure]);
  assert.equal(creations(b).length, 1);
  assert.equal(b.context.sessionId, 'replacement');
  assertComposer(b);
});

for (const change of ['account', 'session']) {
  test(`late 404 cannot replace a different ${change}`, async () => {
    const b = setup();
    const pending = b.context.restoreSessionHistory();
    if (change === 'account') {
      b.context.advanceAuthEpoch();
      b.context.currentAccount = {user_id: 'B', username: 'B'};
    }
    b.context.sessionId = 'chosen-session';
    reply(histories(b)[0], {}, 404);
    await pending;
    assert.equal(b.context.sessionId, 'chosen-session');
    assert.equal(creations(b).length, 0);
    assertComposer(b);
  });

  test(`late creation JSON cannot overwrite a different ${change}`, async () => {
    const b = setup();
    const pending = b.context.restoreSessionHistory();
    reply(histories(b)[0], {}, 404);
    await flush();
    let deliver;
    creations(b)[0].resolve({ok: true, json: () => new Promise(resolve => { deliver = resolve; })});
    await flush();
    if (change === 'account') {
      b.context.advanceAuthEpoch();
      b.context.currentAccount = {user_id: 'B', username: 'B'};
    }
    b.context.sessionId = 'chosen-session';
    deliver({session_id: 'late-replacement'});
    await pending;
    assert.equal(b.context.sessionId, 'chosen-session');
    assert.notEqual(b.context.localStorage.getItem('citrus_session_id_B'), 'late-replacement');
    assertComposer(b);
  });
}

for (const failure of [404, 503, 'invalid', 'network']) {
  test(`replacement creation ${failure} does not loop or adopt an error body`, async () => {
    const b = setup();
    const pending = b.context.ensureSession();
    reply(histories(b)[0], {}, 404);
    await flush();
    if (failure === 'network') creations(b)[0].reject(new Error('offline'));
    else if (failure === 'invalid') reply(creations(b)[0], {detail: 'no session'});
    else reply(creations(b)[0], {session_id: 'must-not-adopt'}, failure);
    await pending;
    assert.equal(b.context.sessionId, null);
    assert.equal(b.context.localStorage.getItem('citrus_session_id_A'), null);
    assert.equal(creations(b).length, 1);
    assert.equal(histories(b).length, 1);
    assertComposer(b);
  });
}
