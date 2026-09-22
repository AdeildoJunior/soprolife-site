#!/usr/bin/env node
/* M26.26 — `loadCatalog` engolia erro de renderização.
 *
 * O DEFEITO. `render()` ficava DENTRO do mesmo `try` que tratava a rede:
 *
 *     try {
 *       const catalog = await client().api(...);
 *       state.catalog = catalog;
 *       render();            // <- uma exceção AQUI...
 *     } catch (error) {
 *       state.catalog = null; // <- ...era tratada como falha de rede
 *       render();             // <- e repintava a tela de CARREGANDO
 *     }
 *
 * Um `TypeError` na pintura virava "o catálogo não carregou": a tela ficava
 * eternamente em "Carregando catálogo de conclusões…", sem nada no console
 * e sem `pageerror`, porque a exceção tinha sido capturada. Aconteceu de
 * verdade na M26.25, com um `siglaDe` duplicado, e só foi diagnosticado
 * depois de instrumentar o `catch` à mão.
 *
 * O QUE ESTE ARQUIVO PROVA. Que os dois erros deixaram de ser o mesmo erro:
 * falha de REDE mostra mensagem própria e oferece nova tentativa; falha de
 * PINTURA não finge ser rede, não apaga o catálogo, continua observável e
 * para numa tela de erro controlada em vez de num laço de repintura.
 *
 * Navegador real, API simulada em memória, sem conexão com dados reais.
 * PLAYWRIGHT_MODULE pode apontar para uma instalação local já disponível.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const panel = path.resolve(__dirname, "..");

const CATALOGO_BOM = {
  conclusoes: [
    { codigo: "NORMAL", rotulo: "Normal", grupo: "normal",
      texto: "Espirometria dentro dos limites da normalidade.", personalizado: false },
    { codigo: "DVO_LEVE", rotulo: "DVO Leve", grupo: "obstrutivo",
      texto: "Distúrbio ventilatório obstrutivo leve.", personalizado: false },
    { codigo: "PERSONALIZADO", rotulo: "Personalizado", grupo: "personalizado",
      texto: "", personalizado: true },
  ],
  complementos_bd: [
    { codigo: "BD_NAO_REALIZADO", rotulo: "BD não realizado", texto: "", acrescenta_frase: false },
  ],
  exame_com_pos_bd: false,
};

// Catálogo que CHEGA (HTTP 200, promessa resolvida) e faz a pintura
// explodir: `renderConclusionPicker` faz `catalog.conclusoes.forEach(...)`.
// É a forma fiel do incidente — o carregamento deu certo, quem quebrou foi
// o código que desenha.
const CATALOGO_QUE_QUEBRA_A_PINTURA = {
  conclusoes: "isto não é um array",
  complementos_bd: [],
  exame_com_pos_bd: false,
};

const FILA = {
  document_id: "doc-1", status: "em_elaboracao",
  patient: { full_name: "Paciente Sintético" },
  exam_code: "ESP-000001", report_code: "LAU-000001",
  location_key: "sintetico", location_name: "Unidade Sintética",
  is_corrective: false, correction_reason_code: null, locked: false,
};

function detalhe() {
  return {
    id: "doc-1",
    public_code: "LAU-000001",
    status: "em_elaboracao",
    corrects_document_id: null,
    correction_reason_code: null,
    locked: false,
    current_version_id: "ver-original",
    versoes: [
      { id: "ver-original", kind: "original", version_number: 1,
        mime_type: "application/pdf", page_count: 1 },
    ],
    patient: { full_name: "Paciente Sintético", date_of_birth: "2000-01-01",
      public_code: "PES-000001" },
    exam: { public_code: "ESP-000001", exam_date: "2027-01-10",
      exam_time: "09:00", post_bronchodilator: false },
    location: null,
  };
}

// Tudo o que a página registrou de errado, nos dois canais que o defeito
// deixava mudos: exceção não tratada e console.
async function montarPagina(browser, viewport) {
  const page = await browser.newPage({ viewport, hasTouch: viewport.width <= 500 });
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (msg) => {
    if (msg.type() === "error" && !/Failed to load resource/.test(msg.text())) {
      consoleErrors.push(msg.text());
    }
  });
  await page.route("**/*", (route) => route.abort());
  await page.route("**/data/m15-config.json", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      enabled: true, reports_mode: "production", reports_enabled: true,
      api_base: "/painel-soprolife/api/m15",
    }),
  }));
  await page.route("https://sopro-sintetico.test/painel/", (route) => route.fulfill({
    contentType: "text/html",
    body: '<html lang="pt-BR"><head>'
      + '<meta name="viewport" content="width=device-width,initial-scale=1">'
      + '</head><body style="margin:0;padding:12px;box-sizing:border-box">'
      + '<main style="max-width:1160px;margin:auto;min-width:0">'
      + '<div id="reportWorkflowRoot" class="report-workflow-root"></div></main></body></html>',
  }));
  await page.goto("https://sopro-sintetico.test/painel/");
  const html = fs.readFileSync(path.join(panel, "index.html"), "utf8");
  for (const m of html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) {
    try {
      await page.addStyleTag({ content: fs.readFileSync(path.join(panel, m[1]), "utf8") });
    } catch (_e) { /* folha opcional ausente — segue sem ela */ }
  }
  return { page, pageErrors, consoleErrors };
}

