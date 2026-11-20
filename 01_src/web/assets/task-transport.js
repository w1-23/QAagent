(function (root) {
  'use strict';
  function parser(deliver) {
    var buffer = '', type = 'message', id = '', lines = [];
    function line(text) {
      if (!text) {
        if (lines.length) {
          var data;
          try { data = JSON.parse(lines.join('\n')); } catch (_) { data = null; }
          if (data) deliver(type, data, id);
        }
        type = 'message'; id = ''; lines = []; return;
      }
      if (text[0] === ':') return;
      var colon = text.indexOf(':'), key = colon < 0 ? text : text.slice(0, colon);
      var value = colon < 0 ? '' : text.slice(colon + 1).replace(/^ /, '');
      if (key === 'event') type = value;
      else if (key === 'id') id = value;
      else if (key === 'data') lines.push(value);
    }
    return {push: function (text, final) {
      buffer += text;
      var offset;
      while ((offset = buffer.indexOf('\n')) >= 0) {
        line(buffer.slice(0, offset).replace(/\r$/, '')); buffer = buffer.slice(offset + 1);
      }
      if (final) { if (buffer) line(buffer.replace(/\r$/, '')); buffer = ''; line(''); }
    }};
  }
  function create(options) {
    var store = options.store, stopped = false, replaying = null, timer = null, followVersion = 0;
    var reads = new AbortController();
    function owns() { return !stopped && options.owns(); }
    async function json(url, settings) {
      if (!owns()) return null;
      var scope = reads;
      var response = await options.fetch(url, settings || {signal: scope.signal, cache: 'no-store'});
      if (!owns() || scope !== reads) return null;
      var data = await response.json();
      if (!owns() || scope !== reads) return null;
      if (!response.ok) {
        var error = new Error(response.status === 409 ? '任务已更新，请核对最新状态后重试。' : '请求未完成，请稍后重试。');
        error.status = response.status; error.detail = data.detail; throw error;
      }
      return data;
    }
    function snapshot(data) {
      if (!owns() || !data) return;
      if (!store.hydrate(data)) return;
      if (options.onSnapshot) options.onSnapshot(data);
    }
    async function replay(jobId) {
      if (!jobId || !owns()) return;
      if (replaying) return replaying;
      var scope = reads;
      var flight = replaying = (async function () {
        var more = true;
        while (more && owns()) {
          if (scope !== reads) return;
          var before = store.state.cursor;
          var page = await json('/jobs/' + encodeURIComponent(jobId) + '/events?after=' + before + '&limit=200');
          if (!owns() || !page) return;
          if (page.protocol !== 'citrus-task/1' || page.job_id && page.job_id !== jobId) throw new Error('任务事件格式不匹配。');
          (page.events || []).forEach(function (frame) {
            if (owns()) store.receive(frame.event, Object.assign({}, frame.data, {seq: frame.seq}), String(frame.seq));
          });
          if (store.hasGap() || Number.isSafeInteger(page.next_cursor) && store.state.cursor < page.next_cursor) {
            throw new Error('任务状态暂未同步，正在补读事件。');
          }
          more = !!page.has_more;
          if (more && store.state.cursor <= before) throw new Error('任务事件游标未前进。');
          if (!more) snapshot(Object.assign({}, page, {event_cursor: page.next_cursor}));
        }
      }());
      try { return await flight; } finally { if (replaying === flight) replaying = null; }
    }
    async function refresh(jobId, withEvents) {
      var job = await json('/jobs/' + encodeURIComponent(jobId));
      if (!job || !owns()) return null;
      if (job.job_id !== jobId) throw new Error('任务状态标识不匹配。');
      var taskState = root.CitrusTaskState;
      // The status read may see recovery admitted after replay returned a terminal event.
      if ((withEvents !== false || taskState.snapshotStatus(job) || taskState.isTerminal(store.state)) &&
          job.event_cursor > store.state.cursor) {
        await replay(jobId);
        if (owns() && job.event_cursor > store.state.cursor) throw new Error('任务状态暂未同步，正在补读事件。');
      }
      if (owns()) snapshot(job);
      return owns() ? job : null;
    }
    function follow(jobId) {
      if (timer) clearTimeout(timer);
      var version = ++followVersion;
      async function poll() {
        if (!owns() || version !== followVersion) return;
        try {
          await replay(jobId);
          if (owns() && version === followVersion) await refresh(jobId, false);
          if (root.CitrusTaskState.isTerminal(store.state)) return;
        } catch (error) { if (owns() && version === followVersion && options.onError) options.onError(error); }
        if (owns() && version === followVersion) timer = setTimeout(poll, 2000);
      }
      return poll();
    }
    function followContinuation(jobId) {
      if (store.state.job_id !== jobId) {
        reads.abort();
        reads = new AbortController();
        replaying = null;
        store.resetJob(jobId);
      }
      return follow(jobId);
    }
    function followRequest(sessionId, requestId, onFound, onUnavailable) {
      if (timer) clearTimeout(timer);
      var version = ++followVersion, attempts = 0;
      var url = '/api/v2/session/' + encodeURIComponent(sessionId) + '/task-by-request/' + encodeURIComponent(requestId);
      async function lookup() {
        if (!owns() || version !== followVersion) return;
        attempts++;
        var claim;
        try { claim = await json(url); }
        catch (error) {
          if (!owns() || version !== followVersion) return;
          if (error.status === 401 || error.status === 403 || attempts >= 8) {
            onUnavailable(error);
            return;
          }
          timer = setTimeout(lookup, 2000);
          return;
        }
        if (!owns() || version !== followVersion) return;
        if (!claim || typeof claim.job_id !== 'string' || !claim.job_id) {
          onUnavailable(new Error('任务标识暂时无法确认。'));
          return;
        }
        onFound(claim.job_id);
        return follow(claim.job_id);
      }
      return lookup();
    }
    async function stream(response, signal) {
      var reader = response.body.getReader(), decoder = new TextDecoder();
      var frames = [];
      var input = parser(function (type, data, id) { frames.push([type, data, id]); });
      async function flush() {
        while (frames.length && owns() && !signal.aborted) {
          var frame = frames.shift();
          store.receive(frame[0], frame[1], frame[2]);
          if (store.hasGap() && store.state.job_id) await replay(store.state.job_id);
        }
      }
      try {
        while (owns() && !signal.aborted) {
          var chunk = await reader.read();
          if (!owns() || signal.aborted) return;
          input.push(chunk.done ? decoder.decode() : decoder.decode(chunk.value, {stream: true}), !!chunk.done);
          await flush();
          if (chunk.done) return;
        }
      } finally {
        if (!owns() || signal.aborted) await reader.cancel().catch(function () {});
      }
    }
    return {stream: stream, replay: replay, refresh: refresh, follow: follow, followContinuation: followContinuation, followRequest: followRequest,
      answer: function (jobId, questionId, payload) { return json('/api/tasks/' + encodeURIComponent(jobId) + '/questions/' + encodeURIComponent(questionId) + '/answer', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      }); },
      post: function (jobId, path, payload) { return json('/api/v2/chat/jobs/' + encodeURIComponent(jobId) + '/' + path, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      }); },
      stop: function () { stopped = true; reads.abort(); if (timer) clearTimeout(timer); timer = null; }};
  }
  root.CitrusTaskTransport = {parser: parser, create: create};
}(typeof window !== 'undefined' ? window : globalThis));
