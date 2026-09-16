#!/usr/bin/env node
/* M26.15 — o "Exame técnico (MIR)" tem que mostrar a versão VIGENTE do PDF
 * original, não a primeira do array, quando existir mais de uma (M26.14
 * passou a permitir isso ao substituir o PDF técnico de uma corretiva).
 * Navegador real, API simulada em memória, sem conexão com dados reais.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const panel = path.resolve(__dirname, "..");

const VERSAO_ANTIGA_ID = "versao-original-antiga";
const VERSAO_NOVA_ID = "versao-original-nova";

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
    body: '<html lang="pt-BR"><body><div id="reportWorkflowRoot"></div></body></html>',
  }));
  await page.goto("https://sopro-sintetico.test/painel/");
  const html = fs.readFileSync(path.join(panel, "index.html"), "utf8");
  for (const match of html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) {
    try { await page.addStyleTag({ content: fs.readFileSync(path.join(panel, match[1]), "utf8") }); } catch (_e) {}
  }

  let passed = 0;
  async function check(name, run) { await run(); passed++; console.log(`PASS ${name}`); }

  const queueItem = {
    document_id: "doc-corretiva-2", status: "atribuido",
    patient: { full_name: "Paciente Sintética" }, exam_code: "ESP-000099", report_code: "LAU-000099",
    location_key: "pastore", location_name: "Pastore", is_corrective: true, correction_reason_code: "technical_document_correction", locked: false,
  };
  const detail = {
    id: "doc-corretiva-2", public_code: "LAU-000099", status: "atribuido",
    corrects_document_id: "doc-corretiva-1", correction_reason_code: "technical_document_correction",
    locked: false, current_version_id: VERSAO_NOVA_ID,
    versoes: [
      { id: VERSAO_ANTIGA_ID, kind: "original", version_number: 1, sha256: "a".repeat(64), size_bytes: 10, page_count: 1 },
      { id: VERSAO_NOVA_ID, kind: "original", version_number: 2, sha256: "b".repeat(64), size_bytes: 10, page_count: 1 },
    ],
    patient: { full_name: "Paciente Sintética", date_of_birth: "2000-01-01", public_code: "PES-000099" },
    exam: { public_code: "ESP-000099", exam_date: "2027-01-10", exam_time: "09:00", post_bronchodilator: false },
    location: null,
  };

  await page.evaluate(({ queueItem, detail }) => {
    window.calls = [];
    window.SoproM15 = {
      hasToken: () => true,
      can: () => false,
      getUser: () => ({ nome: "Dra. Sintética", papeis: ["medico"] }),
      onSessionChange: (cb) => { setTimeout(cb, 0); },
      apiUrl: (u) => u,
      hasSession: () => false,
      apiBlob: async (url) => { window.calls.push({ url, kind: "blob" }); return new Blob([new Uint8Array([1, 2, 3])], { type: "application/pdf" }); },
      api: async (url) => {
        window.calls.push({ url, kind: "json" });
        if (url.startsWith("/laudos/meus")) return [queueItem];
        if (url === "/laudos/templates?catalog=clinical") return [];
        if (url === "/laudos/assinatura-externa/pendentes") return { laudos: [] };
        if (url === "/laudos/doc-corretiva-2") return detail;
        if (url.endsWith("/catalogo-conclusoes")) return { conclusoes: [], complementos_bd: [], exame_com_pos_bd: false };
        if (url.endsWith("/documentos")) return [];
        return [];
      },
    };
  }, { queueItem, detail });

  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-queue-item");
  await page.locator("[data-report-open]").click();
  await page.waitForFunction(() => window.calls.some((c) => c.kind === "blob"));

  await check("o PDF técnico carregado é o da versão VIGENTE, não a primeira do array", async () => {
    const blobCalls = await page.evaluate(() => window.calls.filter((c) => c.kind === "blob"));
    const originalCall = blobCalls.find((c) => c.url.includes("/versoes/"));
    assert.ok(originalCall, "deveria ter buscado algum conteúdo de versão");
    assert.match(originalCall.url, new RegExp(`/versoes/${VERSAO_NOVA_ID}/`), `esperava a versão vigente (${VERSAO_NOVA_ID}), pegou: ${originalCall.url}`);
    assert.ok(!originalCall.url.includes(VERSAO_ANTIGA_ID), "não deveria ter buscado a versão antiga");
  });

  assert.deepEqual(errors, []);
  console.log(`${passed} cenário(s) passaram.`);
  await browser.close();
}

main().catch((error) => {
  console.error("FALHA:", error.message);
  process.exitCode = 1;
});
