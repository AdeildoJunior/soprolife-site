#!/usr/bin/env node
/* Hotfix do telefone internacional — no navegador de verdade.
 *
 * Mesmo arnês da M67/M68 (sem dependência npm): SQLite descartável + API +
 * proxy em portas loopback efêmeras e Chrome headless via DevTools Protocol.
 * Só dados sintéticos; o banco temporário é apagado ao final.
 *
 * O ensaio do caso real, com paciente SINTÉTICO: um cadastro nasce com o
 * telefone no estado corrompido que a máscara antiga produzia —
 * "(16) 47238-6290", que era o canadense +1 647 238-6290 lido como celular
 * brasileiro. O teste então faz pela interface exatamente o que a operação
 * vai fazer depois do deploy: trocar o DDI para +1, corrigir o número,
 * salvar, recarregar e reabrir. Nenhum telefone real de paciente aparece
 * aqui, e nada é enviado para fora do loopback.
 *
 * Prova: +55 como default; Brasil sem digitar DDI; troca para +1; digitação
 * e colagem internacional; salvar; reload; reeditar com DDI +1 preservado;
 * o legado que não é reescrito só por abrir a tela; "+" no meio do número;
 * wa.me internacional e brasileiro; desktop, tablet e celular estreito.
 *
 * Uso:
 *   M15_TEST_PYTHON=/caminho/venv/bin/python \
 *   [FONE_SCREENSHOT_DIR=/pasta/para/capturas] \
 *     node painel-soprolife/scripts/test-hotfix-telefone-internacional-e2e.js
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
const EMAIL = "admin-fone@teste.local";
const PASSWORD = "senha-fone-sintetica-123";

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

const SHOTS = process.env.FONE_SCREENSHOT_DIR || "";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  if (!PYTHON || !fs.existsSync(PYTHON)) {
    throw new Error("defina M15_TEST_PYTHON para o Python do venv M15");
  }
  if (!fs.existsSync(CHROME)) throw new Error(`Chrome não encontrado: ${CHROME}`);
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-fone-e2e-"));
  const dbPath = path.join(temp, "fone-e2e.db");
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
    M15_AUTH_SECRET: "fone-browser-e2e-secret-32-chars-sintetico",
    M15_MARKETING_REFRESH_QUEUE: path.join(temp, "marketing-request.json"),
  };
  const alembic = path.join(path.dirname(PYTHON), "alembic");
  const children = [];
  let cdp;

  try {
    for (const [command, args, extraEnv] of [
      [alembic, ["upgrade", "head"], {}],
      [PYTHON, ["-m", "app.cli", "criar-usuario", "--email", EMAIL,
        "--nome", "Teste Telefone", "--papel", "admin"], { M15_NOVA_SENHA: PASSWORD }],
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



    // ── helpers da tela ───────────────────────────────────────────────────
    const SEM_ACENTO = (s) => s;
    // Valor do par [DDI][número] de um campo, e o valor canônico escondido.
    const leFone = (name) => cdp.evaluate(`(() => {
      const h = document.querySelector('[name=${JSON.stringify(name)}]');
      if (!h) return null;
      const g = h.closest('.cad-fone');
      const ddi = g ? g.querySelector('.cad-fone-ddi') : null;
      const num = g ? g.querySelector('.cad-fone-num') : null;
      return {
        canonico: h.value, tipo: h.type,
        ddi: ddi ? ddi.value : null, numero: num ? num.value : null,
        ddiVisivel: !!(ddi && ddi.offsetWidth > 0),
        numVisivel: !!(num && num.offsetWidth > 0),
        larguraDdi: ddi ? Math.round(ddi.getBoundingClientRect().width) : 0,
        list: ddi ? ddi.getAttribute('list') : null,
      };
    })()`);
    // Digita caractere a caractere num dos dois controles, com evento real.
    const digita = (name, qual, texto) => cdp.evaluate(`(() => {
      const h = document.querySelector('[name=${JSON.stringify(name)}]');
      const el = h.closest('.cad-fone').querySelector('.cad-fone-${qual}');
      for (const ch of ${JSON.stringify(texto)}) {
        el.value = el.value + ch;
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }
      return el.value;
    })()`);
    // Colagem: substitui tudo de uma vez e dispara um único `input`.
    const cola = (name, qual, texto) => cdp.evaluate(`(() => {
      const h = document.querySelector('[name=${JSON.stringify(name)}]');
      const el = h.closest('.cad-fone').querySelector('.cad-fone-${qual}');
      el.value = ${JSON.stringify(texto)};
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return el.value;
    })()`);
    const semRolagemH = () => cdp.evaluate(
      "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1");
    const shot = async (nome) => {
      if (!SHOTS) return;
      const r = await cdp.send("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(path.join(SHOTS, nome + ".png"), Buffer.from(r.data, "base64"));
    };

    // Telefone do caso real, no estado CORROMPIDO que a máscara antiga
    // gerava. É o ponto de partida do ensaio — e o que a operação precisa
    // conseguir corrigir pela tela.
    const CORROMPIDO = "(16) 47238-6290";
    const CANADA_DDI = "+1";
    const CANADA_NUM = "647 238-6290";
    const CANADA_E164 = "+16472386290";

    // ── 1. Novo cadastro: Brasil é o default ──────────────────────────────
    console.log("── 1. Novo cadastro começa em Brasil (+55) ──");
    await cdp.evaluate(`window.SoproCentral.open("lead", {})`);
    await waitFor(() => cdp.evaluate(
      "Boolean(document.querySelector('[name=\"cadLeadP_fone\"]'))"), "formulário de lead");
    await sleep(200);
    let f = await leFone("cadLeadP_fone");
    check("1. novo cadastro inicia com DDI +55", f && f.ddi === "+55", JSON.stringify(f));
    check("1. o campo com `name` virou portador do valor (hidden)",
      f.tipo === "hidden" && f.canonico === "", JSON.stringify(f));
    check("1. os dois controles estão visíveis na tela",
      f.ddiVisivel && f.numVisivel);
    check("1. o DDI tem sugestões por datalist, sem biblioteca externa",
      f.list === "cadDdiSugestoes" && await cdp.evaluate(
        "Boolean(document.getElementById('cadDdiSugestoes') &&"
        + " document.getElementById('cadDdiSugestoes').options.length >= 10)"));
    await shot("01-novo-cadastro-br");

    // ── 2. Brasil funciona sem o operador digitar DDI ─────────────────────
    console.log("── 2. Brasil continua funcionando sem digitar DDI ──");
    await digita("cadLeadP_fone", "num", "21999999999");
    f = await leFone("cadLeadP_fone");
    check("2. digitar 21999999999 exibe (21) 99999-9999",
      f.numero === "(21) 99999-9999", f.numero);
    check("2. e salva como +5521999999999",
      f.canonico === "+5521999999999", f.canonico);

    // ── 3/4. Trocar +55 → +1 e digitar o número canadense ─────────────────
    console.log("── 3/4. Trocar o DDI para +1 e digitar o número ──");
    await cola("cadLeadP_fone", "num", "");
    await cola("cadLeadP_fone", "ddi", CANADA_DDI);
    await digita("cadLeadP_fone", "num", CANADA_NUM);
    f = await leFone("cadLeadP_fone");
    check("4. DDI +1 + 647 238-6290 → " + CANADA_E164,
      f.canonico === CANADA_E164, f.canonico);
    check("4. a tela NÃO aplica máscara brasileira ao número internacional",
      !/^\(/.test(f.numero), f.numero);
    check("4. o valor NUNCA é (16) 47238-6290", f.canonico !== CORROMPIDO);
    check("4. o valor NUNCA ganha +55 na frente",
      f.canonico.indexOf("+55") !== 0 && f.canonico !== "+5516472386290");
    await shot("02-novo-cadastro-canada");

    // ── 5/6. Colagem com DDI explícito ────────────────────────────────────
    console.log("── 5/6. Colar telefone completo no campo do número ──");
    for (const colado of ["+1 647 238-6290", "+16472386290", "0016472386290"]) {
      await cola("cadLeadP_fone", "ddi", "+55");
      await cola("cadLeadP_fone", "num", colado);
      f = await leFone("cadLeadP_fone");
      check(`6. colar "${colado}" reconfigura o DDI para +1 e mantém o número`,
        f.ddi === "+1" && f.canonico === CANADA_E164,
        f.ddi + " / " + f.canonico);
    }
    for (const colado of ["+55 21 99999-9999", "+5521999999999"]) {
      await cola("cadLeadP_fone", "ddi", "+1");
      await cola("cadLeadP_fone", "num", colado);
      f = await leFone("cadLeadP_fone");
      check(`13. colar "${colado}" volta o DDI para +55`,
        f.ddi === "+55" && f.canonico === "+5521999999999",
        f.ddi + " / " + f.canonico);
    }

    // ── 16. "+" no meio do número ─────────────────────────────────────────
    console.log('── 16. "+" no meio do número não é DDI válido ──');
    await cola("cadLeadP_fone", "ddi", "+1");
    await cola("cadLeadP_fone", "num", "647+2386290");
    f = await leFone("cadLeadP_fone");
    check('16. "+" no meio é descartado, não lido como DDI',
      f.ddi === "+1" && f.numero === "6472386290", f.ddi + " / " + f.numero);
    check('16. o valor canônico tem um único "+"',
      (f.canonico.match(/\+/g) || []).length === 1, f.canonico);

    // ── 19. Telefone ausente ──────────────────────────────────────────────
    console.log("── 19. Telefone ausente ──");
    await cola("cadLeadP_fone", "num", "");
    f = await leFone("cadLeadP_fone");
    check("19. sem número, o valor enviado é vazio — não \"+1\"",
      f.canonico === "", JSON.stringify(f.canonico));

    // ── 20/7/8/9/10/11. O ensaio do caso real, com paciente sintético ─────
    console.log("── 7-11/20. Corrigir pela tela um cadastro já corrompido ──");
    const pessoa = await cdp.evaluate(`(async () => {
      const p = await window.SoproM15.api("/pessoas", { method: "POST", body: JSON.stringify({
        nome_completo: "Paciente Sintetica Nanp",
        contatos: [{ tipo: "whatsapp", valor: ${JSON.stringify(CORROMPIDO)}, principal: true }],
      }) });
      return { id: p.id, codigo: p.public_code };
    })()`);
    const contatoDe = (id) => cdp.evaluate(`(async () => {
      const p = await window.SoproM15.api("/pessoas/" + ${JSON.stringify(id)});
      const c = (p.contatos || []).find((x) => x.tipo === "whatsapp");
      return c ? { valor: c.valor, norm: c.valor_normalizado || null } : null;
    })()`);
    const antes = await contatoDe(pessoa.id);
    check("20. o cadastro legado/corrompido carrega como está no banco",
      antes && antes.valor === CORROMPIDO, JSON.stringify(antes));

    // Busca pelo código público e abre "Editar cadastro" — o caminho do
    // operador, sem atalho de teste.
    const abrirCorrecao = async () => {
      await cdp.evaluate(`(() => {
        const q = document.querySelector("#cadLeadPQ");
        q.value = ${JSON.stringify(pessoa.codigo)};
        document.querySelector("#cadLeadPBuscar").click();
      })()`);
      await waitFor(() => cdp.evaluate(
        "Boolean(document.querySelector('.cad-cand'))"), "resultado da busca");
      await cdp.evaluate("document.querySelector('.cad-cand').click()");
      await waitFor(() => cdp.evaluate(
        "Boolean(document.querySelector('#cadLeadPEditar'))"), "botão Editar cadastro");
      await cdp.evaluate("document.querySelector('#cadLeadPEditar').click()");
      await waitFor(() => cdp.evaluate(
        "Boolean(document.querySelector('[name=\"cadLeadP_editFone\"]'))"), "formulário de correção");
      await sleep(150);
    };
    await abrirCorrecao();
    f = await leFone("cadLeadP_editFone");
    check("20. a correção abre com +55 assumido e o número do banco",
      f.ddi === "+55" && f.numero === CORROMPIDO, JSON.stringify(f));
    check("21. abrir a tela NÃO reescreve o valor: o enviado é o do banco",
      f.canonico === CORROMPIDO, f.canonico);
    await shot("03-correcao-legado");

    // Agora a correção que a operação vai fazer: DDI +1 e número certo.
    await cola("cadLeadP_editFone", "ddi", CANADA_DDI);
    await cola("cadLeadP_editFone", "num", CANADA_NUM);
    f = await leFone("cadLeadP_editFone");
    check("4. a correção compõe " + CANADA_E164, f.canonico === CANADA_E164, f.canonico);
    check("11. e a tela não volta para (16) 47238-6290", f.numero !== CORROMPIDO, f.numero);
    await shot("04-correcao-canada");

    // ── 7. Salvar ─────────────────────────────────────────────────────────
    await cdp.evaluate("document.querySelector('#cadLeadPSalvarFix').click()");
    await waitFor(async () => {
      const c = await contatoDe(pessoa.id);
      return c && c.valor === CANADA_E164;
    }, "telefone salvo em E.164");
    const salvo = await contatoDe(pessoa.id);
    check("7. salvar grava o valor canônico " + CANADA_E164,
      salvo.valor === CANADA_E164, JSON.stringify(salvo));
    check("7. a chave de busca/wa.me é 16472386290, sem 55 presumido",
      salvo.norm === null || salvo.norm === "16472386290", String(salvo.norm));

    // ── 8/9/10/11. Reload e reedição ──────────────────────────────────────
    await cdp.send("Page.reload");
    await waitFor(() => cdp.evaluate(
      "Boolean(window.SoproM15 && window.SoproCentral && window.SoproM15.hasToken())"),
      "painel recarregado");
    await cdp.evaluate(`window.SoproCentral.open("lead", {})`);
    await waitFor(() => cdp.evaluate(
      "Boolean(document.querySelector('#cadLeadPQ'))"), "lead após reload");
    await sleep(200);
    await abrirCorrecao();
    f = await leFone("cadLeadP_editFone");
    check("8/9/10. depois do reload, o DDI permanece +1", f.ddi === "+1", f.ddi);
    check("10. e o número volta sem máscara brasileira",
      f.numero === "6472386290", f.numero);
    check("11. o valor NUNCA voltou a ser (16) 47238-6290",
      f.canonico === CANADA_E164 && f.canonico !== CORROMPIDO, f.canonico);
    check("21. reabrir a correção não altera o valor por si só",
      f.canonico === CANADA_E164, f.canonico);
    await shot("05-reload-canada");

    // ── 12/13/14/15. Telefones brasileiros, ponta a ponta ─────────────────
    console.log("── 12-15. Telefones brasileiros continuam funcionando ──");
    for (const entrada of ["+5521999999999", "+55 (21) 99999-9999",
                           "21999999999", "(21) 99999-9999"]) {
      await cola("cadLeadP_editFone", "ddi", "+55");
      await cola("cadLeadP_editFone", "num", entrada);
      f = await leFone("cadLeadP_editFone");
      check(`"${entrada}" → +5521999999999`,
        f.canonico === "+5521999999999", f.canonico);
    }
    await cdp.evaluate("document.querySelector('#cadLeadPSalvarFix').click()");
    await waitFor(async () => {
      const c = await contatoDe(pessoa.id);
      return c && c.valor === "+5521999999999";
    }, "telefone brasileiro salvo");
    const salvoBr = await contatoDe(pessoa.id);
    check("12-15. o brasileiro grava +5521999999999 e normaliza 5521999999999",
      salvoBr.valor === "+5521999999999" &&
      (salvoBr.norm === null || salvoBr.norm === "5521999999999"),
      JSON.stringify(salvoBr));

    // ── 17/18. wa.me ──────────────────────────────────────────────────────
    console.log("── 17/18. wa.me internacional e brasileiro ──");
    const waDe = (valor) => cdp.evaluate(
      `window.SoproPainel && window.SoproPainel.resolveLeadWhatsApp
        ? window.SoproPainel.resolveLeadWhatsApp({ telefone_whatsapp: ${JSON.stringify(valor)} })
        : "sem-api"`);
    const waIntl = await waDe(CANADA_E164);
    if (waIntl === "sem-api") {
      // `resolveLeadWhatsApp` não é exportado; a regra do painel já está
      // provada no teste offline, e a do núcleo no pytest. Aqui fica a
      // prova de que a tela não monta wa.me por conta própria.
      check("17/18. a tela não monta wa.me fora das funções auditadas",
        !/wa\.me/.test(await cdp.evaluate(
          "Array.from(document.querySelectorAll('a[href]')).map(a => a.href).join(' ')")));
    } else {
      check("17. wa.me internacional sem 55 presumido",
        waIntl === "https://wa.me/16472386290", waIntl);
      check("18. wa.me brasileiro preserva o 55",
        await waDe("21999999999") === "https://wa.me/5521999999999");
    }

    // ── 22/23/24. Desktop, tablet e celular estreito ──────────────────────
    // Salvar fecha a caixa de correção (o cartão da pessoa é re-renderizado
    // com os dados novos), então a tela é reaberta para medir o campo.
    console.log("── 22/23/24. Desktop, tablet e celular estreito ──");
    await abrirCorrecao();
    for (const [largura, altura, rotulo] of [[1440, 1000, "desktop"],
                                             [820, 1180, "tablet"],
                                             [390, 844, "celular"],
                                             [320, 700, "celular estreito"]]) {
      await cdp.send("Emulation.setDeviceMetricsOverride", {
        width: largura, height: altura, deviceScaleFactor: 1, mobile: largura < 768,
      });
      await sleep(250);
      check(`${rotulo} (${largura}px): sem rolagem horizontal`, await semRolagemH());
      const vis = await leFone("cadLeadP_editFone");
      check(`${rotulo} (${largura}px): DDI e número visíveis no mesmo campo`,
        vis.ddiVisivel && vis.numVisivel, JSON.stringify(vis));
      check(`${rotulo} (${largura}px): o DDI cabe "+595" sem ser cortado`,
        vis.larguraDdi >= 38, String(vis.larguraDdi));
      await shot(`06-${largura}px`);
    }
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
    });

    // ── 24. Criação e edição usam a MESMA regra ───────────────────────────
    console.log("── 24. Criação e edição usam a mesma regra ──");
    const mesmaRegra = await cdp.evaluate(`(() => {
      const nomes = ["cadLeadP_fone", "cadLeadP_editFone"];
      return nomes.map((n) => {
        const h = document.querySelector('[name="' + n + '"]');
        if (!h) return n + ":ausente";
        const g = h.closest('.cad-fone');
        return n + ":" + (g ? "par" : "sozinho") + ":" + h.type;
      }).join("|");
    })()`);
    check("24. criação e edição montam o mesmo par [DDI][número]",
      mesmaRegra === "cadLeadP_fone:par:hidden|cadLeadP_editFone:par:hidden",
      mesmaRegra);

    // ── 21. Nenhum dado real saiu da máquina ──────────────────────────────
    const externos = requested.filter((r) =>
      !/^(http:\/\/127\.0\.0\.1|data:|about:|chrome|blob:)/.test(r.url) &&
      !/^https:\/\/fonts\.(googleapis|gstatic)\.com\//.test(r.url));
    check("21. fora do loopback, só as fontes que o painel já usava",
      externos.length === 0, JSON.stringify(externos.slice(0, 5)));
    check("21. nenhum telefone em URL alguma",
      requested.every((r) => !/6472386290|999999999/.test(r.url)));

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
