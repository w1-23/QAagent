(function (root) {
  'use strict';
  function create(options) {
    var host = document.createElement('div'); host.className = 'task-controls'; host.hidden = true;
    var edit = document.createElement('details'); edit.className = 'task-update';
    var title = document.createElement('summary'); title.textContent = '调整本轮任务'; edit.appendChild(title);
    var form = document.createElement('form'), label = document.createElement('label');
    label.textContent = '补充或修改要求';
    var input = document.createElement('textarea'); input.rows = 2; input.maxLength = 2000;
    input.setAttribute('aria-label', '补充或修改本轮任务要求'); label.appendChild(input); form.appendChild(label);
    var hint = document.createElement('p'); hint.className = 'task-control-hint';
    hint.textContent = '调整在后续步骤生效，已完成的结果保留。'; form.appendChild(hint);
    var submit = document.createElement('button'); submit.type = 'submit'; submit.textContent = '提交调整'; form.appendChild(submit);
    edit.appendChild(form); host.appendChild(edit);
    var message = document.createElement('p'); message.className = 'task-control-status';
    message.setAttribute('role', 'status'); host.appendChild(message);
    var recover = document.createElement('div'); recover.className = 'task-recovery'; host.appendChild(recover);
    options.container.appendChild(host);
    var busy = false, recoveryKey = '', requestNote = '', acceptedVersion = 0;
    function alive() { return options.owns(); }
    function render(state) {
      if (!alive()) return;
      host.hidden = !state.job_id;
      var active = ['running', 'detached'].indexOf(state.status) >= 0 && !root.CitrusTaskState.isTerminal(state);
      edit.hidden = !active; submit.disabled = busy || !active;
      var notApplied = state.not_applied_updates || [];
      if (acceptedVersion > 0 && (state.applied_version >= acceptedVersion || notApplied.some(function (item) { return item.version === acceptedVersion; }))) { requestNote = ''; acceptedVersion = 0; }
      var pending = state.pending_updates.length;
      message.textContent = requestNote || (pending ? '有 ' + pending + ' 条调整待应用。'
        : state.applied_version > 0 ? '已应用第 ' + state.applied_version + ' 版要求。' : '');
      if (notApplied.length) message.textContent += (message.textContent ? ' ' : '') + notApplied.map(function (item) {
        return '第 ' + item.version + ' 版调整未应用' + (item.reason === 'service_restart' ? '（服务重启）' : '') + '。';
      }).join(' ');
      var eligible = root.CitrusTaskState.resumable(state);
      var key = JSON.stringify(eligible.map(function (op) { return [op.operation_id, op.status, op.resumable]; }));
      if (key !== recoveryKey) {
        recoveryKey = key; recover.replaceChildren();
        eligible.forEach(function (op) {
          var button = document.createElement('button'); button.type = 'button';
          button.textContent = '恢复此步骤'; button.setAttribute('aria-label', '恢复中断步骤：' + (op.display_name || op.tool_name || op.operation_id));
          button.onclick = async function () {
            if (!alive() || button.disabled) return;
            button.disabled = true;
            try {
              var result = await options.transport.post(state.job_id, 'resume', {operation_id: op.operation_id});
              if (!alive() || !result) return;
              var outcomes = {complete: '此步骤已恢复', no_results: '此步骤已恢复，未找到结果', partial: '此步骤恢复了部分结果', failed: '步骤恢复未完成', cancelled: '步骤恢复已停止'};
              requestNote = (outcomes[result.operation_status] || '步骤恢复请求已处理') + '；整轮任务的原有状态保留。';
              if (outcomes[result.operation_status]) options.store.hydrate({job_id: state.job_id,
                operations: state.operations.map(function (item) { return item.operation_id === op.operation_id ? Object.assign({}, item, {status: result.operation_status, resumable: false}) : item; })});
              try { await options.transport.refresh(state.job_id); }
              catch (_) { if (alive()) requestNote += ' 暂时无法刷新步骤记录。'; }
            } catch (error) { if (alive()) requestNote = error.message; }
            finally { if (alive()) { button.disabled = false; render(options.store.state); } }
          };
          recover.appendChild(button);
        });
        if (eligible.length) {
          var note = document.createElement('p'); note.className = 'task-control-hint';
          note.textContent = '仅恢复支持的本地读取或计算步骤；不会重新执行整轮任务。'; recover.appendChild(note);
        }
      }
    }
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      var state = options.store.state, instruction = input.value.trim();
      if (!alive() || busy || submit.disabled || !instruction) return;
      busy = true; requestNote = ''; render(state);
      try {
        var result = await options.transport.post(state.job_id, 'updates', {expected_version: state.task_version, instruction: instruction});
        if (!alive() || !result) return;
        requestNote = '调整已提交，等待后续步骤应用。'; input.value = '';
        if (Number.isSafeInteger(result.version)) {
          acceptedVersion = result.version;
          options.store.hydrate({job_id: state.job_id, task_version: result.version,
            pending_updates: state.pending_updates.filter(function (item) { return item.version !== result.version; }).concat(result.version > state.applied_version ? [result] : [])});
        }
        try { await options.transport.refresh(state.job_id); }
        catch (_) { if (alive()) requestNote = '调整已提交；暂时无法刷新应用状态。'; }
      } catch (error) {
        if (!alive()) return;
        requestNote = error.message;
        if (error.status === 409) {
          try { await options.transport.refresh(state.job_id); } catch (_) { /* preserve draft and conflict explanation */ }
        }
      } finally { if (alive()) { busy = false; render(options.store.state); } }
    });
    var unsubscribe = options.store.subscribe(render); render(options.store.state);
    return {element: host, render: render, dispose: unsubscribe};
  }
  root.CitrusTaskControls = {create: create};
}(typeof window !== 'undefined' ? window : globalThis));
