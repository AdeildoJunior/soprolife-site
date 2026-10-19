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
const { chromium } = await import(process.env.M15_TEST_PLAYWRIGHT);
async function port() { const s = createServer(); await new Promise(r => s.listen(0, '127.0.0.1', r)); const p = s.address().port; await new Promise(r => s.close(r)); return p; }
const [apiPort, panelPort, portalPort] = await Promise.all([port(), port(), port()]);
const dir = mkdtempSync(join(tmpdir(), 'fase2a-eeg-browser-'));
mkdirSync(join(dir, 'reports'), { mode: 0o700 });
const env = { ...process.env, M15_ENV: 'dev', M15_DATABASE_URL: `sqlite:///${dir}/test.db`,
  M15_API_PORT: String(apiPort), M15_API_HOST: '127.0.0.1',
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
try {
  run(['-m', 'alembic', 'upgrade', 'head']);
  run(['-c', `from app.db import get_engine\nfrom sqlalchemy.orm import Session\nfrom app.models import User, Person, Partner, PartnerUnit\nfrom app.security import ensure_roles_exist,get_role,hash_password\nfrom app.normalize import normalize_name\nfrom pypdf import PdfWriter\nwith Session(get_engine()) as db:\n ensure_roles_exist(db)\n for role in ('admin','operacional','medico'):\n  u=User(email=role+'@fase2a.test',nome='Sintetico '+role,password_hash=hash_password('synthetic-password-123'));u.roles.append(get_role(db,role));db.add(u)\n db.add(Person(public_code='PES-900001',nome_completo='Pessoa Corporativa Sintetica',nome_normalizado=normalize_name('Pessoa Corporativa Sintetica')))\n p=Partner(public_code='PAR-900001',nome='Parceiro Sintetico',tipo='clinica');db.add(p);db.flush();db.add(PartnerUnit(public_code='UNI-900001',partner_id=p.id,nome='Unidade Compartilhada Sintetica'));db.commit()\nw=PdfWriter();w.add_blank_page(width=595,height=842);w.write(${JSON.stringify(join(dir,'laudo.pdf'))})`]);
  start(['-m', 'app.serve']); start(['-m','app.portal_serve']);
  start([join(root, 'painel-soprolife/scripts/command-center-local-server.py')], {
    SOPROLIFE_PANEL_HOST: '127.0.0.1', SOPROLIFE_PANEL_PORT: String(panelPort),
    SOPROLIFE_M15_UPSTREAM: `http://127.0.0.1:${apiPort}/api/v1`,
  });
  const base=`http://127.0.0.1:${panelPort}/painel-soprolife`;
  await wait(base+'/api/m15/health'); await wait(`http://127.0.0.1:${portalPort}/p/v1/health`);
  browser=await chromium.launch({headless:true});
  page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  async function login(role) { await page.goto(base+'/'); await page.locator('#email').fill(role+'@fase2a.test'); await page.locator('#password').fill('synthetic-password-123'); await page.locator('#loginForm').evaluate(f=>f.requestSubmit()); await page.waitForFunction(()=>window.SoproM15?.hasSession()); }
  await login('admin');
  await page.locator('[data-life-context="eeglife"]').click();
  async function module(id) { await page.locator('button[data-eeg-modulo="'+id+'"]').click(); }
  async function submit(name='Salvar') { await page.getByRole('dialog').last().getByRole('button',{name,exact:true}).click(); }
  async function openExam(id='exames') { await module(id); await page.locator('[data-eeg-section="'+id+'"]').getByRole('button',{name:'Abrir exame'}).first().click(); }
  await module('cadastros');
  assert.ok(!(await page.locator('[data-eeg-section="cadastros"]').innerText()).includes('Pessoa Corporativa Sintetica'));
  await page.getByRole('button',{name:'Cadastrar / vincular paciente'}).click();
  await page.getByLabel('Nome completo',{exact:true}).fill('Paciente Browser Sintetico');
  await page.getByLabel('Nascimento',{exact:true}).fill('1991-04-11');
  await submit('Verificar identidade'); await page.getByRole('button',{name:'Confirmar novo paciente',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('dialog'));
  await page.getByRole('button',{name:'Cadastrar / vincular paciente'}).click();
  await page.getByLabel('Nome completo',{exact:true}).fill('Pessoa Corporativa Sintetica');
  await submit('Verificar identidade');
  await page.getByRole('button',{name:/Vincular PES-900001/}).click();
  await page.waitForFunction(()=>!document.querySelector('dialog'));
  await page.waitForFunction(()=>document.querySelector('[data-eeg-section="cadastros"]').textContent.includes("Pessoa Corporativa Sintetica"));
  await module('leads'); await page.getByRole('button',{name:'Agendar EEG',exact:true}).click();
  const select=page.getByLabel('Paciente',{exact:true});
  await select.waitFor({state:"visible"});
  const opts=await select.locator('option').evaluateAll(xs=>xs.map(x=>({value:x.value,text:x.textContent})));
  assert.ok(opts.some(o=>o.text.includes("Paciente Browser Sintetico")), JSON.stringify(opts));
  await select.selectOption(opts.find(o=>o.text.includes('Paciente Browser Sintetico')).value);
  await page.getByLabel('Modalidade',{exact:true}).selectOption('vigilia');
  await page.getByLabel('Data e hora',{exact:true}).fill('2026-11-20T10:30');
  const locations=await page.getByLabel('Origem / local',{exact:true}).locator('option').evaluateAll(xs=>xs.map(x=>({value:x.value,text:x.textContent})));
  await page.getByLabel('Origem / local',{exact:true}).selectOption(locations.find(o=>o.text.includes('Compartilhada')).value);
  await submit(); await page.waitForFunction(()=>!document.querySelector('dialog'));
  await openExam('leads'); await page.getByRole('button',{name:'Registrar realização',exact:true}).click();
  await page.getByLabel('Data e hora',{exact:true}).fill('2026-11-20T11:00'); await submit('Confirmar');
  await page.waitForFunction(()=>!document.querySelector('dialog'));
  await openExam(); await page.getByRole('button',{name:'Registrar referência técnica'}).click();
  await page.getByLabel('Referência técnica',{exact:true}).fill('SYNTH-REF-001'); await submit();
  await page.waitForFunction(()=>!document.querySelector('dialog'));
  await openExam(); await page.getByRole('button',{name:'Registrar envio ao laudador'}).click();
  await page.getByLabel('Tipo de destino',{exact:true}).selectOption('empresa');
  await page.getByLabel('Destino',{exact:true}).fill('Empresa Laudadora Sintetica');
  await page.getByLabel('Data e hora do envio').fill('2026-11-20T11:20');
  await page.getByLabel('Protocolo / referência (opcional)').fill('SYNTH-PROTO-001'); await submit();
  await page.waitForFunction(()=>!document.querySelector('dialog'));
  await openExam('laudos'); await page.getByRole('button',{name:'Registrar em laudo'}).click(); await submit('Confirmar');
  await page.waitForFunction(()=>!document.querySelector('dialog'));
  await openExam('laudos'); await page.getByRole('button',{name:'Receber PDF do laudo'}).click();
  await page.getByLabel('Laudo PDF',{exact:true}).setInputFiles(join(dir,'laudo.pdf')); await submit();
  await page.waitForFunction(()=>!document.querySelector('dialog'));
  await openExam('laudos'); await page.getByRole('button',{name:'Disponibilizar PDF final'}).click(); await submit('Confirmar');
  await page.waitForFunction(()=>!document.querySelector('dialog'));
  await openExam('laudos');
  const download=page.waitForEvent('download'); await page.getByRole('button',{name:'Baixar laudo PDF',exact:true}).click(); assert.ok((await download).suggestedFilename().endsWith('.pdf'));
  await page.getByRole('button',{name:'Gerar / exibir acesso do paciente'}).click();
  const url=await page.getByLabel('Link do resultado').inputValue();
  assert.ok(url.startsWith('https://portal.test/p/v1/eeg/#t=eeg_'));
  await page.getByRole('button',{name:'Fechar',exact:true}).click();
  for(const width of [390,1440]) { await page.setViewportSize({width,height:900}); await module('exames'); assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)); }
  const patient=await browser.newPage(); await patient.goto(`http://127.0.0.1:${portalPort}/p/v1/eeg/`+new URL(url).hash);
  await patient.getByLabel('Data de nascimento',{exact:true}).fill('11/04/1991'); await patient.locator('form').evaluate(f=>f.requestSubmit());
  await patient.locator('#linkLaudo').waitFor({state:'visible'});
  assert.equal(await patient.locator('#linkTecnico').count(),0);
  const pdownload=patient.waitForEvent('download'); await patient.locator('#linkLaudo').click(); assert.ok((await pdownload).suggestedFilename().endsWith('.pdf'));
  assert.deepEqual(errors,[]);
  // New browser sessions prove both UI visibility and direct server refusal.
  for(const role of ['operacional','medico']) {
    const ctx=await browser.newContext(); const p=await ctx.newPage();
    await p.goto(base+'/'); await p.locator('#email').fill(role+'@fase2a.test'); await p.locator('#password').fill('synthetic-password-123'); await p.locator('#loginForm').evaluate(f=>f.requestSubmit());
    await p.waitForFunction(()=>window.SoproM15?.hasSession());
    assert.equal(await p.locator('[data-life-context="eeglife"]').count(),0);
    const status=await p.evaluate(async()=> (await fetch('/painel-soprolife/api/m15/eeg/exames')).status);
    assert.equal(status,403); await ctx.close();
  }
  console.log('PASS: identidade compartilhada, Central, agenda com parceiro, realização, referência, envio, em laudo, upload, publicação, download pelo proxy, portal PDF e recusa operacional/médico; 390/1440px.');
} catch (err) {
  if (page) console.error('DOM sintético na falha:', await page.locator('[data-eeg-section="leads"]').innerText().catch(()=>''), 'DIALOG:', await page.locator('dialog').allTextContents());
  throw err;
} finally { if(browser) await browser.close(); for(const c of children) c.kill('SIGTERM'); await new Promise(r=>setTimeout(r,300)); rmSync(dir,{recursive:true,force:true}); }
