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
      if (options.log.children.length > 100) options.log.removeChild(options.log.firstChild);
    }
    var status = data.status || 'running';
    entry.classList.remove('is-running', 'is-complete', 'is-partial', 'is-failed', 'is-skipped');
    entry.classList.add(status === 'running' ? 'is-running' : status === 'complete' || status === 'no_results' ? 'is-complete'
      : status === 'partial' ? 'is-partial' : ['skipped', 'not_run', 'cancelled'].indexOf(status) >= 0 ? 'is-skipped' : 'is-failed');
    var text = status === 'running' ? options.activity(data.display_name || '科研任务') : options.statusText(status, data.reason_code);
    if (data.action_hint) text += ' · ' + data.action_hint;
    if (data.summary) text += ' · ' + data.summary;
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
  root.CitrusTaskCards = {model: model, render: render, updateToolRow: updateToolRow};
}(typeof window !== 'undefined' ? window : globalThis));
