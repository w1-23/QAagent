'use strict';
// Focused offline regressions for the U3 library item actions and the bounded
// browser-side table previews. Loads the production script with a tiny DOM and
// no network, vendor code or credentials.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');

const flush = async () => { for (let i = 0; i < 120; i++) await Promise.resolve(); };
const VOID = new Set(['img', 'input', 'br', 'hr', 'meta', 'link', 'source', 'area',
  'base', 'col', 'embed', 'param', 'track', 'wbr']);

function decodeEntities(value) {
  return String(value)
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&amp;/g, '&');
}

class Element {
  constructor(tag = 'div', doc = null) {
    this.tagName = String(tag).toUpperCase();
    this.doc = doc;
    this.children = [];
    this.parentNode = null;
    this.attrs = {};
    this.dataset = {};
    this.listeners = {};
    this.className = '';
    this._text = '';
    this._html = '';
    this._hidden = false;
    this.title = '';
    this.href = '';
    this.tabIndex = -1;
    this.style = {
      setProperty(k, v) { this[k] = v; },
      getPropertyValue(k) { return this[k] || ''; },
      removeProperty(k) { delete this[k]; }
    };
    this.classList = {
      contains: name => this.className.split(/\s+/).filter(Boolean).includes(name),
      add: (...names) => {
        this.className = [...new Set(this.className.split(/\s+/).filter(Boolean).concat(names))]
          .join(' ').trim();
      },
      remove: (...names) => {
        this.className = this.className.split(/\s+/).filter(n => n && !names.includes(n)).join(' ');
      },
      toggle: (name, on) => {
        const want = on === undefined ? !this.classList.contains(name) : !!on;
        this.classList[want ? 'add' : 'remove'](name);
        return want;
      }
    };
  }
  get parentElement() { return this.parentNode; }
  get hidden() { return this._hidden; }
  set hidden(value) {
    this._hidden = !!value;
    if (this._hidden) this.attrs.hidden = '';
    else delete this.attrs.hidden;
  }
  appendChild(node) {
    if (node.parentNode) node.remove();
    this.children.push(node);
    node.parentNode = this;
    return node;
  }
  append(node) { return this.appendChild(node); }
  insertBefore(node, before) {
    if (!before) return this.appendChild(node);
    node.remove();
    const index = this.children.indexOf(before);
    this.children.splice(index < 0 ? this.children.length : index, 0, node);
    node.parentNode = this;
    return node;
  }
  removeChild(node) {
    this.children = this.children.filter(n => n !== node);
    node.parentNode = null;
    return node;
  }
  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }
  replaceChildren(...nodes) {
    this.children.forEach(n => { n.parentNode = null; });
    this.children = [];
    this._text = '';
    nodes.forEach(n => this.appendChild(n));
  }
  set textContent(text) {
    this.replaceChildren();
    this._text = String(text);
    this._html = '';
  }
  get textContent() {
    return this._text + this.children.map(n => n.textContent).join('');
  }
  set innerHTML(html) {
    this.replaceChildren();
    this._html = String(html);
    const tokens = String(html).match(/<[^>]*>|[^<]+/g) || [];
    const stack = [this];
    for (const token of tokens) {
      if (token.startsWith('</')) {
        if (stack.length > 1) stack.pop();
        continue;
      }
      if (!token.startsWith('<')) {
        stack[stack.length - 1]._text += decodeEntities(token);
        continue;
      }
      const tag = token.match(/^<([a-zA-Z][\w-]*)([\s\S]*?)\/?>$/);
      if (!tag) continue;
      const node = new Element(tag[1], this.doc);
      const attrText = tag[2] || '';
      const attrRe = /([\w:-]+)(?:\s*=\s*"([^"]*)")?/g;
      let attr;
      while ((attr = attrRe.exec(attrText))) {
        if (!attr[1] || attr[1] === '/') continue;
        node.setAttribute(attr[1], attr[2] === undefined ? '' : decodeEntities(attr[2]));
      }
      stack[stack.length - 1].appendChild(node);
      if (!VOID.has(tag[1].toLowerCase())) stack.push(node);
    }
  }
  get innerHTML() { return this._html; }
  get firstChild() { return this.children[0] || null; }
  get childElementCount() { return this.children.length; }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === 'class') this.className = String(value);
    if (name === 'hidden') this._hidden = true;
    if (name === 'title') this.title = String(value);
    if (name.startsWith('data-')) {
      this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
    }
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }
  removeAttribute(name) { delete this.attrs[name]; if (name === 'hidden') this._hidden = false; }
  matches(selector) {
    if (selector.startsWith('.')) {
      return selector.slice(1).split('.').every(c => this.classList.contains(c));
    }
    if (selector.startsWith('[')) {
      const m = selector.match(/^\[([\w:-]+)(?:="([^"]*)")?\]$/);
      if (!m) return false;
      const value = this.getAttribute(m[1]);
      return m[2] === undefined ? value !== null : String(value) === m[2];
    }
    return this.tagName.toLowerCase() === selector.toLowerCase();
  }
  querySelectorAll(selector) {
    return this.children.flatMap(n =>
      (n.matches(selector) ? [n] : []).concat(n.querySelectorAll(selector)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentNode ? this.parentNode.closest(selector) : null; }
  contains(node) { return node === this || this.children.some(n => n.contains(node)); }
  addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); }
  click() {
    const event = {target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; },
      stopPropagation() {}};
    (this.listeners.click || []).forEach(fn => fn(event));
    return event;
  }
  focus() { if (this.doc) this.doc.activeElement = this; }
  blur() { if (this.doc && this.doc.activeElement === this) this.doc.activeElement = null; }
  scrollIntoView() {}
  getBoundingClientRect() { return {left: 0, top: 0, right: 120, bottom: 40, width: 120, height: 40}; }
}

