const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const {webcrypto} = require('node:crypto');
const elements = new Map();
const drawing = new Proxy({}, {get: () => () => ({width:10})});
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    textContent:'', value:'test', hidden:false, disabled:false, dataset:{},
    classList:{toggle(){}, add(){}, remove(){}},
    listeners:{}, setAttribute(){}, appendChild(){}, addEventListener(type, fn){this.listeners[type]=fn;},
    getBoundingClientRect: () => ({width:900, height:600}), getContext: () => drawing,
  });
  return elements.get(id);
}
const sandbox = {
  document:{getElementById:element, querySelector:element, createElement:element},
  window:{innerWidth:1200, scrollY:0, addEventListener(){}, devicePixelRatio:1},
  localStorage:{getItem:()=>null, setItem(){}}, location:{search:''},
  crypto:webcrypto, URLSearchParams, AbortController, TypeError,
  setInterval(){}, clearTimeout,
  setTimeout:(fn,ms)=>setTimeout(fn, Math.min(ms,20)),
  EventSource:class {}, fetch:()=>new Promise(()=>{}), console,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(0,'utf8') + `
globalThis.ui = {
  updateStats, applyEditor, saveServerCalibration, requestJSON, handleEvent,
  setProfile(p) { calibration=p; currentProfile='test'; profileNameInput.value='test'; },
  setEditor(e) { editorSession=e; },
  getProfile() { return calibration; },
  loadServerCalibration,
};`, sandbox);
const ui = sandbox.ui;
const base = {schema_version:'quest.calibration_editor/v1', active:true, state:'calibrating',
  revision:'r1', profile:'test', tracking_valid:true};
const profile = {version:5, state:'ready', origin:{x:0,y:1,z:0},
  right:{x:1,y:0,z:0},forward:{x:0,y:0,z:1},up:{x:0,y:1,z:0}};
const jsonResponse = (value,status=200)=>({ok:status<300,status,text:async()=>JSON.stringify(value)});
const feedback = ()=>element('actionFeedback');
async function main() {
  ui.setEditor({...base}); ui.setProfile({...profile}); ui.updateStats();
  let resolveSave, requests=0;
  sandbox.fetch = (url)=>{
    if(url === '/editor/command') { requests++; return new Promise(resolve=>{resolveSave=resolve;}); }
    return Promise.resolve(jsonResponse(url === '/calibrations' ? {active:'test',profiles:[]} : profile));
  };
  const saving = element('saveProfile').onclick();
  assert.equal(feedback().dataset.kind,'pending');
  assert.equal(element('saveProfile').textContent,'Saving…');
  assert.equal(element('cancelCalibration').disabled,true);
  await element('saveProfile').onclick();
  assert.equal(requests,1); // rapid duplicate click has no side effects
  resolveSave(jsonResponse({applied:true,editor:{...base,active:false,state:'awaiting_b',revision:'r2',last_action:'finish'}}));
  await saving;
  assert.equal(feedback().dataset.kind,'success');
  assert.match(feedback().textContent,/Saved and applied: test/);
  assert.equal(element('saveProfile').textContent,'Saved ✓');
  for(let i=0;i<8;i++) ui.updateStats();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.match(feedback().textContent,/Saved and applied/); // profile refresh cannot overwrite it

  // A rejected disk write is visible and the draft is retained.
  ui.setEditor({...base}); ui.setProfile({...profile});
  sandbox.fetch = async()=>jsonResponse({applied:false,message:'Disk full. Free space and retry.'},409);
  await element('saveProfile').onclick();
  assert.equal(feedback().dataset.kind,'error');
  assert.match(feedback().textContent,/Disk full/);
  ui.updateStats(); assert.match(feedback().textContent,/Disk full/);
  assert.ok(ui.getProfile().origin);

  // Renaming before Finish must not load an older browser draft on input blur.
  sandbox.localStorage.getItem=()=>JSON.stringify({version:5,state:'new'});
  element('profileName').value='existing';
  element('profileName').listeners.change();
  assert.ok(ui.getProfile().origin);
  assert.equal(ui.getProfile().profile,'existing');
  sandbox.localStorage.getItem=()=>null;

  // Incomplete input and absent controller are actionable, not silent no-ops.
  ui.setProfile({version:5,up:profile.up});
  await element('saveProfile').onclick();
  assert.match(feedback().textContent,/Complete right, forward, and origin/);
  ui.setEditor({...base,tracking_valid:false});
  await element('calibNext').onclick();
  assert.match(feedback().textContent,/Controller unavailable/);

  // Explicit capture feedback including insufficient movement.
  ui.setEditor({...base});
  ui.handleEvent({type:'pose',point:{x:.1,y:1,z:.1,recv_unix:Date.now()/1000}});
  await element('calibNext').onclick();
  assert.match(feedback().textContent,/Collecting right/);
  await element('calibNext').onclick();
  assert.match(feedback().textContent,/Right movement too short/);
  ui.updateStats(); assert.equal(feedback().dataset.kind,'error');
  ui.handleEvent({type:'pose',point:{x:.2,y:1,z:.1,recv_unix:Date.now()/1000}});
  await element('calibNext').onclick(); assert.match(feedback().textContent,/Right direction captured/);
  await element('calibNext').onclick(); assert.match(feedback().textContent,/Collecting forward/);
  ui.handleEvent({type:'pose',point:{x:.2,y:1,z:.2,recv_unix:Date.now()/1000}});
  await element('calibNext').onclick(); assert.match(feedback().textContent,/Forward direction captured/);
  await element('calibNext').onclick(); assert.match(feedback().textContent,/Origin captured/);
  assert.ok(ui.getProfile().origin);

  // Timeout finishes the pending UI without clearing the in-memory draft.
  ui.setProfile({...profile});
  sandbox.fetch = (_,options)=>new Promise((resolve,reject)=>{
    options.signal.addEventListener('abort',()=>reject(Object.assign(new Error(),{name:'AbortError'})));
  });
  await element('saveProfile').onclick();
  assert.match(feedback().textContent,/No confirmation within 4 seconds/);
  assert.equal(element('saveProfile').disabled,false);

  // A slow Load cannot overwrite a newer calibration action.
  let resolveLoad;
  sandbox.fetch=()=>new Promise(resolve=>{resolveLoad=resolve;});
  const loading=ui.loadServerCalibration('old');
  await element('calibNext').onclick(); // start new local calibration
  resolveLoad(jsonResponse(profile)); await loading;
  assert.equal(ui.getProfile().state,'new');

  // Browser quota failure must not prevent saving to the source.
  sandbox.localStorage.setItem=()=>{throw new Error('Quota exceeded');};
  ui.setProfile({...profile}); ui.setEditor({...base});
  sandbox.fetch=async(url)=>jsonResponse(url === '/editor/command'
    ? {applied:true,editor:{...base,active:false,state:'awaiting_b',revision:'r3',last_action:'finish'}}
    : url === '/calibrations' ? {active:'test',profiles:[]} : profile);
  await element('saveProfile').onclick();
  assert.equal(feedback().dataset.kind,'success');
  // Local view buttons acknowledge too.
  await element('fit').onclick(); assert.equal(feedback().textContent,'View fitted.');
  await element('clear').onclick(); assert.match(feedback().textContent,/Displayed path cleared/);
  console.log('Page action feedback regression checks passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
