'use strict';
// Focused offline regressions for the durable per-session turn queue.
// Loads the production scripts with no vendor code, network or credentials.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');

const flush = async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null;
    this.style = {setProperty(k, v) { this[k] = v; }, getPropertyValue(k) { return this[k] || ''; },
      removeProperty(k) { delete this[k]; }};
    this.dataset = {}; this.attrs = {}; this.listeners = {};
    this.className = ''; this.value = ''; this._text = ''; this.hidden = false; this.disabled = false;
    this.classList = {
      contains: name => this.className.split(/\s+/).includes(name),
      add: (...names) => { this.className = [...new Set(this.className.split(/\s+/).concat(names))].join(' ').trim(); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(n => !names.includes(n)).join(' '); },
      toggle: (name, on) => { on = on === undefined ? !this.classList.contains(name) : on; this.classList[on ? 'add' : 'remove'](name); return on; }
    };
  }
  appendChild(node) { node.remove(); this.children.push(node); node.parentNode = this; return node; }
  insertBefore(node, before) {
    if (!before) return this.appendChild(node);
    node.remove(); const index = this.children.indexOf(before);
    assert.ok(index >= 0);
    this.children.splice(index, 0, node); node.parentNode = this; return node;
  }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(n => n !== this); this.parentNode = null; }
  replaceChildren(...nodes) { this.children.forEach(n => { n.parentNode = null; }); this.children = []; this._text = ''; nodes.forEach(n => this.appendChild(n)); }
  set textContent(text) { this.replaceChildren(); this._text = String(text); }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  set innerHTML(html) {
    this.replaceChildren(); this._html = String(html);
    const stack = [this];
    for (const token of String(html).match(/<[^>]*>|[^<]+/g) || []) {
      if (token.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
      if (!token.startsWith('<')) { stack.at(-1)._text += token; continue; }
      const tag = token.match(/^<([a-z][\w-]*)/i); if (!tag) continue;
      const node = new Element(tag[1]);
      for (const a of token.matchAll(/([\w-]+)="([^"]*)"/g)) node.setAttribute(a[1], a[2]);
      stack.at(-1).appendChild(node);
      if (!/^(img|input|br|hr|meta|link)$/i.test(tag[1])) stack.push(node);
    }
  }
  get innerHTML() { return this._html || ''; }
  get firstChild() { return this.children[0] || null; }
  get nextSibling() { const p = this.parentNode; return p ? p.children[p.children.indexOf(this) + 1] || null : null; }
  get childElementCount() { return this.children.length; }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === 'class') this.className = String(value);
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
  }
  getAttribute(name) { return this.attrs[name] === undefined ? null : this.attrs[name]; }
  removeAttribute(name) { delete this.attrs[name]; }
  matches(selector) {
    if (selector.startsWith('.')) return selector.slice(1).split('.').every(c => this.classList.contains(c));
    if (selector.startsWith('[')) {
      const m = selector.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
      if (!m) return false;
      const val = m[1].startsWith('data-')
        ? this.dataset[m[1].slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())]
        : this.attrs[m[1]];
      return m[2] === undefined ? val !== undefined && val !== null : String(val) === m[2];
    }
    return this.tagName.toLowerCase() === selector;
  }
  querySelectorAll(selector) { return this.children.flatMap(n => (n.matches(selector) ? [n] : []).concat(n.querySelectorAll(selector))); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentNode ? this.parentNode.closest(selector) : null; }
  contains(node) { return node === this || this.children.some(n => n.contains(node)); }
  addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); }
  focus() {} scrollIntoView() {}
}

function storage() {
  const map = new Map();
  return {getItem: k => map.get(k) === undefined ? null : map.get(k), setItem: (k, v) => map.set(k, String(v)),
    removeItem: k => map.delete(k), key: i => [...map.keys()][i], get length() { return map.size; }};
}

