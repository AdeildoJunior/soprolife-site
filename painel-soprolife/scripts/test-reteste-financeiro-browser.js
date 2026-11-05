#!/usr/bin/env node
/* Navegadores reais, scripts/CSS oficiais, apenas dados sintéticos em memória. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium, firefox } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const panel = path.resolve(__dirname, "..");

async function main() {
  for (const engine of [chromium, firefox]) {
    const browser = await engine.launch({ headless: true });
    try {
      for (const width of [1440, 768, 390]) {
        const page = await browser.newPage({ viewport: { width, height: 1000 } });
        const errors = [];
        page.on("pageerror", e => errors.push(e.message));
        await page.route("**/*", route => route.abort());
        await page.route("https://sopro-sintetico.test/", route => route.fulfill({
          contentType: "text/html", body: '<html lang="pt-BR"><body style="padding:16px"><section id="financeiro" class="active"><div id="conciliacaoExtraPastoreRoot"></div><div id="medicalTransfersRoot"></div></section></body></html>',
        }));
        await page.goto("https://sopro-sintetico.test/");
        for (const css of ["style.css", "m15.css", "medical-transfers.css"]) {
          await page.addStyleTag({ content: fs.readFileSync(path.join(panel, "css", css), "utf8") });
        }
        await page.evaluate(() => {
          window.scenario = "normal";
          window.hasSession = true;
          window.listeners = [];
          window.SoproM15 = {
            hasToken: () => window.hasSession,
            can: () => true,
            getUser: () => ({ papeis_efetivos: ["admin"] }),
            onSessionChange: callback => window.listeners.push(callback),
            api: async (url, options) => {
              if (options && options.method && options.method !== "GET") throw Error("Escrita proibida no teste");
              if (window.scenario === "erro") throw Error("Falha sintética");
              if (url.includes("conciliacao")) return {
                rotulo: "Dados sintéticos", total_alvo: "3044.79", total_vinculado: "0.00", total_pendente: "3044.79",
                exames_conciliados: 0, total_exames: window.scenario === "vazio" ? 0 : 1,
                exames_pendentes: window.scenario === "vazio" ? 0 : 1, conciliados: [],
                pendentes: window.scenario === "vazio" ? [] : [{ exam_id: "normal", exam_public_code: "ESP-SYN-NORMAL", data_exame: "2026-10-15" }],
                retestes_sem_cobranca: [{ exam_id: "reteste", exam_public_code: "ESP-SYN-RETESTE", data_exame: "2026-10-15", local_atendimento: "Local sintético de atendimento", reteste_sem_cobranca: true }],
              };
              if (url.includes("/producao")) return { medica: "Dra. Sintética", competencia: "2026-10", efetivos: 33,
                corrigidos: 0, assinados: 33, entregues: 33, aguardando_assinatura: 0, pendentes: 0, distribuicao_conclusao: [] };
              return { competencia: "2026-10", total_a_pagar: "495.00", total_pago: "495.00", historico: [], medicas: [{
                id: "pago", physician_profile_id: "sintetica", medica: "Dra. Sintética", quantidade_laudos_elegiveis: 33,
                valor_unitario: "15.00", total_calculado: "495.00", valor_pago: "495.00", data_pagamento: "2026-11-05", status: "Pago",
              }] };
            },
          };
        });
        for (const script of ["financeiro-conciliacao.js", "medical-transfers.js"]) {
          await page.addScriptTag({ content: fs.readFileSync(path.join(panel, "js", script), "utf8") });
        }
        await page.waitForSelector(".conciliar-um-btn");
        const retest = page.locator("tr").filter({ hasText: "ESP-SYN-RETESTE" });
        assert.match(await retest.innerText(), /Reteste sem cobrança/);
        assert.doesNotMatch(await retest.innerText(), /Sem lançamento|Conciliar valor/);
        assert.equal(await retest.locator("button,input,a").count(), 0);
        assert.match(await page.locator("tr").filter({ hasText: "ESP-SYN-NORMAL" }).innerText(), /Sem lançamento.*Conciliar valor/s);
        await page.locator("#conciliacaoToggleLote").click();
        assert.equal(await page.locator('.lote-valor-input[data-exam-id="reteste"]').count(), 0);
        assert.equal(await page.locator('.lote-valor-input[data-exam-id="normal"]').count(), 1);
        await page.waitForSelector("[data-medical-production-toggle]");
        assert.match(await page.locator("#medicalTransfersRoot").innerText(), /33 remuneráveis/);
        assert.equal(await page.locator("[data-medical-pay]").count(), 0);
        await page.locator("[data-medical-production-toggle]").first().click();
        await page.waitForSelector(".medical-production-card");
        assert.match(await page.locator(".medical-production").innerText(), /Laudos remuneráveis/);
        if (process.env.SCREENSHOT_DIR) {
          await page.screenshot({ path: path.join(process.env.SCREENSHOT_DIR, `reteste-${engine.name()}-${width}.png`), fullPage: true });
        }
        await page.locator("#conciliacaoToggleLote").focus();
        await page.keyboard.press("Tab");
        assert.equal(await page.evaluate(() => document.activeElement.dataset.examId), "normal");
        for (const zoom of [1, 2]) {
          await page.evaluate(value => { document.body.style.zoom = value; }, zoom);
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1), false, `${engine.name()} ${width} zoom ${zoom}: overflow`);
        }
        await page.evaluate(() => { document.body.style.zoom = 1; window.scenario = "vazio"; window.listeners.forEach(fn => fn()); });
        await page.waitForFunction(() => !document.querySelector(".conciliar-um-btn"));
        assert.equal(await page.locator("#conciliacaoToggleLote").count(), 0);
        await page.evaluate(() => { window.scenario = "erro"; window.listeners.forEach(fn => fn()); });
        await page.waitForFunction(() => document.body.innerText.includes("Falha sintética"));
        await page.evaluate(() => { window.hasSession = false; window.listeners.forEach(fn => fn()); });
        await page.waitForFunction(() => document.body.innerText.includes("Requer sessão"));
        assert.deepEqual(errors, []);
        console.log(`PASS ${engine.name()} ${browser.version()} width=${width}, zoom=100/200%, reteste, pendência normal, lote, pago, teclado, vazio, erro, sem sessão`);
        await page.close();
      }
    } finally { await browser.close(); }
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
