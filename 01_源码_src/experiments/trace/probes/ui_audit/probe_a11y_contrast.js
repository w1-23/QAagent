(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const out = { viewport: [innerWidth, innerHeight], dpr: devicePixelRatio };

  // 1) 资源完整性：页面引用的 js/css 是否都能取到 200
  const urls = [...document.querySelectorAll('script[src], link[rel=stylesheet]')]
    .map(e => e.src || e.href).filter(u => u.startsWith(location.origin));
  const bad = [];
  for (const u of urls) {
    try { const r = await fetch(u, { cache: 'reload' }); if (!r.ok) bad.push(u.replace(location.origin, '') + ':' + r.status); }
    catch (e) { bad.push(u.replace(location.origin, '') + ':ERR'); }
  }
  out.resourceCount = urls.length;
  out.resourceBad = bad;

  // 2) 左栏四入口的可访问属性
  out.rail = ['guideRailBtn', 'memoryRailBtn', 'petRailBtn', 'themeRailBtn'].map(id => {
    const b = document.getElementById(id);
    if (!b) return { id, missing: true };
    const cs = getComputedStyle(b);
    return {
      id, tag: b.tagName, text: (b.textContent || '').trim().slice(0, 12),
      ariaLabel: b.getAttribute('aria-label'), title: b.getAttribute('title'),
      expanded: b.getAttribute('aria-expanded'),
      focusable: b.tabIndex >= 0 && cs.display !== 'none' && cs.visibility !== 'hidden',
      w: Math.round(b.getBoundingClientRect().width), h: Math.round(b.getBoundingClientRect().height),
    };
  });

  // 3) 键盘：Tab 是否可达（模拟 focus）与 Esc 是否关面板
  const pet = document.getElementById('petRailBtn');
  pet.focus();
  out.railFocusable = document.activeElement === pet;
  pet.click();
  await sleep(120);
  const petMenu = document.getElementById('petMenu');
  out.petMenuOpen = petMenu ? !petMenu.hidden : null;
  out.petBtnExpandedAttr = pet.getAttribute('aria-expanded');
  out.focusAfterOpen = (document.activeElement && (document.activeElement.id || document.activeElement.tagName)) || 'body';
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await sleep(120);
  out.petMenuOpenAfterEsc = petMenu ? !petMenu.hidden : null;
  // 收尾：点一下 body 让它按现有的点击外关逻辑关闭
  document.body.click();
  await sleep(100);

  // 4) 对比度
  const lum = (c) => {
    const m = c.match(/[\d.]+/g).map(Number).slice(0, 3).map(v => {
      v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2];
  };
  const ratio = (fg, bg) => { const a = lum(fg), b = lum(bg); const [hi, lo] = a > b ? [a, b] : [b, a]; return +((hi + 0.05) / (lo + 0.05)).toFixed(2); };
  const bgOf = (el) => {
    let n = el;
    while (n && n !== document.documentElement) {
      const c = getComputedStyle(n).backgroundColor;
      if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c;
      n = n.parentElement;
    }
    return getComputedStyle(document.body).backgroundColor;
  };

  const themeBtn = document.getElementById('themeRailBtn');
  const res = {};
  for (const theme of ['light', 'dark']) {
    document.documentElement.setAttribute('data-theme', theme);
    await sleep(80);
    const tpl = document.getElementById('loadSproutTpl');
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.hidden = false;
    document.body.appendChild(node);
    await sleep(60);
    const txt = node.querySelector('.load-sprout-text');
    res[theme] = {
      sproutText: ratio(getComputedStyle(txt).color, bgOf(node)),
    };
    node.remove();
    const railLabel = document.querySelector('.rail-quick button span, .rail-quick button');
    if (railLabel) res[theme].railLabel = ratio(getComputedStyle(railLabel).color, bgOf(railLabel));
    const muted = document.querySelector('.tool-trace-summary, .msg-meta, .session-item');
    if (muted) res[theme].muted = ratio(getComputedStyle(muted).color, bgOf(muted));
  }
  document.documentElement.setAttribute('data-theme', 'light');
  out.contrast = res;

  return 'AUDIT_PROBE:' + JSON.stringify(out);
})()
