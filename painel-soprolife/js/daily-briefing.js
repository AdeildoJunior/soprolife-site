// SoproLife — M9 Briefing Diário Real do Cérebro Operacional.
// Camada ACIMA do M8: consome brainState (buildOperationalBrainState) e
// decisionQueue (buildDecisionQueue) e produz o briefing completo do dia,
// com shape estável (docs/cerebro-operacional/05-briefing-diario.md).
// Puro: sem fetch, sem DOM, sem rede, sem dependência externa.
// Segurança: todo texto emitido passa pelo sanitizador do M4; shapes fixos.

/* eslint-disable no-undef */
const _dbSan = (typeof acoesTextoSeguro === "function")
  ? acoesTextoSeguro
  : require("./operational-actions.js").acoesTextoSeguro;

const DB_AREAS = ["Comercial", "CRM", "Pacientes", "Financeiro",
                  "Marketing", "Operacao", "Sistemas"];
const DB_MAX_ITENS = 4;
const DB_RESUMO_MAX = 200;

function _dbObj(v) { return (v && typeof v === "object" && !Array.isArray(v)) ? v : null; }
function _dbArr(v) { return Array.isArray(v) ? v : []; }
function _dbFrase(t) { return _dbSan(t, "—"); }
function _dbLista(arr) {
  return _dbArr(arr).slice(0, DB_MAX_ITENS)
    .map((t) => _dbFrase(typeof t === "string" ? t : (t && t.titulo) || ""))
    .filter((t) => t && t !== "—");
}

// ── Seções (6 perguntas) a partir de brainState + decisionQueue ─────────────
function buildBriefingSections(input) {
  const inp = _dbObj(input) || {};
  const st = _dbObj(inp.brainState) || {};
  const dq = _dbObj(inp.decisionQueue) || {};
  const diag = _dbObj(st.diagnostico) || {};
  const sis = _dbObj(st.sistemas) || {};
  const com = _dbObj(st.comercial) || {};
  const saude = _dbObj(st.saude) || {};
  const fila = _dbArr(dq.fila);

  const aconteceu = [];
  if (sis.eventosHoje > 0) aconteceu.push(`${sis.eventosHoje} evento(s) operacionais registrados hoje.`);
  if (sis.auditoriaErros === 0 && _dbObj(st.fontes)?.auditoria) {
    aconteceu.push("Escritas do painel auditadas sem erros.");
  }
  aconteceu.push(..._dbArr(diag.dinheiro).slice(0, 1));

  const mudou = [];
  if (saude.statusGeral && saude.statusGeral !== "—") mudou.push(`Saúde operacional: ${saude.statusGeral}.`);
  if (com.convertidas > 0) mudou.push(`${com.convertidas} parceria(s) no status convertida.`);
  mudou.push(..._dbArr(diag.bom).slice(0, 1));

  const atrasado = [..._dbArr(diag.atrasado), ..._dbArr(diag.parado)];

  const fazerHoje = _dbArr(dq.top3).map((a) => (a && a.titulo) || "");

  const baixas = fila.filter((a) => a && a.nivel === "baixa").length;
  const podeEsperar = baixas > 0
    ? [`${baixas} ação(ões) de prioridade baixa podem esperar.`] : [];

  const melhor = _dbObj(dq.proximaMelhorAcao);
  const maiorRetorno = melhor ? _dbFrase(melhor.titulo) : "—";

  return {
    aconteceu: _dbLista(aconteceu),
    mudou: _dbLista(mudou),
    atrasado: _dbLista(atrasado),
    fazerHoje: _dbLista(fazerHoje),
    podeEsperar: _dbLista(podeEsperar),
    maiorRetorno,
  };
}

// ── Riscos do dia (diagnóstico + alertas da fila de decisão) ────────────────
function buildBriefingRiskFlags(briefing) {
  const b = _dbObj(briefing) || {};
  const st = _dbObj(b._brainState) || {};
  const dq = _dbObj(b._decisionQueue) || {};
  const diag = _dbObj(st.diagnostico) || {};
  const vistos = new Set();
  const riscos = [];
  for (const r of [..._dbArr(diag.risco), ..._dbArr(dq.alertasRisco)]) {
    const t = _dbFrase(r);
    if (t !== "—" && !vistos.has(t)) { vistos.add(t); riscos.push(t); }
  }
  return riscos.slice(0, DB_MAX_ITENS);
}