// `window.catalogoModo` decide, A CADA chamada, o que a rota do catálogo
// faz. É isso que permite falhar uma vez e acertar na tentativa seguinte,
// sem recarregar a página.
async function instalarCliente(page) {
  await page.evaluate(({ bom, quebra, queueItem, d }) => {
    window.calls = [];
    window.catalogoModo = "ok";
    window.catalogoChamadas = 0;
    window.SoproM15 = {
      hasToken: () => true,
      can: () => false,
      getUser: () => ({ nome: "Dra. Sintética", papeis: ["medico"] }),
      onSessionChange: (cb) => { setTimeout(cb, 0); },
      apiUrl: (u) => u,
      hasSession: () => false,
      apiBlob: async () => new Blob([new Uint8Array([1, 2, 3])], { type: "application/pdf" }),
      api: async (url, options) => {
        const method = (options && options.method) || "GET";
        window.calls.push({ url, method });
        if (url.startsWith("/laudos/meus")) return [queueItem];
        if (url === "/laudos/templates?catalog=clinical") return [];
        if (url === "/laudos/assinatura-externa/pendentes") return { laudos: [] };
        if (url === "/laudos/doc-1") return d;
        if (url.endsWith("/catalogo-conclusoes")) {
          window.catalogoChamadas += 1;
          if (window.catalogoModo === "rede") {
            const erro = new Error("Serviço de laudos indisponível no momento.");
            erro.code = "http_503";
            throw erro;
          }
          if (window.catalogoModo === "quebra") return quebra;
          return bom;
        }
        if (url.endsWith("/documentos")) return [];
        throw new Error(`rota sintética não coberta: ${method} ${url}`);
      },
    };
  }, { bom: CATALOGO_BOM, quebra: CATALOGO_QUE_QUEBRA_A_PINTURA, queueItem: FILA, d: detalhe() });
}

async function abrirLaudo(page) {
  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-queue-item");
  await page.locator("[data-report-open]").click();
}

const CARREGANDO = "Carregando catálogo de conclusões";
const FALHA_PINTURA = ".report-state[role='alert']";
const ERRO_CATALOGO = ".report-catalog-error";

