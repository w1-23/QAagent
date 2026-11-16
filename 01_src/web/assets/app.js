var API_URL = '/api/v2/chat';
var currentAccount = null;
// An account lifetime owns requests, file readers and all asynchronous UI work.
var authEpoch = 0;
var authAbortController = new AbortController();
var accountFileReaders = new Set();
var authInFlight = false;
var accountDocumentRetired = false;
function staleAccountError() { return new DOMException('Account changed', 'AbortError'); }
function accountFetch(url, options) {
  var epoch = authEpoch;
  var opts = Object.assign({}, options || {});
  var lifetimeSignal = authAbortController.signal;
  if (opts.signal && opts.signal !== lifetimeSignal) {
    var combined = new AbortController();
    var abort = function() { combined.abort(); };
    if (opts.signal.aborted || lifetimeSignal.aborted) abort();
    else {
      opts.signal.addEventListener('abort', abort, {once:true});
      lifetimeSignal.addEventListener('abort', abort, {once:true});
    }
    opts.signal = combined.signal;
  } else opts.signal = lifetimeSignal;
  return fetch(url, opts).then(function(response) {
    if (epoch !== authEpoch) throw staleAccountError();
    return response;
  });
}
var skillPicker = window.CitrusSkillPicker ? window.CitrusSkillPicker.create({
  menu: document.getElementById('input-tools-menu'), list: document.getElementById('skill-picker-list'),
  status: document.getElementById('skill-picker-status'), summary: document.getElementById('selected-skills'),
  retry: document.getElementById('skill-picker-refresh'), input: document.getElementById('user-input'), fetch: accountFetch,
  scope: function () { return currentAccount && sessionId && !accountDocumentRetired ? authEpoch + ':' + sessionId : ''; }
}) : null;
window.addEventListener('citrus:session-changed', function () { if (skillPicker) skillPicker.reset(); });
function advanceAuthEpoch() {
  if (skillPicker) skillPicker.reset();
  authEpoch += 1;
  authAbortController.abort();
  authAbortController = new AbortController();
  accountFileReaders.forEach(function(reader) { reader.abort(); });
  accountFileReaders.clear();
}
function resetAccountState() {
  advanceAuthEpoch();
  if (activeTurnCancel) activeTurnCancel('account');
  if (currentAbortController) currentAbortController.abort();
  currentAbortController = null; activeTurnCancel = null;
  window._activeTurnStatusUpdater = null;
  currentJobId = ''; window._currentJobId = '';
  if (currentAccount) localStorage.removeItem(sessionStorageKey());
  for (var i = localStorage.length - 1; i >= 0; i--) {
    var key = localStorage.key(i);
    if (key && (key.indexOf('citrus_ctx_label:') === 0 || key === 'citrus_session_groups_open')) localStorage.removeItem(key);
  }
  localStorage.removeItem('citrus_session_id');
  sessionStorage.removeItem('citrus_session_id');
  currentAccount = null; sessionId = null;
  _selectedAttachments.length = 0;
  window._selectedAccountFileRefs.length = 0;
  _attachmentPreviewUrls.forEach(function(url) { URL.revokeObjectURL(url); });
  _attachmentPreviewUrls = [];
  clearSentAttachmentPreviewUrls();
  _historyMsgs = []; _historyTurns = null; _historyHasMore = false;
  roundHistory = []; _restoredGroups = null; _restoredRefPool = null;
  _sessions = []; _sessionsVisible = 20; _citationsRetry = 0;
  _ctxData = null; _sentSnapshots = [];
  currentRoundId = 0; currentQuery = ''; totalTokens = 0;
  _agentTokenBuffer = ''; _agentTokenName = '';
  window._fullTextFromDone = {}; window._remapAppliedRounds = {};
  _pendingPermission = null; _runtimeConfig = null; _providerModelLists = {};
  _providerCatalog = JSON.parse(JSON.stringify(_defaultProviderCatalog));
  signupAvatarDataUrl = ''; settingsAvatarDataUrl = '';
  signupAvatarPreset = '01'; settingsAvatarPreset = '01';
  onboardingSaveInFlight = false; reviewConsentEnabled = false;
  if (window.CitrusGuide) window.CitrusGuide.close(false);
  window.lastCopiedSvgSource = '';
  if (chatContainer) chatContainer.replaceChildren();
  if (userInput) { userInput.value = ''; userInput.placeholder = '有什么想做的？'; }
  ['attachment-list', 'session-list', 'sidebar-content', 'workspace-list',
    'ctxDetail', 'ctxSent', 'ctxItems', 'ctxSummary', 'ctxSuggestions',
    'ctxInTokens', 'ctxInNote', 'ctxOutTokens', 'ctxOutNote', 'ctxBarLabel', 'ctx-pct', 'ctxCompressed',
    'accountMemoryContent', 'accountMemoryStatus', 'accountPreferenceStatus',
    'settingsAvatarError', 'settingsAvatarLabel', 'authAvatarError', 'authAvatarLabel',
    'accountMenuName', 'accountMenuRole', 'sessionFilesUploaded', 'sessionFilesGenerated',
    'sessionFilePreviewStage', 'sessionFilePreviewTitle', 'sessionFilePreviewMeta',
    'configuredProvidersList'].forEach(function(id) {
    var node = document.getElementById(id); if (node) node.replaceChildren();
  });
  ['attachment-input', 'authUsername', 'authPassword', 'authAvatarFile', 'settingsAvatarFile',
    'apiKeyInput', 'webSearchApiKeyInput', 'webFetchApiKeyInput', 'mainModelSelect', 'mainApiBaseUrlInput'].forEach(function(id) {
    var node = document.getElementById(id); if (node) node.value = '';
  });
  ['avatarSettingsOverlay', 'memorySettingsOverlay', 'onboardingOverlay',
    'sessionFilePreviewOverlay', 'sessionFilesPanel'].forEach(function(id) {
    var node = document.getElementById(id); if (node) node.hidden = true;
  });
  ['settingsAvatarPreview', 'authAvatarPreview'].forEach(function(id) {
    var node = document.getElementById(id); if (node) node.removeAttribute('src');
  });
  var download = document.getElementById('sessionFilePreviewDownload');
  if (download) download.removeAttribute('href');
  var permission = document.getElementById('permission-card'); if (permission) permission.remove();
  var banner = document.getElementById('degraded-banner'); if (banner) banner.remove();
  closeSessionMenu(); closeSettingsModal(); setPreferenceFormValues({});
  ['ctxDrawer', 'ctxBackdrop'].forEach(function(id) {
    var node = document.getElementById(id); if (node) node.classList.remove('open');
  });
  if (citationCount) citationCount.textContent = '0';
  var count = document.getElementById('session-count'); if (count) count.textContent = '0';
  renderAttachmentSelection(); renderReviewConsent(); setSendDisabled(false);
  showAuthGate(true);
}
// Drawing is available through natural-language requests; no composer opt-in.
var visualizationEnabled = true;
var citrusAvatarPresets = Array.from({length: 20}, function(_, index) { return String(index + 1).padStart(2, '0'); });
var signupAvatarPreset = '01';
var signupAvatarDataUrl = '';
var settingsAvatarPreset = '01';
var settingsAvatarDataUrl = '';
var onboardingSaveInFlight = false;
function sessionStorageKey() { return 'citrus_session_id_' + (currentAccount ? currentAccount.user_id : ''); }
function avatarSource(account) {
  if (!account) return '/assets/avatars/01.webp';
  if (account.avatar_custom) return '/api/v2/auth/avatar?v=' + encodeURIComponent(account.avatar_updated_at || 'current');
  var preset = /^\d{2}$/.test(account.avatar_preset || '') ? account.avatar_preset : '01';
  return '/assets/avatars/' + preset + '.webp';
}
function renderAvatarImage(container, account, className, altText) {
  var epoch = authEpoch;
  if (!container) return;
  container.replaceChildren();
  var image = document.createElement('img');
  image.className = className || '';
  image.src = avatarSource(account);
  image.alt = altText || '用户头像';
  image.decoding = 'async';
  image.onerror = function() {
    if (epoch !== authEpoch || image.parentNode !== container) return;
    container.replaceChildren();
    container.textContent = account && account.username
      ? Array.from(account.username.trim())[0].toLocaleUpperCase() : 'C';
  };
  container.appendChild(image);
}
function renderAgentAvatar(container) {
  if (!container) return;
  var image = document.createElement('img');
  image.className = 'agent-avatar-image';
  image.src = '/assets/citrusscholar-mascot.png';
  image.alt = 'CitrusScholar 助手';
  image.width = 36;
  image.height = 36;
  image.decoding = 'async';
  container.replaceChildren(image);
}
function refreshCurrentUserAvatars() {
  renderAvatarImage(document.getElementById('accountAvatar'), currentAccount, 'account-avatar-image', '账户头像');
  document.querySelectorAll('.message.user .avatar').forEach(function(node) {
    renderAvatarImage(node, currentAccount, 'message-avatar-image', '我的头像');
  });
}
function showAuthGate(show) {
  var gate = document.getElementById('authGate');
  if (gate) gate.hidden = !show;
  var app = document.getElementById('mainContainer');
  if (app) app.style.visibility = show ? 'hidden' : '';
  var account = document.getElementById('sidebarAccount');
  if (account) account.hidden = !currentAccount;
  var settings = document.getElementById('modelSettingsBtn');
  if (settings) settings.hidden = !currentAccount || currentAccount.role !== 'admin';
  var who = document.getElementById('accountLabel');
  if (who) who.textContent = currentAccount ? currentAccount.username : '';
  refreshCurrentUserAvatars();
  var role = document.getElementById('accountRole');
  if (role) role.textContent = currentAccount && currentAccount.role === 'admin' ? '管理员' : '账户';
  var admin = document.getElementById('adminUsersBtn');
  if (admin) admin.hidden = !currentAccount || currentAccount.role !== 'admin';
  var logout = document.getElementById('logoutBtn');
  if (logout) logout.hidden = !currentAccount;
  if (show) setAccountMenuOpen(false);
}
let reviewConsentEnabled = false;

async function refreshReviewConsent() {
  var epoch = authEpoch;
  try {
    const res = await accountFetch(apiBase() + "/api/v2/review-consent", {credentials: "include"});
    if (!res.ok) return;
    const data = await res.json();
    if (epoch !== authEpoch) return;
    reviewConsentEnabled = !!data.enabled;
  } catch (err) { if (epoch !== authEpoch) return;
    return;
  }
  renderReviewConsent();
}

function renderReviewConsent() {
  const btn = document.getElementById("reviewConsentBtn");
  if (!btn) return;
  btn.textContent = "\u21ea 文献共享授权：" + (reviewConsentEnabled ? "已授权" : "未授权");
  btn.setAttribute("aria-pressed", reviewConsentEnabled ? "true" : "false");
}

async function toggleReviewConsent() {
  var epoch = authEpoch;
  const next = !reviewConsentEnabled;
  try {
    const res = await accountFetch(apiBase() + "/api/v2/review-consent", {
      method: "PUT",
      credentials: "include",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({enabled: next})
    });
    if (epoch !== authEpoch) return;
    if (!res.ok) {
      showToast && showToast("\u4fdd\u5b58\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5");
      return;
    }
    if (epoch !== authEpoch) return;
    reviewConsentEnabled = next;
    renderReviewConsent();
    showToast && showToast(next
      ? "\u5df2\u5f00\u542f\uff1a\u4f60\u4e0a\u4f20\u7684\u6587\u732e\u5c06\u8fdb\u5165\u7ba1\u7406\u5458\u5019\u5ba1\u5e93"
      : "\u5df2\u5173\u95ed\uff1a\u4e0a\u4f20\u6587\u4ef6\u4e0d\u518d\u5171\u4eab");
  } catch (err) { if (epoch !== authEpoch) return;
    showToast && showToast("\u7f51\u7edc\u5f02\u5e38\uff0c\u672a\u4fdd\u5b58");
  }
}

function setAccountMenuOpen(open) {
  var menu = document.getElementById('accountMenu');
  var trigger = document.getElementById('accountTrigger');
  if (menu) menu.hidden = !open;
  if (trigger) trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) {
    try { refreshReviewConsent(); } catch (err) {}
    // P1-4: 菜单里的账号信息与左下触发器保持一致
    var nameEl = document.getElementById('accountMenuName');
    var roleEl = document.getElementById('accountMenuRole');
    var triggerName = document.querySelector('.account-trigger-name');
    var triggerRole = document.querySelector('.account-trigger-role');
    if (nameEl && triggerName) nameEl.textContent = triggerName.textContent || '';
    if (roleEl && triggerRole) roleEl.textContent = triggerRole.textContent || '';
  }
}
function toggleAccountMenu() {
  var menu = document.getElementById('accountMenu');
  setAccountMenuOpen(!menu || menu.hidden);
}
document.addEventListener('click', function(event) {
  var area = document.getElementById('sidebarAccount');
  if (area && !area.contains(event.target)) setAccountMenuOpen(false);
});
// 本轮：宠物与主题拆成两个独立弹层——主题只管配色/背景，宠物只管吉祥物本体
function _railMenuEls() {
  return {
    pet: document.getElementById('petMenu'),
    petBtn: document.getElementById('petRailBtn'),
    theme: document.getElementById('themeMenu'),
    themeBtn: document.getElementById('themeRailBtn')
  };
}
function setRailMenuOpen(which, open) {
  var els = _railMenuEls();
  var menu = which === 'pet' ? els.pet : els.theme;
  var btn = which === 'pet' ? els.petBtn : els.themeBtn;
  if (!menu) return;
  if (open) {
    var other = which === 'pet' ? els.theme : els.pet;
    var otherBtn = which === 'pet' ? els.themeBtn : els.petBtn;
    if (other) other.hidden = true;
    if (otherBtn) { otherBtn.setAttribute('aria-expanded', 'false'); otherBtn.classList.remove('is-active'); }
    setAccountMenuOpen(false);
    if (which === 'pet') syncPetPanel();
  }
  menu.hidden = !open;
  if (btn) {
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    btn.classList.toggle('is-active', !!open);
  }
}
function togglePetMenu() {
  var menu = document.getElementById('petMenu');
  setRailMenuOpen('pet', !!menu && menu.hidden);
}
function toggleThemeMenu() {
  var menu = document.getElementById('themeMenu');
  setRailMenuOpen('theme', !!menu && menu.hidden);
}
document.addEventListener('click', function(event) {
  var els = _railMenuEls();
  var pairs = [['pet', els.pet, els.petBtn], ['theme', els.theme, els.themeBtn]];
  for (var i = 0; i < pairs.length; i++) {
    var menu = pairs[i][1]; var btn = pairs[i][2];
    if (!menu || menu.hidden) continue;
    if (menu.contains(event.target) || (btn && btn.contains(event.target))) continue;
    setRailMenuOpen(pairs[i][0], false);
  }
});
document.addEventListener('keydown', function(event) {
  if (event.key !== 'Escape') return;
  var els = _railMenuEls();
  if (els.pet && !els.pet.hidden) setRailMenuOpen('pet', false);
  if (els.theme && !els.theme.hidden) setRailMenuOpen('theme', false);
});
// 宠物面板与吉祥物自身控件共享同一份尺寸/显隐状态（citrus_mascot_scale / citrus_mascot_hidden）
function _mascotScalePct() {
  var scale = 100;
  if (window.CitrusMascot && typeof window.CitrusMascot.getScale === 'function') {
    scale = Number(window.CitrusMascot.getScale());
  } else {
    try { scale = Number(localStorage.getItem('citrus_mascot_scale')); } catch (err) {}
  }
  if (!isFinite(scale) || scale <= 0) return 100;
  if (scale <= 1.8) scale = scale * 100;   // 兼容 0.8–1.8 与 80–180 两种存法
  return Math.max(80, Math.min(180, Math.round(scale)));
}
function syncPetPanel() {
  var hidden = false;
  try { hidden = localStorage.getItem('citrus_mascot_hidden') === '1'; } catch (err) {}
  var text = document.getElementById('petToggleText');
  if (text) text.textContent = hidden ? '显示宠物' : '隐藏宠物';
  var pct = _mascotScalePct();
  var range = document.getElementById('petScaleRange');
  var value = document.getElementById('petScaleValue');
  if (range && Number(range.value) !== pct) range.value = String(pct);
  if (value) value.textContent = pct + '%';
  var motion = null;
  try { motion = localStorage.getItem('citrus_pet_motion'); } catch (err) {}
  var box = document.getElementById('petMotionToggle');
  if (box) box.checked = motion !== '0';
  syncPetActivity();
  syncMascotRailButton();
}
function onPetScaleInput(value) {
  var pct = Math.max(80, Math.min(180, Math.round(Number(value) || 100)));
  if (window.CitrusMascot && typeof window.CitrusMascot.setScale === 'function') {
    window.CitrusMascot.setScale(pct, true);
  } else {
    try { localStorage.setItem('citrus_mascot_scale', String(pct)); } catch (err) {}
  }
  var range = document.getElementById('petScaleRange');
  var label = document.getElementById('petScaleValue');
  if (range && Number(range.value) !== pct) range.value = String(pct);
  if (label) label.textContent = pct + '%';
}
function resetPetScale() {
  onPetScaleInput(100);
  if (window.CitrusMascot && typeof window.CitrusMascot.setScale === 'function') {
    window.CitrusMascot.setScale(100, true);
  }
}
function resetPetPosition() {
  if (window.CitrusMascot && typeof window.CitrusMascot.resetPosition === 'function') {
    window.CitrusMascot.resetPosition();
    return;
  }
  try { localStorage.removeItem('citrus_mascot_pos'); } catch (err) {}
  if (window.CitrusMascot && typeof window.CitrusMascot.setPosition === 'function') {
    window.CitrusMascot.setPosition(null);
  }
}
function setPetMotion(on) {
  try { localStorage.setItem('citrus_pet_motion', on ? '1' : '0'); } catch (err) {}
  if (window.CitrusMascot && typeof window.CitrusMascot.setMotion === 'function') {
    window.CitrusMascot.setMotion(!!on);
  }
}
function syncMascotRailButton() {
  var btn = document.getElementById('petRailBtn');
  if (!btn) return;
  var hidden = false;
  try { hidden = localStorage.getItem('citrus_mascot_hidden') === '1'; } catch (err) {}
  btn.setAttribute('aria-pressed', hidden ? 'false' : 'true');
  btn.classList.toggle('is-off', hidden);
  var text = document.getElementById('petToggleText');
  if (text) text.textContent = hidden ? '显示宠物' : '隐藏宠物';
}
function setPetActivity(level) {
  if (level !== 'quiet' && level !== 'natural' && level !== 'lively') return;
  try { localStorage.setItem('citrus_mascot_activity', level); } catch (err) {}
  if (window.CitrusMascot && typeof window.CitrusMascot.setActivityLevel === 'function') {
    window.CitrusMascot.setActivityLevel(level);
  }
  syncPetActivity();
}
function syncPetActivity() {
  var level = 'natural';
  try { level = localStorage.getItem('citrus_mascot_activity') || 'natural'; } catch (err) {}
  if (level !== 'quiet' && level !== 'natural' && level !== 'lively') level = 'natural';
  if (window.CitrusMascot && typeof window.CitrusMascot.getActivityLevel === 'function') {
    var live = window.CitrusMascot.getActivityLevel();
    if (live) level = live;
  }
  var btns = document.querySelectorAll('[data-pet-activity]');
  for (var i = 0; i < btns.length; i++) {
    var on = btns[i].getAttribute('data-pet-activity') === level;
    btns[i].classList.toggle('is-on', on);
    btns[i].setAttribute('aria-checked', on ? 'true' : 'false');
  }
}
function toggleMascotVisibility() {
  if (window.CitrusMascot) window.CitrusMascot.toggle();
  syncMascotRailButton();
}
// P5: 等待态「绿色小芽」节点从模板克隆（模板在 index.html，避免重复 DOM）
function _hideLoadSprout(el) {
  if (!el || el.hidden) return;
  el.classList.add('is-hiding');
  setTimeout(function () { el.hidden = true; el.classList.remove('is-hiding'); }, 260);
}
function _createLoadSprout() {
  var tpl = document.getElementById('loadSproutTpl');
  if (!tpl || !tpl.content) return null;
  var node = tpl.content.firstElementChild;
  if (!node) return null;
  var clone = node.cloneNode(true);
  clone.hidden = false;
  return clone;
}
// P1-4: 减少动画——只影响过渡与装帧动画，不动吉祥物状态机
function setReduceMotion(on) {
  document.documentElement.setAttribute('data-reduce-motion', on ? '1' : '0');
  var box = document.getElementById('reduceMotionToggle');
  if (box) box.checked = !!on;
  try { localStorage.setItem('citrus_reduce_motion', on ? '1' : '0'); } catch (err) {}
}
// Force motion ignores the OS 'reduce motion' preference, for users who want the
// loading sprout and mascot micro-motion even when Windows animations are off.
function setForceMotion(on) {
  document.documentElement.setAttribute('data-force-motion', on ? '1' : '0');
  var box = document.getElementById('forceMotionToggle');
  if (box) box.checked = !!on;
  try { localStorage.setItem('citrus_force_motion', on ? '1' : '0'); } catch (err) {}
}
(function restoreForceMotion() {
  var saved = null;
  try { saved = localStorage.getItem('citrus_force_motion'); } catch (err) {}
  setForceMotion(saved === '1');
})();
(function restoreReduceMotion() {
  var saved = null;
  try { saved = localStorage.getItem('citrus_reduce_motion'); } catch (err) {}
  setReduceMotion(saved === '1');
  syncMascotRailButton();
})();
(function bindPetPanel() {
  var range = document.getElementById('petScaleRange');
  if (range) range.addEventListener('input', function () { onPetScaleInput(range.value); });
  syncPetPanel();
})();
function renderAvatarGrid(host, selected, onSelect) {
  if (!host) return;
  host.replaceChildren();
  citrusAvatarPresets.forEach(function(preset) {
    var option = document.createElement('button');
    option.type = 'button'; option.className = 'avatar-option' + (selected === preset ? ' selected' : '');
    option.setAttribute('role', 'option'); option.setAttribute('aria-selected', selected === preset ? 'true' : 'false');
    option.setAttribute('aria-label', '头像 ' + preset); option.title = '头像 ' + preset;
    var img = document.createElement('img'); img.src = '/assets/avatars/' + preset + '.webp'; img.alt = '';
    var label = document.createElement('span'); label.textContent = preset;
    option.appendChild(img); option.appendChild(label);
    option.addEventListener('click', function() { onSelect(preset); });
    host.appendChild(option);
  });
}
function setSignupAvatar(preset) {
  signupAvatarPreset = preset; signupAvatarDataUrl = '';
  var file = document.getElementById('authAvatarFile'); if (file) file.value = '';
  var error = document.getElementById('authAvatarError'); if (error) error.textContent = '';
  var preview = document.getElementById('authAvatarPreview'); if (preview) preview.src = '/assets/avatars/' + preset + '.webp';
  var label = document.getElementById('authAvatarLabel'); if (label) label.textContent = '已选 ' + preset;
  renderAvatarGrid(document.getElementById('authAvatarGrid'), preset, setSignupAvatar);
}
function syncAuthAvatarPicker() {
  var picker = document.getElementById('authAvatarPicker');
  var mode = document.getElementById('authMode');
  if (!picker || !mode) return;
  picker.hidden = mode.value !== 'register';
  if (mode.value === 'register' && !document.getElementById('authAvatarGrid').childElementCount) setSignupAvatar('01');
}
function previewSignupAvatar(input) {
  var epoch = authEpoch;
  var file = input && input.files && input.files[0];
  var error = document.getElementById('authAvatarError'); if (error) error.textContent = '';
  if (!file) return;
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) {
    if (error) error.textContent = file.size > 5 * 1024 * 1024 ? '图片不能超过 5 MB。' : '请选择 JPG、PNG 或 WebP 图片。';
    input.value = ''; return;
  }
  fileAsDataUrl(file).then(function(dataUrl) {
    if (epoch !== authEpoch) return;
    signupAvatarDataUrl = dataUrl;
    var preview = document.getElementById('authAvatarPreview'); if (preview) preview.src = signupAvatarDataUrl;
    var label = document.getElementById('authAvatarLabel'); if (label) label.textContent = '自定义头像';
    renderAvatarGrid(document.getElementById('authAvatarGrid'), '', setSignupAvatar);
  }).catch(function() { if (epoch === authEpoch && error) error.textContent = '读取图片失败，请重试。'; });
}
function openAvatarSettings() {
  var overlay = document.getElementById('avatarSettingsOverlay'); if (!overlay) return;
  settingsAvatarPreset = currentAccount && currentAccount.avatar_preset || '01';
  settingsAvatarDataUrl = '';
  var input = document.getElementById('settingsAvatarFile'); if (input) input.value = '';
  var error = document.getElementById('settingsAvatarError'); if (error) error.textContent = '';
  var preview = document.getElementById('settingsAvatarPreview');
  if (preview) preview.src = avatarSource(currentAccount);
  var label = document.getElementById('settingsAvatarLabel');
  if (label) label.textContent = currentAccount && currentAccount.avatar_custom ? '当前自定义头像' : '预设头像 ' + settingsAvatarPreset;
  renderAvatarGrid(document.getElementById('settingsAvatarGrid'),
    currentAccount && currentAccount.avatar_custom ? '' : settingsAvatarPreset, selectSettingsAvatar);
  overlay.hidden = false;
  var first = overlay.querySelector('.avatar-option'); if (first) first.focus();
}
function closeAvatarSettings() {
  var overlay = document.getElementById('avatarSettingsOverlay'); if (overlay) overlay.hidden = true;
  var trigger = document.getElementById('accountTrigger'); if (trigger) trigger.focus();
}
function selectSettingsAvatar(preset) {
  settingsAvatarPreset = preset; settingsAvatarDataUrl = '';
  var input = document.getElementById('settingsAvatarFile'); if (input) input.value = '';
  var preview = document.getElementById('settingsAvatarPreview'); if (preview) preview.src = '/assets/avatars/' + preset + '.webp';
  var label = document.getElementById('settingsAvatarLabel'); if (label) label.textContent = '预设头像 ' + preset;
  var error = document.getElementById('settingsAvatarError'); if (error) error.textContent = '';
  renderAvatarGrid(document.getElementById('settingsAvatarGrid'), preset, selectSettingsAvatar);
}
function previewSettingsAvatar(input) {
  var epoch = authEpoch;
  var file = input && input.files && input.files[0];
  var error = document.getElementById('settingsAvatarError'); if (error) error.textContent = '';
  if (!file) return;
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) {
    if (error) error.textContent = file.size > 5 * 1024 * 1024 ? '图片不能超过 5 MB。' : '请选择 JPG、PNG 或 WebP 图片。';
    input.value = ''; return;
  }
  fileAsDataUrl(file).then(function(dataUrl) {
    if (epoch !== authEpoch) return;
    settingsAvatarDataUrl = dataUrl;
    var preview = document.getElementById('settingsAvatarPreview'); if (preview) preview.src = settingsAvatarDataUrl;
    var label = document.getElementById('settingsAvatarLabel'); if (label) label.textContent = '自定义头像';
    renderAvatarGrid(document.getElementById('settingsAvatarGrid'), '', selectSettingsAvatar);
  }).catch(function() { if (epoch === authEpoch && error) error.textContent = '读取图片失败，请重试。'; });
}
async function saveAccountAvatar() {
  var epoch = authEpoch;
  var button = document.getElementById('saveAvatarButton');
  var error = document.getElementById('settingsAvatarError');
  if (button) button.disabled = true;
  if (error) error.textContent = '';
  try {
    var endpoint = settingsAvatarDataUrl ? '/api/v2/auth/avatar/upload' : '/api/v2/auth/avatar/preset';
    var body = settingsAvatarDataUrl ? {data_url: settingsAvatarDataUrl} : {preset: settingsAvatarPreset};
    var response = await accountFetch(endpoint, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    var result = await response.json(); if (epoch !== authEpoch) return;
    if (!response.ok) throw new Error(result.detail || '头像保存失败。');
    currentAccount = result.user;
    showAuthGate(false);
    closeAvatarSettings();
  } catch (e) { if (epoch !== authEpoch) return; if (error) error.textContent = e.message || '头像保存失败。'; }
  finally { if (epoch !== authEpoch) return; if (button) button.disabled = false; }
}
function openOnboardingGuide(startPage) {
  if (window.CitrusGuide) window.CitrusGuide.open(startPage || 'research', {onClose: completeOnboarding});
}
var memorySettingsCurrentTab = 'preferences';

function switchMemorySettingsTab(tab) {
  var selected = tab === 'records' ? 'records' : 'preferences';
  memorySettingsCurrentTab = selected;
  var preferencesPane = document.getElementById('preferencesPane');
  var recordsPane = document.getElementById('recordsPane');
  var preferencesTab = document.getElementById('preferencesTab');
  var recordsTab = document.getElementById('recordsTab');
  if (preferencesPane) preferencesPane.hidden = selected !== 'preferences';
  if (recordsPane) recordsPane.hidden = selected !== 'records';
  if (preferencesTab) {
    preferencesTab.classList.toggle('is-active', selected === 'preferences');
    preferencesTab.setAttribute('aria-selected', selected === 'preferences' ? 'true' : 'false');
  }
  if (recordsTab) {
    recordsTab.classList.toggle('is-active', selected === 'records');
    recordsTab.setAttribute('aria-selected', selected === 'records' ? 'true' : 'false');
  }
}

function setPreferenceFormValues(data) {
  var preferences = (data && data.preferences) || {};
  var fields = document.querySelectorAll('#preferenceForm [data-preference-field]');
  for (var i = 0; i < fields.length; i++) {
    var field = fields[i].getAttribute('data-preference-field');
    fields[i].value = preferences[field] == null ? '' : String(preferences[field]);
  }
  updateCustomNoteCount();
}

function collectPreferenceFormValues() {
  var payload = {};
  var fields = document.querySelectorAll('#preferenceForm [data-preference-field]');
  for (var i = 0; i < fields.length; i++) {
    payload[fields[i].getAttribute('data-preference-field')] = fields[i].value || '';
  }
  return payload;
}

function updateCustomNoteCount() {
  var note = document.querySelector('#preferenceForm [data-preference-field="custom_note"]');
  var count = document.getElementById('customNoteCount');
  if (note && count) count.textContent = String(Array.from(note.value || '').length);
}

async function loadAccountPreferences() {
  var epoch = authEpoch;
  var status = document.getElementById('accountPreferenceStatus');
  if (status) status.textContent = '正在读取…';
  try {
    var response = await accountFetch('/api/v2/auth/preferences', {credentials:'same-origin'});
    var data = await response.json(); if (epoch !== authEpoch) return;
    if (!response.ok) throw new Error(data.detail || '读取个人偏好失败。');
    setPreferenceFormValues(data);
    if (status) status.textContent = '';
  } catch (error) { if (epoch !== authEpoch) return;
    if (status) status.textContent = error.message || '读取个人偏好失败。';
  }
}

async function saveAccountPreferences() {
  var epoch = authEpoch;
  var status = document.getElementById('accountPreferenceStatus');
  if (status) status.textContent = '正在保存…';
  try {
    var response = await accountFetch('/api/v2/auth/preferences', {
      method:'PUT',
      credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(collectPreferenceFormValues())
    });
    var data = await response.json(); if (epoch !== authEpoch) return;
    if (!response.ok) throw new Error(data.detail || '保存个人偏好失败。');
    setPreferenceFormValues(data);
    if (status) status.textContent = '已保存，下一轮回答生效。';
  } catch (error) { if (epoch !== authEpoch) return;
    if (status) status.textContent = error.message || '保存个人偏好失败。';
  }
}

async function resetAccountPreferences() {
  var epoch = authEpoch;
  if (!window.confirm('将个人偏好恢复为系统默认值？记录和会话不会删除。')) return;
  var status = document.getElementById('accountPreferenceStatus');
  if (status) status.textContent = '正在恢复…';
  try {
    var response = await accountFetch('/api/v2/auth/preferences', {
      method:'DELETE', credentials:'same-origin'
    });
    var data = await response.json(); if (epoch !== authEpoch) return;
    if (!response.ok) throw new Error(data.detail || '恢复默认值失败。');
    setPreferenceFormValues(data);
    if (status) status.textContent = '已恢复默认值。';
  } catch (error) { if (epoch !== authEpoch) return;
    if (status) status.textContent = error.message || '恢复默认值失败。';
  }
}

async function loadAccountMemoryRecords() {
  var epoch = authEpoch;
  var content = document.getElementById('accountMemoryContent');
  var status = document.getElementById('accountMemoryStatus');
  if (content) content.textContent = '正在读取记录…';
  if (status) status.textContent = '';
  try {
    var response = await accountFetch('/api/v2/auth/memory', {credentials:'same-origin'});
    var data = await response.json(); if (epoch !== authEpoch) return;
    if (!response.ok) throw new Error(data.detail || '读取记录失败。');
    var facts = data.facts || [];
    if (!facts.length) {
      if (content) content.innerHTML = '<div class="memory-empty">没有残留长期记录。<br>自动记忆已停用，项目资料请使用科研笔记保存。</div>';
      return;
    }
    if (content) {
      content.innerHTML = '<h3>残留长期记录</h3><div class="memory-list">' + facts.map(function(item) {
        return '<article class="memory-row"><strong>' + escapeHtml(item.key || '记录') + '</strong><span>' + escapeHtml(item.value || '') + '</span><small>置信度 ' + Math.round(Number(item.confidence || 0) * 100) + '% · ' + escapeHtml((item.updated_at || '').slice(0,10)) + '</small></article>';
      }).join('') + '</div>';
    }
  } catch (error) { if (epoch !== authEpoch) return;
    if (content) content.textContent = error.message || '读取记录失败。';
  }
}

async function openMemorySettings(tab) {
  var epoch = authEpoch;
  var overlay = document.getElementById('memorySettingsOverlay');
  if (!overlay) return;
  overlay.hidden = false;
  switchMemorySettingsTab(tab || 'preferences');
  if (memorySettingsCurrentTab === 'records') {
    await loadAccountMemoryRecords();
  } else {
    await loadAccountPreferences();
    if (epoch !== authEpoch) return;
    var first = document.querySelector('#preferenceForm select');
    if (first) first.focus();
  }
}
function closeMemorySettings() {
  var overlay = document.getElementById('memorySettingsOverlay'); if (overlay) overlay.hidden = true;
  var trigger = document.getElementById('accountTrigger'); if (trigger) trigger.focus();
}
async function clearAccountMemory() {
  var epoch = authEpoch;
  if (!window.confirm('清除这个账号残留的长期记录？个人偏好、会话和资料库不会删除。')) return;
  var status = document.getElementById('accountMemoryStatus');
  var content = document.getElementById('accountMemoryContent');
  try {
    var response = await accountFetch('/api/v2/auth/memory', {method:'DELETE', credentials:'same-origin'});
    var data = await response.json(); if (epoch !== authEpoch) return;
    if (!response.ok) throw new Error(data.detail || '清理失败。');
    if (status) status.textContent = '记录已清除';
    if (content) content.innerHTML = '<div class="memory-empty">没有残留长期记录。<br>自动记忆已停用，项目资料请使用科研笔记保存。</div>';
  } catch (error) { if (epoch !== authEpoch) return; if (status) status.textContent = error.message || '清理失败。'; }
}

document.addEventListener('input', function (event) {
  if (event.target && event.target.matches &&
      event.target.matches('#preferenceForm [data-preference-field="custom_note"]')) {
    updateCustomNoteCount();
  }
});
document.addEventListener('submit', function (event) {
  if (!event.target || event.target.id !== 'preferenceForm') return;
  event.preventDefault();
  saveAccountPreferences();
});
document.addEventListener('keydown', function (event) {
  var overlay = document.getElementById('memorySettingsOverlay');
  if (!overlay || overlay.hidden) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeMemorySettings();
  }
});
async function completeOnboarding() {
  var epoch = authEpoch;
  if (window.CitrusGuide) window.CitrusGuide.close(false);
  if (!currentAccount || currentAccount.onboarding_completed || onboardingSaveInFlight) return;
  onboardingSaveInFlight = true;
  try {
    var response = await accountFetch('/api/v2/auth/onboarding/complete', {method:'POST'});
    if (!response.ok) throw new Error('guide state not saved');
    var data = await response.json(); if (epoch !== authEpoch) return; currentAccount = data.user;
  } catch (_) { if (epoch !== authEpoch) return;
    // Keep the guide dismissible during network failures; the account will retry
    // onboarding on its next sign-in, without overwriting account/theme storage.
    updateStatus('warn', '指南阅读状态未保存；可随时从使用指南重新打开。');
  } finally { if (epoch !== authEpoch) return; onboardingSaveInFlight = false; }
}
async function authenticate(mode) {
  if (authInFlight) return;
  advanceAuthEpoch();
  authInFlight = true;
  var epoch = authEpoch;
  var username = (document.getElementById('authUsername').value || '').trim();
  var password = document.getElementById('authPassword').value || '';
  var error = document.getElementById('authError');
  error.textContent = '';
  try {
    var payload = {username:username,password:password};
    if (mode === 'register') payload.avatar_preset = signupAvatarPreset;
    var response = await accountFetch('/api/v2/auth/' + mode, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    var data = await response.json();
    if (epoch !== authEpoch) return;
    if (!response.ok) throw new Error(data.detail || '登录失败');
    // Other page modules own private caches. A retired document may never host
    // another account: navigation releases those closures and their File refs.
    if (accountDocumentRetired) { location.replace('/'); return; }
    currentAccount = data.user;
    localStorage.removeItem('citrus_session_id');
    var next = new URLSearchParams(location.search).get('next');
    if (next === '/admin') {
      if (currentAccount.role !== 'admin') throw new Error('此页面仅管理员可访问。');
      location.replace('/admin'); return;
    }
    var signupAvatarError = '';
    if (mode === 'register' && signupAvatarDataUrl) {
      try {
        var avatarResponse = await accountFetch('/api/v2/auth/avatar/upload', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data_url:signupAvatarDataUrl})});
        var avatarResult = await avatarResponse.json();
        if (epoch !== authEpoch) return;
        if (!avatarResponse.ok) throw new Error(avatarResult.detail || '头像上传失败。');
        currentAccount = avatarResult.user;
      } catch (avatarErr) { if (epoch !== authEpoch) return; signupAvatarError = '账号已创建，自定义头像未保存；你可以稍后在账户菜单中更换。'; }
    }
    signupAvatarDataUrl = '';
    await startAuthenticatedApp();
    if (epoch !== authEpoch) return;
    if (signupAvatarError) updateStatus('warn', signupAvatarError);
  } catch (e) { if (epoch === authEpoch) error.textContent = e.message || '请求失败'; }
  finally { if (epoch === authEpoch) authInFlight = false; }
}
async function logoutAccount() {
  if (accountDocumentRetired) return;
  accountDocumentRetired = true;
  resetAccountState();
  authInFlight = true;
  var epoch = authEpoch;
  try {
    var response = await accountFetch('/api/v2/auth/logout', {method:'POST'});
    if (epoch !== authEpoch) return;
    if (!response.ok) throw new Error('退出未完成，请重试登录。');
    location.replace('/');
  } catch (error) {
    if (epoch !== authEpoch) return;
    document.getElementById('authError').textContent = error.message || '退出未完成，请重试登录。';
  } finally { if (epoch === authEpoch) authInFlight = false; }
}
async function startAuthenticatedApp() {
  var epoch = authEpoch;
  showAuthGate(false);
  sessionId = localStorage.getItem(sessionStorageKey());
  var configReady;
  if (currentAccount && currentAccount.role === 'admin') configReady = loadRuntimeConfig();
  else configReady = accountFetch('/api/v2/config').then(function(r) { if (epoch !== authEpoch) return; return r.json(); }).then(function(cfg) { if (epoch !== authEpoch) return;
    _runtimeConfig = cfg; applyAppVersion();
  }).catch(function(){ if (epoch !== authEpoch) return;});
  await ensureSession(); if (epoch !== authEpoch) return;
  if (currentAccount && currentAccount.role === 'admin' &&
      new URLSearchParams(location.search).get('adminSettings') === '1') {
    if (configReady) await configReady;
    if (epoch !== authEpoch) return;
    history.replaceState(null, '', '/');
    openSettingsModal();
  }
  if (currentAccount && currentAccount.onboarding_required) {
    window.setTimeout(function() { if (epoch === authEpoch) openOnboardingGuide(); }, 300);
  }
}
async function bootstrapAccount() {
  var epoch = authEpoch;
  try {
    var response = await accountFetch('/api/v2/auth/me');
    if (!response.ok) throw new Error('not logged in');
    var data = await response.json(); if (epoch !== authEpoch) return; currentAccount = data.user;
    await startAuthenticatedApp();
  } catch (_) { if (epoch !== authEpoch) return; showAuthGate(true); }
}
window.addEventListener('DOMContentLoaded', bootstrapAccount);
var sessionId = null;
var chatContainer = document.getElementById('chat-container');
var userInput = document.getElementById('user-input');
var sendBtn = document.getElementById('send-btn');
var sidebarContent = document.getElementById('sidebar-content');
var citationCount = document.getElementById('citation-count');
var _selectedAttachments = [];
window._selectedAccountFileRefs = window._selectedAccountFileRefs || [];
var _attachmentPreviewUrls = [];
var _sentAttachmentPreviewUrls = [];
var roundHistory = [];
var _citationsRetry = 0;   // v8.10q: citations 空结果延迟重试计数（防 spawn 时序缺口）
var currentRoundId = 0;
var currentQuery = '';
var totalTokens = 0;
var _agentTokenBuffer = '';
var _agentTokenName = '';

