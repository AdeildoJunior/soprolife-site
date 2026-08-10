#!/usr/bin/env node
/* M25.18 — prova, no navegador de verdade, o nome com que o PDF é salvo.
 *
 * Por que este teste existe separado dos testes de API: a M25.17 conferiu o
 * `Content-Disposition` direto na API e deu por resolvido, mas no uso real o
 * arquivo saiu como `UWNAUiEo.pdf`. Havia duas causas no caminho ENTRE a API
 * e o disco do usuário, e nenhuma delas aparece quando se testa a API
 * isolada:
 *
 *   1. o proxy do painel descartava o cabeçalho (allowlist sem espaço e sem
 *      `filename*`);
 *   2. o visualizador de PDF do Chrome baixava de uma object URL (`blob:`),
 *      que não carrega nome nenhum.
 *
 * Aqui sobe-se SQLite + API + proxy + Chrome real e usa-se
 * `Browser.setDownloadBehavior` + `Browser.downloadWillBegin`, que reporta o
 * `suggestedFilename` — exatamente o nome que o Chrome usaria no disco.
 *
 * Somente usuários, pessoa, exame e PDFs sintéticos; tudo em diretório
 * temporário.
 */
"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const M15_DIR = path.join(ROOT, "painel-soprolife", "nucleo-m15");
const PROXY = path.join(
  ROOT, "painel-soprolife", "scripts", "command-center-local-server.py"
);
const PYTHON = process.env.M15_TEST_PYTHON;
const CHROME = process.env.M24A_CHROME || "/usr/bin/google-chrome";
const PASSWORD = "senha-browser-m25-18-sintetica-123";
const PACIENTE = "ANTONIO SINTETICO DA SILVA";

let failures = 0;
function check(label, condition, detail = "") {
  if (condition) {
    console.log(`  PASS: ${label}`);
    return;
  }
  failures += 1;
  console.log(`  FAIL: ${label}${detail ? ` — ${detail}` : ""}`);
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

async function waitFor(fn, label, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const result = await fn();
      if (result) return result;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 120));
  }
  throw new Error(
    `timeout: ${label}${lastError ? ` (${lastError.message})` : ""}`
  );
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.sequence = 0;
    this.pending = new Map();
    this.events = [];
    ws.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (!message.id) {
        this.events.push(message);
        return;
      }
      const handler = this.pending.get(message.id);
      if (!handler) return;
      this.pending.delete(message.id);
      if (message.error) handler.reject(new Error(message.error.message));
      else handler.resolve(message.result || {});
    });
  }

  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", resolve, { once: true });
      ws.addEventListener("error", reject, { once: true });
    });
    return new Cdp(ws);
  }

  send(method, params = {}) {
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      const exception = result.exceptionDetails.exception;
      throw new Error(
        (exception && exception.description)
          || result.exceptionDetails.text
          || "Runtime.evaluate falhou"
      );
    }
    return result.result ? result.result.value : undefined;
  }

  downloadsIniciados() {
    return this.events
      .filter((e) => e.method === "Browser.downloadWillBegin")
      .map((e) => e.params);
  }

  close() {
    this.ws.close();
  }
}

