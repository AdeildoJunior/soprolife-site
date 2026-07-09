#!/usr/bin/env node
// SoproLife — Testes do "Hoje eu tenho que fazer o quê?" (M10).
// 100% local, sem dependência externa, fixtures sintéticas.
// Uso: node painel-soprolife/scripts/test-today-actions.js
// Exit: 0 = todos passaram | 1 = houve falha.

const path = require("path");
const brain = require(path.resolve(__dirname, "../js/operational-brain.js"));
const db = require(path.resolve(__dirname, "../js/daily-briefing.js"));
const { buildTodayQueue, TODAY_MAX, TODAY_NIVEIS } =
  require(path.resolve(__dirname, "../js/today-actions.js"));

let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}

const CHAVES = ["source", "status", "acaoPrincipal", "proximas", "totalFila"];
const CHAVES_ITEM = ["ordem", "titulo", "origem", "motivo", "nivel", "proximoPasso"];

console.log("M10 — testes do Hoje eu tenho que fazer o quê? (fixtures sintéticas)");

// 1. Nulos/vazios não quebram; shape estável
const qNull = buildTodayQueue(null);
caso("input null não quebra", !!qNull && typeof qNull === "object");
caso("shape tem TODAS as chaves do contrato",
     JSON.stringify(Object.keys(qNull).sort()) === JSON.stringify([...CHAVES].sort()));
caso("sem fontes -> fila vazia e status vazio",
     qNull.acaoPrincipal === null && qNull.proximas.length === 0 &&
     qNull.totalFila === 0 && qNull.status === "vazio");
caso("source com flags seguras",
     qNull.source.safeToDisplay === true && qNull.source.containsPersonalData === false &&
     qNull.source.containsHealthData === false);

// 2. Estado sintético rico (mesma fixture da suíte M9)
const payloads = {
  saudeOperacional: { status_geral: "atencao", alertas: [{ nivel: "atencao", titulo: "Y" }] },
  b2bStats: { totalOportunidades: 4, precisamFollowup: 2, convertidas: 1, semProximoPasso: 1 },
  followupPacientes: { espirometria: { atrasados: 1, hoje: 0 }, consultas: { atrasados: 0, hoje: 0 } },
  auditoria: { stats: { total_eventos: 3, erros: 0 } },
  ultimosLancamentos: { stats: { hoje: 2 } },
};
const acoes = { acoesB2B: [
  { id: "B-1", prioridade: "alta", titulo: "Tratar follow-ups B2B atrasados", origem: "Follow-up Clínicas", proximoPasso: "p" },
  { id: "B-2", prioridade: "media", titulo: "Definir próximo passo", origem: "CRM B2B", proximoPasso: "p" },
  { id: "B-3", prioridade: "baixa", titulo: "Arquivar registros antigos", origem: "Saúde Operacional", proximoPasso: "p" },
] };
const st = brain.buildOperationalBrainState(payloads);
const dq = brain.buildDecisionQueue(st, acoes);
const briefing = db.buildDailyBriefingReal(st, dq, payloads);
const q = buildTodayQueue({ brainState: st, decisionQueue: dq, briefing });

caso("ação nº 1 = topo da fila de decisão (maior score)",
     q.acaoPrincipal && q.acaoPrincipal.titulo === dq.fila[0].titulo, JSON.stringify(q.acaoPrincipal));
caso("ação nº 1 tem ordem 1", q.acaoPrincipal.ordem === 1);
caso("próximas seguem numeração sequencial",
     q.proximas.every((a, i) => a.ordem === i + 2));
caso("itens têm exatamente as chaves do contrato",
     [q.acaoPrincipal, ...q.proximas].every((it) =>
       JSON.stringify(Object.keys(it).sort()) === JSON.stringify([...CHAVES_ITEM].sort())));
caso("níveis sempre válidos",
     [q.acaoPrincipal, ...q.proximas].every((it) => TODAY_NIVEIS.includes(it.nivel)));
caso("status espelha o briefing (atencao)", q.status === "atencao", q.status);
caso("fila respeita o teto TODAY_MAX", q.totalFila <= TODAY_MAX);
caso("totalFila consistente", q.totalFila === 1 + q.proximas.length);

