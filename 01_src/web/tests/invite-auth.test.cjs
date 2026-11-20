'use strict';
// UI-contract fixture: executes the production contract blocks from app.js and
// admin.js inside a Node vm with a minimal `window` double. It verifies the
// browser-side request/response mapping only; the frozen HTTP endpoints are
// covered by A2's server tests against a real backend. No network calls, real
// accounts or credentials are used here.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');

const webRoot = join(__dirname, '..');
function read(name) { return readFileSync(join(webRoot, name), 'utf8'); }
function loadContract(name, begin, end, exportName) {
  const text = read(name);
  const from = text.indexOf(begin);
  const to = text.indexOf(end);
  assert.ok(from >= 0 && to > from, name + ': contract markers missing');
  const sandbox = {window: {}};
  vm.runInNewContext(text.slice(from, to + end.length), sandbox, {filename: name});
  const contract = sandbox.window[exportName];
  assert.ok(contract, name + ': ' + exportName + ' was not exported');
  return contract;
}

const Auth = loadContract('assets/app.js', '// BEGIN INVITE AUTH CONTRACT', '// END INVITE AUTH CONTRACT', 'CitrusAuthContract');
const Admin = loadContract('assets/admin.js', '// BEGIN ADMIN INVITE CONTRACT', '// END ADMIN INVITE CONTRACT', 'CitrusAdminContract');

test('register payload carries the invite code; login payload never does', () => {
  assert.deepEqual({...Auth.buildRegisterPayload({
    username: '  citrus ', password: 'secret passphrase', avatar_preset: '07', invite_code: ' INVITE-1 '
  })}, {
    username: 'citrus', password: 'secret passphrase', avatar_preset: '07', invite_code: 'INVITE-1'
  });
  const login = {...Auth.buildLoginPayload({username: 'citrus', password: 'secret passphrase', invite_code: 'leak'})};
  assert.deepEqual(login, {username: 'citrus', password: 'secret passphrase'});
  assert.equal(Object.prototype.hasOwnProperty.call(login, 'invite_code'), false);
  assert.equal(Auth.buildRegisterPayload({username: 'x', password: 'p'}).avatar_preset, '01');
});

test('202 registration is pending, stays on the login surface and is never a user session', () => {
  const pending = Auth.classifyAuthResponse('register', 202, {
    status: 'pending', application_id: 'application-1', user: {user_id: 9}
  });
  assert.equal(pending.kind, 'pending');
  assert.equal(pending.applicationId, 'application-1');
  assert.equal(Object.prototype.hasOwnProperty.call(pending, 'user'), false);
  assert.match(pending.message, /等待管理员审核/);
});

test('login success requires a user object; public failures stay generic', () => {
  const ok = Auth.classifyAuthResponse('login', 200, {user: {user_id: 3, username: 'citrus'}});
  assert.equal(ok.kind, 'success');
  assert.equal(ok.user.user_id, 3);
  assert.equal(Auth.classifyAuthResponse('login', 200, {}).kind, 'error');
  const unauthorized = Auth.classifyAuthResponse('login', 401, {detail: 'secret passphrase'});
  assert.equal(unauthorized.kind, 'error');
  assert.doesNotMatch(unauthorized.message, /secret passphrase/);
  assert.match(unauthorized.message, /用户名或密码不正确/);
  const inviteFailure = Auth.classifyAuthResponse('register', 400, {});
  assert.match(inviteFailure.message, /邀请码无效/);
  assert.equal(Auth.classifyAuthResponse('register', 409, {}).kind, 'error');
  assert.equal(Auth.classifyAuthResponse('login', 429, {}).kind, 'error');
});

test('password change and recovery validate locally before reauthentication', () => {
  assert.match(Auth.validatePasswordChange({current_password: '', new_password: '0123456789'}), /当前密码/);
  assert.match(Auth.validatePasswordChange({current_password: 'old', new_password: 'short'}), /至少 10 位/);
  assert.match(Auth.validatePasswordChange({current_password: 'old', new_password: '0123456789', confirm_password: 'x'}), /不一致/);
  assert.equal(Auth.validatePasswordChange({current_password: 'old', new_password: '0123456789', confirm_password: '0123456789'}), '');
  assert.match(Auth.validateRecovery({username: '', recovery_code: 'c', new_password: '0123456789'}), /用户名/);
  assert.match(Auth.validateRecovery({username: 'u', recovery_code: '', new_password: '0123456789'}), /恢复码/);
  assert.equal(Auth.validateRecovery({username: 'u', recovery_code: 'c', new_password: '0123456789', confirm_password: '0123456789'}), '');
});