function browser() {
  const nodes = new Map(), requests = [], timers = new Map(); let timerId = 0;
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const document = {readyState: 'loading', getElementById: get, createElement: tag => new Element(tag),
    querySelector: () => null, querySelectorAll: () => [], body: new Element('body'),
    documentElement: new Element('html'), addEventListener() {}};
  document.body.appendChild(get('chat-container'));
  for (const id of ['ctxInTokens', 'ctxOutTokens']) { const card = new Element(); card.className = 'ctx-token-card'; card.appendChild(get(id)); }
  new Element().appendChild(get('ctxBar'));
  const context = {console, document, AbortController, AbortSignal, DOMException, TextDecoder, URLSearchParams, navigator: {},
    marked: {Renderer: class {}, parse: text => '<p>' + String(text) + '</p>'},
    URL: {revokeObjectURL() {}}, FileReader: class { readAsDataURL() { if (this.onload) this.onload(); } },
    localStorage: storage(), sessionStorage: storage(), history: {replaceState() {}},
    location: {search: '', hash: '', pathname: '/'}, performance: {now: () => 10},
    setTimeout: (fn, ms) => { timers.set(++timerId, {fn, ms: ms || 0}); return timerId; },
    clearTimeout: id => timers.delete(id),
    setInterval: (fn, ms) => { timers.set(++timerId, {fn, ms: ms || 0, interval: true}); return timerId; },
    clearInterval: id => timers.delete(id),
    requestAnimationFrame: fn => { timers.set(++timerId, {fn, ms: 0}); return timerId; },
    Event: class {}, CustomEvent: class {}, addEventListener() {}, dispatchEvent() {},
    fetch: (url, opts) => { const req = {url, opts: opts || {}, settled: false, ...deferred()}; requests.push(req); return req.promise; }};
  context.window = context;
  vm.createContext(context);
  for (const file of ['task-state.js', 'task-transport.js', 'task-cards.js', 'task-controls.js', 'app.js']) {
    vm.runInContext(readFileSync(join(__dirname, '../assets', file), 'utf8'), context, {filename: file});
  }
  context.currentAccount = {user_id: 'test', username: 'test'};
  context.sessionId = 'session';
  context.authEpoch = 1;
  context.accountDocumentRetired = false;
  context.historyRestoreVersion = 0;
  context.currentRoundId = 0;
  const api = {
    context, requests, timers, get, document,
    userInput: get('user-input'), chatContainer: get('chat-container'),
    last(predicate) {
      const pending = [...requests].reverse().filter(r => !r.settled);
      return typeof predicate === 'string'
        ? pending.find(r => r.url.includes(predicate))
        : pending.find(r => predicate(r));
    },
    respond(predicate, data, status) {
      const req = api.last(predicate);
      if (!req) throw new Error('no pending request matching ' + String(predicate));
      req.settled = true;
      req.resolve({ok: (status || 200) < 400, status: status || 200,
        headers: {get: () => 'application/json'}, json: async () => data});
      return req;
    },
    async runTimers() {
      const ids = [...timers.keys()];
      for (const id of ids) {
        const timer = timers.get(id);
        if (!timer) continue;
        if (!timer.interval) timers.delete(id);
        timer.fn();
        await flush();
      }
    }
  };
  return api;
}

function turnEntry(over) {
  return Object.assign({session_id: 'session', client_request_id: 'crid-test', queue_seq: 1, state: 'queued',
    active_child_job_id: '', accepted_at: 't0', dispatch_at: null, started_at: null, finished_at: null,
    updated_at: 't0', history_saved: false, clear_reason: '', job_id: 'root-1', queue_position: 1,
    status_url: '/jobs/root-1', events_url: '/jobs/root-1/events', query: '问题', attachments: []}, over || {});
}