function storage() {
  const map = new Map();
  return {
    getItem: key => map.get(key) === undefined ? null : map.get(key),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: key => map.delete(key)
  };
}

function makeBrowser() {
  const nodes = new Map();
  const document = {
    readyState: 'loading',
    activeElement: null,
    body: null,
    documentElement: null,
    getElementById(id) {
      if (!nodes.has(id)) {
        const node = new Element('div', document);
        node.attrs.id = id;
        document.body.appendChild(node);
        nodes.set(id, node);
      }
      return nodes.get(id);
    },
    createElement(tag) { return new Element(tag, document); },
    querySelector(selector) { return document.body.querySelector(selector); },
    querySelectorAll(selector) { return document.body.querySelectorAll(selector); },
    contains(node) { return node === document.body || document.body.contains(node); },
    addEventListener() {}
  };
  document.body = new Element('body', document);
  document.documentElement = new Element('html', document);
  document.body.appendChild(document.documentElement);

  const context = {
    console, document, TextDecoder, TextEncoder, AbortController, DOMException,
    navigator: {}, sessionStorage: storage(), localStorage: storage(),
    setTimeout: () => 0, clearTimeout() {}, requestAnimationFrame: () => 0,
    addEventListener() {}, confirm: () => true, alert() {},
    URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}},
    showBanner(message) { context.banners.push(message); },
    banners: []
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(readFileSync(join(__dirname, '../assets/session_files.js'), 'utf8'),
    context, {filename: 'session_files.js'});

  const api = {
    context, document,
    get(id) { return document.getElementById(id); },
    fetchHandler: null,
    requests: []
  };
  context.fetch = (url, options) => {
    api.requests.push({url, options: options || {}});
    return Promise.resolve().then(() => api.fetchHandler(url, options));
  };
  return api;
}

