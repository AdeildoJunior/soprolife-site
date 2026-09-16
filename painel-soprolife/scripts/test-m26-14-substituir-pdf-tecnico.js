#!/usr/bin/env node
/* M26.14 — botão "Substituir PDF técnico" na fila operacional.
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
const screenshots = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m2614-ui-"));

const ITEM_CORRETIVA_NAO_INICIADA = {
  document_id: "doc-corretiva", status: "atribuido", is_corrective: true,
  correction_reason_code: "technical_document_correction",
  patient: { full_name: "Paciente Corretiva" }, exam_code: "ESP-000090", report_code: "LAU-000090",
  location_key: "pastore", location_name: "Pastore", has_corrective_successor: false, is_delivered: false,
  locked: false, encerramento: null,
};
const ITEM_EM_ELABORACAO = {
  ...ITEM_CORRETIVA_NAO_INICIADA, document_id: "doc-em-elaboracao", status: "em_elaboracao",
  report_code: "LAU-000091", exam_code: "ESP-000091", patient: { full_name: "Paciente Em Elaboração" },
};
const ITEM_NAO_CORRETIVA = {
  ...ITEM_CORRETIVA_NAO_INICIADA, document_id: "doc-original", status: "atribuido", is_corrective: false,
  correction_reason_code: null, report_code: "LAU-000092", exam_code: "ESP-000092",
  patient: { full_name: "Paciente Original" },
};

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", (route) => route.abort());
  await page.route("**/data/m15-config.json", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ enabled: true, reports_mode: "production", reports_enabled: true, api_base: "/painel-soprolife/api/m15" }),
  }));
  await page.route("https://sopro-sintetico.test/painel/", (route) => route.fulfill({
    contentType: "text/html",
    body: '<html lang="pt-BR"><body style="margin:0;padding:12px;box-sizing:border-box">'
      + '<main style="max-width:1160px;margin:auto;min-width:0"><div id="reportWorkflowRoot"></div></main></body></html>',
  }));
  await page.goto("https://sopro-sintetico.test/painel/");
  const html = fs.readFileSync(path.join(panel, "index.html"), "utf8");
  for (const match of html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) {
    try { await page.addStyleTag({ content: fs.readFileSync(path.join(panel, match[1]), "utf8") }); } catch (_e) {}
  }

  let passed = 0;
  async function check(name, run) { await run(); passed++; console.log(`PASS ${name}`); }

  await page.evaluate(({ items }) => {
    window.calls = [];
    window.SoproM15 = {
      hasToken: () => true,
      can: (role) => ["admin", "operacional"].includes(role),
      getUser: () => ({ nome: "Admin Sintético", papeis: ["admin", "operacional"] }),
      onSessionChange: (cb) => { setTimeout(cb, 0); },
      apiUrl: (u) => u,
      hasSession: () => false,
      apiBlob: async () => new Blob(),
      api: async (url, options) => {
        const method = (options && options.method) || "GET";
        window.calls.push({ url, method, isFormData: options && options.body instanceof FormData });
        if (url === "/laudos" || url === "/laudos?somente_superados=true") return items;
        if (url === "/laudos/medicos-disponiveis") return [];
        if (url === "/laudos/exames?somente_sem_laudo=true") return { itens: [] };
        if (url === "/laudos/assinatura-externa/fila") return { estados: [], itens: [] };
        if (url === "/laudos/exames/encerrados") return { itens: [] };
        if (url === "/laudos/exames/motivos-encerramento") return { motivos: [] };
        if (url === "/laudos/admin/medicos") return [];
        if (url.endsWith("/pdf-tecnico-original")) {
          if (window.failNext) { window.failNext = false; const e = new Error("A médica já começou a elaborar este laudo."); e.code = "corretiva_ja_iniciada"; throw e; }
          return { id: url.split("/")[2], versoes: [] };
        }
        throw new Error(`rota sintética não coberta: ${method} ${url}`);
      },
    };
  }, { items: [ITEM_CORRETIVA_NAO_INICIADA, ITEM_EM_ELABORACAO, ITEM_NAO_CORRETIVA] });

  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-operation-row");

  await check("botão só aparece ao selecionar uma corretiva ainda não iniciada", async () => {
    assert.equal(await page.locator("#reportReplaceOriginalPdfForm").count(), 0);
    await page.locator('[data-report-operational="doc-em-elaboracao"]').click();
    assert.equal(await page.locator("#reportReplaceOriginalPdfForm").count(), 0, "já em elaboração não deveria mostrar o formulário");
    await page.locator('[data-report-operational="doc-original"]').click();
    assert.equal(await page.locator("#reportReplaceOriginalPdfForm").count(), 0, "documento original (não corretiva) não deveria mostrar o formulário");
    await page.locator('[data-report-operational="doc-corretiva"]').click();
    assert.equal(await page.locator("#reportReplaceOriginalPdfForm").count(), 1);
  });

  await check("envio manda multipart/form-data para o endpoint certo", async () => {
    await page.setInputFiles("#reportReplaceOriginalPdfFile", { name: "corrigido.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 conteudo sintetico") });
    await page.locator('#reportReplaceOriginalPdfForm button[type="submit"]').click();
    await page.waitForFunction(() => window.calls.some((c) => c.url.endsWith("/pdf-tecnico-original")));
    const call = await page.evaluate(() => window.calls.find((c) => c.url.endsWith("/pdf-tecnico-original")));
    assert.equal(call.url, "/laudos/doc-corretiva/pdf-tecnico-original");
    assert.equal(call.method, "POST");
    assert.equal(call.isFormData, true);
  });

  await check("erro do servidor aparece perto da ação, sem travar a tela", async () => {
    await page.evaluate(() => { window.failNext = true; });
    await page.setInputFiles("#reportReplaceOriginalPdfFile", { name: "corrigido2.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 outro") });
    await page.locator('#reportReplaceOriginalPdfForm button[type="submit"]').click();
    await page.waitForFunction(() => document.getElementById("reportStatus").textContent.includes("começou a elaborar"));
    assert.equal(await page.locator("#reportReplaceOriginalPdfForm").count(), 1, "o formulário deveria continuar disponível após erro");
    assert.equal(
      await page.evaluate(() => document.activeElement === document.getElementById("reportStatus")),
      true,
      "o foco deveria ir para a mensagem de status"
    );
  });

  await check("larguras 1440/768/390 sem overflow com o formulário aberto", async () => {
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, `overflow em ${width}px`);
      await page.screenshot({ path: path.join(screenshots, `substituir-pdf-${width}.png`), fullPage: true });
    }
  });

  assert.deepEqual(errors, []);
  console.log(`${passed} cenários passaram. Capturas sintéticas: ${screenshots}`);
  await browser.close();
}

main().catch((error) => {
  console.error("FALHA:", error.message);
  process.exitCode = 1;
});
