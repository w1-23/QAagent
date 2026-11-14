'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const source = readFileSync(join(__dirname, '../assets/guide.js'), 'utf8');

// Minimal DOM adapter: browser acceptance separately verifies native dialog inertness.
function realm() {
  let document;
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag; this.children = []; this.attrs = {}; this.dataset = {};
      this.listeners = {}; this.parentNode = null; this.open = false; this.disabled = false;
      this.classList = {contains: name => (this.className || '').split(/\s+/).includes(name)};
    }
    setAttribute(name, value) { this.attrs[name] = String(value); if (name === 'id') this.id = value; }
    getAttribute(name) { return this.attrs[name] ?? null; }
    hasAttribute(name) { return name in this.attrs; }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
    matches(selector) {
      const attr = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
      if (attr) return this.hasAttribute(attr[1]) && (attr[2] === undefined || this.getAttribute(attr[1]) === attr[2]);
      return selector.startsWith('#') ? this.id === selector.slice(1) : this.tagName === selector;
    }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
    contains(child) { return child === this || this.children.some(node => node.contains(child)); }
    get isConnected() { return document.body.contains(this); }
    addEventListener(name, listener) { (this.listeners[name] ||= []).push(listener); }
    fire(name, props = {}) {
      const event = {target: this, prevented: false, stopped: false,
        preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...props};
      (this.listeners[name] || []).forEach(listener => listener(event)); return event;
    }
    focus() { document.activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    set innerHTML(html) {
      this._html = html; this.children = [];
      const stack = [this];
      for (const token of html.matchAll(/<\/?[a-z][^>]*>/gi)) {
        if (token[0].startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
        const tag = token[0].match(/^<([\w-]+)/)[1], child = new Element(tag);
        for (const attr of token[0].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
          if (attr.index === 1) continue;
          child.setAttribute(attr[1], attr[2] ?? '');
        }
        stack[stack.length - 1].appendChild(child);
        if (!token[0].endsWith('/>')) stack.push(child);
      }
    }
    get innerHTML() { return this._html || ''; }
  }
  document = new Element('document');
  document.body = new Element('body'); document.documentElement = new Element('html');
  document.getElementById = id => document.body.querySelector('#' + id);
  document.createElement = tag => new Element(tag);
  const opener = document.body.appendChild(new Element('button')); opener.focus();
  const location = Object.freeze({hash: '#unchanged', href: 'https://example.test/figures?project=1#unchanged'});
  const sandbox = {document, location, console, localStorage: {getItem() { return 'light'; }}};
  sandbox.window = sandbox;
  vm.runInNewContext(source, sandbox, {filename: 'guide.js'});
  return {api: sandbox.CitrusGuide, document, opener, location, Element,
    get: id => document.getElementById(id),
    click(selector) { const dialog = document.getElementById('citrusGuideDialog'); return dialog.fire('click', {target: dialog.querySelector(selector)}); }};
}

test('six research-task chapters are reachable without links or navigation', () => {
  const b = realm(); b.api.open();
  const ids = ['research', 'files', 'data', 'figures', 'results', 'account'];
  assert.equal(b.api.pageCount, ids.length);
  assert.equal(b.get('citrusGuideDialog').open, true);
  assert.equal(b.get('citrusGuideDialog').querySelector('[data-guide-prev]').disabled, true);
  for (let index = 0; index < ids.length; index++) {
    b.click('[data-guide-page="' + index + '"]');
    assert.equal(b.get('citrusGuidePage').dataset.page, ids[index]);
    assert.equal(b.get('citrusGuideDialog').querySelectorAll('[aria-current="page"]').length, 1);
    assert.match(b.get('citrusGuideIndex').textContent, new RegExp('第 ' + (index + 1) + ' / 6 章'));
    assert.doesNotMatch(b.get('citrusGuidePage').innerHTML, /<a\b|href=|target=/);
    assert.match(b.get('citrusGuidePage').innerHTML, /<svg\b/);
  }
  assert.equal(b.location.hash, '#unchanged');
  assert.equal(b.get('citrusGuideDialog').querySelector('[data-guide-next]').textContent, '完成阅读');
  b.click('[data-guide-next]'); assert.equal(b.get('citrusGuideDialog').open, false);
});

test('related tasks share chapters and all drawing stages stay in one chapter', () => {
  const b = realm();
  const chapters = {
    research: ['research', 'sources'],
    data: ['audit', 'statistics'],
    figures: ['create', 'objects', 'text', 'series', 'save'],
  };
  for (const [chapter, sections] of Object.entries(chapters)) {
    b.api.open(chapter);
    const article = b.get('citrusGuidePage');
    assert.deepEqual(article.querySelectorAll('[data-guide-section]').map(section => section.getAttribute('data-guide-section')), sections);
    assert.equal(article.querySelectorAll('svg').length, sections.length);
    for (const section of sections) {
      const block = article.querySelector('[data-guide-section="' + section + '"]');
      assert.equal(block.getAttribute('aria-labelledby'), 'cg-section-' + section);
      assert.ok(block.querySelector('h3'));
      assert.equal(block.querySelectorAll('h4').length, 3);
    }
  }
  const drawing = b.get('citrusGuidePage').innerHTML;
  for (const feature of ['绘图脚本', '隔离运行环境', '共同支持的样式', '纯文本', '2,000', 'Shift + 方向键', '透明度', '图例', '最近保存', 'SVG、PNG 或 PDF', '网格和标尺']) {
    assert.ok(drawing.includes(feature), 'drawing coverage: ' + feature);
  }
  assert.equal(b.get('citrusGuideDialog').querySelectorAll('[data-guide-page]').length, 6);
});

