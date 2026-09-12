#!/usr/bin/env node
/* Navegador real, API simulada em memória, sem conexão com dados reais.
 * PLAYWRIGHT_MODULE pode apontar para uma instalação local já disponível.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const panel = path.resolve(__dirname, "..");
const screenshots = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m2610-ui-"));

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", (route) => route.abort());
  let passed = 0;
  async function check(name, run) { await run(); passed++; console.log(`PASS ${name}`); }
  async function month(label) {
    await page.waitForFunction((text) => document.querySelector("[data-medical-selected]")?.textContent === text, label);
    await page.waitForFunction(() => !document.getElementById("medicalTransfersRoot").hasAttribute("aria-busy"));
  }
  try {
    await page.setContent('<html lang="pt-BR"><body style="margin:0;padding:12px;box-sizing:border-box"><main style="max-width:1160px;margin:auto;min-width:0"><div id="medicalTransfersRoot" aria-live="polite"></div></main></body></html>');
    // Mesma cascata CSS da página real, sem carregar scripts de outras áreas.
    const html = fs.readFileSync(path.join(panel, "index.html"), "utf8");
    for (const match of html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) {
      await page.addStyleTag({ content: fs.readFileSync(path.join(panel, match[1]), "utf8") });
    }
    await page.evaluate(() => {
      window.calls = [];
      window.allowed = true;
      window.currentMonth = "2027-01";
      const base = { competencia: "2027-01", quantidade_laudos_elegiveis: 3, quantidade_laudos_atual: 3, valor_unitario: "25.00", total_calculado: "75.00", valor_pago: "0.00", data_pagamento: null, status: "Pendente" };
      const rows = [
        { ...base, id: null, physician_profile_id: "profile-1", medica: "Dra. Sintética Clara", valor_unitario: null, total_calculado: null },
        { ...base, id: "pending-2", physician_profile_id: "profile-2", medica: "Dra. Sintética Helena" },
        { ...base, id: "paid-3", physician_profile_id: "profile-3", medica: "Dra. Sintética Laura", valor_pago: "60.00", data_pagamento: "2027-01-10", status: "Pago" },
      ];
      window.SoproM15 = {
        hasToken: () => window.allowed,
        can: () => window.allowed,
        onSessionChange: (fn) => { window.sessionChange = fn; },
        api: async (url, options) => {
          window.calls.push({ url, options });
          const selected = new URL(url, "https://synthetic.invalid").searchParams.get("competencia") || window.currentMonth;
          if (window.failNext) { window.failNext = false; throw new Error("Falha sintética de rede"); }
          if (window.slowMonth === selected) await new Promise((resolve) => setTimeout(resolve, 180));
          return { competencia: selected, total_a_pagar: "150.00", total_pago: "60.00", total_a_pagar_tem_valor_a_definir: true,
            medicas: selected === "2027-02" ? [] : rows.map((row) => ({ ...row, competencia: selected })),
            historico: [{ ...rows[2], competencia: "2026-08" }] };
        },
      };
    });
    await page.addScriptTag({ path: path.join(panel, "js/medical-transfers.js") });
    await month("Janeiro 2027");
    await check("competência sem digitação; seis chips e destaque do mês", async () => {
      assert.equal(await page.locator('input[type="month"]').count(), 0);
      assert.equal(await page.locator('.medical-month-chips button').count(), 6);
      assert.equal(await page.locator('.medical-month-chips [aria-pressed="true"]').innerText(), "Jan 2027");
    });
    await check("anterior/próximo atravessam a virada do ano e preservam YYYY-MM", async () => {
      await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
      await month("Dezembro 2026");
      assert.match(await page.evaluate(() => window.calls.at(-1).url), /competencia=2026-12$/);
      await page.getByRole("button", { name: "Próximo mês", exact: true }).click();
      await month("Janeiro 2027");
    });
    await check("chips por teclado, histórico clicável e mês atual do servidor", async () => {
      await page.getByRole("button", { name: "Outubro 2026", exact: true }).focus();
      await page.keyboard.press("Enter");
      await month("Outubro 2026");
      await page.locator(".medical-transfers-history summary").click();
      await page.locator(".medical-history-month").click();
      await month("Agosto 2026");
      await page.getByRole("button", { name: "Mês atual", exact: true }).click();
      await month("Janeiro 2027");
      assert.equal(await page.evaluate(() => window.calls.at(-1).url), "/financeiro/repasses-medicos");
    });
    await check("ajuda em campos e cards: hover, foco, Escape e ponteiro no balão", async () => {
      for (const label of ["Competência", "Laudos", "Unitário", "Referência", "Pago", "Pagamento", "Status", "Total a pagar", "Total pago"]) {
        const button = page.getByRole("button", { name: `Ajuda: ${label}`, exact: true }).first();
        await button.hover();
        await page.locator('[role="tooltip"]').waitFor({ state: "visible" });
        assert.ok((await page.locator('[role="tooltip"]').textContent()).length > 30);
        await page.locator('[role="tooltip"]').hover();
        await page.waitForTimeout(180);
        assert.equal(await page.locator('[role="tooltip"]').isVisible(), true);
        await page.keyboard.press("Escape");
        assert.equal(await page.locator('[role="tooltip"]').isVisible(), false);
        await button.focus();
        assert.equal(await button.getAttribute("aria-describedby"), "medicalTransfersHelp");
        await page.keyboard.press("Escape");
      }
    });
    await check("totais, referência monetária e status preservados", async () => {
      assert.match(await page.locator(".medical-transfers-kpi").first().innerText(), /150,00/);
      assert.match(await page.locator(".medical-transfers-kpi").nth(1).innerText(), /60,00/);
      assert.match(await page.locator(".medical-transfers-kpi").first().innerText(), /valor a definir/);
      const help = page.getByRole("button", { name: "Ajuda: Referência", exact: true }).first();
      assert.match(await help.getAttribute("data-medical-help"), /quantidade de laudos × valor unitário/);
      assert.equal(await page.locator('[aria-label="Repasses por médica"] .is-pending').count(), 2);
      assert.equal(await page.locator('[aria-label="Repasses por médica"] .is-paid').count(), 1);
      assert.equal(await page.evaluate(() => window.calls.filter((call) => call.options?.method).length), 0);
    });
    await check("fechamento mantém payload, preço manual e cálculo de prévia", async () => {
      await page.getByRole("button", { name: "Registrar repasse", exact: true }).click();
      assert.equal(await page.locator('[name="unit_amount"]').inputValue(), "");
      await page.locator('[name="unit_amount"]').fill("25,00");
      assert.match(await page.locator('[data-medical-total]').innerText(), /3 ×.*25,00 =.*75,00/);
      await page.locator('[data-medical-create-form] button[type="submit"]').click();
      await page.waitForFunction(() => window.calls.some((call) => call.options?.method === "POST"));
      const call = await page.evaluate(() => window.calls.find((call) => call.options?.method === "POST"));
      assert.equal(call.url, "/financeiro/repasses-medicos");
      assert.deepEqual(JSON.parse(call.options.body), { physician_profile_id: "profile-1", competencia: "2027-01", expected_eligible_report_count: 3, unit_amount: "25.00", paid_amount: "0.00", payment_date: null });
    });
    await check("pagamento mantém rota e payload explícitos", async () => {
      await page.getByRole("button", { name: "Registrar pagamento", exact: true }).click();
      await page.locator('[name="paid_amount"]').fill("70,00");
      await page.locator('[name="payment_date"]').fill("2027-01-12");
      await page.getByRole("button", { name: "Confirmar pagamento", exact: true }).click();
      await page.waitForFunction(() => window.calls.some((call) => call.options?.method === "PATCH"));
      const call = await page.evaluate(() => window.calls.find((call) => call.options?.method === "PATCH"));
      assert.equal(call.url, "/financeiro/repasses-medicos/pending-2/pagamento");
      assert.deepEqual(JSON.parse(call.options.body), { paid_amount: "70.00", payment_date: "2027-01-12" });
    });
    await check("falha de rede mantém mês exibido e permite repetir navegação", async () => {
      await page.evaluate(() => { window.failNext = true; });
      await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
      await page.locator(".is-error").waitFor();
      await month("Janeiro 2027");
      assert.match(await page.locator(".is-error").innerText(), /Dezembro 2026.*mantida/);
      await page.getByRole("button", { name: "Mês anterior", exact: true }).click();
      await month("Dezembro 2026");
    });
    await check("resposta antiga não substitui a competência mais recente", async () => {
      await page.evaluate(async () => {
        window.slowMonth = "2026-10";
        await Promise.all([window.SoproMedicalTransfers.refresh("2026-10"), window.SoproMedicalTransfers.refresh("2026-11")]);
      });
      await month("Novembro 2026");
      await page.getByRole("button", { name: "Mês atual", exact: true }).click();
      await month("Janeiro 2027");
    });
    await check("desktop/mobile sem overflow de página; tabela rola e tooltip cabe", async () => {
      for (const width of [1440, 768, 390, 320]) {
        await page.setViewportSize({ width, height: 1000 });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflow em ${width}px`);
        const help = page.getByRole("button", { name: "Ajuda: Status", exact: true }).first();
        await help.click();
        const box = await page.locator('[role="tooltip"]').boundingBox();
        assert.ok(box.x >= 0 && box.x + box.width <= width, `tooltip em ${width}px`);
        await page.keyboard.press("Escape");
        await help.blur();
        await page.locator('[aria-label="Repasses por médica"]').evaluate((el) => { el.scrollLeft = 0; });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflow após ajuda em ${width}px`);
        await page.screenshot({ path: path.join(screenshots, `repasses-${width}.png`), fullPage: true });
      }
    });
    await check("formulário cabe em 320px e estado vazio orienta a navegação", async () => {
      await page.getByRole("button", { name: "Registrar repasse", exact: true }).click();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.getByRole("button", { name: "Cancelar", exact: true }).click();
      await page.getByRole("button", { name: "Próximo mês", exact: true }).click();
      await month("Fevereiro 2027");
      assert.match(await page.locator(".medical-empty").innerText(), /Escolha outro mês/);
    });
    await check("médica sem permissão não consulta nem vê a área", async () => {
      const before = await page.evaluate(() => window.calls.length);
      await page.evaluate(() => { window.allowed = false; window.sessionChange(); });
      assert.equal(await page.locator("#medicalTransfersRoot").innerText(), "");
      assert.equal(await page.evaluate(() => window.calls.length), before);
    });
    assert.deepEqual(errors, []);
    console.log(`${passed} cenários passaram. Capturas sintéticas: ${screenshots}`);
  } finally { await browser.close(); }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
