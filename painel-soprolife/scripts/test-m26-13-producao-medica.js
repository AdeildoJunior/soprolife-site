#!/usr/bin/env node
/* M26.13 — produção por médica (cartões + donut) dentro de Repasses médicos.
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
const screenshots = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m2613-producao-"));

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", (route) => route.abort());
  let passed = 0;
  async function check(name, run) { await run(); passed++; console.log(`PASS ${name}`); }
  async function ready() {
    await page.waitForFunction(() => !document.getElementById("medicalTransfersRoot").hasAttribute("aria-busy"));
  }

  try {
    await page.setContent('<html lang="pt-BR"><body style="margin:0;padding:12px;box-sizing:border-box"><main style="max-width:1160px;margin:auto;min-width:0"><div id="medicalTransfersRoot" aria-live="polite"></div></main></body></html>');
    const html = fs.readFileSync(path.join(panel, "index.html"), "utf8");
    for (const match of html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) {
      await page.addStyleTag({ content: fs.readFileSync(path.join(panel, match[1]), "utf8") });
    }
    await page.addScriptTag({ path: path.join(panel, "js/vendor/chart.umd.min.js") });

    await page.evaluate(() => {
      window.calls = [];
      const base = {
        competencia: "2027-01", quantidade_laudos_elegiveis: 3, quantidade_laudos_atual: 3,
        valor_unitario: "25.00", total_calculado: "75.00", valor_pago: "0.00",
        data_pagamento: null, status: "Pendente",
      };
      const rows = [
        { ...base, id: "pending-1", physician_profile_id: "profile-1", medica: "Dra. Sintética Ana" },
        { ...base, id: "pending-2", physician_profile_id: "profile-2", medica: "Dra. Sintética Bia" },
      ];
      const producaoPorPerfil = {
        "profile-1": {
          physician_profile_id: "profile-1", medica: "Dra. Sintética Ana", competencia: "2027-01",
          efetivos: 3, corrigidos: 1, assinados: 2, entregues: 1, aguardando_assinatura: 1, pendentes: 4,
          distribuicao_conclusao: [
            { conclusion_code: "NORMAL", rotulo: "Normal", grupo: "normal", quantidade: 2 },
            { conclusion_code: "DVO_LEVE", rotulo: "DVO Leve", grupo: "obstrutivo", quantidade: 1 },
          ],
        },
      };
      window.SoproM15 = {
        hasToken: () => true,
        can: () => true,
        onSessionChange: (fn) => { window.sessionChange = fn; },
        api: async (url, options) => {
          window.calls.push({ url, options });
          if (url.startsWith("/financeiro/repasses-medicos/")) {
            const profileId = url.split("/")[3];
            const producao = producaoPorPerfil[profileId];
            if (!producao) throw new Error("produção sintética não coberta para " + profileId);
            return producao;
          }
          return { competencia: "2027-01", total_a_pagar: "150.00", total_pago: "60.00", total_a_pagar_tem_valor_a_definir: false,
            medicas: rows, historico: [] };
        },
      };
    });
    await page.addScriptTag({ path: path.join(panel, "js/medical-transfers.js") });
    await ready();

    await check("botão 'Ver produção' existe por médica e começa fechado", async () => {
      assert.equal(await page.locator('[data-medical-production-toggle]').count(), 2);
      assert.equal(await page.locator(".medical-production-slot").innerHTML(), "");
    });

    await check("abrir produção busca o endpoint certo e mostra os cartões", async () => {
      await page.locator('[data-medical-production-toggle="profile-1"]').click();
      await page.waitForSelector(".medical-production-cards");
      const call = await page.evaluate(() => window.calls.find((c) => c.url.startsWith("/financeiro/repasses-medicos/profile-1/producao")));
      assert.ok(call, "deveria ter chamado /financeiro/repasses-medicos/profile-1/producao?competencia=2027-01");
      assert.match(call.url, /competencia=2027-01/);
      const cards = await page.locator(".medical-production-card strong").allInnerTexts();
      assert.deepEqual(cards, ["3", "1", "2", "1", "1", "4"]);
    });

    await check("donut renderiza com as cores/legenda por grupo de conclusão", async () => {
      assert.equal(await page.locator("#medicalProductionChart").count(), 1);
      const legenda = await page.locator(".medical-production-legend li").allInnerTexts();
      assert.deepEqual(legenda, ["Normal · 2", "Obstrutivo · 1"]);
      assert.equal(errors.length, 0, "Chart.js não deveria lançar erro no console");
    });

    await check("fechar pelo cabeçalho do painel esvazia o slot sem nova chamada", async () => {
      const antes = await page.evaluate(() => window.calls.length);
      await page.locator('[data-medical-production-toggle="profile-1"]').first().click(); // fecha Ana
      assert.equal(await page.locator(".medical-production-slot").innerHTML(), "");
      const depois = await page.evaluate(() => window.calls.length);
      assert.equal(depois, antes, "fechar não deveria chamar a API de novo");
    });

    await check("reabrir a mesma médica usa cache — não refaz a chamada", async () => {
      const antes = await page.evaluate(() => window.calls.filter((c) => c.url.startsWith("/financeiro/repasses-medicos/profile-1/producao")).length);
      await page.locator('[data-medical-production-toggle="profile-1"]').click();
      await page.waitForSelector(".medical-production-cards");
      const depois = await page.evaluate(() => window.calls.filter((c) => c.url.startsWith("/financeiro/repasses-medicos/profile-1/producao")).length);
      assert.equal(depois, antes, "reabrir a mesma médica/competência deveria reaproveitar o cache");
    });

    await check("larguras 1440/768/390 sem overflow com o painel de produção aberto", async () => {
      for (const width of [1440, 768, 390]) {
        await page.setViewportSize({ width, height: 900 });
        assert.equal(
          await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
          true,
          `overflow horizontal em ${width}px`
        );
        await page.screenshot({ path: path.join(screenshots, `producao-${width}.png`), fullPage: true });
      }
    });

    assert.deepEqual(errors, []);
    console.log(`${passed} cenários passaram. Capturas sintéticas: ${screenshots}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error("FALHA:", error.message);
  process.exitCode = 1;
});
