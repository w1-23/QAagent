'use strict';
// Run production scripts in isolated browser-like realms. No source extraction,
// copied reducers or string assertions: requests, readers and UI events are real
// entry points; only browser/network APIs are doubles.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
class Node {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null;
    this.style = {setProperty() {}}; this.dataset = {}; this.attrs = {};
    this.value = ''; this.hidden = false; this.disabled = false; this.className = '';
    this.listeners = {}; this._text = ''; this.clientWidth = 400;
    this.classList = {
      contains: name => this.className.split(/\s+/).includes(name),
      add: (...names) => { this.className = [...new Set(this.className.split(/\s+/).concat(names))].join(' ').trim(); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(n => !names.includes(n)).join(' '); },
      toggle: (name, on) => { on = on === undefined ? !this.classList.contains(name) : on; this.classList[on ? 'add' : 'remove'](name); return on; }
    };
  }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === 'class') this.className = String(value);
    if (name === 'value') this.value = String(value);
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  removeAttribute(name) { delete this.attrs[name]; delete this[name]; }
  appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
  replaceChildren(...children) { this.children.forEach(n => { n.parentNode = null; }); this.children = []; this._text = ''; children.forEach(n => this.appendChild(n)); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(n => n !== this); this.parentNode = null; }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  set innerHTML(html) {
    this.replaceChildren(); this._html = html;
    // Enough markup parsing for message wrappers and figure property inputs.
    for (const m of String(html).matchAll(/<([a-z][a-z0-9-]*)\b([^>]*)>/gi)) {
      const node = new Node(m[1]);
      for (const a of m[2].matchAll(/([\w-]+)="([^"]*)"/g)) node.setAttribute(a[1], a[2]);
      node.checked = /\bchecked\b/.test(m[2]); this.appendChild(node);
    }
  }
  get innerHTML() { return this._html || ''; }
  get firstChild() { return this.children[0] || null; }
  get childElementCount() { return this.children.length; }
  matches(selector) {
    if (selector.startsWith('.')) return selector.slice(1).split('.').every(n => this.classList.contains(n));
    const attr = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
    if (attr) return this.getAttribute(attr[1]) !== null && (attr[2] === undefined || this.getAttribute(attr[1]) === attr[2]);
    return this.tagName.toLowerCase() === selector;
  }
  querySelectorAll(selector) { return this.children.flatMap(n => (n.matches(selector) ? [n] : []).concat(n.querySelectorAll(selector))); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type, event = {}) { return (this.listeners[type] || []).map(fn => fn({target: this, ...event})); }
  focus() {} scrollIntoView() {} contains(node) { return node === this || this.children.some(n => n.contains(node)); }
}
function storage() {
  const data = new Map();
  return {getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k), key: i => [...data.keys()][i], get length() { return data.size; }};
}
function browser(script) {
  const nodes = new Map(), requests = [], readers = [], revoked = [], navigations = [], timers = new Map();
  let timerId = 0;
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  const document = {readyState: 'loading', getElementById: get, createElement: tag => new Node(tag),
    querySelector: () => null, querySelectorAll: () => [], documentElement: new Node('html'), body: new Node('body'),
    listeners: {}, addEventListener: Node.prototype.addEventListener, removeEventListener() {}};
  class Reader {
    constructor() { readers.push(this); }
    readAsDataURL(file) { this.file = file; }
    abort() { this.aborted = true; this.file = null; this.onabort?.(); }
    finish(data = 'data:application/pdf;base64,QQ==') { this.result = data; this.onload?.(); this.file = null; }
  }
  const sandbox = {console, document, AbortController, DOMException, TextDecoder, URLSearchParams,
    URL: {createObjectURL: () => 'blob:' + Math.random(), revokeObjectURL: url => revoked.push(url)},
    FileReader: Reader, localStorage: storage(), sessionStorage: storage(),
    location: {search: '', hash: '', pathname: '/', replace: url => navigations.push(url)},
    history: {replaceState() {}}, navigator: {}, performance: {now: () => 1},
    innerWidth: 1280, confirm: () => true, alert() {}, prompt: () => null,
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id),
    setInterval: fn => { timers.set(++timerId, fn); return timerId; }, clearInterval: id => timers.delete(id),
    requestAnimationFrame: fn => { timers.set(++timerId, fn); return timerId; },
    Event: class { constructor(type) { this.type = type; } },
    CustomEvent: class { constructor(type, opts) { this.type = type; this.detail = opts.detail; } },
    addEventListener() {}, dispatchEvent() {},
    fetch: (url, opts = {}) => { const pending = deferred(); requests.push({url, opts, ...pending}); return pending.promise; }
  };
  sandbox.window = sandbox;
  const ctx = vm.createContext(sandbox);
  if (script === 'app.js') {
    for (const module of ['task-state.js', 'task-transport.js', 'task-cards.js', 'task-controls.js']) {
      vm.runInContext(readFileSync(join(__dirname, '../assets', module), 'utf8'), ctx, {filename: module});
    }
  }
  vm.runInContext(readFileSync(join(__dirname, '../assets', script), 'utf8'), ctx, {filename: script});
  return {ctx, get, document, requests, readers, revoked, navigations, timers,
    reply(req, data, status = 200) { req.resolve({ok: status < 400, status, json: async () => data}); },
    eval(code) { return vm.runInContext(code, ctx); }};
}
function login(b, id = 'A') { b.ctx.currentAccount = {user_id: id, username: id}; b.ctx.sessionId = id + '-session'; }
function figure(id, color = '#111111') {
  return {figure_id: id, title: id, render_version: 1, source_kind: 'spec', objects: 1,
    manifest: {figure_size_px: [400, 300], objects: [{gid: 'title', role: 'title', editable: ['color'], color}]}};
}
async function figures() {
  const b = browser('figures.js'); b.document.listeners.DOMContentLoaded[0]();
  b.reply(b.requests[0], {projects: []}); await flush(); return b;
}
function openFigure(b, id) {
  const button = new Node('button'); button.setAttribute('data-open', id);
  b.get('projectList').fire('click', {target: button}); return b.requests.at(-1);
}
async function showFigure(b, id) { const req = openFigure(b, id); b.reply(req, figure(id)); await flush(); selectColor(b, '#222222'); }
function selectColor(b, color) {
  b.get('objectSelect').value = 'title'; b.get('objectSelect').fire('change');
  const input = b.get('propertyPanel').querySelector('[data-prop="color"]'); input.value = color;
  b.get('propertyPanel').fire('input', {target: input});
}

