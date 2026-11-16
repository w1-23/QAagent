(function (root) {
  'use strict';
  var titles = {'candidate-search':'科研检索', 'reference-verification':'文献核验',
    'data-audit':'数据审查', 'figure-initial':'科研绘图', 'figure-edit':'图件编辑',
    'nature-figure':'Nature 绘图方法', statistics:'统计分析', 'word-export':'Word 文档'};
  function create(options) {
    var selected = [], catalog = [], generation = 0, revision = 0, pending = false;
    var list = options.list, status = options.status, summary = options.summary;
    function same(a, b) { return a.id === b.id && a.version === b.version && a.sha256 === b.sha256; }
    function identity(item) { return {id:item.id, version:item.version, sha256:item.sha256}; }
    function title(item) { return titles[item.id] || item.id; }
    function draw() {
      list.replaceChildren();
      catalog.forEach(function (item) {
        var label = document.createElement('label'); label.className = 'skill-choice';
        var check = document.createElement('input'); check.type = 'checkbox';
        check.setAttribute('data-skill-id', item.id);
        check.checked = selected.some(function (pick) { return same(pick, item); });
        check.addEventListener('change', function () {
          if (!options.scope()) { reset(); return; }
          if (check.checked && selected.length >= 4) { check.checked = false; status.textContent = '每轮最多选择 4 项场景。'; return; }
          selected = selected.filter(function (pick) { return pick.id !== item.id; });
          if (check.checked) selected.push(identity(item));
          revision++; drawSummary();
        });
        var text = document.createElement('span'), name = document.createElement('strong'), description = document.createElement('small');
        name.textContent = title(item); description.textContent = item.description;
        text.appendChild(name); text.appendChild(description); label.appendChild(check); label.appendChild(text); list.appendChild(label);
      });
      drawSummary();
    }
    function drawSummary() {
      summary.textContent = selected.length ? '本轮场景：' + selected.map(title).join('、') : '';
      summary.hidden = !selected.length;
    }
    async function load() {
      var scope = options.scope();
      if (!scope) { status.textContent = '登录后可选择场景。'; return; }
      if (pending) return;
      var ticket = ++generation; pending = true; status.textContent = '正在读取场景目录…';
      try {
        var response = await options.fetch('/api/v2/skills');
        if (!response.ok) throw new Error('catalog_unavailable');
        var body = await response.json();
        if (ticket !== generation || scope !== options.scope()) return;
        if (!Array.isArray(body.skills) || body.skills.length > 100) throw new Error('invalid_catalog');
        catalog = body.skills.filter(function (item) {
          return item && typeof item.id === 'string' && typeof item.version === 'string' &&
            /^[a-f0-9]{64}$/.test(item.sha256) && typeof item.description === 'string';
        });
        var before = selected.length;
        selected = selected.filter(function (pick) { return catalog.some(function (item) { return same(pick, item); }); });
        if (before !== selected.length) revision++;
        status.textContent = before !== selected.length ? '部分场景已更新，已移除旧选择；请重新选择。' :
          catalog.length ? '可选。不选择时，助手仍会按任务自动发现所需场景。' : '当前没有可选场景；仍可直接提问。';
        draw();
      } catch (_) {
        if (ticket !== generation || scope !== options.scope()) return;
        status.textContent = '场景目录暂不可用，可重试或直接提问。';
      } finally { if (ticket === generation) pending = false; }
    }
    function reset() {
      generation++; revision++; pending = false; selected = []; catalog = [];
      list.replaceChildren(); status.textContent = ''; drawSummary();
      if (options.menu) options.menu.open = false;
    }
    if (options.menu) options.menu.addEventListener('toggle', function () { if (options.menu.open) load(); });
    if (options.retry) options.retry.addEventListener('click', load);
    return {load:load, reset:reset,
      capture:function () { return {items:selected.map(identity), revision:revision, scope:options.scope()}; },
      acknowledge:function (snapshot) {
        if (snapshot.scope === options.scope() && snapshot.revision === revision) {
          selected = []; revision++; draw(); if (options.menu) options.menu.open = false;
        }
      }};
  }
  root.CitrusSkillPicker = {create:create};
})(window);
