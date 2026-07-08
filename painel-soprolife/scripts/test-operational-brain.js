#!/usr/bin/env node
// SoproLife — Testes do Cérebro Operacional (M8, esqueleto).
// 100% local, sem dependência externa, fixtures sintéticas.
// Uso: node painel-soprolife/scripts/test-operational-brain.js
// Exit: 0 = todos passaram | 1 = houve falha.

const path = require("path");
const fs = require("fs");
const brain = require(path.resolve(__dirname, "../js/operational-brain.js"));
const {
  buildOperationalBrainState, buildPriorityScore, buildDailyBriefing,
  buildProjectionSkeleton, buildDecisionQueue, BRAIN_QUEUE_MAX,
} = brain;

let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}

console.log("M8 — testes do Cérebro Operacional (fixtures sintéticas)");

// 1. Payload vazio/nulo não quebra; shapes estáveis
const stVazio = buildOperationalBrainState(null);
caso("estado com payload null não quebra", !!stVazio && typeof stVazio === "object");
caso("estado vazio tem diagnóstico com 6 baldes",
     ["bom", "atrasado", "parado", "atencao", "dinheiro", "risco"]
       .every((k) => Array.isArray(stVazio.diagnostico[k])));
caso("fontes ausentes viram false", stVazio.fontes.b2bStats === false);
caso("contadores de fonte ausente viram null", stVazio.comercial.oportunidades === null);

// 2. Summaries ausentes não quebram as demais funções
const dqVazio = buildDecisionQueue(stVazio, null);
caso("fila com tudo ausente é vazia e estável",
     dqVazio.fila.length === 0 && dqVazio.proximaMelhorAcao === null &&
     Array.isArray(dqVazio.top3) && typeof dqVazio.porArea === "object");
const brVazio = buildDailyBriefing(stVazio, dqVazio);
caso("briefing vazio mantém as 6 chaves",
     ["aconteceu", "mudou", "atrasado", "fazerHoje", "podeEsperar", "maiorRetorno"]
       .every((k) => k in brVazio));
caso("projeções vazias retornam 6 itens com shape",
     buildProjectionSkeleton(stVazio).length === 6 &&
     buildProjectionSkeleton(stVazio).every((p) =>
       ["id", "label", "status", "valorBase", "projecao30d", "premissa"]
         .every((k) => k in p)));

// 3. Estado com dados sintéticos coerentes
const payloads = {
  saudeOperacional: { status_geral: "atencao",
    alertas: [{ nivel: "critico", titulo: "X" }, { nivel: "atencao", titulo: "Y" }] },
  b2bStats: { totalOportunidades: 5, precisamFollowup: 2, convertidas: 1, semProximoPasso: 3 },
  followupPacientes: { espirometria: { atrasados: 2, hoje: 1 }, consultas: { atrasados: 1, hoje: 0 } },
  financeiro: { total_entradas_mes_atual: 2500, saldo_operacional: 3000 },
  custos: { total_mensal_atual: 2000 },
  marketing: { meta: { sources: { searchConsole: true, ga4: false } } },
  auditoria: { stats: { total_eventos: 4, erros: 1 } },
  ultimosLancamentos: { stats: { hoje: 3 } },
};
const st = buildOperationalBrainState(payloads);
caso("pacientes soma espirometria+consultas", st.pacientes.followupsAtrasados === 3);
caso("diagnóstico detecta atrasos", st.diagnostico.atrasado.length >= 2);
caso("diagnóstico detecta risco (crítico + erro auditoria)", st.diagnostico.risco.length >= 2);
caso("diagnóstico detecta dinheiro (funil + convertida + receita)", st.diagnostico.dinheiro.length === 3);

// 4. Motor de prioridade: ordenação e faixas
const alta = buildPriorityScore({ impactoFinanceiro: 3, urgencia: 3, riscoOperacional: 2 });
const media = buildPriorityScore({ urgencia: 2, impactoFinanceiro: 2 });
const baixa = buildPriorityScore({ facilidade: 1 });
caso("eixos altos -> nivel alta", alta.nivel === "alta", `score=${alta.score}`);
caso("eixos médios -> nivel media", media.nivel === "media", `score=${media.score}`);
caso("eixos baixos -> nivel baixa", baixa.nivel === "baixa", `score=${baixa.score}`);
caso("score é 0-100 e eixos inválidos não quebram",
     buildPriorityScore({ urgencia: 99, foo: "bar" }).score <= 100 &&
     buildPriorityScore(null).score === 0);

