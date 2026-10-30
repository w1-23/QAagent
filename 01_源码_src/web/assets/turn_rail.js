/* P2/D-A3: Codex-style turn rail (right-edge navigation for long sessions).
 *
 * Design rules:
 *  - one tick per real user turn, sourced from the server turn index
 *    (`/api/v2/session/{id}/turns`) — never from the loaded DOM, so turns that
 *    have not been paged into the chat yet still get a tick;
 *  - every turn is listed; a long session scrolls the rail instead of dropping
 *    the oldest ticks (D-A3: 全量 + 可滚动);
 *  - tool heartbeats never appear here;
 *  - ticks are buttons, so keyboard users reach them with Tab/Enter;
 *  - hovering/focusing reveals the question text, never a fake title;
 *  - clicking locates the turn, walking back through history pages when the
 *    turn is not loaded yet, and says so plainly when it cannot;
 *  - the current position is derived from the viewport, not from a guess.
 */
(function () {
  'use strict';

  var PAGE = 400;            // turns per server request
  var MAX_PAGES = 5;         // bounded fetch: up to 2000 turns listed at once
  var WALK_LIMIT = 80;       // bounded history walk when locating an old turn
  var DENSE_AT = 40;         // above this many ticks, tighten spacing

  var railEl = null, trackEl = null, tipEl = null;
  var ticks = [];
  var activeMid = '';
  var buildTimer = 0, rafPending = false, busy = false;
  var turnsCache = [];       // oldest → newest, from the server index
  var hasMoreTurns = false, nextBefore = 0, loadedSession = '';

  function chatBox() {
    if (typeof chatContainer !== 'undefined' && chatContainer) return chatContainer;
    return document.getElementById('chatMessages') || document.getElementById('chatArea');
  }

  function currentSession() {
    return (typeof sessionId !== 'undefined' && sessionId) ? String(sessionId) : '';
  }

  function ensureRail() {
    if (railEl && document.body.contains(railEl)) return railEl;
    railEl = document.createElement('nav');
    railEl.id = 'turnRail';
    railEl.className = 'turn-rail';
    railEl.setAttribute('aria-label', '本会话提问定位');
    trackEl = document.createElement('div');
    trackEl.className = 'turn-rail-track';
    railEl.appendChild(trackEl);
    tipEl = document.createElement('div');
    tipEl.id = 'turnRailTip';
    tipEl.className = 'turn-rail-tip';
    tipEl.setAttribute('role', 'tooltip');
    document.body.appendChild(railEl);
    document.body.appendChild(tipEl);
    return railEl;
  }

  function shortText(t) {
    // messages sometimes carry literal backslash escapes instead of newlines
    var s = String(t || '').replace(/\\[nrt]/g, ' ').replace(/\s+/g, ' ').trim();
    if (!s) return '（空提问）';
    return s.length > 46 ? s.slice(0, 46) + '…' : s;
  }

  function fetchTurns(before) {
    var sid = currentSession();
    if (!sid || typeof fetch !== 'function') return Promise.resolve(null);
    var url = '/api/v2/session/' + encodeURIComponent(sid) + '/turns?limit=' + PAGE;
    if (before) url += '&before=' + encodeURIComponent(before);
    return fetch(url).then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  /* Load the whole turn index once per session (paged, bounded). */
  function loadTurns() {
    var sid = currentSession();
    if (!sid) { turnsCache = []; hasMoreTurns = false; loadedSession = ''; build(); return; }
    if (sid === loadedSession) { build(); return; }
    loadedSession = sid;
    turnsCache = [];
    hasMoreTurns = false;
    nextBefore = 0;
    var pages = 0;
    function step() {
      return fetchTurns(nextBefore).then(function (d) {
        if (!d) { build(); return; }
        var got = d.turns || [];
        turnsCache = got.concat(turnsCache);
        hasMoreTurns = !!d.has_more;
        nextBefore = Number(d.next_before || 0);
        pages += 1;
        if (hasMoreTurns && nextBefore && pages < MAX_PAGES) return step();
        build();
      });
    }
    step();
  }

  function build() {
    ensureRail();
    trackEl.innerHTML = '';
    ticks = [];
    if (!turnsCache.length) {
      railEl.classList.add('is-empty');
      hideTip();
      return;
    }
    railEl.classList.remove('is-empty');
    trackEl.classList.toggle('is-dense', turnsCache.length > DENSE_AT);

    if (hasMoreTurns) {
      var more = document.createElement('button');
      more.type = 'button';
      more.className = 'turn-rail-more';
      more.textContent = '⋮';
      more.title = '还有更早的提问未列出，点击继续加载';
      more.setAttribute('aria-label', '继续加载更早的提问');
      more.addEventListener('click', function () {
        fetchTurns(nextBefore).then(function (d) {
          if (!d) return;
          turnsCache = (d.turns || []).concat(turnsCache);
          hasMoreTurns = !!d.has_more;
          nextBefore = Number(d.next_before || 0);
          build();
        });
      });
      trackEl.appendChild(more);
    }

    for (var i = 0; i < turnsCache.length; i++) {
      var t = turnsCache[i];
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'turn-tick';
      b.dataset.mid = String(t.message_id || '');
      b.dataset.tip = '第 ' + (i + 1) + ' 次提问 · ' + shortText(t.text);
      b.setAttribute('aria-label', '定位到第 ' + (i + 1) + ' 次提问：' + shortText(t.text));
      b.addEventListener('click', function () { focusTurn(this.dataset.mid); });
      b.addEventListener('mouseenter', function () { showTip(this); });
      b.addEventListener('focus', function () { showTip(this); });
      b.addEventListener('mouseleave', hideTip);
      b.addEventListener('blur', hideTip);
      trackEl.appendChild(b);
      ticks.push({ mid: b.dataset.mid, el: b });
    }
    syncActive();
  }

  function scheduleBuild() {
    if (buildTimer) clearTimeout(buildTimer);
    buildTimer = setTimeout(function () {
      buildTimer = 0;
      var sid = currentSession();
      if (sid !== loadedSession) loadTurns();
      else build();
    }, 120);
  }

  function showTip(el) {
    if (!tipEl) return;
    tipEl.textContent = el.dataset.tip || '';
    var r = el.getBoundingClientRect();
    tipEl.style.top = Math.round(r.top + r.height / 2) + 'px';
    tipEl.classList.add('is-visible');
  }

  function hideTip() {
    if (tipEl) tipEl.classList.remove('is-visible');
  }

  function markActive() {
    for (var i = 0; i < ticks.length; i++) {
      var on = ticks[i].mid === activeMid;
      ticks[i].el.classList.toggle('is-active', on);
      if (on && trackEl.scrollHeight > trackEl.clientHeight) {
        // keep the current turn visible inside a scrolling rail
        var el = ticks[i].el;
        var top = el.offsetTop, h = el.offsetHeight;
        var view = trackEl.scrollTop, box = trackEl.clientHeight;
        if (top < view + 4) trackEl.scrollTop = Math.max(0, top - 8);
        else if (top + h > view + box - 4) trackEl.scrollTop = top + h - box + 8;
      }
    }
  }

  function syncActive() {
    var box = chatBox();
    if (!box || !ticks.length) return;
    var rect = box.getBoundingClientRect();
    var center = rect.top + rect.height / 2;
    var best = '', bestD = Infinity;
    for (var i = 0; i < ticks.length; i++) {
      var node = box.querySelector('[data-message-id="' + ticks[i].mid + '"]');
      if (!node) continue;
      var nr = node.getBoundingClientRect();
      var d = Math.abs((nr.top + nr.bottom) / 2 - center);
      if (d < bestD) { bestD = d; best = ticks[i].mid; }
    }
    if (best && best !== activeMid) { activeMid = best; markActive(); }
  }

  function onScroll() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(function () { rafPending = false; syncActive(); });
  }

  function notify(msg) {
    if (typeof showBanner === 'function') {
      try { showBanner(msg); return; } catch (e) { /* fall through */ }
    }
    console.warn('[turn-rail] ' + msg);
  }

  function findInHistory(mid) {
    var msgs = (typeof _historyMsgs !== 'undefined' && Array.isArray(_historyMsgs)) ? _historyMsgs : [];
    for (var i = 0; i < msgs.length; i++) {
      if (String(msgs[i] && msgs[i].message_id || '') === String(mid)) return msgs[i];
    }
    return null;
  }

  async function focusTurn(mid) {
    if (!mid || busy) return;
    if (typeof jumpToMessage === 'function' && jumpToMessage(mid)) return;
    busy = true;
    try {
      var found = findInHistory(mid);
      var steps = 0;
      while (!found && steps < WALK_LIMIT) {
        if (typeof _historyHasMore === 'undefined' || !_historyHasMore) break;
        if (typeof loadEarlierHistory !== 'function') break;
        await loadEarlierHistory();
        found = findInHistory(mid);
        steps += 1;
      }
      if (!found) {
        notify('这次提问不在已加载的历史里：请先在对话顶部点「载入更早的对话」，再点右侧定位。');
        return;
      }
      if (typeof jumpToMessage === 'function' && jumpToMessage(mid)) return;
      notify('这一轮已经载入，但页面没能滚动到它，请手动向上查找。');
    } finally {
      busy = false;
    }
  }

  function boot() {
    ensureRail();
    var box = chatBox();
    if (box) {
      box.addEventListener('scroll', onScroll, { passive: true });
      if (typeof MutationObserver === 'function') {
        new MutationObserver(scheduleBuild).observe(box, { childList: true });
      }
    }
    window.addEventListener('resize', onScroll, { passive: true });
    loadTurns();
    // app.js restores history asynchronously; re-check once it settles.
    setTimeout(scheduleBuild, 600);
    setTimeout(scheduleBuild, 1800);
  }

  window.turnRail = { refresh: scheduleBuild, focus: focusTurn, reload: loadTurns };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