if (typeof marked === 'undefined') window.marked = { parse: function(t) { return t; } };

function handleAttachmentSelection(fileList) {
  if (!currentAccount || accountDocumentRetired) return;
  var dropped = Array.from(fileList || []);
  var allowed = dropped.filter(function(file) {
    var name = String(file.name || '').toLowerCase();
    return /^image\/(jpeg|png|webp|gif)$/.test(file.type || '') || /\.(jpe?g|png|webp|gif|pdf|docx|csv|tsv|xlsx|xls|xlsm|xlsb)$/.test(name);
  });
  if (allowed.length !== dropped.length) {
    var rejected = dropped.filter(function(file) { return allowed.indexOf(file) < 0; });
    alert('暂不支持这些文件：' + rejected.map(function(file) { return file.name; }).join('、'));
  }
  var added = allowed;
  if (!added.length) return;
  var total = _selectedAttachments.reduce(function(n, f) { return n + f.size; }, 0) +
    added.reduce(function(n, f) { return n + f.size; }, 0);
  if (_selectedAttachments.length + window._selectedAccountFileRefs.length + added.length > 8 || total > 20 * 1024 * 1024) {
    alert('每轮最多 8 个附件，合计不超过 20 MB。');
    var input = document.getElementById('attachment-input'); if (input) input.value = '';
    return;
  }
  _selectedAttachments = _selectedAttachments.concat(added);
  renderAttachmentSelection();
  var picker = document.getElementById('attachment-input'); if (picker) picker.value = '';
}

function hasDraggedFiles(event) {
  var types = event.dataTransfer && event.dataTransfer.types;
  return !!types && Array.from(types).indexOf('Files') >= 0;
}

(function enableFileDrop() {
  var panel = document.querySelector('.chat-panel');
  if (!panel) return;
  var dragDepth = 0;
  panel.addEventListener('dragenter', function(event) {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    dragDepth += 1;
    panel.classList.add('file-drag-over');
  });
  panel.addEventListener('dragover', function(event) {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
  });
  panel.addEventListener('dragleave', function(event) {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) panel.classList.remove('file-drag-over');
  });
  panel.addEventListener('drop', function(event) {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    dragDepth = 0;
    panel.classList.remove('file-drag-over');
    handleAttachmentSelection(event.dataTransfer.files);
  });
})();

function renderAttachmentSelection() {
  var host = document.getElementById('attachment-list');
  _attachmentPreviewUrls.forEach(function(url) { URL.revokeObjectURL(url); });
  _attachmentPreviewUrls = [];
  if (!host) return;
  host.textContent = '';
  host.style.display = (_selectedAttachments.length || window._selectedAccountFileRefs.length) ? 'flex' : 'none';
  _selectedAttachments.forEach(function(file, index) {
    var card = document.createElement('div');
    card.className = 'attachment-preview-card';
    card.title = file.name;
    if (/^image\/(jpeg|png|webp|gif)$/.test(file.type || '') || /\.(jpe?g|png|webp|gif)$/i.test(file.name || '')) {
      var previewUrl = URL.createObjectURL(file);
      _attachmentPreviewUrls.push(previewUrl);
      var image = document.createElement('img');
      image.className = 'attachment-preview-image';
      image.src = previewUrl;
      image.alt = file.name;
      card.appendChild(image);
    } else {
      var icon = document.createElement('span');
      icon.className = 'attachment-preview-icon';
      icon.textContent = /\.pdf$/i.test(file.name || '') ? 'PDF' :
        /\.xlsx$/i.test(file.name || '') ? 'XLSX' : /\.xlsb$/i.test(file.name || '') ? 'XLSB' : /\.xlsm?$/i.test(file.name || '') ? 'XLS' : /\.tsv$/i.test(file.name || '') ? 'TSV' : /\.csv$/i.test(file.name || '') ? 'CSV' : 'DOC';
      card.appendChild(icon);
    }
    var label = document.createElement('span');
    label.className = 'attachment-preview-name';
    label.textContent = file.name;
    card.appendChild(label);
    var remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'attachment-preview-remove';
    remove.setAttribute('aria-label', '移除 ' + file.name);
    remove.title = '移除附件';
    remove.textContent = '×';
    remove.onclick = function() { _selectedAttachments.splice(index, 1); renderAttachmentSelection(); };
    card.appendChild(remove);
    host.appendChild(card);
  });
  window._selectedAccountFileRefs.forEach(function(file, index) {
    var card = document.createElement('div');
    card.className = 'attachment-preview-card library-reference-chip';
    card.title = file.filename || '资料库文件';
    var icon = document.createElement('span');
    icon.className = 'attachment-preview-icon';
    icon.textContent = String(file.filename || '').split('.').pop().toUpperCase().slice(0, 5) || 'FILE';
    card.appendChild(icon);
    var label = document.createElement('span');
    label.className = 'attachment-preview-name';
    label.textContent = file.filename || '资料库文件';
    card.appendChild(label);
    var remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'attachment-preview-remove';
    remove.setAttribute('aria-label', '移除 ' + (file.filename || '资料库文件'));
    remove.textContent = '×';
    remove.onclick = function() { window._selectedAccountFileRefs.splice(index, 1); renderAttachmentSelection(); };
    card.appendChild(remove); host.appendChild(card);
  });
}

function appendSentLibraryReferences(contentEl, files) {
  if (!contentEl || !files || !files.length) return;
  var gallery = document.createElement('div'); gallery.className = 'sent-attachment-gallery sent-library-gallery';
  files.forEach(function(file) {
    var card = document.createElement('button'); card.type = 'button';
    card.className = 'sent-attachment-card sent-library-card';
    card.title = file.filename || '资料库文件';
    var icon = document.createElement('span'); icon.className = 'sent-attachment-icon';
    icon.textContent = String(file.filename || '').split('.').pop().toUpperCase().slice(0, 5) || 'FILE';
    var label = document.createElement('span'); label.className = 'sent-attachment-name';
    label.textContent = file.filename || '资料库文件';
    card.appendChild(icon); card.appendChild(label);
    card.onclick = function() { if (window.openAccountFilePreview) window.openAccountFilePreview(file); };
    gallery.appendChild(card);
  });
  contentEl.appendChild(gallery);
}

function appendRestoredAttachmentCards(contentEl, files) {
  if (!contentEl || !Array.isArray(files) || !files.length) return;
  var gallery = document.createElement('div'); gallery.className = 'sent-attachment-gallery restored-attachment-gallery';
  files.forEach(function(file) {
    var card = document.createElement('button'); card.type = 'button';
    card.className = 'sent-attachment-card sent-library-card';
    card.title = file.filename || '附件';
    if (file.kind === 'image' && file.preview_url) {
      var img = document.createElement('img'); img.className = 'sent-attachment-image';
      img.src = file.preview_url; img.alt = file.filename || '上传图片'; card.appendChild(img);
    } else {
      var icon = document.createElement('span'); icon.className = 'sent-attachment-icon';
      icon.textContent = String(file.filename || '').split('.').pop().toUpperCase().slice(0, 5) || 'FILE';
      card.appendChild(icon);
    }
    var label = document.createElement('span'); label.className = 'sent-attachment-name';
    label.textContent = file.filename || '附件'; card.appendChild(label);
    card.onclick = function() { if (window.openAccountFilePreview) window.openAccountFilePreview(file); };
    gallery.appendChild(card);
  });
  contentEl.appendChild(gallery);
}

function appendSentAttachmentPreviews(contentEl, files) {
  if (!contentEl || !files || !files.length) return;
  var gallery = document.createElement('div');
  gallery.className = 'sent-attachment-gallery';
  files.forEach(function(file) {
    var card = document.createElement('div');
    card.className = 'sent-attachment-card';
    card.title = file.name;
    if (/^image\/(jpeg|png|webp|gif)$/.test(file.type || '') || /\.(jpe?g|png|webp|gif)$/i.test(file.name || '')) {
      var previewUrl = URL.createObjectURL(file);
      _sentAttachmentPreviewUrls.push(previewUrl);
      var image = document.createElement('img');
      image.className = 'sent-attachment-image';
      image.src = previewUrl;
      image.alt = file.name;
      card.appendChild(image);
    } else {
      var icon = document.createElement('span');
      icon.className = 'sent-attachment-icon';
      icon.textContent = /\.pdf$/i.test(file.name || '') ? 'PDF' :
        /\.xlsx$/i.test(file.name || '') ? 'XLSX' : /\.xlsb$/i.test(file.name || '') ? 'XLSB' : /\.xlsm?$/i.test(file.name || '') ? 'XLS' : /\.tsv$/i.test(file.name || '') ? 'TSV' : /\.csv$/i.test(file.name || '') ? 'CSV' : 'DOC';
      card.appendChild(icon);
    }
    var label = document.createElement('span');
    label.className = 'sent-attachment-name';
    label.textContent = file.name;
    card.appendChild(label);
    if (/\.pdf$/i.test(file.name || '')) {
      var review = document.createElement('button');
      review.type = 'button'; review.className = 'paper-review-btn'; review.textContent = '解析引用';
      review.onclick = function() { reviewUploadedPdf(file, card); };
      card.appendChild(review);
    }
    gallery.appendChild(card);
  });
  contentEl.appendChild(gallery);
}