test('logout synchronously releases chat, files, refs, object URLs and account caches', async () => {
  const b = browser('app.js'); login(b);
  const file = {name: 'private.png', type: 'image/png', size: 2};
  b.ctx.handleAttachmentSelection([file]); b.ctx._selectedAccountFileRefs.push({file_id: 'private-ref'});
  b.ctx.appendSentAttachmentPreviews(b.get('chat-container'), [file]);
  const urls = [...b.ctx._attachmentPreviewUrls, ...b.ctx._sentAttachmentPreviewUrls];
  b.eval("_historyMsgs = [{content:'A secret'}]; _historyTurns = [{secret:1}]; _sessions = [{title:'A'}]; _ctxData = {secret:1}; _sentSnapshots = [{secret:1}]; _runtimeConfig = {secret:1};");
  b.get('user-input').value = 'private draft'; b.get('attachment-input').value = 'file';
  b.ctx.localStorage.setItem('citrus_session_id_A', 'A-session'); b.ctx.localStorage.setItem('citrus_theme', 'dark');
  const pending = b.ctx.loadSessions(); const old = b.requests.at(-1);
  const logout = b.ctx.logoutAccount();
  assert.equal(old.opts.signal.aborted, true);
  assert.equal(b.get('chat-container').childElementCount, 0);
  assert.equal(b.ctx._selectedAttachments.length, 0); assert.equal(b.ctx._selectedAccountFileRefs.length, 0);
  assert.equal(b.ctx._historyMsgs.length, 0); assert.equal(b.ctx._historyTurns, null);
  assert.equal(b.ctx._runtimeConfig, null); assert.equal(b.ctx._sentSnapshots.length, 0);
  assert.equal(b.get('user-input').value, ''); assert.equal(b.get('attachment-input').value, '');
  assert.deepEqual(b.revoked.sort(), urls.sort());
  assert.equal(b.ctx.localStorage.getItem('citrus_session_id_A'), null);
  assert.equal(b.ctx.localStorage.getItem('citrus_theme'), 'dark');
  b.reply(old, {sessions: [{title: 'late A'}]}); await pending;
  assert.equal(b.ctx._sessions.length, 0);
  b.reply(b.requests.at(-1), {}); await logout;
  assert.deepEqual(b.navigations, ['/']);
});

