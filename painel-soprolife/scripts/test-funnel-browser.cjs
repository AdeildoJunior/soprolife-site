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
  await page.setViewportSize({width:1440,height:1000});
  if(name==='chromium') await page.evaluate(async()=>{
   const api=window.SoproM15.api;
   const partner=await api('/parceiros',{method:'POST',body:JSON.stringify({nome:'Pastore',tipo:'clinica',status:'ativa'})});
   await api('/unidades',{method:'POST',body:JSON.stringify({partner_id:partner.id,nome:'Pastore Ipanema'})});
  });
  const openAttendance=async tipo=>{
   await page.evaluate(tipo=>window.SoproCentral.open('atendimento',{tipo}),tipo);
   await page.locator('#cadFormAtend').waitFor();
   await page.waitForFunction(tipo=>document.querySelector('[name=tipo]:checked')?.value===tipo,tipo);
  };
  const dateField=name=>page.locator(`.m15-date:has([name=${name}]) .m15-date-input`);
  await openAttendance('espirometria_soprolife');
  const defaults=await page.locator('#cadFormAtend').evaluate(f=>Object.fromEntries(
   ['esp_bd','esp_status','esp_municipio','esp_responsavel','esp_equipamento','esp_followup'].map(k=>[k,f.elements[k].value])));
  await page.locator('[name=esp_bd]').selectOption('false');
  await dateField('esp_data').fill('31/08/2026');await dateField('esp_data').blur();
  await page.locator('[name=tipo][value=espirometria_pastore]').check();
  assert.equal(await page.locator('[name=esp_bd]').inputValue(),'false','type from same exam survives switch');
  assert.equal(await page.locator('[name=esp_followup]').inputValue(),'2027-03-01','shared calendar-month followup');
  await dateField('esp_followup').fill('10/03/2027');await dateField('esp_followup').blur();
  await dateField('esp_data').fill('01/09/2026');await dateField('esp_data').blur();
  assert.equal(await page.locator('[name=esp_followup]').inputValue(),'2027-03-10','manual followup preserved');
  // Discard only this synthetic draft to test fresh defaults on both paths.
  page.once('dialog',d=>d.accept());await openAttendance('espirometria_pastore');
  const pastoreDefaults=await page.locator('#cadFormAtend').evaluate(f=>Object.fromEntries(
   ['esp_bd','esp_status','esp_municipio','esp_responsavel','esp_equipamento','esp_followup'].map(k=>[k,f.elements[k].value])));
  assert.deepEqual(pastoreDefaults,defaults,'Pastore shares SoproLife clinical defaults');
  assert.equal(await page.locator('[name=esp_pgto_status]').count(),0);
  await page.getByText('Pastore Ipanema',{exact:true}).first().waitFor();
  for(const [tipo,patient] of [['espirometria_pastore','Amostra Parceria '+name],['espirometria_soprolife','Cadastro Direto '+name]]){
   if(tipo!=='espirometria_pastore')await openAttendance(tipo);
   await page.locator('[name=cadAtP_nome]').fill(patient);
   await dateField('esp_data').fill(day.split('-').reverse().join('/'));await dateField('esp_data').blur();
   if(tipo==='espirometria_soprolife'){
    await page.locator('[name=esp_valor]').fill('');
    await page.locator('[name=esp_local]').fill('Espaço Sintético');
   }
   for(const width of [1440,800,390]){
    await page.setViewportSize({width,height:1000});
    await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:path.join(temp,`${name}-${tipo}-${width}.png`),fullPage:true});
    assert(await page.locator('#cadFormAtend').evaluate(el=>el.getBoundingClientRect().right<=innerWidth+2));
   }
   // Keyboard enters the primary submit action; no contact lookup or modal.
   let contactLookups=0;const listener=r=>{if(r.url().includes('/funil/contatos'))contactLookups++;};page.on('request',listener);
   const response=page.waitForResponse(r=>r.url().endsWith('/atendimentos/novo-paciente')&&r.request().method()==='POST');
   const save=page.locator('#cadFormAtend button[type=submit]');await save.focus();await page.keyboard.press('Enter');
   const result=await response.catch(async e=>{console.log('SYNTHETIC SAVE DEBUG',await page.locator('#cadFormAtend').evaluate(f=>({status:f.querySelector('.cad-submit-status')?.textContent,invalid:[...f.elements].filter(x=>x.validity&&!x.validity.valid).map(x=>({name:x.name,validation:x.validationMessage})),dialogs:document.querySelectorAll('dialog[open]').length})));throw e;});assert.equal(result.status(),201,await result.text());
   const saved=(await result.json()).espirometria;
   assert.equal(saved.lead_id,null);assert.equal(saved.broncodilatador,true);
   if(tipo==='espirometria_pastore'){
    assert.equal(saved.modalidade,'clinica_parceira');assert.equal(saved.origem,'Pastore');
    assert.equal(saved.local_atendimento,'Pastore Ipanema');assert(saved.partner_id&&saved.partner_unit_id);
   }
   await page.getByText('Sem contato prospectivo vinculado.',{exact:false}).first().waitFor();
   assert.equal(await page.locator('dialog[open]').count(),0);assert.equal(contactLookups,0);page.off('request',listener);
  }
  const deferred=await page.evaluate(async day=>{
   const api=window.SoproM15.api,lead=(await api('/funil/contatos')).itens[0];
   const exam=await api('/espirometrias',{method:'POST',body:JSON.stringify({person_id:lead.person_id,data_exame:day,status:'Realizado',continuar_sem_rastreamento:true})});
   return {lead,exam};
  },day);
  await page.locator('.nav-item[data-section=leads]').click();
  await page.locator(`[data-link="${deferred.lead.id}"]`).click();
  await page.locator('dialog [name=exam_code]').fill(deferred.exam.public_code);
  await page.locator('dialog form button:not([type=button])').click();
  await page.locator('dialog').waitFor({state:'detached'});
  const afterLink=await page.evaluate(async id=>(await window.SoproM15.api('/funil/contatos')).itens.find(l=>l.id===id),deferred.lead.id);
  assert.equal(afterLink.primeiro_contato_em,deferred.lead.primeiro_contato_em);
  assert.equal(afterLink.agendado_em,deferred.lead.agendado_em);
  assert(afterLink.exam_ids.includes(deferred.exam.id),'deferred link through existing funnel action');
  assert.deepEqual(errors,[],'browser JS errors');
  console.log(`${name}: funnel, shared defaults, BD, manual followup, SoproLife/Pastore save without lead/modal, keyboard, 1440/800/390 PASS`);
  await browser.close();browser=null;
 }
 console.log('Synthetic visual artifacts: '+temp);
}finally{if(browser)await browser.close();for(const p of children)p.kill('SIGTERM');}})().catch(e=>{console.error(e);process.exitCode=1;});