var paperReviewSessionVersion = 0;
window.addEventListener('citrus:session-changed', function() { paperReviewSessionVersion += 1; });
async function reviewUploadedPdf(file, card) {
  var epoch = authEpoch, sid = sessionId, sessionVersion = paperReviewSessionVersion;
  function ownsReview() {
    return epoch === authEpoch && sid === sessionId && sessionVersion === paperReviewSessionVersion &&
      card.querySelector('.paper-review-result') === panel;
  }
  if (!sid || !currentAccount) return;
  var old = card.querySelector('.paper-review-result'); if (old) old.remove();
  var panel = document.createElement('div'); panel.className = 'paper-review-result'; panel.textContent = '正在提取引用…'; card.appendChild(panel);
  try {
    var payload = {name:file.name, data:await fileAsDataUrl(file)};
    if (!ownsReview()) return;
    var response = await accountFetch('/api/v2/session/' + encodeURIComponent(sid) + '/paper-review', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    var result = await response.json(); if (!ownsReview()) return; if (!response.ok) throw new Error(result.detail || '解析失败');
    panel.textContent = '';
    var status = document.createElement('div'); status.textContent = result.status === 'incomplete' ? result.reason : ((result.references || []).length + ' 条参考文献；' + (result.reason || '正文编号引用已标亮。'));
    panel.appendChild(status);
    if (result.highlighted_pdf_url) {
      var artifactId = result.highlighted_pdf_url.split('/').pop();
      var link = document.createElement('a'); link.href = '/api/v2/session/' + encodeURIComponent(sid) + '/paper-preview/' + encodeURIComponent(artifactId); link.target = '_blank'; link.textContent = '预览高亮 PDF'; panel.appendChild(link);
      var download = document.createElement('a'); download.href = result.highlighted_pdf_url; download.textContent = ' · 下载副本'; panel.appendChild(download);
    }
    var auditBase = result.audit_id ? '/api/v2/session/' + encodeURIComponent(sid) + '/reference-audits/' + encodeURIComponent(result.audit_id) : '';
    var rowNodes = {}, rowIndex = {};
    var summary = document.createElement('div'); summary.className = 'paper-audit-summary';
    var controls = document.createElement('div'); controls.className = 'paper-audit-controls';
    function auditButton(label, handler) {
      var button = document.createElement('button'); button.type = 'button'; button.textContent = label;
      button.onclick = handler; controls.appendChild(button); return button;
    }
    function auditLink(label, format) {
      var a = document.createElement('a'); a.href = auditBase + '/export?format=' + format;
      a.textContent = label; a.setAttribute('download', ''); controls.appendChild(a); return a;
    }
    function auditStatusText(row) {
      var state = (row && row.result && row.result.status) || (row && row.status) || 'not_checked';
      return ({metadata_matched:'已匹配', metadata_mismatch:'字段冲突', metadata_uncertain:'字段待确认',
        title_candidate:'标题候选待确认', web_candidate:'网页候选待确认',
        not_found_in_checked_registries:'登记库未查到', network_error:'联网失败，可重试',
        not_checked:'未核验'})[state] || state;
    }
    function paintFields(target, row) {
      var fields = row && row.result && row.result.fields || {};
      var labels = {doi:'DOI', title:'标题', authors:'作者', year:'年份', journal:'期刊', volume:'卷', issue:'期', pages:'页码', article_number:'文章号'};
      var states = {conflicting:'冲突', missing:'登记字段缺失', uncertain:'待确认'};
      target.replaceChildren();
      var problems = Object.keys(labels).filter(function(key) { return fields[key] && states[fields[key].status]; });
      target.hidden = !problems.length;
      if (!problems.length) return;
      var heading = document.createElement('summary');
      heading.textContent = problems.map(function(key) { return labels[key] + '：' + states[fields[key].status]; }).join('；');
      target.appendChild(heading);
      problems.forEach(function(key) {
        var check = fields[key], detail = document.createElement('div');
        detail.textContent = labels[key] + '（' + states[check.status] + '）：提交 ' + String(check.submitted || '—').slice(0, 500) +
          '；登记 ' + String(check.registered || '—').slice(0, 500) + (check.reason ? '；' + String(check.reason).slice(0, 200) : '');
        target.appendChild(detail);
      });
    }
    var resumeButton = null, exportJson = null, exportXlsx = null, cancelButton = null, verifyAll = null;
    var audit = null, running = false, cancelling = false, stopRequested = false, auditRevision = 0;
    function paintAudit() {
      if (!ownsReview()) return;
      var view = audit.view || {}, coverage = view.coverage || {};
      var reasons = {deadline:'本批时间已到，可继续核验', budget_exhausted:'请求预算已用完',
        network_disabled:'联网已关闭', already_running:'已有核验正在运行，请稍后继续',
        cancelled:'已取消，已完成的记录保留', cancelled_use_resume:'已取消，可继续核验',
        lease_lost:'运行状态已改变，请继续核验'};
      summary.textContent = '已核验 ' + (coverage.processed || 0) + '/' + (coverage.total || 0) +
        (coverage.pending ? '，待核验 ' + coverage.pending : '') +
        (coverage.retryable ? '，可重试 ' + coverage.retryable : '') +
        (view.extraction && view.extraction.status !== 'complete' ? '；条目提取不完整，仅核验已提取部分' : '') +
        (audit.note ? '；' + audit.note : reasons[audit.reason] ? '；' + reasons[audit.reason] : '');
      Object.keys(rowNodes).forEach(function(key) {
        var row = rowIndex[key], nodes = rowNodes[key];
        nodes.badge.textContent = auditStatusText(row);
        paintFields(nodes.fields, row);
      });
      var remaining = coverage.pending > 0 || coverage.retryable > 0;
      verifyAll.disabled = running || cancelling || !remaining;
      resumeButton.hidden = !remaining;
      resumeButton.disabled = running || cancelling || !remaining;
      cancelButton.disabled = cancelling || (!running && !remaining);
      if (exportJson) exportJson.hidden = !(coverage.processed > 0);
      if (exportXlsx) exportXlsx.hidden = exportJson.hidden;
    }
    function mergeAudit(view) {
      audit.view = view;
      (view.rows || []).forEach(function(row) { rowIndex[row.record_index] = row; });
    }
    async function refreshAudit(revision) {
      if (!ownsReview() || revision !== auditRevision) return null;
      var response = await accountFetch(auditBase + '?limit=5000', {headers:{'Accept':'application/json'}});
      var detail = await response.json();
      if (!ownsReview() || revision !== auditRevision) return null;
      if (!response.ok) throw new Error(detail.detail || '无法读取核验进度');
      rowIndex = {}; mergeAudit(detail); paintAudit(); return detail;
    }
    if (auditBase) {
      audit = {id: result.audit_id, view: result.audit || {}, reason:'', note:''};
      verifyAll = auditButton('核验全部', function() { return runBatches(false); });
      resumeButton = auditButton('继续核验', function() { return runBatches(true); });
      cancelButton = auditButton('取消', async function() {
        if (!ownsReview() || cancelling) return;
        // Fence both an outstanding run response and the next automatic batch.
        stopRequested = true; cancelling = true;
        var revision = ++auditRevision;
        audit.note = '正在取消…'; paintAudit();
        try {
          var response = await accountFetch(auditBase + '/cancel', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
          var cancelled = await response.json();
          if (!ownsReview() || revision !== auditRevision) return;
          if (!response.ok) throw new Error(cancelled.detail || '请稍后重试');
          mergeAudit(cancelled); audit.reason = 'cancelled'; audit.note = '';
          await refreshAudit(revision);
        } catch (error) {
          if (ownsReview() && revision === auditRevision) audit.note = '取消失败：' + (error.message || '请稍后重试');
        } finally {
          if (ownsReview() && revision === auditRevision) { cancelling = false; paintAudit(); }
        }
      });
      exportJson = auditLink('导出 JSON', 'json');
      exportXlsx = auditLink('导出 XLSX', 'xlsx');
      panel.appendChild(summary); panel.appendChild(controls);
      async function runBatches(resume) {
        if (!ownsReview() || running || cancelling) return;
        var batches = 0, revision = ++auditRevision;
        running = true; stopRequested = false; audit.note = ''; audit.reason = ''; paintAudit();
        try {
          while (ownsReview() && !stopRequested && batches < 30) {
            batches += 1;
            var response = await accountFetch(auditBase + '/run', {method:'POST',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({max_rows:20, time_budget:45, resume:resume})});
            var run = await response.json();
            if (!ownsReview() || stopRequested || revision !== auditRevision) return;
            if (run.coverage) { mergeAudit(run); audit.reason = run.run_reason || ''; paintAudit(); }
            if (!response.ok) throw new Error(run.detail || (response.status === 409 ? '运行状态冲突，请稍后继续核验' : '核验失败'));
            resume = false;
            // Run responses contain only the first 100 records. Read the full
            // bounded inventory so later record_index badges update each batch.
            await refreshAudit(revision);
            if (!ownsReview() || stopRequested || revision !== auditRevision) return;
            var coverage = audit.view.coverage || {};
            if (!coverage.pending || (run.run_reason && run.run_reason !== 'batch_finished')) break;
          }
          if (batches === 30 && (audit.view.coverage || {}).pending) audit.note = '本次批次已结束，可继续核验';
        } catch (error) {
          if (ownsReview() && !stopRequested && revision === auditRevision) audit.note = '核验中断：' + (error.message || '请稍后重试');
        } finally {
          if (ownsReview()) { running = false; paintAudit(); }
        }
      }
      refreshAudit(auditRevision).catch(function(error) {
        if (ownsReview() && auditRevision === 0) { audit.note = '进度读取失败：' + (error.message || '请稍后重试'); paintAudit(); }
      });
    }
    (result.references || []).forEach(function(ref) {
      var row = document.createElement('div'); row.className = 'paper-reference-row';
      var text = document.createElement('span'); text.textContent = '[' + ref.number + '] ' + ref.text; row.appendChild(text);
      if (auditBase && typeof ref.record_index === 'number') {
        var badge = document.createElement('span'); badge.className = 'paper-audit-state'; badge.textContent = '未核验';
        var fields = document.createElement('details'); fields.className = 'paper-audit-fields'; fields.hidden = true;
        rowNodes[ref.record_index] = {badge:badge, fields:fields}; row.appendChild(badge); row.appendChild(fields);
      }
      if (ref.doi) {
        var get = document.createElement('button'); get.textContent = '找开放 PDF';
        get.onclick = async function() { if (!ownsReview()) return; get.disabled = true; var r = await accountFetch('/api/v2/session/' + encodeURIComponent(sid) + '/citation-open-pdf', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({doi:ref.doi})}); var d = await r.json(); if (!ownsReview()) return; if (d.download_url) { get.textContent = '下载 PDF'; get.onclick = function(){ if (!ownsReview()) return; window.open(d.download_url, '_blank'); }; } else get.textContent = d.message || d.status; };
        row.appendChild(get);
      }
      panel.appendChild(row);
    });
    if (audit) { mergeAudit(audit.view); paintAudit(); }
  } catch (e) { if (!ownsReview()) return; panel.textContent = '解析失败：' + (e.message || '请稍后重试'); }
}

function clearSentAttachmentPreviewUrls() {
  _sentAttachmentPreviewUrls.forEach(function(url) { URL.revokeObjectURL(url); });
  _sentAttachmentPreviewUrls = [];
}

function fileAsDataUrl(file) {
  var epoch = authEpoch;
  return new Promise(function(resolve, reject) {
    var reader = new FileReader();
    accountFileReaders.add(reader);
    function finish(error) {
      accountFileReaders.delete(reader);
      reader.onload = reader.onerror = reader.onabort = null;
      if (epoch !== authEpoch) error = staleAccountError();
      if (error) reject(error); else resolve(String(reader.result || ''));
    }
    reader.onload = function() { finish(); };
    reader.onerror = function() { finish(new Error('无法读取附件：' + file.name)); };
    reader.onabort = function() { finish(staleAccountError()); };
    try { reader.readAsDataURL(file); } catch (error) { finish(error); }
  });
}
if (typeof marked.setOptions === 'function') {
  marked.setOptions({ breaks: true });
}

/* ─── XSS 防护: 所有动态文本入 innerHTML 前转义 (v8.3.3) ─── */
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
/* v8.4.3: 代码块内容只转义 & < >（引号安全——处于 <pre><code> 标签内部，
   全量转义会把代码里的 ' 显示成 &#39;，如 5'上游调控区） */
function displayToolName(name) {
  var names = {
    deepseek_search: '网页搜索', deepseek_fetch: '读取网页',
    deepseek_fetch_paper: '获取论文全文',
    local_hybrid_search: '本地检索', local_bm25_search: '本地检索',
    local_dense_search: '本地检索', citrus_rag_search: '本地检索',
    read_account_file: '查阅资料', update_task_plan: '更新工作计划',
    draw_svg: '绘图',
    inspect_uploaded_table: '读取表格', audit_uploaded_table: '数据审查',
    audit_uploaded_data_bundle: '数据审查', audit_uploaded_data: '数据审查',
    verify_uploaded_references: '文献核验', fetch_and_audit_open_supplements: '获取补充数据',
    export_literature_evidence: '导出文献证据', export_paper_analysis: '导出论文解读',
    export_experiment_design: '导出实验方案', save_research_note: '保存课题记录',
    recall_research_notes: '查阅课题记录', export_research_notes: '导出课题记录',
    export_corpus_gap_report: '导出语料清单'
  };
  var value = String(name || '');
  if (names[value]) return names[value];
  if (/^[\u3400-\u9fff·\s]{2,16}$/.test(value)) return value;
  return '科研工具';
}
function toggleToolTrace(id, trigger) {
  var panel = document.getElementById(String(id || ''));
  if (!panel) return;
  var open = panel.style.display === 'none' || !panel.style.display;
  panel.style.display = open ? 'block' : 'none';
  if (trigger) trigger.setAttribute('aria-expanded', String(open));
}
function escapeHtmlCode(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function renderSvgBlock(source) {
  var raw = String(source || '').trim();
  if (!raw || raw.length > 100000 || /<!DOCTYPE|<!ENTITY/i.test(raw)) {
    return '<div class="svg-error">SVG 图形无效或尺寸超过限制。</div>';
  }
  try {
    var doc = new DOMParser().parseFromString(raw, 'image/svg+xml');
    var root = doc.documentElement;
    var ns = 'http://www.w3.org/2000/svg';
    if (!root || root.localName.toLowerCase() !== 'svg' ||
        (root.namespaceURI && root.namespaceURI !== ns) ||
        doc.getElementsByTagName('parsererror').length) throw new Error('invalid SVG');
    var allowedTags = new Set(['svg','g','path','rect','circle','ellipse','line','polyline','polygon',
      'text','tspan','title','desc','defs','lineargradient','radialgradient','stop','clippath','marker',
      'use','symbol','filter','fegaussianblur','feoffset','feflood','fecomposite','femerge',
      'femergenode','fecolormatrix','feblend','fecomponenttransfer','fefunca','fefuncr','fefuncg','fefuncb']);
    var attrNames = {
      'viewbox':'viewBox','preserveaspectratio':'preserveAspectRatio','stroke-width':'stroke-width',
      'stroke-linecap':'stroke-linecap','stroke-linejoin':'stroke-linejoin','stroke-dasharray':'stroke-dasharray',
      'stroke-dashoffset':'stroke-dashoffset','fill-opacity':'fill-opacity','stroke-opacity':'stroke-opacity',
      'font-family':'font-family','font-size':'font-size','font-weight':'font-weight','font-style':'font-style','text-anchor':'text-anchor',
      'dominant-baseline':'dominant-baseline','gradientunits':'gradientUnits','gradienttransform':'gradientTransform',
      'stop-color':'stop-color','stop-opacity':'stop-opacity','clippathunits':'clipPathUnits',
      'markerwidth':'markerWidth','markerheight':'markerHeight','markerunits':'markerUnits',
      'marker-start':'marker-start','marker-mid':'marker-mid','marker-end':'marker-end','aria-label':'aria-label',
      'clip-path':'clip-path','role':'role','id':'id','x':'x','y':'y','x1':'x1','y1':'y1','x2':'x2','y2':'y2','cx':'cx','cy':'cy',
      'r':'r','rx':'rx','ry':'ry','width':'width','height':'height','d':'d','points':'points',
      'fill':'fill','stroke':'stroke','opacity':'opacity','transform':'transform','offset':'offset',
      'style':'style','class':'class','href':'href','xlink:href':'href','filter':'filter'
    };
    var safeStyleProps = new Set(['fill','stroke','stroke-width','stroke-linecap','stroke-linejoin',
      'stroke-dasharray','stroke-dashoffset','fill-opacity','stroke-opacity','opacity','font-family',
      'font-size','font-weight','font-style','text-anchor','dominant-baseline','stop-color','stop-opacity',
      'clip-path','filter','marker-start','marker-mid','marker-end']);
    function safeStyle(value) {
      var kept = [];
      String(value).split(';').forEach(function(part) {
        var colon = part.indexOf(':');
        if (colon < 1) return;
        var name = part.slice(0, colon).trim().toLowerCase();
        var val = part.slice(colon + 1).trim();
        if (!safeStyleProps.has(name) || !val || /[<>\\{}]/.test(val) ||
            /expression|javascript:|data:|@import/i.test(val)) return;
        if (/url\s*\(/i.test(val) && !/^url\(\s*#[A-Za-z][\w:.-]{0,100}\s*\)$/i.test(val)) return;
        kept.push(name + ':' + val);
      });
      return kept.join(';');
    }
    var count = 0;
    function copyNode(node) {
      if (++count > 5000) throw new Error('SVG complexity limit');
      if (node.nodeType === 3 || node.nodeType === 4) return document.createTextNode(node.nodeValue || '');
      if (node.nodeType !== 1 || (node.namespaceURI && node.namespaceURI !== ns) ||
          !allowedTags.has(node.localName.toLowerCase())) return null;
      var out = document.createElementNS(ns, node.localName);
      for (var i = 0; i < node.attributes.length; i++) {
        var attr = node.attributes[i], key = attr.name.toLowerCase(), canonical = attrNames[key];
        if (!canonical) continue;
        var value = attr.value;
        if (value.length > 4000 || /[<>\u0000-\u001f]/.test(value)) continue;
        if (canonical === 'style') { value = safeStyle(value); if (!value) continue; }
        if (canonical === 'class' && !/^[A-Za-z0-9 _-]{1,200}$/.test(value)) continue;
        if (canonical === 'href' && !/^#[A-Za-z][\w:.-]{0,100}$/.test(value)) continue;
        if (canonical === 'id' && !/^[A-Za-z][\w:.-]{0,100}$/.test(value)) continue;
        if (/url\s*\(/i.test(value) && !/^url\(\s*#[A-Za-z][\w:.-]{0,100}\s*\)$/i.test(value)) continue;
        if (/javascript:|data:/i.test(value)) continue;
        out.setAttribute(canonical, value);
      }
      for (var child = node.firstChild; child; child = child.nextSibling) {
        var safeChild = copyNode(child);
        if (safeChild) out.appendChild(safeChild);
      }
      return out;
    }
    var safe = copyNode(root);
    if (!safe.getAttribute('viewBox') && !safe.getAttribute('width')) {
      throw new Error('SVG needs a viewBox or width');
    }
    return '<figure class="svg-figure" role="group">' + new XMLSerializer().serializeToString(safe) + '</figure>';
  } catch (_) {
    return '<div class="svg-error">SVG 图形无法安全显示，请检查 SVG 代码。</div>';
  }
}

function renderMarkdownSafe(text) {
  // Accept bare SVG responses as well as fenced ```svg blocks.
  var svgBlocks = [];
  var src = String(text == null ? '' : text);
  function svgToken(svg) {
    var token = 'CITRUS_SAFE_SVG_' + svgBlocks.length + '_TOKEN';
    svgBlocks.push({ token: token, html: renderSvgBlock(svg) });
    return token;
  }
  src = src.replace(/```svg[ \t]*\n([\s\S]*?)```/gi, function(_, svg) { return svgToken(svg); });
  src = src.replace(/<svg\b[\s\S]*?<\/svg\s*>/gi, function(svg) { return svgToken(svg); });
  var renderer = new marked.Renderer();
  var origCode = renderer.code;
  renderer.code = function(code, lang) {
    // v8.4.8: 兼容 marked>=5 的 token 对象签名（code 可能为 {text:...}），
    // 否则代码块会渲染成 "[object Object]"
    if (code && typeof code === 'object') { lang = code.lang || ''; code = code.text || ''; }
    if (String(lang || '').trim().toLowerCase() === 'svg') return renderSvgBlock(code);
    var cls = lang ? ' class="lang-' + escapeHtml(lang) + '"' : '';
    return '<pre><code' + cls + '>' + escapeHtmlCode(code) + '</code></pre>';
  };
  renderer.codespan = function(code) {
    if (code && typeof code === 'object') { code = code.text || ''; }
    return '<code>' + escapeHtmlCode(code) + '</code>';
  };
  // SVG is accepted only through the fenced-block sanitizer; escape arbitrary raw HTML.
  renderer.html = function(html) { return escapeHtml(html); };
  // v10.0: 回答不使用删除线——防模型输出 ~~...~~ 被 marked 渲染成"横线划掉"观感
  src = src.replace(/~~/g, '');
  var rendered = marked.parse(src, { renderer: renderer, breaks: true });
  svgBlocks.forEach(function(block) {
    rendered = rendered.split('<p>' + block.token + '</p>').join(block.html);
    rendered = rendered.split(block.token).join(block.html);
  });
  // R15: 本地来源标识（paper_… / doc_…）在渲染后变成可点入口，点开本地原文。
  // 历史回答与本轮流式回答都走本函数，因此刷新前后行为一致。
  rendered = linkifyCorpusKeys(rendered);
  return rendered;
}

/* ─── R15: 本地来源标识 → 可点击（原文读取在 corpus_reader.js）────────────
 * 语料键形如 `paper_45bbcac3_000004_B_2017_Nature_Genetics_`（文献）或
 * `doc_Agrobacterium_Transformation_Method`（方法/专著/品种记录）。
 * 这里只把「已渲染 HTML 的文本节点」换成链接，不改任何回答文本内容。 */
/* 严格形状：文献键 = paper_<8位十六进制>_<6位序号>_<大写字母>_<年份>_<期刊slug…>，
 * 尾部可能以 `_` 结尾（期刊被截断时），必须整段吃进来否则 describe() 找不到原文；
 * 期刊 slug 会出现连续下划线（如 GigaByte__Hong_K）。方法/专著/品种键以 doc_ 开头。 */
var _CORPUS_KEY_RE = /\bpaper_[0-9a-f]{8}_\d{6}_[A-Z]_\d{4}_[A-Za-z0-9_]{2,}|\bdoc_[A-Za-z0-9][A-Za-z0-9_.:-]{2,180}/g;

function _corpusKeyOnly(text) {
  var t = String(text == null ? '' : text).trim();
  if (t.length < 12 || t.length > 200) return '';
  var re = new RegExp(_CORPUS_KEY_RE.source, 'g');
  if (!re.test(t)) return '';
  return t.replace(new RegExp(_CORPUS_KEY_RE.source, 'g'), '') === '' ? t : '';
}

function _corpusAnchor(key, innerHtml, extraClass) {
  return '<a class="corpus-key' + (extraClass ? ' ' + extraClass : '') +
    '" href="#" role="button" data-corpus-key="' + escapeHtml(key) +
    '" title="打开本地原文">' + innerHtml + '</a>';
}

function linkifyCorpusKeys(html) {
  var src = String(html == null ? '' : html);
  var stash = [];
  // 1) 模型常把键写成行内代码：`paper_...` → 整段就是键时，换成代码观感的链接。
  //    先换成占位符，避免第 2 步把刚生成的 <a> 里的文本再包一层 <a>（嵌套锚点会点不动）。
  src = src.replace(/<code>([^<]{8,200})<\/code>/g, function (m, inner) {
    var key = _corpusKeyOnly(inner);
    if (!key) return m;
    stash.push(_corpusAnchor(key, escapeHtmlCode(inner), 'corpus-key-code'));
    return '\u0000CR' + (stash.length - 1) + '\u0000';
  });
  // 2) 普通文本节点里的裸键（跳过标签内部，避免破坏属性）
  src = src.replace(/(^|>)([^<]+)(?=<|$)/g, function (m, lead, text) {
    var out = String(text).replace(new RegExp(_CORPUS_KEY_RE.source, 'g'), function (key) {
      return _corpusAnchor(key, key, '');
    });
    return lead + out;
  });
  // 3) 还原行内代码链接
  src = src.replace(/\u0000CR(\d+)\u0000/g, function (m, i) { return stash[+i]; });
  return src;
}

/* 单一代理监听：历史气泡重建、流式替换都不会重复绑定 */
document.addEventListener('click', function (ev) {
  var t = ev.target;
  var a = (t && t.closest) ? t.closest('a.corpus-key') : null;
  if (!a) return;
  ev.preventDefault();
  var key = a.getAttribute('data-corpus-key') || a.textContent || '';
  if (typeof window.openCorpusSource === 'function') {
    window.openCorpusSource(key);
  } else {
    console.warn('[corpus] 原文阅读器未加载');
  }
});

/* ─── v8.4.3: 运行时配置（上下文预算/权限模式单一来源，删本地硬编码）─── */
var _runtimeConfig = null;
var _pendingPermission = null;
function _ctxMaxTokens() {
  if (_runtimeConfig && _runtimeConfig.context && _runtimeConfig.context.max_tokens) {
    return _runtimeConfig.context.max_tokens;
  }
  return 1000000;
}
var _permWaitSec = 90;   /* v8.4.12: 审批卡片超时提示（config 下发，缺省 90s） */
/* v8.5.0 开源版：未配置 API Key 时显示引导遮罩 */
function maybeShowApiSetup(cfg) {
  if (!currentAccount || currentAccount.role !== 'admin') return;
  var overlay = document.getElementById('apiSetupOverlay');
  if (!overlay) return;
  fillModelConfigFields(cfg);
  var closeBtn = document.getElementById('settingsCloseBtn');
  if (cfg && (cfg.any_model_provider_configured || cfg.has_api_key)) {
    if (closeBtn) closeBtn.style.display = 'inline-block';
  } else {
    overlay.style.display = 'flex';
    if (closeBtn) closeBtn.style.display = 'none';
    var input = document.getElementById('apiKeyInput');
    if (input) input.focus();
  }
}
var _providerModelLists = {};
var _providerCatalog = {
  deepseek: { name: 'DeepSeek', base_url: 'https://api.deepseek.com', default_model: 'deepseek-v4-flash' },
  opencode_go: { name: 'OpenCode Go', base_url: 'https://opencode.ai/inference/go/openai/v1', default_model: 'deepseek-v4.1-flash' },
  zhipu: { name: '智谱 GLM', base_url: 'https://open.bigmodel.cn/api/paas/v4', default_model: 'glm-4.5-flash' },
  aliyun_bailian: { name: '阿里云百炼（通义千问）', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', default_model: 'qwen-plus' },
  custom_openai: { name: '其他 OpenAI 兼容服务', base_url: 'https://api.example.com/v1', default_model: '' }
};
var _defaultProviderCatalog = JSON.parse(JSON.stringify(_providerCatalog));
function loadProviderCatalog() {
  var epoch = authEpoch;
  accountFetch('/api/v2/config/providers').then(function(r) { if (epoch !== authEpoch) return; return r.json(); }).then(function(data) { if (epoch !== authEpoch) return;
    var select = document.getElementById('mainProviderSelect');
    if (!select || !Array.isArray(data.providers)) return;
    var selected = select.value;
    _providerCatalog = {};
    select.innerHTML = '';
    data.providers.forEach(function(provider) {
      _providerCatalog[provider.id] = provider;
      var option = document.createElement('option');
      option.value = provider.id;
      option.textContent = provider.name + (provider.configured ? ' · 已配置' : '');
      select.appendChild(option);
    });
    if (_providerCatalog[selected]) select.value = selected;
    else if (_runtimeConfig && _runtimeConfig.model && _providerCatalog[_runtimeConfig.model.provider_id])
      select.value = _runtimeConfig.model.provider_id;
    renderConfiguredProviders();
    onProviderChange(true);
  }).catch(function() { if (epoch !== authEpoch) return;});
}
function hasConfiguredProvider(providerId) {
  if ((_providerCatalog[providerId] || {}).configured) return true;
  var ids = (_runtimeConfig && _runtimeConfig.configured_model_provider_ids) || [];
  return ids.indexOf(providerId) >= 0 || !!(_runtimeConfig && _runtimeConfig.has_api_key &&
    _runtimeConfig.model && _runtimeConfig.model.provider_id === providerId);
}
function renderConfiguredProviders() {
  var box = document.getElementById('configuredProvidersList');
  if (!box) return;
  var configured = Object.keys(_providerCatalog).map(function(id) {
    return _providerCatalog[id];
  }).filter(function(provider) { return provider.configured; });
  box.innerHTML = '';
  box.hidden = configured.length === 0;
  if (!configured.length) return;
  var title = document.createElement('div');
  title.className = 'configured-providers-title';
  title.textContent = '已配置服务商';
  box.appendChild(title);
  configured.forEach(function(provider) {
    var row = document.createElement('div');
    row.className = 'configured-provider-row';
    var name = document.createElement('span');
    name.className = 'configured-provider-name';
    name.textContent = provider.name;
    var status = document.createElement('span');
    status.className = 'configured-provider-state';
    status.textContent = provider.id === ((_runtimeConfig || {}).model || {}).provider_id ? '● 当前使用' : '● 已保存';
    var edit = document.createElement('button');
    edit.type = 'button'; edit.className = 'configured-provider-edit'; edit.textContent = '编辑';
    edit.onclick = function() {
      var select = document.getElementById('mainProviderSelect');
      if (select) { select.value = provider.id; onProviderChange(true); }
    };
    row.appendChild(name); row.appendChild(status); row.appendChild(edit);
    box.appendChild(row);
  });
}
function onProviderChange(preserveConfiguredModel) {
  var id = document.getElementById('mainProviderSelect').value;
  var info = _providerCatalog[id] || _providerCatalog.deepseek;
  var hint = document.getElementById('providerEndpointHint');
  var baseInput = document.getElementById('mainApiBaseUrlInput');
  var models = document.getElementById('mainModelSelect');
  var priorProvider = document.getElementById('mainProviderSelect').dataset.lastProvider;
  if (!preserveConfiguredModel && priorProvider && priorProvider !== id) {
    var apiKeyField = document.getElementById('apiKeyInput');
    if (apiKeyField) apiKeyField.value = '';
    var apiKeyError = document.getElementById('apiKeyError');
    if (apiKeyError) apiKeyError.textContent = '';
  }
  document.getElementById('mainProviderSelect').dataset.lastProvider = id;
  if (hint) hint.textContent = 'API 地址：' + info.base_url;
  var keyInput = document.getElementById('apiKeyInput');
  if (keyInput) keyInput.placeholder = hasConfiguredProvider(id)
    ? '已保存；留空继续使用' : '输入服务商 API Key';
  var current = ((_runtimeConfig || {}).model || {});
  if (baseInput) baseInput.value = preserveConfiguredModel && current.provider_id === id && current.main_base_url
    ? current.main_base_url : info.base_url;
  if (baseInput) baseInput.readOnly = !info.custom_base_url;
  if (models) {
    var showCurrent = preserveConfiguredModel && current.provider_id === id && current.main;
    models.innerHTML = '';
    var option = document.createElement('option');
    if (showCurrent) {
      option.value = current.main;
      var freeModels = (info.free_models || []).map(function(x) { return String(x).toLowerCase(); });
      option.textContent = current.main + (freeModels.indexOf(String(current.main).toLowerCase()) >= 0 ? ' · 免费' : '') + '（当前配置）';
      models.appendChild(option);
      models.value = current.main;
    } else {
      option.value = '';
      option.textContent = hasConfiguredProvider(id) ? '点击“获取模型列表”选择模型' : '先填写 Key 并获取模型列表';
      models.appendChild(option);
    }
    models.disabled = true;
  }
  if (!preserveConfiguredModel) _providerModelLists[id] = null;
}
function fetchProviderModels() {
  var epoch = authEpoch;
  var providerId = document.getElementById('mainProviderSelect').value;
  var keyInput = document.getElementById('apiKeyInput');
  var key = (keyInput && keyInput.value || '').trim();
  var err = document.getElementById('apiKeyError');
  var btn = document.getElementById('providerModelsBtn');
  if (!key && !hasConfiguredProvider(providerId)) {
    if (err) err.textContent = '请填写所选服务商对应的 API Key'; return;
  }
  if (btn) btn.disabled = true;
  if (err) { err.className = 'api-error'; err.textContent = '正在向 ' + ((_providerCatalog[providerId] || {}).name || providerId) + ' 获取模型列表…'; }
  accountFetch('/api/v2/config/provider-models', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider_id: providerId, api_key: key,
      api_base_url: (document.getElementById('mainApiBaseUrlInput').value || '').trim() })
  }).then(function(r) { if (epoch !== authEpoch) return; return r.json().then(function(d) { if (epoch !== authEpoch) return; return { ok: r.ok, d: d }; }); })
    .then(function(res) { if (epoch !== authEpoch) return;
      if (!res.ok) throw new Error((res.d && res.d.detail) || '获取模型列表失败');
      var ids = res.d.models || [];
      _providerModelLists[providerId] = ids;
      var select = document.getElementById('mainModelSelect');
      if (select) {
        select.innerHTML = '';
        ids.forEach(function(id) {
          var opt = document.createElement('option'); opt.value = id;
          var freeModels = ((_providerCatalog[providerId] || {}).free_models || []).map(function(x) { return String(x).toLowerCase(); });
          opt.textContent = id + (freeModels.indexOf(String(id).toLowerCase()) >= 0 ? ' · 免费' : '');
          select.appendChild(opt);
        });
        select.disabled = ids.length === 0;
        var activeModel = ((_runtimeConfig || {}).model || {});
        var preferred = activeModel.provider_id === providerId ? activeModel.main : '';
        if (ids.indexOf(preferred) >= 0) select.value = preferred;
        else if (ids.indexOf((_providerCatalog[providerId] || {}).default_model) >= 0) select.value = _providerCatalog[providerId].default_model;
      }
      if (err) { err.className = 'api-warn'; err.textContent = '已取得 ' + ids.length + ' 个可用模型，请选择后保存。'; }
    }).catch(function(e) { if (epoch !== authEpoch) return;
      if (err) { err.className = 'api-error'; err.textContent = e.message; }
    }).finally(function() { if (epoch !== authEpoch) return; if (btn) btn.disabled = false; });
}
function saveProviderConfig() {
  var epoch = authEpoch;
  var err = document.getElementById('modelConfigError');
  var btn = document.getElementById('modelConfigSave');
  var providerId = document.getElementById('mainProviderSelect').value;
  var key = (document.getElementById('apiKeyInput').value || '').trim();
  var model = document.getElementById('mainModelSelect').value;
  var contextTokens = parseInt(document.getElementById('modelContextWindowInput').value, 10) || 0;
  if (!key && !hasConfiguredProvider(providerId)) {
    if (err) err.textContent = '请填写 Provider API Key'; return;
  }
  if (!_providerModelLists[providerId] || _providerModelLists[providerId].indexOf(model) < 0) {
    if (err) err.textContent = '请先获取模型列表并选择其中一个模型'; return;
  }
  if (btn) btn.disabled = true;
  if (err) { err.className = 'api-error'; err.textContent = '正在验证并保存…'; }
  accountFetch('/api/v2/config/provider', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider_id: providerId, api_key: key, main_model: model,
      api_base_url: (document.getElementById('mainApiBaseUrlInput').value || '').trim(),
      context_window_tokens: contextTokens })
  }).then(function(r) { if (epoch !== authEpoch) return; return r.json().then(function(d) { if (epoch !== authEpoch) return; return { ok: r.ok, d: d }; }); })
    .then(function(res) { if (epoch !== authEpoch) return;
      if (!res.ok) throw new Error((res.d && res.d.detail) || '保存失败');
      if (err) { err.className = 'api-warn'; err.textContent = '配置已保存：' + ((_providerCatalog[providerId] || {}).name) + ' / ' + model; }
      document.getElementById('apiKeyInput').value = '';
      loadRuntimeConfig();
    }).catch(function(e) { if (epoch !== authEpoch) return;
      if (err) { err.className = 'api-error'; err.textContent = e.message; }
    }).finally(function() { if (epoch !== authEpoch) return; if (btn) btn.disabled = false; });
}
function fillModelConfigFields(cfg) {
  var m = (cfg && cfg.model) || {};
  function setv(id, v) { var el = document.getElementById(id); if (el) el.value = v || ''; }
  setv('mainProviderSelect', m.provider_id || 'deepseek');
  var keyInput = document.getElementById('apiKeyInput');
  if (keyInput) keyInput.placeholder = hasConfiguredProvider(m.provider_id || 'deepseek')
    ? '已保存；留空继续使用' : '输入服务商 API Key';
  setv('modelContextWindowInput', m.context_window_tokens);
  onProviderChange(true);
  var web = (cfg && cfg.web_search) || {};
  var webProvider = document.getElementById('webSearchProviderSelect');
  if (webProvider) webProvider.value = web.provider || 'deepseek_search_fetch';
  onWebSearchProviderChange();
}
function onWebSearchProviderChange() {
  var provider = document.getElementById('webSearchProviderSelect');
  var key = document.getElementById('webSearchApiKeyInput');
  var fetchKey = document.getElementById('webFetchApiKeyInput');
  var fetchKeyLabel = document.getElementById('webFetchApiKeyLabel');
  var hint = document.getElementById('webSearchKeyHint');
  var web = ((_runtimeConfig || {}).web_search || {});
  var selected = provider ? provider.value : 'deepseek_search_fetch';
  if (key) key.placeholder = web.configured && web.provider === selected
    ? '已配置；留空继续使用' : '输入对应服务商的 Key';
  if (fetchKey) fetchKey.hidden = selected !== 'zhipu_web_search';
  if (fetchKeyLabel) fetchKeyLabel.hidden = selected !== 'zhipu_web_search';
  if (hint) hint.textContent = selected === 'zhipu_web_search'
    ? (web.deepseek_fetch_configured ? 'Web Fetch 已配置 DeepSeek Key。' : '搜索使用智谱；如需网页全文读取，可另填 DeepSeek Fetch Key。')
    : 'Search 与 Fetch 均使用 DeepSeek Search/Fetch Key。';
}
function saveWebSearchConfig() {
  var epoch = authEpoch;
  var provider = document.getElementById('webSearchProviderSelect').value;
  var input = document.getElementById('webSearchApiKeyInput');
  var key = (input && input.value || '').trim();
  var fetchKey = (document.getElementById('webFetchApiKeyInput').value || '').trim();
  var status = document.getElementById('webSearchConfigError');
  var btn = document.getElementById('webSearchConfigSave');
  var existing = ((_runtimeConfig || {}).web_search || {});
  if (!key && !(existing.configured && existing.provider === provider)) {
    if (status) status.textContent = '请填写所选服务商的搜索 Key';
    return;
  }
  if (btn) btn.disabled = true;
  if (status) { status.className = 'api-warn'; status.textContent = '正在保存…'; }
  accountFetch('/api/v2/config/web-apikey', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider_id: provider, api_key: key, fetch_api_key: fetchKey })
  }).then(function(r) { if (epoch !== authEpoch) return; return r.json().then(function(d) { if (epoch !== authEpoch) return; return { ok: r.ok, d: d }; }); })
    .then(function(res) { if (epoch !== authEpoch) return;
      if (!res.ok) throw new Error((res.d && res.d.detail) || '联网搜索配置保存失败');
      if (input) input.value = '';
      var fetchInput = document.getElementById('webFetchApiKeyInput');
      if (fetchInput) fetchInput.value = '';
      if (status) { status.className = 'api-warn'; status.textContent = '已保存'; }
      return accountFetch('/api/v2/config').then(function(r) { if (epoch !== authEpoch) return; return r.json(); });
    }).then(function(cfg) { if (epoch !== authEpoch) return;
      _runtimeConfig = cfg;
      onWebSearchProviderChange();
    }).catch(function(e) { if (epoch !== authEpoch) return;
      if (status) { status.className = 'api-error'; status.textContent = e.message; }
    }).finally(function() { if (epoch !== authEpoch) return; if (btn) btn.disabled = false; });
}
function openSettingsModal() {
  var overlay = document.getElementById('apiSetupOverlay');
  if (!overlay) return;
  overlay.style.display = 'flex';
  fillModelConfigFields(_runtimeConfig);
  var closeBtn = document.getElementById('settingsCloseBtn');
  if (closeBtn) closeBtn.style.display = (_runtimeConfig &&
    (_runtimeConfig.any_model_provider_configured || _runtimeConfig.has_api_key)) ? 'inline-block' : 'none';
  var keyInput = document.getElementById('apiKeyInput');
  if (!(_runtimeConfig && (_runtimeConfig.any_model_provider_configured || _runtimeConfig.has_api_key)) && keyInput) keyInput.focus();
}
function closeSettingsModal() {
  var overlay = document.getElementById('apiSetupOverlay');
  if (overlay) overlay.style.display = 'none';
}
function loadRuntimeConfig() {
  var epoch = authEpoch;
  loadProviderCatalog();
  return accountFetch('/api/v2/config').then(function(r) { if (epoch !== authEpoch) return; return r.json(); })
    .then(function(cfg) { if (epoch !== authEpoch) return;
      _runtimeConfig = cfg;
      if (cfg.permission_wait_sec) _permWaitSec = cfg.permission_wait_sec;
      applyAppVersion();
      maybeShowApiSetup(cfg);
    })
    .catch(function() { if (epoch !== authEpoch) return; applyAppVersion(); });
}
/* v8.4.5: 版本单源——后端 /api/v2/config.version 下发，静态文案仅为离线回退 */
function _appVersion() {
  return (_runtimeConfig && _runtimeConfig.version) || '1.0';
}
function applyAppVersion() {
  var v = _appVersion();
  var badge = document.getElementById('versionBadge');
  if (badge) badge.textContent = 'v' + v;
  var w = document.getElementById('welcomeVersion');
  if (w) w.textContent = 'CitrusScholar v' + v;
}

/* ─── v8.4.3/8.4.12: 权限审批卡片（授权动作不写入对话历史）───
   v8.4.12: 全 CSS 变量驱动（浅/深色模式一致），会话窗口上方居中弹出 */
function showPermissionCard(data) {
  _pendingPermission = data;
  var existing = document.getElementById('permission-card');
  if (existing) existing.remove();
  var card = document.createElement('div');
  card.id = 'permission-card'; card.className = 'perm-card';
  card.setAttribute('role', 'region'); card.setAttribute('aria-label', '操作权限确认');
  var tool = data.tool_name || '';
  card.innerHTML = '<div class="perm-title">权限确认</div>' +
    '<div class="perm-tool">此操作需要你的确认：' + escapeHtml(displayToolName(tool)) + '</div>' +
    '<div class="perm-actions"><button class="primary" data-scope="once">允许本次</button>' +
    (data.approval_id ? '' : '<button data-scope="session">本会话允许</button><button data-scope="workspace">始终允许工作区</button>') +
    '<button data-decision="rejected">拒绝</button></div>' +
    '<div class="perm-hint" role="status">你可以明确允许或拒绝；未响应将在 ' + (_permWaitSec || 90) + ' 秒后过期。</div>';
  card.querySelectorAll('button').forEach(function(btn) {
    btn.onclick = function() { decidePermission(tool, btn.getAttribute('data-decision') || 'granted', btn.getAttribute('data-scope') || 'once'); };
  });
  document.body.appendChild(card);
}
function decidePermission(toolName, decision, scope) {
  var epoch = authEpoch, sid = sessionId, pending = _pendingPermission;
  var card = document.getElementById('permission-card');
  var canonical = pending && pending.job_id && pending.approval_id;
  var url = canonical ? '/api/v2/chat/jobs/' + encodeURIComponent(pending.job_id) + '/approvals/' + encodeURIComponent(pending.approval_id)
    : '/api/v2/permission/' + (decision === 'rejected' ? 'reject' : 'grant');
  var body = canonical ? {decision: decision} : {session_id: sid, tool_name: toolName, scope: scope};
  if (card) card.querySelectorAll('button').forEach(function(button) { button.disabled = true; });
  return accountFetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
    .then(async function(response) {
      var data = await response.json();
      if (epoch !== authEpoch || sid !== sessionId || _pendingPermission !== pending) return;
      if (!response.ok) throw new Error(data.detail && data.detail.message || '权限决定未保存，请重试。');
      if (card) card.remove(); _pendingPermission = null;
      // Approval continues the original execution; never replay the chat POST.
    }).catch(function(error) {
      if (epoch !== authEpoch || sid !== sessionId || _pendingPermission !== pending) return;
      if (card) {
        var hint = card.querySelector('.perm-hint'); if (hint) hint.textContent = error.message;
        card.querySelectorAll('button').forEach(function(button) { button.disabled = false; });
      }
    });
}
function grantPermission(toolName, scope) { return decidePermission(toolName, 'granted', scope); }
function rejectPermission(toolName) { return decidePermission(toolName, 'rejected', 'once'); }

/* ─── Theme（C: 三套主题循环：浅色清新 → 深色 → 卡通）─── */
var THEME_CYCLE = ['light', 'cartoon', 'dark'];
var THEME_LABEL = { light: '春日花笺', cartoon: '午后书房', dark: '夜林花语' };
(function() {
  var t = localStorage.getItem('citrus_theme') || 'light';
  if (THEME_CYCLE.indexOf(t) < 0) t = 'light';
  document.documentElement.setAttribute('data-theme', t);
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', syncThemePicker);
} else {
  setTimeout(syncThemePicker, 0);
}
})();

function mascotSay(name) {
  try { if (window.CitrusMascot) window.CitrusMascot.setState(name); } catch (_) {}
}

function syncThemePicker() {
  var cur = document.documentElement.getAttribute('data-theme') || 'light';
  var cards = document.querySelectorAll('[data-theme-pick]');
  for (var i = 0; i < cards.length; i++) {
    var on = cards[i].getAttribute('data-theme-pick') === cur;
    cards[i].classList.toggle('is-active', on);
    cards[i].setAttribute('aria-pressed', on ? 'true' : 'false');
  }
  var btn = document.getElementById('themeBtn');
  if (btn) btn.innerHTML = '<span aria-hidden="true">◐</span> 主题：' + (THEME_LABEL[cur] || THEME_LABEL.light);
}

/* 主题切换只改 data-theme 与背景变量：不重建 DOM，不打断进行中的请求，
   也不重置吉祥物状态（C 批与 D 批解耦）。 */
function pickTheme(name) {
  if (THEME_CYCLE.indexOf(name) < 0) return;
  document.documentElement.setAttribute('data-theme', name);
  try { localStorage.setItem('citrus_theme', name); } catch (e) {}
  syncThemePicker();
  setAccountMenuOpen(false);
}

function toggleTheme() {
  var html = document.documentElement;
  var cur = html.getAttribute('data-theme') || 'light';
  var idx = THEME_CYCLE.indexOf(cur);
  var next = THEME_CYCLE[(idx + 1) % THEME_CYCLE.length];
  html.setAttribute('data-theme', next);
  localStorage.setItem('citrus_theme', next);
  var btn = document.getElementById('themeBtn');
  if (btn) btn.innerHTML = '<span aria-hidden="true">◐</span> 主题：' + THEME_LABEL[next];
}
document.addEventListener('DOMContentLoaded', function() {
  var btn = document.getElementById('themeBtn');
  var cur = document.documentElement.getAttribute('data-theme') || 'light';
  if (btn) btn.innerHTML = '<span aria-hidden="true">◐</span> 主题：' + (THEME_LABEL[cur] || '浅色清新');
});

/* ─── AbortController for SSE (cancel inflight on new send) ─── */
var currentAbortController = null;
var activeTurnCancel = null;

/* ─── Plan Mode state (synced from server flags) ─── */
var planModeEnabled = true;

function setSendDisabled(val) {
  var btn = document.getElementById('send-btn');
  var stopBtn = document.getElementById('stop-btn');
  // v8.7: 运行中彻底隐藏发送按钮（此前仅 disabled——两个绝对定位按钮
  // 同位置叠加造成视觉重叠）；恢复时再显示。停止按钮反向切换。
  if (btn) { btn.disabled = val; btn.style.display = val ? 'none' : 'flex'; }
  if (stopBtn) stopBtn.style.display = val ? 'flex' : 'none';
}

// S1: 本轮任务 id（后端首帧 `job` 事件给出）。停止时用它精确取消这一个任务，
// 而不是把会话里所有任务一起停掉。
var currentJobId = '';

/* v8.4.11 用户中断（书中 §4.7.6 取消式处理）：
   1) abort SSE 流（服务端普通问答在断开时取消 graph 任务）
   2) POST /api/v2/chat/cancel 兜底取消该会话所有 running job
      （覆盖 write 断连保活任务——此类任务客户端断开后仍在后台执行）
   停止后可修改输入框内容直接重新发送（新请求 = 新 job）。 */