function fileRecord(overrides) {
  const base = {
    file_id: 'a'.repeat(32),
    session_id: 'session-a',
    filename: 'raw data.csv',
    origin: 'uploaded',
    mime_type: 'text/csv',
    size_bytes: 2048,
    kind: 'table',
    visibility: 'public',
    created_at: 1700000000,
    last_accessed_at: 1700050000,
    turn_request_id: '',
    source_session_id: 'session-a',
    download_url: '/api/v2/files/' + 'a'.repeat(32) + '/download',
    preview_url: '/api/v2/files/' + 'a'.repeat(32) + '/preview'
  };
  return Object.assign(base, overrides || {});
}

function jsonResponse(payload, status = 200) {
  return {
    ok: status < 400,
    status,
    headers: {get: name => name === 'content-length' ? JSON.stringify(payload).length : null},
    json: async () => payload,
    text: async () => JSON.stringify(payload)
  };
}

function bytesResponse(buffer, headers = {}) {
  return {
    ok: true,
    status: 200,
    headers: {get: name => headers[name] === undefined ? null : String(headers[name])},
    arrayBuffer: async () => buffer
  };
}

function bufferOf(text) {
  return new TextEncoder().encode(text).buffer;
}

async function openTablePreview(browser, file, buffer, headers) {
  browser.fetchHandler = (url) => {
    if (url === '/api/v2/files') {
      return jsonResponse({count: 1, uploaded: [file], generated: [], retention_idle_days: 30});
    }
    assert.equal(url, file.download_url);
    return bytesResponse(buffer, headers || {});
  };
  await browser.context.loadSessionFiles();
  browser.context.openAccountFilePreview(file.file_id);
  await flush();
  return browser.get('sessionFilePreviewStage').querySelector('.sf-preview-table');
}

test('library item exposes explicit Preview, a Reference pill and a secondary menu', async () => {
  const b = makeBrowser();
  const uploaded = fileRecord({});
  const generated = fileRecord({
    file_id: 'b'.repeat(32), filename: 'report.pdf', origin: 'generated', kind: 'pdf',
    mime_type: 'application/pdf', download_url: '/api/v2/files/' + 'b'.repeat(32) + '/download',
    turn_request_id: 'crid-1', source_session_id: 'session-a'
  });
  const payload = {
    count: 2, session_id: '', scope: 'account', retention_idle_days: 14,
    uploaded: [uploaded], generated: [generated]
  };
  b.fetchHandler = () => jsonResponse(payload);

  const before = JSON.stringify(payload);
  const returned = await b.context.loadSessionFiles();
  assert.deepEqual(returned, payload, 'loadSessionFiles return shape is unchanged');
  assert.equal(JSON.stringify(returned), before, 'rendering must not mutate the server payload');

  const uploadedList = b.get('sessionFilesUploaded');
  assert.equal(uploadedList.querySelector('[data-file-act="preview"]').textContent, '预览');
  assert.equal(uploadedList.querySelector('.session-file-ref-pill').textContent, '＋ 引用');
  assert.ok(uploadedList.querySelector('[aria-haspopup="menu"]'));
  assert.equal(uploadedList.querySelector('[data-file-act="info"]').getAttribute('role'), 'menuitem');
  assert.ok(uploadedList.querySelector('[data-file-act="delete"]'));
  assert.match(uploadedList.textContent, /最近访问/);

  const generatedList = b.get('sessionFilesGenerated');
  assert.equal(generatedList.querySelector('[data-file-act="delete"]'), null);
  assert.doesNotMatch(generatedList.textContent, /删除/);
  assert.ok(generatedList.querySelector('[data-file-act="jump"]'));

  const retention = b.get('sessionFilesRetention').textContent;
  assert.match(retention, /连续 14 天/);
  assert.match(retention, /浏览资料库不会续期/);
  assert.match(retention, /只刷新所用文件的保留期限/);
  assert.doesNotMatch(retention, /保留 30 天/);

  await b.context.showSessionFileInfo(uploaded.file_id);
  const info = b.get('sessionFilePreviewStage').innerHTML;
  assert.match(info, /最近访问/);
  assert.match(info, /连续 14 天未使用时自动清理/);
});