test('ordinary send posts once to the durable turn queue and never streams /api/v2/chat', async () => {
  const b = browser();
  b.userInput.value = '核对资料并生成报告';
  await b.context.sendMsg();
  await flush();
  const post = b.last(r => r.opts.method === 'POST');
  assert.ok(post, 'expected one admission POST');
  assert.equal(post.url, '/api/v2/session/session/turn-queue');
  const body = JSON.parse(post.opts.body);
  assert.equal(body.query, '核对资料并生成报告');
  assert.match(body.client_request_id, /^crid-/);
  assert.equal(b.requests.filter(r => r.url === '/api/v2/chat').length, 0);
  assert.equal(b.requests.filter(r => r.url.includes('/updates')).length, 0);
  b.respond(r => r.opts.method === 'POST', turnEntry({client_request_id: body.client_request_id, queue_position: 1}), 202);
  await flush();
  const state = b.chatContainer.querySelector('.turn-queue-state');
  assert.ok(state, 'pending bubble exposes queue state');
  assert.match(state.textContent, /排队中/);
  assert.equal(b.requests.filter(r => r.url.includes('/updates')).length, 0);
});

test('a queued turn reports its position and only follows the job after dispatch', async () => {
  const b = browser();
  b.userInput.value = '第三条';
  await b.context.sendMsg();
  await flush();
  const post = b.last(r => r.opts.method === 'POST');
  const body = JSON.parse(post.opts.body);
  b.respond(r => r.opts.method === 'POST', turnEntry({client_request_id: body.client_request_id, queue_position: 3}), 202);
  await flush();
  assert.match(b.chatContainer.querySelector('.turn-queue-state').textContent, /第 3 位/);
  assert.notEqual(b.chatContainer.querySelector('.research-task-line').dataset.state, 'waiting');
  assert.equal(b.requests.filter(r => r.url.includes('/events?after=0')).length, 0);

  await b.runTimers();
  const poll = b.last(r => r.url.includes('/turn-queue?limit=100'));
  assert.ok(poll, 'queued turn polls the queue');
  b.respond(r => r.url.includes('/turn-queue?limit=100'), {turns: [turnEntry({
    client_request_id: body.client_request_id, job_id: 'root-2', state: 'running', active_child_job_id: ''})]});
  await flush();
  const events = b.last(r => r.url.includes('/jobs/root-2/events?after=0'));
  assert.ok(events, 'dispatched turn follows its job events');
  b.respond(r => r.url.includes('/jobs/root-2/events'), {job_id: 'root-2', protocol: 'citrus-task/1',
    events: [{seq: 1, event: 'job', data: {job_id: 'root-2', status: 'running'}}], next_cursor: 1, has_more: false});
  await flush();
  b.respond(r => r.url === '/jobs/root-2', {job_id: 'root-2', status: 'running', event_cursor: 1});
  await flush();
  assert.equal(b.chatContainer.querySelector('.turn-queue-state').hidden, true);
  assert.equal(b.chatContainer.querySelector('.research-task-line').dataset.state, 'waiting');
  assert.match(b.chatContainer.querySelector('.research-task-elapsed').textContent, /等待开始时间/);
  assert.equal(b.requests.filter(r => r.url.includes('/updates')).length, 0);
});

test('sending while a turn runs queues behind it and never posts an implicit update', async () => {
  const b = browser();
  b.userInput.value = '第一条';
  await b.context.sendMsg();
  await flush();
  const firstPost = b.last(r => r.opts.method === 'POST');
  const firstId = JSON.parse(firstPost.opts.body).client_request_id;
  b.respond(r => r.opts.method === 'POST', turnEntry({client_request_id: firstId, job_id: 'root-1', state: 'running'}), 202);
  await flush();
  b.respond(r => r.url.includes('/jobs/root-1/events'), {job_id: 'root-1', protocol: 'citrus-task/1',
    events: [{seq: 1, event: 'job', data: {job_id: 'root-1', status: 'running'}}], next_cursor: 1, has_more: false});
  await flush();
  b.respond(r => r.url === '/jobs/root-1', {job_id: 'root-1', status: 'running', event_cursor: 1});
  await flush();

  b.userInput.value = '第二条';
  await b.context.sendMsg();
  await flush();
  const posts = b.requests.filter(r => r.opts.method === 'POST');
  assert.equal(posts.length, 2);
  const second = posts[posts.length - 1];
  assert.equal(second.url, '/api/v2/session/session/turn-queue');
  assert.equal(JSON.parse(second.opts.body).query, '第二条');
  assert.equal(b.requests.filter(r => r.url.includes('/updates')).length, 0);
  const secondId = JSON.parse(second.opts.body).client_request_id;
  b.respond(r => r.opts.method === 'POST' && JSON.parse(r.opts.body).client_request_id === secondId,
    turnEntry({client_request_id: secondId, job_id: 'root-2', state: 'queued', queue_position: 2}), 202);
  await flush();
  assert.equal(b.chatContainer.querySelectorAll('[data-request-id]').filter(n => n.dataset.requestId === secondId).length > 0, true);
});

