'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
function state() {
  const root = {};
  vm.runInNewContext(readFileSync(join(__dirname, '../assets/task-state.js'), 'utf8'), root);
  return root.CitrusTaskState;
}
const plain = value => JSON.parse(JSON.stringify(value));
const identity = {session_id: 's', request_id: 'r', logical_call_id: 'L', attempt_id: 'A'};
const snapshot = (seq, extra = {}) => ({snapshot_version: 1, scope: 'context_snapshot', ...identity, seq,
  estimate_scope: 'full_request_estimate', estimated_input_tokens: 100, status: 'running', ...extra});
const usage = (seq, input, extra = {}) => ({...identity, seq, scope: 'single_call', call: {input_tokens: input}, ...extra});
const provider = input => ({logical_call_id: 'L', attempt_id: 'A', scope: 'single_call', input_tokens: input});

test('hydration followed by replay preserves settled usage and permits a later correction', () => {
  const store = state().create({session_id: 's', request_id: 'r'});
  store.hydrate({job_id: 'j', context_snapshot: snapshot(5, {status: 'complete', provider_usage: provider(90)})});
  store.receive('job', {job_id: 'j', seq: 1});
  store.receive('context_snapshot', snapshot(2));
  store.receive('context_usage', usage(3, 999));
  assert.equal(store.state.context_snapshot.provider_usage.input_tokens, 90);
  assert.equal(store.state.context_snapshot.status, 'complete');
  store.receive('context_snapshot', snapshot(4, {status: 'complete', provider_usage: provider(999)}));
  store.receive('context_snapshot', snapshot(5, {status: 'complete', provider_usage: provider(90)}));
  store.receive('context_usage', usage(6, 91));
  assert.equal(store.state.context_snapshot.provider_usage.input_tokens, 91);
});

test('usage ordering is preserved when snapshots and responses arrive before selection', () => {
  const context = state().createContext();
  assert.equal(context.receive('context_usage', usage(4, 90)), null);
  context.receive('context_usage', usage(3, 999));
  const current = context.receive('context_snapshot', snapshot(2));
  assert.equal(current.provider_usage.input_tokens, 90);
  context.receive('context_usage', usage(3, 999));
  assert.equal(context.current().provider_usage.input_tokens, 90);
});

test('older embedded usage cannot replace a newer response received before its snapshot', () => {
  const context = state().createContext();
  context.receive('context_usage', usage(4, 90));
  const current = context.receive('context_snapshot', snapshot(2, {provider_usage: provider(999), usage_status: 'reported'}));
  assert.equal(current.provider_usage.input_tokens, 90);
  context.receive('context_usage', usage(4, 90));
  assert.equal(context.current().provider_usage.input_tokens, 90);
  context.receive('context_snapshot', snapshot(5, {status: 'complete', provider_usage: provider(91), usage_status: 'reported'}));
  assert.equal(context.current().provider_usage.input_tokens, 91);
  context.receive('context_usage', usage(6, 92));
  assert.equal(context.current().provider_usage.input_tokens, 92);
});

test('request and session fences protect live, nested terminal and hydrated context, including after clear', () => {
  const store = state().create({session_id: 's', request_id: 'r'});
  for (const foreign of [{session_id: 'other'}, {request_id: 'other'}]) {
    store.receive('context_snapshot', snapshot(undefined, foreign));
    assert.equal(store.state.context_snapshot, null);
    store.hydrate({context_snapshot: snapshot(5, foreign)});
    assert.equal(store.state.context_snapshot, null);
    store.receive('done', {context_snapshot: snapshot(6, foreign)});
    assert.equal(store.state.context_snapshot, null);
    store.clear();
  }
  store.receive('context_snapshot', snapshot(1));
  assert.equal(store.state.context_snapshot.logical_call_id, 'L');
});

test('latest snapshot follows durable sequence rather than epoch, purpose or usage arrival', () => {
  const context = state().createContext();
  context.receive('context_snapshot', snapshot(4, {epoch: 5, source: 'supervisor'}));
  context.receive('context_snapshot', snapshot(6, {logical_call_id: 'summary', attempt_id: 'B', epoch: 0, source: 'working_context_summary'}));
  context.receive('context_usage', usage(7, 999));
  context.receive('context_snapshot', snapshot(3, {epoch: 8}));
  assert.equal(context.current().logical_call_id, 'summary');
  assert.deepEqual(plain(context.current().provider_usage), {});
});

test('not admitted attempts cannot inherit usage or budgets from an earlier attempt', () => {
  const context = state().createContext();
  context.receive('context_snapshot', snapshot(1, {model_window: 1000, input_limit: 500, provider_usage: provider(90)}));
  const blocked = context.receive('context_snapshot', snapshot(2, {attempt_id: '', status: 'not_admitted', estimated_input_tokens: 800}));
  assert.deepEqual(plain(blocked.provider_usage), {});
  assert.equal(blocked.model_window, null);
  assert.equal(blocked.input_limit, undefined);
  context.receive('context_usage', usage(3, 999, {attempt_id: ''}));
  context.receive('context_usage', usage(4, 999));
  assert.equal(context.current().status, 'not_admitted');
  assert.deepEqual(plain(context.current().provider_usage), {});
});

test('embedded usage must match both call and attempt, and a new call clears old optional fields', () => {
  const context = state().createContext();
  context.receive('context_snapshot', snapshot(1, {model_window: 1000, components: {system: 100}, input_limit: 500,
    provider_usage: provider(90)}));
  const next = context.receive('context_snapshot', snapshot(2, {logical_call_id: 'next', provider_usage: provider(999)}));
  assert.equal(next.components, undefined);
  assert.equal(next.input_limit, undefined);
  assert.equal(next.model_window, null);
  assert.deepEqual(plain(next.provider_usage), {});
  context.receive('context_usage', usage(3, 80, {logical_call_id: 'next', attempt_id: 'different'}));
  assert.deepEqual(plain(context.current().provider_usage), {});
});