function stopCurrentTask() {
  var epoch = authEpoch;
  // 停止请求与终态分开；任务回放确认后才显示已停止。
  // 吉祥物：取消后不能继续演"翻书/敲字"，直接回到待机。
  try { if (window.CitrusMascot) CitrusMascot.setState('idle', { force: true }); } catch (_) {}
  if (activeTurnCancel) activeTurnCancel('user');
  else if (currentAbortController) {
    currentAbortController.abort();
    currentAbortController = null;
  }
  var cancelJobId = currentJobId;
  currentJobId = '';
  if (cancelJobId) {
    try {
      accountFetch('/api/v2/chat/jobs/' + encodeURIComponent(cancelJobId) + '/cancel', {
        method: 'POST',
      }).then(function (r) { if (epoch !== authEpoch) return; return r.ok ? r.json() : null; }).then(function (d) { if (epoch !== authEpoch) return;
        if (d && d.accepted === false && d.note !== 'already_finished') {
          showBanner('停止请求已发送，但服务端未确认终止该任务。');
        }
      }).catch(function () { if (epoch !== authEpoch) return;});
    } catch (e) {}
  } else if (sessionId) {
    try {
      accountFetch('/api/v2/chat/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId }),
      }).then(function (r) { if (epoch !== authEpoch) return; return r.ok ? r.json() : null; }).then(function (d) { if (epoch !== authEpoch) return;
        if (!d) return;
        // 服务端是否真的接受了停止：in_process 才算"已终止执行"；
        // not_in_this_process 说明任务不在这台实例上（多实例/已重启），
        // 此时只能如实说"请求已记录，未确认终止"，不能假装已停止。
        var rows = d.results || [];
        var anywhere = false;
        for (var i = 0; i < rows.length; i++) {
          if (rows[i] && rows[i].target === 'in_process') { anywhere = true; }
        }
        if (rows.length === 0) {
          showBanner('停止请求已发送：该会话当前没有正在执行的任务。');
        } else if (!anywhere) {
          showBanner('停止请求已记录，但未能在本实例确认终止；若仍在运行请稍后再试。');
        }
      }).catch(function() { if (epoch !== authEpoch) return;});
    } catch (e) {}
  }
  setSendDisabled(false);
  try { userInput.focus(); } catch (e) {}
  userInput.placeholder = '有什么想做的？';
}

/* ─── Sidebar ─── */
var _sidebarMap = { session: 'session-list' };
var _sidebarSections = ['session'];

function setSidebarVisible(visible) {
  var shell = document.getElementById('mainContainer');
  var button = document.getElementById('sidebarToggleBtn');
  if (!shell || !button) return;
  shell.classList.toggle('sidebar-hidden', !visible);
  button.setAttribute('aria-expanded', visible ? 'true' : 'false');
  button.setAttribute('aria-label', visible ? '隐藏会话侧栏' : '显示会话侧栏');
  button.title = visible ? '隐藏会话侧栏' : '显示会话侧栏';
  localStorage.setItem('citrus_sidebar_visible', visible ? '1' : '0');
}

function toggleSidebar() {
  var shell = document.getElementById('mainContainer');
  setSidebarVisible(!!shell && shell.classList.contains('sidebar-hidden'));
}

(function restoreSidebarVisibility() {
  var saved = localStorage.getItem('citrus_sidebar_visible');
  setSidebarVisible(saved === null ? window.innerWidth > 1024 : saved === '1');
})();

// P2-1: 侧栏宽度可拖拽（240–480px），双击复位 300px，方向键 ±16px
var SIDEBAR_MIN = 240, SIDEBAR_MAX = 480, SIDEBAR_DEFAULT = 300;
function applySidebarWidth(width, persist) {
  var shell = document.getElementById('mainContainer');
  var value = Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, Math.round(width)));
  if (shell) shell.style.setProperty('--sidebar-w', value + 'px');
  if (persist) { try { localStorage.setItem('citrus_sidebar_width', String(value)); } catch (err) {} }
  return value;
}
(function restoreSidebarWidth() {
  var raw = null;
  try { raw = localStorage.getItem('citrus_sidebar_width'); } catch (err) {}
  var n = raw ? parseInt(raw, 10) : NaN;
  applySidebarWidth(isFinite(n) ? n : SIDEBAR_DEFAULT, false);
})();
(function bindSidebarResizer() {
  var handle = document.getElementById('sidebarResizer');
  var shell = document.getElementById('mainContainer');
  var sidebar = document.getElementById('appSidebar');
  if (!handle || !shell || !sidebar) return;
  var dragging = false, startX = 0, startW = 0;
  handle.addEventListener('pointerdown', function(event) {
    if (event.button !== 0) return;
    dragging = true; startX = event.clientX;
    startW = sidebar.getBoundingClientRect().width;
    shell.classList.add('sidebar-resizing');
    try { handle.setPointerCapture(event.pointerId); } catch (err) {}
    event.preventDefault();
  });
  handle.addEventListener('pointermove', function(event) {
    if (!dragging) return;
    applySidebarWidth(startW + (event.clientX - startX), false);
  });
  function finish(event) {
    if (!dragging) return;
    dragging = false;
    shell.classList.remove('sidebar-resizing');
    try { handle.releasePointerCapture(event.pointerId); } catch (err) {}
    applySidebarWidth(sidebar.getBoundingClientRect().width, true);
    setSidebarVisible(true);
  }
  handle.addEventListener('pointerup', finish);
  handle.addEventListener('pointercancel', finish);
  handle.addEventListener('dblclick', function() { applySidebarWidth(SIDEBAR_DEFAULT, true); });
  handle.addEventListener('keydown', function(event) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    var current = sidebar.getBoundingClientRect().width || SIDEBAR_DEFAULT;
    applySidebarWidth(current + (event.key === 'ArrowRight' ? 16 : -16), true);
  });
})();

function applySidebar(active) {
  for (var i = 0; i < _sidebarSections.length; i++) {
    var s = _sidebarSections[i];
    var el = document.getElementById(_sidebarMap[s]);
    var icon = document.getElementById(s + '-toggle');
    var sec = document.getElementById(s + '-section');
    if (!el || !icon || !sec) continue;
    var isActive = (active === s);
    el.classList.toggle('collapsed', !isActive);
    icon.classList.toggle('collapsed', !isActive);
    sec.classList.toggle('expanded', isActive);
  }
  localStorage.setItem('citrus_sidebar_active', active || '');
}

function toggleSection(section) {
  var el = document.getElementById(_sidebarMap[section]);
  if (!el) return;
  var isCollapsed = el.classList.contains('collapsed');
  applySidebar(isCollapsed ? section : null);
}

/* 会话列表默认展开。 */
applySidebar('session');

/* ─── v8.9 会话管理（列表/切换/新建/重命名/软删除）+ 工作区文件 ─── */
var _sessions = [];
var _sessionsVisible = 20;   // v8.9: 默认只显示最近 20 个，加载更多展开

function loadSessions() {
  var epoch = authEpoch;
  return accountFetch('/api/v2/sessions').then(function (r) { if (epoch !== authEpoch) return; return r.json(); }).then(function (d) { if (epoch !== authEpoch) return;
    _sessions = (d && d.sessions) || [];
    renderSessions();
    return true;
  }).catch(function () { if (epoch !== authEpoch) return; return false; });
}

/* v8.10e: 会话列表加载失败自动重试（网络抖动/服务重启竞态），最多 3 次后停止 */
function loadSessionsWithRetry(retries) {
  var epoch = authEpoch;
  var n = retries === undefined ? 3 : retries;
  return loadSessions().then(function (ok) { if (epoch !== authEpoch) return;
    if (!ok && n > 0) {
      return new Promise(function (res) {
        setTimeout(function () { res(epoch === authEpoch ? loadSessionsWithRetry(n - 1) : false); }, 600);
      });
    }
    return ok;
  });
}

function fmtSessionTime(iso) {
  if (!iso) return '';
  var d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  var now = new Date();
  var hh = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  if (d.toDateString() === now.toDateString()) return '今天 ' + hh;
  var yest = new Date(now); yest.setDate(now.getDate() - 1);
  if (d.toDateString() === yest.toDateString()) return '昨天 ' + hh;
  var mm = String(d.getMonth() + 1).padStart(2, '0');
  var dd = String(d.getDate()).padStart(2, '0');
  return mm + '-' + dd + ' ' + hh;
}

function renderSessions() {
  var box = document.getElementById('session-list');
  var count = document.getElementById('session-count');
  if (!box) return;
  // v8.9: 过滤空会话（无消息的测试/残留不显示）；当前活动会话总是显示
  var visible = [];
  for (var i = 0; i < _sessions.length; i++) {
    var s = _sessions[i];
    if (s.msg_count > 0 || s.session_id === sessionId) visible.push(s);
  }
  if (count) count.textContent = visible.length;
  if (visible.length === 0) {
    box.innerHTML = '<div class="empty-sidebar">暂无会话，点击左侧 ＋ 新建</div>';
    return;
  }
  var shown = visible.slice(0, _sessionsVisible);
  var buckets = [
    {key:'today',label:'今天',items:[]},
    {key:'yesterday',label:'昨天',items:[]},
    {key:'week',label:'最近 7 天',items:[]},
    {key:'older',label:'更早',items:[]}
  ];
  shown.forEach(function(s) {
    var key = sessionTimeBucket(s.updated_at);
    var bucket = buckets.find(function(b) { return b.key === key; }) || buckets[3];
    bucket.items.push(s);
  });
  function sessionItemHtml(s) {
    var active = s.session_id === sessionId ? ' active' : '';
    var title = escapeHtml(s.title || '新会话');
    return '<div class="session-item' + active + '">' +
      '<div class="session-title" onclick="switchSession(\'' + s.session_id + '\')" title="' + title + '">' + title + '</div>' +
      '<span class="session-time">' + fmtSessionTime(s.updated_at) + '</span>' +
      '<div class="session-ops">' +
      '<button class="session-op" onclick="sessionMenu(event,\'' + s.session_id + '\')" title="更多">⋯</button>' +
      '</div></div>';
  }
  var savedOpenGroups = ['today'];
  try {
    var parsedOpenGroups = JSON.parse(localStorage.getItem('citrus_session_groups_open') || 'null');
    if (Array.isArray(parsedOpenGroups)) savedOpenGroups = parsedOpenGroups;
  } catch (_) {}
  var html = '';
  buckets.forEach(function(bucket) {
    if (!bucket.items.length) return;
    var containsActive = bucket.items.some(function(s) { return s.session_id === sessionId; });
    var open = containsActive || savedOpenGroups.indexOf(bucket.key) >= 0;
    html += '<details class="session-group" data-session-group="' + bucket.key + '"' + (open ? ' open' : '') + '>' +
      '<summary>' + bucket.label + '<span class="session-group-count">' + bucket.items.length + '</span></summary>' +
      '<div class="session-group-content">' + bucket.items.map(sessionItemHtml).join('') + '</div></details>';
  });
  if (visible.length > _sessionsVisible) {
    html += '<button class="session-more" onclick="loadMoreSessions()">加载更多（还剩 ' +
      (visible.length - _sessionsVisible) + '）</button>';
  }
  box.innerHTML = html;
  box.querySelectorAll('.session-group').forEach(function(group) {
    group.addEventListener('toggle', function() {
      var openKeys = Array.from(box.querySelectorAll('.session-group[open]')).map(function(node) {
        return node.getAttribute('data-session-group');
      });
      localStorage.setItem('citrus_session_groups_open', JSON.stringify(openKeys));
    });
  });
}

function sessionTimeBucket(iso) {
  var d = new Date(iso || 0);
  if (isNaN(d.getTime())) return 'older';
  var now = new Date();
  var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  var date = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  var days = Math.floor((today - date) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 7) return 'week';
  return 'older';
}

function loadMoreSessions() {
  _sessionsVisible += 20;
  renderSessions();
}

function closeSessionMenu() {
  var m = document.querySelector('.session-menu');
  if (m) m.remove();
}

function sessionMenu(ev, sid) {
  ev.stopPropagation();
  closeSessionMenu();
  var item = ev.currentTarget.closest('.session-item');
  var menu = document.createElement('div');
  menu.className = 'session-menu';
  menu.innerHTML = '<div class="session-menu-item" onclick="sessionMenuAction(\'rename\',\'' + sid + '\')">✎ 重命名</div>' +
                   '<div class="session-menu-item danger" onclick="sessionMenuAction(\'delete\',\'' + sid + '\')">🗑 删除</div>';
  var rect = item ? item.getBoundingClientRect() : { bottom: 80, right: 160 };
  menu.style.top = (rect.bottom + 4) + 'px';
  menu.style.left = Math.max(8, rect.right - 130) + 'px';
  document.body.appendChild(menu);
  setTimeout(function () {
    document.addEventListener('click', function once() {
      closeSessionMenu();
      document.removeEventListener('click', once);
    });
  }, 0);
}

function sessionMenuAction(action, sid) {
  closeSessionMenu();
  if (action === 'rename') renameSession(sid);
  else if (action === 'delete') deleteSession(sid);
}

function switchSession(sid) {
  if (!sid || sid === sessionId) return;
  if (activeTurnCancel) activeTurnCancel('switch');
  activeTurnCancel = null; window._activeTurnStatusUpdater = null;
  if (currentAbortController) { currentAbortController.abort(); currentAbortController = null; }
  setSendDisabled(false);
  sessionId = sid;
  localStorage.setItem(sessionStorageKey(), sid);
  window.dispatchEvent(new CustomEvent('citrus:session-changed', {detail:{session_id:sid}}));
  clearSentAttachmentPreviewUrls();
  chatContainer.innerHTML = '';
  _historyMsgs = [];
  _historyTurns = null;
  _historyHasMore = false;
  roundHistory = [];
  _restoredGroups = null;   // v9.2: 切换会话清空恢复视图，等下一次 restore
  _ctxData = null;
  sidebarContent.innerHTML = '<div class="empty-sidebar">提交问题后文献将在此展示</div>';
  citationCount.textContent = '0';
  renderSessions();
  restoreSessionHistory();
  if (window.turnRail && window.turnRail.reload) window.turnRail.reload();
  userInput.focus();
}

function renameSession(sid) {
  var epoch = authEpoch;
  var cur = null;
  for (var i = 0; i < _sessions.length; i++) if (_sessions[i].session_id === sid) cur = _sessions[i];
  var title = prompt('重命名会话：', cur ? (cur.title && cur.title !== '新会话' ? cur.title : '') : '');
  if (title === null || !title.trim()) return;
  accountFetch('/api/v2/session/' + encodeURIComponent(sid) + '/rename', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title.trim() })
  }).then(function (r) { if (epoch !== authEpoch) return; return r.json(); }).then(function () { if (epoch !== authEpoch) return; loadSessions(); })
    .catch(function(error) { if (epoch === authEpoch) alert('重命名失败: ' + error.message); });
}

function deleteSession(sid) {
  var epoch = authEpoch;
  var isCur = sid === sessionId;
  if (!confirm('删除会话？\n' + (isCur ? '（当前会话）' : '') + '\n仅从列表隐藏，历史数据保留（可追溯）。')) return;
  accountFetch('/api/v2/session/' + encodeURIComponent(sid), { method: 'DELETE' })
    .then(function (r) { if (epoch !== authEpoch) return; return r.json(); }).then(function () { if (epoch !== authEpoch) return;
      if (isCur) { newSession(); } else { loadSessions(); }
    }).catch(function(error) { if (epoch === authEpoch) alert('删除失败: ' + error.message); });
}

function loadWorkspaceFiles() {
  var epoch = authEpoch;
  var box = document.getElementById('workspace-list');
  if (!box) return;
  var reqSid = sessionId;   // v8.10l: 竞态防护——快速切换会话时丢弃过期响应
  box.innerHTML = '<div class="empty-sidebar">加载中...</div>';
  accountFetch('/api/v2/session/' + encodeURIComponent(sessionId) + '/workspace-files')
    .then(function (r) { if (epoch !== authEpoch) return; return r.json(); }).then(function (d) { if (epoch !== authEpoch) return;
      if (reqSid !== sessionId) return;
      var files = (d && d.files) || [];
      if (files.length === 0) {
        box.innerHTML = '<div class="empty-sidebar">本会话暂无写作成果</div>';
        return;
      }
      var html = '';
      for (var i = 0; i < files.length; i++) {
        var f = files[i];
        // v8.10i: 路径分段编码（整体 encodeURIComponent 会把 / 编成 %2F 导致 404）
        var enc = f.path.split('/').map(encodeURIComponent).join('/');
        html += '<a class="workspace-file" href="/api/v2/session/' + encodeURIComponent(reqSid) + '/workspace-file?path=' + enc +
          '" target="_blank" title="' + escapeHtml(f.path) + '">📄 ' + escapeHtml(f.name) + '</a>';
      }
      box.innerHTML = html;
    }).catch(function () { if (epoch !== authEpoch) return;
      // v8.10k: 失败可点击重试（不再静默）
      box.innerHTML = '<div class="empty-sidebar" style="cursor:pointer" onclick="loadWorkspaceFiles()">加载失败，点击重试</div>';
    });
}

/* v9.2: 会话历史文献引用恢复——按来源 4 组合并（RAG/UCR/Web/历史），
   与 live 侧栏同构（替代 v8.10k 每轮列表 R{turn}-{i}：编号体系与正文不同源、
   点击不可追踪）。后端 /citations 返回 groups，恢复后按 4 组手风琴渲染；
   新提问触发的实时轮次（roundHistory）到达后优先显示实时轮次结构。 */
var _restoredGroups = null;   // {rag:[], ucr:[], web:[], historical:[]}（刷新恢复视图）
var _restoredRefPool = null;  // v9.4.3: 恢复接口版本标记（自证编号池版本）

function restoreSessionCitations() {
  var epoch = authEpoch;
  var box = document.getElementById('sidebar-content');
  if (!box || !sessionId) return;
  var reqSid = sessionId;   // v8.10l: 竞态防护——快速切换会话时丢弃过期响应
  accountFetch('/api/v2/session/' + encodeURIComponent(sessionId) + '/citations')
    .then(function (r) { if (epoch !== authEpoch) return; return r.json(); }).then(function (d) { if (epoch !== authEpoch) return;
      if (reqSid !== sessionId) return;
      var groups = d && d.groups;
      var gTotal = groups ? ((groups.rag || []).length + (groups.ucr || []).length +
                             (groups.web || []).length + (groups.historical || []).length) : 0;
      if (!groups || gTotal === 0) {
        if (roundHistory.length === 0) {
          // v8.10q: save_evidence 是后台 spawn 异步落库——切换会话时最新一轮
          // 证据可能尚未落库（空结果）。仅重试 1 次覆盖时序缺口（避免新会话无限轮询）
          _citationsRetry = (_citationsRetry || 0) + 1;
          box.innerHTML = '<div class="empty-sidebar">提交问题后文献将在此展示</div>';
          citationCount.textContent = '0';
          if (_citationsRetry <= 1) {
            setTimeout(function () {
              if (epoch === authEpoch && reqSid === sessionId) restoreSessionCitations();
            }, 800);
          } else {
            _citationsRetry = 0;
          }
        } else {
          _citationsRetry = 0;
        }
        return;
      }
      _citationsRetry = 0;
      _restoredGroups = groups;
      _restoredRefPool = (d && d.ref_pool) || null;
      renderRoundSidebar();
    }).catch(function () { if (epoch !== authEpoch) return; /* 保持现状，不打扰 */ });
}

/* ─── Context Drawer ─── */
var _ctxData = null;
var CTX_BTN_R = 15;
var CTX_BTN_CIRCUM = 2 * Math.PI * CTX_BTN_R;

function toggleCtxDrawer() {
  var drawer = document.getElementById('ctxDrawer');
  var backdrop = document.getElementById('ctxBackdrop');
  var willOpen = !drawer.classList.contains('open');
  drawer.classList.toggle('open');
  backdrop.classList.toggle('open');
  // Keep the popover focused on available estimates and measured usage.
  // Raw request/history inspection belongs in the admin tools.
  if (willOpen) renderContextPanel(_ctxData || {});
}

function _ctxLevelClass(pct) {
  if (pct < 0.70) return 'low';
  if (pct < 0.85) return 'mid';
  if (pct < 0.93) return 'high';
  return 'crit';
}

function _updateCtxBtnRing(pct) {
  // v8.10: pct<=0（未使用上下文）渲染系统灰空环，与其他图标一致；有进度才按分级色渲染
  var fg = document.getElementById('ctxBtnFg');
  var dash = CTX_BTN_CIRCUM * (1 - Math.min(pct, 1));
  fg.style.strokeDasharray = CTX_BTN_CIRCUM + ' ' + CTX_BTN_CIRCUM;
  fg.style.strokeDashoffset = dash;
  fg.className = 'ctx-btn-fg ' + (pct <= 0 ? 'none' : _ctxLevelClass(pct));
}

function _ctxCacheNote(provider) {
  var parts = [];
  [['cache_read', '缓存读取'], ['cache_write', '缓存写入'], ['cache_uncached', '未缓存']].forEach(function(pair) {
    var value = provider[pair[0]];
    if (value != null && Number.isFinite(Number(value)) && Number(value) >= 0) {
      parts.push(pair[1] + ' ' + Number(value).toLocaleString());
    }
  });
  return parts.length ? '；' + parts.join(' · ') + ' tokens（仅已报告项）' : '；缓存统计未提供';
}
function _ctxTokenNumber(value) {
  return value != null && value !== '' && typeof value !== 'boolean' && Number.isFinite(Number(value)) && Number(value) >= 0
    ? Number(value) : null;
}
function _ctxPercent(value, max) {
  if (!(max > 0) || value == null) return '—';
  var pct = value / max * 100;
  return pct > 0 && pct < 0.1 ? '<0.1%' : Number(pct.toFixed(1)) + '%';
}
// Fixed identities match request_context.estimate_request(), never model prose.
var CTX_COMPONENTS = [
  ['messages', '消息内容', '用户、助手和工具结果的消息内容；包括已装配的摘要与引用。'],
  ['system', '系统提示', '当前请求中的系统与开发者指令；技能内容若已装配，也计入所在消息。'],
  ['tool_arguments', '调用参数', '当前消息中序列化的工具调用参数。'],
  ['tool_schemas', '工具定义', '实际绑定到本次模型请求的工具定义。'],
  ['images', '图片预留', '图片的保守估算，不代表服务商实际视觉 token 用量。'],
  ['protocol', '协议开销', '消息边界、名称、调用身份及响应格式等协议估算。']
];
function _ctxBreakdown(data, est, fullRequest) {
  var components = data.components || {}, sum = 0, known = 0;
  var rows = fullRequest ? CTX_COMPONENTS.map(function(def, index) {
    var value = _ctxTokenNumber(components[def[0]]);
    if (value != null) { sum += value; known++; }
    return {key: def[0], label: def[1], note: def[2], value: value, slot: index + 1};
  }) : [];
  if (!known || sum > est) return {
    rows: [{key: 'unclassified', label: fullRequest ? '输入估算（未细分）' : '已保存会话／输入文本', value: est, slot: 0}],
    inconsistent: known > 0 && sum > est
  };
  if (sum < est) rows.push({key: 'unclassified', label: '未细分估算', value: est - sum, slot: 0});
  return {rows: rows, inconsistent: false};
}
function renderContextPanel(data) {
  _ctxData = data || {};
  var estimate = _ctxTokenNumber(_ctxData.estimated_tokens);
  var hasEstimate = estimate != null;
  var est = hasEstimate ? estimate : 0;
  var max = _ctxTokenNumber(_ctxData.model_window); // A working budget is not a model window.
  var pct = max > 0 ? est / max : 0;
  _updateCtxBtnRing(pct);

  var emptyEl = document.getElementById('ctx-empty');
  var bodyEl = document.getElementById('ctx-body');
  if (emptyEl) emptyEl.style.display = 'none';
  if (bodyEl) bodyEl.style.display = '';

  // Provider-reported usage stays separate from the assembled input estimate.
  var provider = _ctxData.provider_usage || {};
  var providerIn = _ctxTokenNumber(provider.input_tokens);
  var inEl = document.getElementById('ctxInTokens');
  var inNote = document.getElementById('ctxInNote');
  var hasProviderIn = providerIn != null;
  if (inEl) inEl.textContent = hasProviderIn ? providerIn.toLocaleString() : '—';
  if (inNote) inNote.textContent = '服务商实测 · 此调用尝试' + _ctxCacheNote(provider);
  var outTokens = _ctxTokenNumber(provider.output_tokens);
  var outEl = document.getElementById('ctxOutTokens');
  var outNote = document.getElementById('ctxOutNote');
  if (outEl) outEl.textContent = outTokens != null ? outTokens.toLocaleString() : '—';
  if (outNote) outNote.textContent = '服务商实测 · 此调用尝试';
  [inEl, outEl].forEach(function(el, index) {
    var card = el && el.closest('.ctx-token-card');
    if (card) card.hidden = index === 0 ? !hasProviderIn : outTokens == null;
  });
  var usageBlock = document.getElementById('ctxUsageBlock');
  if (usageBlock) usageBlock.hidden = !hasProviderIn && outTokens == null;

  var bar = document.getElementById('ctxBar');
  var barLabel = document.getElementById('ctxBarLabel');
  var fullRequest = _ctxData.estimate_scope === 'full_request_estimate';
  var breakdown = _ctxBreakdown(_ctxData, est, fullRequest);
  if (bar) {
    bar.replaceChildren();
    bar.style.width = '100%';
    bar.className = 'ctx-bar-inner' + (fullRequest ? '' : ' is-partial');
    // Overflow scales only the drawing to its container; labels retain true values.
    var scale = Math.max(max || 0, est), remaining = max > 0 ? Math.max(0, max - est) : 0;
    var segments = hasEstimate ? breakdown.rows.filter(function(row) { return row.value > 0; }) : [];
    segments.concat(hasEstimate && remaining > 0 ? [{key: 'free', label: fullRequest ? '窗口余量（估算）' : '未计量部分', value: remaining, slot: -1}] : []).forEach(function(row) {
      var segment = document.createElement('span');
      segment.className = 'ctx-segment ctx-slot-' + row.slot;
      segment.dataset.key = row.key;
      segment.style.width = (scale > 0 ? row.value / scale * 100 : 0) + '%';
      segment.tabIndex = 0;
      var label = row.key === 'free' && !fullRequest ? '未计量部分；完整请求剩余空间未知'
        : row.label + '：约 ' + row.value.toLocaleString() + ' tokens，占窗口 ' + _ctxPercent(row.value, max);
      segment.setAttribute('aria-label', label);
      segment.title = label;
      bar.appendChild(segment);
    });
  }
  var estimateName = _ctxData.estimate_scope === 'historical_estimate' ? '历史估算' : fullRequest ? '当前完整输入估算'
    : _ctxData.estimate_scope === 'history_text_only' ? '会话文本估算'
    : (_ctxData.estimate_scope === 'bounded_call_text' || _ctxData.estimate_scope === 'assembled_message_text') ? '本次输入估算' : '上下文估算';
  if (barLabel) barLabel.textContent = hasEstimate
    ? estimateName + ' ' + est.toLocaleString() + (max > 0 ? ' / ' + max.toLocaleString() + ' tokens' : ' tokens · 模型窗口未知')
    : '上下文估算暂不可用';
  var scopeEl = document.getElementById('ctxScope');
  if (scopeEl) scopeEl.textContent = _ctxData.logical_call_id
    ? '调用 ' + String(_ctxData.logical_call_id).slice(0, 12) + (_ctxData.attempt_id ? ' · 尝试 ' + String(_ctxData.attempt_id).slice(0, 12) : ' · 未准入')
    : hasEstimate ? '历史估算 · 未记录完整调用快照' : '尚未记录模型调用快照';
  if (bar && bar.parentNode) {
    bar.parentNode.hidden = !hasEstimate;
    bar.parentNode.setAttribute('aria-valuenow', String(Math.min(pct * 100, 100)));
    bar.parentNode.setAttribute('aria-valuetext', (fullRequest ? '估算输入占窗口 ' : '已知文本占窗口 ') + _ctxPercent(est, max));
  }
  var pctEl = document.getElementById('ctx-pct');
  if (pctEl) pctEl.textContent = hasEstimate && max > 0 ? '约 ' + _ctxPercent(est, max) : '—';
  var kindEl = document.getElementById('ctxTotalKind');
  if (kindEl) kindEl.textContent = !hasEstimate ? '等待请求统计' : !(max > 0) ? '模型窗口未知' : fullRequest ? '输入占模型窗口' : '历史估算，完整占用未知';

  var itemsEl = document.getElementById('ctxItems');
  if (itemsEl) {
    itemsEl.replaceChildren();
    itemsEl.hidden = !hasEstimate;
    var rows = breakdown.rows.concat([{key: 'free', label: fullRequest ? '窗口余量（估算）' : '剩余空间（未知）',
      value: fullRequest && max > 0 ? Math.max(0, max - est) : null, slot: -1,
      note: '模型窗口减去估算输入；输出预留和工作输入限额另列，不代表可继续发送的额度。'}]);
    rows.forEach(function(row) {
      var el = document.createElement('div');
      el.className = 'ctx-item' + (row.key === 'free' ? ' ctx-item-free' : '');
      el.dataset.key = row.key; el.setAttribute('role', 'row');
      if (row.note) el.title = row.note;
      var name = document.createElement('span'); name.className = 'ctx-item-name'; name.setAttribute('role', 'cell');
      var swatch = document.createElement('i'); swatch.className = 'ctx-swatch ctx-slot-' + row.slot;
      swatch.setAttribute('aria-hidden', 'true'); name.appendChild(swatch);
      var label = document.createElement('span'); label.textContent = row.label; name.appendChild(label);
      var value = document.createElement('span'); value.className = 'ctx-item-value'; value.setAttribute('role', 'cell');
      value.textContent = row.value != null ? row.value.toLocaleString() : row.key === 'free' ? '未知' : '未提供';
      var percent = document.createElement('span'); percent.className = 'ctx-item-percent'; percent.setAttribute('role', 'cell');
      percent.textContent = _ctxPercent(row.value, max);
      el.appendChild(name); el.appendChild(value); el.appendChild(percent); itemsEl.appendChild(el);
    });
  }
  var sumEl = document.getElementById('ctxSummary');
  if (sumEl) {
    sumEl.textContent = !hasEstimate ? '收到统计后自动更新。'
      : (fullRequest ? '完整请求估算，含工具定义、调用参数、协议与图片预留；不等同于服务商实测用量。'
        : '仅估算保存的会话或本次输入文本；系统提示、工具定义和图片等未完整计量，剩余空间未知。') +
        (breakdown.inconsistent ? ' 分类合计与总量不一致，暂按总量显示。' : '') +
        (max > 0 && est > max ? ' 输入估算已超过模型窗口。' : '');
    var historyLabels = {success: '已装配', loaded: '已装配', empty: '无历史记录', degraded: '降级装配', failed: '加载失败', unknown: '装配状态未知'};
    var changes = {initial: '首次装配', initial_attempt: '首次调用', retry: '重试同一调用',
      protocol_fallback: '调整请求协议', tools_changed: '工具定义变化',
      request_layout_changed: '请求布局变化（消息或工具定义调整）',
      working_set_compacted: '上下文整理', history_compacted: '历史压缩',
      instructions_changed: '指令变化', tool_results_added: '工具结果增加',
      final_synthesis: '转入最终综述、移除工具定义', prepared_payload_changed: '请求内容变化', epoch_changed: '上下文阶段变化'};
    sumEl.textContent += ' 历史：' + (historyLabels[_ctxData.history_load_status] || historyLabels.unknown) + '。';
    if (changes[_ctxData.change_reason]) sumEl.textContent += ' 变化原因：' + changes[_ctxData.change_reason] + '。';
    if (_ctxData.error) sumEl.textContent += ' 调用状态：' + publicTaskStage(_ctxData.error) + '。';
  }
  var budgetItems = document.getElementById('ctxBudgetItems');
  var budgetBlock = document.getElementById('ctxBudgetBlock');
  if (budgetItems) {
    budgetItems.replaceChildren();
    [['input_limit', '工作输入限额'], ['working_soft_tokens', '自动整理参考阈值'],
      ['working_target_tokens', '整理目标'], ['output_tokens', '输出预留（上限）']].forEach(function(def) {
      var value = fullRequest ? _ctxTokenNumber(_ctxData[def[0]]) : null;
      if (value == null) return;
      var item = document.createElement('div'); item.className = 'ctx-budget-row';
      var label = document.createElement('span'); label.textContent = def[1];
      var count = document.createElement('span'); count.textContent = value.toLocaleString() + ' tokens';
      item.appendChild(label); item.appendChild(count); budgetItems.appendChild(item);
    });
    if (budgetBlock) budgetBlock.hidden = !hasEstimate || !budgetItems.childElementCount;
  }
  var sugEl = document.getElementById('ctxSuggestions');
  if (sugEl) { sugEl.replaceChildren(); sugEl.hidden = true; }
  ['ctxSentBlock', 'ctxDetailBlock'].forEach(function(id) {
    var block = document.getElementById(id); if (block) block.hidden = true;
  });
  var compEl = document.getElementById('ctxCompressed');
  if (compEl) {
    if (_ctxData.compressed) { compEl.style.display = ''; compEl.textContent = '上下文已压缩为检查点摘要'; }
    else compEl.style.display = 'none';
  }
}

/* 上下文细分：system 提示词 / 历史消息(user·ai·tool) / 记忆块，逐段展开看原文 */
var CTX_ROLE_LABEL = {system: '系统', user: '用户', ai: '助手', tool: '工具', memory: '记忆', other: '其他'};

