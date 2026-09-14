// State/race tests for the real UI functions; DOM interactions are checked separately.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('src/mlvideo/studio/static/studio.js','utf8');
function fixture(api=async()=>{}){
 const storage=new Map(),timers=new Map();let timer=0;
 const context=vm.createContext({episode:{revision:{id:'rev',segments:[]}},drafts:new Map(),api,
  localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  document:{},CSS:{escape:x=>x},$:()=>null,
  setTimeout:f=>{timers.set(++timer,f);return timer;},clearTimeout:id=>timers.delete(id)});
 vm.runInContext(source.slice(source.indexOf('function segmentDraft('),source.indexOf('function segmentRow(')),context);
 return {context,storage,timers,run:code=>vm.runInContext(code,context)};
}
test('async translation fills a role draft but never overrides edited or cleared text',()=>{
 const f=fixture();
 f.run(`s={id:'s',text:'Hello',annotation:null,suggestions:{translation:{payload:{source_text:'Hello',text:'你好',source_annotation_id:null}}}};
 episode.revision.segments=[s];drafts.set('rev/s',{role:'Mara',english:'Hello',chinese:'',expected_version:0});`);
 assert.equal(f.run('segmentDraft(s).chinese'),'你好');
 f.run(`drafts.get('rev/s').chinese='用户改写';drafts.get('rev/s').chineseEdited=true;s.suggestions.translation.payload.text='迟到结果';`);
 assert.equal(f.run('segmentDraft(s).chinese'),'用户改写');
 f.run(`drafts.get('rev/s').chinese='';`);
 assert.equal(f.run('segmentDraft(s).chinese'),'');
 f.run(`drafts.clear();s.annotation={id:'new-annotation',chinese_text:'',version:1};`);
 assert.equal(f.run('segmentDraft(s).chinese'),'');
 f.run(`s.annotation=null;s.text='Changed English';`);
 assert.equal(f.run('segmentDraft(s).chinese'),'');
});
test('typing during an in-flight note save serializes the final value and version',async()=>{
 const pending=[],requests=[];
 const f=fixture((path,body)=>{requests.push(body);return new Promise(resolve=>pending.push(resolve));});
 f.run(`s={id:'s'};episode.revision.segments=[s];editNote(s,'第一版');n=noteDraft(s);`);
 assert.equal(f.timers.size,1);
 const saving=f.run('saveNote(n)');
 f.run(`editNote(s,'最终版');`);
 assert.equal(requests.length,1);
 pending.shift()({version:1,notes:'第一版'});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(requests.length,2);
 assert.equal(requests[1].expected_version,1);
 assert.equal(requests[1].notes,'最终版');
 pending.shift()({version:2,notes:'最终版'});
 await saving;
 assert.equal(f.run('n.dirty'),false);
 assert.equal(f.run('noteStatus(n)'),'已保存');
 assert.equal(f.storage.size,0);
 assert.equal(f.run('s.annotation'),undefined);
});
test('failed notes remain recoverable and retry needs no confirmation',async()=>{
 let fail=true;
 const f=fixture(async()=>{if(fail)throw Error('offline');return {version:1,notes:'需要拆分'};});
 f.run(`s={id:'s'};episode.revision.segments=[s];editNote(s,'需要拆分');n=noteDraft(s);`);
 await f.run('saveNote(n)');
 assert.equal(f.run('noteStatus(n)'),'保存失败：offline');
 assert.equal(JSON.parse(f.storage.get('studio-note/rev/s')).value,'需要拆分');
 f.run('noteDrafts.clear();n=noteDraft(s)');
 assert.equal(f.run('n.value'),'需要拆分');
 assert.equal(f.run('n.dirty'),true);
 fail=false;await f.run('saveNote(n)');
 assert.equal(f.run('noteStatus(n)'),'已保存');
});
