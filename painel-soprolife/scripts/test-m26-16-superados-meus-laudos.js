#!/usr/bin/env node
/* M26.16 — em "Meus laudos", um documento já SUPERADO por uma corretiva
 * mais nova não pode aparecer só com o chip de status antigo ("Concluído —
 * aguardando assinatura qualificada"), como se ainda precisasse de ação.
 * Navegador real, API simulada em memória, sem conexão com dados reais.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const panel = path.resolve(__dirname, "..");

let browserRef = null;

async function main() {
  const browser = await chromium.launch({ headless: true });
  browserRef = browser;
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
    body: '<html lang="pt-BR"><body><div id="reportWorkflowRoot"></div></body></html>',
  }));
  await page.goto("https://sopro-sintetico.test/painel/");
  const html = fs.readFileSync(path.join(panel, "index.html"), "utf8");
  for (const match of html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) {
    try { await page.addStyleTag({ content: fs.readFileSync(path.join(panel, match[1]), "utf8") }); } catch (_e) {}
  }

  let passed = 0;
  async function check(name, run) { await run(); passed++; console.log(`PASS ${name}`); }

  // Reproduz o caso real: LAU-000035 (raiz, superada), LAU-000036
  // (corretiva de conteúdo, também já superada pela LAU-000038), LAU-000038
  // (fim da cadeia — vigente, sem sucessora).
  const filaMedica = [
    {
      document_id: "doc-035", status: "liberado", public_code: "LAU-000035",
      patient: { full_name: "Paciente Sintética" }, exam_code: "ESP-000050", report_code: "LAU-000035",
      location_key: "downtown", location_name: "Shopping Downtown", is_corrective: false,
      correction_reason_code: null, locked: true,
      has_corrective_successor: true, is_delivered: false,
    },
    {
      document_id: "doc-036", status: "liberado", public_code: "LAU-000036",
      patient: { full_name: "Paciente Sintética" }, exam_code: "ESP-000050", report_code: "LAU-000036",
      location_key: "downtown", location_name: "Shopping Downtown", is_corrective: true,
      correction_reason_code: "clinical_correction", locked: true,
      has_corrective_successor: true, is_delivered: false,
    },
    {
      document_id: "doc-038", status: "atribuido", public_code: "LAU-000038",
      patient: { full_name: "Paciente Sintética" }, exam_code: "ESP-000050", report_code: "LAU-000038",
      location_key: "downtown", location_name: "Shopping Downtown", is_corrective: true,
      correction_reason_code: "technical_document_correction", locked: false,
      has_corrective_successor: false, is_delivered: false,
    },
  ];

  await page.evaluate((filaMedica) => {
    window.SoproM15 = {
      hasToken: () => true,
      can: () => false,
      getUser: () => ({ nome: "Dra. Sintética", papeis: ["medico"] }),
      onSessionChange: (cb) => { setTimeout(cb, 0); },
      apiUrl: (u) => u,
      hasSession: () => false,
      apiBlob: async () => new Blob([new Uint8Array([1, 2, 3])], { type: "application/pdf" }),
      api: async (url) => {
        if (url.startsWith("/laudos/meus")) return filaMedica;
        if (url === "/laudos/templates?catalog=clinical") return [];
        if (url === "/laudos/assinatura-externa/pendentes") return { laudos: [] };
        if (url.endsWith("/catalogo-conclusoes")) return { conclusoes: [], complementos_bd: [], exame_com_pos_bd: false };
        if (url.endsWith("/documentos")) return [];
        return [];
      },
    };
  }, filaMedica);

  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-queue-item");

  await check("LAU-000035 (raiz, superada) mostra a etiqueta de superada", async () => {
    const card = page.locator('[data-report-open="doc-035"]');
    await assert.doesNotReject(card.locator(".report-queue-flag", { hasText: "Superado por corretiva" }).waitFor());
  });

  await check("LAU-000036 (corretiva também já superada) mostra a etiqueta de superada", async () => {
    const card = page.locator('[data-report-open="doc-036"]');
    await assert.doesNotReject(card.locator(".report-queue-flag", { hasText: "Superado por corretiva" }).waitFor());
  });

  await check("LAU-000038 (fim da cadeia, vigente) NÃO mostra a etiqueta de superada", async () => {
    const card = page.locator('[data-report-open="doc-038"]');
    const texto = await card.innerText();
    assert.ok(!texto.includes("Superado por corretiva"), `cartão vigente não pode dizer 'superado': ${texto}`);
  });

  assert.deepEqual(errors, []);
  console.log(`${passed} cenário(s) passaram.`);
  await browser.close();
}

main().catch(async (error) => {
  console.error("FALHA:", error.message);
  process.exitCode = 1;
  if (browserRef) await browserRef.close();
});