function loadCtxDetail() {
  var epoch = authEpoch;
  var box = document.getElementById('ctxDetail');
  if (!box || !sessionId) return;
  box.innerHTML = '<div class="ctx-sug-item" style="padding:6px 0">加载上下文细分…</div>';
  accountFetch('/api/v2/session/' + encodeURIComponent(sessionId) + '/context-detail')
    .then(function (r) { if (epoch !== authEpoch) return; return r.json(); })
    .then(function (d) { if (epoch !== authEpoch) return;
      var segs = (d && d.segments) || [];
      if (d && d.error) {
        box.innerHTML = '<div class="ctx-sug-item">⚠ 细分加载失败，请稍后重试（异常已记录）</div>';
        return;
      }
      if (segs.length === 0) {
        box.innerHTML = '<div class="ctx-sug-item" style="padding:6px 0">当前会话暂无上下文</div>';
        return;
      }
      // 连续同角色段落合并成一行（工具结果往往有十几段，逐段平铺读不动）
      var groups = [];
      for (var i = 0; i < segs.length; i++) {
        var s = segs[i];
        var role = s.role || 'other';
        var last = groups[groups.length - 1];
        if (last && last.role === role) {
          last.count += 1;
          last.chars += Number(s.chars || 0);
          last.content += "\n\n" + String(s.content || "");
        } else {
          groups.push({role: role, count: 1, chars: Number(s.chars || 0), content: String(s.content || ""), source: String(s.source || "")});
        }
      }
      var lastAi = -1;
      for (var k = 0; k < groups.length; k++) { if (groups[k].role === 'ai') lastAi = k; }
      var html = '';
      for (var g = 0; g < groups.length; g++) {
        var grp = groups[g];
        var label = CTX_ROLE_LABEL[grp.role] || grp.role;
        var meta = (grp.count > 1 ? grp.count + ' 段 · ' : '') + grp.chars.toLocaleString() + ' 字符';
        if (grp.role === 'ai' && g === lastAi) meta += ' · 本轮产出';
        var srcText = grp.source || label;
        if (grp.count > 1) srcText = srcText + ' … 共 ' + grp.count + ' 段';
        html += '<div class="ctx-seg">' +
          '<div class="ctx-seg-head" onclick="toggleCtxSeg(' + g + ')">' +
          '<span class="ctx-seg-role ' + grp.role + '">' + label + '</span>' +
          '<span class="ctx-seg-src">' + escapeHtml(srcText) + '</span>' +
          '<span class="ctx-seg-chars">' + meta + '</span>' +
          '<span class="ctx-seg-arrow" id="ctx-seg-' + g + '-arrow">▶</span>' +
          '</div>' +
          '<div class="ctx-seg-body" id="ctx-seg-' + g + '" style="display:none"><pre>' + escapeHtml(grp.content) + '</pre></div>' +
          '</div>';
      }
      box.innerHTML = html;
    })
    .catch(function () { if (epoch !== authEpoch) return; box.innerHTML = '<div class="ctx-sug-item">⚠ 细分加载失败，请稍后重试（异常已记录）</div>'; });
}

function toggleCtxSeg(i) {
  var body = document.getElementById('ctx-seg-' + i);
  var arrow = document.getElementById('ctx-seg-' + i + '-arrow');
  if (!body) return;
  var open = body.style.display !== 'none';
  body.style.display = open ? 'none' : '';
  if (arrow) arrow.textContent = open ? '▶' : '▼';
}

/* D-A1 实际发送快照：运行时在模型调用漏斗处记录的请求体摘要。
   与"上下文细分"的区别：细分是按角色重建的近似，这里是**真的发出去的那一份**。 */
var SENT_ROLE_LABEL = {system: '系统', user: '用户', assistant: '助手', tool: '工具'};

function _isAdminAccount() {
  return !!(currentAccount && currentAccount.role === 'admin');
}
function loadSentContext() {
  var epoch = authEpoch, sentSessionId = sessionId;
  var sentRequestId = (_ctxData || {}).request_id || '';
  var box = document.getElementById('ctxSent');
  // 技术统计只对管理员可见：先判角色再判会话，否则会话创建失败时会露出空标题（D4）
  var block = document.getElementById('ctxSentBlock');
  if (!_isAdminAccount()) {
    if (block) block.hidden = true;
    if (box) box.innerHTML = '';
    return;
  }
  if (block) block.hidden = false;
  if (!box || !sessionId) return;
  box.innerHTML = '<div class="ctx-sug-item" style="padding:6px 0">读取本轮请求快照…</div>';
  var url = '/api/v2/session/' + encodeURIComponent(sentSessionId) + '/turn-context?limit=6';
  if (sentRequestId) url += '&request_id=' + encodeURIComponent(sentRequestId);
  function ownsSnapshot() {
    return epoch === authEpoch && sentSessionId === sessionId && sentRequestId === ((_ctxData || {}).request_id || '');
  }
  accountFetch(url)
    .then(function (r) { if (!ownsSnapshot()) return; return r.json(); })
    .then(function(data) { if (ownsSnapshot()) renderSentContext(data); })
    .catch(function () { if (!ownsSnapshot()) return;
      box.innerHTML = '<div class="ctx-sent-empty">⚠ 请求快照读取失败（不影响本轮回答）</div>';
    });
}

var _sentSnapshots = [];
function _sentTok(chars) {
  // 逐条 token 未由接口返回，按字符数估算（中文约 1 字 ≈ 0.6 token、英文约 4 字符 ≈ 1 token，取折中 3）
  var n = Math.round((Number(chars) || 0) / 3);
  return n.toLocaleString();
}
function _sentPreview(text) {
  var s = String(text || '').replace(/\s+/g, ' ').trim();
  return s.length > 55 ? s.slice(0, 55) + '…' : s;
}
function renderSentContext(d) {
  var box = document.getElementById('ctxSent');
  if (!box) return;
  var rows = (d && d.rows) || [];
  _sentSnapshots = rows;
  if (!rows.length) {
    box.innerHTML = '<div class="ctx-sent-empty">本轮没有记录到请求快照。' +
      '记录从本次更新后开始，历史轮次不会补录。</div>';
    return;
  }
  var html = '';
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i];
    var payload = row.payload || {};
    var msgs = payload.messages || [];
    var when = row.created_at ? new Date(row.created_at * 1000) : null;
    var timeText = when ? (('0' + when.getHours()).slice(-2) + ':' +
      ('0' + when.getMinutes()).slice(-2) + ':' + ('0' + when.getSeconds()).slice(-2)) : '';
    var label = _sentLabelOf(i);
    var head = '<div class="ctx-sent-head" onclick="toggleSentCall(' + i + ')">' +
      '<span class="ctx-sent-idx">#' + (rows.length - i) + '</span>' +
      '<span class="ctx-sent-meta">' + (payload.scope === 'physical_attempt' ? '物理尝试 · ' : '逻辑观测 · ') + msgCountText(msgs.length, row.msg_count) + ' · ' +
      Number(row.chars || 0).toLocaleString() + ' 字符 · ' +
      (row.input_tokens ? '输入 ' + Number(row.input_tokens).toLocaleString() + ' tokens'
        : '输入 tokens 未返回') + '</span>' +
      (label ? '<span class="ctx-sent-label">' + escapeHtml(label) + '</span>' : '') +
      '<span class="ctx-sent-time">' + timeText + '</span>' +
      '<span class="ctx-seg-arrow" id="ctx-sent-' + i + '-arrow">▶</span></div>' +
      '<div class="ctx-sent-acts">' +
        '<button type="button" class="ctx-mini-btn" onclick="event.stopPropagation();copySentSnapshot(' + i + ')">复制 JSON</button>' +
        '<button type="button" class="ctx-mini-btn" onclick="event.stopPropagation();labelSentSnapshot(' + i + ')">标注</button>' +
      '</div>';
    var body = '<div class="ctx-sent-body" id="ctx-sent-' + i + '" style="display:none">';
    if (row.error) body += '<div class="ctx-sent-err">该次调用失败：' + escapeHtml(row.error) + '</div>';
    body += '<div class="ctx-sent-thead"><span class="ctx-sent-c-no">序号</span>' +
      '<span class="ctx-sent-c-role">角色</span>' +
      '<span class="ctx-sent-c-prev">大小与哈希（旧记录保留预览）</span>' +
      '<span class="ctx-sent-c-tok" title="逐条 token 未返回，按字符数估算">token</span>' +
      '<span class="ctx-sent-c-act">操作</span></div>';
    for (var m = 0; m < msgs.length; m++) {
      var msg = msgs[m];
      var kind = msg.kind || '';
      var role = SENT_ROLE_LABEL[kind] || kind || '?';
      var chip = kind === 'assistant' ? 'ai' : (kind === 'user' ? 'user' : (kind === 'tool' ? 'tool' : 'sys'));
      var calls = (msg.tool_calls && msg.tool_calls.length)
        ? '<span class="ctx-sent-calls">调用 ' + escapeHtml(msg.tool_calls.join(', ')) + '</span>' : '';
      var act = '';
      if (kind === 'system') {
        act = '<span class="ctx-sent-ro" title="系统提示词未在对话历史中渲染">只读</span>';
      } else if (kind === 'tool') {
        act = '<button type="button" class="ctx-mini-btn" onclick="event.stopPropagation();focusSentRow(' + i + ',' + m + ',\'tool\')">查看</button>';
      } else {
        act = '<button type="button" class="ctx-mini-btn" onclick="event.stopPropagation();focusSentRow(' + i + ',' + m + ',\'' + (kind === 'user' ? 'user' : 'answer') + '\')">定位</button>';
      }
      body += '<div class="ctx-sent-row is-' + chip + '">' +
        '<span class="ctx-sent-c-no">' + (m + 1) + '</span>' +
        '<span class="ctx-sent-c-role"><span class="ctx-seg-role ' + chip + '">' + role + '</span>' + calls + '</span>' +
        '<span class="ctx-sent-c-prev">' + escapeHtml(msg.hash
          ? (msg.bytes != null ? msg.bytes + ' bytes · ' : '') + 'sha256 ' + msg.hash.slice(0, 16)
          : _sentPreview(msg.preview || '')) + '</span>' +
        '<span class="ctx-sent-c-tok">≈' + _sentTok(msg.chars) + '</span>' +
        '<span class="ctx-sent-c-act">' + act + '</span></div>';
    }
    if (!msgs.length) body += '<div class="ctx-sent-err">这条记录没有消息明细</div>';
    body += '</div>';
    html += '<div class="ctx-sent-call">' + head + body + '</div>';
  }
  box.innerHTML = html;
}
function msgCountText(n, fallback) {
  var v = (typeof n === 'number' && n > 0) ? n : (fallback || 0);
  return v + ' 条';
}
function _sentLabelKey(i) {
  var row = _sentSnapshots[i] || {};
  return 'citrus_ctx_label:' + (sessionId || '') + ':' + (row.request_id || row.created_at || i);
}
function _sentLabelOf(i) {
  try { return localStorage.getItem(_sentLabelKey(i)) || ''; } catch (e) { return ''; }
}
function labelSentSnapshot(i) {
  var cur = _sentLabelOf(i);
  var next = window.prompt('给这次请求加一个标注（留空则清除）', cur || '');
  if (next === null) return;
  try {
    if (next.trim()) localStorage.setItem(_sentLabelKey(i), next.trim().slice(0, 40));
    else localStorage.removeItem(_sentLabelKey(i));
  } catch (e) {}
  var box = document.getElementById('ctxSent');
  var rows = _sentSnapshots;
  if (box && rows.length) renderSentContext({ rows: rows });
}
function copySentSnapshot(i) {
  var row = _sentSnapshots[i];
  if (!row) return;
  var text = JSON.stringify(row, null, 2);
  var done = function () {
    var box = document.getElementById('ctxSent');
    if (!box) return;
    var tip = document.createElement('div');
    tip.className = 'ctx-sent-toast';
    tip.textContent = '已复制这次请求的 JSON（仅结构摘要，不含完整正文）';
    box.appendChild(tip);
    setTimeout(function () { if (tip.parentNode) tip.parentNode.removeChild(tip); }, 2200);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(function () {});
  } else {
    var ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch (e) {}
    document.body.removeChild(ta);
  }
}
function _flashEl(el) {
  if (!el) return;
  el.classList.add('ctx-focus-flash');
  setTimeout(function () { el.classList.remove('ctx-focus-flash'); }, 2000);
}
function focusSentRow(i, m, kind) {
  var row = _sentSnapshots[i];
  var msg = row && (row.payload || {}).messages ? row.payload.messages[m] : null;
  if (kind === 'tool') {
    // 工具结果：展开本轮执行时间线，定位到同名工步
    var name = (msg && msg.tool_calls && msg.tool_calls.length) ? msg.tool_calls[0] : '';
    var traces = document.querySelectorAll('.terminal-log .term-line');
    var target = null;
    for (var k = traces.length - 1; k >= 0; k--) {
      var t = traces[k];
      if (!name || (t.textContent || '').indexOf(name) >= 0) { target = t; break; }
    }
    if (target) {
      var det = target.closest('details');
      if (det) det.open = true;
      target.scrollIntoView({ block: 'center', behavior: 'smooth' });
      _flashEl(target);
      return;
    }
    var box = document.getElementById('ctxSent');
    if (box) _flashEl(box);
    return;
  }
  // 用户输入 / 助手回答：滚到本轮对应气泡并高亮
  var sel = kind === 'user' ? '.message.user' : '.message.bot';
  var nodes = document.querySelectorAll(sel);
  var el = nodes.length ? nodes[nodes.length - 1] : null;
  if (el) {
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    _flashEl(el);
  }
}

function toggleSentCall(i) {
  var body = document.getElementById('ctx-sent-' + i);
  var arrow = document.getElementById('ctx-sent-' + i + '-arrow');
  if (!body) return;
  var open = body.style.display !== 'none';
  body.style.display = open ? 'none' : '';
  if (arrow) arrow.textContent = open ? '▶' : '▼';
}

