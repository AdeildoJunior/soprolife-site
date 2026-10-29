#!/usr/bin/env node
/* CRM: API/proxy reais em loopback e SQLite descartável. Só dados sintéticos.
 * M15_TEST_PYTHON=... PLAYWRIGHT_MODULE=... [CRM_SCREENSHOT_DIR=...] node este-arquivo
 */
"use strict";
const fs = require('fs');
const os = require('os');
const path = require('path');
const net = require('net');
const cp = require('child_process');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const m15 = path.join(root, 'painel-soprolife/nucleo-m15');
const python = process.env.M15_TEST_PYTHON;
const pw = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const shots = process.env.CRM_SCREENSHOT_DIR;
const email = 'crm-sintetico@teste.local';
const password = 'senha-crm-sintetica-123';
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'soprolife-crm-premium-'));
const children = [];
const browsers = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function port() { return new Promise(resolve => { const s = net.createServer(); s.listen(0,'127.0.0.1',()=>{const n=s.address().port;s.close(()=>resolve(n));}); }); }
function run(args,env,input) { const r=cp.spawnSync(python,args,{cwd:m15,env,encoding:'utf8',input}); assert.equal(r.status,0,(r.stderr||r.stdout).slice(-3000));return r.stdout; }
function spawn(args,env) { const p=cp.spawn(python,args,{cwd:m15,env,stdio:['ignore','pipe','pipe']}); let log='';p.stderr.on('data',d=>{log+=d;});p.stdout.on('data',d=>{log+=d;});p.log=()=>log.slice(-2000);children.push(p); }
(async()=>{
  assert.ok(python && fs.existsSync(python),'M15_TEST_PYTHON necessário');
  const api=await port(), proxy=await port();
  const env={...process.env,PYTHONDONTWRITEBYTECODE:'1',M15_ENV:'dev',M15_DATABASE_URL:`sqlite:///${temp}/crm.db`,M15_API_HOST:'127.0.0.1',M15_API_PORT:String(api),M15_AUTH_SECRET:'crm-premium-synthetic-secret-32-chars',M15_MARKETING_REFRESH_QUEUE:`${temp}/refresh.json`};
  run(['-'],env,`from app.db import Base,get_engine
from app import models
Base.metadata.create_all(get_engine())
`);
  run(['-m','app.cli','criar-usuario','--email',email,'--nome','Operador Sintético','--papel','admin'],{...env,M15_NOVA_SENHA:password});
  run(['-'],env,`from datetime import timedelta
from sqlalchemy.orm import Session
from app.db import get_engine
from app.models import Person,PersonContact,Followup,SpirometryExam
from app.services.followup import today_local
with Session(get_engine()) as db:
 today=today_local()
 for i,days in enumerate([-20,-1,25,None,180],1):
  p=Person(public_code=f'PES-{i:06}',nome_completo=f'Paciente Sintético {i} — nome extenso para validar legibilidade',nome_normalizado=f'paciente sintetico {i}')
  db.add(p);db.flush()
  if i!=1:
   db.add(PersonContact(person_id=p.id,tipo='whatsapp',valor='(21) 98888-7766',valor_normalizado='5521988887766',principal=True,nao_discavel=False))
  e=SpirometryExam(person_id=p.id,public_code=f'ESP-{i:06}',data_exame=today-timedelta(days=7),data_exame_precisao='dia',status='Realizado',origem='Site',reteste_sem_cobranca=i==5)
  db.add(e);db.flush()
  if i!=4: db.add(Followup(person_id=p.id,public_code=f'FUP-{i:06}',tipo='pos_exame',origem_entidade='spirometry_exams',origem_id=e.id,due_date=today+timedelta(days=days) if days is not None else None,due_date_manual=i!=5))
 db.commit()
`);
  const protectedSnapshot = () => run(['-'],env,`import json,hashlib
from app.db import get_engine,Base
from app import models
from sqlalchemy import select
with get_engine().connect() as db:
 names=[t for t in Base.metadata.sorted_tables if t.name.startswith(('fiscal','financial','report','spirometry','person','people','partner','payment')) or t.name in ('followups','interactions','consultations')]
 print(json.dumps({t.name:hashlib.sha256(json.dumps(sorted([dict(r) for r in db.execute(select(t)).mappings()],key=str),sort_keys=True,default=str).encode()).hexdigest() for t in names},sort_keys=True))
`);
  const before=protectedSnapshot();
  spawn(['-m','app.serve'],env);
  spawn([path.join(root,'painel-soprolife/scripts/command-center-local-server.py')],{...env,SOPROLIFE_PANEL_HOST:'127.0.0.1',SOPROLIFE_PANEL_PORT:String(proxy),SOPROLIFE_M15_UPSTREAM:`http://127.0.0.1:${api}/api/v1`});
  const base=`http://127.0.0.1:${proxy}/painel-soprolife/`;
  let ready=false;
  for(let i=0;i<100;i++){try{if((await fetch(`${base}api/m15/health`)).ok){ready=true;break;}}catch{}await sleep(100);}
  assert.ok(ready,children.map(p=>p.log()).join('\n'));
  if(shots) fs.mkdirSync(shots,{recursive:true});
  for(const engine of ['chromium','firefox']){
   const browser=await pw[engine].launch({headless:true,...(engine==='chromium'?{executablePath:process.env.CRM_CHROME||'/usr/bin/google-chrome'}:{})});browsers.push(browser);
   const page=await browser.newPage({viewport:{width:1440,height:1100}});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   const writes=[];page.on('request',r=>{if(!['GET','HEAD'].includes(r.method())) writes.push({url:r.url(),method:r.method()});});
   await page.route('https://**/*',r=>r.abort()); // nenhuma rede externa na prova
   await page.goto(base);
   await page.locator('#email').fill(email);await page.locator('#password').fill(password);
   await page.locator('#loginForm').evaluate(f=>f.requestSubmit());
   await page.waitForFunction(()=>window.SoproM15?.hasToken());
   await page.waitForFunction(()=>{
    document.querySelector('.sidebar .nav-item[data-section="crm"]')?.click();
    return document.querySelector('#crm')?.classList.contains('active');
   });
   await page.locator('.crm-patients tbody tr').first().waitFor();
   assert.equal(await page.locator('[data-crm-ws-view="pacientes"]').getAttribute('aria-selected'),'true');
   assert.equal(await page.locator('#crmWsOrdenar').inputValue(),'proximo_contato');
   assert.equal(await page.locator('.crm-patient-summary > div').count(),4);
   assert.deepEqual(await page.locator('.crm-patients tbody tr').evaluateAll(rs=>rs.map(r=>r.dataset.crmPatient)),['PES-000001','PES-000002','PES-000003','PES-000005','PES-000004']);
   const first=page.locator('[data-crm-patient="PES-000001"]');
   assert.equal(await first.locator('[data-crm-ws-wa-pessoa]').count(),0);
   assert.equal(await first.locator('.crm-ws-sem-tel').first().innerText(),'—');
   assert.equal(await page.locator('#crmSecondaryReports').getAttribute('open'),null);
   const menu=first.locator('.crm-patient-menu');
   await menu.locator('summary').focus();await page.keyboard.press('Enter');
   assert.deepEqual(await menu.locator('[data-crm-ws-tipo]').allTextContents(),['Espirometria SoproLife','Espirometria Pastore','Consulta','Espirometria + Consulta']);
   await page.keyboard.press('Escape');assert.equal(await menu.getAttribute('open'),null);
   assert.equal(await menu.locator('summary').evaluate(el=>el===document.activeElement),true);
   await first.locator('[data-crm-ws-timeline]').click();await page.locator('[role="dialog"]').waitFor();
   await page.keyboard.press('Escape');await page.locator('[role="dialog"]').waitFor({state:'detached'});
   for(const width of [390,768,1440]){
    await page.setViewportSize({width,height:1100});await sleep(150);
    await menu.locator('summary').click();
    assert.ok(await menu.locator('div').evaluate(el=>{const r=el.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth+1;}),`${engine} menu ${width} dentro da tela`);
    await page.keyboard.press('Escape');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${engine} ${width} sem overflow`);
    if(shots) await page.screenshot({path:path.join(shots,`crm-${engine}-${width}.png`),fullPage:true});
    assert.ok(await page.locator('.crm-patients').evaluate(el=>el.getBoundingClientRect().right<=innerWidth+1),`${engine} tabela ${width}: ${JSON.stringify(await page.locator('.crm-patients').evaluate(el=>({right:el.getBoundingClientRect().right,width:el.getBoundingClientRect().width,viewport:innerWidth})))}`);
    if(width===390){assert.equal(await page.locator('.crm-patients thead').isVisible(),false);}
    console.log(`PASS ${engine} ${width}: hierarquia, ações, sem overflow`);
   }
   await page.locator('[data-crm-patient="PES-000004"] [data-crm-ws-agendar]').click();
   await page.locator('#crmWsAgendarData').waitFor();
   await page.keyboard.press('Escape');
   // 200% equivale à metade da largura CSS disponível; verifica reflow e controles.
   await page.setViewportSize({width:720,height:550});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await page.locator('#crmWsBusca').fill('NENHUM REGISTRO SINTÉTICO');await page.locator('#crmWsAplicar').click();
   await page.getByText('Nenhum paciente encontrado com estes filtros.').waitFor();
   await page.locator('#crmWsBusca').fill('');await page.locator('#crmWsAplicar').click();
   await first.waitFor();
   await page.locator('#crmWsOrdenar').selectOption('nome');await first.waitFor();
   await page.locator('#crmWsSortNext').click();
   await page.waitForFunction(()=>document.querySelector('#crmWsOrdenar')?.value==='proximo_contato');
   await page.route('**/crm/pacientes/busca',r=>r.fulfill({status:503,body:'{"detail":"Falha sintética"}',contentType:'application/json'}),{times:1});
   await page.locator('#crmWsAplicar').click();await page.locator('.m15-erro').waitFor();
   await page.locator('[data-crm-ws-view="pacientes"]').click();await first.waitFor();
   // Papel de leitura: mesma renderização sem botões de escrita (RBAC API testado separadamente).
   await page.evaluate(()=>{window.__crmCan=window.SoproM15.can;window.SoproM15.can=()=>false;window.SoproCrm.render();});
   await first.waitFor();assert.equal(await page.locator('.crm-patient-menu,[data-crm-ws-agendar],[data-crm-ws-reagendar],[data-crm-ws-wa-pessoa]').count(),0);
   assert.equal(writes.filter(r=>/\/(fiscal|nfse|espirometrias|consultas|laudos|financeiro|followups|crm\/contatos)(\/|$)/.test(r.url)).length,0);
   assert.deepEqual(errors,[]);
   console.log(`PASS ${engine}: teclado, vazio, erro/recuperação, permissão, zero escrita operacional`);
   await browser.close();
  }
  assert.equal(protectedSnapshot(),before,'leitura CRM preserva integralmente dados operacionais e clínicos');
  console.log('RESULTADO: OK — CRM sintético Chromium + Firefox; dados protegidos idênticos');
})().catch(e=>{console.error(e.stack);process.exitCode=1;}).finally(async()=>{for(const b of browsers)await b.close().catch(()=>{});for(const c of children)c.kill('SIGTERM');await sleep(300);fs.rmSync(temp,{recursive:true,force:true});});
