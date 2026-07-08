// SoproLife — M5 Próximas Ações Comerciais B2B/PCMSO.
// Funções PURAS que transformam summaries seguros (leads, CRM clínicas,
// contatos B2B, follow-up de clínicas) em resumo + ações comerciais sem PII.
// Sem DOM, sem fetch, sem dependência externa. Usadas pelo app.js (script
// clássico) e pelos testes locais em Node (module.exports).
//
// Reusa o sanitizador do M4 (acoesTextoSeguro): no navegador via global
// (operational-actions.js carrega antes); em Node via require local.

/* eslint-disable no-undef */
const _b2bSanitizar = (typeof acoesTextoSeguro === "function")
  ? acoesTextoSeguro
  : require("./operational-actions.js").acoesTextoSeguro;

// Vocabulário canônico de etapas (duplicado de propósito, como no resto do
// projeto — cada módulo é instalável sozinho). Etapa é a única fonte de
// verdade da fase comercial (skill soprolife-b2b-pcmso-crm).
const B2B_CRM_ATIVAS = ["Não abordada", "Abordada", "Em conversa",
  "Pediu apresentação", "Aguardando retorno", "Proposta enviada"];
const B2B_CRM_PARCEIRO = "Parceiro ativo";
const B2B_CRM_PERDIDAS = ["Sem interesse", "Não contatar / bloqueou",
  "Sem canal válido", "Arquivada"];

const B2B_LEAD_SERVICOS = ["clínicas", "clinicas", "pcmso / empresa", "pcmso/empresa"];
const B2B_LEAD_CONVERTIDO = ["convertido em clínica/parceiro", "parceiro ativo"];
const B2B_LEAD_PERDIDO = ["desistiu", "perdido", "sem resposta", "não respondeu", "nao respondeu"];

const B2B_MAX_ACOES = 10;

function b2bParseData(valor) {
  if (!valor) return null;
  const texto = String(valor).trim();
  let m = texto.match(/^(\d{2})\/(\d{2})\/(\d{4})/);
  if (m) return new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]));
  m = texto.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return null;
}

function b2bDiasAtraso(valor, agora) {
  const d = b2bParseData(valor);
  if (!d) return null;
  return Math.floor((agora - d) / 86400000);
}

function b2bEhLeadB2B(lead) {
  const servico = String(lead.servico_interesse || "").toLowerCase().trim();
  if (B2B_LEAD_SERVICOS.includes(servico)) return true;
  return String(lead.tipo_lead || "").toLowerCase().includes("b2b");
}

// payloads = { leads: [...], clinicas: [...], contatosB2B: [...],
//              followupStats: {atrasados,hoje,semData,parceirosAtivos,perdidasArquivadas,...} | null }
// agora: Date injetável (testes determinísticos). Tudo opcional/nulável.
function buildB2BStats(payloads, agora) {
  const p = payloads && typeof payloads === "object" ? payloads : {};
  const leads = Array.isArray(p.leads) ? p.leads.filter((l) => l && typeof l === "object") : [];
  const clinicas = Array.isArray(p.clinicas) ? p.clinicas.filter((c) => c && typeof c === "object") : [];
  const fu = p.followupStats && typeof p.followupStats === "object" ? p.followupStats : null;

  const leadsB2B = leads.filter(b2bEhLeadB2B);
  const etapaLead = (l) => String(l.etapa || "").toLowerCase().trim();
  const leadsConvertidos = leadsB2B.filter((l) => B2B_LEAD_CONVERTIDO.includes(etapaLead(l)));
  const leadsPerdidos = leadsB2B.filter((l) => B2B_LEAD_PERDIDO.includes(etapaLead(l)));
  const leadsAtivos = leadsB2B.filter((l) =>
    !B2B_LEAD_CONVERTIDO.includes(etapaLead(l)) && !B2B_LEAD_PERDIDO.includes(etapaLead(l)));

  const etapaClin = (c) => String(c.etapa || "").trim();
  const clinAtivas = clinicas.filter((c) => B2B_CRM_ATIVAS.includes(etapaClin(c)));
  const parceiros = clinicas.filter((c) => etapaClin(c) === B2B_CRM_PARCEIRO);
  const clinPerdidas = clinicas.filter((c) => B2B_CRM_PERDIDAS.includes(etapaClin(c)));

  const temProx = (c) => c.tem_proxima_acao === true || Boolean(c.proximaAcao || c.proxima_acao);

  return {
    // Dedup (regra do funil): lead convertido conta pela clínica, nunca duas vezes.
    totalOportunidades: clinAtivas.length + leadsAtivos.length,
    precisamFollowup: fu ? (Number(fu.atrasados || 0) + Number(fu.hoje || 0)) : null,
    emConversa: clinAtivas.filter((c) => etapaClin(c) === "Em conversa").length +
                leadsAtivos.filter((l) => etapaLead(l) === "em conversa").length,
    aguardandoRetorno: clinAtivas.filter((c) => etapaClin(c) === "Aguardando retorno").length +
                       leadsAtivos.filter((l) => etapaLead(l) === "aguardando retorno").length,
    convertidas: parceiros.length + leadsConvertidos.length,
    semProximoPasso: clinAtivas.filter((c) => !temProx(c)).length +
                     leadsAtivos.filter((l) => !temProx(l)).length,
    perdidas: clinPerdidas.length + leadsPerdidos.length +
              (fu && fu.perdidasArquivadas !== undefined && clinicas.length === 0
                ? Number(fu.perdidasArquivadas || 0) : 0),
  };
}

