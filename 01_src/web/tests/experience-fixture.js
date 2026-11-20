// Injected only by the isolated Chromium test. No production scripts load this.
(() => {
  const sid = 'browser-fixture-session', rid = 'browser-fixture-request', job = 'browser-fixture-job';
  const state = () => JSON.parse(sessionStorage.getItem('experience-test-state') || '{"phase":"queued"}');
  const save = patch => sessionStorage.setItem('experience-test-state', JSON.stringify({...state(), ...patch}));
  const has = (...phases) => phases.includes(state().phase);
  const start = () => state().start || 0;
  const samples = [['Time', 'Value', 'Notes'], [1, 42, 'north, south'], [2, 84, 'sample']];
  const csv = 'Time,Value,Notes\r\n1,42,"north, south"\r\n2,84,"two\r\nlines"\r\n';
  const file = (id, filename) => ({file_id: id, filename, kind: 'table', origin: 'uploaded', size_bytes: 128,
    download_url: '/api/v2/files/' + id + '/download', last_accessed_at: 1700000000});
  const dataFiles = [file('fixture-file-0', 'Measurements.csv'), file('workbook', 'Measurements.xlsx')];
  const svgId = 'imported-project';
  window.experienceRequests = () => ({posts: state().posts || [], imports: state().imports || []});
  window.experienceStep = phase => save({phase, start: phase === 'start' ? Date.now() / 1000 - 10 : start()});
  window.experienceScale = (turns, files) => save({turns, files});
  localStorage.setItem('citrus_session_id_browser-fixture-user', sid);
  const frame = (seq, event, data) => ({seq, event, data: {...data, job_id: job, request_id: rid, session_id: sid}});
  const estimate = (attempt, count, options = {}) => {
    const components = {messages: count - 1000, system: 480, tool_arguments: 70, tool_schemas: 350, images: 0, protocol: 100};
    return {snapshot_version: 1, estimate_scope: 'full_request_estimate', model: 'deepseek fixture', logical_call_id: 'call-' + attempt,
      attempt_id: 'attempt-' + attempt, estimated_input_tokens: count, components, input_limit: 86000, model_window: null,
      tool_message_count: attempt - 1, history_load_status: 'loaded', status: 'admitted', ...options};
  };
  const envelope = () => has('done') ? {display_status: 'completed', history_saved: true, artifact_ids: []} : null;
  function frames() {
    if (has('queued')) return [];
    const result = [frame(1, 'job', {status: 'running'}), frame(2, 'context_snapshot', estimate(1, 9200, {change_reason: 'initial_attempt'}))];
    if (has('start')) return result;
    result.push(frame(3, 'tool_call_start', {status: 'running', display_name: '读取表格', tool_call_id: 'read-sheet'}));
    if (has('tool')) return result;
    result.push(frame(4, 'tool_result', {status: 'complete', display_name: '读取表格', tool_call_id: 'read-sheet', elapsed_ms: 1720}),
      frame(5, 'context_usage', {scope: 'single_call', logical_call_id: 'call-1', attempt_id: 'attempt-1', call: {input_tokens: 9137, output_tokens: 160, cache_read: 0, cache_uncached: 0}}));
    if (has('zero')) return result;
    result.push(frame(6, 'context_snapshot', estimate(2, 10400, {model_window: 131072, change_reason: 'tool_results_added'})),
      frame(7, 'context_usage', {scope: 'single_call', logical_call_id: 'call-2', attempt_id: 'attempt-2', call: {input_tokens: 10470, output_tokens: 900, cache_read: 2600, cache_uncached: 7800}}));
    if (has('usage')) return result;
    for (let i = 2; i <= 30; i++) {
      const tool = {display_name: '核对列 ' + i, tool_call_id: 'column-' + i};
      result.push(frame(result.length + 1, 'tool_call_start', {...tool, status: 'running'}));
      result.push(frame(result.length + 1, 'tool_result', {...tool, status: 'complete'}));
    }
    result.push(frame(result.length + 1, 'answer_start', {}));
    result.push(frame(result.length + 1, 'text', {content: '  \n'}));
    if (has('whitespace')) return result;
    result.push(frame(result.length + 1, 'text', {content: '## 表格核对结果\n\n已读取数据，保留原始列名。'}));
    if (has('done')) result.push(frame(result.length + 1, 'done', {turn_envelope: envelope(), answer: '## 表格核对结果\n\n已读取数据，保留原始列名。'}));
    return result;
  }
  function queue() {
    return {job_id: job, root_job_id: job, session_id: sid, client_request_id: rid, active_child_job_id: '', queue_seq: 9,
      query: '请读取表格并核对列名。', state: has('queued') ? 'queued' : has('done') ? 'terminal' : 'running',
      queue_position: 3, accepted_at: (start() || Date.now() / 1000) - 30, dispatch_at: start() || null, started_at: start() || null,
      finished_at: has('done') ? start() + 31 : null};
  }
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, settings) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href), p = url.pathname;
    if (url.origin !== location.origin || !p.startsWith('/api/') && !p.startsWith('/jobs/')) return nativeFetch(input, settings);
    if (settings?.signal?.aborted || !navigator.onLine) throw new TypeError('Fixture transport offline');
    let body = {};
    if (p === '/api/v2/auth/me') body = {user: {user_id: 'browser-fixture-user', username: '本地验收', role: 'user'}};
    else if (p === '/api/v2/sessions') body = {sessions: [{session_id: sid, title: '表格核对', updated_at: new Date().toISOString()}]};
    else if (p === '/api/v2/files') body = {retention_idle_days: 30, generated: [], uploaded: Array.from({length: state().files || 0}, (_, i) =>
      i === 0 ? dataFiles[0] : file('fixture-file-' + i, '文件 ' + (i + 1) + '.csv'))};
    else if (p === dataFiles[0].download_url) return new Response(csv, {headers: {'Content-Type': 'text/csv'}});
    else if (p === dataFiles[1].download_url) {
      const workbook = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet(samples), 'Measurements');
      return new Response(XLSX.write(workbook, {type: 'array', bookType: 'xlsx'}));
    } else if (p === '/api/v2/figures/data-files') body = {files: dataFiles};
    else if (p.startsWith('/api/v2/figures/data-files/') && p.endsWith('/schema')) {
      const selected = dataFiles.find(entry => p.endsWith('/' + entry.file_id + '/schema'));
      body = {file_id: selected.file_id, filename: selected.filename, rows: 2, sheet: selected.filename.endsWith('.xlsx') ? 'Measurements' : '',
        columns: samples[0].map(name => ({name, numeric: name !== 'Notes'}))};
    } else if (p === '/api/v2/figures/projects' && settings?.method === 'POST') {
      save({imports: [...(state().imports || []), JSON.parse(settings.body)]});
      body = {figure_id: svgId};
    } else if (p === '/api/v2/figures/projects') {
      const projects = Array.from({length: 122}, (_, i) => ({figure_id: 'old-project-' + i, title: '历史图件 ' + i, editable: false}));
      const offset = +(url.searchParams.get('offset') || 0), limit = +(url.searchParams.get('limit') || 31);
      body = {projects: projects.slice(offset, offset + limit)};
    } else if (p === '/api/v2/figures/projects/' + svgId) {
      body = {figure_id: svgId, title: '隔离夹具新建图件', editable: false, exports: {}};
    } else if (p.endsWith('/messages')) body = {messages: [], turns: Array.from({length: state().turns || 0}, (_, i) => ({turn_request_id: 'saved-' + i, user: {content: '历史问题 ' + i}, answer: {content: '历史回答 ' + i}, display_status: 'completed'})), has_more: false};
    else if (p.endsWith('/turn-queue')) {
      let admissions = state().posts || [];
      if (settings?.method === 'POST') {
        admissions = [...admissions, JSON.parse(settings.body)];
        save({posts: admissions});
      }
      const entries = admissions.map((entry, i) => ({...entry, session_id: sid, job_id: 'new-job-' + i, queue_seq: i + 10,
        queue_position: i + 1, state: 'queued', accepted_at: Date.now() / 1000}));
      body = settings?.method === 'POST' ? entries.at(-1) : {turns: [queue(), ...entries]};
    }
    else if (p === '/jobs/' + job || p === '/jobs/' + job + '/events') {
      const events = frames(), after = +(url.searchParams.get('after') || 0);
      body = {job_id: job, protocol: 'citrus-task/1', status: has('done') ? 'completed' : 'running',
        terminal_snapshot: envelope(), context_snapshot: events.filter(x => x.event === 'context_snapshot').at(-1)?.data,
        event_cursor: events.length, next_cursor: events.length, has_more: false, events: events.filter(x => x.seq > after)};
      if (body.context_snapshot) body.context_snapshot = {...body.context_snapshot, seq: events.filter(x => x.event === 'context_snapshot').at(-1).seq};
    }
    return new Response(JSON.stringify(body), {headers: {'Content-Type': 'application/json'}});
  };
})();
