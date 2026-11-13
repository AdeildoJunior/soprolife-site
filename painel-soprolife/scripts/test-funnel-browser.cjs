/* Real Chromium + Firefox, synthetic database, authenticated existing proxy. */
const assert=require('node:assert/strict');const fs=require('node:fs');const os=require('node:os');const path=require('node:path');const cp=require('node:child_process');const net=require('node:net');
const {chromium,firefox}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'../..'),core=path.join(root,'painel-soprolife/nucleo-m15');
const python=process.env.M15_TEST_PYTHON;if(!python)throw Error('M15_TEST_PYTHON required');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'soprolife-funnel-browser-'));const children=[];
async function port(){return new Promise(r=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});}
async function wait(f){for(let i=0;i<100;i++){try{if(await f())return;}catch{}await new Promise(r=>setTimeout(r,100));}throw Error('Timeout synthetic service');}
function run(bin,args,env){const p=cp.spawnSync(bin,args,{cwd:core,env,encoding:'utf8'});assert.equal(p.status,0,(p.stderr||'').slice(-1800));}
(async()=>{let browser;try{
 const apiPort=await port(),panelPort=await port();
 const env={...process.env,PYTHONDONTWRITEBYTECODE:'1',M15_ENV:'dev',M15_DATABASE_URL:'sqlite:///'+path.join(temp,'test.db'),M15_API_HOST:'127.0.0.1',M15_API_PORT:String(apiPort),M15_AUTH_SECRET:'funnel-browser-synthetic-secret-32-chars',M15_SESSION_COOKIE_PATH:'/painel-soprolife',M15_NOVA_SENHA:'senha-funil-sintetica-123'};
 run(path.join(path.dirname(python),'alembic'),['upgrade','head'],env);
 run(python,['-m','app.cli','criar-usuario','--email','funil-browser@teste.local','--nome','Operador Sintetico','--papel','admin'],env);
 for(const [bin,args,cwd,e] of [[python,['-m','app.serve'],core,env],['python3',[path.join(root,'painel-soprolife/scripts/command-center-local-server.py')],root,{...env,SOPROLIFE_PANEL_HOST:'127.0.0.1',SOPROLIFE_PANEL_PORT:String(panelPort),SOPROLIFE_M15_UPSTREAM:`http://127.0.0.1:${apiPort}/api/v1`}]] ) children.push(cp.spawn(bin,args,{cwd,env:e,stdio:'ignore'}));
 const base=`http://127.0.0.1:${panelPort}/painel-soprolife/`;
 await wait(async()=> (await fetch(base+'api/m15/health')).ok);
 for(const [engine,name] of [[chromium,'chromium'],[firefox,'firefox']]){
  browser=await engine.launch({headless:true});const page=await browser.newPage({locale:'pt-BR',viewport:{width:1440,height:1000}});let errors=[];page.on('pageerror',e=>errors.push(e.message)); page.on('response',r=>{if(r.status()>=400)console.log('SYNTHETIC HTTP',r.status(),new URL(r.url()).pathname);});
  await page.goto(base);await page.locator('#loginForm').waitFor();
  await page.locator('#loginForm [name=email]').fill('funil-browser@teste.local');await page.locator('#loginForm [name=password]').fill('senha-funil-sintetica-123');await page.locator('#loginForm button[type=submit]').click();
  try { await page.waitForFunction(()=>window.SoproM15?.can('operacional')); } catch(e) { console.log('SYNTHETIC PAGE DEBUG', await page.locator('body').innerText(),errors); throw e; }
  await page.waitForFunction(()=>Boolean(document.querySelector('#cardsGrid')?.children.length));
  await page.locator('.nav-item[data-section=leads]').click();await page.locator('#funnelNew').waitFor();
  await page.locator('#funnelNew').click();await page.locator('#cadContato').waitFor();
  await page.locator('[name=cadContactP_nome]').fill('Pessoa Sintética Navegador '+name);
  await page.locator('[name=origem_aquisicao]').selectOption('google_maps');await page.locator('[name=canal_entrada]').selectOption('whatsapp');await page.locator('[name=unidade_pretendida]').selectOption('barra');
  await page.locator('#cadContato button[type=submit]').click();await page.locator('#goFunnel').waitFor();await page.locator('#goFunnel').click();
  await page.locator('#funnelContacts [data-action]').first().waitFor();
  await page.locator('#funnelContacts [data-action]').first().click();await page.locator('dialog form [name=evento]').selectOption('PRIMEIRA_RESPOSTA');await page.locator('dialog form button:not([type=button])').click();await page.locator('dialog').waitFor({state:'detached'});
  await page.locator('#funnelContacts [data-action]').first().click();await page.locator('dialog form [name=evento]').selectOption('AGENDADO');
  const day=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());await page.locator('dialog .m15-date-input').fill(day.split('-').reverse().join('/'));await page.locator('dialog .m15-date-input').blur();await page.locator('dialog form button:not([type=button])').click();await page.locator('dialog').waitFor({state:'detached'});
  await page.locator('#funnelContacts [data-performed]').first().waitFor();
  page.once('dialog',d=>d.accept());await page.locator('#funnelContacts [data-performed]').first().click();
  await page.waitForFunction(()=>document.querySelector('#funnelContacts')?.textContent.includes('Exame realizado'));
  await page.locator('#funnelContacts [data-history]').first().click();await page.locator('dialog').getByText('Exame realizado',{exact:true}).waitFor();await page.locator('dialog button').click();
  for(const width of [1440,800,390]){
   await page.setViewportSize({width,height:1000});await page.screenshot({path:path.join(temp,`${name}-${width}.png`),fullPage:true});
   assert(await page.locator('#funnelRoot').evaluate(el=>el.getBoundingClientRect().right<=window.innerWidth+2),'funnel viewport overflow '+name+' '+width);
  }
  const noContact=page.evaluate(()=>window.SoproFunnel.chooseContact(null));
  await page.locator('dialog #skip').click();assert.equal((await noContact).continuar_sem_rastreamento,true);
  assert.deepEqual(errors,[],'browser JS errors');
  console.log(`${name}: real login, new contact, response, schedule, performed, history, 1440/800/390 PASS`);
  await browser.close();browser=null;
 }
 console.log('Synthetic visual artifacts: '+temp);
}finally{if(browser)await browser.close();for(const p of children)p.kill('SIGTERM');}})().catch(e=>{console.error(e);process.exitCode=1;});
