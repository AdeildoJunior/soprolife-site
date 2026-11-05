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
from datetime import date,datetime,timezone
from sqlalchemy.orm import Session
from app.db import Base,get_engine
from app.models import *
from app.security import ensure_roles_exist,get_role,hash_password
Base.metadata.create_all(get_engine())
with Session(get_engine()) as db:
 ensure_roles_exist(db)
 u=User(email='profit-synthetic@example.invalid',nome='Admin Sintético',password_hash=hash_password('senha-apenas-sintetica-123'))
 u.roles.append(get_role(db,'admin'));db.add(u);db.flush()
 p=Person(public_code='PES-SYN1',nome_completo='Pessoa Sintética',nome_normalizado='pessoa sintetica');db.add(p);db.flush()
 doctor=PhysicianProfile(user_id=u.id,professional_name='Médico Sintético',crm_number='000001',crm_state='RJ',verification_status='pending');db.add(doctor);db.flush()
 db.add(PhysicianTransfer(physician_profile_id=doctor.id,competencia=date(2026,9,1),eligible_report_count=3,unit_amount=15,reference_total=45,paid_amount=45,payment_date=date(2026,10,1),status='Pago',created_by_user_id=u.id,payment_registered_by_user_id=u.id,payment_registered_at=datetime(2026,10,1,tzinfo=timezone.utc)))
 for key,value in [('material',10),('technician',0),('tax_percent',6),('home.parking',0),('home.fuel',10)]:db.add(ProfitCostRule(key=key,value=value,effective_from=date(2026,9,1),reason='Evidência sintética para teste'))
 for n in (1,2,3):
  e=SpirometryExam(public_code=f'ESP-SYN{n}',person_id=p.id,status='Realizado',local_atendimento='SHOPPING DOWNTOWN' if n==1 else 'Domiciliar',modalidade='cowork' if n==1 else 'residencial',data_exame=date(2026,9,14+n))
  db.add(e);db.flush()
  release=datetime(2026,9,20,tzinfo=timezone.utc)
  db.add(ReportDocument(public_code=f'LAU-SYN{n}',spirometry_exam_id=e.id,status='liberado',created_by_user_id=u.id,clinical_started_at=release,ready_for_signature_at=release,released_at=release,released_by_user_id=u.id,released_physician_profile_id=doctor.id,signature_status='liberada_institucional',validation_code=f'SYN{n}'))
  if n!=3:db.add(FinancialEntry(public_code=f'LAN-SYN{n}',tipo='receita',categoria='Espirometria',valor=200 if n==1 else 220,status='Recebido',forma_pagamento='Pix',spirometry_exam_id=e.id,data_competencia=e.data_exame,data_recebimento=e.data_exame))
  if n==1:
   db.add(FinancialEntry(public_code='LAN-SYNT',tipo='despesa',categoria='Imposto',valor=10,status='Recebido',spirometry_exam_id=e.id))
   sess=OperationalSession(place='downtown',service_date=e.data_exame,source='manual',status='confirmado',room_override=60,parking_override=25,fuel_override=10,created_by_user_id=u.id);db.add(sess);db.flush()
   db.add(OperationalSessionExam(session_id=sess.id,spirometry_exam_id=e.id))
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
  const page=await browser.newPage({viewport:{width:1440,height:1000},locale:'pt-BR'}),errors=[];
  const fillDate=async(selector,value)=>{const display=page.locator(selector).locator('..').locator('.m15-date-input');await display.fill(value.split('-').reverse().join('/'));await display.press('Tab');};
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/painel-soprolife/');await page.locator('#email').fill(EMAIL);await page.locator('#password').fill(PASSWORD);await page.locator('#entrarBtn').click();
  await page.waitForFunction(()=>window.SoproM15?.getUser());
  await wait(()=>page.locator('.nav-item[data-section="financeiro"]').evaluate(el=>{el.click();return document.querySelector('#financeiro').classList.contains('active');}));
  await page.locator('#profitTitle').waitFor();
  await page.locator('#profitFilters [name="period"]').selectOption('personalizado');
  await fillDate('#profitFilters [name="start"]','2026-09-01');await fillDate('#profitFilters [name="end"]','2026-09-30');
  await page.locator('#profitFilters button[type="submit"]').click();
  await page.locator('.profit-exam').first().waitFor();
  const kpi=label=>page.locator('.profit-kpi').filter({has:page.locator('small',{hasText:new RegExp('^'+label+'$')})});
  assert.match(await kpi('Receita comprovada').innerText(),/420,00/);
  assert.match(await kpi('Lucro por competência').innerText(),/241,80/);
  assert.match(await kpi('Margem por competência').innerText(),/57.6%/);
  assert.match(await kpi('Exames com cálculo completo').innerText(),/2/);
  assert.match(await kpi('Exames com cálculo pendente').innerText(),/1/);
  for(const width of [1440,430,390]){
   await page.setViewportSize({width,height:1000});
   const detail=page.locator('.profit-exam').filter({hasText:'ESP-SYN2'});
   await detail.locator('summary').first().click();
   assert.match(await detail.innerText(),/Receita comprovada[\s\S]*220,00/);
   assert.match(await detail.innerText(),/171,80/);
   assert.match(await detail.innerText(),/NÃO EMITIDA/);
   assert.match(await detail.innerText(),/Imposto gerencial estimado — 6%/);
   await page.waitForTimeout(400);
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
   if(overflow){
    const widths=await page.evaluate(()=>{const el=document.querySelector('#profitPanelRoot');const withPanel=document.documentElement.scrollWidth;el.hidden=true;const baseline=document.documentElement.scrollWidth;el.hidden=false;return {withPanel,baseline};});
    assert.ok(widths.withPanel<=widths.baseline,`${name}: lucro aumentou overflow herdado`);
    console.log(`BASELINE ${name} ${width}px: tabela de repasses já produz largura ${widths.baseline}px; painel de lucro não amplia`);
   }
   const panelOverflow=await page.locator('.profit-panel').evaluate(el=>el.scrollWidth>el.clientWidth+2);
   assert.equal(panelOverflow,false,`${name} painel overflow ${width}`);
   await detail.locator('summary').first().focus();await page.keyboard.press('Enter');
   assert.equal(await detail.getAttribute('open'),null);
   const calendar=page.locator('#profitFilters [name="start"]').locator('..');
   await calendar.locator('.m15-date-btn').click();
   const bounds=await calendar.locator('.m15-cal').boundingBox();
   assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width,`${name}: calendário fora da tela`);
   await page.keyboard.press('ArrowRight');await page.keyboard.press('Escape');
   assert.equal(await calendar.locator('.m15-cal').count(),0);
   await page.locator('[data-profit-rules]').click();await page.locator('dialog[open]').waitFor();
   assert.match(await page.locator('dialog').innerText(),/vigência histórica somente com prova/);
   await fillDate('dialog [name="effective_from"]','2026-09-01');
   await page.locator('dialog [name="reason"]').fill('Referência sintética somente para teste visual');
   await page.locator('dialog [data-close]').click();
   await page.locator('#profitTitle').scrollIntoViewIfNeeded();
   await page.screenshot({path:path.join(tmp,`${name}-${width}.png`),fullPage:false});
   await page.locator(".profit-panel").screenshot({path:path.join(tmp,`${name}-${width}-panel.png`)});
   console.log(`PASS ${name} ${width}px: KPIs, pendências, detalhes, teclado, regra histórica e sem overflow`);
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('.profit-panel').evaluate(el=>el.style.zoom='2');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${name} zoom 200%`);
  await page.locator('.profit-panel').evaluate(el=>el.style.zoom='');
  // Competência calculada sem NFS-e; exame sem receita continua pendente.
  await page.locator('#profitFilters [name="local"]').selectOption('home');await page.locator('#profitFilters button[type="submit"]').click();
  await page.waitForFunction(()=>document.querySelectorAll('.profit-exam').length===2);
  assert.match(await kpi('Receita comprovada').innerText(),/220,00/);
  assert.match(await kpi('Lucro por competência').innerText(),/171,80/);
  const series=await page.evaluate(()=>Chart.getChart(document.querySelector('#profitMonthly')).data.datasets.map(d=>({label:d.label,data:d.data})));
  assert.equal(series.find(d=>d.label==='Receita comprovada').data[0],220);
  assert.equal(series.find(d=>d.label==='Lucro por competência').data[0],171.8);
  const unknown=page.locator('.profit-exam').filter({hasText:'ESP-SYN3'});await unknown.locator('summary').first().click();
  assert.match(await unknown.innerText(),/Receita comprovada\s*—/);
  assert.match(await page.locator('.profit-chart-pending').first().innerText(),/Lucro pendente/);
  // Erro e vazio continuam compreensíveis e recuperáveis.
  await fillDate('#profitFilters [name="start"]','2026-09-30');await fillDate('#profitFilters [name="end"]','2026-09-01');await page.locator('#profitFilters button[type="submit"]').click();
  await page.locator('#profitPanelRoot [role="alert"]').waitFor();
  await page.reload();await wait(()=>page.locator('.nav-item[data-section="financeiro"]').evaluate(el=>{el.click();return document.querySelector('#financeiro').classList.contains('active');}));await page.locator('#profitTitle').waitFor();
  assert.match(await page.locator('.profit-list').innerText(),/Nenhum exame produtivo/);
  assert.equal(errors.length,0,JSON.stringify(errors));
  console.log(`PASS ${name}: populações mistas, competência sem NFS-e, receita desconhecida, gráficos, vazio, erro e zoom 200%`);
  await browser.close();browser=null;
 }
 console.log('Artefatos sintéticos:',tmp);
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();for(const child of children)child.kill('SIGTERM');});
