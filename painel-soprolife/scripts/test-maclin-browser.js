#!/usr/bin/env node
/* Browser real + API/proxy oficiais em loopback e SQLite sintético isolado.
 * M15_TEST_PYTHON=/venv/bin/python PLAYWRIGHT_MODULE=/caminho/playwright node este-script
 * Chromium e Firefox instalados pelo Playwright; nenhuma conexão de produção.
 */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const os=require('node:os'),net=require('node:net'),cp=require('node:child_process');
const {chromium,firefox}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const ROOT=path.resolve(__dirname,'../..'),CORE=path.join(ROOT,'painel-soprolife/nucleo-m15');
const PY=process.env.M15_TEST_PYTHON;
assert.ok(PY,'Informe M15_TEST_PYTHON com as dependências M15.');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'soprolife-profit-browser-'));
const children=[];let browser;
const EMAIL='profit-synthetic@example.invalid',PASSWORD='senha-apenas-sintetica-123';
async function port(){return new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});}
async function wait(fn){let last;for(let n=0;n<150;n++){try{const v=await fn();if(v)return v;}catch(e){last=e;}await new Promise(r=>setTimeout(r,100));}throw last||new Error('Timeout local');}
function start(args,cwd,env,name){const out=fs.openSync(path.join(tmp,name+'.log'),'w');const p=cp.spawn(PY,args,{cwd,env,stdio:['ignore',out,out]});children.push(p);return p;}
const seed=String.raw`
from sqlalchemy.orm import Session
from app.db import Base,get_engine
from app.models import User,Person,Partner
from app.security import ensure_roles_exist,get_role,hash_password
Base.metadata.create_all(get_engine())
with Session(get_engine()) as db:
 ensure_roles_exist(db)
 u=User(email='profit-synthetic@example.invalid',nome='Admin Sintético',password_hash=hash_password('senha-apenas-sintetica-123'))
 u.roles.append(get_role(db,'admin'));db.add(u)
 db.add(Person(public_code='PES-MAC-SYN',nome_completo='Pessoa Sintética Maclin',nome_normalizado='pessoa sintetica maclin'))
 db.add(Partner(public_code='CLI-MAC-SYN',nome='MACLIN',status='ativa'))
 db.commit()
`;
async function main(){
 const apiPort=await port(),panelPort=await port();
 const env={...process.env,M15_ENV:'dev',M15_DATABASE_URL:'sqlite:///'+path.join(tmp,'synthetic.db'),M15_API_PORT:String(apiPort),M15_API_HOST:'127.0.0.1',M15_SESSION_COOKIE_SECURE:'false',M15_REPORTS_ENABLED:'false'};
 const seeded=cp.spawnSync(PY,['-c',seed],{cwd:CORE,env,encoding:'utf8'});assert.equal(seeded.status,0,seeded.stderr);
 start(['-m','app.serve'],CORE,env,'api');
 await wait(async()=>{const r=await fetch(`http://127.0.0.1:${apiPort}/api/v1/health`);return r.ok;});
 const base=`http://127.0.0.1:${panelPort}`;
 start([path.join(ROOT,'painel-soprolife/scripts/command-center-local-server.py')],ROOT,{...env,SOPROLIFE_PANEL_HOST:'127.0.0.1',SOPROLIFE_PANEL_PORT:String(panelPort),SOPROLIFE_M15_UPSTREAM:`http://127.0.0.1:${apiPort}/api/v1`},'proxy');
 await wait(async()=>{const r=await fetch(base+'/painel-soprolife/api/m15/health');return r.ok;});

 for(const [name,engine] of [['Chromium',chromium],['Firefox',firefox]]){
  browser=await engine.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/painel-soprolife/');
  await page.locator('#email').fill(EMAIL);await page.locator('#password').fill(PASSWORD);await page.locator('#entrarBtn').click();
  await page.waitForFunction(()=>window.SoproM15?.getUser());
  await page.evaluate(()=>window.SoproCentral.open('atendimento'));
  await page.locator('[name="esp_parceria_comercial"]').waitFor();
  await page.locator('[name="esp_parceria_comercial"]').selectOption('maclin');
  assert.equal(await page.locator('[name="esp_local"]').inputValue(),'MACLIN');
  assert.equal(await page.locator('[name="esp_modalidade"]').inputValue(),'cowork');
  assert.equal(await page.locator('[name="esp_pgto_forma"]').inputValue(),'');
  await page.locator('[name="esp_valor"]').fill('200,00');
  const preview=page.locator('#cadMaclinPartilha');
  assert.match(await preview.innerText(),/70%.*140,00.*30%.*60,00/s);
  assert.match(await preview.innerText(),/Cartão: taxa sobre o valor cheio; Pix: sem taxa/);
  for(const width of [1440,768,390]){
   await page.setViewportSize({width,height:1000});await preview.scrollIntoViewIfNeeded();
   assert.equal(await preview.evaluate(el=>el.scrollWidth>el.clientWidth+2),false);
   await page.screenshot({path:path.join(tmp,`maclin-${name}-${width}.png`)});
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('[name="esp_valor"]').focus();
  for(let n=0;n<4;n++){
   await page.keyboard.press('Tab');
   if(await page.evaluate(()=>document.activeElement.name==='esp_pgto_status')) break;
  }
  assert.equal(await page.evaluate(()=>document.activeElement.name),'esp_pgto_status');
  await page.locator('#cadFormAtend').evaluate(el=>el.style.zoom='2');
  assert.equal(await preview.evaluate(el=>el.scrollWidth>el.clientWidth+2),false);
  await page.locator('#cadFormAtend').evaluate(el=>el.style.zoom='');
  await page.locator('#cadAtPQ').fill('PES-MAC-SYN');await page.locator('#cadAtPBuscar').click();
  await page.locator('#cadAtPResultados [data-pid]').first().click();
  const carrier=page.locator('[name="esp_data"]');
  const display=carrier.locator('..').locator('.m15-date-input');
  await display.fill('05/11/2026');await display.press('Tab');
  await page.locator('[name="esp_pgto_forma"]').selectOption('Cartão');
  const request=page.waitForRequest(r=>r.url().endsWith('/atendimentos')&&r.method()==='POST');
  const response=page.waitForResponse(r=>r.url().endsWith('/atendimentos')&&r.request().method()==='POST');
  await page.locator('#cadFormAtend button[type="submit"]').click();
  const sent=await request;
  assert.equal(sent.postDataJSON().espirometria.parceria_comercial,'maclin');
  assert.equal(sent.postDataJSON().financeiro.espirometria.valor,'200.00');
  const reply=await response;
  assert.equal(reply.status(),201,await reply.text());
  const body=await reply.json();assert.equal(body.partilha_maclin.soprolife,'140.00');assert.equal(body.partilha_maclin.maclin,'60.00');
  assert.deepEqual(errors,[]);
  console.log(`PASS ${name}: novo Maclin, preview 70/30, 3 larguras, zoom 200%, teclado, POST oficial com bruto 200 e partilha 140/60, sem pagamento automático`);
  await browser.close();browser=null;
 }
 console.log('Capturas sintéticas:',tmp);
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();for(const child of children)child.kill('SIGTERM');});
