(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const vw = innerWidth, vh = innerHeight;
  const out = { viewport: [vw, vh] };
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), bottom: Math.round(r.bottom) }; };

  // 侧栏/快捷区在窄屏是否隐藏
  const quick = document.querySelector('.rail-quick');
  const sb = document.getElementById('sidebar') || document.querySelector('.sidebar');
  out.sidebar = box(sb);
  out.railQuick = box(quick);
  out.railQuickVisible = quick ? getComputedStyle(quick).display !== 'none' && quick.getBoundingClientRect().width > 0 : null;

  // 宠物/主题菜单是否出界
  const petBtn = document.getElementById('petRailBtn');
  if (petBtn && petBtn.getBoundingClientRect().width > 0) {
    petBtn.click(); await sleep(150);
  } else {
    // 侧栏被折叠时用 JS 打开，验证菜单本身
    if (typeof togglePetMenu === 'function') togglePetMenu();
    await sleep(150);
  }
  const petMenu = document.getElementById('petMenu');
  out.petMenu = box(petMenu);
  out.petMenuOpen = petMenu ? !petMenu.hidden : null;
  out.petMenuFitsX = petMenu ? (out.petMenu.x >= -1 && out.petMenu.x + out.petMenu.w <= vw + 1) : null;
  out.petMenuFitsY = petMenu ? (out.petMenu.y >= -1 && out.petMenu.bottom <= vh + 1) : null;
  document.body.click(); await sleep(120);

  // 上下文抽屉里的实际发送表是否横向溢出
  try { if (typeof toggleCtxDrawer === 'function') toggleCtxDrawer(true); } catch (e) {}
  await sleep(400);
  const drawer = document.getElementById('ctxDrawer');
  out.drawer = box(drawer);
  const table = document.querySelector('.ctx-sent-table, .ctx-sent-row');
  if (table) {
    let scroller = table.parentElement;
    out.table = box(table);
    out.tableOverflow = { scrollW: scroller.scrollWidth, clientW: scroller.clientWidth, overflowX: Math.max(0, scroller.scrollWidth - scroller.clientWidth) };
    out.tableStyle = getComputedStyle(table).overflowX;
  }
  const drawerBody = drawer ? drawer.querySelector('.ctx-drawer-body, .ctx-body') : null;
  out.drawerBodyOverflow = drawerBody ? Math.max(0, drawerBody.scrollWidth - drawerBody.clientWidth) : null;
  out.hasHorizontalPageScroll = Math.max(0, document.documentElement.scrollWidth - innerWidth);
  return 'NARROW:' + JSON.stringify(out);
})()
