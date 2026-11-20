'use strict';
// Focused structural checks for the figure workspace U4/U5 front-end work.
// These assertions stay on the three files this change owns and do not need a
// browser or the service; they guard the contracts that are easy to regress:
// canvas-first layout, opt-in drawers, real bounded data preview, older-project
// paging, and the save/discard/cancel switch guard.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const web = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(web, 'figures.html'), 'utf8');
const js = fs.readFileSync(path.join(web, 'assets', 'figures.js'), 'utf8');
const css = fs.readFileSync(path.join(web, 'assets', 'figures.css'), 'utf8');

test('the canvas is the default surface and both drawers are opt-in', () => {
  assert.match(html, /<main class="workbench" id="workbench">/);
  assert.doesNotMatch(html, /class="workbench[^"]*is-left-open/);
  assert.doesNotMatch(html, /class="workbench[^"]*is-right-open/);
  assert.match(html, /<aside class="asset-sidebar" id="leftSidebar"[^>]*\shidden>/);
  assert.match(html, /<aside class="inspector-sidebar" id="rightSidebar"[^>]*\shidden>/);
  assert.match(html, /id="toggleLeft"[^>]*aria-expanded="false"/);
  assert.match(html, /id="toggleRight"[^>]*aria-expanded="false"/);
  // Selecting an object reveals the context inspector; the toolbar still owns
  // explicit open/close.
  assert.match(js, /if \(gid && quiet !== true\) setSidebar\('right', true\);/);
  assert.match(js, /if \(!cancelled && event && state\.selected\) setSidebar\('right', true\);/);
  assert.match(js, /state\.narrow = narrow;[\s\S]*if \(narrow\) \{ setSidebar\('left', false\); setSidebar\('right', false\); \}/);
  // The hidden attribute must beat the authored display:flex rule.
  assert.match(css, /\.asset-sidebar\[hidden\], \.inspector-sidebar\[hidden\] \{ display: none; \}/);
});

test('unsaved edits offer save, discard and cancel instead of a silent drop', () => {
  assert.match(html, /<dialog class="workbench-dialog" id="unsavedDialog"/);
  assert.match(html, /id="unsavedSave"[^>]*>保存并继续</);
  assert.match(html, /id="unsavedDiscard"[^>]*>放弃修改</);
  assert.match(html, /id="unsavedCancel"[^>]*>取消</);
  assert.match(js, /function confirmUnsaved\(action\)/);
  assert.match(js, /confirmUnsaved\('打开另一图件'\)/);
  assert.match(js, /confirmUnsaved\('创建新图件'\)/);
  assert.match(js, /Promise\.resolve\(\)\.then\(function \(\) \{\s*return saveEdits\(\);/);
  // The old two-outcome native prompt before opening a project must be gone.
  assert.doesNotMatch(js, /confirm\('当前修改尚未全部保存，仍要打开另一图件吗？'\)/);
});

test('project browsing uses the existing limit/offset API and can load older pages', () => {
  assert.match(html, /id="projectListFooter"/);
  assert.match(html, /id="loadMoreProjects"/);
  assert.match(js, /var PROJECT_PAGE = 30;/);
  assert.match(js, /'\/projects\?limit=' \+ requestLimit \+ '&offset=' \+ offset/);
  assert.match(js, /state\.projects = more \? state\.projects\.concat\(visible\) : visible;/);
  assert.match(js, /state\.projectsMore = incoming\.length > wanted;/);
  assert.match(js, /loadProjects\(\{ more: true \}\)/);
  assert.match(js, /loadProjects\(\{ keep: true \}\)/);
});

test('data import shows a real bounded CSV or workbook preview before confirmation', () => {
  assert.match(html, /id="importPreview"/);
  assert.match(html, /id="importPreviewBody"/);
  assert.match(html, /id="importPreviewNote"/);
  assert.match(html, /id="importConfirmButton"[^>]*>确认创建图件</);
  assert.match(js, /var PREVIEW_MAX_BYTES = 16 \* 1024;/);
  assert.match(js, /var PREVIEW_MAX_LINES = 60;/);
  assert.match(js, /headers: textTable \? \{ Range: 'bytes=0-' \+ \(PREVIEW_MAX_BYTES - 1\) \}/);
  assert.match(js, /function readLimited\(stream, limit\)/);
  assert.match(js, /body\.textContent = text\.split/);
  assert.match(js, /sheetRows: PREVIEW_MAX_LINES/);
  assert.match(js, /WORKBOOK_PREVIEW_COLUMNS/);
  assert.match(html, /xlsx\.full\.min\.js/);
  // Import needs the preview before the explicit confirm step can create work.
  assert.match(js, /!previewReady/);
  assert.match(js, /state\.dataPreviewReady \|\| state\.dataPreviewFile !== fileId/);
});

test('read-only static previews are distinguished without claiming recovery', () => {
  assert.match(js, /var readOnly = state\.staticPreview \|\|\s*\(detail\.editable === false && !detail\.edit_requires_preparation\);/);
  assert.match(js, /只读静态预览/);
  assert.match(js, /var staticOnly = project\.editable === false \|\|\s*\(project\.source_kind !== 'svg' && !\(project\.exports && project\.exports\.svg\)\);/);
  assert.match(js, /var readOnly = staticOnly \? '<span class="project-badge">只读<\/span>' : '';/);
  assert.match(js, /state\.current\.edit_limitation \|\|\s*\(state\.current\.edit_error && state\.current\.edit_error\.message\)/);
  // A preparatory legacy project keeps its real "load edit objects" action.
  assert.match(js, /!detail\.edit_requires_preparation \|\| readOnly/);
  assert.match(css, /\.project-badge/);
  assert.match(css, /\.workbench-dialog/);
  assert.match(css, /\.import-preview-body/);
});

test('existing selection, patch, QA, render and export flows remain wired', () => {
  for (const endpoint of ["'/projects/'", "'/overrides'", "'/verify'", "'/bundle'", "'/export/'", "'/preview'"]) {
    assert.ok(js.includes(endpoint), 'missing endpoint ' + endpoint);
  }
  assert.match(js, /function pickObject\(event, cycle\)/);
  assert.match(js, /function renderOverlay\(\)/);
  assert.match(js, /function manifestObjects\(\)/);
  assert.match(js, /state\.selected = gid;/);
  assert.match(js, /expected_revision: expectedRevision/);
  assert.match(js, /figure_id !== figureId/);
});
