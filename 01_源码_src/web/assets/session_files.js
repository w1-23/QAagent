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
    docx: 'Word', text: '文本', download: '文件'
  };

  function currentSessionId() {
    if (typeof window.currentSessionId === 'function') return window.currentSessionId();
    try { return sessionStorage.getItem('citrus_session_id') || ''; } catch (e) { return ''; }
  }

  function itemHtml(f) {
    var kind = f.kind || 'download';
    var label = KIND_LABEL[kind] || '文件';
    var deletable = f.origin === 'uploaded';
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
      '    <button type="button" class="session-file-act" onclick="queueAccountFileReference(\'' +
          esc(f.file_id) + '\')" title="在当前对话中引用" aria-label="引用 ' +
          esc(f.filename) + '">＋</button>' +
      '    <a class="session-file-act" href="' + esc(f.download_url) + '" download' +
           ' title="下载原件" aria-label="下载 ' + esc(f.filename) + '">⤓</a>' +
      (deletable
        ? '<button type="button" class="session-file-act danger" onclick="deleteSessionFile(\'' +
          esc(f.file_id) + '\')" title="删除此上传文件" aria-label="删除 ' +
          esc(f.filename) + '">×</button>'
        : '') +
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
    if (want) loadSessionFiles();
  }

  function closeSessionFilePreview() {
    var o = el('sessionFilePreviewOverlay');
    if (!o) return;
    o.hidden = true;
    var stage = el('sessionFilePreviewStage');
    if (stage) stage.innerHTML = '';
  }

  function previewStageHtml(file, meta) {
    var kind = file.kind;
    var url = file.preview_url;
    var dl = file.download_url;
    if (kind === 'pdf') {
      return '<iframe class="sf-preview-frame" src="' + esc(url) +
             '" title="PDF 预览"></iframe>';
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
      var wb = window.XLSX.read(buf, { type: 'array' });
      var sheets = wb.SheetNames || [];
      var tabs = sheets.map(function (n, i) {
        return '<button type="button" class="sf-sheet-tab' + (i === 0 ? ' active' : '') +
               '" data-sheet="' + esc(n) + '">' + esc(n) + '</button>';
      }).join('');
      node.innerHTML = '<div class="sf-sheet-tabs">' + tabs +
        '</div><div class="sf-sheet-body"></div>';
      var body = node.querySelector('.sf-sheet-body');
      function showSheet(n) {
        var ws = wb.Sheets[n];
        var csv = window.XLSX.utils.sheet_to_csv(ws, { blankrows: false });
        var rows = csv.split(/\r?\n/).filter(function (l) { return l.length; });
        var limit = Math.min(rows.length, 200);
        var html = '<table class="sf-table">';
        for (var i = 0; i < limit; i++) {
          var cells = rows[i].split(',');
          html += '<tr>';
          for (var j = 0; j < cells.length; j++) {
            html += (i === 0 ? '<th>' : '<td>') + esc(cells[j]) +
                    (i === 0 ? '</th>' : '</td>');
          }
          html += '</tr>';
        }
        html += '</table>';
        if (rows.length > limit) {
          html += '<div class="sf-preview-note">仅显示前 ' + limit + ' 行，共 ' +
                  rows.length + ' 行；完整内容请下载原件。</div>';
        }
        body.innerHTML = html;
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
    overlay.hidden = false;
    el('sessionFilePreviewTitle').textContent = file.filename || '文件预览';
    el('sessionFilePreviewDownload').href = file.download_url || '#';
    el('sessionFilePreviewMeta').textContent =
      '资料库文件 · ' + (KIND_LABEL[file.kind] || '文件');
    var stage = el('sessionFilePreviewStage');
    stage.innerHTML = previewStageHtml(file);
    var tnode = stage.querySelector('.sf-preview-text');
    if (tnode) loadTextPreview(tnode);
    var gnode = stage.querySelector('.sf-preview-table');
    if (gnode) loadTablePreview(gnode);
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
  }

  window.loadSessionFiles = loadSessionFiles;
  window.toggleSessionFiles = toggleSessionFiles;
  window.openSessionFilePreview = openAccountFilePreview;
  window.openAccountFilePreview = openAccountFilePreview;
  window.queueAccountFileReference = queueAccountFileReference;
  window.closeSessionFilePreview = closeSessionFilePreview;
  window.deleteSessionFile = deleteSessionFile;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSessionFiles);
  } else {
    initSessionFiles();
  }
})();
