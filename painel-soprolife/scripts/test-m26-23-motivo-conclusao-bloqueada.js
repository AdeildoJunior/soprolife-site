#!/usr/bin/env node
/* M26.23 — por que "Concluir e preparar para assinatura" fica cinza.
 *
 * O caso que originou esta etapa: a médica montou o "Texto final do laudo"
 * inteiro pelas FRASES FREQUENTES, viu o campo preenchido, e o botão
 * continuou desabilitado — sem nada na tela dizendo o que faltava. O que
 * faltava era a CONCLUSÃO do catálogo, que é um gate legítimo (o servidor
 * exige `conclusion_code` em três camadas) mas era um gate mudo.
 *
 * Estes cenários valem para QUALQUER laudo nesta interface: nada aqui
 * depende de paciente, exame ou documento específico.
 *
 * Navegador real, API simulada em memória, sem conexão com dados reais.
 * PLAYWRIGHT_MODULE pode apontar para uma instalação local já disponível.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const panel = path.resolve(__dirname, "..");
const screenshots = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m2623-ui-"));

const CATALOG = {
  conclusoes: [
    { codigo: "NORMAL", rotulo: "Normal", texto: "Espirometria dentro dos limites da normalidade.", personalizado: false },
    { codigo: "DVO_LEVE", rotulo: "DVO leve", texto: "Distúrbio ventilatório obstrutivo leve.", personalizado: false },
    { codigo: "PERSONALIZADO", rotulo: "Personalizada", texto: "", personalizado: true },
  ],
  complementos_bd: [
    { codigo: "BD_NAO_REALIZADO", rotulo: "Sem BD", texto: "" },
    { codigo: "BD_SEM_RESPOSTA", rotulo: "Sem resposta", texto: "Sem resposta significativa ao broncodilatador." },
  ],
  exame_com_pos_bd: false,
};

// `atribuido` é o mesmo status em que o laudo do caso real estava: a tela
// considera editável tanto `atribuido` quanto `em_elaboracao`.
function baseDetail(overrides) {
  return {
    id: "doc-1",
    public_code: "LAU-000001",
    status: "atribuido",
    corrects_document_id: null,
    correction_reason_code: null,
    locked: false,
    current_version_id: "ver-original",
    versoes: [
      { id: "ver-original", kind: "original", version_number: 1, mime_type: "application/pdf", page_count: 1 },
    ],
    patient: { full_name: "Paciente Sintético", date_of_birth: "2000-01-01", public_code: "PES-000001" },
    exam: { public_code: "ESP-000001", exam_date: "2027-01-10", exam_time: "09:00", post_bronchodilator: false },
    location: null,
    ...overrides,
  };
}

const QUEUE_ITEM = {
  document_id: "doc-1", status: "atribuido",
  patient: { full_name: "Paciente Sintético" }, exam_code: "ESP-000001", report_code: "LAU-000001",
  location_key: "pastore", location_name: "Pastore", is_corrective: false, correction_reason_code: null, locked: false,
};

async function commonSetup(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (msg) => {
    // O harness aborta TODA requisição de rede de propósito. Este cenário
    // abre um laudo que já tem PDF original, então o navegador tenta
    // buscá-lo e registra "Failed to load resource" — ruído do simulado,
    // não erro do app. Qualquer outro erro de console continua fatal.
    if (msg.type() === "error" && !/Failed to load resource/.test(msg.text())) {
      errors.push(msg.text());
    }
  });
  await page.route("**/*", (route) => route.abort());
  await page.route("**/data/m15-config.json", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      enabled: true,
      reports_mode: "production",
      reports_enabled: true,
      api_base: "/painel-soprolife/api/m15",
    }),
  }));
  await page.route("https://sopro-sintetico.test/painel/", (route) => route.fulfill({
    contentType: "text/html",
    body: '<html lang="pt-BR"><body style="margin:0;padding:12px;box-sizing:border-box">'
      + '<main style="max-width:1160px;margin:auto;min-width:0">'
      + '<div id="reportWorkflowRoot"></div></main></body></html>',
  }));
  await page.goto("https://sopro-sintetico.test/painel/");
  const html = fs.readFileSync(path.join(panel, "index.html"), "utf8");
  for (const match of html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) {
    try {
      await page.addStyleTag({ content: fs.readFileSync(path.join(panel, match[1]), "utf8") });
    } catch (_e) { /* folha opcional não encontrada — segue sem ela */ }
  }
  return errors;
}

