#!/usr/bin/env node
/* M26.36C — a corretiva cuja devolução foi cancelada, na tela.
 *
 * O servidor já a tira das filas ativas (ver
 * tests/test_m26_36c_corretiva_cancelada_fora_das_filas.py). Este teste
 * cobre a outra metade, que é do navegador:
 *
 *   1. a "Fila de laudos" desenha os contadores EXATAMENTE como o servidor
 *      os manda — nenhum chip é recalculado aqui. Era a dúvida legítima
 *      quando a tela mostrou "Aguardando laudo 3" com dois pendentes: se o
 *      número fosse do navegador, corrigir o backend não resolveria nada.
 *   2. "Históricos operacionais" NOMEIA a linha cancelada pelo que ela é.
 *      Antes havia três textos para dois testes, e o `else` dizia "Entregue
 *      ao paciente" — uma entrega que nunca houve, sobre um laudo que nunca
 *      foi escrito.
 *
 * Navegador real, API simulada em memória, sem conexão com dados reais.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const panel = path.resolve(__dirname, "..");

let browserRef = null;

function linha(extra) {
  return Object.assign({
    patient: { full_name: "Paciente Sintética" },
    exam_code: "ESP-000050", exam_date: "2026-10-20",
    location_key: "domicilio", location_name: "Atendimento domiciliar",
    status: "liberado", is_corrective: false, correction_reason_code: null,
    locked: true, has_corrective_successor: false, is_delivered: false,
    devolucao_cancelada_em: null, encerramento: null,
    assignment_id: null, physician_profile_id: null,
    estado_entrega: null, estado_entrega_rotulo: null,
  }, extra);
}

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

  // O cenário real, com os papéis trocados por códigos sintéticos:
  // LAU-000065 legítimo e pronto para entrega, LAU-000066 cancelado,
  // LAU-000067 de um segundo exame, legitimamente aguardando laudo.
  const ativa = [
    linha({
      document_id: "doc-067", report_code: "LAU-000067", public_code: "LAU-000067",
      exam_code: "ESP-000079", exam_date: "2026-10-22", status: "atribuido", locked: false,
    }),
    linha({
      document_id: "doc-065", report_code: "LAU-000065", public_code: "LAU-000065",
      estado_entrega: "pronto_para_entrega", estado_entrega_rotulo: "Pronto para entrega",
    }),
  ];
  const historico = [
    linha({
      document_id: "doc-066", report_code: "LAU-000066", public_code: "LAU-000066",
      status: "atribuido", locked: false, is_corrective: true,
      correction_reason_code: "technical_document_correction",
      devolucao_cancelada_em: "2026-10-23T12:40:00-03:00",
    }),
  ];
  // Os contadores vêm PRONTOS do servidor: 2 aguardando laudo (e não 3).
  const fila = {
    estados: [
      { chave: "aguardando_laudo", rotulo: "Aguardando laudo", total: 2 },
      { chave: "aguardando_assinatura", rotulo: "Aguardando assinatura", total: 0 },
      { chave: "assinado_recebido_validacao_pendente", rotulo: "Exceção técnica — documento sem aceite", total: 0 },
      { chave: "pronto_para_entrega", rotulo: "Pronto para entrega", total: 1 },
      { chave: "entregue", rotulo: "Entregue", total: 51 },
    ],
    itens: [
      { document_id: "doc-067", patient: { full_name: "Paciente Sintética" },
        report_code: "LAU-000067", exam_code: "ESP-000079", exam_date: "2026-10-22",
        estado: "aguardando_laudo", estado_rotulo: "Aguardando laudo",
        status_clinico: "atribuido", has_corrective: false, released_at: null,
        physician_profile_id: null, assinado: null, resultado: { existe: false } },
    ],
  };

  await page.evaluate(({ ativa, historico, fila }) => {
    window.calls = [];
    window.SoproM15 = {
      hasToken: () => true,
      can: (papel) => papel === "operacional",
      getUser: () => ({ nome: "Operação Sintética", papeis: ["operacional"] }),
      onSessionChange: (cb) => { setTimeout(cb, 0); },
      apiUrl: (u) => u,
      hasSession: () => false,
      apiBlob: async () => new Blob([new Uint8Array([1, 2, 3])], { type: "application/pdf" }),
      api: async (url) => {
        window.calls.push(url);
        if (url === "/laudos") return ativa;
        if (url === "/laudos?somente_superados=true") return historico;
        if (url === "/laudos/assinatura-externa/fila") return fila;
        if (url === "/laudos/exames/encerrados") return { total: 0, itens: [] };
        if (url === "/laudos/exames/motivos-encerramento") return { motivos: [] };
        return [];
      },
    };
  }, { ativa, historico, fila });

  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-operation-row");

  await check("a corretiva cancelada não está na lista ativa", async () => {
    await page.locator('[data-report-operational="doc-065"]').waitFor();
    const cancelada = await page.locator('[data-report-operational="doc-066"]').count();
    assert.equal(cancelada, 0, "LAU-000066 não pode aparecer no acompanhamento ativo");
  });

  await check("os contadores da Fila de laudos são os do servidor, não recalculados", async () => {
    const chips = await page.locator(".report-delivery-chips .report-delivery-chip").allInnerTexts();
    const aguardando = chips.find((t) => t.includes("Aguardando laudo"));
    assert.ok(aguardando, `nenhum chip de aguardando laudo: ${JSON.stringify(chips)}`);
    assert.match(aguardando, /\b2\b/, `o chip deve repetir o 2 do servidor: ${aguardando}`);
    const lista = await page.locator(".report-delivery-list").innerText();
    assert.doesNotMatch(lista, /LAU-000066/, "a corretiva cancelada não pode aparecer na fila de entrega");
  });

  await check("Históricos operacionais nomeia a linha como 'Devolução cancelada'", async () => {
    const details = page.locator("details.report-closed-catalog").first();
    await details.waitFor();
    const summary = await details.locator("summary").innerText();
    assert.match(summary, /Históricos operacionais \(1\)/, `resumo inesperado: ${summary}`);
    await details.locator("summary").click();
    const texto = await details.innerText();
    assert.match(texto, /LAU-000066/);
    assert.match(texto, /Devolução cancelada/);
    // A afirmação falsa de antes não pode voltar: nunca houve entrega.
    assert.doesNotMatch(texto, /Entregue ao paciente/);
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
