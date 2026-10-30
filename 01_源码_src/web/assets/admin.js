(function () {
  'use strict';
  var pageSize = 25;
  var offset = 0;
  var toastTimer = null;
  function el(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]);
    });
  }
  function notify(message) {
    var box = el('toast'); if (!box) return;
    box.textContent = message; box.classList.add('visible');
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { box.classList.remove('visible'); }, 3000);
  }
  async function jsonRequest(url, options) {
    var response = await fetch(url, options || {credentials:'same-origin'});
    var data = await response.json().catch(function () { return {}; });
    if (response.status === 401) { location.replace('/?next=%2Fadmin'); throw new Error('登录已失效'); }
    if (response.status === 403) throw new Error('仅管理员可访问');
    if (!response.ok) throw new Error(data.detail || '请求失败');
    return data;
  }
  function renderUsers(users) {
    var body = el('usersTable');
    if (!users.length) { body.innerHTML = '<tr><td colspan="4" class="muted">暂无账号</td></tr>'; return; }
    body.innerHTML = users.map(function (user) {
      var state = user.active ? '<span class="state active">启用</span>' : '<span class="state inactive">停用</span>';
      var actions = '<button class="small-button" data-action="reset" data-id="' + user.user_id + '">重置密码</button>';
      if (user.role !== 'admin') {
        actions += '<button class="small-button" data-action="limit" data-id="' + user.user_id + '" data-limit="' + user.daily_limit + '">设置限额</button>';
        actions += '<button class="small-button danger" data-action="active" data-id="' + user.user_id + '" data-active="' + (!user.active) + '">' + (user.active ? '停用' : '启用') + '</button>';
      }
      return '<tr><td><strong>' + esc(user.username) + '</strong><small>' + esc(user.role) + '</small></td><td>' + state + '</td>' +
        '<td>' + esc(user.requests_today) + ' / ' + esc(user.daily_limit) + '</td><td class="actions">' + actions + '</td></tr>';
    }).join('');
  }
  function renderAudit(items) {
    var host = el('auditList');
    if (!items.length) { host.innerHTML = '<p class="muted">没有匹配的请求记录</p>'; return; }
    host.innerHTML = items.map(function (item) {
      var tools = (item.tools || []).map(function (tool) { return tool.name || tool.tool || ''; }).filter(Boolean).join('、') || '无';
      var attachments = (item.attachments || []).map(function (file) {
        var kb = Math.max(1, Math.round((file.approx_bytes || 0) / 1024));
        return esc(file.name) + '（' + kb + ' KB）';
      }).join('、') || '无';
      var when = item.created_at ? new Date(item.created_at).toLocaleString() : '时间未知';
      return '<details class="audit-item"><summary><span class="audit-user">' + esc(item.username) + '</span><span class="audit-status">' + esc(item.status) + '</span><time>' + esc(when) + '</time><span class="audit-question">' + esc(item.query) + '</span></summary>' +
        '<div class="audit-content"><p><b>问题</b><span>' + esc(item.query) + '</span></p>' +
        '<p><b>回答</b><span>' + esc(item.answer || '尚未完成') + '</span></p>' +
        '<p><b>工具</b><span>' + esc(tools) + '</span></p>' +
        '<p><b>附件</b><span>' + attachments + '</span></p>' +
        '<p><b>会话与耗时</b><span>' + esc(item.session_id) + ' · ' + esc(item.elapsed_ms == null ? '—' : item.elapsed_ms + ' ms') + '</span></p></div></details>';
    }).join('');
  }
  async function loadOverview() {
    var results = await Promise.all([
      jsonRequest('/api/v2/admin/users'),
      jsonRequest('/api/v2/admin/queue'),
      jsonRequest('/api/v2/config'),
      jsonRequest('/api/v2/config/providers')
    ]);
    var users = results[0].users || [], queue = results[1], config = results[2], providers = results[3].providers || [];
    el('userCount').textContent = users.length;
    el('requestCount').textContent = users.reduce(function (sum, user) { return sum + (Number(user.requests_today) || 0); }, 0);
    el('activeCount').textContent = queue.active + ' / ' + queue.max_active;
    el('queuedCount').textContent = queue.queued + ' / ' + queue.max_pending;
    renderUsers(users);
    var activeProvider = providers.find(function (p) { return p.id === config.model.provider_id; });
    el('modelStatus').textContent = activeProvider
      ? '主模型：' + activeProvider.name + ' · ' + config.model.main + ' · ' + (config.model.image_input ? '支持图像输入' : '不支持图像输入')
      : '主模型：' + config.model.main;
    var configured = providers.filter(function (p) { return p.configured; });
    var webName = config.web_search && config.web_search.provider === 'zhipu_web_search' ? '智谱 Web Search' : 'DeepSeek Search/Fetch';
    el('serviceSummary').innerHTML = '<span class="service-chip ' + (configured.length ? 'ok' : 'warn') + '">主模型服务 ' + (configured.length ? '已配置' : '未配置') + '</span>' +
      '<span class="service-chip ' + (config.web_search && config.web_search.configured ? 'ok' : 'warn') + '">' + esc(webName) + (config.web_search && config.web_search.configured ? ' 已配置' : ' 未配置') + '</span>' +
      '<span class="service-chip neutral">处理 ' + queue.active + ' · 排队 ' + queue.queued + '</span>';
  }
  async function loadAudit() {
    var query = (el('auditQuery').value || '').trim();
    var data = await jsonRequest('/api/v2/admin/requests?limit=' + pageSize + '&offset=' + offset + '&q=' + encodeURIComponent(query));
    renderAudit(data.items || []);
    var total = Number(data.total) || 0;
    el('auditPageLabel').textContent = total ? (offset + 1) + '–' + Math.min(offset + pageSize, total) + ' / ' + total : '0 条';
    el('olderButton').disabled = offset + pageSize >= total;
    el('newerButton').disabled = offset <= 0;
  }
  async function refresh() {
    el('refreshButton').disabled = true;
    try { await Promise.all([loadOverview(), loadAudit()]); }
    catch (error) { notify(error.message); }
    finally { el('refreshButton').disabled = false; }
  }
  async function handleUserAction(event) {
    var button = event.target.closest('button[data-action]'); if (!button) return;
    var id = button.dataset.id, action = button.dataset.action;
    try {
      if (action === 'reset') {
        if (!confirm('重置此账号密码？临时密码只显示一次。')) return;
        var reset = await jsonRequest('/api/v2/admin/users/' + id + '/reset-password', {method:'POST'});
        window.prompt('请通过安全渠道交付此临时密码：', reset.temporary_password);
      } else if (action === 'active') {
        await jsonRequest('/api/v2/admin/users/' + id + '/active', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({active:button.dataset.active === 'true'})});
      } else if (action === 'limit') {
        var value = Number(window.prompt('每日请求上限（1–10000）', button.dataset.limit));
        if (!Number.isInteger(value) || value < 1 || value > 10000) return;
        await jsonRequest('/api/v2/admin/users/' + id + '/daily-limit', {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({daily_limit:value})});
      }
      await loadOverview(); notify('已更新');
    } catch (error) { notify(error.message); }
  }
  // ---------------------------------------------------------------- 文献入库审核
  function corpusBadge(kind) {
    var map = {fetch_paper: '问答抓取', upload: '用户上传'};
    return '<span class="corpus-badge">' + esc(map[kind] || kind || '其他') + '</span>';
  }
  function renderCorpusCandidates(data) {
    var host = el('corpusList');
    var counts = data.counts || {};
    el('corpusSummary').textContent = '待审 ' + (counts.pending || 0) + ' 篇 · 已批准 ' + (counts.approved || 0) +
      ' 篇 · 已入库 ' + (counts.ingested || 0) + ' 篇 · 已拒绝 ' + (counts.rejected || 0) +
      ' 篇（按抓取频次排序，频次高者先入库）';
    var items = data.candidates || [];
    if (!items.length) { host.innerHTML = '<p class="muted">当前没有待审候选</p>'; return; }
    host.innerHTML = items.map(function (item) {
      var actions = '';
      if (item.status === 'pending') {
        actions = '<button class="small-button" data-corpus="approve" data-id="' + item.id + '">批准</button>' +
                  '<button class="small-button danger" data-corpus="reject" data-id="' + item.id + '">拒绝</button>';
      } else if (item.status === 'approved') {
        actions = '<button class="small-button" data-corpus="reject" data-id="' + item.id + '">撤下</button>';
      } else if (item.status === 'rejected') {
        actions = '<button class="small-button" data-corpus="reopen" data-id="' + item.id + '">重审</button>';
      }
      var doi = item.doi ? '<span class="corpus-doi">' + esc(item.doi) + '</span>' : '<span class="muted">无 DOI</span>';
      return '<article class="corpus-item"><div class="corpus-main"><strong>' + esc(item.title || '(无标题)') + '</strong>' +
        '<small>' + doi + ' · ' + corpusBadge(item.source_kind) + ' · ' +
        (item.char_count / 1000).toFixed(1) + 'k 字符 · 频次 ' + esc(item.frequency) +
        ' · ' + esc(item.status) + '</small></div><div class="actions">' + actions + '</div></article>';
    }).join('');
  }
  function renderCorpusBatches(data) {
    var host = el('corpusBatches');
    var items = data.batches || [];
    if (!items.length) { host.innerHTML = '<p class="muted">还没有入库批次</p>'; return; }
    host.innerHTML = items.map(function (batch) {
      var action = batch.status === 'applied'
        ? '<button class="small-button danger" data-batch="' + esc(batch.id) + '">整批回滚</button>' : '';
      return '<article class="corpus-item"><div class="corpus-main"><strong>' + esc(batch.id) + '</strong>' +
        '<small>' + esc(batch.status) + ' · generation ' + esc(batch.generation || '—') +
        ' · 回溯 ' + esc(batch.prev_generation || '—') + ' · ' +
        (batch.candidate_ids || []).length + ' 篇</small></div><div class="actions">' + action + '</div></article>';
    }).join('');
  }
  async function loadCorpus() {
    var data = await jsonRequest('/api/v2/admin/corpus/candidates?status=pending&limit=50');
    renderCorpusCandidates(data);
    renderCorpusBatches(await jsonRequest('/api/v2/admin/corpus/batches?limit=10'));
  }
  async function handleCorpusAction(event) {
    var button = event.target.closest('button[data-corpus],button[data-batch]');
    if (!button) return;
    try {
      if (button.dataset.batch) {
        if (!confirm('把语料索引切回该批次之前的版本？候选会回到「已批准」。')) return;
        await jsonRequest('/api/v2/admin/corpus/batches/' + button.dataset.batch + '/rollback', {method:'POST'});
        notify('已回滚到上一代语料');
      } else {
        var action = button.dataset.corpus;
        if (action === 'reject' && !confirm('把这篇移出候选？之后可以重审。')) return;
        await jsonRequest('/api/v2/admin/corpus/candidates/' + button.dataset.id + '/' + action,
          {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({note:''})});
        notify('已更新候选状态');
      }
      await loadCorpus();
    } catch (error) { notify(error.message); }
  }
  async function applyCorpusBatch() {
    var activate = !!(el('corpusActivate') && el('corpusActivate').checked);
    if (!confirm('把当前所有「已批准」的论文攒批入库？' + (activate ? '入库后立即生效。' : '入库后需再次激活才生效。'))) return;
    el('corpusApply').disabled = true;
    try {
      var result = await jsonRequest('/api/v2/admin/corpus/batches', {method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({activate:activate})});
      if (result.ok) notify('入库完成：' + (result.candidate_ids || []).length + ' 篇，generation ' + (result.generation || '—'));
      else notify('入库失败：' + (result.error || result.status || '未知原因'));
      await loadCorpus();
    } catch (error) { notify(error.message); }
    finally { el('corpusApply').disabled = false; }
  }
  async function init() {
    var me = await jsonRequest('/api/v2/auth/me');
    if (!me.user || me.user.role !== 'admin') { document.body.innerHTML = '<main class="denied"><h1>仅管理员可访问</h1><a href="/">返回 CitrusQA</a></main>'; return; }
    el('adminName').textContent = me.user.username;
    el('refreshButton').addEventListener('click', refresh);
    el('usersTable').addEventListener('click', handleUserAction);
    el('auditSearch').addEventListener('submit', function (event) { event.preventDefault(); offset = 0; loadAudit().catch(function (e) { notify(e.message); }); });
    el('olderButton').addEventListener('click', function () { offset += pageSize; loadAudit().catch(function (e) { notify(e.message); }); });
    el('newerButton').addEventListener('click', function () { offset = Math.max(0, offset - pageSize); loadAudit().catch(function (e) { notify(e.message); }); });
    el('logoutButton').addEventListener('click', async function () { await fetch('/api/v2/auth/logout', {method:'POST'}); location.replace('/'); });
    if (el('corpusList')) {
      el('corpusList').addEventListener('click', function (event) { handleCorpusAction(event).catch(function (e) { notify(e.message); }); });
      el('corpusBatches').addEventListener('click', function (event) { handleCorpusAction(event).catch(function (e) { notify(e.message); }); });
      el('corpusRefresh').addEventListener('click', function () { loadCorpus().catch(function (e) { notify(e.message); }); });
      el('corpusApply').addEventListener('click', function () { applyCorpusBatch().catch(function (e) { notify(e.message); }); });
    }
    await refresh();
    if (el('corpusList')) await loadCorpus().catch(function (e) { notify(e.message); });
  }
  document.addEventListener('DOMContentLoaded', function () { init().catch(function (e) { notify(e.message); }); });
}());
