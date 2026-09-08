/* eslint-disable @typescript-eslint/no-require-imports */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ts=require('typescript');
const source=fs.readFileSync(require('node:path').join(__dirname,'../app/stopReview.ts'),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const context={exports:{}};
vm.runInNewContext(compiled,context);
const {orderedStops,maintenanceLeads}=context.exports;
test('ground sorting keeps original chronological numbers and does not mutate map sequence',()=>{
  const stops=[{number:1,ground_time_seconds:0},{number:2,ground_time_seconds:900},{number:3,ground_time_seconds:900}];
  assert.deepEqual(Array.from(orderedStops(stops,'GROUND'),s=>s.number),[2,3,1]);
  assert.deepEqual(stops.map(s=>s.number),[1,2,3]);
  assert.deepEqual(Array.from(orderedStops(stops,'NEWEST'),s=>s.number),[3,2,1]);
});
test('maintenance leads require an explicit airport link and preserve scope status',()=>{
  const sites=[{airport_ident:'EGTK',match:'SITE_MATCH'},{airport_ident:'EGTK',match:'POSSIBLE_FAMILY'},{airport_ident:null,match:'SITE_MATCH'},{airport_ident:'EGPF',match:'COMPANY_MATCH'}];
  assert.deepEqual(maintenanceLeads(sites,'EGTK').map(s=>s.match),['SITE_MATCH','POSSIBLE_FAMILY']);
});
