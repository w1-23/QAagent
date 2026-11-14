'use strict';
// Execute production UI code with DOM/network doubles; no server or model.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const ID = 'a'.repeat(32), PEER = 'b'.repeat(32), SHA = 'c'.repeat(64);
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.attrs = {}; this.dataset = {};
    this.hidden = false; this.disabled = false; this.style = {}; this._text = ''; this._html = '';
    this.classList = {add(){}, remove(){}, toggle(){}};
  }
  set textContent(value) { this._text = String(value); this.children = []; this._html = ''; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set innerHTML(value) { this._html = String(value); this.children = []; }
  get innerHTML() { return this._html; }
  setAttribute(key, value) { this.attrs[key] = value; }
  getAttribute(key) { return this.attrs[key]; }
  appendChild(child) { this.children.push(child); child.parentElement = this; }
  removeChild(child) { this.children = this.children.filter(c => c !== child); }
  querySelector(selector) { return this.children.find(c => '.' + c.className === selector) || null; }
  focus() {}
}
function browser() {
  const nodes = new Map(), requests = [], downloads = [], notices = [], cards = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const close = new Element('button'); close.className = 'overlay-close';
  get('sessionFilePreviewOverlay').appendChild(close);
  get('sessionFilePreviewOverlay').hidden = true;
  const body = new Element('body');
  const document = {
    readyState: 'loading', body, activeElement: new Element('button'), getElementById: get,
    addEventListener(){}, contains: () => true,
    createElement(tag) { const e = new Element(tag); e.click = () => { if (tag === 'a') downloads.push(e.href); }; return e; },
    querySelectorAll(selector) { return selector === '.turn-file-card[data-file-id]' ? cards : []; }
  };
  const sandbox = {document, console, navigator: {}, encodeURIComponent,
    fetch: (path, options) => new Promise(resolve => requests.push({path, options, resolve})),
    showBanner: message => notices.push(message)};
  sandbox.window = sandbox;
  vm.runInContext(readFileSync(join(__dirname, '../assets/session_files.js'), 'utf8'), vm.createContext(sandbox));
  return {window: sandbox, get, requests, downloads, notices, cards,
    reply(req, data, status = 200) { req.resolve({ok: status < 400, status, json: async () => data}); }};
}
function options(fileId = ID) {
  return {file_id: fileId, filename: 'synthetic.svg', source_sha256: SHA, note: 'Only saved versions', options: [
    {target_format: 'svg', file_id: fileId, filename: 'synthetic.svg', mode: 'existing'},
    {target_format: 'png', file_id: PEER, filename: 'synthetic.png', sha256: SHA, mode: 'existing'}
  ]};
}
function buttons(b) { return b.get('sessionFilePreviewStage').children.filter(c => c.tagName === 'button'); }

test('only saved formats appear; selecting downloads pinned existing bytes without a chat request', async () => {
  const b = browser();
  const opened = b.window.openAccountFileExport(ID);
  assert.equal(b.requests[0].path, '/api/v2/files/' + ID + '/export-options');
  const data = options();
  data.options.push({target_format: 'pdf', file_id: PEER, mode: 'conversion'});
  b.reply(b.requests[0], data); await opened;
  assert.deepEqual(buttons(b).map(x => x.textContent), ['SVG（原件）', 'PNG（已生成）']);
  const clicked = buttons(b)[1].onclick();
  const post = b.requests[1];
  assert.equal(post.options.method, 'POST');
  assert.deepEqual(JSON.parse(post.options.body), {target_format: 'png', source_sha256: SHA, target_file_id: PEER, target_sha256: SHA});
  b.reply(post, {status: 'complete', file_id: PEER, source_sha256: SHA}); await clicked;
  assert.deepEqual(b.downloads, ['/api/v2/files/' + PEER + '/download']);
  assert.ok(b.requests.every(r => r.path.startsWith('/api/v2/files/')));
});

test('failed or changed export does not start a download', async () => {
  const b = browser(); const opened = b.window.openAccountFileExport(ID);
  b.reply(b.requests[0], options()); await opened;
  const clicked = buttons(b)[0].onclick();
  b.reply(b.requests[1], {detail: {message: 'Source changed'}}, 409); await clicked;
  assert.equal(b.downloads.length, 0); assert.equal(b.notices.at(-1), 'Source changed');
  assert.equal(buttons(b)[0].disabled, false);
  const second = buttons(b)[0].onclick();
  b.reply(b.requests[2], {status: 'complete', file_id: PEER, source_sha256: SHA}); await second;
  assert.equal(b.downloads.length, 0); assert.match(b.notices.at(-1), /版本已改变/);
});

test('late option responses and closed-dialog exports cannot alter current UI or download', async () => {
  const b = browser(); const old = b.window.openAccountFileExport(ID);
  const current = b.window.openAccountFileExport(PEER);
  b.reply(b.requests[1], options(PEER)); await current;
  b.reply(b.requests[0], {detail: {message: 'Old failure'}}, 409); await old;
  assert.equal(b.get('sessionFilePreviewTitle').textContent, '导出 · synthetic.svg');
  assert.equal(buttons(b).length, 2);
  const selected = buttons(b)[0].onclick();
  b.window.closeSessionFilePreview();
  b.reply(b.requests[2], {status: 'complete', file_id: PEER, source_sha256: SHA}); await selected;
  assert.equal(b.downloads.length, 0);
  assert.equal(b.get('sessionFilePreviewOverlay').hidden, true);
});

test('library and chat generated-file cards share export action; internal entries stay hidden', async () => {
  const b = browser();
  const card = new Element(); card.dataset.fileId = ID;
  const name = new Element(); name.className = 'turn-file-name'; name.textContent = 'synthetic.svg'; card.appendChild(name);
  b.cards.push(card);
  const loaded = b.window.loadSessionFiles();
  b.reply(b.requests[0], {uploaded: [], generated: [
    {file_id: ID, filename: 'synthetic.svg', origin: 'generated', visibility: 'public', download_url: '/api/v2/files/' + ID + '/download'},
    {file_id: PEER, filename: 'private-snapshot.json', origin: 'generated', visibility: 'internal'}
  ]}); await loaded; await flush();
  const html = b.get('sessionFilesGenerated').innerHTML;
  assert.match(html, /data-file-act="export"/);
  assert.doesNotMatch(html, /private-snapshot/);
  const extra = card.querySelector('.turn-file-extra');
  assert.ok(extra.children.some(c => c.getAttribute('data-file-act') === 'export'));
  assert.equal(extra.children.filter(c => c.getAttribute('data-file-act') === 'download').length, 0);
});

test('original-only options do not advertise converters or execute document content', async () => {
  const b = browser(); const opened = b.window.openAccountFileExport(ID);
  const data = options(); data.filename = '<script>bad()</script>.xlsx';
  data.options = [{target_format: 'xlsx', file_id: ID, filename: data.filename, mode: 'existing'}];
  b.reply(b.requests[0], data); await opened;
  assert.equal(buttons(b).length, 1); assert.equal(buttons(b)[0].textContent, 'XLSX（原件）');
  assert.equal(b.get('sessionFilePreviewTitle').textContent, '导出 · <script>bad()</script>.xlsx');
  assert.equal(b.get('sessionFilePreviewStage').innerHTML, '');
});
