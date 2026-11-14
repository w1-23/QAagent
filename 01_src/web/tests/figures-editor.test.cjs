'use strict';
// Execute the production workspace through DOM events and deferred network replies.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const flush = async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); };

function realm({theme = null, pointer = false, width = 1200} = {}) {
  let document;
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null;
      this.attrs = {}; this.dataset = {}; this.style = {}; this.listeners = {};
      this.className = ''; this.value = ''; this.disabled = false; this.hidden = false;
      this.clientWidth = 200; this.clientHeight = 150; this.captures = new Set();
      this.classList = {
        contains: name => this.className.split(/\s+/).includes(name),
        add: name => { if (!this.classList.contains(name)) this.className += ' ' + name; },
        remove: name => { this.className = this.className.split(/\s+/).filter(x => x !== name).join(' '); },
        toggle: (name, on) => { this.classList[on ? 'add' : 'remove'](name); }
      };
    }
    setAttribute(name, value) {
      this.attrs[name] = String(value);
      if (name === 'value') this.value = String(value);
      if (name === 'class') this.className = String(value);
      if (name.startsWith('data-')) this.dataset[name.slice(5)] = String(value);
    }
    getAttribute(name) { return this.attrs[name] ?? null; }
    removeAttribute(name) { delete this.attrs[name]; delete this[name]; }
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
    closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
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
    setPointerCapture(id) { this.captures.add(id); }
    hasPointerCapture(id) { return this.captures.has(id); }
    releasePointerCapture(id) { this.captures.delete(id); this.fire('lostpointercapture', {pointerId: id}); }
  }
  const nodes = new Map(), requests = [], windowEvents = new Element('window'), timers = new Map();
  let timerId = 0;
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  document = new Element('document');
  Object.assign(document, {readyState: 'loading', documentElement: new Element('html'), activeElement: null,
    getElementById: get, createElement: tag => new Element(tag)});
  const sandbox = {document, console, AbortController, DOMException, URLSearchParams, innerWidth: width,
    location: {search: '', replace() {}}, localStorage: {getItem: () => theme},
    setTimeout(callback, delay) { timers.set(++timerId, {callback, delay}); return timerId; },
    clearTimeout(id) { timers.delete(id); }, confirm: () => true,
    addEventListener: (name, callback) => windowEvents.addEventListener(name, callback),
    fetch(url, options = {}) {
      let resolve, reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      requests.push({url, options, resolve, reject}); return promise;
    }};
  if (pointer) sandbox.PointerEvent = class {};
  sandbox.window = sandbox;
  vm.runInNewContext(readFileSync(join(__dirname, '../assets/figures.js'), 'utf8'), sandbox, {filename: 'figures.js'});
  return {get, requests, document, sandbox, windowEvents, Element,
    previewTick() {
      for (const [id, timer] of [...timers]) if (timer.delay === 180) { timers.delete(id); timer.callback(); }
    },
    reply(request, data, status = 200) { request.resolve({ok: status < 400, status, json: async () => data}); }};
}

test('unknown and multicolor metadata never pretends a blue current color or produces an untouched patch', async () => {
  for (const color of [undefined, null, ['#ff0000', '#0000ff']]) {
    const project = detail(); project.manifest.objects[0].color = color;
    const b = await workspace({}, project); select(b);
    const field = b.get('propertyPanel').querySelector('[data-prop="color"]');
    assert.equal(field.getAttribute('type'), 'text'); assert.equal(field.value, '');
    assert.match(b.get('propertyPanel').innerHTML, /原色未提供或包含多种颜色/);
    assert.doesNotMatch(b.get('propertyPanel').innerHTML, /#0f4d92/);
    const count = b.requests.length;
    click(b, 'saveButton'); input(b, 'color', '#12'); click(b, 'saveButton');
    assert.equal(b.requests.length, count); assert.equal(b.get('undoButton').disabled, true);
    input(b, 'color', '#abcdef'); click(b, 'undoButton');
    assert.equal(value(b, 'color'), ''); assert.equal(b.get('undoButton').disabled, true);
    click(b, 'redoButton'); assert.equal(value(b, 'color'), '#abcdef');
    click(b, 'saveButton');
    assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides, [{gid: 'title', prop: 'color', value: '#abcdef'}]);
  }
});

test('known colors show the actual swatch including black and saved overrides', async () => {
  const project = detail(); project.manifest.objects[0].color = '#000000';
  const b = await workspace({}, project); select(b);
  assert.equal(value(b, 'color'), '#000000');
  assert.equal(b.get('propertyPanel').querySelector('[data-prop="color"]').getAttribute('type'), 'color');
  assert.doesNotMatch(b.get('propertyPanel').innerHTML, /原色未提供/);
  input(b, 'color', '#000000'); assert.equal(b.get('undoButton').disabled, true);
  const saved = detail('figure-B', {overrides: [{gid: 'title', prop: 'color', value: '#123456'}]});
  delete saved.manifest.objects[0].color;
  await open(b, saved); select(b); assert.equal(value(b, 'color'), '#123456');
});

test('unknown frame state requires an explicit choice, including an explicit false', async () => {
  for (const choice of ['true', 'false']) {
    const project = detail(); project.manifest.objects[0].editable.push('frameon');
    const b = await workspace({}, project); select(b);
    const field = b.get('propertyPanel').querySelector('[data-prop="frameon"]');
    assert.equal(field.tagName, 'SELECT');
    assert.match(b.get('propertyPanel').innerHTML, /未提供边框状态/);
    const count = b.requests.length;
    input(b, 'frameon', ''); click(b, 'saveButton'); assert.equal(b.requests.length, count);
    input(b, 'frameon', choice); assert.equal(b.get('undoButton').disabled, false);
    click(b, 'undoButton'); assert.equal(b.get('propertyPanel').querySelector('[data-prop="frameon"]').tagName, 'SELECT');
    click(b, 'redoButton');
    assert.equal(b.get('propertyPanel').querySelector('[data-prop="frameon"]').checked, choice === 'true');
    click(b, 'saveButton');
    assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides, [{gid: 'title', prop: 'frameon', value: choice === 'true'}]);
  }
});

test('known frame metadata remains a checkbox and figure role is labeled as canvas', async () => {
  for (const frameon of [false, true]) {
    const project = detail();
    Object.assign(project.manifest.objects[0], {role: 'figure', editable: ['color', 'frameon'], frameon});
    const b = await workspace({}, project); select(b);
    assert.equal(b.get('objectName').textContent, '画布');
    const field = b.get('propertyPanel').querySelector('[data-prop="frameon"]');
    assert.equal(field.getAttribute('type'), 'checkbox'); assert.equal(field.checked, frameon);
    b.get('propertyPanel').fire('input', {target: field});
    assert.equal(b.get('undoButton').disabled, true);
  }
});

function previewReply(project = detail(), svg = '<svg xmlns="http://www.w3.org/2000/svg"/>') {
  return {figure_id: project.figure_id, manifest: project.manifest, render_version: project.render_version, svg, overrides: []};
}

test('temporary preview sends pending edits only and never double-adds preview offsets', async () => {
  const b = await workspace({}, detail('figure-A', {overrides: [{gid: 'title', prop: 'color', value: '#444444'}]}));
  select(b); input(b, 'offset_x', 20); b.previewTick();
  const req = b.requests.at(-1);
  assert.match(req.url, /\/preview$/);
  assert.deepEqual(JSON.parse(req.options.body), {overrides: [{gid: 'title', prop: 'offset_px', value: [20, 0]}], expected_revision: 1});
  const result = previewReply(); result.manifest.objects[0].bbox.x0 += 20; result.manifest.objects[0].bbox.x1 += 20;
  b.reply(req, result); await flush();
  assert.match(b.get('previewImage').src, /^data:image\/svg\+xml;charset=utf-8,/);
  assert.equal(box(b).style.left, '7.5%');
  assert.match(b.get('editStatus').textContent, /未保存.*预览已更新/);
  assert.match(b.get('projectMeta').textContent, /第 1 版/);
  b.get('overlay').fire('keydown', {target: box(b), key: 'ArrowRight'});
  assert.equal(box(b).style.left, '7.75%');
  click(b, 'undoButton'); assert.equal(box(b).style.left, '7.5%');
  click(b, 'undoButton'); assert.match(b.get('previewImage').src, /\/preview.svg\?v=1$/);
  assert.equal(box(b).style.left, '2.5%'); assert.match(b.get('editStatus').textContent, /没有本地修改/);
});

