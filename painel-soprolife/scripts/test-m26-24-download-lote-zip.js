#!/usr/bin/env node
/* M26.24 — baixar DOIS laudos para assinatura devolvia erro.
 *
 * O caso real: a médica marcou duas pacientes, clicou em "Baixar 2 para
 * assinatura" e recebeu "A API não devolveu um PDF válido para
 * visualização.". Baixar uma a uma funcionava.
 *
 * Causa: `apiBlob` (m15-nucleo.js) só aceitava `application/pdf`, mas o
 * servidor devolve `application/zip` quando são 2+ laudos
 * (`reports.py:4062`) e SEMPRE na rota do lote M25.8 (`reports.py:3505`).
 *
 * O guarda continua existindo — ele impede que uma página de erro caia na
 * pasta da médica com cara de laudo. O que mudou é quem declara o que
 * aceita.
 *
 * Navegador real, API simulada em memória, sem conexão com dados reais.
 * PLAYWRIGHT_MODULE pode apontar para uma instalação local já disponível.
 */
"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const panel = path.resolve(__dirname, "..");

// Exercita o `api()` real do núcleo: intercepta a REDE, não o cliente.
async function comNucleo(page, run) {
  await page.route("https://sopro-sintetico.test/painel/", (route) => route.fulfill({
    contentType: "text/html",
    body: '<html lang="pt-BR"><body></body></html>',
  }));
  await page.goto("https://sopro-sintetico.test/painel/");
  await page.addScriptTag({ path: path.join(panel, "js/m15-nucleo.js") });
  return run();
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  // `finally` obrigatório: sem ele, uma asserção que falha deixa o navegador
  // aberto e o processo PENDURA em vez de sair com erro — um teste que trava
  // em vez de reprovar é pior que teste nenhum.
  try {
    await rodar(browser);
  } finally {
    await browser.close();
  }
}

async function rodar(browser) {
  const page = await browser.newPage();
  let passed = 0;
  const check = async (nome, fn) => { await fn(); passed++; console.log(`PASS ${nome}`); };

  await comNucleo(page, async () => {
    // A rota devolve o tipo que o teste mandar em `?tipo=`.
    await page.route("**/api/**", (route) => {
      const url = new URL(route.request().url());
      const tipo = url.searchParams.get("tipo") || "application/pdf";
      if (tipo === "text/html") {
        return route.fulfill({
          status: 200, contentType: "text/html",
          body: "<html><body>sessão expirada</body></html>",
        });
      }
      return route.fulfill({
        status: 200,
        contentType: tipo,
        headers: { "Content-Disposition": 'attachment; filename="lote.zip"' },
        body: Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x00]),
      });
    });

    const pedir = (tipoDaResposta, aceita) => page.evaluate(async ({ t, a }) => {
      // `apiBase` padrão é /painel-soprolife/api/m15 — a rota do teste
      // intercepta por `**/api/**`, então não é preciso reconfigurar nada.
      try {
        const opcoes = { method: "POST", body: JSON.stringify({}) };
        if (a) opcoes.aceitaBlob = a;
        const blob = await window.SoproM15.apiBlob(`/x?tipo=${encodeURIComponent(t)}`, opcoes);
        return { ok: true, tipo: blob.type, nome: blob.nomeSugerido || "" };
      } catch (e) {
        return { ok: false, erro: e.message };
      }
    }, { t: tipoDaResposta, a: aceita });

    await check("UM laudo: resposta PDF continua aceita sem declarar nada", async () => {
      const r = await pedir("application/pdf", null);
      assert.equal(r.ok, true, `esperava sucesso, veio: ${r.erro}`);
      assert.match(r.tipo, /application\/pdf/);
    });

    await check("regressão do bug: ZIP sem declarar continua RECUSADO (padrão fechado)", async () => {
      const r = await pedir("application/zip", null);
      assert.equal(r.ok, false, "o padrão não pode aceitar ZIP por descuido");
      assert.match(r.erro, /PDF válido/);
    });

    await check("DOIS laudos: ZIP é aceito quando quem chama declara", async () => {
      const r = await pedir("application/zip", ["application/pdf", "application/zip"]);
      assert.equal(r.ok, true, `esperava sucesso, veio: ${r.erro}`);
      assert.match(r.tipo, /application\/zip/);
      assert.equal(r.nome, "lote.zip", "o nome do servidor precisa sobreviver ao blob");
    });

    await check("rota que só aceita ZIP ainda recusa PDF", async () => {
      const r = await pedir("application/pdf", ["application/zip"]);
      assert.equal(r.ok, false);
      assert.match(r.erro, /arquivo válido para download/);
    });

    await check("o guarda continua de pé: HTML nunca vira arquivo na pasta da médica", async () => {
      for (const aceita of [null, ["application/pdf", "application/zip"]]) {
        const r = await pedir("text/html", aceita);
        assert.equal(r.ok, false, "uma página HTML jamais pode ser salva como documento");
      }
    });
  });

  // Os dois pontos de chamada do painel declaram o que a rota devolve.
  await check("report-workflow declara ZIP nas duas rotas que devolvem ZIP", async () => {
    const fs = require("node:fs");
    const src = fs.readFileSync(path.join(panel, "js/report-workflow.js"), "utf8");
    const lote = src.slice(src.indexOf('apiBlob("/laudos/lote/baixar"'));
    assert.match(lote.slice(0, 400), /aceitaBlob:\s*\["application\/zip"\]/);
    const assin = src.slice(src.indexOf('apiBlob("/laudos/assinatura-externa/baixar"'));
    assert.match(assin.slice(0, 600), /aceitaBlob:\s*\["application\/pdf",\s*"application\/zip"\]/);
  });

  console.log(`${passed} cenários passaram.`);
}

main().catch((error) => {
  console.error("FALHA:", error.message);
  process.exitCode = 1;
});