test('the reference pill queues the existing attachment reference without upload', async () => {
  const b = makeBrowser();
  const uploaded = fileRecord({});
  b.fetchHandler = () => jsonResponse({retention_idle_days: 30, uploaded: [uploaded], generated: []});
  await b.context.loadSessionFiles();
  b.context.queueAccountFileReference(uploaded.file_id);
  assert.equal(b.context._selectedAccountFileRefs.length, 1);
  assert.equal(b.context._selectedAccountFileRefs[0].file_id, uploaded.file_id);
  assert.deepEqual(b.context.banners, ['已添加到当前问题。']);
});

test('CSV parsing keeps quoted commas, doubled quotes and embedded newlines in one cell', async () => {
  const b = makeBrowser();
  const file = fileRecord({file_id: 'c'.repeat(32), filename: 'quoted.csv',
    download_url: '/api/v2/files/' + 'c'.repeat(32) + '/download'});
  const csv = 'name,note\r\n"Zhang, San","line one\nline ""two"""\r\nplain,"a,b"';
  const node = await openTablePreview(b, file, bufferOf(csv));

  const headers = node.querySelectorAll('th').map(n => n.textContent);
  assert.deepEqual(headers, ['name', 'note']);
  const cells = node.querySelectorAll('td').map(n => n.textContent);
  assert.deepEqual(cells, ['Zhang, San', 'line one\nline "two"', 'plain', 'a,b']);
  assert.equal(node.querySelectorAll('tr').length, 3);
});

test('CSV preview renders client-side batches and never claims server paging', async () => {
  const b = makeBrowser();
  const file = fileRecord({file_id: 'd'.repeat(32), filename: 'many.csv',
    download_url: '/api/v2/files/' + 'd'.repeat(32) + '/download'});
  const rows = ['value'];
  for (let i = 0; i < 250; i++) rows.push('v' + i);
  const node = await openTablePreview(b, file, bufferOf(rows.join('\n')));

  assert.equal(node.querySelectorAll('tr').length, 100);
  let status = node.querySelector('.sf-table-status').textContent;
  assert.match(status, /已渲染 100 \/ 251 行/);
  let more = node.querySelector('.sf-load-more');
  assert.ok(more);
  assert.equal(more.hidden, false);
  assert.equal(more.textContent, '再显示 100 行');

  more.click();
  await flush();
  assert.equal(node.querySelectorAll('tr').length, 200);
  assert.match(node.querySelector('.sf-table-status').textContent, /200 \/ 251/);

  more = node.querySelector('.sf-load-more');
  more.click();
  await flush();
  assert.equal(node.querySelectorAll('tr').length, 251);
  status = node.querySelector('.sf-table-status').textContent;
  assert.match(status, /已渲染全部 251 行/);
  assert.equal(node.querySelector('.sf-load-more').hidden, true);
  assert.doesNotMatch(node.textContent, /服务器分页/);
});

test('declared over-cap CSV is not parsed at all and offers the original download', async () => {
  const b = makeBrowser();
  const file = fileRecord({file_id: 'e'.repeat(32), filename: 'huge.csv',
    download_url: '/api/v2/files/' + 'e'.repeat(32) + '/download'});
  b.fetchHandler = (url) => {
    if (url === '/api/v2/files') return jsonResponse({retention_idle_days: 30, uploaded: [file], generated: []});
    return bytesResponse(bufferOf('a,b\n1,2'), {'content-length': 9 * 1024 * 1024});
  };
  await b.context.loadSessionFiles();
  b.context.openAccountFilePreview(file.file_id);
  await flush();
  const node = b.get('sessionFilePreviewStage').querySelector('.sf-preview-table');
  assert.match(node.textContent, /解析上限/);
  assert.match(node.textContent, /未在页面内解析/);
  assert.match(node.innerHTML, /下载原件/);
  assert.equal(node.querySelector('table'), null, 'no partial table is shown');
});

