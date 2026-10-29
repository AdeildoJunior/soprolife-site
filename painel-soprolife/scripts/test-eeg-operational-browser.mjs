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
    await login(page,'admin');await page.locator('[data-life-context="eeglife"]').click();
    await module('cadastros');await page.getByRole('button',{name:'Cadastrar / vincular paciente',exact:true}).click();
    await page.getByRole('dialog').last().getByLabel('Nome completo',{exact:true}).fill('Paciente Browser Completo '+name);await page.getByRole('dialog').last().getByLabel('Nascimento',{exact:true}).fill('1991-04-11');await submit('Verificar identidade');await page.getByRole('button',{name:'Confirmar novo paciente',exact:true}).click();await closed();
    await page.getByRole('button',{name:'Novo Atendimento EEG',exact:true}).click();
    await page.getByRole('dialog').last().getByLabel('Paciente',{exact:true}).waitFor({state:'visible'});
    await page.getByRole('dialog').last().getByLabel('Paciente',{exact:true}).selectOption({label:(await page.getByRole('dialog').last().getByLabel('Paciente',{exact:true}).locator('option').allTextContents()).find(t=>t.includes('Paciente Browser Completo '+name))});
    await page.getByRole('dialog').last().getByLabel('Data e hora',{exact:true}).fill('2026-10-20T10:30');await page.getByRole('dialog').last().getByLabel('Local de atendimento',{exact:true}).fill('Local Browser Sintetico');await page.getByRole('dialog').last().getByLabel('Valor contratado (R$)',{exact:true}).fill('250');
    assert.equal(await page.getByRole('dialog').last().getByLabel('Município da prestação',{exact:true}).inputValue(),'3304557');await submit('Criar atendimento');await closed();
    const e=await page.evaluate(async name=>(await window.SoproM15.api('/eeg/exames?tamanho=100')).itens.find(e=>e.paciente.nome_completo==='Paciente Browser Completo '+name),name);
    assert.ok(e?.id,JSON.stringify(await page.evaluate(()=>window.SoproM15.api('/eeg/exames?tamanho=100'))));
    await openExam();await page.getByRole('button',{name:'Registrar realização',exact:true}).click();await page.getByRole('dialog').last().getByLabel('Data e hora',{exact:true}).fill('2026-10-20T10:35');await submit('Confirmar');await closed();
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
        if(['visao','crm','exames','laudos','fiscal'].includes(id))await page.screenshot({path:join(evidence,`${name}-${width}-${id}.png`),fullPage:true});
        results.push({engine:name,width,module:id,overflow:false});
      }
    }
    await page.setViewportSize({width:1440,height:1000});await module('crm');await page.evaluate(()=>document.documentElement.style.zoom='2');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.evaluate(()=>document.documentElement.style.zoom='');
    await module('tarefas');await page.getByRole('button',{name:'Adicionar tarefa',exact:true}).click();await page.keyboard.press('Tab');assert.ok(await page.getByRole('dialog').evaluate(d=>d.contains(document.activeElement)));await page.keyboard.press('Escape');await closed();
    // Every exclusive EEG role has a useful brand, and no respiratory authority.
    for(const role of ['eeg_operador','eeg_tecnico','eeg_financeiro']){const p=await browser.newPage();await login(p,role);await p.waitForFunction(()=>document.body.dataset.lifeAtivo==='eeglife');assert.equal(await p.locator('[data-life-context="soprolife"]').count(),0);const status=await p.evaluate(async()=> (await fetch('/painel-soprolife/api/m15/espirometrias')).status);assert.equal(status,403);await p.close();}
    assert.deepEqual(errors,[]);await browser.close();browser=null;
  }
  const {writeFileSync}=await import('node:fs');writeFileSync(join(evidence,'browser-results.json'),JSON.stringify(results,null,2));
  console.log('PASS: Chromium e Firefox; 16 módulos em 390/768/1440; identidade, atendimento, importação, conferência, médico EEG, PDF final, assinatura, portal isolado, entrega e RBAC.');
} catch(err) {if(page){console.error('Falha sintética:',await page.locator('dialog').allTextContents().catch(()=>[]));await page.screenshot({path:join(evidence,'browser-failure.png'),fullPage:true}).catch(()=>{});}throw err;}
finally {if(browser)await browser.close();for(const c of children)c.kill('SIGTERM');await new Promise(r=>setTimeout(r,300));rmSync(dir,{recursive:true,force:true});}
