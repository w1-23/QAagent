'use strict';
// Focused, offline UI regressions. Loads production scripts, no vendor scripts,
// services, user files, credentials or model calls. Run this file alone.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null;
    this.style = {setProperty() {}}; this.dataset = {}; this.attrs = {}; this.listeners = {};
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
    node.remove(); const index = this.children.indexOf(before); assert.ok(index >= 0);
    this.children.splice(index, 0, node); node.parentNode = this; return node;
  }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(n => n !== this); this.parentNode = null; }
  replaceChildren(...nodes) { this.children.forEach(n => { n.parentNode = null; }); this.children = []; this._text = ''; nodes.forEach(n => this.appendChild(n)); }
  set textContent(text) { this.replaceChildren(); this._text = String(text); }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  set innerHTML(html) {
    this.replaceChildren(); this._html = html;
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
  get nextSibling() { return this.parentNode?.children[this.parentNode.children.indexOf(this) + 1] || null; }
  get childElementCount() { return this.children.length; }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === 'class') this.className = String(value);
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  removeAttribute(name) { delete this.attrs[name]; }
  matches(selector) {
    if (selector.startsWith('.')) return selector.slice(1).split('.').every(c => this.classList.contains(c));
    return this.tagName.toLowerCase() === selector;
  }
  querySelectorAll(selector) { return this.children.flatMap(n => (n.matches(selector) ? [n] : []).concat(n.querySelectorAll(selector))); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
  contains(node) { return node === this || this.children.some(n => n.contains(node)); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  focus() {} scrollIntoView() {}
}
function storage() {
  const map = new Map();
  return {getItem: k => map.get(k) ?? null, setItem: (k, v) => map.set(k, String(v)), removeItem: k => map.delete(k), key: i => [...map.keys()][i], get length() { return map.size; }};
}
function browser() {
  const nodes = new Map(), requests = [], timers = new Map(); let timerId = 0;
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const document = {readyState: 'loading', getElementById: get, createElement: tag => new Element(tag),
    querySelector: () => null, querySelectorAll: () => [], body: new Element('body'), documentElement: new Element('html'), addEventListener() {}};
  document.body.appendChild(get('chat-container'));
  for (const id of ['ctxInTokens', 'ctxOutTokens']) { const card = new Element(); card.className = 'ctx-token-card'; card.appendChild(get(id)); }
  new Element().appendChild(get('ctxBar'));
  const context = {console, document, AbortController, DOMException, TextDecoder, URLSearchParams, navigator: {},
    // Markdown parsing is outside these lifecycle assertions; no vendored code runs.
    marked: {Renderer: class {}, parse: text => '<p>' + String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</p>'},
    URL: {revokeObjectURL() {}}, FileReader: class {}, localStorage: storage(), sessionStorage: storage(),
    history: {replaceState() {}}, location: {search: '', hash: '', pathname: '/'}, performance: {now: () => 10},
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id),
    setInterval: fn => { timers.set(++timerId, fn); return timerId; }, clearInterval: id => timers.delete(id),
    requestAnimationFrame: fn => { timers.set(++timerId, fn); return timerId; },
    Event: class {}, CustomEvent: class {}, addEventListener() {}, dispatchEvent() {},
    fetch: (url, opts) => { const request = {url, opts, ...deferred()}; requests.push(request); return request.promise; }};
  context.window = context;
  vm.createContext(context);
  for (const file of ['task-state.js', 'task-transport.js', 'task-cards.js', 'task-controls.js', 'app.js']) {
    vm.runInContext(readFileSync(join(__dirname, '../assets', file), 'utf8'), context, {filename: file});
  }
  context.currentAccount = {user_id: 'test', username: 'test'}; context.sessionId = 'session';
  return {context, get, requests, document};
}
async function turn(b, query = '核对资料并生成报告') {
  b.get('user-input').value = query; await b.context.sendMsg(); await flush();
  const request = b.requests.findLast(r => r.opts?.method === 'POST');
  const queue = []; let pending;
  function put(chunk) { if (pending) { const waiting = pending; pending = null; waiting.resolve(chunk); } else queue.push(chunk); }
  request.resolve({ok: true, body: {getReader: () => ({
    read: () => queue.length ? Promise.resolve(queue.shift()) : (pending = deferred()).promise,
    cancel: async () => {}
  })}});
  await flush();
  const content = b.get('chat-container').children.at(-1).querySelector('.content');
  return {content, panel: content.querySelector('.research-process'), id: JSON.parse(request.opts.body).client_request_id,
    async emit(type, data) { put({done: false, value: new TextEncoder().encode('event: ' + type + '\ndata: ' + JSON.stringify(data) + '\n\n')}); await flush(); },
    async end() { put({done: true}); await flush(); }};
}
const done = {answer: '已整理现有资料。', turn_envelope: {display_status: 'completed'}, artifact_ids: []};
const file = (id, sid = 'session', rid = 'turn') => ({file_id: id, artifact_id: id, filename: id + '.txt', source_session_id: sid, turn_request_id: rid});

test('every sequential turn shows a live stage and collapses at its first visible answer', async () => {
  const b = browser();
  for (let round = 0; round < 3; round++) {
    const t = await turn(b, '第 ' + round + ' 轮目标');
    assert.equal(t.panel.open, true);
    await t.emit('status', {stage: 'intake', stage_label: '理解需求'});
    assert.match(t.panel.textContent, /理解需求/);
    await t.emit('tool_progress', {message: '已加载历史 12 条，正在理解问题'});
    assert.doesNotMatch(t.panel.textContent, /已加载|12 条/);
    await t.emit('tool_call_start', {tool_call_id: 'tool', tool_name: 'web_search', display_name: '网页搜索'});
    await t.emit('answer_start', {}); await t.emit('text', {content: '回答'});
    await t.emit('status', {stage: 'final_answer', message: '核对本轮结果'});
    assert.match(t.panel.textContent, /核对本轮结果/); assert.equal(t.panel.open, false);
    assert.equal(t.panel.querySelectorAll('.term-line').length, 1);
    await t.emit('done', done); await t.end();
    assert.equal(t.panel.open, false); assert.match(t.panel.textContent, /执行轨迹：已完成/);
    t.panel.open = true; assert.match(t.panel.textContent, new RegExp('第 ' + round + ' 轮目标'));
  }
  assert.equal(b.get('chat-container').querySelectorAll('.research-process').length, 3);
});

test('a no-tool turn and a failed turn retain exactly one expandable process panel', async () => {
  const b = browser(); const first = await turn(b);
  await first.emit('done', done); await first.end();
  assert.equal(first.panel.open, false); assert.equal(first.content.querySelectorAll('.tool-trace').length, 1);
  const second = await turn(b);
  await second.emit('error', {turn_envelope: {display_status: 'failed'}}); await second.end();
  assert.equal(second.panel.open, true); assert.match(second.panel.textContent, /执行轨迹：未完成/);
});

test('disconnect keeps an uncertain expanded panel until the server supplies a terminal state', async () => {
  const b = browser(); const t = await turn(b); await t.end();
  assert.equal(t.panel.open, true); assert.match(t.panel.textContent, /状态待确认/);
  assert.doesNotMatch(t.panel.textContent, /执行轨迹：已完成|执行轨迹：已停止/);
});

test('late files still attach after their own answer when a later round has started', async () => {
  const b = browser(), library = deferred(); b.context.loadSessionFiles = () => library.promise;
  const first = await turn(b); await first.emit('done', {...done, artifact_ids: ['own']}); await first.end();
  const second = await turn(b);
  library.resolve({generated: [file('own', 'session', first.id), file('other', 'elsewhere', second.id)]}); await flush();
  assert.equal(first.content.nextSibling.className, 'turn-files');
  assert.match(first.content.nextSibling.textContent, /own.txt/);
  assert.equal(second.content.closest('.message').querySelector('.turn-files'), null);
});

test('history places only attributed files immediately after their own bubble and deduplicates', async () => {
  const b = browser();
  b.context.renderHistoryMessages([], b.context.normalizeHistoryTurns([
    {turn_request_id: 'one', user: {content: '第一个目标'}, answer: {content: '回答一'}, display_status: 'partial', files: [file('a', 'session', 'one'), file('bad', 'other', 'one')]},
    {turn_request_id: 'two', user: {content: '第二个目标'}, answer: {content: '回答二'}, files: [file('b', 'session', 'two')]}
  ]));
  const bots = b.get('chat-container').querySelectorAll('.bot');
  assert.equal(bots.length, 2);
  for (const [i, bot] of bots.entries()) {
    const content = bot.querySelector('.content'), files = content.nextSibling;
    assert.equal(files.className, 'turn-files'); assert.equal(bot.querySelectorAll('.research-process').length, 1);
    assert.equal(content.querySelector('.research-process').open, false);
    assert.equal(files.querySelectorAll('.turn-file-card').length, 1);
    b.context.appendTurnGeneratedFiles(content, [file(i ? 'b' : 'a', 'session', i ? 'two' : 'one')]);
    assert.equal(files.querySelectorAll('.turn-file-card').length, 1);
  }
  assert.match(bots[0].textContent, /部分完成/); assert.doesNotMatch(b.get('chat-container').textContent, /bad.txt/);
  assert.equal(b.context.filesAttributedToTurn([file('wrong', 'other', 'one'), {file_id: 'unattributed'}], 'session', 'one').length, 0);
  assert.equal(b.context.filesAttributedToTurn([file('a')], 'session', 'turn', []).length, 0);
});

// Match the durable backend contract: order by event seq, join provider usage
// only on logical_call_id + attempt_id, and never infer model_window from max_tokens.
function contextEvents(t) {
  let seq = 0;
  const identity = {session_id: 'session', request_id: t.id, logical_call_id: 'call-a', attempt_id: 'attempt-a'};
  return {
    snapshot: (data = {}) => t.emit('context_snapshot', {snapshot_version: 1, ...identity, seq: data.seq ?? ++seq,
      estimate_scope: 'full_request_estimate', status: 'estimated', ...data}),
    usage: (data = {}) => t.emit('context_usage', {...identity, scope: 'single_call', source: 'supervisor', seq: ++seq, ...data}),
    legacy: (type, data) => t.emit(type, {seq: ++seq, ...data})
  };
}

test('context refreshes during execution, separates estimate from measured usage and resets each round', async () => {
  const b = browser(), t = await turn(b), events = contextEvents(t);
  await events.snapshot({estimated_input_tokens: 120, model_window: 1000, history_msgs: 42});
  assert.match(b.get('ctxBarLabel').textContent, /估算 120 \/ 1,000/);
  assert.equal(b.get('ctxInTokens').closest('.ctx-token-card').hidden, true);
  await events.usage({call: {input_tokens: 150, output_tokens: 25}, total: {input_tokens: 99999, output_tokens: 88888}});
  assert.equal(b.get('ctxInTokens').textContent, '150'); assert.match(b.get('ctxInNote').textContent, /实测.*此调用尝试/);
  await events.snapshot({estimated_input_tokens: 180, model_window: 1000, input_limit: 900});
  assert.match(b.get('ctxBarLabel').textContent, /估算 180 \/ 1,000/);
  assert.equal(b.get('ctxInTokens').textContent, '150');
  assert.match(b.get('ctxItems').textContent, /输入估算（未细分）180/);
  assert.match(b.get('ctxItems').textContent, /窗口余量（估算）82082%/);
  assert.equal(b.get('ctxDetailBlock').hidden, true);
  await events.usage({call: {input_tokens: null}, total: {input_tokens: 99999}});
  assert.equal(b.get('ctxInTokens').textContent, '—');
  assert.equal(b.get('ctxInTokens').closest('.ctx-token-card').hidden, true);
  await t.emit('done', done); await t.end(); await turn(b);
  assert.equal(b.get('ctxInTokens').textContent, '—'); assert.match(b.get('ctxBarLabel').textContent, /暂不可用/);
});

test('usage attaches to the selected logical call and attempt, never selects a call by response arrival', async () => {
  const b = browser(), t = await turn(b), events = contextEvents(t);
  // Even an identified provider response cannot choose the displayed request.
  await events.usage({call: {input_tokens: 101}});
  assert.equal(b.get('ctxInTokens').textContent, '—');
  await events.snapshot({estimated_input_tokens: 120, model_window: 1000});
  assert.equal(b.get('ctxInTokens').textContent, '101');
  await events.snapshot({attempt_id: 'retry', estimated_input_tokens: 130, model_window: 1000});
  assert.equal(b.get('ctxInTokens').textContent, '—');
  for (const rejected of [
    {logical_call_id: 'other'}, {attempt_id: 'attempt-a'}, {logical_call_id: ''}, {attempt_id: ''},
    {request_id: 'other'}, {session_id: 'other'}, {scope: 'task_total'}, {scope: undefined}
  ]) {
    await events.usage({attempt_id: 'retry', ...rejected, call: {input_tokens: 99999}});
    assert.equal(b.get('ctxInTokens').textContent, '—');
  }
  await events.usage({attempt_id: 'retry', source: 'thinking_parameter_fallback', parent_purpose: 'supervisor', call: {input_tokens: 111}});
  assert.equal(b.get('ctxInTokens').textContent, '111');
  assert.match(b.get('ctxInNote').textContent, /此调用尝试/);
  await t.emit('done', done); await t.end();
});

test('full request estimate uses the model window while work allowance and reported cache remain separate', async () => {
  const b = browser(), t = await turn(b), events = contextEvents(t);
  await events.snapshot({estimated_input_tokens: 180, model_window: 1000, input_limit: 300, epoch: 2,
    components: {tool_schemas: 50, images: 100}});
  assert.match(b.get('ctxBarLabel').textContent, /当前完整输入估算 180 \/ 1,000/);
  assert.match(b.get('ctxSummary').textContent, /工具定义.*图片.*不等同于服务商实测/);
  assert.match(b.get('ctxBudgetItems').textContent, /工作输入限额300 tokens/);
  assert.match(b.get('ctxItems').textContent, /工具定义505%/);
  assert.match(b.get('ctxItems').textContent, /图片预留10010%/);
  assert.match(b.get('ctxItems').textContent, /未细分估算303%/);
  assert.match(b.get('ctxItems').textContent, /窗口余量（估算）82082%/);
  await events.usage({epoch: 2, call: {input_tokens: 100, output_tokens: 3, cache_read: 40, cache_write: 20, cache_uncached: null}});
  assert.match(b.get('ctxInNote').textContent, /缓存读取 40.*缓存写入 20.*仅已报告项/);
  assert.doesNotMatch(b.get('ctxInNote').textContent, /未缓存|60|命中率/);
  assert.match(b.get('ctxBarLabel').textContent, /180 \/ 1,000/);
  await events.usage({logical_call_id: 'earlier-call', epoch: 1, call: {input_tokens: 9999}});
  assert.equal(b.get('ctxInTokens').textContent, '100');
  await events.legacy('context_status', {estimate_scope: 'history_text_only', estimated_tokens: 9999});
  assert.match(b.get('ctxBarLabel').textContent, /180 \/ 1,000/);
  await events.usage({epoch: 2, call: {input_tokens: 110}});
  assert.match(b.get('ctxInNote').textContent, /缓存统计未提供/);
  assert.doesNotMatch(b.get('ctxInNote').textContent, /缓存读取|缓存写入/);
  await t.emit('done', done); await t.end();
});

test('six additive request components use the explicit model window, with true zero and a separate output reserve', async () => {
  const b = browser(), t = await turn(b), events = contextEvents(t);
  b.context._runtimeConfig = {context: {max_tokens: 123456}}; // May describe a different model.
  await events.snapshot({estimated_input_tokens: 51000, model_window: 1000000,
    max_tokens: 64000, input_limit: 64000, output_tokens: 8192, working_soft_tokens: 48000, working_target_tokens: 32000,
    components: {messages: 25000, system: 5000, tool_arguments: 2000, tool_schemas: 18000, images: 0, protocol: 1000}});
  assert.match(b.get('ctxBarLabel').textContent, /51,000 \/ 1,000,000/);
  assert.equal(b.get('ctx-pct').textContent, '约 5.1%');
  const rows = b.get('ctxItems').children;
  assert.equal(rows.length, 7);
  assert.equal(rows.find(n => n.dataset.key === 'images').textContent, '图片预留00%');
  assert.match(b.get('ctxBudgetItems').textContent, /工作输入限额64,000.*参考阈值48,000.*整理目标32,000.*输出预留（上限）8,192/s);
  const widths = b.get('ctxBar').children.map(n => Number.parseFloat(n.style.width));
  assert.ok(Math.abs(widths.reduce((a, n) => a + n, 0) - 100) < 1e-8);
  assert.equal(b.get('ctxBar').children.find(n => n.dataset.key === 'messages').getAttribute('aria-label'), '消息内容：约 25,000 tokens，占窗口 2.5%');
  await events.snapshot({logical_call_id: 'window-unknown', estimated_input_tokens: 51000, input_limit: 64000, max_tokens: 64000});
  assert.match(b.get('ctxBarLabel').textContent, /模型窗口未知/);
  assert.equal(b.get('ctx-pct').textContent, '—');
  await t.emit('done', done); await t.end();
});

test('history is explicitly partial and cannot show missing categories or exact free space', () => {
  const b = browser();
  b.context.renderContextPanel({estimate_scope: 'history_text_only', estimated_tokens: 2132, model_window: 1000000,
    components: {system: 1000, tool_schemas: 2000}, input_limit: 64000, working_soft_tokens: 48000});
  assert.equal(b.get('ctx-pct').textContent, '约 0.2%');
  assert.match(b.get('ctxTotalKind').textContent, /完整占用未知/);
  assert.equal(b.get('ctxItems').children.length, 2);
  assert.match(b.get('ctxItems').textContent, /剩余空间（未知）未知—/);
  assert.doesNotMatch(b.get('ctxItems').textContent, /系统提示|工具定义|997,868/);
  assert.equal(b.get('ctxBudgetBlock').hidden, true);
  assert.match(b.get('ctxBar').children.at(-1).getAttribute('aria-label'), /剩余空间未知/);
  b.context.renderContextPanel({estimated_tokens: 1, model_window: 1000000});
  assert.equal(b.get('ctx-pct').textContent, '约 <0.1%');
});

test('a newer snapshot clears old categories and budgets while earlier sequence and legacy events cannot replace it', async () => {
  const b = browser(), t = await turn(b), events = contextEvents(t);
  await events.snapshot({estimated_input_tokens: 80, model_window: 1000, epoch: 2, components: {system: 80}, input_limit: 300, output_tokens: 50});
  await events.snapshot({logical_call_id: 'call-b', estimated_input_tokens: 100, model_window: 1000, epoch: 3});
  assert.match(b.get('ctxItems').textContent, /输入估算（未细分）10010%/);
  assert.doesNotMatch(b.get('ctxItems').textContent, /系统提示/);
  assert.equal(b.get('ctxBudgetBlock').hidden, true);
  await events.snapshot({seq: 1, estimated_input_tokens: 999, model_window: 1000, epoch: 2, components: {system: 999}});
  for (const event of ['context_budget', 'context_status']) {
    await events.legacy(event, {scope: 'full_request_estimate', estimate_scope: 'full_request_estimate', estimated_input_tokens: 555, epoch: 2});
    assert.match(b.get('ctxBarLabel').textContent, /100 \/ 1,000/);
  }
  await t.emit('done', done); await t.end(); await turn(b);
  assert.equal(b.get('ctxItems').hidden, true);
  assert.equal(b.get('ctxUsageBlock').hidden, true);
});

test('partial, invalid and inconsistent component payloads never fabricate a matched breakdown', () => {
  const b = browser();
  const base = {estimate_scope: 'full_request_estimate', estimated_tokens: 100, model_window: 1000};
  b.context.renderContextPanel({...base, components: {messages: 50, system: null, tool_schemas: 'NaN', images: -4, protocol: false}});
  assert.match(b.get('ctxItems').textContent, /系统提示未提供—/);
  assert.match(b.get('ctxItems').textContent, /工具定义未提供—/);
  assert.match(b.get('ctxItems').textContent, /未细分估算505%/);
  b.context.renderContextPanel({...base, components: {messages: 110, system: 50}});
  assert.match(b.get('ctxSummary').textContent, /分类合计与总量不一致/);
  assert.equal(b.get('ctxItems').children.length, 2);
  assert.doesNotMatch(b.get('ctxItems').textContent, /110|系统提示/);
  b.context.renderContextPanel({...base, estimated_tokens: 1500, components: {messages: 1500}});
  assert.equal(b.get('ctx-pct').textContent, '约 150%');
  assert.match(b.get('ctxSummary').textContent, /超过模型窗口/);
  assert.match(b.get('ctxItems').textContent, /窗口余量（估算）00%/);
  assert.equal(b.get('ctxBar').parentNode.getAttribute('aria-valuenow'), '100');
  b.context.renderContextPanel({...base, estimated_tokens: NaN});
  assert.equal(b.get('ctxItems').hidden, true);
  assert.equal(b.get('ctx-pct').textContent, '—');
});

test('legacy context remains historical and cannot borrow a model window or identified usage', async () => {
  const b = browser(), t = await turn(b), events = contextEvents(t);
  b.context._runtimeConfig = {context: {max_tokens: 1000000}};
  await events.legacy('context_status', {estimated_tokens: 120, max_tokens: 64000});
  assert.match(b.get('ctxBarLabel').textContent, /历史估算 120 tokens · 模型窗口未知/);
  assert.equal(b.get('ctxBudgetBlock').hidden, true);
  await events.usage({call: {input_tokens: 999}});
  assert.equal(b.get('ctxUsageBlock').hidden, true);
  await events.legacy('context_budget', {scope: 'full_request_estimate', estimated_input_tokens: 180, input_limit: 64000});
  assert.match(b.get('ctxBarLabel').textContent, /历史估算 180 tokens · 模型窗口未知/);
  assert.equal(b.get('ctxUsageBlock').hidden, true);
  assert.match(b.get('ctxItems').textContent, /剩余空间（未知）未知—/);
  await t.emit('done', done); await t.end();
});

test('foreign snapshots cannot poison the context cache and reappear on a valid event', async () => {
  for (const foreign of [{request_id: 'other'}, {session_id: 'other'}]) {
    const b = browser(), t = await turn(b), events = contextEvents(t);
    await events.snapshot({estimated_input_tokens: 120, model_window: 1000});
    await events.snapshot({...foreign, logical_call_id: 'foreign', estimated_input_tokens: 999, model_window: 1000});
    assert.match(b.get('ctxBarLabel').textContent, /120 \/ 1,000/);
    await events.usage({call: {input_tokens: 100}});
    assert.match(b.get('ctxBarLabel').textContent, /120 \/ 1,000/);
    assert.equal(b.get('ctxInTokens').textContent, '100');
    await t.emit('done', done); await t.end();
  }
});

test('cache and token counters reject blank, boolean and structured values while preserving zero', () => {
  const b = browser(), base = {estimate_scope: 'full_request_estimate', estimated_tokens: 100, model_window: 1000};
  b.context.renderContextPanel({...base, provider_usage: {input_tokens: 0, output_tokens: 0, cache_read: false, cache_write: ' ', cache_uncached: []}});
  assert.equal(b.get('ctxInTokens').textContent, '0');
  assert.equal(b.get('ctxUsageBlock').hidden, false);
  assert.match(b.get('ctxInNote').textContent, /缓存统计未提供/);
  for (const invalid of [false, true, '', ' ', [], {}]) {
    b.context.renderContextPanel({...base, estimated_tokens: invalid, provider_usage: {input_tokens: invalid}});
    assert.equal(b.get('ctxItems').hidden, true);
    assert.equal(b.get('ctxInTokens').closest('.ctx-token-card').hidden, true);
  }
  b.context.renderContextPanel({...base, provider_usage: {input_tokens: 0, cache_read: 0}});
  assert.match(b.get('ctxInNote').textContent, /缓存读取 0/);
});

test('usage from a retired request cannot update a later turn or another account', async () => {
  const b = browser(), first = await turn(b);
  await first.emit('done', done); await first.end();
  const second = await turn(b);
  await second.emit('context_usage', {scope: 'single_call', source: 'supervisor', session_id: 'session', request_id: first.id,
    call: {input_tokens: 999}});
  assert.equal(b.get('ctxInTokens').textContent, '—');
  b.context.advanceAuthEpoch();
  await second.emit('context_usage', {scope: 'single_call', source: 'supervisor', session_id: 'session', request_id: second.id,
    call: {input_tokens: 999}});
  assert.equal(b.get('ctxInTokens').textContent, '—');
  await second.end();
});

test('a restored pending turn retains an expanded panel without claiming completion', () => {
  const b = browser();
  b.context.renderHistoryMessages([], b.context.normalizeHistoryTurns([
    {turn_request_id: 'pending', user: {content: '检查尚未完成的请求'}, partial: true}
  ]));
  const panel = b.get('chat-container').querySelector('.research-process');
  assert.ok(panel); assert.equal(panel.open, true); assert.match(panel.textContent, /状态未确认/);
});

test('stale file callbacks cannot mutate another session or account', async () => {
  for (const invalidate of [b => { b.context.sessionId = 'other'; }, b => { b.context.advanceAuthEpoch(); }]) {
    const b = browser(), library = deferred(); b.context.loadSessionFiles = () => library.promise;
    const t = await turn(b); await t.emit('done', {...done, artifact_ids: ['a']}); await t.end();
    invalidate(b); library.resolve({generated: [file('a', 'session', t.id)]}); await flush();
    assert.equal(t.content.closest('.message').querySelector('.turn-files'), null);
  }
});

function reply(request, data, status = 200) {
  assert.ok(request, 'expected an offline HTTP request');
  request.resolve({ok: status < 400, status, json: async () => data});
}
const framesPage = (job, events, extra = {}) => ({protocol: 'citrus-task/1', job_id: job,
  events: events.map(([seq, event, data]) => ({seq, event, data})), has_more: false, ...extra});
const question = (extra = {}) => ({id: 'q1', version: 1, status: 'pending', job_id: 'job',
  prompt: '请选择处理范围', multi_select: false, allow_text: true,
  options: [{id: 'a', label: '全部资料', description: '使用已选资料', refs: [{private: 'do not show refs'}]}, {id: 'b', label: '仅摘要'}], ...extra});
async function fire(element, type) {
  for (const listener of element.listeners[type] || []) await listener({preventDefault() {}});
  await flush();
}

test('public notes, tool rows and optional records stay flat in temporal order, heartbeat adds no rows', async () => {
  const b = browser(), t = await turn(b);
  await t.emit('assistant_progress', {text: '先核对资料范围。'});
  await t.emit('tool_call_start', {tool_call_id: 'read', display_name: '读取表格'});
  await t.emit('tool_call_start', {tool_call_id: 'audit', display_name: '数据审查'});
  await t.emit('tool_result', {tool_call_id: 'read', display_name: '读取表格', status: 'complete', summary: '已读取三个字段'});
  await t.emit('assistant_progress', {text: '已确定字段，接着核对统计方法。'});
  await t.emit('tool_call_start', {tool_call_id: 'stats', display_name: '统计分析'});
  await t.emit('status', {stage: 'final_answer', message: '整理可交付结果'});
  const timeline = t.panel.querySelector('.research-process-timeline');
  assert.deepEqual(timeline.children.map(n => n.className), [
    'research-process-note', 'research-process-note', 'term-line is-complete', 'research-tool-details',
    'term-line is-running', 'research-tool-details', 'research-process-note',
    'term-line is-running', 'research-tool-details', 'research-process-note'
  ]);
  assert.equal(timeline.querySelectorAll('.research-action-group').length, 0);
  assert.equal(timeline.querySelectorAll('.task-result').length, 0);
  assert.equal(timeline.querySelectorAll('details').length, 0);
  for (const row of timeline.querySelectorAll('.term-line')) {
    assert.equal(row.parentNode, timeline);
    assert.equal(row.nextSibling.className, 'research-tool-details');
    assert.equal(row.nextSibling.hidden, true);
  }
  assert.equal(timeline.querySelectorAll('.term-line').length, 3);
  assert.match(timeline.children[2].textContent, /已读取三个字段/);
  const before = timeline.textContent;
  await t.emit('heartbeat', {elapsed_ms: 42000, message: 'must not appear as a process step'});
  assert.equal(timeline.textContent, before);
  assert.match(t.panel.querySelector('.tool-trace-elapsed').textContent, /42 秒/);
  await t.emit('done', done); await t.end();
  assert.equal(t.panel.open, false); t.panel.open = true;
  assert.match(t.panel.textContent, /先核对资料范围.*已读取三个字段.*已确定字段.*整理可交付结果/);
});

test('structured tool receipts stay out of the public process summary', async () => {
  const b = browser(), t = await turn(b, '请整理数据并绘图');
  await t.emit('tool_result', {name: 'run_research_figure_script', tool_call_id: 'structured',
    status: 'complete', summary: '{"execution_id":"internal-receipt","dataset_refs":[{"id":"private-table"}]}'});
  const text = t.panel.querySelector('.research-process-timeline').textContent;
  assert.ok(text.includes('执行结果已记录'));
  assert.ok(!text.includes('internal-receipt') && !text.includes('private-table'));
});

test('the process retains more than 100 actions and updates the earliest action in place', () => {
  const b = browser(), panel = b.context.createResearchProcessPanel('检查所有步骤');
  for (let i = 0; i < 125; i++) panel.receive('tool_call_start', {tool_call_id: 't' + i, display_name: '步骤 ' + i});
  panel.receive('tool_result', {tool_call_id: 't0', display_name: '步骤 0', status: 'complete', summary: '首个结果保留'});
  assert.equal(panel.projection.entries.filter(e => e.kind === 'action').length, 125);
  assert.equal(panel.element.querySelectorAll('.term-line').length, 125);
  assert.match(panel.element.querySelectorAll('.term-line')[0].textContent, /首个结果保留/);
});

test('aliases join one logical operation while failed and retried attempts stay distinct', () => {
  const b = browser(), panel = b.context.createResearchProcessPanel('重试检查');
  panel.receive('tool_call_start', {tool_call_id: 'call', attempt: 1, display_name: '读取资料'});
  panel.receive('operation', {operation_id: 'op', attempt: 1, status: 'running', display_name: '读取资料'});
  panel.receive('tool_result', {logical_key: 'logical', operation_id: 'op', tool_call_id: 'call', aliases: ['alias'], attempt: 1,
    display_name: '读取资料', status: 'failed', summary: '首次超时'});
  panel.receive('tool_call_start', {logical_key: 'logical', tool_call_id: 'retry', attempt: 2, display_name: '读取资料'});
  panel.receive('tool_result', {logical_key: 'logical', tool_call_id: 'retry', attempt: 2, display_name: '读取资料', status: 'complete', summary: '第二次读取完成'});
  // A late alias start must not erase a finished attempt's result.
  panel.receive('tool_executing', {aliases: ['alias'], attempt: 1, status: 'running'});
  const rows = panel.element.querySelectorAll('.term-line');
  assert.equal(rows.length, 2); assert.match(rows[0].textContent, /未完成.*首次超时/);
  assert.match(rows[1].textContent, /已完成.*第二次读取完成.*第 2 次尝试/);
  assert.equal(panel.projection.entries.filter(e => e.kind === 'action')[0].aliases.includes('op'), true);
});

test('sequence gaps buffer, replay deduplicates, and snapshots never advance the consumed cursor', () => {
  const b = browser(), panel = b.context.createResearchProcessPanel('顺序检查'), seen = [];
  const store = b.context.CitrusTaskState.create({onEvent: (event, data) => { seen.push(data.seq); panel.receive(event, data); }});
  store.hydrate({job_id: 'job', event_cursor: 999, replay_cursor: 999});
  assert.equal(store.state.cursor, 0);
  store.receive('assistant_progress', {seq: 3, text: '第三条'});
  store.receive('job', {seq: 1, job_id: 'job'});
  assert.equal(store.state.cursor, 1); assert.equal(store.hasGap(), true);
  store.receive('assistant_progress', {seq: 2, text: '第二条'});
  store.receive('assistant_progress', {seq: 3, text: '重复第三条'});
  assert.deepEqual(seen, [1, 2, 3]); assert.equal(store.state.cursor, 3);
  assert.match(panel.element.textContent, /第二条.*第三条/); assert.doesNotMatch(panel.element.textContent, /重复/);
  store.receive('done', {seq: 4, turn_envelope: {display_status: 'awaiting_input'}});
  assert.equal(store.state.display_status, ''); assert.equal(store.state.status, 'awaiting_input');
});

test('live and trace history render the same public event projection', async () => {
  const b = browser(), live = await turn(b, '相同投影');
  const events = [
    ['assistant_progress', {text: '先检查字段'}],
    ['tool_call_start', {tool_call_id: 'a', display_name: '读取表格'}],
    ['tool_result', {tool_call_id: 'a', display_name: '读取表格', status: 'partial', summary: '部分字段可用', facts: {coverage: '三列'}}],
    ['assistant_progress', {text: '保留现有结果并解释限制'}],
    ['status', {stage: 'final_answer', message: '整理结果'}]
  ];
  for (const [event, data] of events) await live.emit(event, data);
  const text = live.panel.querySelector('.research-process-timeline').textContent;
  await live.emit('done', done); await live.end();
  b.context.renderHistoryMessages([], b.context.normalizeHistoryTurns([{turn_request_id: 'history', user: {content: '相同投影'}, answer: {content: '回答'},
    terminal_snapshot: {display_status: 'partial'}, trace: events.map(([event, data], i) => ({seq: i + 1, event, data}))}]));
  const history = b.get('chat-container').querySelector('.research-process');
  assert.equal(history.querySelector('.research-process-timeline').textContent, text);
});

test('history with a job replays from actual zero and repeated snapshots cannot close a manual reopen', async () => {
  const b = browser();
  b.context.renderHistoryMessages([], b.context.normalizeHistoryTurns([{turn_request_id: 'old', job_id: 'job', replay_cursor: 100,
    terminal_snapshot: {display_status: 'partial', terminal_seq: 100}, user: {content: '恢复'}, answer: {content: '保存的回答'},
    trace: [{kind: 'note', content: 'trace fallback must not duplicate job events'}]}]));
  const panel = b.get('chat-container').querySelector('.research-process');
  assert.equal(panel.open, false); panel.open = true;
  const replay = b.requests.find(r => r.url.includes('/events?'));
  assert.equal(replay.url, '/jobs/job/events?after=0&limit=200');
  reply(replay, framesPage('job', [[1, 'job', {job_id: 'job'}], [2, 'assistant_progress', {text: '回放的公开说明'}],
    [3, 'tool_result', {tool_call_id: 't', display_name: '数据审查', status: 'partial'}], [4, 'done', {answer: '权威回答', turn_envelope: {display_status: 'partial'}}]],
    {terminal_snapshot: {display_status: 'partial'}}));
  await flush();
  reply(b.requests.find(r => r.url === '/jobs/job'), {job_id: 'job', event_cursor: 100, terminal_snapshot: {display_status: 'partial'}});
  await flush();
  assert.equal(panel.open, true); assert.equal(panel.querySelectorAll('.term-line').length, 1);
  assert.match(panel.textContent, /回放的公开说明/); assert.doesNotMatch(panel.textContent, /trace fallback/);
  assert.match(b.get('chat-container').textContent, /权威回答/);
});

test('answered history keeps its question label when the closed pending event replays', async () => {
  const b = browser();
  b.context.renderHistoryMessages([], b.context.normalizeHistoryTurns([{turn_request_id: 'old', job_id: 'job',
    terminal_snapshot: {display_status: 'awaiting_input'}, question: question({status: 'answered', child_job_id: 'child'}),
    user: {content: '恢复'}, answer: {content: '请选择'}}]));
  const panel = b.get('chat-container').querySelector('.research-process');
  assert.match(panel.textContent, /已答复并继续/);
  const replay = b.requests.find(r => r.url.includes('/events?'));
  reply(replay, framesPage('job', [[1, 'request_user_input', {question: question()}],
    [2, 'done', {turn_envelope: {display_status: 'awaiting_input'}}]], {terminal_snapshot: {display_status: 'awaiting_input'}}));
  await flush();
  assert.match(panel.textContent, /已答复并继续/);
  assert.doesNotMatch(panel.querySelector('summary').textContent, /状态未确认|等待你的选择/);
  assert.equal(panel.open, false);
});

test('terminal authority changes status but only a visible answer collapses the trace once', () => {
  const b = browser();
  for (const status of ['completed', 'partial', 'failed', 'cancelled']) {
    const panel = b.context.createResearchProcessPanel('终态');
    panel.receive('answer_start', {}); assert.equal(panel.element.open, true);
    assert.equal(panel.terminal('awaiting_input'), false); assert.equal(panel.element.open, true);
    assert.equal(panel.terminal(status), true); assert.equal(panel.element.open, true);
    panel.receive('done', {answer: '保留的答复'}); assert.equal(panel.element.open, false);
    panel.element.open = true; panel.terminal(status); panel.terminal(status);
    panel.receive('done', {answer: '答复补全'});
    assert.equal(panel.element.open, true);
  }
});

test('an older stopped job finishes in its own DOM without releasing a newer composer', async () => {
  const b = browser(), old = await turn(b, '旧请求');
  await old.emit('job', {seq: 1, job_id: 'old-job'});
  b.context.stopCurrentTask(); await flush();
  const replay = b.requests.find(r => r.url.includes('/jobs/old-job/events?after=1'));
  const current = await turn(b, '当前请求');
  assert.equal(b.get('send-btn').disabled, true);
  reply(replay, framesPage('old-job', [[2, 'assistant_progress', {text: '旧轮补回的说明'}],
    [3, 'done', {answer: '旧轮最终回答', turn_envelope: {display_status: 'cancelled'}}]], {terminal_snapshot: {display_status: 'cancelled'}}));
  await flush();
  assert.equal(old.panel.open, false); assert.match(old.panel.textContent, /旧轮补回的说明/);
  assert.match(old.content.textContent, /旧轮最终回答/);
  assert.equal(current.panel.open, true); assert.equal(b.get('send-btn').disabled, true);
  assert.doesNotMatch(current.content.textContent, /旧轮最终回答/);
  await current.emit('done', done); await current.end();
});

test('awaiting question stays outside the process panel and is never labelled failed on stream end', async () => {
  const b = browser(), t = await turn(b);
  await t.emit('request_user_input', {question: question()});
  await t.emit('done', {answer: '请确认范围后继续。', turn_envelope: {display_status: 'awaiting_input'}}); await t.end();
  const card = t.content.querySelector('.task-question');
  assert.ok(card); assert.equal(t.panel.contains(card), false); assert.equal(card.hidden, false);
  assert.equal(t.panel.open, false); assert.match(t.panel.textContent, /等待你的选择/);
  assert.doesNotMatch(t.content.textContent, /未完成|失败|do not show refs/);
  assert.equal(b.get('send-btn').disabled, false);
});

function questionUI(b, value, extra = {}) {
  const store = b.context.CitrusTaskState.create();
  const transport = b.context.CitrusTaskTransport.create({store, fetch: b.context.accountFetch, owns: extra.owns || (() => true)});
  const container = new Element(); b.document.body.appendChild(container);
  const ui = b.context.CitrusTaskControls.createQuestion({container, store, transport, owns: extra.owns || (() => true), onContinue: extra.onContinue});
  store.hydrate({job_id: 'job', question: value});
  return {store, transport, ui, container};
}

test('single and multiple choices submit strict persistent question answers and create no chat request', async () => {
  for (const multi_select of [false, true]) {
    const b = browser(), continued = [], q = question({multi_select});
    const {store, ui} = questionUI(b, q, {onContinue: result => continued.push(result.child_job_id)});
    const inputs = ui.element.querySelectorAll('input');
    assert.equal(inputs[0].type, multi_select ? 'checkbox' : 'radio');
    inputs[0].checked = true; if (multi_select) inputs[1].checked = true;
    ui.element.querySelector('textarea').value = '补充范围';
    const form = ui.element.querySelector('form'), pending = fire(form, 'submit'); await flush();
    const req = b.requests.at(-1);
    assert.equal(req.url, '/api/tasks/job/questions/q1/answer');
    const body = JSON.parse(req.opts.body);
    assert.deepEqual(body.selected_option_ids, multi_select ? ['a', 'b'] : ['a']);
    assert.equal(body.version, 1); assert.equal(body.text, '补充范围'); assert.equal(body.cancelled, false); assert.match(body.client_request_id, /^q-/);
    reply(req, {question: {...q, status: 'answered', child_job_id: 'child'}, child_job_id: 'child', created: true}); await pending;
    assert.equal(store.state.question.status, 'answered'); assert.deepEqual(continued, ['child']);
    assert.equal(b.requests.some(r => r.url === '/api/v2/chat'), false);
    assert.equal(ui.element.querySelectorAll('input').every(input => input.disabled), true);
    store.hydrate({job_id: 'job', question: q}); assert.equal(store.state.question.status, 'answered');
  }
});

test('custom-only answers and cancellation preserve the endpoint contract', async () => {
  for (const cancelled of [false, true]) {
    const b = browser(), q = question({options: []}), {ui} = questionUI(b, q);
    ui.element.querySelector('textarea').value = '我的自定义范围';
    const pending = cancelled ? fire(ui.element.querySelector('.task-question-actions').children[1], 'click') : fire(ui.element.querySelector('form'), 'submit');
    await flush(); const req = b.requests.at(-1), body = JSON.parse(req.opts.body);
    assert.equal(body.cancelled, cancelled); assert.deepEqual(body.selected_option_ids, []);
    assert.equal(body.text, cancelled ? '' : '我的自定义范围');
    reply(req, {question: {...q, status: cancelled ? 'cancelled' : 'answered'}}); await pending;
    assert.match(ui.element.textContent, cancelled ? /已取消/ : /已记录/);
  }
});

test('question drafts survive heartbeat snapshots and rejected answers can retry with the same request id', async () => {
  const b = browser(), q = question(), {store, ui} = questionUI(b, q);
  const form = ui.element.querySelector('form'), input = ui.element.querySelectorAll('input')[0]; input.checked = true;
  store.hydrate({job_id: 'job', question: {...q}});
  assert.equal(ui.element.querySelector('form'), form);
  const first = fire(form, 'submit'); await flush(); const req = b.requests.at(-1);
  reply(req, {detail: 'offline'}, 503); await first;
  assert.equal(input.checked, true);
  const second = fire(form, 'submit'); await flush(); const retry = b.requests.at(-1);
  assert.equal(JSON.parse(req.opts.body).client_request_id, JSON.parse(retry.opts.body).client_request_id);
  reply(retry, {question: {...q, status: 'answered'}}); await second;
});

test('late question replies cannot update another account/session UI or start a continuation', async () => {
  const b = browser(); let owns = true, continued = false;
  const q = question(), {store, ui} = questionUI(b, q, {owns: () => owns, onContinue: () => { continued = true; }});
  ui.element.querySelector('textarea').value = '范围';
  const pending = fire(ui.element.querySelector('form'), 'submit'); await flush(); const req = b.requests.at(-1);
  owns = false; reply(req, {question: {...q, status: 'answered'}, child_job_id: 'wrong'}); await pending;
  assert.equal(store.state.question.status, 'pending'); assert.equal(continued, false);
});

test('an accepted answer that has not dispatched can retry the same child after refresh', async () => {
  const b = browser(), continued = [], accepted = {selected_option_ids: ['a'], text: 'saved purpose', cancelled: false};
  const q = question({status: 'answered', answer: accepted, child_job_id: 'child', child_status: 'queued'});
  const {ui} = questionUI(b, q, {onContinue: result => continued.push(result.child_job_id)});
  assert.match(ui.element.textContent, /重试继续/);
  assert.equal(ui.element.querySelector('input').disabled, true);
  const pending = fire(ui.element.querySelector('form'), 'submit'); await flush();
  const req = b.requests.at(-1), body = JSON.parse(req.opts.body);
  assert.deepEqual(body.selected_option_ids, accepted.selected_option_ids); assert.equal(body.text, accepted.text);
  reply(req, {question: {...q, child_status: 'running'}, child_job_id: 'child', dispatched: true}); await pending;
  assert.deepEqual(continued, ['child']); assert.doesNotMatch(ui.element.textContent, /重试继续/);
});

test('private reasoning, scripts and internal intermediate tables never enter process prose or formal attachments', () => {
  const b = browser(), panel = b.context.createResearchProcessPanel('公开过程');
  panel.receive('reasoning', {text: 'private secret'});
  panel.receive('assistant_progress', {text: '```python\nimport os\nprint("secret")\n```'});
  panel.receive('tool_result', {tool_call_id: 'tool', display_name: '数据审查', status: 'complete',
    summary: '表格已检查', inputs: {script: 'private script'}, facts: {coverage: '三个字段', script: 'secret'},
    tables: [{name: 'intermediate.csv', visibility: 'internal'}], result_refs: [{label: 'private ref', visibility: 'internal'}]});
  assert.match(panel.element.textContent, /表格已检查/);
  assert.equal(panel.element.querySelectorAll('.task-result').length, 0);
  assert.doesNotMatch(panel.element.textContent, /三个字段|secret|import os|private|intermediate.csv/);
  const content = b.context.addMessage('bot', '回答'); content.dataset.sessionId = 'session'; content.dataset.requestId = 'turn';
  b.context.appendTurnGeneratedFiles(content, [file('public'), {...file('internal'), visibility: 'internal'}, {...file('intermediate'), is_intermediate: true}]);
  assert.match(content.nextSibling.textContent, /public.txt/); assert.doesNotMatch(content.nextSibling.textContent, /internal.txt|intermediate.txt/);
});

test('production stream arms at answer_start and collapses only on nonempty text or done.answer', async () => {
  for (const status of ['failed', 'cancelled']) {
    const b = browser(), t = await turn(b);
    await t.emit('text', {content: '工具轮临时文本'}); assert.equal(t.panel.open, true);
    await t.emit('answer_start', {}); assert.equal(t.panel.open, true);
    await t.emit('text', {content: ' \n\t'}); assert.equal(t.panel.open, true);
    await t.emit('done', {turn_envelope: {display_status: status}}); await t.end();
    assert.equal(t.panel.open, true); assert.match(t.panel.textContent, status === 'failed' ? /未完成/ : /已停止/);
  }
  const b = browser(), t = await turn(b);
  await t.emit('answer_start', {}); await t.emit('text', {content: '首段回答'});
  assert.equal(t.panel.open, false);
  t.panel.open = true;
  await t.emit('text', {content: '后续回答'}); assert.equal(t.panel.open, true);
  await t.emit('done', done); await t.end(); assert.equal(t.panel.open, true);
  const next = await turn(b); await next.emit('done', done); await next.end();
  assert.equal(next.panel.open, false);
});

test('production text_reset reopens a withdrawn answer and requires a fresh arm before text', async () => {
  const b = browser(); b.context.requestAnimationFrame = fn => fn();
  const t = await turn(b);
  await t.emit('answer_start', {}); await t.emit('text', {content: '已撤回回答'});
  assert.equal(t.panel.open, false);
  await t.emit('text_reset', {}); assert.equal(t.panel.open, true);
  await t.emit('text', {content: '这仍是临时工作文本'}); assert.equal(t.panel.open, true);
  assert.doesNotMatch(t.content.querySelector('.turn-answer').textContent, /临时工作文本|已撤回回答/);
  await t.emit('answer_start', {}); await t.emit('text', {content: '确认后的回答'});
  assert.equal(t.panel.open, false); assert.match(t.content.querySelector('.turn-answer').textContent, /确认后的回答/);
  await t.emit('done', {...done, answer: '确认后的回答'}); await t.end();
});

test('persisted answered history ignores old reset and never closes a manual reopen during replay', async () => {
  const b = browser();
  b.context.renderHistoryMessages([], b.context.normalizeHistoryTurns([{turn_request_id:'old', job_id:'job',
    user:{content:'旧问题'}, answer:{content:'持久回答'}, terminal_snapshot:{display_status:'completed'}}]));
  const panel = b.get('chat-container').querySelector('.research-process'); assert.equal(panel.open, false);
  const replay = b.requests.find(r => r.url.includes('/events?'));
  reply(replay, framesPage('job', [[1,'job',{job_id:'job'}], [2,'answer_start',{}], [3,'text_reset',{}],
    [4,'text',{content:'旧临时文本'}]], {has_more:true})); await flush();
  assert.equal(panel.open, false); assert.match(b.get('chat-container').textContent, /持久回答/);
  assert.doesNotMatch(b.get('chat-container').textContent, /旧临时文本/);
  panel.open = true;
  const next = b.requests.findLast(r => r.url.includes('/events?after=4'));
  reply(next, framesPage('job', [[5,'answer_start',{}], [6,'text',{content:'回放文本'}],
    [7,'done',done]], {terminal_snapshot:{display_status:'completed'}})); await flush();
  assert.equal(panel.open, true);
});

// Synthetic event fixtures below verify UI contracts only; no model or service executes.
function attachedPanel(b, goal = '界面回放测试') {
  const panel = b.context.createResearchProcessPanel(goal);
  b.get('chat-container').appendChild(panel.element); return panel;
}
function toolRecord(id, extra = {}) {
  return {tool_call_id: id, args_json: '{"file":"fixture.csv"}', result_text: '测试记录正文', error_text: '', truncated: false, ...extra};
}

test('timeline public notes preserve full paragraphs, stable identities and tool associations', () => {
  const b = browser(), panel = attachedPanel(b), text = '公开说明。'.repeat(150) + '\n下一段说明。';
  const note = {text, note_id: 'n1', phase: 'working', step_id: 'inspect', model_step: 2, tool_call_ids: ['read']};
  panel.receive('assistant_progress', {...note, seq: 1});
  panel.receive('assistant_progress', {...note, seq: 2});
  panel.receive('assistant_progress', {...note, note_id: 'n2', seq: 3});
  const notes = panel.projection.entries.filter(item => item.note_id);
  assert.equal(notes.length, 2); assert.equal(notes[0].text, text);
  const row = panel.element.querySelectorAll('.research-process-note')[1];
  assert.equal(row.textContent, text); assert.equal(row.dataset.noteId, 'n1');
  assert.equal(row.dataset.phase, 'working'); assert.equal(row.dataset.stepId, 'inspect');
  assert.equal(row.dataset.modelStep, '2'); assert.equal(row.dataset.toolCallIds, '["read"]');
  assert.equal(panel.element.querySelectorAll('script').length, 0);
});

test('timeline plan stays separate, supports all step states and cannot terminate the task', () => {
  const b = browser(), panel = attachedPanel(b);
  const planBox = panel.element.querySelector('.research-process-plan');
  assert.equal(planBox.hidden, true); // Ordinary QA never gets an invented plan.
  panel.receive('job', {job_id: 'job'});
  const states = ['pending', 'in_progress', 'completed', 'blocked', 'cancelled'];
  panel.receive('task_plan', {plan_id: 'job', seq: 2, steps: states.map((status, i) => ({id: 's' + i, title: '测试步骤 ' + i, status}))});
  assert.equal(planBox.hidden, false); assert.equal(planBox.dataset.planId, 'job');
  assert.deepEqual(planBox.querySelectorAll('.research-plan-step').map(row => row.dataset.status), states);
  assert.equal(planBox.querySelectorAll('.research-plan-step')[1].getAttribute('aria-current'), 'step');
  assert.equal(panel.element.querySelectorAll('.term-line').length, 0);
  assert.match(planBox.textContent, /1\/5 步已完成/);
  assert.match(panel.stage.textContent, /测试步骤 1/);
  assert.equal(planBox.tagName, 'SECTION'); assert.equal(planBox.querySelectorAll('details').length, 0);
  panel.receive('task_plan', {plan_id: 'job', seq: 3, steps: [{id: 'only', title: '测试完成步骤', status: 'completed'}]});
  assert.equal(planBox.hidden, false); assert.equal(panel.element.open, true);
  assert.equal(planBox.querySelectorAll('.research-plan-step').length, 1);
  assert.match(panel.label.textContent, /进行中/); assert.match(panel.stage.textContent, /等待下一步执行记录/);
  panel.receive('task_plan', {plan_id: 'other', seq: 4, steps: []});
  assert.equal(panel.projection.currentPlan().plan_id, 'job');
});

test('timeline current work follows active actions and clears honestly at terminal authority', () => {
  const b = browser(), panel = attachedPanel(b);
  panel.receive('assistant_progress', {text: '检查输入资料', note_id: 'n'});
  panel.receive('tool_call_start', {tool_call_id: 'read', display_name: '读取测试表格'});
  panel.receive('tool_call_start', {tool_call_id: 'audit', display_name: '检查测试字段'});
  assert.match(panel.stage.textContent, /读取测试表格.*检查测试字段/);
  panel.receive('tool_result', {tool_call_id: 'read', status: 'complete'});
  assert.doesNotMatch(panel.stage.textContent, /读取测试表格/);
  assert.equal(panel.element.querySelectorAll('.term-line')[1].getAttribute('aria-current'), 'step');
  panel.terminal('failed');
  assert.equal(panel.stage.hidden, true);
  const unfinished = panel.element.querySelectorAll('.term-line')[1];
  assert.equal(unfinished.getAttribute('aria-current'), null); assert.match(unfinished.textContent, /未记录结束状态/);
  assert.equal(panel.projection.entries.find(item => item.data?.tool_call_id === 'audit').data.status, 'running');
});

test('timeline tool details load only on expansion, render text, preserve open state and cache results', async () => {
  const b = browser(), panel = attachedPanel(b);
  panel.receive('tool_result', {tool_call_id: 'call / 1', display_name: '测试读取', status: 'partial', summary: '测试摘要'});
  const detail = panel.element.querySelector('.research-tool-details');
  assert.equal(b.requests.length, 0);
  await fire(detail.parentNode.querySelector('.research-tool-toggle'), 'click');
  assert.equal(b.requests.length, 1); assert.equal(b.requests[0].url, '/api/v2/session/session/tool-result/call%20%2F%201');
  reply(b.requests[0], toolRecord('call / 1', {args_json: '<img src=x onerror=alert(1)>', result_text: '<script>fixture only</script>',
    error_text: '测试错误 <b>text</b>', truncated: true, result_chars: 9000})); await flush();
  assert.match(detail.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.match(detail.textContent, /<script>fixture only<\/script>/); assert.match(detail.textContent, /测试错误/);
  assert.match(detail.textContent, /已截断.*9000/);
  assert.equal(detail.querySelectorAll('script').length, 0); assert.equal(detail.querySelectorAll('img').length, 0);
  panel.receive('assistant_progress', {text: '保留部分结果', note_id: 'n'});
  panel.receive('tool_result', {tool_call_id: 'call / 1', status: 'partial', summary: '补充测试摘要'});
  assert.equal(panel.element.querySelector('.research-tool-details'), detail); assert.equal(detail.hidden, false);
  const toggle = detail.parentNode.querySelector('.research-tool-toggle');
  await fire(toggle, 'click'); assert.equal(detail.hidden, true); assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  await fire(toggle, 'click'); assert.equal(detail.hidden, false); assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(b.requests.length, 1);
});

test('timeline tool details show missing/access/error states and allow an explicit retry', async () => {
  for (const [status, notice] of [[404, /未留存或已轮转/], [403, /无权读取/], [500, /读取失败/]]) {
    const b = browser(), panel = attachedPanel(b);
    panel.receive('tool_result', {tool_call_id: 'missing', status: 'failed', summary: '原摘要保留'});
    const detail = panel.element.querySelector('.research-tool-details'); await fire(detail.parentNode.querySelector('.research-tool-toggle'), 'click');
    reply(b.requests[0], {}, status); await flush();
    assert.match(detail.textContent, notice); assert.match(panel.element.textContent, /原摘要保留/);
    const retrying = fire(detail.querySelector('button'), 'click'); await flush();
    reply(b.requests[1], toolRecord('missing')); await retrying;
    assert.match(detail.textContent, /测试记录正文/); assert.doesNotMatch(detail.textContent, /重新读取/);
  }
});

test('timeline tool detail late JSON cannot cross account, session or retired DOM boundaries', async () => {
  for (const invalidate of [b => { b.context.advanceAuthEpoch(); }, b => { b.context.sessionId = 'other'; },
    (b, panel) => { panel.element.remove(); }]) {
    const b = browser(), panel = attachedPanel(b);
    panel.receive('tool_result', {tool_call_id: 'late', status: 'complete'});
    const detail = panel.element.querySelector('.research-tool-details'); await fire(detail.parentNode.querySelector('.research-tool-toggle'), 'click');
    const body = deferred(); b.requests[0].resolve({ok: true, json: () => body.promise}); await flush();
    invalidate(b, panel); body.resolve(toolRecord('late', {result_text: 'must not enter stale DOM'})); await flush();
    assert.doesNotMatch(detail.textContent, /must not enter stale DOM/);
  }
});

test('timeline tool detail refreshes after a running attempt settles without duplicating concurrent loads', async () => {
  const b = browser(), panel = attachedPanel(b);
  panel.receive('tool_call_start', {tool_call_id: 'pending', display_name: '测试处理中'});
  const detail = panel.element.querySelector('.research-tool-details'); await fire(detail.parentNode.querySelector('.research-tool-toggle'), 'click');
  const toggle = detail.parentNode.querySelector('.research-tool-toggle');
  await fire(toggle, 'click'); await fire(toggle, 'click'); assert.equal(b.requests.length, 1);
  panel.receive('tool_result', {tool_call_id: 'pending', status: 'complete'});
  reply(b.requests[0], {}, 404); await flush();
  assert.equal(b.requests.length, 2);
  reply(b.requests[1], toolRecord('pending')); await flush();
  assert.match(detail.textContent, /测试记录正文/); assert.equal(detail.hidden, false);
});

test('timeline live and job replay share public metadata, plan and expandable receipt renderer', async () => {
  const b = browser(), live = await turn(b, '界面回放测试');
  const events = [
    ['job', {job_id: 'job'}],
    ['status', {stage: 'task_plan', plan_id: 'job', steps: [{id: 's', title: '检查测试资料', status: 'in_progress'}]}],
    ['assistant_progress', {text: '先核对测试输入。\n再解释限制。', note_id: 'n', phase: 'working', step_id: 's', model_step: 1, tool_call_ids: ['t']}],
    ['tool_call_start', {tool_call_id: 't', step_id: 's', display_name: '测试资料检查'}],
    ['tool_result', {tool_call_id: 't', status: 'partial', summary: '测试资料不完整', facts: {coverage: '合成测试输入'}}],
    ['task_plan', {plan_id: 'job', steps: [{id: 's', title: '检查测试资料', status: 'blocked'}]}],
    ['done', {...done, turn_envelope: {display_status: 'partial'}}]
  ];
  for (let i = 0; i < events.length; i++) await live.emit(events[i][0], {...events[i][1], seq: i + 1});
  await live.end();
  const expected = live.panel.querySelector('.research-process-timeline').textContent;
  const expectedPlan = live.panel.querySelector('.research-process-plan').textContent;
  b.context.renderHistoryMessages([], b.context.normalizeHistoryTurns([{job_id: 'job', turn_request_id: 'history',
    user: {content: '界面回放测试'}, answer: {content: '测试回答'}, terminal_snapshot: {display_status: 'partial'}}]));
  const history = b.get('chat-container').querySelector('.research-process');
  const replay = b.requests.findLast(request => request.url === '/jobs/job/events?after=0&limit=200');
  reply(replay, framesPage('job', events.map(([event, data], i) => [i + 1, event, data]), {terminal_snapshot: {display_status: 'partial'}}));
  await flush();
  assert.equal(history.querySelector('.research-process-timeline').textContent, expected);
  assert.equal(history.querySelector('.research-process-plan').textContent, expectedPlan);
  assert.equal(history.querySelectorAll('.research-process-note')[1].dataset.noteId, 'n');
  assert.equal(history.querySelector('.research-process-stage').hidden, true);
  const detail = history.querySelector('.research-tool-details'); await fire(detail.parentNode.querySelector('.research-tool-toggle'), 'click');
  const request = b.requests.findLast(request => request.url.endsWith('/tool-result/t'));
  assert.ok(request); reply(request, toolRecord('t')); await flush();
  assert.match(detail.textContent, /测试记录正文/);
});

const repaired = (old = 'old', next = 'new', resolution = 'repaired') => ({tool_call_id:old, resolved_by_tool_call_id:next, resolution});
test('explicit tool resolutions update the original row without erasing status, errors or unrelated failures', async () => {
  for (const resolution of ['repaired','source_replaced','superseded']) {
    const b=browser(), panel=attachedPanel(b);
    panel.receive('tool_result',{tool_call_id:'old',tool_name:'same',display_name:'读取资料',status:'failed',summary:'原始失败摘要'});
    panel.receive('tool_result',{tool_call_id:'other',tool_name:'same',display_name:'不同输入',status:'failed'});
    const row=panel.element.querySelector('.term-line'), detail=panel.element.querySelector('.research-tool-details');
    await fire(row.querySelector('button'),'click');
    reply(b.requests[0],toolRecord('old',{error_text:'原始错误始终保留'})); await flush();
    panel.receive('tool_result',{tool_call_id:'new',tool_name:'same',display_name:'后续成功',status:'complete'});
    assert.equal(row.classList.contains('is-failed'),true);
    const relation={resolutions:[repaired('old','new',resolution)]};
    panel.receive('tool_resolution',relation); panel.receive('tool_resolution',relation);
    assert.equal(panel.element.querySelector('.term-line'),row);
    assert.equal(panel.element.querySelectorAll('.term-line').length,3);
    assert.equal(row.classList.contains('is-resolved'),true); assert.equal(row.classList.contains('is-failed'),false);
    assert.equal(row.dataset.resolution,resolution); assert.equal(row.dataset.resolvedBy,'new');
    assert.match(row.textContent,resolution==='repaired'?/已修复/:/已由后续结果替代/);
    assert.match(row.textContent,/原始失败摘要/);
    assert.equal(panel.projection.entries.find(e=>e.data?.tool_call_id==='old').data.status,'failed');
    assert.equal(panel.element.querySelector('.research-tool-details'),detail); assert.equal(detail.hidden,false);
    assert.match(detail.textContent,/原始错误始终保留/);
    const other=panel.element.querySelectorAll('.term-line')[1]; assert.equal(other.classList.contains('is-failed'),true);
  }
});

test('explicit admission repair resolves not_run in place while cancelled and unknown attempts remain unresolved', () => {
  const b=browser(), panel=attachedPanel(b);
  panel.receive('tool_result',{tool_call_id:'reject',status:'not_run',reason_code:'invalid_tool_arguments',summary:'缺少必需参数'});
  const row=panel.element.querySelector('.term-line');
  panel.receive('tool_result',{tool_call_id:'accepted',status:'complete',summary:'修正参数后成功'});
  panel.receive('tool_resolution',{resolutions:[repaired('reject','accepted')]});
  assert.equal(panel.element.querySelector('.term-line'),row); assert.match(row.textContent,/已修复/);
  assert.equal(row.className,'term-line is-resolved'); assert.equal(row.dataset.resolvedBy,'accepted');
  assert.equal(panel.projection.entries[1].data.status,'not_run');
  for (const status of ['cancelled','unknown']) {
    panel.receive('tool_result',{tool_call_id:status,status});
    panel.receive('tool_resolution',{resolutions:[repaired(status,'accepted')]});
    const unresolved=panel.element.querySelectorAll('.term-line').at(-1);
    assert.equal(unresolved.classList.contains('is-resolved'),false); assert.equal(unresolved.dataset.resolution,'');
  }
  assert.equal(panel.element.querySelectorAll('.term-line').length,4);
});

test('resolution before rows survives ordering, rejects malformed and unknown outcomes and never invents rows', () => {
  const b=browser(), panel=attachedPanel(b);
  panel.receive('tool_resolution',{seq:1,resolutions:[repaired()]});
  assert.equal(panel.element.querySelectorAll('.term-line').length,0);
  panel.receive('tool_result',{tool_call_id:'new',status:'complete'});
  panel.receive('tool_result',{tool_call_id:'old',status:'partial'});
  const row=panel.element.querySelectorAll('.term-line')[1]; assert.equal(row.dataset.resolution,'repaired');
  for (const value of [repaired('old','new','unknown'),repaired('old','','repaired'),repaired('old','old'),
    {tool_call_id:4,resolved_by_tool_call_id:'new',resolution:'repaired'},repaired('another','new','unknown')]) {
    panel.receive('tool_resolution',{seq:2,resolutions:[value]});
  }
  panel.receive('tool_resolution',{seq:1,resolutions:[repaired('old','new','superseded')]});
  assert.equal(row.dataset.resolution,'repaired'); assert.equal(panel.element.querySelectorAll('.term-line').length,2);
  panel.receive('tool_result',{tool_call_id:'another',status:'failed'});
  assert.equal(panel.element.querySelectorAll('.term-line')[2].classList.contains('is-failed'),true);
});

test('persisted terminal resolutions restore before trace rows and late snapshots update live original rows', async () => {
  const b=browser();
  b.context.renderHistoryMessages([],b.context.normalizeHistoryTurns([{turn_request_id:'saved',user:{content:'历史修复'},
    answer:{content:'已保存回答'},terminal_snapshot:{display_status:'completed',tool_resolutions:[repaired()]},
    trace:[{seq:1,event:'tool_result',data:{tool_call_id:'old',status:'not_run'}},
      {seq:2,event:'tool_result',data:{tool_call_id:'new',status:'complete'}}]}]));
  const history=b.get('chat-container').querySelector('.research-process');
  assert.equal(history.querySelector('.term-line').className,'term-line is-resolved');
  assert.equal(history.querySelector('.term-line').dataset.resolution,'repaired'); assert.equal(history.open,false);
  const live=await turn(b); await live.emit('job',{seq:1,job_id:'live-job'});
  await live.emit('tool_result',{seq:2,tool_call_id:'old',status:'failed'});
  const row=live.panel.querySelector('.term-line');
  b.context.stopCurrentTask(); await flush();
  const replay=b.requests.findLast(r=>r.url.includes('/jobs/live-job/events?after=2'));
  reply(replay,framesPage('live-job',[],{terminal_snapshot:{display_status:'partial',tool_resolutions:[repaired()]}})); await flush();
  assert.equal(live.panel.querySelector('.term-line'),row); assert.equal(row.dataset.resolution,'repaired');
  assert.equal(live.panel.open,true); await live.end();
});

test('production live and history replay share deduplicated tool resolution and envelope fallback', async () => {
  const b=browser(), live=await turn(b);
  const events=[['job',{job_id:'job'}],['tool_result',{tool_call_id:'old',status:'failed',summary:'真实失败记录'}],
    ['tool_result',{tool_call_id:'new',status:'complete'}],['tool_resolution',{resolutions:[repaired()]}],
    ['done',{...done,turn_envelope:{display_status:'completed',tool_resolutions:[repaired()]}}]];
  for (let i=0;i<events.length;i++) {await live.emit(events[i][0],{...events[i][1],seq:i+1});}
  await live.emit('tool_resolution',{seq:4,resolutions:[repaired('old','new','unknown')]}); await live.end();
  const row=live.panel.querySelector('.term-line'); assert.equal(row.dataset.resolution,'repaired');
  assert.equal(live.panel.querySelectorAll('.term-line').length,2);
  const expected=live.panel.querySelector('.research-process-timeline').textContent;
  b.context.renderHistoryMessages([],b.context.normalizeHistoryTurns([{job_id:'job',turn_request_id:'history',
    user:{content:'回放'},answer:{content:'持久回答'},terminal_snapshot:{display_status:'completed',tool_resolutions:[repaired()]}}]));
  const history=b.get('chat-container').querySelector('.research-process');
  const replay=b.requests.findLast(r=>r.url.includes('/events?after=0'));
  reply(replay,framesPage('job',events.map(([event,data],i)=>[i+1,event,data]))); await flush();
  assert.equal(history.querySelector('.research-process-timeline').textContent,expected);
  assert.equal(history.querySelector('.term-line').dataset.resolution,'repaired');
  // A terminal envelope restores the relationship even when the distinct event was not retained.
  const fallback=attachedPanel(b);
  fallback.receive('error',{turn_envelope:{display_status:'partial',tool_resolutions:[repaired('a','b','superseded')]}});
  fallback.receive('tool_result',{tool_call_id:'a',status:'failed'});
  assert.equal(fallback.element.querySelector('.term-line').dataset.resolution,'superseded');
  assert.equal(fallback.projection.entries[1].data.status,'failed');
});