// ── Resumo executivo (curto, 1–2 frases, sanitizado) ────────────────────────
function buildExecutiveSummary(briefing) {
  const b = _dbObj(briefing) || {};
  const nAtras = _dbArr(b.atrasado).length;
  const nHoje = _dbArr(b.fazerHoje).length;
  const nRiscos = _dbArr(b.riscos).length;
  const status = String(b.status || "");

  let frase;
  if (status === "critico") {
    frase = `Dia CRÍTICO: ${nRiscos} risco(s) ativo(s) — tratar segurança/pipeline antes de decidir por números.`;
  } else if (status === "atencao") {
    frase = `Dia de atenção: ${nAtras} atraso(s) e ${nHoje} ação(ões) para hoje.`;
  } else if (status === "demo") {
    frase = "Briefing demonstrativo — sem fontes reais carregadas neste ambiente.";
  } else {
    frase = "Operação em dia: sem atrasos nem riscos relevantes.";
  }
  if (b.maiorRetorno && b.maiorRetorno !== "—") {
    frase += ` Maior retorno agora: ${b.maiorRetorno}`;
  }
  return _dbSan(frase.slice(0, DB_RESUMO_MAX), "—");
}

// ── Lista de ações do dia (ordem + título) ──────────────────────────────────
function buildTodayActionList(briefing) {
  const b = _dbObj(briefing) || {};
  return _dbArr(b.fazerHoje).map((titulo, i) => ({
    ordem: i + 1,
    titulo: _dbFrase(titulo),
  }));
}

// ── Por área: 7 chaves fixas, arrays ────────────────────────────────────────
function _dbPorArea(decisionQueue) {
  const dq = _dbObj(decisionQueue) || {};
  const origem = _dbObj(dq.porArea) || {};
  const porArea = {};
  for (const area of DB_AREAS) {
    const v = origem[area];
    porArea[area] = (v && v !== "—") ? [_dbFrase(v)] : [];
  }
  return porArea;
}

// ── Briefing completo (shape do contrato M9) ────────────────────────────────
function buildDailyBriefingReal(brainState, decisionQueue, payloads) {
  const st = _dbObj(brainState) || {};
  const dq = _dbObj(decisionQueue) || {};
  const fontes = _dbObj(st.fontes) || {};
  const dadosReais = Object.values(fontes).some((v) => v === true);

  const secoes = buildBriefingSections({ brainState: st, decisionQueue: dq });

  // Riscos precisam do estado/fila; injeta referências internas temporárias.
  const riscos = buildBriefingRiskFlags({ _brainState: st, _decisionQueue: dq });

  const saudeGeral = String(_dbObj(st.saude)?.statusGeral || "");
  let status;
  if (!dadosReais) status = "demo";
  else if (riscos.length > 0 || saudeGeral === "critico") status = "critico";
  else if (secoes.atrasado.length > 0 || secoes.fazerHoje.length > 0 || saudeGeral === "atencao") status = "atencao";
  else status = "ok";

  const briefing = {
    source: {
      type: "daily_briefing",
      safeToDisplay: true,
      containsPersonalData: false,
      containsHealthData: false,
      dadosReais,
      generatedAt: new Date().toISOString(),
      nota: "Gerado no navegador a partir dos summaries seguros já carregados — nunca de dados privados.",
    },
    status,
    titulo: _dbFrase(`Briefing do dia — ${new Date().toLocaleDateString("pt-BR")}`),
    resumoExecutivo: "",
    aconteceu: secoes.aconteceu,
    mudou: secoes.mudou,
    atrasado: secoes.atrasado,
    fazerHoje: secoes.fazerHoje,
    podeEsperar: secoes.podeEsperar,
    maiorRetorno: secoes.maiorRetorno,
    riscos,
    porArea: _dbPorArea(dq),
  };
  briefing.resumoExecutivo = buildExecutiveSummary(briefing);
  return briefing;
}

if (typeof module === "object" && module.exports) {
  module.exports = {
    buildDailyBriefingReal, buildBriefingSections, buildExecutiveSummary,
    buildTodayActionList, buildBriefingRiskFlags, DB_AREAS,
  };
}
