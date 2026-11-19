'use strict';
// Shared offline fixture for UI regressions using production task scripts.
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
  fire(name, event = {}) {
    for (const handler of this.listeners[name] || []) handler({target: this, ...event});
  }
  focus() {} scrollIntoView() {}
}
function storage() {
  const map = new Map();
  return {getItem: k => map.get(k) ?? null, setItem: (k, v) => map.set(k, String(v)), removeItem: k => map.delete(k), key: i => [...map.keys()][i], get length() { return map.size; }};
}
function browser() {
  const nodes = new Map(), requests = [], timers = new Map(), readers = [], revoked = [];
  let timerId = 0, objectUrlId = 0;
  class Reader {
    constructor() { readers.push(this); }
    readAsDataURL(file) { this.file = file; }
    abort() { this.aborted = true; this.file = null; this.onabort?.(); }
  }
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const document = {readyState: 'loading', getElementById: get, createElement: tag => new Element(tag),
    querySelector: () => null, querySelectorAll: () => [], body: new Element('body'), documentElement: new Element('html'), addEventListener() {}};
  document.body.appendChild(get('chat-container'));
  for (const id of ['ctxInTokens', 'ctxOutTokens']) { const card = new Element(); card.className = 'ctx-token-card'; card.appendChild(get(id)); }
  new Element().appendChild(get('ctxBar'));
  const context = {console, document, AbortController, AbortSignal, DOMException, TextDecoder, URLSearchParams, navigator: {},
    // Markdown parsing is outside these lifecycle assertions; no vendored code runs.
    marked: {Renderer: class {}, parse: text => '<p>' + String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</p>'},
    URL: {createObjectURL: () => 'blob:fixture-' + ++objectUrlId, revokeObjectURL: url => revoked.push(url)},
    FileReader: Reader, localStorage: storage(), sessionStorage: storage(),
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
  return {context, get, requests, document, readers, revoked};
}
function reply(request, data, status = 200) {
  assert.ok(request, 'expected an offline HTTP request');
  request.resolve({ok: status < 400, status, json: async () => data});
}
const framesPage = (job, events, extra = {}) => ({protocol: 'citrus-task/1', job_id: job,
  events: events.map(([seq, event, data]) => ({seq, event, data})), has_more: false, ...extra});
module.exports = {browser, flush, reply, framesPage};