// 3. Dedup: título repetido entre fila e briefing entra uma vez só
const brutoQ = JSON.stringify([q.acaoPrincipal, ...q.proximas].map((i) => i.titulo.toLowerCase()));
caso("sem título duplicado na fila",
     new Set(JSON.parse(brutoQ)).size === q.totalFila);

// 4. Dia crítico: riscos vão para a frente da fila
const payloadsRisco = { ...payloads,
  saudeOperacional: { status_geral: "critico", alertas: [{ nivel: "critico", titulo: "Z" }] },
  auditoria: { stats: { total_eventos: 3, erros: 2 } } };
const stR = brain.buildOperationalBrainState(payloadsRisco);
const dqR = brain.buildDecisionQueue(stR, acoes);
const bR = db.buildDailyBriefingReal(stR, dqR, payloadsRisco);
const qR = buildTodayQueue({ brainState: stR, decisionQueue: dqR, briefing: bR });
caso("dia crítico -> status critico", qR.status === "critico", qR.status);
caso("dia crítico -> ação nº 1 é tratar risco",
     qR.acaoPrincipal && /^Tratar risco:/.test(qR.acaoPrincipal.titulo), JSON.stringify(qR.acaoPrincipal));
caso("risco entra com nível crítico", qR.acaoPrincipal.nivel === "critico");

// 5. PII/segredo não vaza em nenhum texto
const dqSujo = brain.buildDecisionQueue(st, { acoesB2B: [
  { id: "S1", prioridade: "alta", titulo: "ligar (21) 99999-8888", origem: "CRM B2B", proximoPasso: "x@y.com" },
  { id: "S2", prioridade: "alta", titulo: "doc 123.456.789-09", origem: "CRM B2B", proximoPasso: "ya29.abc" },
] });
const bSujo = db.buildDailyBriefingReal(st, dqSujo, payloads);
const qSujo = buildTodayQueue({ brainState: st, decisionQueue: dqSujo, briefing: bSujo });
const bruto = JSON.stringify(qSujo);
caso("telefone não vaza", !bruto.includes("99999-8888"));
caso("e-mail não vaza", !bruto.includes("x@y.com"));
caso("CPF não vaza", !bruto.includes("123.456.789-09"));
caso("token não vaza", !bruto.includes("ya29.abc"));

// 6. Campos extras nas ações não vazam (shape fixo)
const dqExtra = brain.buildDecisionQueue(st, { acoesB2B: [
  { id: "X", prioridade: "alta", titulo: "t", origem: "CRM B2B", proximoPasso: "p",
    telefone: "(21) 97777-1111", interno: { a: 1 } }] });
const qExtra = buildTodayQueue({ brainState: st, decisionQueue: dqExtra,
  briefing: db.buildDailyBriefingReal(st, dqExtra, payloads) });
caso("campo extra não vaza", !JSON.stringify(qExtra).includes("97777-1111"));

// 7. Briefing demo passa como status demo
const qDemo = buildTodayQueue({ briefing: { status: "demo", fazerHoje: ["Ação demo 1", "Ação demo 2"] } });
caso("briefing demo -> status demo com fila", qDemo.status === "demo" && qDemo.totalFila === 2);
caso("complemento do briefing recebe origem Briefing Diário",
     qDemo.acaoPrincipal.origem === "Briefing Diário");

// 8. Teto da fila com excesso de itens
const muitas = { fila: Array.from({ length: 12 }, (_, i) => (
  { id: `F-${i}`, titulo: `Ação número ${i}`, origem: "CRM B2B", proximoPasso: "p", nivel: "media", score: 50 - i })) };
const qMuitas = buildTodayQueue({ decisionQueue: muitas, briefing: { status: "atencao" } });
caso("excesso de itens é cortado no teto", qMuitas.totalFila === TODAY_MAX);

console.log();
if (falhas) { console.log(`RESULTADO: ${falhas} caso(s) FALHARAM.`); process.exit(1); }
console.log("RESULTADO: todos os casos passaram.");
