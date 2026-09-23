#!/usr/bin/env node
/* M67 — prontidão para NFS-e no "Novo atendimento", no navegador de verdade.
 *
 * Mesmo arnês da M21 (sem dependência npm): SQLite descartável + API + proxy
 * em portas loopback efêmeras e Chrome headless via DevTools Protocol. Só
 * dados sintéticos/fictícios; o banco temporário é apagado ao final. Nenhuma
 * rede externa, nenhum SEFIN, nenhum dado de produção.
 *
 * Prova: selos NFS-e condicionais, painel de prontidão reagindo ao
 * preenchimento, Pastore só com a explicação, consulta pura sem nada, paciente
 * existente julgado pelo servidor, salvar incompleto permitido, nenhuma
 * requisição a rota fiscal, sem overflow em desktop/celular.
 *
 * Uso:
 *   M15_TEST_PYTHON=/caminho/venv/bin/python \
 *   [M67_SCREENSHOT_DIR=/pasta/para/capturas] \
 *     node painel-soprolife/scripts/test-m67-browser-e2e.js
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
const EMAIL = "admin-m67@teste.local";
const PASSWORD = "senha-m67-sintetica-123";

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

const SHOTS = process.env.M67_SCREENSHOT_DIR || "";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  if (!PYTHON || !fs.existsSync(PYTHON)) {
    throw new Error("defina M15_TEST_PYTHON para o Python do venv M15");
  }
  if (!fs.existsSync(CHROME)) throw new Error(`Chrome não encontrado: ${CHROME}`);
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m67-e2e-"));
  const dbPath = path.join(temp, "m67-e2e.db");
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
    M15_AUTH_SECRET: "m67-browser-e2e-secret-32-chars-synthetic-only",
    M15_MARKETING_REFRESH_QUEUE: path.join(temp, "marketing-request.json"),
  };
  const alembic = path.join(path.dirname(PYTHON), "alembic");
  const children = [];
  let cdp;

  try {
    for (const [command, args, extraEnv] of [
      [alembic, ["upgrade", "head"], {}],
      [PYTHON, ["-m", "app.cli", "criar-usuario", "--email", EMAIL,
        "--nome", "Teste M67", "--papel", "admin"], { M15_NOVA_SENHA: PASSWORD }],
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

    // Pastore sintética (mesmo preparo da M22) e um paciente existente SEM CPF.
    await cdp.evaluate(`(async () => {
      const api = window.SoproM15.api;
      const partner = await api("/parceiros", { method: "POST",
        body: JSON.stringify({ nome: "Pastore", tipo: "clinica", status: "ativa" }) });
      await api("/unidades", { method: "POST",
        body: JSON.stringify({ partner_id: partner.id, nome: "Pastore Ipanema" }) });
      await api("/pessoas", { method: "POST",
        body: JSON.stringify({ nome_completo: "Rafael Moreira Lima", contatos: [] }) });
      window.SoproCentral.open("atendimento", { tipo: "espirometria_soprolife" });
    })()`);
    await waitFor(() => cdp.evaluate(
      "Boolean(document.querySelector('#cadAtNfse') && !document.querySelector('#cadAtNfse').hidden)"),
      "painel de prontidão");
    await sleep(300);

    // Helpers no contexto da página.
    await cdp.evaluate(`(() => {
      const form = () => document.querySelector("#cadFormAtend");
      window.__m67 = {
        set(name, value) {
          const el = form().elements[name];
          const alvo = el.type === "hidden"
            ? el.closest(".m15-date").querySelector("input:not([type=hidden])") : el;
          alvo.value = value;
          alvo.dispatchEvent(new Event("input", { bubbles: true }));
          alvo.dispatchEvent(new Event("change", { bubbles: true }));
        },
        tipo(v) {
          const r = form().querySelector('input[name="tipo"][value="' + v + '"]');
          r.checked = true;
          r.dispatchEvent(new Event("change", { bubbles: true }));
        },
        estado() {
          const box = document.querySelector("#cadAtNfse");
          return {
            hidden: box.hidden,
            titulo: document.querySelector("#cadAtNfseEstado").textContent,
            pendentes: [...box.querySelectorAll(".cad-nfse-item.is-pendente")]
              .map((li) => li.getAttribute("data-nfse-item")),
            faltas: [...box.querySelectorAll(".cad-nfse-faltas li")].map((li) => li.textContent),
            itens: box.querySelectorAll(".cad-nfse-item").length,
            nota: document.querySelector("#cadAtNfseNota").textContent,
          };
        },
        selosVisiveis() {
          return [...form().querySelectorAll(".cad-nfse-selo")]
            .filter((b) => b.offsetParent !== null)
            .map((b) => b.closest("label").querySelector(".m15-field-label").firstChild.textContent.trim());
        },
      };
    })()`);
    const estado = () => cdp.evaluate("window.__m67.estado()");
    const settle = () => sleep(120);

    async function shot(nome, seletor) {
      if (!SHOTS) return;
      const rect = await cdp.evaluate(`(() => {
        const el = document.querySelector(${JSON.stringify(seletor)});
        el.scrollIntoView({ block: "start" });
        const r = el.getBoundingClientRect();
        return { x: r.left + window.scrollX, y: r.top + window.scrollY,
                 width: r.width, height: r.height };
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

    // Daqui em diante é só o formulário. O que veio antes é o boot do painel
    // (inclui os GETs somente-leitura do módulo Fiscal já existente).
    const inicioFormulario = requested.length;

    console.log("── Espirometria SoproLife: selos e estado inicial ──");
    const esperados = ["Nome completo", "CPF", "Data do exame", "Status", "Broncodilatador",
      "Município onde o exame foi realizado", "Modalidade", "Valor da espirometria (R$)",
      "Status do pagamento"];
    const selos = await cdp.evaluate("window.__m67.selosVisiveis()");
    check("1. selo NFS-e visível exatamente nos nove campos fiscais",
          JSON.stringify([...selos].sort()) === JSON.stringify([...esperados].sort()),
          JSON.stringify(selos));
    let e = await estado();
    check("formulário vazio → 'Faltam dados para NFS-e'", e.titulo === "Faltam dados para NFS-e");
    check("seis itens de prontidão", e.itens === 6, String(e.itens));
    check("frases humanas, nenhum código técnico",
          e.faltas.includes("CPF necessário para emissão da NFS-e") &&
          e.faltas.includes("Informe o município onde o exame foi realizado") &&
          !e.faltas.join(" ").match(/recipient_|service_location|financial_entry/));
    const a11y = await cdp.evaluate(`(() => {
      const cpf = document.querySelector('[name="cadAtP_cpf"]');
      const ids = (cpf.getAttribute("aria-describedby") || "").split(/\\s+/).filter(Boolean);
      const selo = cpf.closest("label").querySelector(".cad-nfse-selo");
      return {
        descricao: ids.map((id) => document.getElementById(id)?.textContent || "").join(" "),
        seloNome: selo.getAttribute("aria-label"),
        seloTexto: selo.textContent.trim(),
        req: Boolean(cpf.closest("label").querySelector(".cad-req")),
        nomeReq: Boolean(document.querySelector('[name="cadAtP_nome"]').closest("label").querySelector(".cad-req")),
      };
    })()`);
    check("CPF descrito por 'Obrigatório para emissão da NFS-e da SoproLife.'",
          a11y.descricao.includes("Obrigatório para emissão da NFS-e da SoproLife."), a11y.descricao);
    check("selo com texto visível e nome acessível",
          a11y.seloTexto === "NFS-e" && a11y.seloNome === "Obrigatório para NFS-e");
    check("CPF continua SEM asterisco de salvar; Nome mantém o asterisco",
          !a11y.req && a11y.nomeReq);
    await cdp.evaluate(`document.querySelector('[name="cadAtP_cpf"]').closest("label").querySelector(".cad-nfse-selo").click()`);
    check("selo abre o tooltip por clique/toque",
          await cdp.evaluate(`(() => { const s = document.querySelector('[name="cadAtP_cpf"]').closest("label").querySelector(".cad-nfse-selo");
            return s.getAttribute("aria-expanded") === "true" && !document.getElementById(s.getAttribute("aria-describedby")).hidden; })()`));
    check("balão do selo aberto não é cortado pelo rótulo",
          await cdp.evaluate(`(() => {
            const s = document.querySelector('[name="cadAtP_cpf"]').closest("label").querySelector(".cad-nfse-selo");
            const b = document.getElementById(s.getAttribute("aria-describedby")).getBoundingClientRect();
            const l = s.closest(".m15-field-label").getBoundingClientRect();
            return b.height > 20 && b.bottom > l.bottom &&
              getComputedStyle(s.closest(".m15-field-label")).overflow === "visible";
          })()`));
    await shot("01-desktop-selo-tooltip", "#cadAtPNovaBox");
    await shot("01b-desktop-tipos", "#cadAtPasso2");
    await cdp.evaluate(`document.querySelector("#cadFormAtend .cad-passo-titulo").click()`);
    check("clicar fora fecha o tooltip do selo",
          await cdp.evaluate(`document.querySelectorAll('.cad-nfse-selo[aria-expanded="true"]').length === 0`));
    await shot("02-desktop-soprolife-incompleto", "#cadFormAtend");

    console.log("── Preenchimento reage ao vivo ──");
    await cdp.evaluate(`(() => {
      __m67.set("cadAtP_nome", "Marina Costa Ribeiro");
      __m67.set("cadAtP_cpf", "12345678909");
      __m67.set("esp_data", "01/09/2026");
      __m67.set("esp_municipio", "3304557");
      __m67.set("esp_modalidade", "residencial");
    })()`);
    await settle();
    e = await estado();
    check("tudo preenchido → 'Dados fiscais completos'", e.titulo === "Dados fiscais completos",
          JSON.stringify(e.faltas));
    check("CPF mascarado na tela (123.456.789-09)",
          await cdp.evaluate(`document.querySelector('[name="cadAtP_cpf"]').value === "123.456.789-09"`));
    await shot("03-desktop-soprolife-completo", "#cadAtNfse");

    const ajustes = [
      ["Niterói aceito", "esp_municipio", "3303302", null],
      ["Cowork (DIRECT) aceito", "esp_modalidade", "cowork", null],
      ["broncodilatador indefinido", "esp_bd", "", "broncodilatador"],
      ["pagamento Pendente", "esp_pgto_status", "Pendente", "receita"],
      ["valor vazio", "esp_valor", "", "receita"],
      ["valor zero", "esp_valor", "0,00", "receita"],
      ["CPF com dígito errado", "cadAtP_cpf", "12345678900", "identidade"],
      ["nome de uma palavra", "cadAtP_nome", "Marina", "identidade"],
      ["data só mês/ano", "esp_data", "09/2026", "servico"],
      ["exame Aguardando", "esp_status", "Aguardando", "servico"],
    ];
    const restaurar = { esp_municipio: "3304557", esp_modalidade: "residencial", esp_bd: "false",
      esp_pgto_status: "Recebido", esp_valor: "220,00", cadAtP_cpf: "12345678909",
      cadAtP_nome: "Marina Costa Ribeiro", esp_data: "01/09/2026", esp_status: "Realizado" };
    for (const [rotulo, campo, valor, itemPendente] of ajustes) {
      await cdp.evaluate(`__m67.set(${JSON.stringify(campo)}, ${JSON.stringify(valor)})`);
      await settle();
      e = await estado();
      if (itemPendente) {
        check(`${rotulo} → incompleto em "${itemPendente}"`,
              e.titulo === "Faltam dados para NFS-e" && e.pendentes.includes(itemPendente),
              JSON.stringify(e.pendentes));
        if (campo === "esp_pgto_status") {
          check("mensagem exata do pagamento",
                e.faltas.includes("O pagamento precisa estar como Recebido para ficar elegível à NFS-e"));
          await shot("04-desktop-pagamento-pendente", "#cadAtNfse");
        }
      } else {
        check(`${rotulo} → continua completo`, e.titulo === "Dados fiscais completos",
              JSON.stringify(e.faltas));
      }
      await cdp.evaluate(`__m67.set(${JSON.stringify(campo)}, ${JSON.stringify(restaurar[campo])})`);
      await settle();
    }
    await cdp.evaluate(`(() => { __m67.set("esp_pgto_data", ""); __m67.set("esp_pgto_forma", ""); })()`);
    await settle();
    e = await estado();
    check("12/13. sem data de recebimento e sem forma de pagamento → continua completo",
          e.titulo === "Dados fiscais completos", JSON.stringify(e.faltas));

    console.log("── Consulta pura e Pastore ──");
    await cdp.evaluate(`__m67.tipo("consulta_soprolife")`);
    await settle();
    e = await estado();
    check("consulta pura: painel escondido", e.hidden === true);
    check("consulta pura: nenhum selo NFS-e visível",
          (await cdp.evaluate("window.__m67.selosVisiveis()")).length === 0);

    await cdp.evaluate(`__m67.tipo("espirometria_pastore")`);
    await waitFor(() => cdp.evaluate(
      "document.querySelector('#cadAtBlocoEsp')?.textContent.includes('Pastore Ipanema')"), "bloco Pastore");
    await settle();
    e = await estado();
    check("9. Pastore: só a explicação da parceria",
          e.titulo === "Parceria Pastore — NFS-e não emitida por exame pela SoproLife" &&
          e.itens === 0 && e.faltas.length === 0, JSON.stringify(e));
    check("9b. Pastore: nenhum selo NFS-e visível (nem Nome/CPF)",
          (await cdp.evaluate("window.__m67.selosVisiveis()")).length === 0);
    check("9c. Pastore: CPF sem descrição fiscal ativa",
          !(await cdp.evaluate(`(document.querySelector('[name="cadAtP_cpf"]').getAttribute("aria-describedby") || "").includes("cadNfse-")`)));
    check("cartão do tipo Pastore avisa 'Sem NFS-e por exame'",
          await cdp.evaluate(`document.querySelector('input[value="espirometria_pastore"]').closest("label").textContent.includes("Sem NFS-e por exame")`));
    await shot("05-desktop-pastore", "#cadAtPasso2");
    await shot("06-desktop-pastore-painel", "#cadAtNfse");

    console.log("── Salvar incompleto continua permitido ──");
    await cdp.evaluate(`__m67.tipo("espirometria_soprolife")`);
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('#cadFormAtend').elements.esp_modalidade)"),
      "bloco SoproLife de volta");
    await cdp.evaluate(`(() => {
      __m67.set("cadAtP_nome", "Marina Costa Ribeiro");
      __m67.set("cadAtP_cpf", "");
      __m67.set("esp_data", "01/09/2026");
      __m67.set("esp_municipio", "");
      __m67.set("esp_modalidade", "residencial");
      __m67.set("esp_pgto_data", "01/09/2026");
    })()`);
    await settle();
    e = await estado();
    check("sem CPF e sem município → incompleto", e.titulo === "Faltam dados para NFS-e" &&
          e.pendentes.includes("identidade") && e.pendentes.includes("municipio"));
    const antesDoSalvar = requested.length;
    await cdp.evaluate(`document.querySelector('#cadFormAtend').requestSubmit()`);
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.cad-sucesso'))"), "cadastro salvo", 15000);
    await sleep(600);
    const depoisDoSalvar = requested.length;
    check("15. atendimento incompleto para NFS-e foi SALVO",
          await cdp.evaluate("document.querySelector('.cad-sucesso').textContent.includes('Espirometria ESP-')"));

    console.log("── Paciente existente: julgado pelo servidor ──");
    await cdp.evaluate(`(() => {
      document.querySelector("#cadAtPQ").value = "Rafael Moreira";
      document.querySelector("#cadAtPBuscar").click();
    })()`);
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('#cadAtPResultados .cad-cand'))"), "busca");
    await cdp.evaluate("document.querySelector('#cadAtPResultados .cad-cand').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.cad-cartao-pessoa'))"), "cartão");
    await settle();
    e = await estado();
    check("existente sem CPF → 'CPF necessário para emissão da NFS-e' (veredito do servidor)",
          e.pendentes.includes("identidade") && e.faltas.includes("CPF necessário para emissão da NFS-e"),
          JSON.stringify(e.faltas));

    console.log("── Viewport móvel ──");
    await cdp.evaluate(`document.querySelector("#cadAtPTrocar").click()`);
    for (const width of [390, 820, 1440]) {
      await cdp.send("Emulation.setDeviceMetricsOverride", {
        width, height: 900, deviceScaleFactor: 1, mobile: width <= 420,
      });
      await sleep(200);
      const m = await cdp.evaluate(`({ inner: window.innerWidth,
        body: document.body.scrollWidth, root: document.documentElement.scrollWidth })`);
      check(`viewport ${width}px sem overflow horizontal`,
            m.body <= m.inner && m.root <= m.inner, JSON.stringify(m));
      if (width === 390) {
        await shot("07-mobile-soprolife-incompleto", "#cadAtNfse");
        await shot("08-mobile-campos-paciente", "#cadAtPNovaBox");
        await cdp.evaluate(`(() => {
          __m67.set("cadAtP_nome", "Marina Costa Ribeiro"); __m67.set("cadAtP_cpf", "12345678909");
          __m67.set("esp_data", "01/09/2026"); __m67.set("esp_municipio", "3303302");
          __m67.set("esp_modalidade", "cowork"); __m67.set("esp_pgto_status", "Recebido");
        })()`);
        await settle();
        e = await estado();
        check("celular: preenchido → 'Dados fiscais completos'", e.titulo === "Dados fiscais completos",
              JSON.stringify(e.faltas));
        await shot("09-mobile-soprolife-completo", "#cadAtNfse");
        await cdp.evaluate(`__m67.tipo("espirometria_pastore")`);
        await settle();
        await shot("10-mobile-pastore", "#cadAtNfse");
        await cdp.evaluate(`__m67.tipo("espirometria_soprolife")`);
        await settle();
      }
    }

    console.log("── Nenhuma rota fiscal ──");
    const ehFiscal = (r) => /\/api\/m15\/(fiscal|nfse)\b|sefin|nfse\.gov/i.test(r.url);
    // A prontidão (digitar, trocar tipo, escolher paciente) não fala com o
    // servidor fiscal. O salvar dispara `soprolife:cadastro`, e o módulo
    // Fiscal JÁ EXISTENTE (fiscal-command-center.js) recarrega seus resumos
    // somente-leitura — comportamento anterior à M67, verificado como GET.
    const interacao = requested.slice(inicioFormulario, antesDoSalvar)
      .concat(requested.slice(depoisDoSalvar));
    check("16. prontidão/preenchimento: zero requisições a rota fiscal / NFS-e / SEFIN",
          interacao.filter(ehFiscal).length === 0, JSON.stringify(interacao.filter(ehFiscal)));
    const aoSalvar = requested.slice(antesDoSalvar, depoisDoSalvar).filter(ehFiscal);
    check("16a. ao salvar, só o refresh somente-leitura já existente do painel Fiscal (GET)",
          aoSalvar.every((r) => r.method === "GET"), JSON.stringify(aoSalvar));
    check("16b. na sessão inteira: nenhuma escrita (POST/PUT/PATCH/DELETE) em rota fiscal",
          requested.filter((r) => ehFiscal(r) && r.method !== "GET").length === 0,
          JSON.stringify(requested.filter((r) => ehFiscal(r) && r.method !== "GET")));
    check("16c. nenhuma chamada a SEFIN / nfse.gov.br",
          requested.filter((r) => /sefin|nfse\.gov/i.test(r.url)).length === 0);
    const externos = requested.filter((r) =>
      !/^(http:\/\/127\.0\.0\.1|data:|about:|chrome|blob:)/.test(r.url) &&
      !/^https:\/\/fonts\.(googleapis|gstatic)\.com\//.test(r.url));
    check("fora do loopback, só as fontes que o painel já usava", externos.length === 0,
          JSON.stringify(externos.slice(0, 5)));

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
