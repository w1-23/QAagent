(function (root) {
  'use strict';
  var labels = {method: '方法', statistic: '统计量', pvalue: 'P 值', p_value: 'P 值', n: '样本量',
    sample_size: '样本量', effect_size: '效应量', assumptions: '适用条件', coverage: '检查范围',
    findings: '检查发现', limitations: '限制', warnings: '提示', status: '状态', summary: '结果摘要',
    figure_id: '图件编号', title: '标题', render_version: '保存版本', artifact_id: '文件编号'};
  function node(tag, cls, text) {
    var el = document.createElement(tag); if (cls) el.className = cls;
    if (text !== undefined) el.textContent = String(text); return el;
  }
  function valueText(value) {
    if (value === null || value === undefined) return '未提供';
    if (typeof value === 'object') return JSON.stringify(value, null, 2);
    return String(value);
  }
  function model(data) {
    var outcome = data.outcome && typeof data.outcome === 'object' ? data.outcome : {};
    var facts = data.facts || outcome.facts;
    var refs = data.result_refs || outcome.result_refs || outcome.artifacts || data.artifacts;
    if ((!facts || typeof facts !== 'object' || !Object.keys(facts).length) && (!Array.isArray(refs) || !refs.length)) return null;
    var name = String(data.tool_name || data.name || data.display_name || '');
    var kind = String(data.kind || outcome.kind || '');
    var title = /statistic|statistical|describe_data|统计/.test(name + kind) ? '统计分析'
      : /audit|integrity|审查|数据检查/.test(name + kind) ? '数据审查'
      : /figure|plot|绘图|图件/.test(name + kind) ? '图件' : '步骤结果';
    return {title: title, status: String(data.status || outcome.status || ''),
      facts: facts && typeof facts === 'object' ? facts : {}, refs: Array.isArray(refs) ? refs : []};
  }
  function render(container, data) {
    var result = model(data); if (!container || !result) return null;
    var card = container.querySelector('.task-result');
    if (!card) { card = node('details', 'task-result'); container.appendChild(card); }
    card.replaceChildren();
    var statuses = {complete: '已完成', partial: '部分完成', failed: '未完成', not_run: '未执行', no_results: '未找到结果', cancelled: '已停止', skipped: '已跳过', running: '处理中'};
    card.appendChild(node('summary', 'task-result-title', result.title + (result.status ? ' · ' + (statuses[result.status] || result.status) : '')));
    var facts = node('dl', 'task-result-facts');
    Object.keys(result.facts).forEach(function (key) {
      facts.appendChild(node('dt', '', labels[key] || key));
      var value = node('dd', '', valueText(result.facts[key])); facts.appendChild(value);
    });
    if (facts.children.length) card.appendChild(facts);
    if (result.refs.length) {
      var list = node('ul', 'task-result-refs');
      result.refs.forEach(function (ref) {
        // References are server evidence, never guessed artifact/download URLs.
        var text = typeof ref === 'string' ? ref : (ref.label || ref.title || ref.file_name || ref.ref_id || ref.artifact_id || ref.file_id || valueText(ref));
        var row = node('li', '', text); list.appendChild(row);
        var href = typeof ref === 'string' ? ref : ref && ref.url;
        if (typeof href === 'string' && /^\/api\/v2\/session\/[A-Za-z0-9_-]+\/artifacts\/[a-f0-9]{32}$/.test(href)) {
          var link = node('a', '', '查看文件'); link.href = href; row.appendChild(link);
        }
      });
      card.appendChild(list);
    }
    return card;
  }
  function updateToolRow(options) {
    var data = options.data || {}, callId = String(data.tool_call_id || '');
    var key = String(data.logical_key || callId || data.operation_id || '');
    var entry = key ? options.rows[key] : null;
    if (!entry) {
      entry = node('div', 'term-line is-running'); entry.appendChild(node('span', 'term-msg'));
      options.log.appendChild(entry); if (key) options.rows[key] = entry;
    }
    var status = data.status || 'running';
    entry.classList.remove('is-running', 'is-complete', 'is-partial', 'is-failed', 'is-skipped');
    entry.classList.add(status === 'running' ? 'is-running' : status === 'complete' || status === 'no_results' ? 'is-complete'
      : status === 'partial' ? 'is-partial' : ['skipped', 'not_run', 'cancelled'].indexOf(status) >= 0 ? 'is-skipped' : 'is-failed');
    var text = status === 'running' ? options.activity(data.display_name || '科研任务') : options.statusText(status, data.reason_code);
    if (data.action_hint) text += ' · ' + data.action_hint;
    if (data.summary) text += ' · ' + (/^\s*[\[{]/.test(data.summary) ? '结果已记录，可展开查看' : data.summary);
    if (data.attempt > 1) text += ' · 第 ' + data.attempt + ' 次尝试';
    if (data.elapsed_ms > 0) text += ' · ' + options.elapsed(data.elapsed_ms);
    entry.querySelector('.term-msg').textContent = text;
    render(entry, data);
    if (callId && status !== 'running' && !entry.querySelector('.term-full-btn') && !entry.querySelector('.term-full')) {
      var more = node('button', 'term-full-btn', '查看原文'); more.type = 'button';
      more.addEventListener('click', function () { options.onFull(callId, entry, more); }); entry.appendChild(more);
    }
    options.log.scrollTop = options.log.scrollHeight;
    return {entry: entry, key: key, status: status};
  }
  function createProcessView(options) {
    var projection = root.CitrusTaskState.createProcess(), rows = new Map(), groups = new Map();
    var host = node('div', 'research-process-timeline'); options.container.appendChild(host);
    function draw() {
      var group = null, activeRows = new Set(), activeGroups = new Set();
      projection.entries.forEach(function (item) {
        var row = rows.get(item.id);
        if (!row) { row = node('div', item.kind === 'note' ? 'research-process-note' : 'term-line'); rows.set(item.id, row); }
        activeRows.add(item.id);
        if (item.kind === 'note') {
          row.textContent = item.text; host.appendChild(row); group = null; return;
        }
        if (!group) {
          group = groups.get(item.id);
          if (!group) { group = node('div', 'research-action-group terminal-log'); group.setAttribute('aria-label', '执行动作'); groups.set(item.id, group); }
          activeGroups.add(item.id); host.appendChild(group);
        }
        var data = item.data, status = data.status || 'running';
        row.className = 'term-line ' + (['running', 'resuming'].indexOf(status) >= 0 ? 'is-running'
          : ['complete', 'completed', 'no_results'].indexOf(status) >= 0 ? 'is-complete'
          : status === 'partial' ? 'is-partial' : ['skipped', 'cancelled', 'not_run'].indexOf(status) >= 0 ? 'is-skipped' : 'is-failed');
        row.dataset.attempt = item.attempt; row.dataset.operationId = data.logical_key || data.operation_id || data.tool_call_id || '';
        var title = options.clean(data.display_name || options.toolName(data.tool_name || data.name) || '科研任务');
        var text = title + ' · ' + (['running', 'resuming'].indexOf(status) >= 0 ? '处理中' : options.statusText(status, data.reason_code));
        var rawSummary = data.summary || data.result_summary || (data.outcome || {}).summary;
        var summary = /^\s*[\[{]/.test(rawSummary || '') ? '执行结果已记录' : options.clean(rawSummary);
        var hint = options.clean(data.action_hint);
        if (summary) text += ' · ' + summary;
        if (hint) text += ' · ' + hint;
        if (Number(item.attempt) > 1) text += ' · 第 ' + item.attempt + ' 次尝试';
        if (data.elapsed_ms > 0) text += ' · ' + options.elapsed(data.elapsed_ms);
        var msg = row.querySelector('.term-msg');
        if (!msg) { msg = node('span', 'term-msg'); row.appendChild(msg); }
        msg.textContent = text;
        // Only named public result facts enter the process; tool inputs, scripts
        // and intermediate tables never become prose or formal file references.
        var facts = {}, source = data.facts || (data.outcome || {}).facts || {};
        Object.keys(source).forEach(function (key) { if (labels[key]) { var value = options.clean(typeof source[key] === 'object' ? '' : source[key]); if (value) facts[key] = value; } });
        if (Object.keys(facts).length) render(row, {tool_name: data.tool_name, status: status, facts: facts});
        group.appendChild(row);
      });
      rows.forEach(function (row, id) { if (!activeRows.has(id)) { row.remove(); rows.delete(id); } });
      groups.forEach(function (groupNode, id) { if (!activeGroups.has(id)) { groupNode.remove(); groups.delete(id); } });
    }
    return {element: host, projection: projection,
      note: function (text, id) { var clean = options.clean(text); if (clean) { projection.note(clean, id); draw(); } },
      action: function (data) { projection.action(data); draw(); }};
  }
  root.CitrusTaskCards = {model: model, render: render, updateToolRow: updateToolRow, createProcessView: createProcessView};
}(typeof window !== 'undefined' ? window : globalThis));
