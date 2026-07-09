// SoproLife — M10 "Hoje eu tenho que fazer o quê?".
// Camada acima do M8/M9: consome brainState (buildOperationalBrainState),
// decisionQueue (buildDecisionQueue) e o briefing diário (buildDailyBriefingReal)
// e produz a fila ÚNICA de execução do dia — ação nº 1 em destaque + próximas.
// Puro: sem DOM, sem fetch, sem rede, sem persistência (feito/pendente fica
// para etapa futura). Shape fixo; todo texto emitido passa pelo sanitizador
// do M4 (acoesTextoSeguro) — campo inesperado não existe na saída.

/* eslint-disable no-undef */
const _taSan = (typeof acoesTextoSeguro === "function")
  ? acoesTextoSeguro
  : require("./operational-actions.js").acoesTextoSeguro;

const TODAY_MAX = 5;
const TODAY_NIVEIS = ["critico", "alta", "media", "baixa"];
const TODAY_STATUS = ["demo", "critico", "atencao", "ok"];

function _taObj(v) { return (v && typeof v === "object" && !Array.isArray(v)) ? v : null; }
function _taArr(v) { return Array.isArray(v) ? v : []; }
function _taFrase(t) { return _taSan(t, "—"); }
function _taNivel(n) {
  const s = String(n || "").toLowerCase();
  return TODAY_NIVEIS.includes(s) ? s : "media";
}

// { brainState, decisionQueue, briefing } -> fila única do dia (shape fixo).
// Ordem de composição: riscos do dia crítico > fila de decisão (já pontuada
// por score no M8) > complementos do briefing (fazer hoje, atrasados).
// Dedup por título; nulos/vazios nunca quebram.
function buildTodayQueue(input) {
  const inp = _taObj(input) || {};
  const dq = _taObj(inp.decisionQueue) || {};
  const briefing = _taObj(inp.briefing) || {};

  const vistos = new Set();
  const itens = [];
  const add = (titulo, origem, motivo, nivel, proximoPasso) => {
    const t = _taFrase(titulo);
    if (t === "—") return;
    const chave = t.toLowerCase();
    if (vistos.has(chave)) return;
    vistos.add(chave);
    itens.push({
      titulo: t,
      origem: _taFrase(origem),
      motivo: _taFrase(motivo),
      nivel: _taNivel(nivel),
      proximoPasso: _taFrase(proximoPasso),
    });
  };

  // 1. Dia crítico: riscos vão para a frente da fila (segurança antes de números).
  if (String(briefing.status) === "critico") {
    for (const r of _taArr(briefing.riscos).slice(0, 2)) {
      add(`Tratar risco: ${String(r)}`, "Briefing Diário", "Risco ativo hoje", "critico",
        "Resolver o risco antes das demais ações do dia.");
    }
  }

  // 2. Fila de decisão do Cérebro Operacional (ordenada por score no M8).
  for (const a of _taArr(dq.fila)) {
    const it = _taObj(a);
    if (!it) continue;
    add(it.titulo, it.origem,
      `Prioridade ${_taNivel(it.nivel)} na fila de decisão`, it.nivel, it.proximoPasso);
  }

  // 3. Complementos do briefing que não entraram acima (dedup por título).
  for (const t of _taArr(briefing.fazerHoje)) add(t, "Briefing Diário", "Marcada para hoje", "media", "");
  for (const t of _taArr(briefing.atrasado)) add(t, "Briefing Diário", "Atrasada — destravar", "media", "");

  const fila = itens.slice(0, TODAY_MAX).map((it, i) => ({ ordem: i + 1, ...it }));

  const brStatus = String(briefing.status || "");
  const status = fila.length === 0 ? "vazio"
    : (TODAY_STATUS.includes(brStatus) ? brStatus : "atencao");

  return {
    source: {
      type: "today_actions",
      safeToDisplay: true,
      containsPersonalData: false,
      containsHealthData: false,
      generatedAt: new Date().toISOString(),
      nota: "Calculado no navegador a partir do Cérebro Operacional e do Briefing Diário — nunca de dados privados.",
    },
    status,
    acaoPrincipal: fila[0] || null,
    proximas: fila.slice(1),
    totalFila: fila.length,
  };
}

// Node (testes locais) — no navegador, as funções ficam no escopo global.
if (typeof module === "object" && module.exports) {
  module.exports = { buildTodayQueue, TODAY_MAX, TODAY_NIVEIS };
}
