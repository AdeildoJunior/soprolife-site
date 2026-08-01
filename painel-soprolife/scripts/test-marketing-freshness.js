#!/usr/bin/env node
// SoproLife — Testes do frescor de Marketing & SEO no painel (M14.3A.1).
// 100% local, relógio injetado, mesmas fixtures sintéticas do teste Python
// (scripts/fixtures/freshness/) — os dois lados do contrato devem concordar.
// Uso: node painel-soprolife/scripts/test-marketing-freshness.js
// Exit: 0 = todos passaram | 1 = houve falha.

const path = require("path");
const fs = require("fs");
const mf = require(path.resolve(__dirname, "../js/marketing-freshness.js"));

let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}

const FIXTURES = path.resolve(__dirname, "fixtures/freshness");
const fixture = (n) => JSON.parse(fs.readFileSync(path.join(FIXTURES, `${n}.json`), "utf8"));
const AGORA = Date.parse("2026-07-12T12:00:00Z");

console.log("── Estados por fixture (mesmos resultados do lado Python) ──");
const ESPERADOS = {
  "fresh": mf.MF_FRESH,
  "stale": mf.MF_STALE,
  "authentication-required": mf.MF_AUTH,
  "unavailable": mf.MF_UNAVAILABLE,
  "legacy-v1": mf.MF_AUTH,
};
for (const [nome, estado] of Object.entries(ESPERADOS)) {
  const av = mf.mfAvaliar(fixture(nome), AGORA);
  caso(`${nome} → ${estado}`, av.overall === estado, `obteve ${av.overall}`);
}
caso("snapshot null → unknown", mf.mfAvaliar(null, AGORA).overall === mf.MF_UNKNOWN);

console.log("── Selos honestos por estado ──");
caso("fresh → 'Atualizado'", mf.mfRotulo(mf.MF_FRESH).label === "Atualizado");
// M21 — vocabulário do painel: "Dados antigos" (stale) e "Falha temporária"
// (error), mais os dois estados novos.
caso("stale → 'Dados antigos'", mf.mfRotulo(mf.MF_STALE).label === "Dados antigos");
caso("credential_pending → 'Credencial/configuração pendente'",
     mf.mfRotulo(mf.MF_CREDENTIAL).label === "Credencial/configuração pendente");
caso("refreshing → 'Atualizando'",
     mf.mfRotulo(mf.MF_REFRESHING).label === "Atualizando");
caso("auth → 'Reautenticação necessária'",
     mf.mfRotulo(mf.MF_AUTH).label === "Reautenticação necessária");
caso("unavailable → 'Fonte indisponível'",
     mf.mfRotulo(mf.MF_UNAVAILABLE).label === "Fonte indisponível");
caso("estado inventado → rótulo de desconhecido (nunca 'Atualizado')",
     mf.mfRotulo("qualquer-coisa").label === "Estado desconhecido");

console.log("── Relógio injetável ──");
const snapFresh = fixture("fresh");
caso("fresh no mesmo dia",
     mf.mfAvaliar(snapFresh, Date.parse("2026-07-12T10:00:00Z")).overall === mf.MF_FRESH);
caso("stale 3 dias depois (sem tocar o arquivo)",
     mf.mfAvaliar(snapFresh, Date.parse("2026-07-15T10:00:00Z")).overall === mf.MF_STALE);

console.log("── GA4 e Search Console independentes ──");
const misto = fixture("fresh");
misto.meta.sourceStatus.ga4 = {
  ...misto.meta.sourceStatus.ga4,
  status: "failed", errorCode: "AUTH_REQUIRED", authenticationRequired: true,
  errorMessageSafe: "Reautenticação necessária. Execute a renovação do ADC manualmente.",
};
const avMisto = mf.mfAvaliar(misto, AGORA);
caso("SC fresh com GA4 em auth", avMisto.fontes.searchConsole.status === mf.MF_FRESH);
caso("GA4 em auth", avMisto.fontes.ga4.status === mf.MF_AUTH);
caso("agregado = pior estado", avMisto.overall === mf.MF_AUTH);

console.log("── Snapshot legado v1 (formato de produção pré-M14.3A.1) ──");
const avLegacy = mf.mfAvaliar(fixture("legacy-v1"), AGORA);
caso("legado sintetiza sourceStatus", Object.keys(avLegacy.fontes).length === 2);
caso("warning de reauth vira AUTH (nunca 'sem tráfego')",
     avLegacy.fontes.searchConsole.status === mf.MF_AUTH);
caso("mensagem segura no lugar do erro técnico",
     avLegacy.fontes.searchConsole.errorMessageSafe.includes("Reautenticação"));

console.log("── Período coberto separado da data de sincronização ──");
const avFresh = mf.mfAvaliar(snapFresh, AGORA);
caso("generatedAt exposto", avFresh.generatedAt === "2026-07-12T09:00:00+00:00");
caso("período coberto exposto separadamente",
     avFresh.period && avFresh.period.end === "2026-07-11");

console.log("── Fonte não configurada não rebaixa o agregado ──");
const parcial = fixture("fresh");
parcial.meta.sourceStatus.ga4 = {
  ...parcial.meta.sourceStatus.ga4,
  status: "failed", errorCode: "NOT_CONFIGURED", sourceAvailable: false,
  lastSuccessAt: null,
};
delete parcial.ga4;
caso("SC fresh + GA4 não configurado → agregado fresh",
     mf.mfAvaliar(parcial, AGORA).overall === mf.MF_FRESH);

console.log("── Cache-busting do app.js: por sessão e sem token ──");
const appSrc = fs.readFileSync(path.resolve(__dirname, "../js/app.js"), "utf8");
caso("bust é constante de sessão (DATA_CACHE_BUST)",
     appSrc.includes("const DATA_CACHE_BUST") &&
     appSrc.includes("cacheBust = DATA_CACHE_BUST") &&
     appSrc.includes("_cb=${cacheBust}"));
caso("refresh pode forçar cache-buster novo na mesma sessão",
     /loadOptionalJson\(\s*"\.\/data\/marketing-seo\.local\.json",\s*`\$\{Date\.now/.test(appSrc));
caso("nenhum token/credencial na URL de dados",
     !/[?&](token|key|auth|credential)[=$]/i.test(appSrc.match(/function withCacheBust[\s\S]{0,300}/)[0]));
caso("banner de frescor ligado no app.js",
     appSrc.includes("mktFreshnessBanner") && appSrc.includes("mfAvaliar"));
caso("KPI vazio honesto quando não há dados (— em vez de zero)",
     appSrc.includes("mkt-kpi-empty") && appSrc.includes("Sem dados sincronizados"));

const indexSrc = fs.readFileSync(path.resolve(__dirname, "../index.html"), "utf8");
caso("index.html inclui marketing-freshness.js antes do app.js",
     indexSrc.indexOf("marketing-freshness.js") !== -1 &&
     indexSrc.indexOf("marketing-freshness.js") < indexSrc.indexOf("js/app.js?"));
caso("index.html tem o container do banner de frescor",
     indexSrc.includes('id="mktFreshnessBanner"'));

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
