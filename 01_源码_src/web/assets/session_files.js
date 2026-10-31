// 会话文件（session files）前端：列表、预览、下载、删除。
//
// 边界（与本轮改造范围一致）：
//  - 账号私有资料库：列表、预览、下载与当前轮引用；不写入共享知识库。
//  - 打开预览是普通 HTTP 读取，不调用模型，也不把内容送进检索或知识库。
//  - 生成成果由科研管线拥有，这里只展示与下载，不提供删除。

(function () {
  'use strict';

  var panelEl = null;
  var apiBase = '';
  var fileCache = Object.create(null);

  function el(id) { return document.getElementById(id); }

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtSize(n) {
    n = Number(n || 0);
    if (!n) return '0 B';
    var u = ['B', 'KB', 'MB', 'GB'];
    var i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return n.toFixed(n >= 10 || i === 0 ? 0 : 1) + ' ' + u[i];
  }

  function fmtTime(v) {
    var ts = Number(v || 0);
    if (!ts) return '';
    var d = new Date(ts * 1000);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString();
  }

  var KIND_LABEL = {
    pdf: 'PDF', image: '图片', html: 'HTML 报告', table: '表格',
    docx: 'Word', text: '文本', download: '文件', svg: 'SVG'
  };

  function currentSessionId() {
    if (typeof window.currentSessionId === 'function') return window.currentSessionId();
    try { return sessionStorage.getItem('citrus_session_id') || ''; } catch (e) { return ''; }
  }

  function itemHtml(f) {
    var kind = displayKind(f);
    var label = KIND_LABEL[kind] || '文件';
    var deletable = f.origin === 'uploaded';
    var svg = isSvgFile(f);
    var jumpBtn = (f.turn_request_id && f.source_session_id)
      ? '    <button type="button" class="session-file-act" onclick="jumpToFileTurn(\'' +
          esc(f.source_session_id) + '\',\'' + esc(f.turn_request_id) + '\')" title="定位到生成它的会话" aria-label="定位到会话">↗</button>'
      : '';
    return '' +
      '<div class="session-file-item" data-file-id="' + esc(f.file_id) + '"' +
      '     data-kind="' + esc(kind) + '" data-filename="' + esc(f.filename) + '">' +
      '  <button type="button" class="session-file-open" onclick="openAccountFilePreview(\'' +
           esc(f.file_id) + '\')" title="预览 ' + esc(f.filename) + '">' +
      '    <span class="session-file-badge k-' + esc(kind) + '">' + esc(label) + '</span>' +
      '    <span class="session-file-name">' + esc(f.filename) + '</span>' +
      '    <span class="session-file-sub">' + esc(fmtSize(f.size_bytes)) +
             ' · ' + esc(fmtTime(f.created_at)) + '</span>' +
      '  </button>' +
      '  <div class="session-file-actions">' +
           jumpBtn +
      '    <button type="button" class="session-file-act" onclick="queueAccountFileReference(\'' +
          esc(f.file_id) + '\')" title="在当前对话中引用" aria-label="引用 ' +
          esc(f.filename) + '">＋</button>' +
      (svg
        ? '    <button type="button" class="session-file-act is-svg" data-file-act="copy-svg" data-file-id="' +
          esc(f.file_id) + '" title="复制 SVG 源码" aria-label="复制 SVG 源码">⧉</button>'
        : '') +
      '    <a class="session-file-act" href="' + esc(f.download_url) + '" download' +
           ' title="下载原件" aria-label="下载 ' + esc(f.filename) + '">⤓</a>' +
      '    <button type="button" class="session-file-act" data-file-act="more" data-file-id="' +
           esc(f.file_id) + '" title="更多操作" aria-label="更多操作" aria-haspopup="menu" aria-expanded="false">⋯</button>' +
      (deletable
        ? '<button type="button" class="session-file-act danger" onclick="deleteSessionFile(\'' +
          esc(f.file_id) + '\')" title="删除此上传文件" aria-label="删除 ' +
          esc(f.filename) + '">×</button>'
        : '') +
      '  </div>' +
      '  <div class="session-file-more" data-file-menu="' + esc(f.file_id) + '" role="menu" hidden>' +
      '    <button type="button" role="menuitem" data-file-act="info" data-file-id="' + esc(f.file_id) + '">文件信息</button>' +
      (svg
        ? '    <button type="button" role="menuitem" data-file-act="copy-svg" data-file-id="' + esc(f.file_id) + '">复制 SVG 源码</button>'
        : '') +
      '    <button type="button" role="menuitem" data-file-act="download" data-file-id="' + esc(f.file_id) + '">下载原件</button>' +
      '    <button type="button" role="menuitem" data-file-act="preview" data-file-id="' + esc(f.file_id) + '">预览</button>' +
      '  </div>' +
      '</div>';
  }

  function renderGroup(listEl, groupEl, files) {
    if (!files || !files.length) { groupEl.hidden = true; listEl.innerHTML = ''; return; }
    groupEl.hidden = false;
    listEl.innerHTML = files.map(itemHtml).join('');
  }

  function renderList(payload) {
    var empty = el('sessionFilesEmpty');
    var up = payload.uploaded || [];
    var gen = payload.generated || [];
    fileCache = Object.create(null);
    up.concat(gen).forEach(function(file) { fileCache[file.file_id] = file; });
    renderGroup(el('sessionFilesUploaded'), el('sessionFilesUploadedGroup'), up);
    renderGroup(el('sessionFilesGenerated'), el('sessionFilesGeneratedGroup'), gen);
    empty.hidden = (up.length + gen.length) > 0;
    if (!empty.hidden) empty.textContent = '还没有资料。上传的文件和生成成果会保存在你的账号资料库中，可在其他会话引用。';
    var retention = el('sessionFilesRetention');
    if (retention) {
      retention.textContent = payload.retention_idle_days
        ? '账号资料库 · 文件保留 30 天，可在其他会话中引用。'
        : '';
    }
  }

  function loadSessionFiles() {
    var empty = el('sessionFilesEmpty');
    var hasFiles = (el('sessionFilesUploaded') && el('sessionFilesUploaded').children.length)
      || (el('sessionFilesGenerated') && el('sessionFilesGenerated').children.length);
    if (empty && !hasFiles) {
      empty.hidden = false;
      empty.classList.remove('session-files-error');
      empty.textContent = '正在加载资料库…';
    }
    return fetch(apiBase + '/api/v2/files',
                 { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        if (!r.ok) {
          var err = new Error('library-unavailable');
          err.status = r.status;
          throw err;
        }
        return r.json();
      })
      .then(function (d) {
        renderList(d || {});
        listLoaded = true;
        decorateTurnCards();
        var empty = el('sessionFilesEmpty');
        if (empty) empty.classList.remove('session-files-error');
        return d || {};
      })
      .catch(function (err) {
        var empty = el('sessionFilesEmpty');
        if (empty) {
          empty.hidden = false;
          empty.classList.add('session-files-error');
          empty.textContent = err && err.status === 401
            ? '登录后可查看你的资料库。'
            : '资料库暂时无法加载，请稍后重试。';
        }
      });
  }

  function toggleSessionFiles(show) {
    var p = el('sessionFilesPanel');
    if (!p) return;
    var want = (typeof show === 'boolean') ? show : p.hidden;
    p.hidden = !want;
    document.body.classList.toggle('session-files-open', want);
    var toggle = el('sessionFilesRailBtn');
    if (toggle) {
      toggle.setAttribute('aria-expanded', want ? 'true' : 'false');
      toggle.classList.toggle('active', want);
    }
    if (want) { syncLibraryBounds(); loadSessionFiles(); }
  }

  // P1-3: 打开前记住焦点，关闭后还回去（键盘用户不会掉到页面顶部）
  var previewReturnFocus = null;

  function closeSessionFilePreview() {
    var o = el('sessionFilePreviewOverlay');
    if (!o || o.hidden) return;
    o.hidden = true;
    var stage = el('sessionFilePreviewStage');
    if (stage) stage.innerHTML = '';
    var back = previewReturnFocus;
    previewReturnFocus = null;
    if (back && typeof back.focus === 'function' && document.contains(back)) {
      try { back.focus(); } catch (_) {}
    }
  }

  function previewStageHtml(file, meta) {
    var kind = file.kind;
    var url = file.preview_url;
    var dl = file.download_url;
    if (isSvgFile(file)) {
      // 安全边界：SVG 只以 <img> 载入（图片上下文里脚本、外链、事件处理器都不
      // 执行），绝不把源码 innerHTML 注入聊天页；要看源码请用「复制 SVG 源码」。
      return '<div class="sf-image-wrap"><img class="sf-preview-image" src="' +
             esc(dl) + '" alt="' + esc(file.filename) + '"></div>' +
             '<div class="sf-preview-note">SVG 以隔离图片方式渲染（脚本不会执行）；' +
             '需要源码请用上方「复制 SVG 源码」。</div>';
    }
    if (kind === 'pdf') {
      // 不再靠 iframe.onload 断言"已展示"：先把字节取回来、验过 PDF 魔数，再交给
      // 浏览器渲染。取不到 / 不是 PDF / 渲染器缺失时，显示可点击的兜底动作。
      return '<div class="sf-pdf-wrap" data-src="' + esc(url) + '" data-dl="' + esc(dl) + '">' +
             '<div class="sf-pdf-status">正在读取 PDF…</div>' +
             '<iframe class="sf-preview-frame sf-pdf-frame" title="PDF 预览" hidden></iframe>' +
             '<div class="sf-pdf-fallback" hidden>' +
             '<div class="sf-pdf-fallback-text"></div>' +
             '<div class="sf-pdf-fallback-actions">' +
             '<a class="api-secondary" href="' + esc(url) + '" target="_blank" rel="noopener">在新窗口打开</a>' +
             '<a class="api-secondary" href="' + esc(dl) + '" download>下载原件</a>' +
             '</div></div></div>';
    }
    if (kind === 'image') {
      return '<div class="sf-image-wrap"><img class="sf-preview-image" src="' +
             esc(url) + '" alt="' + esc(file.filename) + '"></div>';
    }
    if (kind === 'html') {
      return '<iframe class="sf-preview-frame" sandbox="" src="' + esc(url) +
             '" title="HTML 报告预览（已隔离）"></iframe>';
    }
    if (kind === 'text') {
      return '<pre class="sf-preview-text" data-src="' + esc(url) + '">加载中…</pre>';
    }
    if (kind === 'table') {
      return '<div class="sf-preview-table" data-src="' + esc(dl) +
             '" data-name="' + esc(file.filename) + '">解析中…</div>';
    }
    if (kind === 'docx') {
      return '<div class="sf-preview-docx">Word 文档以只读方式预览，' +
             '排版可能与原件略有差异。<br><a href="' + esc(dl) +
             '" download>下载原件查看完整格式</a></div>';
    }
    return '<div class="sf-preview-fallback">该格式暂不支持在线预览。<br>' +
           '<a href="' + esc(dl) + '" download>下载原件</a></div>';
  }

  // 同一个 PDF 可能被反复打开：旧的 blob URL 必须回收，否则会话越长内存越大。
  var _pdfObjectUrls = [];
  function _releasePdfObjectUrls() {
    while (_pdfObjectUrls.length) {
      try { URL.revokeObjectURL(_pdfObjectUrls.pop()); } catch (e) { /* 忽略 */ }
    }
  }

  function loadPdfPreview(node) {
    var src = node.getAttribute('data-src');
    var dl = node.getAttribute('data-dl') || src;
    var status = node.querySelector('.sf-pdf-status');
    var frame = node.querySelector('.sf-pdf-frame');
    var fallback = node.querySelector('.sf-pdf-fallback');
    var fbText = node.querySelector('.sf-pdf-fallback-text');
    _releasePdfObjectUrls();

    function fail(message) {
      if (status) status.hidden = true;
      if (frame) frame.hidden = true;
      if (fallback) fallback.hidden = false;
      if (fbText) fbText.textContent = message;
    }

    if (!src) { fail('没有可用的预览地址。'); return; }
    var MAX_INLINE_PDF = 40 * 1024 * 1024;
    fetch(src, { credentials: 'same-origin' })
      .then(function (r) {
        if (r.status === 403) throw new Error('forbidden');
        if (r.status === 404) throw new Error('not-found');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        var size = Number(r.headers && r.headers.get ? r.headers.get('content-length') : 0) || 0;
        if (size > MAX_INLINE_PDF) {
          throw new Error('too-large:' + Math.round(size / 1048576) + ':' + Math.round(MAX_INLINE_PDF / 1048576));
        }
        return r.arrayBuffer();
      })
      .then(function (buf) {
        if (!buf || !buf.byteLength) throw new Error('empty');
        var head = new Uint8Array(buf, 0, Math.min(5, buf.byteLength));
        var magic = '';
        for (var i = 0; i < head.length; i++) magic += String.fromCharCode(head[i]);
        if (magic !== '%PDF-') throw new Error('not-pdf');
        var blobUrl = URL.createObjectURL(new Blob([buf], { type: 'application/pdf' }));
        _pdfObjectUrls.push(blobUrl);
        if (status) status.hidden = true;
        if (fallback) fallback.hidden = true;
        if (frame) { frame.src = blobUrl; frame.hidden = false; }
      })
      .catch(function (err) {
        var reason = String(err && err.message || err);
        if (reason === 'not-pdf') {
          fail('服务器返回的内容不是 PDF 文件，未做预览。');
        } else if (reason === 'empty') {
          fail('文件内容为空（0 字节），未做预览。');
        } else if (reason === 'forbidden') {
          fail('无权访问该文件（它属于其他会话或账号），请联系文件所有者。');
        } else if (reason === 'not-found') {
          fail('文件不存在或已被删除，请刷新侧栏确认。');
        } else if (reason.indexOf('too-large:') === 0) {
          var parts = reason.split(':');
          fail('文件为 ' + parts[1] + ' MB，超过 ' + parts[2] +
               ' MB 的内嵌预览上限，为避免页面卡死没有直接加载；请下载或直接打开。');
        } else if (reason.indexOf('HTTP ') === 0) {
          fail('服务器返回 ' + reason + '，未能读取文件；请稍后重试或下载。');
        } else if (/Failed to fetch|NetworkError|Load failed/i.test(reason)) {
          fail('网络中断，文件没能下载下来；请检查网络后重试，或下载到本地再打开。');
        } else {
          fail('读取失败（' + reason + '），请重试或下载到本地打开。');
        }
      });
  }

  function loadTextPreview(node) {
    var src = node.getAttribute('data-src');
    if (!src) return;
    fetch(src, { credentials: 'same-origin' })
      .then(function (r) { return r.text(); })
      .then(function (t) { node.textContent = t.slice(0, 200000); })
      .catch(function () { node.textContent = '读取失败，请下载原件查看。'; });
  }

  // 表格预览：浏览器端解析 CSV / XLSX，不经过后端模型。
  function loadTablePreview(node) {
    var src = node.getAttribute('data-src');
    var name = node.getAttribute('data-name') || '';
    var isCsv = /\.(csv|tsv)$/i.test(name);
    if (!src) return;
    fetch(src, { credentials: 'same-origin' })
      .then(function (r) { return r.arrayBuffer(); })
      .then(function (buf) {
        if (isCsv) { renderCsv(node, buf); return; }
        if (window.XLSX) { renderXlsx(node, buf); return; }
        node.innerHTML = '该浏览器未加载表格解析组件。' +
          '<br><a href="' + esc(src) + '" download>下载原件</a>';
      })
      .catch(function () { node.innerHTML = '读取失败，请下载原件查看。'; });
  }

  function renderCsv(node, buf) {
    var text = new TextDecoder('utf-8').decode(buf);
    var rows = text.split(/\r?\n/).filter(function (l) { return l.length; });
    var limit = Math.min(rows.length, 200);
    var html = '<table class="sf-table">';
    for (var i = 0; i < limit; i++) {
      var cells = rows[i].split(/[,\t]/);
      html += '<tr>';
      for (var j = 0; j < cells.length; j++) {
        html += (i === 0 ? '<th>' : '<td>') + esc(cells[j]) + (i === 0 ? '</th>' : '</td>');
      }
      html += '</tr>';
    }
    html += '</table>';
    if (rows.length > limit) {
      html += '<div class="sf-preview-note">仅显示前 ' + limit + ' 行，共 ' +
              rows.length + ' 行。完整内容请下载原件。</div>';
    }
    node.innerHTML = html;
  }

  function renderXlsx(node, buf) {
    try {
      var wb = window.XLSX.read(buf, {
        type: 'array',
        cellStyles: true,
        cellDates: true,
        cellNF: false
      });
      var sheets = wb.SheetNames || [];
      var tabs = sheets.map(function (n, i) {
        return '<button type="button" class="sf-sheet-tab' + (i === 0 ? ' active' : '') +
               '" data-sheet="' + esc(n) + '">' + esc(n) + '</button>';
      }).join('');
      node.innerHTML = '<div class="sf-sheet-tabs">' + tabs +
        '</div><div class="sf-sheet-body"></div>';
      var body = node.querySelector('.sf-sheet-body');

      function colorOf(spec) {
        if (!spec || !spec.rgb) return '';
        var v = String(spec.rgb);
        if (v.length === 8) v = v.slice(2);
        return /^[0-9A-Fa-f]{6}$/.test(v) ? '#' + v.toLowerCase() : '';
      }

      // SheetJS 读 xlsx 时把填充摊平在 s 上（s.patternType + s.fgColor），
      // 读 xls 时则在 s.fill 里；两种形态都要认，否则标注色一块都显示不出来。
      function fillColor(st) {
        if (!st) return '';
        if (st.fill) {
          var nested = colorOf(st.fill.fgColor || st.fill.bgColor);
          if (nested) return nested;
        }
        if (st.patternType && st.patternType !== 'none') return colorOf(st.fgColor);
        return '';
      }

      function cellInlineStyle(cell) {
        var st = cell && cell.s;
        if (!st) return '';
        var css = [];
        var bg = fillColor(st);
        if (bg && bg !== '#ffffff' && bg !== '#000000') css.push('background:' + bg);
        var fg = colorOf(st.font && st.font.color);
        if (fg) css.push('color:' + fg);
        if (st.font && st.font.bold) css.push('font-weight:700');
        if (st.font && st.font.italic) css.push('font-style:italic');
        return css.join(';');
      }

      function hasBorder(cell) {
        var b = cell && cell.s && cell.s.border;
        if (!b) return false;
        return ['top', 'bottom', 'left', 'right'].some(function (side) {
          return b[side] && b[side].style;
        });
      }

      function commentOf(cell) {
        var c = cell && cell.c;
        if (!c || !c.length) return '';
        return String(c[0].t || '').trim();
      }

      var _ISSUE_SHEET = '问题清单';
      var pendingFocus = null;

      function bindIssueList(body) {
        var rows = body.querySelectorAll('tr');
        Array.prototype.forEach.call(rows, function (tr) {
          var cells = tr.querySelectorAll('td');
          if (cells.length < 3) return;   // 表头行是 th
          var sheetName = (cells[1].textContent || '').trim();
          var target = (cells[2].textContent || '').trim();
          if (!sheetName) return;
          tr.classList.add('sf-issue-row');
          tr.title = '跳到 ' + sheetName + ' · ' + target;
          tr.addEventListener('click', function () {
            var ref = /^([A-Z]+)(\d+)$/.test(target) ? target : '';
            var rowMatch = target.match(/第\s*(\d+)\s*行/);
            pendingFocus = {
              sheet: sheetName,
              ref: ref,
              row: rowMatch ? parseInt(rowMatch[1], 10) : 0
            };
            var tab = null;
            node.querySelectorAll('.sf-sheet-tab').forEach(function (b) {
              if (b.getAttribute('data-sheet') === sheetName) tab = b;
            });
            if (!tab) return;
            node.querySelectorAll('.sf-sheet-tab').forEach(function (b) {
              b.classList.remove('active');
            });
            tab.classList.add('active');
            showSheet(sheetName);
          });
        });
      }

      function applyPendingFocus(body) {
        if (!pendingFocus) return;
        var target = null;
        if (pendingFocus.ref) {
          target = body.querySelector('[data-ref="' + pendingFocus.ref + '"]');
        } else if (pendingFocus.row) {
          target = body.querySelector('[data-row="' + pendingFocus.row + '"]');
        }
        pendingFocus = null;
        if (!target) return;
        target.classList.add('sf-cell-focus');
        try {
          target.scrollIntoView({ block: 'center', behavior: 'smooth' });
        } catch (err) {
          target.scrollIntoView();
        }
        window.setTimeout(function () { target.classList.remove('sf-cell-focus'); }, 2400);
      }

      function showSheet(name) {
        var ws = wb.Sheets[name];
        if (!ws || !ws['!ref']) {
          body.innerHTML = '<div class="sf-preview-note">该工作表没有可显示的数据区域。</div>';
          return;
        }
        var range = window.XLSX.utils.decode_range(ws['!ref']);
        var limit = Math.min(range.e.r - range.s.r + 1, 200);
        // Merged ranges: the anchor cell renders with colspan/rowspan; the cells
        // it covers are skipped so the table keeps its real grid shape.
        var anchors = {};
        var covered = {};
        (ws['!merges'] || []).forEach(function (m) {
          var key = m.s.r + ':' + m.s.c;
          anchors[key] = { rows: m.e.r - m.s.r + 1, cols: m.e.c - m.s.c + 1 };
          for (var r = m.s.r; r <= m.e.r; r++) {
            for (var c = m.s.c; c <= m.e.c; c++) {
              if (r === m.s.r && c === m.s.c) continue;
              covered[r + ':' + c] = true;
            }
          }
        });
        var html = '<table class="sf-table sf-table-styled">';
        for (var r = range.s.r; r < range.s.r + limit; r++) {
          html += '<tr>';
          for (var c = range.s.c; c <= range.e.c; c++) {
            if (covered[r + ':' + c]) continue;
            var addr = window.XLSX.utils.encode_cell({ r: r, c: c });
            var cell = ws[addr];
            var text = '';
            if (cell) {
              text = (cell.w != null && cell.w !== '') ? String(cell.w)
                   : (cell.v != null ? String(cell.v) : '');
            }
            var span = anchors[r + ':' + c];
            var attrs = '';
            if (span) {
              if (span.cols > 1) attrs += ' colspan="' + span.cols + '"';
              if (span.rows > 1) attrs += ' rowspan="' + span.rows + '"';
            }
            attrs += ' data-ref="' + addr + '" data-row="' + (r + 1) + '"';
            var style = cellInlineStyle(cell);
            var cls = 'sf-cell';
            if (hasBorder(cell) || fillColor(cell && cell.s)) cls += ' sf-cell-boxed';
            var note = commentOf(cell);
            if (note) {
              cls += ' sf-cell-noted';
              attrs += ' title="' + esc(note) + '"';
            }
            if (style) attrs += ' style="' + style + '"';
            var tag = (r === range.s.r) ? 'th' : 'td';
            html += '<' + tag + ' class="' + cls + '"' + attrs + '>' + esc(text) +
                    (note ? '<span class="sf-cell-mark" aria-hidden="true">!</span>' : '') +
                    '</' + tag + '>';
          }
          html += '</tr>';
        }
        html += '</table>';
        if (range.e.r - range.s.r + 1 > limit) {
          html += '<div class="sf-preview-note">仅显示前 ' + limit + ' 行，共 ' +
                  (range.e.r - range.s.r + 1) + ' 行；完整内容请下载原件。</div>';
        } else if (Object.keys(anchors).length || Object.keys(covered).length) {
          html += '<div class="sf-preview-note">已按原始合并单元格还原显示。</div>';
        }
        body.innerHTML = html;
        if (name === _ISSUE_SHEET) bindIssueList(body);
        applyPendingFocus(body);
      }

      node.querySelectorAll('.sf-sheet-tab').forEach(function (btn) {
        btn.addEventListener('click', function () {
          node.querySelectorAll('.sf-sheet-tab').forEach(function (b) {
            b.classList.remove('active');
          });
          btn.classList.add('active');
          showSheet(btn.getAttribute('data-sheet'));
        });
      });
      if (sheets.length) showSheet(sheets[0]);
    } catch (e) {
      node.innerHTML = '表格解析失败，请下载原件查看。';
    }
  }

  function openAccountFilePreview(fileOrId) {
    var file = (fileOrId && typeof fileOrId === 'object')
      ? fileOrId : fileCache[String(fileOrId || '')];
    if (!file) {
      loadSessionFiles().then(function() {
        var found = fileCache[String(fileOrId || '')];
        if (found) openAccountFilePreview(found);
      });
      return;
    }
    var overlay = el('sessionFilePreviewOverlay');
    previewReturnFocus = document.activeElement;
    overlay.hidden = false;
    el('sessionFilePreviewTitle').textContent = file.filename || '文件预览';
    previewFileId = String(file.file_id || '');
    el('sessionFilePreviewDownload').href = file.download_url || '#';
    var copySvgBtn = el('sessionFilePreviewCopySvg');
    if (copySvgBtn) {
      copySvgBtn.hidden = !isSvgFile(file);
      copySvgBtn.setAttribute('data-file-id', previewFileId);
    }
    el('sessionFilePreviewMeta').textContent =
      '资料库文件 · ' + (KIND_LABEL[displayKind(file)] || '文件');
    var stage = el('sessionFilePreviewStage');
    stage.innerHTML = previewStageHtml(file);
    var pnode = stage.querySelector('.sf-pdf-wrap');
    if (pnode) loadPdfPreview(pnode);
    var tnode = stage.querySelector('.sf-preview-text');
    if (tnode) loadTextPreview(tnode);
    var gnode = stage.querySelector('.sf-preview-table');
    if (gnode) loadTablePreview(gnode);
    // 打开后把焦点交给关闭按钮：Tab 顺序从弹窗开始，Esc 随时可退
    var closeBtn = overlay.querySelector('.overlay-close');
    if (closeBtn && typeof closeBtn.focus === 'function') {
      try { closeBtn.focus({ preventScroll: true }); } catch (_) { closeBtn.focus(); }
    }
  }

  function queueAccountFileReference(fileId) {
    var file = fileCache[String(fileId || '')];
    if (!file) return;
    var refs = window._selectedAccountFileRefs || (window._selectedAccountFileRefs = []);
    if (refs.some(function(item) { return item.file_id === file.file_id; })) return;
    if (refs.length + (window._selectedAttachments || []).length >= 8) {
      if (window.showBanner) window.showBanner('每轮最多上传或引用 8 个文件。');
      return;
    }
    refs.push(file);
    if (window.renderAttachmentSelection) window.renderAttachmentSelection();
    if (window.showBanner) window.showBanner('已添加到当前问题。');
  }

  function deleteSessionFile(fileId) {
    if (!confirm('删除这个上传文件？此操作不可撤销。')) return;
    fetch(apiBase + '/api/v2/files/' + encodeURIComponent(fileId),
          { method: 'DELETE', credentials: 'same-origin' })
      .then(function (r) { return r.ok; })
      .then(function (ok) { if (ok) loadSessionFiles(); })
      .catch(function () { /* 忽略 */ });
  }

  function initSessionFiles() {
    panelEl = el('sessionFilesPanel');
    if (!panelEl) return;
    bindLibraryResizer();
    bindFileActions();
    observeTurnCards();
    // 会话切换时刷新列表
    if (typeof window.addEventListener === 'function') {
      window.addEventListener('citrus:session-changed', function () {
        if (!panelEl.hidden) loadSessionFiles();
      });
      // 生成成果后刷新（发送完成事件）
      window.addEventListener('citrus:turn-finished', function () {
        if (!panelEl.hidden) loadSessionFiles();
      });
    }
    // P1-3: Esc 关闭预览（只在弹窗真的打开时响应，不干扰输入框的 Esc）
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape' && e.key !== 'Esc') return;
      var o = el('sessionFilePreviewOverlay');
      if (!o || o.hidden) return;
      e.preventDefault();
      e.stopPropagation();
      closeSessionFilePreview();
    }, true);
  }


  /* ------------------------------------------------------------------ P1-D
     资料库宽度：最小 300px，最大宽度动态计算（不设固定 800 上限）。
       最大宽度 = 视口宽 − 左侧栏（最左快捷栏 + 会话栏）实际右边界 − LIB_GAP(24px)
     左侧栏被拖宽 / 收起、窗口缩放时最大值同步变化；空间不足时优先保证聊天区
     （LIB_MIN_CHAT）。拖动只改 CSS 变量 --library-w，不触碰消息 DOM，
     所以拖动过程不会触发消息列表重渲染。 */
  var LIB_MIN_W = 300;
  var LIB_DEFAULT_W = 340;
  var LIB_GAP = 24;
  var LIB_MIN_CHAT = 320;
  var LIB_W_KEY = 'citrus_library_width';
  var libWidth = LIB_DEFAULT_W;
  var libDragging = false;

  function viewportWidth() {
    return Math.max(0, window.innerWidth || 0, document.documentElement.clientWidth || 0);
  }

  function isDrawerMode() { return viewportWidth() <= 900; }

  function sidebarRightEdge() {
    var edge = 0;
    var rail = document.querySelector('.left-rail');
    if (rail) {
      var railRect = rail.getBoundingClientRect();
      if (railRect.width || railRect.height) edge = Math.max(edge, railRect.right);
    }
    var shell = el('mainContainer');
    var sidebar = el('appSidebar');
    var hidden = shell && shell.classList.contains('sidebar-hidden');
    if (sidebar && !hidden) {
      var sideRect = sidebar.getBoundingClientRect();
      if (sideRect.width) edge = Math.max(edge, sideRect.right);
    }
    if (!edge) {
      var main = document.querySelector('.main-container');
      if (main) edge = main.getBoundingClientRect().left;
    }
    return Math.max(0, edge);
  }

  function libraryMaxWidth() {
    var vw = viewportWidth();
    if (!vw) return LIB_DEFAULT_W;
    var available = vw - sidebarRightEdge();
    var geometryMax = available - LIB_GAP;         // 贴到左侧栏右边界，保留 24px 安全间隙
    var chatSafeMax = available - LIB_MIN_CHAT;    // 聊天区至少 320px 才可操作
    var max = geometryMax;
    if (chatSafeMax < LIB_MIN_W) max = Math.max(240, chatSafeMax);  // 空间不足：先保聊天区
    max = Math.min(max, Math.round(vw * 0.94));
    return Math.max(240, Math.round(max));
  }

  function clampLibraryWidth(width) {
    var max = libraryMaxWidth();
    var min = Math.min(LIB_MIN_W, max);
    var value = Math.round(Number(width) || 0);
    if (!isFinite(value) || value <= 0) value = LIB_DEFAULT_W;
    return Math.max(min, Math.min(max, value));
  }

  function applyLibraryWidth(width, persist) {
    // libWidth 记的是「用户想要多宽」，实际渲染值再按当前上限夹一次：
    // 刷新瞬间左侧栏/字体还没稳定，上限会短暂偏小，不能让那一次夹紧把宽度永久改小。
    var desired = Math.round(Number(width) || 0);
    if (!isFinite(desired) || desired <= 0) desired = LIB_DEFAULT_W;
    libWidth = desired;
    var value = clampLibraryWidth(desired);
    document.documentElement.style.setProperty('--library-w', value + 'px');
    var panel = el('sessionFilesPanel');
    if (panel) panel.style.setProperty('--library-w', value + 'px');
    var handle = el('sessionFilesResizer');
    if (handle) {
      handle.setAttribute('aria-valuenow', String(value));
      handle.setAttribute('aria-valuemax', String(libraryMaxWidth()));
    }
    if (persist) {
      try { localStorage.setItem(LIB_W_KEY, String(value)); } catch (err) { /* 忽略 */ }
    }
    return value;
  }

  function restoreLibraryWidth() {
    var raw = null;
    try { raw = localStorage.getItem(LIB_W_KEY); } catch (err) { /* 忽略 */ }
    var saved = raw ? parseInt(raw, 10) : NaN;
    applyLibraryWidth(isFinite(saved) ? saved : LIB_DEFAULT_W, false);
  }

  // 左侧栏被拖宽/收起、窗口缩放后：最大宽度同步，必要时把超出的宽度收回来
  function syncLibraryBounds() {
    if (isDrawerMode()) return;
    applyLibraryWidth(libWidth, false);
  }

  function bindLibraryResizer() {
    var handle = el('sessionFilesResizer');
    if (!handle) return;
    var startX = 0, startW = 0;
    handle.addEventListener('pointerdown', function (event) {
      if (event.button !== 0 || isDrawerMode()) return;
      libDragging = true;
      startX = event.clientX;
      startW = libWidth;
      document.body.classList.add('session-files-resizing');
      try { handle.setPointerCapture(event.pointerId); } catch (err) { /* 忽略 */ }
      event.preventDefault();
    });
    handle.addEventListener('pointermove', function (event) {
      if (!libDragging) return;
      // 面板贴住右侧：指针左移（clientX 变小）→ 变宽
      applyLibraryWidth(startW + (startX - event.clientX), false);
    });
    function finish(event) {
      if (!libDragging) return;
      libDragging = false;
      document.body.classList.remove('session-files-resizing');
      try { handle.releasePointerCapture(event.pointerId); } catch (err) { /* 忽略 */ }
      applyLibraryWidth(libWidth, true);      // 松开记住宽度
    }
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
    handle.addEventListener('dblclick', function () {
      applyLibraryWidth(LIB_DEFAULT_W, true); // 双击复位
    });
    handle.addEventListener('keydown', function (event) {
      if (event.key === 'Home') {
        event.preventDefault();
        applyLibraryWidth(LIB_DEFAULT_W, true);
        return;
      }
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      applyLibraryWidth(libWidth + (event.key === 'ArrowLeft' ? 16 : -16), true);
    });
    if (window.ResizeObserver) {
      var observer = new ResizeObserver(function () { syncLibraryBounds(); });
      [el('appSidebar'), document.querySelector('.left-rail')].forEach(function (node) {
        if (node) { try { observer.observe(node); } catch (err) { /* 忽略 */ } }
      });
    }
    window.addEventListener('resize', syncLibraryBounds);
    // 首屏布局稳定后再同步一次：刷新时先在旧上限下渲染，稳定后应该回到用户宽度
    window.setTimeout(syncLibraryBounds, 0);
    window.setTimeout(syncLibraryBounds, 350);
    window.addEventListener('load', syncLibraryBounds);
    var sidebarToggle = el('sidebarToggleBtn');
    if (sidebarToggle) {
      sidebarToggle.addEventListener('click', function () {
        window.setTimeout(syncLibraryBounds, 0);
        window.setTimeout(syncLibraryBounds, 260);
      });
    }
    restoreLibraryWidth();
  }

  /* ------------------------------------------------------------------ P1-E
     Agent 生成的 SVG 是科研产物：与 HTML/Excel/PDF 共用同一套资料库登记，
     这里只补四件事——预览（隔离渲染）、下载原件、复制源码、定位来源。
     归属仍用后端已有的 turn_request_id / source_session_id，前端不自己猜。 */
  var SVG_EXT_RE = /\.svg$/i;
  var listLoaded = false;
  var previewFileId = '';
  var lastCopiedSvg = '';

  function isSvgFile(file) {
    if (!file) return false;
    if (SVG_EXT_RE.test(String(file.filename || ''))) return true;
    return /svg/i.test(String(file.mime_type || ''));
  }

  function displayKind(file) {
    return isSvgFile(file) ? 'svg' : ((file && file.kind) || 'download');
  }

  function fileDownloadUrl(fileId) {
    var cached = fileCache[String(fileId || '')];
    return (cached && cached.download_url) ||
      (apiBase + '/api/v2/files/' + encodeURIComponent(fileId) + '/download');
  }

  function resolveFile(fileId) {
    var id = String(fileId || '');
    if (fileCache[id]) return Promise.resolve(fileCache[id]);
    return loadSessionFiles().then(function () { return fileCache[id] || null; });
  }

  function legacyCopy(value) {
    var area = document.createElement('textarea');
    area.value = value;
    area.setAttribute('readonly', 'readonly');
    area.style.position = 'fixed';
    area.style.top = '-1000px';
    area.style.left = '-1000px';
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
    document.body.removeChild(area);
    if (!ok) throw new Error('clipboard-blocked');
    return true;
  }

  function copyText(value) {
    var text = String(value || '');
    if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
      return navigator.clipboard.writeText(text)
        .catch(function () { return legacyCopy(text); });
    }
    return Promise.resolve(legacyCopy(text));
  }

  function toast(message) {
    if (typeof window.showBanner === 'function') { window.showBanner(message); return; }
    console.info('[资料库]', message);
  }

  function fetchSvgSource(fileId) {
    return fetch(fileDownloadUrl(fileId), { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) {
          var err = new Error('HTTP ' + r.status);
          err.status = r.status;
          throw err;
        }
        return r.text();
      })
      .then(function (text) {
        var source = String(text || '');
        if (!/<svg[\s>]/i.test(source)) throw new Error('not-svg');
        return source;
      });
  }

  // 复制的是**源码文本**（从带鉴权的下载接口取回后原样写入剪贴板），
  // 不是文件名；取不到 / 不是 SVG 一律不写剪贴板。
  function copySvgSource(fileId) {
    return resolveFile(fileId)
      .then(function () { return fetchSvgSource(fileId); })
      .then(function (source) { return copyText(source).then(function () { return source; }); })
      .then(function (source) {
        lastCopiedSvg = source;
        window.lastCopiedSvgSource = source;   // 验收探针据此断言"复制到的是源码"
        toast('已复制 SVG 源码（' + source.length + ' 字符）。');
        return source;
      })
      .catch(function (err) {
        var reason = String((err && err.message) || err);
        if (reason === 'clipboard-blocked') toast('浏览器拒绝了剪贴板写入，请允许后重试。');
        else if (reason === 'not-svg') toast('这个文件的内容不是 SVG 源码，未复制。');
        else if (err && err.status === 401) toast('登录后才能复制文件内容。');
        else toast('复制 SVG 源码失败，请重试。');
        throw err;
      });
  }

  function fileInfoHtml(file) {
    var rows = [
      ['文件名', file.filename || '—'],
      ['类型', (KIND_LABEL[displayKind(file)] || '文件') +
               (file.mime_type ? '（' + file.mime_type + '）' : '')],
      ['大小', fmtSize(file.size_bytes)],
      ['创建时间', fmtTime(file.created_at) || '—'],
      ['来源', file.origin === 'generated' ? 'Agent 生成（科研产物）' : '用户上传'],
      ['生成工具', file.producer_tool || '—'],
      ['来源会话', file.source_session_id || '—'],
      ['来源轮次', file.turn_request_id || '—'],
      ['校验值', file.sha256 ? (String(file.sha256).slice(0, 16) + '…') : '—']
    ];
    var html = '<dl class="sf-fileinfo">';
    rows.forEach(function (row) {
      html += '<dt>' + esc(row[0]) + '</dt><dd>' + esc(row[1]) + '</dd>';
    });
    html += '</dl>';
    var actions = '<div class="sf-fileinfo-actions">' +
      '<a class="api-secondary" href="' + esc(file.download_url || '#') + '" download>下载原件</a>';
    if (isSvgFile(file)) {
      actions += '<button type="button" class="api-secondary" data-file-act="copy-svg" data-file-id="' +
                 esc(file.file_id) + '">复制 SVG 源码</button>';
    }
    if (file.turn_request_id && file.source_session_id) {
      actions += '<button type="button" class="api-secondary" data-file-act="jump" data-file-id="' +
                 esc(file.file_id) + '">定位到来源会话</button>';
    }
    actions += '</div>';
    return html + actions;
  }

  function showFileInfo(fileId) {
    return resolveFile(fileId).then(function (file) {
      if (!file) { toast('文件信息暂不可用，请刷新资料库后重试。'); return; }
      var overlay = el('sessionFilePreviewOverlay');
      if (!overlay) return;
      previewReturnFocus = document.activeElement;
      overlay.hidden = false;
      previewFileId = String(file.file_id || '');
      el('sessionFilePreviewTitle').textContent = '文件信息 · ' + (file.filename || '');
      var download = el('sessionFilePreviewDownload');
      if (download) download.href = file.download_url || '#';
      var copySvgBtn = el('sessionFilePreviewCopySvg');
      if (copySvgBtn) {
        copySvgBtn.hidden = !isSvgFile(file);
        copySvgBtn.setAttribute('data-file-id', previewFileId);
      }
      el('sessionFilePreviewMeta').textContent =
        '资料库文件 · ' + (KIND_LABEL[displayKind(file)] || '文件');
      el('sessionFilePreviewStage').innerHTML = fileInfoHtml(file);
      var closeBtn = overlay.querySelector('.overlay-close');
      if (closeBtn && typeof closeBtn.focus === 'function') {
        try { closeBtn.focus({ preventScroll: true }); } catch (err) { closeBtn.focus(); }
      }
    });
  }

  // 面板有 transform 动画（会成为 fixed 的包含块）、面板与文件项又都是
  // overflow:hidden，菜单挂进 item 里会被裁切/错位。打开时临时挂到 body，
  // 关闭时放回原位，保证下次渲染和 DOM 结构一致。
  function closeMoreMenus(except) {
    document.querySelectorAll('.session-file-more').forEach(function (menu) {
      if (except && menu === except) return;
      menu.hidden = true;
      if (menu.__homeParent && menu.parentElement === document.body) {
        menu.__homeParent.appendChild(menu);
      }
      menu.__homeParent = null;
      var host = menu.parentElement;
      var opener = host && host.querySelector('[data-file-act="more"]');
      if (opener) opener.setAttribute('aria-expanded', 'false');
    });
  }

  // 资料库面板与文件项都是 overflow:hidden（为了圆角和内部滚动），下拉菜单直接
  // 绝对定位会被裁掉。这里改成 position:fixed 按按钮位置摆放，并夹在视口内。
  function placeMoreMenu(button, menu) {
    var rect = button.getBoundingClientRect();
    if (menu.parentElement !== document.body) {
      menu.__homeParent = menu.parentElement;
      document.body.appendChild(menu);
    }
    menu.style.position = 'fixed';
    menu.style.zIndex = '90';
    menu.style.right = 'auto';
    menu.style.bottom = 'auto';
    var width = menu.offsetWidth || 152;
    var height = menu.offsetHeight || 150;
    var left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
    var top = rect.bottom + 4;
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 4);
    menu.style.left = Math.round(left) + 'px';
    menu.style.top = Math.round(top) + 'px';
  }

  function toggleMoreMenu(button) {
    var item = button.closest('.session-file-item');
    var menu = item && item.querySelector('.session-file-more');
    if (!menu) return;
    var willOpen = menu.hidden;
    closeMoreMenus(menu);
    menu.hidden = !willOpen;
    button.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    if (willOpen) {
      placeMoreMenu(button, menu);
      var first = menu.querySelector('button');
      if (first) { try { first.focus({ preventScroll: true }); } catch (err) { /* 忽略 */ } }
    }
  }

  function triggerDownload(fileId) {
    var link = document.createElement('a');
    link.href = fileDownloadUrl(fileId);
    link.setAttribute('download', '');
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  function onFileActionClick(event) {
    var target = event.target;
    if (!target || typeof target.closest !== 'function') return;
    var button = target.closest('[data-file-act]');
    if (!button) { closeMoreMenus(null); return; }
    var action = button.getAttribute('data-file-act');
    var fileId = String(button.getAttribute('data-file-id') || '');
    if (action === 'more') {
      event.preventDefault();
      toggleMoreMenu(button);
      return;
    }
    if (action === 'info') {
      event.preventDefault();
      showFileInfo(fileId);
      closeMoreMenus(null);
      return;
    }
    if (action === 'copy-svg') {
      event.preventDefault();
      copySvgSource(fileId);
      closeMoreMenus(null);
      return;
    }
    if (action === 'download') {
      event.preventDefault();
      triggerDownload(fileId);
      closeMoreMenus(null);
      return;
    }
    if (action === 'preview') {
      event.preventDefault();
      openAccountFilePreview(fileId);
      closeMoreMenus(null);
      return;
    }
    if (action === 'jump') {
      event.preventDefault();
      resolveFile(fileId).then(function (file) {
        if (!file || !file.turn_request_id || !file.source_session_id) {
          toast('这个文件没有关联的会话轮次。');
          return;
        }
        var jumpFn = window.jumpToFileTurn || window.jumpFromFile;
        if (typeof jumpFn !== 'function') {
          toast('当前页面不支持定位到来源会话。');
          return;
        }
        // app.js 的 jumpToFileTurn 只在真的找到并高亮了那一轮时返回 true，不做“假跳转”。
        Promise.resolve(jumpFn(file.source_session_id, file.turn_request_id)).then(function (ok) {
          if (ok === false) toast('来源轮次不在当前可加载的历史里。');
        }, function () {
          toast('定位到来源会话失败。');
        });
      });
      closeMoreMenus(null);
    }
  }

  function bindFileActions() {
    document.addEventListener('click', onFileActionClick, false);
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') closeMoreMenus(null);
    });
    // 菜单是 position:fixed，面板滚动/窗口尺寸变化后位置会失效，直接关掉。
    var bodyEl = el('sessionFilesBody');
    if (bodyEl) bodyEl.addEventListener('scroll', function () { closeMoreMenus(null); }, { passive: true });
    window.addEventListener('resize', function () { closeMoreMenus(null); });
    var copySvgBtn = el('sessionFilePreviewCopySvg');
    if (copySvgBtn) {
      copySvgBtn.addEventListener('click', function () {
        var id = String(copySvgBtn.getAttribute('data-file-id') || previewFileId || '');
        if (id) copySvgSource(id);
      });
    }
  }

  // 聊天里的"本轮生成文件"卡片由 app.js 渲染（只给了预览按钮）。这里按
  // data-file-id 追加同样的下载/复制源码/定位/信息入口，复用同一套资料库，
  // 不另建一套文件库，也不改 app.js。
  function turnCardAction(action, fileId, glyph, label) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'turn-file-act';
    button.setAttribute('data-file-act', action);
    button.setAttribute('data-file-id', fileId);
    button.title = label;
    button.setAttribute('aria-label', label);
    button.textContent = glyph;
    return button;
  }

  function decorateTurnCards(root) {
    var scope = (root && typeof root.querySelectorAll === 'function') ? root : document;
    var cards = scope.querySelectorAll('.turn-file-card[data-file-id]');
    Array.prototype.forEach.call(cards, function (card) {
      if (card.querySelector('.turn-file-extra')) return;
      var fileId = String(card.dataset.fileId || '');
      if (!fileId) return;
      var nameEl = card.querySelector('.turn-file-name');
      var name = nameEl ? String(nameEl.textContent || '') : '';
      var cached = fileCache[fileId];
      var svg = SVG_EXT_RE.test(name) || isSvgFile(cached);
      // app.js 的徽标是按 kind 取前几位（SVG 的后端 kind 是 download，会显示成 DOWNLOAD
      // 并在 34px 宽的徽标里竖向折行）；这里只改这一张卡片的徽标文案，不动 app.js。
      if (svg) {
        var typeEl = card.querySelector('.turn-file-type');
        if (typeEl) {
          typeEl.textContent = 'SVG';
          typeEl.setAttribute('title', 'SVG 矢量图');
        }
      }
      var box = document.createElement('div');
      box.className = 'turn-file-extra';
      box.dataset.fileId = fileId;
      if (svg) box.appendChild(turnCardAction('copy-svg', fileId, '⧉', '复制 SVG 源码'));
      box.appendChild(turnCardAction('download', fileId, '⤓', '下载原件'));
      if (cached && cached.turn_request_id && cached.source_session_id) {
        box.appendChild(turnCardAction('jump', fileId, '↗', '定位到生成它的会话'));
      }
      if (svg) box.appendChild(turnCardAction('info', fileId, 'ⓘ', '文件信息'));
      card.appendChild(box);
    });
    if (cards.length && !listLoaded) {
      // 元数据到位后才知道 turn_request_id / source_session_id，据此补"定位"
      loadSessionFiles().then(function () { decorateTurnCards(); });
    }
  }

  function observeTurnCards() {
    var chat = el('chat-container');
    if (!chat || !window.MutationObserver) { decorateTurnCards(); return; }
    var timer = 0;
    var observer = new MutationObserver(function () {
      if (timer) return;
      timer = window.setTimeout(function () {
        timer = 0;
        decorateTurnCards(chat);
      }, 60);
    });
    observer.observe(chat, { childList: true, subtree: true });
    decorateTurnCards(chat);
  }

  window.loadSessionFiles = loadSessionFiles;
  window.toggleSessionFiles = toggleSessionFiles;
  window.openSessionFilePreview = openAccountFilePreview;
  window.openAccountFilePreview = openAccountFilePreview;
  window.queueAccountFileReference = queueAccountFileReference;
  window.closeSessionFilePreview = closeSessionFilePreview;
  window.deleteSessionFile = deleteSessionFile;
  // P1-E：这两个入口也对外暴露，方便探针/其它面板复用同一套操作
  window.copySvgSource = copySvgSource;
  window.showSessionFileInfo = showFileInfo;
  window.decorateTurnCards = decorateTurnCards;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSessionFiles);
  } else {
    initSessionFiles();
  }
})();
