const test = require('node:test');
const assert = require('node:assert/strict');
require('../assets/task-state.js');
require('../assets/task-transport.js');

const envelope = {display_status: 'completed'};
const job = {job_id: 'job-1', status: 'completed', event_cursor: 3, terminal_snapshot: envelope};
const terminal = {seq: 3, event: 'done', data: {job_id: job.job_id, answer: 'Final answer', artifact_ids: ['file-1'], turn_envelope: envelope}};
const running = {seq: 1, event: 'job', data: {job_id: job.job_id, status: 'running'}};
const output = {seq: 2, event: 'answer_start', data: {}};

function reply(data, status = 200) {
  return {json: async () => data, ok: status === 200, status};
}

function page(events, next, snapshot = {}) {
  return reply({job_id: job.job_id, protocol: 'citrus-task/1', events, next_cursor: next, has_more: false, ...snapshot});
}

function observation(responses, owns = () => true) {
  const requests = [], frames = [], errors = [];
  const store = CitrusTaskState.create({onEvent: (type, data) => frames.push({type, data})});
  const transport = CitrusTaskTransport.create({store, owns, onError: error => errors.push(error),
    fetch: async (url, options) => {
      requests.push([url, options.method || 'GET']);
      assert.ok(responses.length, `unexpected ${url}`);
      return responses.shift();
    }});
  return {requests, frames, errors, store, transport};
}

test('terminal between replay and status delivers answer and files once', async t => {
  const {transport, store, frames, requests} = observation([
    page([running], 1, {status: 'running'}), reply(job),
    page([running, output, terminal], 3, {status: job.status, terminal_snapshot: envelope})
  ]);
  t.after(() => transport.stop());
  await transport.follow(job.job_id);
  assert.equal(store.state.cursor, 3);
  assert.equal(CitrusTaskState.isTerminal(store.state), true);
  assert.deepEqual(frames.filter(frame => frame.type === 'done'), [{type: 'done', data: {...terminal.data, seq: 3}}]);
  assert.deepEqual(requests, [
    ['/jobs/job-1/events?after=0&limit=200', 'GET'], ['/jobs/job-1', 'GET'],
    ['/jobs/job-1/events?after=1&limit=200', 'GET']
  ]);
});

test('a missing terminal page is retryable and never hydrates ahead of consumption', async t => {
  const {transport, store, frames, errors} = observation([
    page([running], 1, {status: 'running'}), reply(job), page([], 1, {status: 'running'}),
    page([terminal], 3, {status: job.status, terminal_snapshot: envelope}),
    page([output, terminal], 3, {status: job.status, terminal_snapshot: envelope}), reply(job)
  ]);
  t.after(() => transport.stop());
  await transport.follow(job.job_id);
  assert.equal(store.state.cursor, 1);
  assert.equal(CitrusTaskState.isTerminal(store.state), false);
  assert.equal(errors.length, 1);
  await transport.follow(job.job_id);
  assert.equal(store.state.cursor, 1);
  assert.equal(CitrusTaskState.isTerminal(store.state), false);
  assert.equal(errors.length, 2);
  await transport.follow(job.job_id);
  assert.equal(store.state.cursor, 3);
  assert.equal(frames.filter(frame => frame.type === 'done').length, 1);
});

test('a lost first frame discovers only the original request and never submits', async t => {
  const {transport, store, requests} = observation([
    reply({job_id: job.job_id}), page([running, output, terminal], 3, {terminal_snapshot: envelope}), reply(job)
  ]);
  t.after(() => transport.stop());
  const found = [];
  await transport.followRequest('session a', 'request-1', id => found.push(id), assert.fail);
  assert.deepEqual(found, [job.job_id]);
  assert.equal(store.state.cursor, 3);
  assert.equal(CitrusTaskState.isTerminal(store.state), true);
  assert.deepEqual(requests[0], ['/api/v2/session/session%20a/task-by-request/request-1', 'GET']);
  assert.ok(requests.every(([, method]) => method === 'GET'));
});

test('stopping an observer aborts its read and ignores a late claim after navigation', async () => {
  let signal, complete;
  const store = CitrusTaskState.create();
  const transport = CitrusTaskTransport.create({store, owns: () => true, fetch: (_, opts) => {
    signal = opts.signal;
    return new Promise(resolve => { complete = resolve; });
  }});
  const lookup = transport.followRequest('session-a', 'request-1', assert.fail, assert.fail);
  transport.stop();
  assert.equal(signal.aborted, true);
  complete(reply({job_id: job.job_id}));
  await lookup;
  assert.equal(store.state.job_id, '');
});

test('running recovery between replay and status supersedes an older terminal event', async t => {
  const recovered = {seq: 4, event: 'agent_recovery', data: {job_id: job.job_id, status: 'admitted'}};
  const {transport, store, requests} = observation([
    page([running, output, terminal], 3, {status: 'completed', terminal_snapshot: envelope}),
    reply({job_id: job.job_id, status: 'running', event_cursor: 4, terminal_snapshot: {}}),
    page([recovered], 4, {status: 'running', terminal_snapshot: {}})
  ]);
  t.after(() => transport.stop());
  await transport.follow(job.job_id);
  assert.equal(store.state.cursor, 4);
  assert.equal(store.state.status, 'running');
  assert.equal(CitrusTaskState.isTerminal(store.state), false);
  assert.deepEqual(requests.slice(-1), [['/jobs/job-1/events?after=3&limit=200', 'GET']]);
});

test('an admission failure without an envelope stops polling after replay', async t => {
  const failed = {job_id: job.job_id, event_cursor: 0, status: 'failed', terminal_snapshot: {}};
  const {transport, store, frames, requests} = observation([page([], 0, {status: 'failed'}), reply(failed)]);
  t.after(() => transport.stop());
  await transport.follow(job.job_id);
  assert.equal(store.state.display_status, 'failed');
  assert.equal(CitrusTaskState.isTerminal(store.state), true);
  assert.equal(store.state.terminal_snapshot, null);
  assert.deepEqual(frames, []);
  assert.equal(requests.length, 2);
});


test('continuation starts at cursor zero and ignores the retired job response', async t => {
  const requests = [], received = [];
  let lateResponse, oldSignal;
  const store = CitrusTaskState.create({onEvent: (_, data) => received.push(data)});
  store.receive('job', {...running.data, seq: 1});
  const child = {job_id: 'child', status: 'completed', event_cursor: 1, terminal_snapshot: envelope};
  const transport = CitrusTaskTransport.create({store, owns: () => true, fetch: (url, options) => {
    requests.push(url);
    if (url.startsWith('/jobs/job-1/')) {
      oldSignal = options.signal;
      return new Promise(resolve => { lateResponse = resolve; });
    }
    if (url.includes('/events')) return reply({job_id: child.job_id, protocol: 'citrus-task/1',
      events: [{seq: 1, event: 'done', data: {...terminal.data, job_id: child.job_id, answer: 'Child answer'}}],
      next_cursor: 1, has_more: false});
    return reply(child);
  }});
  t.after(() => transport.stop());
  const old = transport.follow(job.job_id);
  await transport.followContinuation(child.job_id);
  assert.equal(oldSignal.aborted, true);
  lateResponse(page([output, terminal], 3));
  await old;
  assert.deepEqual(requests, ['/jobs/job-1/events?after=1&limit=200', '/jobs/child/events?after=0&limit=200', '/jobs/child']);
  assert.equal(store.state.job_id, child.job_id);
  assert.equal(store.state.cursor, 1);
  assert.deepEqual(received.filter(data => data.answer).map(data => data.answer), ['Child answer']);
});
