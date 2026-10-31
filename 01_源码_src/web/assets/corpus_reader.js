/* ─── R15: 本地语料原文阅读器 ─────────────────────────────────────────────
 * 回答里出现的本地来源标识（paper_… / doc_…）点击后，直接打开整篇原文阅读。
 * 原文来自本地语料（chunks.jsonl 按 chunk_index 还原），由只读接口
 * GET /api/v2/corpus/source?record=<键> 提供；前端只传键，从不传路径。
 * DOI 有就跳转、没有也照常打开——DOI 不是打开原文的前置条件。
 * ---------------------------------------------------------------------- */
(function () {
  'use strict';

  var MASK_ID = 'corpusReaderMask';
  var state = { key: '', data: null, view: 'rendered', loading: false };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function humanNum(n) {
    var v = Number(n) || 0;
    return v.toLocaleString('en-US');
  }

  /* 把纯文本里的 DOI 变成可点链接；已经是链接的段落不重复处理 */
  function linkifyDoi(text) {
    return String(text || '').replace(
      /(^|[\s（(【，,;；:：])(10\.\d{4,9}\/[^\s<>"'）)】\]]+)/g,
      function (m, lead, doi) {
        var clean = doi.replace(/[.,;]+$/, '');
        return lead + '<a class="cr-doi" href="https://doi.org/' + esc(clean) +
          '" target="_blank" rel="noopener noreferrer">' + esc(clean) + '</a>';
      }
    );
  }

  function ensureMask() {
    var mask = document.getElementById(MASK_ID);
    if (mask) return mask;
    mask = document.createElement('div');
    mask.id = MASK_ID;
    mask.className = 'corpus-reader-mask';
    mask.setAttribute('hidden', '');
    mask.innerHTML =
      '<div class="corpus-reader" role="dialog" aria-modal="true" aria-label="本地原文阅读">' +
        '<header class="cr-head">' +
          '<div class="cr-head-main">' +
            '<span class="cr-kind" id="crKind">本地文献</span>' +
            '<h3 class="cr-title" id="crTitle">正在读取…</h3>' +
            '<div class="cr-meta" id="crMeta"></div>' +
          '</div>' +
          '<div class="cr-actions">' +
            '<button type="button" class="cr-btn" id="crViewBtn" title="切换渲染/原始 Markdown">原始 Markdown</button>' +
            '<button type="button" class="cr-btn" id="crCopyBtn" title="复制原文">复制原文</button>' +
            '<button type="button" class="cr-close" id="crCloseBtn" aria-label="关闭">×</button>' +
          '</div>' +
        '</header>' +
        '<div class="cr-body" id="crBody" tabindex="0"></div>' +
        '<footer class="cr-foot" id="crFoot"></footer>' +
      '</div>';
    document.body.appendChild(mask);

    mask.addEventListener('click', function (ev) {
      if (ev.target === mask) closeReader();
    });
    mask.querySelector('#crCloseBtn').addEventListener('click', closeReader);
    mask.querySelector('#crViewBtn').addEventListener('click', function () {
      state.view = state.view === 'rendered' ? 'raw' : 'rendered';
      paint();
    });
    mask.querySelector('#crCopyBtn').addEventListener('click', copySource);
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && !mask.hasAttribute('hidden')) closeReader();
    });
    return mask;
  }

  function setStatus(text, kind) {
    var body = document.getElementById('crBody');
    if (!body) return;
    body.innerHTML = '<div class="cr-note cr-note-' + (kind || 'info') + '">' + esc(text) + '</div>';
  }

  function closeReader() {
    var mask = document.getElementById(MASK_ID);
    if (mask) mask.setAttribute('hidden', '');
    document.body.classList.remove('corpus-reader-open');
  }

  function isOpen() {
    var mask = document.getElementById(MASK_ID);
    return !!mask && !mask.hasAttribute('hidden');
  }

  function paint() {
    var d = state.data;
    if (!d) return;
    var titleEl = document.getElementById('crTitle');
    var metaEl = document.getElementById('crMeta');
    var kindEl = document.getElementById('crKind');
    var body = document.getElementById('crBody');
    var foot = document.getElementById('crFoot');
    var viewBtn = document.getElementById('crViewBtn');

    titleEl.textContent = d.title_label || d.record_key || '本地原文';
    titleEl.title = d.title_label || '';
    kindEl.textContent = d.source_type === 'methods' ? '实验方法'
      : (d.source_type === 'ucr' ? '品种记录'
      : (d.source_type === 'deng' ? '专著' : '本地文献'));

    var bits = [];
    if (d.journal) bits.push(esc(d.journal));
    if (d.year) bits.push(esc(d.year) + ' 年');
    bits.push(humanNum(d.chunks) + ' 段');
    bits.push(humanNum(d.chars) + ' 字');
    if (d.has_doi && d.doi) {
      bits.push('<a class="cr-doi" href="https://doi.org/' + esc(d.doi) +
        '" target="_blank" rel="noopener noreferrer">DOI ' + esc(d.doi) + '</a>');
    } else {
      bits.push('<span class="cr-nodoi">原文未含 DOI</span>');
    }
    metaEl.innerHTML = bits.join(' · ');

    if (state.view === 'raw') {
      body.innerHTML = '<pre class="cr-raw">' + esc(d.markdown || '') + '</pre>';
      viewBtn.textContent = '渲染视图';
    } else {
      var html = '';
      try {
        html = (typeof window.renderMarkdownSafe === 'function')
          ? window.renderMarkdownSafe(d.markdown || '')
          : '<pre class="cr-raw">' + esc(d.markdown || '') + '</pre>';
      } catch (e) {
        html = '<pre class="cr-raw">' + esc(d.markdown || '') + '</pre>';
      }
      body.innerHTML = linkifyDoi(html);
      viewBtn.textContent = '原始 Markdown';
    }
    body.scrollTop = 0;
    if (state.view !== 'raw' && state.focus) markFocus(state.focus);
    foot.textContent = '来源：本地语料 ' + (d.corpus || '') +
      '（按分块顺序还原全文，共 ' + humanNum(d.chunks) + ' 段；只读，不修改原始语料）';
  }

  // 命中片段高亮：把工具结果里的证据片段在正文中定位出来。
  // 跨行/多空格用 \s+ 宽松匹配；找不到就静默返回（预览本身不受影响）。
  function markFocus(focus) {
    var body = document.getElementById('crBody');
    var needle = String(focus || '').replace(/\s+/g, ' ').trim();
    if (!body || needle.length < 16) return false;
    var words = needle.slice(0, 90).split(' ').filter(function (w) { return w; });
    if (!words.length) return false;
    var pattern = words.map(function (w) {
      return w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }).join('\\s+');
    var re;
    try { re = new RegExp(pattern, 'i'); } catch (e) { return false; }
    var walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) {
      var value = node.nodeValue || '';
      if (value.length < 16) continue;
      var m = re.exec(value);
      if (!m) continue;
      var range = document.createRange();
      range.setStart(node, m.index);
      range.setEnd(node, m.index + m[0].length);
      var mark = document.createElement('mark');
      mark.className = 'cr-hit';
      try { range.surroundContents(mark); } catch (e) { return false; }
      mark.scrollIntoView({ block: 'center' });
      var banner = document.createElement('div');
      banner.className = 'cr-hit-banner';
      banner.textContent = '已定位到命中片段';
      body.insertBefore(banner, body.firstChild);
      return true;
    }
    return false;
  }

  function copySource() {
    var d = state.data;
    if (!d) return;
    var text = d.markdown || '';
    var done = function (ok) {
      var btn = document.getElementById('crCopyBtn');
      if (!btn) return;
      var old = btn.textContent;
      btn.textContent = ok ? '已复制' : '复制失败';
      setTimeout(function () { btn.textContent = old; }, 1400);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); },
        function () { done(fallbackCopy(text)); });
    } else {
      done(fallbackCopy(text));
    }
  }

  function fallbackCopy(text) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.top = '-1000px';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch (e) {
      return false;
    }
  }

  function reasonText(payload, status) {
    if (status === 401) return '登录状态已失效，请刷新页面后重试。';
    if (status === 403) {
      return (payload && payload.message) || '该来源需要更高权限，当前账号无法查看。';
    }
    if (payload && payload.message) return payload.message;
    if (status === 404) return '本地原文不可用。';
    return '读取原文失败（HTTP ' + status + '），请稍后重试。';
  }

  function openCorpusSource(recordKey, opts) {
    var key = String(recordKey || '').trim();
    if (!key) return;
    var focus = (opts && opts.focus) ? String(opts.focus) : '';
    var mask = ensureMask();
    mask.removeAttribute('hidden');
    document.body.classList.add('corpus-reader-open');

    state.key = key;
    state.data = null;
    state.view = 'rendered';
    state.focus = focus;
    document.getElementById('crTitle').textContent = '正在读取…';
    document.getElementById('crTitle').title = '';
    document.getElementById('crMeta').textContent = '';
    document.getElementById('crFoot').textContent = '';
    document.getElementById('crViewBtn').textContent = '原始 Markdown';
    setStatus('正在读取本地原文…', 'info');

    var url = '/api/v2/corpus/source?record=' + encodeURIComponent(key);
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) {
        return r.json().catch(function () { return {}; })
          .then(function (payload) { return { ok: r.ok, status: r.status, payload: payload }; });
      })
      .then(function (res) {
        if (!res.ok || !res.payload || res.payload.ok !== true) {
          setStatus(reasonText(res.payload, res.status), 'warn');
          document.getElementById('crTitle').textContent = '本地原文不可用';
          document.getElementById('crMeta').textContent = '';
          return;
        }
        state.data = res.payload;
        paint();
      })
      .catch(function () {
        setStatus('读取原文失败：网络或服务异常，请稍后重试。', 'warn');
        document.getElementById('crTitle').textContent = '本地原文不可用';
      });
  }

  // ── 本轮本地来源：按 request_id 回查工具结果，渲染可点开的来源列表 ──────
  // 回答正文里的内部键已被后端清洗，溯源只从 tool_results 回查；
  // 键只作为请求参数与 data 属性存在，标签一律用可读题名/年份/期刊。
  function attachTurnSources(bubble, requestId) {
    if (!bubble || !requestId) return;
    var sid = window.sessionId || '';
    if (!sid) return;                                   // 没登录/没会话：下一轮再试
    if (bubble.getAttribute('data-cr-sources') === String(requestId)) return;
    bubble.setAttribute('data-cr-sources', String(requestId));
    var url = '/api/v2/session/' + encodeURIComponent(sid) +
              '/turn-sources?request_id=' + encodeURIComponent(requestId);
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (payload) {
        var list = (payload && payload.sources) || [];
        if (list.length) renderTurnSources(bubble, list);
      })
      .catch(function () { /* 溯源是增强，失败静默，不打扰回答 */ });
  }

  function renderTurnSources(bubble, list) {
    var old = bubble.querySelector('.cr-turn-sources');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var locals = [];
    var webs = [];
    for (var i = 0; i < (list || []).length; i++) {
      var item = list[i] || {};
      if (item.kind === 'web' && item.url) webs.push(item);
      else if (item.record_key) locals.push(item);
    }
    if (!locals.length && !webs.length) return;

    var row = document.createElement('div');
    row.className = 'cr-turn-sources';

    function group(title, items, makeChip) {
      if (!items.length) return;
      var label = document.createElement('span');
      label.className = 'cr-ts-label';
      label.textContent = title;
      row.appendChild(label);
      for (var k = 0; k < items.length; k++) row.appendChild(makeChip(items[k]));
    }

    group('本轮本地来源', locals, function (item) {
      var a = document.createElement('a');
      a.className = 'cr-ts-chip';
      a.href = '#';
      a.setAttribute('data-corpus-key', item.record_key);
      if (item.snippet) a.setAttribute('data-focus', item.snippet);
      var label = item.title || '本地文献';
      a.textContent = label;
      var tip = [label];
      if (item.journal) tip.push(item.journal);
      if (item.year) tip.push(String(item.year));
      if (item.registry) tip.push(item.registry);
      a.title = tip.join(' · ') + ' — 点击查看原文并定位命中片段';
      return a;
    });

    group('本轮网络来源', webs, function (item) {
      var a = document.createElement('a');
      a.className = 'cr-ts-chip cr-ts-web';
      a.href = item.url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      var label = item.title || item.url;
      a.textContent = label;
      a.title = (item.snippet ? item.snippet.slice(0, 120) + ' — ' : '') + item.url;
      return a;
    });

    bubble.appendChild(row);
  }

  function attachHistorySources(root) {
    var scope = root || document;
    var bubbles = scope.querySelectorAll('.message.bot');
    for (var i = 0; i < bubbles.length; i++) {
      var b = bubbles[i];
      if (b.getAttribute('data-cr-sources')) continue;
      // request id 挂在气泡内的 .content 上（历史与流式同一契约）
      var holder = b.querySelector('[data-request-id]');
      if (!holder) continue;
      var rid = holder.getAttribute('data-request-id');
      if (!rid) continue;
      var trace = b.querySelector('.tool-trace');
      if (trace && !trace.classList.contains('is-done')) continue;  // 流式未结束
      attachTurnSources(b, rid);
    }
  }

  function watchChatSources() {
    var host = document.getElementById('chat-container') || document.getElementById('chatMessages');
    if (!host) { setTimeout(watchChatSources, 900); return; }
    var timer = null;
    var run = function () { try { attachHistorySources(host); } catch (e) {} };
    if (typeof MutationObserver === 'function') {
      new MutationObserver(function () {
        if (timer) clearTimeout(timer);
        timer = setTimeout(run, 1500);
      }).observe(host, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['data-request-id', 'class']
      });
    }
    setTimeout(run, 1200);
  }


  // chips 自己的点击代理：不依赖 app.js 里针对 a.corpus-key 的代理
  document.addEventListener('click', function (ev) {
    var el = ev.target;
    if (!el || !el.closest) return;
    var chip = el.closest('.cr-ts-chip');
    if (!chip) return;
    ev.preventDefault();
    if (chip.classList.contains('cr-ts-web')) return;   // 网页来源交给浏览器
    ev.preventDefault();
    var key = chip.getAttribute('data-corpus-key');
    var focus = chip.getAttribute('data-focus') || '';
    if (key) {
      if (typeof openCorpusSource === 'function') openCorpusSource(key, { focus: focus });
      else window.openCorpusSource(key, { focus: focus });
    }
  }, true);

  window.openCorpusSource = openCorpusSource;
  window.CitrusCorpusReader = {
    open: openCorpusSource,
    close: closeReader,
    isOpen: isOpen,
    getState: function () { return { key: state.key, view: state.view, chars: (state.data && state.data.chars) || 0 }; },
    attachTurnSources: attachTurnSources,
    attachHistorySources: attachHistorySources
  };
  watchChatSources();
})();
