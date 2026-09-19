#!/usr/bin/env node
// SoproLife — M26.20: renderização do bloco "Pastore Ipanema" (Marketing & SEO).
//
// O teste Python cobre a agregação dos dados; este cobre o LADO DO PAINEL:
// executa renderMktIpanema() de verdade, extraído de js/app.js, contra um DOM
// mínimo. Pega erro de JavaScript no renderer e prova que ausência de dado sai
// como "N/D" em vez de 0 — que é o jeito de um painel mentir sem querer.
//
// Não usa navegador, rede, credencial nem dado privado.
// Uso:  node painel-soprolife/scripts/test-m26-20-ipanema-render.js
// Exit: 0 = todos passaram | 1 = houve falha.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.resolve(__dirname, "../js/app.js");

let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}

// ── Extração do trecho real do app.js ───────────────────────────────────────
// app.js não é módulo (o painel o carrega por <script>), então recortamos as
// funções necessárias em vez de duplicá-las aqui. Se o recorte falhar, o teste
// falha — nunca testa uma cópia desatualizada.
const fonte = fs.readFileSync(APP_JS, "utf8");

function recortar(inicio, fim) {
  const i = fonte.indexOf(inicio);
  const f = fonte.indexOf(fim, i + 1);
  if (i < 0 || f < 0) return null;
  return fonte.slice(i, f);
}

const trechoIpanema = recortar("const MKT_IPANEMA_ND", "function renderMktAlerts()");
const trechoEscape  = recortar("function escapeHtml(str)", "/* Automação CRM (M21)");
const trechoTip     = recortar("function mktTipAttrs(tip, label, value)", "function renderMktKpiStripDemo()");

caso("recorte de renderMktIpanema encontrado em app.js", Boolean(trechoIpanema));
caso("recorte de escapeHtml encontrado em app.js", Boolean(trechoEscape));
caso("recorte de mktTipAttrs encontrado em app.js", Boolean(trechoTip));
if (!trechoIpanema || !trechoEscape || !trechoTip) {
  console.log("\nRESULTADO: recorte falhou — app.js mudou de forma incompatível.");
  process.exit(1);
}

// ── DOM mínimo ──────────────────────────────────────────────────────────────
function criarDom() {
  const nos = {};
  for (const id of ["mktIpanemaPanel", "mktIpanemaKpis", "mktIpanemaNote", "mktIpanemaSubtitle"]) {
    nos[id] = { hidden: false, innerHTML: "", textContent: "" };
  }
  return {
    nos,
    document: {
      querySelector(sel) { return nos[sel.replace("#", "")] || null; },
    },
  };
}

function render(snapshot) {
  const dom = criarDom();
  const ctx = {
    document: dom.document,
    state: { marketingSeo: snapshot },
    console,
  };
  vm.createContext(ctx);
  vm.runInContext(`${trechoEscape}\n${trechoTip}\n${trechoIpanema}\nrenderMktIpanema();`, ctx);
  return dom.nos;
}

const META = {
  configured: true, schemaVersion: 2, safeToDisplay: true,
  containsPersonalData: false,
  periodStart: "2026-08-22", periodEnd: "2026-09-18", lookbackDays: 28,
};

// ── 1. Snapshot completo ────────────────────────────────────────────────────
console.log("── Snapshot completo ──");
const completo = render({
  meta: META,
  ga4: {
    pastoreIpanema: {
      pagePathPrefix: "/espirometria-ipanema",
      outboundUtm: { source: "soprolife", medium: "referral", campaign: "espirometria_ipanema" },
      page: { pageviews: 150, users: 90, sessions: 128 },
      events: [
        { event: "click_agendar_pastore", count: 12, users: 11 },
        { event: "click_whatsapp_ipanema", count: 8, users: 7 },
        { event: "click_rota_pastore_ipanema", count: 0, users: 0 },
      ],
      intentInteractions: { interactions: 20, metric: "eventCount", note: "Soma de interações, não de pessoas únicas." },
      conversion: {
        rate: 8, numerator: 12, numeratorMetric: "eventCount",
        numeratorEvent: "click_agendar_pastore",
        denominator: 150, denominatorMetric: "screenPageViews",
        formula: "click_agendar_pastore (eventCount) ÷ page_view da página Ipanema (screenPageViews) × 100",
      },
    },
  },
});

const kpis = completo.mktIpanemaKpis.innerHTML;
caso("painel deixa de ficar oculto", completo.mktIpanemaPanel.hidden === false);
caso("mostra os 5 cards pedidos + usuários + intenção",
     (kpis.match(/mkt-kpi-card/g) || []).length === 7,
     String((kpis.match(/mkt-kpi-card/g) || []).length));
caso("Visitas = 150", /Visitas<\/span>\s*<strong class="mkt-kpi-value">150</.test(kpis));
caso("Usuários = 90", /Usuários<\/span>\s*<strong class="mkt-kpi-value">90</.test(kpis));
caso("Agendamentos → Pastore = 12",
     /Agendamentos → Pastore<\/span>\s*<strong class="mkt-kpi-value">12</.test(kpis));
