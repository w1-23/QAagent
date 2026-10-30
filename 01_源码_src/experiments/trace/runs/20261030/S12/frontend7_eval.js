
localStorage.setItem('citrus_session_id_1', "rail-many-1ccbf7ba");
localStorage.removeItem('citrus_mascot_pos');
'ok'
__RELOAD__
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const out = {};
  const el = document.getElementById('citrusMascot');
  const frame = el.querySelector('.citrus-mascot-frame');

  // 1) 待机采样：3.2 秒内每 80ms 取一次帧号与元素 transform
  const cols = new Set(), transforms = new Set(), bgPos = new Set();
  for (let i = 0; i < 40; i++) {
    cols.add(getComputedStyle(el).getPropertyValue('--mascot-col').trim());
    transforms.add(el.style.transform || 'none');
    bgPos.add(getComputedStyle(frame).backgroundPosition);
    await sleep(80);
  }
  out.idleFrameVariants = cols.size;
  out.idleTransformVariants = transforms.size;
  out.idleTransform = Array.from(transforms)[0];
  out.idleBgPositions = Array.from(bgPos).slice(0, 4);
  out.frameTransition = getComputedStyle(frame).transitionDuration;

  // 2) 少量轮次均匀分布
  const ticks = Array.from(document.querySelectorAll('.turn-tick'));
  out.tickCount = ticks.length;
  const tops = ticks.map(t => Math.round(t.getBoundingClientRect().top));
  const gaps = [];
  for (let i = 1; i < tops.length; i++) gaps.push(tops[i] - tops[i-1]);
  out.gaps = gaps;
  out.gapSpread = gaps.length ? Math.max(...gaps) - Math.min(...gaps) : 0;

  // 3) 拖到左/中/右
  const pd = (t, x, y) => el.dispatchEvent(new PointerEvent(t, {bubbles:true, cancelable:true, clientX:x, clientY:y, pointerId:9, button:0, buttons:1}));
  async function dragTo(dx, dy) {
    const r = el.getBoundingClientRect();
    pd('pointerdown', r.left + 15, r.top + 15);
    pd('pointermove', r.left + 15 + dx, r.top + 15 + dy);
    pd('pointerup', r.left + 15 + dx, r.top + 15 + dy);
    await sleep(140);
    return { transform: el.style.transform, stored: localStorage.getItem('citrus_mascot_pos'),
             left: Math.round(el.getBoundingClientRect().left) };
  }
  out.dragLeft = await dragTo(-40, -120);
  out.dragMiddle = await dragTo(300, -60);
  out.dragRight = await dragTo(900, 0);

  // 4) 取消任务后回 idle
  window.CitrusMascot.setState('generating', {force:true});
  await sleep(120);
  out.stateBeforeCancel = window.CitrusMascot.getState();
  if (typeof stopCurrentTask === 'function') stopCurrentTask();
  await sleep(300);
  out.stateAfterCancel = window.CitrusMascot.getState();
  return out;
})()
