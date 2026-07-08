// SoproLife — M8 Cérebro Operacional (esqueleto v0).
// Funções PURAS que transformam summaries seguros em: estado normalizado,
// score de prioridade, briefing diário, projeções e fila de decisão.
// Sem DOM, sem fetch, sem rede, sem dependência externa. Contratos estáveis
// em docs/cerebro-operacional/ — as camadas M9+ trocam a lógica interna
// SEM mudar os shapes daqui.
//
// Segurança (Camada 0): entradas já são summaries seguros (M2); ainda assim
// todo texto emitido passa pelo sanitizador do M4 (acoesTextoSeguro) e os
// shapes de saída são fixos (campo inesperado não existe).

/* eslint-disable no-undef */
const _brainSan = (typeof acoesTextoSeguro === "function")
  ? acoesTextoSeguro
  : require("./operational-actions.js").acoesTextoSeguro;

const BRAIN_QUEUE_MAX = 7;
const BRAIN_TOP = 3;
const BRAIN_BRIEFING_MAX = 4;

// Pesos do motor de prioridade (somam 100 — ver docs 03).
const BRAIN_PESOS = {
  impactoFinanceiro: 30, urgencia: 25, riscoOperacional: 20,
  crescimentoB2B: 10, pacientes: 10, facilidade: 5,
};

const BRAIN_AREAS = ["Comercial", "CRM", "Pacientes", "Financeiro",
                     "Marketing", "Operacao", "Sistemas"];

