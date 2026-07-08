#!/usr/bin/env node
// SoproLife — Testes da Central de Ações Operacionais (M4).
// 100% local, sem dependência externa, sem rede, sem dados reais.
// Uso: node painel-soprolife/scripts/test-operational-actions.js
// Exit: 0 = todos passaram | 1 = houve falha.

const path = require("path");
const { buildOperationalActions, ACOES_GENERICO } =
  require(path.resolve(__dirname, "../js/operational-actions.js"));

let falhas = 0;
function caso(nome, cond, detalhe = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${detalhe ? " — " + detalhe : ""}`); }
}

function payloadBase(alertas) {
  return {
    source: { safeToDisplay: true, containsPersonalData: false, generatedAt: "2026-07-07T20:00:00-03:00" },
    status_geral: "atencao",
    indicadores: [],
    alertas,
  };
}

console.log("M4 — testes de buildOperationalActions (fixtures sintéticas)");

// 1. Sem alertas -> array vazio (estado vazio no painel)
caso("payload sem alertas -> []", buildOperationalActions(payloadBase([])).length === 0);

// 2/3. Níveis atenção e crítico mapeados; ação recomendada por família
const acoes = buildOperationalActions(payloadBase([
  { id: "ALERTA-PIPELINE-ATRASO", nivel: "atencao", titulo: "Dados sem atualização recente",
    mensagem: "x", proximo_passo: "Rodar update-local-data.sh." },
  { id: "ALERTA-CHECK-ACCESS", nivel: "critico", titulo: "Check de segurança falhou",
    mensagem: "y", proximo_passo: "Corrigir antes de deploy." },
]));
caso("alerta atencao -> acao nivel atencao", acoes[0].nivel === "atencao");
caso("alerta critico -> acao nivel critico", acoes[1].nivel === "critico");
caso("acao recomendada do pipeline cita atualizacao/timer",
     /atualização local|timer da VPS/.test(acoes[0].acao), acoes[0].acao);
caso("acao recomendada do check-access cita deploy/segurança",
     /deploy|segurança/.test(acoes[1].acao), acoes[1].acao);
caso("origem e status fixos",
     acoes.every((a) => a.origem === "Saúde Operacional" && a.status === "pendente"));
caso("geradoEm vem do source.generatedAt",
     acoes[0].geradoEm === "2026-07-07T20:00:00-03:00");

// 4. proximo_passo ausente -> fallback seguro
const semPasso = buildOperationalActions(payloadBase([
  { id: "ALERTA-X", nivel: "atencao", titulo: "T" },
]))[0];
caso("proximo_passo ausente -> fallback",
     /Sem próximo passo registrado/.test(semPasso.proximoPasso));

// 5. Campos extras inesperados -> ignorados (shape fixo da ação)
const comExtras = buildOperationalActions(payloadBase([
  { id: "ALERTA-X", nivel: "critico", titulo: "T", proximo_passo: "P",
    telefone: "(21) 99999-8888", debug: { secreto: true }, html: "<img onerror=x>" },
]))[0];
const SHAPE = ["id", "nivel", "titulo", "acao", "proximoPasso", "origem", "status", "geradoEm"];
caso("shape fixo da acao (extras ignorados)",
     JSON.stringify(Object.keys(comExtras).sort()) === JSON.stringify([...SHAPE].sort()),
     Object.keys(comExtras).join(","));
caso("campo extra com telefone nao vaza em nenhum valor",
     !JSON.stringify(comExtras).includes("99999-8888"));

// 6. Texto suspeito (segredo/CPF/e-mail/telefone/token) -> mensagem genérica
const suspeitos = [
  ["CPF",      { id: "A", nivel: "atencao", titulo: "doc 123.456.789-09", proximo_passo: "ok" }, "titulo"],
  ["telefone", { id: "A", nivel: "atencao", titulo: "T", proximo_passo: "ligar (21) 98888-7777" }, "proximoPasso"],
  ["e-mail",   { id: "A", nivel: "atencao", titulo: "mande para x@exemplo.com", proximo_passo: "ok" }, "titulo"],
  ["token ya29", { id: "A", nivel: "atencao", titulo: "T", proximo_passo: "use ya29.abc123" }, "proximoPasso"],
  ["bearer",   { id: "A", nivel: "atencao", titulo: "Bearer abcdef", proximo_passo: "ok" }, "titulo"],
  ["planilha", { id: "A", nivel: "atencao", titulo: "T", proximo_passo: "abra /spreadsheets/d/abc" }, "proximoPasso"],
];
for (const [nome, alerta, campo] of suspeitos) {
  const acao = buildOperationalActions(payloadBase([alerta]))[0];
  caso(`texto suspeito (${nome}) vira mensagem genérica`,
       acao[campo] === ACOES_GENERICO, acao[campo]);
}

// 7. Payload nulo/inválido -> [] sem quebrar
caso("payload null -> []", buildOperationalActions(null).length === 0);
caso("payload string -> []", buildOperationalActions("x").length === 0);
caso("alertas nao-array -> []", buildOperationalActions({ alertas: "nope" }).length === 0);
caso("alerta nao-objeto ignorado",
     buildOperationalActions(payloadBase([null, "x", 42])).length === 0);

// Extra: nível inválido -> desconhecido; texto longo truncado
const estranho = buildOperationalActions(payloadBase([
  { id: "A", nivel: "EXPLOSIVO", titulo: "t".repeat(500), proximo_passo: "p" },
]))[0];
caso("nivel invalido -> desconhecido", estranho.nivel === "desconhecido");
caso("titulo longo truncado (<= 220)", estranho.titulo.length <= 220);

console.log();
if (falhas) { console.log(`RESULTADO: ${falhas} caso(s) FALHARAM.`); process.exit(1); }
console.log("RESULTADO: todos os casos passaram.");