test('streamed CSV is cancelled as soon as the byte cap is crossed', async () => {
  const b = makeBrowser();
  const file = fileRecord({file_id: 'f'.repeat(32), filename: 'stream.csv',
    download_url: '/api/v2/files/' + 'f'.repeat(32) + '/download'});
  let cancelled = false;
  let reads = 0;
  const chunk = new Uint8Array(5 * 1024 * 1024);
  const body = {
    getReader() {
      return {
        read() {
          reads++;
          return Promise.resolve({done: false, value: chunk});
        },
        cancel() { cancelled = true; }
      };
    }
  };
  b.fetchHandler = (url) => {
    if (url === '/api/v2/files') return jsonResponse({retention_idle_days: 30, uploaded: [file], generated: []});
    return {ok: true, status: 200, headers: {get: () => null}, body,
      arrayBuffer: async () => { throw new Error('arrayBuffer fallback must not run'); }};
  };
  await b.context.loadSessionFiles();
  b.context.openAccountFilePreview(file.file_id);
  await flush();
  const node = b.get('sessionFilePreviewStage').querySelector('.sf-preview-table');
  assert.equal(cancelled, true);
  assert.equal(reads, 2);
  assert.match(node.textContent, /解析上限/);
  assert.equal(node.querySelector('table'), null);
});

test('XLSX parsing is bounded with sheetRows and its DOM is paged client-side', async () => {
  const b = makeBrowser();
  const file = fileRecord({file_id: '1'.repeat(32), filename: 'sheet.xlsx',
    download_url: '/api/v2/files/' + '1'.repeat(32) + '/download'});
  let readOptions = null;
  b.context.XLSX = {
    read(buffer, options) {
      readOptions = options;
      return {SheetNames: ['S1'], Sheets: {S1: {'!ref': 'A1:B250'}}};
    },
    utils: {
      decode_range(ref) {
        const end = Number(String(ref).split(':')[1].replace(/[A-Z]+/g, ''));
        return {s: {r: 0, c: 0}, e: {r: end - 1, c: 1}};
      },
      encode_cell(cell) { return String.fromCharCode(65 + cell.c) + (cell.r + 1); }
    }
  };
  const node = await openTablePreview(b, file, new ArrayBuffer(16));
  assert.equal(readOptions.sheetRows, 2000);

  let body = node.querySelector('.sf-sheet-body');
  assert.equal(body.querySelectorAll('tr').length, 100);
  assert.match(body.querySelector('.sf-table-status').textContent, /已渲染 100 \/ 250 行/);
  body.querySelector('.sf-load-more').click();
  await flush();
  body = node.querySelector('.sf-sheet-body');
  assert.equal(body.querySelectorAll('tr').length, 200);
  body.querySelector('.sf-load-more').click();
  await flush();
  body = node.querySelector('.sf-sheet-body');
  assert.equal(body.querySelectorAll('tr').length, 250);
  assert.match(body.querySelector('.sf-table-status').textContent, /已渲染全部 250 行/);
  assert.equal(body.querySelector('.sf-load-more'), null);
});

test('XLSX at the sheetRows cap is described as bounded, never as complete', async () => {
  const b = makeBrowser();
  const file = fileRecord({file_id: '2'.repeat(32), filename: 'long.xlsx',
    download_url: '/api/v2/files/' + '2'.repeat(32) + '/download'});
  b.context.XLSX = {
    read() { return {SheetNames: ['S1'], Sheets: {S1: {'!ref': 'A1:A2000'}}}; },
    utils: {
      decode_range() { return {s: {r: 0, c: 0}, e: {r: 1999, c: 0}}; },
      encode_cell(cell) { return 'A' + (cell.r + 1); }
    }
  };
  const node = await openTablePreview(b, file, new ArrayBuffer(16));
  const body = node.querySelector('.sf-sheet-body');
  const status = body.querySelector('.sf-table-status').textContent;
  assert.match(status, /已达解析上限 2000 行/);
  assert.match(status, /超出部分请下载原件/);
  assert.match(status, /已渲染 100 \/ 2000 行/);
  assert.doesNotMatch(status, /已渲染全部/);
});
