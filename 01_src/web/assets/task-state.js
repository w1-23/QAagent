(function (root) {
  'use strict';
  var terminal = ['completed', 'partial', 'failed', 'cancelled'];
  function displayStatus(data) {
    var envelope = data && (data.turn_envelope || data.terminal_snapshot);
    var status = String(envelope && envelope.display_status || data && (data.display_status || data.turn_status) || '');
    return terminal.indexOf(status) >= 0 ? status : '';
  }
  function snapshotStatus(job) {
    return displayStatus(job) || (job && terminal.indexOf(job.status) >= 0 ? job.status : '');
  }
  function list(value) { return Array.isArray(value) ? value : []; }
  // Event arrival is ordered separately from the public process projection.
  // All aliases identify one logical operation; attempts remain separate facts.
  function createProcess() {
    var entries = [], actions = [], seen = new Set(), nextId = 0, currentPlan = null, resolutions = new Map();
    function resolution(data) {
      var changed = false;
      list(data && data.resolutions).forEach(function (value) {
        if (!value || ['repaired', 'source_replaced', 'superseded'].indexOf(value.resolution) < 0 ||
            typeof value.tool_call_id !== 'string' || !value.tool_call_id.trim() ||
            typeof value.resolved_by_tool_call_id !== 'string' || !value.resolved_by_tool_call_id.trim() ||
            value.tool_call_id === value.resolved_by_tool_call_id) return;
        var previous = resolutions.get(value.tool_call_id);
        if (previous && Number.isSafeInteger(previous.seq) && Number.isSafeInteger(data.seq) && data.seq <= previous.seq) return;
        var record = {tool_call_id: value.tool_call_id, resolved_by_tool_call_id: value.resolved_by_tool_call_id,
          resolution: value.resolution, seq: data.seq};
        resolutions.set(value.tool_call_id, record);
        actions.forEach(function (item) { if (item.data.tool_call_id === record.tool_call_id) item.resolution = record; });
        changed = true;
      });
      return changed;
    }
    function note(text, id, metadata) {
      metadata = metadata || {};
      if (typeof text !== 'string' || metadata.visibility !== 'public' || metadata.origin !== 'model' || metadata.internal === true || metadata.private === true) return null;
      var identity = metadata.note_id ? 'note:' + metadata.note_id : id;
      if (!text || identity && seen.has(identity)) return null;
      if (identity) seen.add(identity);
      var last = entries[entries.length - 1];
      if (!identity && last && last.kind === 'note' && last.text === text) return last;
      var item = {kind: 'note', id: ++nextId, text: text,
        note_id: metadata.note_id || '', phase: metadata.phase || '', step_id: metadata.step_id || '',
        model_step: metadata.model_step, tool_call_ids: list(metadata.tool_call_ids).map(String)};
      entries.push(item); return item;
    }
    function plan(data) {
      if (!data || !data.plan_id || !Array.isArray(data.steps)) return false;
      var statuses = ['pending', 'in_progress', 'completed', 'blocked', 'cancelled'];
      var ids = new Set();
      if (data.steps.some(function (step) {
        if (!step || !step.id || typeof step.title !== 'string' || !step.title.trim() || statuses.indexOf(step.status) < 0 || ids.has(String(step.id))) return true;
        ids.add(String(step.id)); return false;
      })) return false;
      if (currentPlan) {
        if (String(data.plan_id) !== currentPlan.plan_id) return false;
        if (Number.isSafeInteger(data.seq) && Number.isSafeInteger(currentPlan.seq) && data.seq <= currentPlan.seq) return false;
        if (Number.isSafeInteger(data.version) && Number.isSafeInteger(currentPlan.version) && data.version < currentPlan.version) return false;
      }
      currentPlan = {plan_id: String(data.plan_id), seq: data.seq, version: data.version,
        steps: data.steps.map(function (step) { return {id: String(step.id), title: step.title, status: step.status}; })};
      return true;
    }
    function action(data) {
      var keys = [data.logical_key, data.operation_id, data.tool_call_id].concat(list(data.aliases)).filter(Boolean).map(String);
      var attempt = String(data.attempt || data.attempts || 1);
      var matches = actions.filter(function (item) { return item.attempt === attempt && keys.some(function (key) { return item.aliases.indexOf(key) >= 0; }); });
      var item = matches[0];
      if (!item) {
        item = {kind: 'action', id: ++nextId, attempt: attempt, aliases: [], data: {}};
        entries.push(item); actions.push(item);
      }
      // A later event can bridge a tool_call_id and operation_id already seen.
      matches.slice(1).forEach(function (duplicate) {
        item.data = Object.assign({}, duplicate.data, item.data);
        item.aliases = item.aliases.concat(duplicate.aliases);
        entries.splice(entries.indexOf(duplicate), 1); actions.splice(actions.indexOf(duplicate), 1);
      });
      keys.forEach(function (key) { if (item.aliases.indexOf(key) < 0) item.aliases.push(key); });
      var incoming = Object.assign({}, data);
      // An alias start arriving after a result must not erase its outcome.
      if (['running', 'resuming'].indexOf(incoming.status) >= 0 && item.data.status && ['running', 'resuming'].indexOf(item.data.status) < 0) delete incoming.status;
      item.data = Object.assign({}, item.data, incoming);
      item.resolution = resolutions.get(item.data.tool_call_id) || null;
      return item;
    }
    return {entries: entries, note: note, action: action, plan: plan, resolution: resolution, currentPlan: function () { return currentPlan; }};
  }
  function newerQuestion(previous, incoming) {
    if (!incoming || !incoming.id) return previous;
    if (previous && previous.id === incoming.id) {
      if (Number(incoming.version || 0) < Number(previous.version || 0)) return previous;
      if (Number(incoming.version || 0) === Number(previous.version || 0) && previous.status && previous.status !== 'pending' && incoming.status === 'pending') return previous;
    }
    return Object.assign({}, incoming);
  }
  // One projection for live events, replay and persisted job snapshots. Usage
  // can attach only to the exact logical-call/attempt pair that produced it.
  function createContext(options) {
    options = options || {};
    var records = new Map(), latest = '', latestSeq = 0, legacy = null, loadStatus = 'unknown';
    function trim() {
      if (records.size > 64) {
        var oldest = Array.from(records.keys()).find(function (id) { return id !== latest; });
        if (oldest) records.delete(oldest);
      }
    }
    function identity(data) {
      if (!data.logical_call_id) return '';
      return data.attempt_id ? String(data.logical_call_id) + '/' + String(data.attempt_id)
        : data.snapshot_version ? String(data.logical_call_id) + '/not_admitted' : '';
    }
    function receive(type, data) {
      data = data || {};
      // Reject foreign context before caching, not only before rendering. A
      // later valid event must never reveal a previously rejected snapshot.
      if (options.request_id && data.request_id && data.request_id !== options.request_id ||
          options.session_id && data.session_id && data.session_id !== options.session_id) return current();
      if (data.history_load_status) loadStatus = data.history_load_status;
      if (type === 'context_usage') {
        var usageKey = identity(data), record = records.get(usageKey);
        if (!usageKey || !data.attempt_id || !data.call || data.scope !== 'single_call') return current();
        // Do not select a call just because its response arrived last.
        if (!record) { record = {}; records.set(usageKey, record); }
        var knownSeq = Math.max(record.usageSeq || 0, record.snapshot && record.snapshot.seq || 0);
        // Hydration may already include a later settled observation. Replaying
        // older usage must not replace it, even before the store catches up.
        if (knownSeq && (!data.seq || data.seq <= knownSeq)) return current();
        record.usageSeq = data.seq || 0;
        record.usage = Object.assign({}, data.call, {logical_call_id: data.logical_call_id, attempt_id: data.attempt_id, scope: 'single_call'});
        if (record.snapshot) record.snapshot = Object.assign({}, record.snapshot, {provider_usage: record.usage, usage_status: 'reported'});
        trim();
        return current();
      }
      if (['context_snapshot', 'context_budget', 'context_status'].indexOf(type) < 0) return current();
      var key = identity(data);
      if (!key) {
        // Old rows can be displayed as historical estimates, never relabelled
        // as the current complete model request or joined to unrelated usage.
        legacy = Object.assign({}, data, {estimate_scope: 'historical_estimate',
          estimate_label: '历史估算（缺少调用身份）', provider_usage: {}, usage_status: 'unknown',
          estimated_tokens: data.estimated_input_tokens != null ? data.estimated_input_tokens : data.estimated_tokens,
          model_window: data.model_window || null, history_load_status: data.history_load_status || loadStatus});
        return current();
      }
      var previous = records.get(key);
      if (previous && previous.snapshot && previous.snapshot.seq && (!data.seq || previous.snapshot.seq > data.seq)) return current();
      var snapshot = Object.assign({}, data, {
        estimated_tokens: data.estimated_input_tokens != null ? data.estimated_input_tokens : data.estimated_tokens,
        model_window: data.model_window || null, history_load_status: data.history_load_status || loadStatus
      });
      var usage = data.provider_usage || data.usage;
      if (previous && previous.usageSeq > (data.seq || 0)) {
        // A response can arrive before its older snapshot. The snapshot's
        // embedded usage has that snapshot's seq, not the newer response's.
        snapshot.provider_usage = previous.usage || {};
        snapshot.usage_status = 'reported';
      } else if (usage && usage.logical_call_id === data.logical_call_id && usage.attempt_id === data.attempt_id) {
        snapshot.provider_usage = Object.assign({}, usage);
      } else snapshot.provider_usage = previous && previous.usage || {};
      // Replayed starts cannot erase a settled snapshot hydrated from storage.
      if (previous && previous.snapshot && ['complete', 'failed', 'cancelled'].indexOf(previous.snapshot.status) >= 0 &&
          ['estimated', 'running', 'admitted'].indexOf(snapshot.status) >= 0) return current();
      records.set(key, {snapshot: snapshot, usage: snapshot.provider_usage,
        usageSeq: Math.max(data.seq || 0, previous && previous.usageSeq || 0)});
      // Selection follows authoritative snapshot order, matching the durable
      // job projection; a usage-only response never changes the selected call.
      if (!latestSeq || data.seq >= latestSeq) { latest = key; latestSeq = data.seq || 0; }
      trim();
      return current();
    }
    function current() { var record = records.get(latest); return record && record.snapshot || legacy; }
    return {receive: receive, current: current};
  }
  function create(options) {
    options = options || {};
    var state = {job_id: '', cursor: 0, task_version: 0, applied_version: 0, status: '',
      display_status: '', terminal_snapshot: null, question: null, pending_updates: [], not_applied_updates: [], operations: [], approvals: []};
    var pending = new Map(), subscribers = [], context = createContext(options);
    state.context_snapshot = null;
    function notify() { subscribers.slice().forEach(function (fn) { fn(state); }); }
    function hydrate(job) {
      if (!job || job.job_id && state.job_id && job.job_id !== state.job_id) return false;
      if (Number.isSafeInteger(job.event_cursor) && job.event_cursor < state.cursor) return false;
      if (Number.isSafeInteger(job.task_version) && job.task_version < state.task_version) return false;
      if (job.job_id) state.job_id = String(job.job_id);
      ['task_version', 'applied_version'].forEach(function (key) {
        if (Number.isSafeInteger(job[key]) && job[key] >= state[key]) state[key] = job[key];
      });
      ['pending_updates', 'operations', 'approvals'].forEach(function (key) {
        if (Array.isArray(job[key])) state[key] = job[key].slice();
        if (key === 'pending_updates') state.pending_updates = state.pending_updates.filter(function (item) {
          return item.version > state.applied_version && !state.not_applied_updates.some(function (update) { return update.version === item.version; });
        });
      });
      var savedContext = job.context_snapshot || (job.terminal_snapshot || {}).context_snapshot;
      if (savedContext) state.context_snapshot = context.receive('context_snapshot', savedContext);
      if (job.question) state.question = newerQuestion(state.question, job.question);
      if (job.status && !state.display_status) state.status = String(job.status);
      var settled = snapshotStatus(job);
      if (settled) {
        state.terminal_snapshot = displayStatus(job) ? job.terminal_snapshot || null : null;
        state.display_status = settled;
        state.status = settled;
      }
      notify(); return true;
    }
    function reduce(type, data) {
      if (['context_snapshot', 'context_status', 'context_budget', 'context_usage'].indexOf(type) >= 0) {
        state.context_snapshot = context.receive(type, data);
      }
      if (data.context_snapshot) state.context_snapshot = context.receive('context_snapshot', data.context_snapshot);
      if (data.question) state.question = newerQuestion(state.question, data.question);
      if (data.turn_envelope && data.turn_envelope.display_status === 'awaiting_input' && !state.display_status) state.status = 'awaiting_input';
      if (data.job_id) state.job_id = String(data.job_id);
      ['task_version', 'applied_version'].forEach(function (key) {
        if (Number.isSafeInteger(data[key])) state[key] = Math.max(state[key], data[key]);
      });
      if (type === 'job') state.status = String(data.status || 'running');
      if (type === 'agent_recovery' && data.status === 'admitted' && Number.isSafeInteger(data.seq)) {
        state.status = 'running'; state.display_status = ''; state.terminal_snapshot = null;
        var reopened = Array.isArray(data.reactivated_updates) ? data.reactivated_updates.filter(function (item) {
          return Number.isSafeInteger(item.version) && item.version > state.applied_version;
        }) : [];
        state.not_applied_updates = state.not_applied_updates.filter(function (item) {
          return !reopened.some(function (update) { return update.version === item.version; });
        });
        reopened.forEach(function (item) {
          state.pending_updates = state.pending_updates.filter(function (update) { return update.version !== item.version; });
          state.pending_updates.push(Object.assign({}, item, {status: 'pending'}));
        });
      }
      if (type === 'task_update' && Number.isSafeInteger(data.version)) {
        state.task_version = Math.max(state.task_version, data.version);
        state.pending_updates = state.pending_updates.filter(function (item) { return item.version !== data.version; });
        if (data.status === 'pending' && data.version > state.applied_version && !state.not_applied_updates.some(function (item) { return item.version === data.version; })) state.pending_updates.push(data);
        if (data.status === 'not_applied') {
          state.not_applied_updates = state.not_applied_updates.filter(function (item) { return item.version !== data.version; });
          state.not_applied_updates.push(data);
        }
        if (data.status === 'applied') {
          state.applied_version = Math.max(state.applied_version, data.version);
          state.pending_updates = state.pending_updates.filter(function (item) { return item.version > state.applied_version; });
        }
      }
      if (type === 'operation' && data.operation_id) {
        var previous = state.operations.find(function (item) { return item.operation_id === data.operation_id; });
        state.operations = state.operations.filter(function (item) { return item.operation_id !== data.operation_id; });
        state.operations.push(Object.assign({}, previous || {}, data));
      }
      if (type === 'permission_request' && data.approval_id) {
        state.approvals = state.approvals.filter(function (item) { return item.approval_id !== data.approval_id; });
        state.approvals.push(Object.assign({status: 'pending'}, data));
      }
      if (type === 'approval_decided' || type === 'permission_decision') {
        state.approvals = state.approvals.map(function (item) {
          return item.approval_id === data.approval_id ? Object.assign({}, item, data) : item;
        });
      }
      if (type === 'request_user_input' || type === 'question' || type === 'question_answered' || type === 'question_cancelled') {
        state.question = newerQuestion(state.question, data.question || data);
        if (state.question && state.question.status === 'pending' && !state.display_status) state.status = 'awaiting_input';
      }
      if ((type === 'done' || type === 'error') && displayStatus(data)) {
        state.display_status = displayStatus(data);
        state.terminal_snapshot = data.turn_envelope || data.terminal_snapshot || null;
        state.status = state.display_status;
      }
    }
    function dispatch(frame) {
      reduce(frame.event, frame.data);
      if (options.onEvent) options.onEvent(frame.event, frame.data);
    }
    function receive(type, data, eventId) {
      data = data && typeof data === 'object' ? data : {};
      if (data.job_id && state.job_id && data.job_id !== state.job_id) return false;
      var seq = data.seq;
      if (seq === undefined && eventId && /^\d+$/.test(String(eventId))) seq = Number(eventId);
      if (seq === undefined) { dispatch({event: type, data: data}); notify(); return true; }
      if (!Number.isSafeInteger(seq) || seq <= state.cursor || seq < 1) return false;
      if (eventId && /^\d+$/.test(String(eventId)) && Number(eventId) !== seq) return false;
      if (!pending.has(seq)) pending.set(seq, {event: type, data: Object.assign({}, data, {seq: seq})});
      while (pending.has(state.cursor + 1)) {
        var frame = pending.get(state.cursor + 1);
        pending.delete(++state.cursor);
        dispatch(frame);
      }
      notify(); return true;
    }
    return {state: state, receive: receive, hydrate: hydrate,
      hasGap: function () { return pending.size > 0; },
      subscribe: function (fn) { subscribers.push(fn); return function () { subscribers = subscribers.filter(function (item) { return item !== fn; }); }; },
      clear: function () {
        pending.clear(); subscribers.length = 0; context = createContext(options); state.context_snapshot = null;
        Object.assign(state, {job_id: '', cursor: 0, task_version: 0, applied_version: 0, status: '',
          display_status: '', terminal_snapshot: null, question: null, approvals: [], operations: [], pending_updates: [], not_applied_updates: []});
      }};
  }
  root.CitrusTaskState = {create: create, createProcess: createProcess, createContext: createContext, displayStatus: displayStatus,
    snapshotStatus: snapshotStatus, isTerminal: function (state) { return terminal.indexOf(state.display_status) >= 0; },
    resumable: function (state) { return list(state.operations).filter(function (op) {
      return op.resumable === true && op.status === 'interrupted' && ['local_read', 'pure_computation'].indexOf(op.recovery_mode) >= 0;
    }); }};
}(typeof window !== 'undefined' ? window : globalThis));