test('preview coalesces changes into one latest request and preserves property focus', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#aaaaaa'); b.previewTick();
  const first = b.requests.at(-1), count = b.requests.length, oldSource = b.get('previewImage').src;
  const field = b.get('propertyPanel').querySelector('[data-prop="color"]'); field.focus();
  input(b, 'color', '#bbbbbb'); b.previewTick(); input(b, 'color', '#cccccc'); b.previewTick();
  assert.equal(b.requests.length, count);
  b.reply(first, previewReply(detail(), '<svg>obsolete</svg>')); await flush();
  assert.equal(b.get('previewImage').src, oldSource); assert.equal(b.requests.length, count + 1);
  const latest = b.requests.at(-1);
  assert.deepEqual(JSON.parse(latest.options.body).overrides, [{gid: 'title', prop: 'color', value: '#cccccc'}]);
  const svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>untrusted()</script></svg>';
  b.reply(latest, previewReply(detail(), svg)); await flush();
  assert.equal(decodeURIComponent(b.get('previewImage').src.split(',')[1]), svg);
  assert.equal(b.get('propertyPanel').querySelector('[data-prop="color"]'), field);
  assert.equal(b.document.activeElement, field);
  assert.equal(b.get('overlay').querySelector('script'), null);
  b.get('propertyPanel').fire('change', {target: field}); b.previewTick();
  assert.equal(b.requests.length, count + 1, 'same accepted revision needs no duplicate preview');
});

test('preview completion from an earlier project visit is rejected even when abort is ignored', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#aaaaaa'); b.previewTick();
  const stale = b.requests.at(-1);
  await open(b, detail('figure-B')); await open(b, detail());
  const source = b.get('previewImage').src;
  b.reply(stale, previewReply(detail(), '<svg>old visit</svg>')); await flush();
  assert.equal(b.get('previewImage').src, source); assert.equal(b.get('undoButton').disabled, true);
  assert.equal(stale.options.signal.aborted, true);
});

test('formal save invalidates temporary rendering and continues to use saved CAS revision', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#aaaaaa'); b.previewTick();
  const preview = b.requests.at(-1);
  const save = click(b, 'saveButton'), req = b.requests.at(-1);
  assert.match(req.url, /\/overrides$/); assert.equal(JSON.parse(req.options.body).expected_revision, 1);
  const project = detail('figure-A', {render_version: 2}); project.manifest.objects[0].color = '#aaaaaa';
  await finishSave(b, save, req, project, [{gid: 'title', prop: 'color', value: '#aaaaaa'}]);
  const savedSource = b.get('previewImage').src;
  b.reply(preview, previewReply(detail(), '<svg>late temporary image</svg>')); await flush();
  assert.equal(b.get('previewImage').src, savedSource); assert.match(b.get('projectMeta').textContent, /第 2 版/);
  assert.equal(b.get('undoButton').disabled, true);
});

test('preview errors retain edits and explicitly report an unsaved stale preview', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#aaaaaa'); b.previewTick();
  const req = b.requests.at(-1), count = b.requests.length;
  b.reply(req, {detail: '图件版本已变化', code: 'revision_conflict'}, 409); await flush(); b.previewTick();
  assert.equal(b.requests.length, count); assert.equal(value(b, 'color'), '#aaaaaa');
  assert.match(b.get('editStatus').textContent, /未保存.*预览未更新/);
  assert.equal(b.get('undoButton').disabled, false); assert.match(b.get('projectMeta').textContent, /第 1 版/);
});

test('figure and large series do not intercept text; dragging keeps target nodes mounted and redraws on release', async () => {
  const project = detail();
  project.manifest.objects[1].bbox = {x0: 0, y0: 0, x1: 400, y1: 300};
  project.manifest.objects.push({gid: 'figure_1', role: 'figure', editable: [], bbox: {x0: 0, y0: 0, x1: 400, y1: 300}});
  const b = await workspace({pointer: true}, project), title = box(b), root = box(b, 'figure_1');
  assert.equal(root.style.pointerEvents, 'none');
  assert.ok(Number(title.style.zIndex) > Number(box(b, 'series').style.zIndex));
  b.get('overlay').fire('pointerdown', {target: title, pointerId: 3, button: 0, clientX: 10, clientY: 10});
  assert.equal(box(b), title); assert.equal(title.parentNode, b.get('overlay'));
  b.document.fire('pointermove', {pointerId: 3, clientX: 20, clientY: 20});
  assert.equal(box(b), title); assert.equal(b.get('overlay').hasPointerCapture(3), true);
  const count = b.requests.length; b.previewTick(); assert.equal(b.requests.length, count);
  b.document.fire('pointerup', {pointerId: 3}); b.previewTick();
  assert.match(b.requests.at(-1).url, /\/preview$/);
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides, [{gid: 'title', prop: 'offset_px', value: [20, 20]}]);
});

test('declared fonts, weights, point colors and spine widths are editable; hidden objects stay restorable', async () => {
  const project = detail(); project.manifest.font_families = ['DejaVu Sans', 'Noto Sans CJK SC'];
  Object.assign(project.manifest.objects[0], {editable: ['fontfamily', 'fontweight', 'visible'], fontfamily: 'DejaVu Sans', fontweight: 'normal', visible: true});
  project.manifest.objects.push({gid: 'point', role: 'point', editable: ['color', 'visible'], color: '#000000', visible: true,
    bbox: {x0: 20, y0: 50, x1: 26, y1: 56}}, {gid: 'spine', role: 'spine', editable: ['linewidth'], linewidth: 1});
  const b = await workspace({}, project); select(b);
  assert.match(b.get('propertyPanel').innerHTML, /Noto Sans CJK SC/);
  input(b, 'fontfamily', 'Invented Font'); assert.equal(b.get('undoButton').disabled, true);
  input(b, 'fontfamily', 'Noto Sans CJK SC'); input(b, 'fontweight', 'bold');
  select(b, 'point'); assert.equal(value(b, 'offset_x'), undefined); input(b, 'color', '#ff0000');
  b.get('propertyPanel').fire('click', {target: b.get('propertyPanel').querySelector('[data-action="toggle-visible"]')});
  assert.match(b.get('objectSelect').innerHTML, /数据点（已移除）/); assert.equal(box(b, 'point').hidden, true);
  assert.match(b.get('propertyPanel').innerHTML, /恢复对象/);
  b.get('propertyPanel').fire('click', {target: b.get('propertyPanel').querySelector('[data-action="toggle-visible"]')});
  assert.equal(box(b, 'point').hidden, false);
  select(b, 'spine'); input(b, 'linewidth', 2.3); click(b, 'saveButton');
  const edits = JSON.parse(b.requests.at(-1).options.body).overrides;
  assert.deepEqual(edits, [{gid: 'point', prop: 'color', value: '#ff0000'}, {gid: 'spine', prop: 'linewidth', value: 2.3},
    {gid: 'title', prop: 'fontfamily', value: 'Noto Sans CJK SC'}, {gid: 'title', prop: 'fontweight', value: 'bold'}]);
});