caso("WhatsApp = 8", /WhatsApp<\/span>\s*<strong class="mkt-kpi-value">8</.test(kpis));
caso("Rotas com zero real mostra 0, não N/D",
     /Rotas<\/span>\s*<strong class="mkt-kpi-value">0</.test(kpis));
caso("Conversão = 8,0%", /Conversão<\/span>\s*<strong class="mkt-kpi-value">8,0%</.test(kpis));
caso("Interações de intenção = 20",
     /Interações de intenção<\/span>\s*<strong class="mkt-kpi-value">20</.test(kpis));
caso("cards reusam a anatomia padrão .mkt-kpi-card do Marketing",
     kpis.includes('class="mkt-kpi-card kpi-ipanema mkt-tip"'));
caso("nenhum N/D quando há dado completo", !kpis.includes("N/D"));

caso("subtítulo traz o período do snapshot, sem data fixa",
     completo.mktIpanemaSubtitle.innerHTML.includes("2026-08-22 a 2026-09-18 (28 dias)"));
const nota = completo.mktIpanemaNote.innerHTML;
caso("nota diz que a origem é GA4 · dados reais", nota.includes("GA4 · dados reais"));
caso("nota informa o período consultado", nota.includes("2026-08-22 a 2026-09-18"));
caso("nota nega exame concluído / paciente convertido",
     nota.includes("não exames concluídos nem pacientes convertidos"));
caso("nota diz que a soma é interação, não pessoa",
     nota.includes("são interações, não pessoas únicas"));
caso("nota cita a UTM enviada à Pastore",
     nota.includes("utm_source=soprolife") && nota.includes("utm_campaign=espirometria_ipanema"));
caso("nota explica que a UTM é medida no GA4 da Pastore",
     nota.includes("medidas no GA4 da Pastore, não neste"));

// ── 2. Métrica indisponível → N/D ───────────────────────────────────────────
console.log("── Métrica indisponível vira N/D ──");
const parcial = render({
  meta: META,
  ga4: {
    pastoreIpanema: {
      pagePathPrefix: "/espirometria-ipanema",
      page: null, events: null, intentInteractions: null, conversion: null,
    },
  },
});
const kpisParcial = parcial.mktIpanemaKpis.innerHTML;
caso("painel continua visível (não some por falta de dado)",
     parcial.mktIpanemaPanel.hidden === false);
caso("todos os 7 cards mostram N/D",
     (kpisParcial.match(/>N\/D</g) || []).length === 7,
     String((kpisParcial.match(/>N\/D</g) || []).length));
caso("nenhum zero é inventado no lugar de N/D",
     !/mkt-kpi-value">0</.test(kpisParcial));

// ── 3. Divisão indefinida ───────────────────────────────────────────────────
console.log("── Zero visita: conversão indefinida, não 0% ──");
const semTrafego = render({
  meta: META,
  ga4: {
    pastoreIpanema: {
      page: { pageviews: 0, users: 0, sessions: 0 },
      events: [
        { event: "click_agendar_pastore", count: 0, users: 0 },
        { event: "click_whatsapp_ipanema", count: 0, users: 0 },
        { event: "click_rota_pastore_ipanema", count: 0, users: 0 },
      ],
      intentInteractions: { interactions: 0, metric: "eventCount", note: "Soma de interações, não de pessoas únicas." },
      conversion: {
        rate: null, numerator: 0, numeratorMetric: "eventCount",
        numeratorEvent: "click_agendar_pastore",
        denominator: 0, denominatorMetric: "screenPageViews",
        formula: "click_agendar_pastore (eventCount) ÷ page_view da página Ipanema (screenPageViews) × 100",
      },
    },
  },
});
const kpisZero = semTrafego.mktIpanemaKpis.innerHTML;
caso("Visitas 0 aparece como 0", /Visitas<\/span>\s*<strong class="mkt-kpi-value">0</.test(kpisZero));
caso("Conversão com denominador 0 vira N/D, nunca 0%",
     /Conversão<\/span>\s*<strong class="mkt-kpi-value">N\/D</.test(kpisZero));
caso("tooltip explica por que é N/D e não 0%",
     kpisZero.includes("a divisão é indefinida"));

// ── 4. Snapshot sem o bloco (painel antigo) ─────────────────────────────────
console.log("── Snapshot sem o bloco ──");
const semBloco = render({ meta: META, ga4: { totals: { users: 10, sessions: 12, pageviews: 20 } } });
caso("painel fica oculto quando o snapshot ainda não tem o bloco",
     semBloco.mktIpanemaPanel.hidden === true);
caso("nada é renderizado nos KPIs", semBloco.mktIpanemaKpis.innerHTML === "");

const semGa4 = render(null);
caso("snapshot ausente também mantém o painel oculto",
     semGa4.mktIpanemaPanel.hidden === true);

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
process.exit(0);
