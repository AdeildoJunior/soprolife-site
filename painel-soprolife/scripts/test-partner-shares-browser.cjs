/* Real browsers, real local proxy/API, synthetic private DB. Production gate off. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),cp=require('node:child_process'),net=require('node:net');
const {chromium,firefox}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'../..'),core=path.join(root,'painel-soprolife/nucleo-m15'),python=process.env.M15_TEST_PYTHON;
assert(python,'M15_TEST_PYTHON required');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'soprolife-partner-browser-')),children=[];
async function port(){return new Promise(r=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});}
async function wait(f){for(let i=0;i<100;i++){try{if(await f())return;}catch{}await new Promise(r=>setTimeout(r,100));}throw Error('Synthetic service timeout');}
function run(args,env){const p=cp.spawnSync(python,args,{cwd:core,env,encoding:'utf8'});assert.equal(p.status,0,p.stderr.slice(-1800));}
(async()=>{let browser;try{
 const apiPort=await port(),panelPort=await port();
 const env={...process.env,M15_ENV:'dev',M15_DATABASE_URL:'sqlite:///'+path.join(temp,'test.db'),M15_API_HOST:'127.0.0.1',M15_API_PORT:String(apiPort),M15_AUTH_SECRET:'whatsapp-browser-synthetic-secret-32-chars',M15_SESSION_COOKIE_PATH:'/painel-soprolife',M15_NOVA_SENHA:'senha-sintetica-123',WHATSAPP_INTEGRATION_ENABLED:'false',WHATSAPP_VERIFY_TOKEN:'',WHATSAPP_APP_SECRET:'',WHATSAPP_WABA_ID:'',WHATSAPP_PHONE_NUMBER_ID:'',WHATSAPP_IDENTITY_KEY:''};
 run(['-m','alembic','upgrade','head'],env);
 run(['-m','app.cli','criar-usuario','--email','wa-browser@teste.local','--nome','Operador Sintetico','--papel','admin'],env);
 run(['-c',`from sqlalchemy.orm import Session
from sqlalchemy import select
from app.db import get_engine
from app.models import User,Partner,PartnerUnit
from app.services.partner_share_setup import configure
from datetime import date
with Session(get_engine()) as db:
 user=db.scalar(select(User))
 for code in ('maclin','alergocare'):configure(db,code,date(2026,1,1),user.id)
 p=Partner(public_code='CLI-SYN-L',nome='ALERGO LIFE',status='ativa');db.add(p);db.flush()
 db.add(PartnerUnit(public_code='UNI-SYN-L',partner_id=p.id,nome='ALERGO LIFE'))
 p=Partner(public_code='CLI-SYN-P',nome='Pastore',status='ativa');db.add(p);db.flush()
 db.add(PartnerUnit(public_code='UNI-SYN-P',partner_id=p.id,nome='Pastore Ipanema'));db.commit()`],env);
 for(const [bin,args,cwd,e] of [[python,['-m','app.serve'],core,env],['python3',[path.join(root,'painel-soprolife/scripts/command-center-local-server.py')],root,{...env,SOPROLIFE_PANEL_HOST:'127.0.0.1',SOPROLIFE_PANEL_PORT:String(panelPort),SOPROLIFE_M15_UPSTREAM:`http://127.0.0.1:${apiPort}/api/v1`}]] )children.push(cp.spawn(bin,args,{cwd,env:e,stdio:'ignore'}));
 const base=`http://127.0.0.1:${panelPort}/painel-soprolife/`;await wait(async()=> (await fetch(base+'api/m15/health')).ok);
 assert.equal((await fetch(`http://127.0.0.1:${apiPort}/api/v1/integrations/whatsapp/webhook`,{method:'POST',body:'{}'})).status,503);
 for(const [engine,name] of [[chromium,'chromium'],[firefox,'firefox']]){
  browser=await engine.launch({headless:true});const page=await browser.newPage({locale:'pt-BR',viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.locator('#loginForm [name=email]').fill('wa-browser@teste.local');await page.locator('#loginForm [name=password]').fill('senha-sintetica-123');await page.locator('#loginForm button[type=submit]').click();
  await page.waitForFunction(()=>window.SoproM15?.can('operacional'));await page.waitForFunction(()=>Boolean(document.querySelector('#cardsGrid')?.children.length));

  const day=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const cfg=await page.evaluate(async()=> (await window.SoproM15.api('/atendimentos/configuracao')).espirometria_soprolife);
  assert(!JSON.stringify(cfg.parcerias_comerciais).includes('ALERGO LIFE'));
  await page.evaluate(()=>window.SoproCentral.open('atendimento',{tipo:'espirometria_soprolife'}));
  await page.locator('#cadFormAtend').waitFor();
  assert.equal(await page.locator('.cad-tipo').count(),6);
  const quick=await page.locator('[name=esp_local_opcao] option').allTextContents();
  assert(quick.includes('Coworking Downtown')&&quick.includes('Coworking Nova América'));
  assert(!quick.some(t=>/maclin|alergocare|alergo life|pastore/i.test(t)));
  for(const local of ['Coworking Downtown','Coworking Nova América']){
   await page.locator('[name=esp_local_opcao]').selectOption(local);
   assert.equal(await page.locator('[name=esp_local]').inputValue(),local);
   assert.equal(await page.locator('[name=esp_origem]').inputValue(),'');
  }
  await page.locator('[name=esp_origem]').fill('Indicação');
  await page.locator('[name=esp_modalidade]').selectOption('residencial');
  assert.equal(await page.locator('[name=esp_modalidade]').inputValue(),'residencial');
  assert.equal(await page.locator('[name=esp_origem]').inputValue(),'Indicação');
  await page.locator('[name=esp_local_opcao]').selectOption('Domicílio do paciente');
  assert.equal(await page.locator('[name=esp_parceria_comercial]').inputValue(),'');
  await page.locator('[name=esp_modalidade]').selectOption('cowork');
  await page.locator('[name=esp_local_opcao]').selectOption('__manual__');
  await page.locator('[name=esp_local]').fill('Coworking sintético manual');
  await page.locator('[name=esp_bd]').selectOption('false');
  await page.locator('[name=esp_valor]').fill('123,45');
  await page.locator('[name=tipo][value=espirometria_pastore]').check();
  assert.equal(await page.locator('[name=esp_bd]').inputValue(),'false');
  assert((await page.locator('#cadAtBlocoEsp').innerText()).includes('Pastore Ipanema'));
  assert.equal(await page.locator('[name=esp_valor]').count(),0);
  await page.locator('[name=tipo][value=espirometria_maclin]').check();
  await page.locator('[name=tipo][value=espirometria_alergocare]').check();
  await page.locator('[name=tipo][value=espirometria_soprolife]').check();
  assert.equal(await page.locator('[name=esp_local]').inputValue(),'Coworking sintético manual');
  assert.equal(await page.locator('[name=esp_origem]').inputValue(),'Indicação');
  assert.equal(await page.locator('[name=esp_valor]').inputValue(),'123,45');
  for(const code of ['maclin','alergocare']){
   await page.evaluate(()=>window.SoproCentral.open('atendimento',{tipo:'espirometria_soprolife'}));
   await page.locator('#cadFormAtend').waitFor();
   const initial=await page.locator('[name=esp_bd]').inputValue();
   await page.locator('[name=cadAtP_nome]').fill('Pessoa Sintética '+code+' '+name);
   await page.locator(`[name=tipo][value=espirometria_${code}]`).check();
   await page.locator('.m15-date:has([name=esp_data]) .m15-date-input').fill(day.split('-').reverse().join('/'));
   await page.locator('.m15-date:has([name=esp_data]) .m15-date-input').blur();
   assert.equal(await page.locator('[name=esp_parceria_comercial]').inputValue(),code);
   assert.equal(await page.locator('[name=esp_municipio]').inputValue(),'3304557');
   assert.equal(await page.locator('[name=esp_origem]').inputValue(),cfg.parcerias_comerciais.find(r=>r.codigo===code).origem_operacional);
   assert(await page.locator('[name=esp_parceria_comercial]').isHidden());
   assert(await page.locator('[name=esp_modalidade]').isHidden());
   assert(await page.locator('[name=esp_local_opcao]').isHidden());
   assert(!(await page.locator('[name=esp_valor]').getAttribute('readonly')));
   assert.equal(await page.locator('[name=esp_modalidade]').inputValue(),'clinica_parceira');
   assert.equal(await page.locator('[name=esp_bd]').inputValue(),initial);
   assert.equal(await page.locator('[name=esp_local]').inputValue(),cfg.parcerias_comerciais.find(r=>r.codigo===code).unidade);
   await page.locator('[name=esp_valor]').fill('100,00');
   await page.locator('[name=esp_pgto_forma]').selectOption('Dinheiro');
   await page.locator('[name=esp_recebido_por]').selectOption('dividido');
   await page.locator('#cadMaclinPartilha').getByText('Parcela parceiro (30%):',{exact:false}).waitFor();
   if(code==='maclin')assert(!(await page.locator('#cadAtNfse').innerText()).includes('Esta modalidade não tem fluxo fiscal'));
   const summary=await page.locator('#cadMaclinPartilha').innerText();assert(summary.includes('100,00')&&summary.includes('30,00')&&summary.includes('70,00'));
   for(const [gross,own,partner] of [['220,00','154,00','66,00'],['123,45','86,41','37,04'],['100,00','70,00','30,00']]){
    await page.locator('[name=esp_valor]').fill(gross);
    const text=await page.locator('#cadMaclinPartilha').innerText();
    assert(text.includes(gross)&&text.includes(own)&&text.includes(partner),text);
   }
   if(code==='alergocare'){
    assert((await page.locator('#cadAtNfse').innerText()).includes('Esta modalidade não tem fluxo fiscal'));
    await page.locator('[name=esp_valor]').fill('123,45');
   }
   for(const width of [1440,800,390]){
    await page.setViewportSize({width,height:1000});
    assert(await page.locator('#cadFormAtend').evaluate(el=>el.getBoundingClientRect().right<=innerWidth+2));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
    await page.screenshot({path:path.join(temp,`${name}-${code}-${width}.png`),fullPage:true});
   }
   await page.setViewportSize({width:1440,height:1000});
   // Reflow a 200% de uma janela de 1440 px: viewport CSS efetivo de 720 px.
   // CSS zoom isolado não muda media queries e não simula o zoom do navegador.
   await page.setViewportSize({width:720,height:800});
   assert(await page.locator('#cadFormAtend').evaluate(el=>el.getBoundingClientRect().right<=innerWidth+2));
   await page.screenshot({path:path.join(temp,`${name}-${code}-zoom200.png`),fullPage:true});
   await page.setViewportSize({width:1440,height:1000});
   const response=page.waitForResponse(r=>r.url().endsWith('/atendimentos/novo-paciente')&&r.request().method()==='POST');
   await page.locator('#cadFormAtend button[type=submit]').focus();await page.keyboard.press('Enter');
   const result=await response.catch(async e=>{console.error(await page.locator('#cadFormAtend').innerText());console.error(await page.locator('.m15-toast, .cad-campo-erro').allTextContents());throw e;});assert.equal(result.status(),201,await result.text());
   const saved=await result.json();assert.equal(saved.partilha_parceiro.bruto,code==='alergocare'?'123.45':'100.00');
   const rule=cfg.parcerias_comerciais.find(r=>r.codigo===code);
   assert.equal(saved.espirometria.partner_id,rule.partner_id);
   assert.equal(saved.espirometria.partner_unit_id,rule.partner_unit_id);
   assert.equal(saved.espirometria.origem,rule.origem_operacional);
   assert.equal(saved.partilha_parceiro.a_pagar,'0.00');assert.equal(saved.partilha_parceiro.a_receber,'0.00');
   assert.equal(await page.locator('dialog[open]').count(),0);
  }
  await page.locator('.nav-item[data-section=financeiro]').click();
  await page.locator('#partnerSharesRoot h4').first().waitFor();
  const rows=await page.evaluate(async()=> (await window.SoproM15.api('/financeiro/partilhas')).itens);
  const row=rows.find(r=>r.parceiro.toLowerCase()==='maclin'&&!r.movimentos.length);assert(row);
  const f=page.locator(`[data-share="${row.id}"]`);
  await f.locator('..').locator('summary').click();
  await f.locator('[name=valor]').fill('70,00');await f.locator('[name=data]').fill(day);
  await f.locator('[name=hora]').fill('19:28:24');await f.locator('[name=conta]').fill('C6 PJ');await f.locator('[name=origem]').fill('Faustino');
  await f.locator('button[type=submit]').focus();await page.keyboard.press('Enter');
  await page.waitForFunction(id=>!document.querySelector(`[data-share="${id}"]`),row.id);
  const ledger=await page.evaluate(async id=>(await window.SoproM15.api('/lancamentos?tamanho=100')).itens.filter(e=>e.spirometry_exam_id===id),row.exam_id);
  assert.equal(ledger.filter(e=>e.tipo==='receita').length,1);assert.equal(ledger.find(e=>e.tipo==='receita').valor,'100.00');
  assert.equal(ledger.filter(e=>e.tipo==='movimentacao').length,1);
  for(const width of [1440,800,390]){
   await page.setViewportSize({width,height:1000});
   assert(await page.locator('#partnerSharesRoot').evaluate(el=>el.getBoundingClientRect().right<=innerWidth+2));
   await page.screenshot({path:path.join(temp,`${name}-finance-${width}.png`),fullPage:true});
  }
  await page.setViewportSize({width:1440,height:1000});await page.evaluate(()=>document.body.style.zoom='2');
  assert(await page.locator('#partnerSharesRoot').evaluate(el=>el.getBoundingClientRect().width<=innerWidth+2));
  assert.deepEqual(errors,[]);await browser.close();browser=null;
  console.log(`${name}: defaults, independent clinics, auto 30/70, receiver, keyboard save, settlement, unique revenue, 1440/800/390 and 200% PASS`);
 }
 console.log('Synthetic visual artifacts: '+temp);
}finally{if(browser)await browser.close();for(const p of children)p.kill('SIGTERM');}})().catch(e=>{console.error(e);process.exitCode=1;});