test('line marker controls use declared options and keep unknown fill and size unset', async () => {
  const project = detail();
  project.manifest.marker_options = ['o', 's', 'None']; project.manifest.linestyle_options = ['-', '--', 'None'];
  Object.assign(project.manifest.objects[1], {editable: ['marker', 'markersize', 'markerfacecolor', 'linestyle'], marker: 'o', linestyle: '-'});
  const b = await workspace({}, project); select(b, 'series');
  assert.equal(value(b, 'markersize'), ''); assert.equal(value(b, 'markerfacecolor'), '');
  assert.match(b.get('propertyPanel').innerHTML, /圆形/); assert.match(b.get('propertyPanel').innerHTML, /无线条/);
  const count = b.requests.length;
  input(b, 'marker', 'invented'); input(b, 'markersize', 25); input(b, 'markerfacecolor', '');
  click(b, 'saveButton'); assert.equal(b.requests.length, count);
  input(b, 'marker', 's'); input(b, 'markersize', 8); input(b, 'markerfacecolor', '#ffaa00'); input(b, 'linestyle', '--');
  b.previewTick();
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides, [
    {gid: 'series', prop: 'linestyle', value: '--'}, {gid: 'series', prop: 'marker', value: 's'},
    {gid: 'series', prop: 'markerfacecolor', value: '#ffaa00'}, {gid: 'series', prop: 'markersize', value: 8}
  ]);
});

test('object search groups a long list and preserves selected properties and edits outside results', async () => {
  const project = detail();
  for (let i = 0; i < 113; i++) project.manifest.objects.push({gid: 'axes_0.series_0.point_' + i, role: 'point', label: '测量点 ' + i, editable: ['color'], color: '#000000'});
  const b = await workspace({}, project); select(b); input(b, 'color', '#abcdef');
  const field = b.get('propertyPanel').querySelector('[data-prop="color"]'), count = b.requests.length;
  assert.equal(b.get('objectCount').textContent, '115');
  assert.match(b.get('objectSelect').innerHTML, /<optgroup label="数据点（113）">/);
  b.get('objectSearch').value = '测量点 112'; b.get('objectSearch').fire('input');
  assert.equal(b.get('objectCount').textContent, '1/115');
  assert.deepEqual(b.get('objectSelect').querySelectorAll('option').map(n => n.value), ['axes_0.series_0.point_112']);
  assert.equal(b.get('objectSelect').value, '');
  assert.equal(b.get('propertyPanel').querySelector('[data-prop="color"]'), field);
  assert.equal(value(b, 'color'), '#abcdef'); assert.equal(b.get('objectName').textContent, '标题');
  assert.match(b.get('objectFilterStatus').textContent, /当前选中对象不在筛选结果中/);
  b.get('objectSearch').value = '不存在的对象'; b.get('objectSearch').fire('input');
  assert.equal(b.get('objectSelect').disabled, true); assert.equal(b.get('objectFilterStatus').hidden, false);
  assert.match(b.get('objectFilterStatus').textContent, /没有匹配对象/);
  b.get('objectSelect').value = ''; b.get('objectSelect').fire('change');
  assert.equal(value(b, 'color'), '#abcdef'); assert.equal(b.requests.length, count);
  b.get('objectSearch').value = ''; b.get('objectSearch').fire('input');
  assert.equal(b.get('objectSelect').value, 'title'); assert.equal(b.get('objectFilterStatus').hidden, true);
  click(b, 'saveButton');
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides, [{gid: 'title', prop: 'color', value: '#abcdef'}]);
});

test('object filters match Chinese role, English role and case-insensitive gid without preview requests', async () => {
  const project = detail(); project.manifest.objects.push(
    {gid: 'axes_0.series_0.point_0', role: 'point', editable: ['color'], color: '#123456'},
    {gid: 'axes_0.spine_left', role: 'spine', editable: ['linewidth'], linewidth: 1});
  const b = await workspace({}, project), count = b.requests.length;
  for (const query of ['轴线', 'spine', 'AXES_0.SPINE_LEFT']) {
    b.get('objectSearch').value = query; b.get('objectSearch').fire('input');
    assert.deepEqual(b.get('objectSelect').querySelectorAll('option').map(n => n.value), ['axes_0.spine_left']);
  }
  b.get('objectSearch').value = ''; b.get('objectSearch').fire('input');
  b.get('objectRoleFilter').value = 'point'; b.get('objectRoleFilter').fire('change');
  assert.deepEqual(b.get('objectSelect').querySelectorAll('option').map(n => n.value), ['axes_0.series_0.point_0']);
  select(b, 'axes_0.series_0.point_0'); assert.equal(b.get('objectName').textContent, '数据点');
  b.get('objectRoleFilter').value = 'spine'; b.get('objectRoleFilter').fire('change');
  assert.equal(b.get('objectName').textContent, '数据点'); assert.equal(value(b, 'color'), '#123456');
  b.previewTick(); assert.equal(b.requests.length, count); assert.equal(b.get('saveButton').disabled, true);
  await open(b, detail('figure-B'));
  assert.equal(b.get('objectSearch').value, ''); assert.equal(b.get('objectRoleFilter').value, '');
  assert.equal(b.get('objectCount').textContent, '2');
  const html = readFileSync(join(__dirname, '../figures.html'), 'utf8');
  assert.match(html, /id="objectSearch"[^>]*aria-label=/); assert.match(html, /id="objectRoleFilter"[^>]*aria-label=/);
  assert.doesNotMatch(html, /仅显示当前对象支持的属性|选择标题、标签、图例或数据系列/);
});

