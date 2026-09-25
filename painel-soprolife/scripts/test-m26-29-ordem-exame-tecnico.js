#!/usr/bin/env node
/* M26.29 — ordem clínica da bancada e "Como funciona" recolhido.
 *
 * O QUE ESTE ARQUIVO PROVA:
 *
 *   1. "Como funciona" nasce FECHADO — inclusive para a médica cuja fila só
 *      tem laudos "pendentes de laudo", que era justamente o caso em que ele
 *      abria sozinho (produção: fila ativa zerada depois da M26.28). Abre e
 *      fecha por clique, toque e teclado.
 *
 *   2. O exame técnico (MIR) vem ANTES das Conclusões rápidas — no DOM (é a
 *      ordem do teclado e do leitor de tela) e na tela, em 1440, 1024, 430 e
 *      390px. A M26.25 invertia a pilha com `order` abaixo de 1100px.
 *
 *   3. Voltar o exame para cima não devolveu a tela enorme: o visualizador
 *      segue compacto, as conclusões chegam logo abaixo dele (a prévia do
 *      laudo gerado passou para depois da ação), a doca continua `sticky` e
 *      não há rolagem horizontal.
 *
 * Mesmo harness da M26.25: navegador real, API simulada em memória, nenhum
 * dado real. PLAYWRIGHT_MODULE pode apontar para uma instalação local.
 * M2629_SHOTS=<pasta> grava as capturas numa pasta escolhida.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const panel = path.resolve(__dirname, "..");
const shots = process.env.M2629_SHOTS
  || fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m2629-ui-"));
fs.mkdirSync(shots, { recursive: true });

// Recorte fiel do catálogo REAL (`app/services/report_conclusions.py`):
// mesmos códigos, mesmos textos por extenso, mesmos grupos. Um catálogo
// inventado mediria um agrupamento que não existe.
const CATALOG = {
  conclusoes: [
    { codigo: "NORMAL", rotulo: "Normal", grupo: "normal",
      texto: "Espirometria dentro dos limites da normalidade.", personalizado: false },
    { codigo: "DVO_LEVE", rotulo: "DVO Leve", grupo: "obstrutivo",
      texto: "Distúrbio ventilatório obstrutivo leve.", personalizado: false },
    { codigo: "DVO_GRAVE", rotulo: "DVO Grave", grupo: "obstrutivo",
      texto: "Distúrbio ventilatório obstrutivo grave.", personalizado: false },
    { codigo: "DVR_SUG_MODERADO", rotulo: "DVR sug. Moderado", grupo: "restritivo",
      texto: "Padrão sugestivo de distúrbio ventilatório restritivo moderado.", personalizado: false },
    { codigo: "DVM_SUG_LEVE", rotulo: "DVM sug. Leve", grupo: "misto",
      texto: "Padrão sugestivo de distúrbio ventilatório misto leve.", personalizado: false },
    { codigo: "DVI", rotulo: "DVI", grupo: "inespecifico",
      texto: "Padrão sugestivo de distúrbio ventilatório inespecífico.", personalizado: false },
    { codigo: "PERSONALIZADO", rotulo: "Personalizado", grupo: "personalizado",
      texto: "", personalizado: true },
  ],
  complementos_bd: [
    { codigo: "RBD_POSITIVO", rotulo: "RBD+",
      texto: "Com resposta significativa ao broncodilatador.", acrescenta_frase: true },
    { codigo: "RBD_NEGATIVO", rotulo: "RBD−",
      texto: "Sem resposta significativa ao broncodilatador.", acrescenta_frase: true },
    { codigo: "BD_NAO_REALIZADO", rotulo: "BD não realizado",
      texto: "", acrescenta_frase: false },
  ],
  exame_com_pos_bd: true,
};

const DOCUMENTOS = {
  patient: { full_name: "Paciente Sintético" },
  report_code: "LAU-000001",
  exam_code: "ESP-000001",
  validation_code: "SINTETICO-0000-0000",
  tecnico_mir: { kind: "original", version_number: 1, version_id: "ver-original",
    sha256: "0".repeat(64), previa: false, assinavel: false },
  laudo_soprolife: null,
};

function baseDetail(overrides) {
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
      exam_time: "09:00", post_bronchodilator: true },
    location: null,
    ...overrides,
  };
}

const QUEUE_ITEM = {
  document_id: "doc-1", status: "em_elaboracao",
  patient: { full_name: "Paciente Sintético" },
  exam_code: "ESP-000001", report_code: "LAU-000001",
  location_key: "sintetico", location_name: "Unidade Sintética",
  is_corrective: false, correction_reason_code: null, locked: false,
};

async function commonSetup(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (msg) => {
    // O harness aborta TODA requisição de rede de propósito; o PDF original
    // do laudo aberto vira "Failed to load resource". Ruído do simulado.
    if (msg.type() === "error" && !/Failed to load resource/.test(msg.text())) {
      errors.push(msg.text());
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
      // A MESMA meta do index.html. Sem ela o Chrome emula um layout
      // viewport de ~980px e NENHUMA media query de celular entra: o teste
      // mediria um desktop estreito achando que é um iPhone.
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
  return errors;
}

async function abrirLaudo(page, detail, queueItem) {
  await page.evaluate(({ catalog, detail: d, queueItem, documentos }) => {
    window.calls = [];
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
        const body = options && options.body ? JSON.parse(options.body) : null;
        window.calls.push({ url, method, body });
        if (url.startsWith("/laudos/meus")) return [queueItem];
        if (url === "/laudos/templates?catalog=clinical") return [];
        if (url === "/laudos/assinatura-externa/pendentes") return { laudos: [] };
        if (url === "/laudos/doc-1") return d;
        if (url.endsWith("/catalogo-conclusoes")) return catalog;
        if (url.endsWith("/documentos")) return documentos;
        if (url.endsWith("/laudo/previa")) {
          // Fiel ao servidor: a prévia CRIA uma versão nativa com os
          // snapshots do catálogo, e é dela que a tela reidrata a escolha.
          d.versoes = d.versoes
            .filter((v) => v.kind !== "laudo_previa")
            .concat([{
              id: "prev-1", kind: "laudo_previa", version_number: d.versoes.length + 1,
              mime_type: "application/pdf", page_count: 1,
              conclusion_code_snapshot: body.conclusion_code,
              conclusion_text_snapshot: body.conclusion_custom_text || "texto do catálogo",
              bronchodilator_code_snapshot: body.bronchodilator_code || null,
              interpretation_text_snapshot: body.final_text || "",
              interpretation_text_sha256: "hash1",
              observations_snapshot: body.observations || null,
            }]);
          d.current_version_id = "prev-1";
          return { final_text: body.final_text || "", preview_version_id: "prev-1",
            final_text_sha256: "hash1" };
        }
        if (url.endsWith("/assinar-e-liberar")) {
          d.status = "liberado";
          d.locked = true;
          return { validation_code: "SINTETICO-0000-0000" };
        }
        throw new Error(`rota sintética não coberta: ${method} ${url}`);
      },
    };
  }, { catalog: CATALOG, detail, queueItem: queueItem || QUEUE_ITEM, documentos: DOCUMENTOS });
  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-queue-item");
  await page.locator("[data-report-open]").click();
  await page.waitForSelector("#reportFinalText");
  // O catálogo chega numa requisição PRÓPRIA, depois do detalhe: esperar só
  // pelo formulário deixa o teste numa corrida com "Carregando catálogo de
  // conclusões…". As siglas são o sinal de que ele chegou.
  await page.waitForSelector("[data-report-conclusion]");
}


// A fila que fazia "Como funciona" abrir sozinho: nada além de "pendente
// de laudo". É o estado de quem acabou de receber exames novos.
const QUEUE_PENDENTE = { ...QUEUE_ITEM, status: "atribuido" };

// Mede, no navegador, a posição dos blocos da bancada.
async function medir(page) {
  return page.evaluate(() => {
    const box = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { top: r.top + window.scrollY, left: r.left, width: r.width,
        height: r.height, bottom: r.bottom + window.scrollY };
    };
    const exame = document.querySelector(".report-source-pane");
    const conclusoes = document.querySelector(".report-quick-conclusions");
    const segue = (a, b) => Boolean(a && b
      && (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING));
    const visor = document.querySelector(
      ".report-source-pane .report-pdf-frame, .report-source-pane .report-pdf-placeholder");
    return {
      vh: window.innerHeight,
      exame: box(".report-source-pane"),
      visor: visor ? visor.getBoundingClientRect().height : null,
      conclusoes: box(".report-quick-conclusions"),
      texto: box(".report-final-text-field"),
      observacoes: box(".report-optional-block"),
      acao: box(".report-conclude-cta"),
      previa: box(".report-preview-pane"),
      domExameAntesConclusoes: segue(exame, conclusoes),
      domConclusoesAntesPrevia: segue(conclusoes, document.querySelector(".report-preview-pane")),
      overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
    };
  });
}

async function runLargura(browser, largura) {
  const movel = largura <= 480;
  const page = await browser.newPage({
    viewport: { width: largura, height: movel ? 844 : 900 },
    hasTouch: movel, isMobile: movel, deviceScaleFactor: movel ? 2 : 1,
  });
  const errors = await commonSetup(page);
  const falhas = [];
  let passed = 0;
  const check = async (nome, run) => {
    try { await run(); passed++; console.log(`PASS [${largura}px] ${nome}`); }
    catch (e) { falhas.push(`[${largura}px] ${nome}: ${e.message}`); console.log(`FAIL [${largura}px] ${nome}\n  ${e.message}`); }
  };
  const tocar = (sel) => (movel ? page.locator(sel).tap() : page.locator(sel).click());

  await abrirLaudo(page, baseDetail({ status: "atribuido" }), QUEUE_PENDENTE);
  // As capturas vêm ANTES de qualquer interação: é a tela que a médica vê
  // ao entrar.
  await page.screenshot({ path: path.join(shots, `entrada-${largura}.png`) });
  await page.screenshot({ path: path.join(shots, `pagina-${largura}.png`), fullPage: true });
  const m = await medir(page);
  fs.writeFileSync(path.join(shots, `medidas-${largura}.json`), JSON.stringify(m, null, 2));

  // ---------------------------------------------------------------- A
  await check("A — \"Como funciona\" nasce fechado, só título e seta", async () => {
    const howto = page.locator("[data-report-howto]");
    assert.equal(await howto.evaluate((el) => el.tagName), "DETAILS");
    assert.equal(await howto.evaluate((el) => el.open), false,
      "fila só com pendentes não pode mais abrir o bloco sozinho");
    assert.equal(await page.locator("[data-report-howto] > summary").isVisible(), true);
    assert.equal(await page.locator(".report-howto-list").isVisible(), false,
      "o texto explicativo só aparece depois do clique");
  });

  // ---------------------------------------------------------------- B
  await check("B — clique/toque abre, novo clique/toque fecha", async () => {
    await tocar("[data-report-howto] > summary");
    assert.equal(await page.locator("[data-report-howto]").evaluate((el) => el.open), true);
    assert.equal(await page.locator(".report-howto-list").isVisible(), true);
    assert.equal(await page.locator(".report-howto-step").count(), 6,
      "o conteúdo de ajuda continua inteiro");
    await tocar("[data-report-howto] > summary");
    assert.equal(await page.locator("[data-report-howto]").evaluate((el) => el.open), false);
    assert.equal(await page.locator(".report-howto-list").isVisible(), false);
  });

  await check("B — a escolha sobrevive à próxima renderização", async () => {
    await tocar("[data-report-howto] > summary");
    // Qualquer gesto da bancada re-renderiza a raiz inteira.
    await tocar('[data-report-conclusion="NORMAL"]');
    assert.equal(await page.locator("[data-report-howto]").evaluate((el) => el.open), true,
      "abrir e ver o bloco fechar sozinho no próximo render seria pior que antes");
    await tocar("[data-report-howto] > summary");
    await tocar("[data-report-conclusion-clear]");
    assert.equal(await page.locator("[data-report-howto]").evaluate((el) => el.open), false);
  });

  if (!movel) {
    await check("B — teclado: Enter abre, Espaço fecha, foco visível no resumo", async () => {
      await page.locator("[data-report-howto] > summary").focus();
      await page.keyboard.press("Enter");
      assert.equal(await page.locator("[data-report-howto]").evaluate((el) => el.open), true);
      await page.keyboard.press("Space");
      assert.equal(await page.locator("[data-report-howto]").evaluate((el) => el.open), false);
      assert.equal(await page.evaluate(
        () => document.activeElement.matches("[data-report-howto] > summary")), true);
    });
  }

  // ---------------------------------------------------------------- C / D
  await check("C — no DOM (teclado e leitor de tela) o exame técnico vem antes das conclusões", async () => {
    assert.equal(m.domExameAntesConclusoes, true);
    const ordem = await page.evaluate(() => [...document.querySelectorAll(
      ".report-source-pane h4, .report-quick-conclusions legend, .report-final-text-field, .report-optional-block > summary, .report-conclude-cta"
    )].map((el) => el.className || el.tagName));
    assert.equal(ordem.length, 5, JSON.stringify(ordem));
    assert.equal(ordem[0], "H4", `primeiro na leitura deveria ser o exame: ${JSON.stringify(ordem)}`);
  });

  await check("C — na tela o exame técnico é percebido antes das conclusões", async () => {
    if (largura > 1100) {
      // Lado a lado: exame à ESQUERDA, e as conclusões não começam acima
      // dele.
      assert.ok(m.exame.left + m.exame.width <= m.conclusoes.left + 1,
        `exame deveria ocupar a coluna da esquerda: ${JSON.stringify([m.exame, m.conclusoes])}`);
      assert.ok(m.exame.top <= m.conclusoes.top,
        `conclusões começam acima do exame: ${m.conclusoes.top} < ${m.exame.top}`);
    } else {
      // Empilhado: o exame termina antes de as conclusões começarem.
      assert.ok(m.exame.bottom <= m.conclusoes.top + 1,
        `exame (fim ${m.exame.bottom}) deveria vir acima das conclusões (início ${m.conclusoes.top})`);
    }
  });

  await check("C — conclusões → texto final → observações → concluir, nessa ordem", async () => {
    assert.ok(m.conclusoes.top < m.texto.top, "texto final acima das conclusões");
    assert.ok(m.texto.top < m.observacoes.top, "observações acima do texto final");
    // A CTA mora numa doca `sticky` no celular: a posição medida é a do
    // rodapé da tela, não a do fluxo. A ordem dela vale pelo DOM.
    assert.equal(await page.evaluate(() => Boolean(
      document.querySelector(".report-optional-block").compareDocumentPosition(
        document.querySelector(".report-conclude-cta")) & Node.DOCUMENT_POSITION_FOLLOWING)),
      true, "ação acima das observações");
  });

  await check("C — a prévia do laudo gerado não se mete entre o exame e as conclusões", async () => {
    assert.equal(m.domConclusoesAntesPrevia, true);
    if (largura <= 1100) {
      // Logo abaixo: entre o fim do exame e o início das conclusões só
      // cabe o título do formulário, nunca outro visualizador de PDF (o
      // menor deles tem 220px).
      const entre = await page.evaluate(() => {
        const ini = document.querySelector(".report-source-pane").getBoundingClientRect().bottom;
        const fim = document.querySelector(".report-quick-conclusions").getBoundingClientRect().top;
        return [...document.querySelectorAll(".report-pdf-frame, .report-pdf-placeholder")]
          .filter((el) => { const r = el.getBoundingClientRect(); return r.top >= ini - 1 && r.bottom <= fim + 1; })
          .length;
      });
      assert.equal(entre, 0, "há um visualizador de PDF entre o exame e as conclusões");
      assert.ok(m.conclusoes.top - m.exame.bottom < 140,
        `distância exame→conclusões ${Math.round(m.conclusoes.top - m.exame.bottom)}px`);
    }
  });

  // ---------------------------------------------------------------- E
  if (movel) {
    await check("E — visualizador do exame continua compacto", async () => {
      assert.ok(m.visor <= Math.ceil(m.vh * 0.40),
        `visualizador ${m.visor}px em viewport ${m.vh}px (teto 40vh)`);
      // Com o topo do exame no alto da tela, as conclusões já começam dentro
      // dela — não depois de mais uma tela inteira de PDF.
      assert.ok(m.conclusoes.top - m.exame.top < m.vh,
        `conclusões a ${Math.round(m.conclusoes.top - m.exame.top)}px do topo do exame (viewport ${m.vh}px)`);
    });

    await check("E — três siglas por linha", async () => {
      const colunas = await page.locator(".report-sigla-grid").first().evaluate(
        (el) => getComputedStyle(el).gridTemplateColumns.split(" ").length);
      assert.ok(colunas >= 3, `${colunas} colunas`);
    });

    await check("E — CTA na doca sticky", async () => {
      assert.equal(await page.locator(".report-action-dock")
        .evaluate((el) => getComputedStyle(el).position), "sticky");
      await page.locator(".report-quick-conclusions").scrollIntoViewIfNeeded();
      const visivel = await page.evaluate(() => {
        const b = document.querySelector(".report-conclude-cta").getBoundingClientRect();
        return b.top >= 0 && b.bottom <= window.innerHeight + 1;
      });
      assert.equal(visivel, true, "com as conclusões na tela, o botão precisa estar visível");
    });

    await check("E — observações e documentos seguem recolhidos", async () => {
      assert.equal(await page.locator(".report-optional-block").evaluate((el) => el.open), false);
      assert.equal(await page.locator(".report-documents-panel").evaluate((el) => el.open), false);
    });
  }

  await check("E — sem rolagem horizontal", async () => {
    assert.equal(m.overflow, false);
  });

  await check("E — conclusões rápidas intactas", async () => {
    assert.equal(await page.locator(".report-quick-conclusions").count(), 1);
    assert.equal(await page.locator("[data-report-conclusion]").count(), CATALOG.conclusoes.length);
    await tocar('[data-report-conclusion="DVO_LEVE"]');
    assert.equal(await page.locator('[data-report-conclusion="DVO_LEVE"]')
      .getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator("#reportFinalText").inputValue(),
      "Distúrbio ventilatório obstrutivo leve.");
    assert.equal(await page.locator(".report-conclude-cta").isDisabled(), false);
    await tocar("[data-report-conclusion-clear]");
    assert.equal(await page.locator(".report-conclude-cta").isDisabled(), true);
  });

  assert.deepEqual(errors, []);
  await page.close();
  return { passed, falhas };
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const falhas = [];
  let total = 0;
  try {
    for (const largura of [1440, 1024, 430, 390]) {
      const r = await runLargura(browser, largura);
      total += r.passed;
      falhas.push(...r.falhas);
    }
  } finally {
    await browser.close();
  }
  console.log(`\n${total} cenários passaram, ${falhas.length} falharam. Capturas: ${shots}`);
  if (falhas.length) {
    console.error("FALHA:\n" + falhas.join("\n"));
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error("FALHA:", error.message);
  process.exitCode = 1;
});