// 5. Fila de decisão: prioridade alta antes de média/baixa; limite; dedup
const acoes = {
  acoesOperacionais: [
    { id: "OP-1", nivel: "atencao", titulo: "Ação média op", origem: "Saúde Operacional", proximoPasso: "p" },
  ],
  acoesB2B: [
    { id: "B2B-1", prioridade: "alta", titulo: "Ação alta b2b", origem: "CRM B2B", proximoPasso: "p" },
    { id: "B2B-1", prioridade: "alta", titulo: "duplicada", origem: "CRM B2B", proximoPasso: "p" },
    { id: "B2B-2", prioridade: "baixa", titulo: "Ação baixa b2b", origem: "CRM B2B", proximoPasso: "p" },
  ],
};
const dq = buildDecisionQueue(st, acoes);
caso("alta vem ante de média/baixa", dq.fila[0].id === "B2B-1");
caso("dedup por id", dq.fila.filter((x) => x.id === "B2B-1").length === 1);
caso("proximaMelhorAcao = topo da fila", dq.proximaMelhorAcao.id === "B2B-1");
caso("porArea tem as 7 áreas fixas",
     ["Comercial", "CRM", "Pacientes", "Financeiro", "Marketing", "Operacao", "Sistemas"]
       .every((k) => k in dq.porArea));
const muitas = { acoesB2B: Array.from({ length: 20 }, (_, i) =>
  ({ id: `A-${i}`, prioridade: "media", titulo: `t${i}`, origem: "CRM B2B", proximoPasso: "p" })) };
caso(`fila limitada a ${BRAIN_QUEUE_MAX}`,
     buildDecisionQueue(st, muitas).fila.length === BRAIN_QUEUE_MAX);

// 6. Briefing estável e coerente
const br = buildDailyBriefing(st, dq);
caso("fazerHoje espelha top3", br.fazerHoje.length === dq.top3.length);
caso("maiorRetorno = título da melhor ação", br.maiorRetorno === "Ação alta b2b");
caso("atrasado vem do diagnóstico", br.atrasado.length >= 1);

// 7. PII/segredo não vaza (neutralizado pelo sanitizador)
const sujas = { acoesB2B: [
  { id: "S-1", prioridade: "alta", titulo: "ligar (21) 99999-8888", origem: "CRM B2B", proximoPasso: "mandar para x@y.com" },
  { id: "S-2", prioridade: "alta", titulo: "doc 123.456.789-09", origem: "CRM B2B", proximoPasso: "use ya29.abc" },
] };
const dqSujo = buildDecisionQueue(st, sujas);
const brutoTudo = JSON.stringify([dqSujo, buildDailyBriefing(st, dqSujo)]);
caso("telefone não vaza", !brutoTudo.includes("99999-8888"));
caso("e-mail não vaza", !brutoTudo.includes("x@y.com"));
caso("CPF não vaza", !brutoTudo.includes("123.456.789-09"));
caso("token não vaza", !brutoTudo.includes("ya29.abc"));

// 8. Campos extras são ignorados (shape fixo da fila)
const extra = buildDecisionQueue(st, { acoesB2B: [
  { id: "E-1", prioridade: "alta", titulo: "t", origem: "CRM B2B", proximoPasso: "p",
    telefone: "(21) 90000-0000", debug: { x: 1 } },
] }).fila[0];
const SHAPE = ["id", "titulo", "origem", "proximoPasso", "score", "nivel"];
caso("shape fixo do item da fila",
     JSON.stringify(Object.keys(extra).sort()) === JSON.stringify([...SHAPE].sort()),
     Object.keys(extra).join(","));
caso("extra não vaza em valor", !JSON.stringify(extra).includes("90000-0000"));

// 9. Demo commitável passa validação básica
const demo = JSON.parse(fs.readFileSync(
  path.resolve(__dirname, "../data/cerebro-operacional.json"), "utf8"));
caso("demo: flags de segurança corretas",
     demo.source.safeToDisplay === true && demo.source.containsPersonalData === false &&
     demo.source.containsHealthData === false && demo.source.dadosReais === false);
const demoTexto = JSON.stringify(demo);
caso("demo: sem padrões de PII/segredo",
     !/\(?\d{2}\)?\s?\d{4,5}-\d{4}|\d{3}\.\d{3}\.\d{3}-\d{2}|ya29\.|AIza[A-Za-z0-9_-]{10}|@[a-z]+\.[a-z]{2,}/.test(demoTexto));
caso("demo: 7 próximos módulos (M9–M15)", demo.proximos_modulos.length === 7);

console.log();
if (falhas) { console.log(`RESULTADO: ${falhas} caso(s) FALHARAM.`); process.exit(1); }
console.log("RESULTADO: todos os casos passaram.");