function spawnLogged(command, args, options) {
  const handle = childProcess.spawn(command, args, {
    ...options,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  handle.stdout.on("data", (chunk) => { log += chunk; });
  handle.stderr.on("data", (chunk) => { log += chunk; });
  handle.getLog = () => log.slice(-6000);
  return handle;
}

function stop(handle) {
  if (handle && handle.exitCode === null && !handle.killed) {
    handle.kill("SIGTERM");
  }
}

function runChecked(command, args, options) {
  const result = childProcess.spawnSync(command, args, {
    ...options,
    encoding: "utf8",
  });
  if (result.status !== 0) {
    throw new Error(
      `${path.basename(command)} falhou: ${
        (result.stderr || result.stdout || "").slice(-3000)
      }`
    );
  }
  return result;
}

async function main() {
  if (!PYTHON || !fs.existsSync(PYTHON)) {
    throw new Error("defina M15_TEST_PYTHON para o Python do venv M15");
  }
  if (!fs.existsSync(CHROME)) {
    throw new Error(`Google Chrome não encontrado: ${CHROME}`);
  }

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m2518-"));
  const dbPath = path.join(temp, "m2518.db");
  const reportsPath = path.join(temp, "reports");
  const downloads = path.join(temp, "downloads");
  const pdfPath = path.join(temp, "TESTE-APAGAR-mir.pdf");
  fs.mkdirSync(downloads, { recursive: true });
  const apiPort = await freePort();
  const panelPort = await freePort();
  const chromePort = await freePort();
  const env = {
    ...process.env,
    PYTHONDONTWRITEBYTECODE: "1",
    M15_ENV: "dev",
    M15_DATABASE_URL: `sqlite:///${dbPath}`,
    M15_API_HOST: "127.0.0.1",
    M15_API_PORT: String(apiPort),
    M15_AUTH_SECRET: "m25-18-download-e2e-secret-synthetic-0123456789",
    M15_SESSION_COOKIE_PATH: "/painel-soprolife/api/m15",
    M15_REPORTS_STORAGE_DIR: reportsPath,
    M15_REPORTS_ENABLED: "true",
    M15_REPORTS_MODE: "pilot",
  };
  const alembic = path.join(path.dirname(PYTHON), "alembic");
  const children = [];
  let cdp;

  try {
    console.log("── Preparando ambiente sintético ──");
    runChecked(PYTHON, [
      "-c",
      "import sys\nfrom pypdf import PdfWriter\nw=PdfWriter()\n"
        + "w.add_blank_page(width=595,height=842)\n"
        + "with open(sys.argv[1],'wb') as f:w.write(f)",
      pdfPath,
    ], { cwd: M15_DIR, env });
    runChecked(alembic, ["upgrade", "head"], { cwd: M15_DIR, env });

    for (const [email, nome, papel] of [
      ["oper-m2518@teste.local", "TESTE APAGAR Operacional", "operacional"],
      ["medica-m2518@teste.local", "TESTE APAGAR Medica", "medico"],
      ["admin-m2518@teste.local", "TESTE APAGAR Admin", "admin"],
    ]) {
      runChecked(PYTHON, [
        "-m", "app.cli", "criar-usuario",
        "--email", email, "--nome", nome, "--papel", papel,
      ], { cwd: M15_DIR, env: { ...env, M15_NOVA_SENHA: PASSWORD } });
    }

    // Só o que não tem endpoint conveniente entra por ORM: parceiro,
    // unidade e o perfil médico verificado. Pessoa, exame e o laudo
    // atribuído passam pela API DE VERDADE, mais adiante — é o mesmo
    // caminho que a operação usa, e é o que o teste precisa exercitar.
    const seedScript = path.join(temp, "seed.py");
    fs.writeFileSync(seedScript, `
from datetime import datetime, timezone
from sqlalchemy import select
from app.db import get_sessionmaker
from app.models import Partner, PartnerUnit, PhysicianProfile, User

S = get_sessionmaker()
with S() as db:
    medica = db.execute(select(User).where(
        User.email == "medica-m2518@teste.local")).scalar_one()
    # Um CHECK impede autoverificação (verification_not_self): quem verifica
    # o CRM tem de ser outra pessoa. Aqui, o admin.
    admin = db.execute(select(User).where(
        User.email == "admin-m2518@teste.local")).scalar_one()
    db.add(PhysicianProfile(
        user_id=medica.id, professional_name="TESTE APAGAR Medica",
        crm_number="700518", crm_state="SC", verification_status="verified",
        verified_at=datetime.now(timezone.utc), verified_by_user_id=admin.id,
        verification_reference="CRM-VERIF-M2518", active=True,
        especialidade="Pneumologista",
    ))
    parceiro = Partner(public_code="PAR-M2518", nome="TESTE APAGAR Parceira")
    db.add(parceiro); db.flush()
    unidade = PartnerUnit(
        public_code="UNI-M2518", partner_id=parceiro.id,
        nome="TESTE APAGAR Unidade", logradouro="Rua Sintetica, 18",
        bairro="Centro", cidade="Rio de Janeiro", uf="RJ", ativo=True,
    )
    db.add(unidade); db.commit()
    print(parceiro.id + " " + unidade.id)
`);
    const [partnerId, unitId] = runChecked(PYTHON, [seedScript], {
      cwd: M15_DIR,
      // O arquivo vive em /tmp; sem isto o Python não acha o pacote `app`.
      env: { ...env, PYTHONPATH: M15_DIR },
    }).stdout.trim().split("\n").pop().split(" ");
    console.log(`  parceiro/unidade semeados: ${unitId}`);

    const api = spawnLogged(PYTHON, ["-m", "app.serve"], {
      cwd: M15_DIR, env,
    });
    children.push(api);
    const proxy = spawnLogged("python3", [PROXY], {
      cwd: ROOT,
      env: {
        ...process.env,
        PYTHONDONTWRITEBYTECODE: "1",
        SOPROLIFE_PANEL_HOST: "127.0.0.1",
        SOPROLIFE_PANEL_PORT: String(panelPort),
        SOPROLIFE_M15_UPSTREAM: `http://127.0.0.1:${apiPort}/api/v1`,
      },
    });
    children.push(proxy);
    const base = `http://127.0.0.1:${panelPort}/painel-soprolife`;
    await waitFor(
      async () => (await fetch(`${base}/api/m15/health`)).ok,
      "API/proxy local"
    );

    console.log("\n── Semeando pessoa, exame e laudo pela API real ──");
    async function apiLogin(email) {
      const resposta = await fetch(`${base}/api/m15/auth/token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password: PASSWORD }),
      });
      if (!resposta.ok) throw new Error(`login ${email}: ${resposta.status}`);
      const cookie = resposta.headers.getSetCookie
        ? resposta.headers.getSetCookie()
        : [resposta.headers.get("set-cookie")];
      const corpo = await resposta.json();
      return {
        cookie: cookie.filter(Boolean).map((c) => c.split(";")[0]).join("; "),
        csrf: corpo && corpo.csrf,
      };
    }
    const oper = await apiLogin("oper-m2518@teste.local");
    async function apiOper(caminho, opcoes = {}) {
      const resposta = await fetch(`${base}/api/m15${caminho}`, {
        ...opcoes,
        headers: {
          Cookie: oper.cookie,
          "X-CSRF-Token": oper.csrf || "",
          ...(opcoes.headers || {}),
        },
      });
      if (!resposta.ok) {
        throw new Error(`${caminho}: ${resposta.status} ${await resposta.text()}`);
      }
      return resposta.json();
    }
    const pessoa = await apiOper("/pessoas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nome_completo: PACIENTE }),
    });
    const exame = await apiOper("/espirometrias", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        person_id: pessoa.id,
        data_exame: "2026-08-09",
        status: "Realizado",
        broncodilatador: true,
        modalidade: "clinica_parceira",
        partner_id: partnerId,
        partner_unit_id: unitId,
      }),
    });
    const perfil = (await apiOper("/laudos/medicos-disponiveis"))[0];
    const formulario = new FormData();
    formulario.append("exam_code", exame.public_code);
    formulario.append("physician_profile_id", perfil.id);
    formulario.append(
      "file",
      new Blob([fs.readFileSync(pdfPath)], { type: "application/pdf" }),
      "tecnico.pdf"
    );
    const laudo = await apiOper("/laudos", {
      method: "POST",
      body: formulario,
    });
    console.log(`  ${pessoa.public_code} / ${exame.public_code} / ${laudo.public_code}`);

    const chrome = spawnLogged(CHROME, [
      "--headless=new", "--disable-gpu", "--no-first-run",
      "--no-default-browser-check", "--disable-background-networking",
      `--remote-debugging-port=${chromePort}`,
      `--user-data-dir=${path.join(temp, "chrome")}`,
      "about:blank",
    ], { cwd: ROOT, env: process.env });
    children.push(chrome);
    await waitFor(
      async () => (await fetch(`http://127.0.0.1:${chromePort}/json/version`)).ok,
      "Chrome DevTools"
    );
    const target = await (await fetch(
      `http://127.0.0.1:${chromePort}/json/new?${encodeURIComponent(base + "/")}`,
      { method: "PUT" }
    )).json();
    cdp = await Cdp.connect(target.webSocketDebuggerUrl);
    await Promise.all([
      cdp.send("Page.enable"),
      cdp.send("Runtime.enable"),
      cdp.send("Network.enable"),
    ]);
    // É este evento que revela o nome REAL: o Chrome o emite com o
    // `suggestedFilename` que usaria no disco.
    await cdp.send("Browser.setDownloadBehavior", {
      behavior: "allowAndName",
      downloadPath: downloads,
      eventsEnabled: true,
    });

    console.log("\n── Login por senha (sessão por cookie, como a médica) ──");
    await waitFor(
      () => cdp.evaluate("Boolean(document.querySelector('#m15LoginForm'))"),
      "formulário de login"
    );
    await cdp.evaluate(`(() => {
      const form = document.querySelector("#m15LoginForm");
      form.elements.email.value = "medica-m2518@teste.local";
      form.elements.password.value = ${JSON.stringify(PASSWORD)};
      form.requestSubmit();
    })()`);
    await waitFor(
      () => cdp.evaluate("Boolean(window.SoproM15?.hasSession())"),
      "sessão por cookie estabelecida"
    );
    check("médica autenticada por cookie (não por token)", true);

    console.log("\n── Abre o laudo na bancada ──");
    await cdp.evaluate(`document.querySelector(
      '.nav-item[data-section="laudos-espirometria"]').click()`);
    await waitFor(
      () => cdp.evaluate(
        "Boolean(document.querySelector('[data-report-open]'))"
      ),
      "fila da médica"
    );
    await cdp.evaluate(
      "document.querySelector('[data-report-open]').click()"
    );
    await waitFor(
      () => cdp.evaluate(
        "Boolean(document.querySelector('#reportPdfOriginal'))"
      ),
      "bancada com o PDF da MIR"
    );

    // CAUSA 2 — o <iframe> não pode mais apontar para blob:.
    const src = await cdp.evaluate(
      "document.querySelector('#reportPdfOriginal').src"
    );
    check(
      "visualizador NÃO usa object URL (blob:)",
      !String(src).startsWith("blob:"),
      `src=${String(src).slice(0, 60)}`
    );
    check(
      "visualizador aponta para a API de mesma origem",
      String(src).includes("/painel-soprolife/api/m15/laudos/"),
      String(src).slice(0, 90)
    );

    // CAUSA 1 — o cabeçalho precisa sobreviver ao proxy.
    const disposicao = await cdp.evaluate(`(async () => {
      const r = await fetch(${JSON.stringify(src)}, {
        credentials: "same-origin",
      });
      return r.headers.get("Content-Disposition");
    })()`);
    check(
      "Content-Disposition atravessa o proxy do painel",
      Boolean(disposicao),
      String(disposicao)
    );
    check(
      "cabeçalho carrega o nome humano do paciente",
      String(disposicao).includes(`${PACIENTE} - Exame técnico.pdf`)
        || String(disposicao).includes(`${PACIENTE} - Exame tecnico.pdf`),
      String(disposicao)
    );

    console.log("\n── Download real: o nome que o Chrome usaria no disco ──");
    const antes = cdp.downloadsIniciados().length;
    await cdp.evaluate(
      "document.querySelector('[data-report-download]').click()"
    );
    const evento = await waitFor(
      () => {
        const lista = cdp.downloadsIniciados();
        return lista.length > antes ? lista[lista.length - 1] : null;
      },
      "Browser.downloadWillBegin"
    );
    console.log(`  suggestedFilename = ${evento.suggestedFilename}`);
    check(
      "download do painel sai com o nome do paciente",
      evento.suggestedFilename === `${PACIENTE} - Exame técnico.pdf`,
      evento.suggestedFilename
    );
    // Sem os consertos o Chrome nomeia sozinho — ora com 8 caracteres
    // (`UWNAUiEo.pdf`, o caso relatado), ora com um GUID. As duas formas
    // são "nome gerado", e nenhuma contém o nome do paciente.
    check(
      "nome NÃO é gerado pelo navegador (aleatório ou GUID)",
      !/^[A-Za-z0-9]{8}\.pdf$/.test(evento.suggestedFilename)
        && !/^[0-9a-f-]{36}\.pdf$/i.test(evento.suggestedFilename)
        && evento.suggestedFilename.includes(PACIENTE),
      evento.suggestedFilename
    );
  } finally {
    if (cdp) cdp.close();
    for (const child of children) stop(child);
    await new Promise((resolve) => setTimeout(resolve, 400));
    try {
      fs.rmSync(temp, { recursive: true, force: true });
    } catch (_) { /* diretório temporário */ }
  }

  console.log(
    `\nRESULTADO: ${failures === 0 ? "OK" : `FALHOU (${failures})`}`
  );
  if (failures > 0) process.exitCode = 1;
}

main().catch((error) => {
  console.error(`RESULTADO: FALHOU — ${error.message}`);
  process.exitCode = 1;
});
