(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const lum = (c) => { const m = c.match(/[\d.]+/g).map(Number).slice(0,3).map(v => { v/=255; return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055,2.4); }); return 0.2126*m[0]+0.7152*m[1]+0.0722*m[2]; };
  const ratio = (fg,bg) => { const a=lum(fg), b=lum(bg); const [hi,lo]= a>b?[a,b]:[b,a]; return +((hi+0.05)/(lo+0.05)).toFixed(2); };
  const bgOf = (el) => { let n=el; while(n && n!==document.documentElement){ const c=getComputedStyle(n).backgroundColor; if(c && c!=='rgba(0, 0, 0, 0)' && c!=='transparent') return c; n=n.parentElement; } return getComputedStyle(document.body).backgroundColor; };
  const out = {};

  // 焦点：开面板后焦点是否进入面板
  const pet = document.getElementById('petRailBtn');
  pet.focus(); pet.click(); await sleep(150);
  out.focusRightAfterOpen = (document.activeElement.id || document.activeElement.tagName);
  out.petMenuRole = document.getElementById('petMenu').getAttribute('role');
  out.petMenuAriaLabel = document.getElementById('petMenu').getAttribute('aria-label');
  out.petMenuTabReachable = document.getElementById('petMenu').querySelectorAll('button,input,select,[tabindex]').length;
  document.body.click(); await sleep(120);

  // 对比度：三套主题 × 关键元素
  const themes = ['light','dark','cartoon'];
  const res = {};
  for (const t of themes) {
    document.documentElement.setAttribute('data-theme', t);
    await sleep(100);
    const r = {};
    const tpl = document.getElementById('loadSproutTpl');
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.hidden = false; document.body.appendChild(node); await sleep(60);
    const txt = node.querySelector('.load-sprout-text');
    r.sproutText = ratio(getComputedStyle(txt).color, bgOf(node));
    node.remove();
    const railBtn = document.getElementById('petRailBtn');
    r.railIcon = ratio(getComputedStyle(railBtn).color, bgOf(railBtn));
    const muted = document.querySelector('.tool-trace-elapsed, .session-item .session-time, .msg-meta');
    if (muted) r.muted = ratio(getComputedStyle(muted).color, bgOf(muted));
    const body = document.body;
    r.bodyText = ratio(getComputedStyle(body).color, bgOf(body));
    res[t] = r;
  }
  document.documentElement.setAttribute('data-theme','light');
  out.contrast = res;

  // 430px 窄屏：抽屉表格与菜单是否溢出
  out.narrow = (() => {
    const el = document.getElementById('ctxDrawer');
    const wasOpen = el && !el.hidden;
    return { drawerHidden: el ? el.hidden : null, note: '窄屏需单独窗口尺寸，见下一探针' };
  })();
  return 'A2:' + JSON.stringify(out);
})()
