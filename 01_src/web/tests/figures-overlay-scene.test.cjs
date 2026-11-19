'use strict';
// Regression: the overlay boxes must follow the scene that produced the
// displayed SVG. A server-preview record can survive a newer local projection
// (the reload of the saved scene), and must not keep supplying stale geometry.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');

const flush = async () => { for (let i = 0; i < 50; i++) await Promise.resolve(); };

const SAVED_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><g id="tone"><text x="5" y="5">SAVED</text></g></svg>';
const PREVIEW_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><g id="tone"><text x="5" y="5">PREVIEW</text></g></svg>';

const TONE_ID = 'obj_' + '1'.repeat(32);

function figureDocument(revision, objectId = TONE_ID) {
  return {schema: 'citrus.figure-document/1', document_id: 'figure-A', revision,
    objects: [{gid: 'tone', object_id: objectId, identity_scope: 'semantic', resolvable: true}]};
}

function detail() {
  return {figure_id: 'figure-A', title: 'A', render_version: 1, source_kind: 'spec', editable: true,
    document: figureDocument(1),
    overrides: [], objects: 2, exports: {svg: {size_bytes: SAVED_SVG.length}, png: {}, pdf: {}},
    manifest: {figure_size_px: [400, 300], objects: [
      // No saved colour: reverting the draft forces the saved-scene reload path.
      {gid: 'tone', role: 'annot', editable: ['color', 'offset_px'], bbox: {x0: 10, y0: 10, x1: 110, y1: 40}},
      {gid: 'series', role: 'series', editable: ['color'], color: '#222222', bbox: {x0: 20, y0: 50, x1: 160, y1: 200}}
    ]}};
}

