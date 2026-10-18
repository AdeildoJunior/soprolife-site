#!/usr/bin/env node
/* Fase 1.3 — Centro de Comando Life no navegador de verdade.
 *
 * Mesmo arnês da M67/M68 (sem dependência npm): SQLite descartável + API +
 * proxy em portas loopback efêmeras e Chrome headless via DevTools Protocol.
 * Só dados sintéticos; o banco temporário é apagado ao final.
 *
 * A prova central desta fase é de REDE: com o contexto salvo em `eeglife`, a
 * página é recarregada e NENHUMA leitura de `data/*.json` da SoproLife sai do
 * navegador. É isso que garante que a casca EEGLife não mostra paciente,
 * lead, laudo, receita ou métrica da SoproLife com aparência de EEG — não por
 * disciplina de quem escreve a tela, mas porque o dado não é buscado.
 *
 * O contraprova vem junto: no contexto SoproLife as mesmas leituras SAEM.
 * Sem ela, "nenhuma requisição" poderia significar apenas um teste cego.
 *
 * Uso:
 *   M15_TEST_PYTHON=/caminho/venv/bin/python \
 *   [LIFE_SCREENSHOT_DIR=/pasta/para/capturas] \
 *     node painel-soprolife/scripts/test-fase1-3-life-browser-e2e.js
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
const EMAIL = "admin-fase13@teste.local";
const PASSWORD = "senha-fase13-sintetica-123";
const CHAVE = "soprolife.life.contexto";

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

  close() { this.ws.close(); }
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

const SHOTS = process.env.LIFE_SCREENSHOT_DIR || "";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* As seis resoluções exigidas pela homologação, mais 1920 e 1024. */
const VIEWPORTS = [
  { nome: "1920x1080", w: 1920, h: 1080, mobile: false },
  { nome: "1440x900", w: 1440, h: 900, mobile: false },
  { nome: "1024x768", w: 1024, h: 768, mobile: false },
  { nome: "768x1024", w: 768, h: 1024, mobile: true },
  { nome: "430x932", w: 430, h: 932, mobile: true },
  { nome: "390x844", w: 390, h: 844, mobile: true },
  { nome: "360x800", w: 360, h: 800, mobile: true },
];

const CONTEXTOS = ["life_geral", "soprolife", "eeglife"];

/* O painel SoproLife já tem abas próprias (CRM workspace, Pastore, Custos):
 * doze `role="tab"` que não têm nada a ver com o seletor de contexto. Toda
 * consulta de aba desta casca é escopada aqui, senão o teste conta as abas
 * erradas — foi exatamente o que aconteceu na primeira execução. */
const ABAS = '.life-switcher-track [role="tab"][data-life-context]';
/* Pronto para interpolar dentro das expressões avaliadas na página. */
const ABAS_JS = JSON.stringify(ABAS);

