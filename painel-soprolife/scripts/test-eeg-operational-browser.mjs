#!/usr/bin/env node
// API + proxy reais; SQLite, PDFs e contas sintéticos em diretório descartável.
// M15_TEST_PYTHON e M15_TEST_PLAYWRIGHT apontam para dependências já instaladas.
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:net';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const core = join(root, 'painel-soprolife/nucleo-m15');
const python = process.env.M15_TEST_PYTHON;
assert.ok(python && process.env.M15_TEST_PLAYWRIGHT, 'Defina Python M15 e módulo Playwright instalado.');
const { chromium, firefox } = await import(process.env.M15_TEST_PLAYWRIGHT);
async function port() { const s = createServer(); await new Promise(r => s.listen(0, '127.0.0.1', r)); const p = s.address().port; await new Promise(r => s.close(r)); return p; }
const [apiPort, panelPort, portalPort] = await Promise.all([port(), port(), port()]);
const dir = mkdtempSync(join(tmpdir(), 'fase2a-eeg-browser-'));
mkdirSync(join(dir, 'reports'), { mode: 0o700 });
const env = { ...process.env, M15_ENV: 'dev', M15_DATABASE_URL: `sqlite:///${dir}/test.db`,
  M15_NFSE_ENABLED:'true', M15_API_PORT: String(apiPort), M15_API_HOST: '127.0.0.1',
  M15_AUTH_SECRET: 'fase2a-browser-synthetic-auth-0123456789abcdef',
  M15_REPORTS_STORAGE_DIR: join(dir, 'reports'), M15_PORTAL_ENABLED: 'true',
  M15_PORTAL_PUBLIC_BASE_URL: 'https://site.test/resultados',
  M15_PORTAL_EEG_PUBLIC_BASE_URL: `https://portal.test/p/v1/eeg`,
  M15_PORTAL_TOKEN_KEY: 'fase2a-browser-synthetic-token-0123456789abcdef',
  M15_PORTAL_SESSION_SECRET: 'fase2a-browser-synthetic-session-fedcba9876543210',
  M15_PORTAL_COOKIE_SECURE: 'false', M15_PORTAL_API_PORT: String(portalPort),
};
const children = [];
function run(args, input) { return execFileSync(python, args, { cwd: core, env, input, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }); }
function start(args, extra={}) { const c = spawn(python, args, { cwd: core, env: {...env, ...extra}, stdio: ['ignore', 'pipe', 'pipe'] }); let log=''; c.stderr.on('data', d => {log += d;}); c.log = () => log.slice(-2500); children.push(c); return c; }
async function wait(url) { for(let i=0;i<100;i++) { try { if((await fetch(url)).ok) return; } catch{} await new Promise(r=>setTimeout(r,100)); } throw Error('Servidor não iniciou: '+children.map(c=>c.log()).join('\n')); }
let browser, page;
const evidence=process.env.EEG_EVIDENCE_DIR || dir;
mkdirSync(evidence,{recursive:true,mode:0o700});
try {
  run(['-m','alembic','upgrade','head']);
  run(['-c', `from app.db import get_engine
from sqlalchemy.orm import Session
from app.models import User,EegProfessional
from app.security import ensure_roles_exist,get_role,hash_password
from pypdf import PdfWriter
with Session(get_engine()) as db:
 ensure_roles_exist(db)
 for role in ('admin','operacional','medico','eeg_operador','eeg_tecnico','medical_eeg','eeg_financeiro'):
  u=User(email=role+'@fase2a.test',nome='Sintetico '+role,password_hash=hash_password('synthetic-password-123'));u.roles.append(get_role(db,role));db.add(u)
  db.flush()
  if role=='admin': admin_id=u.id
  if role=='medical_eeg': med_id=u.id
 db.add(EegProfessional(user_id=med_id,nome='Neurologista Browser Sintetico',crm='900003',uf='RJ',tipo='interno',referencia_verificacao='SYNTH-CRM-003',verificado_por=admin_id))
 db.commit()
w=PdfWriter();w.add_blank_page(width=595,height=842);w.write(${JSON.stringify(join(dir,'assinado.pdf'))})`]);
  start(['-m','app.serve']);start(['-m','app.portal_serve']);
  start([join(root,'painel-soprolife/scripts/command-center-local-server.py')],{SOPROLIFE_PANEL_HOST:'127.0.0.1',SOPROLIFE_PANEL_PORT:String(panelPort),SOPROLIFE_M15_UPSTREAM:`http://127.0.0.1:${apiPort}/api/v1`});
  const base=`http://127.0.0.1:${panelPort}/painel-soprolife`;
  await wait(base+'/api/m15/health');await wait(`http://127.0.0.1:${portalPort}/p/v1/health`);
  const results=[];
  for(const [engine,name] of [[chromium,'chromium'],[firefox,'firefox']]) {
    browser=await engine.launch({headless:true});
    page=await browser.newPage({locale:'pt-BR',timezoneId:'America/Sao_Paulo',viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    async function login(p,role){await p.goto(base+'/');await p.locator('#email').fill(role+'@fase2a.test');await p.locator('#password').fill('synthetic-password-123');await p.locator('#loginForm').evaluate(f=>f.requestSubmit());await p.waitForFunction(()=>window.SoproM15?.hasSession());await p.waitForFunction(()=>window.SoproBootGate?.identidade);}
    async function module(id,p=page){var menu=p.locator('#eegMobileMenu');if(await menu.count() && !await menu.evaluate(d=>d.open)){if(await menu.locator('summary').isVisible())await menu.locator('summary').click();else await p.waitForFunction(()=>document.getElementById('eegMobileMenu').open);}await p.locator('button[data-eeg-modulo="'+id+'"]').click();await p.locator('[data-eeg-section="'+id+'"] [data-eeg-corpo]').first().waitFor();await p.waitForFunction(id=>Array.from(document.querySelectorAll('[data-eeg-section="'+id+'"] [data-eeg-corpo]')).every(el=>!el.textContent.includes('Carregando…')),id);}
    async function submit(label='Salvar',p=page){await p.getByRole('dialog').last().getByRole('button',{name:label,exact:true}).click();}
    async function closed(p=page){await p.waitForFunction(()=>!document.querySelector('dialog'));}
    async function openExam(p=page,id='exames'){await module(id,p);await p.locator('[data-eeg-section="'+id+'"] [data-eeg-record]').filter({hasText:'Paciente Browser Completo '+name}).getByRole('button',{name:'Abrir exame',exact:true}).click();}
    await login(page,'admin');
    await page.evaluate(()=>window.SoproCentral.open('atendimento',{tipo:'espirometria_soprolife'}));
    const sopro=page.locator('#cadFormAtend');
    await sopro.locator('[name="esp_equipamento"]').waitFor();
    assert.equal(await sopro.locator('[name="esp_equipamento"]').inputValue(),'');
    assert.equal(await sopro.locator('[name="modalidade_codigo"]').count(),0);
    await sopro.locator('[name="cadAtP_nome"]').fill('Pessoa MIR EEG Browser '+name);
    await sopro.locator('[name="esp_data"]').locator('..').locator('.m15-date-input').fill('20/10/2026');
    await sopro.locator('[name="esp_equipamento"]').selectOption('MIR_SPIROBANK_SMART_II');
    await sopro.locator('[name="esp_modalidade"]').selectOption('cowork');
    await sopro.locator('[name="esp_local"]').fill('Unidade Sintetica Integracao MIR EEG');
    await sopro.locator('[name="esp_pgto_status"]').selectOption('Pendente');
    for(const width of [390,768,1440]){
      await page.setViewportSize({width,height:1000});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      assert.equal(await sopro.locator('[name="esp_equipamento"]').inputValue(),'MIR_SPIROBANK_SMART_II');
      await page.evaluate(()=>scrollTo(0,0));
      await page.screenshot({path:join(evidence,`${name}-${width}-central-sopro-mir.png`),fullPage:true});
    }
    const savedMIR=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/atendimentos/novo-paciente'));
    await sopro.getByRole('button',{name:'Salvar atendimento',exact:true}).click();
    const mirResponse=await savedMIR;assert.equal(mirResponse.status(),201);const mirSaved=await mirResponse.json();
    assert.equal(mirSaved.espirometria.equipamento_fonte,'MIR_SPIROBANK_SMART_II');
    const corporate=await page.evaluate(async id=>window.SoproM15.api('/pessoas/'+id),mirSaved.espirometria.person_id);
    await page.locator('[data-life-context="eeglife"]').click();
    await module('cadastros');
    const at=page.locator('[data-eeg-atendimento]');
    assert.equal(await page.locator('dialog').count(),0);
    await at.getByLabel('Nome completo',{exact:true}).waitFor({state:'visible'});
    assert.equal(await at.getByLabel('Município onde o exame será/foi realizado',{exact:true}).inputValue(),'3304557');
    assert.equal(await at.getByLabel('Origem',{exact:true}).inputValue(),'direto');
    assert.equal(await at.getByLabel('Status do pagamento',{exact:true}).inputValue(),'Pendente');
    assert.equal(await at.getByLabel('Forma de pagamento',{exact:true}).inputValue(),'');
    for(const code of ['video-eeg','hospitalar-beira-leito','especial'])assert.ok(await at.locator('input[value="'+code+'"]').isDisabled());
    await at.getByLabel('Buscar pessoa existente',{exact:true}).focus();
    const tabbed=new Set();let reachedSave=false;
    for(let i=0;i<80;i++){
      const focused=await at.evaluate(f=>{const a=document.activeElement;return {inside:f.contains(a),name:a.name,type:a.type,text:a.textContent};});
      assert.ok(focused.inside,'O foco saiu do atendimento antes de alcançar Salvar.');
      if(focused.name)tabbed.add(focused.name);
      if(focused.type==='submit'){reachedSave=true;break;}
      await page.keyboard.press('Tab');
    }
    assert.ok(reachedSave);
    for(const field of ['cpf','nome_completo','modalidade_codigo','status_inicial','valor','pagamento_status'])assert.ok(tabbed.has(field),'Campo ausente da ordem de teclado: '+field);

    await at.getByLabel('Buscar pessoa existente',{exact:true}).fill(corporate.public_code);
    await at.getByRole('button',{name:'Buscar',exact:true}).click();
    await at.locator('[data-at-results]').getByRole('button').filter({hasText:corporate.public_code}).click();
    assert.ok(await at.locator('[data-at-new]').isHidden());
    assert.ok((await at.locator('[data-at-selected]').textContent()).includes(corporate.public_code));
    await at.getByRole('button',{name:'Trocar paciente / cadastrar novo',exact:true}).click();
    await at.getByLabel('Nome completo',{exact:true}).fill('Paciente Browser Completo '+name);
    await at.getByLabel('Nascimento (formato DD/MM/AAAA)',{exact:true}).fill('11/04/1991');
    await at.getByLabel('Consentimento operacional WhatsApp · somente EEGLife',{exact:true}).selectOption('revogado');
    await at.locator('input[name="modalidade_codigo"][value="vigilia"]').check();
    await at.getByLabel('Data do exame (formato DD/MM/AAAA)',{exact:true}).fill('20/10/2026');
    await at.getByLabel('Horário',{exact:true}).fill('10:30');
    await at.getByLabel('Local de atendimento',{exact:true}).fill('Local Browser Sintetico');
    await at.getByLabel('Valor do EEG (R$)',{exact:true}).fill('250');
    // Readiness is local guidance; it never calls fiscal preparation/issuance.
    const fiscalRequests=[];page.on('request',r=>{if(r.method()!=='GET'&&r.url().includes('/fiscal'))fiscalRequests.push(r.url());});
    for(const width of [390,768,1440]){
      await page.setViewportSize({width,height:1000});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:join(evidence,`${name}-${width}-central-filled.png`),fullPage:true});
    }
    await at.getByRole('button',{name:'SALVAR ATENDIMENTO EEG',exact:true}).click();
    await at.locator('[data-at-success]').filter({hasText:'Atendimento EEG salvo'}).waitFor();
    assert.deepEqual(fiscalRequests,[]);
    assert.equal(await page.locator('dialog').count(),0);
    const e=await page.evaluate(async name=>(await window.SoproM15.api('/eeg/exames?tamanho=100')).itens.find(e=>e.paciente.nome_completo==='Paciente Browser Completo '+name),name);
    assert.ok(e?.id,JSON.stringify(await page.evaluate(()=>window.SoproM15.api('/eeg/exames?tamanho=100'))));
    await openExam();await page.getByRole('button',{name:'Registrar realização',exact:true}).click();await page.getByRole('dialog').last().getByLabel('Data e hora',{exact:true}).fill('2026-10-20T10:35');await submit('Confirmar');await closed();
    await openExam();await page.getByRole('button',{name:'Conferir NFS-e EEG',exact:true}).click();
    const fiscalDialog=page.getByRole('dialog').last();
    await fiscalDialog.getByText('Eletroencefalograma (EEG) de rotina',{exact:false}).waitFor();
    assert.ok((await fiscalDialog.textContent()).includes('63.544.026/0001-10'));
    assert.ok((await fiscalDialog.textContent()).includes('Classificação fiscal EEG pendente de validação específica'));
    assert.equal(await fiscalDialog.getByRole('button',{name:'Emitir NFS-e EEG',exact:true}).count(),0);
    assert.ok(fiscalRequests.every(url=>url.includes('/eeg/fiscal/exames/')&&url.endsWith('/preparar')));
    await fiscalDialog.getByRole('button',{name:'Fechar',exact:true}).click();
    await page.getByRole('dialog').last().getByRole('button',{name:'Fechar',exact:true}).click();await closed();
    await openExam();await page.getByRole('button',{name:'Importar arquivo do exame',exact:true}).click();
    await page.getByRole('dialog').last().getByLabel('Confirme o código EEG',{exact:true}).fill('EEG-999999');await page.getByRole('dialog').last().getByLabel('Arquivo exportado',{exact:true}).setInputFiles(join(dir,'assinado.pdf'));await submit('Validar e importar');await page.getByRole('dialog').last().getByRole('alert').filter({hasText:'Confirme'}).waitFor();assert.equal(await page.getByRole('dialog').last().getByLabel('Confirme o código EEG',{exact:true}).inputValue(),'EEG-999999');
    await page.getByRole('dialog').last().getByLabel('Confirme o código EEG',{exact:true}).fill(e.public_code);await submit('Validar e importar');await closed();
    await openExam();await page.getByRole('button',{name:'Confirmar conferência técnica',exact:true}).click();await page.getByRole('dialog').last().getByLabel('Confirme o código EEG',{exact:true}).fill(e.public_code);await submit('Confirmar');await closed();
    await openExam();await page.getByRole('button',{name:'Atribuir laudador EEG',exact:true}).click();await submit();await closed();
    const med=await browser.newPage({locale:'pt-BR',timezoneId:'America/Sao_Paulo',viewport:{width:1440,height:1000}});await login(med,'medical_eeg');await med.waitForFunction(()=>document.body.dataset.lifeAtivo==='eeglife');
    await openExam(med,'laudos');assert.equal(await med.getByRole('button',{name:'Registrar realização',exact:true}).count(),0);
    await med.getByRole('button',{name:'Editor de laudo EEG',exact:true}).click();
    await med.getByRole('dialog').last().getByLabel('Técnica',{exact:true}).fill('Técnica sintética fornecida pelo médico.');await med.getByRole('dialog').last().getByLabel('Descrição',{exact:true}).fill('Descrição exclusivamente sintética para validação.');await med.getByRole('dialog').last().getByLabel('Conclusão',{exact:true}).fill('Conclusão sintética do médico.');await submit('Salvar versão',med);await closed(med);
    await openExam(med,'laudos');await med.getByRole('button',{name:'Concluir laudo e gerar PDF final',exact:true}).click();await med.getByRole('dialog').last().getByLabel('Confirme o código EEG',{exact:true}).fill(e.public_code);await submit('Confirmar',med);await closed(med);await med.close();
    await openExam();const download=page.waitForEvent('download');await page.getByRole('button',{name:'Baixar PDF final para assinatura',exact:true}).click();assert.ok((await download).suggestedFilename().endsWith('.pdf'));await page.getByRole('button',{name:'Receber PDF assinado',exact:true}).click();await page.getByRole('dialog').last().getByLabel('Confirme o código EEG',{exact:true}).fill(e.public_code);await page.getByRole('dialog').last().getByLabel('PDF assinado recebido',{exact:true}).setInputFiles(join(dir,'assinado.pdf'));await submit('Registrar recebido assinado');await closed();
    await openExam();await page.getByRole('button',{name:'Liberar resultado para entrega',exact:true}).click();await page.getByRole('dialog').last().getByLabel('Confirme o código EEG',{exact:true}).fill(e.public_code);await submit('Confirmar');await closed();
    await openExam();await page.getByRole('button',{name:'Gerar / exibir acesso do paciente',exact:true}).click();const link=await page.getByRole('dialog').last().getByLabel('Link do resultado').inputValue();await page.getByRole('button',{name:'Fechar',exact:true}).click();
    const patient=await browser.newPage();await patient.goto(`http://127.0.0.1:${portalPort}/p/v1/eeg/`+new URL(link).hash);await patient.getByLabel('Data de nascimento',{exact:true}).fill('11/04/1991');await patient.locator('form').evaluate(f=>f.requestSubmit());await patient.locator('#linkLaudo').waitFor({state:'visible'});assert.equal(await patient.locator('#linkTecnico').count(),0);const pd=patient.waitForEvent('download');await patient.locator('#linkLaudo').click();assert.ok((await pd).suggestedFilename().endsWith('.pdf'));await patient.close();
    await openExam();await page.getByRole('button',{name:'Registrar entrega ao paciente',exact:true}).click();await page.getByRole('dialog').last().getByLabel('Confirme o código EEG',{exact:true}).fill(e.public_code);await submit('Confirmar');await closed();
    for(const width of [390,768,1440]) {
      await page.setViewportSize({width,height:1000});
      for(const id of ['visao','cadastros','crm','leads','tarefas','exames','laudos','marketing','financeiro','fiscal','parceiros','custos','documentos','fontes','lancamentos','nucleo']) {
        await module(id);
        const overflow=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(overflow.scroll<=overflow.width+1,JSON.stringify({engine:name,module:id,...overflow}));
        if(['visao','cadastros','crm','exames','laudos','fiscal'].includes(id))await page.screenshot({path:join(evidence,`${name}-${width}-${id}.png`),fullPage:true});
        results.push({engine:name,width,module:id,overflow:false});
      }
    }
    await page.setViewportSize({width:1440,height:1000});await module('cadastros');await page.evaluate(()=>document.documentElement.style.zoom='2');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.evaluate(()=>document.documentElement.style.zoom='');
    for(const width of [390,768,1440]){
      await page.setViewportSize({width,height:1000});await module('cadastros');
      const f=page.locator('[data-eeg-atendimento]');
      // Keyboard search, selection and correction in the same screen.
      await f.getByLabel('Buscar pessoa existente',{exact:true}).fill(corporate.public_code);
      await f.getByLabel('Buscar pessoa existente',{exact:true}).press('Enter');
      await f.locator('[data-at-results]').getByRole('button').filter({hasText:corporate.public_code}).click();
      await f.locator('input[name="modalidade_codigo"][value="infantil"]').check();
      await f.getByLabel('Data do exame (formato DD/MM/AAAA)',{exact:true}).fill('20/10/2026');
      await f.getByLabel('Horário',{exact:true}).fill('09:30');
      await f.getByLabel('Local de atendimento',{exact:true}).fill('Unidade Sintetica com um nome operacional extenso para validar quebra de linha');
      await f.getByLabel('Município onde o exame será/foi realizado',{exact:true}).selectOption('3303302');
      await f.getByLabel('Origem',{exact:true}).selectOption('site');
      await f.getByLabel('Reteste técnico / cobrança',{exact:true}).selectOption('true');
      const before=await page.evaluate(async()=> (await window.SoproM15.api('/eeg/financeiro')).total);
      // One failed request must retain identity, form decisions and idempotency key.
      await page.route('**/eeg/atendimentos',async route=>{await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({erro:{codigo:'synthetic_unavailable',mensagem:'Falha sintética temporária'}})});},{times:1});
      await f.getByRole('button',{name:'SALVAR ATENDIMENTO EEG',exact:true}).click();
      await f.getByRole('alert').filter({hasText:'Falha sintética'}).waitFor();
      assert.equal(await f.getByLabel('Município onde o exame será/foi realizado',{exact:true}).inputValue(),'3303302');
      assert.equal(await f.getByLabel('Origem',{exact:true}).inputValue(),'site');
      assert.ok((await f.locator('[data-at-selected]').textContent()).includes(corporate.public_code));
      await f.getByRole('button',{name:'SALVAR ATENDIMENTO EEG',exact:true}).click();
      await f.locator('[data-at-success]').filter({hasText:'Atendimento EEG salvo'}).waitFor();
      assert.equal(await page.evaluate(async()=> (await window.SoproM15.api('/eeg/financeiro')).total),before);
      const exams=await page.evaluate(async()=> (await window.SoproM15.api('/eeg/exames?tamanho=100')).itens);
      assert.equal(exams.filter(e=>e.paciente.id===corporate.id).length,[390,768,1440].indexOf(width)+1);
      assert.ok(exams.filter(e=>e.paciente.id===corporate.id).every(e=>e.paciente.public_code===corporate.public_code));
      assert.equal(await page.locator('dialog').count(),0);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    }
    await page.setViewportSize({width:1440,height:1000});await module('cadastros');
    const dup=page.locator('[data-eeg-atendimento]');
    await dup.getByLabel('Nome completo',{exact:true}).fill('Paciente Browser Completo '+name);
    await dup.getByLabel('Nascimento (formato DD/MM/AAAA)',{exact:true}).fill('11/04/1991');
    await dup.locator('input[name="modalidade_codigo"][value="vigilia"]').check();
    await dup.getByLabel('Data do exame (formato DD/MM/AAAA)',{exact:true}).fill('20/10/2026');
    await dup.getByLabel('Horário',{exact:true}).fill('11:30');
    await dup.getByLabel('Local de atendimento',{exact:true}).fill('Local Sintetico Conferencia de Identidade');
    await dup.getByLabel('Reteste técnico / cobrança',{exact:true}).selectOption('true');
    await dup.getByRole('button',{name:'SALVAR ATENDIMENTO EEG',exact:true}).click();
    await dup.getByRole('alert').filter({hasText:'Confira os cadastros semelhantes'}).waitFor();
    await dup.locator('[data-at-duplicates]').getByRole('button').filter({hasText:'Paciente Browser Completo '+name}).click();
    await dup.locator('[data-at-new]').waitFor({state:'hidden'});
    await dup.getByRole('button',{name:'SALVAR ATENDIMENTO EEG',exact:true}).click();
    await dup.locator('[data-at-success]').filter({hasText:'Atendimento EEG salvo'}).waitFor();
    const found=await page.evaluate(async name=>window.SoproM15.api('/eeg/pessoas/busca',{method:'POST',body:JSON.stringify({q:'Paciente Browser Completo '+name,corporativo:true})}),name);
    assert.equal(found.total,1);
    await module('tarefas');await page.getByRole('button',{name:'Adicionar tarefa',exact:true}).click();await page.keyboard.press('Tab');assert.ok(await page.getByRole('dialog').evaluate(d=>d.contains(document.activeElement)));await page.keyboard.press('Escape');await closed();
    // Every exclusive EEG role has a useful brand, and no respiratory authority.
    for(const role of ['eeg_operador','eeg_tecnico','eeg_financeiro']){const p=await browser.newPage();await login(p,role);await p.waitForFunction(()=>document.body.dataset.lifeAtivo==='eeglife');assert.equal(await p.locator('[data-life-context="soprolife"]').count(),0);const status=await p.evaluate(async()=> (await fetch('/painel-soprolife/api/m15/espirometrias')).status);assert.equal(status,403);await p.close();}
    assert.deepEqual(errors,[]);await browser.close();browser=null;
  }
  const {writeFileSync}=await import('node:fs');writeFileSync(join(evidence,'browser-results.json'),JSON.stringify(results,null,2));
  console.log('PASS: Chromium e Firefox; 16 módulos em 390/768/1440; identidade, atendimento, importação, conferência, médico EEG, PDF final, assinatura, portal isolado, entrega e RBAC.');
} catch(err) {if(page){console.error('Falha sintética:',await page.locator('dialog').allTextContents().catch(()=>[]));await page.screenshot({path:join(evidence,'browser-failure.png'),fullPage:true}).catch(()=>{});}throw err;}
finally {if(browser)await browser.close();for(const c of children)c.kill('SIGTERM');await new Promise(r=>setTimeout(r,300));rmSync(dir,{recursive:true,force:true});}
