'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const harness = {exports:{}};
vm.runInNewContext(readFileSync(join(__dirname, 'frontend-isolation.test.cjs'), 'utf8') + '\nmodule.exports={browser,flush,Node};', {
  module:harness, __dirname, console, AbortController, DOMException, TextDecoder, TextEncoder, URLSearchParams,
  require(name) { return name === 'node:test' ? {test(){}} : require(name); }
});
const {browser,flush,Node} = harness.exports;
const skill = {id:'data-audit',version:'1.0',sha256:'a'.repeat(64),description:'检查真实数据'};
function fixture() {
  const b=browser('app.js'); b.ctx.currentAccount={user_id:17}; b.ctx.sessionId='session';
  vm.runInContext(readFileSync(join(__dirname,'../assets/skill-picker.js'),'utf8'), b.ctx);
  const list=new Node(), status=new Node(), summary=new Node(), menu=new Node();
  b.ctx.skillPicker=b.ctx.CitrusSkillPicker.create({list,status,summary,menu,fetch:b.ctx.accountFetch,
    scope:()=>b.ctx.currentAccount && b.ctx.sessionId ? b.ctx.authEpoch+':'+b.ctx.sessionId : ''});
  return {...b,list,status,summary,menu,picker:b.ctx.skillPicker};
}
async function load(b, item=skill) {
  const pending=b.picker.load(); b.reply(b.requests.at(-1),{skills:[item]}); await pending;
}
function choose(b,on=true) { const box=b.list.querySelector('input'); box.checked=on; box.fire('change'); }
const plain=x=>JSON.parse(JSON.stringify(x));
test('optional skill selection loads metadata only and preserves version/hash',async()=>{
  const b=fixture(); assert.equal(b.picker.capture().items.length,0);
  await load(b); choose(b);
  assert.deepEqual(plain(b.picker.capture().items),[{id:skill.id,version:skill.version,sha256:skill.sha256}]);
  assert.match(b.summary.textContent,/数据审查/); assert.match(b.status.textContent,/自动发现/);
});
test('catalog reply after account reset cannot populate another account',async()=>{
  const b=fixture(), pending=b.picker.load(); const req=b.requests.at(-1);
  b.ctx.resetAccountState(); b.reply(req,{skills:[skill]}); await pending;
  assert.equal(b.list.children.length,0); assert.equal(b.picker.capture().items.length,0);
});
test('changed package removes stale explicit selection',async()=>{
  const b=fixture(); await load(b); choose(b); await load(b,{...skill,sha256:'b'.repeat(64)});
  assert.equal(b.picker.capture().items.length,0); assert.match(b.status.textContent,/已更新/);
});
test('request acceptance does not clear a newer selection',async()=>{
  const b=fixture(); await load(b); choose(b); const old=b.picker.capture();
  choose(b,false); choose(b,true); b.picker.acknowledge(old);
  assert.equal(b.picker.capture().items.length,1);
  b.picker.acknowledge(b.picker.capture()); assert.equal(b.picker.capture().items.length,0);
});
test('failed catalog keeps ordinary chat available and can retry',async()=>{
  const b=fixture(), pending=b.picker.load(); b.reply(b.requests.at(-1),{},503); await pending;
  assert.match(b.status.textContent,/直接提问/); await load(b); assert.equal(b.list.children.length,1);
});
test('production chat sends selection separately from the unchanged original query',async()=>{
  const b=fixture(); await load(b); choose(b); b.get('user-input').value='检查这份数据';
  await b.ctx.sendMsg(); await flush();
  const req=b.requests.find(r=>r.url==='/api/v2/chat'); assert.ok(req);
  const body=JSON.parse(req.opts.body); assert.equal(body.query,'检查这份数据');
  assert.deepEqual(body.selected_skills,[{id:skill.id,version:skill.version,sha256:skill.sha256}]);
  b.ctx.resetAccountState();
});
