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
    var store = options.store, stopped = false, replaying = null, timer = null;
    function owns() { return !stopped && options.owns(); }
    async function json(url, settings) {
      if (!owns()) return null;
      var response = await options.fetch(url, settings);
      if (!owns()) return null;
      var data = await response.json();
      if (!owns()) return null;
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
      replaying = (async function () {
        var more = true;
        while (more && owns()) {
          var before = store.state.cursor;
          var page = await json('/jobs/' + encodeURIComponent(jobId) + '/events?after=' + before + '&limit=200');
          if (!owns() || !page) return;
          if (page.protocol !== 'citrus-task/1' || page.job_id && page.job_id !== jobId) throw new Error('任务事件格式不匹配。');
          (page.events || []).forEach(function (frame) {
            if (owns()) store.receive(frame.event, Object.assign({}, frame.data, {seq: frame.seq}), String(frame.seq));
          });
          if (store.hasGap()) throw new Error('任务事件尚不连续，请重新读取。');
          more = !!page.has_more;
          if (more && store.state.cursor <= before) throw new Error('任务事件游标未前进。');
          if (!more) snapshot(page);
        }
      }());
      try { return await replaying; } finally { replaying = null; }
    }
    async function refresh(jobId, withEvents) {
      var job = await json('/jobs/' + encodeURIComponent(jobId));
      if (!job || !owns()) return null;
      // Apply events before the terminal snapshot so the final answer and cards
      // reach the same reducer used by the live stream.
      if (withEvents !== false && Number(job.event_cursor) > store.state.cursor) await replay(jobId);
      if (owns()) snapshot(job);
      return owns() ? job : null;
    }
    function follow(jobId) {
      if (timer) clearTimeout(timer);
      async function poll() {
        if (!owns()) return;
        try {
          await replay(jobId);
          if (owns()) await refresh(jobId, false);
          if (!owns() || root.CitrusTaskState.isTerminal(store.state)) return;
        } catch (error) { if (owns() && options.onError) options.onError(error); }
        if (owns()) timer = setTimeout(poll, 2000);
      }
      return poll();
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
    return {stream: stream, replay: replay, refresh: refresh, follow: follow,
      post: function (jobId, path, payload) { return json('/api/v2/chat/jobs/' + encodeURIComponent(jobId) + '/' + path, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      }); },
      stop: function () { stopped = true; if (timer) clearTimeout(timer); timer = null; }};
  }
  root.CitrusTaskTransport = {parser: parser, create: create};
}(typeof window !== 'undefined' ? window : globalThis));
