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
  function createQuestion(options) {
    var host = document.createElement('section'); host.className = 'task-question'; host.hidden = true;
    host.setAttribute('aria-label', '需要你的选择'); options.container.appendChild(host);
    var current = null, signature = '', busy = false, requestId = '';
    function render(state) {
      if (!options.owns()) return;
      var question = state.question;
      if (!question || !question.id) { host.hidden = true; return; }
      host.hidden = false;
      var key = JSON.stringify([question.id, question.version, question.status, question.prompt, question.options, question.multi_select, question.allow_text, question.child_status]);
      if (key === signature) return;
      current = question;
      signature = key; busy = false; requestId = '';
      host.replaceChildren();
      var heading = document.createElement('h3'); heading.textContent = '需要你的选择'; host.appendChild(heading);
      var prompt = document.createElement('p'); prompt.className = 'task-question-prompt'; prompt.textContent = String(question.prompt || '请选择后继续'); host.appendChild(prompt);
      var form = document.createElement('form'), choices = [], textInput = null;
      var pending = question.status === 'pending';
      var retry = question.status === 'answered' && question.child_status === 'queued' && question.answer;
      (question.options || []).forEach(function (option) {
        var label = document.createElement('label'); label.className = 'task-question-option';
        var input = document.createElement('input'); input.type = question.multi_select ? 'checkbox' : 'radio';
        input.name = 'question-' + question.id; input.value = String(option.id); input.disabled = !pending;
        input.checked = (question.selected_option_ids || (question.answer || {}).selected_option_ids || []).indexOf(option.id) >= 0;
        choices.push(input); label.appendChild(input);
        var copy = document.createElement('span'); copy.textContent = String(option.label || option.id);
        if (option.description) { var detail = document.createElement('small'); detail.textContent = String(option.description); copy.appendChild(detail); }
        label.appendChild(copy); form.appendChild(label);
      });
      if (question.allow_text) {
        var label = document.createElement('label'); label.className = 'task-question-custom'; label.textContent = '补充或自定义';
        textInput = document.createElement('textarea'); textInput.rows = 2; textInput.maxLength = 4000;
        textInput.setAttribute('aria-label', '补充或自定义回答'); textInput.disabled = !pending;
        textInput.value = question.text || (question.answer || {}).text || ''; label.appendChild(textInput); form.appendChild(label);
      }
      var actions = document.createElement('div'); actions.className = 'task-question-actions';
      var submit = document.createElement('button'); submit.type = 'submit'; submit.textContent = '确认并继续'; submit.disabled = !pending; actions.appendChild(submit);
      var cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = '取消'; cancel.disabled = !pending; actions.appendChild(cancel);
      if (retry) { submit.textContent = '重试继续'; submit.disabled = false; cancel.hidden = true; }
      if (pending || retry) form.appendChild(actions);
      var note = document.createElement('p'); note.className = 'task-question-status'; note.setAttribute('role', 'status');
      var statuses = {pending: '等待你的选择，任务暂未继续。', answered: '已记录你的选择。', cancelled: '已取消本次选择。', expired: '问题已过期，请重新发起请求。'};
      note.textContent = statuses[question.status] || '问题状态待确认。'; form.appendChild(note); host.appendChild(form);
      async function answer(cancelled) {
        if (!options.owns() || busy || current !== question || (!pending && !retry)) return;
        var selected = retry ? question.answer.selected_option_ids : choices.filter(function (input) { return input.checked; }).map(function (input) { return input.value; });
        var text = retry ? question.answer.text : (textInput ? textInput.value.trim() : '');
        if (!cancelled && !selected.length && !text) { note.textContent = '请选择一个选项或填写自定义回答。'; return; }
        if (!cancelled && !question.multi_select && selected.length > 1) { note.textContent = '请选择一个选项。'; return; }
        busy = true; submit.disabled = true; cancel.disabled = true;
        choices.forEach(function (input) { input.disabled = true; }); if (textInput) textInput.disabled = true;
        // Retried answers reuse their id until the user changes the payload.
        var payloadKey = JSON.stringify([selected, text, cancelled]);
        if (answer.payloadKey !== payloadKey) { requestId = 'q-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10); answer.payloadKey = payloadKey; }
        try {
          var result = await options.transport.answer(state.job_id || question.job_id, question.id, {
            version: question.version, selected_option_ids: cancelled ? [] : selected, text: cancelled ? '' : text,
            cancelled: cancelled, client_request_id: requestId
          });
          if (!options.owns() || !result) return;
          if (result.question) options.store.hydrate({job_id: state.job_id, question: result.question});
          if (result.child_job_id && result.question && result.question.child_status === 'queued') {
            note.textContent = '答复已保存，任务尚未启动；可重试继续。';
          } else if (result.child_job_id && options.onContinue) options.onContinue(result);
          else if (!result.question) note.textContent = '答复已提交，等待状态确认。';
        } catch (error) {
          if (!options.owns()) return;
          note.textContent = error.status === 409 ? '问题已更新或已答复；请核对最新内容后重试。' : error.message;
          if (error.status === 409) { try { await options.transport.refresh(state.job_id); } catch (_) {} }
        } finally {
          if (options.owns() && current === question) {
            busy = false; submit.disabled = !(pending || retry); cancel.disabled = !pending;
            choices.forEach(function (input) { input.disabled = !pending; }); if (textInput) textInput.disabled = !pending;
          }
        }
      }
      form.addEventListener('submit', function (event) { event.preventDefault(); return answer(false); });
      cancel.addEventListener('click', function () { return answer(true); });
    }
    var unsubscribe = options.store.subscribe(render); render(options.store.state);
    return {element: host, render: render, dispose: unsubscribe};
  }
  root.CitrusTaskControls = {create: create, createQuestion: createQuestion};
}(typeof window !== 'undefined' ? window : globalThis));
