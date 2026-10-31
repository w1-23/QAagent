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
var focusedIdx = -1;
var activeLockUntil = 0;   // 跳转后短暂锁定当前刻度，避免滚动动画把高亮甩到别的轮次
  var activeMid = '';
  var buildTimer = 0, rafPending = false, busy = false;
  var turnsCache = [];       // oldest → newest, from the server index
  var hasMoreTurns = false, nextBefore = 0, loadedSession = '';
  var pendingTurns = [];   // 本轮已提交、尚未拿到正式 message_id 的提问

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
    pendingTurns = [];
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
      b.addEventListener('mouseenter', (function (idx) {
        return function () { applyFisheye(idx); showTip(this); };
      })(i));
      b.addEventListener('focus', function () { showTip(this); });
      b.addEventListener('mouseleave', hideTip);
      b.addEventListener('blur', hideTip);
      trackEl.appendChild(b);
      ticks.push({ mid: b.dataset.mid, el: b });
    }
    // P0: 刚提交、还没拿到正式 message_id 的提问——先给一个临时刻度，
    //     服务端确认后由 sync() 换成真实 ID；发送失败则 dropPending() 撤掉。
    for (var j = 0; j < pendingTurns.length; j++) {
      var pt = pendingTurns[j];
      var pb = document.createElement('button');
      pb.type = 'button';
      pb.className = 'turn-tick is-pending';
      pb.dataset.mid = String(pt.message_id || '');
      pb.dataset.tip = '第 ' + (turnsCache.length + j + 1) + ' 次提问（正在保存）· ' + shortText(pt.text);
      pb.setAttribute('aria-label', '第 ' + (turnsCache.length + j + 1) + ' 次提问，正在保存');
      pb.addEventListener('click', function () {
        notify('这条提问还在保存中，稍等片刻就能定位。');
      });
      trackEl.appendChild(pb);
      ticks.push({ mid: pb.dataset.mid, el: pb, pending: true });
    }
    layoutTicks();
    bindRailInteractions();
    syncActive();
  }

  /* P0: 提交瞬间先插一个临时刻度（不依赖刷新、不轮询）。 */
  function addPending(crid, text) {
    if (!crid) return;
    var sid = currentSession();
    if (!sid) return;
    for (var i = 0; i < pendingTurns.length; i++) {
      if (pendingTurns[i].crid === crid) return;   // 同一条提问只生成一个刻度
    }
    pendingTurns.push({ message_id: 'pending:' + crid, crid: crid, text: String(text || '') });
    build();
  }

  /* P0: 发送失败——临时刻度必须撤掉，不能永久留在正式导航里。 */
  function dropPending(crid) {
    if (!crid) return;
    var before = pendingTurns.length;
    pendingTurns = pendingTurns.filter(function (t) { return t.crid !== crid; });
    if (pendingTurns.length !== before) build();
  }

  /* P0: 服务端已落库——拉一次最新一页索引，用真实 message_id 替换临时刻度。
     只发一次 GET，不轮询、不改 SSE 协议。 */
  function sync(pendingCrid, text) {
    var sid = currentSession();
    if (!sid) return Promise.resolve(false);
    var known = turnsCache.length;
    return fetchTurns(0).then(function (d) {
      if (d && sid === currentSession()) {
        var got = d.turns || [];
        if (got.length) {
          var seen = Object.create(null);
          for (var i = 0; i < turnsCache.length; i++) seen[String(turnsCache[i].message_id)] = 1;
          var merged = [];
          for (var k = 0; k < got.length; k++) {
            var mid = String(got[k].message_id);
            if (!seen[mid]) { merged.push(got[k]); seen[mid] = 1; }
          }
          if (merged.length) turnsCache = turnsCache.concat(merged);
        }
      }
      // 只有真的拿到新轮次才撤临时刻度；否则保留，等下一次确认（避免"提问消失了"）。
      if (turnsCache.length > known || !pendingCrid) {
        if (pendingCrid) dropPending(pendingCrid);
        else build();
      } else {
        build();
      }
      return turnsCache.length > known;
    }).catch(function () { build(); return false; });
  }

  /* 按轮次数量算刻度间距与高度：轮次少时稀疏、多了自动压缩、再多就滚动。
     间距上限 28px 避免两三个轮次时轨道出现大片空白。 */
  function layoutTicks() {
    var n = ticks.length;
    if (!n) return;
    var avail = Math.max(160, Math.min(Math.round(window.innerHeight * 0.72), 620) - 16);
    var tickH = n > 90 ? 2 : 3;
    var gap = n > 1 ? (avail - n * tickH) / (n - 1) : 0;
    gap = Math.max(2, Math.min(28, gap));
    trackEl.style.setProperty('--rail-tick-h', tickH + 'px');
    trackEl.style.setProperty('--rail-gap', (Math.round(gap * 10) / 10) + 'px');
    trackEl.classList.toggle('is-dense', n > 90);
  }

  /* 鱼眼：悬停刻度 i 时，它和上下各两级邻居局部放大，其余保持原样。     鼠标按下到松开之间锁布局，防止目标在点击瞬间位移导致误点。 */
  function applyFisheye(index) {
    if (trackEl.classList.contains('is-locked')) return;
    focusedIdx = index;
    for (var i = 0; i < ticks.length; i++) {
      var d = Math.abs(i - index);
      var el = ticks[i].el;
      el.classList.toggle('is-focus', d === 0);
      el.classList.toggle('is-near-1', d === 1);
      el.classList.toggle('is-near-2', d === 2);
    }
  }

  function clearFisheye() {
    if (trackEl.classList.contains('is-locked')) return;
    focusedIdx = -1;
    for (var i = 0; i < ticks.length; i++) {
      ticks[i].el.classList.remove('is-focus', 'is-near-1', 'is-near-2');
    }
  }

  function bindRailInteractions() {
    if (!trackEl || trackEl.dataset.fisheyeBound === '1') return;
    trackEl.dataset.fisheyeBound = '1';
    trackEl.addEventListener('mouseleave', clearFisheye);
    trackEl.addEventListener('pointerdown', function () {
      trackEl.classList.add('is-locked');
    });
    var unlock = function () {
      if (!trackEl.classList.contains('is-locked')) return;
      trackEl.classList.remove('is-locked');
      if (focusedIdx >= 0) applyFisheye(focusedIdx);
    };
    window.addEventListener('pointerup', unlock);
    window.addEventListener('pointercancel', unlock);
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
    if (Date.now() < activeLockUntil) return;
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

  function pinActive(mid) {
    activeMid = String(mid);
    activeLockUntil = Date.now() + 2200;
    markActive();
    // 跳转会重建刻度（新 DOM 节点），高亮需要在新节点上再钉两次
    [800, 2000].forEach(function (ms) {
      setTimeout(function () { activeMid = String(mid); markActive(); }, ms);
    });
  }

  async function focusTurn(mid) {
    if (!mid || busy) return;
    if (typeof jumpToMessage === 'function' && jumpToMessage(mid)) { pinActive(mid); return; }
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
      if (typeof jumpToMessage === 'function' && jumpToMessage(mid)) { pinActive(mid); return; }
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
    window.addEventListener('resize', function () { layoutTicks(); onScroll(); }, { passive: true });
    loadTurns();
    // app.js restores history asynchronously; re-check once it settles.
    setTimeout(scheduleBuild, 600);
    setTimeout(scheduleBuild, 1800);
  }

  window.turnRail = {
    refresh: scheduleBuild, focus: focusTurn, reload: loadTurns,
    addPending: addPending, dropPending: dropPending, sync: sync
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
