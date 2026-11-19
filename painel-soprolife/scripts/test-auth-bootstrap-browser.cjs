/* Auth presentation regression: real engines + API/proxy, synthetic private DB.
 * MutationObserver installed before parsing observes every live DOM insertion;
 * rAF samples the first visible menu and every subsequent frame. No real data.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const cp = require('node:child_process'), net = require('node:net');
const {chromium, firefox} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..'), core = path.join(root, 'painel-soprolife/nucleo-m15');
const python = process.env.M15_TEST_PYTHON;
if (require.main === module) assert(python, 'M15_TEST_PYTHON required');
const temp = require.main === module ? fs.mkdtempSync(path.join(os.tmpdir(), 'soprolife-auth-bootstrap-')) : null, children = [];
const password = 'auth-synthetic-password-123';
async function port() { return new Promise(r => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); }); }
async function wait(f) { for (let i=0;i<150;i++) { try { if(await f()) return; } catch {} await new Promise(r=>setTimeout(r,100)); } throw Error('Local service timeout'); }
function run(args, env) { const p = cp.spawnSync(python,args,{cwd:core,env,encoding:'utf8'}); assert.equal(p.status,0,p.stderr.slice(-2000)); }
// Exported as browser init source for the authenticated production read-only check.
function observeAuthFrames() {
  window.authEvidence = {loadingNodes:0, forbiddenNodes:0, frames:0, menuSets:[]};
  function inspect(frame) {
    const e = window.authEvidence, role = window.__expectedClinical;
    const protectedNodes = document.querySelectorAll('.nav-item,.nav-hub-card,.section,[data-eeg-modulo],#lifeMasthead');
    if (document.body?.dataset.authState === 'loading' && protectedNodes.length) e.loadingNodes++;
    if (role && document.querySelector('.nav-item:not([data-section="laudos-espirometria"]),.section:not(#laudos-espirometria),#lifePanelEegLife,#m15-nucleo,#globalSearch')) e.forbiddenNodes++;
    if (frame && document.body?.dataset.authPublished === 'true' && !document.querySelector('#authorizedPanel')?.hidden) {
      e.frames++;
      const ids = [...document.querySelectorAll('.nav-item:not([hidden]),[data-eeg-modulo]')].map(n=>n.dataset.section||n.dataset.eegModulo).join(',');
      if (!e.menuSets.includes(ids)) e.menuSets.push(ids);
    }
  }
  new MutationObserver(()=>inspect(false)).observe(document,{childList:true,subtree:true,attributes:true});
  function frame() { inspect(true); requestAnimationFrame(frame); }
  requestAnimationFrame(frame);
}
module.exports = {observeAuthFrames};
if (require.main === module) (async()=>{let browser;try {
  const apiPort=await port(), panelPort=await port();
  const env={...process.env,M15_ENV:'dev',M15_REPORTS_ENABLED:'true',M15_REPORTS_MODE:'pilot',M15_REPORTS_STORAGE_DIR:path.join(temp,'reports'),M15_DATABASE_URL:'sqlite:///'+path.join(temp,'test.db'),M15_API_HOST:'127.0.0.1',M15_API_PORT:String(apiPort),M15_AUTH_SECRET:'auth-browser-synthetic-secret-32-chars',M15_SESSION_COOKIE_PATH:'/painel-soprolife',M15_NOVA_SENHA:password,WHATSAPP_INTEGRATION_ENABLED:'false',WHATSAPP_VERIFY_TOKEN:'',WHATSAPP_APP_SECRET:'',WHATSAPP_WABA_ID:'',WHATSAPP_PHONE_NUMBER_ID:'',WHATSAPP_IDENTITY_KEY:''};
  run(['-m','alembic','upgrade','head'],env);
  for(const role of ['admin','medico','eeg_tecnico']) run(['-m','app.cli','criar-usuario','--email',role+'@bootstrap.local','--nome','Conta Sintética '+role,'--papel',role==='eeg_tecnico'?'leitura':role],env);
  run(['-c',`from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_engine
from app.models import User,Role,PhysicianProfile
from datetime import datetime,timezone
with Session(get_engine()) as db:
 u=db.scalar(select(User).where(User.email=='eeg_tecnico@bootstrap.local')); r=db.scalar(select(Role).where(Role.name=='eeg_tecnico')); assert r; u.roles=[r]
 doctor=db.scalar(select(User).where(User.email=='medico@bootstrap.local'))
 db.add(PhysicianProfile(user_id=doctor.id,professional_name='Médica Sintética',crm_number='999999',crm_state='RJ',active=True,verification_status='verified',verified_at=datetime.now(timezone.utc),verified_by_user_id=db.scalar(select(User.id).where(User.email=='admin@bootstrap.local')),verification_reference='SYNTHETIC-BOOTSTRAP-ONLY')); db.commit()`],env);
  for(const [bin,args,cwd,e] of [[python,['-m','app.serve'],core,env],['python3',[path.join(root,'painel-soprolife/scripts/command-center-local-server.py')],root,{...env,SOPROLIFE_PANEL_HOST:'127.0.0.1',SOPROLIFE_PANEL_PORT:String(panelPort),SOPROLIFE_M15_UPSTREAM:`http://127.0.0.1:${apiPort}/api/v1`}]] ) children.push(cp.spawn(bin,args,{cwd,env:e,stdio:'ignore'}));
  const base=`http://127.0.0.1:${panelPort}/painel-soprolife/`;
  await wait(async()=>(await fetch(base+'api/m15/health')).ok);
  assert.equal((await fetch(base+'api/m15/auth/me')).status,401);
  for(const [engine,name] of [[chromium,'chromium'],[firefox,'firefox']]) {
    browser=await engine.launch({headless:true});
    for(const width of [1440,800,390]) {
      const context=await browser.newContext({viewport:{width,height:1000},locale:'pt-BR'});
      const page=await context.newPage(), errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await context.addInitScript(observeAuthFrames);
      await context.addInitScript(()=>{window.__expectedClinical=sessionStorage.getItem('testClinical')==='yes';});
      // Deliberately stagger auth, context manifest and script responses.
      await page.route('**/*',async route=>{
        const u=new URL(route.request().url());
        const delay=u.pathname.endsWith('/auth/me')?240:u.pathname.endsWith('/auth/sessao')?400:u.pathname.endsWith('/m15-config.json')?650:u.pathname.endsWith('.js')?8:0;
        if(delay)await new Promise(r=>setTimeout(r,delay));
        await route.continue();
      });
      async function login(role) {
        await page.goto(base);
        await page.locator('#loginForm [name=email]').fill(role+'@bootstrap.local');
        await page.locator('#loginForm [name=password]').fill(password);
        await page.locator('#loginForm button[type=submit]').click();
        await page.waitForFunction(()=>document.body?.dataset.authState==='loading');
        assert.equal(await page.locator('.nav-item,.section,#lifeMasthead').count(),0,'No live protected DOM during bootstrap');
        await ready(role);
      }
      async function ready(role) {
        await page.waitForFunction(()=>document.body?.dataset.authPublished==='true'&&!document.querySelector('#authorizedPanel')?.hidden);
        assert.equal(await page.evaluate(()=>window.SoproM15.can('admin')),role==='admin');
        await page.waitForFunction(()=>window.authEvidence.frames>=8);
        const proof=await page.evaluate(()=>window.authEvidence);
        assert.equal(proof.loadingNodes,0,JSON.stringify(proof));
        assert.equal(proof.forbiddenNodes,0,JSON.stringify(proof));
        assert.equal(proof.menuSets.length,1,'Authorized navigation published atomically');
        const calls=await page.evaluate(()=>performance.getEntriesByType('resource').map(r=>new URL(r.name).pathname));
        for(const suffix of ['/auth/me','/auth/sessao','/m15-config.json'])assert.equal(calls.filter(p=>p.endsWith(suffix)).length,1,'One shared bootstrap request: '+suffix);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false);
        if(role==='medico') {
          // The module must initiate the clinical queue even when it subscribes
          // after the shared session restoration event has already completed.
          await page.waitForFunction(()=>performance.getEntriesByType('resource').some(r=>new URL(r.name).pathname.endsWith('/laudos/meus')));
          const queue=await page.evaluate(async()=>{const r=await fetch('/painel-soprolife/api/m15/laudos/meus');return {status:r.status,payload:await r.json()};});
          assert.equal(queue.status,200,'Verified synthetic physician queue');
          assert.deepEqual(await page.locator('.nav-item').evaluateAll(ns=>ns.map(n=>n.dataset.section)),['laudos-espirometria']);
          assert.equal(await page.locator('#lifePanelEegLife,.nav-hub-card,#m15-nucleo,#central-cadastros,#financeiro,#fiscal,#crm,#leads,#marketing').count(),0);
          assert.equal(await page.evaluate(()=>{window.LifeCommandCenter.setContext('eeglife');return window.LifeCommandCenter.getContext();}),'soprolife');
        } else if(role==='admin') {
          assert(await page.locator('.nav-item').count()>15);
          assert.equal(await page.locator('[data-report-entry]').first().getAttribute('hidden'),null);
          assert.equal(await page.evaluate(()=>window.SoproM15.hasSession()),true);
        } else {
          assert.equal(await page.locator('#lifePanelSoproLife').count(),0);
          assert.equal(await page.locator('#ccPendingToggle').isEnabled(),true);
          assert.match(await page.locator('#ccPendingToggle').textContent(),/^Pendências \(\d+\)$/);
          assert.deepEqual(await page.locator('[data-eeg-modulo]').evaluateAll(ns=>[...new Set(ns.map(n=>n.dataset.eegModulo))].sort()),['exames','fontes','laudos']);
        }
      }
      await login('admin');
      if(process.env.AUTH_BOOTSTRAP_SCREENSHOT_DIR){fs.mkdirSync(process.env.AUTH_BOOTSTRAP_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.AUTH_BOOTSTRAP_SCREENSHOT_DIR,`${name}-${width}-admin.png`)});}
      // New login, hard refresh (routing disables cache), direct URL, both brands.
      await page.evaluate(()=>window.LifeCommandCenter.setContext('eeglife'));
      await page.reload();await ready('admin');
      assert.equal(await page.evaluate(()=>window.LifeCommandCenter.getContext()),'eeglife');
      await page.evaluate(()=>window.LifeCommandCenter.setContext('soprolife'));
      await page.goto(base+'?section=fiscal#financeiro');await ready('admin');
      // Logout must remove the complete shell synchronously, before revocation.
      const removed=await page.evaluate(()=>{window.SoproM15.logout();return !document.getElementById('authorizedPanel')&&document.body.dataset.authState==='denied';});
      assert(removed);
      await page.locator('#loginForm').waitFor();
      await page.evaluate(()=>sessionStorage.setItem('testClinical','yes'));
      await login('medico');
      if(process.env.AUTH_BOOTSTRAP_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.AUTH_BOOTSTRAP_SCREENSHOT_DIR,`${name}-${width}-medico.png`)});
      assert.equal((await context.request.get(base+'api/m15/lancamentos')).status(),403);
      assert.equal((await context.request.get(base+'api/m15/admin/usuarios')).status(),403);
      await page.evaluate(()=>localStorage.setItem('soprolife.life.contexto','eeglife'));
      await page.reload();await ready('medico');
      await page.goto(base+'?section=central-cadastros#fiscal');await ready('medico');
      await page.evaluate(()=>window.SoproM15.logout());await page.locator('#loginForm').waitFor();
      await page.evaluate(()=>sessionStorage.removeItem('testClinical'));
      await login('eeg_tecnico');
      await page.evaluate(()=>window.SoproM15.logout());await page.locator('#loginForm').waitFor();
      // Restore admin for fail-closed outage and mixed identity tests.
      await login('admin');
      for(const endpoint of ['/auth/me','/auth/sessao','/m15-config.json','/js/m15-nucleo.js']) {
        const match='**/*'+endpoint+'*';
        await page.route(match,r=>r.fulfill({status:503,body:'unavailable'}));
        await page.reload();await page.waitForFunction(()=>document.body?.dataset.authState==='denied');
        assert.equal(await page.locator('.nav-item,.section,#lifeMasthead').count(),0);
        assert(await page.locator('#authRetry').isVisible());
        await page.unroute(match);
      }
      // A valid cookie paired with another /me identity must never mount UI.
      await page.route('**/auth/me',async r=>{const res=await r.fetch();const body=await res.json();body.id='synthetic-mismatched-identity';await r.fulfill({response:res,json:body});});
      await page.reload();await page.waitForFunction(()=>document.body?.dataset.authState==='denied');
      assert.equal(await page.locator('.nav-item,.section,#lifeMasthead').count(),0);
      await page.unroute('**/auth/me');
      assert.deepEqual(errors,[],name+' '+width+' script errors');
      console.log(`${name} ${width}: delayed auth, atomic admin, doctor deny-first, EEG roles, refresh/direct URL/logout/login, 401/403, initial clinical queue, outages PASS`);
      await context.close();
    }
    await browser.close();browser=null;
  }
  console.log('Auth bootstrap presentation: PASS (synthetic local data only)');
}finally{if(browser)await browser.close();for(const c of children)c.kill();fs.rmSync(temp,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exitCode=1;});
