'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {browser} = require('./chat-turn-fixture.cjs');

function panelFixture() {
  const {context, get} = browser();
  const panel = context.createResearchProcessPanel('');
  get('chat-container').appendChild(panel.element);
  return {context, panel};
}

test('verified Browse phases and terminal status are rendered independently of answer text', () => {
  const {panel} = panelFixture();
  panel.receive('browse_phase', {phase: 'waiting'});
  assert.equal(panel.label.textContent, '等待浏览器');
  panel.receive('browse_phase', {phase: 'downloading', summary: 'untrusted'});
  assert.equal(panel.label.textContent, '正在下载资料');
  panel.receive('browse_phase', {phase: 'unknown'});
  assert.equal(panel.label.textContent, '正在下载资料');
  panel.receive('tool_result', {tool_name: 'browse_web', tool_call_id: 'call-1', status: 'complete'});
  assert.equal(panel.label.textContent, '');
  panel.terminal('cancelled');
  panel.receive('browse_phase', {phase: 'organizing'});
  assert.equal(panel.label.textContent, '已停止');
  panel.receive('agent_recovery', {status: 'admitted'});
  panel.receive('browse_phase', {phase: 'organizing'});
  assert.equal(panel.label.textContent, '正在组织回答');
});

test('a retired conversation cannot receive late Browse progress', () => {
  const {context, panel} = panelFixture();
  context.sessionId = 'different-session';
  panel.receive('browse_phase', {phase: 'verifying'});
  assert.equal(panel.label.textContent || '', '');
});
