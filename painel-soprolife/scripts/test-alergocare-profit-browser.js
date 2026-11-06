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
from datetime import date
from decimal import Decimal
from sqlalchemy.orm import Session
from app.db import Base,get_engine
from app.models import *
from app.security import ensure_roles_exist,get_role,hash_password
from app.services.maclin import record_basis
from tests.test_profit_competence import seed
Base.metadata.create_all(get_engine())
with Session(get_engine()) as db:
 ensure_roles_exist(db)
 u=User(email='profit-synthetic@example.invalid',nome='Admin Sintético',password_hash=hash_password('senha-apenas-sintetica-123'))
 u.roles.append(get_role(db,'admin'));db.add(u);db.flush()
 p=Person(public_code='PES-ALERGO-SYN',nome_completo='Pessoa Sintética',nome_normalizado='pessoa sintetica');db.add(p);db.flush()
 partner=Partner(public_code='CLI-ALERGO-SYN',nome='ALERGOCARE',status='ativa');db.add(partner);db.flush()
 e=seed(db,{'id':p.id},{'admin':u},day=date(2026,10,27))(1,'ALERGOCARE',partner_id=partner.id,modalidade='cowork')
 f=FinancialEntry(public_code='LAN-ALERGO-SYN',tipo='receita',categoria='Espirometria',valor=219,status='Recebido',forma_pagamento='Cartão',spirometry_exam_id=e.id);db.add(f);db.flush()
 record_basis(db,e,f,basis='bruto',gross_amount=Decimal('219'),evidence_note='Bruto confirmado em teste sintético',user_id=u.id,request_id='synthetic')
 sess=OperationalSession(place='alergocare',service_date=e.data_exame,source='manual',status='confirmado',room_override=0,parking_override=10,fuel_override=10,created_by_user_id=u.id);db.add(sess);db.flush()
 db.add(OperationalSessionExam(session_id=sess.id,spirometry_exam_id=e.id));db.commit()
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
  const page=await browser.newPage({viewport:{width:1440,height:1000},locale:'pt-BR'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/painel-soprolife/');await page.locator('#email').fill(EMAIL);await page.locator('#password').fill(PASSWORD);await page.locator('#entrarBtn').click();
  await page.waitForFunction(()=>window.SoproM15?.getUser());
  await wait(()=>page.locator('.nav-item[data-section="financeiro"]').evaluate(el=>{el.click();return document.querySelector('#financeiro').classList.contains('active');}));
  await page.locator('#profitTitle').waitFor();await page.locator('#profitFilters [name="period"]').selectOption('personalizado');
  for(const [key,value] of [['start','01/10/2026'],['end','31/10/2026']]){
   const display=page.locator(`#profitFilters [name="${key}"]`).locator('..').locator('.m15-date-input');await display.fill(value);await display.press('Tab');
  }
  await page.locator('#profitFilters button[type="submit"]').click();
  const exam=page.locator('.profit-exam').filter({hasText:'ESP-SYN1'});await exam.waitFor();await exam.locator('summary').first().click();
  assert.match(await exam.innerText(),/ALERGOCARE.*219,00.*153,30.*65,70/s);
  assert.match(await exam.innerText(),/88,15/);
  assert.doesNotMatch(await page.locator('.profit-panel').innerText(),/não classificado/i);
  assert.equal(await page.locator('#profitFilters [name="local"] option[value="alergocare"]').innerText(),'ALERGOCARE');
  for(const width of [1440,768,390]){
   await page.setViewportSize({width,height:1000});await exam.scrollIntoViewIfNeeded();
   assert.equal(await page.locator('.profit-panel').evaluate(el=>el.scrollWidth>el.clientWidth+2),false);
   await page.screenshot({path:path.join(tmp,`alergocare-${name}-${width}.png`)});
  }
  await exam.locator('summary').first().focus();await page.keyboard.press('Enter');assert.equal(await exam.getAttribute('open'),null);
  await page.locator('.profit-panel').evaluate(el=>el.style.zoom='2');
  assert.equal(await page.locator('.profit-panel').evaluate(el=>el.scrollWidth>el.clientWidth+2),false);
  assert.deepEqual(errors,[]);
  console.log(`PASS ${name}: ALERGOCARE classificada, bruto 219, receita 153,30, parceiro 65,70 sem dupla dedução, lucro 88,15, três larguras, teclado e zoom 200%`);
  await browser.close();browser=null;
 }
 console.log('Capturas sintéticas:',tmp);
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();for(const child of children)child.kill('SIGTERM');});
