/* P2: Codex-style turn rail (right-edge navigation for long sessions).
 *
 * Design rules:
 *  - one tick per real user turn; tool heartbeats never appear here;
 *  - ticks are buttons, so keyboard users reach them with Tab/Enter;
 *  - hovering/focusing reveals the question text, never a fake title;
 *  - clicking locates the turn, walking back through history pages when the
 *    turn is not loaded yet, and says so plainly when it cannot;
 *  - the current position is derived from the viewport, not from a guess.
 */
(function () {
  'use strict';

  var RAIL_MAX = 80;          // keep the newest N turns for very long sessions
  var PAGE_GUARD = 8;         // bounded history walk when locating an old turn
  var railEl = null, trackEl = null, tipEl = null;
  var ticks = [];
  var activeMid = '';
  var buildTimer = 0, rafPending = false;

  function chatBox() {
    if (typeof chatContainer !== 'undefined' && chatContainer) return chatContainer;
    return document.getElementById('chatMessages') || document.getElementById('chatArea');
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

  function userTurns() {
    var msgs = (typeof _historyMsgs !== 'undefined' && Array.isArray(_historyMsgs)) ? _historyMsgs : [];
    var out = [];
    for (var i = 0; i < msgs.length; i++) {
      var m = msgs[i];
      if (!m || m.role !== 'user') continue;
      var mid = String(m.message_id || '');
      if (!mid) continue;
      out.push({ mid: mid, reqId: String(m.request_id || ''), text: String(m.content || '') });
    }
    return out;
  }

  function shortText(t) {
    var s = String(t || '').replace(/\s+/g, ' ').trim();
    if (!s) return '（空提问）';
    return s.length > 46 ? s.slice(0, 46) + '…' : s;
  }

  function build() {
    ensureRail();
    var turns = userTurns();
    if (turns.length > RAIL_MAX) turns = turns.slice(turns.length - RAIL_MAX);
    trackEl.innerHTML = '';
    ticks = [];
    if (!turns.length) {
      railEl.classList.add('is-empty');
      hideTip();
      return;
    }
    railEl.classList.remove('is-empty');
    for (var i = 0; i < turns.length; i++) {
      var t = turns[i];
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'turn-tick';
      b.dataset.mid = t.mid;
      b.dataset.tip = '第 ' + (i + 1) + ' 次提问 · ' + shortText(t.text);
      b.setAttribute('aria-label', '定位到第 ' + (i + 1) + ' 次提问：' + shortText(t.text));
      b.addEventListener('click', function () { focusTurn(this.dataset.mid); });
      b.addEventListener('mouseenter', function () { showTip(this); });
      b.addEventListener('focus', function () { showTip(this); });
      b.addEventListener('mouseleave', hideTip);
      b.addEventListener('blur', hideTip);
      trackEl.appendChild(b);
      ticks.push({ mid: t.mid, el: b });
    }
    syncActive();
  }

  function scheduleBuild() {
    if (buildTimer) clearTimeout(buildTimer);
    buildTimer = setTimeout(function () { buildTimer = 0; build(); }, 120);
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
      ticks[i].el.classList.toggle('is-active', ticks[i].mid === activeMid);
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
    if (!mid) return;
    if (typeof jumpToMessage === 'function' && jumpToMessage(mid)) return;
    var found = findInHistory(mid);
    for (var g = 0; g < PAGE_GUARD && !found; g++) {
      if (typeof _historyHasMore === 'undefined' || !_historyHasMore) break;
      if (typeof loadEarlierHistory !== 'function') break;
      await loadEarlierHistory();
      found = findInHistory(mid);
    }
    if (!found) {
      notify('这次提问还没有加载出来：请先在对话顶部点「载入更早的对话」，再点右侧定位。');
      return;
    }
    if (typeof jumpToMessage === 'function' && jumpToMessage(mid)) return;
    notify('这一轮已经载入，但页面没能滚动到它，请手动向上查找。');
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
    build();
    // app.js restores history asynchronously; rebuild once more after it settles.
    setTimeout(build, 600);
    setTimeout(build, 1800);
  }

  window.turnRail = { refresh: scheduleBuild, focus: focusTurn };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