test('admissions are serialized so queue order matches send order', async () => {
  const b = browser();
  b.userInput.value = '第一条';
  await b.context.sendMsg();
  await flush();
  b.userInput.value = '第二条';
  await b.context.sendMsg();
  await flush();
  let posts = b.requests.filter(r => r.opts.method === 'POST');
  assert.equal(posts.length, 1, 'second admission waits for the first');
  assert.equal(posts[0].opts.signal.aborted, false, 'second send leaves the first admission in flight');
  const firstBody = JSON.parse(posts[0].opts.body);
  b.respond(r => r.opts.method === 'POST', turnEntry({client_request_id: firstBody.client_request_id}), 202);
  await flush();
  posts = b.requests.filter(r => r.opts.method === 'POST');
  assert.equal(posts.length, 2);
  assert.deepEqual(posts.map(p => JSON.parse(p.opts.body).query), ['第一条', '第二条']);
  const secondBody = JSON.parse(posts[1].opts.body);
  assert.notEqual(secondBody.client_request_id, firstBody.client_request_id);
});

test('the request payload freezes query and skills at send time', async () => {
  const b = browser();
  b.context.skillPicker = {capture: () => ({items: [{id: 's1', name: '技能一'}]}), acknowledge() {}};
  b.userInput.value = '冻结内容';
  await b.context.sendMsg();
  b.context.skillPicker.capture = () => ({items: [{id: 's2', name: '技能二'}]});
  b.userInput.value = '后来编辑';
  await flush();
  const post = b.last(r => r.opts.method === 'POST');
  const body = JSON.parse(post.opts.body);
  assert.equal(body.query, '冻结内容');
  assert.deepEqual(body.selected_skills.map(item => item.id), ['s1']);
});

test('a lost admission response is reconciled by request identity without re-posting', async () => {
  const b = browser();
  b.userInput.value = '网络中断';
  await b.context.sendMsg();
  await flush();
  const post = b.last(r => r.opts.method === 'POST');
  const body = JSON.parse(post.opts.body);
  post.settled = true;
  post.reject(new Error('network'));
  await flush();
  const lookupPath = '/api/v2/session/session/task-by-request/' + body.client_request_id;
  assert.ok(b.last(r => r.url === lookupPath), 'uncertain send looks up the exact request');
  b.respond(r => r.url === lookupPath, turnEntry({
    client_request_id: body.client_request_id, job_id: 'root-9', state: 'queued', queue_position: 1}));
  await flush();
  assert.equal(b.requests.filter(r => r.opts.method === 'POST').length, 1);
  assert.match(b.chatContainer.querySelector('.turn-queue-state').textContent, /排队中/);
});

test('reloading renders queued turns from the queue listing and cancels one by id', async () => {
  const b = browser();
  const restored = b.context.restoreSessionHistory();
  await flush();
  b.respond(r => r.url.includes('/messages?'), {messages: [], turns: [], has_more: false});
  await flush();
  b.respond(r => r.url.includes('/turn-queue?limit=100'), {turns: [turnEntry({
    client_request_id: 'q1', job_id: 'rootq', state: 'queued', queue_position: 2, query: '旧的排队消息'})]});
  await restored;
  await flush();
  const statusNode = b.chatContainer.querySelectorAll('[data-request-id]')
    .filter(n => n.dataset.requestId === 'q1')
    .map(n => n.querySelector('.turn-queue-state')).find(Boolean);
  assert.ok(statusNode, 'restored turn shows its queue state');
  assert.match(statusNode.textContent, /排队中/);
  const cancel = statusNode.querySelector('.turn-queue-cancel');
  assert.ok(cancel);
  cancel.onclick();
  await flush();
  const del = b.last(r => r.opts.method === 'DELETE');
  assert.ok(del, 'cancel deletes the exact queued turn');
  assert.match(del.url, /\/turn-queue\/q1$/);
  b.respond(r => r.opts.method === 'DELETE', turnEntry({
    client_request_id: 'q1', job_id: 'rootq', state: 'terminal', clear_reason: 'cancelled', queue_position: 0}));
  await flush();
  assert.match(statusNode.textContent, /已取消/);
});

