#!/usr/bin/env node
/* M68 — cadastro assistido por CPF + nascimento, no navegador de verdade.
 *
 * Mesmo arnês da M67 (sem dependência npm): SQLite descartável + API + proxy
 * em portas loopback efêmeras e Chrome headless via DevTools Protocol. Só
 * dados sintéticos; o banco temporário é apagado ao final.
 *
 * O SERPRO NUNCA é chamado: a API sobe com a integração DESLIGADA (o padrão)
 * e, para os cenários de resposta oficial, a rota INTERNA
 * /pessoas/identificacao-assistida é respondida pelo próprio teste via
 * Fetch.requestPaused do DevTools. Nenhum gancho de teste no código de
 * produção, nenhuma rede externa.
 *
 * Prova: ordem CPF → Nascimento → Nome (desktop e celular), CPF inválido sem
 * consulta, paciente existente com "Usar este paciente", orientação de
 * nascimento, integração desligada sem bloquear o salvar, preenchimento do
 * nome oficial, nome social separado, não correspondência, edição sinalizada,
 * invalidação por troca de CPF/nascimento, uma consulta por par, CPF e
 * nascimento fora de toda URL, zero rota fiscal.
 *
 * Uso:
 *   M15_TEST_PYTHON=/caminho/venv/bin/python \
 *   [M68_SCREENSHOT_DIR=/pasta/para/capturas] \
 *     node painel-soprolife/scripts/test-m68-browser-e2e.js
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
const EMAIL = "admin-m68@teste.local";
const PASSWORD = "senha-m68-sintetica-123";

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

const SHOTS = process.env.M68_SCREENSHOT_DIR || "";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  if (!PYTHON || !fs.existsSync(PYTHON)) {
    throw new Error("defina M15_TEST_PYTHON para o Python do venv M15");
  }
  if (!fs.existsSync(CHROME)) throw new Error(`Chrome não encontrado: ${CHROME}`);
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m68-e2e-"));
  const dbPath = path.join(temp, "m68-e2e.db");
  const profile = path.join(temp, "chrome-profile");
  const apiPort = await freePort();
  const panelPort = await freePort();
  const chromePort = await freePort();
  const commonEnv = {
    ...process.env,
    PYTHONDONTWRITEBYTECODE: "1",
    M15_ENV: "dev",
    M15_DATABASE_URL: `sqlite:///${dbPath}`,
    M15_API_HOST: "127.0.0.1",
    M15_API_PORT: String(apiPort),
    M15_AUTH_SECRET: "m68-browser-e2e-secret-32-chars-synthetic-only",
    M15_MARKETING_REFRESH_QUEUE: path.join(temp, "marketing-request.json"),
  };
  const alembic = path.join(path.dirname(PYTHON), "alembic");
  const children = [];
  let cdp;

  try {
    for (const [command, args, extraEnv] of [
      [alembic, ["upgrade", "head"], {}],
      [PYTHON, ["-m", "app.cli", "criar-usuario", "--email", EMAIL,
        "--nome", "Teste M68", "--papel", "admin"], { M15_NOVA_SENHA: PASSWORD }],
    ]) {
      const result = childProcess.spawnSync(command, args, {
        cwd: M15_DIR, env: { ...commonEnv, ...extraEnv }, encoding: "utf8",
      });
      if (result.status !== 0) {
        throw new Error(`${path.basename(command)} falhou: ${
          (result.stderr || result.stdout || "").slice(-2000)}`);
      }
    }
    children.push(spawnLogged(PYTHON, ["-m", "app.serve"], { cwd: M15_DIR, env: commonEnv }));
    children.push(spawnLogged("python3", [PROXY], {
      cwd: ROOT,
      env: {
        ...process.env,
        PYTHONDONTWRITEBYTECODE: "1",
        SOPROLIFE_PANEL_HOST: "127.0.0.1",
        SOPROLIFE_PANEL_PORT: String(panelPort),
        SOPROLIFE_M15_UPSTREAM: `http://127.0.0.1:${apiPort}/api/v1`,
      },
    }));
    const base = `http://127.0.0.1:${panelPort}/painel-soprolife`;
    await waitFor(async () => (await fetch(`${base}/api/m15/health`)).ok, "API/proxy local");

    children.push(spawnLogged(CHROME, [
      "--headless=new", "--disable-gpu", "--disable-background-networking",
      "--disable-component-update", "--no-first-run", "--no-default-browser-check",
      `--remote-debugging-port=${chromePort}`, `--user-data-dir=${profile}`, "about:blank",
    ], { cwd: ROOT, env: process.env }));
    await waitFor(async () => {
      const r = await fetch(`http://127.0.0.1:${chromePort}/json/version`);
      return r.ok;
    }, "Chrome DevTools");
    const target = await (await fetch(
      `http://127.0.0.1:${chromePort}/json/new?${encodeURIComponent(base + "/")}`,
      { method: "PUT" })).json();
    cdp = await Cdp.connect(target.webSocketDebuggerUrl);
    await Promise.all([cdp.send("Page.enable"), cdp.send("Runtime.enable"),
      cdp.send("Network.enable"), cdp.send("Accessibility.enable")]);

    // Toda URL pedida pela página, para provar que nada fiscal foi chamado.
    const requested = [];
    cdp.ws.addEventListener("message", (event) => {
      const msg = JSON.parse(String(event.data));
      if (msg.method === "Network.requestWillBeSent") {
        requested.push({ method: msg.params.request.method, url: msg.params.request.url });
      }
    });

    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
    });
    // Portão de autenticação atual (login.html, M25.23): o painel só carrega
    // depois do login real, que redireciona de volta para ele.
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('#loginForm'))"), "portão de login");
    await cdp.evaluate(`(() => {
      document.querySelector("#email").value = ${JSON.stringify(EMAIL)};
      document.querySelector("#password").value = ${JSON.stringify(PASSWORD)};
      document.querySelector("#loginForm").requestSubmit();
    })()`);
    await waitFor(() => cdp.evaluate(
      "Boolean(window.SoproM15 && window.SoproCentral && window.SoproM15.hasToken())"), "login concluído")
      .catch(async (err) => {
        const cookies = ((await cdp.send("Network.getCookies", { urls: [base + "/"] })).cookies || [])
          .map((c) => `${c.name}(path=${c.path},secure=${c.secure})`);
        throw new Error(err.message + " — página: " + await cdp.evaluate(
          "location.href + ' | erro: ' + ((document.querySelector('#loginErro') || {}).textContent || '')") +
          " | cookies: " + cookies.join(","));
      });


    // ── SERPRO simulado na rota INTERNA (nunca o SERPRO de verdade) ────────
    // null → a requisição segue para a API local (integração desligada).
    let mock = null;
    const identReqs = [];
    const postDatas = [];
    cdp.ws.addEventListener("message", (event) => {
      const msg = JSON.parse(String(event.data));
      if (msg.method === "Network.requestWillBeSent" && msg.params.request.postData) {
        postDatas.push({ url: msg.params.request.url, body: msg.params.request.postData });
      }
      if (msg.method !== "Fetch.requestPaused") return;
      const { requestId, request } = msg.params;
      identReqs.push({ url: request.url, method: request.method, body: request.postData || "" });
      if (!mock) { cdp.send("Fetch.continueRequest", { requestId }); return; }
      cdp.send("Fetch.fulfillRequest", {
        requestId, responseCode: mock.status || 200,
        responseHeaders: [{ name: "Content-Type", value: "application/json" }],
        body: Buffer.from(JSON.stringify(mock.body)).toString("base64"),
      });
    });
    await cdp.send("Fetch.enable", { patterns: [{ urlPattern: "*identificacao-assistida*", requestStage: "Request" }] });

    // CPFs sintéticos (verificadores calculados, sem dono conhecido).
    const cpf = (b) => {
      const dv = (d, p) => { let s = 0; for (let i = 0; i < d.length; i++) s += Number(d[i]) * (p - i);
        const r = (s * 10) % 11; return r === 10 ? 0 : r; };
      const d1 = dv(b, 10); return b + d1 + dv(b + d1, 11);
    };
    const CPF_EXISTE = cpf("713204859");
    const CPF_NOVO = cpf("284017395");
    const CPF_M1 = cpf("590318462");
    const CPF_M2 = cpf("406128357");
    const CPF_M3 = cpf("839105274");
    const CPF_INVALIDO = CPF_NOVO.slice(0, 10) + String((Number(CPF_NOVO[10]) + 1) % 10);
    const TODOS_CPFS = [CPF_EXISTE, CPF_NOVO, CPF_M1, CPF_M2, CPF_M3, CPF_INVALIDO];
    const oficial = (extra) => ({ body: Object.assign({
      resultado: "confere", mensagem: "Nome confirmado na Receita Federal via SERPRO.",
      nome_oficial: "MARINA COSTA RIBEIRO", nome_social: null,
      situacao: { codigo: "0", descricao: "Regular" }, parcial: false,
      comprovante: "v1.0.sintetico.sintetico" }, extra || {}) });

    await cdp.evaluate(`(async () => {
      await window.SoproM15.api("/pessoas", { method: "POST", body: JSON.stringify({
        nome_completo: "Paciente Existente 001", cpf: ${JSON.stringify(CPF_EXISTE)},
        data_nascimento: "1980-02-03", contatos: [] }) });
      window.SoproCentral.open("atendimento", { tipo: "espirometria_soprolife" });
    })()`);
    await waitFor(() => cdp.evaluate(
      "Boolean(document.querySelector('#cadAtNfse') && !document.querySelector('#cadAtNfse').hidden && document.querySelector('#cadAtPIdentStatus'))"),
      "Novo atendimento com identificação assistida");
    await sleep(300);

    await cdp.evaluate(`(() => {
      const form = () => document.querySelector("#cadFormAtend");
      const lbl = (n) => form().elements["cadAtP_" + n].closest("label");
      window.__m68 = {
        set(name, value) {
          const el = form().elements[name];
          const alvo = el.type === "hidden"
            ? el.closest(".m15-date").querySelector("input:not([type=hidden])") : el;
          alvo.value = value;
          alvo.dispatchEvent(new Event("input", { bubbles: true }));
          alvo.dispatchEvent(new Event("change", { bubbles: true }));
          alvo.dispatchEvent(new Event("focusout", { bubbles: true }));
        },
        status() {
          const s = document.querySelector("#cadAtPIdentStatus");
          return { hidden: s.hidden, tom: s.getAttribute("data-tom"), texto: s.textContent.replace(/\\s+/g, " ").trim(),
            alterado: !!s.querySelector("[data-ident-alterado]") && !s.querySelector("[data-ident-alterado]").hidden };
        },
        existente() {
          const b = document.querySelector("#cadAtPCpfExistente");
          return { hidden: b.hidden, texto: b.textContent.replace(/\\s+/g, " ").trim() };
        },
        nome() { return form().elements.cadAtP_nome.value; },
        ordem() {
          const nomes = ["cpf", "nasc", "nome", "fone", "email", "sexo", "consent"];
          const labels = nomes.map(lbl);
          const dom = labels.every((l, i) => i === 0 ||
            (labels[i - 1].compareDocumentPosition(l) & Node.DOCUMENT_POSITION_FOLLOWING));
          const r = Object.fromEntries(nomes.map((n, i) => {
            const b = labels[i].getBoundingClientRect(); return [n, { top: Math.round(b.top), left: Math.round(b.left) }];
          }));
          return { dom, r };
        },
        selo(n) { return !!lbl(n).querySelector(".cad-nfse-selo"); },
        nfse() {
          return [...document.querySelectorAll("#cadAtNfse .cad-nfse-item.is-pendente")]
            .map((li) => li.getAttribute("data-nfse-item"));
        },
      };
    })()`);
    const st = () => cdp.evaluate("window.__m68.status()");
    const set = (n, v) => cdp.evaluate(`__m68.set(${JSON.stringify(n)}, ${JSON.stringify(v)})`);
    const esperarTexto = (t, label) => waitFor(async () => (await st()).texto.includes(t), label || t, 8000);

    async function shot(nome, seletor) {
      if (!SHOTS) return;
      const rect = await cdp.evaluate(`(() => {
        const el = document.querySelector(${JSON.stringify(seletor)});
        el.scrollIntoView({ block: "start" });
        const r = el.getBoundingClientRect();
        return { x: r.left + window.scrollX, y: r.top + window.scrollY, width: r.width, height: r.height };
      })()`);
      await sleep(150);
      const { data } = await cdp.send("Page.captureScreenshot", {
        format: "png", captureBeyondViewport: true,
        clip: { x: Math.max(0, rect.x - 8), y: Math.max(0, rect.y - 8),
                width: rect.width + 16, height: rect.height + 16, scale: 1 },
      });
      fs.writeFileSync(path.join(SHOTS, nome + ".png"), Buffer.from(data, "base64"));
      console.log(`  [captura] ${nome}.png`);
    }

    const inicio = requested.length;

    console.log("── 1/2. Ordem e selos (desktop 1440) ──");
    let o = await cdp.evaluate("__m68.ordem()");
    check("1. ordem no DOM: CPF → Nascimento → Nome → WhatsApp → E-mail → Sexo → Consentimento", o.dom);
    check("desktop: CPF, Nascimento e Nome na mesma linha, nessa ordem",
      Math.abs(o.r.cpf.top - o.r.nasc.top) < 4 && Math.abs(o.r.cpf.top - o.r.nome.top) < 12 &&
      o.r.cpf.left < o.r.nasc.left && o.r.nasc.left < o.r.nome.left, JSON.stringify(o.r));
    check("desktop: contatos na linha de baixo", o.r.fone.top > o.r.nome.top + 20);
    check("selo NFS-e em CPF e Nome; Nascimento sem selo",
      await cdp.evaluate("__m68.selo('cpf') && __m68.selo('nome') && !__m68.selo('nasc')"));
    check("nota 'Usados para consulta oficial de identificação' visível",
      await cdp.evaluate(`(() => { const n = document.querySelector("#cadAtPIdentNota");
        return n && n.offsetParent !== null && n.textContent.includes("Usados para consulta oficial de identificação"); })()`));
    await shot("01-desktop-ordem-cpf-nascimento-nome", "#cadAtPNovaBox");

    console.log("── 3. CPF inválido ──");
    let i0 = identReqs.length, r0 = requested.length;
    await set("cadAtP_cpf", CPF_INVALIDO);
    await sleep(1100);
    let s = await st();
    check("3. CPF inválido → 'CPF inválido.'", s.texto === "CPF inválido." && s.tom === "erro", JSON.stringify(s));
    check("3. nenhuma busca local nem consulta oficial",
      identReqs.length === i0 && !requested.slice(r0).some((r) => /busca-cpf|identificacao-assistida/.test(r.url)));

    console.log("── 4. CPF já cadastrado ──");
    i0 = identReqs.length;
    await set("cadAtP_cpf", CPF_EXISTE);
    await waitFor(async () => !(await cdp.evaluate("__m68.existente()")).hidden, "aviso de paciente existente", 8000);
    const ex = await cdp.evaluate("__m68.existente()");
    check("4. 'Paciente já cadastrado' com PES, nome e nascimento",
      ex.texto.includes("Paciente já cadastrado") && /PES-\d+/.test(ex.texto) &&
      ex.texto.includes("Paciente Existente 001") && ex.texto.includes("03/02/1980"), ex.texto);
    check("4. SERPRO não consultado para CPF existente", identReqs.length === i0);
    await set("cadAtP_nasc", "14/11/1970");
    await sleep(1100);
    check("4. informar nascimento de CPF existente também não consulta", identReqs.length === i0);
    await shot("02-desktop-cpf-existente", "#cadAtPNovaBox");
    await cdp.evaluate(`document.querySelector("#cadAtPCpfExistente [data-usar-existente]").click()`);
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.cad-cartao-pessoa'))"), "cartão do paciente");
    check("4. 'Usar este paciente' seleciona o cadastro existente (sem duplicar)",
      await cdp.evaluate(`document.querySelector(".cad-cartao-pessoa").textContent.includes("Paciente Existente 001") &&
        document.querySelector("#cadAtPNovaBox").hidden`));
    await cdp.evaluate(`document.querySelector("#cadAtPTrocar").click()`);
    await set("cadAtP_cpf", ""); await set("cadAtP_nasc", "");
    await sleep(300);

    console.log("── 5. CPF novo sem nascimento ──");
    i0 = identReqs.length;
    await set("cadAtP_cpf", CPF_NOVO);
    await esperarTexto("CPF válido — informe a data de nascimento para consultar o cadastro oficial.");
    check("5. orientação discreta e nenhuma consulta oficial", identReqs.length === i0 && (await st()).tom === "info");
    await shot("03-desktop-cpf-novo-aguardando-nascimento", "#cadAtPNovaBox");

    console.log("── 6/25. Integração desligada (API real) ──");
    await set("cadAtP_nasc", "14/11/1970");
    await esperarTexto("Consulta oficial indisponível — preencha o nome manualmente.");
    s = await st();
    check("6. mensagem de indisponível, sem tom de erro", s.tom === "info", JSON.stringify(s));
    check("6. uma única chamada interna, POST", identReqs.length === i0 + 1 && identReqs[identReqs.length - 1].method === "POST");
    await shot("04-desktop-integracao-desligada", "#cadAtPNovaBox");
    await cdp.evaluate(`(() => {
      __m68.set("cadAtP_nome", "Nome Digitado Manualmente");
      __m68.set("esp_data", "01/09/2026");
      __m68.set("esp_modalidade", "residencial");
      __m68.set("esp_pgto_data", "01/09/2026");
    })()`);
    await sleep(200);
    await cdp.evaluate(`document.querySelector('#cadFormAtend').requestSubmit()`);
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.cad-sucesso'))"), "atendimento salvo", 15000);
    check("25. salvar manualmente com a consulta oficial indisponível",
      await cdp.evaluate("document.querySelector('.cad-sucesso').textContent.includes('Nome Digitado Manualmente')"));
    check("após salvar, o formulário volta limpo (status escondido)", (await st()).hidden);
    check("o paciente salvo agora é achado pela busca exata por CPF",
      await cdp.evaluate(`window.SoproM15.api("/pessoas/busca-cpf", { method: "POST",
        body: JSON.stringify({ cpf: ${JSON.stringify(CPF_NOVO)} }) }).then((r) => r.encontrada)`));
    await cdp.evaluate(`document.querySelector(".cad-sucesso").remove()`);

    console.log("── 7. Sucesso (resposta oficial simulada) ──");
    mock = oficial();
    i0 = identReqs.length;
    await set("esp_data", "01/09/2026"); await set("esp_modalidade", "residencial");
    await set("cadAtP_cpf", CPF_M1);
    await set("cadAtP_nasc", "14/11/1970");
    await waitFor(async () => (await st()).tom === "ok", "confirmação oficial", 8000);
    s = await st();
    check("7. nome preenchido com o nome oficial", (await cdp.evaluate("__m68.nome()")) === "MARINA COSTA RIBEIRO");
    check("7. 'Nome confirmado na Receita Federal via SERPRO.' + situação cadastral",
      s.texto.includes("Nome confirmado na Receita Federal via SERPRO.") && s.texto.includes("Situação cadastral: Regular"), s.texto);
    check("M67: prontidão NFS-e reage ao nome preenchido (identidade sem pendência)",
      !(await cdp.evaluate("__m68.nfse()")).includes("identidade"), JSON.stringify(await cdp.evaluate("__m68.nfse()")));
    await shot("05-desktop-nome-confirmado", "#cadAtPNovaBox");
    await cdp.evaluate(`document.querySelector("#cadFormAtend").click()`);
    await cdp.evaluate(`document.querySelector("#cadFormAtend .cad-passo-titulo").click()`);
    await sleep(1000);
    check("17. eventos repetidos no mesmo par → uma consulta só", identReqs.length === i0 + 1, String(identReqs.length - i0));

    await set("cadAtP_nome", "Marina C. Ribeiro");
    await sleep(150);
    check("edição do nome confirmado é permitida e sinalizada", (await st()).alterado &&
      (await st()).texto.includes("Nome alterado após a confirmação oficial."));
    await shot("06-desktop-nome-alterado", "#cadAtPNovaBox");
    await set("cadAtP_nome", "MARINA COSTA RIBEIRO");
    await sleep(150);
    check("voltar ao nome oficial remove o aviso", !(await st()).alterado);

    console.log("── 9/19. Não correspondência e troca de nascimento ──");
    mock = { body: { resultado: "nao_confere", mensagem: "CPF e data de nascimento não conferem no cadastro oficial." } };
    await set("cadAtP_nasc", "15/11/1970");
    await esperarTexto("CPF e data de nascimento não conferem no cadastro oficial.");
    check("9. mensagem de não correspondência em tom de aviso", (await st()).tom === "aviso");
    check("19. trocar o nascimento invalida o resultado: nome preenchido automaticamente é limpo",
      (await cdp.evaluate("__m68.nome()")) === "");
    await shot("07-desktop-nao-confere", "#cadAtPNovaBox");
    i0 = identReqs.length;
    await set("cadAtP_nasc", "14/11/1970");
    await waitFor(async () => (await st()).tom === "ok", "par anterior de volta", 8000);
    check("par já consultado nesta sessão → sem nova chamada e nome de volta",
      identReqs.length === i0 && (await cdp.evaluate("__m68.nome()")) === "MARINA COSTA RIBEIRO");

    console.log("── 8/18. Nome social e troca de CPF ──");
    mock = oficial({ nome_oficial: "JOANA PEREIRA LIMA", nome_social: "JOANA SOCIAL" });
    await set("cadAtP_cpf", CPF_M2);
    await sleep(50);
    check("18. trocar o CPF invalida o resultado anterior (nome automático limpo)",
      (await cdp.evaluate("__m68.nome()")) === "");
    await waitFor(async () => (await st()).tom === "ok", "confirmação com nome social", 8000);
    s = await st();
    check("8. nome civil no campo; nome social só como informação separada",
      (await cdp.evaluate("__m68.nome()")) === "JOANA PEREIRA LIMA" &&
      s.texto.includes("Nome social no cadastro oficial: JOANA SOCIAL") && s.texto.includes("não foi substituído") &&
      !s.alterado, s.texto);
    await shot("08-desktop-nome-social", "#cadAtPNovaBox");

    console.log("── Nome digitado pelo operador nunca é sobrescrito ──");
    mock = oficial({ nome_oficial: "CARLOS ALBERTO SOUZA" });
    await set("cadAtP_cpf", ""); await sleep(50);
    await set("cadAtP_nome", "Carlos Souza");
    await set("cadAtP_cpf", CPF_M3);
    await waitFor(async () => (await st()).tom === "ok", "confirmação com nome já digitado", 8000);
    check("nome digitado preservado + botão 'Usar nome oficial'",
      (await cdp.evaluate("__m68.nome()")) === "Carlos Souza" &&
      await cdp.evaluate(`Boolean(document.querySelector("#cadAtPIdentStatus [data-usar-oficial]"))`));
    await cdp.evaluate(`document.querySelector("#cadAtPIdentStatus [data-usar-oficial]").click()`);
    check("'Usar nome oficial' aplica o nome", (await cdp.evaluate("__m68.nome()")) === "CARLOS ALBERTO SOUZA");

    console.log("── 10/11. Indisponível ──");
    mock = { status: 500, body: { erro: { codigo: "interno", mensagem: "Erro interno." } } };
    await set("cadAtP_nasc", "01/01/1980");
    await esperarTexto("Não foi possível consultar o cadastro oficial agora. Você pode preencher o nome manualmente e continuar.");
    check("11. falha → aviso, sem bloquear", (await st()).tom === "aviso");
    mock = { body: { resultado: "indisponivel",
      mensagem: "Não foi possível consultar o cadastro oficial agora. Você pode preencher o nome manualmente e continuar." } };
    await set("cadAtP_nasc", "02/01/1980");
    await esperarTexto("Não foi possível consultar o cadastro oficial agora.");
    check("11. 'indisponivel' do servidor → mesma mensagem", (await st()).tom === "aviso");
    await shot("09-desktop-indisponivel", "#cadAtPNovaBox");

    console.log("── Comprovante vai no salvar ──");
    mock = oficial();
    await set("cadAtP_nome", "");
    await set("cadAtP_cpf", CPF_M1);
    await set("cadAtP_nasc", "14/11/1970");
    await waitFor(async () => (await st()).tom === "ok", "confirmação para salvar", 8000);
    const antesSalvar = postDatas.length;
    await cdp.evaluate(`document.querySelector('#cadFormAtend').requestSubmit()`);
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.cad-sucesso'))"), "salvo com confirmação", 15000);
    const envio = postDatas.slice(antesSalvar).find((p) => /atendimentos\/novo-paciente/.test(p.url));
    check("o salvar leva o comprovante opaco (não o nome devolvido)",
      envio && JSON.parse(envio.body).pessoa.identificacao_oficial === "v1.0.sintetico.sintetico");
    await cdp.evaluate(`document.querySelector(".cad-sucesso").remove()`);

    console.log("── Celular e tablet ──");
    for (const width of [390, 820, 1440]) {
      await cdp.send("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: width <= 420 });
      await sleep(250);
      const m = await cdp.evaluate(`({ inner: window.innerWidth, body: document.body.scrollWidth, root: document.documentElement.scrollWidth })`);
      check(`2. viewport ${width}px sem overflow horizontal`, m.body <= m.inner && m.root <= m.inner, JSON.stringify(m));
      o = await cdp.evaluate("__m68.ordem()");
      // Ordem de leitura: mesma linha (±16 px, o grupo CPF+Nascimento tem
      // respiro interno) da esquerda para a direita; senão, de cima para baixo.
      const seq = ["cpf", "nasc", "nome", "fone", "email", "sexo", "consent"].map((n) => o.r[n]);
      const depois = (a, b) => Math.abs(a.top - b.top) <= 16 ? b.left > a.left : b.top > a.top;
      check(`2. viewport ${width}px: ordem de leitura CPF → Nascimento → Nome → contatos`,
        seq.every((v, i) => i === 0 || depois(seq[i - 1], v)), JSON.stringify(o.r));
      if (width === 390) {
        await set("cadAtP_cpf", CPF_EXISTE);
        await waitFor(async () => !(await cdp.evaluate("__m68.existente()")).hidden, "existente no celular", 8000);
        await shot("10-mobile-cpf-existente", "#cadAtPNovaBox");
        mock = oficial({ nome_social: "MARINA SOCIAL" });
        await set("cadAtP_nome", "");
        await set("cadAtP_cpf", CPF_M2);
        await set("cadAtP_nasc", "20/05/1985");
        await waitFor(async () => (await st()).tom === "ok", "confirmação no celular", 8000);
        const m2 = await cdp.evaluate(`({ inner: window.innerWidth, body: document.body.scrollWidth })`);
        check("celular: status confirmado sem overflow", m2.body <= m2.inner, JSON.stringify(m2));
        await shot("11-mobile-nome-confirmado", "#cadAtPNovaBox");
      }
    }

    console.log("── 13/14/15. URLs, segredos e rotas fiscais ──");
    const urls = requested.slice(inicio).map((r) => r.url);
    const pii = [...TODOS_CPFS, "1970-11-14", "14/11/1970", "14111970", "1980-02-03"];
    check("13/14. nenhum CPF nem nascimento em URL alguma",
      urls.every((u) => pii.every((p) => !decodeURIComponent(u).includes(p))),
      JSON.stringify(urls.filter((u) => pii.some((p) => decodeURIComponent(u).includes(p)))));
    check("consultas internas sempre POST com os dados no corpo",
      identReqs.every((r) => r.method === "POST" && /"cpf"/.test(r.body) && !/\d{11}/.test(r.url)));
    const ehFiscal = (r) => /\/api\/m15\/(fiscal|nfse)\b|sefin|nfse\.gov/i.test(r.url);
    check("23. nenhuma escrita em rota fiscal / SEFIN",
      requested.filter((r) => ehFiscal(r) && r.method !== "GET").length === 0 &&
      requested.filter((r) => /sefin|nfse\.gov/i.test(r.url)).length === 0);
    check("nenhuma chamada do navegador ao SERPRO",
      requested.filter((r) => /serpro/i.test(r.url)).length === 0);
    const externos = requested.filter((r) =>
      !/^(http:\/\/127\.0\.0\.1|data:|about:|chrome|blob:)/.test(r.url) &&
      !/^https:\/\/fonts\.(googleapis|gstatic)\.com\//.test(r.url));
    check("fora do loopback, só as fontes que o painel já usava", externos.length === 0,
      JSON.stringify(externos.slice(0, 5)));
    const apiLog = children[0].getLog();
    check("16. log da API local sem CPF nem nascimento",
      pii.every((p) => !apiLog.includes(p)), apiLog.slice(-400));

    console.log(`RESULTADO: ${failures === 0 ? "OK" : "FALHOU"} — ${failures} falha(s)`);
    process.exitCode = failures === 0 ? 0 : 1;
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
