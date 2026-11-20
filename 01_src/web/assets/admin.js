(function () {
  'use strict';
  // BEGIN ADMIN INVITE CONTRACT（纯函数；状态标签与请求构造，不读写 DOM）
  var CitrusAdminContract = (function () {
    var INVITE_STATES = ['issued', 'claimed', 'revoked', 'expired'];
    var INVITE_LABELS = {issued: '已签发', claimed: '已使用', revoked: '已撤销', expired: '已过期'};
    var APPLICATION_STATES = ['pending', 'approved', 'rejected'];
    var APPLICATION_LABELS = {pending: '待审核', approved: '已通过', rejected: '已拒绝'};
    function clampLimit(limit) {
      var value = Number(limit);
      if (!Number.isFinite(value)) return 50;
      value = Math.trunc(value);
      return value < 1 ? 1 : value > 100 ? 100 : value;
    }
    function pageQuery(status, limit, offset) {
      var query = '?limit=' + clampLimit(limit) + '&offset=' + Math.max(0, Math.trunc(Number(offset) || 0));
      if (status && APPLICATION_STATES.indexOf(status) >= 0) query += '&status=' + encodeURIComponent(status);
      return query;
    }
    function invitesQuery(limit, offset) {
      return '?limit=' + clampLimit(limit) + '&offset=' + Math.max(0, Math.trunc(Number(offset) || 0));
    }
    function inviteState(status) {
      var key = INVITE_STATES.indexOf(status) >= 0 ? status : 'issued';
      return {key: key, label: INVITE_LABELS[key], canRevoke: key === 'issued'};
    }
    function applicationState(status) {
      var key = APPLICATION_STATES.indexOf(status) >= 0 ? status : 'pending';
      return {key: key, label: APPLICATION_LABELS[key], canReview: key === 'pending'};
    }
    function expiresInDays(value) {
      var days = Number(value);
      if (value === '' || value == null || !Number.isFinite(days)) return undefined;
      days = Math.trunc(days);
      return days >= 1 && days <= 30 ? days : undefined;
    }
    function buildInviteBody(value) {
      var days = expiresInDays(value);
      return days === undefined ? {} : {expires_in_days: days};
    }
    function decisionBody(decision, reason) {
      if (decision !== 'approve' && decision !== 'reject') return null;
      if (decision === 'approve') return {};
      var text = String(reason == null ? '' : reason).trim();
      if (Array.from(text).length > 300) text = Array.from(text).slice(0, 300).join('');
      return text ? {reason: text} : {};
    }
    function decisionUrl(id, decision) {
      if (decision !== 'approve' && decision !== 'reject') return '';
      return '/api/v2/admin/applications/' + encodeURIComponent(String(id)) + '/' + decision;
    }
    function resetBody(currentPassword) {
      return {current_password: String(currentPassword || '')};
    }
    return {
      INVITE_STATES: INVITE_STATES,
      INVITE_LABELS: INVITE_LABELS,
      APPLICATION_STATES: APPLICATION_STATES,
      APPLICATION_LABELS: APPLICATION_LABELS,
      clampLimit: clampLimit,
      pageQuery: pageQuery,
      invitesQuery: invitesQuery,
      inviteState: inviteState,
      applicationState: applicationState,
      expiresInDays: expiresInDays,
      buildInviteBody: buildInviteBody,
      decisionBody: decisionBody,
      decisionUrl: decisionUrl,
      resetBody: resetBody
    };
  }());
  if (typeof window !== 'undefined') window.CitrusAdminContract = CitrusAdminContract;
  // END ADMIN INVITE CONTRACT
  var pageSize = 25;
  var offset = 0;
  var toastTimer = null;
  var appStatus = 'pending';
  var appOffset = 0;
  var appPageSize = 50;
  var inviteOffset = 0;
  var invitePageSize = 50;
  var oneTimeInviteCode = '';
  var oneTimeAdminSecret = '';
  var pendingResetUserId = '';
  var pendingResetName = '';
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
    if (!response.ok) {
      var error = new Error(data.detail || '请求失败');
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function renderUsers(users) {
    var body = el('usersTable');
    if (!users.length) { body.innerHTML = '<tr><td colspan="4" class="muted">暂无账号</td></tr>'; return; }
    body.innerHTML = users.map(function (user) {
      var state = user.active ? '<span class="state active">启用</span>' : '<span class="state inactive">停用</span>';
      var actions = user.role !== 'admin'
        ? '<button class="small-button" data-action="reset" data-id="' + user.user_id + '" data-name="' + esc(user.username) + '">重置密码</button>'
        : '<span class="muted">本人改密</span>';
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
      jsonRequest('/api/v2/config/providers'),
      jsonRequest('/api/v2/admin/onnx/device')
    ]);
    var users = results[0].users || [], queue = results[1], config = results[2], providers = results[3].providers || [], device = results[4];
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
    renderDevice(device);
  }
  function renderDevice(device) {
    if (!device) return;
    el('onnxDeviceMode').value = device.mode || 'auto';
    var active = device.selected_providers || [];
    el('onnxDeviceStatus').textContent = '模式：' + (device.mode || 'auto') +
      ' · 当前选择：' + active.join(' → ') + (device.gpu_available ? ' · 已检测到 GPU' : ' · 未检测到 GPU');
    var activeModels = device.active_model_providers || {};
    el('onnxProviders').innerHTML = (device.available_providers || []).map(function (provider) {
      return '<span class="service-chip neutral">' + esc(provider) + '</span>';
    }).join('') + Object.keys(activeModels).map(function (name) {
      return '<span class="service-chip ok">' + esc(name) + '：' + esc((activeModels[name] || []).join(' → ')) + '</span>';
    }).join('');
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
    try { await Promise.all([loadOverview(), loadAudit(), loadApplications(), loadInvites()]); }
    catch (error) { notify(error.message); }
    finally { el('refreshButton').disabled = false; }
  }
  async function handleUserAction(event) {
    var button = event.target.closest('button[data-action]'); if (!button) return;
    var id = button.dataset.id, action = button.dataset.action;
    try {
      if (action === 'reset') {
        openResetDialog(id, button.dataset.name);
        return;
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
  async function applyOnnxDevice() {
    var button = el('onnxDeviceApply');
    button.disabled = true;
    try {
      var mode = el('onnxDeviceMode').value;
      var data = await jsonRequest('/api/v2/admin/onnx/device', {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({mode: mode})
      });
      renderDevice(data);
      notify(data.note || '推理设备已切换');
    } catch (error) { notify(error.message); }
    finally { button.disabled = false; }
  }
  // ---------------------------------------------------------------- 文献候审库
  function corpusBadge(kind) {
    var map = {fetch_paper: '问答抓取', upload: '用户上传'};
    return '<span class="corpus-badge">' + esc(map[kind] || kind || '其他') + '</span>';
  }
  function corpusFilter() {
    var select = el('corpusStatus');
    return (select && select.value) || 'pending';
  }
  function renderCorpusCandidates(data) {
    var host = el('corpusList');
    var counts = data.counts || {};
    el('corpusSummary').textContent = '待审 ' + (counts.pending || 0) + ' 篇 · 已保留 ' +
      (counts.approved || 0) + ' 篇 · 已拒绝 ' + (counts.rejected || 0) +
      ' 篇（只保存原始资料：不切块、不向量化、不建索引、不激活语料）';
    var items = data.candidates || [];
    if (!items.length) { host.innerHTML = '<p class="muted">当前没有符合条件的资料</p>'; return; }
    host.innerHTML = items.map(function (item) {
      var raw = '<a class="small-button" href="/api/v2/admin/corpus/candidates/' + item.id +
        '/raw" target="_blank" rel="noopener">查看原文</a>';
      var actions = '';
      if (item.status === 'pending') {
        actions = raw +
          '<button class="small-button" data-corpus="approve" data-id="' + item.id + '">保留</button>' +
          '<button class="small-button danger" data-corpus="reject" data-id="' + item.id + '">拒绝</button>';
      } else if (item.status === 'approved') {
        actions = raw + '<button class="small-button danger" data-corpus="reject" data-id="' + item.id + '">撤下</button>';
      } else {
        actions = raw + '<button class="small-button" data-corpus="reopen" data-id="' + item.id + '">重审</button>';
      }
      var doi = item.doi ? '<span class="corpus-doi">' + esc(item.doi) + '</span>' : '<span class="muted">无 DOI</span>';
      var format = (item.raw_format || '未知格式') + (item.conversion && item.conversion.indexOf('none') === 0
        ? '' : ' · ' + item.conversion);
      var bytes = Number(item.raw_bytes || 0);
      return '<article class="corpus-item"><div class="corpus-main"><strong>' + esc(item.title || '(无标题)') + '</strong>' +
        '<small>' + doi + ' · ' + corpusBadge(item.source_kind) + ' · ' + esc(format) +
        ' · ' + (bytes ? (bytes / 1024).toFixed(1) + ' KB' : '—') +
        ' · 频次 ' + esc(item.frequency) + ' · ' + esc(item.status) + '</small></div>' +
        '<div class="actions">' + actions + '</div></article>';
    }).join('');
  }
  async function loadCorpus() {
    var status = corpusFilter();
    var data = await jsonRequest('/api/v2/admin/corpus/candidates?status=' + encodeURIComponent(status) + '&limit=50');
    renderCorpusCandidates(data);
  }
  async function handleCorpusAction(event) {
    var button = event.target.closest('button[data-corpus]');
    if (!button) return;
    try {
      var action = button.dataset.corpus;
      if (action === 'reject' && !confirm('不再保留这篇资料？之后仍可重审。')) return;
      await jsonRequest('/api/v2/admin/corpus/candidates/' + button.dataset.id + '/' + action,
        {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({note:''})});
      notify('已更新保留状态');
      await loadCorpus();
    } catch (error) { notify(error.message); }
  }
  // ---------------------------------------------------------------- 注册申请
  function formatWhen(value) {
    return value ? new Date(value).toLocaleString() : '—';
  }
  function renderApplications(data) {
    var body = el('applicationsTable');
    var items = data.applications || [];
    var next = data.next_offset;
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="5" class="muted">当前筛选没有注册申请。</td></tr>';
    } else {
      body.innerHTML = items.map(function (application) {
        var meta = CitrusAdminContract.applicationState(application.status);
        var reviewed = application.reviewed_at ? formatWhen(application.reviewed_at) : '—';
        var reason = application.rejection_reason
          ? '<small>拒绝理由：' + esc(application.rejection_reason) + '</small>' : '';
        var actions = meta.canReview
          ? '<button class="small-button" data-app="approve" data-id="' + esc(application.application_id) + '">通过</button>' +
            '<button class="small-button danger" data-app="reject" data-id="' + esc(application.application_id) + '">拒绝</button>'
          : '<span class="muted">已处理</span>';
        return '<tr><td><strong>' + esc(application.username) + '</strong><small>头像 ' + esc(application.avatar_preset || '01') + '</small></td>' +
          '<td><span class="state app-' + meta.key + '">' + esc(meta.label) + '</span></td>' +
          '<td>' + esc(formatWhen(application.submitted_at)) + '</td>' +
          '<td>' + esc(reviewed) + reason + '</td>' +
          '<td class="actions">' + actions + '</td></tr>';
      }).join('');
    }
    var label = '第 ' + (Math.floor(appOffset / appPageSize) + 1) + ' 页 · 本页 ' + items.length + ' 条';
    el('applicationsPageLabel').textContent = label;
    el('applicationsNewer').disabled = appOffset <= 0;
    el('applicationsOlder').disabled = next == null;
    el('applicationsSummary').textContent = label + ' · 状态：' + CitrusAdminContract.applicationState(appStatus).label;
  }
  async function loadApplications() {
    var data = await jsonRequest('/api/v2/admin/applications' + CitrusAdminContract.pageQuery(appStatus, appPageSize, appOffset));
    renderApplications(data);
  }
  async function handleApplicationAction(event) {
    var button = event.target.closest('button[data-app]');
    if (!button) return;
    var id = button.dataset.id;
    var decision = button.dataset.app;
    var reason = '';
    if (decision === 'approve') {
      if (!confirm('通过该注册申请？通过后该用户即可登录。')) return;
    } else {
      var answer = window.prompt('拒绝理由（可留空，最多 300 字，只有管理员可见）：', '');
      if (answer === null) return;
      reason = answer;
      if (Array.from(reason).length > 300) { notify('拒绝理由不能超过 300 字。'); return; }
    }
    button.disabled = true;
    try {
      await jsonRequest(CitrusAdminContract.decisionUrl(id, decision), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(CitrusAdminContract.decisionBody(decision, reason))
      });
      notify(decision === 'approve' ? '已通过该申请' : '已拒绝该申请');
      await loadApplications();
    } catch (error) {
      if (error.status === 409) notify('该申请已被其他管理员处理，请刷新列表。');
      else if (error.status === 404) notify('申请不存在，请刷新列表。');
      else notify(error.message);
    } finally { button.disabled = false; }
  }

  // ---------------------------------------------------------------- 邀请码
  function renderInvites(data) {
    var body = el('invitesTable');
    var items = data.invites || [];
    var next = data.next_offset;
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="5" class="muted">还没有邀请码。</td></tr>';
    } else {
      body.innerHTML = items.map(function (invite) {
        var meta = CitrusAdminContract.inviteState(invite.status);
        var actions = meta.canRevoke
          ? '<button class="small-button danger" data-invite="revoke" data-id="' + esc(invite.invite_id) + '">撤销</button>'
          : '<span class="muted">—</span>';
        var note = invite.claimed_at ? '<small>使用时间：' + esc(formatWhen(invite.claimed_at)) + '</small>'
          : invite.revoked_at ? '<small>撤销时间：' + esc(formatWhen(invite.revoked_at)) + '</small>' : '';
        return '<tr><td><code>' + esc(invite.invite_id) + '</code>' + note + '</td>' +
          '<td><span class="state invite-' + meta.key + '">' + esc(meta.label) + '</span></td>' +
          '<td>' + esc(formatWhen(invite.created_at)) + '</td>' +
          '<td>' + esc(formatWhen(invite.expires_at)) + '</td>' +
          '<td class="actions">' + actions + '</td></tr>';
      }).join('');
    }
    var label = '第 ' + (Math.floor(inviteOffset / invitePageSize) + 1) + ' 页 · 本页 ' + items.length + ' 条';
    el('invitesPageLabel').textContent = label;
    el('invitesNewer').disabled = inviteOffset <= 0;
    el('invitesOlder').disabled = next == null;
    el('invitesSummary').textContent = label + ' · 列表不显示明文邀请码。';
  }
  async function loadInvites() {
    var data = await jsonRequest('/api/v2/admin/invites' + CitrusAdminContract.invitesQuery(invitePageSize, inviteOffset));
    renderInvites(data);
  }
  function showOneTimeInvite(code) {
    oneTimeInviteCode = String(code || '');
    el('inviteCodeValue').textContent = oneTimeInviteCode;
    el('inviteOneTime').hidden = !oneTimeInviteCode;
    if (oneTimeInviteCode) el('inviteCopy').focus();
  }
  function discardOneTimeInvite() {
    oneTimeInviteCode = '';
    var value = el('inviteCodeValue'); if (value) value.textContent = '';
    var panel = el('inviteOneTime'); if (panel) panel.hidden = true;
  }
  async function createInvite() {
    var expiry = el('inviteExpiresDays').value;
    if (expiry !== '' && CitrusAdminContract.expiresInDays(expiry) === undefined) {
      notify('有效天数需为 1–30 的整数。');
      return;
    }
    var button = el('inviteCreate');
    button.disabled = true;
    try {
      var data = await jsonRequest('/api/v2/admin/invites', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(CitrusAdminContract.buildInviteBody(el('inviteExpiresDays').value)),
        cache: 'no-store'
      });
      var invite = data.invite || {};
      if (!invite.code) {
        notify('邀请码已创建，但响应未返回明文邀请码；请撤销后重新生成。');
        await loadInvites();
        return;
      }
      showOneTimeInvite(invite.code);
      notify('邀请码已生成，只显示一次，请立即复制。');
      await loadInvites();
    } catch (error) { notify(error.message); }
    finally { button.disabled = false; }
  }
  async function handleInviteAction(event) {
    var button = event.target.closest('button[data-invite]');
    if (!button) return;
    if (!confirm('撤销这个邀请码？撤销后无法再用于注册。')) return;
    button.disabled = true;
    try {
      await jsonRequest('/api/v2/admin/invites/' + encodeURIComponent(button.dataset.id) + '/revoke', {method: 'POST'});
      notify('邀请码已撤销');
      await loadInvites();
    } catch (error) {
      if (error.status === 409) notify('该邀请码已被使用或已过期，无法撤销。');
      else if (error.status === 404) notify('邀请码不存在，请刷新列表。');
      else notify(error.message);
    } finally { button.disabled = false; }
  }
  async function copySecret(value, successMessage) {
    if (!value) return;
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(value);
        notify(successMessage || '已复制');
        return;
      }
    } catch (_) {}
    notify('无法自动复制，请手动选中后复制。');
  }

  // ---------------------------------------------------------------- 管理员重置密码
  function openResetDialog(id, name) {
    pendingResetUserId = String(id || '');
    pendingResetName = String(name || '');
    el('adminResetHint').textContent = '为“' + pendingResetName + '”生成一次性临时密码。需要先输入你当前的管理员密码确认。';
    el('adminResetPassword').value = '';
    el('adminResetError').textContent = '';
    el('adminResetDialog').hidden = false;
    el('adminResetPassword').focus();
  }
  function closeResetDialog() {
    var dialog = el('adminResetDialog'); if (dialog) dialog.hidden = true;
    var password = el('adminResetPassword'); if (password) password.value = '';
    var error = el('adminResetError'); if (error) error.textContent = '';
    pendingResetUserId = '';
    pendingResetName = '';
  }
  function showOneTimeAdminSecret(username, secret) {
    oneTimeAdminSecret = String(secret || '');
    el('adminSecretTitle').textContent = '“' + String(username || '') + '”的一次性临时密码（关闭后无法再次查看）';
    el('adminSecretValue').textContent = oneTimeAdminSecret;
    el('adminSecretPanel').hidden = !oneTimeAdminSecret;
    if (oneTimeAdminSecret) el('adminSecretCopy').focus();
  }
  function discardAdminSecret() {
    oneTimeAdminSecret = '';
    var value = el('adminSecretValue'); if (value) value.textContent = '';
    var panel = el('adminSecretPanel'); if (panel) panel.hidden = true;
  }
  async function submitReset(event) {
    event.preventDefault();
    if (!pendingResetUserId) return;
    var error = el('adminResetError');
    var button = el('adminResetSubmit');
    var password = el('adminResetPassword').value || '';
    error.textContent = '';
    if (!password) { error.textContent = '请输入你当前的管理员密码。'; return; }
    var targetName = pendingResetName;
    button.disabled = true;
    try {
      var response = await fetch('/api/v2/admin/users/' + encodeURIComponent(pendingResetUserId) + '/reset-password', {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(CitrusAdminContract.resetBody(password))
      });
      var data = await response.json().catch(function () { return {}; });
      if (response.status === 401) { location.replace('/?next=%2Fadmin'); return; }
      if (response.status === 403) { error.textContent = '管理员密码不正确，或该账号不允许由他人重置。'; return; }
      if (response.status === 429) { error.textContent = '操作过于频繁，请稍后再试。'; return; }
      if (response.status === 404) { error.textContent = '账号不存在，请刷新列表。'; return; }
      if (!response.ok) { error.textContent = data.detail || '重置失败，请稍后重试。'; return; }
      var temporary = data.temporary_password || '';
      closeResetDialog();
      if (!temporary) {
        notify('已重置，但未收到临时密码；请让用户使用恢复码，或稍后重试。');
        await loadOverview();
        return;
      }
      showOneTimeAdminSecret(targetName, temporary);
      notify('临时密码已生成，只显示一次，请立即复制。');
      await loadOverview();
    } catch (e) { error.textContent = '网络异常，请稍后重试。'; }
    finally { button.disabled = false; }
  }

  async function init() {
    var me = await jsonRequest('/api/v2/auth/me');
    if (!me.user || me.user.role !== 'admin') { document.body.innerHTML = '<main class="denied"><h1>仅管理员可访问</h1><a href="/">返回 CitrusScholar</a></main>'; return; }
    el('adminName').textContent = me.user.username;
    el('refreshButton').addEventListener('click', refresh);
    el('usersTable').addEventListener('click', handleUserAction);
    el('applicationsTable').addEventListener('click', function (event) {
      handleApplicationAction(event).catch(function (e) { notify(e.message); });
    });
    el('applicationsRefresh').addEventListener('click', function () { loadApplications().catch(function (e) { notify(e.message); }); });
    el('applicationStatus').addEventListener('change', function () {
      appStatus = this.value; appOffset = 0;
      loadApplications().catch(function (e) { notify(e.message); });
    });
    el('applicationsOlder').addEventListener('click', function () {
      appOffset += appPageSize; loadApplications().catch(function (e) { notify(e.message); });
    });
    el('applicationsNewer').addEventListener('click', function () {
      appOffset = Math.max(0, appOffset - appPageSize); loadApplications().catch(function (e) { notify(e.message); });
    });
    el('invitesTable').addEventListener('click', function (event) {
      handleInviteAction(event).catch(function (e) { notify(e.message); });
    });
    el('invitesRefresh').addEventListener('click', function () { loadInvites().catch(function (e) { notify(e.message); }); });
    el('inviteCreate').addEventListener('click', function () { createInvite().catch(function (e) { notify(e.message); }); });
    el('inviteCopy').addEventListener('click', function () { copySecret(oneTimeInviteCode, '邀请码已复制'); });
    el('inviteDiscard').addEventListener('click', discardOneTimeInvite);
    el('invitesNewer').addEventListener('click', function () {
      inviteOffset = Math.max(0, inviteOffset - invitePageSize); loadInvites().catch(function (e) { notify(e.message); });
    });
    el('invitesOlder').addEventListener('click', function () {
      inviteOffset += invitePageSize; loadInvites().catch(function (e) { notify(e.message); });
    });
    el('adminSecretCopy').addEventListener('click', function () { copySecret(oneTimeAdminSecret, '临时密码已复制'); });
    el('adminSecretDiscard').addEventListener('click', discardAdminSecret);
    el('adminResetForm').addEventListener('submit', function (event) {
      submitReset(event).catch(function (e) { el('adminResetError').textContent = e.message || '重置失败。'; });
    });
    el('adminResetCancel').addEventListener('click', closeResetDialog);
    el('adminResetDialog').addEventListener('click', function (event) { if (event.target === this) closeResetDialog(); });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !el('adminResetDialog').hidden) closeResetDialog();
    });
    window.addEventListener('pagehide', function () { discardOneTimeInvite(); discardAdminSecret(); });
    el('auditSearch').addEventListener('submit', function (event) { event.preventDefault(); offset = 0; loadAudit().catch(function (e) { notify(e.message); }); });
    el('onnxDeviceApply').addEventListener('click', applyOnnxDevice);
    el('olderButton').addEventListener('click', function () { offset += pageSize; loadAudit().catch(function (e) { notify(e.message); }); });
    el('newerButton').addEventListener('click', function () { offset = Math.max(0, offset - pageSize); loadAudit().catch(function (e) { notify(e.message); }); });
    el('logoutButton').addEventListener('click', async function () { await fetch('/api/v2/auth/logout', {method:'POST'}); location.replace('/'); });
    if (el('corpusList')) {
      el('corpusList').addEventListener('click', function (event) { handleCorpusAction(event).catch(function (e) { notify(e.message); }); });
      el('corpusRefresh').addEventListener('click', function () { loadCorpus().catch(function (e) { notify(e.message); }); });
      if (el('corpusStatus')) el('corpusStatus').addEventListener('change', function () { loadCorpus().catch(function (e) { notify(e.message); }); });
    }
    await refresh();
    if (el('corpusList')) await loadCorpus().catch(function (e) { notify(e.message); });
  }
  document.addEventListener('DOMContentLoaded', function () { init().catch(function (e) { notify(e.message); }); });
}());
