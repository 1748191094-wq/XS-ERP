// Execute the production async prefill closure with controllable API responses.
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const js=fs.readFileSync('app/static/app.js','utf8');
const block=js.slice(js.indexOf('  let previewRequest=0,'),js.indexOf("  form.elements.repair_order_id.addEventListener('change'"));
function harness(){
 const classes=new Set(),source={isConnected:true,textContent:''},rows=[],requests=[],toasts=[];
 const retryClasses=new Set(['hidden']),retryButton={disabled:false,classList:{add:k=>retryClasses.add(k),remove:k=>retryClasses.delete(k)},addEventListener:(name,fn)=>retryButton.click=fn};
 const elements=Object.fromEntries(['repair_order_id','assessment_result','assessment_responsibility','repair_recommendation'].map(k=>[k,{value:k==='repair_order_id'?'1':''}]));
 const form={elements,querySelector:s=>s==='#quote-assessment-source'?source:s==='#quote-assessment-retry'?retryButton:rows.at(-1),querySelectorAll:()=>rows.filter(r=>r.dataset.assessmentEstimate)};
 let activeForm=form;
 const ctx=vm.createContext({document:{activeElement:null},base:null,form,$:s=>s==='#modal-form'?activeForm:{classList:{contains:k=>classes.has(k)}},api:()=>new Promise((resolve,reject)=>requests.push({resolve,reject})),addQuoteLine:item=>rows.push({item,dataset:{},remove(){rows.splice(rows.indexOf(this),1)}}),toast:message=>toasts.push(message)});
 vm.runInContext(block+'\nglobalThis.run=prefillAssessment;globalThis.state=()=>({previewPending,previewFailed,assessmentSourceId});',ctx);
 return {ctx,classes,source,rows,requests,toasts,elements,retryButton,retryClasses,replace(){source.isConnected=false;activeForm={}}};
}
const result=id=>({assessment:{id,assessment_no:`D${id}`,conclusion:`结论${id}`},suggested_item:{item_name:`维修${id}`}});
for(const state of ['is-closing','hidden','replaced'])for(const outcome of ['resolve','reject'])test(`${state} ignores late ${outcome}`,async()=>{
 const h=harness(),p=h.ctx.run();const before=h.source.textContent;
 if(state==='replaced')h.replace();else h.classes.add(state);
 h.requests[0][outcome](outcome==='resolve'?result(1):new Error('offline'));await p;
 assert.equal(h.source.textContent,before);assert.equal(h.elements.assessment_result.value,'');assert.equal(h.rows.length,0);assert.equal(h.toasts.length,0);assert.equal(h.ctx.state().assessmentSourceId,null);assert.equal(h.ctx.state().previewFailed,false);
});
test('newer selection owns source, content and pending state',async()=>{
 const h=harness(),old=h.ctx.run();h.elements.repair_order_id.value='2';const newer=h.ctx.run();
 h.requests[0].resolve(result(1));await old;assert.equal(h.ctx.state().previewPending,true);assert.equal(h.rows.length,0);
 h.requests[1].resolve(result(2));await newer;assert.equal(h.ctx.state().assessmentSourceId,2);assert.equal(h.elements.assessment_result.value,'结论2');assert.equal(h.rows.length,1);
});
test('active dialog still displays request errors',async()=>{const h=harness(),p=h.ctx.run();h.requests[0].reject(new Error('offline'));await p;assert.equal(h.ctx.state().previewFailed,true);assert.match(h.source.textContent,/offline/);assert.match(h.source.textContent,/未能读取/)});
test('user typing during request is preserved',async()=>{const h=harness(),p=h.ctx.run();h.elements.assessment_result.value='人工结论';h.requests[0].resolve(result(1));await p;assert.equal(h.elements.assessment_result.value,'人工结论');assert.equal(h.ctx.state().assessmentSourceId,1)});

async function failOnce(h){const p=h.ctx.run();h.requests.at(-1).reject(new Error('offline'));await p}
test('retry preserves manual conclusions and rows while resolving source',async()=>{
 const h=harness();await failOnce(h);assert.equal(h.retryClasses.has('hidden'),false);
 h.elements.assessment_result.value='人工结论';h.elements.assessment_responsibility.value='人工责任';
 const row={item:{item_name:'人工报价',unit_price:'180'},dataset:{}};h.rows.push(row);
 const p=h.retryButton.click();assert.equal(h.retryButton.disabled,true);assert.equal(h.ctx.state().previewPending,true);
 assert.equal(h.elements.assessment_result.value,'人工结论');assert.equal(h.rows[0],row);
 await h.retryButton.click();assert.equal(h.requests.length,2);
 h.requests[1].resolve(result(8));await p;
 assert.equal(h.elements.assessment_result.value,'人工结论');assert.equal(h.elements.assessment_responsibility.value,'人工责任');assert.equal(h.rows[0],row);assert.equal(h.rows.length,2);
 assert.equal(h.ctx.state().assessmentSourceId,8);assert.equal(h.ctx.state().previewFailed,false);assert.equal(h.retryClasses.has('hidden'),true);
});
test('repeat failure keeps retry available and preserves editing',async()=>{
 const h=harness();await failOnce(h);h.elements.assessment_result.value='保留';const p=h.retryButton.click();h.requests[1].reject(new Error('offline again'));await p;
 assert.equal(h.retryButton.disabled,false);assert.equal(h.retryClasses.has('hidden'),false);assert.equal(h.ctx.state().previewFailed,true);assert.equal(h.elements.assessment_result.value,'保留');
});
test('switch during retry rejects old response and resets prior context',async()=>{
 const h=harness();await failOnce(h);h.elements.assessment_result.value='旧结论';const old=h.retryButton.click();h.elements.repair_order_id.value='2';const fresh=h.ctx.run();
 assert.equal(h.elements.assessment_result.value,'');h.requests[1].resolve(result(1));await old;assert.equal(h.ctx.state().previewPending,true);
 h.requests[2].resolve(result(2));await fresh;assert.equal(h.ctx.state().assessmentSourceId,2);assert.equal(h.elements.assessment_result.value,'结论2');
});
test('closed dialog does not start a retry',async()=>{const h=harness();await failOnce(h);h.classes.add('hidden');await h.retryButton.click();assert.equal(h.requests.length,1)});
test('retry without completed assessment preserves manual work and resolves no source',async()=>{const h=harness();await failOnce(h);h.elements.assessment_result.value='手动说明';const p=h.retryButton.click();h.requests[1].resolve({assessment:null,suggested_item:null});await p;assert.equal(h.ctx.state().assessmentSourceId,null);assert.equal(h.ctx.state().previewFailed,false);assert.equal(h.elements.assessment_result.value,'手动说明');assert.equal(h.rows.length,0);await h.retryButton.click();assert.equal(h.requests.length,2)});
test('successful retry moves focus away from hidden retry button',async()=>{const h=harness();await failOnce(h);let focused=false;h.source.focus=()=>focused=true;h.ctx.document.activeElement=h.retryButton;const p=h.retryButton.click();h.requests[1].resolve(result(1));await p;assert.equal(focused,true);assert.equal(h.retryClasses.has('hidden'),true)});
