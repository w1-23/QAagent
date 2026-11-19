/* The figure workspace.
 *
 * The SVG is loaded as an <img>, never as markup. That is the same stance the
 * chat page takes: a figure is a picture, and a picture cannot script the
 * page. Picking reads paths, clips and paint order from the validated detached
 * SVG paired with the displayed image. Manifest boxes supply coarse rejection,
 * focus rings and explicit move handles; a box alone never selects an object.
 *
 * Local drafts apply appearance edits to a detached SVG document displayed
 * through a Blob IMG. Unsupported mappings explicitly await the server.
 * Downloads remain the authoritative saved render, never the local draft.
 */
(function () {
  'use strict';

  var API = '/api/v2/figures';
  var model = window.FigureEditModel;
  function applyTheme(name) {
    document.documentElement.setAttribute('data-theme',
      ['light', 'cartoon', 'dark'].indexOf(name) >= 0 ? name : 'light');
  }
  try { applyTheme(localStorage.getItem('citrus_theme')); } catch (error) { applyTheme('light'); }
  window.addEventListener('storage', function (event) {
    if (event.key === 'citrus_theme' || event.key === null) applyTheme(event.newValue);
  });

  var state = {
    projects: [],
    current: null,      // the open project's detail
    selected: '',       // the selected gid
    selectedGroup: '',  // group selection is separate from artist identity
    expanded: {},
    treeNodes: [],
    edits: {},          // only the unsaved patch, never server history
    saved: {},          // persisted overrides for the current render version
    undo: [],
    redo: [],
    saveError: '',
    preview: null,       // temporary image geometry and the exact patch rendered
    previewFlight: null,
    previewTimer: null,
    previewWanted: false,
    previewGeneration: 0,
    previewError: '', savedPreviewStatus: '',
    scene: null, sceneEdits: {}, sceneFlight: null, sceneGeneration: 0,
    blobUrl: null, projectionFrame: null, localUnsupported: [], localRevision: -1,
    displayedSvg: null, hitDocument: null, hitManifest: null, hitScene: null, hitCoarse: true, hitEpoch: 0, hitCycle: null,
    schemaRequest: 0, schema: null, importing: false,
    drag: null,
    epoch: 0,           // advances at navigation intent, before detail arrives
    editRevision: 0,
    saving: null,
    deleting: null,
    opening: null,
    listRequest: 0,
    view: { zoom: 1, x: 0, y: 0, fit: true, mode: 'select', pan: null },
    narrow: false,
    qa: null, qaFlight: null, qaError: ''
  };

  function ownsProject(figureId, epoch) {
    return state.epoch === epoch && state.current && state.current.figure_id === figureId;
  }

  function clearProject() {
    onOverlayMouseUp(null, true);
    state.epoch += 1;
    invalidatePreview(false);
    if (state.previewFlight) state.previewFlight.controller.abort();
    state.previewFlight = null;
    if (state.opening) state.opening.abort();
    state.opening = null;
    resetScene();
    state.current = null;
    state.qa = null; state.qaFlight = null; state.qaError = ''; renderQa();
    state.selected = '';
    state.selectedGroup = '';
    state.expanded = {};
    state.treeNodes = [];
    el('objectTree').replaceChildren();
    state.edits = {};
    state.saved = {};
    state.undo = [];
    state.redo = [];
    state.saveError = '';
    state.drag = null;
    state.editRevision = 0;
    state.saving = null;
    state.deleting = null;
    endPan();
    state.view = { zoom: 1, x: 0, y: 0, fit: true, mode: 'select', pan: null };
    el('deleteButton').disabled = true;
    el('workspace').hidden = true;
    el('emptyState').hidden = false;
    el('projectTitle').textContent = '未打开图件';
    el('projectMeta').textContent = '在左侧打开图件开始编辑';
    el('objectName').textContent = '未选择对象';
    el('objectCount').textContent = '';
    el('objectSearch').value = '';
    el('objectSearch').disabled = true;
    el('objectRoleFilter').value = '';
    el('objectRoleFilter').disabled = true;
    el('objectFilterStatus').hidden = true;
    el('objectSelect').disabled = true;
    el('previewImage').removeAttribute('src');
    el('prepareEditButton').hidden = true;
    var scienceNotice = el('scienceNotice');
    if (scienceNotice) { scienceNotice.hidden = true; scienceNotice.textContent = ''; }
    state.savedPreviewStatus = '';
    ['downloadSvg', 'downloadPng', 'downloadPdf', 'downloadTiff'].forEach(function(id) {
      el(id).removeAttribute('href'); el(id).setAttribute('aria-disabled', 'true');
    });
    renderCanvasInfo();
    el('overlay').replaceChildren();
    el('propertyPanel').replaceChildren();
    el('propertyScope').replaceChildren(); el('propertyScope').hidden = true;
    el('objectSelect').replaceChildren();
    renderEditState();
  }

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

  function jsonRequest(url, options, isCurrent) {
    return fetch(url, options || { credentials: 'same-origin' })
      .then(function (response) {
        if (isCurrent && !isCurrent()) throw new DOMException('Project changed', 'AbortError');
        return response.json().catch(function () { return {}; })
          .then(function (data) {
            if (isCurrent && !isCurrent()) throw new DOMException('Project changed', 'AbortError');
            if (response.status === 401) {
              location.replace('/?next=%2Ffigures');
              throw new Error('登录已失效');
            }
            if (!response.ok) {
              var detail = data.detail || data;
              var conflict = response.status === 409 && (detail.code || data.code) === 'revision_conflict';
              throw new Error(conflict ? '图件已被修改，本次修改尚未保存。请先重新打开图件核对。'
                : typeof detail === 'string' ? detail : detail.message || '请求失败');
            }
            return data;
          });
      });
  }

  // ── The project list ───────────────────────────────────────────────────

  function renderProjects() {
    var list = el('projectList');
    if (!state.projects.length) {
      list.innerHTML = '<li class="list-empty">还没有图件。在问答中生成图件，或从资料库导入表格。</li>';
      return;
    }
    list.innerHTML = state.projects.map(function (project) {
      var when = project.updated_at
        ? new Date(project.updated_at * 1000).toLocaleString()
        : '时间未知';
      var current = state.current && state.current.figure_id === project.figure_id
        ? ' is-current' : '';
      return '<li data-id="' + esc(project.figure_id) + '">' +
        '<button class="project-open' + current + '" data-open="' + esc(project.figure_id) +
        '" title="' + esc(when) + '" aria-current="' + (current ? 'true' : 'false') + '">' +
        '<span class="project-thumb" aria-hidden="true"><svg class="icon" viewBox="0 0 24 24"><path d="M3 4v16h18M6 15l4-5 4 2 6-7"/><circle cx="6" cy="15" r="1"/><circle cx="10" cy="10" r="1"/><circle cx="14" cy="12" r="1"/><circle cx="20" cy="5" r="1"/></svg></span>' +
        '<span class="project-main"><strong>' + esc(project.title || '未命名图件') + '</strong>' +
        '<small>' + esc(project.source_kind === 'script' ? '脚本图件' : '数据图件') +
        ' · ' + esc(project.objects || 0) + ' 个对象</small></span></button></li>';
    }).join('');
  }

  function loadProjects() {
    var request = ++state.listRequest;
    return jsonRequest(API + '/projects').then(function (payload) {
      if (request !== state.listRequest) return;
      state.projects = payload.projects || [];
      el('figureCount').textContent = state.projects.length
        ? state.projects.length + ' 个图件' : '';
      renderProjects();
    });
  }

  // ── Opening a project ──────────────────────────────────────────────────

  function openProject(figureId, prepareEdit) {
    if ((hasEdits() || state.saving) && !confirm('当前修改尚未全部保存，仍要打开另一图件吗？')) {
      return Promise.resolve();
    }
    clearProject();
    var epoch = state.epoch;
    var controller = new AbortController();
    state.opening = controller;
    var url = API + '/projects/' + encodeURIComponent(figureId);
    if (prepareEdit === true) url += '?prepare_edit=true';
    return jsonRequest(url, {
      credentials: 'same-origin', signal: controller.signal
    }, function () { return epoch === state.epoch && !controller.signal.aborted; }).then(function (detail) {
      if (epoch !== state.epoch || controller.signal.aborted) return;
      if (detail.figure_id !== figureId) throw new Error('图件标识不匹配');
      state.opening = null;
      state.current = detail;
      state.saved = overridesMap(detail.overrides);
      el('saveButton').disabled = detail.editable === false;
      state.selected = '';
      state.edits = {};
      el('workspace').hidden = false;
      el('projectTitle').textContent = detail.title || '图件';
      state.preview = null;
      loadSavedScene();
      renderProjectMeta();
      renderDownloads();
      el('emptyState').hidden = true;
      el('deleteButton').disabled = false;
      if (state.narrow) setSidebar('left', false);
      renderCanvasInfo();
      fitView();

      renderObjectSelect();
      renderOverlay();
      renderProperties();
      renderProjects();
      renderEditState();
    }).catch(function (error) {
      if (epoch !== state.epoch || controller.signal.aborted) return;
      state.opening = null;
      notify(error.message, true);
    });
  }

  function renderQa() {
    var current = state.current;
    // A receipt describes only its saved revision; owns() also fences responses.
    if (state.qa && (!current || state.qa.render_version !== current.render_version)) state.qa = null;
    var disabled = !current || !!state.qaFlight || !!state.saving || hasEdits() || !Number.isSafeInteger(current.render_version);
    el('verifyFigureButton').disabled = disabled; el('bundleFigureButton').disabled = disabled;
    var status = el('figureQaStatus'), checks = el('figureQaChecks'), links = el('figureQaDownloads');
    checks.replaceChildren(); links.replaceChildren();
    status.textContent = state.qaError || (state.qaFlight ? '正在检查已保存文件…' : current ? '尚未运行当前文件检查。' : '未打开图件');
    if (!current) return;
    var manifest = current.manifest || {};
    var receipts = state.qa ? [['当前文件检查', state.qa.receipt]] : [
      ['初始图件检查', manifest.initial_qa_receipt || manifest.qa_receipt],
      ['初始绘图检查', manifest.initial_nature_skill_receipt || manifest.nature_skill_receipt],
      ['编辑后检查', manifest.manual_edit_qa_receipt]];
    var labels = {pass:'通过', warn:'提示', fail:'失败', error:'错误', incomplete:'未完成',
      not_applicable:'不适用', undetermined:'待确认', not_auditable:'无法审计', review_required:'需复核',
      not_run:'未运行', attempted:'已尝试', executed:'已执行', complete:'执行完成', partial:'部分执行', not_started:'未开始',
      unsupported:'不支持', needs_attention:'需处理', measured_checks_pass:'已测量项目通过'};
    function label(value) { return labels[value] || String(value || '未标注'); }
    receipts.forEach(function(pair) {
      var receipt = pair[1]; if (!receipt || typeof receipt !== 'object') return;
      var section = document.createElement('details');
      var heading = document.createElement('summary');
      var overall = receipt.overall || {};
      heading.textContent = pair[0] + '：' + (receipt.version === 2 ?
        label(overall.execution) + ' · ' + label(overall.conclusion) : label(receipt.status));
      section.appendChild(heading);
      var entries = Array.isArray(receipt.checks) ? receipt.checks.map(function(check) { return [check && check.check, check]; }) :
        Object.keys(receipt.checks || {}).map(function(key) { return [key, receipt.checks[key]]; });
      entries.forEach(function(entry) {
        var check = entry[1]; if (!check || typeof check !== 'object') return;
        var row = document.createElement('div'); row.className = 'hint';
        row.textContent = String(entry[0] || '检查项') + '：' + (receipt.version === 2 ?
          label(check.executed) + ' · 结论：' + label(check.conclusion) : label(check.status)) +
          (check.reason_code || check.reason ? '；' + String(check.reason_code || check.reason) : '');
        if (check.artifact && typeof check.artifact === 'object') {
          var artifact = check.artifact;
          row.textContent += ' · 文件：' + String(artifact.format || artifact.role || '未标注') +
            (typeof artifact.sha256 === 'string' ? ' · SHA256 ' + artifact.sha256.slice(0, 12) : '');
        }
        section.appendChild(row);
      });
      checks.appendChild(section);
    });
    if (state.qa) {
      var overall = state.qa.receipt.overall || {};
      if (!state.qaError && !state.qaFlight) status.textContent = '当前文件：' + label(overall.execution) + ' · 结论：' + label(overall.conclusion);
      [['下载检查回执', state.qa.receipt_file], ['下载证据包', state.qa.bundle]].forEach(function(pair) {
        var file = pair[1], url = file && file.download_url;
        // Never guess a download from an id, nor accept script/external URLs.
        if (!file || typeof file.file_id !== 'string' || typeof url !== 'string' ||
            !/^\/api\/v2\/[A-Za-z0-9_./?=&%+-]+$/.test(url) || /%2f|%5c|%2e|\/\.\./i.test(url)) return;
        var link = document.createElement('a'); link.href = url; link.textContent = pair[0]; link.setAttribute('download', '');
        var row = document.createElement('div'); row.appendChild(link); links.appendChild(row);
      });
    }
  }

  function verifyFigure(bundle) {
    if (!state.current || state.qaFlight || state.saving || hasEdits() || !Number.isSafeInteger(state.current.render_version)) return Promise.resolve();
    var id = state.current.figure_id, epoch = state.epoch, revision = state.current.render_version;
    var flight = {}; state.qaFlight = flight; state.qaError = '';
    function owns() { return ownsProject(id, epoch) && state.current.render_version === revision && state.qaFlight === flight; }
    var body = {expected_revision: revision};
    if (state.qa && state.qa.render_version === revision && state.qa.receipt_file) body.supersedes_file_id = state.qa.receipt_file.file_id;
    renderQa();
    return jsonRequest(API + '/projects/' + encodeURIComponent(id) + (bundle ? '/bundle' : '/verify'), {
      method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
    }, owns).then(function(result) {
      if (!owns()) return;
      if (result.figure_id !== id || result.render_version !== revision || !result.receipt || result.receipt.version !== 2 ||
          !result.receipt.overall || !Array.isArray(result.receipt.checks)) throw new Error('检查回执与当前图件不匹配');
      state.qa = result;
    }).catch(function(error) {
      if (owns()) state.qaError = '检查未完成：' + (error.message || '请稍后重试');
    }).finally(function() {
      if (state.qaFlight === flight) { state.qaFlight = null; renderQa(); }
    });
  }

  function renderProjectMeta() {
    var detail = state.current;
    var formats = Object.keys(detail.exports || {}).map(function (format) { return format.toUpperCase(); });
    el('projectMeta').textContent = (detail.source_kind === 'script' ? '脚本图件' : '数据图件') + ' · ' +
      (detail.editable === false ? formats.join(' / ') || '无已保存格式' : manifestObjects().length + ' 个图内对象');
    el('prepareEditButton').hidden = !detail.edit_requires_preparation;
    renderScienceNotice(detail);
  }

  function renderScienceNotice(detail) {
    var notice = el('scienceNotice');
    if (!notice) return;
    var figureDocument = detail && detail.document;
    var science = figureDocument && figureDocument.science || {};
    var identity = figureDocument && figureDocument.identity || {};
    var messages = [];
    if (science.state === 'downgraded') {
      var downgrade = science.downgrade || {};
      var scope = [];
      if (downgrade.chart) scope.push(String(downgrade.chart));
      if (downgrade.panel_id) scope.push('面板 ' + String(downgrade.panel_id));
      var downgradeLabels = {
        scientific_geometry_not_exactly_replayable: '统计几何无法精确重放',
        unexpected_scientific_geometry: '成对图不应携带统计几何'
      };
      var reason = downgradeLabels[downgrade.code] || String(downgrade.code || 'unknown');
      messages.push('冻结降级：' + reason +
        '（' + String(downgrade.code || 'unknown') +
        (scope.length ? ' · ' + scope.join(' · ') : '') + '）' +
        '；该图件按现行解析路径重绘，未声称创建时冻结。');
    }
    var distribution = Array.isArray(identity.distribution) ? identity.distribution : [];
    var charts = distribution.map(function (item) { return item && item.chart; })
      .filter(function (chart) { return typeof chart === 'string' && chart; });
    if (charts.length) {
      var chartLabels = { histogram: '直方图', boxplot: '箱线图' };
      var chartText = charts.map(function (chart) { return chartLabels[chart] || String(chart); });
      messages.push('分布标记（' + chartText.join('、') + '）没有来源声明的稳定标识，' +
        '保持为当前渲染范围，不能跨重绘安全编辑。');
    }
    var marks = Array.isArray(identity.render_scoped_marks) ? identity.render_scoped_marks : [];
    if (!charts.length && marks.length) {
      messages.push('部分数据标记（' + marks.join('、') + '）仅限当前渲染身份，不能跨重绘安全编辑。');
    }
    notice.textContent = messages.join(' ');
    notice.hidden = !messages.length;
  }

  function renderDownloads() {
    renderQa();
    var base = API + '/projects/' + encodeURIComponent(state.current.figure_id);
    ['svg', 'png', 'pdf', 'tiff'].forEach(function (format) {
      var link = el('download' + format.charAt(0).toUpperCase() + format.slice(1));
      var available = !!(state.current.exports || {})[format];
      link.setAttribute('aria-disabled', available ? 'false' : 'true');
      if (available) link.href = base + '/export/' + format;
      else link.removeAttribute('href');
    });
  }

  // ── The overlay ────────────────────────────────────────────────────────

  function manifestObjects() {
    if (!state.current || state.current.editable === false || !state.current.manifest) return [];
    return state.current.manifest.objects || [];
  }

  function canvasSize() {
    var size = (state.current && state.current.manifest
      && state.current.manifest.figure_size_px) || [0, 0];
    if (state.current && (!size[0] || !size[1])) {
      var image = el('previewImage');
      size = [image.naturalWidth / POINT_TO_CSS, image.naturalHeight / POINT_TO_CSS];
    }
    return { width: Number(size[0]) || 0, height: Number(size[1]) || 0 };
  }

  // Manifest coordinates are SVG points (72/in), not export raster pixels.
  var POINT_TO_CSS = 96 / 72;
  function viewBounds() {
    var node = el('viewport');
    return node.getBoundingClientRect ? node.getBoundingClientRect()
      : { left: 0, top: 0, width: node.clientWidth || 800, height: node.clientHeight || 600 };
  }
  function renderGuides() {
    var size = canvasSize();
    el('gridOverlay').hidden = !state.current || !el('gridToggle').checked;
    el('gridOverlay').style.backgroundSize = (10 * 96 / 25.4) + 'px ' + (10 * 96 / 25.4) + 'px';
    ['rulerTop', 'rulerLeft'].forEach(function (id, axis) {
      var ruler = el(id);
      ruler.hidden = !state.current || !el('rulerToggle').checked;
      ruler.replaceChildren();
      if (ruler.hidden) return;
      var length = (axis ? size.height : size.width) * 25.4 / 72;
      var steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];
      var step = steps.filter(function (value) { return value * 96 / 25.4 * state.view.zoom >= 38; })[0] || 1000;
      for (var mm = 0; mm < length && mm / step < 200; mm += step) {
        var tick = document.createElement('span');
        tick.textContent = String(mm);
        tick.style[axis ? 'top' : 'left'] = (mm * 96 / 25.4) + 'px';
        ruler.appendChild(tick);
      }
    });
  }
  function renderView() {
    var size = canvasSize(), view = state.view, workspace = el('workspace');
    workspace.style.width = (size.width * POINT_TO_CSS) + 'px';
    workspace.style.height = (size.height * POINT_TO_CSS) + 'px';
    workspace.style.transform = 'translate(' + view.x + 'px, ' + view.y + 'px) scale(' + view.zoom + ')';
    if (workspace.style.setProperty) workspace.style.setProperty('--view-inverse', String(1 / view.zoom));
    el('zoomPercent').value = String(Math.round(view.zoom * 100));
    el('viewport').classList.toggle('is-pan-mode', view.mode === 'pan');
    el('viewport').classList.toggle('is-panning', !!view.pan);
    ['select', 'pan'].forEach(function (mode) {
      el(mode + 'Mode').classList.toggle('is-active', view.mode === mode);
      el(mode + 'Mode').setAttribute('aria-pressed', String(view.mode === mode));
    });
    ['zoomPercent', 'zoomIn', 'zoomOut', 'fitView', 'selectMode', 'panMode', 'gridToggle', 'rulerToggle'].forEach(function (id) {
      el(id).disabled = !state.current || !!state.drag;
    });
    renderGuides();
  }
  function fitView() {
    if (!state.current || state.drag || state.view.pan) return;
    var size = canvasSize(), bounds = viewBounds();
    if (!size.width || !size.height) return;
    var zoom = Math.min((bounds.width - 80) / (size.width * POINT_TO_CSS),
      (bounds.height - 80) / (size.height * POINT_TO_CSS), 2);
    state.view.zoom = Math.max(.1, zoom);
    state.view.x = (bounds.width - size.width * POINT_TO_CSS * state.view.zoom) / 2;
    state.view.y = (bounds.height - size.height * POINT_TO_CSS * state.view.zoom) / 2;
    state.view.fit = true;
    renderView();
  }
  function zoomView(zoom, x, y) {
    if (!state.current || state.drag || state.view.pan || !Number.isFinite(zoom) || zoom <= 0) { renderView(); return; }
    var bounds = viewBounds(), view = state.view;
    x = x === undefined ? bounds.width / 2 : x;
    y = y === undefined ? bounds.height / 2 : y;
    zoom = Math.max(.1, Math.min(8, zoom));
    var ratio = zoom / view.zoom;
    view.x = x - (x - view.x) * ratio;
    view.y = y - (y - view.y) * ratio;
    view.zoom = zoom; view.fit = false;
    renderView();
  }
  function renderCanvasInfo() {
    var size = canvasSize();
    var dimensions = state.current ? (size.width * 25.4 / 72).toFixed(1) + ' × ' +
      (size.height * 25.4 / 72).toFixed(1) + ' mm' : '未打开图件';
    el('canvasDimensions').textContent = dimensions;
    el('canvasSizeBadge').textContent = state.current ? dimensions : '';
    el('canvasOrientation').textContent = !state.current ? '—' : size.width === size.height ? '正方形' : size.width > size.height ? '横向' : '纵向';
    renderView();
  }
  function setViewMode(mode) {
    if (state.drag) return;
    endPan();
    state.view.mode = mode;
    renderView();
    if (state.current) el('viewport').focus();
  }
  function beginPan(event) {
    if (!state.current || state.drag || state.view.pan ||
        (event.button !== 1 && (state.view.mode !== 'pan' || (event.button !== undefined && event.button !== 0)))) return;
    event.preventDefault();
    state.view.pan = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY,
      x: state.view.x, y: state.view.y };
    state.view.fit = false;
    var viewport = el('viewport');
    if (event.pointerId !== undefined && viewport.setPointerCapture) viewport.setPointerCapture(event.pointerId);
    viewport.focus();
    renderView();
  }
  function movePan(event) {
    var pan = state.view.pan;
    if (!pan || (event.pointerId !== undefined && event.pointerId !== pan.pointerId)) return;
    state.view.x = pan.x + event.clientX - pan.startX;
    state.view.y = pan.y + event.clientY - pan.startY;
    renderView();
  }
  function endPan(event) {
    var pan = state.view.pan, viewport = el('viewport');
    if (!pan || (event && event.pointerId !== undefined && event.pointerId !== pan.pointerId)) return;
    state.view.pan = null;
    if (pan.pointerId !== undefined && viewport.hasPointerCapture && viewport.hasPointerCapture(pan.pointerId)) viewport.releasePointerCapture(pan.pointerId);
    renderView();
  }
  function wheelView(event) {
    if (!state.current) return;
    event.preventDefault();
    if (state.drag || state.view.pan) return;
    var bounds = viewBounds();
    var unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? bounds.height : 1;
    if (event.ctrlKey || event.metaKey) {
      zoomView(state.view.zoom * Math.exp(-event.deltaY * unit * .002), event.clientX - bounds.left, event.clientY - bounds.top);
    } else {
      state.view.x -= (event.shiftKey ? event.deltaY : event.deltaX || 0) * unit;
      state.view.y -= (event.shiftKey ? 0 : event.deltaY) * unit;
      state.view.fit = false; renderView();
    }
  }
  function setSidebar(side, open) {
    var left = side === 'left', name = left ? 'Left' : 'Right';
    el(side + 'Sidebar').hidden = !open;
    el('toggle' + name).setAttribute('aria-expanded', String(open));
    el('workbench').classList.toggle('is-' + side + '-open', open);
    if (open && state.narrow) {
      var other = left ? 'right' : 'left';
      el(other + 'Sidebar').hidden = true;
      el('toggle' + (left ? 'Right' : 'Left')).setAttribute('aria-expanded', 'false');
      el('workbench').classList.remove('is-' + other + '-open');
    }
    if (state.view.fit) fitView();
  }
  function resizeView() {
    var narrow = (window.innerWidth || 1200) < 1000;
    if (narrow !== state.narrow) {
      state.narrow = narrow;
      setSidebar('left', !narrow); setSidebar('right', !narrow);
    }
    if (state.view.fit) fitView();
  }
  function selectTab(name, focus) {
    ['properties', 'canvas'].forEach(function (item) {
      var active = item === name, tab = el(item + 'Tab');
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', String(active)); tab.setAttribute('tabindex', active ? '0' : '-1');
      el(item + 'Section').hidden = !active;
      if (active && focus) tab.focus();
    });
  }

  function canMove(object) {
    return !!(state.current && state.current.editable !== false && object &&
      ['title', 'xlabel', 'ylabel', 'legend', 'annot', 'axes', 'ticklabel', 'colorbar'].indexOf(object.role) >= 0 &&
      (object.editable || []).indexOf('offset_px') >= 0);
  }

  function focusSelected() {
    var boxes = el('overlay').children;
    for (var i = 0; i < boxes.length; i++) {
      if (boxes[i].dataset.gid === state.selected) boxes[i].focus();
    }
  }

  function renderOverlay() {
    var overlay = el('overlay');
    var restoreFocus = document.activeElement && overlay.contains(document.activeElement);
    var size = canvasSize();
    var existing = Array.prototype.slice.call(overlay.children);
    var byGid = new Map(existing.map(function (box) { return [box.dataset.gid, box]; }));
    existing.forEach(function (box) { box.hidden = true; });
    if (!size.width || !size.height) return;
    var displayed = state.scene && state.scene.manifest && state.scene.manifest.objects || manifestObjects();
    var ordered = displayed.slice().sort(function (a, b) {
      function area(item) { var r = item.bbox; return r ? Math.abs((r.x1 - r.x0) * (r.y1 - r.y0)) : 0; }
      function layer(item) { return ['figure', 'axes', 'series'].indexOf(item.role) >= 0 ? 0 : 1; }
      return layer(a) - layer(b) || area(b) - area(a);
    });
    ordered.forEach(function (object, index) {
      if (!object.bbox || currentValue(object.gid, 'visible', true) === false) return;
      var box = byGid.get(object.gid);
      var fresh = !box;
      if (fresh) {
        box = document.createElement('div');
        var handle = document.createElement('button');
        handle.className = 'move-handle'; handle.dataset.moveGid = object.gid;
        handle.setAttribute('data-move-gid', object.gid);
        handle.setAttribute('tabindex', '-1'); handle.setAttribute('aria-label', '拖动已选对象');
        box.appendChild(handle);
      }
      box.hidden = false;
      // Smaller targets sit above whole-series and axes bounds. Nodes stay
      // mounted throughout a gesture, including implicit touch capture.
      box.style.zIndex = String(index + 1);
      box.style.pointerEvents = 'none';
      box.className = 'overlay-box';
      if (object.gid === state.selected) box.classList.add('is-selected');
      if (canMove(object)) box.classList.add('is-draggable');
      box.dataset.gid = object.gid;
      box.title = object.gid + '（' + object.role + '）';
      box.setAttribute('role', 'button');
      box.setAttribute('tabindex', '0');
      box.setAttribute('aria-label', objectLabel(object));
      box.setAttribute('aria-pressed', object.gid === state.selected ? 'true' : 'false');
      var savedOffset = savedValue(object.gid, 'offset_px', [0, 0]);
      var renderedEdits = state.scene && state.sceneEdits && state.sceneEdits[object.gid];
      var renderedOffset = renderedEdits && renderedEdits.offset_px !== undefined ? renderedEdits.offset_px : savedOffset;
      var offset = canMove(object) ? currentValue(object.gid, 'offset_px', [0, 0]) : renderedOffset;
      box.style.left = ((object.bbox.x0 + offset[0] - renderedOffset[0]) / size.width * 100) + '%';
      box.style.top = ((object.bbox.y0 + offset[1] - renderedOffset[1]) / size.height * 100) + '%';
      box.style.width = ((object.bbox.x1 - object.bbox.x0) / size.width * 100) + '%';
      box.style.height = ((object.bbox.y1 - object.bbox.y0) / size.height * 100) + '%';
      if (fresh) overlay.appendChild(box);
    });
    if (restoreFocus) focusSelected();
  }

  // Categories are selection scopes; disclosure never changes the selection.
  function objectGroups() {
    var objects = manifestObjects();
    function leaf(object) { return { id: object.gid, object: object, label: objectLabel(object), members: [object], children: [] }; }
    function group(id, label, children, collapsed) {
      return { id: id, label: label, children: children, collapsed: !!collapsed,
        members: children.reduce(function (all, child) { return all.concat(child.members); }, []) };
    }
    var series = objects.filter(function (o) { return o.role === 'series'; });
    var data = series.map(function (object) {
      var node = leaf(object);
      node.children = objects.filter(function (o) { return o.parent_gid === object.gid; }).map(leaf);
      node.collapsed = true;
      return node;
    });
    data = data.concat(objects.filter(function (o) {
      return ['point', 'bar'].indexOf(o.role) >= 0 && !series.some(function (s) { return s.gid === o.parent_gid; });
    }).map(leaf));
    var ticks = {};
    objects.filter(function (o) { return o.role === 'ticklabel'; }).forEach(function (object) {
      var locator = object.locator || {};
      var axis = locator.axis || (/tick_y/.test(object.gid) ? 'y' : 'x');
      var index = locator.axes_index === undefined ? 0 : locator.axes_index;
      var key = 'ticks:' + index + ':' + axis;
      if (!ticks[key]) ticks[key] = group(key, '子图 ' + (Number(index) + 1) + ' · ' + axis.toUpperCase() + ' 轴刻度', [], true);
      ticks[key].children.push(leaf(object)); ticks[key].members.push(object);
    });
    return [
      group('text', '文字', objects.filter(function (o) { return ['title', 'xlabel', 'ylabel', 'annot'].indexOf(o.role) >= 0; }).map(leaf)),
      group('series', '数据系列', data),
      group('legend', '图例', objects.filter(function (o) { return o.role === 'legend'; }).map(leaf)),
      group('grid', '网格线', objects.filter(function (o) { return o.role === 'grid'; }).map(leaf)),
      group('colorbar', '色条', objects.filter(function (o) { return o.role === 'colorbar'; }).map(leaf)),
      group('axes', '坐标轴', objects.filter(function (o) { return ['axes', 'spine', 'figure'].indexOf(o.role) >= 0; }).map(leaf).concat(Object.keys(ticks).map(function (key) { return ticks[key]; })))
    ].filter(function (node) { return node.members.length; });
  }

  function selectedMembers() {
    if (!state.selectedGroup) return selectedObject() ? [selectedObject()] : [];
    var found;
    function visit(nodes) { nodes.forEach(function (node) { if (!node.object && node.id === state.selectedGroup) found = node; visit(node.children); }); }
    visit(objectGroups());
    return found ? found.members : [];
  }

  function propertyObject() {
    if (!state.selectedGroup) return selectedObject();
    var members = selectedMembers();
    if (!members.length) return null;
    return { gid: null, editable: (members[0].editable || []).filter(function (prop) {
      return ['text', 'label', 'offset_px'].indexOf(prop) < 0 && members.every(function (object) { return (object.editable || []).indexOf(prop) >= 0; });
    }) };
  }

  function renderObjectTree(matches, filtering) {
    var tree = el('objectTree'), active = document.activeElement;
    var focusKey = active && tree.contains(active) ? active.getAttribute('data-tree-key') : null;
    var scroll = tree.scrollTop;
    tree.replaceChildren(); state.treeNodes = [];
    var matching = new Set(matches.map(function (o) { return o.gid; }));
    function render(nodes, container, depth, parent) {
      nodes.forEach(function (node) {
        var selfMatch = !!node.object && matching.has(node.object.gid);
        function containsMatch(item) { return (item.object && matching.has(item.object.gid)) || item.children.some(containsMatch); }
        if (filtering && !containsMatch(node)) return;
        var item = document.createElement('li'), row = document.createElement('div');
        row.className = 'object-tree-row'; row.style.paddingLeft = (depth * 12) + 'px';
        var key = (node.object ? 'object:' : 'group:') + node.id;
        var expanded = filtering || (state.expanded[key] === undefined ? !node.collapsed : state.expanded[key]);
        if (node.children.length) {
          var disclosure = document.createElement('button'); disclosure.type = 'button'; disclosure.className = 'object-disclosure';
          disclosure.setAttribute('data-expand', key); disclosure.setAttribute('data-tree-key', 'expand:' + key);
          disclosure.setAttribute('aria-label', (expanded ? '收起' : '展开') + node.label);
          disclosure.setAttribute('aria-expanded', String(expanded)); disclosure.textContent = expanded ? '▾' : '▸';
          disclosure.disabled = !!filtering;
          if (filtering) disclosure.title = '筛选期间展开匹配对象的上级分组';
          row.appendChild(disclosure);
        } else { var spacer = document.createElement('span'); spacer.className = 'object-disclosure-spacer'; row.appendChild(spacer); }
        var button = document.createElement('button'); button.type = 'button'; button.className = 'object-tree-select';
        button.setAttribute(node.object ? 'data-gid' : 'data-group', node.id);
        button.setAttribute('data-tree-key', key);
        var selected = node.object ? !state.selectedGroup && state.selected === node.id : state.selectedGroup === node.id;
        button.setAttribute('aria-pressed', String(selected));
        button.classList.toggle('is-selected', selected);
        button.classList.toggle('is-context', !!filtering && !!node.object && !selfMatch);
        var hidden = node.object && currentValue(node.id, 'visible', true) === false;
        button.textContent = node.label + (hidden ? '（已移除）' : '') + (!node.object ? '（' + node.members.length + '）' : '');
        button.title = node.object ? node.id : '批量修改组内 ' + node.members.length + ' 个对象的共同样式';
        row.appendChild(button); item.appendChild(row); container.appendChild(item);
        state.treeNodes.push({ id: node.id, key: key, parent: parent, button: button, expanded: expanded, filtering: filtering, children: node.children.length });
        if (node.children.length && expanded) {
          var list = document.createElement('ul'); item.appendChild(list); render(node.children, list, depth + 1, key);
        }
      });
    }
    render(objectGroups(), tree, 0, null);
    if (!tree.children.length) { var empty = document.createElement('li'); empty.className = 'list-empty'; empty.textContent = manifestObjects().length ? '没有匹配的对象' : '打开图件后查看对象'; tree.appendChild(empty); }
    tree.scrollTop = scroll;
    if (focusKey) Array.prototype.some.call(tree.querySelectorAll('[data-tree-key]'), function (button) {
      if (button.getAttribute('data-tree-key') !== focusKey) return false;
      button.focus(); return true;
    });
  }

  function onTreeKeyDown(event) {
    var button = event.target.closest ? event.target.closest('[data-tree-key]') : null;
    if (!button) return;
    var key = button.getAttribute('data-expand') || button.getAttribute('data-tree-key');
    var index = state.treeNodes.findIndex(function (node) { return node.key === key; });
    var node = state.treeNodes[index], next;
    if (!node) return;
    if (event.key === 'ArrowDown') next = state.treeNodes[index + 1];
    else if (event.key === 'ArrowUp') next = state.treeNodes[index - 1];
    else if (event.key === 'Home') next = state.treeNodes[0];
    else if (event.key === 'End') next = state.treeNodes[state.treeNodes.length - 1];
    else if (event.key === 'ArrowRight' && node.children) {
      if (!node.expanded) { state.expanded[key] = true; renderObjectSelect(); }
      else next = state.treeNodes[index + 1];
    } else if (event.key === 'ArrowLeft') {
      if (node.children && node.expanded && !node.filtering) { state.expanded[key] = false; renderObjectSelect(); }
      else next = state.treeNodes.filter(function (item) { return item.key === node.parent; })[0];
    } else return;
    event.preventDefault(); if (next) next.button.focus();
  }

  function renderObjectSelect() {
    var select = el('objectSelect'), search = el('objectSearch'), filter = el('objectRoleFilter');
    var objects = manifestObjects();
    var roles = Array.from(new Set(objects.map(function (object) { return object.role; })));
    var role = roles.indexOf(filter.value) >= 0 ? filter.value : '';
    filter.innerHTML = '<option value="">全部类型</option>' + roles.map(function (name) {
      return '<option value="' + esc(name) + '">' + esc(ROLE_LABELS[name] || name || '其他') + '</option>';
    }).join('');
    filter.value = role;
    search.disabled = filter.disabled = !objects.length;
    var query = search.value.trim().toLowerCase();
    var terms = query ? query.split(/\s+/) : [];
    var matches = objects.filter(function (object) {
      var text = [objectLabel(object), object.label || '', object.role, ROLE_LABELS[object.role] || '', object.gid].join(' ').toLowerCase();
      return (!role || object.role === role) && terms.every(function (term) { return text.indexOf(term) >= 0; });
    });
    select.innerHTML = roles.map(function (name) {
      var group = matches.filter(function (object) { return object.role === name; });
      if (!group.length) return '';
      return '<optgroup label="' + esc(ROLE_LABELS[name] || name || '其他') + '（' + group.length + '）">' + group.map(function (object) {
        return '<option value="' + esc(object.gid) + '" title="' + esc(object.gid) + '">' +
          esc(objectLabel(object)) + (currentValue(object.gid, 'visible', true) === false ? '（已移除）' : '') + '</option>';
      }).join('') + '</optgroup>';
    }).join('') || '<option value="">' + (objects.length ? '没有匹配的对象' : state.current ? '这张图没有可编辑的对象' : '打开图件后查看对象') + '</option>';
    select.disabled = !matches.length;
    var selectedVisible = matches.some(function (object) { return object.gid === state.selected; });
    select.value = selectedVisible ? state.selected : '';
    el('objectCount').textContent = objects.length ? (query || role ? matches.length + '/' + objects.length : String(objects.length)) : '';
    var status = el('objectFilterStatus');
    status.textContent = objects.length && !matches.length ? '没有匹配对象，请修改搜索或类型。' :
      state.selected && !selectedVisible ? '当前选中对象不在筛选结果中，右侧保留当前属性。' : '';
    status.hidden = !status.textContent;
    if (state.selectedGroup) { status.textContent = '组选择会修改全部成员的共同样式，包含筛选之外的成员。'; status.hidden = false; }
    renderObjectTree(matches, !!(query || role));
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
    legend: '图例', annot: '注释', series: '数据系列', axes: '子图', figure: '画布',
    point: '数据点', bar: '柱形', spine: '轴线', ticklabel: '刻度标签',
    grid: '网格线', errorbar: '误差棒', colorbar: '色条'
  };

  function objectLabel(object) {
    var label = String(currentValue(object.gid, ['title', 'xlabel', 'ylabel', 'annot'].indexOf(object.role) >= 0 ? 'text' : 'label', object.label || '') || '').trim();
    if (label && label !== object.gid && label !== object.role && label.charAt(0) !== '_') return label;
    var siblings = manifestObjects().filter(function (item) { return item.role === object.role; });
    var index = siblings.findIndex(function (item) { return item.gid === object.gid; }) + 1;
    return (ROLE_LABELS[object.role] || '对象') + (siblings.length > 1 ? ' ' + index : '');
  }

  // Original control icons, built only from fixed paths; never manifest markup.
  function styleGlyph(prop, value) {
    var shapes = { o: '<circle cx="12" cy="12" r="6"/>', s: '<rect x="6" y="6" width="12" height="12"/>',
      '^': '<path d="M12 5 20 19H4Z"/>', v: '<path d="m4 5 8 14 8-14Z"/>', D: '<path d="m12 3 8 9-8 9-8-9Z"/>',
      P: '<path d="M9 3h6v6h6v6h-6v6H9v-6H3V9h6Z"/>', X: '<path d="m5 2 7 7 7-7 3 3-7 7 7 7-3 3-7-7-7 7-3-3 7-7-7-7Z"/>',
      '+': '<path d="M12 4v16M4 12h16" fill="none" stroke="currentColor" stroke-width="2"/>',
      x: '<path d="m5 5 14 14M19 5 5 19" fill="none" stroke="currentColor" stroke-width="2"/>', '.': '<circle cx="12" cy="12" r="3"/>' };
    if (value === 'None') return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 19 14-14" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';
    if (prop === 'marker') return '<svg viewBox="0 0 24 24" aria-hidden="true">' + (shapes[value] || '') + '</svg>';
    var dash = { '-': '', '--': '6 3', '-.': '7 3 1 3', ':': '1 3' }[value];
    return '<svg viewBox="0 0 32 24" aria-hidden="true"><path d="M2 12h28" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="' + (dash || '') + '"/></svg>';
  }

  function styleOptions(object, prop) {
    var key = prop === 'marker' ? 'marker_options' : 'linestyle_options';
    var options = state.current.manifest[key] || [];
    var members = object.gid === null ? selectedMembers() : [object];
    return options.filter(function (value) {
      return members.every(function (member) { return !member[key] || member[key].indexOf(value) >= 0; });
    });
  }

  function propertyNames(object) {
    return { text: '文字内容', label: object.role === 'colorbar' ? '色条标签' : '系列名称',
      color: object.artist === 'Line2D' ? '线条颜色' : '颜色', facecolor: '填色', edgecolor: '边色',
      capsize: '端帽大小', markerfacecolor: '标记填色', markeredgecolor: '标记边色',
      markeredgewidth: '标记边宽', alpha: '不透明度', ncols: '图例列数', loc: '图例位置',
      fontstyle: '字形', horizontalalignment: '文字对齐', marker: '标记形状', markersize: '标记大小',
      linestyle: '线型', fontfamily: '字体', fontweight: '字重', fontsize: '字号', linewidth: '线宽',
      frameon: '边框', visible: '显示状态', offset_px: '位置', offset_x: '水平偏移', offset_y: '垂直偏移' };
  }

  function renderPropertyScope() {
    var scope = el('propertyScope'), object = selectedObject(), members = selectedMembers();
    scope.replaceChildren();
    if (state.selectedGroup) {
      scope.textContent = '编辑范围：' + members.length + ' 个对象的共同属性';
    } else if (object) {
      scope.textContent = '编辑范围：' + (ROLE_LABELS[object.role] || '对象');
      var parentId = object.parent_gid || (object.locator || {}).parent_gid;
      var parent = manifestObjects().filter(function (item) { return item.gid === parentId; })[0];
      if (parent && parent.gid !== object.gid) {
        var button = document.createElement('button');
        button.type = 'button'; button.className = 'property-parent';
        button.setAttribute('data-select-parent', parent.gid);
        button.textContent = '选择所属' + (ROLE_LABELS[parent.role] || '对象') + '：' + objectLabel(parent);
        scope.appendChild(button);
      }
    }
    scope.hidden = !state.selectedGroup && !object;
  }

  function renderPropertyResets() {
    var controls = el('propertyDraftControls'), object = propertyObject(), members = selectedMembers();
    var props = object && state.current && state.current.editable !== false ? (object.editable || []).filter(function (prop) {
      return members.some(function (member) { return state.edits[member.gid] && state.edits[member.gid][prop] !== undefined; });
    }) : [];
    controls.hidden = !props.length;
    var names = object ? propertyNames(object) : {};
    var markup = props.length ? '<h3>未保存的属性</h3><p class="property-empty">只恢复当前选择的这一项，其他修改保留。</p>' + props.map(function (prop) {
      return '<div class="property-draft-row"><span>' + esc(names[prop] || prop) + '</span><button type="button" data-reset-prop="' + esc(prop) +
        '" aria-label="恢复' + esc(names[prop] || prop) + '的已保存值"' + (state.saving || state.deleting || state.drag ? ' disabled' : '') + '>恢复已保存值</button></div>';
    }).join('') : '';
    if (controls.draftMarkup !== markup) {
      var active = document.activeElement;
      var focused = active && controls.contains(active) ? active.getAttribute('data-reset-prop') : null;
      controls.innerHTML = markup; controls.draftMarkup = markup;
      var replacement = focused && controls.querySelector('[data-reset-prop="' + focused + '"]');
      if (replacement) replacement.focus();
    }
  }

  function restoreProperty(prop) {
    var object = propertyObject();
    if (!state.current || state.current.editable === false || state.saving || state.deleting || state.drag ||
        !object || (object.editable || []).indexOf(prop) < 0) return;
    var next = copy(state.edits);
    selectedMembers().forEach(function (member) {
      if (next[member.gid]) delete next[member.gid][prop];
    });
    next = normalizeEdits(next);
    if (same(next, state.edits)) return;
    remember(state.edits, state.editRevision);
    state.edits = next; state.editRevision += 1;
    renderProperties(); renderObjectSelect(); renderOverlay(); renderEditState(); schedulePreview();
    var field = el('propertyPanel').querySelector('[data-prop="' + (prop === 'offset_px' ? 'offset_x' : prop) + '"]');
    if (field) field.focus();
  }

  function savedMarkerFollowsLine(gid, prop) {
    if (prop !== 'markerfacecolor' && prop !== 'markeredgecolor') return false;
    var object = manifestObjects().filter(function (item) { return item.gid === gid; })[0];
    var flag = prop === 'markerfacecolor' ? 'markerface_auto' : 'markeredge_auto';
    return !!object && object[flag] === true && !(state.saved[gid] && state.saved[gid][prop] !== undefined);
  }

  function markerFollowsLine(gid, prop) {
    return savedMarkerFollowsLine(gid, prop) && !(state.edits[gid] && state.edits[gid][prop] !== undefined);
  }

  function renderProperties() {
    renderPropertyScope(); renderPropertyResets();
    var panel = el('propertyPanel');
    var selected = selectedObject();
    el('objectName').textContent = state.selectedGroup ? '批量编辑 · ' + selectedMembers().length + ' 个对象' : selected ? objectLabel(selected) : '未选择对象';
    if (state.current && state.current.editable === false) {
      panel.innerHTML = '<p class="property-empty">这张图件目前只读，可预览和下载，暂不支持样式修改。</p>';
      return;
    }
    var object = propertyObject();
    if (!object) {
      panel.innerHTML = '<p class="property-empty">选择一个对象后可调整它的样式。</p>';
      return;
    }
    var editable = object.editable || [];
    if (!editable.length) {
      panel.innerHTML = '<p class="property-empty">这个对象没有可修改的属性。</p>';
      return;
    }
    var rows = state.selectedGroup ? ['<p class="property-empty">只显示全部成员共有的样式。不同值留空；一次调整可整体撤销。文字内容与系列名称请单独选择对象修改。</p>'] : [];
    ['text', 'label'].forEach(function (prop) {
      if (editable.indexOf(prop) < 0 || state.selectedGroup) return;
      var content = currentValue(object.gid, prop, '');
      rows.push('<div class="property-row property-content"><label>' + (prop === 'text' ? '文字内容' : object.role === 'colorbar' ? '色条标签' : '系列名称') + '</label>' +
        (prop === 'text' ? '<textarea data-prop="text" maxlength="2000" rows="3">' + esc(content) + '</textarea>'
          : '<input type="text" data-prop="label" maxlength="2000" value="' + esc(content) + '">') + '</div>');
      if (prop === 'label' && object.role !== 'colorbar') rows.push('<p class="property-empty">系列名称会用于对应图例；预览后请核对图例。</p>');
    });

    ['color', 'facecolor', 'edgecolor', 'markerfacecolor', 'markeredgecolor'].forEach(function (prop) {
      if (editable.indexOf(prop) < 0) return;
      var color = currentValue(object.gid, prop);
      var knownColor = typeof color === 'string' && /^#[0-9a-f]{6}$/i.test(color);
      rows.push('<div class="property-row"><label>' + propertyNames(object)[prop] + '</label>' + (knownColor
        ? '<input type="color" data-prop="' + prop + '" value="' + esc(color) + '">'
        : '<input type="text" data-prop="' + prop + '" value="" placeholder="#RRGGBB" maxlength="7" aria-describedby="' + prop + 'State">') + '</div>');
      if (!knownColor) rows.push('<p class="property-empty" id="' + prop + 'State">原色未提供或包含多种颜色；输入十六进制颜色后统一颜色。</p>');
      if (selectedMembers().every(function (member) { return markerFollowsLine(member.gid, prop); })) {
        rows.push('<p class="property-empty" id="' + prop + 'Mode">当前随线条颜色；单独设色后独立保存。</p>');
      }
    });
    if (editable.indexOf('fontfamily') >= 0) {
      var family = currentValue(object.gid, 'fontfamily', '');
      var fontLabels = { 'Microsoft YaHei': '微软雅黑', 'Microsoft YaHei UI': '微软雅黑 UI', SimSun: '宋体', SimHei: '黑体', KaiTi: '楷体', FangSong: '仿宋', 'Noto Sans CJK SC': '思源黑体 / Noto Sans CJK SC' };
      var families = (state.current.manifest.font_families || []).filter(function (name) { return typeof name === 'string'; });
      rows.push('<div class="property-row"><label>字体</label><select data-prop="fontfamily"' + (!families.length ? ' disabled' : '') + '>' +
        '<option value="">' + esc(fontLabels[family] || family || (families.length ? '选择字体' : '暂无可用字体')) + '</option>' +
        families.map(function (name) { return '<option value="' + esc(name) + '"' + (name === family ? ' selected' : '') + '>' + esc(fontLabels[name] || name) + '</option>'; }).join('') + '</select></div>');
    }
    if (editable.indexOf('fontweight') >= 0) {
      var weight = currentValue(object.gid, 'fontweight', '');
      var weights = { light: '细体', normal: '常规', medium: '中等', semibold: '半粗', bold: '粗体', heavy: '特粗', black: '极粗' };
      rows.push('<div class="property-row"><label>字重</label><select data-prop="fontweight"><option value="">选择字重</option>' +
        Object.keys(weights).map(function (name) { return '<option value="' + name + '"' + (name === weight ? ' selected' : '') + '>' + weights[name] + '</option>'; }).join('') + '</select></div>');
    }
    [['fontstyle', '字形', 'font_styles', {normal: '常规', italic: '斜体', oblique: '倾斜'}],
      ['horizontalalignment', '文字对齐', 'horizontalalignment_options', {left: '左对齐', center: '居中', right: '右对齐'}],
      ['loc', '图例位置', 'legend_loc_options', {best: '自动', 'upper right': '右上', 'upper left': '左上', 'lower left': '左下', 'lower right': '右下', right: '右侧', 'center left': '左中', 'center right': '右中', 'lower center': '下中', 'upper center': '上中', center: '居中'}]
    ].forEach(function (spec) {
      var prop = spec[0];
      if (editable.indexOf(prop) < 0) return;
      var options = state.current.manifest[spec[2]] || [], value = currentValue(object.gid, prop);
      rows.push('<div class="property-row"><label>' + spec[1] + '</label><select data-prop="' + prop + '"' + (!options.length ? ' disabled' : '') + '><option value="">选择' + spec[1] + '</option>' + options.map(function (name) {
        return '<option value="' + esc(name) + '"' + (name === value ? ' selected' : '') + '>' + esc(spec[3][name] || name) + '</option>';
      }).join('') + '</select></div>');
    });
    ['fontsize', 'linewidth', 'markeredgewidth', 'alpha', 'capsize', 'ncols'].forEach(function (prop) {
      if (editable.indexOf(prop) < 0) return;
      var spec = NUMBER_PROPS[prop], value = currentValue(object.gid, prop);
      rows.push('<div class="property-row"><label>' + spec[3] + '</label><input type="number" data-prop="' + prop + '" min="' + spec[0] + '" max="' + spec[1] + '" step="' + spec[2] + '" placeholder="不同或未提供" value="' + (Number.isFinite(value) ? esc(value) : '') + '"></div>');
    });
    ['marker', 'linestyle'].forEach(function (prop) {
      if (editable.indexOf(prop) < 0) return;
      var chosen = currentValue(object.gid, prop, '');
      var options = styleOptions(object, prop);
      var labels = prop === 'marker' ? { o: '圆形', s: '方形', '^': '上三角', v: '下三角', D: '菱形', P: '实心加号', X: '实心叉号', '+': '加号', x: '叉号', '.': '小点', None: '无标记' }
        : { '-': '实线', '--': '虚线', '-.': '点划线', ':': '点线', None: '无线条' };
      rows.push('<fieldset class="style-picker"><legend>' + (prop === 'marker' ? '标记形状' : '线型') + '</legend><div class="style-options">' +
        options.map(function (name) { return '<button type="button" class="style-option" data-prop="' + prop + '" data-value="' + esc(name) + '" value="' + esc(name) + '" aria-pressed="' + (name === chosen) + '" title="' + esc(labels[name] || name) + '" aria-label="' + esc(labels[name] || name) + '">' + styleGlyph(prop, name) + '<span>' + esc(labels[name] || name) + '</span></button>'; }).join('') + '</div>' + (!options.length ? '<p class="property-empty">暂无可用样式</p>' : '') + '</fieldset>');
    });
    if (editable.indexOf('markersize') >= 0) {
      var markerSize = currentValue(object.gid, 'markersize');
      var knownSize = Number.isFinite(markerSize);
      rows.push('<div class="property-row"><label>标记大小</label><input type="number" data-prop="markersize" min="1" max="24" step="0.5" placeholder="未提供" value="' + (knownSize ? esc(markerSize) : '') + '"></div>');
    }
    if (canMove(object)) {
      var offset = currentValue(object.gid, 'offset_px', [0, 0]);
      rows.push('<div class="property-row"><label>位置</label>' +
        '<input type="number" data-prop="offset_x" step="1" value="' + esc(offset[0]) + '">' +
        '<input type="number" data-prop="offset_y" step="1" value="' + esc(offset[1]) + '"></div>');
    }
    if (editable.indexOf('frameon') >= 0) {
      var on = currentValue(object.gid, 'frameon');
      rows.push('<div class="property-row"><label>边框</label>' + (typeof on === 'boolean'
        ? '<input type="checkbox" data-prop="frameon"' + (on ? ' checked' : '') + '>'
        : '<select data-prop="frameon" aria-describedby="frameState"><option value="">原状态未提供</option><option value="true">显示边框</option><option value="false">隐藏边框</option></select>') + '</div>');
      if (typeof on !== 'boolean') rows.push('<p class="property-empty" id="frameState">未提供边框状态；选择后才会修改。</p>');
    }
    if (editable.indexOf('visible') >= 0) {
      var hidden = currentValue(object.gid, 'visible', true) === false;
      rows.push('<button class="button ' + (hidden ? 'secondary' : 'danger') + '" data-action="toggle-visible">' + (hidden ? '恢复对象' : '移除对象') + '</button>');
    }
    panel.innerHTML = rows.join('');
    var names = propertyNames(object);
    var inputs = panel.querySelectorAll('[data-prop]');
    for (var i = 0; i < inputs.length; i++) if (inputs[i].tagName !== 'BUTTON') inputs[i].setAttribute('aria-label', names[inputs[i].dataset.prop] || '属性');
  }

  function copy(value) { return JSON.parse(JSON.stringify(value)); }
  function same(left, right) { return JSON.stringify(left) === JSON.stringify(right); }
  function hasEdits() { return Object.keys(state.edits).length > 0; }

  var overridesMap = model.map;

  function savedValue(gid, prop, fallback) {
    if (state.saved[gid] && state.saved[gid][prop] !== undefined) return state.saved[gid][prop];
    var object = manifestObjects().filter(function (item) { return item.gid === gid; })[0] || {};
    if (object[prop] !== undefined) return object[prop];
    if (prop === 'offset_px') return [0, 0];
    return fallback;
  }

  function currentValue(gid, prop, fallback) {
    if (gid === null && state.selectedGroup) {
      var members = selectedMembers();
      if (!members.length) return undefined;
      var value = currentValue(members[0].gid, prop);
      return members.every(function (object) { return same(value, currentValue(object.gid, prop)); }) ? value : undefined;
    }
    var edits = state.edits[gid];
    if (edits && edits[prop] !== undefined) return edits[prop];
    if (markerFollowsLine(gid, prop)) return currentValue(gid, 'color', savedValue(gid, prop, fallback));
    return savedValue(gid, prop, fallback);
  }

  function normalizeEdits(edits) {
    var result = {};
    Object.keys(edits).sort().forEach(function (gid) {
      Object.keys(edits[gid]).sort().forEach(function (prop) {
        var pending = state.saving && state.saving.submitted[gid];
        // Returning to the old saved value during an in-flight save is still
        // a new command if that save is about to persist a different value.
        // Choosing the displayed auto colour explicitly changes its mode even
        // when its RGB equals the saved appearance. Keep that intent for replay.
        if (!savedMarkerFollowsLine(gid, prop) && same(edits[gid][prop], savedValue(gid, prop)) &&
            (!pending || pending[prop] === undefined || same(edits[gid][prop], pending[prop]))) return;
        if (!result[gid]) result[gid] = {};
        result[gid][prop] = edits[gid][prop];
      });
    });
    return result;
  }

  function remember(before, revision) {
    state.undo.push({ edits: copy(before), revision: revision });
    if (state.undo.length > 100) state.undo.shift();
    state.redo = [];
  }

  function renderEditState() {
    renderQa(); renderPropertyResets();
    var object = propertyObject();
    ['markerfacecolor', 'markeredgecolor'].forEach(function (prop) {
      var follows = !!object && selectedMembers().every(function (member) { return markerFollowsLine(member.gid, prop); });
      var hint = document.getElementById(prop + 'Mode');
      if (hint) hint.hidden = !follows;
      var field = el('propertyPanel').querySelector('[data-prop="' + prop + '"]');
      if (follows && field) {
        var value = currentValue(object.gid, prop);
        if (typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)) field.value = value;
      }
    });
    var editable = state.current && state.current.editable !== false;
    var blocked = !editable || state.saving || state.deleting || state.drag;
    el('undoButton').disabled = !!blocked || !state.undo.length;
    el('redoButton').disabled = !!blocked || !state.redo.length;
    el('saveButton').disabled = !editable || !hasEdits() || !!state.saving || !!state.deleting;
    var status = el('editStatus');
    status.classList.toggle('is-dirty', hasEdits());
    el('checkPreviewButton').disabled = !editable || !!state.saving || !!state.deleting || !!state.drag;
    status.textContent = !state.current ? '未打开图件' : state.savedPreviewStatus || (!editable ? '只读图件' : state.saving
      ? '正在保存；期间的新修改会保留。' : state.saveError && hasEdits() ? state.saveError
      : state.previewError ? '预览未更新 · ' + state.previewError
      : state.previewFlight || state.previewWanted ? '未保存 · 等待服务端校正（本地可映射部分已显示）'
      : hasEdits() ? (state.localRevision === state.editRevision
        ? '未保存 · 本地草稿已显示；字体与布局以保存为准'
        : '未保存 · 正在准备预览') : state.preview ? '临时检查预览；导出为当前已保存图件。' : '已保存；没有本地修改。');
  }

  function setProperty(gid, prop, value, transient) {
    if (!state.current || state.current.editable === false) return;
    var object = gid === null ? propertyObject() : manifestObjects().filter(function (item) { return item.gid === gid; })[0];
    if (!object || (object.editable || []).indexOf(prop) < 0 || (prop === 'offset_px' && !canMove(object))) return;
    var targets = gid === null ? selectedMembers() : [object];
    var next = copy(state.edits);
    targets.forEach(function (target) {
      if (!next[target.gid]) next[target.gid] = {};
      next[target.gid][prop] = value;
    });
    next = normalizeEdits(next);
    if (same(next, state.edits)) return;
    if (!transient) remember(state.edits, state.editRevision);
    state.edits = next;
    state.editRevision += 1;
    schedulePreview();
    renderEditState();
  }

  function changeHistory(redo) {
    if (!state.current || state.current.editable === false || state.saving || state.deleting || state.drag) return;
    var from = redo ? state.redo : state.undo;
    var to = redo ? state.undo : state.redo;
    if (!from.length) return;
    to.push({ edits: copy(state.edits), revision: state.editRevision });
    state.edits = normalizeEdits(from.pop().edits);
    state.editRevision += 1;
    renderProperties();
    renderObjectSelect();
    renderOverlay();
    renderEditState();
    schedulePreview();
  }

  var NUMBER_PROPS = { fontsize: [5, 24, .5, '字号'], linewidth: [.2, 8, .1, '线宽'],
    markersize: [1, 24, .5, '标记大小'], markeredgewidth: [0, 8, .1, '标记边宽'],
    alpha: [0, 1, .05, '不透明度'], capsize: [0, 24, .5, '端帽大小'], ncols: [1, 6, 1, '图例列数'] };

  function collectEdits(input) {
    var object = propertyObject();
    if (!object || !input || state.drag) return;
    var prop = input.dataset.prop;
    var value = input.value;
    if (prop === 'offset_x' || prop === 'offset_y') {
      if (value === '' || !Number.isFinite(Number(value))) return;
      var offset = currentValue(object.gid, 'offset_px', [0, 0]).slice();
      offset[prop === 'offset_x' ? 0 : 1] = Number(value);
      setProperty(object.gid, 'offset_px', offset);
      renderOverlay();
    } else if (prop === 'frameon') {
      if (input.tagName === 'SELECT') {
        if (value !== 'true' && value !== 'false') return;
        setProperty(object.gid, prop, value === 'true');
      } else {
        setProperty(object.gid, prop, input.checked);
      }
    } else if (NUMBER_PROPS[prop]) {
      var limits = NUMBER_PROPS[prop], number = Number(value);
      if (value !== '' && Number.isFinite(number) && number >= limits[0] && number <= limits[1] && (prop !== 'ncols' || Number.isInteger(number))) setProperty(object.gid, prop, number);
    } else if (prop === 'color' || prop === 'facecolor' || prop === 'edgecolor' || prop === 'markerfacecolor' || prop === 'markeredgecolor') {
      if (/^#[0-9a-f]{6}$/i.test(value)) setProperty(object.gid, prop, value);
    } else if (prop === 'text' || prop === 'label') {
      if (!state.selectedGroup && typeof value === 'string' && Array.from(value).length <= 2000) {
        setProperty(object.gid, prop, value);
        el('objectName').textContent = objectLabel(object);
        renderObjectSelect();
      }
    } else if (prop === 'fontstyle' || prop === 'horizontalalignment' || prop === 'loc') {
      var field = {fontstyle: 'font_styles', horizontalalignment: 'horizontalalignment_options', loc: 'legend_loc_options'}[prop];
      if ((state.current.manifest[field] || []).indexOf(value) >= 0) setProperty(object.gid, prop, value);
    } else if (prop === 'marker' || prop === 'linestyle') {
      var options = styleOptions(object, prop);
      if (options.indexOf(value) >= 0) setProperty(object.gid, prop, value);
    } else if (prop === 'fontfamily') {
      if ((state.current.manifest.font_families || []).indexOf(value) >= 0) setProperty(object.gid, prop, value);
    } else if (prop === 'fontweight') {
      if (['normal', 'bold', 'light', 'medium', 'semibold', 'heavy', 'black'].indexOf(value) >= 0) setProperty(object.gid, prop, value);
    }
  }

  function toggleVisibility() {
    var object = propertyObject();
    if (!object || (object.editable || []).indexOf('visible') < 0) return;
    setProperty(object.gid, 'visible', currentValue(object.gid, 'visible', true) === false);
    renderProperties(); renderObjectSelect(); renderOverlay(); schedulePreview();
  }

  // Temporary previews never become the saved baseline or export links.
  var editBatch = model.batch;
  function showSvg(svg, doc, manifest, coarse) {
    // Geometry is eligible only after the exact paired IMG has loaded. Keep
    // repeated identical drafts from needlessly replacing that image/revision.
    if (svg === state.displayedSvg && state.hitManifest === manifest && state.hitCoarse === coarse) return;
    var previous = state.blobUrl, image = el('previewImage');
    state.hitEpoch += 1; state.hitDocument = null; state.hitScene = null; state.hitCycle = null;
    state.displayedSvg = svg; state.hitManifest = manifest; state.hitCoarse = coarse;
    var epoch = state.hitEpoch;
    state.blobUrl = URL.createObjectURL(new Blob([svg], {type: 'image/svg+xml'}));
    image.onload = function () {
      if (state.hitEpoch !== epoch || image.currentSrc !== state.blobUrl || !image.complete) return;
      state.hitDocument = doc || null;
    };
    image.src = state.blobUrl;
    if (previous) URL.revokeObjectURL(previous);
  }
  function resetScene() {
    if (state.sceneFlight) state.sceneFlight.abort();
    state.sceneFlight = null; state.sceneGeneration += 1;
    if (state.projectionFrame !== null) cancelAnimationFrame(state.projectionFrame);
    state.projectionFrame = null; state.scene = null; state.sceneEdits = {};
    state.hitEpoch += 1; state.hitDocument = null; state.hitScene = null; state.hitManifest = null; state.hitCycle = null; state.displayedSvg = null;
    el('previewImage').onload = null;
    el('previewImage').onerror = null;
    state.localUnsupported = []; state.localRevision = -1;
    if (state.blobUrl) URL.revokeObjectURL(state.blobUrl);
    state.blobUrl = null;
  }
  function loadSavedScene() {
    if (!state.current) return;
    resetScene();
    state.savedPreviewStatus = '';
    var project = state.current, exports = project.exports || {}, image = el('previewImage');
    var figureId = project.figure_id, epoch = state.epoch, version = project.render_version;
    var generation = state.sceneGeneration, controller = new AbortController();
    var base = API + '/projects/' + encodeURIComponent(figureId);
    var format = exports.svg ? 'svg' : exports.png ? 'png' : '';
    var url = format ? base + '/preview.' + format + '?v=' + version : '';
    var maxSceneBytes = 8 * 1024 * 1024;
    function current() { return ownsProject(figureId, epoch) && state.sceneGeneration === generation &&
      state.current.render_version === version && !controller.signal.aborted; }
    function showImage(src, scene) {
      image.onload = function () {
        if (!current() || image.src !== src && image.getAttribute('src') !== src) return;
        if (scene) {
          state.hitDocument = scene.base;
          state.hitManifest = scene.manifest;
          state.hitCoarse = true;
        }
        if (state.view.fit) fitView();
      };
      image.onerror = function () {
        if (!current() || image.src !== src && image.getAttribute('src') !== src) return;
        state.savedPreviewStatus = '已保存的 ' + format.toUpperCase() + ' 无法预览；请检查下载或重新打开。';
        renderEditState();
      };
      if (image.getAttribute('src') !== src) image.src = src;
    }
    function staticOnly(message) {
      project.editable = false;
      state.savedPreviewStatus = message;
      if (url) showImage(url, null);
      else image.removeAttribute('src');
    }
    if (format !== 'svg') {
      staticOnly(format === 'png' ? '当前显示已保存的 PNG；项目没有 SVG。' :
        exports.pdf ? '此项目没有画布预览；可下载已保存的 PDF 查看。' : '此项目没有可在画布预览的文件。');
      return;
    }
    if (Number(exports.svg.size_bytes) > maxSceneBytes) {
      project.edit_requires_preparation = false;
      staticOnly('SVG 超过 8 MiB，可静态查看并下载原图。');
      return;
    }
    if (project.edit_requires_preparation || project.editable === false) {
      staticOnly(project.edit_requires_preparation ? '已显示保存的图件，可按需加载旧项目编辑对象。' :
        (project.edit_error && project.edit_error.message) || '当前图件只支持查看和下载。');
      return;
    }
    state.sceneFlight = controller;
    fetch(url, {credentials: 'same-origin', signal: controller.signal}).then(function (response) {
      if (!current()) return null;
      if (!response.ok) throw new Error(response.status === 409 ? '图件版本已更新，请重新打开。' : '已保存的 SVG 无法读取。');
      var length = Number(response.headers.get('Content-Length'));
      if (!(length > 0 && length <= maxSceneBytes)) {
        if (response.body) response.body.cancel().catch(function () {});
        staticOnly(length > maxSceneBytes ? 'SVG 超过 8 MiB，可静态查看并下载原图。' : '无法确认 SVG 大小，当前使用静态预览。');
        renderProjectMeta(); renderObjectSelect(); renderOverlay(); renderProperties();
        return null;
      }
      return response.blob();
    }).then(async function (blob) {
      if (!blob || !current()) return;
      state.blobUrl = URL.createObjectURL(blob);
      showImage(state.blobUrl, null);
      try {
        if (blob.size > maxSceneBytes) throw new Error('SVG 超出本地编辑上限');
        var svg = await blob.text();
        if (!current()) return;
        state.scene = new window.FigureScene(svg, project.manifest);
        state.sceneEdits = {}; state.preview = null;
        if (image.complete && image.naturalWidth) {
          state.hitDocument = state.scene.base;
          state.hitManifest = state.scene.manifest;
          state.hitCoarse = true;
        } else {
          showImage(state.blobUrl, state.scene);
        }
      } catch (error) {
        if (current()) state.previewError = '本地编辑预览不可用；仍可使用服务端检查。';
      }
    }).catch(function (error) {
      if (current()) state.savedPreviewStatus = error.message || '已保存的 SVG 无法读取。';
    }).finally(function () {
      if (!current()) return;
      state.sceneFlight = null;
      if (hasEdits() && project.editable !== false) projectLocally();
      else renderEditState();
    });
  }
  function cancelPreview(abort) {
    clearTimeout(state.previewTimer); state.previewTimer = null;
    state.previewGeneration += 1; state.previewWanted = false;
    // Ordinary edits invalidate eligibility, but keep one transport in flight:
    // aborting fetch cannot prove the sandbox stopped. Navigation/save abort.
    if (abort !== false) {
      if (state.previewFlight) state.previewFlight.controller.abort();
      state.previewFlight = null;
    }
  }
  function invalidatePreview(restore) {
    cancelPreview(); state.previewError = ''; state.preview = null;
    if (restore) schedulePreview();
  }
  function projectLocally() {
    state.projectionFrame = null;
    if (!state.current) return;
    if (!state.scene) {
      if (!state.sceneFlight && hasEdits()) { state.previewWanted = true; state.previewTimer = setTimeout(runPreview, 180); }
      renderEditState(); return;
    }
    // A temporary server scene can have replaced auto/mixed/unknown values.
    // Restoring those requires the saved SVG, not an invented setter value.
    var needsSavedScene = Object.keys(state.sceneEdits).some(function (gid) {
      return Object.keys(state.sceneEdits[gid]).some(function (prop) {
        return !(state.edits[gid] && state.edits[gid][prop] !== undefined) &&
          (savedMarkerFollowsLine(gid, prop) || savedValue(gid, prop) === undefined);
      });
    });
    if (needsSavedScene) { loadSavedScene(); renderEditState(); return; }
    var changes = model.delta(state.edits, state.sceneEdits, savedValue, savedMarkerFollowsLine);
    var result = state.scene.project(changes, function (gid, prop) {
      return state.sceneEdits[gid] && state.sceneEdits[gid][prop] !== undefined ? state.sceneEdits[gid][prop] : savedValue(gid, prop);
    }, editBatch(state.saved).concat(editBatch(state.edits)));
    showSvg(result.svg, result.document, state.scene.manifest, changes.length === 0);
    state.localUnsupported = result.unsupported;
    state.localRevision = result.unsupported.length ? -1 : state.editRevision;
    state.previewWanted = result.unsupported.length > 0;
    if (state.previewWanted && !state.saving && !state.drag) state.previewTimer = setTimeout(runPreview, 180);
    renderOverlay(); renderEditState();
  }
  function schedulePreview() {
    if (!state.current || state.current.editable === false) return;
    cancelPreview(false); state.previewError = '';
    if (state.projectionFrame === null) state.projectionFrame = requestAnimationFrame(projectLocally);
  }
  function runPreview(force) {
    state.previewTimer = null;
    if ((!state.previewWanted && force !== true) || state.saving || state.deleting || state.drag || !state.current) return;
    if (state.previewFlight) { if (force === true) state.previewWanted = true; return; }
    cancelPreview();
    var flight = { figureId: state.current.figure_id, epoch: state.epoch, revision: state.editRevision,
      generation: state.previewGeneration, version: state.current.render_version || 0,
      edits: copy(state.edits), controller: new AbortController() };
    state.previewFlight = flight; state.previewWanted = false; state.previewError = '';
    function current() {
      return ownsProject(flight.figureId, flight.epoch) && state.previewGeneration === flight.generation &&
        state.editRevision === flight.revision && !flight.controller.signal.aborted && !state.saving && !state.deleting && !state.drag;
    }
    renderEditState();
    jsonRequest(API + '/projects/' + encodeURIComponent(flight.figureId) + '/preview', {
      method: 'POST', credentials: 'same-origin', signal: flight.controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ overrides: authoritativeBatch(editBatch(flight.edits), flight.version), expected_revision: flight.version, draft_revision: flight.revision })
    }, current).then(function (result) {
      if (!current()) return;
      if (result.figure_id !== flight.figureId || result.render_version !== flight.version || result.draft_revision !== flight.revision ||
          !result.manifest || !Array.isArray(result.manifest.objects) || typeof result.svg !== 'string' || !result.svg.trim()) throw new Error('预览响应不完整');
      var scene = new window.FigureScene(result.svg, result.manifest);
      if (state.sceneFlight) state.sceneFlight.abort();
      state.sceneFlight = null; state.sceneGeneration += 1;
      state.scene = scene; state.sceneEdits = copy(flight.edits);
      state.preview = { manifest: result.manifest, edits: flight.edits, revision: flight.revision };
      state.localUnsupported = []; state.localRevision = flight.revision;
      showSvg(result.svg, scene.base, result.manifest, true); renderOverlay();
    }).catch(function (error) {
      if (current()) state.previewError = '服务端预览未更新：' + error.message;
    }).then(function () {
      if (state.previewFlight !== flight) return;
      state.previewFlight = null; renderEditState();
      if (state.previewWanted) state.previewTimer = setTimeout(runPreview, 180);
    });
  }

  function authoritativeBatch(batch, version) {
    var document = state.current && state.current.document;
    if (!document || document.schema !== 'citrus.figure-document/1' ||
        document.document_id !== state.current.figure_id || document.revision !== version ||
        !Array.isArray(document.objects)) return batch;
    var targets = new Map();
    document.objects.forEach(function (binding) {
      if (binding.resolvable && binding.identity_scope === 'semantic') targets.set(binding.gid, binding.object_id);
    });
    return batch.map(function (edit) {
      var target = targets.get(edit.gid);
      return target ? { object_id: target, prop: edit.prop, value: edit.value } : edit;
    });
  }

  // ── Saving ─────────────────────────────────────────────────────────────

  function applyAuthoritativeResult(result, rebaseRevision) {
    state.current = Object.assign({}, state.current, result);
    state.saved = overridesMap(result.overrides);
    // Only commands made after submission remain local and undoable.
    state.edits = normalizeEdits(state.edits);
    state.undo = state.undo.filter(function (command) { return command.revision >= rebaseRevision; })
      .map(function (command) { return { edits: normalizeEdits(command.edits), revision: command.revision }; });
    state.redo = [];
    state.saveError = ''; state.preview = null;
    loadSavedScene();
    renderProjectMeta();
    renderCanvasInfo(); if (state.view.fit) fitView();
    renderOverlay(); renderProperties(); renderObjectSelect(); renderProjects(); renderDownloads();
  }

  function saveEdits() {
    if (!state.current || state.current.editable === false || state.saving || state.deleting) return;
    onOverlayMouseUp();
    var figureId = state.current.figure_id;
    var epoch = state.epoch;
    var revision = state.editRevision;
    var expectedRevision = state.current.render_version || 0;
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
    cancelPreview();
    var button = el('saveButton');
    button.disabled = true;
    var save = { figureId: figureId, epoch: epoch, submitted: overridesMap(batch) };
    state.saving = save;
    state.saveError = '';
    renderEditState();
    return jsonRequest(API + '/projects/' + encodeURIComponent(figureId) + '/overrides', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ overrides: authoritativeBatch(batch, expectedRevision), expected_revision: expectedRevision })
    }, function () { return ownsProject(figureId, epoch); }).then(function (result) {
      if (!ownsProject(figureId, epoch) || state.saving !== save) return;
      if (result.figure_id !== figureId || result.render_version !== expectedRevision + 1 ||
          !result.manifest || !Array.isArray(result.manifest.objects) || !Array.isArray(result.overrides)) {
        throw new Error('保存回执不完整；请核对当前已保存图件后再重试');
      }
      onOverlayMouseUp();
      applyAuthoritativeResult(result, revision);
      notify(hasEdits() ? '已保存；保存期间的新修改尚未保存。' : '已保存并重新渲染。');
      return loadProjects().catch(function () {
        if (ownsProject(figureId, epoch)) notify('图件已保存；列表刷新未完成。', true);
      });
    }).catch(function (error) {
      if (ownsProject(figureId, epoch) && state.saving === save) {
        state.saveError = '未保存：' + error.message;
        notify(error.message, true);
      }
    }).then(function () {
      if (!ownsProject(figureId, epoch) || state.saving !== save) return;
      state.saving = null;
      state.edits = normalizeEdits(state.edits);
      renderEditState();
      if (hasEdits()) schedulePreview();
    });
  }

  // ── Dragging ───────────────────────────────────────────────────────────

  function selectObject(gid, focus) {
    state.selectedGroup = '';
    state.selected = gid;
    function reveal(nodes) {
      return nodes.some(function (node) {
        if (node.object && node.id === gid) return true;
        if (reveal(node.children)) { state.expanded[(node.object ? 'object:' : 'group:') + node.id] = true; return true; }
        return false;
      });
    }
    reveal(objectGroups());
    renderObjectSelect();
    renderProperties();
    renderOverlay();
    if (focus) focusSelected();
  }

  function pickObject(event, cycle) {
    if (!state.hitDocument || !state.hitManifest || !window.FigureHitScene) {
      el('hitStatus').textContent = '画布选择尚未就绪；可用左侧对象树'; return '';
    }
    if (document.fonts && document.fonts.status !== 'loaded') {
      el('hitStatus').textContent = '字体正在载入；可用左侧对象树'; return '';
    }
    try {
      if (!state.hitScene) state.hitScene = new window.FigureHitScene(state.hitDocument, state.hitManifest, state.hitCoarse);
      var point = state.hitScene.point(event.clientX, event.clientY, el('previewImage').getBoundingClientRect());
      var hits = state.hitScene.hits(point, !!event.ctrlKey || !!event.metaKey);
      el('hitStatus').textContent = state.hitScene.unsupported.size ? '部分对象请在左侧选择 · Alt 点击切换重叠对象' : 'Alt 点击切换重叠对象';
      if (!hits.length) { if (cycle) state.hitCycle = null; return ''; }
      var previous = state.hitCycle, index = 0;
      if (cycle && event.altKey && previous && previous.epoch === state.hitEpoch &&
          Math.hypot(event.clientX - previous.x, event.clientY - previous.y) <= 3 && same(previous.hits, hits)) {
        index = (previous.index + 1) % hits.length;
      }
      if (cycle) state.hitCycle = {epoch: state.hitEpoch, x: event.clientX, y: event.clientY, hits: hits, index: index, time: Date.now()};
      return hits[index];
    } catch (error) {
      el('hitStatus').textContent = '此图暂不可从画布精确选择；请用左侧对象树'; return '';
    }
  }

  function onOverlayMouseDown(event) {
    if (state.drag || state.view.pan || state.view.mode === 'pan' || (event.button !== undefined && event.button !== 0)) return;
    var handle = event.target.closest ? event.target.closest('[data-move-gid]') : null;
    var gid = handle && handle.dataset.moveGid === state.selected && canMove(selectedObject()) ? state.selected : pickObject(event, true);
    if (!gid) return;
    event.preventDefault();
    selectObject(gid, true);
    if (event.altKey || !canMove(selectedObject())) return;
    state.drag = {
      gid: gid, pointerId: event.pointerId, geometryEpoch: state.hitEpoch,
      startX: event.clientX, startY: event.clientY,
      before: copy(state.edits), revision: state.editRevision,
      offset: currentValue(gid, 'offset_px', [0, 0]).slice()
    };
    var overlay = el('overlay');
    if (event.pointerId !== undefined && overlay.setPointerCapture) overlay.setPointerCapture(event.pointerId);
    renderEditState();
  }

  function onOverlayMouseMove(event) {
    if (!state.drag || (event.pointerId !== undefined && event.pointerId !== state.drag.pointerId)) return;
    var image = el('previewImage');
    var size = canvasSize();
    // BoundingClientRect includes the viewport transform; clientWidth does not.
    var rect = image.getBoundingClientRect ? image.getBoundingClientRect() : { width: image.clientWidth, height: image.clientHeight };
    if (!rect.width || !size.width) return;
    var scaleX = size.width / rect.width;
    var scaleY = rect.height ? size.height / rect.height : scaleX;
    var dx = Math.round((event.clientX - state.drag.startX) * scaleX);
    var dy = Math.round((event.clientY - state.drag.startY) * scaleY);
    setProperty(state.drag.gid, 'offset_px', [state.drag.offset[0] + dx, state.drag.offset[1] + dy], true);
    var offset = currentValue(state.drag.gid, 'offset_px', [0, 0]);
    ['offset_x', 'offset_y'].forEach(function (prop, index) {
      var input = el('propertyPanel').querySelector('[data-prop="' + prop + '"]');
      if (input) input.value = offset[index];
    });
    renderOverlay();
  }

  function onOverlayMouseUp(event, cancelled) {
    if (!state.drag || (event && event.pointerId !== undefined && event.pointerId !== state.drag.pointerId)) return;
    var drag = state.drag;
    state.drag = null;
    if (!same(drag.before, state.edits)) {
      if (cancelled) {
        state.edits = drag.before;
        state.editRevision += 1;
      } else {
        remember(drag.before, drag.revision);
      }
    }
    var overlay = el('overlay');
    if (drag.pointerId !== undefined && overlay.hasPointerCapture && overlay.hasPointerCapture(drag.pointerId)) {
      overlay.releasePointerCapture(drag.pointerId);
    }
    renderProperties();
    renderOverlay();
    renderEditState();
    schedulePreview();
  }

  function onOverlayKeyDown(event) {
    var box = event.target.closest ? event.target.closest('.overlay-box') : null;
    if (!box || state.drag || state.view.mode === 'pan') return;
    if (event.key === 'Delete') {
      event.preventDefault(); selectObject(box.dataset.gid, true); toggleVisibility(); return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      selectObject(box.dataset.gid, true);
      return;
    }
    var direction = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    if (!direction || event.ctrlKey || event.metaKey || event.altKey) return;
    selectObject(box.dataset.gid, true);
    if (!canMove(selectedObject())) return;
    event.preventDefault();
    var step = event.shiftKey ? 10 : 1;
    var offset = currentValue(state.selected, 'offset_px', [0, 0]);
    setProperty(state.selected, 'offset_px', [offset[0] + direction[0] * step, offset[1] + direction[1] * step]);
    renderProperties();
    renderOverlay();
    schedulePreview();
  }

  function onHistoryKeyDown(event) {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    var target = event.target || {};
    if (target.closest && target.closest('dialog[open]')) return;
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName || '') || target.isContentEditable) return;
    var key = String(event.key || '').toLowerCase();
    if (key !== 'z' && key !== 'y') return;
    event.preventDefault();
    changeHistory(key === 'y' || event.shiftKey);
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
      loadDataSchema();
    });
  }

  function updateMapping() {
    var paired = ['scatter', 'line', 'bar'].indexOf(el('importChart').value) >= 0;
    el('importX').disabled = !paired;
    el('importXUnit').disabled = !paired;
    el('importGroup').disabled = el('importChart').value === 'histogram';
    el('importButton').disabled = state.importing || !state.schema || !el('importY').value || (paired && !el('importX').value);
  }
  function loadDataSchema() {
    var request = ++state.schemaRequest, fileId = el('dataFileSelect').value;
    state.schema = null; el('importMapping').disabled = true; el('importButton').disabled = true;
    el('schemaStatus').textContent = fileId ? '正在读取列信息…' : '请选择表格。';
    if (!fileId) return;
    jsonRequest(API + '/data-files/' + encodeURIComponent(fileId) + '/schema').then(function (schema) {
      if (request !== state.schemaRequest || fileId !== el('dataFileSelect').value) return;
      state.schema = schema;
      ['importX', 'importY', 'importGroup'].forEach(function (id) {
        var options = (schema.columns || []).filter(function (column) { return id !== 'importY' || column.numeric; });
        el(id).innerHTML = '<option value="">' + (id === 'importGroup' ? '不分组' : '请明确选择列') + '</option>' + options.map(function (column) {
          return '<option value="' + esc(column.name) + '">' + esc(column.name) + '</option>';
        }).join('');
      });
      el('importMapping').disabled = !!schema.truncated;
      el('schemaStatus').textContent = schema.truncated ? '解析范围不完整，不能出图。' : schema.rows + ' 行 · 请明确指标、单位与图型。';
      if (schema.truncated) state.schema = null;
      updateMapping();
    }).catch(function (error) { if (request === state.schemaRequest) el('schemaStatus').textContent = error.message; });
  }

  function importFromLibrary() {
    var epoch = state.epoch;
    var fileId = el('dataFileSelect').value;
    if (!fileId) {
      notify('请先选择资料库里的表格文件。', true);
      return;
    }
    if (!state.schema || state.schema.file_id !== fileId || state.importing) return;
    updateMapping();
    var button = el('importButton');
    if (button.disabled) return;
    state.importing = true; button.disabled = true;
    jsonRequest(API + '/projects', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: fileId, mapping: { chart: el('importChart').value,
        x: el('importX').disabled ? '' : el('importX').value, y: el('importY').value, group: el('importGroup').disabled ? '' : el('importGroup').value,
        x_unit: el('importXUnit').value, y_unit: el('importYUnit').value } })
    }).then(function (result) {
      if (epoch !== state.epoch) return;
      notify('已导入并生成图件。');
      el('importPanel').hidden = true;
      el('importToggle').setAttribute('aria-expanded', 'false');
      return loadProjects().then(function () {
        if (epoch === state.epoch) return openProject(result.figure_id);
      });
    }).catch(function (error) {
      if (epoch === state.epoch) notify(error.message, true);
    }).then(function () {
      state.importing = false; updateMapping();
    });
  }

  // ── Deleting ───────────────────────────────────────────────────────────

  function deleteCurrent() {
    if (!state.current || state.deleting || state.saving) return;
    var figureId = state.current.figure_id;
    var epoch = state.epoch;
    if (!confirm('删除这张图件？资料库里已导出的文件不会被删除。')) return;
    state.deleting = figureId;
    el('deleteButton').disabled = true;
    return jsonRequest(API + '/projects/' + encodeURIComponent(figureId), {
      method: 'DELETE',
      credentials: 'same-origin'
    }, function () { return ownsProject(figureId, epoch); }).then(function () {
      if (!ownsProject(figureId, epoch)) return;
      clearProject();
      notify('已删除。');
      return loadProjects();
    }).catch(function (error) {
      if (ownsProject(figureId, epoch)) notify(error.message, true);
    }).then(function () {
      if (!ownsProject(figureId, epoch)) return;
      state.deleting = null;
      el('deleteButton').disabled = false;
    });
  }

  // ── Wiring ─────────────────────────────────────────────────────────────

  function init() {
    el('refreshButton').addEventListener('click', function () { loadProjects(); });
    el('saveButton').addEventListener('click', saveEdits);
    el('verifyFigureButton').addEventListener('click', function () { return verifyFigure(false); });
    el('bundleFigureButton').addEventListener('click', function () { return verifyFigure(true); });
    el('checkPreviewButton').addEventListener('click', function () { runPreview(true); });
    el('prepareEditButton').addEventListener('click', function () {
      if (state.current && state.current.edit_requires_preparation) openProject(state.current.figure_id, true);
    });
    el('dataFileSelect').addEventListener('change', loadDataSchema);
    el('importChart').addEventListener('change', updateMapping);
    ['importX', 'importY', 'importGroup'].forEach(function (id) { el(id).addEventListener('change', updateMapping); });
    el('undoButton').addEventListener('click', function () { changeHistory(false); });
    el('redoButton').addEventListener('click', function () { changeHistory(true); });
    el('deleteButton').addEventListener('click', deleteCurrent);
    el('importButton').addEventListener('click', importFromLibrary);
    el('importToggle').addEventListener('click', function () {
      var panel = el('importPanel');
      panel.hidden = !panel.hidden;
      el('importToggle').setAttribute('aria-expanded', String(!panel.hidden));
      if (!panel.hidden) loadDataFiles();
    });
    el('objectSearch').addEventListener('input', renderObjectSelect);
    el('objectRoleFilter').addEventListener('change', renderObjectSelect);
    el('objectSelect').addEventListener('change', function (event) {
      if (!manifestObjects().some(function (object) { return object.gid === event.target.value; })) return;
      selectObject(event.target.value);
    });
    el('objectTree').addEventListener('click', function (event) {
      if (state.drag) return;
      var button = event.target.closest ? event.target.closest('button') : null;
      if (!button) return;
      var expand = button.getAttribute('data-expand'), gid = button.getAttribute('data-gid'), group = button.getAttribute('data-group');
      if (expand !== null) { if (button.disabled) return; state.expanded[expand] = button.getAttribute('aria-expanded') !== 'true'; renderObjectSelect(); }
      else if (gid !== null) selectObject(gid);
      else if (group !== null) {
        state.selected = ''; state.selectedGroup = group;
        renderObjectSelect(); renderProperties(); renderOverlay();
      }
      if (expand === null) { selectTab('properties'); if (state.narrow) setSidebar('right', true); }
    });
    el('objectTree').addEventListener('keydown', onTreeKeyDown);
    el('propertyDraftControls').addEventListener('click', function (event) {
      var button = event.target.closest ? event.target.closest('[data-reset-prop]') : null;
      if (button) restoreProperty(button.getAttribute('data-reset-prop'));
    });
    el('propertyScope').addEventListener('click', function (event) {
      var button = event.target.closest ? event.target.closest('[data-select-parent]') : null;
      if (button && manifestObjects().some(function (item) { return item.gid === button.getAttribute('data-select-parent'); })) {
        selectObject(button.getAttribute('data-select-parent'));
      }
    });
    el('propertyPanel').addEventListener('input', function (event) {
      var prop = event.target.dataset.prop;
      if (!prop) return;
      collectEdits(event.target);
      schedulePreview();
      if (prop === 'fontsize' || prop === 'linewidth') {
        var out = el('propertyPanel').querySelector('[data-out="' + prop + '"]');
        if (out) out.textContent = event.target.value;
      }
    });

    el('propertyPanel').addEventListener('change', function (event) {
      if (!event.target.dataset.prop) return;
      collectEdits(event.target); schedulePreview();
    });
    el('propertyPanel').addEventListener('click', function (event) {
      var button = event.target.closest ? event.target.closest('[data-action="toggle-visible"]') : null;
      if (button) toggleVisibility();
      var style = event.target.closest ? event.target.closest('[data-value]') : null;
      if (style) {
        collectEdits(style); schedulePreview();
        Array.prototype.forEach.call(el('propertyPanel').querySelectorAll('[data-value]'), function (option) {
          if (option.dataset.prop === style.dataset.prop) option.setAttribute('aria-pressed', String(option === style));
        });
      }
    });
    el('overlay').addEventListener('dblclick', function (event) {
      if (state.view.mode === 'pan' || state.drag) return;
      var gid = pickObject(event, false), last = state.hitCycle;
      // MouseEvent dblclick rounds client coordinates on Chromium whereas the
      // selecting PointerEvent retains fractions. Reuse that verified ink hit
      // only within rounding distance, time and the exact same image revision.
      if (!gid && last && last.epoch === state.hitEpoch && Date.now() - last.time < 1000 &&
          Math.hypot(event.clientX - last.x, event.clientY - last.y) <= 1.5 && last.hits[last.index] === state.selected) gid = state.selected;
      var object = manifestObjects().filter(function (item) { return item.gid === gid; })[0];
      if (!object || (object.editable || []).indexOf('text') < 0 || state.current.editable === false) return;
      selectObject(object.gid); selectTab('properties'); setSidebar('right', true);
      var editor = el('propertyPanel').querySelector('[data-prop="text"]');
      if (editor) { editor.focus(); if (editor.select) editor.select(); }
    });
    el('overlay').addEventListener('keyup', function (event) {
      if (/^Arrow/.test(event.key || '')) schedulePreview();
    });

    el('projectList').addEventListener('click', function (event) {
      var button = event.target.closest ? event.target.closest('[data-open]') : null;
      if (button) openProject(button.getAttribute('data-open'));
    });

    el('overlay').addEventListener('keydown', onOverlayKeyDown);
    document.addEventListener('keydown', onHistoryKeyDown);
    if (window.PointerEvent) {
      el('overlay').addEventListener('pointerdown', onOverlayMouseDown);
      document.addEventListener('pointermove', onOverlayMouseMove);
      document.addEventListener('pointerup', onOverlayMouseUp);
      document.addEventListener('pointercancel', function (event) { onOverlayMouseUp(event, true); });
      el('overlay').addEventListener('lostpointercapture', function (event) { onOverlayMouseUp(event, true); });
    } else {
      el('overlay').addEventListener('mousedown', onOverlayMouseDown);
      document.addEventListener('mousemove', onOverlayMouseMove);
      document.addEventListener('mouseup', onOverlayMouseUp);
    }
    // Viewport listeners follow the object listeners so a selection drag owns
    // its gesture. No viewport operation writes overrides or command history.
    var down = window.PointerEvent ? 'pointerdown' : 'mousedown';
    var move = window.PointerEvent ? 'pointermove' : 'mousemove';
    var up = window.PointerEvent ? 'pointerup' : 'mouseup';
    el('viewport').addEventListener(down, beginPan);
    document.addEventListener(move, movePan);
    document.addEventListener(up, endPan);
    if (window.PointerEvent) {
      document.addEventListener('pointercancel', endPan);
      el('viewport').addEventListener('lostpointercapture', endPan);
    }
    el('viewport').addEventListener('wheel', wheelView, { passive: false });
    el('viewport').addEventListener('keydown', function (event) {
      if (!state.current || state.view.mode !== 'pan' || event.target !== el('viewport') || state.view.pan) return;
      var delta = { ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40] }[event.key];
      if (!delta) return;
      event.preventDefault(); state.view.x += delta[0]; state.view.y += delta[1]; state.view.fit = false; renderView();
    });
    el('zoomIn').addEventListener('click', function () { zoomView(state.view.zoom * 1.25); });
    el('zoomOut').addEventListener('click', function () { zoomView(state.view.zoom / 1.25); });
    el('zoomPercent').addEventListener('change', function (event) { zoomView(Number(event.target.value) / 100); });
    el('fitView').addEventListener('click', fitView);
    el('selectMode').addEventListener('click', function () { setViewMode('select'); });
    el('panMode').addEventListener('click', function () { setViewMode('pan'); });
    el('gridToggle').addEventListener('change', renderGuides);
    el('rulerToggle').addEventListener('change', renderGuides);
    ['left', 'right'].forEach(function (side) {
      el('toggle' + (side === 'left' ? 'Left' : 'Right')).addEventListener('click', function () {
        setSidebar(side, el(side + 'Sidebar').hidden);
      });
    });
    el('openProjects').addEventListener('click', function () { setSidebar('left', true); });
    var tabs = ['properties', 'canvas'];
    tabs.forEach(function (name, index) {
      el(name + 'Tab').addEventListener('click', function () { selectTab(name); });
      el(name + 'Tab').addEventListener('keydown', function (event) {
        var next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
          : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : -1;
        if (next >= 0) { event.preventDefault(); selectTab(tabs[next], true); }
      });
    });
    window.addEventListener('resize', resizeView);
    if (window.ResizeObserver) new window.ResizeObserver(function () { if (state.view.fit) fitView(); }).observe(el('viewport'));
    resizeView();
    renderCanvasInfo();
    window.addEventListener('beforeunload', function (event) {
      if (!hasEdits() && !state.saving) return;
      event.preventDefault();
      event.returnValue = '';
    });
    renderEditState();

    var initialEpoch = state.epoch;
    loadProjects().then(function () {
      if (initialEpoch !== state.epoch) return;
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