test('saved panel exposes only the newest supplied revision', async () => {
  const b = await workspace({}, detail('figure-A', {revisions: [
    {render_version: 1, exports: {svg: {download_url: '/api/v2/files/old/download'}}},
    {render_version: 2, exports: {svg: {download_url: '/api/v2/files/new/download'}}}
  ]}));
  assert.equal(b.get('revisionList').children.length, 1);
  assert.deepEqual(b.get('revisionList').querySelectorAll('a').map(link => link.getAttribute('href')), ['/api/v2/files/new/download']);
  const html = readFileSync(join(__dirname, '../figures.html'), 'utf8');
  assert.match(html, /<button[^>]*id="figureGuideButton"[^>]*data-citrus-guide="figures"/);
  assert.match(html, /\/assets\/guide.js\?v=/); assert.doesNotMatch(html, /历史版本|guide.html#edit/);
});

function treeClick(b, attr, id) {
  const button = b.get('objectTree').querySelector(`[${attr}="${id}"]`);
  assert.ok(button, `${attr}=${id}`); button.focus();
  b.get('objectTree').fire('click', {target: button});
  return button;
}
function groupedDetail() {
  const project = detail();
  Object.assign(project.manifest, {marker_options: ['o', 's', '^', 'None'], linestyle_options: ['-', '--'],
    font_styles: ['normal', 'italic', 'oblique'], horizontalalignment_options: ['left', 'center', 'right'], legend_loc_options: ['best', 'upper right']});
  Object.assign(project.manifest.objects[0], {text: 'Original title', editable: ['text', 'color', 'fontsize', 'offset_px', 'fontstyle', 'horizontalalignment'], fontstyle: 'normal', horizontalalignment: 'center'});
  Object.assign(project.manifest.objects[1], {label: 'Treatment', editable: ['label', 'color', 'marker', 'markersize', 'markerfacecolor', 'markeredgecolor', 'markeredgewidth', 'alpha'], marker: 'o', alpha: 1});
  project.manifest.objects.push(
    {gid: 'xlabel', role: 'xlabel', text: 'Time', color: '#555555', fontsize: 7, editable: ['text', 'color', 'fontsize', 'offset_px']},
    {gid: 'point1', parent_gid: 'series', role: 'point', label: 'Measured point', color: '#123456', editable: ['color', 'visible', 'marker'], marker: 'o', visible: false},
    {gid: 'bar1', parent_gid: 'series', role: 'bar', editable: ['color'], color: '#123456'},
    {gid: 'legend', role: 'legend', label: 'Legend', editable: ['ncols', 'loc'], ncols: 1, loc: 'best'},
    {gid: 'axes_0', role: 'axes', editable: ['visible'], visible: true},
    {gid: 'tick_x', role: 'ticklabel', label: '1.0', locator: {axes_index: 0, axis: 'x'}, editable: ['color'], color: '#000000'},
    {gid: 'tick_y', role: 'ticklabel', label: '2.0', locator: {axes_index: 0, axis: 'y'}, editable: ['color'], color: '#000000'});
  return project;
}

test('visible object hierarchy has categories, collapsed series children and separate axis tick groups', async () => {
  const b = await workspace({}, groupedDetail()), tree = b.get('objectTree');
  assert.deepEqual(tree.children.map(item => item.querySelector('[data-group]').getAttribute('data-group')), ['text', 'series', 'legend', 'axes']);
  assert.equal(tree.querySelector('[data-gid="point1"]'), null);
  assert.equal(tree.querySelector('[data-expand="group:series"]').getAttribute('aria-expanded'), 'true');
  // Category and artist IDs may be identical; disclosure identities must remain distinct.
  assert.equal(tree.querySelector('[data-expand="object:series"]').getAttribute('aria-expanded'), 'false');
  assert.ok(tree.querySelector('[data-group="ticks:0:x"]'));
  assert.ok(tree.querySelector('[data-group="ticks:0:y"]'));
  assert.equal(tree.querySelector('[data-gid="tick_x"]'), null);
  const artistDisclosure = tree.querySelector('[data-expand="object:series"]'); artistDisclosure.focus(); tree.fire('click', {target: artistDisclosure});
  const child = tree.querySelector('[data-gid="point1"]'); assert.ok(child);
  assert.match(child.textContent, /已移除/);
  treeClick(b, 'data-gid', 'point1');
  assert.equal(b.get('objectName').textContent, 'Measured point');
  assert.equal(tree.querySelector('[data-gid="point1"]').getAttribute('aria-pressed'), 'true');
  assert.equal(b.document.activeElement, tree.querySelector('[data-gid="point1"]'));
  assert.equal(b.get('saveButton').disabled, true);
  const html = readFileSync(join(__dirname, '../figures.html'), 'utf8');
  assert.match(html, /id="objectSelect"[^>]*hidden[^>]*tabindex="-1"/);
});

test('tree filtering reveals matching children with parents and retains hidden leaf selection', async () => {
  const b = await workspace({}, groupedDetail()), tree = b.get('objectTree'), count = b.requests.length;
  b.get('objectSearch').value = 'Measured'; b.get('objectSearch').fire('input');
  assert.ok(tree.querySelector('[data-group="series"]')); assert.ok(tree.querySelector('[data-gid="series"]'));
  assert.ok(tree.querySelector('[data-gid="point1"]')); assert.equal(tree.querySelector('[data-gid="bar1"]'), null);
  treeClick(b, 'data-gid', 'point1');
  b.get('objectSearch').value = 'Time'; b.get('objectSearch').fire('input');
  assert.equal(tree.querySelector('[data-gid="point1"]'), null);
  assert.equal(b.get('objectName').textContent, 'Measured point');
  assert.equal(value(b, 'color'), '#123456');
  b.previewTick(); assert.equal(b.requests.length, count);
});

test('group styles intersect capabilities and batch in one undo without copying content', async () => {
  const b = await workspace({}, groupedDetail());
  treeClick(b, 'data-group', 'text');
  assert.equal(value(b, 'color'), '', 'different colors are not presented as the first member color');
  assert.equal(value(b, 'fontsize'), '7');
  for (const prop of ['text', 'label', 'offset_x', 'fontstyle']) assert.equal(value(b, prop), undefined);
  input(b, 'color', '#abcdef'); b.previewTick();
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides, [
    {gid: 'title', prop: 'color', value: '#abcdef'}, {gid: 'xlabel', prop: 'color', value: '#abcdef'}]);
  click(b, 'undoButton'); assert.equal(b.get('undoButton').disabled, true); assert.equal(b.get('saveButton').disabled, true);
  click(b, 'redoButton'); assert.equal(value(b, 'color'), '#abcdef');
  click(b, 'saveButton');
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides.map(x => x.prop), ['color', 'color']);
});

test('group selection includes filtered-out members and blocks forged content propagation', async () => {
  const b = await workspace({}, groupedDetail());
  b.get('objectSearch').value = 'Original'; b.get('objectSearch').fire('input');
  treeClick(b, 'data-group', 'text');
  assert.match(b.get('objectFilterStatus').textContent, /包含筛选之外/);
  const fake = new b.Element('textarea'); fake.dataset.prop = 'text'; fake.value = 'Do not copy';
  b.get('propertyPanel').fire('input', {target: fake});
  assert.equal(b.get('saveButton').disabled, true);
  input(b, 'fontsize', 12); click(b, 'saveButton');
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides, [
    {gid: 'title', prop: 'fontsize', value: 12}, {gid: 'xlabel', prop: 'fontsize', value: 12}]);
});

test('group command made during save remains one undo step after rebasing', async () => {
  const project = groupedDetail(), b = await workspace({}, project);
  treeClick(b, 'data-group', 'text'); input(b, 'color', '#abcdef');
  const pending = click(b, 'saveButton'), request = b.requests.at(-1);
  input(b, 'fontsize', 14);
  project.render_version = 2; project.manifest.objects[0].color = '#abcdef'; project.manifest.objects[2].color = '#abcdef';
  await finishSave(b, pending, request, project, [
    {gid: 'title', prop: 'color', value: '#abcdef'}, {gid: 'xlabel', prop: 'color', value: '#abcdef'}]);
  assert.equal(value(b, 'fontsize'), '14'); assert.equal(b.get('undoButton').disabled, false);
  click(b, 'undoButton'); assert.equal(value(b, 'fontsize'), '7');
  assert.equal(b.get('saveButton').disabled, true); assert.equal(b.get('undoButton').disabled, true);
  assert.equal(value(b, 'color'), '#abcdef');
  click(b, 'redoButton'); click(b, 'saveButton');
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body), {expected_revision: 2, overrides: [
    {gid: 'title', prop: 'fontsize', value: 14}, {gid: 'xlabel', prop: 'fontsize', value: 14}]});
});

test('read-only groups and missing style options cannot create edits', async () => {
  const project = groupedDetail(); project.editable = false;
  const b = await workspace({}, project); treeClick(b, 'data-group', 'text');
  assert.match(b.get('propertyPanel').innerHTML, /只读/);
  const forged = new b.Element('input'); forged.dataset.prop = 'color'; forged.value = '#abcdef';
  b.get('propertyPanel').fire('input', {target: forged}); assert.equal(b.get('saveButton').disabled, true);
  const editable = groupedDetail(); delete editable.manifest.marker_options;
  await open(b, editable); treeClick(b, 'data-gid', 'series');
  assert.equal(b.get('propertyPanel').querySelector('[data-prop="marker"]'), null);
  assert.match(b.get('propertyPanel').innerHTML, /暂无可用样式/);
});

