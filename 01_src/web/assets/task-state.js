(function (root) {
  'use strict';
  var terminal = ['completed', 'partial', 'failed', 'cancelled'];
  function displayStatus(data) {
    var envelope = data && (data.turn_envelope || data.terminal_snapshot);
    var status = String(envelope && envelope.display_status || data && (data.display_status || data.turn_status) || '');
    return terminal.indexOf(status) >= 0 ? status : '';
  }
  function list(value) { return Array.isArray(value) ? value : []; }
  function create(options) {
    options = options || {};
    var state = {job_id: '', cursor: 0, task_version: 0, applied_version: 0, status: '',
      display_status: '', terminal_snapshot: null, pending_updates: [], not_applied_updates: [], operations: [], approvals: []};
    var pending = new Map(), subscribers = [];
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
      if (job.status) state.status = String(job.status);
      if (job.terminal_snapshot && displayStatus(job)) {
        state.terminal_snapshot = job.terminal_snapshot;
        state.display_status = displayStatus(job);
      }
      notify(); return true;
    }
    function reduce(type, data) {
      if (data.job_id) state.job_id = String(data.job_id);
      ['task_version', 'applied_version'].forEach(function (key) {
        if (Number.isSafeInteger(data[key])) state[key] = Math.max(state[key], data[key]);
      });
      if (type === 'job') state.status = String(data.status || 'running');
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
      if (type === 'done' || type === 'error') {
        state.display_status = displayStatus(data) || 'failed';
        state.terminal_snapshot = data.turn_envelope || null;
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
      if (!pending.has(seq)) pending.set(seq, {event: type, data: data});
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
        pending.clear(); subscribers.length = 0;
        Object.assign(state, {job_id: '', cursor: 0, task_version: 0, applied_version: 0, status: '',
          display_status: '', terminal_snapshot: null, approvals: [], operations: [], pending_updates: [], not_applied_updates: []});
      }};
  }
  root.CitrusTaskState = {create: create, displayStatus: displayStatus,
    isTerminal: function (state) { return terminal.indexOf(state.display_status) >= 0; },
    resumable: function (state) { return list(state.operations).filter(function (op) {
      return op.resumable === true && op.status === 'interrupted' && ['local_read', 'pure_computation'].indexOf(op.recovery_mode) >= 0;
    }); }};
}(typeof window !== 'undefined' ? window : globalThis));