async function abrirLaudo(page, detail) {
  await page.evaluate(({ catalog, detail: d, queueItem }) => {
    window.calls = [];
    window.SoproM15 = {
      hasToken: () => true,
      can: () => false,
      getUser: () => ({ nome: "Dra. Sintética Ana", papeis: ["medico"] }),
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
        if (url.endsWith("/documentos")) return [];
        if (url.endsWith("/laudo/previa")) {
          // Fiel ao servidor: gerar a prévia CRIA uma versão nativa com os
          // snapshots do catálogo (`reports.py`). É dela que a tela
          // reidrata a escolha quando recarrega o documento — sem isso o
          // simulado perderia a conclusão a cada prévia, o que não é o
          // comportamento real.
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
          return { final_text: body.final_text || "", preview_version_id: "prev-1", final_text_sha256: "hash1" };
        }
        throw new Error(`rota sintética não coberta: ${method} ${url}`);
      },
    };
  }, { catalog: CATALOG, detail, queueItem: QUEUE_ITEM });
  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-queue-item");
  await page.locator("[data-report-open]").click();
}

const cta = '.report-conclude-cta';
const previa = '[data-report-preview-only]';
const motivo = '#reportConcludeBlocker';

async function runCenarios(browser) {
  // `hasTouch` para que o cenário mobile possa usar `tap()` de verdade —
  // `tap()` não emite eventos de mouse, que é o ponto: se a inserção de
  // frase dependesse de hover/mouse, o celular da médica não funcionaria.
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 }, hasTouch: true,
  });
  const errors = await commonSetup(page);
  let passed = 0;
  async function check(name, run) { await run(); passed++; console.log(`PASS ${name}`); }

  await abrirLaudo(page, baseDetail());
  await page.waitForSelector("#reportFinalText");

  // ---------------------------------------------------------------- C + D
  await check("C/D — laudo recém-aberto: sem conclusão o botão fica desabilitado E diz por quê", async () => {
    assert.equal(await page.locator("#reportFinalText").inputValue(), "");
    assert.equal(await page.locator(cta).isDisabled(), true);
    assert.equal(await page.locator(previa).isDisabled(), true);
    const texto = await page.locator(motivo).innerText();
    assert.match(texto, /conclusão/i, `motivo deveria citar a conclusão; veio: ${texto}`);
    assert.doesNotMatch(texto, /campos pendentes/i, "motivo genérico é proibido");
  });

  // -------------------------------------------------------------------- B
  // A reprodução exata do caso relatado: texto final inteiro montado por
  // frases frequentes, campo visivelmente preenchido, botão ainda cinza.
  await check("B — texto montado só por frases frequentes NÃO habilita, e a tela explica que falta a conclusão", async () => {
    await page.locator(".report-frequent-phrase-chip", { hasText: "Redução de CVF e VEF1 isolados." }).click();
    await page.locator(".report-frequent-phrase-chip", { hasText: "Sem resposta significativa ao broncodilatador." }).click();
    assert.equal(
      await page.locator("#reportFinalText").inputValue(),
      "Redução de CVF e VEF1 isolados.\nSem resposta significativa ao broncodilatador.",
      "as frases deveriam ter montado o texto final"
    );
    assert.equal(await page.locator(cta).isDisabled(), true, "sem conclusão o gate legítimo continua de pé");
    assert.match(await page.locator(motivo).innerText(), /conclusão/i);
    // O motivo precisa dizer que as FRASES não substituem a conclusão —
    // era exatamente essa a confusão de quem viu o campo preenchido.
    assert.match(await page.locator(motivo).innerText(), /frases/i);
  });

  // -------------------------------------------------------------------- A
  await check("A/B — escolher a conclusão habilita, e o texto vindo dos chips é o que vai no payload", async () => {
    await page.getByRole("button", { name: "DVO leve", exact: true }).click();
    assert.equal(await page.locator(cta).isDisabled(), false, "com conclusão escolhida o botão precisa habilitar");
    assert.equal(await page.locator(previa).isDisabled(), false);
    assert.equal(await page.locator(motivo).count(), 0, "habilitado não mostra motivo de bloqueio");
    await page.locator(previa).click();
    await page.waitForFunction(() => window.calls.some((c) => c.url.endsWith("/laudo/previa")));
    const payload = await page.evaluate(() =>
      window.calls.filter((c) => c.url.endsWith("/laudo/previa")).pop().body
    );
    // Prova objetiva de que os chips atualizaram o ESTADO, não só o DOM:
    // o texto inserido por chip chega inteiro ao corpo da requisição.
    assert.equal(
      payload.final_text,
      "Redução de CVF e VEF1 isolados.\nSem resposta significativa ao broncodilatador."
    );
    assert.equal(payload.conclusion_code, "DVO_LEVE");
  });

  await check("A — digitação manual segue pelo mesmo caminho dos chips", async () => {
    await page.locator("#reportFinalText").fill("Redação inteiramente digitada pela médica.");
    assert.equal(await page.locator(cta).isDisabled(), false);
    await page.locator(previa).click();
    await page.waitForFunction(
      () => window.calls.filter((c) => c.url.endsWith("/laudo/previa")).length >= 2
    );
    const payload = await page.evaluate(() =>
      window.calls.filter((c) => c.url.endsWith("/laudo/previa")).pop().body
    );
    assert.equal(payload.final_text, "Redação inteiramente digitada pela médica.");
  });

  // -------------------------------------------------------------------- C
  await check("C — desmarcar a conclusão desabilita de novo e o motivo volta", async () => {
    await page.getByRole("button", { name: "DVO leve", exact: true }).click();
    assert.equal(await page.locator(cta).isDisabled(), true);
    assert.match(await page.locator(motivo).innerText(), /conclusão/i);
  });

  await check("mobile 390px — chip responde a toque (sem hover) e o motivo continua visível", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    const chip = page.locator(".report-frequent-phrase-chip").first();
    // `tap()` não emite mouseover: se a inserção dependesse de hover,
    // morreria aqui — que é o celular da médica.
    await chip.tap();
    assert.ok((await page.locator("#reportFinalText").inputValue()).length > 0);
    assert.equal(await page.locator(motivo).isVisible(), true, "o motivo não pode sumir no celular");
    await page.getByRole("button", { name: "Normal", exact: true }).tap();
    assert.equal(await page.locator(cta).isDisabled(), false, "toque no chip de conclusão precisa habilitar");
    await page.screenshot({ path: path.join(screenshots, "mobile-390.png"), fullPage: true });
  });

  await check("larguras 1440/1024/768/430/390 sem overflow horizontal com o motivo na tela", async () => {
    await page.getByRole("button", { name: "Normal", exact: true }).click(); // volta ao estado bloqueado
    assert.equal(await page.locator(motivo).count(), 1);
    for (const width of [1440, 1024, 768, 430, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
        true,
        `overflow horizontal em ${width}px`
      );
      await page.screenshot({ path: path.join(screenshots, `bloqueado-${width}.png`), fullPage: true });
    }
  });

  assert.deepEqual(errors, []);
  await page.close();
  return passed;
}

// -------------------------------------------------------------------- E
// Os gates que já existiam continuam de pé: em estado não editável não há
// formulário nenhum — e portanto nada para habilitar.
async function runNaoEditavel(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = await commonSetup(page);
  await abrirLaudo(page, baseDetail({ status: "liberado", locked: true }));
  await page.waitForSelector("#reportDetailHeading, .report-detail");
  assert.equal(await page.locator("#reportNativeForm").count(), 0, "laudo liberado não oferece formulário de conclusão");
  assert.equal(await page.locator(cta).count(), 0);
  assert.equal(await page.locator(motivo).count(), 0, "sem formulário não há motivo de bloqueio a exibir");
  assert.deepEqual(errors, []);
  await page.close();
  console.log("PASS E — laudo já liberado: gates existentes seguem, nenhum botão de conclusão aparece");
  return 1;
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const a = await runCenarios(browser);
    const b = await runNaoEditavel(browser);
    console.log(`${a + b} cenários passaram. Capturas sintéticas: ${screenshots}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error("FALHA:", error.message);
  process.exitCode = 1;
});
