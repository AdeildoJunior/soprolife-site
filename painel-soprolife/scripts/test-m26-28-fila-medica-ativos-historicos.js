#!/usr/bin/env node
/* M26.28 — "Meus laudos" mostra só trabalho pendente da médica; o que já
 * terminou (PDF assinado recebido, entregue, superado) fica em Históricos.
 *
 * A separação é do SERVIDOR (`/laudos/meus` × `?somente_superados=true`).
 * Este arquivo prova o lado da tela: o filtro não oferece mais estado que
 * nunca está na fila ativa, Históricos diz o estado canônico de cada laudo
 * (sem o `else` que chamava tudo de "Entregue ao paciente"), a linha abre o
 * laudo, e um laudo aberto a partir de Históricos não fecha sozinho quando
 * a fila é recarregada.
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

function linha(id, overrides) {
  return {
    document_id: id, status: "liberado", public_code: id.toUpperCase(),
    patient: { full_name: `Paciente Sintética ${id}` },
    exam_code: "ESP-900001", report_code: id.toUpperCase(),
    location_key: "sintetico", location_name: "Unidade Sintética",
    is_corrective: false, correction_reason_code: null, locked: true,
    released_at: "2026-09-01T10:00:00Z",
    has_corrective_successor: false, is_delivered: false,
    estado_entrega: "aguardando_assinatura",
    estado_entrega_rotulo: "Aguardando assinatura",
    ...overrides,
  };
}

function detalhe(item) {
  return {
    id: item.document_id, public_code: item.report_code, status: item.status,
    corrects_document_id: null, correction_reason_code: null, locked: true,
    current_version_id: "ver-final",
    estado_entrega: item.estado_entrega,
    estado_entrega_rotulo: item.estado_entrega_rotulo,
    versoes: [
      { id: "ver-original", kind: "original", version_number: 1,
        mime_type: "application/pdf", page_count: 1 },
      { id: "ver-final", kind: "laudo_liberado", version_number: 2,
        mime_type: "application/pdf", page_count: 1 },
    ],
    patient: { full_name: item.patient.full_name, date_of_birth: "2000-01-01",
      public_code: "PES-900001" },
    exam: { public_code: item.exam_code, exam_date: "2026-09-01",
      exam_time: "09:00", post_bronchodilator: false },
    location: null,
  };
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
    body: '<html lang="pt-BR"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>'
      + '<body><div id="reportWorkflowRoot" class="report-workflow-root"></div></body></html>',
  }));
  await page.goto("https://sopro-sintetico.test/painel/");
  const html = fs.readFileSync(path.join(panel, "index.html"), "utf8");
  for (const match of html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) {
    try { await page.addStyleTag({ content: fs.readFileSync(path.join(panel, match[1]), "utf8") }); } catch (_e) {}
  }

  let passed = 0;
  async function check(name, run) { await run(); passed++; console.log(`PASS ${name}`); }

  const dados = {
    ativos: [
      linha("lau-a"),
      linha("lau-c", { status: "atribuido", locked: false,
        estado_entrega: "aguardando_laudo", estado_entrega_rotulo: "Aguardando laudo" }),
    ],
    historico: [
      linha("lau-p", { estado_entrega: "pronto_para_entrega",
        estado_entrega_rotulo: "Pronto para entrega" }),
      linha("lau-e", { is_delivered: true, estado_entrega: "entregue",
        estado_entrega_rotulo: "Entregue" }),
      linha("lau-s", { has_corrective_successor: true,
        estado_entrega: "pronto_para_entrega", estado_entrega_rotulo: "Pronto para entrega" }),
    ],
  };
  const detalhes = {};
  [...dados.ativos, ...dados.historico].forEach((i) => { detalhes[i.document_id] = detalhe(i); });

  await page.evaluate(({ dados, detalhes }) => {
    const estado = { ...dados, detalhes, chamadas: [] };
    window.__m2628 = estado;
    window.SoproM15 = {
      hasToken: () => true,
      can: () => false,
      getUser: () => ({ nome: "Dra. Sintética", papeis: ["medico"] }),
      onSessionChange: (cb) => { setTimeout(cb, 0); },
      apiUrl: (u) => u,
      hasSession: () => false,
      apiBlob: async () => new Blob([new Uint8Array([1, 2, 3])], { type: "application/pdf" }),
      api: async (url, options) => {
        estado.chamadas.push(url);
        if (url === "/laudos/assinatura-externa/enviar" && options && options.method === "POST") {
          // O servidor aceitou lau-a: ele sai da fila ativa e vai para
          // Históricos. O navegador só sabe disso reconsultando.
          const item = estado.ativos.find((i) => i.document_id === "lau-a");
          estado.ativos = estado.ativos.filter((i) => i.document_id !== "lau-a");
          estado.historico = [{ ...item, estado_entrega: "pronto_para_entrega",
            estado_entrega_rotulo: "Pronto para entrega" }, ...estado.historico];
          return { aceitos: 1, resumo: { com_problema: 0 }, arquivos: [
            { ok: true, arquivo: "assinado.pdf", resultado: "validado_e_liberado" },
          ] };
        }
        if (url === "/laudos/meus?somente_superados=true") return estado.historico;
        if (url.startsWith("/laudos/meus")) return estado.ativos;
        if (url === "/laudos/templates?catalog=clinical") return [];
        if (url === "/laudos/assinatura-externa/pendentes") {
          const laudos = estado.ativos.filter((i) => i.estado_entrega === "aguardando_assinatura");
          return { total: laudos.length, laudos };
        }
        if (url.endsWith("/catalogo-conclusoes")) return { conclusoes: [], complementos_bd: [], exame_com_pos_bd: false };
        if (url.endsWith("/documentos")) return [];
        const m = url.match(/^\/laudos\/([a-z-]+)$/);
        if (m && estado.detalhes[m[1]]) return estado.detalhes[m[1]];
        return [];
      },
    };
  }, { dados, detalhes });

  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-queue-item");

  await check("fila ativa mostra só o que o servidor mandou como ativo", async () => {
    const ids = await page.$$eval(".report-queue-item", (els) => els.map((e) => e.getAttribute("data-report-open")));
    assert.deepEqual(ids.sort(), ["lau-a", "lau-c"]);
  });

  await check("H — filtro: 'Todos os ativos' e nenhuma opção pós-assinatura", async () => {
    const opcoes = await page.$$eval("#reportStatusFilter option", (els) => els.map((e) => [e.value, e.textContent.trim()]));
    assert.deepEqual(opcoes[0], ["", "Todos os ativos"]);
    const valores = opcoes.map((o) => o[0]);
    assert.ok(!valores.includes("assinado"), `opção 'assinado' não pode estar na fila ativa: ${valores}`);
    assert.ok(valores.includes("liberado"));
    const ajuda = await page.locator("#reportStatusFilterHelp").textContent();
    assert.match(ajuda, /Históricos/);
  });

  await check("C/D/F — Históricos diz o estado canônico de cada laudo", async () => {
    const motivos = await page.$$eval(".report-closed-row", (els) => els.map((e) => [
      e.querySelector("[data-report-history-open]").getAttribute("data-report-history-open"),
      e.querySelector(".report-closed-reason").textContent.trim(),
    ]));
    assert.deepEqual(motivos, [
      ["lau-p", "Pronto para entrega"],
      ["lau-e", "Entregue"],
      ["lau-s", "Superado por corretiva"],
    ]);
  });

  await check("C — laudo de Históricos continua consultável (abre o detalhe)", async () => {
    await page.evaluate(() => { document.querySelector(".report-closed-catalog").open = true; });
    await page.click('.report-closed-row [data-report-history-open="lau-p"]');
    await page.waitForFunction(() => {
      const h = document.getElementById("reportDetailHeading");
      return h && /lau-p/.test(h.textContent);
    });
    const chip = (await page.locator(".report-status-badges .report-status-chip").first().textContent()).trim();
    assert.equal(chip, "Pronto para entrega");
    const acao = await page.locator(".report-concluded").innerText();
    assert.match(acao, /PDF assinado recebido/i);
  });

  await check("E — envio aceito move o laudo para Históricos sem fechar o aberto", async () => {
    await page.setInputFiles("#reportSignatureUpload", {
      name: "assinado.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 sintetico"),
    });
    await page.waitForFunction(() => !document.querySelector('.report-queue-item[data-report-open="lau-a"]'));
    const noHistorico = await page.locator('.report-closed-row [data-report-history-open="lau-a"]').count();
    assert.equal(noHistorico, 1, "lau-a precisa aparecer em Históricos");
    const heading = await page.locator("#reportDetailHeading").textContent();
    assert.match(heading, /lau-p/, "o laudo aberto a partir de Históricos continua aberto");
    const central = await page.locator(".report-signature").innerText();
    assert.match(central, /Nenhum laudo aguardando assinatura/);
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
