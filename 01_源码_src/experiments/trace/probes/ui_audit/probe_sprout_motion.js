(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const tpl = document.getElementById('loadSproutTpl');
  const node = tpl.content.firstElementChild.cloneNode(true);
  node.hidden = false;
  node.style.position = 'fixed';
  node.style.left = '80px'; node.style.top = '80px';
  node.style.background = '#fff'; node.style.padding = '8px 12px';
  node.style.zIndex = '99999';
  document.body.appendChild(node);
  await sleep(400);
  const L = node.querySelector('.leaf-left'), R = node.querySelector('.leaf-right');
  const dots = [...node.querySelectorAll('.load-sprout-dots i')];
  const icon = node.querySelector('.load-sprout-icon');
  const snap = () => ({
    L: getComputedStyle(L).transform,
    R: getComputedStyle(R).transform,
    dots: dots.map(d => Number(getComputedStyle(d).opacity).toFixed(2)).join('/'),
    icon: getComputedStyle(icon).transform,
  });
  const a = snap();
  await sleep(650);
  const b = snap();
  await sleep(650);
  const c = snap();
  return 'SPROUT_PROBE:' + JSON.stringify({
    iconInline: icon.tagName,
    iconAnims: icon.getAnimations().map(x => x.animationName + ':' + x.playState),
    leafAnims: [L, R].map(g => g.getAnimations().map(x => x.animationName + ':' + x.playState).join('|')),
    t0: a, t1: b, t2: c,
    leafLeftMoves: a.L !== b.L || b.L !== c.L,
    leafRightMoves: a.R !== b.R || b.R !== c.R,
    dotsCycle: a.dots !== b.dots && b.dots !== c.dots,
  });
})()
