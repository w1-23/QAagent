(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const parse = (c) => { const m = c.match(/[\d.]+/g).map(Number); return { r: m[0], g: m[1], b: m[2], a: m.length > 3 ? m[3] : 1 }; };
  const over = (top, bottom) => ({ // top 半透明叠在 bottom 上
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });
  const chain = (el) => { const arr = []; let n = el; while (n) { arr.push(parse(getComputedStyle(n).backgroundColor)); n = n.parentElement; } return arr; };
  const effBg = (el) => {
    // 从最外层向内合成：html 基底先取不透明上位（白或主题底色）
    const arr = chain(el).reverse();
    let cur = { r: 255, g: 255, b: 255, a: 1 };
    for (const c of arr) { if (c.a === 0) continue; cur = over({ ...c, a: c.a }, cur); }
    return cur;
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (fg, bg) => { const a = lum(fg), b = lum(bg); const [hi, lo] = a > b ? [a, b] : [b, a]; return +((hi + 0.05) / (lo + 0.05)).toFixed(2); };
  const hex = (c) => '#' + [c.r, c.g, c.b].map(v => Math.round(v).toString(16).padStart(2, '0')).join('').toUpperCase();

  const out = {};
  for (const t of ['light', 'dark', 'cartoon']) {
    document.documentElement.setAttribute('data-theme', t);
    await sleep(100);
    const tpl = document.getElementById('loadSproutTpl');
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.hidden = false;
    document.body.appendChild(node);
    await sleep(80);
    const txt = node.querySelector('.load-sprout-text');
    const bg = effBg(txt);
    const fg = parse(getComputedStyle(txt).color);
    const r = {
      bg: hex(bg),
      fg: hex(fg),
      ratio: ratio(fg, bg),
      fontSize: getComputedStyle(txt).fontSize,
      fontWeight: getComputedStyle(txt).fontWeight,
    };
    node.remove();
    const mutedEl = document.querySelector('.tool-trace-elapsed, .msg-meta, .session-item');
    if (mutedEl) {
      const mbg = effBg(mutedEl);
      r.muted = { fg: hex(parse(getComputedStyle(mutedEl).color)), bg: hex(mbg), ratio: ratio(parse(getComputedStyle(mutedEl).color), mbg) };
    }
    out[t] = r;
  }
  document.documentElement.setAttribute('data-theme', 'light');
  return 'CONTRAST:' + JSON.stringify(out);
})()