test('late history JSON and earlier pages cannot populate another account; empty history clears DOM', async () => {
  const b = browser('app.js'); login(b);
  const body = deferred(); const restore = b.ctx.restoreSessionHistory();
  b.requests[0].resolve({ok: true, json: () => body.promise}); await flush();
  b.ctx.resetAccountState(); login(b, 'B');
  body.resolve({messages: [{role: 'user', content: 'private A'}]}); await restore;
  assert.equal(b.ctx._historyMsgs.length, 0);
  b.get('chat-container').textContent = 'stale content';
  const empty = b.ctx.restoreSessionHistory(); b.reply(b.requests.at(-1), {messages: [], turns: []}); await empty;
  assert.equal(b.get('chat-container').textContent, '');
  b.eval('_historyMsgs = [{message_id: 9}]; _historyHasMore = true;');
  const older = b.ctx.loadEarlierHistory(); const req = b.requests.at(-1);
  b.ctx.resetAccountState(); login(b, 'C'); b.reply(req, {messages: [{message_id: 1, content: 'B'}]}); await older;
  assert.equal(b.ctx._historyMsgs.length, 0);
});

test('late account preferences and avatar results cannot mutate a new account', async () => {
  const b = browser('app.js'); login(b);
  const pending = b.ctx.saveAccountAvatar(); const req = b.requests.at(-1); const body = deferred();
  req.resolve({ok: true, json: () => body.promise}); await flush();
  b.ctx.resetAccountState(); login(b, 'B'); b.get('settingsAvatarError').textContent = 'B status';
  body.resolve({user: {user_id: 'A'}}); await pending;
  assert.equal(b.ctx.currentAccount.user_id, 'B'); assert.equal(b.get('settingsAvatarError').textContent, 'B status');
  const prefs = b.ctx.loadAccountPreferences(); const prefReq = b.requests.at(-1);
  b.ctx.resetAccountState(); login(b, 'C'); b.get('accountPreferenceStatus').textContent = 'C status';
  prefReq.reject(new Error('late A error')); await prefs;
  assert.equal(b.get('accountPreferenceStatus').textContent, 'C status');
});

test('logout while encoding attachments prevents POST; next account sends no old attachments or refs', async () => {
  const b = browser('app.js'); login(b);
  b.ctx.handleAttachmentSelection([{name: 'secret.pdf', type: 'application/pdf', size: 2}]);
  b.ctx._selectedAccountFileRefs.push({file_id: 'A-ref'}); b.get('user-input').value = 'read A';
  await b.ctx.sendMsg(); assert.equal(b.readers.length, 1);
  b.ctx.resetAccountState(); assert.equal(b.readers[0].aborted, true);
  await flush(); assert.equal(b.requests.filter(r => r.url === '/api/v2/chat').length, 0);
  login(b, 'B'); b.get('user-input').value = 'hello B'; await b.ctx.sendMsg(); await flush();
  const request = b.requests.find(r => r.url === '/api/v2/chat');
  const payload = JSON.parse(request.opts.body);
  assert.equal(payload.session_id, 'B-session'); assert.deepEqual(payload.attachments, []); assert.deepEqual(payload.file_refs, []);
  b.ctx.resetAccountState(); b.reply(request, {}, 401); await flush();
});

