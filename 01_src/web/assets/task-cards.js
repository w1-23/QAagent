(function (root) {
  'use strict';
  var labels = {method: '方法', statistic: '统计量', pvalue: 'P 值', p_value: 'P 值', n: '样本量',
    sample_size: '样本量', effect_size: '效应量', assumptions: '适用条件', coverage: '检查范围',
    findings: '检查发现', limitations: '限制', warnings: '提示', status: '状态', summary: '结果摘要',
    figure_id: '图件编号', title: '标题', render_version: '保存版本', artifact_id: '文件编号',
    object: '处理对象', objects: '处理对象', action: '动作', result: '结果', gaps: '未完成项',
    targets: '目标', records: '记录', counts: '条目统计', checked: '已检查', verified: '已核实',
    total: '总数', selected: '已选择', remaining: '剩余', missing: '缺失', files: '文件',
    sheets: '工作表', rows: '记录', columns: '字段', checks: '检查项', duplicates: '重复项',
    reason: '原因', reason_code: '原因标识', recovery: '恢复建议', recoverable: '可恢复',
    file_name: '文件名', file_index: '文件序号', record_id: '记录编号', doi: 'DOI',
    inspected_files: '已审查文件', unreviewed_files: '未审查文件', accepted_artifacts: '已交付文件'};
  function node(tag, cls, text) {
    var el = document.createElement(tag); if (cls) el.className = cls;
    if (text !== undefined) el.textContent = String(text); return el;
  }
  function valueText(value) {
    if (value === null || value === undefined) return '未提供';
    if (typeof value === 'boolean') return value ? '是' : '否';
    if (Array.isArray(value)) return value.map(valueText).join('；');
    if (typeof value === 'object') return Object.keys(value).map(function (key) {
      return (labels[key] || key) + '：' + valueText(value[key]);
    }).join('；');
    return String(value);
  }
  function renderValue(container, value) {
    if (Array.isArray(value)) {
      var list = node('ul', 'task-fact-list');
      value.forEach(function (item) { var row = node('li'); renderValue(row, item); list.appendChild(row); });
      if (value.length) container.appendChild(list); else container.textContent = '无';
    } else if (value && typeof value === 'object') {
      var fields = node('dl', 'task-result-facts');
      Object.keys(value).forEach(function (key) {
        fields.appendChild(node('dt', '', labels[key] || key));
        var detail = node('dd'); renderValue(detail, value[key]); fields.appendChild(detail);
      });
      if (fields.children.length) container.appendChild(fields); else container.textContent = '无';
    } else container.textContent = valueText(value);
  }
  function publicValue(value) {
    if (Array.isArray(value)) return value.map(publicValue).filter(function (item) { return item !== undefined; });
    if (!value || typeof value !== 'object') return value;
    if (value.internal === true || value.is_intermediate === true || value.formal_attachment === false ||
        ['internal', 'private', 'intermediate'].indexOf(String(value.visibility || '').toLowerCase()) >= 0 ||
        ['internal', 'intermediate', 'working'].indexOf(String(value.purpose || value.role || '').toLowerCase()) >= 0) return undefined;
    var result = {};
    Object.keys(value).forEach(function (key) {
      if (/^(script|source_code|code|raw_result|provider_reasoning|private_reasoning|reasoning|inputs|tool_args|args_json)$/i.test(key)) return;
      var child = publicValue(value[key]); if (child !== undefined) result[key] = child;
    });
    return result;
  }
  function model(data) {
    var outcome = data.outcome && typeof data.outcome === 'object' ? data.outcome : {};
    var facts = data.facts != null ? data.facts : outcome.facts;
    facts = Array.isArray(facts) ? {records: facts} : Object.assign({}, facts && typeof facts === 'object' ? facts : {});
    ['object', 'objects', 'action', 'result', 'gaps', 'reason', 'reason_code', 'recovery', 'recoverable'].forEach(function (key) {
      var value = data[key] != null ? data[key] : outcome[key];
      if (value != null && value !== '' && facts[key] == null) facts[key] = value;
    });
    var refs = data.result_refs != null ? data.result_refs : outcome.result_refs || outcome.artifacts || data.artifacts;
    if (refs && !Array.isArray(refs)) refs = [refs];
    facts = publicValue(facts) || {}; refs = publicValue(refs);
    if ((!facts || typeof facts !== 'object' || !Object.keys(facts).length) && (!Array.isArray(refs) || !refs.length)) return null;
    var name = String(data.tool_name || data.name || data.display_name || '');
    var kind = String(data.kind || outcome.kind || '');
    var title = /statistic|statistical|describe_data|统计/.test(name + kind) ? '统计分析'
      : /audit|integrity|审查|数据检查/.test(name + kind) ? '数据审查'
      : /figure|plot|绘图|图件/.test(name + kind) ? '图件'
      : /verif|reference|文献|核验/.test(name + kind) ? '文献核验'
      : /search|retriev|检索|候选/.test(name + kind) ? '资料检索' : '步骤结果';
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
      var value = node('dd'); renderValue(value, result.facts[key]); facts.appendChild(value);
    });
    if (facts.children.length) card.appendChild(facts);
    if (result.refs.length) {
      var list = node('ul', 'task-result-refs');
      result.refs.forEach(function (ref) {
        // References are server evidence, never guessed artifact/download URLs.
        var text = typeof ref === 'string' ? ref : ref && (ref.label || ref.title || ref.file_name || ref.ref_id || ref.artifact_id || ref.file_id) || valueText(ref);
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
    var projection = root.CitrusTaskState.createProcess(), rows = new Map(), groups = new Map(), settled = false;
    // Public model paragraphs and real operations are the complete transcript.
    // Stage labels and inferred plans are not conversation content.
    var host = node('div', 'research-process-timeline'); options.container.appendChild(host);
    function owns(row) { return (!options.owns || options.owns()) && host.contains(row); }
    function addTextSection(container, title, text) {
      container.appendChild(node('div', 'research-tool-section-title', title));
      container.appendChild(node('pre', 'term-full', text));
    }
    function details(row, item) {
      var data = item.data, callId = String(data.tool_call_id || '');
      if (!callId || !options.loadToolResult) return;
      var state = row._toolDetails;
      if (state && state.callId !== callId) { state.element.remove(); state.button.remove(); state = null; }
      if (!state) {
        // Optional records are a sibling of the activity, never another card
        // or disclosure nested under the tool row. Keep one visual left edge.
        var detail = node('div', 'research-tool-details'); detail.hidden = true;
        var button = node('button', 'research-tool-toggle', '查看记录'); button.type = 'button';
        button.setAttribute('aria-expanded', 'false');
        button.setAttribute('aria-label', '查看' + (data.display_name || options.toolName(data.tool_name || data.name) || '工具') + '的参数、结果与错误');
        row.appendChild(button);
        var body = detail;
        state = {element: detail, button: button, body: body, callId: callId, loading: false, loaded: false, status: data.status};
        row._toolDetails = state;
        function retry(text) {
          body.replaceChildren(node('div', 'research-tool-notice', text));
          var button = node('button', 'research-tool-retry', '重新读取'); button.type = 'button';
          button.addEventListener('click', load); body.appendChild(button);
        }
        async function load() {
          if (detail.hidden || state.loading || state.loaded || !owns(row)) return;
          var requestedStatus = state.status;
          state.loading = true; body.textContent = '正在读取工具记录…';
          body.setAttribute('aria-busy', 'true');
          try {
            var result = await options.loadToolResult(callId);
            if (!owns(row) || row._toolDetails !== state || !result) return;
            body.replaceChildren();
            addTextSection(body, '参数', result.args_json || '未留存参数记录');
            addTextSection(body, '结果', result.result_text || '未留存结果正文');
            if (result.error_text) addTextSection(body, '错误', result.error_text);
            if (result.truncated) body.appendChild(node('div', 'research-tool-notice',
              '内容已截断，仅展示已留存部分。' + (result.result_chars ? '记录长度：' + result.result_chars + ' 字符。' : '')));
            state.loaded = true;
          } catch (error) {
            if (!owns(row) || row._toolDetails !== state) return;
            retry(error.status === 404 ? '该工具结果未留存或已轮转，当前摘要仍可查看。'
              : error.status === 403 || error.status === 401 ? '无权读取此会话的工具记录。' : '工具记录读取失败，请重试。');
          } finally {
            state.loading = false;
            if (owns(row)) {
              body.removeAttribute('aria-busy');
              if (requestedStatus !== state.status) { state.loaded = false; state.load(); }
            }
          }
        }
        state.load = load;
        button.addEventListener('click', function () {
          detail.hidden = !detail.hidden;
          button.setAttribute('aria-expanded', String(!detail.hidden));
          button.textContent = detail.hidden ? '查看记录' : '收起记录';
          if (!detail.hidden) load();
        });
      }
      // A result may settle while its records are visible after an earlier 404.
      if (state.status !== data.status) {
        state.status = data.status; state.loaded = false;
        if (!state.element.hidden) state.load();
      }
    }
    function place(parent, child, index) {
      // Updating an existing action must not detach a focused details control.
      var before = parent.children[index];
      if (before === child) return;
      if (before) parent.insertBefore(child, before); else parent.appendChild(child);
    }
    function draw() {
      var hostIndex = 0, activeRows = new Set(), activeGroups = new Set(), group = null, groupIndex = 0, groupCount = 0;
      function finishGroup() {
        if (group) group.summary.textContent = '使用了 ' + groupCount + ' 个工具';
      }
      projection.entries.forEach(function (item) {
        var row = rows.get(item.id);
        if (!row) { row = node('div', item.kind === 'note' ? 'research-process-note' : 'term-line'); rows.set(item.id, row); }
        activeRows.add(item.id);
        row.removeAttribute('aria-current');
        if (item.kind === 'note') {
          row.textContent = item.text;
          row.dataset.noteId = item.note_id; row.dataset.phase = item.phase; row.dataset.stepId = item.step_id;
          row.dataset.modelStep = item.model_step == null ? '' : String(item.model_step);
          row.dataset.toolCallIds = JSON.stringify(item.tool_call_ids);
          finishGroup(); group = null;
          place(host, row, hostIndex++); return;
        }
        if (!group) {
          group = groups.get(item.id);
          if (!group) {
            var disclosure = node('details', 'research-action-group'); disclosure.open = false;
            var heading = node('summary', 'research-action-summary');
            var list = node('div', 'research-action-list'); disclosure.appendChild(heading); disclosure.appendChild(list);
            group = {element: disclosure, summary: heading, body: list}; groups.set(item.id, group);
          }
          activeGroups.add(item.id); groupIndex = 0; groupCount = 0;
          place(host, group.element, hostIndex++);
        }
        groupCount++;
        var data = item.data, status = data.status || 'running';
        var resolution = item.resolution && ['failed', 'partial', 'error', 'not_run'].indexOf(status) >= 0 ? item.resolution : null;
        row.dataset.resolution = resolution ? resolution.resolution : '';
        row.dataset.resolvedBy = resolution ? resolution.resolved_by_tool_call_id : '';
        row.className = 'term-line ' + (['running', 'resuming'].indexOf(status) >= 0 ? 'is-running'
          : ['complete', 'completed', 'no_results'].indexOf(status) >= 0 ? 'is-complete'
          : status === 'partial' ? 'is-partial' : ['skipped', 'cancelled', 'not_run'].indexOf(status) >= 0 ? 'is-skipped' : 'is-failed');
        var running = ['running', 'resuming'].indexOf(status) >= 0;
        if (resolution) row.className = 'term-line is-resolved';
        if (settled && running) row.className = 'term-line is-unconfirmed';
        if (!settled && running) row.setAttribute('aria-current', 'step');
        row.dataset.toolCallId = data.tool_call_id || ''; row.dataset.stepId = data.step_id || '';
        row.dataset.attempt = item.attempt; row.dataset.operationId = data.logical_key || data.operation_id || data.tool_call_id || '';
        var title = options.clean(data.display_name || options.toolName(data.tool_name || data.name) || '科研任务');
        var text = title + ' · ' + (resolution ? resolution.resolution === 'repaired' ? '已修复' : '已由后续结果替代'
          : running ? settled ? '未记录结束状态' : '处理中' : options.statusText(status, data.reason_code));
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
        // Keep actual operations under their disclosure; records load only
        // after the user explicitly expands a tool's parameters/results.
        place(group.body, row, groupIndex++);
        details(row, item);
        if (row._toolDetails) place(group.body, row._toolDetails.element, groupIndex++);
      });
      finishGroup();
      groups.forEach(function (value, id) { if (!activeGroups.has(id)) { value.element.remove(); groups.delete(id); } });
      rows.forEach(function (row, id) {
        if (!activeRows.has(id)) {
          if (row._toolDetails) row._toolDetails.element.remove();
          row.remove(); rows.delete(id);
        }
      });
    }
    return {element: host, projection: projection,
      note: function (text, id, metadata) {
        var clean = (metadata && metadata.public_note && options.publicNote ? options.publicNote : options.clean)(text);
        if (clean) { projection.note(clean, id, metadata); draw(); }
      },
      plan: function (data) { if (projection.plan(data)) draw(); },
      terminal: function (value) { settled = value !== false; draw(); },
      action: function (data) { projection.action(data); draw(); },
      resolution: function (data) { if (projection.resolution(data)) draw(); },
      progress: function (data) {
        // Progress can update an existing real operation, never invent a tool.
        var keys = [data.tool_call_id, data.operation_id, data.logical_key].filter(Boolean).map(String);
        if (keys.length && projection.entries.some(function (entry) { return entry.kind === 'action' && keys.some(function (key) { return entry.aliases.indexOf(key) >= 0; }); })) {
          projection.action(data); draw();
        }
      }};
  }
  root.CitrusTaskCards = {model: model, render: render, updateToolRow: updateToolRow, createProcessView: createProcessView};
}(typeof window !== 'undefined' ? window : globalThis));
