#!/usr/bin/env node
/* Fase 1.3A — Centro de Comando Life no navegador, com TRÊS sessões reais.
 *
 * Mesmo arnês da M67/M68 (sem dependência npm): SQLite descartável + API +
 * proxy em portas loopback efêmeras e Chrome headless via DevTools Protocol.
 * Só dados sintéticos; o banco temporário é apagado ao final.
 *
 * Duas provas centrais:
 *
 *  1. AUTORIZAÇÃO. Três contas de papéis diferentes entram de verdade —
 *     `admin`, `operacional` e `medico`. A primeira alterna entre as duas
 *     marcas; as outras duas nem veem a aba da EEGLife, não têm o painel dela
 *     no DOM, e não conseguem entrar nem forçando por localStorage nem
 *     chamando `setContext` pelo console.
 *
 *  2. REDE. Com o contexto salvo em `eeglife`, a página é recarregada e
 *     NENHUMA leitura de `data/*.json` da SoproLife sai do navegador — com a
 *     contraprova de que, em SoproLife, as mesmas leituras SAEM.
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
const CHAVE = "soprolife.life.contexto";
const SENHA = "senha-fase13-sintetica-123";

/* Três papéis reais do sistema, nenhum inventado. `admin` é o único que
 * alcança a EEGLife; `operacional` enxerga o painel SoproLife inteiro mas não
 * é admin; `medico` é a conta exclusivamente clínica. Nenhum nome de pessoa
 * aparece aqui — a regra é de papel. */
const CONTAS = {
  admin: { email: "admin-fase13@teste.local", nome: "Admin Sintetico F13", papel: "admin" },
  operacional: { email: "oper-fase13@teste.local", nome: "Operacional Sintetico F13", papel: "operacional" },
  medico: { email: "medica-fase13@teste.local", nome: "Medica Sintetica F13", papel: "medico" },
};

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
      const d = result.exceptionDetails;
      const detalhe = (d.exception && (d.exception.description || d.exception.value)) || "";
      throw new Error((d.text || "Runtime.evaluate falhou") +
        (detalhe ? " :: " + String(detalhe).split("\n")[0] : "") +
        " :: expr=" + expression.replace(/\s+/g, " ").slice(0, 160));
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