test('late stream chunk does not rewrite session, history, DOM, or send controls', async () => {
  const b = browser('app.js'); login(b); b.get('user-input').value = 'question';
  await b.ctx.sendMsg(); await flush(); const req = b.requests.at(-1);
  const chunk = deferred(); let cancelled = false;
  req.resolve({ok: true, body: {getReader: () => ({read: () => chunk.promise, cancel: async () => { cancelled = true; }})}}); await flush();
  b.ctx.resetAccountState(); login(b, 'B'); b.get('send-btn').disabled = true;
  chunk.resolve({done: false, value: new TextEncoder().encode('event: done\ndata: {"session_id":"A-session","answer":"secret"}\n\n')}); await flush();
  assert.equal(cancelled, true); assert.equal(b.ctx.sessionId, 'B-session');
  assert.equal(b.get('chat-container').childElementCount, 0); assert.equal(b.get('send-btn').disabled, true);
});

test('PDF local reader is aborted before it can submit old file to new session', async () => {
  const b = browser('app.js'); login(b);
  const pending = b.ctx.reviewUploadedPdf({name: 'A.pdf'}, new Node());
  b.ctx.resetAccountState(); login(b, 'B'); await pending;
  assert.equal(b.readers[0].aborted, true); assert.equal(b.requests.length, 0);
});

test('figure opens are ordered by intent even when abort is ignored by transport', async () => {
  const b = await figures(); const a = openFigure(b, 'A'); const next = openFigure(b, 'B');
  assert.equal(a.opts.signal.aborted, true);
  b.reply(next, figure('B')); await flush(); selectColor(b, '#abcdef');
  b.reply(a, figure('A')); await flush();
  assert.equal(b.get('projectTitle').textContent, 'B');
  assert.equal(b.get('propertyPanel').querySelector('[data-prop="color"]').value, '#abcdef');
});

test('save A completion cannot overwrite B edits or enable B pending save', async () => {
  const b = await figures(); await showFigure(b, 'A');
  const savingA = b.get('saveButton').fire('click')[0]; const requestA = b.requests.at(-1);
  await showFigure(b, 'B'); selectColor(b, '#abcdef');
  const savingB = b.get('saveButton').fire('click')[0]; const requestB = b.requests.at(-1);
  b.reply(requestA, {manifest: figure('A').manifest, render_version: 8}); await savingA;
  assert.equal(b.get('projectTitle').textContent, 'B'); assert.equal(b.get('saveButton').disabled, true);
  assert.equal(b.get('propertyPanel').querySelector('[data-prop="color"]').value, '#abcdef');
  b.reply(requestB, {manifest: figure('B', '#abcdef').manifest, render_version: 2}); await flush();
  b.reply(b.requests.at(-1), {projects: []}); await savingB;
  assert.equal(b.get('saveButton').disabled, true);
});

test('edits made during save survive and are submitted by the next save', async () => {
  const b = await figures(); await showFigure(b, 'A');
  const saving = b.get('saveButton').fire('click')[0]; const req = b.requests.at(-1);
  selectColor(b, '#ff0000');
  b.reply(req, {manifest: figure('A', '#222222').manifest, render_version: 2}); await flush();
  b.reply(b.requests.at(-1), {projects: []}); await saving;
  assert.equal(b.get('propertyPanel').querySelector('[data-prop="color"]').value, '#ff0000');
  const next = b.get('saveButton').fire('click')[0]; const nextReq = b.requests.at(-1);
  assert.equal(JSON.parse(nextReq.opts.body).overrides[0].value, '#ff0000');
  assert.equal(JSON.parse(nextReq.opts.body).expected_revision, 2);
  b.reply(nextReq, {manifest: figure('A', '#ff0000').manifest, render_version: 3}); await flush();
  b.reply(b.requests.at(-1), {projects: []}); await next;
  assert.equal(b.get('propertyPanel').querySelector('[data-prop="color"]').value, '#ff0000');
});

