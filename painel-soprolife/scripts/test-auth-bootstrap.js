/* Offline deny-by-default regression; real loading transitions are also covered
 * by test-auth-bootstrap-browser.cjs in Chromium and Firefox. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const panel=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(panel,'js/boot-gate.js'),'utf8');
const html=fs.readFileSync(path.join(panel,'index.html'),'utf8');
const inert=html.match(/<template id="authorizedPanelTemplate">([\s\S]*?)<\/template>/);
assert(inert,'Protected panel must be inert at parse time');
const live=html.replace(inert[0],'');
assert(!/class="(?:nav-item|section|nav-hub-card)|id="(?:lifeMasthead|lifePanelSoproLife|lifePanelEegLife)"/.test(live));
assert(live.includes('id="authShell"'));
assert(!/<script src="\.\/js\/(?:app|m15-nucleo|central-cadastros|report-workflow)\.js/.test(live));
async function checkFailure() {
  let rejectFetch, appendCount=0;
  const elements={authShell:{hidden:false,setAttribute(){}},authMessage:{},authRetry:{hidden:true}};
  const body={dataset:{authState:'loading'},appendChild(){appendCount++;}};
  const window={SoproM15Security:{canAuthenticate:()=>true},addEventListener(){},location:{replace(){}},sessionStorage:{getItem(){},setItem(){}}};
  const context={window,document:{body,getElementById:id=>elements[id]},location:{},fetch:()=>new Promise((_,reject)=>{rejectFetch=reject;}),AbortSignal,URL,Promise,Event};
  vm.runInNewContext(source,context);
  const gate=window.SoproBootGate;
  assert.equal(gate.estado,'loading');
  assert.equal(gate.can('admin'),false);
  gate.identidade={papeis_efetivos:['admin']};
  assert.equal(gate.can('admin'),false,'An identity alone never grants access during loading');
  rejectFetch(new Error('synthetic offline'));
  await gate.pronto;await gate.interfacePronta;
  assert.equal(gate.estado,'denied');assert.equal(gate.can('admin'),false);
  assert.equal(appendCount,0);assert.equal(elements.authShell.hidden,false);assert.equal(elements.authRetry.hidden,false);
}
checkFailure().then(()=>console.log('Auth bootstrap: inert initial DOM, unknown/loading/failed identity denied PASS')).catch(e=>{console.error(e);process.exitCode=1;});