test('tree keyboard navigation expands ticks, traverses children and keeps focus on collapse', async () => {
  const b = await workspace({}, groupedDetail()), tree = b.get('objectTree');
  treeClick(b, 'data-group', 'ticks:0:x');
  let button = tree.querySelector('[data-group="ticks:0:x"]');
  tree.fire('keydown', {target: button, key: 'ArrowRight'});
  assert.ok(tree.querySelector('[data-gid="tick_x"]'));
  button = tree.querySelector('[data-group="ticks:0:x"]');
  assert.equal(b.document.activeElement, button);
  tree.fire('keydown', {target: button, key: 'ArrowRight'});
  assert.equal(b.document.activeElement, tree.querySelector('[data-gid="tick_x"]'));
  tree.fire('keydown', {target: b.document.activeElement, key: 'ArrowLeft'});
  assert.equal(b.document.activeElement, button);
  tree.fire('keydown', {target: button, key: 'ArrowLeft'});
  assert.equal(tree.querySelector('[data-gid="tick_x"]'), null);
  assert.equal(b.document.activeElement, tree.querySelector('[data-group="ticks:0:x"]'));
  tree.fire('keydown', {target: b.document.activeElement, key: 'Home'});
  assert.equal(b.document.activeElement.getAttribute('data-group'), 'text');
});

test('text content is literal escaped text and never becomes page markup', async () => {
  const project = groupedDetail(), text = '</textarea><img src=x onerror=alert(1)><script>alert(1)</script> & “研究”';
  project.manifest.objects[0].text = text;
  const b = await workspace({}, project); treeClick(b, 'data-gid', 'title');
  assert.match(b.get('propertyPanel').innerHTML, /&lt;\/textarea&gt;&lt;img/);
  assert.equal(b.get('propertyPanel').querySelector('img'), null); assert.equal(b.get('propertyPanel').querySelector('script'), null);
  assert.equal(b.get('objectName').textContent, text);
  input(b, 'text', text + '\nLiteral $x$'); b.previewTick();
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides, [{gid: 'title', prop: 'text', value: text + '\nLiteral $x$'}]);
  click(b, 'undoButton'); assert.equal(b.get('saveButton').disabled, true);
  input(b, 'text', 'x'.repeat(2001)); assert.equal(b.get('saveButton').disabled, true);
  input(b, 'text', ''); click(b, 'saveButton');
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides, [{gid: 'title', prop: 'text', value: ''}]);
});

test('visual marker and line buttons send declared values and retain keyboard focus', async () => {
  const project = groupedDetail(); project.manifest.objects[1].editable.push('linestyle');
  const b = await workspace({}, project); treeClick(b, 'data-gid', 'series');
  const panel = b.get('propertyPanel'), square = panel.querySelector('[data-value="s"]');
  assert.equal(square.tagName, 'BUTTON'); assert.equal(square.getAttribute('aria-label'), '方形');
  assert.match(panel.innerHTML, /viewBox="0 0 24 24"/); assert.match(panel.innerHTML, /stroke-dasharray="6 3"/);
  square.focus(); panel.fire('click', {target: square});
  assert.equal(square.getAttribute('aria-pressed'), 'true'); assert.equal(b.document.activeElement, square);
  const dashed = panel.querySelector('[data-value="--"]'); panel.fire('click', {target: dashed});
  b.previewTick();
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides, [
    {gid: 'series', prop: 'linestyle', value: '--'}, {gid: 'series', prop: 'marker', value: 's'}]);
  select(b, 'bar1'); assert.equal(value(b, 'marker'), undefined); assert.equal(value(b, 'linestyle'), undefined);
});

test('new style controls honor capability, options and numeric limits', async () => {
  const b = await workspace({}, groupedDetail()); treeClick(b, 'data-gid', 'series');
  input(b, 'alpha', 1.1); input(b, 'markeredgewidth', -1); assert.equal(b.get('saveButton').disabled, true);
  input(b, 'alpha', 0.4); input(b, 'markeredgewidth', 0); input(b, 'markeredgecolor', '#123abc'); input(b, 'label', 'Control <b>literal</b>');
  treeClick(b, 'data-gid', 'legend');
  assert.equal(value(b, 'text'), undefined); input(b, 'ncols', 1.5); input(b, 'ncols', 7);
  input(b, 'ncols', 2); input(b, 'loc', 'upper right');
  treeClick(b, 'data-gid', 'title'); input(b, 'fontstyle', 'italic'); input(b, 'horizontalalignment', 'left');
  click(b, 'saveButton'); const batch = JSON.parse(b.requests.at(-1).options.body).overrides;
  assert.deepEqual(batch, [
    {gid: 'legend', prop: 'loc', value: 'upper right'}, {gid: 'legend', prop: 'ncols', value: 2},
    {gid: 'series', prop: 'alpha', value: 0.4}, {gid: 'series', prop: 'label', value: 'Control <b>literal</b>'},
    {gid: 'series', prop: 'markeredgecolor', value: '#123abc'}, {gid: 'series', prop: 'markeredgewidth', value: 0},
    {gid: 'title', prop: 'fontstyle', value: 'italic'}, {gid: 'title', prop: 'horizontalalignment', value: 'left'}]);
});

test('double-clicking editable text focuses its content editor without changing data', async () => {
  const b = await workspace({}, groupedDetail()), count = b.requests.length;
  b.get('overlay').fire('dblclick', {target: box(b)});
  assert.equal(b.document.activeElement, b.get('propertyPanel').querySelector('[data-prop="text"]'));
  assert.equal(b.get('propertiesTab').getAttribute('aria-selected'), 'true');
  assert.equal(b.get('saveButton').disabled, true); assert.equal(b.requests.length, count);
});

function transform(b) {
  return b.get('workspace').style.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px\) scale\(([-\d.]+)\)/).slice(1).map(Number);
}
function setZoom(b, percent) { b.get('zoomPercent').value = String(percent); b.get('zoomPercent').fire('change'); }

function detail(id = 'figure-A', options = {}) {
  return {figure_id: id, title: id, render_version: 1, source_kind: 'spec', editable: true,
    overrides: [], revisions: [], objects: 2,
    manifest: {figure_size_px: [400, 300], objects: [
      {gid: 'title', role: 'title', editable: ['color', 'fontsize', 'offset_px'], color: '#111111', fontsize: 7,
        bbox: {x0: 10, y0: 10, x1: 150, y1: 40}},
      // A legacy manifest must not reopen series positioning.
      {gid: 'series', role: 'series', editable: ['color', 'offset_px'], color: '#222222',
        bbox: {x0: 20, y0: 50, x1: 160, y1: 200}}
    ]}, ...options};
}
async function workspace(options = {}, project = detail()) {
  const b = realm(options); b.document.fire('DOMContentLoaded');
  b.reply(b.requests[0], {projects: []}); await flush();
  await open(b, project); return b;
}
function requestOpen(b, id) {
  const button = new b.Element('button'); button.setAttribute('data-open', id);
  b.get('projectList').fire('click', {target: button}); return b.requests.at(-1);
}
async function open(b, project) { const req = requestOpen(b, project.figure_id); b.reply(req, project); await flush(); }
function select(b, gid = 'title') { b.get('objectSelect').value = gid; b.get('objectSelect').fire('change'); }
function input(b, prop, value) {
  const field = b.get('propertyPanel').querySelector(`[data-prop="${prop}"]`);
  assert.ok(field, prop); field.value = String(value); b.get('propertyPanel').fire('input', {target: field});
}
function value(b, prop) { return b.get('propertyPanel').querySelector(`[data-prop="${prop}"]`)?.value; }
function click(b, id) { return b.get(id).fire('click').results[0]; }
function box(b, gid = 'title') { return b.get('overlay').children.find(item => item.dataset.gid === gid); }
async function finishSave(b, pending, request, project, overrides) {
  b.reply(request, {manifest: project.manifest, render_version: project.render_version, overrides}); await flush();
  b.reply(b.requests.at(-1), {projects: []}); await pending;
}

