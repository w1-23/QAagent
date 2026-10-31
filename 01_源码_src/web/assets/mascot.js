/* D: 吉祥物 V3 —— 状态机 + 优先级仲裁 + 自主行为调度层

   优先级（高 → 低）：
   1. 用户主动操作：拖动 > 点击回应 > 尺寸/显隐控件（rank 40~50）
   2. 真实任务动作：planning / retrieving / generating / completed / failed（rank 30）
   3. 自主随机动作：idle（眨眼/歪头/看向旁边）、表情（惊讶/开心/星星眼）、
      leisure（打盹/看书/发呆/听音乐/喝茶）、睡觉（rank 20）
   4. 默认待机：idle[0] 安静趴着常驻（rank 10）

   调度原则：
   - 自主动作只在「没有真实任务动作」时出现；真实任务动作绝不被随机动作抢占
     （canAuto() 不满足 + requestAction 的 interruptible 仲裁双保险）。
   - 用户点击可短暂打断任务动作，但播完必须回到被打断的真实任务动作。
   - 加权随机（按 bucket 45/25/20/10）+ 单动作冷却 + 最近 3 个不重复，不是固定循环。
   - 统一用 setTimeout 推进（不用 setInterval）：隐藏 / 拖动 / 减少动画 / document.hidden
     时全部清空，恢复时不补播积压动作。
   - 待机间隔 6–12 秒（三档活跃程度可切），随机种子整个页面会话固定，便于复现。
   - 调度只改 --mascot-col 与 data-strip，绝不写 transform / left / top，
     所以自主动作不会引起位移或缩放抖动。

   帧来源：assets/mascot/<strip>.png，10 列 × 1 行，单帧 160×118。 */