test('legacy topic openers resolve to their containing chapters without navigation', () => {
  const b = realm();
  const aliases = {start: 'research', sources: 'research', audit: 'data', statistics: 'data', edit: 'figures', objects: 'figures', text: 'figures', series: 'figures', save: 'figures', keys: 'figures'};
  for (const [topic, chapter] of Object.entries(aliases)) {
    b.api.open(topic);
    assert.equal(b.get('citrusGuidePage').dataset.page, chapter);
  }
  b.api.open('unknown'); assert.equal(b.get('citrusGuidePage').dataset.page, 'research');
  b.api.open(4); assert.equal(b.get('citrusGuidePage').dataset.page, 'figures');
  assert.equal(b.location.hash, '#unchanged');
});

test('closing after the opener becomes hidden focuses its visible fallback', () => {
  const b = realm(), fallback = b.document.body.appendChild(new b.Element('button'));
  fallback.setAttribute('id', 'panelToggle');
  b.document.querySelector = selector => b.document.body.querySelector(selector);
  b.opener.setAttribute('data-guide-focus-fallback', '#panelToggle');
  b.api.open();
  b.opener.focus = () => {}; // A hidden native button cannot receive focus.
  b.api.close();
  assert.equal(b.document.activeElement, fallback);
});

test('prev/next, direct figure opener and arrows update only the current guide page', () => {
  const b = realm(), button = b.document.body.appendChild(new b.Element('button'));
  button.setAttribute('data-citrus-guide', 'figures'); button.focus();
  const event = b.document.fire('click', {target: button});
  assert.equal(event.prevented, true);
  assert.equal(b.get('citrusGuidePage').dataset.page, 'figures');
  b.click('[data-guide-next]'); assert.equal(b.get('citrusGuidePage').dataset.page, 'results');
  b.click('[data-guide-prev]'); assert.equal(b.get('citrusGuidePage').dataset.page, 'figures');
  b.get('citrusGuideDialog').fire('keydown', {key: 'ArrowRight'});
  assert.equal(b.get('citrusGuidePage').dataset.page, 'results');
  b.api.close(); assert.equal(b.document.activeElement, button);
});

test('keyboard focus wraps, Escape restores opener and close callback fires once', () => {
  const b = realm(); let completed = 0;
  b.api.open('text', {onClose() { completed++; }});
  const dialog = b.get('citrusGuideDialog');
  const close = dialog.querySelector('[data-guide-close]'), next = dialog.querySelector('[data-guide-next]');
  assert.equal(b.document.activeElement, close);
  assert.equal(dialog.fire('keydown', {key: 'Tab', shiftKey: true}).prevented, true);
  assert.equal(b.document.activeElement, next);
  dialog.fire('keydown', {key: 'Tab'}); assert.equal(b.document.activeElement, close);
  b.click('[data-guide-next]'); assert.equal(b.document.activeElement, b.get('citrusGuidePage'));
  dialog.fire('keydown', {key: 'Tab', shiftKey: true}); assert.equal(b.document.activeElement, next);
  dialog.fire('keydown', {key: 'Escape'});
  assert.equal(dialog.open, false); assert.equal(b.document.activeElement, b.opener); assert.equal(completed, 1);
  b.api.close(); assert.equal(completed, 1);
});

test('auth reset closes without completing onboarding; native cancel closes normally', () => {
  const b = realm(); let completed = 0;
  b.api.open(1, {onClose() { completed++; }}); b.api.close(false);
  assert.equal(completed, 0);
  b.api.open(2, {onClose() { completed++; }});
  assert.equal(b.get('citrusGuideDialog').fire('cancel').prevented, true);
  assert.equal(completed, 1); assert.equal(b.document.activeElement, b.opener);
});

test('chat includes shared guide before app and no obsolete onboarding or outbound guide link', () => {
  const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
  const app = readFileSync(join(__dirname, '../assets/app.js'), 'utf8');
  assert.ok(html.indexOf('/assets/guide.js') < html.indexOf('/assets/app.js'));
  assert.match(html, /id="guideRailBtn" onclick="openOnboardingGuide\(\)"/);
  assert.doesNotMatch(html, /onboardingOverlay|href="\/assets\/guide.html/);
  assert.doesNotMatch(app, /ONB_TOTAL|onbRender|#guide=/);
  assert.match(app, /CitrusGuide\.open\(startPage \|\| 'research', \{onClose: completeOnboarding\}\)/);
  assert.match(app, /currentAccount\.onboarding_required/);
});