function realm() {
  let document;
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null;
      this.attrs = {}; this.dataset = {}; this.style = {}; this.listeners = {};
      this.className = ''; this.value = ''; this.disabled = false; this.hidden = false;
      this.checked = false; this.type = ''; this.clientWidth = 200; this.clientHeight = 150;
      this.naturalWidth = 400; this.naturalHeight = 300; this.complete = true; this._src = '';
      this.classList = {
        contains: name => this.className.split(/\s+/).includes(name),
        add: name => { if (!this.classList.contains(name)) this.className += ' ' + name; },
        remove: name => { this.className = this.className.split(/\s+/).filter(x => x !== name).join(' '); },
        toggle: (name, on) => { this.classList[on === undefined ? (this.classList.contains(name) ? 'remove' : 'add') : on ? 'add' : 'remove'](name); }
      };
    }
    get src() { return this._src; }
    set src(value) { this._src = String(value); this.attrs.src = String(value); }
    get currentSrc() { return this._src; }
    setAttribute(name, value) {
      this.attrs[name] = String(value);
      if (name === 'value') this.value = String(value);
      if (name === 'class') this.className = String(value);
      if (name.startsWith('data-')) this.dataset[name.slice(5)] = String(value);
    }
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }
    removeAttribute(name) { delete this.attrs[name]; if (name === 'src') this._src = ''; }
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
    replaceChildren(...children) { this.children.forEach(n => { n.parentNode = null; }); this.children = []; this._text = ''; children.forEach(n => this.appendChild(n)); }
    set innerHTML(value) {
      this.replaceChildren(); this._html = value;
      for (const match of String(value).matchAll(/<([a-z][a-z0-9-]*)\b([^>]*)>/gi)) {
        const child = new Element(match[1]);
        for (const attr of match[2].matchAll(/([\w-]+)="([^"]*)"/g)) child.setAttribute(attr[1], attr[2]);
        child.checked = /\bchecked\b/.test(match[2]); this.appendChild(child);
      }
    }
    get innerHTML() { return this._html || ''; }
    set textContent(value) { this.replaceChildren(); this._text = String(value); }
    get textContent() { return (this._text || '') + this.children.map(n => n.textContent).join(''); }
    matches(selector) {
      if (selector.startsWith('.')) return selector.slice(1).split('.').every(n => this.classList.contains(n));
      const attr = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
      if (attr) return this.getAttribute(attr[1]) !== null && (attr[2] === undefined || this.getAttribute(attr[1]) === attr[2]);
      return this.tagName.toLowerCase() === selector;
    }
    closest(selector) { return this.matches(selector) ? this : this.parentNode ? this.parentNode.closest(selector) : null; }
    querySelectorAll(selector) { return this.children.flatMap(n => (n.matches(selector) ? [n] : []).concat(n.querySelectorAll(selector))); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    contains(node) { return node === this || this.children.some(n => n.contains(node)); }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    fire(name, data = {}) {
      const event = {target: this, prevented: false, preventDefault() { this.prevented = true; }, ...data};
      const results = (this.listeners[name] || []).map(callback => callback(event));
      return {event, results};
    }
    focus() { document.activeElement = this; }
    setPointerCapture(id) { (this.captures ||= new Set()).add(id); }
    hasPointerCapture(id) { return !!(this.captures && this.captures.has(id)); }
    releasePointerCapture(id) { if (this.captures) this.captures.delete(id); }
    getBoundingClientRect() { return {left: 0, top: 0, width: 800, height: 600}; }
  }

  const nodes = new Map(), timers = new Map(), pending = [], windowEvents = new Element('window');
  let savedGets = 0, blobId = 0, timerId = 0;
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  document = new Element('document');
  Object.assign(document, {readyState: 'loading', documentElement: new Element('html'), activeElement: null,
    getElementById: get, createElement: tag => new Element(tag)});

  const savedResponse = () => ({ok: true, status: 200, headers: {get: () => String(SAVED_SVG.length)},
    blob: async () => new Blob([SAVED_SVG], {type: 'image/svg+xml'}), body: {cancel: async () => {}}});
  const staticResponse = () => ({ok: true, status: 200, headers: {get: () => null}, body: {cancel: async () => {}}});
  const json = data => ({ok: true, status: 200, json: async () => data});

  const sandbox = {
    document, console, AbortController, DOMException, URLSearchParams, Blob, innerWidth: 1200,
    URL: {createObjectURL() { return 'blob:fixture/' + ++blobId; }, revokeObjectURL() {}},
    location: {search: '', replace() {}}, localStorage: {getItem: () => null},
    setTimeout(callback, delay) { timers.set(++timerId, {callback, delay}); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    requestAnimationFrame(callback) { timers.set(++timerId, {callback, frame: true}); return timerId; },
    cancelAnimationFrame(id) { timers.delete(id); }, confirm: () => true,
    addEventListener: (name, callback) => windowEvents.addEventListener(name, callback),
    fetch(url, options = {}) {
      if (options.method === 'POST' && /\/(preview|overrides)$/.test(url)) {
        let resolve, reject;
        const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
        pending.push({url, options, resolve, reject}); return promise;
      }
      if (/\/preview\.svg\?v=1$/.test(url)) {
        savedGets += 1;
        // First load: a real saved scene. Reload after the local projection:
        // the server omits a usable length, so the workspace keeps the static
        // saved image and must not keep the old preview overlay.
        return Promise.resolve(savedGets === 1 ? savedResponse() : staticResponse());
      }
      if (/\/api\/v2\/figures\/projects$/.test(url)) return Promise.resolve(json({projects: []}));
      if (/\/api\/v2\/figures\/projects\/figure-A$/.test(url)) return Promise.resolve(json(detail()));
      return Promise.reject(new Error('unexpected request ' + url));
    }
  };
  sandbox.window = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(readFileSync(join(__dirname, '../assets/figures/edit_model.js'), 'utf8'), ctx, {filename: 'edit_model.js'});
  vm.runInContext(readFileSync(join(__dirname, '../assets/figures.js'), 'utf8'), ctx, {filename: 'figures.js'});
  // Controller double: geometry/picking is exercised in the real browser. It
  // keeps the manifest it was constructed with, exactly like the production
  // FigureScene, and reports every local change as unsupported so the
  // workspace asks the server for the preview.
  sandbox.FigureScene = class {
    constructor(svg, manifest) { this.svg = svg; this.manifest = manifest; this.base = {}; }
    project(changes) { return {svg: this.svg, document: this.base, unsupported: changes}; }
  };

  return {
    get, pending, sandbox, document, windowEvents, Element,
    tick() {
      for (const [id, timer] of [...timers]) if (timer.frame) { timers.delete(id); timer.callback(); }
      for (const [id, timer] of [...timers]) if (timer.delay === 180) { timers.delete(id); timer.callback(); }
    },
    reply(request, data) { request.resolve(json(data)); }
  };
}