for (const theme of ['light', 'cartoon', 'dark', 'invalid', null]) {
  test(`uses existing shared theme selection: ${theme}`, async () => {
    const b = await workspace({theme});
    assert.equal(b.document.documentElement.getAttribute('data-theme'), ['light', 'cartoon', 'dark'].includes(theme) ? theme : 'light');
    select(b); input(b, 'color', '#abcdef');
    b.windowEvents.fire('storage', {key: 'citrus_theme', newValue: 'dark'});
    assert.equal(b.document.documentElement.getAttribute('data-theme'), 'dark');
    assert.equal(value(b, 'color'), '#abcdef'); assert.equal(b.get('undoButton').disabled, false);
  });
}

test('theme stylesheet is reused without defining another page palette or background resource', () => {
  const html = readFileSync(join(__dirname, '../figures.html'), 'utf8');
  const css = readFileSync(join(__dirname, '../assets/figures.css'), 'utf8');
  assert.ok(html.indexOf('/assets/app.css') < html.indexOf('/assets/figures.css'));
  assert.match(css, /--surface:\s*var\(--bg-surface\)/);
  assert.match(css, /--ink:\s*var\(--text-primary\)/);
  assert.doesNotMatch(css, /--(?:accent|theme-bg|theme-veil|bg-deep)\s*:/);
  assert.doesNotMatch(css, /url\(/);
  assert.doesNotMatch(css.match(/body\.figure-app\s*\{([^}]+)\}/)[1], /background/);
  // Only the explicitly requested display grid gets a local background layer.
  assert.equal((css.match(/background-image\s*:/g) || []).length, 1);
  assert.match(css, /\.canvas-grid\s*\{[^}]*background-image/);
  assert.match(html, /图表工作台 · CitrusSchalor/);
  assert.doesNotMatch(html, /QAagent|CitrusQA|CitrusScholar/);
  assert.doesNotMatch(css, /(?:^|\})\s*svg\s*\{[^}]*display:\s*none/);
});

test('selection and unchanged inputs produce no patch; undo and redo remain local', async () => {
  const b = await workspace(); select(b);
  const count = b.requests.length; click(b, 'saveButton');
  assert.equal(b.requests.length, count);
  assert.equal(b.get('saveButton').disabled, true);
  input(b, 'color', '#111111'); assert.equal(b.get('undoButton').disabled, true);
  assert.equal(b.get('saveButton').disabled, true);
  input(b, 'color', '#abcdef'); assert.match(b.get('editStatus').textContent, /未保存/);
  assert.equal(b.get('saveButton').disabled, false);
  click(b, 'undoButton'); assert.equal(value(b, 'color'), '#111111');
  assert.equal(b.get('saveButton').disabled, true);
  assert.match(b.get('editStatus').textContent, /没有本地修改/);
  click(b, 'redoButton'); assert.equal(value(b, 'color'), '#abcdef');
  assert.equal(b.get('saveButton').disabled, false);
  assert.equal(b.requests.length, count);
  click(b, 'saveButton');
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides,
    [{gid: 'title', prop: 'color', value: '#abcdef'}]);
});

test('editing after undo clears redo; returning to the saved value clears the dirty patch', async () => {
  const b = await workspace(); select(b);
  input(b, 'color', '#aaaaaa'); input(b, 'color', '#bbbbbb'); click(b, 'undoButton');
  input(b, 'color', '#cccccc'); assert.equal(b.get('redoButton').disabled, true);
  click(b, 'redoButton'); assert.equal(value(b, 'color'), '#cccccc');
  input(b, 'color', '#111111'); assert.match(b.get('editStatus').textContent, /没有本地修改/);
  click(b, 'undoButton'); assert.equal(value(b, 'color'), '#cccccc');
});

test('successful save ends local history and never changes render version on undo', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#abcdef');
  const pending = click(b, 'saveButton'), req = b.requests.at(-1);
  const project = detail('figure-A', {render_version: 2}); project.manifest.objects[0].color = '#abcdef';
  await finishSave(b, pending, req, project, [{gid: 'title', prop: 'color', value: '#abcdef'}]);
  assert.equal(b.get('saveButton').disabled, true);
  const count = b.requests.length;
  assert.equal(b.get('undoButton').disabled, true); assert.equal(b.get('redoButton').disabled, true);
  click(b, 'undoButton'); assert.equal(value(b, 'color'), '#abcdef');
  assert.match(b.get('projectMeta').textContent, /第 2 版/); assert.equal(b.requests.length, count);
});

test('commands made during save are rebased and remain individually undoable', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#aaaaaa');
  const pending = click(b, 'saveButton'), req = b.requests.at(-1);
  input(b, 'color', '#bbbbbb'); input(b, 'color', '#cccccc');
  assert.equal(b.get('undoButton').disabled, true); click(b, 'undoButton');
  assert.equal(value(b, 'color'), '#cccccc');
  const project = detail('figure-A', {render_version: 2}); project.manifest.objects[0].color = '#aaaaaa';
  await finishSave(b, pending, req, project, [{gid: 'title', prop: 'color', value: '#aaaaaa'}]);
  click(b, 'undoButton'); assert.equal(value(b, 'color'), '#bbbbbb');
  click(b, 'undoButton'); assert.equal(value(b, 'color'), '#aaaaaa');
  assert.equal(b.get('undoButton').disabled, true); assert.match(b.get('editStatus').textContent, /没有本地修改/);
  click(b, 'redoButton'); click(b, 'redoButton'); click(b, 'saveButton');
  const body = JSON.parse(b.requests.at(-1).options.body);
  assert.equal(body.expected_revision, 2); assert.equal(body.overrides[0].value, '#cccccc');
});

test('returning to the previous saved value during a save remains a new unsaved command', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#aaaaaa');
  const pending = click(b, 'saveButton'), req = b.requests.at(-1);
  input(b, 'color', '#111111');
  const project = detail('figure-A', {render_version: 2}); project.manifest.objects[0].color = '#aaaaaa';
  await finishSave(b, pending, req, project, [{gid: 'title', prop: 'color', value: '#aaaaaa'}]);
  assert.equal(value(b, 'color'), '#111111'); assert.match(b.get('editStatus').textContent, /未保存/);
  click(b, 'undoButton'); assert.equal(value(b, 'color'), '#aaaaaa');
  click(b, 'redoButton'); click(b, 'saveButton');
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides,
    [{gid: 'title', prop: 'color', value: '#111111'}]);
});

test('returning to the previous value is clean if the in-flight save fails', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#aaaaaa');
  const pending = click(b, 'saveButton'), req = b.requests.at(-1);
  input(b, 'color', '#111111'); req.reject(new Error('offline')); await pending;
  assert.match(b.get('editStatus').textContent, /没有本地修改/);
  const count = b.requests.length; click(b, 'saveButton'); assert.equal(b.requests.length, count);
  click(b, 'undoButton'); assert.equal(value(b, 'color'), '#aaaaaa');
});

test('CAS 409 retains patch and local history without inventing a new saved version', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#abcdef');
  const pending = click(b, 'saveButton');
  b.reply(b.requests.at(-1), {detail: {code: 'revision_conflict'}}, 409); await pending;
  assert.match(b.get('editStatus').textContent, /未保存.*版本/);
  assert.match(b.get('projectMeta').textContent, /第 1 版/);
  click(b, 'undoButton'); assert.equal(value(b, 'color'), '#111111');
  click(b, 'redoButton'); assert.equal(value(b, 'color'), '#abcdef');
});

