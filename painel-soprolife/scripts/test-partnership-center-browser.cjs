/* Real browsers + local API/proxy + isolated synthetic DB. No production writes. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),cp=require('node:child_process'),net=require('node:net');
const {chromium,firefox}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {observeAuthFrames}=require('./test-auth-bootstrap-browser.cjs');
const root=path.resolve(__dirname,'../..'),core=path.join(root,'painel-soprolife/nucleo-m15'),python=process.env.M15_TEST_PYTHON;
assert(python,'M15_TEST_PYTHON required');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'soprolife-command-browser-')),children=[];
const password='command-synthetic-password-123';
async function port(){return new Promise(r=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});}
async function wait(f){for(let i=0;i<150;i++){try{if(await f())return;}catch{}await new Promise(r=>setTimeout(r,100));}throw Error('Local service timeout');}
function run(args,env){const p=cp.spawnSync(python,args,{cwd:core,env,encoding:'utf8'});assert.equal(p.status,0,p.stderr.slice(-2000));return p.stdout;}
(async()=>{let browser;try{
 const apiPort=await port(),panelPort=await port();
 const env={...process.env,M15_ENV:'dev',M15_DATABASE_URL:'sqlite:///'+path.join(temp,'test.db'),M15_API_HOST:'127.0.0.1',M15_API_PORT:String(apiPort),M15_AUTH_SECRET:'command-browser-synthetic-secret-32-chars',M15_SESSION_COOKIE_PATH:'/painel-soprolife',M15_NOVA_SENHA:password,M15_REPORTS_ENABLED:'true',M15_REPORTS_MODE:'pilot',M15_REPORTS_STORAGE_DIR:path.join(temp,'reports'),M15_NFSE_ENABLED:'true',WHATSAPP_INTEGRATION_ENABLED:'false',WHATSAPP_VERIFY_TOKEN:'',WHATSAPP_APP_SECRET:'',WHATSAPP_WABA_ID:'',WHATSAPP_PHONE_NUMBER_ID:'',WHATSAPP_IDENTITY_KEY:''};
 run(['-m','alembic','upgrade','head'],env);
 for(const role of ['admin','medico','operacional','leitura'])run(['-m','app.cli','criar-usuario','--email',role+'@command.local','--nome','Conta Sintética '+role,'--papel',role],env);
 const ids=JSON.parse(run(['-c',`import json
from decimal import Decimal
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_engine
from app.models import User,Person,PersonContact,SpirometryExam,Partner,PartnerUnit,PhysicianProfile,ReportDocument,ReportAssignment,FinancialEntry,Lead,Followup,FiscalDocument,EegEnrollment,EegExam
with Session(get_engine()) as db:
 admin=db.scalar(select(User).where(User.email=='admin@command.local')); doctor=db.scalar(select(User).where(User.email=='medico@command.local'))

 for engine in ('chromium','firefox'):
  for width in (1440,1024,800,390,720):
   db.add(User(email=f'{engine}-{width}-admin@command.local',nome='Admin Sintético Isolado',password_hash=admin.password_hash,roles=list(admin.roles)))
 now=datetime.now(timezone.utc);today=now.astimezone(ZoneInfo('America/Sao_Paulo')).date()
 p=Person(public_code='PES-990001',nome_completo='Paciente Sintético Comando',nome_normalizado='paciente sintetico comando',cpf='52998224725');p2=Person(public_code='PES-990002',nome_completo='Paciente Restrito Sintético',nome_normalizado='paciente restrito sintetico');db.add_all([p,p2]);db.flush()
 db.add(PersonContact(person_id=p.id,tipo='telefone',valor='(21) 0000-9001',valor_normalizado='552100009001'))
 partner=Partner(public_code='CLI-990001',nome='Maclin Sintética',status='ativa');db.add(partner);db.flush();unit=PartnerUnit(public_code='UNI-990001',partner_id=partner.id,nome='Norte Shopping Sintético');db.add(unit)
 profile=PhysicianProfile(user_id=doctor.id,professional_name='Profissional Sintético',crm_number='999999',crm_state='RJ',active=True,verification_status='verified',verified_at=now,verified_by_user_id=admin.id,verification_reference='SYNTHETIC-ONLY');db.add(profile);db.flush()
 e=SpirometryExam(public_code='ESP-990001',person_id=p.id,status='Realizado',data_exame=today,data_exame_precisao='dia',modalidade='cowork',local_atendimento='Coworking Downtown')
 scheduled=SpirometryExam(public_code='ESP-990002',person_id=p.id,status='Aguardando',data_exame=today+timedelta(days=1),data_exame_precisao='dia',modalidade='cowork')
 assigned=SpirometryExam(public_code='ESP-990003',person_id=p.id,status='Realizado',data_exame=today,data_exame_precisao='dia',modalidade='cowork',local_atendimento='Coworking Downtown')
 hidden=SpirometryExam(public_code='ESP-990004',person_id=p2.id,status='Realizado',data_exame=today,data_exame_precisao='dia',modalidade='cowork');db.add_all([e,scheduled,assigned,hidden]);db.flush()
 doc=ReportDocument(public_code='LAU-990001',spirometry_exam_id=assigned.id,created_by_user_id=admin.id,origin_type='coworking',origin_label='Coworking Downtown');db.add(doc);db.flush();db.add(ReportAssignment(report_document_id=doc.id,physician_profile_id=profile.id,assigned_by_user_id=admin.id,reason_code='initial_assignment'))
 db.add(FinancialEntry(public_code='LAN-990001',tipo='receita',valor='123.45',spirometry_exam_id=e.id,data_competencia=today,data_competencia_precisao='dia',status='Recebido',forma_pagamento='Pix'))
 lead=Lead(public_code='LEA-990001',person_id=p.id,primeiro_contato_em=now,status_funil='NOVO_CONTATO');db.add(lead)
 db.add(Followup(public_code='FUP-990001',person_id=p.id,tipo='manual',due_date=today-timedelta(days=1)))
 fiscal=FiscalDocument(spirometry_exam_id=e.id,environment='production',kind='single',state='blocked',created_by=admin.id,idempotency_key='synthetic-fiscal',idempotency_fingerprint='a'*64);db.add(fiscal)
 en=EegEnrollment(person_id=p.id);db.add(en);db.flush();eeg=EegExam(public_code='EEG-990001',enrollment_id=en.id,modalidade_codigo='eeg_rotina',status='agendado',agendado_para=now);db.add(eeg);db.flush()
 ef=FiscalDocument(eeg_exam_id=eeg.id,environment='production',kind='eeg',state='blocked',created_by=admin.id,idempotency_key='synthetic-eeg-fiscal',idempotency_fingerprint='b'*64);db.add(ef)
 db.add(FinancialEntry(public_code='LAN-990002',tipo='receita',categoria='EEG',valor='987.65',eeg_exam_id=eeg.id,data_competencia=today,status='Pendente'))
 from app.services.partner_share_setup import configure
 from app.services.partner_shares import resolve_rule,create_share
 from app.models import PartnerSettlement,PartnerSettlementItem
 shares={};canonical_ids={}
 for n,code,gross,receiver in [(1,'maclin','123.45','soprolife'),(2,'alergocare','220.00','parceiro')]:
  configure(db,code,today-timedelta(days=30),admin.id)
  rule,cp,cu=resolve_rule(db,code,today);canonical_ids[code]=cp.id
  ce=SpirometryExam(public_code=f'ESP-99100{n}',person_id=p.id,partner_id=cp.id,partner_unit_id=cu.id,modalidade='clinica_parceira',local_atendimento=cu.nome,status='Realizado',data_exame=today,data_exame_precisao='dia',broncodilatador=True);db.add(ce);db.flush()
  cash=FinancialEntry(public_code=f'LAN-99100{n}',tipo='receita',valor=Decimal(gross),spirometry_exam_id=ce.id,status='Recebido',forma_pagamento='Pix',data_competencia=today);db.add(cash);db.flush()
  shares[code]=create_share(db,ce,cash,code,receiver,admin.id,'synthetic-partnership-center').id
 pp=Partner(public_code='CLI-991003',nome='Pastore',status='ativa');db.add(pp);db.flush();pu=PartnerUnit(public_code='UNI-991003',partner_id=pp.id,nome='Pastore Ipanema');db.add(pu);db.flush()
 pe=SpirometryExam(public_code='ESP-991003',person_id=p.id,partner_id=pp.id,partner_unit_id=pu.id,modalidade='clinica_parceira',local_atendimento=pu.nome,status='Realizado',data_exame=today,data_exame_precisao='dia',broncodilatador=True);db.add(pe);db.flush()
 st=PartnerSettlement(partner_id=pp.id,partner_unit_id=pu.id,competencia=today.replace(day=1),periodo_inicio=today.replace(day=1),periodo_fim=today,valor_total='109.50',status='a_receber');db.add(st);db.flush();db.add(PartnerSettlementItem(settlement_id=st.id,spirometry_exam_id=pe.id))
 al=Partner(public_code='CLI-991004',nome='Alergo Life');db.add(al)
 db.commit();print(json.dumps({'person':p.id,'exam':e.id,'assigned':assigned.id,'doc':doc.id,'fiscal':fiscal.id,'partner':partner.id,'unit':unit.id,'lead':lead.id,'eeg':eeg.id,'eegFiscal':ef.id,'canonical':canonical_ids,'shares':shares}))`],env));
 for(const [bin,args,cwd,e] of [[python,['-m','app.serve'],core,env],['python3',[path.join(root,'painel-soprolife/scripts/command-center-local-server.py')],root,{...env,SOPROLIFE_PANEL_HOST:'127.0.0.1',SOPROLIFE_PANEL_PORT:String(panelPort),SOPROLIFE_M15_UPSTREAM:`http://127.0.0.1:${apiPort}/api/v1`}]] )children.push(cp.spawn(bin,args,{cwd,env:e,stdio:'ignore'}));
 const base=`http://127.0.0.1:${panelPort}/painel-soprolife/`;await wait(async()=>(await fetch(base+'api/m15/health')).ok);
 const widths=(process.env.PC_WIDTHS||'1440,800,390').split(',').map(Number);
 const engines=process.env.PC_ENGINE==='chromium'?[[chromium,'chromium']]:[[chromium,'chromium'],[firefox,'firefox']];
 for(const [engine,name] of engines){browser=await engine.launch({headless:true});for(const width of widths){
  const context=await browser.newContext({locale:'pt-BR',viewport:{width,height:1000}}),page=await context.newPage(),errors=[],requests=[];
  await context.addInitScript(observeAuthFrames);await context.addInitScript(()=>{window.__expectedClinical=sessionStorage.getItem('testClinical')==='yes';});
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
  await page.route('**/auth/me',async r=>{await new Promise(resolve=>setTimeout(resolve,350));await r.continue();});
  async function ready(){await page.waitForFunction(()=>document.body?.dataset.authPublished==='true');await page.waitForFunction(()=>window.authEvidence.frames>5);const proof=await page.evaluate(()=>window.authEvidence);assert.equal(proof.loadingNodes,0);assert.equal(proof.forbiddenNodes,0);assert.equal(proof.menuSets.length,1);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false);}
  async function login(role){await page.goto(base);await page.evaluate(v=>sessionStorage.setItem('testClinical',v),role==='medico'?'yes':'no');await page.locator('[name=email]').fill(role+'@command.local');await page.locator('[name=password]').fill(password);await page.locator('#loginForm button[type=submit]').click();await ready();}
  async function clickNav(){const nav=page.locator('.nav-item[data-section=parcerias]');if(!await nav.isVisible())await page.locator('#soproMobileMenu>summary').click();await nav.click();}
  async function landing(){await clickNav();await page.locator('#partnershipCenterRoot[data-state=ready]').waitFor();}
  async function search(q){const response=page.waitForResponse(r=>r.url().endsWith('/comando/busca'));await page.locator('#ccGlobalSearch').fill(q);assert.equal((await response).status(),200);await page.locator('#ccSearchResults[data-state=ready]').waitFor();}
  function card(code){return page.locator('[data-partnership-card='+code+']');}
  await login('admin');assert(!requests.some(u=>u.includes('/partnership-center.js')),'Lazy module');
  let release;const hold=new Promise(r=>release=r);await page.route('**/parcerias/central?*',async r=>{await hold;await r.continue();});
  await clickNav();await page.locator('#partnershipCenterRoot[data-state=loading]').waitFor();assert.equal(await page.locator('#partnershipCenterRoot dd').count(),0);release();await page.locator('#partnershipCenterRoot[data-state=ready]').waitFor();await page.unroute('**/parcerias/central?*');
  assert.equal(await page.locator('[data-partnership-card]').count(),3);assert.equal(await page.locator('#parcerias-pastore.active').count(),0);
  assert(!(await page.locator('#partnershipCenterRoot').innerText()).includes('Alergo Life'));
  assert((await card('maclin').innerText()).includes('123,45'));assert((await card('alergocare').innerText()).includes('220,00'));
  assert((await card('pastore').innerText()).includes('109,50'));
  await page.locator('.pc-filters select').selectOption('maclin');await page.locator('.pc-filters button[type=submit]').click();await page.locator('[data-partnership-card=maclin]').waitFor();assert.equal(await page.locator('[data-partnership-card]').count(),1);
  await card('maclin').locator('button').focus();await page.keyboard.press('Enter');await page.locator('[data-partnership-detail=maclin]').waitFor();
  const text=await page.locator('[data-partnership-detail=maclin]').innerText();assert(text.includes('70.00% SoproLife / 30.00% Maclin')&&text.includes('DIRECT')&&text.includes('ESP-991001'));
  await page.locator('[data-pc-share]').click();await page.locator('[data-share-record="'+ids.shares.maclin+'"]').waitFor();
  await landing();await page.locator('.pc-filters select').selectOption('todas');await page.locator('.pc-filters button[type=submit]').click();await card('pastore').waitFor();
  await card('pastore').locator('button').click();await page.locator('#parcerias-pastore.active').waitFor();await page.locator('#pastoreSettlementRoot .pastore-settlement-panel').waitFor();
  assert.equal(await page.locator('[data-parceria-tab]').count(),3);await page.locator('[data-parceria-tab=financeiro]').click();assert(await page.locator('#parceria-financeiro').isVisible());
  await page.locator('[data-partnership-back]').click();await page.locator('#parcerias.active').waitFor();await page.locator('#partnershipCenterRoot[data-state=ready]').waitFor();
  await search('Alergocare');await page.locator('[data-cc-partnership]').click();await page.locator('[data-partnership-detail=alergocare]').waitFor();
  await page.reload();await ready();await page.locator('[data-partnership-detail=alergocare]').waitFor();
  await search('Alergocare');await page.locator('#ccSearchResults button').first().click();await page.locator('#ccSelectedRecord[data-record-id="'+ids.canonical.alergocare+'"]').waitFor();
  await landing();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false);
  if(process.env.PC_SCREENSHOTS){fs.mkdirSync(process.env.PC_SCREENSHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.PC_SCREENSHOTS,`${name}-${width}-admin.png`),fullPage:true});}
  await page.route('**/parcerias/central?*',r=>r.fulfill({status:503,contentType:'application/json',body:'{}'}));await clickNav();
  // Wait on the error state directly, rather than presenting provisional totals.
  await page.locator('#partnershipCenterRoot[data-state=error]').waitFor();assert.equal(await page.locator('#partnershipCenterRoot dd').count(),0);await page.unroute('**/parcerias/central?*');
  await page.evaluate(()=>window.SoproM15.logout());await page.locator('#loginForm').waitFor();await login('operacional');await landing();assert.equal(await page.locator('.pc-metric dt').filter({hasText:'Receita bruta'}).count(),0);
  const noMoney=await page.evaluate(()=>window.SoproM15.api('/parcerias/central'));assert(!JSON.stringify(noMoney).includes('financeiro":{'));
  await page.evaluate(()=>window.SoproM15.logout());await page.locator('#loginForm').waitFor();await login('medico');assert.equal(await page.locator('#parcerias,#parcerias-pastore').count(),0);assert(!await page.evaluate(()=>performance.getEntriesByType('resource').some(r=>r.name.includes('/partnership-center.js'))));
  const denied=await page.evaluate(async()=>{try{await window.SoproM15.api('/parcerias/central');return 200;}catch(e){return e.status;}});assert.equal(denied,403);
  assert.deepEqual(errors,[],name+' '+width+' runtime');console.log(`${name} ${width}: multi-partner cards/filter/details, canonical money, Pastore, search links/refresh, loading/error, RBAC/deny-default PASS`);
  await context.close();
 }await browser.close();browser=null;}
 console.log('Partnership Center browser suite PASS');
}finally{if(browser)await browser.close();for(const c of children)c.kill();fs.rmSync(temp,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exitCode=1;});