function select(b, gid) { b.get('objectSelect').value = gid; b.get('objectSelect').fire('change'); }
function input(b, prop, value) {
  const field = b.get('propertyPanel').querySelector(`[data-prop="${prop}"]`);
  assert.ok(field, `missing property ${prop}`);
  field.value = String(value);
  b.get('propertyPanel').fire('input', {target: field});
}
function visibleBoxes(b) { return b.get('overlay').children.filter(node => !node.hidden); }

test('overlay follows the displayed scene after a local projection reloads the saved scene', async () => {
  const b = realm();
  b.document.fire('DOMContentLoaded');
  await flush();

  // Open the project: the first saved-scene GET yields a usable scene.
  const open = new b.Element('button');
  open.setAttribute('data-open', 'figure-A');
  b.get('projectList').fire('click', {target: open});
  await flush();
  assert.equal(b.sandbox.FigureScene !== undefined, true);

  // Draft a colour with no saved baseline; it cannot be projected locally.
  select(b, 'tone');
  input(b, 'color', '#abcdef');
  b.tick();

  // Server preview returns a scene whose manifest carries a preview-only box.
  const preview = b.pending.at(-1);
  assert.match(preview.url, /\/preview$/);
  assert.deepEqual(JSON.parse(preview.options.body).overrides,
    [{object_id: TONE_ID, prop: 'color', value: '#abcdef'}]);
  const previewManifest = JSON.parse(JSON.stringify(detail().manifest));
  previewManifest.objects.push({gid: 'previewOnly', role: 'annot', editable: ['color'],
    bbox: {x0: 5, y0: 5, x1: 25, y1: 25}});
  b.reply(preview, {figure_id: 'figure-A', render_version: 1,
    draft_revision: JSON.parse(preview.options.body).draft_revision,
    manifest: previewManifest, svg: PREVIEW_SVG});
  await flush();
  assert.equal(b.get('overlay').children.some(node => node.dataset.gid === 'previewOnly' && !node.hidden), true,
    'server preview geometry is displayed');

  // Revert the draft: the local projection decides it needs the saved scene and
  // reloads it. The reload keeps the static saved image.
  b.get('undoButton').fire('click');
  b.tick();
  await flush();
  assert.match(b.get('previewImage').getAttribute('src'), /\/preview\.svg\?v=1$/,
    'saved image is the displayed render');

  // The overlay must agree with the displayed saved scene, not the superseded
  // server preview that still exists in memory.
  assert.deepEqual(visibleBoxes(b), [], 'no stale server-preview overlay boxes remain visible');
  assert.equal(b.get('overlay').children.some(node => node.dataset.gid === 'previewOnly' && !node.hidden), false);
});

test('consecutive saves use the FigureDocument returned with each committed revision', async () => {
  const b = realm();
  b.document.fire('DOMContentLoaded');
  await flush();
  const open = new b.Element('button');
  open.setAttribute('data-open', 'figure-A');
  b.get('projectList').fire('click', {target: open});
  await flush();
  select(b, 'tone');
  input(b, 'color', '#123456');
  b.get('saveButton').fire('click');
  const first = b.pending.at(-1);
  assert.match(first.url, /\/overrides$/);
  assert.deepEqual(JSON.parse(first.options.body), {expected_revision: 1,
    overrides: [{object_id: TONE_ID, prop: 'color', value: '#123456'}]});
  const nextId = 'obj_' + '2'.repeat(32);
  b.reply(first, {...detail(), render_version: 2, document: figureDocument(2, nextId),
    overrides: [{gid: 'tone', prop: 'color', value: '#123456'}]});
  await flush();
  input(b, 'color', '#234567');
  b.get('saveButton').fire('click');
  const second = b.pending.at(-1);
  assert.notEqual(second, first);
  assert.deepEqual(JSON.parse(second.options.body), {expected_revision: 2,
    overrides: [{object_id: nextId, prop: 'color', value: '#234567'}]});
  b.reply(second, {...detail(), render_version: 3, document: figureDocument(3, nextId),
    overrides: [{gid: 'tone', prop: 'color', value: '#234567'}]});
  await flush();
});
