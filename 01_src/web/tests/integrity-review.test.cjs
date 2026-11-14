'use strict';
// Execute the production page script; only DOM/network APIs are test doubles.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const vm=require('node:vm');
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.attrs={};this.value='';this.hidden=false;this.disabled=false;this._text='';}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=[...children];this._text='';}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
  setAttribute(name,value){this.attrs[name]=value;}
}
const flush=async()=>{for(let i=0;i<40;i++)await Promise.resolve();};
function browser(){
  const nodes=new Map(),requests=[],events={};
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const document={getElementById:get,createElement:tag=>new Element(tag),documentElement:new Element('html')};
  const sandbox={document,AbortController,URLSearchParams,URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},location:{search:''},
    localStorage:{getItem:()=> 'dark'},addEventListener:(name,callback)=>{events[name]=callback;},console,
    fetch:(path,options)=>new Promise(resolve=>requests.push({path,options,resolve}))};
  sandbox.window=sandbox;
  vm.runInContext(readFileSync(join(__dirname,'../assets/integrity-review.js'),'utf8'),vm.createContext(sandbox));
  return {get,requests,events,document,reply(req,data,status=200){req.resolve({ok:status<400,status,headers:{get:()=> 'application/json'},json:async()=>data});}};
}
function report(id='A') {return {review_id:id,report:{common_findings:[{finding_id:'finding-'+id,rule:'exact_column',summary:'Evidence '+id,
  locations:[{source_name:'SYNTHETIC '+id,source_sha256:'0'.repeat(64),file_id:id,rows:[2],columns:[{index:1,name:'value'}]}],
  applicability:['Finite numbers'],counterexamples:['Shared control'],coverage:{scope:'selected cells'},evidence:{value:'<script>bad()</script>'},review:{state:'pending',revision:0,note:''},review_history:[]}]}};}
async function opened(b){b.reply(b.requests[0],{reviews:[{review_id:'A',created_at:'fixture'}]});await flush();b.reply(b.requests[1],report());await flush();b.reply(b.requests[2],{columns:[{name:'value'}],rows:[{row:2,values:['<img src=x onerror=bad()>']}],coverage:'50 rows max'});await flush();}

test('renders actual evidence as text and sends explicit revision plus manual note',async()=>{
  const b=browser();await opened(b);
  assert.equal(b.document.documentElement.dataset.theme,'dark');
  assert.match(b.get('ir-sources').textContent,/<img src=x onerror=bad\(\)>/);
  assert.equal(b.get('ir-sources').children[0].children.some(c=>c.tagName==='img'),false);
  assert.match(b.get('ir-evidence').textContent,/<script>/);
  b.get('ir-state').value='needs_context';b.get('ir-note').value='Check actual sample unit';
  const saving=b.get('ir-form').onsubmit({preventDefault(){}});await flush();
  const req=b.requests.at(-1),body=JSON.parse(req.options.body);
  assert.deepEqual(body,{state:'needs_context',note:'Check actual sample unit',expected_revision:0});
  assert.equal(req.options.method,'PUT');
  b.reply(req,{detail:'conflict'},409);await saving;
  assert.match(b.get('ir-status').textContent,/记录已改变/);
  assert.equal(b.get('ir-note').value,'Check actual sample unit');
});

test('late evidence response cannot rewrite a different review or its authentication state',async()=>{
  const b=browser();b.reply(b.requests[0],{reviews:[{review_id:'A',created_at:'fixture'}]});await flush();b.reply(b.requests[1],report('A'));await flush();const old=b.requests[2];
  b.get('ir-reviews').value='B';b.get('ir-reviews').onchange.call(b.get('ir-reviews'));await flush();const next=b.requests[3];
  assert.equal(b.get('ir-evidence').textContent,'');assert.equal(b.get('ir-note').value,'');
  b.reply(next,report('B'));await flush();const sourceB=b.requests[4];
  b.reply(old,{detail:'expired'},401);await flush();
  assert.equal(b.get('ir-summary').textContent,'Evidence B');
  b.reply(sourceB,{columns:[],rows:[],coverage:'fixture'});await flush();
  assert.equal(b.get('ir-summary').textContent,'Evidence B');
});

test('expired authentication clears evidence, manual notes and review identifiers',async()=>{
  const b=browser();await opened(b);b.get('ir-note').value='Private note';
  const reload=b.get('ir-reload').onclick();await flush();b.reply(b.requests.at(-1),{detail:'login'},401);await flush();
  assert.equal(b.get('ir-evidence').textContent,'');assert.equal(b.get('ir-summary').textContent,'');
  assert.equal(b.get('ir-note').value,'');assert.equal(b.get('ir-reviews').children.length,0);
  assert.equal(b.get('ir-form').hidden,true);assert.match(b.get('ir-status').textContent,/登录已失效/);
});

test('pagehide clears private DOM and aborts outstanding requests',async()=>{
  const b=browser();await opened(b);b.events.pagehide();
  assert.equal(b.get('ir-sources').children.length,0);assert.equal(b.get('ir-evidence').textContent,'');
  assert.equal(b.requests[2].options.signal.aborted,true);
});
