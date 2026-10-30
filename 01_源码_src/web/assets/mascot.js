/* D: 吉祥物状态机 —— 与真实事件对齐，不做假动画
   事件来源（app.js handleEvent）：thinking→planning、tool_call_start→retrieving、
   text 首字→generating、done→completed、error→failed、拖拽→dragging/dropping。 */
(function () {
  var ROWS = { idle: 0, planning: 2, retrieving: 3, generating: 4, completed: 5, failed: 6, dragging: 7, dropping: 8 };
  var DUR = { idle: '2.8s', planning: '1.6s', retrieving: '1.4s', generating: '1.2s', completed: '1.1s', failed: '1.8s', dropping: '.8s' };
  var AUTOBACK = { completed: 2600, failed: 3000, dropping: 900 };

  var el = null;
  var state = 'idle';
  var timer = null;

  function apply(name) {
    if (!el) return;
    state = name;
    el.setAttribute('data-state', name);
    el.style.setProperty('--mascot-row', ((ROWS[name] || 0) * 12.5) + '%');
    if (DUR[name]) el.style.setProperty('--mascot-dur', DUR[name]);
  }

  function setState(name, opts) {
    if (!el || !ROWS.hasOwnProperty(name)) return;
    if (opts && opts.force !== true && name === state) return;
    if (timer) { clearTimeout(timer); timer = null; }
    apply(name);
    if (AUTOBACK[name]) {
      timer = setTimeout(function () { timer = null; apply('idle'); }, AUTOBACK[name]);
    }
  }

  function bindDrag(node) {
    var dragging = false, moved = false, sx = 0, sy = 0, dx = 0, dy = 0;

    node.addEventListener('pointerdown', function (e) {
      if (e.button !== undefined && e.button !== 0) return;
      dragging = true; moved = false; sx = e.clientX; sy = e.clientY; dx = 0; dy = 0;
      try { node.setPointerCapture(e.pointerId); } catch (_) {}
    });

    node.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      dx = e.clientX - sx; dy = e.clientY - sy;
      if (!moved && Math.abs(dx) + Math.abs(dy) > 6) {
        moved = true;
        node.classList.add('is-dragging');
        setState('dragging');
      }
      if (moved) node.style.transform = 'translate(' + dx + 'px,' + dy + 'px) rotate(' + Math.max(-14, Math.min(14, dx / 12)) + 'deg)';
    });

    function finish() {
      if (!dragging) return;
      dragging = false;
      node.classList.remove('is-dragging');
      if (moved) {
        node.style.transform = '';
        setState('dropping', { force: true });
      }
      moved = false;
    }
    node.addEventListener('pointerup', finish);
    node.addEventListener('pointercancel', finish);
    node.addEventListener('lostpointercapture', finish);
  }

  function mount() {
    el = document.getElementById('citrusMascot');
    if (!el) return;
    bindDrag(el);
    apply('idle');
  }

  window.CitrusMascot = {
    setState: setState,
    getState: function () { return state; },
    reset: function () { setState('idle', { force: true }); }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
