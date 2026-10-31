const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const js=fs.readFileSync('app/static/app.js','utf8');
for(const role of ['viewer','call_operator','finance','warehouse','engineer','technical_support','manager','admin'])test(`${role} assessment home actions`,async()=>{
 const content={innerHTML:''},writable=['admin','manager','engineer','technical_support'].includes(role);
 const ctx=vm.createContext({currentUser:{role},render:{},$:()=>content,warm:async()=>{},api:async url=>url==='/api/damage-assessments'?[{id:1,status:'in_progress'}]:[],esc:x=>x||'',recordRef:()=>'',orderNo:()=>'',badge:()=>'',fmtTime:()=>'',table:(headers,rows)=>rows.join('')});
 vm.runInContext(js.slice(js.indexOf('const damageAssessmentWritable='),js.indexOf('render.point_maps=')),ctx);
 await ctx.render.damage_sop();
 assert.equal(content.innerHTML.includes('onclick="newDamageAssessment()"'),writable);
 assert.equal(content.innerHTML.includes('继续定损'),writable);
 assert.ok(content.innerHTML.includes('showDamageAssessment(1)'));
});
for(const role of ['viewer','finance',null])test(`${role} cannot open create dialog directly`,async()=>{
 let calls=0,toasts=0;const ctx=vm.createContext({currentUser:role?{role}:null,toast:()=>toasts++,warm:async()=>calls++});
 vm.runInContext(js.slice(js.indexOf('const damageAssessmentWritable='),js.indexOf('const damageKnowledgeEditor='))+js.slice(js.indexOf('async function newDamageAssessment('),js.indexOf('async function showDamageAssessment(')),ctx);
 await ctx.newDamageAssessment();assert.equal(calls,0);assert.equal(toasts,1);
});
