/* D: 吉祥物状态机 V2 —— 状态决定动作类别，随机数只决定小动作及其出现时间。

   设计约束（来自设计稿）：
   - 待机时 80%–90% 的时间安静趴着，偶尔眨眼；不会一会儿翻书一会儿跳舞。
   - 每 4–12 秒才考虑一次微动作，且不连续重复同一个小动作。
   - 翻书 / 敲字按真实工具与生成事件驱动；完成、失败只短暂播放一次。
   - 拖动优先于展示动作，松开后回到真实 Agent 状态。
   - 取消后不保留检索或敲字状态（cancel → idle）。
   - 旧请求的终态事件不得覆盖新请求的动画。

   帧来源：assets/mascot/<state>.png，10 列 × 1 行的帧带，单帧 160×118。 */
(function () {
  'use strict';

  /* base: 常态帧列；micro: 可偶尔触发的小动作帧列 */
  var POSES = {
    idle:       { base: 0, micro: [1, 2, 3], idleMin: 4000, idleMax: 12000, baseChance: 0.86 },
    planning:   { cycle: [0, 1, 2],          hold: [900, 1600] },
    retrieving: { cycle: [0, 1, 2, 3],       hold: [700, 1100] },
    generating: { cycle: [0, 1, 2],          hold: [520, 820] },
    completed:  { once: [0, 1, 2, 3],        hold: [200, 260], then: 'idle', back: 2400 },
    failed:     { once: [0, 1],              hold: [420, 520], then: 'idle', back: 2800 },
    dragging:   { static: 0 },
    dropping:   { once: [0, 1, 2],           hold: [200, 240], then: 'idle', back: 800 }
  };

  var STORE_KEY = 'citrus_mascot_hidden';
  var TERMINAL = { completed: 1, failed: 1 };

  var el = null;
  var state = 'idle';
  var timer = null;
  var idleTimer = null;
  var lastMicro = -1;
  var lastPlanningAt = 0;
  var epoch = 0;
  var posX = 0, posY = 0;            // 相对默认锚点的持久偏移（聊天区坐标）
  var POS_KEY = 'citrus_mascot_pos';
  var reduced = false;

  function now() { return Date.now(); }

  function rnd(min, max) { return min + Math.random() * (max - min); }

  function showFrame(col) {
    if (!el) return;
    el.style.setProperty('--mascot-col', String(col));
  }

  function clearTimers() {
    if (timer) { clearTimeout(timer); timer = null; }
    if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
  }

  function paint(name) {
    if (!el) return;
    state = name;
    el.setAttribute('data-state', name);
    el.dataset.pose = name;
  }

  /* 待机：绝大多数时间停在一帧安静趴着，隔 4–12 秒才可能眨眼/伸个懒腰 */
  function idleLoop(token) {
    if (token !== epoch) return;
    var spec = POSES.idle;
    idleTimer = setTimeout(function () {
      if (token !== epoch) return;
      var wantsMicro = Math.random() > spec.baseChance;
      if (!wantsMicro || reduced) {
        showFrame(spec.base);
        idleLoop(token);
        return;
      }
      var pool = spec.micro.filter(function (c) { return c !== lastMicro; });
      var col = pool[Math.floor(Math.random() * pool.length)];
      lastMicro = col;
      showFrame(col);
      // 小动作只持续很短，然后回到安静趴着
      setTimeout(function () {
        if (token !== epoch) return;
        showFrame(spec.base);
      }, rnd(260, 520));
      idleLoop(token);
    }, rnd(spec.idleMin, spec.idleMax));
  }

  function cycleLoop(token, name, idx) {
    if (token !== epoch) return;
    var spec = POSES[name];
    if (!spec || !spec.cycle) return;
    var col = spec.cycle[idx % spec.cycle.length];
    showFrame(col);
    timer = setTimeout(function () {
      cycleLoop(token, name, idx + 1);
    }, rnd(spec.hold[0], spec.hold[1]));
  }

  function onceLoop(token, name, idx) {
    if (token !== epoch) return;
    var spec = POSES[name];
    if (!spec || !spec.once) return;
    if (idx >= spec.once.length) {
      timer = setTimeout(function () {
        if (token !== epoch) return;
        setState(spec.then || 'idle');
      }, 120);
      return;
    }
    showFrame(spec.once[idx]);
    timer = setTimeout(function () {
      onceLoop(token, name, idx + 1);
    }, rnd(spec.hold[0], spec.hold[1]));
  }

  function start(name, token) {
    var spec = POSES[name];
    if (!spec) return;
    if (spec.static !== undefined) { showFrame(spec.static); return; }
    if (spec.cycle) { cycleLoop(token, name, 0); return; }
    if (spec.once) { onceLoop(token, name, 0); return; }
    showFrame(spec.base || 0);
    idleLoop(token);
  }

  function setState(name, opts) {
    if (!el || !POSES.hasOwnProperty(name)) return;
    var force = !!(opts && opts.force);
    if (name === 'planning') lastPlanningAt = now();
    // 旧请求的终态不能覆盖刚开始的新请求（新请求不可能 400ms 内就结束）
    if (TERMINAL[name] && !force && now() - lastPlanningAt < 400) return;
    if (name === state && !force) return;
    clearTimers();
    epoch += 1;
    paint(name);
    start(name, epoch);
  }

  /* P3: 位置以「相对默认锚点的偏移」保存，拖到哪里就留在哪里；
     靠近聊天区左/右/输入框上沿时连续吸附，不设单一固定吸附点。 */
  function baseRect() {
    if (!el) return null;
    var prev = el.style.transform;
    el.style.transform = 'none';
    var r = el.getBoundingClientRect();
    el.style.transform = prev;
    return r;
  }

  function limits() {
    var base = baseRect();
    var box = document.getElementById('chat-container');
    var br = box ? box.getBoundingClientRect() : { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
    var composer = document.querySelector('.input-box') || (el && el.parentNode);
    var cr = composer && composer.getBoundingClientRect ? composer.getBoundingClientRect() : null;
    var bottom = cr ? cr.top - 4 : br.bottom - 8;
    return {
      minX: br.left + 8 - base.left,
      maxX: br.right - 8 - base.right,
      minY: br.top + 8 - base.top,
      maxY: bottom - base.bottom
    };
  }

  function clamp(x, y) {
    var L = limits();
    return {
      x: Math.max(L.minX, Math.min(L.maxX, x)),
      y: Math.max(L.minY, Math.min(L.maxY, y))
    };
  }

  function applyPos() {
    if (!el) return;
    el.style.transform = 'translate(' + Math.round(posX) + 'px,' + Math.round(posY) + 'px)';
  }

  function savePos() {
    try { localStorage.setItem(POS_KEY, JSON.stringify({ x: posX, y: posY })); } catch (_) {}
  }

  function restorePos() {
    var raw = null;
    try { raw = localStorage.getItem(POS_KEY); } catch (_) {}
    if (!raw) return;
    try {
      var v = JSON.parse(raw);
      if (typeof v.x === 'number' && typeof v.y === 'number') {
        var c = clamp(v.x, v.y);
        posX = c.x; posY = c.y;
        applyPos();
      }
    } catch (_) {}
  }

  function resetPosition() {
    posX = 0; posY = 0;
    applyPos();
    try { localStorage.removeItem(POS_KEY); } catch (_) {}
  }

  /* 松手时的就近吸附：贴左/右边缘或输入框上沿时对齐，否则原地停留。 */
  function snap(x, y) {
    var L = limits(), S = 28;
    if (Math.abs(y - L.maxY) < S) y = L.maxY;
    if (Math.abs(x - L.minX) < S) x = L.minX;
    else if (Math.abs(x - L.maxX) < S) x = L.maxX;
    return { x: x, y: y };
  }

  function bindDrag(node) {
    var dragging = false, moved = false, sx = 0, sy = 0, ox = 0, oy = 0;

    node.addEventListener('pointerdown', function (e) {
      if (e.button !== undefined && e.button !== 0) return;
      dragging = true; moved = false; sx = e.clientX; sy = e.clientY;
      ox = posX; oy = posY;
      try { node.setPointerCapture(e.pointerId); } catch (_) {}
    });

    node.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var dx = e.clientX - sx, dy = e.clientY - sy;
      if (!moved && Math.abs(dx) + Math.abs(dy) > 6) {
        moved = true;
        node.classList.add('is-dragging');
        setState('dragging', { force: true });   // 拖动优先于任何展示动作
      }
      if (moved) {
        var c = clamp(ox + dx, oy + dy);
        posX = c.x; posY = c.y;
        applyPos();
        node.style.transform += ' rotate(' + Math.max(-14, Math.min(14, dx / 12)) + 'deg)';
      }
    });

    function finish() {
      if (!dragging) return;
      dragging = false;
      node.classList.remove('is-dragging');
      if (moved) {
        var s2 = snap(posX, posY);
        posX = s2.x; posY = s2.y;
        applyPos();
        savePos();
        setState('dropping', { force: true });   // 轻微落地，然后回到真实 Agent 状态
      }
      moved = false;
    }

    node.addEventListener('pointerup', finish);
    node.addEventListener('pointercancel', finish);
    node.addEventListener('lostpointercapture', finish);

    /* 双击复位：把吉祥物放回输入框上沿的默认位置 */
    node.addEventListener('dblclick', function (e) {
      e.preventDefault();
      resetPosition();
    });
  }

  function applyHidden(hidden) {
    if (!el) return;
    el.classList.toggle('is-hidden', !!hidden);
    el.setAttribute('aria-hidden', hidden ? 'true' : 'false');
    var btn = document.getElementById('mascotToggleBtn');
    if (btn) btn.textContent = hidden ? '显示吉祥物' : '隐藏吉祥物';
  }

  function hide() {
    try { localStorage.setItem(STORE_KEY, '1'); } catch (_) {}
    applyHidden(true);
  }

  function show() {
    try { localStorage.removeItem(STORE_KEY); } catch (_) {}
    applyHidden(false);
  }

  function init() {
    el = document.getElementById('citrusMascot');
    if (!el) return;
    reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    var stored = '0';
    try { stored = localStorage.getItem(STORE_KEY) || '0'; } catch (_) {}
    applyHidden(stored === '1');
    bindDrag(el);
    restorePos();
    window.addEventListener('resize', function () {
      var c = clamp(posX, posY);
      posX = c.x; posY = c.y;
      applyPos();
    }, { passive: true });
    var close = document.getElementById('mascotClose');
    if (close) {
      close.addEventListener('click', function (e) {
        e.stopPropagation();
        hide();
      });
    }
    setState('idle', { force: true });
  }

  window.CitrusMascot = {
    setState: setState,
    resetPosition: resetPosition,
    hide: hide,
    show: show,
    toggle: function () {
      if (!el) return;
      applyHidden(!el.classList.contains('is-hidden'));
      try {
        if (el.classList.contains('is-hidden')) localStorage.setItem(STORE_KEY, '1');
        else localStorage.removeItem(STORE_KEY);
      } catch (_) {}
    },
    getState: function () { return state; }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
