"""Real browser/UI acceptance with explicitly simulated S5 task endpoints.

Authentication, sessions, static assets, native controls and DOM/layout are real
on the existing local preview. Task chat/events/updates/approvals/resume replies
are an isolated in-browser fixture: this does not validate backend integration,
execute tools, call a model, or produce scientific evidence.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from browser_driver import Browser

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8135)
args = parser.parse_args()
base = f'http://127.0.0.1:{args.port}'
out = Path(__file__).parent / 'results' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-task-ui-browser')
out.mkdir(exist_ok=False)
fixture = (Path(__file__).parent / 'preview_app.py').read_text(encoding='utf-8')
password = re.search(r"auth.register\(name, '([^']+)'\)", fixture).group(1)
checks = []


def check(name, value, source='real_browser_simulated_task_api'):
    checks.append({'check': name, 'passed': bool(value), 'source': source})
    if not value:
        raise AssertionError(name)


# Only these sentinel task paths are simulated. Real auth/session/static requests
# remain on loopback. Fixture records and labels explicitly identify UI-only data.
TASK_FIXTURE = r"""
(() => {
  const nativeFetch = window.fetch.bind(window), jobId = 'synthetic-s5-browser';
  const log = [], events = [];
  let controller = null, currentSession = '', version = 1, applied = 0;
  let pending = [], approvals = [], operations = [], status = 'running', terminal = null, firstConflict = true;
  const copy = value => JSON.parse(JSON.stringify(value));
  const response = (value, code = 200) => new Response(JSON.stringify(value), {status: code, headers: {'Content-Type':'application/json'}});
  function sse(frame) { return 'id: '+frame.seq+'\nevent: '+frame.event+'\ndata: '+JSON.stringify(frame.data)+'\n\n'; }
  function append(event, data, live = true) {
    const seq = events.length + 1, frame = {seq, event, data: {...data, seq}, created_at: 'synthetic-ui-fixture'};
    events.push(frame); if (live && controller) controller.enqueue(new TextEncoder().encode(sse(frame))); return frame;
  }
  function metadata() { return {job_id: jobId, session_id: currentSession, status, task_version: version, applied_version: applied,
    event_cursor: events.length, pending_updates: copy(pending), approvals: copy(approvals), operations: copy(operations), terminal_snapshot: copy(terminal)}; }
  window.__s5Acceptance = {log, events,
    applyUpdates() {
      const items = pending.slice(); pending = [];
      for (const item of items) { applied = item.version; append('task_update', {...item, status:'applied'}); }
    },
    disconnectWithInterruptedStep() {
      operations = [{operation_id:'synthetic-read', tool_name:'read_account_file', status:'interrupted', recovery_mode:'local_read', resumable:true, input_refs:[], result_refs:[]}];
      append('operation', {...operations[0]}, false);
      status = 'failed'; terminal = {display_status:'partial', history_saved:true, verified_artifacts:0, notices:[]};
      append('done', {session_id:currentSession, turn_status:'partial', turn_envelope:terminal, answer:'', artifact_ids:[]}, false);
      controller.close(); controller = null;
    }
  };
  window.fetch = async function(input, options = {}) {
    const url = new URL(typeof input === 'string' ? input : input.url, location.origin);
    const method = options.method || 'GET', path = url.pathname;
    if (url.origin !== location.origin) throw Error('External network disabled in local browser acceptance');
    const taskPath = path.startsWith('/jobs/'+jobId) || path.startsWith('/api/v2/chat/jobs/'+jobId);
    if (path !== '/api/v2/chat' && !taskPath) {
      log.push({method, path, source:'real_local_preview'}); return nativeFetch(input, options);
    }
    log.push({method, path: path+url.search, source:'simulated_task_fixture'});
    const body = options.body ? JSON.parse(options.body) : {};
    if (path === '/api/v2/chat') {
      if (body.query !== 'Synthetic S5 browser acceptance') return response({detail:'Only the UI fixture query is permitted'}, 400);
      currentSession = body.session_id;
      approvals = [{approval_id:'synthetic-approval', job_id:jobId, tool_name:'write_local_file', status:'pending'}];
      const one = append('job', {job_id:jobId, status:'running', task_protocol:'citrus-task/1'}, false);
      const two = append('tool_call_start', {tool_call_id:'synthetic-statistics', display_name:'统计分析'}, false);
      append('permission_request', approvals[0], false);
      const four = append('tool_result', {tool_call_id:'synthetic-statistics', tool_name:'statistics_test', display_name:'统计分析', status:'partial',
        facts:{method:'Synthetic browser UI fixture', coverage:'No scientific data; UI-only scope'}, result_refs:[{label:'Synthetic reference; no real artifact'}]}, false);
      return new Response(new ReadableStream({start(stream) {
        controller = stream; stream.enqueue(new TextEncoder().encode([one,two,four,four].map(sse).join('')));
      }}), {headers:{'Content-Type':'text/event-stream'}});
    }
    if (path === '/jobs/'+jobId+'/events') {
      const after = Number(url.searchParams.get('after') || 0), rows = events.filter(e => e.seq > after).slice(0,200);
      return response({protocol:'citrus-task/1',job_id:jobId,events:copy(rows),next_cursor:rows.length ? rows.at(-1).seq : after,has_more:false,status,terminal_snapshot:copy(terminal)});
    }
    if (path === '/jobs/'+jobId) return response(metadata());
    if (path.endsWith('/approvals/synthetic-approval')) {
      approvals[0].status = body.decision; append('permission_decision', {...approvals[0]}); return response(copy(approvals[0]));
    }
    if (path.endsWith('/updates')) {
      if (firstConflict) {
        firstConflict = false; version = 2;
        const competing = {version, status:'pending', instruction:'Competing synthetic UI fixture request'};
        pending.push(competing); append('task_update', competing); return response({detail:{code:'version_conflict'}},409);
      }
      if (body.expected_version !== version) return response({detail:{code:'version_conflict'}},409);
      const update = {job_id:jobId, version:++version, status:'pending', instruction:body.instruction};
      pending.push(update); append('task_update', update); return response(update);
    }
    if (path.endsWith('/resume')) {
      if (body.operation_id !== 'synthetic-read') return response({detail:'Not resumable'},409);
      append('operation', {operation_id:'synthetic-read', status:'resuming', recovery:'safe_step'});
      operations[0].status = 'complete'; operations[0].resumable = false;
      const outcome = {status:'complete', summary:'Synthetic local-read UI fixture only', facts:{coverage:'UI-only recovery fixture'}, artifacts:[]};
      append('operation', {operation_id:'synthetic-read', tool_name:'read_account_file', status:'complete', outcome});
      return response({job_id:jobId,operation_id:'synthetic-read',recovery:'safe_step',operation_status:'complete',outcome,result:'Synthetic UI fixture only'});
    }
    return response({detail:'Unexpected fixture endpoint'},404);
  };
})();
"""

with Browser() as browser:
    try:
        # Prevent CDN/model/external dependencies from being fetched by this
        # owned browser. This scenario uses empty answer bodies, not Markdown.
        browser.call('Network.enable')
        browser.call('Network.setBlockedURLs', {'urls': ['https://*']})
        browser.call('Page.addScriptToEvaluateOnNewDocument', {'source': TASK_FIXTURE})
        browser.navigate(base + '/')
        browser.wait('!!document.querySelector("#authUsername")')
        for selector, value in (('#authUsername', 'qa_preview_a'), ('#authPassword', password)):
            browser.click(selector)
            browser.call('Input.insertText', {'text': value})
        browser.click('.auth-card button[type="submit"]')
        browser.wait('document.querySelector("#authGate").hidden')
        browser.wait('!!sessionId && typeof CitrusTaskTransport === "object"')
        observed = browser.evaluate('(async()=>{const r=await fetch("/openapi.json");const d=await r.json();return {status:r.status,event_route:!!d.paths["/jobs/{job_id}/events"],update_route:!!d.paths["/api/v2/chat/jobs/{job_id}/updates"]};})()')
        check('real local preview authenticates and serves S5 modules', True, 'real_local_preview')
        browser.click('#user-input')
        browser.call('Input.insertText', {'text': 'Synthetic S5 browser acceptance'})
        browser.click('#send-btn')
        browser.wait('!!document.querySelector(".perm-card [data-decision=\\"rejected\\"]") && !!document.querySelector(".task-result")')
        check('sequence gap recovered via the production replay transport', browser.evaluate('__s5Acceptance.log.some(r=>r.source==="simulated_task_fixture" && r.path.includes("/events?after=2"))'))
        check('duplicate result frame creates exactly one structured card', browser.evaluate('document.querySelectorAll(".task-result").length') == 1)
        browser.click('.tool-trace > summary')
        browser.click('.task-result > summary')
        check('structured facts and references visible in real DOM', browser.evaluate('document.querySelector(".task-result").textContent.includes("No scientific data; UI-only scope") && document.querySelector(".task-result").textContent.includes("Synthetic reference")'))
        browser.screenshot(out / 'task-permission.png')
        browser.click('.perm-card [data-decision="rejected"]')
        browser.wait('!document.querySelector(".perm-card")')
        check('explicit rejection reaches canonical decision endpoint', browser.evaluate('__s5Acceptance.log.some(r=>r.method==="POST" && r.path.endsWith("/approvals/synthetic-approval"))'))
        browser.click('.task-update > summary')
        browser.click('.task-update textarea')
        browser.call('Input.insertText', {'text': 'Synthetic UI-only adjustment'})
        browser.click('.task-update button[type="submit"]')
        browser.wait('document.querySelector(".task-control-status").textContent.includes("已更新")')
        check('CAS conflict preserves the editable draft', browser.evaluate('document.querySelector(".task-update textarea").value') == 'Synthetic UI-only adjustment')
        browser.screenshot(out / 'task-conflict.png')
        browser.click('.task-update button[type="submit"]')
        browser.wait('document.querySelector(".task-control-status").textContent.includes("已提交")')
        browser.evaluate('__s5Acceptance.applyUpdates()')
        browser.wait('document.querySelector(".task-control-status").textContent.includes("已应用第 3 版")')
        check('accepted and applied updates have distinct visible states', browser.evaluate('document.querySelector(".task-update textarea").value') == '')
        browser.screenshot(out / 'task-update.png')
        for theme in ('dark', 'cartoon', 'light'):
            if browser.evaluate('document.querySelector("#themeMenu").hidden'):
                browser.click('#themeRailBtn')
            browser.wait('!document.querySelector("#themeMenu").hidden')
            browser.click('#themeMenu [data-theme-pick="' + theme + '"]')
            browser.wait('document.documentElement.dataset.theme === ' + json.dumps(theme))
            if not browser.evaluate('document.querySelector("#themeMenu").hidden'):
                browser.click('#themeRailBtn')
            browser.wait('document.querySelector("#themeMenu").hidden')
            check('task UI retains ' + theme + ' theme', browser.evaluate('document.documentElement.dataset.theme') == theme)
            browser.screenshot(out / ('task-' + theme + '.png'))
        browser.call('Emulation.setDeviceMetricsOverride', {'width':375,'height':812,'deviceScaleFactor':1,'mobile':True})
        if browser.evaluate('document.querySelector("#sidebarToggleBtn").getAttribute("aria-expanded") === "true"'):
            browser.click('#sidebarToggleBtn')
        browser.wait('document.querySelector("#mainContainer").classList.contains("sidebar-hidden")')
        browser.click('.task-update textarea')
        check('task controls fit mobile viewport', browser.evaluate('document.documentElement.scrollWidth <= innerWidth && document.querySelector(".task-controls").getBoundingClientRect().width <= innerWidth'))
        check('mobile update input is visible and receives native pointer focus', browser.evaluate('(()=>{const e=document.querySelector(".task-update textarea"),r=e.getBoundingClientRect();return document.activeElement===e && document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===e;})()'))
        browser.screenshot(out / 'task-mobile.png')
        browser.call('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
        browser.click('#sidebarToggleBtn')
        browser.wait('!document.querySelector("#mainContainer").classList.contains("sidebar-hidden")')
        browser.evaluate('__s5Acceptance.disconnectWithInterruptedStep()')
        browser.wait('document.querySelector(".tool-trace-label").textContent === "部分完成" && !!document.querySelector(".task-recovery button")')
        check('disconnect replay restores canonical partial terminal and eligible step', browser.evaluate('document.querySelector(".task-update").hidden'))
        browser.click('.tool-trace > summary')
        browser.screenshot(out / 'task-recovery-available.png')
        browser.click('.task-recovery button')
        browser.wait('document.querySelector(".task-control-status").textContent.includes("此步骤已恢复") && !document.querySelector(".task-recovery button")')
        check('recovered step preserves original partial task terminal', browser.evaluate('document.querySelector(".tool-trace-label").textContent') == '部分完成')
        check('rejection updates and recovery never replay the chat POST', browser.evaluate('__s5Acceptance.log.filter(r=>r.path==="/api/v2/chat" && r.method==="POST").length') == 1)
        browser.screenshot(out / 'task-recovered.png')
        log = browser.evaluate('__s5Acceptance.log')
        (out / 'endpoint-sources.json').write_text(json.dumps({'real_backend_probe':observed,'requests':log,
            'limitation':'Task API responses simulated in owned browser. No full new-backend integration or nonempty Markdown answer tested. External HTTPS blocked.'},ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception:
        browser.screenshot(out / 'failure.png')
        (out / 'failure-ui.txt').write_text(browser.evaluate('document.body.innerText') or '',encoding='utf-8')
        raise
    finally:
        (out / 'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'checks':checks,'evidence':str(out),'task_endpoints':'simulated; real production frontend and browser'},ensure_ascii=False))