test('recovery codes are capped at eight and only display strings', () => {
  const codes = Auth.normalizeRecoveryCodes({codes: ['a', '', null, 3, 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i']});
  assert.equal(codes.length, 8);
  assert.deepEqual(Array.from(codes), ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']);
  assert.equal(Auth.normalizeRecoveryCodes({}).length, 0);
});

test('invite states use the frozen issued/claimed/revoked/expired enum', () => {
  assert.deepEqual(Array.from(Admin.INVITE_STATES), ['issued', 'claimed', 'revoked', 'expired']);
  assert.equal(Admin.inviteState('issued').label, '已签发');
  assert.equal(Admin.inviteState('issued').canRevoke, true);
  assert.equal(Admin.inviteState('claimed').label, '已使用');
  assert.equal(Admin.inviteState('claimed').canRevoke, false);
  assert.equal(Admin.inviteState('revoked').label, '已撤销');
  assert.equal(Admin.inviteState('expired').label, '已过期');
  assert.equal(Admin.inviteState('unknown').key, 'issued');
});

test('application states only allow review while pending', () => {
  assert.equal(Admin.applicationState('pending').canReview, true);
  assert.equal(Admin.applicationState('approved').canReview, false);
  assert.equal(Admin.applicationState('rejected').canReview, false);
  assert.equal(Admin.applicationState('pending').label, '待审核');
});

test('pagination queries clamp limits and keep offsets non-negative', () => {
  assert.equal(Admin.pageQuery('pending', 1000, -5), '?limit=100&offset=0&status=pending');
  assert.equal(Admin.pageQuery('rejected', 0, '17'), '?limit=1&offset=17&status=rejected');
  assert.equal(Admin.pageQuery('all', 50, 0), '?limit=50&offset=0');
  assert.equal(Admin.invitesQuery(999, -1), '?limit=100&offset=0');
  assert.equal(Admin.clampLimit(undefined), 50);
});

test('invite creation sends only a valid 1-30 day expiry', () => {
  assert.deepEqual({...Admin.buildInviteBody('7')}, {expires_in_days: 7});
  assert.deepEqual({...Admin.buildInviteBody(30)}, {expires_in_days: 30});
  assert.deepEqual({...Admin.buildInviteBody('0')}, {});
  assert.deepEqual({...Admin.buildInviteBody('31')}, {});
  assert.deepEqual({...Admin.buildInviteBody('')}, {});
  assert.deepEqual({...Admin.buildInviteBody('abc')}, {});
});

test('application decisions build the frozen URLs and bounded reason body', () => {
  assert.equal(Admin.decisionUrl('app-1', 'approve'), '/api/v2/admin/applications/app-1/approve');
  assert.equal(Admin.decisionUrl('a b/c', 'reject'), '/api/v2/admin/applications/a%20b%2Fc/reject');
  assert.equal(Admin.decisionUrl('app-1', 'other'), '');
  assert.deepEqual({...Admin.decisionBody('approve')}, {});
  assert.deepEqual({...Admin.decisionBody('reject', '  no  ')}, {reason: 'no'});
  assert.deepEqual({...Admin.decisionBody('reject', '')}, {});
  const bounded = Admin.decisionBody('reject', 'x'.repeat(400));
  assert.equal(Array.from(bounded.reason).length, 300);
});

test('admin reset sends the administrator reauthentication password only', () => {
  assert.deepEqual({...Admin.resetBody('admin passphrase')}, {current_password: 'admin passphrase'});
  assert.deepEqual({...Admin.resetBody('')}, {current_password: ''});
});

test('UI wiring keeps pending off the success path and clears secrets', () => {
  const app = read('assets/app.js');
  const pendingAt = app.indexOf("outcome.kind === 'pending'");
  const successAt = app.indexOf('currentAccount = outcome.user');
  assert.ok(pendingAt >= 0 && successAt > pendingAt, 'pending branch must precede success assignment');
  const pendingBranch = app.slice(pendingAt, successAt);
  assert.match(pendingBranch, /clearAuthSecrets\(\)/);
  assert.match(pendingBranch, /setAuthNotice\(/);
  assert.doesNotMatch(pendingBranch, /startAuthenticatedApp/);
  assert.match(app, /invite_code: inviteCode/);
  assert.match(app, /if \(invite\) invite\.hidden = !registering/);
  assert.match(app, /cache: 'no-store'/);
  for (const endpoint of ['/api/v2/auth/password/change', '/api/v2/auth/password/recovery-codes', '/api/v2/auth/password/recover']) {
    assert.ok(app.includes(endpoint), 'missing ' + endpoint);
  }
});

test('admin wiring exposes filters, one-time secrets and reauthed reset', () => {
  const admin = read('assets/admin.js');
  const html = read('admin.html');
  for (const endpoint of ['/api/v2/admin/applications', '/api/v2/admin/invites', '/reset-password']) {
    assert.ok(admin.includes(endpoint), 'missing ' + endpoint);
  }
  assert.match(admin, /CitrusAdminContract\.resetBody\(password\)/);
  assert.match(admin, /temporary_password/);
  assert.match(admin, /showOneTimeInvite/);
  assert.match(admin, /discardOneTimeInvite/);
  assert.match(admin, /showOneTimeAdminSecret/);
  assert.doesNotMatch(admin, /prompt\([^)]*temporary_password/);
  for (const id of ['applicationsTable', 'applicationStatus', 'invitesTable', 'inviteOneTime', 'adminResetDialog', 'adminSecretPanel']) {
    assert.ok(html.includes('id="' + id + '"'), 'missing #' + id);
  }
});

test('admin list rendering escapes server-provided text', () => {
  const admin = read('assets/admin.js');
  assert.match(admin, /esc\(application\.username\)/);
  assert.match(admin, /esc\(application\.rejection_reason\)/);
  assert.match(admin, /esc\(invite\.invite_id\)/);
  assert.match(admin, /el\('adminSecretValue'\)\.textContent/);
});

test('no credentials are written to the console', () => {
  for (const name of ['assets/app.js', 'assets/admin.js']) {
    const text = read(name);
    assert.doesNotMatch(text, /console\.(?:log|info|debug|warn|error)\s*\([^;\n]*(?:password|invite_code|recovery_code)/i, name);
  }
});
