#!/usr/bin/env node
/* M21 browser/E2E sem dependência npm.
 *
 * Sobe SQLite + API + proxy em portas loopback efêmeras, dirige o Chrome pelo
 * DevTools Protocol e valida login real, cookie, F5, logout, CRM, navegação,
 * acessibilidade básica e viewports 1440/1000/800/420. Usa somente registros
 * sintéticos e remove o banco temporário ao terminar.
 *
 * Uso:
 *   M15_TEST_PYTHON=/caminho/venv/bin/python \
 *     node painel-soprolife/scripts/test-m21-browser-e2e.js
 */
"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const M15_DIR = path.join(ROOT, "painel-soprolife", "nucleo-m15");
const PROXY = path.join(ROOT, "painel-soprolife", "scripts",
                        "command-center-local-server.py");
const PYTHON = process.env.M15_TEST_PYTHON;
const CHROME = process.env.M21_CHROME || "/usr/bin/google-chrome";
const EMAIL = "admin-browser@teste.local";
const PASSWORD = "senha-browser-sintetica-123";

let failures = 0;
function check(label, condition, detail = "") {
  if (condition) console.log(`  PASS: ${label}`);
  else {
    failures += 1;
    console.log(`  FAIL: ${label}${detail ? " — " + detail : ""}`);
  }
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

async function waitFor(fn, label, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const result = await fn();
      if (result) return result;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`timeout: ${label}${lastError ? ` (${lastError.message})` : ""}`);
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.sequence = 0;
    this.pending = new Map();
    ws.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (!message.id) return;
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
      throw new Error(result.exceptionDetails.text || "Runtime.evaluate falhou");
    }
    return result.result ? result.result.value : undefined;
  }

  close() {
    this.ws.close();
  }
}

