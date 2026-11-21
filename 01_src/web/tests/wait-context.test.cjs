const assert = require('node:assert/strict');
const test = require('node:test');
require('../assets/task-state.js');
const {create, createContext, waitingView} = global.CitrusTaskState;

const root = {job_id: 'root', state: 'running', started_at: 1770000000};
const running = {job_id: 'root', status: 'running'};

test('waiting uses the durable execution timestamp and stops for other phases', () => {
  for (const state of ['queued', 'claimed', 'reconcile_required']) {
    assert.deepEqual(waitingView(running, {...root, state}, {}), {kind: '', start: 0, finish: 0});
  }
  assert.deepEqual(waitingView(running, root, {}), {kind: 'waiting', start: root.started_at, finish: 0});
  assert.equal(waitingView(running, root, {activeTool: true}).kind, 'tool');
  assert.equal(waitingView(running, root, {hasAnswer: true}).kind, '');
  assert.equal(waitingView(running, root, {disconnected: true, activeTool: true}).kind, 'disconnected');
  assert.equal(waitingView(running, root, {stopping: true}).kind, 'stopping');
  assert.equal(waitingView(running, {...root, state: 'settling'}, {}).kind, 'settling');
  assert.equal(waitingView({...running, question: {status: 'pending'}}, root, {}).kind, 'awaiting_input');
  assert.equal(waitingView({...running, approvals: [{status: 'pending'}]}, root, {}).kind, 'awaiting_input');
  const finish = root.started_at + 21;
  assert.deepEqual(waitingView({...running, display_status: 'completed'}, {...root, state: 'terminal', finished_at: finish}, {}),
    {kind: 'completed', start: root.started_at, finish});
  assert.equal(waitingView(running, {...root, state: 'terminal', clear_reason: 'cancelled'}, {}).kind, 'cancelled');
});

test('continuation rejects a retired job clock and accepts its own epoch seconds', () => {
  const store = create();
  store.hydrate({...running, started_at: root.started_at});
  const entry = {...root, active_child_job_id: 'child'};
  assert.deepEqual(waitingView(store.state, entry, {}), {kind: '', start: 0, finish: 0});
  store.resetJob('child');
  store.hydrate({job_id: 'child', status: 'running', started_at: '2026-02-02T03:04:05'});
  assert.deepEqual(waitingView(store.state, entry, {}), {kind: 'waiting', start: 0, finish: 0});
  store.receive('job', {seq: 1, job_id: 'child', status: 'running', started_at: root.started_at + 30});
  assert.equal(waitingView(store.state, entry, {}).start, root.started_at + 30);
  store.resetJob('another');
  assert.equal(store.state.started_at, 0);
});

const ids = {session_id: 's', request_id: 'r'};
const attempt = (logical, physical, seq, options = {}) => ({...ids, snapshot_version: 1, seq,
  logical_call_id: logical, attempt_id: physical, status: 'running', ...options});
const usage = (logical, physical, seq, call) => ({...ids, seq, logical_call_id: logical,
  attempt_id: physical, scope: 'single_call', call});

test('interleaved admission and settlement select by admission sequence after replay', () => {
  const context = createContext(ids);
  context.receive('context_snapshot', attempt('first', 'alpha', 12, {status: 'complete', estimated_input_tokens: 120}));
  context.receive('context_snapshot', attempt('second', 'beta', 7, {estimated_input_tokens: 140}));
  context.receive('context_snapshot', attempt('first', 'alpha', 2, {estimated_input_tokens: 1}));
  context.receive('context_snapshot', attempt('first', '', 13, {status: 'not_admitted'}));
  context.receive('context_usage', usage('first', 'alpha', 11, {input_tokens: 122, cache_read: 0}));
  assert.equal(context.current().attempt_id, 'beta');
  assert.deepEqual(context.current().observed_attempts.map(row => [row.attempt_id, row.seq, row.estimated_tokens]),
    [['alpha', 2, 120], ['beta', 7, 140]]);
  assert.deepEqual(context.current().provider_usage, {});
});

test('replay keeps original usage, zero counts and ownership across hydration', () => {
  const context = createContext(ids);
  context.receive('context_usage', usage('l', 'a', 4, {input_tokens: 80, cache_read: 0, cache_uncached: 80}));
  context.receive('context_snapshot', attempt('l', 'a', 7, {status: 'complete'}));
  assert.equal(context.current().provider_usage.cache_read, 0);
  assert.equal(context.current().provider_usage.cache_write, undefined);
  const before = context.current();
  context.receive('context_usage', usage('l', 'a', 3, {cache_read: 90}));
  context.receive('context_usage', {...usage('l', 'a', 9, {cache_read: 95}), request_id: 'foreign'});
  context.receive('context_snapshot', {...attempt('l', 'other', 10), session_id: 'foreign'});
  assert.equal(context.current(), before);
  context.receive('context_snapshot', attempt('l', 'b', 10, {change_reason: 'retry'}));
  assert.equal(context.current().attempt_id, 'b');
  assert.deepEqual(context.current().provider_usage, {});
});
