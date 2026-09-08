/* eslint-disable @typescript-eslint/no-require-imports */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const source=fs.readFileSync(path.join(__dirname,'../app/CapabilityWording.tsx'),'utf8');
const context={exports:{},require};
vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,context);
const render=capability=>renderToStaticMarkup(React.createElement(context.exports.CapabilityWording,{capability}));
test('LBA model and scope are both visible',()=>{
  const html=render({model:'Airbus Helicopters Deutschland GmbH EC135',limitation:'A3 - Hubschrauber (nur Line Maintenance)',aircraft_type_code:null});
  assert.match(html,/Airbus Helicopters Deutschland GmbH EC135/);
  assert.match(html,/A3 - Hubschrauber \(nur Line Maintenance\)/);
  assert.doesNotMatch(html,/Recorded ICAO type/);
});
test('legacy combined wording survives without inventing a model',()=>{
  const html=render({model:null,limitation:'Aircraft A3 HELICOPTERS EC135',aircraft_type_code:null});
  assert.match(html,/Not separately recorded/);
  assert.match(html,/Aircraft A3 HELICOPTERS EC135/);
});
test('model-only and structured ICAO records are explicit',()=>{
  const html=render({model:'EC135',limitation:null,aircraft_type_code:'EC35'});
  assert.match(html,/EC135/); assert.match(html,/EC35/); assert.match(html,/Not supplied/);
});
