(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const out = {};
  out.reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  out.appReduceAttr = document.documentElement.getAttribute('data-reduce-motion');
  out.lsReduce = (() => { try { return localStorage.getItem('citrus_reduce_motion'); } catch (e) { return 'ERR'; } })();

  // 把模板节点真的挂到页面上，量它自己的动画状态
  const tpl = document.getElementById('loadSproutTpl');
  const node = tpl.content.firstElementChild.cloneNode(true);
  node.hidden = false;
  document.body.appendChild(node);
  await sleep(120);
  const txt = node.querySelector('.load-sprout-text');
  const dots = [...node.querySelectorAll('.load-sprout-dots i')];
  const icon = node.querySelector('.load-sprout-icon');
  out.textAnims = txt ? txt.getAnimations().map(a => a.animationName + ':' + a.playState) : [];
  out.dotAnims = dots.map(d => d.getAnimations().map(a => a.animationName + ':' + a.playState).join('|'));
  out.dotOpacity = dots.map(d => getComputedStyle(d).opacity);
  out.iconAnimCount = icon ? icon.getAnimations().length : -1;
  out.iconSrc = icon ? icon.getAttribute('src') : '';
  node.remove();

  // 实验一：<img> 引入的 SVG 内部 CSS 动画是否真的在跑（画到 canvas 比像素）
  const svgImg = 'data:image/svg+xml;utf8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><style>@keyframes k{0%{fill:#000}100%{fill:#fff}} rect{animation:k 0.4s linear infinite alternate}</style><rect width="40" height="40" fill="#000"/></svg>');
  const im = new Image();
  im.src = svgImg;
  await im.decode().catch(() => {});
  await sleep(300);
  const c = document.createElement('canvas'); c.width = 40; c.height = 40;
  const g = c.getContext('2d');
  g.drawImage(im, 0, 0);
  const px1 = g.getImageData(20, 20, 1, 1).data[0];
  await sleep(400);
  g.clearRect(0, 0, 40, 40); g.drawImage(im, 0, 0);
  const px2 = g.getImageData(20, 20, 1, 1).data[0];
  out.imgSvgAnimation = { px1: px1, px2: px2, animating: px1 !== px2 };

  // 实验二：同一段动画放在内联 <svg> 里（不经过 img）
  const host = document.createElement('div');
  host.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><style>@keyframes k2{0%{fill:#000}100%{fill:#fff}} .r2{animation:k2 0.4s linear infinite alternate}</style><rect class="r2" width="40" height="40" fill="#000"/></svg>';
  document.body.appendChild(host);
  const r2 = host.querySelector('rect');
  await sleep(200);
  out.inlineSvgAnims = r2.getAnimations().map(a => a.animationName + ':' + a.playState);
  host.remove();
  return 'PROBE_JSON:' + JSON.stringify(out);
})()
