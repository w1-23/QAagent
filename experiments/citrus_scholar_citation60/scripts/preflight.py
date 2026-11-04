#!/usr/bin/env python3
"""Safe read-only source code checks; never claims real deployment passed."""
import argparse,json,re,subprocess,sys,hashlib
from pathlib import Path
from common import ROOT,sha256_file,utc,jsondump
p=argparse.ArgumentParser();p.add_argument('--repo',required=True,help='Exact local QAagent repository directory');p.add_argument('--out',default='01_preflight/preflight_observed.json');a=p.parse_args()
repo=Path(a.repo).resolve();res={'checked_utc':utc(),'repo':str(repo),'observations':{},'manual_ui_e2e':'PENDING','production_sha':'PENDING','sse_issue_confirmed':'NOT_YET_REPRODUCED'}
if not repo.exists():raise SystemExit('Repo does not exist; no fake PASS: '+str(repo))
def check(rel):
 f=repo/rel
 if f.is_file():return {'exists':True,'sha256':sha256_file(f),'bytes':f.stat().st_size}
 return {'exists':False}
for f in ['README.md','01_src/src/tools/__init__.py','01_src/src/graph/tool_policy.py','01_src/src/graph/tool_availability.py','01_src/src/graph/general_agent_graph.py','01_src/web/assets/app.js','01_src/src/core/resource_access.py','01_src/src/api/main.py']:
 res['observations'][f]=check(f)
try:
 git=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,capture_output=True,text=True,timeout=10,check=True);res['git_sha']=git.stdout.strip()
except Exception as e:res['git_sha']=f'UNVERIFIED: {e}'
toolfile=repo/'01_src/src/tools/__init__.py'
if toolfile.exists():
 t=toolfile.read_text(encoding='utf-8');res['tool_registrations_detected']=len(re.findall(r'^    \([a-z_][a-z_0-9]*,',t,re.M))
 sseg=repo/'01_src/src/graph/general_agent_graph.py'
 if sseg.exists():
  tx=sseg.read_text(encoding='utf-8');res['sse_potential_markers']={'uses_emit_text': 'emit_text(' in tx,'verified_artifact_links': '_append_verified_artifact_links' in tx,'text_reset_supported_in_code':'emit_text_reset(' in tx}
jsondump(a.out,res)
print(json.dumps({'out':str(ROOT/a.out),'git_sha':res['git_sha'],'registered_tool_approx':res.get('tool_registrations_detected'),'manual_gates_required':'YES; this is only source static inspection'},ensure_ascii=False,indent=2))