async function main() {
  if (!PYTHON || !fs.existsSync(PYTHON)) {
    throw new Error("defina M15_TEST_PYTHON para o Python do venv M15");
  }
  if (!fs.existsSync(CHROME)) throw new Error(`Chrome não encontrado: ${CHROME}`);
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-fase13-e2e-"));
  const dbPath = path.join(temp, "fase13-e2e.db");
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
    M15_AUTH_SECRET: "fase13-browser-e2e-secret-32-chars-synthetic",
    M15_MARKETING_REFRESH_QUEUE: path.join(temp, "marketing-request.json"),
  };
  const alembic = path.join(path.dirname(PYTHON), "alembic");
  const children = [];
  let cdp;

  try {
    for (const [command, args, extraEnv] of [
      [alembic, ["upgrade", "head"], {}],
      [PYTHON, ["-m", "app.cli", "criar-usuario", "--email", EMAIL,
        "--nome", "Teste Fase 1.3", "--papel", "admin"], { M15_NOVA_SENHA: PASSWORD }],
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
    await waitFor(async () => (await fetch(`http://127.0.0.1:${chromePort}/json/version`)).ok,
                  "Chrome DevTools");
    const target = await (await fetch(
      `http://127.0.0.1:${chromePort}/json/new?${encodeURIComponent(base + "/")}`,
      { method: "PUT" })).json();
    cdp = await Cdp.connect(target.webSocketDebuggerUrl);
    await Promise.all([cdp.send("Page.enable"), cdp.send("Runtime.enable"),
      cdp.send("Network.enable"), cdp.send("Log.enable")]);

    // Toda URL pedida pela página e todo erro de console.
    let requested = [];
    const respostas = [];
    const erros = [];
    cdp.ws.addEventListener("message", (event) => {
      const msg = JSON.parse(String(event.data));
      if (msg.method === "Network.requestWillBeSent") {
        requested.push({
          method: msg.params.request.method,
          url: msg.params.request.url,
          body: msg.params.request.postData || "",
        });
      }
      if (msg.method === "Network.responseReceived") {
        respostas.push({ url: msg.params.response.url, status: msg.params.response.status });
      }
      if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
        erros.push({ texto: msg.params.entry.text || "", url: msg.params.entry.url || "" });
      }
    });

    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
    });

    // ── Login real (o painel só carrega depois dele) ───────────────────────
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('#loginForm'))"),
                  "portão de login");
    check("18. sem sessão, o navegador recebe a tela de login (não o painel)",
          await cdp.evaluate(
            "Boolean(document.querySelector('#loginForm')) && !document.querySelector('#lifeMasthead')"));

    await cdp.evaluate(`(() => {
      document.querySelector("#email").value = ${JSON.stringify(EMAIL)};
      document.querySelector("#password").value = ${JSON.stringify(PASSWORD)};
      document.querySelector("#loginForm").requestSubmit();
    })()`);
    await waitFor(() => cdp.evaluate(
      "Boolean(window.SoproM15 && window.LifeCommandCenter && window.SoproM15.hasToken())"),
      "login concluído");

    // `window.LifeCommandCenter` existe desde o parse do script, ANTES de
    // `ligar()` rodar no DOMContentLoaded. Esperar só por ele lê um DOM pela
    // metade; o sinal de que a casca aplicou o contexto é o atributo no body.
    const pronto = () => cdp.evaluate(`Boolean(
      window.LifeCommandCenter
      && document.querySelector('#lifeMasthead')
      && document.querySelector('#lifeContextoRotulo')
      && document.body.getAttribute('data-life-ativo'))`);
    await waitFor(pronto, "casca do Centro de Comando Life");

    // ── A) estrutura dos três contextos ────────────────────────────────────
    console.log();
    console.log("A) Três contextos, Life Geral não é marca");

    check("1. os três contextos existem no seletor",
          await cdp.evaluate(`JSON.stringify(
            [...document.querySelectorAll(${ABAS_JS})].map((b) => b.dataset.lifeContext))`)
          === JSON.stringify(CONTEXTOS), await cdp.evaluate(`JSON.stringify(
            [...document.querySelectorAll(${ABAS_JS})].map((b) => b.dataset.lifeContext))`));
    check("1. \"Centro de Comando Life\" aparece no topo",
          (await cdp.evaluate(
            "document.querySelector('#lifeMasthead .life-identity-text strong').textContent.trim()"))
          === "Centro de Comando Life");
    check("2. Life Geral é declarado contexto consolidado, não marca",
          await cdp.evaluate(
            `document.querySelector('[data-life-context="life_geral"]').dataset.lifeKind === "contexto"
             && window.LifeCommandCenter.CONTEXTOS.life_geral.tipo === "contexto"`));
    check("2. só SoproLife e EEGLife são marcas",
          await cdp.evaluate(`(() => {
            const c = window.LifeCommandCenter.CONTEXTOS;
            return Object.keys(c).filter((k) => c[k].tipo === "marca").sort().join(",") === "eeglife,soprolife";
          })()`));
    check("2. o rótulo \"consolidado\" aparece na aba Life Geral",
          (await cdp.evaluate(
            `document.querySelector('[data-life-context="life_geral"] .life-tab-kind').textContent.trim()`))
          === "consolidado");

    // ── B) padrão, troca, persistência ─────────────────────────────────────
    console.log();
    console.log("B) Padrão SoproLife, trocas e persistência");

    check("3. com perfil novo (sem preferência salva), o padrão é SoproLife",
          await cdp.evaluate("window.LifeCommandCenter.getContext() === 'soprolife'"));
    check("3. o painel SoproLife é o visível no primeiro acesso",
          await cdp.evaluate(`!document.querySelector('#lifePanelSoproLife').hidden
            && document.querySelector('#lifePanelEegLife').hidden
            && document.querySelector('#lifePanelLifeGeral').hidden`));

    async function trocar(ctx) {
      await cdp.evaluate(`document.querySelector('[data-life-context="${ctx}"]').click()`);
      await sleep(180);
    }
    async function estado() {
      return cdp.evaluate(`JSON.stringify({
        ctx: window.LifeCommandCenter.getContext(),
        salvo: localStorage.getItem(${JSON.stringify(CHAVE)}),
        body: document.body.getAttribute("data-life-ativo"),
        visiveis: [...document.querySelectorAll(".life-contexto")]
          .filter((p) => !p.hidden).map((p) => p.dataset.lifePanel),
        selecionadas: [...document.querySelectorAll(${ABAS_JS})]
          .filter((b) => b.getAttribute("aria-selected") === "true")
          .map((b) => b.dataset.lifeContext),
        rotulo: document.querySelector("#lifeContextoRotulo").textContent.trim(),
      })`).then(JSON.parse);
    }

    await trocar("eeglife");
    let e = await estado();
    check("4. troca para EEGLife muda contexto, painel e aba",
          e.ctx === "eeglife" && e.body === "eeglife" &&
          JSON.stringify(e.visiveis) === '["eeglife"]' &&
          JSON.stringify(e.selecionadas) === '["eeglife"]', JSON.stringify(e));
    check("4. o rótulo em texto acompanha (contexto não depende de cor)",
          /EEGLife/.test(e.rotulo), e.rotulo);

    await trocar("life_geral");
    e = await estado();
    check("5. troca para Life Geral muda contexto, painel e aba",
          e.ctx === "life_geral" && JSON.stringify(e.visiveis) === '["life_geral"]' &&
          JSON.stringify(e.selecionadas) === '["life_geral"]', JSON.stringify(e));

    await trocar("soprolife");
    e = await estado();
    check("6. volta para SoproLife restaura o painel operacional",
          e.ctx === "soprolife" && JSON.stringify(e.visiveis) === '["soprolife"]' &&
          JSON.stringify(e.selecionadas) === '["soprolife"]', JSON.stringify(e));
    check("6. exatamente um painel visível por vez, sempre",
          e.visiveis.length === 1);

    // Persistência: a preferência sobrevive ao recarregamento.
    await trocar("eeglife");
    check("7. a escolha é gravada no navegador",
          (await estado()).salvo === "eeglife");
    await cdp.send("Page.reload");
    await waitFor(pronto, "recarga em EEGLife");
    await waitFor(() => cdp.evaluate(
      "Boolean(window.SoproM15 && window.SoproM15.hasToken())"), "sessão após recarga");
    e = await estado();
    check("7. o contexto escolhido sobrevive ao recarregamento",
          e.ctx === "eeglife" && JSON.stringify(e.visiveis) === '["eeglife"]', JSON.stringify(e));

    // Valor inválido: cai no padrão, sem quebrar.
    await cdp.evaluate(`localStorage.setItem(${JSON.stringify(CHAVE)}, "marca_inexistente")`);
    await cdp.send("Page.reload");
    await waitFor(pronto, "recarga com valor inválido");
    e = await estado();
    check("8. valor de contexto inválido cai para SoproLife",
          e.ctx === "soprolife" && JSON.stringify(e.visiveis) === '["soprolife"]', JSON.stringify(e));
    check("8. e o valor inválido é corrigido no armazenamento",
          e.salvo === "soprolife", String(e.salvo));

    // ── C) painel SoproLife continua funcional ─────────────────────────────
    console.log();
    console.log("C) O painel SoproLife continua acessível e funcional");

    check("9. a sidebar SoproLife continua com todos os módulos",
          await cdp.evaluate(
            `document.querySelectorAll('#lifePanelSoproLife .nav-item[data-section]').length >= 14`),
          String(await cdp.evaluate(
            `document.querySelectorAll('#lifePanelSoproLife .nav-item[data-section]').length`)));
    check("9. os dados do painel SoproLife carregaram (KPIs preenchidos)",
          await waitFor(() => cdp.evaluate(
            `(document.querySelector('#overview .kpi-card') || {}).textContent ? true : false`),
            "KPIs do painel", 15000).then(() => true).catch(() => false));
    await cdp.evaluate(
      `document.querySelector('#lifePanelSoproLife .nav-item[data-section="crm"]').click()`);
    await sleep(220);
    check("9. navegar entre módulos SoproLife continua funcionando",
          await cdp.evaluate(`document.querySelector('#crm').classList.contains('active')`));
    await cdp.evaluate(
      `document.querySelector('#lifePanelSoproLife .nav-item[data-section="overview"]').click()`);
    await sleep(160);

    // ── D) a prova de rede ─────────────────────────────────────────────────
    console.log();
    console.log("D) A casca EEGLife não busca dado SoproLife (prova de rede)");

    const DADOS_OPERACIONAIS = [
      "resumo.json", "crm-clinicas.json", "leads.json", "marketing.json",
      "tarefas.json", "documentos.json", "automacoes.json",
    ];
    const ehDadoSopro = (u) =>
      /\/painel-soprolife\/data-private\//.test(u) ||
      /\/painel-soprolife\/data\/.*\.local\.json/.test(u) ||
      DADOS_OPERACIONAIS.some((f) => u.includes("/painel-soprolife/data/" + f));

    // Contraprova primeiro: no contexto SoproLife, as leituras SAEM. Sem isto
    // "nenhuma requisição" poderia ser só um teste cego.
    await cdp.evaluate(`localStorage.setItem(${JSON.stringify(CHAVE)}, "soprolife")`);
    requested = [];
    await cdp.send("Page.reload");
    await waitFor(pronto, "recarga em SoproLife");
    await waitFor(() => Promise.resolve(requested.some((r) => ehDadoSopro(r.url))),
                  "leitura de data/ no contexto SoproLife", 15000)
      .catch(() => null);
    await sleep(1500);
    const noSopro = requested.filter((r) => ehDadoSopro(r.url));
    check("contraprova: em SoproLife o painel REALMENTE lê data/*.json",
          noSopro.length > 0, `${noSopro.length} requisições`);

    // Agora o requisito: contexto EEGLife, recarga, e nenhuma leitura.
    await cdp.evaluate(`localStorage.setItem(${JSON.stringify(CHAVE)}, "eeglife")`);
    requested = [];
    await cdp.send("Page.reload");
    await waitFor(pronto, "recarga em EEGLife");
    await waitFor(() => cdp.evaluate(
      "Boolean(window.SoproM15 && window.SoproM15.hasToken())"), "sessão em EEGLife");
    await sleep(2500); // folga generosa: se fosse buscar, já teria buscado
    const noEeg = requested.filter((r) => ehDadoSopro(r.url));
    check("10. em EEGLife, NENHUMA leitura de data/*.json da SoproLife",
          noEeg.length === 0, noEeg.slice(0, 6).map((r) => r.url).join(" "));
    check("10. e a casca EEGLife está de fato na tela",
          await cdp.evaluate(`!document.querySelector('#lifePanelEegLife').hidden
            && document.querySelector('#lifePanelSoproLife').hidden`));
    check("10. a sessão continua válida em EEGLife (auth não regrediu)",
          requested.some((r) => /\/api\/m15\/auth\/me/.test(r.url)));

    // Clicar nos módulos EEG não deve gerar requisição alguma.
    const antesCliques = requested.length;
    const modulos = await cdp.evaluate(
      `[...document.querySelectorAll('[data-eeg-modulo]')].map((b) => b.dataset.eegModulo).length`);
    await cdp.evaluate(`(() => {
      document.querySelectorAll('[data-eeg-modulo]').forEach((b) => b.click());
    })()`);
    await sleep(900);
    check("10. clicar em TODOS os módulos EEGLife não dispara requisição",
          requested.length === antesCliques,
          requested.slice(antesCliques).map((r) => r.url).join(" "));
    check("10. os módulos previstos aparecem no menu EEGLife",
          modulos >= 14, String(modulos));
    check("10. módulo sem backend mostra \"Estrutura em implantação\"",
          await cdp.evaluate(
            `/Estrutura em implantação/.test(document.querySelector('#lifePanelEegLife').textContent)`));

    // Nenhum parâmetro de marca em requisição alguma, em nenhum contexto.
    await trocar("life_geral");
    await sleep(400);
    await trocar("soprolife");
    await sleep(1200);
    const comMarca = requested.filter((r) =>
      /brand=|marca=|life_geral|eeglife/i.test(r.url) || /brand=|life_geral/i.test(r.body));
    check("12. nenhuma requisição carrega brand=/marca= nem o valor do contexto",
          comMarca.length === 0, comMarca.slice(0, 4).map((r) => r.url).join(" "));

    // ── E) Life Geral não inventa indicador ────────────────────────────────
    console.log();
    console.log("E) Life Geral consolidado, sem número inventado");

    await trocar("life_geral");
    const lgTexto = await cdp.evaluate(
      "document.querySelector('#lifePanelLifeGeral').innerText");
    check("11. o painel Life Geral não exibe número algum",
          !/\d/.test(lgTexto), (lgTexto.match(/[^\s]*\d[^\s]*/g) || []).slice(0, 5).join(" "));
    check("11. a frase sobre indicadores consolidados está na tela",
          /Indicadores consolidados serão disponibilizados após a ativação do modelo multimarcas/
            .test(lgTexto.replace(/\s+/g, " ")));
    check("11. Life Geral não monta gráfico nem KPI",
          await cdp.evaluate(
            `document.querySelectorAll('#lifePanelLifeGeral canvas, #lifePanelLifeGeral .kpi-card').length === 0`));
    check("11. o atalho \"Entrar em SoproLife\" leva ao painel operacional",
          await (async () => {
            await cdp.evaluate(`document.querySelector('[data-life-entrar="soprolife"]').click()`);
            await sleep(200);
            return cdp.evaluate("window.LifeCommandCenter.getContext() === 'soprolife'");
          })());

    // ── F) teclado e aria ──────────────────────────────────────────────────
    console.log();
    console.log("F) Teclado e acessibilidade");

    await trocar("life_geral");
    await cdp.evaluate(`document.querySelector('[data-life-context="life_geral"]').focus()`);
    async function tecla(key) {
      // keydown sintético no elemento focado, como um teclado real faria.
      await cdp.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key, code: key });
      await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key, code: key });
      await sleep(160);
    }
    await tecla("ArrowRight");
    check("13. seta à direita avança para o próximo contexto",
          await cdp.evaluate("window.LifeCommandCenter.getContext() === 'soprolife'"),
          await cdp.evaluate("window.LifeCommandCenter.getContext()"));
    await tecla("ArrowRight");
    check("13. seta à direita segue para EEGLife",
          await cdp.evaluate("window.LifeCommandCenter.getContext() === 'eeglife'"));
    await tecla("ArrowLeft");
    check("13. seta à esquerda volta",
          await cdp.evaluate("window.LifeCommandCenter.getContext() === 'soprolife'"));
    await tecla("Home");
    check("13. Home vai ao primeiro contexto",
          await cdp.evaluate("window.LifeCommandCenter.getContext() === 'life_geral'"));
    await tecla("End");
    check("13. End vai ao último contexto",
          await cdp.evaluate("window.LifeCommandCenter.getContext() === 'eeglife'"));
    check("13. o foco acompanha a aba ativa",
          await cdp.evaluate(
            `document.activeElement.dataset.lifeContext === window.LifeCommandCenter.getContext()`));
    check("13. só a aba ativa está na ordem do Tab",
          await cdp.evaluate(`(() => {
            const abas = [...document.querySelectorAll(${ABAS_JS})];
            return abas.filter((b) => b.getAttribute("tabindex") === "0").length === 1;
          })()`));

    check("14. aria-selected e aria-current marcam exatamente um contexto",
          await cdp.evaluate(`(() => {
            const abas = [...document.querySelectorAll(${ABAS_JS})];
            return abas.filter((b) => b.getAttribute("aria-selected") === "true").length === 1
              && abas.filter((b) => b.getAttribute("aria-current") === "true").length === 1;
          })()`));
    check("14. cada aba controla um tabpanel existente",
          await cdp.evaluate(`[...document.querySelectorAll(${ABAS_JS})].every((b) => {
            const alvo = document.getElementById(b.getAttribute("aria-controls"));
            return Boolean(alvo) && alvo.getAttribute("role") === "tabpanel";
          })`));
    check("14. o seletor é feito de <button>, não de div clicável",
          await cdp.evaluate(`[...document.querySelectorAll(${ABAS_JS})].length === 3
            && [...document.querySelectorAll(${ABAS_JS})]
              .every((b) => b.tagName === "BUTTON" && b.type === "button")`));
    check("14. o contexto ativo é anunciado por região live",
          await cdp.evaluate(
            `document.querySelector('#lifeContextoRotulo').getAttribute('aria-live') === 'polite'`));

    // ── G) responsividade, overflow e capturas ─────────────────────────────
    console.log();
    console.log("G) Responsividade nas sete larguras, sem estouro horizontal");

    for (const vp of VIEWPORTS) {
      await cdp.send("Emulation.setDeviceMetricsOverride", {
        width: vp.w, height: vp.h, deviceScaleFactor: 1, mobile: vp.mobile,
      });
      for (const ctx of ["soprolife", "eeglife", "life_geral"]) {
        await trocar(ctx);
        await sleep(240);
        const m = await cdp.evaluate(`JSON.stringify({
          scroll: document.documentElement.scrollWidth,
          inner: window.innerWidth,
          seletorVisivel: (() => {
            const t = document.querySelector('.life-switcher-track');
            if (!t) return false;
            const r = t.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
          })(),
          ativoVisivel: (() => {
            const a = document.querySelector('.life-switcher-track [role="tab"][aria-selected="true"]');
            if (!a) return false;
            const r = a.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
          })(),
          rotuloVisivel: (() => {
            const l = document.querySelector('#lifeContextoRotulo');
            if (!l) return false;
            const r = l.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && l.textContent.trim().length > 0;
          })(),
        })`).then(JSON.parse);
        check(`${vp.mobile ? "16" : "15"}. ${vp.nome} · ${ctx}: sem estouro horizontal`,
              m.scroll <= m.inner + 1, `scrollWidth=${m.scroll} innerWidth=${m.inner}`);
        check(`${vp.nome} · ${ctx}: seletor utilizável e contexto ativo visível`,
              m.seletorVisivel && m.ativoVisivel && m.rotuloVisivel, JSON.stringify(m));

        if (SHOTS) {
          const shot = await cdp.send("Page.captureScreenshot", {
            format: "png", captureBeyondViewport: false,
          });
          fs.writeFileSync(
            path.join(SHOTS, `fase13-${ctx}-${vp.nome}.png`),
            Buffer.from(shot.data, "base64"));
        }
      }
    }

    // ── H) higiene final ───────────────────────────────────────────────────
    console.log();
    console.log("H) Higiene: console, rede externa, sessão");

    // 404 de arquivo OPCIONAL não é erro de JS: `loadOptionalJson` existe
    // justamente para os summaries locais que podem não ter sido gerados —
    // num banco de teste recém-criado, nenhum existe. O que não pode falhar é
    // arquivo que a página precisa: script, folha de estilo, ou dado
    // obrigatório.
    const ehOpcional = (u) => /\.local\.json(\?|$)|favicon/i.test(u);
    // Só ARQUIVOS. Resposta de API tem semântica própria e não é falha de
    // carregamento: neste ambiente sintético o fiscal responde 503 (integração
    // desligada, que é o padrão correto) e Pastore responde 409 por não haver
    // competência aberta. Nada disso tem a ver com a casca.
    const ehArquivo = (u) =>
      !/\/api\/m15\//.test(u) && /\.(css|js|json|html|png|svg|woff2?|ico)(\?|$)/i.test(u);
    const falhasArquivo = respostas.filter(
      (r) => r.status >= 400 && ehArquivo(r.url) && !ehOpcional(r.url));
    check("17. nenhum arquivo necessário falhou (script, estilo, dado)",
          falhasArquivo.length === 0,
          falhasArquivo.slice(0, 5).map((r) => `${r.status} ${r.url}`).join(" | "));

    // A casca é recarregada várias vezes ao longo do teste; o que importa é
    // que CSS e JS existam e nunca tenham respondido diferente de 200. Se o
    // gate de arquivos do proxy não servisse os arquivos novos, a aparência
    // sumiria em produção sem nenhum teste reclamar.
    const cascaCss = respostas.filter((r) => /life-command-center\.css/.test(r.url));
    const cascaJs = respostas.filter((r) => /life-command-center\.js/.test(r.url));
    check("17. a casca do Centro de Comando Life é servida (CSS e JS, sempre 200)",
          cascaCss.length > 0 && cascaJs.length > 0 &&
          [...cascaCss, ...cascaJs].every((r) => r.status === 200),
          `css=${cascaCss.length} js=${cascaJs.length} status=${
            [...new Set([...cascaCss, ...cascaJs].map((r) => r.status))].join(",")}`);
    const errosScript = erros.filter((e) =>
      !/favicon/i.test(e.texto + e.url) && !ehOpcional(e.url) &&
      !/Failed to load resource/i.test(e.texto));
    check("17. nenhum erro de JavaScript no console", errosScript.length === 0,
          errosScript.slice(0, 3).map((e) => e.texto).join(" | "));
    const externos = requested.filter((r) =>
      !/^(http:\/\/127\.0\.0\.1|data:|about:|chrome|blob:)/.test(r.url) &&
      !/^https:\/\/fonts\.(googleapis|gstatic)\.com\//.test(r.url));
    check("fora do loopback, só as fontes que o painel já usava",
          externos.length === 0, JSON.stringify(externos.slice(0, 4)));
    check("18. a sessão continuou válida do início ao fim",
          await cdp.evaluate("window.SoproM15.hasToken() === true"));

    console.log();
    console.log(`RESULTADO: ${failures === 0 ? "OK" : "FALHOU"} — ${failures} falha(s)`);
    if (SHOTS) console.log(`Capturas em: ${SHOTS}`);
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