// Public task descriptions only. Never render loaded-history counters or private reasoning.
function publicTaskStage(value) {
  var text = String(value == null ? '' : value).trim();
  if (/已加载.*(?:历史|\d+\s*条)|loaded\s+(?:history|\d+)|history\s+loaded/i.test(text)) return '';
  if (/```|<script\b|<think\b|private[_ ]reasoning|chain.of.thought|(?:^|\n)\s*(?:import \w|from \w+ import|def \w+\(|function\s*\w*\(|const \w+\s*=|Traceback)/i.test(text)) return '';
  return text.replace(/\s+/g, ' ').slice(0, 600);
}

function publicTaskNote(value) {
  var text = String(value == null ? '' : value).trim();
  // Apply the existing privacy/code guard, but keep the complete public prose
  // and its paragraph breaks rather than reducing it to a stage label.
  return publicTaskStage(text) ? text : '';
}

function createResearchProcessPanel(goal) {
  var epoch = authEpoch, sid = sessionId, knownJobId = '';
  var panel = document.createElement('details');
  panel.className = 'tool-trace research-process is-running'; panel.open = true;
  var summary = document.createElement('summary'); summary.className = 'tool-trace-summary';
  var label = document.createElement('span'); label.className = 'tool-trace-label'; label.textContent = '执行轨迹：进行中';
  var elapsed = document.createElement('span'); elapsed.className = 'tool-trace-elapsed';
  summary.appendChild(label); summary.appendChild(elapsed); panel.appendChild(summary);
  var body = document.createElement('div'); body.className = 'research-process-body';
  var intent = document.createElement('div'); intent.className = 'research-process-goal';
  intent.textContent = '本轮目标：' + String(goal || '未记录任务描述').replace(/\s+/g, ' ').slice(0, 360);
  var stage = document.createElement('div'); stage.className = 'research-process-stage';
  stage.setAttribute('role', 'status'); stage.setAttribute('aria-live', 'polite');
  stage.hidden = true;
  body.appendChild(intent); body.appendChild(stage); panel.appendChild(body);
  function ownsDetails() { return epoch === authEpoch && sid === sessionId && chatContainer.contains(panel); }
  var view = CitrusTaskCards.createProcessView({container: body, currentNode: stage, clean: publicTaskStage, publicNote: publicTaskNote,
    toolName: displayToolName, statusText: toolStatusText, owns: ownsDetails,
    loadToolResult: async function (callId) {
      if (!ownsDetails()) return null;
      var response = await accountFetch('/api/v2/session/' + encodeURIComponent(sid) + '/tool-result/' + encodeURIComponent(callId), {credentials: 'same-origin'});
      if (!ownsDetails()) return null;
      if (!response.ok) { var error = new Error('工具记录读取失败'); error.status = response.status; throw error; }
      var result = await response.json();
      if (!ownsDetails()) return null;
      if (!result || result.tool_call_id !== callId) throw new Error('工具记录身份不匹配');
      return result;
    },
    elapsed: function (ms) { return (Math.max(0, Number(ms) || 0) / 1000).toFixed(1) + ' 秒'; }});
  view.note('正在理解你的请求…');
  var collapsedAtTerminal = false;
  function note(text, id, metadata) { view.note(text, id, metadata); }
  var answerArmed = false, answerSeen = false, retainedAnswer = false, publicNoteCount = 0;
  function answerVisible(retained) {
    if (retained) retainedAnswer = true;
    if (answerSeen) return;
    answerSeen = true; panel.open = false; collapsedAtTerminal = true;
  }
  function receive(type, data) {
    data = data || {};
    var savedResolutions = (data.turn_envelope || data.terminal_snapshot || data).tool_resolutions;
    if (Array.isArray(savedResolutions)) view.resolution({resolutions: savedResolutions});
    if (type === 'job') knownJobId = String(data.job_id || knownJobId);
    else if (type === 'task_plan' || type === 'status' && data.stage === 'task_plan') {
      if (!knownJobId || String(data.plan_id) === knownJobId) view.plan(data);
    }
    else if (type === 'agent_recovery' && data.status === 'admitted') {
      view.terminal(false); panel.classList.add('is-running'); panel.classList.remove('is-done', 'has-failure');
      label.textContent = '执行轨迹： 进行中'; panel.open = true; collapsedAtTerminal = false;
      answerArmed = false; answerSeen = false; retainedAnswer = false;
    }
    else if (type === 'plan') note('正在整理回答…');
    else if (type === 'thinking') note('正在处理…');
    else if (type === 'plan_ready') note('正在整理内容…');
    else if (type === 'section_start' || type === 'section_done') note('正在组织回答…');
    else if (type === 'tool_resolution') view.resolution(data);
    else if (type === 'answer_start') answerArmed = true;
    else if (type === 'text' && answerArmed && String(data.content || '').trim()) answerVisible();
    else if (type === 'done' && String(data.answer || '').trim()) answerVisible();
    else if (type === 'text_reset' && !retainedAnswer) {
      if (answerSeen) { panel.open = true; collapsedAtTerminal = false; }
      answerArmed = false; answerSeen = false;
    }
    else if (type === 'request_user_input' || type === 'question') note('等待你的选择后继续。');
    else if (type === 'status') {
      var understanding = publicTaskStage(data.intent_summary || data.task_summary);
      if (understanding) intent.textContent = '本轮目标：' + understanding;
      if (data.facts || data.result_refs || data.object || data.objects || data.gaps) view.progress(data);
      else handleStatusEvent(data, function (_, text) { note(text, data.seq ? 'seq-' + data.seq : ''); });
    } else if (type === 'assistant_progress') {
      note(data.text || data.working_note || data.content || data.message || data.summary,
        data.seq ? 'seq-' + data.seq : data.message_id ? 'message-' + data.message_id : data.note_id ? '' : 'public-' + (++publicNoteCount),
        Object.assign({}, data, {public_note: true}));
    } else if (type === 'agent_progress' || type === 'tool_progress') {
      if (data.facts || data.result_refs || data.object || data.objects || data.gaps) view.progress(data);
      else note(data.text || data.working_note || data.content || data.message || data.summary, data.seq ? 'seq-' + data.seq : data.message_id ? 'message-' + data.message_id : 'public-' + (++publicNoteCount));
    }
    else if (['tool_call_start', 'tool_executing', 'tool_result', 'operation'].indexOf(type) >= 0) {
      view.action(Object.assign({}, data.outcome || {}, data, {status: data.status || (type === 'tool_result' ? (data.is_error ? 'failed' : 'complete') : 'running')}));
    }
  }
  function terminal(status) {
    var names = {completed: '已完成', partial: '部分完成', cancelled: '已停止', failed: '未完成'};
    if (!names[status]) return false;
    label.textContent = '执行轨迹：' + names[status];
    view.terminal();
    panel.classList.remove('is-running'); panel.classList.add('is-done');
    panel.classList.toggle('has-failure', status === 'failed');
    if (answerSeen && !collapsedAtTerminal) { panel.open = false; collapsedAtTerminal = true; }
    return true;
  }
  return {element: panel, label: label, elapsed: elapsed, body: body, intent: intent, stage: stage,
    note: note, receive: receive, terminal: terminal, answerVisible: answerVisible, projection: view.projection};
}

function handleStatusEvent(data, applyStatus) {
  applyStatus = applyStatus || window._activeTurnStatusUpdater;
  if (typeof applyStatus !== 'function') return;
  var stage = data.stage || '';
  var names = {queued: '等待处理', planning: '准备处理请求', intake: '准备处理请求',
    agent_progress: '处理请求', final_answer: '正在生成回答', answer: '正在生成回答',
    retrieval_done: '正在整理检索结果', local_done_web_running: '正在检索联网资料', web_done: '正在整理检索结果'};
  var text = publicTaskStage(data.stage_label) || publicTaskStage(data.message);
  if (!text && stage === 'step_active') text = data.step_id === 'load' ? '正在准备会话上下文'
    : data.step_id === 'retrieve' ? '正在准备任务上下文' : '';
  text = text || names[stage];
  if (text) applyStatus(stage, text);
}




/* ─── Chat ─── */
/* v8.10: 反馈栏默认不随气泡创建（回答生成完成后由 appendFeedback 追加，主流时序）；
   历史恢复的 bot 消息同样不带反馈 */
function addMessage(role, html) {
  var div = document.createElement('div');
  div.className = 'message ' + role;
  div.dataset.messageRole = role;
  div.innerHTML = '<div class="avatar"></div><div class="content">' + html + '</div>';
  if (role === 'user') renderAvatarImage(div.querySelector('.avatar'), currentAccount, 'message-avatar-image', '我的头像');
  else renderAgentAvatar(div.querySelector('.avatar'));
  chatContainer.appendChild(div);
  chatContainer.scrollTop = chatContainer.scrollHeight;
  return div.querySelector('.content');
}

function appendMessageActions(content, role, text) {
  if (!content) return;
  var message = content.closest ? content.closest('.message') : content.parentNode;
  var actionHost = role === 'user' && message ? message : content;
  if (actionHost.querySelector('.message-actions')) return;
  var actions = document.createElement('div');
  actions.className = 'message-actions';
  var copy = document.createElement('button');
  copy.type = 'button'; copy.className = 'message-action'; copy.title = '复制内容';
  copy.setAttribute('aria-label', '复制内容'); copy.textContent = '⧉ 复制';
  copy.addEventListener('click', function() { copyMessageText(String(text || ''), copy); });
  actions.appendChild(copy);
  if (role === 'user') {
    var edit = document.createElement('button');
    edit.type = 'button'; edit.className = 'message-action';
    var hasAttachments = !!content.querySelector('.sent-attachment-gallery');
    edit.title = hasAttachments ? '修改问题后重新发送；附件需重新添加' : '将问题放回输入框修改后重新发送';
    edit.setAttribute('aria-label', '编辑问题');
    edit.textContent = '✎ 编辑';
    edit.addEventListener('click', function() {
      userInput.value = String(text || '');
      userInput.focus();
      userInput.setSelectionRange(userInput.value.length, userInput.value.length);
      userInput.scrollIntoView({behavior:'smooth', block:'nearest'});
    });
    actions.appendChild(edit);
  }
  actionHost.appendChild(actions);
}

function copyMessageText(text, button) {
  function showCopied() {
    if (!button) return;
    var old = button.textContent; button.textContent = '✓ 已复制';
    setTimeout(function() { button.textContent = old; }, 1200);
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(showCopied).catch(function() { copyMessageFallback(text, showCopied); });
  } else copyMessageFallback(text, showCopied);
}

function copyMessageFallback(text, done) {
  var node = document.createElement('textarea');
  node.value = text; node.style.position = 'fixed'; node.style.opacity = '0';
  document.body.appendChild(node); node.select();
  try { document.execCommand('copy'); if (done) done(); } catch (e) {}
  node.remove();
}

function isPublicTurnFile(file) {
  return file && file.internal !== true && file.is_intermediate !== true && file.formal_attachment !== false
    && ['internal', 'private', 'intermediate'].indexOf(String(file.visibility || '').toLowerCase()) < 0
    && ['internal', 'intermediate', 'working'].indexOf(String(file.purpose || file.role || '').toLowerCase()) < 0;
}

function filesAttributedToTurn(files, sid, rid, artifactIds) {
  return (files || []).filter(function(file) {
    if (!isPublicTurnFile(file) || (file.source_session_id && String(file.source_session_id) !== String(sid))) return false;
    if (file.turn_request_id && String(file.turn_request_id) !== String(rid)) return false;
    if (Array.isArray(artifactIds)) return artifactIds.indexOf(String(file.artifact_id || file.file_id || '')) >= 0;
    return String(file.source_session_id || '') === String(sid) && String(file.turn_request_id || '') === String(rid);
  });
}

function syncHistoryTurnFiles() {
  if (!window.loadSessionFiles || !sessionId) return;
  var epoch = authEpoch, sid = sessionId;
  window.loadSessionFiles().then(function(payload) {
    if (epoch !== authEpoch || sid !== sessionId || !payload) return;
    chatContainer.querySelectorAll('.content').forEach(function(content) {
      if (!content.closest('.bot') || content.dataset.sessionId !== sid || !content.dataset.requestId) return;
      appendTurnGeneratedFiles(content, filesAttributedToTurn(payload.generated, sid, content.dataset.requestId));
    });
  }).catch(function() {});
}

function appendTurnGeneratedFiles(content, files, headingText) {
  if (!content || !files || !files.length) return;
  var host = content.closest('.message') || content;
  var ownerSession = content.dataset.sessionId || sessionId;
  var ownerTurn = content.dataset.requestId || '';
  // A canonical turn's file references are authoritative. Conflicting metadata
  // is never allowed to move a file into another session or turn.
  files = files.filter(function(file) {
    return isPublicTurnFile(file) && file.file_id && (!file.source_session_id || String(file.source_session_id) === ownerSession)
      && (!file.turn_request_id || String(file.turn_request_id) === ownerTurn);
  });
  if (!files.length) return;
  // The section is a sibling immediately after the answer bubble, owned by
  // its message wrapper. Library refreshes append idempotently to that section.
  var section = host.querySelector('.turn-files');
  var list = section ? section.querySelector('.turn-file-list') : null;
  if (!section) {
    section = document.createElement('section'); section.className = 'turn-files';
    var heading = document.createElement('div'); heading.className = 'turn-files-heading';
    heading.textContent = headingText || '本轮生成文件'; section.appendChild(heading);
    list = document.createElement('div'); list.className = 'turn-file-list';
    section.appendChild(list);
  } else if (!list) {
    list = document.createElement('div'); list.className = 'turn-file-list';
    section.appendChild(list);
  }
  var added = 0;
  var seen = {};
  host.querySelectorAll('.turn-file-card').forEach(function(existing) {
    seen[String(existing.dataset.fileId || '')] = true;
  });
  files.forEach(function(file) {
    var fid = String((file && file.file_id) || '');
    if (fid && seen[fid]) return;          // same file must not appear twice in one bubble
    if (fid) seen[fid] = true;
    var card = document.createElement('div'); card.className = 'turn-file-card';
    if (fid) card.dataset.fileId = fid;
    var type = document.createElement('span'); type.className = 'turn-file-type';
    type.textContent = String(file.kind || file.filename || 'FILE').toUpperCase().replace(/[^A-Z]/g, '').slice(0, 5) || 'FILE';
    var info = document.createElement('div'); info.className = 'turn-file-info';
    var name = document.createElement('span'); name.className = 'turn-file-name'; name.textContent = file.filename || '生成文件';
    name.title = file.filename || '';
    var meta = document.createElement('span'); meta.className = 'turn-file-meta';
    meta.textContent = file.size_bytes ? (Math.max(1, Math.round(Number(file.size_bytes) / 1024)) + ' KB') : '可预览与下载';
    info.appendChild(name); info.appendChild(meta);
    var preview = document.createElement('button'); preview.type = 'button'; preview.className = 'turn-file-open';
    preview.textContent = '预览'; preview.setAttribute('aria-label', '预览 ' + (file.filename || '生成文件'));
    preview.addEventListener('click', function() {
      if (window.openSessionFilePreview) window.openSessionFilePreview(file.file_id);
    });
    card.appendChild(type); card.appendChild(info); card.appendChild(preview); list.appendChild(card);
    added++;
  });
  if (!added) return;                     // no new file: keep the DOM as it was
  if (section.parentNode) return;
  if (host !== content) host.insertBefore(section, content.nextSibling);
  else content.appendChild(section);
}

/* v8.6 用户反馈（书 §4.6 反馈循环）: 助手回答下方追加 👍/👎 记录条（纯记录，不影响回答）
   v8.10: 回答完整生成后才调用（done 事件），不再随气泡创建即显示 */
function appendFeedback(msgEl) {
  if (!msgEl || msgEl.querySelector('.msg-feedback')) return;
  var fb = document.createElement('div');
  fb.className = 'msg-feedback';
  fb.innerHTML = '<button class="fb-btn" data-rating="1" title="回答有帮助">👍 有用</button>' +
                 '<button class="fb-btn" data-rating="-1" title="回答有问题">👎 有问题</button>' +
                 '<span class="fb-msg"></span>';
  msgEl.appendChild(fb);
}

/* v8.6/v8.7 反馈提交：点击 👍/👎 展开"哪里好/哪里不好"填写行，提交时带 comment；
   内容哈希作 message_id（幂等去重），仅记录不阻断 */
function submitFeedback(fb, mid, rating, comment) {
  var epoch = authEpoch;
  var fbMsg = fb.querySelector('.fb-msg');
  accountFetch('/api/v2/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId || '', message_id: mid, rating: rating, comment: comment || '' })
  }).then(function (r) { if (epoch !== authEpoch) return; return r.json(); }).then(function (d) { if (epoch !== authEpoch) return;
    var row = fb.querySelector('.fb-row');
    if (row) row.remove();
    if (d && d.status === 'ok' && fbMsg) { fbMsg.textContent = rating === 1 ? '已记录 👍' : '已记录 👎'; }
  }).catch(function () { if (epoch !== authEpoch) return;
    var row = fb.querySelector('.fb-row');
    if (row) row.remove();
    var allBtns = fb.querySelectorAll('.fb-btn');
    for (var i = 0; i < allBtns.length; i++) allBtns[i].disabled = false;
  });
}

document.addEventListener('click', function (e) {
  var btn = e.target.closest ? e.target.closest('.fb-btn') : null;
  if (!btn) return;
  var rating = parseInt(btn.getAttribute('data-rating'), 10);
  var fb = btn.closest('.msg-feedback');
  if (!fb || fb.querySelector('.fb-input')) return;   // 已展开填写行
  var msgEl = btn.closest('.message');
  var contentEl = msgEl ? msgEl.querySelector('.content') : null;
  var text = (contentEl ? contentEl.textContent || '' : '').substring(0, 2000);
  var hash = 0;
  for (var i = 0; i < text.length; i++) { hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0; }
  var mid = 'h' + (hash >>> 0).toString(16);

  var row = document.createElement('div');
  row.className = 'fb-row';
  row.innerHTML = '<input class="fb-input" placeholder="哪里好 / 哪里不好（可留空直接提交）" maxlength="500">' +
                  '<button class="fb-submit">提交</button>' +
                  '<button class="fb-cancel">取消</button>';
  fb.appendChild(row);
  var inp = row.querySelector('.fb-input');
  inp.focus();
  var allBtns = fb.querySelectorAll('.fb-btn');
  for (var i = 0; i < allBtns.length; i++) allBtns[i].disabled = true;
  row.querySelector('.fb-submit').addEventListener('click', function () {
    submitFeedback(fb, mid, rating, inp.value);
  });
  row.querySelector('.fb-cancel').addEventListener('click', function () {
    row.remove();
    for (var i = 0; i < allBtns.length; i++) allBtns[i].disabled = false;
  });
});

/* v8.10: silent=true 跳过确认（newSession 内部调用——新建会话不应弹"清空"提示） */
async function clearSession(silent) {
  var epoch = authEpoch;
  if (!silent && !confirm('确定清空当前会话？')) return;
  try {
    await accountFetch('/api/v1/session/' + sessionId + '/clear', { method: 'POST' });
    if (epoch !== authEpoch) return;
    chatContainer.innerHTML = '<div class="message bot"><div class="avatar"><img class="agent-avatar-image" src="/assets/citrusscholar-mascot.png" alt="CitrusScholar 助手" width="36" height="36"></div><div class="content">您好！有什么想了解的？</div></div>';
    sidebarContent.innerHTML = '<div class="empty-sidebar">提交问题后文献将在此展示</div>';
    citationCount.textContent = '0';
    _historyMsgs = [];
    _historyTurns = null;
    _historyHasMore = false;
    roundHistory = [];
    _restoredGroups = null;   // v9.2: 清空会话同步清空恢复视图
    // P3-2: 重置上下文面板（清空后不再残留旧会话数字）
    _ctxData = null;
    renderContextPanel({ estimated_tokens: 0, max_tokens: _ctxMaxTokens(), history_msgs: 0,
                         soft_threshold: 0.75, hard_threshold: 0.93 });
    // P0: 清空会话后导航立即重建（服务端历史已空）
    if (window.turnRail && window.turnRail.reload) window.turnRail.reload();
    // 清空后刷新会话列表。
    loadSessions();
  } catch (e) { if (epoch !== authEpoch) return; alert('清空失败: ' + e.message); }
}

async function newSession() {
  var epoch = authEpoch;
  try {
    var resp = await accountFetch('/api/v1/session/new', { method: 'POST' });
    var data = await resp.json(); if (epoch !== authEpoch) return;
    sessionId = data.session_id;
    localStorage.setItem(sessionStorageKey(), sessionId);
    window.dispatchEvent(new CustomEvent('citrus:session-changed', {detail:{session_id:sessionId}}));
    // v8.10: 静默清空旧会话界面（不弹"确定清空"确认——新建会话语义，勿误导用户）
    await clearSession(true); if (epoch !== authEpoch) return;
    // v8.10e: 创建后强制刷新会话列表（await + 重试），并延迟二次刷新防后端时序
    applySidebar('session');
    await loadSessionsWithRetry(); if (epoch !== authEpoch) return;
    setTimeout(function () { if (epoch === authEpoch) loadSessions(); }, 300);
  } catch (e) { if (epoch !== authEpoch) return; alert('创建新会话失败: ' + e.message); }
}

function toggleRound(id) {
  var body = document.getElementById(id);
  var icon = document.getElementById(id + '-icon');
  body.style.display = body.style.display === 'none' ? 'block' : 'none';
  icon.classList.toggle('collapsed');
}
/* v8.15.2: 证据来源组手风琴（全局版本——内联 onclick 需要全局解析；
   此前定义在 streamOnce 内导致 RAG/UCR 组无法展开收缩） */
function toggleSrcGroup(id) {
  var g = document.getElementById('srcgroup-' + id);
  if (!g) return;
  var willOpen = g.style.display === 'none';
  var box = g.parentNode;
  // 收起同 Round 内的其他来源组（手风琴语义）
  var siblings = box.querySelectorAll('.src-group-body');
  for (var i = 0; i < siblings.length; i++) {
    siblings[i].style.display = 'none';
    var sibIcon = siblings[i].previousElementSibling &&
      siblings[i].previousElementSibling.querySelector('.toggle-icon');
    if (sibIcon) sibIcon.classList.add('collapsed');
  }
  if (willOpen) {
    g.style.display = 'block';
    var myIcon = g.previousElementSibling &&
      g.previousElementSibling.querySelector('.toggle-icon');
    if (myIcon) myIcon.classList.remove('collapsed');
  }
}
/* v8.15.2: 引用编号自然排序（[1]..[13] 升序；[W1] 组在数字之后） */
function _refIdKey(id) {
  var s = String(id == null ? '' : id);
  var m = s.match(/^([WH])?(\d+)$/i);
  if (!m) return 'ZZ' + s;
  return (m[1] ? m[1].toUpperCase() : 'N') + ('0000' + m[2]).slice(-4);
}
function _refIdSort(items) {
  return (items || []).slice().sort(function (a, b) {
    var ka = _refIdKey(a && a.ref_id), kb = _refIdKey(b && b.ref_id);
    return ka < kb ? -1 : (ka > kb ? 1 : 0);
  });
}
  // v10.13（Task E，用户指示）：联网组内按**来源分级**排序 T1 → T2 → T3 → TU。
  //   用户原话："每一个 url 分开，T1T2T3 优先排列"。
  //   同 tier 内回退到 ref_id 升序（保持可追溯、稳定）；无 tier 的排最后。
  // v10.13（改动 3.2）：联网组内按 `Wn` 数字升序（组内已同 tier）。
  function _webRefSort(items) {
    return (items || []).slice().sort(function (a, b) {
      var na = parseInt(String((a && a.ref_id) || '').replace(/\D/g, ''), 10);
      var nb = parseInt(String((b && b.ref_id) || '').replace(/\D/g, ''), 10);
      if (isNaN(na)) na = 1e9;
      if (isNaN(nb)) nb = 1e9;
      return na - nb;
    });
  }
  function _tierSort(items) {
    var ORD = { 'T1': 0, 'T2': 1, 'T3': 2, 'TU': 3 };
    return (items || []).slice().sort(function (a, b) {
      var ta = String((a && a.tier) || '').toUpperCase();
      var tb = String((b && b.tier) || '').toUpperCase();
      var oa = (ORD[ta] != null) ? ORD[ta] : 9;
      var ob = (ORD[tb] != null) ? ORD[tb] : 9;
      if (oa !== ob) return oa - ob;
      var ka = _refIdKey(a && a.ref_id), kb = _refIdKey(b && b.ref_id);
      return ka < kb ? -1 : (ka > kb ? 1 : 0);
    });
  }

/* v9.2: 引用编号统一重排已收敛到后端（evidence.renumber_refs）——citations 事件
   附带 remap（旧ref_id → 新ref_id；数字 [n] 连续 1..k、[Wn] 连续 W1..Wm、
   [Hn] 连续 H1..Hp）。前端只按 remap 通用重写正文与 ref_id，替代 v8.17.3
   前端 W 专用压缩（_buildCompactRefMap/_rewriteWRefsInText 移除）。 */
function _rewriteRefsByMap(text, remap) {
  if (!text || !remap) return text;
  return text.replace(/\[([WH]?)(\d+)\]/gi, function (full, pre, num) {
    var key = pre ? String(pre).toUpperCase() + num : num;
    return '[' + (remap[key] != null ? remap[key] : key) + ']';
  });
}

// Hoisted to file scope: renderHistoryMessages() also renders tool status text,
// and the previous nested declarations were invisible to it (refresh lost all
// messages after the first tool row with a ReferenceError).
function toolActivityLabel(name) {
  var labels = {
    '网页搜索': '正在搜索网页…', '读取网页': '正在读取网页资料…',
    '获取论文全文': '正在查找论文全文…', '本地检索': '正在检索本地资料…',
    '数据审查': '正在检查数据…', '读取表格': '正在读取表格结构…',
    '文献核验': '正在核对文献信息…', '读取 PDF': '正在读取 PDF…',
    '绘图': '正在生成图示…', '统计分析': '正在分析数据…',
    '实验设计': '正在整理实验方案…', '导出论文解读': '正在整理论文解读…',
    '导出实验方案': '正在整理实验方案…', '写作': '正在整理内容…',
    '分析': '正在分析资料…'
  };
  return labels[name] || ('正在处理：' + (name || '科研任务') + '…');
}
function toolStatusText(status, reason) {
  var reasons = {
    quota: '服务额度暂不可用', auth: '服务认证未通过', timeout: '处理超时',
    network: '网络连接失败', dependency: '所需组件不可用',
    configuration_missing: '服务尚未配置', no_matching_corpus: '没有匹配的本地资料类别',
    translation_failed: '查询语言转换未完成', no_selected_attachment: '未找到可处理的附件',
    no_reference_identifiers: '没有提取到可核验的文献信息',
    no_open_archive: '没有找到可获取的开放材料', blocked_destination: '目标暂不可访问',
    duplicate_request: '重复操作已跳过', tool_batch_limit: '本轮操作数量已达上限',
    execution_record_unavailable: '执行记录不可用',
    status_unavailable: '结果状态暂不可读',
    source_unavailable: '来源暂时无法访问',
    some_records_unverified: '部分条目未能核实', coverage_incomplete: '部分内容未完成检查',
    some_material_unreviewed: '部分材料未能检查',
    fields_unavailable: '读取字段不存在；请使用 JSON Pointer 路径（如 /rows）',
    no_new_evidence: '未获得新的证据或覆盖', finalization_budget: '工作预算已用尽，保留已有结果',
    context_budget_exhausted: '上下文超出工作输入限额', history_load_failed: '会话历史加载失败',
    model_timeout: '模型响应超时', model_network: '模型连接失败',
    model_rate_limit: '模型请求频率受限', model_quota: '模型服务额度不足',
    model_auth: '模型服务认证失败', model_unavailable: '模型服务暂不可用',
    range_exhausted: '选择范围已处理完，没有新条目', output_missing: '没有生成所需产物',
    service_restart: '服务进程中断，已保留已有记录'
  };
  var reasonText = reason ? ' · ' + (reasons[reason] || publicTaskStage(reason)) : '';
  if (status === 'complete' || status === 'completed') return '已完成';
  if (status === 'partial') {
    if (reason === 'source_unavailable') return '部分完成 · 来源暂时无法访问，已用已有资料';
    if (reason === 'timeout') return '部分完成 · 时间到，已保留已有结果';
    return '部分完成' + reasonText;
  }
  if (status === 'no_results') return '已完成，未找到结果';
  if (status === 'unavailable') return '执行记录不可用';
  if (status === 'cancelled') return '已停止';
  if (status === 'skipped') return '已跳过' + reasonText;
  if (status === 'not_run') return '未执行' + reasonText;
  return '未完成' + reasonText;
}

async function sendMsg(isFollowUp) {
  if (!currentAccount || !sessionId || accountDocumentRetired) return;
  var epoch = authEpoch, requestSessionId = sessionId;
  function ownsTurn() { return epoch === authEpoch && requestSessionId === sessionId && (!botContent || chatContainer.contains(botContent)); }
  function ownsControls() { return ownsTurn() && (requestRoundId === undefined || requestRoundId === currentRoundId); }
  if (isFollowUp === undefined) isFollowUp = false;
  // v8.7: 运行中忽略重复发送（防连按 Enter/双击造成双气泡与交错回答）
  var sendBtnEl = document.getElementById('send-btn');
  if (sendBtnEl && sendBtnEl.disabled) return;
  var query = userInput.value.trim();
  if (!query) return;
  var _requestStartedAt = performance.now();
  mascotSay('planning');
  var _turnTotalMs = 0;
  var _firstTokenElapsedMs = null;
  var filesForRequest = _selectedAttachments.slice();
  var fileRefsForRequest = window._selectedAccountFileRefs.slice();
  var skillsForRequest = skillPicker ? skillPicker.capture() : {items: []};

  // v8.3.7 M1: 每次点击生成稳定幂等 ID，重试复用（防重发重复写历史）
  var clientRequestId = 'crid-' + Date.now().toString(36) + '-' +
    Math.random().toString(36).slice(2, 10);

  currentRoundId++;
  var requestRoundId = currentRoundId;
  currentJobId = ''; window._currentJobId = '';
  currentQuery = query;
  renderContextPanel({max_tokens: _ctxMaxTokens(), request_id: clientRequestId, session_id: requestSessionId});

  // v8.4.11: 新发送清理停止提示，恢复输入框占位
  var stopMsg = document.getElementById('stop-msg');
  if (stopMsg) stopMsg.style.display = 'none';
  userInput.placeholder = '有什么想做的？';
  var userMessageContent = addMessage('user', escapeHtml(query) +
    (filesForRequest.length + fileRefsForRequest.length ? '<div class="sent-attachment-count">已附加 ' + (filesForRequest.length + fileRefsForRequest.length) + ' 个文件</div>' : ''));
  appendSentAttachmentPreviews(userMessageContent, filesForRequest);
  appendSentLibraryReferences(userMessageContent, fileRefsForRequest);
  appendMessageActions(userMessageContent, 'user', query);
  // P0: 提交瞬间先给导航一个临时刻度，服务端确认后换成正式 message_id
  if (window.turnRail && window.turnRail.addPending) window.turnRail.addPending(clientRequestId, query);
  userInput.value = '';
  setSendDisabled(true);

  var botContent = addMessage('bot', '');
  // P0-A: 实时气泡带与历史恢复相同的轮次锚点（同一套归属规则，文件/跳转可定位）
  if (botContent) { botContent.dataset.requestId = clientRequestId; botContent.dataset.sessionId = requestSessionId; }
  var botMessage = botContent && botContent.closest ? botContent.closest('.message') : botContent.parentNode;
  var isCancelled = false;
  var _turnDisconnected = false;  // 断线 ≠ 取消：后台可能仍在运行
  var _turnDisplayStatus = '';    // 服务端 TurnEnvelope 的唯一终态
  var _turnEnvelope = null;
  var spinnerTimeout = null;
  var requestAbortController = null;

  // Keep the answer and completed rows. Aborting receipt or requesting cancel
  // never supplies a business terminal state; replay confirms that state.
  function stopCurrentTurn(reason) {
    if (reason === 'account' || reason === 'switch') {
      isCancelled = true;
      if (taskTransport) taskTransport.stop();
      if (taskControls) taskControls.dispose();
      if (taskStore) taskStore.clear();
      clearTimeout(spinnerTimeout); clearInterval(_elapsedTimer);
      if (requestAbortController) requestAbortController.abort();
      requestAbortController = null;
      filesForRequest.length = 0; fileRefsForRequest.length = 0;
      return;
    }
    if (!ownsTurn() || isCancelled) return;
    if (!ownsControls()) {
      _turnDisconnected = true;
      if (!hasTerminalEvent) { appendNoticeRow('连接中断，任务状态待确认。'); finishStatus(); }
      if (taskStore.state.job_id) taskTransport.follow(taskStore.state.job_id);
      return;
    }
    if (reason === 'disconnect') {
      // Transport ended, business state did not. Keep the stop button wired so
      // the user can still cancel, and query the authoritative job state once.
      if (hasTerminalEvent || _turnDisconnected) return;
      if (taskStore.state.question && taskStore.state.question.status === 'pending') {
        _turnDisconnected = true; clearTimeout(spinnerTimeout);
        statusLabel.textContent = '执行轨迹：等待你的选择';
        if (ownsControls()) setSendDisabled(false);
        if (taskStore.state.job_id) taskTransport.follow(taskStore.state.job_id);
        return;
      }
      _turnDisconnected = true;
      if (spinnerTimeout) { clearTimeout(spinnerTimeout); spinnerTimeout = null; }
      var discAbort = requestAbortController;
      requestAbortController = null;
      if (discAbort) {
        try { discAbort.abort(); } catch (e) {}
        if (currentAbortController === discAbort) currentAbortController = null;
      }
      appendNoticeRow('连接中断 · 任务可能仍在后台运行', 'is-running');
      finishStatus();
      showBanner('连接中断；任务可能仍在后台运行。可刷新查看最终结果，或点击停止取消。');
      setSendDisabled(true);   // keep the stop button available
      var stoppedJobId = String(currentJobId || window._currentJobId || '');
      if (stoppedJobId) taskTransport.follow(stoppedJobId);
      return;
    }
    isCancelled = true;
    _turnDisconnected = true;
    clearTimeout(spinnerTimeout); clearInterval(_elapsedTimer);
    var ac = requestAbortController; requestAbortController = null;
    if (ac) {
      ac.abort(); if (currentAbortController === ac) currentAbortController = null;
    }
    appendNoticeRow('已请求停止；等待服务端确认。', 'is-skipped');
    finishStatus(); setSendDisabled(false);
    var stoppingJobId = taskStore.state.job_id || String(currentJobId || window._currentJobId || '');
    if (stoppingJobId) taskTransport.follow(stoppingJobId);
    if (activeTurnCancel === stopCurrentTurn) activeTurnCancel = null;
    try { userInput.focus(); } catch (_) {}
    userInput.placeholder = '有什么想做的？';
  }

  // Every turn owns its panel, including plain answers and unsuccessful turns.
  var processPanel = createResearchProcessPanel(query);
  var statusDiv = processPanel.element;
  var statusLabel = processPanel.label;
  var statusElapsed = processPanel.elapsed;
  botContent.appendChild(statusDiv);

  activeTurnCancel = stopCurrentTurn;
  spinnerTimeout = setTimeout(function() {
    if (ownsTurn() && !isCancelled) {
      stopCurrentTurn('disconnect');
      showBanner('本轮长时间无响应；正在查询后台任务状态。');
    }
  }, 300000);

  var answerDiv = document.createElement('div'); answerDiv.className = 'turn-answer';
  answerDiv.style.display = 'none';

  var citationsDiv = document.createElement('div');
  citationsDiv.style.display = 'none';

  botContent.appendChild(answerDiv);
  botContent.appendChild(citationsDiv);

  var fullText = '';
  var isAnswerStarted = false;
  var isFinalAnswerArmed = false;
  var preAnswerText = '';
  var renderPending = false;
  var hasTerminalEvent = false;
  var snapshotOnly = false;
  var existingJobNotice = false;
  var taskStore = CitrusTaskState.create({onEvent: handleEvent});
  var taskTransport = CitrusTaskTransport.create({store: taskStore, fetch: accountFetch, owns: ownsTurn,
    onSnapshot: reconcileTaskSnapshot,
    onError: function(error) { if (ownsControls()) showBanner(error.message || '任务状态暂时无法读取。'); }
  });
  var taskControls = CitrusTaskControls.create({container: botContent, store: taskStore, transport: taskTransport, owns: ownsTurn});
  var questionCard = CitrusTaskControls.createQuestion({container: botContent, store: taskStore, transport: taskTransport, owns: ownsTurn,
    onContinue: function (result) {
      if (ownsControls()) { setSendDisabled(false); refreshQuestionContinuation(result, ownsControls, questionCard.element); }
      else showQuestionContinuation(questionCard.element, result.child_job_id, ownsTurn);
    }});

  function reconcileTaskSnapshot(job) {
    if (!ownsTurn()) return;
    processPanel.receive('snapshot', job);
    if (taskStore.state.question && taskStore.state.question.status === 'pending' && !CitrusTaskState.displayStatus(job)) {
      statusLabel.textContent = '执行轨迹：等待你的选择';
      if (ownsControls()) { clearTimeout(spinnerTimeout); setSendDisabled(false); }
    }
    if (!ownsControls()) {
      if (CitrusTaskState.displayStatus(job)) {
        applyTurnEnvelope({turn_envelope: job.terminal_snapshot}); hasTerminalEvent = true; finishStatus();
        loadTurnGeneratedFiles(job.terminal_snapshot);
      }
      return;
    }
    if (taskStore.state.context_snapshot) renderContextPanel(taskStore.state.context_snapshot);
    if (_pendingPermission && _pendingPermission.job_id === taskStore.state.job_id && Array.isArray(job.approvals)) {
      var pendingApproval = job.approvals.find(function(approval) { return approval.approval_id === _pendingPermission.approval_id; });
      if (!pendingApproval || pendingApproval.status !== 'pending') {
        var staleApprovalCard = document.getElementById('permission-card'); if (staleApprovalCard) staleApprovalCard.remove();
        _pendingPermission = null;
      }
    }
    (job.approvals || []).forEach(function(approval) {
      if (approval.status === 'pending' && (!_pendingPermission || _pendingPermission.approval_id !== approval.approval_id)) {
        showPermissionCard(Object.assign({job_id: taskStore.state.job_id}, approval));
      }
    });
    if (!job.terminal_snapshot || !CitrusTaskState.displayStatus(job) || hasTerminalEvent) return;
    applyTurnEnvelope({turn_envelope: job.terminal_snapshot});
    hasTerminalEvent = true; snapshotOnly = true; _turnDisconnected = false;
    clearTimeout(spinnerTimeout); clearStageLine(); finishStatus(); setSendDisabled(false);
    loadTurnGeneratedFiles(job.terminal_snapshot);
    if (activeTurnCancel === stopCurrentTurn) activeTurnCancel = null;
    if (!fullText && !existingJobNotice) {
      answerDiv.style.display = '';
      answerDiv.textContent = '任务已结束；可从会话记录查看已保存的回答。';
    }
  }

  var _elapsedTimer = null;

  function elapsedText(ms) {
    var seconds = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
    return seconds < 60 ? seconds + ' 秒' : Math.floor(seconds / 60) + ' 分 ' + (seconds % 60) + ' 秒';
  }
  function startElapsedTimer() {
    if (_elapsedTimer) clearInterval(_elapsedTimer);
    _elapsedTimer = setInterval(function() {
      if (!ownsTurn()) { clearInterval(_elapsedTimer); return; }
      statusElapsed.textContent = '用时 ' + elapsedText(performance.now() - _requestStartedAt);
    }, 1000);
    statusElapsed.textContent = '用时 0 秒';
  }

  function applyTurnEnvelope(data) {
    var env = data && data.turn_envelope;
    var status = CitrusTaskState.displayStatus(data);
    _turnEnvelope = env || null;
    _turnDisplayStatus = status;
    answerDiv.dataset.displayStatus = status;
    return status;
  }

  function updateStatus(type, text) {
    if (!ownsTurn() || hasTerminalEvent) return;
    var stage = publicTaskStage(text);
    if (!stage) return;
    processPanel.note(stage);
  }
  window._activeTurnStatusUpdater = updateStatus;

  function appendNoticeRow(text) {
    processPanel.note(text);
  }

  function clearStageLine() {
    // Public process entries remain available when a completed panel reopens.
  }

  function clearStatus() {
    if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
    // Admission errors have no business envelope. Keep an honest, expandable
    // panel rather than making this turn's process UI disappear.
    statusDiv.classList.remove('is-running'); statusDiv.classList.add('is-done');
    statusLabel.textContent = '执行轨迹：未开始执行';
    processPanel.note('请求未进入执行，请查看回答中的提示。');
  }

  function finishStatus() {
    if (!ownsTurn()) return;
    if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
    // Receipt failure/cancel requests are not a business terminal state.
    if (!hasTerminalEvent) {
      statusLabel.textContent = '执行轨迹：状态待确认';
      statusDiv.classList.remove('is-done'); statusDiv.classList.add('is-running');
      statusDiv.open = true;
      return;
    }
    var totalMs = _turnTotalMs > 0 ? _turnTotalMs : (performance.now() - _requestStartedAt);
    statusElapsed.textContent = '用时 ' + elapsedText(totalMs);
    processPanel.terminal(_turnDisplayStatus);
  }

  function loadTurnGeneratedFiles(data) {
    if (!window.loadSessionFiles) return;
    var artifactIds = Array.isArray(data && data.artifact_ids) ? data.artifact_ids.map(String) : null;
    window.loadSessionFiles().then(function(payload) {
      // A later turn may be active. Only account/session/view changes retire
      // this completed turn's DOM. Account-library growth is not attribution.
      if (epoch !== authEpoch || requestSessionId !== sessionId || !payload || !chatContainer.contains(botContent)) return;
      appendTurnGeneratedFiles(botContent,
        filesAttributedToTurn(payload.generated, requestSessionId, clientRequestId, artifactIds));
    }).catch(function() {});
  }

  function renderAnswer() {
    if (!renderPending) {
      renderPending = true;
      requestAnimationFrame(function() {
        if (!ownsTurn()) return;
        var s = fullText.replace(/__NEED_COMPLEX__/g, '');
        var frx = /---\s*\*\*.. \u6df1\u5165\u63a2\u7d22.*?\*\*([\s\S]*?)$/;
        var m = s.match(frx);
        var main = s;
        var qs = [];
        if (m) { main = s.replace(frx, '').trim();
          var qb = m[1]; var ql = qb.split('\n');
          for (var qi = 0; qi < ql.length; qi++) {
            var q = ql[qi].trim().replace(/^\d+\.\s*/, '');
            if (q) qs.push(q);
          }
        }
        answerDiv.innerHTML = renderMarkdownSafe(main);
        if (qs.length > 0) {
          var cc = document.createElement('div'); cc.className = 'follow-up-container';
          var cl = document.createElement('div'); cl.className = 'follow-up-label'; cl.textContent = '深入探索（点击追问）'; cc.appendChild(cl);
          var cd = document.createElement('div'); cd.className = 'follow-up-chips';
          for (var qi = 0; qi < qs.length; qi++) { (function(qt){var c=document.createElement('button');c.className='follow-up-chip';c.textContent=qt;c.onclick=function(){handleSendFollowUp(qt)};cd.appendChild(c)})(qs[qi]); }
          cc.appendChild(cd); answerDiv.appendChild(cc);
        }
        if (ownsControls()) chatContainer.scrollTop = chatContainer.scrollHeight; renderPending = false;
      });
    }
  }

  function handleSendFollowUp(q) { userInput.value = q; sendMsg(true); }

  function showBanner(msg) {
    if (!ownsControls()) return;
    var b = document.getElementById('degraded-banner');
    if (!b) {
      b = document.createElement('div');
      b.id = 'degraded-banner';
      b.style.cssText = 'margin:8px auto;max-width:760px;padding:8px 12px;border-radius:10px;' +
        'background:var(--bg-surface);border:1px solid var(--border-hover);color:var(--text-primary);font-size:13px';
      var sc = document.getElementById('chatContainer') || chatContainer;
      sc.parentNode.insertBefore(b, sc);
    }
    b.textContent = msg;
    b.style.display = '';
    clearTimeout(showBanner._t);
    showBanner._t = setTimeout(function () { b.style.display = 'none'; }, 8000);
  }

  function handleEvent(type, data) {
    if (!ownsTurn()) return;
    processPanel.receive(type, data);
    // Retired rounds may finish replaying into their own DOM. They cannot
    // release the composer, alter context usage, permissions or the sidebar.
    if (!ownsControls()) {
      if (type === 'answer_start') isFinalAnswerArmed = true;
      if (type === 'text' && isFinalAnswerArmed) { fullText += data.content || ''; answerDiv.style.display = ''; answerDiv.innerHTML = renderMarkdownSafe(fullText); }
      if (type === 'text_reset') { fullText = ''; isFinalAnswerArmed = false; answerDiv.innerHTML = ''; }
      if ((type === 'done' || type === 'error') && CitrusTaskState.displayStatus(data)) {
        applyTurnEnvelope(data); hasTerminalEvent = true; clearTimeout(spinnerTimeout); finishStatus();
        if (data.answer) {
          fullText = data.answer; answerDiv.style.display = ''; answerDiv.innerHTML = renderMarkdownSafe(fullText);
          if (String(data.answer).trim()) processPanel.answerVisible();
        }
        loadTurnGeneratedFiles(data);
      }
      return;
    }
    if ((type === 'done' || type === 'error') && !CitrusTaskState.displayStatus(data)) {
      if (data.question || taskStore.state.question && taskStore.state.question.status === 'pending' || data.status === 'awaiting_input' || (data.turn_envelope || {}).display_status === 'awaiting_input') {
        statusLabel.textContent = '执行轨迹：等待你的选择'; clearTimeout(spinnerTimeout); setSendDisabled(false);
        if (data.answer) { fullText = data.answer; answerDiv.style.display = ''; answerDiv.innerHTML = renderMarkdownSafe(fullText); }
      } else { appendNoticeRow('任务状态待确认，正在等待执行记录。'); }
      return;
    }
    if ((type === 'done' || type === 'error') && hasTerminalEvent && !snapshotOnly) return;
    switch (type) {
      case 'reasoning': break; // Private model reasoning is never shown to users.
      case 'plan': break;
      case 'thinking': mascotSay('planning'); break;
      case 'tool_call_start':
      case 'tool_executing': mascotSay('retrieving'); break;
      case 'tool_progress':
      case 'tool_result':
      case 'agent_switch':
      case 'assistant_progress': break; // Public content is projected once above.
      case 'answer_start':
        isFinalAnswerArmed = true;
        break;
      case 'text':
        if (!isFinalAnswerArmed) {
          // Compatibility buffer for an old-style tool-round stream. New
          // backend never sends text before answer_start.
          preAnswerText += data.content;
          break;
        }
        if (!isAnswerStarted) {
          _firstTokenElapsedMs = performance.now() - _requestStartedAt;
          isAnswerStarted=true; clearTimeout(spinnerTimeout); mascotSay('generating');
          // The first visible answer chunk collapses the trace independently
          // of the authoritative task status; users can reopen it at any time.
          answerDiv.style.display='';
        }
        fullText+=data.content; renderAnswer(); break;
      case 'text_reset':
        // A1: 工具轮里的临时增量不是回答——作废已上屏文本；该轮内容随后由
        // assistant_progress 作为工作说明进入执行时间线。
        isFinalAnswerArmed = false; isAnswerStarted = false;
        if (fullText || preAnswerText) { fullText=''; preAnswerText=''; renderAnswer(); }
        break;
      case 'draft':
        // v8.17.15: 草稿全链删除——后端不再推送 draft 事件，忽略（防御残留）
        break;
      case 'context_snapshot':
      case 'context_status':
      case 'context_budget':
      case 'context_usage':
        if (data.request_id && data.request_id !== clientRequestId) break;
        if (data.session_id && data.session_id !== requestSessionId) break;
        if (taskStore.state.context_snapshot) renderContextPanel(taskStore.state.context_snapshot);
        break;
      case 'status':
        if (data.stage === 'awaiting_input' || data.status === 'awaiting_input') {
          statusLabel.textContent = '执行轨迹：等待你的选择'; clearTimeout(spinnerTimeout); setSendDisabled(false);
        }
        break;
      case 'retrieval_cache':
        break;
      case 'context_degraded':
        showBanner(String(data.reason || data.message || data.summary
          || '上下文压缩未完成；已保留关键证据继续回答。'));
        break;
      case 'permission_request':
        showPermissionCard(data);
        break;
      case 'permission_decision':
        if (_pendingPermission && _pendingPermission.approval_id === data.approval_id) {
          var approvalCard = document.getElementById('permission-card'); if (approvalCard) approvalCard.remove();
          _pendingPermission = null;
        }
        break;
      case 'task_update':
        break; // Version/pending state is reduced by CitrusTaskState.
      case 'operation':
        break; // Every operation uses the shared public process projection.
      case 'request_user_input':
      case 'question':
        if (taskStore.state.question && taskStore.state.question.status === 'pending') {
          statusLabel.textContent = '执行轨迹：等待你的选择';
          clearTimeout(spinnerTimeout); setSendDisabled(false);
        }
        break;

      case 'plan_ready':
      case 'section_start':
      case 'section_done': break;
      case 'citations':
        // v9.2: 引用编号统一重排在后端完成（renumber_refs）——事件附带 remap，
        // 前端按 remap 重写正文 [n]/[Wn]/[Hn] 与 cited ref_id 后写入 roundHistory，
        // 侧栏与正文编号一致（替代 v8.17.3 前端 W 专用压缩）。
        var _cited0 = data.cited || [];
        var _remap = data.remap || {};
        var _dropped = (data.dropped || []).map(String);
        // v9.4.4（关键修复）: 后端 payload 的 ref_id 已是重排后的最终编号，
        // 前端**不再**按 remap 改写 ref_id——历史 bug：把 remap 二次套用到
        // 已重排编号上，与"已是新号的号"碰撞 → 不同条目撞号/跳号（[4][9]、
        // [4][7]、[5]/[W4] 三次实测的统一根因）。remap 只用于重写正文旧编号；
        // dropped（后端清除的无映射死号）同步从正文移除（如 [13]/[W3]）。
        var _applied = (window._remapAppliedRounds = window._remapAppliedRounds || {});
        // v10.5（F1b）: pre_renumbered=true 表示 done.answer 已是**终稿编号**——由后端
        // 返修轮重跑 renumber_refs 得到（expert_graph.py 的 F1 块）。此时 done 事件
        // 已用它覆盖 fullText，若此处再套一次 remap 就是二次重排：正文号相对侧栏
        // ref_id 发生置换错配（丢号/重号）。故仅在**未**预重排时按 remap 重写正文；
        // dropped 的清除保留（预重排时已无残留，属幂等兜底）。
        // _fullTextFromDone 是更根本的判据（done.answer 恒为重排终稿），用于兜住
        // done 事件期间流式全失败、fullText 只能取自 done.answer 的情况。
        var _preRenumbered = !!data.pre_renumbered
                             || !!(window._fullTextFromDone || {})[currentRoundId];
        if ((Object.keys(_remap).length > 0 || _dropped.length > 0)
            && !_applied[currentRoundId]) {
          if (Object.keys(_remap).length > 0 && !_preRenumbered) {
            fullText = _rewriteRefsByMap(fullText, _remap);
          }
          for (var _di = 0; _di < _dropped.length; _di++) {
            var _tok = String(_dropped[_di]).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            fullText = fullText.replace(new RegExp('\\[' + _tok + '\\]', 'g'), '');
          }
          renderAnswer();
          _applied[currentRoundId] = 1;
        }
        // ── v10.14（阶段 3 · 修复「侧栏不显示联网条目」）────────────────────────
        //   实测根因：`citations` 事件里 `web_separate` 有 26–27 条（每条带
        //   `round_no` + `tier` + `tier_label` + `url` + `title`），但本文件
        //   **从未引用 `web_separate`**（grep 命中数 = 0）⇒ 侧栏"联网搜索"组
        //   恒为空，用户看到的就是"侧栏不显示"。
        //   修法：把 `web_separate` 映射成侧栏条目（`source:'web'` ⇒ 落到 web 组），
        //   存进 roundHistory 的新字段 `web`，由 `renderRoundSidebar` 并入渲染。
        //   注：`ref_id` 用 `W1..Wn`（**仅侧栏展示用**，不进正文引用池 —— 引用池
        //   隔离仍保留：正文第一段只挂本地 `[n]`）。
        var _webSep = (data.web_separate || []).map(function (w, wi) {
          return {
            // v10.13-B（用户决策：恢复 `[Wn]`）：`ref_id` 用于**内部对齐**
            // （后端已按"正文实际引用的 `[Wn]`"过滤过 web_separate，
            //  这里只是把编号带过来）。**侧栏不显示编号本身** —— 仍显示「标题 🔗」。
            ref_id: w.ref_id || '',
            type: 'web',
            source: 'web',
            url: w.url || '',
            title: w.title || w.host || '',
            doi: '',
            tier: w.tier || '',
            tier_label: w.tier_label || '',
            round_no: (w.round_no != null ? w.round_no : 1),
            _isSummary: !!w.is_summary
          };
        });
        roundHistory.push({roundId:currentRoundId,query:currentQuery,cited:_cited0,uncited:data.uncited||[],historical:data.historical||[],web:_webSep});
        renderRoundSidebar(); citationsDiv.style.display='';
        var cited = _cited0;
        if (cited.length>0) {
          var c=document.createElement('div'); c.style.cssText='margin-top:10px;padding-top:10px;border-top:1px solid var(--border-hairline)';
          c.innerHTML='<div style="font-size:11px;color:var(--text-muted);margin-bottom:5px">引用 ('+cited.length+'篇)'+(data.ref_pool?' · '+data.ref_pool:'')+'</div>';
          // v8.15.2: 徽章条按 ref_id 升序（此前按回答出现顺序显得混乱）
          var order = _refIdSort(cited);
          for (var k=0;k<order.length;k++) c.innerHTML+='<span style="display:inline-block;font-family:var(--font-mono);font-size:10px;background:var(--success-glow);color:var(--success);padding:1px 5px;border-radius:3px;margin:2px;font-weight:600">['+escapeHtml(order[k].ref_id)+']</span>';
          citationsDiv.appendChild(c);
        }
        break;
      case 'job':
        // S1: 记下本轮 job_id——点停止时据此精确取消这一个任务。
        currentJobId = String(data.job_id || '');
        window._currentJobId = currentJobId;
        taskTransport.refresh(currentJobId, false).catch(function() {});
        break;
      case 'memory_saved':
        var _memoryCount = Number(data.count || 0);
        if (_memoryCount > 0) {
          showBanner('已保存 ' + _memoryCount + ' 条账号记忆。');
          var _memoryOverlay = document.getElementById('memorySettingsOverlay');
          if (_memoryOverlay && !_memoryOverlay.hidden) openMemorySettings();
        }
        break;
      case 'memory_save_failed':
        showBanner('账号记忆暂时未能保存，下次打开记忆面板会自动重试。');
        break;
      case 'done':
        snapshotOnly = false;
        hasTerminalEvent = true;
        _turnDisconnected = false;
        clearStageLine();
        _turnTotalMs = Number(data.total_elapsed_ms) || 0;   // 服务端实测整轮耗时
        // One authoritative reducer: the server TurnEnvelope owns the terminal
        // state. Local tool rows never promote a partial turn to completed.
        var _terminal = applyTurnEnvelope(data);
        // The mascot has no "partial" state; only a fully completed turn gets
        // the success animation, everything else stays neutral or fails.
        mascotSay(_terminal === 'completed' ? 'completed'
          : _terminal === 'failed' ? 'failed' : 'idle');
        if (data.warning) showBanner(String(data.warning));
        if (_turnEnvelope && _turnEnvelope.history_saved === false) {
          showBanner('本轮历史未确认写入；刷新后可能无法恢复。');
        }
        if (_turnEnvelope && _turnEnvelope.notices && _turnEnvelope.notices.length) {
          showBanner(_turnEnvelope.notices.join('；'));
        }
        // P0: 本轮已落库——拉一次最新索引，把临时刻度换成真实 ID
        if (window.turnRail && window.turnRail.sync) window.turnRail.sync(clientRequestId, query);
        clearTimeout(spinnerTimeout); isCancelled=true; answerDiv.style.display=''; finishStatus();
        window._activeTurnStatusUpdater = null;
        if (activeTurnCancel === stopCurrentTurn) activeTurnCancel = null;
        // v8.3.7 M3: 引用无检索支撑 → 轻提示（不阻断回答）
        if (data.citation_unsupported) {
          showBanner('回答中的部分引用尚未核实，请引用前检查原文。');
        } else if (data.citation_mismatch) {
          showBanner('部分引用与本轮资料未能对应，请核对原文。');
        }
        // v10.0（卡点③）: 修复循环的修正答案不流式上屏，须以 done.answer 覆盖草稿
        var _repaired = !!(data.audit && data.audit.repaired);
        // v10.5（F6，用户口径）: **判官看的是 done.answer，人看的也必须是 done.answer**
        //   ——即"重编号后的最终答案"。后端已在出口保证 done.answer 为终稿编号
        //   （expert_graph.py 的 F6 块），因此这里一律以它为准覆盖草稿。
        //   此前只在 audit.repaired 为真时覆盖，导致两类错位：
        //     ① 强制收尾题（预算/熔断/跑满轮次）用户看到的是"历轮草稿拼接"，
        //        判官评的却是终稿 —— final84 实测 38/84 题落此坑；
        //     ② 被改写但未置 repaired 标志的题同理。
        //   例外：done.answer 为空或为错误串时保留草稿（错误事件另有提示）。
        var _fromDone = (window._fullTextFromDone = window._fullTextFromDone || {});
        var _ansOk = !!data.answer && data.answer.indexOf('An error occurred') !== 0;
        if (_ansOk) {
          fullText = data.answer;
          _fromDone[currentRoundId] = 1;
          if (_repaired) {
            showBanner('回答已完成一致性检查。');
          }
        } else if (!fullText && data.answer) {
          fullText = data.answer;
          _fromDone[currentRoundId] = 1;
        }
        // A cancelled or empty turn has no answer, but it still has a real
        // terminal text (stop marker / honest notice). Show it instead of an
        // empty bubble; never fabricate answer prose.
        if (!fullText && data.stop_notice) {
          fullText = String(data.stop_notice);
          _fromDone[currentRoundId] = 1;
        }
        if (!fullText && data.notice && _terminal !== 'completed') {
          fullText = String(data.notice);
          _fromDone[currentRoundId] = 1;
        }
        // v10.0（卡点③）: 审计结果轻提示（critical / 需求未满足）
        try {
          var _au = data.audit || {};
          var _sa = (_au.stage_a || {}).severity || 'ok';
          var _sb = (_au.stage_b || {}).severity || '';
          if (_sa === 'critical' || _sb === 'critical') {
            showBanner('回答仍有待核实之处，请结合原文判断。');
          } else if (_au.stage_b && _au.stage_b.requirement_met === false) {
            showBanner('回答可能未覆盖问题的全部要求，请检查后续内容。');
          }
        } catch (_ae) {}
        if (fullText) {
          answerDiv.innerHTML=renderMarkdownSafe(fullText.replace(/__NEED_COMPLEX__/g,''));
        }
        if (fullText.indexOf('__NEED_COMPLEX__')>=0) {
          var uc=document.createElement('div');uc.style.cssText='margin-top:12px;text-align:center';
          var ub=document.createElement('button');ub.className='need-complex-btn';ub.textContent='继续深入检索';
          ub.onclick=function(){userInput.value=currentQuery;sendMsg()};
          uc.appendChild(ub);botContent.appendChild(uc);
        }
        botContent.appendChild(createElapsedBadge(_firstTokenElapsedMs != null ? _firstTokenElapsedMs : (performance.now() - _requestStartedAt)));
        if (data.session_id) sessionId=data.session_id;if(sessionId)localStorage.setItem(sessionStorageKey(),sessionId);
        window.dispatchEvent(new Event('citrus:turn-finished'));
        appendMessageActions(botContent, 'assistant', fullText.replace(/__NEED_COMPLEX__/g, ''));
        loadTurnGeneratedFiles(data);
        // v8.10: 回答完整生成后才显示 👍/👎 反馈栏（主流时序）
        appendFeedback(answerDiv.closest('.message'));
        if (window.CitrusCorpusReader && typeof window.CitrusCorpusReader.attachTurnSources === 'function') {
          window.CitrusCorpusReader.attachTurnSources(botMessage || botContent.closest('.message'), clientRequestId);
        }
        setSendDisabled(false);userInput.focus();break;
      case 'error':
        snapshotOnly = false;
        hasTerminalEvent = true;
        // A server error frame is a terminal frame too: consume the same
        // envelope reducer instead of hard-coding a local failure label.
        applyTurnEnvelope(data);
        window._activeTurnStatusUpdater = null;
        mascotSay('failed');
        // P0: 这一轮没有落库成功，撤掉临时刻度
        if (window.turnRail && window.turnRail.dropPending) window.turnRail.dropPending(clientRequestId);
        clearTimeout(spinnerTimeout);isCancelled=true;setSendDisabled(false);userInput.focus();
        if (activeTurnCancel === stopCurrentTurn) activeTurnCancel = null;
        if(!fullText){finishStatus();answerDiv.style.display='';answerDiv.textContent='这次没有完成，请稍后重试。';}
        else { finishStatus(); showBanner('回答提前中断，当前内容可能不完整。'); }
        loadTurnGeneratedFiles(data);
        break;
      case 'heartbeat':
        if (!hasTerminalEvent) statusElapsed.textContent = '用时 ' + elapsedText(data.elapsed_ms || performance.now() - _requestStartedAt);
        break;
      default: break;
    }
  }

  function showExistingJob(data) {
    if (!ownsTurn()) return;
    existingJobNotice = true;
    clearTimeout(spinnerTimeout);
    isCancelled = true; // This POST has finished; the original job has not been cancelled.
    if (activeTurnCancel === stopCurrentTurn) activeTurnCancel = null;
    window._activeTurnStatusUpdater = null;
    currentJobId = String(data.job_id || '');
    window._currentJobId = currentJobId;
    taskStore.hydrate(data);
    answerDiv.style.display = '';
    answerDiv.replaceChildren();
    var note = document.createElement('p');
    function describe(job) {
      var names = {running:'处理中', detached:'后台处理中', cancel_requested:'正在停止',
        completed:'已完成', failed:'未完成', cancelled:'已停止', interrupted:'已中断'};
      var snapshot = job.terminal_snapshot || {};
      var status = (job.status === 'interrupted' || job.current_step === 'interrupted') ? names.interrupted
        : snapshot.display_status === 'partial' ? '部分完成' : (names[job.status] || '状态待确认');
      note.textContent = '这条请求已受理，未重复执行。原任务：' + status + '。';
      statusLabel.textContent = '执行轨迹：' + status;
      processPanel.note('查看原任务的执行状态');
      processPanel.terminal(CitrusTaskState.displayStatus(job));
    }
    describe(data);
    answerDiv.appendChild(note);
    var check = document.createElement('button');
    check.type = 'button'; check.textContent = '查看任务状态';
    check.onclick = async function () {
      if (!ownsTurn()) return;
      check.disabled = true;
      try {
        var job = await taskTransport.refresh(currentJobId);
        if (ownsTurn() && job) describe(job);
      } catch (_) {
        if (ownsTurn()) note.textContent = '暂时无法读取原任务状态；请求没有重复执行，请稍后再查看。';
      } finally { if (ownsTurn()) check.disabled = false; }
    };
    answerDiv.appendChild(check);
    if (data.session_id && data.current_step !== 'admission_failed') {
      var history = document.createElement('button');
      history.type = 'button'; history.textContent = '查看原会话';
      history.onclick = function () {
        if (!ownsTurn()) return;
        if (sessionId !== data.session_id) switchSession(data.session_id);
        else restoreSessionHistory();
      };
      answerDiv.appendChild(history);
    }
    setSendDisabled(false);
    userInput.focus();
  }

  function streamOnce() {
    if (!ownsTurn()) return;
    // Start this request once; permission approval resumes its existing execution.
    fullText = '';
    preAnswerText = '';
    isAnswerStarted = false;
    isFinalAnswerArmed = false;
    renderPending = false;
    _turnDisconnected = false;
    _turnDisplayStatus = '';
    _turnEnvelope = null;
    _turnTotalMs = 0;                 // 服务端实测整轮耗时（done 事件下发）
    _requestStartedAt = performance.now();
    answerDiv.style.display = '';
    answerDiv.innerHTML = '';
    updateStatus('thinking', '正在理解你的请求…');
    statusElapsed.textContent = '';
    statusDiv.className = 'tool-trace research-process is-running'; statusDiv.open = true;
    hasTerminalEvent = false;
    startElapsedTimer();

    if (currentAbortController) currentAbortController.abort();
    var streamAbortController = new AbortController();
    requestAbortController = streamAbortController;
    currentAbortController = streamAbortController;

    (async function() {
      try {
        var attachments = await Promise.all(filesForRequest.map(async function(file) { return {name:file.name,type:file.type,data:await fileAsDataUrl(file)}; }));
        if (!ownsTurn() || streamAbortController.signal.aborted) return;
        var response = await accountFetch(API_URL, {method:'POST',headers:{'Content-Type':'application/json'},signal:streamAbortController.signal,body:JSON.stringify({session_id:requestSessionId,query:query,client_request_id:clientRequestId,visualization_enabled:visualizationEnabled,selected_skills:skillsForRequest.items,attachments:attachments,file_refs:fileRefsForRequest.map(function(f){return f.file_id;})})});
        if (!ownsTurn() || streamAbortController.signal.aborted) return;
        if (response.ok && skillPicker) skillPicker.acknowledge(skillsForRequest);
        if (response.ok && response.headers &&
            String(response.headers.get('content-type') || '').indexOf('application/json') >= 0) {
          var replay = await response.json();
          if (!ownsTurn() || streamAbortController.signal.aborted) return;
          if (replay.code !== 'existing_job') throw new Error('Unexpected chat response');
          showExistingJob(replay);
          return;
        }
        if (response.ok && (filesForRequest.length || fileRefsForRequest.length)) {
          window.dispatchEvent(new Event('citrus:turn-finished'));
        }
        if (response.ok && (filesForRequest.length || fileRefsForRequest.length)) {
          _selectedAttachments = _selectedAttachments.filter(function(f) { return filesForRequest.indexOf(f) < 0; });
          window._selectedAccountFileRefs = window._selectedAccountFileRefs.filter(function(f) { return fileRefsForRequest.indexOf(f) < 0; });
          renderAttachmentSelection();
        }
        if (!response.ok) {
          var conflictMessage = '';
          if (response.status === 409) {
            var conflict = await response.json().catch(function () { return {}; });
            if (!ownsTurn() || streamAbortController.signal.aborted) return;
            if (conflict.detail && conflict.detail.code === 'idempotency_conflict') {
              conflictMessage = '相同请求标识已用于不同内容；请重新发送为新请求。';
            } else if (conflict.detail && conflict.detail.code === 'skill_selection_changed') {
              conflictMessage = '所选技能已变更或不可用，请打开“＋”刷新技能目录后重新选择。';
            }
          }
          clearTimeout(spinnerTimeout); isCancelled = true;
          if (activeTurnCancel === stopCurrentTurn) activeTurnCancel = null;
          clearStatus(); answerDiv.style.display = '';
          answerDiv.textContent = conflictMessage || (response.status === 401 ? '请先登录后继续。'
            : response.status === 413 ? '附件过大或数量超限，请调整后重试。'
            : response.status === 429 ? '当前请求较多，请稍后重试。'
            : response.status === 503 ? '资料暂时无法处理，请稍后重试。' : '这次没有完成，请稍后重试。');
          setSendDisabled(false); userInput.focus(); return;
        }
        await taskTransport.stream(response, streamAbortController.signal);
        if (!hasTerminalEvent && !isCancelled) {
          stopCurrentTurn('disconnect');
          if (!(taskStore.state.question && taskStore.state.question.status === 'pending') && ownsControls()) showBanner('连接提前结束；已完成的步骤保留。');
        }
      } catch(err){
        if (!ownsTurn() || requestAbortController !== streamAbortController) return;
        if (err.name === 'AbortError') stopCurrentTurn('user');
        else {
          stopCurrentTurn('disconnect');
          showBanner('连接暂时中断；已完成的步骤保留。');
        }
      }
      finally{
        if (currentAbortController === streamAbortController) currentAbortController = null;
        if (activeTurnCancel === stopCurrentTurn && (isCancelled || hasTerminalEvent)) activeTurnCancel = null;
      }
    })();
  }

  streamOnce();

  // v8.15: 证据来源展示元数据（来源分组手风琴 + 条目徽标；新增来源只加一行）
  // v9.2 (P0#4): 硬编码仅限 4 个内置来源的名称/顺序；未知来源（如 pipeline1
  // --source-type 新标注）动态生成同名分组 —— 见 srcKey()，分组本身代码驱动。
  var SOURCE_META = {
    rag:  { tag: 'RAG',  name: '本地文献库', cls: 'rag' },
    ucr:  { tag: 'UCR',  name: 'UCR品种库',  cls: 'ucr' },
    web:  { tag: 'Web',  name: '联网搜索',   cls: 'web' },
    historical: { tag: '历史', name: '历史证据', cls: 'historical' },
    // v10.13（改动 3.2）：联网按来源分级拆成三组（用户口径：T1 优先排列）
    web_t1: { tag: 'T1', name: '联网 · T1 同行评议 / 官方 / 学术数据库', cls: 'web' },
    web_t2: { tag: 'T2', name: '联网 · T2 权威与行业媒体 / 机构新闻稿', cls: 'web' },
    web_t3: { tag: 'T3', name: '联网 · T3 自媒体 / 论坛 / 来源不明',     cls: 'web' }
  };
  function srcMeta(item) {
    var s = (item && (item.source || item._src)) || '';
    var sm = SOURCE_META[s];
    if (sm) return sm;
    if (!s) return SOURCE_META['rag'];
    var key = _normSourceKey(s);
    return { tag: key, name: key, cls: 'custom' };
  }
  // v9.4 (P0#4 增强): 来源分组键规范化——非内置来源去尾部数字
  // （paper1→paper、Citrus varieties1→Citrus varieties），同名文件夹在前端
  // 经代码提取自动收纳为同类型分组；内置 4 组（rag/ucr/web/historical）原样。
  function _normSourceKey(s) {
    s = String(s || '').trim();
    if (s === 'rag' || s === 'ucr' || s === 'web' || s === 'historical') return s;
    var norm = s.replace(/\d+$/, '').trim();
    return norm || s;
  }
  // v9.2 (P0#4): 来源分组键——内置 4 组原样；未知来源（pipeline1 source_type）
  // 经 _normSourceKey 归组，使新来源（paper1..8 → paper、Citrus varieties1..3 →
  // Citrus varieties）自动生成侧栏手风琴组（不再并入 RAG）。
  // ── v10.13（改动 3.2/3.4）：侧栏按 **4 组固定顺序**归组 ──────────────────
  //   引用
  //   ├─ 本地文献                        （rag / ucr / historical 全归此组）
  //   ├─ 联网 · T1 同行评议 / 官方 / 学术数据库
  //   ├─ 联网 · T2 权威与行业媒体 / 机构新闻稿
  //   └─ 联网 · T3 自媒体 / 论坛 / 来源不明
  //   说明：历史组（`historical`）已按 3.4 归入「本地文献」——`Hn` 已退役，
  //   历史不再是独立引用通道（见 reports/38 §3）。
  function srcKey(item) {
    var s = (item && (item.source || item._src)) || '';
    var t = String((item && item.type) || '').toLowerCase();
    // 联网条目：按来源分级拆组
    if (s === 'web' || t === 'web') {
      var tier = String((item && item.tier) || '').toUpperCase();
      if (tier === 'T1') return 'web_t1';
      if (tier === 'T2') return 'web_t2';
      return 'web_t3';                       // T3 / TU / 未标注 一律归 T3 组
    }
    // 本地文献（rag / ucr / historical / 其它）统一归「本地文献」
    return 'rag';
  }
  // v8.15: 条目渲染——加来源徽标 + UCR 专属字段（无 DOI 时显示登记号/品种名）
  function renderCitationItem(item) {
    var sm = srcMeta(item);
    var isUcr = sm.cls === 'ucr';
    var du = item.doi && item.doi !== 'N/A' ? 'https://doi.org/' + encodeURIComponent(item.doi) : '#';
    var b = item.type === 'main' ? 'citation-badge' : 'citation-badge uncited';
    var sl = item.type === 'dynamic' ? 'Agent 发现' : (item.type === 'web' ? 'Web' : (item.type === 'historical' ? '历史证据' : (item.score ? 'CE=' + item.score.toFixed(3) : '')));
    var h = '<div class="citation-item" onclick="this.classList.toggle(\'expanded\')">';
    // ── v10.15（C.1 + C.2）：侧栏渲染为「**标题 + 🔗**」 ─────────────────────
    //   用户口径（报告 45 问题 2）：侧栏此前显示 `[W2] Web:` —— **标题行根本不存在**
    //   （`renderCitationItem` 整段没有一处读 `item.title`）。
    //   现改为：**不显示 `[Wn]` 编号、不显示 `Web:` 前缀**，只显示
    //   `标题` + 紧随其后的 `🔗`（点击跳转）。分组（本地/T1/T2/T3）保持不变。
    var _title = item.title || item.name || item.host || '未命名';
    var _turl = item.url || (item.doi && item.doi !== 'N/A'
      ? 'https://doi.org/' + encodeURIComponent(item.doi) : '');
    h += '<div class="cite-row">';
    h += '<span class="cite-title">' + escapeHtml(_title) + '</span>';
    if (_turl) {
      h += '<a href="' + escapeHtml(_turl) + '" target="_blank" class="cite-link"'
        + ' onclick="event.stopPropagation()" title="' + escapeHtml(_turl) + '">🔗</a>';
    }
    h += '</div>';
    // 折叠区（点击条目展开）：保留原有元数据，不再占据首行
    h += '<div class="citation-header citation-meta">';
    h += '<span class="src-badge ' + sm.cls + '">' + escapeHtml(sm.tag) + '</span>';
    h += '<span class="citation-score">' + escapeHtml(sl) + '</span></div>';
    // ── v10.13（改动 3.3）：联网条目显示为「标题 🔗」（🔗 点击跳转）────────
    //   用户口径：正文里的"标题 + 明文 URL 列表"全部移到侧栏。
    // ── v10.15（C.2）：标题 + 🔗 已上移到首行（`.cite-row`）⇒ 此处只保留
    //   **非联网**条目的标识信息（UCR 登记号 / DOI），避免重复渲染。
    if (isUcr && item.registry_id) {
      h += '<div class="citation-doi" style="color:var(--accent)">登记号: ' + escapeHtml(item.registry_id) + '</div>';
    } else if ((item.type === 'main' || item.type === 'dynamic')
               && item.doi && item.doi !== 'N/A') {
      h += '<a href="' + du + '" target="_blank" class="citation-doi" onclick="event.stopPropagation()">' + escapeHtml(item.doi) + '</a>';
    }
    // ── v10.16 实测修复：**侧栏 T1/T2/T3 与标题重复** ────────────────────────
    //   现象（用户真机截图）：侧栏把来源分级**重复列出** ——
    //     组标题已经写着「联网 · T1 同行评议 / 官方 / 学术数据库」，
    //     条目里**又**渲染一行「来源分级: T1（…）」；
    //     且标题已在首行 `.cite-title` 显示，折叠区**又**渲染「Title: …」。
    //   修法：**折叠态只留「标题 + 🔗」**（用户口径），其余元数据一律收进
    //   `.citation-detail`（点击才展开）。**不删数据**，只是不再常显。
    h += '<div class="citation-detail">';
    if (isUcr && item.variety_name) {
      h += '<div class="citation-text"><strong>品种:</strong> ' + escapeHtml(item.variety_name) + '</div>';
    }
    if (item.section_name) h += '<div class="citation-text"><strong>Section:</strong> ' + escapeHtml(item.section_name) + '</div>';
    // v10.5（§6.4 可溯源）: 联网条目展示来源分级与六层管线的判定痕迹
    //   tier/tier_reason 来自确定性分级（source_tiers）；filter_stage/content_chars/
    //   relevance_score/rerank_score 来自六层管线（只记录不裁剪时也有值）。
    // v10.16: 此处**只在折叠区**展示（组标题已表达 tier，常显会重复列出 T1/T2/T3）。
    if (item.source_tier) {
      h += '<div class="citation-text"><strong>来源分级:</strong> ' + escapeHtml(item.source_tier)
        + (item.tier_reason ? '（' + escapeHtml(item.tier_reason) + '）' : '') + '</div>';
    }
    if (item.filter_stage) {
      h += '<div class="citation-text"><strong>过滤:</strong> ' + escapeHtml(item.filter_stage)
        + (item.content_chars != null ? ' · 正文 ' + escapeHtml(String(item.content_chars)) + ' 字' : '')
        + (item.relevance_score != null ? ' · 相关性 ' + escapeHtml(String(item.relevance_score)) : '')
        + (item.rerank_score != null ? ' · 精排 ' + escapeHtml(String(item.rerank_score)) : '')
        + '</div>';
    }
    // v9.2: 恢复视图条目携带来源轮次元数据（live 条目无 round_seq 不显示）
    if (item.round_seq) h += '<div class="citation-text"><strong>Round:</strong> 第 ' + item.round_seq + ' 轮</div>';
    if (item.text_preview) h += '<div class="citation-text">' + escapeHtml((item.text_preview || '').substring(0, 250)) + '</div>';
    // v10.16: `Title:` 不再重复渲染 —— 标题已在首行 `.cite-title` 显示。
    //   UCR 条目的首行也是标题，同样无需重复。
    if (item.authors) h += '<div class="citation-text"><strong>Authors:</strong> ' + escapeHtml(item.authors) + '</div>';
    if (item.year) h += '<div class="citation-text"><strong>Year:</strong> ' + escapeHtml(item.year) + '</div>';
    if (item.chunk_id) h += '<div class="citation-text"><strong>Chunk:</strong> ' + escapeHtml(item.chunk_id) + '</div>';
    h += '</div></div>';
    return h;
  }
  // v8.15: 来源分组手风琴（同一 Round 内同时只展开一组）
  // v8.15.2: toggleSrcGroup 已提升为全局函数（见 toggleRound 下方）——
  // 此前的内部定义导致内联 onclick 全局解析失败，RAG/UCR 来源组无法展开收缩。
  /* v9.2: 刷新恢复视图——按来源 4 组手风琴（与 live 侧栏同构，共用
     src-group 结构与 renderCitationItem；条目携带来源轮次元数据） */
  function renderRestoredGroups() {
    if (!_restoredGroups) return false;
    // v9.4: 恢复视图同样支持动态来源组（与 live 侧栏同口径，未知组排在内置 4 组之后）
    var dyn = [];
    var gkeys = Object.keys(_restoredGroups || {});
    for (var gk = 0; gk < gkeys.length; gk++) {
      if (['rag', 'ucr', 'web', 'historical'].indexOf(gkeys[gk]) === -1) dyn.push(gkeys[gk]);
    }
    var order = ['rag', 'ucr', 'web', 'historical'].concat(dyn);
    var total = 0;
    for (var g = 0; g < order.length; g++) total += (_restoredGroups[order[g]] || []).length;
    citationCount.textContent = String(total) + (_restoredRefPool ? ' · ' + _restoredRefPool : '');
    var html = '';
    var firstOpen = true;
    for (var gi = 0; gi < order.length; gi++) {
      var gk = order[gi];
      var items = _restoredGroups[gk] || [];
      if (items.length === 0) continue;
      var sm2 = SOURCE_META[gk] || srcMeta({ source: gk });
      var gid = 'restore-' + gk;
      var gOpen = firstOpen;
      firstOpen = false;
      html += '<div class="src-group">';
      html += '<div class="src-group-header" onclick="toggleSrcGroup(\'' + gid + '\')">';
      html += '<span class="toggle-icon' + (gOpen ? '' : ' collapsed') + '">▶</span>';
      html += '<span class="src-badge ' + sm2.cls + '">' + escapeHtml(sm2.tag) + ':</span>';
      html += '<span class="round-title" style="font-size:11px">' + escapeHtml(sm2.name) + '</span>';
      html += '<span class="round-count">' + items.length + '</span></div>';
      html += '<div class="src-group-body" id="srcgroup-' + gid + '"' + (gOpen ? '' : ' style="display:none"') + '>';
      // v8.15.2: 组内按 ref_id 升序（重排后数字/W/H 均连续，_refIdSort 保序）
      // v10.13（Task E，用户指示）：**web 组内改按来源分级排序** T1 → T2 → T3 → TU，
      //   同 tier 内再按 ref_id。用户要求"每一个 url 分开，T1T2T3 优先排列"。
      //   其它组（本地文献库 / UCR / 历史）保持原 ref_id 升序，不受影响。
      // v10.13：web 组已按 tier 拆组，组内改用 `_webRefSort`（按 Wn 数字升序）；
      // 本地文献组用 `_refIdSort`（[n] 升序）。
      var gItems = (gk.indexOf('web_') === 0) ? _webRefSort(items) : _refIdSort(items);
      for (var m2 = 0; m2 < gItems.length; m2++) {
        html += renderCitationItem(gItems[m2]);
      }
      html += '</div></div>';
    }
    if (!html) return false;
    sidebarContent.innerHTML = html;
    return true;
  }

  function renderRoundSidebar() {
    if (roundHistory.length === 0) {
      // v9.2: 无实时轮次时优先显示刷新恢复的 4 组合并视图
      if (renderRestoredGroups()) return;
      sidebarContent.innerHTML = '<div class="empty-sidebar">提交问题后文献将在此展示</div>';
      citationCount.textContent = '0';
      return;
    }
    var tC = 0, tU = 0;
    for (var i = 0; i < roundHistory.length; i++) {
      tC += roundHistory[i].cited.length; tU += roundHistory[i].uncited.length;
    }
    citationCount.textContent = (tC + tU);
    var html = '';
    for (var i = roundHistory.length - 1; i >= 0; i--) {
      var round = roundHistory[i];
      // v8.15: 不再过滤 web 条目——按来源分组展示（本地文献库/UCR品种库/联网搜索/历史）
      var all = [];
      for (var k = 0; k < round.cited.length; k++) all.push(round.cited[k]);
      for (var k = 0; k < round.uncited.length; k++) all.push(round.uncited[k]);
      for (var k = 0; k < (round.historical || []).length; k++) all.push(round.historical[k]);
      // v10.14（阶段 3）：并入联网条目（`web_separate` ⇒ `round.web`）。
      //   这是「侧栏不显示联网条目」的修复点：此前该数组根本没进 `all`。
      for (var k = 0; k < (round.web || []).length; k++) all.push(round.web[k]);
      if (all.length === 0) continue;
      var qp = round.query.length > 25 ? round.query.substring(0, 25) + '...' : round.query;
      var open = i === roundHistory.length - 1;
      html += '<div class="round-group">';
      html += '<div class="round-header" onclick="toggleRound(\'round-' + round.roundId + '\')">';
      html += '<span class="toggle-icon" id="round-' + round.roundId + '-icon">▶</span>';
      html += '<span class="round-title">Round ' + round.roundId + ': ' + qp + '</span>';
      html += '<span class="round-count">' + all.length + '</span></div>';
      html += '<div class="round-body" id="round-' + round.roundId + '"' + (open ? '' : ' style="display:none"') + '>';
      // v8.15: 按来源分组（固定顺序：本地文献库→UCR品种库→联网搜索→历史证据）
      // v9.2 (P0#4): 未知来源（非 4 内置组）动态归组，排在固定 4 组之后
      var groups = {};
      for (var m = 0; m < all.length; m++) {
        var key = srcKey(all[m]);
        if (!groups[key]) groups[key] = [];
        groups[key].push(all[m]);
      }
      // v10.13（改动 3.2）：固定 4 组顺序 —— 本地文献 → 联网 T1 → T2 → T3
      // ── v10.19 修复：**分组标题重复列出**（用户截图：T1 出现两次、T3 出现两次）──
      //   缺陷：动态段只排除了 `rag/ucr/web/historical`，**没排除 `web_t1/t2/t3`**——
      //   而这三个键在前半段已列过 ⇒ `groupOrder` 里各出现**两次** ⇒ 同组渲染两遍。
      //   实测吻合：T1 两次、T3 两次，T2 因空被 `continue` 跳过（故只两次两组）。
      //   修法：把 `web_t1/web_t2/web_t3` 一并纳入排除集（固定段用固定顺序，动态段只收未知组）。
      var _FIXED_KEYS = ['rag', 'ucr', 'web', 'historical',
                         'web_t1', 'web_t2', 'web_t3'];
      var groupOrder = ['rag', 'web_t1', 'web_t2', 'web_t3']
        .concat(Object.keys(groups).filter(function (k) {
          return _FIXED_KEYS.indexOf(k) === -1;
        }));
      var firstOpen = true;
      for (var gi = 0; gi < groupOrder.length; gi++) {
        var gk = groupOrder[gi];
        if (!groups[gk] || groups[gk].length === 0) continue;
        var sm2 = SOURCE_META[gk] || srcMeta({ source: gk });
        var gid = round.roundId + '-' + gk;
        var gOpen = open && firstOpen;
        firstOpen = false;
        html += '<div class="src-group">';
        html += '<div class="src-group-header" onclick="toggleSrcGroup(\'' + gid + '\')">';
        html += '<span class="toggle-icon' + (gOpen ? '' : ' collapsed') + '">▶</span>';
        html += '<span class="src-badge ' + sm2.cls + '">' + escapeHtml(sm2.tag) + ':</span>';
        html += '<span class="round-title" style="font-size:11px">' + escapeHtml(sm2.name) + '</span>';
        html += '<span class="round-count">' + groups[gk].length + '</span></div>';
        html += '<div class="src-group-body" id="srcgroup-' + gid + '"' + (gOpen ? '' : ' style="display:none"') + '>';
        // v8.15.2: 组内按 ref_id 升序（回答乱序引用时侧栏保持编号顺序整齐）
        var gItems = _refIdSort(groups[gk]);
        for (var m2 = 0; m2 < gItems.length; m2++) {
          html += renderCitationItem(gItems[m2]);
        }
        html += '</div></div>';
      }
      html += '</div></div>';
    }
    sidebarContent.innerHTML = html || '<div class="empty-sidebar">本轮未检索到相关文献</div>';
  }
  }

/* v8.4.9 会话持久化：刷新/关闭重开后恢复历史对话渲染（数据在 sessions.db，只缺读取通道）
   v8.10l: 恢复增强——工具链折叠块（tool 消息）+ 深度思考折叠块（reasoning 持久化） */
// S3: history paging + file→turn jump. The server returns the newest page and
// a ``before`` cursor; older pages are prepended in memory and re-rendered, so a
// long session can be walked backwards without losing the latest turns.
var _historyMsgs = [];
var _historyTurns = null;    // canonical /messages.turns; null = legacy fallback
var _historyHasMore = false;
var HISTORY_PAGE = 60;

async function fetchHistoryPage(limit, before) {
  var epoch = authEpoch, sid = sessionId;
  var url = '/api/v2/session/' + encodeURIComponent(sessionId) + '/messages?limit='
    + encodeURIComponent(limit) + (before ? '&before=' + encodeURIComponent(before) : '');
  var resp = await accountFetch(url);
  if (epoch !== authEpoch || sid !== sessionId) return null;
  // The API deliberately returns 404 for both absent and owner-invisible sessions.
  // Authentication, transport and server failures do not invalidate a session.
  if (resp.status === 404) {
    await recoverMissingSession(epoch, sid);
    return null;
  }
  if (!resp.ok) return null;
  var data = await resp.json();
  return epoch === authEpoch && sid === sessionId ? data : null;
}

function buildEarlierHistoryButton() {
  var wrap = document.createElement('div');
  wrap.className = 'history-earlier';
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'history-earlier-btn';
  btn.textContent = '载入更早的对话';
  btn.onclick = function () { loadEarlierHistory(); };
  wrap.appendChild(btn);
  return wrap;
}

function syncEarlierHistoryButton() {
  var existing = chatContainer.querySelector('.history-earlier');
  if (_historyHasMore && !existing) {
    chatContainer.insertBefore(buildEarlierHistoryButton(), chatContainer.firstChild);
  } else if (!_historyHasMore && existing) {
    existing.remove();
  }
}

async function loadEarlierHistory() {
  var epoch = authEpoch, sid = sessionId;
  if (!sessionId) return;
  var oldest = 0;
  for (var i = 0; i < _historyMsgs.length; i++) {
    var mid = Number(_historyMsgs[i].message_id) || 0;
    if (mid) { oldest = mid; break; }
  }
  if (!oldest) return;
  var data;
  try { data = await fetchHistoryPage(HISTORY_PAGE, oldest); } catch (_) { return; }
  if (epoch !== authEpoch || sid !== sessionId || !data) return;
  var earlier = (data.messages || []);
  if (!earlier.length) { _historyHasMore = false; syncEarlierHistoryButton(); return; }
  // P0-A: pages are merged by message_id, so a re-fetched boundary row can
  // never be rendered twice (and never by "same text" guessing).
  var seenIds = {};
  for (var si = 0; si < _historyMsgs.length; si++) {
    var smid = String((_historyMsgs[si] || {}).message_id || '');
    if (smid) seenIds[smid] = true;
  }
  var fresh = [];
  for (var ei = 0; ei < earlier.length; ei++) {
    var emid = String((earlier[ei] || {}).message_id || '');
    if (emid && seenIds[emid]) continue;
    if (emid) seenIds[emid] = true;
    fresh.push(earlier[ei]);
  }
  _historyMsgs = fresh.concat(_historyMsgs);
  var earlierTurns = normalizeHistoryTurns(data.turns);
  if (earlierTurns.length && _historyTurns) {
    _historyTurns = mergeHistoryTurns(earlierTurns, _historyTurns);
  }
  _historyHasMore = !!data.has_more;
  renderHistoryMessages(_historyMsgs, _historyTurns);
  jumpToMessage(oldest, {silent: true});
}

function jumpToMessage(messageId, opts) {
  opts = opts || {};
  var mid = String(messageId || '');
  if (!mid) return false;
  var node = chatContainer.querySelector('[data-message-id="' + mid + '"]');
  if (!node) return false;
  // Center the whole bubble, not just its text box; the id can live on .content.
  var host = node.closest('.message') || node;
  try { host.scrollIntoView({block: 'center', behavior: opts.silent ? 'auto' : 'smooth'}); }
  catch (e) { host.scrollIntoView(); }
  if (!opts.silent) {
    var lit = node.classList.contains('message') ? node : (host.classList.contains('message') ? host : node);
    lit.classList.remove('is-jump-target');
    void lit.offsetWidth;
    lit.classList.add('is-jump-target');
    setTimeout(function () { lit.classList.remove('is-jump-target'); }, 2400);
  }
  return true;
}

// Called from the file library: open the session that owns the file, then scroll
// to the turn that uploaded/produced it. Returns true only when the anchor was
// actually found and highlighted (no silent "jumped" claims).
window.jumpToFileTurn = async function (targetSessionId, requestId) {
  var epoch = authEpoch;
  if (!targetSessionId || !requestId) return false;
  if (targetSessionId !== sessionId) {
    switchSession(targetSessionId);
    await restoreSessionHistory();
    if (epoch !== authEpoch || targetSessionId !== sessionId) return false;
  }
  if (!_historyMsgs.length) await restoreSessionHistory();
  if (epoch !== authEpoch || targetSessionId !== sessionId) return false;
  var target = null;
  for (var i = 0; i < _historyMsgs.length; i++) {
    if (String(_historyMsgs[i].request_id || '') === String(requestId)) { target = _historyMsgs[i]; break; }
  }
  if (!target) {
    // The turn may sit in an older page: walk back a bounded number of pages.
    for (var guard = 0; guard < 8 && _historyHasMore && !target; guard++) {
      await loadEarlierHistory();
      if (epoch !== authEpoch || targetSessionId !== sessionId) return false;
      for (var j = 0; j < _historyMsgs.length; j++) {
        if (String(_historyMsgs[j].request_id || '') === String(requestId)) { target = _historyMsgs[j]; break; }
      }
    }
  }
  if (!target) return false;
  return jumpToMessage(target.message_id);
};

async function restoreSessionHistory() {
  var epoch = authEpoch, sid = sessionId;
  if (!sessionId) return;
  try {
    var data = await fetchHistoryPage(HISTORY_PAGE, 0);
    if (epoch !== authEpoch || sid !== sessionId || !data) return;
    var restoredContext = CitrusTaskState.createContext();
    var savedContext = data.context_snapshot || data.context;
    if (savedContext) renderContextPanel(restoredContext.receive('context_snapshot', savedContext));
    _historyMsgs = data.messages || [];
    // Canonical turns are the server's own grouping of the same window. The
    // local adapter is only for legacy sessions that predate ``turns``.
    _historyTurns = normalizeHistoryTurns(data.turns);
    if (!_historyTurns.length) _historyTurns = null;
    _historyHasMore = !!data.has_more;
    renderHistoryMessages(_historyMsgs, _historyTurns);
  } catch (e) { /* 无历史或读取失败：保持欢迎语，不打扰用户 */ }
}

/* ─── P0-A: 逻辑轮次归并（历史恢复与实时流式共用同一套归属规则） ───
   一轮 = 用户问题 + 公开工作说明 + 全部工具调用/结果（含失败与重试）
        + 最终回答 + 该轮生成的文件。
   归并键是 turn_request_id（绝不用"文本相同"判重，因此同一轮里两次相同的
   检索都会保留）；幂等键是 message_id / tool_call_id / file_id。
   规则与 src/api/main.py 的 build_history_turns() 逐条对应。 */
function newHistoryTurn(key) {
  return {turnRequestId: key, user: null, answer: null, trace: [], files: [], messageIds: []};
}

function findTraceCall(turn, toolCallId, attempt) {
  if (!toolCallId) return null;
  for (var i = 0; i < turn.trace.length; i++) {
    var e = turn.trace[i];
    if ((e.kind === 'call' || e.kind === 'tool') && e.tool_call_id === toolCallId && String(e.attempt || 1) === String(attempt || 1)) return e;
  }
  return null;
}

function pushTurnFile(turn, file) {
  var fid = String((file && file.file_id) || '');
  if (!fid) return;
  for (var i = 0; i < turn.files.length; i++) {
    if (String((turn.files[i] || {}).file_id || '') === fid) return;
  }
  turn.files.push(file);
}

function groupHistoryTurns(msgs) {
  var turns = [];
  var current = null;
  // 一条持久化的助手行会产出两个 item（tool_calls + 公开工作说明），
  // 所以身份是 (message_id, role)，不是 message_id 单键。
  var seenItems = {};
  var list = msgs || [];
  for (var i = 0; i < list.length; i++) {
    var m = list[i];
    if (!m || typeof m !== 'object') continue;
    var mid = String(m.message_id || '');
    var role = String(m.role || '');
    if (mid) {
      var itemKey = mid + '|' + role;
      if (seenItems[itemKey]) continue;         // same page merged twice
      seenItems[itemKey] = true;
    }
    var key = String(m.turn_request_id || m.request_id || '');
    if (role === 'user') {
      current = newHistoryTurn(key);
      turns.push(current);
      if (mid) current.messageIds.push(mid);
      var attachments = m.attachments || [];
      var uploaded = [], generated = [];
      for (var a = 0; a < attachments.length; a++) {
        var f = attachments[a];
        if (!f) continue;
        if (String(f.origin || '') === 'generated') generated.push(f); else uploaded.push(f);
      }
      current.user = {content: m.content || '', message_id: mid, request_id: key,
                      attachments: uploaded};
      for (var g = 0; g < generated.length; g++) pushTurnFile(current, generated[g]);
      continue;
    }
    if (!current || (key && key !== current.turnRequestId)) {
      // 分页窗口可能从一轮中间开始：这些行归到自己的轮次键下，
      // 不能挂到下一个问题上（否则刷新后同一轮会被拆成两个气泡）。
      current = newHistoryTurn(key);
      turns.push(current);
    }
    if (mid && current.messageIds.indexOf(mid) < 0) current.messageIds.push(mid);
    if (role === 'tool_calls') {
      var calls = m.calls || [];
      for (var c = 0; c < calls.length; c++) {
        var call = calls[c] || {};
        var cid = String(call.id || '');
        if (cid && findTraceCall(current, cid, call.attempt)) continue;
        current.trace.push(Object.assign({}, call, {kind: 'call', tool_call_id: cid,
                            name: String(call.name || 'tool'),
                            summary: String(call.summary || ''), message_id: mid}));
      }
    } else if (role === 'tool') {
      var tcid = String(m.tool_call_id || '');
      var entry = findTraceCall(current, tcid, m.attempt);
      if (!entry) {
        // 调用行缺失（旧数据/截断历史）时，结果本身仍是真实步骤：保留记录。
        entry = {kind: 'tool', tool_call_id: tcid, name: String(m.name || 'tool'), summary: ''};
        current.trace.push(entry);
      }
      ['logical_key', 'operation_id', 'attempt', 'aliases', 'facts', 'outcome', 'result_refs'].forEach(function (field) { if (m[field] != null) entry[field] = m[field]; });
      entry.status = m.status || m.content || '';
      entry.reason_code = m.reason_code || '';
      entry.action_hint = m.action_hint || '';
      entry.result_summary = m.summary || '';
      entry.completed_at_ms = Number(m.completed_at_ms) || 0;
      entry.elapsed_ms = Number(m.elapsed_ms) || 0;
      entry.execution_order_available = !!m.execution_order_available;
      entry.truncated = !!m.truncated;
      // 结果行单独记 id，不覆盖调用行的 message_id（工作说明要据此找到
      // 它下面那批调用并插到其上方）。
      entry.toolMessageId = mid || entry.toolMessageId || '';
    } else if (role === 'assistant') {
      // 每条助手消息先都当作执行轨迹里的"工作说明"；一轮的最后一条在收尾时
      // 提升为回答。实时流正是这个语义：旁白走 assistant_progress 进 term-log，
      // done.answer 覆盖气泡正文。
      var noteEntry = {kind: 'note', content: String(m.content || ''), message_id: mid,
                       gen_time_ms: Number(m.gen_time_ms) || 0, answerCandidate: true};
      if (m.progress === true) {
        // 与 tool_calls 同一条消息的旁白，说的是它下面那批调用，紧贴其上方。
        var insertAt = current.trace.length;
        for (var p = current.trace.length - 1; p >= 0; p--) {
          var prev = current.trace[p];
          if (prev.kind === 'call' && String(prev.message_id || '') === mid) insertAt = p;
          else break;
        }
        current.trace.splice(insertAt, 0, noteEntry);
      } else {
        current.trace.push(noteEntry);
      }
    }
  }
  // 整页最后一条不是助手消息 => 最后一轮还在进行中（分页窗口截断在轮中），
  // 不把旁白提升成回答，避免刷新时把工作说明当成最终答案。
  var last = list.length ? (list[list.length - 1] || {}) : {};
  var lastIsAnswer = String(last.role || '') === 'assistant' && !last.progress;
  for (var ti = 0; ti < turns.length; ti++) {
    if (ti === turns.length - 1 && !lastIsAnswer) { turns[ti].partial = true; continue; }
    promoteLastAnswer(turns[ti]);
  }
  return turns;
}

function promoteLastAnswer(turn) {
  for (var pos = turn.trace.length - 1; pos >= 0; pos--) {
    var entry = turn.trace[pos];
    if (entry.answerCandidate) {
      turn.trace.splice(pos, 1);
      turn.answer = {content: entry.content || '', message_id: entry.message_id || '',
                     gen_time_ms: entry.gen_time_ms || 0};
      return;
    }
  }
}

function historyTraceStatus(entry) {
  return String(entry.status || (entry.content ? 'complete' : 'unavailable'));
}

/* 轨迹行的显示顺序：并行工具按真实完成时间（实时流即按到达顺序），
   没有时间戳的旧数据保持库内顺序；工作说明跟随它前面那一步，不被挪走。 */
function orderHistoryTrace(trace) {
  var out = [], anchor = Number.MAX_SAFE_INTEGER;
  for (var i = 0; i < trace.length; i++) {
    var e = trace[i];
    var ts = Number(e.completed_at_ms) || 0;
    if (ts > 0) anchor = ts;
    out.push({entry: e, sortKey: ts > 0 ? ts : anchor, index: i});
  }
  out.sort(function(a, b) {
    return a.sortKey === b.sortKey ? a.index - b.index : a.sortKey - b.sortKey;
  });
  return out.map(function(x) { return x.entry; });
}

/* Canonical turn adapter: the server's ``/messages.turns`` uses snake_case
   for a few keys; normalise them once so rendering never cares where the
   turn came from. A legacy session without ``turns`` still goes through
   groupHistoryTurns() below. */
function normalizeHistoryTurn(raw) {
  var t = raw || {};
  var trace = [];
  var rawTrace = Array.isArray(t.trace) ? t.trace : [];
  for (var i = 0; i < rawTrace.length; i++) {
    var e = rawTrace[i] || {};
    var entry = Object.assign({}, e, {
      kind: String(e.kind || 'call'),
      tool_call_id: String(e.tool_call_id || ''),
      name: String(e.name || ''),
      summary: String(e.summary || ''),
      content: String(e.content || ''),
      message_id: (e.message_id != null ? e.message_id : 0),
      gen_time_ms: Number(e.gen_time_ms) || 0
    });
    if (e.status != null) entry.status = e.status;
    if (e.reason_code != null) entry.reason_code = e.reason_code;
    if (e.action_hint != null) entry.action_hint = e.action_hint;
    if (e.result_summary != null) entry.result_summary = e.result_summary;
    if (e.completed_at_ms != null) entry.completed_at_ms = Number(e.completed_at_ms) || 0;
    if (e.elapsed_ms != null) entry.elapsed_ms = Number(e.elapsed_ms) || 0;
    if (e.execution_order_available != null) entry.execution_order_available = !!e.execution_order_available;
    if (e.truncated != null) entry.truncated = !!e.truncated;
    var toolMsgId = (e.tool_message_id != null ? e.tool_message_id : e.toolMessageId);
    if (toolMsgId != null) entry.toolMessageId = toolMsgId;
    if (e.answer_candidate || e.answerCandidate) entry.answerCandidate = true;
    trace.push(entry);
  }
  var files = [];
  var rawFiles = Array.isArray(t.files) ? t.files : [];
  for (var f = 0; f < rawFiles.length; f++) { if (rawFiles[f]) files.push(rawFiles[f]); }
  var ids = Array.isArray(t.message_ids) ? t.message_ids
    : (Array.isArray(t.messageIds) ? t.messageIds : []);
  return {
    turnRequestId: String(t.turn_request_id || t.turnRequestId || ''),
    user: t.user || null,
    answer: t.answer || null,
    trace: trace,
    files: files,
    messageIds: ids.slice(),
    partial: !!t.partial,
    intentSummary: publicTaskStage(t.intent_summary || t.task_summary),
    displayStatus: CitrusTaskState.displayStatus(t),
    stageLabel: publicTaskStage(t.stage_label),
    jobId: String(t.job_id || ''),
    terminalSnapshot: t.terminal_snapshot || null,
    contextSnapshot: t.context_snapshot || (t.terminal_snapshot || {}).context_snapshot || null,
    replayCursor: Number.isSafeInteger(t.replay_cursor) ? t.replay_cursor : 0,
    question: t.question || null,
    parentJobId: String(t.parent_job_id || ''),
    parentQuestionId: String(t.parent_question_id || '')
  };
}

function normalizeHistoryTurns(list) {
  var out = [];
  if (!Array.isArray(list)) return out;
  for (var i = 0; i < list.length; i++) {
    if (list[i] && typeof list[i] === 'object') out.push(normalizeHistoryTurn(list[i]));
  }
  return out;
}

/* Paging merges two server windows that can overlap on the boundary turn.
   The same turn key is one logical turn: join it instead of rendering the
   question twice. Entries are matched by tool_call_id / (message_id, text). */
function mergeHistoryTurns(earlierTurns, laterTurns) {
  var out = [];
  var byKey = Object.create(null);
  function mergeEntry(base, incoming) {
    for (var k = 0; k < base.trace.length; k++) {
      var existing = base.trace[k];
      var same = false;
      if (Number.isSafeInteger(incoming.seq) && Number.isSafeInteger(existing.seq)) same = incoming.seq === existing.seq;
      else if (incoming.tool_call_id || existing.tool_call_id) {
        same = incoming.tool_call_id && existing.tool_call_id === incoming.tool_call_id
          && existing.kind === incoming.kind && String(existing.attempt || 1) === String(incoming.attempt || 1);
      } else {
        same = incoming.message_id && existing.kind === incoming.kind
          && String(existing.message_id || '') === String(incoming.message_id);
      }
      if (!same) continue;
      Object.keys(incoming).forEach(function (key) {
        if (incoming[key] != null && incoming[key] !== '') existing[key] = incoming[key];
      });
      if (!existing.toolMessageId && incoming.toolMessageId) existing.toolMessageId = incoming.toolMessageId;
      return;
    }
    base.trace.push(incoming);
  }
  function adopt(turn) {
    var key = String(turn.turnRequestId || '');
    if (key && byKey[key]) {
      var base = byKey[key];
      if (!base.user && turn.user) base.user = turn.user;
      if (turn.answer) base.answer = turn.answer;
      if (turn.intentSummary) base.intentSummary = turn.intentSummary;
      if (turn.displayStatus) base.displayStatus = turn.displayStatus;
      if (turn.stageLabel) base.stageLabel = turn.stageLabel;
      ['jobId', 'terminalSnapshot', 'question', 'parentJobId', 'parentQuestionId'].forEach(function (key) { if (turn[key]) base[key] = turn[key]; });
      // Merging history windows never advertises events as consumed.
      base.replayCursor = 0;
      for (var i = 0; i < turn.trace.length; i++) mergeEntry(base, turn.trace[i]);
      for (var f = 0; f < turn.files.length; f++) pushTurnFile(base, turn.files[f]);
      for (var m = 0; m < turn.messageIds.length; m++) {
        if (base.messageIds.indexOf(turn.messageIds[m]) < 0) base.messageIds.push(turn.messageIds[m]);
      }
      // A boundary turn is partial only until the page that contains its
      // answer arrives; a merged turn with an answer is complete again.
      base.partial = !!(turn.partial && !base.answer);
      return;
    }
    if (key) byKey[key] = turn;
    out.push(turn);
  }
  for (var a = 0; a < (earlierTurns || []).length; a++) adopt(earlierTurns[a]);
  for (var b = 0; b < (laterTurns || []).length; b++) adopt(laterTurns[b]);
  return out;
}

function showQuestionContinuation(container, childJobId, owns) {
  if (!container || !childJobId || container.querySelector('.task-question-continue')) return;
  var link = document.createElement('button'); link.type = 'button'; link.className = 'task-question-continue';
  link.textContent = '查看继续任务';
  link.addEventListener('click', function () { if (owns()) refreshQuestionContinuation({child_job_id: childJobId}, owns, container); });
  container.appendChild(link);
}

async function refreshQuestionContinuation(result, owns, container) {
  var epoch = authEpoch, sid = sessionId, round = currentRoundId;
  try {
    var data = await fetchHistoryPage(HISTORY_PAGE);
    if (!data || !owns() || epoch !== authEpoch || sid !== sessionId || round !== currentRoundId) return;
    _historyMsgs = data.messages || []; _historyTurns = normalizeHistoryTurns(data.turns); _historyHasMore = !!data.has_more;
    renderHistoryMessages(_historyMsgs, _historyTurns);
  } catch (_) { if (owns()) showQuestionContinuation(container, result.child_job_id, owns); }
}

function projectHistoryTrace(process, trace) {
  // Server trace order preserves public notes interleaved with action groups.
  // Never sort notes by a neighbouring tool's completion timestamp.
  var entries = (trace || []).slice(), seenSeq = new Set();
  if (entries.length && entries.every(function (entry) { return Number.isSafeInteger(entry.seq); })) entries.sort(function (a, b) { return a.seq - b.seq; });
  entries.forEach(function (entry) {
    if (Number.isSafeInteger(entry.seq)) { if (seenSeq.has(entry.seq)) return; seenSeq.add(entry.seq); }
    if (entry.event) { process.receive(entry.event, Object.assign({}, entry.data || {}, entry.seq != null ? {seq: entry.seq} : {})); return; }
    if (entry.kind === 'note') { process.receive('assistant_progress', {text: entry.content, message_id: entry.message_id}); return; }
    if (entry.kind === 'call' || entry.kind === 'tool') process.receive('tool_result', Object.assign({}, entry, {
      tool_name: entry.tool_name || entry.name, display_name: entry.display_name || displayToolName(entry.name),
      summary: entry.result_summary || entry.summary, status: entry.status || 'unavailable'
    }));
  });
}

function bindHistoryTask(turn, content, answer, process, ownsContext) {
  var epoch = authEpoch, sid = sessionId, round = currentRoundId, armed = false, streamedAnswer = '';
  function projectContext() {
    if (owns() && ownsContext && round === currentRoundId && store.state.context_snapshot) renderContextPanel(store.state.context_snapshot);
  }
  function owns() { return epoch === authEpoch && sid === sessionId && chatContainer.contains(content); }
  function questionLabel() {
    if (!store.state.question || CitrusTaskState.isTerminal(store.state)) return;
    var labels = {pending: '等待你的选择', answered: '已答复并继续', cancelled: '选择已取消', expired: '选择已过期'};
    var label = labels[store.state.question.status];
    if (label) process.label.textContent = '执行轨迹：' + label;
  }
  function snapshot(job) {
    if (!owns()) return;
    process.receive('snapshot', job);
    answer.dataset.displayStatus = CitrusTaskState.displayStatus(job);
    process.terminal(CitrusTaskState.displayStatus(job));
    projectContext();
    questionLabel();
  }
  var store = CitrusTaskState.create({onEvent: function (type, data) {
    if (!owns()) return;
    process.receive(type, data);
    if (type.indexOf('context_') === 0 || data.context_snapshot) projectContext();
    if (type === 'answer_start') armed = true;
    if (type === 'text' && armed) { streamedAnswer += data.content || ''; if (!turn.answer || !turn.answer.content) answer.innerHTML = renderMarkdownSafe(streamedAnswer); }
    if (type === 'text_reset') { armed = false; streamedAnswer = ''; if (!turn.answer || !turn.answer.content) answer.innerHTML = ''; }
    if ((type === 'done' || type === 'error') && data.answer) {
      answer.innerHTML = renderMarkdownSafe(data.answer);
      if (String(data.answer).trim()) process.answerVisible();
    }
    if ((type === 'done' || type === 'error') && CitrusTaskState.displayStatus(data)) {
      process.terminal(CitrusTaskState.displayStatus(data));
      answer.dataset.displayStatus = CitrusTaskState.displayStatus(data);
      if (data.answer) answer.innerHTML = renderMarkdownSafe(data.answer);
      if (Array.isArray(data.artifact_ids) && window.loadSessionFiles) window.loadSessionFiles().then(function (payload) {
        if (owns() && payload) appendTurnGeneratedFiles(content, filesAttributedToTurn(payload.generated, sid, turn.turnRequestId, data.artifact_ids.map(String)));
      }).catch(function () {});
    }
    if (type === 'request_user_input' || type === 'question' || type === 'done') questionLabel();
    if (type === 'heartbeat' && !CitrusTaskState.isTerminal(store.state) && data.elapsed_ms != null) process.elapsed.textContent = '用时 ' + (Math.max(0, Number(data.elapsed_ms) || 0) / 1000).toFixed(0) + ' 秒';
  }});
  var transport = CitrusTaskTransport.create({store: store, fetch: accountFetch, owns: owns, onSnapshot: snapshot,
    onError: function () { if (owns() && !CitrusTaskState.isTerminal(store.state)) process.label.textContent = '执行轨迹：状态待确认'; }});
  var questionCard = CitrusTaskControls.createQuestion({container: content, store: store, transport: transport, owns: owns,
    onContinue: function (result) { refreshQuestionContinuation(result, owns, questionCard.element); }});
  store.hydrate({job_id: turn.jobId, terminal_snapshot: turn.terminalSnapshot, question: turn.question, context_snapshot: turn.contextSnapshot});
  snapshot({terminal_snapshot: turn.terminalSnapshot});
  if (turn.question && turn.question.child_job_id) showQuestionContinuation(questionCard.element, turn.question.child_job_id, owns);
  // replay_cursor and snapshot terminal_seq are metadata, not proof this view
  // consumed the preceding events. Replay starts at the reducer's actual 0.
  if (turn.jobId) transport.follow(turn.jobId);
}

function renderHistoryMessages(msgs, serverTurns) {
  clearSentAttachmentPreviewUrls();
  chatContainer.innerHTML = '';
  if (_historyHasMore) chatContainer.appendChild(buildEarlierHistoryButton());
  var turns = (serverTurns && serverTurns.length) ? serverTurns : groupHistoryTurns(msgs);
  for (var ti = 0; ti < turns.length; ti++) {
    var turn = turns[ti];
    if (turn.user) {
      var userContent = addMessage('user', escapeHtml(turn.user.content));
      // S3: stable anchors so a file can jump back to the exact turn.
      if (turn.user.message_id) userContent.dataset.messageId = String(turn.user.message_id);
      if (turn.user.request_id) userContent.dataset.requestId = String(turn.user.request_id);
      // P0-1: an upload belongs to the user bubble; a generated file belongs to
      // the assistant bubble of the same turn (placed further below).
      appendRestoredAttachmentCards(userContent, turn.user.attachments || []);
      appendMessageActions(userContent, 'user', turn.user.content || '');
    }
    var answer = turn.answer || {content: '', message_id: '', gen_time_ms: 0};
    if (!turn.user && !answer.content && !turn.trace.length && !turn.files.length) continue;
    // P0-A: 每轮只渲染一个助手气泡——与实时流式的形状完全一致
    // （一个气泡、一份执行轨迹、一个最终回答、一次生成文件）。
    var contentEl = addMessage('bot', '');
    if (answer.message_id) contentEl.dataset.messageId = String(answer.message_id);
    if (turn.turnRequestId) contentEl.dataset.requestId = String(turn.turnRequestId);
    contentEl.dataset.sessionId = sessionId;
    var answerBody = document.createElement('div'); answerBody.className = 'turn-answer';
    answerBody.dataset.displayStatus = turn.displayStatus;
    answerBody.innerHTML = renderMarkdownSafe(answer.content); contentEl.appendChild(answerBody);
    var process = createResearchProcessPanel(turn.intentSummary || (turn.user && turn.user.content));
    contentEl.insertBefore(process.element, contentEl.firstChild);
    process.receive('snapshot', {terminal_snapshot: turn.terminalSnapshot});
    if (String(answer.content || '').trim()) process.answerVisible(true);
    if (!process.terminal(turn.displayStatus)) process.label.textContent = '执行轨迹：状态未确认';
    if (turn.parentJobId || turn.parentQuestionId) {
      var relation = document.createElement('div'); relation.className = 'turn-continuation'; relation.textContent = '根据上一轮选择继续';
      contentEl.insertBefore(relation, process.element);
    }
    if (!turn.jobId) {
      projectHistoryTrace(process, turn.trace);
      if (turn.stageLabel) process.note(turn.stageLabel);
      if (!turn.trace.length) process.note(answer.content ? '回答已保存，执行状态未完整记录。' : '本轮结果尚未完整记录。');
    }
    if (turn.jobId || turn.question) bindHistoryTask(turn, contentEl, answerBody, process, ti === turns.length - 1);
    else if (ti === turns.length - 1) {
      var context = CitrusTaskState.createContext();
      if (turn.contextSnapshot) context.receive('context_snapshot', turn.contextSnapshot);
      (turn.trace || []).forEach(function (entry) {
        if (entry.event && entry.event.indexOf('context_') === 0) context.receive(entry.event, Object.assign({}, entry.data || {}, {seq: entry.seq}));
      });
      if (context.current()) renderContextPanel(context.current());
    }
    appendMessageActions(contentEl, 'assistant', answer.content || '');
    // P0-A: 该轮生成文件只在这一处、按 file_id 幂等追加一次。
    if (turn.files.length) appendTurnGeneratedFiles(contentEl, turn.files, '本轮生成文件');
  }
  syncEarlierHistoryButton();
  syncHistoryTurnFiles();
}

/* v8.10: 无会话时自动创建默认会话（主流行为）——打开即有一个会话，历史回答有处可存，
   不再依赖用户手动点 ＋ 新建 */
function createElapsedBadge(milliseconds) {
  var badge = document.createElement('div');
  badge.style.cssText = 'margin-top:8px;font-size:11px;color:var(--text-muted);font-family:var(--font-mono)';
  badge.textContent = '生成 ' + (Math.max(0, Number(milliseconds) || 0) / 1000).toFixed(1) + 's';
  return badge;
}

var _sessionCreation = null;
async function createDefaultSession(epoch) {
  if (epoch !== authEpoch || sessionId) return;
  if (_sessionCreation && _sessionCreation.epoch === epoch) return _sessionCreation.promise;
  var creation = {epoch: epoch};
  _sessionCreation = creation;
  creation.promise = (async function() {
    try {
      var resp = await accountFetch('/api/v1/session/new', {method: 'POST'});
      if (!resp.ok) return;
      var data = await resp.json();
      if (epoch !== authEpoch || sessionId || !data || typeof data.session_id !== 'string' || !data.session_id) return;
      sessionId = data.session_id;
      localStorage.setItem(sessionStorageKey(), sessionId);
      window.dispatchEvent(new CustomEvent('citrus:session-changed', {detail:{session_id:sessionId}}));
    } catch (e) { /* Leave creation retryable on the next explicit initialization. */ }
    finally { if (_sessionCreation === creation) _sessionCreation = null; }
  })();
  return creation.promise;
}

async function recoverMissingSession(epoch, sid) {
  if (epoch !== authEpoch || !sid || sid !== sessionId) return;
  // Clear only the invalid session's rendered state. Composer draft, selected
  // local Files, preview URLs and account file references belong to the user.
  if (activeTurnCancel) activeTurnCancel('switch');
  activeTurnCancel = null; window._activeTurnStatusUpdater = null;
  if (currentAbortController) { currentAbortController.abort(); currentAbortController = null; }
  setSendDisabled(false);
  sessionId = null;
  localStorage.removeItem(sessionStorageKey());
  clearSentAttachmentPreviewUrls();
  chatContainer.replaceChildren();
  _historyMsgs = []; _historyTurns = null; _historyHasMore = false;
  roundHistory = []; _restoredGroups = null; _restoredRefPool = null;
  _ctxData = null; _sentSnapshots = [];
  sidebarContent.innerHTML = '<div class="empty-sidebar">提交问题后文献将在此展示</div>';
  citationCount.textContent = '0';
  renderContextPanel({estimated_tokens: 0, max_tokens: _ctxMaxTokens(), history_msgs: 0,
                      soft_threshold: 0.75, hard_threshold: 0.93});
  // One creation attempt; do not recursively reload a replacement's history.
  await createDefaultSession(epoch);
  if (epoch !== authEpoch) return;
  loadSessions();
  if (window.turnRail && window.turnRail.reload) window.turnRail.reload();
}

async function ensureSession() {
  var epoch = authEpoch;
  if (!sessionId) await createDefaultSession(epoch);
  if (epoch !== authEpoch) return;
  if (sessionId) {
    userInput.focus();
    await restoreSessionHistory();
  }
  if (epoch === authEpoch) loadSessions();
}
