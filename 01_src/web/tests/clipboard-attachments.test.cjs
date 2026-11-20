'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {File} = require('node:buffer');
const {browser, flush} = require("./frontend-harness.cjs");
function setup() {
  const b = browser('app.js');
  b.ctx.currentAccount = {user_id: 'A', username: 'A'};
  b.ctx.sessionId = 'A-session';
  b.ctx.File = File;
  b.alerts = [];
  b.ctx.alert = text => b.alerts.push(text);
  return b;
}
function paste(b, {files = [], items = [], text = '', defaultPrevented = false} = {}) {
  const event = {defaultPrevented, clipboardData: {files, items, getData: type => type === 'text/plain' ? text : ''},
    prevented: false, preventDefault() { event.prevented = true; }};
  b.get('user-input').fire('paste', event);
  return event;
}
const png = (name = 'screenshot.png') => new File(['image-bytes'], name, {type: 'image/png'});

test('screenshot paste queues one preview through existing intake without upload or clipboard permissions', () => {
  const b = setup(), image = png();
  const event = paste(b, {files: [image], items: [{kind: 'file', getAsFile() { throw new Error('duplicate clipboard view read'); }}]});
  assert.equal(event.prevented, true);
  assert.equal(b.ctx._selectedAttachments.length, 1);
  assert.equal(b.ctx._selectedAttachments[0], image);
  assert.equal(b.get('attachment-list').childElementCount, 1);
  assert.equal(b.requests.length, 0);
});

test('clipboard items fallback ignores null items and retains distinct files', () => {
  const b = setup(), first = png(), second = new File(['a,b'], 'data.csv', {type: 'text/csv'});
  paste(b, {items: [{kind: 'string'}, {kind: 'file', getAsFile: () => null},
    {kind: 'file', getAsFile: () => first}, {kind: 'file', getAsFile: () => second}]});
  assert.equal(b.ctx._selectedAttachments.length, 2);
  assert.equal(b.ctx._selectedAttachments[1], second);
});

test('unnamed clipboard image gets a supported filename without changing bytes', async () => {
  const b = setup(), image = png('');
  paste(b, {files: [image]});
  const selected = b.ctx._selectedAttachments[0];
  assert.match(selected.name, /^clipboard-\d+-1\.png$/);
  assert.equal(selected.type, 'image/png');
  assert.equal(await selected.text(), await image.text());
});

for (const text of ['plain text\n中文', 'https://example.org/image.png', 'C:\\Users\\User\\photo.png']) {
  test('text-only paste remains native: ' + text.split('\n')[0], () => {
    const b = setup(); b.get('user-input').value = 'existing draft';
    assert.equal(paste(b, {text}).prevented, false);
    assert.equal(b.get('user-input').value, 'existing draft');
    assert.equal(b.ctx._selectedAttachments.length, 0);
    assert.equal(b.requests.length, 0);
  });
}

test('mixed image and text preserves native text insertion and existing selections', () => {
  const b = setup(); b.ctx.handleAttachmentSelection([png('first.png')]);
  b.get('user-input').value = 'replace selected text';
  assert.equal(paste(b, {files: [png()], text: 'caption\n第二行'}).prevented, false);
  assert.equal(b.ctx._selectedAttachments.length, 2);
  assert.equal(b.get('user-input').value, 'replace selected text');
});

test('unsupported paste and count/size overflow use existing admission without losing selections', () => {
  const b = setup(); b.ctx.handleAttachmentSelection([png('keep.png')]);
  paste(b, {files: [new File(['x'], 'script.exe', {type: 'application/octet-stream'})]});
  assert.equal(b.ctx._selectedAttachments.length, 1);
  paste(b, {files: Array.from({length: 8}, (_, i) => png(i + '.png'))});
  assert.equal(b.ctx._selectedAttachments.length, 1);
  paste(b, {files: [{name: 'too-big.png', type: 'image/png', size: 20 * 1024 * 1024 + 1}]});
  assert.equal(b.ctx._selectedAttachments.length, 1);
  assert.equal(b.alerts.length, 3);
});

test('paste is ignored after logout and when another handler already owns the event', () => {
  const b = setup();
  assert.equal(paste(b, {files: [png()], defaultPrevented: true}).prevented, false);
  assert.equal(b.ctx._selectedAttachments.length, 0);
  b.ctx.resetAccountState();
  assert.equal(paste(b, {files: [png()]}).prevented, false);
  assert.equal(b.ctx._selectedAttachments.length, 0);
  assert.equal(b.requests.length, 0);
});

test('pasted attachment is encoded only on Send and logout fences the in-flight reader', async () => {
  const b = setup(); paste(b, {files: [png()]});
  assert.equal(b.readers.length, 0);
  b.get('user-input').value = 'read this screenshot';
  await b.ctx.sendMsg();
  assert.equal(b.readers.length, 1);
  b.ctx.resetAccountState();
  assert.equal(b.readers[0].aborted, true);
  await flush();
  assert.equal(b.requests.filter(r => r.url === '/api/v2/chat').length, 0);
  assert.equal(b.ctx._selectedAttachments.length, 0);
});
