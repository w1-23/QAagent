/* The figure workspace.
 *
 * The SVG is loaded as an <img>, never as markup. That is the same stance the
 * chat page takes: a figure is a picture, and a picture cannot script the
 * page. The cost of that stance is that the browser cannot tell which shape
 * is the title, so the *manifest* says: the server publishes each object's
 * bounding box in the SVG's own coordinates, and the selection boxes are
 * drawn from those. The box and the drawing therefore agree by construction.
 *
 * The edits are not applied here. The page collects them and posts them to
 * the server, which validates them, re-renders the figure and publishes the
 * result — so the preview and the download are the same bytes, and the data
 * cannot be changed from the browser.
 */
(function () {
  'use strict';

  var API = '/api/v2/figures';
  var state = {
    projects: [],
    current: null,      // the open project's detail
    selected: '',       // the selected gid
    edits: {},          // gid -> {prop: value}
    drag: null
  };

  function el(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]);
    });
  }

  var toastTimer = null;
  function notify(message, isError) {
    var box = el('toast');
    if (!box) return;
    box.textContent = message;
    box.classList.add('visible');
    box.classList.toggle('is-error', !!isError);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { box.classList.remove('visible'); }, 3600);
  }

  function jsonRequest(url, options) {
    return fetch(url, options || { credentials: 'same-origin' })
      .then(function (response) {
        return response.json().catch(function () { return {}; })
          .then(function (data) {
            if (response.status === 401) {
              location.replace('/?next=%2Ffigures');
              throw new Error('登录已失效');
            }
            if (!response.ok) {
              throw new Error(data.detail || '请求失败');
            }
            return data;
          });
      });
  }

  // ── The project list ───────────────────────────────────────────────────

  function renderProjects() {
    var list = el('projectList');
    if (!state.projects.length) {
      list.innerHTML = '<li class="muted">还没有图件。在聊天里让服务出图，或从资料库导入一张表。</li>';
      return;
    }
    list.innerHTML = state.projects.map(function (project) {
      var when = project.updated_at
        ? new Date(project.updated_at * 1000).toLocaleString()
        : '时间未知';
      var current = state.current && state.current.figure_id === project.figure_id
        ? ' is-current' : '';
      return '<li data-id="' + esc(project.figure_id) + '">' +
        '<div class="project-main"><strong>' + esc(project.title || '未命名图件') + '</strong>' +
        '<small>' + esc(project.source_kind === 'script' ? '脚本来源' : '规格来源') +
        ' · ' + project.objects + ' 个可编辑对象 · ' + esc(when) + '</small></div>' +
        '<button class="project-open' + current + '" data-open="' + esc(project.figure_id) + '">打开</button>' +
        '</li>';
    }).join('');
  }

  function loadProjects() {
    return jsonRequest(API + '/projects').then(function (payload) {
      state.projects = payload.projects || [];
      el('figureCount').textContent = state.projects.length
        ? state.projects.length + ' 个图件' : '';
      renderProjects();
    });
  }

  // ── Opening a project ──────────────────────────────────────────────────

  function openProject(figureId) {
    return jsonRequest(API + '/projects/' + encodeURIComponent(figureId)).then(function (detail) {
      state.current = detail;
      state.selected = '';
      state.edits = {};
      el('workspace').hidden = false;
      el('projectTitle').textContent = detail.title || '图件';
      el('projectMeta').textContent =
        (detail.source_kind === 'script' ? '脚本来源' : '规格来源') +
        ' · 第 ' + (detail.render_version || 0) + ' 版 · ' +
        (detail.objects || 0) + ' 个可编辑对象';

      var base = API + '/projects/' + encodeURIComponent(figureId);
      el('previewImage').src = base + '/preview.svg?v=' + Date.now();
      el('downloadSvg').href = base + '/export/svg';
      el('downloadPng').href = base + '/export/png';
      el('downloadPdf').href = base + '/export/pdf';

      renderObjectSelect();
      renderOverlay();
      renderProperties();
      renderProjects();
    }).catch(function (error) {
      notify(error.message, true);
    });
  }

  // ── The overlay ────────────────────────────────────────────────────────

  function manifestObjects() {
    if (!state.current || !state.current.manifest) return [];
    return state.current.manifest.objects || [];
  }

  function canvasSize() {
    var size = (state.current && state.current.manifest
      && state.current.manifest.figure_size_px) || [0, 0];
    return { width: Number(size[0]) || 0, height: Number(size[1]) || 0 };
  }

  function renderOverlay() {
    var overlay = el('overlay');
    var size = canvasSize();
    overlay.innerHTML = '';
    if (!size.width || !size.height) return;

    manifestObjects().forEach(function (object) {
      if (!object.bbox) return;
      var box = document.createElement('div');
      box.className = 'overlay-box';
      if (object.gid === state.selected) box.classList.add('is-selected');
      if ((object.editable || []).indexOf('offset_px') >= 0) {
        box.classList.add('is-draggable');
      }
      box.dataset.gid = object.gid;
      box.title = object.gid + '（' + object.role + '）';
      box.style.left = (object.bbox.x0 / size.width * 100) + '%';
      box.style.top = (object.bbox.y0 / size.height * 100) + '%';
      box.style.width = ((object.bbox.x1 - object.bbox.x0) / size.width * 100) + '%';
      box.style.height = ((object.bbox.y1 - object.bbox.y0) / size.height * 100) + '%';
      overlay.appendChild(box);
    });
  }

  function renderObjectSelect() {
    var select = el('objectSelect');
    var options = manifestObjects().map(function (object) {
      var label = object.label || object.role;
      return '<option value="' + esc(object.gid) + '">' +
        esc(object.gid) + (label ? '（' + esc(label) + '）' : '') + '</option>';
    });
    select.innerHTML = options.length
      ? options.join('')
      : '<option value="">这张图没有可编辑的对象</option>';
    if (state.selected) select.value = state.selected;
  }

  function selectedObject() {
    var gid = state.selected;
    var found = null;
    manifestObjects().forEach(function (object) {
      if (object.gid === gid) found = object;
    });
    return found;
  }

  // ── The property panel ─────────────────────────────────────────────────

  var ROLE_LABELS = {
    title: '标题', xlabel: 'X 轴标签', ylabel: 'Y 轴标签',
    legend: '图例', annot: '注释', series: '数据系列', axes: '子图'
  };

  function renderProperties() {
    var panel = el('propertyPanel');
    var object = selectedObject();
    if (!object) {
      panel.innerHTML = '<p class="property-empty">选择一个对象后可调整它的样式。</p>';
      return;
    }
    var editable = object.editable || [];
    if (!editable.length) {
      panel.innerHTML = '<p class="property-empty">这个对象没有可修改的属性。</p>';
      return;
    }
    var rows = [];
    var label = ROLE_LABELS[object.role] || object.role;

    if (editable.indexOf('color') >= 0) {
      rows.push('<div class="property-row"><label>颜色</label>' +
        '<input type="color" data-prop="color" value="' +
        esc(currentValue(object.gid, 'color', object.color || '#0f4d92')) + '"></div>');
    }
    if (editable.indexOf('fontsize') >= 0) {
      var size = currentValue(object.gid, 'fontsize', object.fontsize || 7);
      rows.push('<div class="property-row"><label>字号</label>' +
        '<input type="range" data-prop="fontsize" min="5" max="24" step="0.5" value="' +
        esc(size) + '"><span class="property-value" data-out="fontsize">' + esc(size) + '</span></div>');
    }
    if (editable.indexOf('linewidth') >= 0) {
      var width = currentValue(object.gid, 'linewidth', object.linewidth || 1.2);
      rows.push('<div class="property-row"><label>线宽</label>' +
        '<input type="range" data-prop="linewidth" min="0.2" max="8" step="0.1" value="' +
        esc(width) + '"><span class="property-value" data-out="linewidth">' + esc(width) + '</span></div>');
    }
    if (editable.indexOf('offset_px') >= 0) {
      var offset = currentValue(object.gid, 'offset_px', [0, 0]);
      rows.push('<div class="property-row"><label>位置</label>' +
        '<input type="number" data-prop="offset_x" step="1" value="' + esc(offset[0]) + '">' +
        '<input type="number" data-prop="offset_y" step="1" value="' + esc(offset[1]) + '"></div>');
    }
    if (editable.indexOf('frameon') >= 0) {
      var on = currentValue(object.gid, 'frameon', false);
      rows.push('<div class="property-row"><label>边框</label>' +
        '<input type="checkbox" data-prop="frameon"' + (on ? ' checked' : '') + '></div>');
    }
    panel.innerHTML = '<p class="property-empty">选中：' + esc(label) +
      '（' + esc(object.gid) + '）</p>' + rows.join('');
  }

  function currentValue(gid, prop, fallback) {
    var edits = state.edits[gid];
    if (edits && edits[prop] !== undefined) return edits[prop];
    return fallback;
  }

  function collectEdits() {
    var panel = el('propertyPanel');
    var object = selectedObject();
    if (!object) return;
    var gid = object.gid;
    if (!state.edits[gid]) state.edits[gid] = {};

    var colour = panel.querySelector('[data-prop="color"]');
    if (colour) state.edits[gid].color = colour.value;

    var size = panel.querySelector('[data-prop="fontsize"]');
    if (size) state.edits[gid].fontsize = Number(size.value);

    var width = panel.querySelector('[data-prop="linewidth"]');
    if (width) state.edits[gid].linewidth = Number(width.value);

    var frame = panel.querySelector('[data-prop="frameon"]');
    if (frame) state.edits[gid].frameon = frame.checked;

    var ox = panel.querySelector('[data-prop="offset_x"]');
    var oy = panel.querySelector('[data-prop="offset_y"]');
    if (ox && oy) {
      state.edits[gid].offset_px = [Number(ox.value) || 0, Number(oy.value) || 0];
    }
  }

  // ── Saving ─────────────────────────────────────────────────────────────

  function saveEdits() {
    if (!state.current) return;
    collectEdits();
    var batch = [];
    Object.keys(state.edits).forEach(function (gid) {
      var edits = state.edits[gid];
      Object.keys(edits).forEach(function (prop) {
        batch.push({ gid: gid, prop: prop, value: edits[prop] });
      });
    });
    if (!batch.length) {
      notify('没有需要保存的修改。');
      return;
    }
    var button = el('saveButton');
    button.disabled = true;
    jsonRequest(API + '/projects/' + encodeURIComponent(state.current.figure_id) + '/overrides', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ overrides: batch })
    }).then(function (result) {
      state.current.manifest = result.manifest;
      state.current.render_version = result.render_version;
      state.edits = {};
      var base = API + '/projects/' + encodeURIComponent(state.current.figure_id);
      el('previewImage').src = base + '/preview.svg?v=' + Date.now();
      el('projectMeta').textContent =
        (state.current.source_kind === 'script' ? '脚本来源' : '规格来源') +
        ' · 第 ' + result.render_version + ' 版 · ' +
        (result.manifest.objects || []).length + ' 个可编辑对象';
      renderOverlay();
      renderProperties();
      renderProjects();
      loadProjects();
      notify('已保存并重新渲染。');
    }).catch(function (error) {
      notify(error.message, true);
    }).then(function () {
      button.disabled = false;
    });
  }

  // ── Dragging ───────────────────────────────────────────────────────────

  function onOverlayMouseDown(event) {
    var box = event.target.closest ? event.target.closest('.overlay-box') : null;
    if (!box) return;
    event.preventDefault();
    state.selected = box.dataset.gid;
    renderObjectSelect();
    renderProperties();
    renderOverlay();

    var editable = (selectedObject() || {}).editable || [];
    if (editable.indexOf('offset_px') < 0) return;

    state.drag = {
      gid: box.dataset.gid,
      startX: event.clientX,
      startY: event.clientY,
      offset: currentValue(box.dataset.gid, 'offset_px', [0, 0]).slice()
    };
    box.classList.add('is-dragging');
  }

  function onOverlayMouseMove(event) {
    if (!state.drag) return;
    var image = el('previewImage');
    var size = canvasSize();
    if (!image.clientWidth || !size.width) return;
    // The offset is in SVG units; the drag is in screen pixels.
    var scale = size.width / image.clientWidth;
    var dx = Math.round((event.clientX - state.drag.startX) * scale);
    var dy = Math.round((event.clientY - state.drag.startY) * scale);
    var next = [state.drag.offset[0] + dx, state.drag.offset[1] + dy];
    if (!state.edits[state.drag.gid]) state.edits[state.drag.gid] = {};
    state.edits[state.drag.gid].offset_px = next;
    renderProperties();
  }

  function onOverlayMouseUp() {
    if (!state.drag) return;
    var boxes = document.querySelectorAll('.overlay-box.is-dragging');
    for (var i = 0; i < boxes.length; i++) boxes[i].classList.remove('is-dragging');
    state.drag = null;
  }

  // ── Importing ──────────────────────────────────────────────────────────

  function loadDataFiles() {
    return jsonRequest(API + '/data-files').then(function (payload) {
      var select = el('dataFileSelect');
      var files = payload.files || [];
      select.innerHTML = files.length
        ? files.map(function (file) {
            return '<option value="' + esc(file.file_id) + '">' + esc(file.filename) + '</option>';
          }).join('')
        : '<option value="">资料库里还没有表格文件</option>';
      select.disabled = !files.length;
      el('importButton').disabled = !files.length;
    });
  }

  function importFromLibrary() {
    var fileId = el('dataFileSelect').value;
    if (!fileId) {
      notify('请先选择资料库里的表格文件。', true);
      return;
    }
    var button = el('importButton');
    button.disabled = true;
    jsonRequest(API + '/projects', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: fileId })
    }).then(function (result) {
      notify('已导入并生成图件。');
      el('importPanel').hidden = true;
      return loadProjects().then(function () { return openProject(result.figure_id); });
    }).catch(function (error) {
      notify(error.message, true);
    }).then(function () {
      button.disabled = false;
    });
  }

  // ── Deleting ───────────────────────────────────────────────────────────

  function deleteCurrent() {
    if (!state.current) return;
    if (!confirm('删除这张图件？资料库里已导出的文件不会被删除。')) return;
    jsonRequest(API + '/projects/' + encodeURIComponent(state.current.figure_id), {
      method: 'DELETE',
      credentials: 'same-origin'
    }).then(function () {
      state.current = null;
      el('workspace').hidden = true;
      notify('已删除。');
      return loadProjects();
    }).catch(function (error) {
      notify(error.message, true);
    });
  }

  // ── Wiring ─────────────────────────────────────────────────────────────

  function init() {
    el('refreshButton').addEventListener('click', function () { loadProjects(); });
    el('saveButton').addEventListener('click', saveEdits);
    el('deleteButton').addEventListener('click', deleteCurrent);
    el('importButton').addEventListener('click', importFromLibrary);
    el('importToggle').addEventListener('click', function () {
      var panel = el('importPanel');
      panel.hidden = !panel.hidden;
      if (!panel.hidden) loadDataFiles();
    });
    el('objectSelect').addEventListener('change', function (event) {
      state.selected = event.target.value;
      renderOverlay();
      renderProperties();
    });
    el('propertyPanel').addEventListener('input', function (event) {
      var prop = event.target.dataset.prop;
      if (!prop) return;
      collectEdits();
      if (prop === 'fontsize' || prop === 'linewidth') {
        var out = el('propertyPanel').querySelector('[data-out="' + prop + '"]');
        if (out) out.textContent = event.target.value;
      }
    });

    el('projectList').addEventListener('click', function (event) {
      var button = event.target.closest ? event.target.closest('[data-open]') : null;
      if (button) openProject(button.getAttribute('data-open'));
    });

    el('overlay').addEventListener('mousedown', onOverlayMouseDown);
    document.addEventListener('mousemove', onOverlayMouseMove);
    document.addEventListener('mouseup', onOverlayMouseUp);

    loadProjects().then(function () {
      var wanted = new URLSearchParams(location.search).get('project');
      if (wanted) return openProject(wanted);
      return null;
    }).catch(function (error) {
      notify(error.message, true);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
