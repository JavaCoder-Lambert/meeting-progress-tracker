const test = require('node:test');
const assert = require('node:assert/strict');
const {createFollowupGuard,saveFollowup} = require('../../static/js/history-followup.js');

test('unsaved response is acknowledged before navigating to another risk', async () => {
  let state = {response: '未暂存答复'};
  const events = [];
  const guard = createFollowupGuard({version:0, getState:()=>state, valid:()=>true,
    onVersion:()=>{}, save:async ({version,state})=>{events.push(['save', state.response]); return {ok:true,version:version+1,state};}});
  guard.mark();
  assert.equal(await guard.navigate('?risk=2',url=>events.push(['navigate',url])),true);
  assert.deepEqual(events,[['save','未暂存答复'],['navigate','?risk=2']]);
  assert.equal(guard.dirty,false);
});

for (const failure of ['invalid','network','conflict','rejected']) test(`a ${failure} followup cannot leave the page or discard its response`, async () => {
  const state={response:'保留本次答复'}; const visited=[];
  const guard=createFollowupGuard({version:0,getState:()=>state,valid:()=>failure!=='invalid',save:async()=>{
    throw Object.assign(new Error('保存失败'),{conflict:failure==='conflict',rejected:failure==='rejected'});
  }});
  guard.mark();
  assert.equal(await guard.navigate('/return',url=>visited.push(url)),false);
  assert.equal(guard.dirty,true); assert.equal(state.response,'保留本次答复'); assert.deepEqual(visited,[]);
});

test('lost save response is retried exactly before newer typing can navigate', async () => {
  let state={response:'第一次答复'}; let server={version:0,state:{}}; let lost=true;
  const sent=[]; const visited=[];
  const guard=createFollowupGuard({version:0,getState:()=>state,valid:()=>true,save:async body=>{
    sent.push(body);
    if (body.version===server.version) server={version:server.version+1,state:body.state};
    else assert.deepEqual(body.state,server.state);
    if (lost) {lost=false; state={response:'保存回包丢失后的新答复'}; guard.mark(); throw new Error('lost response');}
    return {ok:true,...server};
  }});
  guard.mark(); assert.equal(await guard.navigate('?risk=2',url=>visited.push(url)),false);
  assert.equal(await guard.navigate('?risk=2',url=>visited.push(url)),true);
  assert.deepEqual(sent.map(body=>[body.version,body.state.response]),[[0,'第一次答复'],[0,'第一次答复'],[1,'保存回包丢失后的新答复']]);
  assert.equal(server.state.response,'保存回包丢失后的新答复'); assert.deepEqual(visited,['?risk=2']);
});

test('HTTP adapter sends form fields and recognizes conflict instead of following HTML', async () => {
  const original=global.fetch; const sent=[];
  global.fetch=async(url,options)=>{sent.push([url,Object.fromEntries(options.body)]); return {ok:false,status:409,
    headers:{get:()=> 'application/json'},json:async()=>({ok:false,error:'其他页面已修改'})};};
  try {
    await assert.rejects(()=>saveFollowup('/followups',{version:4,state:{response:'未丢失',csrfmiddlewaretoken:'test'}}),error=>error.conflict===true);
    assert.deepEqual(sent,[['/followups',{response:'未丢失',csrfmiddlewaretoken:'test',version:'4'}]]);
  } finally {global.fetch=original;}
});

test('mounted page intercepts risk links, saves entered response, and guards browser unload', async () => {
  const fs=require('node:fs'); const vm=require('node:vm');
  const documentEvents={}; const formEvents={}; const windowEvents={}; const visited=[];
  const fields={response:'',csrfmiddlewaretoken:'test'};
  const form={action:'/followups',elements:{version:{value:'0'}},dataset:{followupDirty:'true'},reportValidity:()=>true,
    addEventListener:(key,fn)=>{formEvents[key]=fn;}};
  const status={textContent:''}; let release;
  const responsePromise=new Promise(resolve=>{release=resolve;});
  const context={URLSearchParams,AbortController,setTimeout,clearTimeout,
    FormData:class {constructor(){return Object.entries({...fields,version:form.elements.version.value});}},
    fetch:async()=>{await responsePromise; return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({ok:true,version:1,state:fields})};},
    document:{querySelector:selector=>selector==='[data-followup-form]'?form:status,addEventListener:(key,fn)=>{documentEvents[key]=fn;}},
    window:{MeetingWorkspace:require('../../static/js/meeting-workspace.js'),location:{assign:url=>visited.push(url)},addEventListener:(key,fn)=>{windowEvents[key]=fn;}}};
  vm.runInNewContext(fs.readFileSync(require.resolve('../../static/js/history-followup.js'),'utf8'),context);
  documentEvents.DOMContentLoaded();
  let boundPrevented=false; windowEvents.beforeunload({preventDefault:()=>{boundPrevented=true;}});
  assert.equal(boundPrevented,true,'server-rejected bound input must be protected immediately');
  fields.response='输入的新答复'; formEvents.input();
  let prevented=false; const link={href:'?risk=2',target:'',hasAttribute:()=>false,getAttribute:()=>'?risk=2'};
  documentEvents.click({target:{closest:()=>link},preventDefault:()=>{prevented=true;}});
  assert.equal(prevented,true); assert.deepEqual(visited,[]);
  let unloadPrevented=false; windowEvents.beforeunload({preventDefault:()=>{unloadPrevented=true;}});
  assert.equal(unloadPrevented,true);
  release(); await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(visited,['?risk=2']); assert.equal(form.elements.version.value,1);
});