test('navigating away discards a late admission response', async () => {
  const b = browser();
  b.userInput.value = '离开的会话';
  await b.context.sendMsg();
  await flush();
  const post = b.last(r => r.opts.method === 'POST');
  const body = JSON.parse(post.opts.body);
  b.context.sessionId = 'other-session';
  b.respond(r => r.opts.method === 'POST', turnEntry({client_request_id: body.client_request_id, state: 'running'}), 202);
  await flush();
  assert.equal(b.requests.filter(r => r.url.includes('/events')).length, 0);
});

test('a pending question leaves the ordinary send available for the next turn', async () => {
  const b = browser();
  const store = b.context.CitrusTaskState.create({});
  store.hydrate({job_id: 'job-q', status: 'running'});
  store.state.question = {id: 'q1', status: 'pending', prompt: '选择', options: []};
  const fake = {requestId: 'pending', owns: () => true, store, controls: {}, cancel() {}, stopping: false};
  b.context.activeTurnComposer = fake;
  b.context.turnQueueBindings.push(fake);
  b.userInput.value = '下一条独立消息';
  await b.context.sendMsg();
  await flush();
  const posts = b.requests.filter(r => r.opts.method === 'POST');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, '/api/v2/session/session/turn-queue');
  assert.equal(JSON.parse(posts[0].opts.body).query, '下一条独立消息');
  assert.equal(store.state.question.status, 'pending');
  assert.equal(b.userInput.value, '');
});

test('only the explicit task card form posts /updates', async () => {
  const b = browser();
  const store = b.context.CitrusTaskState.create({});
  store.hydrate({job_id: 'job-update', status: 'running'});
  const calls = [];
  const container = new Element('div');
  b.context.CitrusTaskControls.create({container, store, owns: () => true,
    transport: {post: async (job, path, payload) => { calls.push([job, path, payload]); return {version: 2}; },
      refresh: async () => ({job_id: 'job-update', status: 'running'}), stop() {}}});
  const input = container.querySelector('.task-update-input');
  assert.ok(input, 'explicit update form exists');
  input.value = '换一个角度补充';
  await container.querySelector('.task-update-submit').onclick();
  await flush();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'job-update');
  assert.equal(calls[0][1], 'updates');
  assert.equal(calls[0][2].instruction, '换一个角度补充');
});


test('history groups the latest continuation under its original user turn', () => {
  const {context} = browser();
  const history = context.normalizeHistoryTurns([
    {turn_request_id: 'root-request', job_id: 'root', user: {content: 'Original question'}},
    {turn_request_id: 'child-request', job_id: 'child', parent_job_id: 'root', user: {content: 'Clarification'}},
    {turn_request_id: 'last-child', job_id: 'grandchild', parent_job_id: 'child', answer: {content: 'Final answer'}},
    {turn_request_id: 'follower', job_id: 'other', user: {content: 'Next question'}},
  ]);
  const merged = context.mergeQueuedHistory(history, [turnEntry({client_request_id: 'root-request', job_id: 'root',
    active_child_job_id: 'grandchild', state: 'terminal', dispatch_at: 'started'})]);
  assert.deepEqual(Array.from(merged, turn => turn.turnRequestId), ['root-request', 'follower']);
  assert.equal(merged[0].user.content, 'Original question');
  assert.equal(merged[0].answer.content, 'Final answer');
  assert.equal(merged[0].jobId, 'grandchild');
});