(function () {
  'use strict';

  var STORE_KEY = 'citrus_mascot_hidden';
  var SCALE_KEY = 'citrus_mascot_scale';
  var POS_KEY = 'citrus_mascot_pos';
  var MOTION_KEY = 'citrus_pet_motion';       // app.js: '0' = 关闭宠物动画
  var ACTIVITY_KEY = 'citrus_mascot_activity';
  var SCALE_MIN = 0.8, SCALE_MAX = 1.8;
  var ACTION_THROTTLE = 200;                  // 连点节流：不排队
  var RECENT_MEMORY = 3;                      // 最近 N 个动作不重复
  var NEAR_PX = 36;                           // 「靠近宠物」的判定半径
  var REVEAL_LEAVE_MS = 260;                  // 离开宠物+浮层后收起控件的延迟
  var TOUCH_REVEAL_MS = 4000;                 // 触屏点一下，控件显示 4s

  /* ═══ 动作清单（唯一事实来源）══════════════════════════════════════
     字段：
       id            动作 ID（日志 / window.CitrusMascot.getAction() 用）
       cat           idle | expression | leisure | sleep | work | interact | drag | fallback
       bucket        自主动作的权重桶：idle / leisure / expression / sleep
       strip         帧带文件名（对应 CSS 的 [data-strip]）
       frames        [[列号, 该帧停留毫秒], ...]
       loop          once 播完即止 | hold 定格 frames[0] | cycle 循环 | static 定格到被替换
       auto          是否允许进入自主随机池
       weight        同桶内相对权重
       cooldown      同一个动作两次之间的最小间隔（毫秒）
       interruptible 是否允许被更低优先级的动作替换
       label         中文备注                                                */
  var ACTIONS = [
    /* ── 4. 默认待机 ───────────────────────────────────────────── */
    { id: 'idle.rest', cat: 'fallback', bucket: null, strip: 'idle',
      frames: [[0, 0]], loop: 'hold', auto: false, weight: 0, cooldown: 0,
      interruptible: true, label: '安静趴着' },

    /* ── 3a. idle：眨眼 / 歪头 / 看向旁边（桶权重 45）─────────── */
    { id: 'idle.blink', cat: 'idle', bucket: 'idle', strip: 'idle',
      frames: [[1, 90], [2, 110], [0, 150]], loop: 'once', auto: true,
      weight: 32, cooldown: 6000, interruptible: true, label: '眨眼' },
    { id: 'idle.glance', cat: 'idle', bucket: 'idle', strip: 'idle',
      frames: [[3, 240], [1, 190], [0, 220]], loop: 'once', auto: true,
      weight: 20, cooldown: 11000, interruptible: true, label: '看向旁边' },
    { id: 'idle.tilt', cat: 'idle', bucket: 'idle', strip: 'idle',
      frames: [[6, 380], [0, 240]], loop: 'once', auto: true,
      weight: 16, cooldown: 16000, interruptible: true, label: '歪头' },
    { id: 'idle.stretch', cat: 'idle', bucket: 'idle', strip: 'idle',
      frames: [[4, 300], [5, 220], [0, 240]], loop: 'once', auto: true,
      weight: 12, cooldown: 24000, interruptible: true, label: '伸个懒腰' },
    { id: 'idle.shift', cat: 'idle', bucket: 'idle', strip: 'idle',
      frames: [[1, 260], [3, 240], [0, 200]], loop: 'once', auto: true,
      weight: 10, cooldown: 20000, interruptible: true, label: '换个姿势趴' },

    /* ── 3b. 表情：开心 / 星星眼 / 惊讶 / 眯眼 / 好奇（桶权重 20）── */
    { id: 'expr.smile', cat: 'expression', bucket: 'expression', strip: 'idle',
      frames: [[7, 360]], loop: 'once', auto: true,
      weight: 30, cooldown: 18000, interruptible: true, label: '开心笑' },
    { id: 'expr.shine', cat: 'expression', bucket: 'expression', strip: 'idle',
      frames: [[8, 460]], loop: 'once', auto: true,
      weight: 25, cooldown: 22000, interruptible: true, label: '星星眼' },
    { id: 'expr.surprise', cat: 'expression', bucket: 'expression', strip: 'planning',
      frames: [[4, 420]], loop: 'once', auto: true,
      weight: 20, cooldown: 26000, interruptible: true, label: '惊讶' },
    { id: 'expr.squint', cat: 'expression', bucket: 'expression', strip: 'planning',
      frames: [[8, 480]], loop: 'once', auto: true,
      weight: 15, cooldown: 22000, interruptible: true, label: '眯眼' },
    { id: 'expr.curious', cat: 'expression', bucket: 'expression', strip: 'planning',
      frames: [[9, 520]], loop: 'once', auto: true,
      weight: 10, cooldown: 30000, interruptible: true, label: '好奇歪头' },

    /* ── 3c. leisure：打盹 / 看书 / 发呆 / 听音乐 / 喝茶（桶权重 25）── */
    { id: 'leisure.read', cat: 'leisure', bucket: 'leisure', strip: 'retrieving',
      frames: [[3, 5200]], loop: 'hold', auto: true,
      weight: 26, cooldown: 30000, interruptible: true, label: '低头看书' },
    { id: 'leisure.readbook', cat: 'leisure', bucket: 'leisure', strip: 'retrieving',
      frames: [[2, 4600]], loop: 'hold', auto: true,
      weight: 18, cooldown: 30000, interruptible: true, label: '把书摊开看' },
    { id: 'leisure.doze', cat: 'leisure', bucket: 'leisure', strip: 'dropping',
      frames: [[0, 4200]], loop: 'hold', auto: true,
      weight: 20, cooldown: 26000, interruptible: true, label: '趴着发呆' },
    { id: 'leisure.music', cat: 'leisure', bucket: 'leisure', strip: 'dropping',
      frames: [[7, 6000]], loop: 'hold', auto: true,
      weight: 12, cooldown: 45000, interruptible: true, label: '戴耳机听音乐' },
    { id: 'leisure.tea', cat: 'leisure', bucket: 'leisure', strip: 'dropping',
      frames: [[8, 4200]], loop: 'hold', auto: true,
      weight: 10, cooldown: 45000, interruptible: true, label: '端杯子喝茶' },
    { id: 'leisure.books', cat: 'leisure', bucket: 'leisure', strip: 'retrieving',
      frames: [[9, 5200]], loop: 'hold', auto: true,
      weight: 8, cooldown: 50000, interruptible: true, label: '头顶书歇一会' },

    /* ── 3d. 睡觉（桶权重 10）────────────────────────────────── */
    { id: 'sleep.curl', cat: 'sleep', bucket: 'sleep', strip: 'idle',
      frames: [[9, 8000]], loop: 'hold', auto: true,
      weight: 60, cooldown: 70000, interruptible: true, label: '趴绿叶垫打盹' },
    { id: 'sleep.blanket', cat: 'sleep', bucket: 'sleep', strip: 'dropping',
      frames: [[9, 9000]], loop: 'hold', auto: true,
      weight: 40, cooldown: 90000, interruptible: true, label: '盖被子睡觉' },

    /* ── 2. work：只在真实任务态出现，不进随机池 ──────────────── */
    { id: 'work.think', cat: 'work', bucket: null, strip: 'planning',
      frames: [[0, 1200], [1, 900], [2, 1400], [4, 1000], [5, 1100], [8, 1300], [9, 950]],
      loop: 'cycle', auto: false, weight: 0, cooldown: 0,
      interruptible: false, label: '思考' },
    { id: 'work.read', cat: 'work', bucket: null, strip: 'retrieving',
      frames: [[0, 900], [1, 750], [2, 1000], [3, 800], [4, 1100], [0, 850]],
      loop: 'cycle', auto: false, weight: 0, cooldown: 0,
      interruptible: false, label: '翻书检索' },
    { id: 'work.search', cat: 'work', bucket: null, strip: 'retrieving',
      frames: [[5, 850], [7, 700], [8, 780], [5, 900]],
      loop: 'cycle', auto: false, weight: 0, cooldown: 0,
      interruptible: false, label: '举放大镜检索' },
    { id: 'work.type', cat: 'work', bucket: null, strip: 'generating',
      frames: [[0, 700], [1, 560], [2, 820], [3, 640], [0, 700]],
      loop: 'cycle', auto: false, weight: 0, cooldown: 0,
      interruptible: false, label: '敲字生成' },
    { id: 'work.done', cat: 'work', bucket: null, strip: 'completed',
      frames: [[0, 210], [1, 220], [2, 240], [4, 210], [8, 230]],
      loop: 'once', auto: false, weight: 0, cooldown: 0,
      interruptible: false, label: '完成欢呼' },
    { id: 'work.fail', cat: 'work', bucket: null, strip: 'failed',
      frames: [[0, 460], [1, 420], [5, 480]],
      loop: 'once', auto: false, weight: 0, cooldown: 0,
      interruptible: false, label: '失败低落' },

    /* ── 1. interact：只由用户点击触发 ───────────────────────── */
    { id: 'interact.click', cat: 'interact', bucket: null, strip: 'completed',
      frames: [[2, 180], [0, 200], [8, 200]], loop: 'once', auto: false,
      weight: 0, cooldown: 0, interruptible: true, label: '点击欢呼' },
    { id: 'interact.wave', cat: 'interact', bucket: null, strip: 'completed',
      frames: [[9, 200], [5, 200]], loop: 'once', auto: false,
      weight: 0, cooldown: 0, interruptible: true, label: '挥手' },
    { id: 'interact.love', cat: 'interact', bucket: null, strip: 'completed',
      frames: [[7, 560]], loop: 'once', auto: false,
      weight: 0, cooldown: 0, interruptible: true, label: '抱爱心' },

    /* ── 拖动（最高优先级，只由指针事件驱动）────────────────── */
    { id: 'drag.hold', cat: 'drag', bucket: null, strip: 'dragging',
      frames: [[0, 0]], loop: 'static', auto: false, weight: 0, cooldown: 0,
      interruptible: false, label: '被拎起' },
    { id: 'drop.land', cat: 'drag', bucket: null, strip: 'dropping',
      frames: [[0, 180], [1, 200], [2, 220]], loop: 'once', auto: false,
      weight: 0, cooldown: 0, interruptible: true, label: '落下' }
  ];

  var BY_ID = {};
  (function () {
    for (var i = 0; i < ACTIONS.length; i++) BY_ID[ACTIONS[i].id] = ACTIONS[i];
  })();

  /* 自主随机池的桶权重：idle 45 / leisure 25 / 表情 20 / 睡觉 10 */
  var BUCKET_WEIGHT = { idle: 45, leisure: 25, expression: 20, sleep: 10 };

  /* 真实任务态 → 动作。检索翻书/放大镜交替，避免每次都是同一段 */
  var TASK_ACTION = { planning: 'work.think', retrieving: 'work.read', generating: 'work.type' };
  var TASK_ALT = { retrieving: ['work.read', 'work.search'] };
  var TERMINAL_ACTION = { completed: 'work.done', failed: 'work.fail' };
  var STATE_NAMES = { idle: 1, planning: 1, retrieving: 1, generating: 1, completed: 1, failed: 1, dragging: 1, dropping: 1 };

  /* 待机间隔（毫秒）：三档活跃程度 */
  var ACTIVITY = { quiet: [15000, 25000], natural: [6000, 12000], lively: [4000, 8000] };

  /* ═══ 运行态 ═══════════════════════════════════════════════════════ */
  var el = null;
  var state = 'idle';          // 语义态（data-state）
  var strip = 'idle';          // 真实帧带（data-strip）
  var taskState = null;        // 真实任务态：planning / retrieving / generating
  var taskAltIndex = 0;
  var current = null;          // 当前动作对象
  var currentRank = 0;
  var currentReason = '';
  var playToken = 0;
  var autoTimer = null, frameTimer = null, revealTimer = null, touchTimer = null;
  var autoDueAt = 0;
  var dragging = false, hidden = false, reduced = false, motionOff = false, docHidden = false;
  var autoOn = true, ready = false;
  var lastActionId = null;
  var recentIds = [];
  var lastPlayedAt = {};
  var interactSeq = 0, lastInteractAt = 0, lastPlanningAt = 0;
  var activity = 'natural';
  var seed = 0, rand = null;
  var scale = 1, posX = 0, posY = 0;
  var t0 = Date.now();

  /* ═══ 计时器登记（用于「活动计时器数量」审计）═════════════════════ */
  var timers = new Map();
  function later(fn, ms, label) {
    var h = setTimeout(function () {
      timers.delete(h);
      fn();
    }, Math.max(0, ms | 0));
    timers.set(h, label || 'timer');
    return h;
  }
  function cancelTimer(h) {
    if (h && timers.has(h)) { clearTimeout(h); timers.delete(h); }
  }
  function timerCount() { return timers.size; }
  function timerLabels() {
    var out = {};
    timers.forEach(function (label) { out[label] = (out[label] || 0) + 1; });
    return out;
  }

  /* ═══ 日志 ═════════════════════════════════════════════════════════ */
  var LOG = {
    entries: [],
    max: 400,
    seed: 0,
    onEntry: null,
    dump: function (n) {
      var a = LOG.entries;
      return n ? a.slice(Math.max(0, a.length - n)) : a.slice();
    },
    clear: function () { LOG.entries.length = 0; },
    timers: function () { return { count: timerCount(), labels: timerLabels() }; },
    state: function () { return snapshot(); }
  };

  function now() { return Date.now(); }

  function logEvent(kind, id, reason, extra) {
    var e = {
      t: now(), rel: now() - t0, kind: kind, id: id || null, reason: reason || '',
      state: state, strip: strip, task: taskState, action: current ? current.id : null,
      seed: seed, timers: timerCount()
    };
    if (extra) {
      for (var k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k)) e[k] = extra[k];
      }
    }
    LOG.entries.push(e);
    if (LOG.entries.length > LOG.max) LOG.entries.shift();
    if (typeof LOG.onEntry === 'function') { try { LOG.onEntry(e); } catch (_) {} }
    return e;
  }

  /* ═══ 随机（会话级固定种子，可复现）═══════════════════════════════ */
  function mulberry32(a) {
    return function () {
      a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function rr(min, max) { return min + rand() * (max - min); }

  /* ═══ 帧渲染：只写 --mascot-col 与 data-strip，不动 transform ══════ */
  function renderFrame(stripName, col) {
    if (!el) return;
    if (stripName) {
      strip = stripName;
      // 属性也要跟 CSS 一致：即使当前已是这条帧带，首帧初始化时属性可能还不存在
      if (el.getAttribute('data-strip') !== stripName) el.setAttribute('data-strip', stripName);
    }
    el.style.setProperty('--mascot-col', String(col));
  }

  function paint(name) {
    if (!el) return;
    state = name;
    el.setAttribute('data-state', name);
    el.dataset.pose = name;
  }

  /* ═══ 动作播放 ═════════════════════════════════════════════════════
     loop: once 顺序播完 / hold 定格 frames[0] / cycle 无限循环 / static 定格到被替换 */
  function playAction(action, reason, rank, after) {
    if (!el || !action) return;
    var token = ++playToken;
    cancelTimer(frameTimer);
    frameTimer = null;
    current = action;
    currentRank = rank || 10;
    currentReason = reason || '';
    lastPlayedAt[action.id] = now();
    el.setAttribute('data-action', action.id);
    var frames = action.frames;
    var idx = 0;

    function finish() {
      if (token !== playToken) return;
      var done = current;
      current = null;
      currentRank = 0;
      el.removeAttribute('data-action');
      if (typeof after === 'function') after(done);
    }

    function step() {
      if (token !== playToken) return;
      idx += 1;
      if (idx >= frames.length) { finish(); return; }
      var f = frames[idx];
      renderFrame(action.strip, f[0]);
      if (idx === frames.length - 1) {
        frameTimer = later(finish, f[1] || 200, 'action-frame');
      } else {
        frameTimer = later(step, f[1] || 200, 'action-frame');
      }
    }

    if (!frames || !frames.length) { finish(); return; }
    renderFrame(action.strip, frames[0][0]);

    if (action.loop === 'static') return;              // 定格，等下一段动作替换
    if (action.loop === 'cycle') {
      var advance = function () {
        if (token !== playToken) return;
        idx = (idx + 1) % frames.length;
        var f = frames[idx];
        renderFrame(action.strip, f[0]);
        frameTimer = later(advance, f[1] || 800, 'action-frame');
      };
      frameTimer = later(advance, frames[0][1] || 800, 'action-frame');
      return;
    }
    if (action.loop === 'hold') {
      frameTimer = later(finish, frames[0][1] || 800, 'action-frame');
      return;
    }
    // once
    if (frames.length === 1) {
      frameTimer = later(finish, frames[0][1] || 200, 'action-frame');
      return;
    }
    frameTimer = later(step, frames[0][1] || 200, 'action-frame');
  }

  /* 优先级仲裁：低优先级不能打断 interruptible === false 的动作 */
  function requestAction(action, reason, rank, after) {
    if (!action) return false;
    if (current && current.interruptible === false && rank < currentRank) {
      logEvent('blocked', action.id, 'lower-than:' + current.id);
      return false;
    }
    playAction(action, reason, rank, after);
    return true;
  }

  /* ═══ 待机 / 恢复 ═════════════════════════════════════════════════ */
  function playRest() {
    if (!el) return;
    cancelTimer(frameTimer);
    frameTimer = null;
    playToken += 1;
    current = null;
    currentRank = 10;
    currentReason = 'rest';
    paint('idle');
    renderFrame('idle', 0);
    el.setAttribute('data-action', 'idle.rest');
  }

  /* 从「被覆盖的状态」回到正确状态：有任务回任务，没任务回待机 */
  function restoreFromTask() {
    if (!el || !ready) return;
    if (dragging || hidden) return;
    if (still()) { playRest(); return; }
    if (taskState) {
      paint(taskState);
      var id = TASK_ACTION[taskState];
      var alt = TASK_ALT[taskState];
      if (alt) { id = alt[taskAltIndex % alt.length]; taskAltIndex += 1; }
      requestAction(BY_ID[id], 'task-resume', 30, null);
      return;
    }
    playRest();
    scheduleAuto();
  }

  /* ═══ 自主调度 ═════════════════════════════════════════════════════ */
  function still() { return reduced || motionOff; }

  function canAuto() {
    // 「用户交互动作还在播」时不排自主动作；用当前动作的 cat 判定，
    // 而不是另立一个标志位——动作被更高优先级替换时标志位会残留成 true。
    var busyWithUserAction = !!(current && current.cat === 'interact');
    return !!el && autoOn && !hidden && !dragging && !taskState && !docHidden &&
           !busyWithUserAction && !still();
  }

  function cooldownLeft(action) {
    var last = lastPlayedAt[action.id] || 0;
    if (!action.cooldown || !last) return 0;
    var left = action.cooldown - (now() - last);
    return left > 0 ? left : 0;
  }

  function pickWeighted(list) {
    var total = 0, i;
    for (i = 0; i < list.length; i++) total += (list[i].weight || 1);
    if (total <= 0) return list[list.length - 1];
    var r = rand() * total, acc = 0;
    for (i = 0; i < list.length; i++) {
      acc += (list[i].weight || 1);
      if (r <= acc) return list[i];
    }
    return list[list.length - 1];
  }

  function pickByBucket(pool) {
    var groups = {}, order = [], i, j, a, b;
    for (i = 0; i < pool.length; i++) {
      a = pool[i];
      b = a.bucket || 'idle';
      if (!groups[b]) { groups[b] = []; order.push(b); }
      groups[b].push(a);
    }
    var total = 0;
    for (j = 0; j < order.length; j++) total += (BUCKET_WEIGHT[order[j]] || 0);
    if (total <= 0) return pickWeighted(pool);
    var r = rand() * total, acc = 0, chosen = order[0];
    for (j = 0; j < order.length; j++) {
      acc += (BUCKET_WEIGHT[order[j]] || 0);
      if (r <= acc) { chosen = order[j]; break; }
    }
    return pickWeighted(groups[chosen] || pool);
  }

  /* 逐级放宽：先严格（不重复 + 冷却），池空就放宽重复限制，再放宽冷却，
     最后只要合法就上——「缺帧/全冷却不能让调度器停摆」。 */
  function pickAutoAction() {
    var tiers = [
      function (a) { return recentIds.indexOf(a.id) < 0 && a.id !== lastActionId && cooldownLeft(a) <= 0; },
      function (a) { return a.id !== lastActionId && cooldownLeft(a) <= 0; },
      function (a) { return cooldownLeft(a) <= 0; },
      function () { return true; }
    ];
    for (var t = 0; t < tiers.length; t++) {
      var pool = [];
      for (var i = 0; i < ACTIONS.length; i++) {
        var a = ACTIONS[i];
        if (!a.auto) continue;
        if (!a.frames || !a.frames.length) continue;
        if (tiers[t](a)) pool.push(a);
      }
      if (pool.length) return pickByBucket(pool);
    }
    return null;
  }

  function scheduleAuto(delay) {
    cancelTimer(autoTimer);
    autoTimer = null;
    autoDueAt = 0;
    if (!canAuto()) return;
    var range = ACTIVITY[activity] || ACTIVITY.natural;
    var d = (typeof delay === 'number') ? delay : rr(range[0], range[1]);
    autoDueAt = now() + d;
    autoTimer = later(autoFire, d, 'auto');
  }

  function autoFire() {
    autoTimer = null;
    autoDueAt = 0;
    if (!canAuto()) return;
    var action = pickAutoAction();
    if (!action) { scheduleAuto(); return; }
    var accepted = requestAction(action, 'auto', 20, function () {
      if (lastActionId) {
        recentIds.unshift(lastActionId);
        if (recentIds.length > RECENT_MEMORY) recentIds.pop();
      }
      lastActionId = action.id;
      playRest();
      scheduleAuto();
    });
    if (!accepted) { scheduleAuto(); return; }
    logEvent('auto', action.id, 'idle-interval', { cat: action.cat, bucket: action.bucket, strip: action.strip });
  }

  /* ═══ 对外 state 入口（app.js 的 mascotSay 走这里）════════════════ */
  function playTaskAction(name, reason) {
    var id = TASK_ACTION[name];
    var alt = TASK_ALT[name];
    if (alt) { id = alt[taskAltIndex % alt.length]; taskAltIndex += 1; }
    requestAction(BY_ID[id], reason, 30, null);
  }

  function setState(name, opts) {
    if (!el || !STATE_NAMES[name]) return;
    var force = !!(opts && opts.force);
    if (name === 'planning') lastPlanningAt = now();
    // 旧请求的终态不能覆盖刚开始的新请求（新请求不可能 400ms 内就结束）
    if ((name === 'completed' || name === 'failed') && !force && now() - lastPlanningAt < 400) return;
    if (name === 'dragging' || name === 'dropping') return;   // 只由指针事件驱动

    if (name === 'idle') {
      taskState = null;
      playRest();
      scheduleAuto(0);
      return;
    }
    if (TERMINAL_ACTION[name]) {
      taskState = null;
      paint(name);
      cancelTimer(autoTimer); autoTimer = null;
      requestAction(BY_ID[TERMINAL_ACTION[name]], 'task-' + name, 30, function () {
        restoreFromTask();
      });
      return;
    }
    // planning / retrieving / generating
    if (taskState === name && !force) return;   // 幂等：同一任务态不重播，避免打断循环
    taskState = name;
    cancelTimer(autoTimer); autoTimer = null;
    paint(name);
    playTaskAction(name, 'task-' + name);
  }

  /* 用户点击回应：短暂打断任务动作，播完回到被打断的任务态 */
  function userInteract() {
    if (!el || hidden || still()) return;
    var t = now();
    if (t - lastInteractAt < ACTION_THROTTLE) return;   // 节流：连点不排队
    lastInteractAt = t;
    var pool = [BY_ID['interact.click'], BY_ID['interact.wave'], BY_ID['interact.love']];
    var action = pool[interactSeq % pool.length];
    interactSeq += 1;
    var accepted = requestAction(action, 'interact', 40, function () {
      restoreFromTask();
    });
    logEvent('interact', action.id, taskState ? 'while-task:' + taskState : 'idle-click', { accepted: accepted });
  }

  /* ═══ 位置 / 缩放（与 v2 行为一致，缩放不动姿势）══════════════════ */
  function baseRect() {
    if (!el) return null;
    var prev = el.style.transform;
    el.style.transform = 'scale(' + scale + ')';
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
    el.style.transform = 'translate(' + Math.round(posX) + 'px,' + Math.round(posY) + 'px) scale(' + scale + ')';
  }

  var SCALE_INPUTS = ['mascotScale', 'mascotSizeRange', 'petScaleRange'];
  var SCALE_LABELS = ['mascotScaleValue', 'mascotSizeValue', 'petScaleValue'];

  function syncScaleInputs(v) {
    var i, node;
    for (i = 0; i < SCALE_INPUTS.length; i++) {
      node = document.getElementById(SCALE_INPUTS[i]);
      if (node && String(node.value) !== String(v)) node.value = String(v);
    }
    for (i = 0; i < SCALE_LABELS.length; i++) {
      node = document.getElementById(SCALE_LABELS[i]);
      if (node) node.textContent = v + '%';
    }
  }

  function applyScale(pct, persist) {
    var v = Math.round(Number(pct));
    if (!isFinite(v)) v = 100;
    v = Math.max(SCALE_MIN * 100, Math.min(SCALE_MAX * 100, v));
    scale = v / 100;
    if (persist) { try { localStorage.setItem(SCALE_KEY, String(v)); } catch (_) {} }
    syncScaleInputs(v);
    if (el) {
      var c = clamp(posX, posY);
      posX = c.x; posY = c.y;
      applyPos();
    }
    clampPopover();
    return v;
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

  function setPosition(x, y) {
    if (x === null || typeof x === 'undefined') { resetPosition(); return; }
    var c = clamp(Number(x) || 0, Number(y) || 0);
    posX = c.x; posY = c.y;
    applyPos();
    savePos();
  }

  function snap(x, y) {
    var L = limits(), S = 28;
    if (Math.abs(y - L.maxY) < S) y = L.maxY;
    if (Math.abs(x - L.minX) < S) x = L.minX;
    else if (Math.abs(x - L.maxX) < S) x = L.maxX;
    return { x: x, y: y };
  }

  /* ═══ 拖动（保留 v2 的 6px 阈值与指针捕获）════════════════════════ */
  function bindDrag(node) {
    var pressed = false, moved = false, sx = 0, sy = 0, ox = 0, oy = 0, pointerId = null;
    var suppressClick = false;

    function isControlTarget(target) {
      return !!(target && target.closest &&
        target.closest('#mascotClose, #mascotScaleBtn, #mascotSizePopover'));
    }

    node.addEventListener('pointerdown', function (e) {
      if (e.button !== undefined && e.button !== 0) return;
      if (isControlTarget(e.target)) return;
      pressed = true; moved = false;
      sx = e.clientX; sy = e.clientY;
      ox = posX; oy = posY;
      pointerId = e.pointerId;
    });

    node.addEventListener('pointermove', function (e) {
      if (!pressed) return;
      var dx = e.clientX - sx, dy = e.clientY - sy;
      if (!moved && Math.abs(dx) + Math.abs(dy) > 6) {
        moved = true;
        try { node.setPointerCapture(pointerId); } catch (_) {}
        node.classList.add('is-dragging');
        // 拖动优先：暂停自主动作，切成「被拎起」定格帧
        dragging = true;
        cancelTimer(autoTimer); autoTimer = null; autoDueAt = 0;
        cancelTimer(frameTimer); frameTimer = null;
        playToken += 1;
        logEvent('drag', 'drag.hold', 'pointer-move');
        playAction(BY_ID['drag.hold'], 'drag', 50, null);
      }
      if (moved) {
        var c = clamp(ox + dx, oy + dy);
        posX = c.x; posY = c.y;
        applyPos();
        node.style.transform += ' rotate(' + Math.max(-14, Math.min(14, dx / 12)) + 'deg)';
      }
    });

    function finish() {
      if (!pressed) return;
      pressed = false;
      pointerId = null;
      node.classList.remove('is-dragging');
      if (moved) {
        var s2 = snap(posX, posY);
        posX = s2.x; posY = s2.y;
        applyPos();
        savePos();
        dragging = false;
        suppressClick = true;
        logEvent('drop', 'drop.land', 'pointer-up');
        playAction(BY_ID['drop.land'], 'drop', 45, function () { restoreFromTask(); });
      }
      moved = false;
    }

    /* 点一下有反馈；控制按钮的点击不算「点我」；200ms 节流不排队 */
    node.addEventListener('click', function (e) {
      if (isControlTarget(e.target)) return;
      if (suppressClick) { suppressClick = false; return; }
      if (state === 'dragging' || dragging) return;
      userInteract();
    });

    node.addEventListener('pointerup', finish);
    node.addEventListener('pointercancel', finish);
    node.addEventListener('lostpointercapture', finish);

    /* 点击/连点只播动作，不改位置；位置只由拖动或面板里的“复位”决定。 */
  }

  /* ═══ 显隐 ═════════════════════════════════════════════════════════ */
  function syncVisibilityLabels(isHidden) {
    var btn = document.getElementById('mascotToggleBtn');
    if (btn) btn.textContent = isHidden ? '显示吉祥物' : '隐藏吉祥物';
    var petText = document.getElementById('petToggleText');
    if (petText) petText.textContent = isHidden ? '显示宠物' : '隐藏宠物';
  }

  function applyHidden(flag) {
    if (!el) return;
    hidden = !!flag;
    el.classList.toggle('is-hidden', hidden);
    el.setAttribute('aria-hidden', hidden ? 'true' : 'false');
    syncVisibilityLabels(hidden);
    if (hidden) {
      // 隐藏即停表：不留任何还在跑的计时器
      cancelTimer(autoTimer); autoTimer = null; autoDueAt = 0;
      cancelTimer(frameTimer); frameTimer = null;
      playToken += 1;
      current = null;
      if (popEl && !popEl.hidden) closePopover('hidden');
    } else if (ready) {
      restoreFromTask();
    }
    if (typeof window.syncMascotRailButton === 'function') {
      try { window.syncMascotRailButton(); } catch (_) {}
    }
  }

  function hide() {
    try { localStorage.setItem(STORE_KEY, '1'); } catch (_) {}
    applyHidden(true);
  }

  function show() {
    try { localStorage.removeItem(STORE_KEY); } catch (_) {}
    applyHidden(false);
  }

  function getVisible() {
    if (el) return !el.classList.contains('is-hidden');
    try { return localStorage.getItem(STORE_KEY) !== '1'; } catch (_) { return true; }
  }

  function setVisible(flag) {
    if (flag) show(); else hide();
    return getVisible();
  }

  function refreshPanelState() {
    var v = Math.round(scale * 100);
    syncScaleInputs(v);
    var visible = getVisible();
    syncVisibilityLabels(!visible);
    if (popEl && scaleBtnEl) scaleBtnEl.setAttribute('aria-expanded', popEl.hidden ? 'false' : 'true');
    if (typeof window.syncMascotRailButton === 'function') {
      try { window.syncMascotRailButton(); } catch (_) {}
    }
    return { scale: v, visible: visible };
  }

  /* ═══ 大小浮层（紧贴左上角缩放按钮）═══════════════════════════════ */
  var popEl = null, scaleBtnEl = null, popRange = null;

  function clampPopover() {
    if (!popEl || popEl.hidden) return;
    popEl.style.transform = '';
    var r = popEl.getBoundingClientRect();
    var pad = 8, dx = 0, dy = 0;
    if (r.left < pad) dx = pad - r.left;
    else if (r.right > window.innerWidth - pad) dx = window.innerWidth - pad - r.right;
    if (r.top < pad) dy = pad - r.top;
    else if (r.bottom > window.innerHeight - pad) dy = window.innerHeight - pad - r.bottom;
    if (dx || dy) {
      // 父层有 scale()，平移量要先除回去，否则放大后会被多推 scale 倍
      var s = scale || 1;
      popEl.style.transform = 'translate(' + Math.round(dx / s) + 'px,' + Math.round(dy / s) + 'px)';
    }
  }

  function openPopover() {
    if (!popEl || !scaleBtnEl) return;
    // 打开浮层即“钉住”：取消失焦后排队的那次收起，否则 blur→click 的
    // 260ms 窗口里浮层会刚打开就被 leave 定时器关掉。
    cancelTimer(revealTimer);
    revealTimer = null;
    if (el) { el.classList.add('is-near'); el.classList.add('is-pinned'); }
    popEl.hidden = false;
    scaleBtnEl.setAttribute('aria-expanded', 'true');
    syncScaleInputs(Math.round(scale * 100));
    // 等一帧布局稳定再夹取，避免首帧 rect 全是 0
    if (window.requestAnimationFrame) {
      window.requestAnimationFrame(function () {
        window.requestAnimationFrame(clampPopover);
      });
    } else {
      clampPopover();
    }
    logEvent('popover', null, 'open');
  }

  function closePopover(reason) {
    if (!popEl || popEl.hidden) return;
    popEl.hidden = true;
    popEl.style.transform = '';
    if (scaleBtnEl) scaleBtnEl.setAttribute('aria-expanded', 'false');
    if (el) el.classList.remove('is-pinned');
    logEvent('popover', null, 'close:' + (reason || ''));
  }

  function bindScaleControl() {
    scaleBtnEl = document.getElementById('mascotScaleBtn');
    popEl = document.getElementById('mascotSizePopover');
    if (!scaleBtnEl || !popEl) return;
    popRange = document.getElementById('mascotSizeRange');

    // tooltip 文案从 title 搬到 data-tip，避免原生 tooltip 与自绘 tooltip 双弹
    var closeBtn = document.getElementById('mascotClose');
    var tips = [[scaleBtnEl, '调整大小（80%–180%）'], [closeBtn, '隐藏吉祥物']];
    for (var i = 0; i < tips.length; i++) {
      var node = tips[i][0];
      if (!node) continue;
      var tip = node.getAttribute('title') || tips[i][1];
      node.setAttribute('data-tip', tip);
      node.removeAttribute('title');
    }
    if (!popEl.getAttribute('role')) popEl.setAttribute('role', 'group');
    if (!popEl.getAttribute('aria-label')) popEl.setAttribute('aria-label', '吉祥物大小');

    scaleBtnEl.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
    scaleBtnEl.addEventListener('click', function (e) {
      e.stopPropagation();
      if (popEl.hidden) openPopover(); else closePopover('button');
    });
    popEl.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
    popEl.addEventListener('click', function (e) { e.stopPropagation(); });
    if (popRange) {
      var onInput = function () { applyScale(popRange.value, true); };
      popRange.addEventListener('input', onInput);
      popRange.addEventListener('change', onInput);
    }
    // 主面板的滑块（app.js 的「宠物大小」）：同一个元素也可能由 app.js 直接调 setScale，
    // 这里再挂一次 listener 保证「拖面板滑块 → 吉祥物立即跟随」；找不到就安全跳过。
    var petRange = document.getElementById('petScaleRange');
    if (petRange) {
      var onPetInput = function () {
        if (Math.round(Number(petRange.value)) !== Math.round(scale * 100)) applyScale(petRange.value, true);
      };
      petRange.addEventListener('input', onPetInput);
      petRange.addEventListener('change', onPetInput);
    }
    window.addEventListener('resize', function () { clampPopover(); }, { passive: true });
    window.addEventListener('scroll', function () { clampPopover(); }, { passive: true, capture: true });
    document.addEventListener('click', function (e) {
      if (popEl.hidden) return;
      if (popEl.contains(e.target)) return;
      if (scaleBtnEl && scaleBtnEl.contains(e.target)) return;
      closePopover('outside');
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape' || popEl.hidden) return;
      closePopover('esc');
      try { scaleBtnEl.focus(); } catch (_) {}
    });
  }

  /* ═══ P1-C：控件靠近浮现 / 离开 260ms 收起 / 触屏点显 ═════════════ */
  function bindControlsReveal() {
    var lastMoveAt = 0;

    function reveal() {
      cancelTimer(revealTimer);
      revealTimer = null;
      if (el) el.classList.add('is-near');
    }

    function concealLater() {
      cancelTimer(revealTimer);
      revealTimer = later(function () {
        revealTimer = null;
        if (!el) return;
        if (el.matches && el.matches(':hover')) return;   // 还在宠物/浮层上
        el.classList.remove('is-near');
        if (popEl && !popEl.hidden) closePopover('leave');
      }, REVEAL_LEAVE_MS, 'reveal-leave');
    }

    el.addEventListener('pointerenter', reveal);
    el.addEventListener('pointerleave', concealLater);
    if (popEl) {
      popEl.addEventListener('pointerenter', reveal);
      popEl.addEventListener('pointerleave', concealLater);
    }

    // 「靠近」不等于正好悬停：指针进入宠物外 36px 的框也算
    document.addEventListener('pointermove', function (e) {
      if (e.pointerType === 'touch' || !el) return;
      var t = now();
      if (t - lastMoveAt < 80) return;
      lastMoveAt = t;
      var r = el.getBoundingClientRect();
      var near = e.clientX >= r.left - NEAR_PX && e.clientX <= r.right + NEAR_PX &&
                 e.clientY >= r.top - NEAR_PX && e.clientY <= r.bottom + NEAR_PX;
      if (near) {
        if (!el.classList.contains('is-near')) reveal();
      } else if (el.classList.contains('is-near')) {
        concealLater();
      }
    }, { passive: true });

    // 触屏：点一下先显形 4s
    el.addEventListener('pointerdown', function (e) {
      if (e.pointerType !== 'touch' || !el) return;
      el.classList.add('is-touch');
      cancelTimer(touchTimer);
      touchTimer = later(function () {
        touchTimer = null;
        if (el) el.classList.remove('is-touch');
      }, TOUCH_REVEAL_MS, 'touch-reveal');
    }, true);

    el.addEventListener('focusin', reveal);
    el.addEventListener('focusout', concealLater);
  }

  /* ═══ 动效开关 / 页面可见性 ═══════════════════════════════════════ */
  function isReducedMotion() {
    var html = document.documentElement;
    if (html && html.getAttribute('data-reduce-motion') === '1') return true;
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  function refreshStill() {
    var r = isReducedMotion();
    if (r !== reduced) {
      reduced = r;
      if (el) el.classList.toggle('is-still', still());
      if (still()) {
        cancelTimer(autoTimer); autoTimer = null; autoDueAt = 0;
        cancelTimer(frameTimer); frameTimer = null;
        playToken += 1;
        current = null;
        playRest();
      } else {
        restoreFromTask();
      }
      logEvent('config', null, 'reduced=' + reduced + ',motion=' + !motionOff);
    }
  }

  function setMotion(on) {
    motionOff = !on;
    if (el) el.classList.toggle('is-still', still());
    if (motionOff) {
      cancelTimer(autoTimer); autoTimer = null; autoDueAt = 0;
      cancelTimer(frameTimer); frameTimer = null;
      playToken += 1;
      current = null;
      playRest();
    } else {
      restoreFromTask();
    }
    logEvent('config', null, 'motion=' + (on ? 'on' : 'off'));
    return !motionOff;
  }

  function getMotion() { return !motionOff; }

  function setPageHidden(h) {
    docHidden = !!h;
    if (docHidden) {
      // 暂停但不补播：直接丢掉在途计时器
      cancelTimer(autoTimer); autoTimer = null; autoDueAt = 0;
    } else if (!taskState) {
      scheduleAuto();
    }
    logEvent('visibility', null, docHidden ? 'hidden' : 'visible');
  }

  /* ═══ 活跃程度 ═════════════════════════════════════════════════════ */
  function setActivityLevel(level) {
    if (!ACTIVITY[level]) return activity;
    activity = level;
    try { localStorage.setItem(ACTIVITY_KEY, level); } catch (_) {}
    logEvent('config', null, 'activity=' + level);
    if (canAuto() || (!taskState && !dragging && !hidden && !still())) scheduleAuto();
    return activity;
  }

  function getActivityLevel() { return activity; }

  function snapshot() {
    var cd = {}, i, a;
    for (i = 0; i < ACTIONS.length; i++) {
      a = ACTIONS[i];
      if (a.cooldown) cd[a.id] = cooldownLeft(a);
    }
    return {
      state: state,
      strip: strip,
      action: current ? current.id : (el ? el.getAttribute('data-action') : null),
      cat: current ? current.cat : null,
      reason: currentReason,
      task: taskState,
      activity: activity,
      seed: seed,
      hidden: hidden,
      reduced: reduced,
      motion: !motionOff,
      dragging: dragging,
      docHidden: docHidden,
      auto: canAuto(),
      nextIn: autoDueAt ? Math.max(0, autoDueAt - now()) : null,
      timers: timerCount(),
      cooldowns: cd
    };
  }

  /* ═══ 初始化 ═══════════════════════════════════════════════════════ */
  function init() {
    el = document.getElementById('citrusMascot');
    if (!el) return;

    // 会话级随机种子：整个页面生命周期不变，日志可复现
    seed = ((Date.now() >>> 0) ^ ((Math.random() * 0xffffffff) >>> 0)) >>> 0;
    rand = mulberry32(seed);
    LOG.seed = seed;

    reduced = isReducedMotion();
    try { motionOff = localStorage.getItem(MOTION_KEY) === '0'; } catch (_) {}
    try {
      var lv = localStorage.getItem(ACTIVITY_KEY);
      if (lv && ACTIVITY[lv]) activity = lv;
    } catch (_) {}

    bindDrag(el);
    bindScaleControl();
    bindControlsReveal();

    var storedHidden = false;
    try { storedHidden = localStorage.getItem(STORE_KEY) === '1'; } catch (_) {}
    el.classList.toggle('is-still', still());
    applyHidden(storedHidden);

    restorePos();

    window.addEventListener('resize', function () {
      var c = clamp(posX, posY);
      posX = c.x; posY = c.y;
      applyPos();
    }, { passive: true });

    var storedScale = null;
    try { storedScale = localStorage.getItem(SCALE_KEY); } catch (_) {}
    applyScale(storedScale === null ? 100 : storedScale, false);

    var legacyScale = document.getElementById('mascotScale');
    if (legacyScale) {
      legacyScale.addEventListener('input', function () { applyScale(legacyScale.value, true); });
      legacyScale.addEventListener('change', function () { applyScale(legacyScale.value, true); });
    }
    var close = document.getElementById('mascotClose');
    if (close) {
      close.addEventListener('click', function (e) {
        e.stopPropagation();
        hide();
      });
    }

    // 页面可见性：隐藏暂停，回来重新排期（不补播积压）
    document.addEventListener('visibilitychange', function () {
      setPageHidden(document.hidden);
    });
    // 应用内「减少动画」开关写在 <html data-reduce-motion>，用 MutationObserver 跟随
    if (window.MutationObserver && document.documentElement) {
      var mo = new MutationObserver(function () { refreshStill(); });
      mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-reduce-motion'] });
    }
    if (window.matchMedia) {
      var mq = window.matchMedia('(prefers-reduced-motion: reduce)');
      if (mq.addEventListener) mq.addEventListener('change', refreshStill);
      else if (mq.addListener) mq.addListener(refreshStill);
    }

    ready = true;
    playRest();
    if (!hidden && !still()) scheduleAuto();
    logEvent('init', null, 'boot', { seed: seed, activity: activity, reduced: reduced, motion: !motionOff });
  }

  /* ═══ 对外 API ═════════════════════════════════════════════════════
     兼容旧调用：setState / resetPosition / hide / show / toggle /
     getScale / setScale / getVisible / setVisible / refreshPanelState；
     app.js 需要：setScale / resetPosition / setPosition / setMotion / toggle；
     v3 新增：setActivityLevel / getActivityLevel / getAction / getTimerCount / pause / resume。 */
  window.CitrusMascot = {
    setState: setState,
    resetPosition: resetPosition,
    setPosition: setPosition,
    hide: hide,
    show: show,
    toggle: function () { return setVisible(!getVisible()); },
    setScale: function (pct, persist) { return applyScale(pct, persist === undefined ? true : !!persist); },
    getScale: function () { return Math.round(scale * 100); },
    getVisible: getVisible,
    setVisible: setVisible,
    refreshPanelState: refreshPanelState,
    setActivityLevel: setActivityLevel,
    getActivityLevel: getActivityLevel,
    setMotion: setMotion,
    getMotion: getMotion,
    getAction: function () {
      if (current) {
        return {
          id: current.id, cat: current.cat, strip: current.strip,
          reason: currentReason, label: current.label
        };
      }
      var attr = el ? el.getAttribute('data-action') : null;
      if (!attr) return null;
      return { id: attr, cat: 'fallback', strip: strip, reason: currentReason, label: '' };
    },
    getTimerCount: timerCount,
    pause: function () { autoOn = false; cancelTimer(autoTimer); autoTimer = null; autoDueAt = 0; },
    resume: function () { autoOn = true; scheduleAuto(); },
    getState: function () { return state; },
    getSnapshot: snapshot,
    play: function (id) {
      var a = BY_ID[id];
      if (!a) return false;
      return requestAction(a, 'manual', 40, function () { restoreFromTask(); });
    }
  };

  window.__mascotLog = LOG;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
