'use strict';
// Run production scripts in isolated browser-like realms. No source extraction,
// copied reducers or string assertions: requests, readers and UI events are real
// entry points; only browser/network APIs are doubles.
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
  appendChild(child) { child.remove(); this.children.push(child); child.parentNode = this; return child; }
  insertBefore(child, before) {
    if (!before) return this.appendChild(child);
    child.remove(); const index = this.children.indexOf(before);
    if (index < 0) return this.appendChild(child);
    this.children.splice(index, 0, child); child.parentNode = this; return child;
  }
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
function browser(script, {localMarkdown = false} = {}) {
  const nodes = new Map(), requests = [], readers = [], revoked = [], navigations = [], timers = new Map();
  let timerId = 0;
  const windowListeners = {};
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
  const sandbox = {console, document, AbortController, AbortSignal: new AbortController().signal.constructor, DOMException, TextDecoder, URLSearchParams,
    URL: {createObjectURL: () => 'blob:' + Math.random(), revokeObjectURL: url => revoked.push(url)},
    FileReader: Reader, localStorage: storage(), sessionStorage: storage(),
    location: {search: '', hash: '', pathname: '/', replace: url => navigations.push(url)},
    history: {replaceState() {}}, navigator: {}, performance: {now: () => 1},
    innerWidth: 1280, confirm: () => true, alert() {}, prompt: () => null,
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id),
    setInterval: fn => { timers.set(++timerId, fn); return timerId; }, clearInterval: id => timers.delete(id),
    requestAnimationFrame: fn => { timers.set(++timerId, fn); return timerId; },
    cancelAnimationFrame: id => timers.delete(id),
    Event: class { constructor(type) { this.type = type; } },
    CustomEvent: class { constructor(type, opts) { this.type = type; this.detail = opts.detail; } },
    addEventListener(type, fn) { (windowListeners[type] ||= []).push(fn); },
    dispatchEvent(event) { (windowListeners[event.type] || []).forEach(fn => fn(event)); },
    fetch: (url, opts = {}) => { const pending = deferred(); requests.push({url, opts, ...pending}); return pending.promise; }
  };
  sandbox.window = sandbox;
  const ctx = vm.createContext(sandbox);
  if (localMarkdown) {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const markdownPath = html.match(/<script src="([^"]*marked[^"]*)"/)[1];
    assert.ok(markdownPath.startsWith('/assets/'), 'Markdown must load without an external network');
    vm.runInContext(readFileSync(join(__dirname, '..', markdownPath.slice(1)), 'utf8'), ctx,
      {filename: markdownPath});
  }
  if (script === 'app.js') {
    for (const module of ['task-state.js', 'task-transport.js', 'task-cards.js', 'task-controls.js']) {
      vm.runInContext(readFileSync(join(__dirname, '../assets', module), 'utf8'), ctx, {filename: module});
    }
  }
  if (script === 'figures.js') {
    for (const module of ['figures/edit_model.js', 'figures/scene_projection.js']) {
      vm.runInContext(readFileSync(join(__dirname, '../assets', module), 'utf8'), ctx, {filename: module});
    }
  }
  vm.runInContext(readFileSync(join(__dirname, '../assets', script), 'utf8'), ctx, {filename: script});
  return {ctx, get, document, requests, readers, revoked, navigations, timers,
    reply(req, data, status = 200) { req.resolve({ok: status < 400, status, json: async () => data}); },
    eval(code) { return vm.runInContext(code, ctx); }};
}

// Shared wrapper: run production app.js, then give lifecycle tests the same
// authenticated bare session they historically used. Tests may override both.
function defaultBrowser(script = 'app.js', options) {
  const b = browser(script, options);
  b.context = b.ctx;
  if (script === "app.js") {
    if (!b.ctx.marked || typeof b.ctx.marked.Renderer !== "function") {
      b.ctx.marked = {Renderer: class {}, parse: text => "<p>" + String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</p>"};
    }
  }
  if (script === "app.js" && b.ctx && !b.ctx.currentAccount) {
    b.ctx.currentAccount = {user_id: 'test', username: 'test'};
    b.ctx.sessionId = 'session';
  }
  return b;
}
function login(b, id = 'A') { b.ctx.currentAccount = {user_id: id, username: id}; b.ctx.sessionId = id + '-session'; }
function reply(request, data, status = 200) {
  if (!request) throw new Error('expected an offline HTTP request');
  request.resolve({ok: status < 400, status, json: async () => data});
}
const framesPage = (job, events, extra = {}) => ({protocol: 'citrus-task/1', job_id: job,
  events: events.map(([seq, event, data]) => ({seq, event, data})), has_more: false, ...extra});
const question = (extra = {}) => ({id: 'q1', version: 1, status: 'pending', job_id: 'job',
  prompt: '请选择处理范围', multi_select: false, allow_text: true,
  options: [{id: 'a', label: '全部资料', description: '使用已选资料', refs: [{private: 'do not show refs'}]}, {id: 'b', label: '仅摘要'}], ...extra});
async function fire(element, type, event = {}) {
  if (typeof element.fire === 'function') return element.fire(type, event);
  for (const listener of element.listeners[type] || []) await listener({target: element, preventDefault() {}, ...event});
  await flush();
}
module.exports = {Node, browser: defaultBrowser, flush, deferred, storage, login, reply, framesPage, question, fire};