const CONTEXTOS = ["soprolife", "eeglife"];

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
      ...Object.values(CONTAS).map((c) => [
        PYTHON, ["-m", "app.cli", "criar-usuario", "--email", c.email,
          "--nome", c.nome, "--papel", c.papel], { M15_NOVA_SENHA: SENHA },
      ]),
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

    // ─────────────────────────────────────────────────────────────────────
    //  Infraestrutura de sessão: entrar e sair de verdade, três vezes.
    // ─────────────────────────────────────────────────────────────────────

    async function sair() {
      await cdp.send("Network.clearBrowserCookies");
      await cdp.evaluate("try { localStorage.clear(); } catch (e) {}");
    }

    const pronto = () => cdp.evaluate(`Boolean(
      window.LifeCommandCenter
      && document.querySelector('#lifeMasthead')
      && document.body.getAttribute('data-life-ativo'))`);

    /* Recarrega e espera o documento NOVO.
     *
     * `Page.reload` é assíncrono: esperar só por `pronto()` logo depois faz a
     * primeira leitura cair no documento ANTIGO, que também satisfaz a
     * condição — e a leitura seguinte explode porque a página já trocou. A
     * marca posta antes da navegação não existe na página nova, então ela
     * distingue um documento do outro. */
    async function navegar(acao, rotulo) {
      await cdp.evaluate("window.__paginaAnterior = true");
      await acao();
      await waitFor(() => cdp.evaluate(`Boolean(
        !window.__paginaAnterior
        && window.LifeCommandCenter
        && document.querySelector('#lifeMasthead')
        && document.body.getAttribute('data-life-ativo'))`), rotulo);
    }

    const recarregar = (rotulo) =>
      navegar(() => cdp.send("Page.reload"), rotulo);

    async function entrar(conta) {
      await cdp.send("Page.navigate", { url: base + "/" });
      await waitFor(() => cdp.evaluate("Boolean(document.querySelector('#loginForm'))"),
                    "portão de login (" + conta.papel + ")");
      await cdp.evaluate(`(() => {
        document.querySelector("#email").value = ${JSON.stringify(conta.email)};
        document.querySelector("#password").value = ${JSON.stringify(SENHA)};
        document.querySelector("#loginForm").requestSubmit();
      })()`);
      await waitFor(() => cdp.evaluate(
        "Boolean(window.SoproM15 && window.SoproM15.hasToken())"),
        "login " + conta.papel);
      await waitFor(pronto, "casca pronta (" + conta.papel + ")");
    }

    /* Tudo que importa sobre o estado da casca, numa leitura só. */
    async function estado() {
      return cdp.evaluate(`JSON.stringify({
        ctx: window.LifeCommandCenter.getContext(),
        permitidos: window.LifeCommandCenter.allowedContexts(),
        salvo: localStorage.getItem(${JSON.stringify(CHAVE)}),
        body: document.body.getAttribute("data-life-ativo"),
        abas: [...document.querySelectorAll(${ABAS_JS})].map((b) => b.dataset.lifeContext),
        paineis: [...document.querySelectorAll(".life-contexto")].map((p) => p.dataset.lifePanel),
        visiveis: [...document.querySelectorAll(".life-contexto")]
          .filter((p) => !p.hidden).map((p) => p.dataset.lifePanel),
        selecionadas: [...document.querySelectorAll(${ABAS_JS})]
          .filter((b) => b.getAttribute("aria-selected") === "true")
          .map((b) => b.dataset.lifeContext),
        rotulo: (document.querySelector("#lifeContextoRotulo") || {}).textContent || "",
        papeis: (window.SoproBootGate && window.SoproBootGate.identidade
                 && window.SoproBootGate.identidade.papeis_efetivos) || [],
      })`).then(JSON.parse);
    }

    async function trocar(ctx) {
      await cdp.evaluate(
        `(document.querySelector('[data-life-context="${ctx}"]') || { click() {} }).click()`);
      await sleep(200);
    }

    // ═════════════════════════════════════════════════════════════════════
    //  A) ADMIN — alterna entre as duas marcas
    // ═════════════════════════════════════════════════════════════════════
    console.log();
    console.log("A) Conta admin: duas marcas, e nenhum Life Geral");

    await entrar(CONTAS.admin);
    let e = await estado();

    check("5. o admin recebe as duas marcas",
          JSON.stringify(e.permitidos.slice().sort()) === '["eeglife","soprolife"]',
          JSON.stringify(e.permitidos));
    check("1/2. o seletor tem exatamente SoproLife e EEGLife",
          JSON.stringify(e.abas) === '["soprolife","eeglife"]', JSON.stringify(e.abas));
    check("1. Life Geral não existe em aba alguma",
          e.abas.indexOf("life_geral") === -1 && e.paineis.indexOf("life_geral") === -1);
    check("1. Life Geral não existe no texto da página",
          !(await cdp.evaluate("/Life Geral/i.test(document.body.innerText)")));
    check("4. o padrão, sem preferência salva, é SoproLife",
          e.ctx === "soprolife" && JSON.stringify(e.visiveis) === '["soprolife"]',
          JSON.stringify(e));
    check("11. o papel veio do servidor (papeis_efetivos)",
          Array.isArray(e.papeis) && e.papeis.indexOf("admin") !== -1,
          JSON.stringify(e.papeis));

    await trocar("eeglife");
    e = await estado();
    check("5. o admin entra na EEGLife",
          e.ctx === "eeglife" && JSON.stringify(e.visiveis) === '["eeglife"]' &&
          JSON.stringify(e.selecionadas) === '["eeglife"]', JSON.stringify(e));
    check("5. o subtítulo diz a marca ativa em texto",
          /Contexto:\s*EEGLife/.test(e.rotulo), e.rotulo);

    await trocar("soprolife");
    e = await estado();
    check("6. o admin volta para a SoproLife",
          e.ctx === "soprolife" && JSON.stringify(e.visiveis) === '["soprolife"]',
          JSON.stringify(e));

    // Persistência e queda de valor legado/inválido.
    await trocar("eeglife");
    check("a escolha é gravada no navegador", (await estado()).salvo === "eeglife");
    await recarregar("recarga em EEGLife");
    e = await estado();
    check("a preferência sobrevive ao recarregamento",
          e.ctx === "eeglife", JSON.stringify(e));

    for (const [rotulo, valor] of [["3. legado life_geral", "life_geral"],
                                   ["2. valor inválido", "marca_inexistente"]]) {
      await cdp.evaluate(`localStorage.setItem(${JSON.stringify(CHAVE)}, ${JSON.stringify(valor)})`);
      await recarregar("recarga com " + valor);
      e = await estado();
      check(`${rotulo} cai para SoproLife`,
            e.ctx === "soprolife" && e.salvo === "soprolife", JSON.stringify(e));
    }

    // ═════════════════════════════════════════════════════════════════════
    //  B) SoproLife continua funcionando (sessão admin)
    // ═════════════════════════════════════════════════════════════════════
    console.log();
    console.log("B) O painel SoproLife continua acessível e funcional");

    check("10. a sidebar SoproLife continua com todos os módulos",
          await cdp.evaluate(
            `document.querySelectorAll('#lifePanelSoproLife .nav-item[data-section]').length >= 14`));
    check("10. os dados do painel SoproLife carregaram",
          await waitFor(() => cdp.evaluate(
            `(document.querySelector('#overview .kpi-card') || {}).textContent ? true : false`),
            "KPIs", 15000).then(() => true).catch(() => false));
    await cdp.evaluate(
      `document.querySelector('#lifePanelSoproLife .nav-item[data-section="crm"]').click()`);
    await sleep(220);
    check("10. navegar entre módulos SoproLife continua funcionando",
          await cdp.evaluate(`document.querySelector('#crm').classList.contains('active')`));
    await cdp.evaluate(
      `document.querySelector('#lifePanelSoproLife .nav-item[data-section="overview"]').click()`);
    await sleep(160);

    // ═════════════════════════════════════════════════════════════════════
    //  C) A casca EEGLife não busca dado SoproLife (prova de rede)
    // ═════════════════════════════════════════════════════════════════════
    console.log();
    console.log("C) A casca EEGLife não busca dado SoproLife");

    const DADOS_OPERACIONAIS = [
      "resumo.json", "crm-clinicas.json", "leads.json", "marketing.json",
      "tarefas.json", "documentos.json", "automacoes.json",
    ];
    // `m15-config.json` mora em data/ mas é manifesto de boot, não dado
    // operacional: o próprio proxy registra a distinção (M25.27).
    const ehDadoSopro = (u) =>
      /\/painel-soprolife\/data-private\//.test(u) ||
      /\/painel-soprolife\/data\/.*\.local\.json/.test(u) ||
      DADOS_OPERACIONAIS.some((f) => u.includes("/painel-soprolife/data/" + f));

    await cdp.evaluate(`localStorage.setItem(${JSON.stringify(CHAVE)}, "soprolife")`);
    requested = [];
    await recarregar("recarga em SoproLife");
    await sleep(2000);
    check("contraprova: em SoproLife o painel REALMENTE lê data/*.json",
          requested.filter((r) => ehDadoSopro(r.url)).length > 0);

    await cdp.evaluate(`localStorage.setItem(${JSON.stringify(CHAVE)}, "eeglife")`);
    requested = [];
    await recarregar("recarga em EEGLife");
    await sleep(2500);
    let vazou = requested.filter((r) => ehDadoSopro(r.url));
    check("11. em EEGLife, NENHUMA leitura de data/*.json da SoproLife",
          vazou.length === 0, vazou.slice(0, 5).map((r) => r.url).join(" "));
    check("11. a sessão continua válida em EEGLife",
          requested.some((r) => /\/api\/m15\/auth\/me/.test(r.url)));

    // ═════════════════════════════════════════════════════════════════════
    //  D) Estrutura da EEGLife no padrão do produto
    // ═════════════════════════════════════════════════════════════════════
    console.log();
    console.log("D) EEGLife segue o padrão do painel SoproLife, sem dado");

    const casca = await cdp.evaluate(`JSON.stringify({
      modulos: [...document.querySelectorAll('[data-eeg-modulo]')].map((b) => b.dataset.eegModulo),
      grupos: [...document.querySelectorAll('#lifePanelEegLife .eeg-nav-group')].map((g) => g.textContent.trim()),
      temShell: Boolean(document.querySelector('#lifePanelEegLife .app-shell')),
      temSidebar: Boolean(document.querySelector('#lifePanelEegLife .sidebar')),
      temTopbar: Boolean(document.querySelector('#lifePanelEegLife .topbar')),
      paineis: document.querySelectorAll('#lifePanelEegLife .panel').length,
      tabelas: document.querySelectorAll('#lifePanelEegLife .table-wrap').length,
      vazios: document.querySelectorAll('#lifePanelEegLife .empty-state').length,
      stats: document.querySelectorAll('#lifePanelEegLife .stat-card').length,
      canvas: document.querySelectorAll('#lifePanelEegLife canvas').length,
    })`).then(JSON.parse);

    check("16. a EEGLife usa a mesma casca (app-shell + sidebar + topbar)",
          casca.temShell && casca.temSidebar && casca.temTopbar, JSON.stringify(casca));
    check("16. usa os componentes do produto (panel, tabela, estado vazio, stat)",
          casca.paineis > 0 && casca.tabelas > 0 && casca.vazios > 0 && casca.stats > 0,
          JSON.stringify(casca));
    check("16. os cinco grupos de módulos aparecem",
          JSON.stringify(casca.grupos) ===
          JSON.stringify(["Operação", "Clínico EEG", "Crescimento", "Gestão", "Sistema"]),
          JSON.stringify(casca.grupos));
    check("16. os dezesseis módulos aparecem no menu",
          casca.modulos.length === 16, String(casca.modulos.length));
    check("16. nenhum gráfico inventado",
          casca.canvas === 0);

    /* O tema não pode ficar só na sidebar: se os tokens não descerem, o corpo
     * da EEGLife continua com o fundo azulado da SoproLife e as duas marcas
     * ficam indistinguíveis fora do menu. */
    const tema = await cdp.evaluate(`JSON.stringify((() => {
      const p = document.querySelector('#lifePanelEegLife');
      const cs = getComputedStyle(p);
      const main = getComputedStyle(p.querySelector('.main'));
      const body = getComputedStyle(document.body);
      return {
        bgToken: cs.getPropertyValue('--bg').trim(),
        tealToken: cs.getPropertyValue('--teal').trim(),
        mainBg: main.backgroundColor,
        bodyBg: body.backgroundColor,
      };
    })())`).then(JSON.parse);
    check("16. os tokens do tema descem para o painel da EEGLife",
          tema.bgToken === "#f8f5ee" && tema.tealToken === "#c48a32",
          JSON.stringify(tema));
    check("16. o corpo da EEGLife é areia, não o azul da SoproLife",
          tema.mainBg === "rgb(248, 245, 238)",
          JSON.stringify(tema));

    // Nenhum módulo pode gerar requisição: é o que impede dado SoproLife de
    // aparecer com rótulo de EEG.
    const antes = requested.length;
    await cdp.evaluate(
      `document.querySelectorAll('[data-eeg-modulo]').forEach((b) => b.click())`);
    await sleep(900);
    check("11/12/13/14. clicar nos 16 módulos não dispara requisição alguma",
          requested.length === antes,
          requested.slice(antes).map((r) => r.url).join(" "));

    // Telas específicas pedidas: cadastro, CRM, leads, financeiro, laudos.
    for (const [num, modulo, rotulo] of [
      ["12", "cadastros", "Central de Cadastros"],
      ["13", "crm", "CRM"],
      ["13", "leads", "Leads e Agendamentos"],
      ["14", "financeiro", "Financeiro"],
      ["15", "laudos", "Laudos de EEG"],
      ["15", "exames", "Exames e traçados"],
    ]) {
      await cdp.evaluate(`window.LifeCommandCenter.openEegModule(${JSON.stringify(modulo)})`);
      await sleep(120);
      const tela = await cdp.evaluate(`JSON.stringify((() => {
        const s = document.querySelector('[data-eeg-section="${modulo}"]');
        return {
          visivel: Boolean(s) && !s.hidden,
          titulo: (s.querySelector('h2') || {}).textContent || "",
          texto: s ? s.innerText : "",
          temTabela: Boolean(s && s.querySelector('.table-wrap')),
          temVazio: Boolean(s && s.querySelector('.empty-state')),
        };
      })())`).then(JSON.parse);
      check(`${num}. ${rotulo}: tela operacional vazia, no padrão do produto`,
            tela.visivel && tela.titulo === rotulo && tela.temTabela && tela.temVazio,
            JSON.stringify({ v: tela.visivel, t: tela.titulo }));
      check(`${num}. ${rotulo}: sem número e sem dado da SoproLife`,
            !/\d/.test(tela.texto), (tela.texto.match(/[^\s]*\d[^\s]*/g) || []).slice(0, 3).join(" "));
    }

    // Painel Geral: indicadores sem valor, nunca zero.
    await cdp.evaluate(`window.LifeCommandCenter.openEegModule("visao")`);
    await sleep(150);
    const visao = await cdp.evaluate(`JSON.stringify((() => {
      const s = document.querySelector('[data-eeg-section="visao"]');
      return {
        valores: [...s.querySelectorAll('.stat-card strong')].map((x) => x.textContent.trim()),
        dicas: [...s.querySelectorAll('.stat-card small')].map((x) => x.textContent.trim()),
      };
    })())`).then(JSON.parse);
    check("8. o Painel Geral da EEGLife não mostra zero — mostra travessão",
          visao.valores.length >= 7 && visao.valores.every((v) => v === "—"),
          JSON.stringify(visao.valores));
    check("8. e explica a ausência (aguardando ativação / sem fonte)",
          visao.dicas.every((d) => /Aguardando ativação|Sem fonte ativa/.test(d)),
          JSON.stringify(visao.dicas));

    // ═════════════════════════════════════════════════════════════════════
    //  E) Teclado e ARIA (sessão admin, duas abas)
    // ═════════════════════════════════════════════════════════════════════
    console.log();
    console.log("E) Teclado e acessibilidade");

    await trocar("soprolife");
    await cdp.evaluate(`document.querySelector('[data-life-context="soprolife"]').focus()`);
    async function tecla(key) {
      await cdp.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key, code: key });
      await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key, code: key });
      await sleep(170);
    }
    await tecla("ArrowRight");
    check("18. seta à direita vai para a EEGLife",
          await cdp.evaluate("window.LifeCommandCenter.getContext() === 'eeglife'"));
    await tecla("ArrowLeft");
    check("18. seta à esquerda volta para a SoproLife",
          await cdp.evaluate("window.LifeCommandCenter.getContext() === 'soprolife'"));
    await tecla("End");
    check("18. End vai para a última marca",
          await cdp.evaluate("window.LifeCommandCenter.getContext() === 'eeglife'"));
    await tecla("Home");
    check("18. Home vai para a primeira marca",
          await cdp.evaluate("window.LifeCommandCenter.getContext() === 'soprolife'"));
    check("18. o foco acompanha a aba ativa",
          await cdp.evaluate(
            `document.activeElement.dataset.lifeContext === window.LifeCommandCenter.getContext()`));
    check("18. aria-selected e aria-current marcam exatamente uma marca",
          await cdp.evaluate(`(() => {
            const abas = [...document.querySelectorAll(${ABAS_JS})];
            return abas.filter((b) => b.getAttribute("aria-selected") === "true").length === 1
              && abas.filter((b) => b.getAttribute("aria-current") === "true").length === 1
              && abas.filter((b) => b.getAttribute("tabindex") === "0").length === 1;
          })()`));
    check("18. cada aba controla um tabpanel existente",
          await cdp.evaluate(`[...document.querySelectorAll(${ABAS_JS})].every((b) => {
            const alvo = document.getElementById(b.getAttribute("aria-controls"));
            return Boolean(alvo) && alvo.getAttribute("role") === "tabpanel";
          })`));
    check("18. o seletor é feito de <button>",
          await cdp.evaluate(`[...document.querySelectorAll(${ABAS_JS})]
            .every((b) => b.tagName === "BUTTON" && b.type === "button")`));
    check("18. o menu da EEGLife é feito de <button>",
          await cdp.evaluate(`[...document.querySelectorAll('[data-eeg-modulo]')]
            .every((b) => b.tagName === "BUTTON")`));

    // ═════════════════════════════════════════════════════════════════════
    //  F) Capturas nas sete larguras (sessão admin, duas marcas)
    // ═════════════════════════════════════════════════════════════════════
    console.log();
    console.log("F) Responsividade nas sete larguras, sem estouro horizontal");

    for (const vp of VIEWPORTS) {
      await cdp.send("Emulation.setDeviceMetricsOverride", {
        width: vp.w, height: vp.h, deviceScaleFactor: 1, mobile: vp.mobile,
      });
      for (const ctx of CONTEXTOS) {
        await trocar(ctx);
        await sleep(240);
        const m = await cdp.evaluate(`JSON.stringify({
          scroll: document.documentElement.scrollWidth,
          inner: window.innerWidth,
          seletor: (() => { const t = document.querySelector('.life-switcher-track');
            if (!t) return false; const r = t.getBoundingClientRect();
            return r.width > 0 && r.height > 0; })(),
          ativo: (() => { const a = document.querySelector('.life-switcher-track [role="tab"][aria-selected="true"]');
            if (!a) return false; const r = a.getBoundingClientRect();
            return r.width > 0 && r.height > 0; })(),
          rotulo: (() => { const l = document.querySelector('#lifeContextoRotulo');
            if (!l) return false; const r = l.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && l.textContent.trim().length > 0; })(),
        })`).then(JSON.parse);
        check(`17. ${vp.nome} · ${ctx}: sem estouro horizontal`,
              m.scroll <= m.inner + 1, `scrollWidth=${m.scroll} innerWidth=${m.inner}`);
        check(`17. ${vp.nome} · ${ctx}: seletor e marca ativa visíveis`,
              m.seletor && m.ativo && m.rotulo, JSON.stringify(m));
        if (SHOTS) {
          const shot = await cdp.send("Page.captureScreenshot", { format: "png" });
          fs.writeFileSync(path.join(SHOTS, `fase13a-${ctx}-${vp.nome}.png`),
                           Buffer.from(shot.data, "base64"));
        }
      }
    }
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
    });

    // Telas de lista da EEGLife, para a homologação visual: é nelas que se vê
    // se a tela parece operacional à espera de dado, e não página de obra.
    if (SHOTS) {
      await trocar("eeglife");
      for (const modulo of ["cadastros", "crm", "laudos", "financeiro"]) {
        await cdp.evaluate(`window.LifeCommandCenter.openEegModule(${JSON.stringify(modulo)})`);
        await sleep(200);
        const shot = await cdp.send("Page.captureScreenshot", { format: "png" });
        fs.writeFileSync(path.join(SHOTS, `fase13a-eeglife-modulo-${modulo}.png`),
                         Buffer.from(shot.data, "base64"));
      }
      await trocar("soprolife");
    }

    // ═════════════════════════════════════════════════════════════════════
    //  G) NÃO-ADMIN — a EEGLife não existe para eles
    // ═════════════════════════════════════════════════════════════════════
    console.log();
    console.log("G) Contas não-admin ficam restritas à SoproLife");

    for (const conta of [CONTAS.operacional, CONTAS.medico]) {
      console.log(`  — papel: ${conta.papel}`);
      await sair();
      await entrar(conta);
      let n = await estado();

      check(`7. ${conta.papel}: só a SoproLife é permitida`,
            JSON.stringify(n.permitidos) === '["soprolife"]', JSON.stringify(n.permitidos));
      check(`7. ${conta.papel}: a aba da EEGLife NÃO existe no DOM`,
            n.abas.indexOf("eeglife") === -1, JSON.stringify(n.abas));
      check(`7. ${conta.papel}: o painel da EEGLife NÃO existe no DOM`,
            n.paineis.indexOf("eeglife") === -1, JSON.stringify(n.paineis));
      check(`7. ${conta.papel}: nenhum módulo da EEGLife no documento`,
            (await cdp.evaluate("document.querySelectorAll('[data-eeg-modulo]').length")) === 0);
      check(`7. ${conta.papel}: a palavra EEGLife não aparece na tela`,
            !(await cdp.evaluate("/EEGLife/i.test(document.body.innerText)")));
      check(`7. ${conta.papel}: o contexto é SoproLife`,
            n.ctx === "soprolife" && n.body === "soprolife", JSON.stringify(n));

      // Forçar por JavaScript.
      await cdp.evaluate(`window.LifeCommandCenter.setContext("eeglife")`);
      await sleep(200);
      n = await estado();
      check(`9. ${conta.papel}: setContext("eeglife") é recusado`,
            n.ctx === "soprolife" && n.salvo !== "eeglife", JSON.stringify(n));
      check(`9. ${conta.papel}: isAllowed("eeglife") é falso`,
            (await cdp.evaluate(`window.LifeCommandCenter.isAllowed("eeglife")`)) === false);

      // Forçar por localStorage + recarga.
      await cdp.evaluate(`localStorage.setItem(${JSON.stringify(CHAVE)}, "eeglife")`);
      requested = [];
      await recarregar("recarga forçada (" + conta.papel + ")");
      await sleep(1200);
      n = await estado();
      check(`8. ${conta.papel}: forçar por localStorage não abre a EEGLife`,
            n.ctx === "soprolife" && n.salvo === "soprolife" &&
            n.paineis.indexOf("eeglife") === -1, JSON.stringify(n));
      check(`8. ${conta.papel}: e o painel da EEGLife segue fora do DOM`,
            (await cdp.evaluate(
              "document.querySelectorAll('#lifePanelEegLife').length")) === 0);

      // Forçar por URL.
      await navegar(
        () => cdp.send("Page.navigate", { url: base + "/?contexto=eeglife&brand=eeglife" }),
        "recarga por URL (" + conta.papel + ")");
      await sleep(900);
      n = await estado();
      check(`9. ${conta.papel}: parâmetro na URL não abre a EEGLife`,
            n.ctx === "soprolife" && n.paineis.indexOf("eeglife") === -1, JSON.stringify(n));
    }

    // ═════════════════════════════════════════════════════════════════════
    //  H) Higiene
    // ═════════════════════════════════════════════════════════════════════
    console.log();
    console.log("H) Higiene: console, rede e parâmetro de marca");

    const comMarca = requested.filter((r) =>
      /[?&](brand|marca)=/i.test(r.url) || /brand=|"marca"/i.test(r.body));
    // A URL forçada pelo teste é a única exceção conhecida — e ela prova o
    // contrário do risco: o parâmetro foi ignorado.
    const forjadas = comMarca.filter((r) => !/\?contexto=eeglife&brand=eeglife/.test(r.url));
    check("a casca nunca envia brand=/marca= por conta própria",
          forjadas.length === 0, forjadas.slice(0, 3).map((r) => r.url).join(" "));

    const ehOpcional = (u) => /\.local\.json(\?|$)|favicon/i.test(u);
    const ehArquivo = (u) =>
      !/\/api\/m15\//.test(u) && /\.(css|js|json|html|png|svg|woff2?|ico)(\?|$)/i.test(u);
    const falhasArquivo = respostas.filter(
      (r) => r.status >= 400 && ehArquivo(r.url) && !ehOpcional(r.url));
    check("nenhum arquivo necessário falhou",
          falhasArquivo.length === 0,
          falhasArquivo.slice(0, 4).map((r) => `${r.status} ${r.url}`).join(" | "));
    // 200 ou 304: o 304 é revalidação de cache numa recarga, e significa que o
    // arquivo está lá e a cópia do navegador vale. O que não pode é 4xx/5xx.
    const cascaAtivos = respostas.filter((r) => /life-command-center\.(css|js)/.test(r.url));
    check("a casca é servida (CSS e JS, sem erro)",
          cascaAtivos.length > 0 && cascaAtivos.every((r) => r.status === 200 || r.status === 304),
          `n=${cascaAtivos.length} status=${
            [...new Set(cascaAtivos.map((r) => r.status))].sort().join(",")}`);
    const errosScript = erros.filter((x) =>
      !/favicon/i.test(x.texto + x.url) && !ehOpcional(x.url) &&
      !/Failed to load resource/i.test(x.texto));
    check("nenhum erro de JavaScript no console", errosScript.length === 0,
          errosScript.slice(0, 3).map((x) => x.texto).join(" | "));
    const externos = requested.filter((r) =>
      !/^(http:\/\/127\.0\.0\.1|data:|about:|chrome|blob:)/.test(r.url) &&
      !/^https:\/\/fonts\.(googleapis|gstatic)\.com\//.test(r.url));
    check("fora do loopback, só as fontes que o painel já usava",
          externos.length === 0, JSON.stringify(externos.slice(0, 3)));

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
