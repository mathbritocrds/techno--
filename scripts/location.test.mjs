import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html=readFileSync(new URL('../app/static/index.html',import.meta.url),'utf8');
const source=html.slice(html.indexOf('async function getEmployeePosition('),html.indexOf('function clockSample('));
const position=(accuracy,age=0)=>({coords:{latitude:-23.55,longitude:-46.63,accuracy},timestamp:Date.now()-age});
function browser(responses,secure=true){
  const calls=[],context=vm.createContext({Date,isSecureContext:secure,navigator:{geolocation:{getCurrentPosition(ok,error,options){calls.push(options);const response=responses.shift();response?.code?error(response):ok(response)}}}});
  vm.runInContext(source,context);
  return {calls,read:limit=>context.getEmployeePosition(limit)};
}

test('a recent ordinary fix is sufficient within the configured tolerance',async()=>{
  const b=browser([position(120)]),result=await b.read(150);
  assert.equal(result.coords.accuracy,120);assert.equal(b.calls.length,1);
  assert.equal(b.calls[0].enableHighAccuracy,false);assert.equal(b.calls[0].maximumAge,15000);
});
test('unavailable ordinary location retries with high accuracy',async()=>{
  const b=browser([{code:2},position(40)]);
  assert.equal((await b.read(150)).coords.accuracy,40);assert.equal(b.calls.length,2);
  assert.equal(b.calls[1].enableHighAccuracy,true);assert.equal(b.calls[1].maximumAge,0);
});
test('a custom stricter policy triggers refinement',async()=>{
  const b=browser([position(120),position(30)]);
  assert.equal((await b.read(40)).coords.accuracy,30);assert.equal(b.calls.length,2);
});
test('permission denial is reported without another location request',async()=>{
  const b=browser([{code:1}]);await assert.rejects(b.read(150),/Permita o acesso/);assert.equal(b.calls.length,1);
});
test('stale cached readings are replaced and never returned',async()=>{
  const b=browser([position(10,180000),position(100)]);
  assert.equal((await b.read(150)).coords.accuracy,100);
  const stale=browser([position(10,180000),position(5,180000)]);
  await assert.rejects(stale.read(150),/localização recente/);
});
test('a failed refinement keeps the real accuracy for server validation',async()=>{
  const b=browser([position(200),{code:3}]);
  assert.equal((await b.read(150)).coords.accuracy,200);
});
test('insecure contexts fail before collecting any location',async()=>{
  const b=browser([],false);await assert.rejects(b.read(150),/HTTPS/);assert.equal(b.calls.length,0);
});
