(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const bgOf = (el) => { let n=el; while(n && n!==document.documentElement){ const c=getComputedStyle(n).backgroundColor; if(c && c!=='rgba(0, 0, 0, 0)' && c!=='transparent') return c; n=n.parentElement; } return getComputedStyle(document.body).backgroundColor; };
  const out = {};
  for (const t of ['light','dark','cartoon']) {
    document.documentElement.setAttribute('data-theme', t);
    await sleep(90);
    const tpl = document.getElementById('loadSproutTpl');
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.hidden = false; document.body.appendChild(node); await sleep(60);
    const txt = node.querySelector('.load-sprout-text');
    out[t] = { bg: bgOf(node), fg: getComputedStyle(txt).color, accentVar: getComputedStyle(document.documentElement).getPropertyValue('--loading-accent').trim() };
    node.remove();
    const muted = document.querySelector('.tool-trace-elapsed, .msg-meta');
    if (muted) out[t].muted = { fg: getComputedStyle(muted).color, bg: bgOf(muted) };
  }
  document.documentElement.setAttribute('data-theme','light');
  return 'BG:' + JSON.stringify(out);
})()