function buildB2BActions(payloads, agora) {
  const p = payloads && typeof payloads === "object" ? payloads : {};
  const hoje = agora instanceof Date ? agora : new Date();
  const leads = Array.isArray(p.leads) ? p.leads.filter((l) => l && typeof l === "object") : [];
  const clinicas = Array.isArray(p.clinicas) ? p.clinicas.filter((c) => c && typeof c === "object") : [];
  const contatos = Array.isArray(p.contatosB2B) ? p.contatosB2B.filter((c) => c && typeof c === "object") : [];
  const fu = p.followupStats && typeof p.followupStats === "object" ? p.followupStats : null;

  const acoes = [];
  const push = (id, prioridade, origem, titulo, motivo, proximoPasso) => acoes.push({
    id: _b2bSanitizar(id, `B2B-${acoes.length + 1}`),
    prioridade,
    origem,
    titulo: _b2bSanitizar(titulo, "Oportunidade B2B"),
    motivo: _b2bSanitizar(motivo, "Ver detalhes no CRM."),
    proximoPasso: _b2bSanitizar(proximoPasso, "Abrir CRM → Clínicas e Parceiros."),
  });

  // ── Agregadas do follow-up (contagens seguras) ─────────────────────────
  if (fu && Number(fu.atrasados || 0) > 0) {
    push("B2B-FU-ATRASADOS", "alta", "Follow-up Clínicas",
      "Follow-ups B2B atrasados",
      `${Number(fu.atrasados)} clínica(s)/empresa(s) com retorno vencido`,
      "Abrir CRM → Clínicas e Parceiros e contatar os atrasados primeiro.");
  }
  if (fu && Number(fu.hoje || 0) > 0) {
    push("B2B-FU-HOJE", "alta", "Follow-up Clínicas",
      "Follow-ups marcados para hoje",
      `${Number(fu.hoje)} contato(s) previsto(s) para hoje`,
      "Fazer os contatos de hoje antes que virem atraso.");
  }

  // ── Por clínica (nome institucional já permitido no summary seguro) ────
  const etapaClin = (c) => String(c.etapa || "").trim();
  const nomeClin = (c) => _b2bSanitizar(c.nome_clinica || c.clinica, "Clínica");
  const temProxC = (c) => c.tem_proxima_acao === true || Boolean(c.proximaAcao || c.proxima_acao);
  clinicas.forEach((c) => {
    const etapa = etapaClin(c);
    const nome = nomeClin(c);
    const prioAlta = String(c.prioridade || "").toLowerCase() === "alta";
    if (etapa === "Proposta enviada") {
      push(`B2B-PROP-${c.clinica_id || c.id || nome}`, "alta", "CRM B2B",
        `Cobrar retorno da proposta — ${nome}`,
        "Proposta enviada sem resposta registrada",
        "Ligar/mandar mensagem cobrando a proposta com gentileza.");
    } else if (etapa === "Pediu apresentação") {
      push(`B2B-APRES-${c.clinica_id || c.id || nome}`, "alta", "CRM B2B",
        `Enviar apresentação — ${nome}`,
        "A clínica pediu apresentação e está esperando",
        "Enviar a apresentação padrão e registrar a data do envio.");
    } else if (etapa === "Em conversa") {
      push(`B2B-CONV-${c.clinica_id || c.id || nome}`, prioAlta ? "alta" : "media", "CRM B2B",
        `Manter conversa aquecida — ${nome}`,
        "Negociação em andamento",
        "Confirmar próximo contato e registrar a data no CRM.");
    } else if (etapa === "Aguardando retorno") {
      push(`B2B-AGRET-${c.clinica_id || c.id || nome}`, prioAlta ? "alta" : "media", "CRM B2B",
        `Reforçar contato — ${nome}`,
        "Aguardando retorno da clínica",
        "Se o prazo combinado passou, fazer um follow-up educado.");
    } else if (B2B_CRM_ATIVAS.includes(etapa) && !temProxC(c)) {
      push(`B2B-SEMPASSO-${c.clinica_id || c.id || nome}`, "media", "CRM B2B",
        `Definir próximo passo — ${nome}`,
        "Oportunidade ativa sem próxima ação registrada",
        "Abrir a planilha e registrar a próxima ação e a data.");
    } else if (etapa === B2B_CRM_PARCEIRO && !temProxC(c)) {
      push(`B2B-PARC-${c.clinica_id || c.id || nome}`, "baixa", "CRM B2B",
        `Alinhar rotina com o parceiro — ${nome}`,
        "Parceria ativa sem próxima ação registrada",
        "Combinar a rotina operacional (agenda, volume, contato).");
    } else if (etapa && !B2B_CRM_ATIVAS.includes(etapa) &&
               etapa !== B2B_CRM_PARCEIRO && !B2B_CRM_PERDIDAS.includes(etapa)) {
      // Etapa fora do vocabulário oficial: fica INVISÍVEL nas contagens do
      // funil — denunciar em vez de ignorar (qualidade de dado).
      push(`B2B-ETAPA-${c.clinica_id || c.id || nome}`, "media", "CRM B2B",
        `Revisar etapa no CRM — ${nome}`,
        `Etapa "${etapa}" fora do padrão oficial (não entra nas contagens)`,
        "Corrigir a etapa na planilha para um dos valores oficiais do dropdown.");
    }
  });

  // ── Leads B2B (só IDs — o resumo seguro não tem nome de pessoa) ────────
  const etapaLead = (l) => String(l.etapa || "").toLowerCase().trim();
  leads.filter(b2bEhLeadB2B).forEach((l) => {
    const etapa = etapaLead(l);
    if (B2B_LEAD_CONVERTIDO.includes(etapa) || B2B_LEAD_PERDIDO.includes(etapa)) return;
    const atraso = b2bDiasAtraso(l.data_proxima_acao, hoje);
    const id = _b2bSanitizar(l.lead_id, "lead B2B");
    if (atraso !== null && atraso > 0) {
      push(`B2B-LEAD-ATRASO-${id}`, "alta", "Lead",
        `Retomar lead B2B ${id}`,
        `Próxima ação venceu há ${atraso} dia(s)`,
        "Reabrir a conversa hoje — lead esfriando.");
    } else if (l.tem_proxima_acao !== true) {
      push(`B2B-LEAD-SEMPASSO-${id}`, "media", "Lead",
        `Definir próximo passo do lead ${id}`,
        "Lead B2B ativo sem próxima ação registrada",
        "Registrar a próxima ação e a data na aba Leads.");
    }
  });

  // ── Contatos B2B sem próximo passo (agregada) ──────────────────────────
  const contatosSemPasso = contatos.filter((c) => c.tem_proxima_acao !== true).length;
  if (contatosSemPasso > 0) {
    push("B2B-CONTATOS-SEMPASSO", "media", "CRM B2B",
      "Contatos B2B sem próximo passo",
      `${contatosSemPasso} contato(s) vinculados sem ação registrada`,
      "Revisar a aba CRM Contatos B2B e registrar as próximas ações.");
  }

  // Ordena por prioridade e limita (área densa e operacional).
  const peso = { alta: 0, media: 1, baixa: 2 };
  acoes.sort((a, b) => (peso[a.prioridade] ?? 3) - (peso[b.prioridade] ?? 3));
  return acoes.slice(0, B2B_MAX_ACOES);
}

if (typeof module === "object" && module.exports) {
  module.exports = { buildB2BActions, buildB2BStats, b2bParseData, b2bDiasAtraso, B2B_MAX_ACOES };
}
