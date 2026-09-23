#!/usr/bin/env node
/* M26.27 — "Meus laudos" dizia "Concluído — aguardando assinatura
 * qualificada" num laudo cujo PDF assinado já tinha sido recebido e aceito.
 *
 * `status` fica `liberado` para sempre depois da conclusão; quem diz se a
 * assinatura já voltou é `estado_entrega`, derivado no servidor pela mesma
 * função da fila de entrega. Este arquivo prova que o cartão usa esse campo
 * (sem recalcular nada no navegador) e que o envio do PDF assinado repinta a
 * fila sem nova autenticação.
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
    has_corrective_successor: false, is_delivered: false,
    estado_entrega: "aguardando_assinatura",
    estado_entrega_rotulo: "Aguardando assinatura",
    ...overrides,
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

  const iniciais = {
    // Cenário A: concluído, PDF assinado ainda não voltou.
    aguardando: linha("lau-a"),
    // Cenário B: PDF assinado recebido e aceito — `status` NÃO mudou.
    assinado: linha("lau-b", {
      estado_entrega: "pronto_para_entrega",
      estado_entrega_rotulo: "Pronto para entrega",
    }),
    // Ainda em elaboração: o rótulo continua vindo de `status`.
    elaboracao: linha("lau-c", {
      status: "em_elaboracao", locked: false,
      estado_entrega: "aguardando_laudo", estado_entrega_rotulo: "Aguardando laudo",
    }),
  };

  await page.evaluate((iniciais) => {
    const estado = {
      fila: [iniciais.aguardando, iniciais.assinado, iniciais.elaboracao],
      pendentes: [{
        document_id: "lau-a", patient: { full_name: "Paciente Sintética lau-a" },
        report_code: "LAU-A", exam_code: "ESP-900001", exam_date: "2026-09-01",
        location_name: "Unidade Sintética", released_at: "2026-09-01T10:00:00Z",
        validation_code: "ABCDEFGHJKMN",
      }],
      cargasDaFila: 0,
      autenticacoes: 0,
    };
    window.__m2627 = estado;
    window.SoproM15 = {
      hasToken: () => true,
      can: () => false,
      getUser: () => ({ nome: "Dra. Sintética", papeis: ["medico"] }),
      onSessionChange: (cb) => { estado.autenticacoes += 1; setTimeout(cb, 0); },
      apiUrl: (u) => u,
      hasSession: () => false,
      apiBlob: async () => new Blob([new Uint8Array([1, 2, 3])], { type: "application/pdf" }),
      api: async (url, options) => {
        if (url === "/laudos/assinatura-externa/enviar" && options && options.method === "POST") {
          // O servidor aceitou: a partir daqui ele passa a responder o
          // estado novo. O navegador só pode saber disso reconsultando.
          estado.fila = estado.fila.map((item) => item.document_id === "lau-a"
            ? { ...item, estado_entrega: "pronto_para_entrega", estado_entrega_rotulo: "Pronto para entrega" }
            : item);
          estado.pendentes = [];
          return { aceitos: 1, resumo: { com_problema: 0 }, arquivos: [
            { ok: true, arquivo: "assinado.pdf", resultado: "validado_e_liberado" },
          ] };
        }
        if (url === "/laudos/meus?somente_superados=true") return [];
        if (url.startsWith("/laudos/meus")) { estado.cargasDaFila += 1; return estado.fila; }
        if (url === "/laudos/templates?catalog=clinical") return [];
        if (url === "/laudos/assinatura-externa/pendentes") {
          return { total: estado.pendentes.length, laudos: estado.pendentes };
        }
        if (url.endsWith("/catalogo-conclusoes")) return { conclusoes: [], complementos_bd: [], exame_com_pos_bd: false };
        if (url.endsWith("/documentos")) return [];
        return [];
      },
    };
  }, iniciais);

  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-queue-item");

  const chip = (id) => page.locator(`[data-report-open="${id}"] .report-status-chip`).first();

  await check("A — concluído sem PDF assinado continua 'aguardando assinatura qualificada'", async () => {
    assert.equal((await chip("lau-a").textContent()).trim(), "Concluído — aguardando assinatura qualificada");
    const classe = await page.locator('[data-report-open="lau-a"]').getAttribute("class");
    assert.match(classe, /report-family-signature/);
  });

  await check("B — PDF assinado aceito: o cartão deixa de dizer 'aguardando'", async () => {
    const texto = await page.locator('[data-report-open="lau-b"]').innerText();
    assert.ok(!/aguardando/i.test(texto), `cartão assinado ainda diz aguardando: ${texto}`);
    assert.equal((await chip("lau-b").textContent()).trim(), "Pronto para entrega");
    const classe = await page.locator('[data-report-open="lau-b"]').getAttribute("class");
    assert.match(classe, /report-family-success/, "assinado recebido é verde (M26.8)");
    assert.doesNotMatch(classe, /report-family-signature/);
  });

  await check("C — laudo não concluído continua rotulado por `status`", async () => {
    assert.equal((await chip("lau-c").textContent()).trim(), "Em elaboração clínica");
  });

  await check("E — envio aceito repinta a fila e a central sem nova autenticação", async () => {
    const autenticacoesAntes = await page.evaluate(() => window.__m2627.autenticacoes);
    const cargasAntes = await page.evaluate(() => window.__m2627.cargasDaFila);
    await page.setInputFiles("#reportSignatureUpload", {
      name: "assinado.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 sintetico"),
    });
    await page.waitForFunction(() => {
      const el = document.querySelector('[data-report-open="lau-a"] .report-status-chip');
      return el && el.textContent.trim() === "Pronto para entrega";
    });
    const cargasDepois = await page.evaluate(() => window.__m2627.cargasDaFila);
    assert.ok(cargasDepois > cargasAntes, "a fila precisa ser reconsultada após o envio");
    assert.equal(await page.evaluate(() => window.__m2627.autenticacoes), autenticacoesAntes);
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