test('keyboard selection and nudging respect saved offsets and reject series positions', async () => {
  const b = await workspace({}, detail('figure-A', {overrides: [{gid: 'title', prop: 'offset_px', value: [5, 8]}]}));
  assert.equal(box(b).getAttribute('tabindex'), '0');
  b.get('overlay').fire('keydown', {target: box(b), key: 'Enter'});
  assert.equal(value(b, 'offset_x'), '5');
  b.get('overlay').fire('keydown', {target: box(b), key: 'ArrowRight'});
  b.get('overlay').fire('keydown', {target: box(b), key: 'ArrowDown', shiftKey: true});
  assert.equal(value(b, 'offset_x'), '6'); assert.equal(value(b, 'offset_y'), '18');
  assert.equal(b.document.activeElement.dataset.gid, 'title');
  click(b, 'undoButton'); assert.equal(value(b, 'offset_y'), '8');
  select(b, 'series'); assert.equal(value(b, 'offset_x'), undefined);
  assert.equal(box(b, 'series').classList.contains('is-draggable'), false);
  b.get('overlay').fire('keydown', {target: box(b, 'series'), key: 'ArrowRight'});
  click(b, 'saveButton');
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides,
    [{gid: 'title', prop: 'offset_px', value: [6, 8]}]);
});

test('history shortcuts inside an open dialog cannot undo the underlying figure', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#abcdef');
  const dialogButton = {tagName: 'BUTTON', closest: selector => selector === 'dialog[open]' ? {} : null};
  const result = b.document.fire('keydown', {target: dialogButton, key: 'z', ctrlKey: true});
  assert.equal(result.event.prevented, false);
  assert.equal(value(b, 'color'), '#abcdef');
  assert.equal(b.get('undoButton').disabled, false);
});

test('history shortcuts work outside inputs but do not hijack native input undo', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#abcdef');
  const inputEvent = b.document.fire('keydown', {target: b.get('propertyPanel').querySelector('input'), key: 'z', ctrlKey: true});
  assert.equal(inputEvent.event.prevented, false); assert.equal(value(b, 'color'), '#abcdef');
  b.document.fire('keydown', {target: box(b), key: 'z', metaKey: true}); assert.equal(value(b, 'color'), '#111111');
  b.document.fire('keydown', {target: box(b), key: 'z', metaKey: true, shiftKey: true}); assert.equal(value(b, 'color'), '#abcdef');
});

test('pointer drag captures a single pointer and creates one undo command in manifest units', async () => {
  const b = await workspace({pointer: true});
  b.get('overlay').fire('pointerdown', {target: box(b), pointerId: 4, button: 0, clientX: 10, clientY: 10});
  assert.equal(b.get('overlay').hasPointerCapture(4), true);
  b.document.fire('pointermove', {pointerId: 5, clientX: 100, clientY: 100});
  assert.equal(value(b, 'offset_x'), '0');
  b.document.fire('pointermove', {pointerId: 4, clientX: 15, clientY: 13});
  b.document.fire('pointermove', {pointerId: 4, clientX: 20, clientY: 15});
  b.document.fire('pointerup', {pointerId: 4});
  assert.equal(value(b, 'offset_x'), '20'); assert.equal(value(b, 'offset_y'), '10');
  assert.equal(b.get('overlay').hasPointerCapture(4), false);
  click(b, 'undoButton'); assert.equal(value(b, 'offset_x'), '0'); assert.equal(b.get('undoButton').disabled, true);
  click(b, 'redoButton'); assert.equal(value(b, 'offset_x'), '20');
});

test('pointer cancellation rolls back the gesture and does not undo earlier style edits', async () => {
  const b = await workspace({pointer: true}); select(b); input(b, 'color', '#abcdef');
  b.get('overlay').fire('pointerdown', {target: box(b), pointerId: 4, clientX: 10, clientY: 10});
  b.document.fire('pointermove', {pointerId: 4, clientX: 20, clientY: 15});
  b.document.fire('pointercancel', {pointerId: 4});
  assert.equal(value(b, 'offset_x'), '0'); assert.equal(value(b, 'color'), '#abcdef');
  click(b, 'undoButton'); assert.equal(value(b, 'color'), '#111111'); assert.equal(b.get('undoButton').disabled, true);
});

test('read-only detail rejects property, keyboard and pointer edits', async () => {
  const b = await workspace({pointer: true}, detail('figure-A', {editable: false}));
  b.get('overlay').fire('keydown', {target: box(b), key: 'ArrowRight'});
  b.get('overlay').fire('pointerdown', {target: box(b), pointerId: 1, clientX: 0, clientY: 0});
  b.document.fire('pointermove', {pointerId: 1, clientX: 50, clientY: 50});
  const count = b.requests.length; click(b, 'saveButton'); click(b, 'undoButton'); click(b, 'redoButton');
  assert.equal(b.requests.length, count); assert.equal(b.get('saveButton').disabled, true);
  assert.equal(b.get('undoButton').disabled, true); assert.match(b.get('editStatus').textContent, /只读/);
});

test('unsaved navigation can be cancelled and beforeunload warns without discarding edits', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#abcdef');
  b.sandbox.confirm = () => false;
  const count = b.requests.length; requestOpen(b, 'figure-B');
  assert.equal(b.requests.length, count); assert.equal(b.get('projectTitle').textContent, 'figure-A');
  assert.equal(b.windowEvents.fire('beforeunload').event.prevented, true);
  click(b, 'undoButton'); assert.equal(b.windowEvents.fire('beforeunload').event.prevented, false);
});

test('project switches clear local command history and release an active pointer', async () => {
  const b = await workspace({pointer: true});
  b.get('overlay').fire('pointerdown', {target: box(b), pointerId: 4, clientX: 10, clientY: 10});
  b.document.fire('pointermove', {pointerId: 4, clientX: 20, clientY: 15});
  await open(b, detail('figure-B'));
  assert.equal(b.get('overlay').hasPointerCapture(4), false);
  assert.equal(b.get('undoButton').disabled, true); assert.equal(b.get('redoButton').disabled, true);
  b.document.fire('pointerup', {pointerId: 4}); assert.equal(b.get('undoButton').disabled, true);
});

test('server revision downloads use supplied URLs only and stay separate from local undo', async () => {
  const url = '/api/v2/session/figure-session/artifacts/' + 'a'.repeat(32);
  const b = await workspace({}, detail('figure-A', {revisions: [
    {render_version: 2, exports: {svg: {artifact_url: url}, png: {download_url: '/api/v2/files/file-A/download'}}},
    {render_version: 1, exports: {svg: {artifact_url: 'javascript:alert(1)'}}}
  ]}));
  const links = b.get('revisionList').querySelectorAll('a');
  assert.deepEqual(links.map(link => link.getAttribute('href')), [url, '/api/v2/files/file-A/download']);
  assert.match(b.get('revisionList').textContent, /已保存版本 2/);
  select(b); input(b, 'color', '#abcdef'); click(b, 'undoButton');
  assert.deepEqual(b.get('revisionList').querySelectorAll('a').map(link => link.getAttribute('href')), [url, '/api/v2/files/file-A/download']);
});

test('history refresh never replaces edits or render version and stale history is ignored', async () => {
  const b = await workspace(); select(b); input(b, 'color', '#abcdef');
  const pending = click(b, 'refreshRevisions'); const req = b.requests.at(-1);
  b.reply(req, detail('figure-A', {render_version: 8, revisions: [{render_version: 8, exports: {}}]})); await pending;
  assert.equal(value(b, 'color'), '#abcdef'); assert.match(b.get('projectMeta').textContent, /第 1 版/);
  const stale = click(b, 'refreshRevisions'); const old = b.requests.at(-1);
  await open(b, detail('figure-B'));
  b.reply(old, detail('figure-A', {revisions: [{render_version: 99, exports: {}}]})); await stale;
  assert.equal(b.get('revisionList').textContent, ''); assert.equal(b.get('projectTitle').textContent, 'figure-B');
});

