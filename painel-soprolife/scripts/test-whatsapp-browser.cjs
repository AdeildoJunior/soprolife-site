/* Real browsers, real local proxy/API, synthetic private DB. Production gate off. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),cp=require('node:child_process'),net=require('node:net');
const {chromium,firefox}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'../..'),core=path.join(root,'painel-soprolife/nucleo-m15'),python=process.env.M15_TEST_PYTHON;
assert(python,'M15_TEST_PYTHON required');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'soprolife-whatsapp-browser-')),children=[];
async function port(){return new Promise(r=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});}
async function wait(f){for(let i=0;i<100;i++){try{if(await f())return;}catch{}await new Promise(r=>setTimeout(r,100));}throw Error('Synthetic service timeout');}
function run(args,env){const p=cp.spawnSync(python,args,{cwd:core,env,encoding:'utf8'});assert.equal(p.status,0,p.stderr.slice(-1800));}
(async()=>{let browser;try{
 const apiPort=await port(),panelPort=await port();
 const env={...process.env,M15_ENV:'dev',M15_DATABASE_URL:'sqlite:///'+path.join(temp,'test.db'),M15_API_HOST:'127.0.0.1',M15_API_PORT:String(apiPort),M15_AUTH_SECRET:'whatsapp-browser-synthetic-secret-32-chars',M15_SESSION_COOKIE_PATH:'/painel-soprolife',M15_NOVA_SENHA:'senha-sintetica-123',WHATSAPP_INTEGRATION_ENABLED:'false',WHATSAPP_VERIFY_TOKEN:'',WHATSAPP_APP_SECRET:'',WHATSAPP_WABA_ID:'',WHATSAPP_PHONE_NUMBER_ID:'',WHATSAPP_IDENTITY_KEY:''};
 run(['-m','alembic','upgrade','head'],env);
 run(['-m','app.cli','criar-usuario','--email','wa-browser@teste.local','--nome','Operador Sintetico','--papel','admin'],env);
 // Only metadata fixture setup in this disposable local database. No HTTP outbound.
 run(['-c',`from sqlalchemy.orm import Session
from app.db import get_engine
from app.models import WhatsAppPeer,WhatsAppIntake,WhatsAppEvent,utcnow
from datetime import timedelta
with Session(get_engine()) as db:
 for i in range(4):
  key=str(i)*64
  db.add(WhatsAppPeer(sender_key=key));db.flush()
  intake=WhatsAppIntake(id='synthetic-intake-'+str(i),sender_key=key,phone_suffix='900'+str(i),first_at=utcnow()-timedelta(minutes=10-i),state='unknown')
  db.add(intake);db.flush()
  db.add(WhatsAppEvent(event_key=str(i+4)*64,sender_key=key,intake_id=intake.id,direction='out',event_at=utcnow()-timedelta(minutes=5),state='pending'))
 db.commit()`],env);
 for(const [bin,args,cwd,e] of [[python,['-m','app.serve'],core,env],['python3',[path.join(root,'painel-soprolife/scripts/command-center-local-server.py')],root,{...env,SOPROLIFE_PANEL_HOST:'127.0.0.1',SOPROLIFE_PANEL_PORT:String(panelPort),SOPROLIFE_M15_UPSTREAM:`http://127.0.0.1:${apiPort}/api/v1`}]] )children.push(cp.spawn(bin,args,{cwd,env:e,stdio:'ignore'}));
 const base=`http://127.0.0.1:${panelPort}/painel-soprolife/`;await wait(async()=> (await fetch(base+'api/m15/health')).ok);
 assert.equal((await fetch(`http://127.0.0.1:${apiPort}/api/v1/integrations/whatsapp/webhook`,{method:'POST',body:'{}'})).status,503);
 for(const [engine,name] of [[chromium,'chromium'],[firefox,'firefox']]){
  browser=await engine.launch({headless:true});const page=await browser.newPage({locale:'pt-BR',viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#loginForm [name=email]').fill('wa-browser@teste.local');await page.locator('#loginForm [name=password]').fill('senha-sintetica-123');await page.locator('#loginForm button[type=submit]').click();
  await page.waitForFunction(()=>window.SoproM15?.can('operacional'));await page.waitForFunction(()=>Boolean(document.querySelector('#cardsGrid')?.children.length));await page.locator('.nav-item[data-section=leads]').click();
  await page.getByText('Preparado, aguardando ativação do número.',{exact:true}).waitFor();
  const index=name==='chromium'?0:2;
  await page.locator(`[data-wa-identify="synthetic-intake-${index}"]`).click();
  await page.locator('dialog [name=mode]').selectOption('new');await page.locator('dialog [name=name]').fill('Pessoa Sintética WhatsApp '+name);await page.locator('dialog [name=confirmed]').check();
  await page.locator('dialog button[type=submit]').click();await page.locator('dialog').waitFor({state:'detached'});
  await page.locator('#funnelContacts').getByText('Pessoa Sintética WhatsApp '+name,{exact:false}).waitFor();
  const lead=await page.evaluate(async name=>(await window.SoproM15.api('/funil/contatos')).itens.find(l=>l.nome_completo==='Pessoa Sintética WhatsApp '+name),name);
  assert(lead.primeira_resposta_em&&lead.primeiro_contato_em&&lead.canal_entrada==='whatsapp'&&lead.origem_aquisicao==='nao_informado');
  // Explicit search and association to an existing person, including ambiguity UX.
  await page.evaluate(async person_id=>window.SoproM15.api('/funil/contatos',{method:'POST',body:JSON.stringify({person_id,idempotency_key:crypto.randomUUID(),canal_entrada:'whatsapp',origem_aquisicao:'site',primeiro_contato_em:new Date(Date.now()-20*60000).toISOString()})}),lead.person_id);
  await page.locator(`[data-wa-identify="synthetic-intake-${index+1}"]`).click();
  await page.locator('dialog [name=query]').fill('Pessoa Sintética WhatsApp '+name);await page.locator('dialog [data-search]').click();
  await page.locator(`dialog [name=person_id] option[value="${lead.person_id}"]`).waitFor({state:'attached'});
  await page.locator('dialog [name=person_id]').selectOption(lead.person_id);await page.locator('dialog [name=confirmed]').check();
  await page.locator('dialog button[type=submit]').click();await page.locator('dialog [role=status]').getByText('Escolha o contato existente ou confirme um novo contato.',{exact:true}).waitFor();
  await page.locator('dialog [name=lead_id]').selectOption(lead.id);
  for(const width of [1440,800,390]){
   await page.setViewportSize({width,height:1000});await page.screenshot({path:path.join(temp,`${name}-identify-${width}.png`),fullPage:true});
   assert(await page.locator('dialog').evaluate(el=>el.getBoundingClientRect().width<=innerWidth),'dialog overflow');
  }
  await page.locator('dialog button[type=submit]').click();await page.locator('dialog').waitFor({state:'detached'});
  for(const width of [1440,800,390]){
   await page.setViewportSize({width,height:1000});await page.screenshot({path:path.join(temp,`${name}-${width}.png`),fullPage:true});
   assert(await page.locator('#whatsappArea').evaluate(el=>el.getBoundingClientRect().right<=innerWidth+2),'area overflow');
  }
  assert.deepEqual(errors,[]);await browser.close();browser=null;console.log(`${name}: disabled/no secrets, intake, manual registration, search, explicit association, ambiguity, echo, 1440/800/390 PASS`);
 }
 console.log('Synthetic visual artifacts: '+temp);
}finally{if(browser)await browser.close();for(const p of children)p.kill('SIGTERM');}})().catch(e=>{console.error(e);process.exitCode=1;});
