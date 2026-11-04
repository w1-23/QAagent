"""Minimal B0 example. Real OpenAI-compatible model API only, no search or files.
Requires OPENAI_COMPAT_BASE_URL, OPENAI_COMPAT_API_KEY env, configured model_id.
Never fabricate model output. dsh must confirm it is the same model B2 uses.
"""
import json,os,urllib.request,urllib.parse,time

def run_case(case,config):
 base=os.environ.get('OPENAI_COMPAT_BASE_URL','').rstrip('/')
 key=os.environ.get('OPENAI_COMPAT_API_KEY','')
 model=config.get('model_id','')
 if not base or not key or not model or model.startswith('FILL'):
  raise RuntimeError('B0 not configured; set environment keys and actual model_id')
 if not base.startswith('https://') and not base.startswith('http://127.0.0.1'):
  raise RuntimeError('Refuse non-TLS remote model endpoint; localhost allowed')
 endpoint=base+'/chat/completions' if base.endswith('/v1') else base+'/v1/chat/completions'
 payload={'model':model,'messages':[{'role':'system','content':'You are a careful research assistant. Do not pretend to have retrieved external sources or read attachments. If evidence is unavailable, say so.'},{'role':'user','content':case['prompt']}], 'temperature':config.get('temperature',0),'max_tokens':config.get('max_output_tokens',2048),'stream':False}
 started=time.monotonic()
 req=urllib.request.Request(endpoint,json.dumps(payload).encode(),{'Authorization':'Bearer '+key,'Content-Type':'application/json','User-Agent':'CitrusScholarExperiment/B0'})
 try:
  with urllib.request.urlopen(req,timeout=int(config.get('timeout_seconds',120))) as r:data=json.loads(r.read(15*1024*1024))
 except Exception as e:raise RuntimeError('B0 model request failed (no credentials echoed): '+type(e).__name__) from e
 ans=data.get('choices',[{}])[0].get('message',{}).get('content','')
 if not isinstance(ans,str):ans=str(ans)
 usage=data.get('usage') or {}
 return {'status':'completed' if ans else 'no_results','final_answer':ans,'raw_answer':ans,'tool_calls':[],'citations':[],'artifact_paths':[],'model_id':str(data.get('model',model)),'tokens_in':usage.get('prompt_tokens'),'tokens_out':usage.get('completion_tokens'),'elapsed_ms':round((time.monotonic()-started)*1000,1),'api_cost':None,'implementation_notes':'No tools, no PDF; B0 descriptive diagnostic only'}