async function main() {
  const browser = await chromium.launch({ headless: true });
  let passed = 0;
  const check = async (nome, run) => { await run(); passed++; console.log(`PASS ${nome}`); };

  try {
    // ---------------------------------------------------------------- 1
    await check("1 — catálogo carrega normalmente → render funciona, sem erro em canal nenhum", async () => {
      const { page, pageErrors, consoleErrors } = await montarPagina(browser, { width: 1440, height: 1000 });
      await instalarCliente(page);
      await abrirLaudo(page);
      await page.waitForSelector("[data-report-conclusion]");
      assert.equal(await page.locator('[data-report-conclusion="NORMAL"]').count(), 1);
      assert.equal(await page.locator(ERRO_CATALOGO).count(), 0);
      assert.ok(!(await page.locator("body").innerText()).includes(CARREGANDO));
      assert.deepEqual(pageErrors, []);
      assert.deepEqual(consoleErrors, []);
      await page.close();
    });

    // ---------------------------------------------------------------- 2
    await check("2 — fetch do catálogo falha → erro próprio na tela, com a mensagem do servidor", async () => {
      const { page, pageErrors } = await montarPagina(browser, { width: 1440, height: 1000 });
      await instalarCliente(page);
      await page.evaluate(() => { window.catalogoModo = "rede"; });
      await abrirLaudo(page);
      await page.waitForSelector(ERRO_CATALOGO);
      const texto = await page.locator(ERRO_CATALOGO).innerText();
      assert.match(texto, /Não foi possível carregar as conclusões/i);
      // `readableError` já é o padrão do painel: mensagem + código.
      assert.match(texto, /Serviço de laudos indisponível no momento\./);
      assert.match(texto, /http_503/);
      // E o que NÃO pode estar lá: o carregamento eterno.
      assert.ok(
        !(await page.locator("body").innerText()).includes(CARREGANDO),
        "falha de rede não pode ser servida como carregamento"
      );
      // O resto da bancada continua de pé — falhar o catálogo não derruba a
      // tela inteira.
      assert.equal(await page.locator("#reportFinalText").count(), 1);
      // Falha de REDE é esperada e tratada: não vira exceção não tratada.
      assert.deepEqual(pageErrors, []);
      await page.close();
    });

    // ---------------------------------------------------------------- 4
    await check("4 — nova tentativa depois da falha de rede recarrega só o catálogo", async () => {
      const { page } = await montarPagina(browser, { width: 1440, height: 1000 });
      await instalarCliente(page);
      await page.evaluate(() => { window.catalogoModo = "rede"; });
      await abrirLaudo(page);
      await page.waitForSelector(ERRO_CATALOGO);
      // A médica já escreveu algo: a nova tentativa não pode jogar fora.
      await page.locator("#reportFinalText").fill("Rascunho escrito antes da falha.");
      await page.evaluate(() => { window.catalogoModo = "ok"; });
      await page.locator("[data-report-catalog-retry]").click();
      await page.waitForSelector("[data-report-conclusion]");
      assert.equal(await page.locator(ERRO_CATALOGO).count(), 0);
      assert.equal(
        await page.locator("#reportFinalText").inputValue(),
        "Rascunho escrito antes da falha.",
        "a nova tentativa do catálogo não pode descartar o texto da médica"
      );
      // Uma nova tentativa é UMA nova chamada, não um laço.
      assert.equal(await page.evaluate(() => window.catalogoChamadas), 2);
      // E não houve recarregamento da página.
      assert.ok((await page.evaluate(() => window.calls.length)) > 0);
      await page.close();
    });

    // ---------------------------------------------------------------- 3
    await check("3 — catálogo OK mas render lança: catálogo NÃO é zerado e o erro não vira 'carregando'", async () => {
      const { page, pageErrors, consoleErrors } = await montarPagina(browser, { width: 1440, height: 1000 });
      await instalarCliente(page);
      await page.evaluate(() => { window.catalogoModo = "quebra"; });
      await abrirLaudo(page);
      await page.waitForSelector(FALHA_PINTURA);

      const corpo = await page.locator("body").innerText();
      // A prova central. Com o defeito, o `catch` da rede zerava
      // `state.catalog` e repintava — e ESTA string é o que a médica via,
      // para sempre. Ela não pode aparecer: significaria que a exceção de
      // programação foi convertida em "catálogo não carregado".
      assert.ok(
        !corpo.includes(CARREGANDO),
        "erro de renderização virou 'Carregando catálogo…' — o catálogo foi zerado"
      );
      // Nem pode se disfarçar de falha de rede.
      assert.equal(
        await page.locator(ERRO_CATALOGO).count(), 0,
        "erro de programação não pode ser apresentado como falha de rede"
      );
      // Estado de erro controlado, com mensagem segura e sem stack trace.
      assert.match(corpo, /Não foi possível exibir as conclusões/i);
      assert.match(corpo, /Tentar novamente/);
      assert.ok(!/TypeError|forEach|at .*report-workflow/.test(corpo),
        "a stack trace não pode chegar à tela");

      // O erro ORIGINAL continua observável nos dois canais que o defeito
      // deixava mudos.
      const todos = pageErrors.concat(consoleErrors).join(" | ");
      assert.match(todos, /forEach is not a function/,
        `o erro original precisa continuar observável; veio: ${todos}`);
      assert.match(consoleErrors.join(" | "), /\[laudos\] falha ao desenhar a tela/);

      // Sem laço de repintura: uma falha, uma mensagem.
      const repeticoes = consoleErrors.filter(
        (t) => /falha ao desenhar a tela/.test(t)).length;
      assert.equal(repeticoes, 1, `repintou em laço: ${repeticoes} falhas registradas`);
      await page.close();
    });

    // ---------------------------------------------------------------- 3b
    await check("3b — depois da falha de pintura, 'Tentar novamente' recupera a tela", async () => {
      const { page } = await montarPagina(browser, { width: 1440, height: 1000 });
      await instalarCliente(page);
      await page.evaluate(() => { window.catalogoModo = "quebra"; });
      await abrirLaudo(page);
      await page.waitForSelector(FALHA_PINTURA);
      // Corrigido o que quebrava, o botão devolve a bancada: a trava de
      // repintura cai em `boot()`, senão a tela ficaria morta para sempre.
      await page.evaluate(() => { window.catalogoModo = "ok"; });
      await page.locator("[data-report-retry]").click();
      await page.waitForSelector(".report-queue-item");
      await page.locator("[data-report-open]").click();
      await page.waitForSelector("[data-report-conclusion]");
      assert.equal(await page.locator('[data-report-conclusion="NORMAL"]').count(), 1);
      await page.close();
    });

    // ---------------------------------------------------------------- 5
    await check("5 — conclusões rápidas da M26.25 seguem inteiras depois da correção", async () => {
      const { page, pageErrors } = await montarPagina(browser, { width: 1440, height: 1000 });
      await instalarCliente(page);
      await abrirLaudo(page);
      await page.waitForSelector("[data-report-conclusion]");
      assert.equal(await page.locator(".report-quick-conclusions").count(), 1);
      await page.locator('[data-report-conclusion="DVO_LEVE"]').click();
      assert.equal(
        await page.locator("#reportFinalText").inputValue(),
        "Distúrbio ventilatório obstrutivo leve."
      );
      assert.equal(await page.locator(".report-conclude-cta").isDisabled(), false);
      assert.match(
        await page.locator(".report-sigla-preview").innerText(),
        /Distúrbio ventilatório obstrutivo leve\./
      );
      // O gate continua de pé quando se limpa.
      await page.locator("[data-report-conclusion-clear]").click();
      assert.equal(await page.locator(".report-conclude-cta").isDisabled(), true);
      assert.match(await page.locator("#reportConcludeBlocker").innerText(), /conclus/i);
      assert.deepEqual(pageErrors, []);
      await page.close();
    });

    // ---------------------------------------------------------------- 6
    for (const largura of [390, 430]) {
      await check(`6 — ${largura}px: erro de catálogo e erro de pintura cabem na tela, sem overflow`, async () => {
        const { page } = await montarPagina(browser, { width: largura, height: 844 });
        await instalarCliente(page);
        await page.evaluate(() => { window.catalogoModo = "rede"; });
        await abrirLaudo(page);
        await page.waitForSelector(ERRO_CATALOGO);
        assert.equal(
          await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
          true, `overflow horizontal com o erro de catálogo em ${largura}px`
        );
        assert.equal(await page.locator("[data-report-catalog-retry]").isVisible(), true);

        await page.evaluate(() => { window.catalogoModo = "quebra"; });
        await page.locator("[data-report-catalog-retry]").click();
        await page.waitForSelector(FALHA_PINTURA);
        assert.equal(
          await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
          true, `overflow horizontal com a falha de pintura em ${largura}px`
        );
        assert.ok(!(await page.locator("body").innerText()).includes(CARREGANDO));
        await page.close();
      });
    }

    console.log(`\n${passed} cenários passaram.`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error("FALHA:", error.message);
  process.exitCode = 1;
});