function spawnLogged(command, args, options) {
  const proc = childProcess.spawn(command, args, {
    ...options,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  proc.stdout.on("data", (chunk) => { log += chunk; });
  proc.stderr.on("data", (chunk) => { log += chunk; });
  proc.getLog = () => log.slice(-6000);
  return proc;
}

function stop(proc) {
  if (proc && proc.exitCode === null && !proc.killed) proc.kill("SIGTERM");
}

async function main() {
  if (!PYTHON || !fs.existsSync(PYTHON)) {
    throw new Error("defina M15_TEST_PYTHON para o Python do venv M15");
  }
  if (!fs.existsSync(CHROME)) throw new Error(`Chrome não encontrado: ${CHROME}`);

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m21-e2e-"));
  const dbPath = path.join(temp, "m21-e2e.db");
  const profile = path.join(temp, "chrome-profile");
  const apiPort = await freePort();
  const panelPort = await freePort();
  const chromePort = await freePort();
  const databaseUrl = `sqlite:///${dbPath}`;
  const commonEnv = {
    ...process.env,
    PYTHONDONTWRITEBYTECODE: "1",
    M15_ENV: "dev",
    M15_DATABASE_URL: databaseUrl,
    M15_API_HOST: "127.0.0.1",
    M15_API_PORT: String(apiPort),
    M15_AUTH_SECRET: "m21-browser-e2e-secret-32-chars-synthetic-only",
    M15_SESSION_COOKIE_PATH: "/painel-soprolife/api/m15",
    M15_MARKETING_REFRESH_QUEUE: path.join(temp, "marketing-request.json"),
  };
  const alembic = path.join(path.dirname(PYTHON), "alembic");
  const children = [];
  let cdp;

  try {
    for (const [command, args, extraEnv] of [
      [alembic, ["upgrade", "head"], {}],
      [PYTHON, ["-m", "app.cli", "criar-usuario", "--email", EMAIL,
        "--nome", "Teste Browser", "--papel", "admin"],
       { M15_NOVA_SENHA: PASSWORD }],
    ]) {
      const result = childProcess.spawnSync(command, args, {
        cwd: M15_DIR,
        env: { ...commonEnv, ...extraEnv },
        encoding: "utf8",
      });
      if (result.status !== 0) {
        throw new Error(`${path.basename(command)} falhou: ${
          (result.stderr || result.stdout || "").slice(-2000)}`);
      }
    }

    const api = spawnLogged(PYTHON, ["-m", "app.serve"], {
      cwd: M15_DIR,
      env: commonEnv,
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
    await waitFor(async () => {
      const response = await fetch(`${base}/api/m15/health`);
      return response.ok;
    }, "API/proxy local");

    const chrome = spawnLogged(CHROME, [
      "--headless=new",
      "--disable-gpu",
      "--disable-background-networking",
      "--disable-component-update",
      "--no-first-run",
      "--no-default-browser-check",
      `--remote-debugging-port=${chromePort}`,
      `--user-data-dir=${profile}`,
      "about:blank",
    ], { cwd: ROOT, env: process.env });
    children.push(chrome);

    const version = await waitFor(async () => {
      const response = await fetch(`http://127.0.0.1:${chromePort}/json/version`);
      return response.ok ? response.json() : null;
    }, "Chrome DevTools");
    const targetResponse = await fetch(
      `http://127.0.0.1:${chromePort}/json/new?${encodeURIComponent(base + "/")}`,
      { method: "PUT" },
    );
    if (!targetResponse.ok) throw new Error("não foi possível criar aba Chrome");
    const target = await targetResponse.json();
    cdp = await Cdp.connect(target.webSocketDebuggerUrl);
    await Promise.all([
      cdp.send("Page.enable"),
      cdp.send("Runtime.enable"),
      cdp.send("Network.enable"),
      cdp.send("Accessibility.enable"),
    ]);

    await waitFor(
      () => cdp.evaluate("Boolean(document.querySelector('#m15LoginForm'))"),
      "formulário M21",
    );

    console.log("── Formulário e login real ──");
    const dom = await cdp.evaluate(`(() => {
      const form = document.querySelector("#m15LoginForm");
      const email = form && form.elements.email;
      const password = form && form.elements.password;
      const submit = form && form.querySelector('button[type="submit"]');
      return {
        tag: form && form.tagName,
        email: email && [email.type, email.name, email.autocomplete],
        password: password && [password.type, password.name, password.autocomplete],
        submit: Boolean(submit),
      };
    })()`);
    check("login é FORM com submit normal", dom.tag === "FORM" && dom.submit);
    check("e-mail compatível com password manager",
          JSON.stringify(dom.email) === JSON.stringify(["email", "email", "username"]));
    check("senha compatível com password manager",
          JSON.stringify(dom.password) ===
            JSON.stringify(["password", "password", "current-password"]));

    await cdp.evaluate(`(() => {
      const form = document.querySelector("#m15LoginForm");
      form.elements.email.value = ${JSON.stringify(EMAIL)};
      form.elements.password.value = ${JSON.stringify(PASSWORD)};
      form.elements.manter_conectado.checked = true;
      form.requestSubmit();
    })()`);
    await waitFor(
      () => cdp.evaluate("Boolean(document.querySelector('#m15Sair'))"),
      "login concluído",
    );
    check("senha saiu do DOM depois da requisição",
          !(await cdp.evaluate("Boolean(document.querySelector('#m15Senha'))")));
    check("identidade correta após login",
          await cdp.evaluate(
            "document.querySelector('.m15-session-user')?.textContent.includes('Teste Browser')"
          ));

    const cookies = (await cdp.send("Network.getCookies", {
      urls: [`${base}/api/m15/auth/sessao`],
    })).cookies || [];
    const cookie = cookies.find((item) => item.name === "soprolife_m15_sessao");
    check("cookie HttpOnly emitido", Boolean(cookie && cookie.httpOnly));
    check("cookie SameSite=Strict", Boolean(cookie && cookie.sameSite === "Strict"));
    check("cookie persistente tem no máximo 7 dias",
          Boolean(cookie && cookie.expires > Date.now() / 1000 &&
            cookie.expires - Date.now() / 1000 <= 7 * 86400 + 5));

    console.log("── F5, CRM e navegação ──");
    await cdp.evaluate("window.__m21BeforeReload = true");
    await cdp.send("Page.reload", { ignoreCache: true });
    await waitFor(
      () => cdp.evaluate(
        "typeof window.__m21BeforeReload === 'undefined' && document.readyState === 'complete'"
      ),
      "novo documento após F5",
    );
    await waitFor(
      () => cdp.evaluate("Boolean(document.querySelector('#m15Sair'))"),
      "restauração após F5",
    );
    await waitFor(
      () => cdp.evaluate(
        "Boolean(document.querySelector('#cardsGrid')?.children.length)"
      ),
      "aplicação principal pronta após F5",
    );
    check("F5 restaura o mesmo usuário",
          await cdp.evaluate(
            "document.querySelector('.m15-session-user')?.textContent.includes('Teste Browser')"
          ));

    await cdp.evaluate(
      "document.querySelector('.sidebar .nav-item[data-section=\"crm\"]').click()"
    );
    await waitFor(
      () => cdp.evaluate("document.querySelector('#crm')?.classList.contains('active')"),
      "seção CRM ativa",
    );
    await waitFor(
      () => cdp.evaluate("Boolean(document.querySelector('#crmWorkspace .crm-ws-kpi'))"),
      "KPIs reais do CRM",
    );
    const crmText = await cdp.evaluate("document.querySelector('#crmView').textContent");
    for (const label of [
      "Total de pacientes", "Contatos hoje", "Contatos atrasados",
      "Próximos 7 dias", "Próximos 30 dias", "Sem telefone válido",
      "Follow-ups concluídos no mês", "Pacientes reativados",
    ]) check(`CRM contém KPI: ${label}`, crmText.includes(label));

    const sidebarCount = await cdp.evaluate(
      "document.querySelectorAll('.sidebar .nav-item[data-section=\"automacoes-crm\"]').length"
    );
    check("sidebar tem exatamente um destino Automação CRM", sidebarCount === 1);
    await cdp.evaluate(
      "document.querySelector('.sidebar .nav-item[data-section=\"automacoes-crm\"]').click()"
    );
    await waitFor(
      () => cdp.evaluate(
        "document.querySelector('#automacoes-crm')?.classList.contains('active') && " +
        "document.querySelector('#automacoes-crm')?.textContent.includes('Envio automático DESLIGADO')"
      ),
      "Automação CRM",
    );
    check("WhatsApp automático continua desligado", true);

    console.log("── Viewports e acessibilidade ──");
    await cdp.evaluate(
      "document.querySelector('.sidebar .nav-item[data-section=\"crm\"]').click()"
    );
    await waitFor(
      () => cdp.evaluate("document.querySelector('#crm')?.classList.contains('active')"),
      "CRM reaberto",
    );
    await waitFor(
      () => cdp.evaluate("Boolean(document.querySelector('#crmWorkspace .crm-ws-kpi'))"),
      "KPIs reabertos para viewports",
    );
    for (const width of [1440, 1000, 800, 420]) {
      await cdp.send("Emulation.setDeviceMetricsOverride", {
        width, height: 900, deviceScaleFactor: 1, mobile: width <= 420,
      });
      await new Promise((resolve) => setTimeout(resolve, 180));
      const metrics = await cdp.evaluate(`({
        inner: window.innerWidth,
        body: document.body.scrollWidth,
        root: document.documentElement.scrollWidth
      })`);
      check(`viewport ${width}px sem overflow horizontal`,
            metrics.body <= metrics.inner && metrics.root <= metrics.inner,
            JSON.stringify(metrics));
    }
    const ax = (await cdp.send("Accessibility.getFullAXTree")).nodes || [];
    const axNames = new Set(ax.map((node) => node.name && node.name.value).filter(Boolean));
    check("árvore acessível nomeia CRM", axNames.has("CRM"));
    check("árvore acessível nomeia Automação CRM", axNames.has("Automação CRM"));
    check("árvore acessível nomeia Total de pacientes",
          [...axNames].some((name) =>
            String(name).toLocaleLowerCase("pt-BR").includes("total de pacientes")
          ));

    console.log("── Logout explícito ──");
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1440, height: 900, deviceScaleFactor: 1, mobile: false,
    });
    await cdp.evaluate(
      "document.querySelector('.nav-item[data-section=\"m15-nucleo\"]').click()"
    );
    await waitFor(
      () => cdp.evaluate(
        "document.querySelector('#m15-nucleo')?.classList.contains('active') && " +
        "Boolean(document.querySelector('#m15Sair'))"
      ),
      "botão sair",
    );
    await cdp.evaluate("document.querySelector('#m15Sair').click()");
    await waitFor(
      () => cdp.evaluate("Boolean(document.querySelector('#m15LoginForm'))"),
      "logout",
    );
    const status = await cdp.evaluate(
      `fetch(${JSON.stringify(base + "/api/m15/auth/sessao")}, {
        credentials: "same-origin"
      }).then((response) => response.status)`
    );
    check("sessão não restaura depois do logout", status === 401, String(status));

    if (failures) {
      children.forEach((proc) => {
        const log = proc.getLog();
        if (log) console.error(`── log sintético ${path.basename(proc.spawnfile)} ──\n${log}`);
      });
      throw new Error(`${failures} falha(s) de browser/E2E`);
    }
    console.log("RESULTADO: todos os casos passaram.");
  } finally {
    if (cdp) cdp.close();
    children.reverse().forEach(stop);
    await new Promise((resolve) => setTimeout(resolve, 400));
    fs.rmSync(temp, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(`RESULTADO: FALHOU — ${error.message}`);
  process.exit(1);
});