test('delete A completion cannot hide B or clear its unsaved edit', async () => {
  const b = await figures(); await showFigure(b, 'A');
  const deleting = b.get('deleteButton').fire('click')[0]; const req = b.requests.at(-1);
  await showFigure(b, 'B'); selectColor(b, '#fedcba');
  b.reply(req, {}); await deleting;
  assert.equal(b.get('workspace').hidden, false); assert.equal(b.get('projectTitle').textContent, 'B');
  assert.equal(b.get('propertyPanel').querySelector('[data-prop="color"]').value, '#fedcba');
});

test('memory records tab reads only records and does not load preferences', async () => {
  const b = browser('app.js'); login(b);
  const pending = b.ctx.openMemorySettings('records');
  assert.equal(b.requests.length, 1); assert.equal(b.requests[0].url, '/api/v2/auth/memory');
  b.reply(b.requests[0], {facts: []}); await pending;
  assert.equal(b.requests.length, 1);
});

test('retired document navigates after next login instead of reusing private library closures', async () => {
  const b = browser('app.js'); login(b);
  const logout = b.ctx.logoutAccount(); b.requests[0].reject(new Error('offline')); await logout;
  b.get('authUsername').value = 'B'; b.get('authPassword').value = 'test';
  const pending = b.ctx.authenticate('login');
  b.reply(b.requests.at(-1), {user: {user_id: 'B'}}); await pending;
  assert.deepEqual(b.navigations, ['/']); assert.equal(b.ctx.currentAccount, null);
  assert.equal(b.requests.length, 2); assert.equal(b.get('authGate').hidden, false);
});

test('avatar image failure after logout cannot restore the previous username', () => {
  const b = browser('app.js'); login(b);
  b.ctx.showAuthGate(false);
  const container = b.get('accountAvatar'), image = container.firstChild;
  b.ctx.resetAccountState(); login(b, 'B'); b.ctx.showAuthGate(false);
  const current = container.firstChild; image.onerror();
  assert.equal(container.firstChild, current); assert.equal(container.textContent, '');
});

test('save sends captured expected revision and preserves edits on a revision conflict', async () => {
  const b = await figures(); await showFigure(b, 'A'); selectColor(b, '#123456');
  const pending = b.get('saveButton').fire('click')[0]; const req = b.requests.at(-1);
  assert.equal(JSON.parse(req.opts.body).expected_revision, 1);
  b.reply(req, {detail: {code: 'revision_conflict'}}, 409); await pending;
  assert.equal(b.get('propertyPanel').querySelector('[data-prop="color"]').value, '#123456');
  assert.equal(b.get('saveButton').disabled, false);
  assert.match(b.get('toast').textContent, /本次修改尚未保存/);
});

test('explicitly read-only figures reject save and drag while legacy detail remains editable', async () => {
  const b = await figures(); const req = openFigure(b, 'script'); const detail = figure('script');
  detail.editable = false; detail.manifest.objects[0].editable.push('offset_px');
  detail.manifest.objects[0].bbox = {x0: 0, y0: 0, x1: 100, y1: 50};
  b.reply(req, detail); await flush();
  assert.equal(b.get('saveButton').disabled, true);
  assert.equal(b.get('propertyPanel').querySelector('input'), null);
  const box = b.get('overlay').firstChild;
  assert.equal(box.classList.contains('is-draggable'), false);
  b.get('overlay').fire('mousedown', {target: box, clientX: 1, clientY: 1, preventDefault() {}});
  b.document.listeners.mousemove[0]({clientX: 200, clientY: 200});
  const count = b.requests.length; b.get('saveButton').fire('click');
  assert.equal(b.requests.length, count);
  await showFigure(b, 'legacy'); assert.equal(b.get('saveButton').disabled, false);
});