test('real SVG point dimensions become millimeters; zoom and guides never create edits', async () => {
  const b = await workspace(), count = b.requests.length;
  assert.equal(b.get('canvasDimensions').textContent, '141.1 × 105.8 mm');
  assert.equal(b.get('canvasOrientation').textContent, '横向');
  assert.equal(b.get('emptyState').hidden, true); assert.equal(b.get('objectSelect').disabled, false);
  assert.equal(b.get('downloadPng').getAttribute('aria-disabled'), 'false');
  assert.match(b.get('downloadPdf').href, /figure-A\/export\/pdf$/);
  setZoom(b, 200); assert.equal(transform(b)[2], 2);
  click(b, 'zoomIn'); assert.equal(transform(b)[2], 2.5);
  click(b, 'zoomOut'); assert.equal(transform(b)[2], 2);
  setZoom(b, 9999); assert.equal(transform(b)[2], 8);
  setZoom(b, 'invalid'); assert.equal(transform(b)[2], 8);
  b.get('gridToggle').checked = true; b.get('gridToggle').fire('change');
  b.get('rulerToggle').checked = true; b.get('rulerToggle').fire('change');
  assert.equal(b.get('gridOverlay').hidden, false);
  assert.ok(b.get('rulerTop').children.length > 2);
  assert.equal(b.get('rulerTop').children[1].textContent, '2');
  assert.equal(b.get('undoButton').disabled, true); assert.equal(b.requests.length, count);
  assert.match(b.get('editStatus').textContent, /没有本地修改/);
});

test('fit centers the artboard, and zoom keeps the view center anchored', async () => {
  const b = await workspace();
  b.get('viewport').clientWidth = 800; b.get('viewport').clientHeight = 600;
  click(b, 'fitView');
  let [x, y, scale] = transform(b);
  assert.equal(scale, 1.3); assert.ok(Math.abs(x - (800 - 400 * 4 / 3 * scale) / 2) < 1e-9);
  assert.equal(y, 40);
  const center = [(400 - x) / scale, (300 - y) / scale];
  setZoom(b, 200); [x, y, scale] = transform(b);
  assert.ok(Math.abs((400 - x) / scale - center[0]) < 1e-9);
  assert.ok(Math.abs((300 - y) / scale - center[1]) < 1e-9);
  b.get('viewport').clientWidth = 600; click(b, 'fitView');
  assert.ok(transform(b)[2] < 1.3);
});

test('pan captures one pointer, wheel honors units, and switching projects releases capture', async () => {
  const b = await workspace({pointer: true}); setZoom(b, 100); click(b, 'panMode');
  const initial = transform(b);
  b.get('viewport').fire('pointerdown', {pointerId: 7, button: 0, clientX: 10, clientY: 20});
  assert.equal(b.get('viewport').hasPointerCapture(7), true);
  b.document.fire('pointermove', {pointerId: 8, clientX: 200, clientY: 200}); assert.deepEqual(transform(b), initial);
  b.document.fire('pointermove', {pointerId: 7, clientX: 40, clientY: 70});
  assert.deepEqual(transform(b), [initial[0] + 30, initial[1] + 50, 1]);
  b.document.fire('pointerup', {pointerId: 7}); assert.equal(b.get('viewport').hasPointerCapture(7), false);
  b.get('viewport').fire('wheel', {deltaX: 2, deltaY: 3, deltaMode: 1});
  assert.deepEqual(transform(b), [initial[0] - 2, initial[1] + 2, 1]);
  const beforeWheelZoom = transform(b);
  b.get('viewport').fire('wheel', {deltaY: -100, clientX: 90, clientY: 60, ctrlKey: true});
  const after = transform(b);
  assert.ok(after[2] > 1);
  assert.ok(Math.abs((90 - after[0]) / after[2] - (90 - beforeWheelZoom[0])) < 1e-9);
  b.get('viewport').fire('pointerdown', {pointerId: 9, button: 0, clientX: 0, clientY: 0});
  await open(b, detail('figure-B'));
  assert.equal(b.get('viewport').hasPointerCapture(9), false);
  assert.equal(b.get('selectMode').getAttribute('aria-pressed'), 'true');
  assert.equal(b.get('undoButton').disabled, true);
});

test('zoomed and translated object drag uses transformed image geometry and excludes pan edits', async () => {
  const b = await workspace({pointer: true}); setZoom(b, 200);
  b.get('previewImage').getBoundingClientRect = () => {
    const [left, top, zoom] = transform(b);
    return {left, top, width: 400 * 4 / 3 * zoom, height: 300 * 4 / 3 * zoom};
  };
  b.get('viewport').fire('wheel', {deltaX: 37, deltaY: 29});
  const initial = transform(b);
  b.get('overlay').fire('pointerdown', {target: box(b), pointerId: 4, button: 0, clientX: 100, clientY: 100});
  setZoom(b, 50); b.get('viewport').fire('wheel', {deltaX: 10, deltaY: 10});
  assert.deepEqual(transform(b), initial);
  b.document.fire('pointermove', {pointerId: 4, clientX: 180, clientY: 140});
  b.document.fire('pointerup', {pointerId: 4});
  assert.equal(value(b, 'offset_x'), '30'); assert.equal(value(b, 'offset_y'), '15');
  click(b, 'panMode');
  b.get('overlay').fire('pointerdown', {target: box(b), pointerId: 5, button: 0, clientX: 0, clientY: 0});
  b.document.fire('pointermove', {pointerId: 5, clientX: 100, clientY: 100});
  assert.equal(value(b, 'offset_x'), '30');
  click(b, 'saveButton');
  assert.deepEqual(JSON.parse(b.requests.at(-1).options.body).overrides, [{gid: 'title', prop: 'offset_px', value: [30, 15]}]);
});

test('mobile sidebars are collapsible drawers and inspector tabs support keyboard selection', async () => {
  const b = await workspace({width: 375});
  assert.equal(b.get('leftSidebar').hidden, true); assert.equal(b.get('rightSidebar').hidden, true);
  click(b, 'openProjects'); assert.equal(b.get('leftSidebar').hidden, false);
  click(b, 'toggleRight'); assert.equal(b.get('leftSidebar').hidden, true); assert.equal(b.get('rightSidebar').hidden, false);
  click(b, 'canvasTab');
  assert.equal(b.get('canvasSection').hidden, false); assert.equal(b.get('propertiesSection').hidden, true);
  b.get('canvasTab').fire('keydown', {key: 'ArrowRight'});
  assert.equal(b.get('versionsSection').hidden, false); assert.equal(b.get('versionsTab').getAttribute('aria-selected'), 'true');
  assert.equal(b.document.activeElement, b.get('versionsTab'));
  b.sandbox.innerWidth = 1440; b.windowEvents.fire('resize');
  assert.equal(b.get('leftSidebar').hidden, false); assert.equal(b.get('rightSidebar').hidden, false);
});

test('read-only figures retain navigation and exports without reopening editing', async () => {
  const b = await workspace({}, detail('figure-A', {editable: false}));
  setZoom(b, 150); click(b, 'panMode'); const initial = transform(b);
  b.get('viewport').fire('keydown', {key: 'ArrowRight'});
  assert.equal(transform(b)[0], initial[0] - 40);
  assert.equal(b.get('zoomPercent').disabled, false); assert.equal(b.get('downloadSvg').getAttribute('aria-disabled'), 'false');
  assert.equal(b.get('saveButton').disabled, true); assert.equal(b.get('undoButton').disabled, true);
});

test('object list uses real labels or Chinese roles without fabricated legend names', async () => {
  const project = detail(); project.manifest.objects[0].label = '真实研究标题';
  const b = await workspace({}, project); select(b);
  assert.equal(b.get('objectName').textContent, '真实研究标题');
  assert.match(b.get('objectSelect').innerHTML, />真实研究标题<\/option>/);
  assert.match(b.get('objectSelect').innerHTML, />数据系列<\/option>/);
  assert.doesNotMatch(b.get('propertyPanel').innerHTML, /（title）/);
  assert.doesNotMatch(b.get('projectMeta').textContent, /规格来源/);
});