function _num(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function _arr(v) { return Array.isArray(v) ? v.filter((x) => x && typeof x === "object") : []; }

function _obj(v) { return (v && typeof v === "object" && !Array.isArray(v)) ? v : null; }

function _frase(texto) { return _brainSan(texto, "—"); }

// ── Camada 1+2 — Estado atual + diagnóstico ─────────────────────────────────
// payloads: ver docs/cerebro-operacional/02-contratos-de-dados-seguros.md.
function buildOperationalBrainState(payloads) {
  const p = _obj(payloads) || {};

  const saudeP = _obj(p.saudeOperacional);
  const alertasSaude = saudeP ? _arr(saudeP.alertas) : [];
  const b2b = _obj(p.b2bStats);
  const fuPac = _obj(p.followupPacientes);
  const espi = fuPac ? _obj(fuPac.espirometria) : null;
  const cons = fuPac ? _obj(fuPac.consultas) : null;
  const fin = _obj(p.financeiro);
  const custos = _obj(p.custos);
  const mkt = _obj(p.marketing);
  const mktSources = mkt ? (_obj(_obj(mkt.meta)?.sources) || _obj(mkt.sources)) : null;
  const aud = _obj(p.auditoria);
  const audStats = aud ? _obj(aud.stats) : null;
  const ul = _obj(p.ultimosLancamentos);
  const ulStats = ul ? _obj(ul.stats) : null;

  const estado = {
    geradoEm: new Date().toISOString(),
    fontes: {
      saudeOperacional: !!saudeP,
      acoesOperacionais: Array.isArray(p.acoesOperacionais),
      acoesB2B: Array.isArray(p.acoesB2B),
      b2bStats: !!b2b,
      resumoDashboard: !!_obj(p.resumoDashboard),
      followupPacientes: !!fuPac,
      financeiro: !!fin,
      custos: !!custos,
      marketing: !!mkt,
      auditoria: !!aud,
      ultimosLancamentos: !!ul,
    },
    saude: {
      statusGeral: _frase(saudeP ? saudeP.status_geral : "desconhecido"),
      alertasCriticos: alertasSaude.filter((a) => String(a.nivel) === "critico").length,
      alertasAtencao: alertasSaude.filter((a) => String(a.nivel) === "atencao").length,
    },
    comercial: {
      oportunidades: b2b ? _num(b2b.totalOportunidades) : null,
      precisamFollowup: b2b && b2b.precisamFollowup !== null && b2b.precisamFollowup !== undefined
        ? _num(b2b.precisamFollowup) : null,
      convertidas: b2b ? _num(b2b.convertidas) : null,
      semProximoPasso: b2b ? _num(b2b.semProximoPasso) : null,
    },
    pacientes: {
      followupsAtrasados: fuPac ? _num(espi?.atrasados) + _num(cons?.atrasados) : null,
      followupsHoje: fuPac ? _num(espi?.hoje) + _num(cons?.hoje) : null,
    },
    financeiro: {
      receitaMes: fin ? _num(fin.total_entradas_mes_atual, _num(fin.receita_exames, 0)) : null,
      saldo: fin ? _num(fin.saldo_operacional) : null,
      custoMensal: custos ? _num(custos.total_mensal_atual) : null,
    },
    marketing: {
      searchConsoleOk: mktSources ? mktSources.searchConsole === true : null,
      ga4Ok: mktSources ? mktSources.ga4 === true : null,
    },
    sistemas: {
      auditoriaErros: audStats ? _num(audStats.erros) : null,
      eventosHoje: ulStats ? _num(ulStats.hoje) : null,
    },
  };

  // Camada 2 — diagnóstico em 6 baldes (frases de template, sanitizadas).
  const d = { bom: [], atrasado: [], parado: [], atencao: [], dinheiro: [], risco: [] };
  const s = estado;

  if (s.saude.statusGeral === "ok") d.bom.push("Saúde operacional verde — pipeline e integrações OK.");
  if (s.saude.statusGeral === "atencao") d.atencao.push("Saúde operacional em atenção — ver seção Automações.");
  if (s.saude.statusGeral === "critico") d.risco.push("Saúde operacional CRÍTICA — tratar antes de decidir por números.");
  if (s.saude.alertasCriticos > 0) d.risco.push(`${s.saude.alertasCriticos} alerta(s) crítico(s) ativos.`);

  if (s.comercial.precisamFollowup > 0) d.atrasado.push(`${s.comercial.precisamFollowup} follow-up(s) B2B vencido(s) ou para hoje.`);
  if (s.pacientes.followupsAtrasados > 0) d.atrasado.push(`${s.pacientes.followupsAtrasados} follow-up(s) de paciente atrasado(s).`);
  if (s.comercial.semProximoPasso > 0) d.parado.push(`${s.comercial.semProximoPasso} oportunidade(s) B2B sem próximo passo.`);
  if (s.comercial.oportunidades > 0) d.dinheiro.push(`${s.comercial.oportunidades} oportunidade(s) B2B ativas no funil.`);
  if (s.comercial.convertidas > 0) d.dinheiro.push(`${s.comercial.convertidas} parceria(s) convertida(s) — ativar rotina gera receita.`);
  if (s.financeiro.receitaMes > 0) d.dinheiro.push("Receita registrada no mês — manter cadência de exames.");
  if (s.sistemas.auditoriaErros > 0) d.risco.push(`${s.sistemas.auditoriaErros} erro(s) de escrita na auditoria.`);
  if (s.marketing.searchConsoleOk === false || s.marketing.ga4Ok === false) {
    d.atencao.push("Marketing sem dados novos (Search Console/GA4) — conferir integração.");
  }
  if (s.comercial.oportunidades === 0 && s.fontes.b2bStats) d.atencao.push("Funil B2B vazio — priorizar prospecção.");

  estado.diagnostico = {
    bom: d.bom.map(_frase), atrasado: d.atrasado.map(_frase),
    parado: d.parado.map(_frase), atencao: d.atencao.map(_frase),
    dinheiro: d.dinheiro.map(_frase), risco: d.risco.map(_frase),
  };
  return estado;
}

// ── Camada 3 — Motor de prioridade ──────────────────────────────────────────
function buildPriorityScore(item) {
  const it = _obj(item) || {};
  const eixo = (k) => Math.max(0, Math.min(3, _num(it[k], 0)));
  let score = 0;
  for (const [k, peso] of Object.entries(BRAIN_PESOS)) score += (eixo(k) / 3) * peso;
  score = Math.round(score);
  const nivel = score >= 60 ? "alta" : score >= 30 ? "media" : "baixa";
  return { score, nivel };
}

// v0: mapeia ações M4/M5 (que só têm nível) para os 6 eixos — ver docs 03.
function _eixosDeAcao(acao) {
  const nivel = String(acao.nivel || acao.prioridade || "").toLowerCase();
  const origem = String(acao.origem || "").toLowerCase();
  const eixos = { impactoFinanceiro: 0, urgencia: 0, riscoOperacional: 0,
                  crescimentoB2B: 0, pacientes: 0, facilidade: 1 };
  if (nivel === "alta" || nivel === "critico") { eixos.urgencia = 3; eixos.riscoOperacional = 2; }
  else if (nivel === "media" || nivel === "atencao") { eixos.urgencia = 2; }
  else { eixos.urgencia = 1; }
  if (origem.includes("b2b") || origem.includes("lead") || origem.includes("clínica") || origem.includes("clinica")) {
    eixos.crescimentoB2B = 2; eixos.impactoFinanceiro = 2;
  }
  if (origem.includes("saúde") || origem.includes("saude")) eixos.riscoOperacional = Math.max(eixos.riscoOperacionais || eixos.riscoOperacional, 2);
  if (origem.includes("paciente")) eixos.pacientes = 3;
  return eixos;
}

// ── Camada 6 — Central de decisão ───────────────────────────────────────────
// brainState + { acoesOperacionais, acoesB2B } (as próprias ações M4/M5).
function buildDecisionQueue(brainState, acoes) {
  const st = _obj(brainState) || {};
  const a = _obj(acoes) || {};
  const todas = [..._arr(a.acoesOperacionais), ..._arr(a.acoesB2B)];

  const vistos = new Set();
  const fila = [];
  for (const acao of todas) {
    const id = _frase(acao.id);
    if (vistos.has(id)) continue;
    vistos.add(id);
    const { score, nivel } = buildPriorityScore(_eixosDeAcao(acao));
    fila.push({
      id,
      titulo: _frase(acao.titulo),
      origem: _frase(acao.origem),
      proximoPasso: _frase(acao.proximoPasso),
      score, nivel,
    });
  }
  fila.sort((x, y) => y.score - x.score);
  const filaLimitada = fila.slice(0, BRAIN_QUEUE_MAX);

  const diag = _obj(st.diagnostico) || {};
  const alertasRisco = Array.isArray(diag.risco) ? diag.risco.slice(0, 4) : [];

  const porArea = {};
  for (const area of BRAIN_AREAS) porArea[area] = "—";
  const achar = (fn) => filaLimitada.find(fn);
  const b2bAcao = achar((x) => /b2b|lead|clínica|clinica/i.test(x.origem));
  if (b2bAcao) porArea.Comercial = b2bAcao.titulo;
  const crmAcao = achar((x) => /revisar etapa|próximo passo|proximo passo/i.test(x.titulo));
  if (crmAcao) porArea.CRM = crmAcao.titulo;
  const pac = _obj(st.pacientes);
  if (pac && pac.followupsAtrasados > 0) porArea.Pacientes = _frase(`Tratar ${pac.followupsAtrasados} follow-up(s) de paciente atrasado(s).`);
  const fin = _obj(st.financeiro);
  if (fin && fin.custoMensal !== null) porArea.Financeiro = "Revisar pendências de custos e rateio do mês.";
  const mkt = _obj(st.marketing);
  if (mkt && (mkt.searchConsoleOk === false || mkt.ga4Ok === false)) porArea.Marketing = "Conferir integrações de Search Console/GA4.";
  const opAcao = achar((x) => /saúde|saude|operacional/i.test(x.origem));
  if (opAcao) porArea.Operacao = opAcao.titulo;
  const sis = _obj(st.sistemas);
  if (sis && sis.auditoriaErros > 0) porArea.Sistemas = _frase(`Investigar ${sis.auditoriaErros} erro(s) de escrita auditados.`);

  return {
    proximaMelhorAcao: filaLimitada[0] || null,
    top3: filaLimitada.slice(0, BRAIN_TOP),
    fila: filaLimitada,
    alertasRisco,
    porArea,
  };
}

// ── Camada 5 — Briefing diário ──────────────────────────────────────────────
function buildDailyBriefing(brainState, decisionQueue) {
  const st = _obj(brainState) || {};
  const dq = _obj(decisionQueue) || { fila: [], top3: [] };
  const diag = _obj(st.diagnostico) || {};
  const lim = (arr) => (Array.isArray(arr) ? arr : []).slice(0, BRAIN_BRIEFING_MAX).map(_frase);

  const aconteceu = [];
  const sis = _obj(st.sistemas);
  if (sis && sis.eventosHoje > 0) aconteceu.push(`${sis.eventosHoje} evento(s) operacionais registrados hoje.`);
  if (sis && sis.auditoriaErros === 0 && _obj(st.fontes)?.auditoria) aconteceu.push("Escritas do painel auditadas sem erros.");

  const mudou = [];
  const saude = _obj(st.saude);
  if (saude) mudou.push(`Saúde operacional: ${saude.statusGeral}.`);
  const com = _obj(st.comercial);
  if (com && com.convertidas > 0) mudou.push(`${com.convertidas} parceria(s) no status convertida.`);

  const baixas = (Array.isArray(dq.fila) ? dq.fila : []).filter((x) => x.nivel === "baixa").length;

  return {
    aconteceu: lim(aconteceu),
    mudou: lim(mudou),
    atrasado: lim(diag.atrasado),
    fazerHoje: (Array.isArray(dq.top3) ? dq.top3 : []).map((x) => _frase(x.titulo)),
    podeEsperar: baixas > 0 ? [_frase(`${baixas} ação(ões) de prioridade baixa podem esperar.`)] : [],
    maiorRetorno: dq.proximaMelhorAcao ? _frase(dq.proximaMelhorAcao.titulo) : "—",
  };
}

// ── Camada 4 — Projeções (esqueleto com fórmulas simples) ───────────────────
function buildProjectionSkeleton(brainState) {
  const st = _obj(brainState) || {};
  const com = _obj(st.comercial) || {};
  const fin = _obj(st.financeiro) || {};
  const pac = _obj(st.pacientes) || {};
  const mkt = _obj(st.marketing) || {};
  const r2 = (n) => Math.round(n * 100) / 100;

  const proj = (id, label, valorBase, projecao30d, premissa, status) => ({
    id, label,
    status: status || (valorBase === null ? "esqueleto" : "parcial"),
    valorBase: valorBase === null ? null : r2(valorBase),
    projecao30d: projecao30d === null ? null : r2(projecao30d),
    premissa: _frase(premissa),
  });

  const oport = com.oportunidades;
  const receita = fin.receitaMes;
  const custo = fin.custoMensal;
  const gargalos = (com.semProximoPasso ?? 0) + (com.precisamFollowup ?? 0) + (pac.followupsAtrasados ?? 0);

  return [
    proj("comercial_b2b", "Comercial B2B",
      oport ?? null, oport != null ? oport * 0.2 : null,
      "20% de conversão demo sobre oportunidades ativas — trocar por taxa real no M11."),
    proj("exames", "Exames",
      null, null, "Sem série histórica exposta ainda — ativa no M11 com produção real."),
    proj("receita", "Receita",
      receita ?? null, receita != null ? receita : null,
      "Repete a receita do mês corrente — cenários chegam no M12."),
    proj("custos", "Custos",
      custo ?? null, custo != null ? custo : null,
      "Custo mensal recorrente atual — ponto de equilíbrio no M12."),
    proj("gargalos", "Gargalos",
      st.fontes ? gargalos : null, null,
      "Soma de itens sem próximo passo + follow-ups vencidos."),
    proj("marketing_seo", "Marketing/SEO",
      null, null,
      mkt.searchConsoleOk === true
        ? "Integrações medindo — projeção de tráfego no M11+."
        : "Sem dados suficientes das integrações."),
  ];
}

if (typeof module === "object" && module.exports) {
  module.exports = {
    buildOperationalBrainState, buildPriorityScore, buildDailyBriefing,
    buildProjectionSkeleton, buildDecisionQueue,
    BRAIN_QUEUE_MAX, BRAIN_TOP, BRAIN_PESOS,
  };
}
