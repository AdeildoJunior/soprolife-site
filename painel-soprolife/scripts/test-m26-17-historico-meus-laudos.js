#!/usr/bin/env node
/* M26.17 — "Meus laudos" agora busca a fila ativa (sem superados) e um
 * histórico recolhido (`?somente_superados=true`) em chamadas separadas,
 * espelhando o padrão já usado no Acompanhamento operacional (M26.13). Este
 * teste confirma que a tela faz as DUAS chamadas certas e que o histórico
 * aparece na seção recolhida "Históricos", não misturado na fila ativa.
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

  const ativa = [{
    document_id: "doc-038", status: "atribuido", public_code: "LAU-000038",
    patient: { full_name: "Paciente Sintética" }, exam_code: "ESP-000050", report_code: "LAU-000038",
    location_key: "downtown", location_name: "Shopping Downtown", is_corrective: true,
    correction_reason_code: "technical_document_correction", locked: false,
    has_corrective_successor: false, is_delivered: false,
  }];
  const historico = [
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
  ];

  await page.evaluate(({ ativa, historico }) => {
    window.calls = [];
    window.SoproM15 = {
      hasToken: () => true,
      can: () => false,
      getUser: () => ({ nome: "Dra. Sintética", papeis: ["medico"] }),
      onSessionChange: (cb) => { setTimeout(cb, 0); },
      apiUrl: (u) => u,
      hasSession: () => false,
      apiBlob: async () => new Blob([new Uint8Array([1, 2, 3])], { type: "application/pdf" }),
      api: async (url) => {
        window.calls.push(url);
        if (url === "/laudos/meus") return ativa;
        if (url === "/laudos/meus?somente_superados=true") return historico;
        if (url.startsWith("/laudos/meus")) return ativa;
        if (url === "/laudos/templates?catalog=clinical") return [];
        if (url === "/laudos/assinatura-externa/pendentes") return { laudos: [] };
        if (url.endsWith("/catalogo-conclusoes")) return { conclusoes: [], complementos_bd: [], exame_com_pos_bd: false };
        if (url.endsWith("/documentos")) return [];
        return [];
      },
    };
  }, { ativa, historico });

  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-queue-item");

  await check("a tela busca a fila ativa e o histórico em chamadas separadas", async () => {
    const calls = await page.evaluate(() => window.calls);
    assert.ok(calls.includes("/laudos/meus"), `esperava chamada a /laudos/meus, chamadas: ${calls}`);
    assert.ok(calls.includes("/laudos/meus?somente_superados=true"), `esperava chamada ao histórico, chamadas: ${calls}`);
  });

  await check("a fila ativa mostra só o documento vigente (LAU-000038)", async () => {
    await page.locator('[data-report-open="doc-038"]').waitFor();
    const superados = await page.locator('[data-report-open="doc-035"], [data-report-open="doc-036"]').count();
    assert.equal(superados, 0, "os documentos superados não podem estar na lista ativa");
  });

  await check("a seção 'Históricos' existe, recolhida, com os 2 superados", async () => {
    const details = page.locator(".report-queue-panel details.report-closed-catalog");
    await details.waitFor();
    assert.equal(await details.getAttribute("open"), null, "a seção de histórico deve nascer recolhida");
    const summary = await details.locator("summary").innerText();
    assert.match(summary, /Históricos \(2\)/, `resumo inesperado: ${summary}`);
    await details.locator("summary").click();
    const texto = await details.innerText();
    assert.match(texto, /LAU-000035/);
    assert.match(texto, /LAU-000036/);
    assert.match(texto, /Superado por corretiva/);
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
