#!/usr/bin/env node
/* M26.25 — conclusões rápidas unificadas, siglas no desktop, bancada
 * compacta no celular.
 *
 * O QUE ESTE ARQUIVO PROVA, e por quê cada coisa está aqui:
 *
 *   A tela tinha DUAS listas de botões parecidas com significados
 *   diferentes — os chips de conclusão (que definem `conclusion_code`) e as
 *   "frases frequentes" (que só escreviam no texto final). A médica clicava
 *   nas frases, via o laudo pronto na tela e o sistema continuava pedindo
 *   uma conclusão. A M26.23 passou a explicar o bloqueio; esta etapa remove
 *   a duplicidade que o produzia.
 *
 *   Nada do contrato clínico muda: `conclusion_code` continua obrigatório,
 *   o gate continua de pé, o motivo continua aparecendo. O que muda é o
 *   gesto — uma área só, siglas, e um caminho de leitura que funciona no
 *   toque.
 *
 * Navegador real, API simulada em memória, sem conexão com dados reais.
 * Nenhum paciente, exame ou laudo verdadeiro passa por aqui.
 * PLAYWRIGHT_MODULE pode apontar para uma instalação local já disponível.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const panel = path.resolve(__dirname, "..");
const shots = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m2625-ui-"));

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

async function abrirLaudo(page, detail) {
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
  }, { catalog: CATALOG, detail, queueItem: QUEUE_ITEM, documentos: DOCUMENTOS });
  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-queue-item");
  await page.locator("[data-report-open]").click();
  await page.waitForSelector("#reportFinalText");
  // O catálogo chega numa requisição PRÓPRIA, depois do detalhe: esperar só
  // pelo formulário deixa o teste numa corrida com "Carregando catálogo de
  // conclusões…". As siglas são o sinal de que ele chegou.
  await page.waitForSelector("[data-report-conclusion]");
}

const cta = ".report-conclude-cta";
const previa = "[data-report-preview-only]";
const motivo = "#reportConcludeBlocker";
const sigla = (codigo) => `[data-report-conclusion="${codigo}"]`;

// =====================================================================
// DESKTOP — 1440px, com mouse de verdade.
// =====================================================================
async function runDesktop(browser) {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 }, hasTouch: false,
  });
  const errors = await commonSetup(page);
  let passed = 0;
  const check = async (nome, run) => { await run(); passed++; console.log(`PASS ${nome}`); };

  await abrirLaudo(page, baseDetail());

  // ------------------------------------------------- unificação
  await check("unificação — existe UMA área principal, chamada Conclusões rápidas", async () => {
    assert.equal(await page.locator(".report-quick-conclusions").count(), 1);
    assert.match(
      await page.locator(".report-quick-conclusions legend").innerText(),
      /Conclusões rápidas/i
    );
    // Toda conclusão do catálogo virou sigla; nenhuma ficou de fora.
    assert.equal(
      await page.locator("[data-report-conclusion]").count(),
      CATALOG.conclusoes.length
    );
    // Agrupadas por família clínica, com a cor vindo do grupo do servidor.
    const grupos = await page.locator(".report-sigla-group").evaluateAll(
      (els) => els.map((el) => el.dataset.grupo)
    );
    assert.deepEqual(grupos,
      ["normal", "obstrutivo", "restritivo", "misto", "inespecifico", "personalizado"]);
  });

  await check("unificação — as frases que duplicavam o catálogo saíram do bloco de complementos", async () => {
    const complementos = await page.locator(".report-frequent-phrase-chip")
      .evaluateAll((els) => els.map((el) => el.dataset.reportFrequentPhrase));
    // As três que eram, caractere por caractere, o texto de uma entrada do
    // catálogo (NORMAL, DVO_LEVE, RBD_NEGATIVO) não podem voltar: é delas
    // que nascia o "laudo pronto na tela, sistema pedindo conclusão".
    for (const duplicada of [
      "Espirometria dentro dos limites da normalidade.",
      "Distúrbio ventilatório obstrutivo leve.",
      "Sem resposta significativa ao broncodilatador.",
    ]) {
      assert.ok(!complementos.includes(duplicada),
        `"${duplicada}" voltou a existir como complemento — é uma conclusão do catálogo`);
    }
    assert.ok(complementos.length >= 2, "os complementos legítimos precisam continuar na tela");
    // E o bloco diz, antes do clique, que complemento não conclui laudo.
    assert.match(
      await page.locator(".report-text-complements").innerText(),
      /não concluem o laudo/i
    );
  });

  // ------------------------------------------------------------ D
  await check("D — sem conclusão o gate continua bloqueado e o motivo continua na tela", async () => {
    assert.equal(await page.locator(cta).isDisabled(), true);
    assert.equal(await page.locator(previa).isDisabled(), true);
    const texto = await page.locator(motivo).innerText();
    assert.match(texto, /conclus/i);
    assert.match(texto, /complement/i);
    assert.match(
      await page.locator(".report-sigla-preview").innerText(),
      /Nenhuma conclusão escolhida/i
    );
  });

  // ------------------------------------------------------------ E
  await check("E — hover numa sigla mostra o texto completo, sem depender de `title`", async () => {
    const chip = page.locator(sigla("DVR_SUG_MODERADO"));
    // `title` não pode ser a solução: não responde a foco de teclado e não
    // é estilizável. O balão é um elemento de verdade.
    assert.equal(await chip.getAttribute("title"), null);
    const tip = chip.locator(".report-sigla-tip");
    assert.equal(
      await tip.evaluate((el) => getComputedStyle(el).visibility), "hidden",
      "o balão não pode nascer visível — seriam 7 textos longos permanentes"
    );
    await chip.hover();
    await page.waitForFunction(
      (s) => {
        const el = document.querySelector(`${s} .report-sigla-tip`);
        return el && getComputedStyle(el).visibility === "visible";
      },
      sigla("DVR_SUG_MODERADO")
    );
    assert.match(await tip.innerText(),
      /Padrão sugestivo de distúrbio ventilatório restritivo moderado\./);
  });

  await check("E — o mesmo texto aparece por FOCO DE TECLADO, não só por mouse", async () => {
    // Tab a partir da bolha de ajuda do bloco: o foco chega na primeira
    // sigla vindo do teclado, que é o que liga `:focus-visible`.
    await page.locator(".report-quick-conclusions .report-help-toggle").first().focus();
    await page.keyboard.press("Tab");
    const foco = await page.evaluate(() => {
      const el = document.activeElement;
      return el ? el.getAttribute("data-report-conclusion") : null;
    });
    assert.equal(foco, "NORMAL", "o Tab deveria pousar na primeira sigla");
    assert.equal(
      await page.evaluate(() => document.activeElement.matches(":focus-visible")),
      true,
      "o chip precisa casar `:focus-visible` — é o que liga o balão sem mouse"
    );
    // A abertura é animada (0,12s): esperar a transição, não a medir no
    // meio dela.
    await page.waitForFunction(() => {
      const el = document.activeElement.querySelector(".report-sigla-tip");
      return el && getComputedStyle(el).visibility === "visible";
    });
    assert.match(
      await page.evaluate(
        () => document.activeElement.querySelector(".report-sigla-tip").innerText),
      /Espirometria dentro dos limites da normalidade\./
    );
  });

  await check("acessibilidade — o nome do botão carrega sigla, grau e texto por extenso", async () => {
    assert.equal(
      await page.locator(sigla("DVO_GRAVE")).getAttribute("aria-label"),
      "DVO Grave. Distúrbio ventilatório obstrutivo grave."
    );
    // Os `<span>` visuais são `aria-hidden` para o nome não sair duplicado.
    assert.equal(
      await page.locator(`${sigla("DVO_GRAVE")} .report-sigla`).getAttribute("aria-hidden"),
      "true"
    );
  });

  // ------------------------------------------------------------ A
  await check("A — clicar numa sigla define o código, escreve o texto e habilita o botão", async () => {
    await page.locator(sigla("DVO_LEVE")).click();
    assert.equal(await page.locator(sigla("DVO_LEVE")).getAttribute("aria-pressed"), "true");
    assert.equal(
      await page.locator("#reportFinalText").inputValue(),
      "Distúrbio ventilatório obstrutivo leve."
    );
    assert.equal(await page.locator(cta).isDisabled(), false);
    assert.equal(await page.locator(previa).isDisabled(), false);
    assert.equal(await page.locator(motivo).count(), 0);
    // E o payload que vai ao servidor carrega o código, não só o texto.
    await page.locator(previa).click();
    await page.waitForFunction(() => window.calls.some((c) => c.url.endsWith("/laudo/previa")));
    const payload = await page.evaluate(
      () => window.calls.filter((c) => c.url.endsWith("/laudo/previa")).pop().body
    );
    assert.equal(payload.conclusion_code, "DVO_LEVE");
    assert.equal(payload.final_text, "Distúrbio ventilatório obstrutivo leve.");
  });

  // ------------------------------------------------------------ B
  await check("B — editar o texto à mão depois da escolha mantém a conclusão e o fluxo válido", async () => {
    await page.locator("#reportFinalText").fill("Redação inteiramente reescrita pela médica.");
    assert.equal(await page.locator(sigla("DVO_LEVE")).getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator(cta).isDisabled(), false);
    await page.locator(previa).click();
    await page.waitForFunction(
      () => window.calls.filter((c) => c.url.endsWith("/laudo/previa")).length >= 2
    );
    const payload = await page.evaluate(
      () => window.calls.filter((c) => c.url.endsWith("/laudo/previa")).pop().body
    );
    assert.equal(payload.conclusion_code, "DVO_LEVE");
    assert.equal(payload.final_text, "Redação inteiramente reescrita pela médica.");
  });

  // ------------------------------------------------------------ C
  await check("C — trocar de conclusão troca o código e preserva o texto que a médica escreveu", async () => {
    await page.locator(sigla("DVI")).click();
    assert.equal(await page.locator(sigla("DVI")).getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator(sigla("DVO_LEVE")).getAttribute("aria-pressed"), "false");
    // O texto autoral NÃO é sobrescrito em silêncio — regra da M25.2.
    assert.equal(
      await page.locator("#reportFinalText").inputValue(),
      "Redação inteiramente reescrita pela médica."
    );
    assert.match(await page.locator("#reportStatus").innerText(), /preservado/i);
    // A prévia da seleção já mostra o texto da conclusão NOVA.
    assert.match(
      await page.locator(".report-sigla-preview").innerText(),
      /Padrão sugestivo de distúrbio ventilatório inespecífico\./
    );
  });

  await check("C — sobre um texto ainda não editado, trocar de sigla troca o template inteiro", async () => {
    await page.locator("[data-report-conclusion-clear]").click();
    await page.locator("#reportFinalText").fill("");
    await page.locator(sigla("NORMAL")).click();
    assert.equal(
      await page.locator("#reportFinalText").inputValue(),
      "Espirometria dentro dos limites da normalidade."
    );
    await page.locator(sigla("DVO_GRAVE")).click();
    assert.equal(
      await page.locator("#reportFinalText").inputValue(),
      "Distúrbio ventilatório obstrutivo grave."
    );
  });

  await check("o complemento pós-BD compõe com a conclusão, e continua sendo complemento", async () => {
    await page.locator('[data-report-bd="RBD_NEGATIVO"]').click();
    assert.equal(
      await page.locator("#reportFinalText").inputValue(),
      "Distúrbio ventilatório obstrutivo grave.\nSem resposta significativa ao broncodilatador."
    );
    // Segundo toque não desfaz; limpar é ato próprio, como na conclusão.
    await page.locator('[data-report-bd="RBD_NEGATIVO"]').click();
    assert.equal(
      await page.locator('[data-report-bd="RBD_NEGATIVO"]').getAttribute("aria-pressed"), "true");
    await page.locator("[data-report-bd-clear]").click();
    assert.equal(
      await page.locator('[data-report-bd="RBD_NEGATIVO"]').getAttribute("aria-pressed"), "false");
  });

  // ------------------------------------------------------------ I
  await check("I — prévia, conclusão e confirmação seguem o mesmo caminho de antes", async () => {
    await page.locator(sigla("NORMAL")).click();
    await page.locator("#reportFinalText").fill("Texto do laudo sintético.");
    await page.locator(cta).click();
    await page.waitForSelector(".report-release-confirm");
    assert.match(
      await page.locator("#reportReleaseConfirmTitle").innerText(),
      /Concluir este laudo\?/
    );
    await page.locator("[data-report-release-confirm]").click();
    await page.waitForFunction(
      () => window.calls.some((c) => c.url.endsWith("/assinar-e-liberar"))
    );
    const chamada = await page.evaluate(
      () => window.calls.filter((c) => c.url.endsWith("/assinar-e-liberar")).pop()
    );
    assert.equal(chamada.body.confirmacao, "ASSINAR E LIBERAR");
    assert.equal(chamada.body.expected_version_id, "prev-1");
  });

  await check("I — documentos e assinatura continuam na tela, agora recolhidos", async () => {
    await page.waitForSelector(".report-documents-panel");
    assert.equal(await page.locator(".report-documents-panel").count(), 1);
    assert.equal(
      await page.locator(".report-documents-panel").evaluate((el) => el.tagName), "DETAILS");
    assert.equal(
      await page.locator(".report-documents-panel").evaluate((el) => el.open), false,
      "durante a elaboração os PDFs não ocupam o caminho principal");
    // Recolhido não é sumido: o conteúdo continua inteiro lá dentro.
    await page.locator(".report-documents-panel > summary").click();
    assert.match(
      await page.locator(".report-documents-panel").innerText(),
      /Exame técnico \(MIR\)/);
    assert.equal(await page.locator(".report-signature-panel").count(), 1);
  });

  assert.deepEqual(errors, []);
  await page.screenshot({ path: path.join(shots, "desktop-1440.png"), fullPage: true });
  await page.close();
  return passed;
}

// =====================================================================
// MOBILE — 390px e 430px, com toque de verdade e sem mouse nenhum.
// =====================================================================
async function runMobile(browser, largura) {
  const page = await browser.newPage({
    viewport: { width: largura, height: 844 },
    hasTouch: true, isMobile: true, deviceScaleFactor: 2,
  });
  const errors = await commonSetup(page);
  let passed = 0;
  const check = async (nome, run) => { await run(); passed++; console.log(`PASS [${largura}px] ${nome}`); };

  await abrirLaudo(page, baseDetail());

  // ------------------------------------------------------------ F
  await check("F — o toque seleciona, sem nenhum evento de mouse", async () => {
    await page.locator(sigla("DVO_LEVE")).tap();
    assert.equal(await page.locator(sigla("DVO_LEVE")).getAttribute("aria-pressed"), "true");
    assert.equal(
      await page.locator("#reportFinalText").inputValue(),
      "Distúrbio ventilatório obstrutivo leve."
    );
    assert.equal(await page.locator(cta).isDisabled(), false);
  });

  await check("F — o texto completo fica acessível sem hover, na prévia da seleção", async () => {
    // No toque o balão nem existe na tela: `hover: none` o desliga.
    const balao = await page.locator(`${sigla("DVO_LEVE")} .report-sigla-tip`)
      .evaluate((el) => getComputedStyle(el).display);
    assert.equal(balao, "none", "balão de hover não pode ocupar espaço no celular");
    // O caminho do toque é a prévia, e ela mostra o texto por extenso.
    assert.match(
      await page.locator(".report-sigla-preview").innerText(),
      /Distúrbio ventilatório obstrutivo leve\./
    );
    // Trocar de opção é claro e imediato.
    await page.locator(sigla("DVI")).tap();
    assert.match(
      await page.locator(".report-sigla-preview").innerText(),
      /distúrbio ventilatório inespecífico\./
    );
  });

  // ------------------------------------------------------------ G
  await check("G — toque repetido na mesma conclusão não apaga a seleção", async () => {
    await page.locator(sigla("DVI")).tap();
    await page.locator(sigla("DVI")).tap();
    assert.equal(await page.locator(sigla("DVI")).getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator(cta).isDisabled(), false);
    assert.equal(await page.locator(motivo).count(), 0);
  });

  await check("G — limpar existe, é explícito, e devolve exatamente o estado bloqueado", async () => {
    await page.locator("[data-report-conclusion-clear]").tap();
    assert.equal(await page.locator(sigla("DVI")).getAttribute("aria-pressed"), "false");
    assert.equal(await page.locator(cta).isDisabled(), true);
    assert.match(await page.locator(motivo).innerText(), /conclus/i);
  });

  // ------------------------------------------------------------ H
  await check("H — nenhuma largura produz rolagem horizontal da página", async () => {
    await page.locator(sigla("NORMAL")).tap();
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
      true, `overflow horizontal em ${largura}px`
    );
  });

  await check("H — a ação principal fica ao alcance enquanto se escolhe a conclusão", async () => {
    await page.locator(".report-quick-conclusions").scrollIntoViewIfNeeded();
    const visivel = await page.evaluate(() => {
      const b = document.querySelector(".report-conclude-cta").getBoundingClientRect();
      return b.top >= 0 && b.bottom <= window.innerHeight + 1;
    });
    assert.equal(visivel, true,
      "com as conclusões na tela, o botão de concluir precisa estar visível");
  });

  await check("H — a doca gruda no rodapé sem cobrir conteúdo nenhum", async () => {
    const posicao = await page.locator(".report-action-dock")
      .evaluate((el) => getComputedStyle(el).position);
    assert.equal(posicao, "sticky",
      "`fixed` cobriria o conteúdo; `sticky` ocupa o próprio espaço no fluxo");
    // No fim da página, o que estiver embaixo da doca precisa ser ela
    // mesma ou o que está por baixo dela — nunca conteúdo escondido.
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(250);
    const coberto = await page.evaluate(() => {
      const dock = document.querySelector(".report-action-dock");
      const r = dock.getBoundingClientRect();
      // Se a doca já saiu da viewport, não há nada a cobrir.
      if (r.bottom <= 0 || r.top >= window.innerHeight) return false;
      const alvo = document.elementFromPoint(
        Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
      return !(alvo && dock.contains(alvo));
    });
    assert.equal(coberto, false, "a doca não pode ficar por cima de outro conteúdo");
  });

  await check("compacidade — os blocos secundários nascem recolhidos", async () => {
    // Observações vazias e documentos do exame saem do caminho; o fluxo
    // clínico não perdeu nada, só deixou de ocupar a tela.
    assert.equal(
      await page.locator(".report-optional-block").evaluate((el) => el.open), false);
    assert.equal(
      await page.locator(".report-documents-panel").evaluate((el) => el.open), false);
    assert.equal(
      await page.locator(".report-signature-panel").evaluate((el) => el.open), false);
  });

  await page.screenshot({ path: path.join(shots, `mobile-${largura}.png`), fullPage: true });
  assert.deepEqual(errors, []);
  await page.close();
  return passed;
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    let total = await runDesktop(browser);
    total += await runMobile(browser, 390);
    total += await runMobile(browser, 430);
    console.log(`\n${total} cenários passaram. Capturas sintéticas: ${shots}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error("FALHA:", error.message);
  process.exitCode = 1;
});
