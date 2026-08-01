#!/usr/bin/env node
// Regressões do refresh e da apresentação de KPIs, sem navegador nem rede.

"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "js", "app.js"), "utf8");

function trecho(inicio, fim) {
  const a = source.indexOf(inicio);
  const b = source.indexOf(fim, a);
  assert(a >= 0 && b > a, `trecho não encontrado: ${inicio}`);
  return source.slice(a, b);
}

async function testRefresh() {
  const refreshCode = trecho(
    "const MKT_REFRESH_URL",
    "function renderMktKpiStrip()"
  );
  const calls = [];
  const timers = [];
  const button = { disabled: false };
  const message = { textContent: "", hidden: true };
  const newSnapshot = {
    meta: { generatedAt: "2026-08-01T12:00:00+00:00" },
    searchConsole: { totals: { clicks: 71, impressions: 2160, ctr: 0.033, avgPosition: 6.1 } },
  };
  const context = vm.createContext({
    state: {
      marketingSeo: { meta: { generatedAt: "old" } },
      mktRefreshPendente: false,
      mktRefreshPolling: false,
      mktRefreshResultado: null,
      mktRefreshRequestId: null,
    },
    document: {
      querySelector(selector) {
        if (selector === "#mktRefreshBtn") return button;
        if (selector === "#mktRefreshMsg") return message;
        return null;
      },
    },
    window: {
      SoproM15: {
        hasToken: () => true,
        async api(url, options) {
          calls.push({ kind: "api", url, method: options?.method || "GET" });
          if (url === "/marketing/refresh") {
            return { ok: true, queued: true, requestId: "req-1" };
          }
          return {
            ok: true, pending: false, state: "completed", requestId: "req-1",
            success: true, degraded: false,
          };
        },
      },
      setTimeout(callback) { timers.push(callback); },
    },
    async loadOptionalJson(url, cacheBust) {
      calls.push({ kind: "snapshot", url, cacheBust });
      return newSnapshot;
    },
    renderMktHeader() {},
    renderMarketingSection() {},
    console,
    Date,
    Error,
  });

  vm.runInContext(refreshCode, context);
  await vm.runInContext("pedirAtualizacaoMarketing()", context);
  assert.strictEqual(calls[0].url, "/marketing/refresh");
  assert.strictEqual(calls[0].method, "POST");
  assert.strictEqual(button.disabled, true);
  assert.strictEqual(timers.length, 1, "frontend deve aguardar o backend");

  await timers.shift()();
  assert.strictEqual(calls[1].url, "/marketing/refresh-status");
  assert.strictEqual(calls[2].kind, "snapshot",
    "snapshot só deve ser carregado depois da conclusão do backend");
  assert.match(calls[2].cacheBust, /-1$/,
    "releitura deve usar cache-buster novo na mesma sessão");
  assert.strictEqual(context.state.marketingSeo.searchConsole.totals.clicks, 71);
  assert.strictEqual(context.state.mktRefreshResultado.success, true);
  assert.strictEqual(button.disabled, false);
}

function testCtrOnceAndWording() {
  const kpiCode = trecho(
    "function renderMktKpiStrip()",
    "// Anexa tabindex/data-tip/aria-label"
  );
  const container = { innerHTML: "" };
  const context = vm.createContext({
    state: {
      marketingSeo: {
        searchConsole: {
          totals: { clicks: 71, impressions: 2160, ctr: 0.033, avgPosition: 6.1 },
        },
      },
    },
    document: { querySelector: () => container },
    escapeHtml: (value) => String(value),
    mktTipAttrs: () => "",
  });
  vm.runInContext(kpiCode, context);
  vm.runInContext("renderMktKpiStrip()", context);
  assert.match(container.innerHTML, />3\.3%<\/strong>/,
    "CTR fracionário deve virar percentual exatamente uma vez");
  assert.doesNotMatch(container.innerHTML, /330(?:\.0)?%/);

  assert(source.includes("Última consulta ao Google:"));
  assert(source.includes("Dados disponíveis no Google:"));
  assert(source.includes("Os dados do Search Console podem ter atraso de processamento."));
}

(async () => {
  await testRefresh();
  testCtrOnceAndWording();
  console.log("OK: refresh aguarda backend, recarrega snapshot e converte CTR uma vez");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