test('stale figure authorization error cannot redirect a newly opened figure', async () => {
  const b = await figures(); const a = openFigure(b, 'A'); await showFigure(b, 'B');
  b.reply(a, {}, 401); await flush();
  assert.deepEqual(b.navigations, []); assert.equal(b.get('projectTitle').textContent, 'B');
});

test('save from an earlier visit to the same figure is stale; late errors stay silent', async () => {
  const b = await figures(); await showFigure(b, 'A');
  const saving = b.get('saveButton').fire('click')[0]; const old = b.requests.at(-1);
  await showFigure(b, 'B'); await showFigure(b, 'A'); selectColor(b, '#123456');
  b.get('toast').textContent = 'current'; old.reject(new Error('old failure')); await saving;
  assert.equal(b.get('toast').textContent, 'current');
  assert.equal(b.get('propertyPanel').querySelector('[data-prop="color"]').value, '#123456');
});

test('idempotent chat replay preserves attachments and uses the existing job recovery pointers', async () => {
  const b = browser('app.js'); login(b); b.get('user-input').value = 'question';
  const file = {name: 'input.pdf', type: 'application/pdf', size: 2};
  b.ctx.handleAttachmentSelection([file]);
  await b.ctx.sendMsg(); b.readers.at(-1).finish(); await flush(); const post = b.requests.at(-1);
  post.resolve({ok: true, status: 200, headers: {get: () => 'application/json'},
    json: async () => ({code: 'existing_job', job_id: 'original-job', session_id: 'A-session',
      status: 'running', terminal_snapshot: {}}),
    body: {getReader: () => { throw new Error('replay must not be parsed as SSE'); }}});
  await flush();
  assert.match(b.get('chat-container').textContent, /未重复执行.*处理中/);
  assert.doesNotMatch(b.get('chat-container').textContent, /原任务：已完成/);
  assert.equal(b.get('send-btn').disabled, false);
  assert.equal(b.ctx._currentJobId, 'original-job');
  assert.equal(b.ctx._selectedAttachments.length, 1);
  assert.equal(b.ctx._selectedAttachments[0], file);
  const buttons = b.get('chat-container').querySelectorAll('button');
  const check = buttons.find(button => button.textContent === '查看任务状态');
  const checking = check.onclick(); const request = b.requests.at(-1);
  assert.equal(request.url, '/jobs/original-job');
  b.reply(request, {status: 'failed', terminal_snapshot: {display_status: 'partial'}});
  await checking;
  assert.match(b.get('chat-container').textContent, /原任务：部分完成/);
  const history = buttons.find(button => button.textContent === '查看原会话');
  history.onclick(); await flush();
  const reload = b.requests.find(req => String(req.url).includes('/A-session/messages'));
  assert.ok(reload); b.reply(reload, {messages: [], turns: []}); await flush();
  assert.equal(b.requests.filter(req => req.opts.method === 'POST').length, 1);
});

test('idempotent chat replay does not release selected attachments or replay stale account data', async () => {
  const b = browser('app.js'); login(b);
  const file = {name: 'input.pdf', type: 'application/pdf', size: 2};
  b.ctx.handleAttachmentSelection([file]); b.get('user-input').value = 'question';
  await b.ctx.sendMsg(); await flush(); b.readers.at(-1).finish('data:text/plain;base64,QQ==');
  await flush(); const post = b.requests.at(-1);
  const body = deferred();
  post.resolve({ok: true, status: 200, headers: {get: () => 'application/json'}, json: () => body.promise});
  await flush();
  assert.equal(b.ctx._selectedAttachments.length, 1);
  b.ctx.resetAccountState(); login(b, 'B'); b.get('send-btn').disabled = true;
  body.resolve({code: 'existing_job', job_id: 'old-job', session_id: 'A-session', status: 'completed'});
  await flush();
  assert.equal(b.get('chat-container').childElementCount, 0);
  assert.equal(b.get('send-btn').disabled, true);
  assert.equal(b.ctx.sessionId, 'B-session');
});

