#!/usr/bin/env node
// SoproLife — subtítulo de "Impressões — tendência" (Marketing & SEO).
//
// Executa renderMktTrendChart() real, recortado de js/app.js, contra um DOM
// mínimo. Prova que o subtítulo segue o período REAL da requisição do Search
// Console (60 dias → "Search Console · 60 dias"), que a série diária é
// repassada ao gráfico sem interpolação e que período inválido não inventa número.
//
// Sem navegador, rede, credencial ou dado privado.
// Uso:  node painel-soprolife/scripts/test-marketing-trend-subtitle.js

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const fonte = fs.readFileSync(path.resolve(__dirname, "../js/app.js"), "utf8");
let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) console.log(`  PASS: ${nome}`);
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}
function recortar(inicio, fim) {
  const i = fonte.indexOf(inicio);
  const f = fonte.indexOf(fim, i + 1);
  return i < 0 || f < 0 ? null : fonte.slice(i, f);
}

const trechoContagem = recortar("function mktInclusiveDateCount(", "/* ────");
const trechoTrend = recortar("function renderMktTrendChart()", "// ── Evolução mensal");
caso("recortes encontrados em app.js", Boolean(trechoContagem && trechoTrend));
if (!trechoContagem || !trechoTrend) process.exit(1);

function serie(inicio, n) {
  const out = [];
  const d = new Date(`${inicio}T00:00:00Z`);
  for (let i = 0; i < n; i += 1) {
    out.push({ date: d.toISOString().slice(0, 10), impressions: i % 3 === 0 ? 0 : i, clicks: 0 });
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}

function render(sc) {
  const nos = { mktTrendPanel: { hidden: true }, mktTrendSubtitle: { textContent: "Search Console · por dia" }, mktTrendChart: {} };
  const chamadas = [];
  const ctx = {
    document: { querySelector: (sel) => nos[sel.replace("#", "")] || null },
    state: { marketingSeo: { searchConsole: sc } },
    createChart: (...args) => chamadas.push(args),
    chartGradient: () => "gradiente",
  };
  vm.createContext(ctx);
  vm.runInContext(`${trechoContagem}\n${trechoTrend}\nrenderMktTrendChart();`, ctx);
  return { nos, chamadas };
}

console.log("── 60 dias ──");
const byDate = serie("2026-08-05", 60);
const r60 = render({ byDate, request: { startDate: "2026-08-05", endDate: "2026-10-03" } });
caso("subtítulo = 'Search Console · 60 dias'", r60.nos.mktTrendSubtitle.textContent === "Search Console · 60 dias",
     r60.nos.mktTrendSubtitle.textContent);
caso("painel visível", r60.nos.mktTrendPanel.hidden === false);
const cfg = r60.chamadas[0] && r60.chamadas[0][2];
caso("gráfico de linha diário", cfg && cfg.type === "line");
caso("60 pontos, mesmos valores (zeros preservados, sem interpolação)",
     cfg && cfg.data.datasets[0].data.length === 60
       && JSON.stringify(cfg.data.datasets[0].data) === JSON.stringify(byDate.map((d) => d.impressions)));
caso("rótulos são as datas reais", cfg && cfg.data.labels[0] === "08-05" && cfg.data.labels[59] === "10-03");
caso("gráfico responsivo sem proporção fixa",
     cfg && cfg.options.responsive === true && cfg.options.maintainAspectRatio === false);

console.log("── Período segue a requisição, não um número fixo ──");
const r28 = render({ byDate: serie("2026-09-06", 28), request: { startDate: "2026-09-06", endDate: "2026-10-03" } });
caso("snapshot antigo de 28 dias continua dizendo 28", r28.nos.mktTrendSubtitle.textContent === "Search Console · 28 dias");
const rSem = render({ byDate: serie("2026-09-06", 5) });
caso("sem período válido → 'por dia' (não inventa número)", rSem.nos.mktTrendSubtitle.textContent === "Search Console · por dia");

console.log();
if (falhas) { console.log(`RESULTADO: ${falhas} falha(s).`); process.exit(1); }
console.log("RESULTADO: subtítulo da tendência íntegro.");
