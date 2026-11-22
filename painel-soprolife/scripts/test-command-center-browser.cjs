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
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_engine
from app.models import User,Person,PersonContact,SpirometryExam,AuditLog,Partner,PartnerUnit,PhysicianProfile,ReportDocument,ReportAssignment,FinancialEntry,Lead,Followup,FiscalDocument,EegEnrollment,EegExam
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
 db.add_all([Followup(public_code='FUP-990002',person_id=p.id,tipo='pos_exame',due_date=today),Followup(public_code='FUP-990003',person_id=p.id,tipo='pos_consulta',due_date=today+timedelta(days=2)),Followup(public_code='FUP-990004',person_id=p.id,tipo='lead_sem_atendimento',due_date=None)])
 fiscal=FiscalDocument(spirometry_exam_id=e.id,environment='production',kind='single',state='blocked',created_by=admin.id,idempotency_key='synthetic-fiscal',idempotency_fingerprint='a'*64);db.add(fiscal)
 en=EegEnrollment(person_id=p.id);db.add(en);db.flush();eeg=EegExam(public_code='EEG-990001',enrollment_id=en.id,modalidade_codigo='eeg_rotina',status='agendado',agendado_para=now);db.add(eeg);db.flush()
 ef=FiscalDocument(eeg_exam_id=eeg.id,environment='production',kind='eeg',state='blocked',created_by=admin.id,idempotency_key='synthetic-eeg-fiscal',idempotency_fingerprint='b'*64);db.add(ef)
 db.add(FinancialEntry(public_code='LAN-990002',tipo='receita',categoria='EEG',valor='987.65',eeg_exam_id=eeg.id,data_competencia=today,status='Pendente'))
 for n in range(6):
  recent=SpirometryExam(public_code=f'ESP-99100{n}',person_id=p.id,status='Aguardando');db.add(recent);db.flush()
  db.add(AuditLog(acao='espirometria.criada',entidade='spirometry_exams',entidade_id=recent.id,user_id=admin.id,ts_utc=now-timedelta(minutes=n),detalhes={'public_code':recent.public_code}))
 for action in ('auth.login','sync.finalizada','deploy.concluido'):
  db.add(AuditLog(acao=action,entidade='runtime',user_id=admin.id,ts_utc=now+timedelta(seconds=1),detalhes={}))
 db.commit();print(json.dumps({'person':p.id,'exam':e.id,'assigned':assigned.id,'doc':doc.id,'fiscal':fiscal.id,'partner':partner.id,'unit':unit.id,'lead':lead.id,'eeg':eeg.id,'eegFiscal':ef.id}))`],env));
 for(const [bin,args,cwd,e] of [[python,['-m','app.serve'],core,env],['python3',[path.join(root,'painel-soprolife/scripts/command-center-local-server.py')],root,{...env,SOPROLIFE_PANEL_HOST:'127.0.0.1',SOPROLIFE_PANEL_PORT:String(panelPort),SOPROLIFE_M15_UPSTREAM:`http://127.0.0.1:${apiPort}/api/v1`}]] )children.push(cp.spawn(bin,args,{cwd,env:e,stdio:'ignore'}));
 const base=`http://127.0.0.1:${panelPort}/painel-soprolife/`;await wait(async()=>(await fetch(base+'api/m15/health')).ok);
 const widths=(process.env.CC2_WIDTHS||'1440,1024,800,390').split(',').map(Number);
 const engines=process.env.CC2_ENGINE==='chromium'?[[chromium,'chromium']]:[[chromium,'chromium'],[firefox,'firefox']];
 for(const [engine,name] of engines){browser=await engine.launch({headless:true});
 for(const width of widths){
  const context=await browser.newContext({locale:'pt-BR',viewport:{width,height:900}}),page=await context.newPage(),errors=[],requests=[],searchRequests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{requests.push(r.url());if(r.url().endsWith('/comando/busca'))searchRequests.push(r);});
  await context.addInitScript(observeAuthFrames);
  await context.addInitScript(()=>{window.__expectedClinical=sessionStorage.getItem('testClinical')==='yes';});
  let holdSummary=false,releaseSummary;
  const held=new Promise(r=>releaseSummary=r);
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(holdSummary&&u.pathname.endsWith('/comando/resumo'))await held;
   if(/\/(auth\/me|auth\/sessao|m15-config\.json)$/.test(u.pathname))await new Promise(r=>setTimeout(r,300));
   await route.continue();
  });
  async function ready(role){
   await page.waitForFunction(()=>document.body?.dataset.authPublished==='true');
   await page.waitForFunction(()=>window.authEvidence.frames>=5);
   const proof=await page.evaluate(()=>window.authEvidence);
   assert.equal(proof.loadingNodes,0);assert.equal(proof.forbiddenNodes,0);assert.equal(proof.menuSets.length,1);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false,name+' '+width+' overflow');
   assert.equal(await page.evaluate(()=>window.SoproM15.can('admin')),role==='admin');
  }
  async function login(role,hold=false){
   await page.goto(base);await page.evaluate(v=>sessionStorage.setItem('testClinical',v),role==='medico'?'yes':'no');
   await page.locator('#loginForm [name=email]').fill((role==='admin'?`${name}-${width}-admin`:role)+'@command.local');await page.locator('#loginForm [name=password]').fill(password);
   const start=requests.length;holdSummary=hold;
   await page.locator('#loginForm button[type=submit]').click();
   await page.waitForFunction(()=>document.body?.dataset.authState==='loading');
   assert.equal(await page.locator('.nav-item,.section').count(),0);
   if(hold){
    await page.waitForFunction(()=>window.SoproM15&&document.querySelector('#ccHome')?.dataset.state==='loading');
    assert.equal(await page.locator('#ccHome .cc-stat strong').count(),0,'No placeholder zeros');
    assert(await page.locator('#authShell').isVisible());holdSummary=false;releaseSummary();
   }
   await ready(role);return requests.slice(start);
  }
  async function query(q){
   const response=page.waitForResponse(r=>r.url().endsWith('/comando/busca')&&r.request().postDataJSON()?.termo===q);
   await page.locator('#ccGlobalSearch').fill(q);
   await response;
   await page.waitForFunction(()=>['ready','empty','error'].includes(document.querySelector('#ccSearchResults')?.dataset.state));
   await page.locator('#ccSearchResults:not([hidden])').waitFor();
  }
  const initial=await login('admin',true);
  assert.equal(await page.locator('#ccHome[data-state=ready]').count(),1);
  assert.equal(await page.locator('#ccHome [data-cc-financial],#ccLegacy,#overview canvas').count(),0);
  assert.deepEqual(await page.locator('#ccHome .cc-operation h3').allTextContents(),['Hoje','Precisa de atenção','Próximos']);
  const canonical=await page.evaluate(()=>window.SoproM15.api('/comando/pendencias'));
  assert.equal(await page.locator('#ccPendingToggle').textContent(),'Pendências ('+canonical.total+')');
  await page.locator('#ccHome [data-cc-recent][data-state=ready]').waitFor();
  assert.equal(await page.locator('#ccHome [data-cc-recent-item]').count(),5,'At most five business events');
  const activity=await page.locator('#ccHome .cc-recent').innerText();
  assert(activity.includes('ESP-991000')&&!activity.includes('ESP-991005'));
  assert(!/auth|login|sync|deploy|runtime|PostgreSQL/i.test(activity),'No technical activity');
  const audits=requests.filter(u=>u.includes('/auditoria?'));
  assert.equal(audits.length,3,'Only three bounded business sources');
  for(const url of audits){const q=new URL(url).searchParams;assert.equal(q.get('tamanho'),'5');assert(q.get('acao')&&q.get('entidade'));}

  for(const window of ['hoje','atencao','proximos']){
   const button=page.locator('#ccHome [data-cc-category="followup"][data-cc-window="'+window+'"]');
   assert.equal(await button.locator('strong').textContent(),'1');
   await button.click();await page.locator('#ccPendingPanel[data-state=ready]').waitFor();
   assert.equal(await page.locator('#ccPendingPanel [data-cc-pending-item]').count(),1);
   await page.locator('[data-cc-close-pending]').click();
  }
  if(width>=1024){
   assert(await page.locator('#ccHome .cc-operation-grid').evaluate(e=>e.getBoundingClientRect().bottom<=innerHeight),'Essential home fits first desktop viewport');
  }
  const timing=await page.evaluate(()=>({projection:performance.getEntriesByType('resource').filter(r=>r.name.includes('/comando/resumo')).map(r=>Math.round(r.duration)),scripts:performance.getEntriesByType('resource').filter(r=>r.initiatorType==='script').length}));
  console.log(name+' '+width+' home performance '+JSON.stringify(timing));
  await page.locator('#ccHome [data-cc-new="atendimento"]').focus();
  await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.dataset.ccNew),'contato');
  if(width===1440){
   await page.evaluate(()=>document.documentElement.style.zoom='2');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false,'Home zoom 200%');
   await page.evaluate(()=>document.documentElement.style.zoom='');
  }
  assert(!initial.some(u=>/\/(app|central-cadastros|fiscal-command-center|profit-panel|funnel-panel|eeg-operacao|report-workflow)\.js/.test(u)),'Initial admin assets are lazy');
  assert.equal(initial.filter(u=>u.includes('/comando/resumo')).length,1,'Single home projection');
  assert(!(await page.locator('#ccHome').innerText()).includes('987,65'),'SoproLife excludes EEG money');
  if(process.env.CC2_SCREENSHOT_DIR){fs.mkdirSync(process.env.CC2_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.CC2_SCREENSHOT_DIR,`${name}-${width}-admin.png`)});}
  if(width===1440){
   const recentNow=Date.now();
   await page.route('**/auditoria?*',r=>{
    const action=new URL(r.request().url()).searchParams.get('acao');
    const row=(id,entidade,entidade_id,minutes,detalhes)=>({id,acao:action,entidade,entidade_id,ts_utc:new Date(recentNow+minutes*60000).toISOString(),detalhes});
    const itens=action==='espirometria.criada'?Array.from({length:5},(_,n)=>row(1000+n,'spirometry_exams',ids.exam,-n,{public_code:'ESP-990001'})):
      action==='laudo_assinado_e_liberado'?[row(9000,'report_documents',ids.doc,2,{})]:
      [row(9001,'partner_share_movements',ids.exam,1,{exam_code:'ESP-990001',tipo:'acerto'})];
    return r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({itens})});
   });
   await page.evaluate(()=>window.SoproCommandCenter.refresh());
   await page.locator('#ccHome [data-cc-recent][data-state=ready]').waitFor();
   const merged=await page.locator('#ccHome [data-cc-recent-item]').allTextContents();
   assert.equal(merged.length,5,'Merge three business sources before applying the five-item limit');
   assert(merged[0].includes('Laudo liberado')&&merged[1].includes('Acerto de parceiro registrado'),'Newest events across sources come first');
   await page.locator('#ccHome [data-cc-recent-item="1"]').click();
   await page.waitForFunction(()=>document.querySelector('#financeiro')?.classList.contains('active'));
   await page.evaluate(()=>window.SoproBootGate.navegar('overview'));
   await page.locator('#ccHome [data-cc-recent][data-state=ready]').waitFor();
   const openedRecent=page.waitForResponse(r=>r.url().endsWith('/laudos/'+ids.doc));
   await page.locator('#ccHome [data-cc-recent-item="0"]').click();
   assert.equal((await openedRecent).status(),200,'Recent report uses the secure existing record route');
   await page.keyboard.press('Escape');
   await page.evaluate(()=>window.SoproBootGate.navegar('overview'));
   await page.locator('#ccHome [data-cc-recent][data-state=ready]').waitFor();
   await page.unroute('**/auditoria?*');
   // An incorrectly filtered response must still never expose technical events.
   await page.route('**/auditoria?*',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({itens:[{id:999,acao:'auth.login',entidade:'runtime',entidade_id:ids.exam,ts_utc:new Date().toISOString(),detalhes:{public_code:'ESP-990001'}}]})}));
   await page.evaluate(()=>window.SoproCommandCenter.refresh());
   await page.locator('#ccHome [data-cc-recent][data-state=empty]').waitFor({state:'attached'});
   assert.equal(await page.locator('#ccHome .cc-recent').count(),0,'No placeholder for an empty business feed');
   assert.equal(await page.locator('#ccPendingToggle').textContent(),'Pendências ('+canonical.total+')');
   await page.unroute('**/auditoria?*');
   await page.route('**/auditoria?*',r=>r.fulfill({status:503,body:'{}',contentType:'application/json'}));
   await page.evaluate(()=>window.SoproCommandCenter.refresh());
   await page.locator('#ccHome [data-cc-recent][data-state=unavailable]').waitFor({state:'attached'});
   assert.equal(await page.locator('#ccHome .cc-recent').count(),0,'Unavailable secondary feed leaves home ready');
   assert.equal(await page.locator('#ccHome[data-state=ready]').count(),1);
   await page.unroute('**/auditoria?*');
   await page.evaluate(()=>window.SoproCommandCenter.refresh());
   await page.locator('#ccHome [data-cc-recent][data-state=ready]').waitFor();
  }
  const recentResolved=page.waitForResponse(r=>r.url().endsWith('/comando/registro')&&r.request().postDataJSON()?.tipo==='exame');
  const recentLocated=page.waitForResponse(r=>{const u=new URL(r.url());return u.pathname.endsWith('/laudos/exames')&&u.searchParams.get('q')==='ESP-991000';});
  await page.locator('#ccHome [data-cc-recent-item]').first().click();
  const recentResponse=await recentResolved;assert.equal(recentResponse.status(),200);
  assert.equal((await recentResponse.json()).destino.codigo,'ESP-991000');
  assert.equal((await recentLocated).status(),200,'Recent attendance resolves and locates the exact existing record');
  await page.waitForFunction(()=>document.querySelector('#laudos-espirometria')?.classList.contains('active'));
  // Reset the module state before the independent existing search/navigation scenarios.
  await page.goto(base);await ready('admin');
  await page.evaluate(()=>window.SoproCommandCenter.pending('hoje'));await page.locator('#ccPendingPanel[data-state=ready]').waitFor();assert.equal(await page.locator('#ccPendingPanel [data-cc-pending-item]').count(),3);await page.locator('[data-cc-close-pending]').click();
  const initialSearch=searchRequests.length;
  await page.locator('#ccGlobalSearch').fill('Mac');await page.locator('#ccGlobalSearch').fill('Macli');await page.locator('#ccGlobalSearch').fill('Maclin');
  await page.locator('#ccSearchResults [data-cc-search-result]').first().waitFor();
  assert.equal(searchRequests.length-initialSearch,1,'Debounce');
  assert.equal(JSON.parse(searchRequests.at(-1).postData()).termo,'Maclin');
  assert(!new URL(searchRequests.at(-1).url()).search,'Search term never in URL');
  await page.locator('#ccGlobalSearch').press('ArrowDown');await page.keyboard.press('Enter');
  await page.locator('#ccSelectedRecord[data-record-id="'+ids.partner+'"]').waitFor();
  await page.evaluate(()=>{renderCharts();renderOverviewExtraCharts();if(state.charts.monthly||state.charts.funnel||state.charts.leadsEtapa||state.charts.crmEtapa)throw Error('Removed home charts recreated');});
  await query('Norte');await page.locator('#ccSearchResults button').first().click();await page.locator('#ccSelectedRecord[data-record-id="'+ids.unit+'"]').waitFor();
  await query('ESP-990001');await page.locator('#ccSearchResults button').filter({hasText:'ESP-990001'}).first().click();
  await page.waitForFunction(()=>document.querySelector('#laudos-espirometria')?.classList.contains('active'));
  await page.waitForFunction(()=>document.getElementById('reportExamCode')?.value==='ESP-990001');
  await query('Paciente Sintético');await page.locator('#ccSearchResults button').filter({hasText:'PES-990001'}).first().click();
  await page.locator('.crm-ws-modal:visible, dialog[open]').first().waitFor();
  await page.keyboard.press('Escape');await page.evaluate(()=>document.querySelectorAll('dialog[open]').forEach(d=>{d.close();d.remove();}));
  await page.evaluate(()=>document.querySelectorAll('.crm-ws-overlay,.crm-ws-modal-backdrop').forEach(n=>n.remove()));
  await page.evaluate(()=>window.SoproCommandCenter.pending('fiscal'));
  await page.locator('#ccPendingPanel[data-state=ready]').waitFor();
  await page.locator('#ccPendingPanel [data-cc-pending-item]').first().click();await page.locator('#ccSelectedRecord[data-record-id="'+ids.fiscal+'"]').waitFor();
  await page.evaluate(id=>window.SoproCommandCenter.openTarget({tipo:'contato',id}),ids.lead);
  await page.locator('dialog[open]').waitFor();await page.keyboard.press('Escape');await page.evaluate(()=>document.querySelectorAll('dialog[open]').forEach(d=>{d.close();d.remove();}));
  await page.locator('#funnelNew').click();await page.locator('#centralRoot form').waitFor();await page.keyboard.press('Escape');
  // Header refresh and route restoration, without putting search terms in URLs.
  await page.goto(base+'#cc/soprolife/parceiro/'+ids.partner);await ready('admin');await page.locator('#ccSelectedRecord[data-record-id="'+ids.partner+'"]').waitFor();
  await page.evaluate(()=>window.LifeCommandCenter.setContext('eeglife'));
  await page.waitForFunction(()=>window.LifeCommandCenter.getContext()==='eeglife');
  await page.locator('[data-eeg-section=visao][data-state=ready]').waitFor();
  const eegText=await page.locator('[data-eeg-section=visao]').innerText();assert(!eegText.includes('987,65')&&!eegText.includes('123,45'));assert.deepEqual(await page.locator('[data-eeg-section=visao] .cc-operation h3').allTextContents(),['Hoje','Precisa de atenção','Próximos']);
  if(process.env.CC2_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.CC2_SCREENSHOT_DIR,`${name}-${width}-eeg.png`)});
  await query(ids.eegFiscal);await page.locator('#ccSearchResults button').first().click();await page.locator('[data-eeg-section=fiscal] #ccSelectedRecord[data-record-id="'+ids.eegFiscal+'"]').waitFor();
  await query('ESP-990001');assert.equal(await page.locator('#ccSearchResults button').count(),0);
  await page.reload();await ready('admin');assert.equal(await page.evaluate(()=>window.LifeCommandCenter.getContext()),'eeglife');
  await page.evaluate(()=>window.LifeCommandCenter.setContext('soprolife'));
  await page.evaluate(()=>window.SoproM15.logout());await page.locator('#loginForm').waitFor();
  const doctorRequests=await login('medico');
  assert(!doctorRequests.some(u=>/\/(app|central-cadastros|fiscal-command-center|profit-panel|funnel-panel|eeg-operacao|partner-shares|pastore-settlement)\.js/.test(u)),'Doctor downloads no administrative modules');
  assert.equal(await page.locator('#overview,#financeiro,#fiscal,#central-cadastros').count(),0);
  assert.equal(await page.locator('#ccPendingToggle').textContent(),'Pendências (1)');
  await query('ESP-990001');assert.equal(await page.locator('#ccSearchResults button').count(),0,'Same patient does not widen exam assignment');
  await query('ESP-990003');const opened=page.waitForResponse(r=>r.url().endsWith('/laudos/'+ids.doc));await page.locator('#ccSearchResults button').first().click();
  assert.equal((await opened).status(),200);
  await page.goto(base+'#cc/soprolife/fiscal/'+ids.fiscal);await ready('medico');assert.equal(await page.locator('#fiscal').count(),0);
  if(process.env.CC2_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.CC2_SCREENSHOT_DIR,`${name}-${width}-medico.png`)});
  const denied=await page.evaluate(async id=>{try{await window.SoproM15.api('/comando/registro',{method:'POST',body:JSON.stringify({marca:'soprolife',tipo:'fiscal',id})});return 200;}catch(e){return e.status;}},ids.fiscal);
  assert([403,404].includes(denied));
  await page.evaluate(()=>window.SoproM15.logout());await page.locator('#loginForm').waitFor();
  const operationalRequests=await login('operacional');
  assert(!operationalRequests.some(u=>u.includes('/auditoria')),'Operational role never requests gestor audit');
  assert.equal(await page.locator('#ccHome .cc-recent').count(),0);
  const auditDenied=await page.evaluate(async()=>{try{await window.SoproM15.api('/auditoria?tamanho=5&acao=espirometria.criada&entidade=spirometry_exams');return 200;}catch(e){return e.status;}});
  assert.equal(auditDenied,403,'Server preserves the gestor restriction');
  const payload=await page.evaluate(()=>window.SoproM15.api('/comando/resumo'));
  assert(!Object.hasOwn(payload,'financeiro'));
  assert.equal(await page.locator('#ccHome [data-cc-financial]').count(),0);
  await query('ESP-990001');assert(!(await page.locator('#ccSearchResults').innerText()).includes('NFS-e'));
  const empty={...payload,hoje:{exames:0},pendencias:{...payload.pendencias,total:0,itens:[],categorias:{},grupos:{hoje:{},atencao:{},proximos:{}},indisponiveis:[],completo:true}};
  await page.route('**/comando/resumo*',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify(empty)}));
  await page.evaluate(()=>window.SoproCommandCenter.refresh());
  assert.equal(await page.locator('#ccHome .cc-stat').count(),0,'Zero categories render no row');
  assert.equal(await page.locator('#ccHome .cc-empty').count(),3,'Empty home explains three queues');
  for(const block of await page.locator('#ccHome .cc-operation').all())assert(await block.evaluate(e=>e.getBoundingClientRect().height<160),'Empty block stays compact');
  assert.equal(await page.locator('#ccPendingToggle').textContent(),'Pendências (0)');
  await page.unroute('**/comando/resumo*');
  const mixed={...payload,hoje:{exames:0}};
  await page.route('**/comando/resumo*',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify(mixed)}));
  await page.evaluate(()=>window.SoproCommandCenter.refresh());
  assert.equal(await page.locator('#ccHome [data-cc-category="hoje"]').count(),0);
  await page.locator('#ccHome [data-cc-category="followup"][data-cc-window="hoje"]').click();
  await page.locator('#ccPendingPanel[data-state=ready]').waitFor();
  assert.equal(await page.locator('#ccPendingPanel [data-cc-pending-item]').count(),1);
  await page.locator('[data-cc-close-pending]').click();
  assert.equal(await page.locator('#ccPendingToggle').textContent(),'Pendências ('+payload.pendencias.total+')','Presentation never changes canonical total');
  await page.unroute('**/comando/resumo*');
  await page.route('**/comando/resumo*',r=>r.fulfill({status:503,contentType:'application/json',body:'{}'}));
  await page.reload();await ready('operacional');assert.equal(await page.locator('#ccHome[data-state=error]').count(),1);assert.equal(await page.locator('#ccHome .cc-stat strong').count(),0);
  assert.deepEqual(errors,[],name+' '+width+' runtime errors');
  if(process.env.CC2_SCREENSHOT_DIR){fs.mkdirSync(process.env.CC2_SCREENSHOT_DIR,{recursive:true});await page.unroute('**/comando/resumo*');await page.evaluate(()=>window.SoproCommandCenter.refresh());await page.screenshot({path:path.join(process.env.CC2_SCREENSHOT_DIR,`${name}-${width}-operacional.png`)});}
  console.log(`${name} ${width}: admin/doctor/operational, deny first, loading/error/empty, search RBAC/debounce, direct records, pending, refresh/logout, brands PASS`);
  await context.close();
 }
 await browser.close();browser=null;
 }
 console.log('Command Center 2.0 browser suite PASS');
}finally{if(browser)await browser.close();for(const c of children)c.kill();fs.rmSync(temp,{recursive:true,force:true});}})().catch(e=>{console.error(e);process.exitCode=1;});