test('idempotent job status response cannot mutate a different session', async () => {
  const b = browser('app.js'); login(b); b.get('user-input').value = 'question';
  await b.ctx.sendMsg(); await flush();
  b.requests.at(-1).resolve({ok: true, status: 200, headers: {get: () => 'application/json'},
    json: async () => ({code: 'existing_job', job_id: 'original-job', session_id: 'A-session', status: 'running'})});
  await flush();
  const check = b.get('chat-container').querySelectorAll('button').find(button => button.textContent === '查看任务状态');
  const checking = check.onclick(); const request = b.requests.at(-1); const body = deferred();
  request.resolve({ok: true, json: () => body.promise}); await flush();
  b.ctx.sessionId = 'another-session';
  const before = b.get('chat-container').textContent;
  body.resolve({status: 'completed'}); await checking;
  assert.equal(b.get('chat-container').textContent, before);
});

test('idempotent replay identifies interrupted status and restart failure facts', async () => {
  for (const fact of [{status: 'interrupted'}, {status: 'failed', current_step: 'interrupted'}]) {
    const b = browser('app.js'); login(b); b.get('user-input').value = 'question';
    await b.ctx.sendMsg(); await flush();
    b.requests.at(-1).resolve({ok: true, status: 200, headers: {get: () => 'application/json'},
      json: async () => ({code: 'existing_job', job_id: 'original-job', session_id: 'A-session', ...fact})});
    await flush();
    assert.match(b.get('chat-container').textContent, /原任务：已中断/);
    assert.doesNotMatch(b.get('chat-container').textContent, /状态待确认/);
  }
});

test('permission approval keeps the original chat stream without replaying its client request', async () => {
  const b = browser('app.js'); login(b); b.get('user-input').value = 'question';
  await b.ctx.sendMsg(); await flush(); const post = b.requests.at(-1);
  const pending = deferred(); let reads = 0, cancelled = false;
  post.resolve({ok: true, body: {getReader: () => ({
    read: () => ++reads === 1 ? Promise.resolve({done: false, value: new TextEncoder().encode(
      'event: permission_request\ndata: {"tool_name":"write_local_file"}\n\n')}) : pending.promise,
    cancel: async () => { cancelled = true; }
  })}});
  await flush();
  assert.equal(b.ctx._pendingPermission.tool_name, 'write_local_file');
  b.ctx.grantPermission('write_local_file', 'once'); const grant = b.requests.at(-1);
  assert.equal(grant.url, '/api/v2/permission/grant');
  b.reply(grant, {status: 'ok'}); await flush();
  assert.equal(b.requests.filter(req => req.url === '/api/v2/chat').length, 1);
  assert.equal(reads, 2); assert.equal(cancelled, false);
  assert.equal(b.ctx._pendingPermission, null);
  b.ctx.resetAccountState(); pending.resolve({done: true}); await flush();
});

test('idempotency conflict is shown explicitly without starting another request', async () => {
  const b = browser('app.js'); login(b); b.get('user-input').value = 'question';
  await b.ctx.sendMsg(); await flush();
  b.reply(b.requests.at(-1), {detail: {code: 'idempotency_conflict'}}, 409); await flush();
  assert.match(b.get('chat-container').textContent, /相同请求标识.*不同内容.*新请求/);
  assert.equal(b.get('send-btn').disabled, false);
  assert.equal(b.requests.filter(req => req.opts.method === 'POST').length, 1);
});
