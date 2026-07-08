#!/usr/bin/env node
// SoproLife — Testes do Briefing Diário Real (M9).
// 100% local, sem dependência externa, fixtures sintéticas.
// Uso: node painel-soprolife/scripts/test-daily-briefing.js
// Exit: 0 = todos passaram | 1 = houve falha.

const path = require("path");
const fs = require("fs");
const brain = require(path.resolve(__dirname, "../js/operational-brain.js"));
const db = require(path.resolve(__dirname, "../js/daily-briefing.js"));
const { buildDailyBriefingReal, buildExecutiveSummary, buildTodayActionList,
        buildBriefingRiskFlags, DB_AREAS } = db;

let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}

const SECOES = ["source", "status", "titulo", "resumoExecutivo", "aconteceu",
  "mudou", "atrasado", "fazerHoje", "podeEsperar", "maiorRetorno", "riscos", "porArea"];

console.log("M9 — testes do Briefing Diário Real (fixtures sintéticas)");

// 1–3. Nulos/vazios não quebram; shape estável
const bNull = buildDailyBriefingReal(null, null, null);
caso("payload null não quebra", !!bNull && typeof bNull === "object");
caso("briefing tem TODAS as seções obrigatórias",
     SECOES.every((k) => k in bNull), SECOES.filter((k) => !(k in bNull)).join(","));
const stVazio = brain.buildOperationalBrainState(null);
const dqVazio = brain.buildDecisionQueue(stVazio, null);
const bVazio = buildDailyBriefingReal(stVazio, dqVazio, null);
caso("brainState vazio gera briefing estável",
     Array.isArray(bVazio.fazerHoje) && bVazio.maiorRetorno === "—");
caso("sem fontes vivas -> status demo", bVazio.status === "demo");

// 4. Estado sintético rico — status atenção previsível
const payloads = {
  saudeOperacional: { status_geral: "atencao", alertas: [{ nivel: "atencao", titulo: "Y" }] },
  b2bStats: { totalOportunidades: 4, precisamFollowup: 2, convertidas: 1, semProximoPasso: 1 },
  followupPacientes: { espirometria: { atrasados: 1, hoje: 0 }, consultas: { atrasados: 0, hoje: 0 } },
  auditoria: { stats: { total_eventos: 3, erros: 0 } },
  ultimosLancamentos: { stats: { hoje: 2 } },
};
const st = brain.buildOperationalBrainState(payloads);
const acoes = { acoesB2B: [
  { id: "B-1", prioridade: "alta", titulo: "Tratar follow-ups B2B atrasados", origem: "Follow-up Clínicas", proximoPasso: "p" },
  { id: "B-2", prioridade: "media", titulo: "Definir próximo passo", origem: "CRM B2B", proximoPasso: "p" },
  // baixa genuína: origem não-comercial (a matriz repontua baixas B2B p/ média)
  { id: "B-3", prioridade: "baixa", titulo: "Arquivar registros antigos", origem: "Saúde Operacional", proximoPasso: "p" },
] };
const dq = brain.buildDecisionQueue(st, acoes);
const b = buildDailyBriefingReal(st, dq, payloads);
caso("com fontes vivas -> dadosReais true", b.source.dadosReais === true);
caso("atrasos + ações -> status atencao (previsível)", b.status === "atencao", b.status);
caso("atrasos B2B entram em atrasado", b.atrasado.some((t) => /follow-up/i.test(t)));
caso("fazerHoje espelha top3 da fila", b.fazerHoje.length === dq.top3.length);
caso("maiorRetorno = próxima melhor ação", b.maiorRetorno === "Tratar follow-ups B2B atrasados");
caso("podeEsperar conta as baixas", b.podeEsperar.length === 1);

// 5. Risco operacional -> status critico + seção riscos
const payloadsRisco = { ...payloads,
  saudeOperacional: { status_geral: "critico", alertas: [{ nivel: "critico", titulo: "Z" }] },
  auditoria: { stats: { total_eventos: 3, erros: 2 } } };
const stR = brain.buildOperationalBrainState(payloadsRisco);
const bR = buildDailyBriefingReal(stR, brain.buildDecisionQueue(stR, acoes), payloadsRisco);
caso("risco operacional gera seção riscos", bR.riscos.length >= 1);
caso("risco -> status critico previsível", bR.status === "critico");

// 6. porArea: 7 áreas fixas, valores em arrays
caso("porArea tem as 7 áreas fixas como arrays",
     DB_AREAS.every((a) => Array.isArray(b.porArea[a])));

// 7. Campos extras em ações são ignorados (shape fixo do briefing)
const dqExtra = brain.buildDecisionQueue(st, { acoesB2B: [
  { id: "X", prioridade: "alta", titulo: "t", origem: "CRM B2B", proximoPasso: "p",
    telefone: "(21) 97777-1111", interno: { a: 1 } }] });
const bExtra = buildDailyBriefingReal(st, dqExtra, payloads);
caso("chaves do briefing são exatamente as do contrato",
     JSON.stringify(Object.keys(bExtra).sort()) === JSON.stringify([...SECOES].sort()));
caso("campo extra não vaza", !JSON.stringify(bExtra).includes("97777-1111"));

// 8. PII/segredo não vaza em nenhum texto
const dqSujo = brain.buildDecisionQueue(st, { acoesB2B: [
  { id: "S1", prioridade: "alta", titulo: "ligar (21) 99999-8888", origem: "CRM B2B", proximoPasso: "x@y.com" },
  { id: "S2", prioridade: "alta", titulo: "doc 123.456.789-09", origem: "CRM B2B", proximoPasso: "ya29.abc" },
] });
const bSujo = buildDailyBriefingReal(st, dqSujo, payloads);
const bruto = JSON.stringify(bSujo);
caso("telefone não vaza", !bruto.includes("99999-8888"));
caso("e-mail não vaza", !bruto.includes("x@y.com"));
caso("CPF não vaza", !bruto.includes("123.456.789-09"));
caso("token não vaza", !bruto.includes("ya29.abc"));

// 9. Executive summary curto e seguro; helpers
caso("resumo executivo curto (≤ 220 chars)", b.resumoExecutivo.length <= 220);
caso("resumo executivo cita o dia de atenção", /atenção/i.test(b.resumoExecutivo));
caso("buildTodayActionList numera as ações",
     buildTodayActionList(b).every((a, i) => a.ordem === i + 1 && typeof a.titulo === "string"));
caso("buildBriefingRiskFlags deduplica",
     buildBriefingRiskFlags({ _brainState: stR, _decisionQueue: dq }).length ===
     new Set(buildBriefingRiskFlags({ _brainState: stR, _decisionQueue: dq })).size);

// 10. Demo commitável seguro
const demo = JSON.parse(fs.readFileSync(
  path.resolve(__dirname, "../data/briefing-diario.json"), "utf8"));
caso("demo: flags seguras",
     demo.source.safeToDisplay === true && demo.source.containsPersonalData === false &&
     demo.source.containsHealthData === false && demo.source.dadosReais === false);
caso("demo: sem padrões de PII",
     !/\(?\d{2}\)?\s?\d{4,5}-\d{4}|\d{3}\.\d{3}\.\d{3}-\d{2}|ya29\.|AIza[A-Za-z0-9_-]{10}|[a-z0-9._]+@[a-z]+\.[a-z]{2,}/i
       .test(JSON.stringify(demo)));
caso("demo: tem as seções do contrato", SECOES.every((k) => k in demo));

console.log();
if (falhas) { console.log(`RESULTADO: ${falhas} caso(s) FALHARAM.`); process.exit(1); }
console.log("RESULTADO: todos os casos passaram.");
