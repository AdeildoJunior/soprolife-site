// SoproLife Command Center — Apps Script Web App API
// Versão: 1.0  |  Fase 3 do Centro de Comando
//
// INSTALAÇÃO:
//   1. Abra a planilha Google Sheets da SoproLife.
//   2. Extensões → Apps Script → cole este arquivo no editor.
//   3. Arquivo → Propriedades do projeto → Propriedades do script:
//      Adicione a propriedade  API_TOKEN  com um valor secreto de sua escolha.
//      Ex.: API_TOKEN = aX9mQ2rT8uV5wZ3n  (gere algo aleatório, min. 16 chars)
//   4. Publicar → Implantar como app da web:
//      Executar como: Eu (minha conta)
//      Quem pode acessar: Qualquer pessoa
//   5. Copie a URL gerada e salve em:
//      painel-soprolife/data-private/command-center-config.local.json
//
// SEGURANÇA:
//   - O token só existe nas Propriedades do Script (nunca no código).
//   - Toda requisição exige o token no corpo do POST (não na URL).
//   - Telefone e observação privada NUNCA são registrados no log.
//   - O log registra apenas: timestamp, ação, status, aba, id, e-mail do executor, resumo.

// ── Constantes ────────────────────────────────────────────────────────────────

var _SHEETS = {
  LEADS:             "Leads",
  // Abas técnicas já criadas por converter-lead-em-paciente.gs (ocultas na planilha).
  // Reaproveitadas aqui, não recriadas — mesmos nomes exatos de _CV_ABA_CONVERTIDOS / _CV_ABA_LOG.
  LEADS_CONVERTIDOS: "Leads Convertidos",
  LOG_CONVERSOES:    "Log Conversões Leads",
  PACIENTES:         "CRM Pacientes",
  ESPIROMETRIA:      "CRM Espirometria",
  CONSULTAS:         "CRM Consultas",
  B2B_PCMSO:         "Base Prospecção B2B PCMSO",
  CRM_CLINICAS:      "CRM Clinicas",
  CRM_CONTATOS_B2B:  "CRM Contatos B2B",
  FOLLOWUP_WA:       "Follow-up WhatsApp",
  LOG:               "Log Centro Comando",
  // Parceria Pastore — ver painel-soprolife/docs/parceria-pastore-planilha.md
  PARCERIA_PASTORE_ATENDIMENTOS: "Parceria Pastore - Atendimentos",
};

// Cabeçalho canônico da aba "Parceria Pastore - Atendimentos" — mesma ordem
// de painel-soprolife/templates/parceria-pastore-atendimentos-template.csv.
// Colunas S/T/U (receita_bruta, custo_total, resultado_liquido) são
// calculadas por FÓRMULA na planilha real, nunca escritas por esta ação —
// ver _registrarAtendimentoPastore e _ensureFormulaColumns.
var _PARCERIA_PASTORE_ATENDIMENTOS_CABECALHO = [
  "data_atendimento", "unidade", "dia_semana", "horario_inicio", "horario_fim",
  "origem", "paciente_nome", "paciente_whatsapp", "tipo_exame", "broncodilatador",
  "valor_cobrado", "forma_pagamento", "recebido_por", "repasse_pastore",
  "custo_insumo", "custo_deslocamento", "custo_profissional", "outros_custos",
  "receita_bruta", "custo_total", "resultado_liquido", "status", "followup_status",
  "consentimento_contato_futuro", "observacao_privada_minima",
];

// S=19, T=20, U=21 — ver cabeçalho acima.
var _PARCERIA_PASTORE_FORMULA_COLS = [19, 20, 21];

// Etapas que disparam a conversão automática do lead para os CRMs de
// atendimento (mesma lista usada pelo gatilho onEdit em converter-lead-em-paciente.gs).
var _LEAD_ETAPAS_CONVERSAO = [
  "Realizou consulta",
  "Realizou espirometria",
  "Realizou consulta e espirometria",
];

// Etapas oficiais aceitas pelo painel e pelo dropdown da aba Leads.
// A função updateLeadStage garante que a célula permita a etapa antes de gravar.
var _LEAD_ETAPAS_OFICIAIS = [
  "Novo contato",
  "Em conversa",
  "Aguardando retorno",
  "Agendado",
  "Realizou consulta",
  "Realizou espirometria",
  "Realizou consulta e espirometria",
  "Concluído",
  "Não respondeu",
  "Desistiu",
  "Perdido",
  // Etapas B2B / parcerias (leads de clínica, PCMSO, empresas) — ver vincularLeadAClinica.
  "Reunião marcada",
  "Proposta enviada",
  "Parceria fechada",
  "Parceiro ativo",
  "Convertido em clínica/parceiro",
  "Sem resposta",
];

// Etapas B2B terminais aceitas por vincularLeadAClinica — marcam o lead como
// convertido/arquivado e vinculado a uma entidade em CRM Clínicas.
// Propositalmente separadas de _LEAD_ETAPAS_CONVERSAO (fluxo de paciente,
// que aciona _cvConverterLeadCore) para não disparar a conversão em CRM Pacientes.
var _LEAD_ETAPAS_B2B_TERMINAL = [
  "Convertido em clínica/parceiro",
  "Parceiro ativo",
];

// Etapas oficiais do CRM Clínicas / Base Prospecção B2B PCMSO — mesmo
// vocabulário canônico usado em app.js (CRM_ETAPAS_ATIVAS + CRM_ETAPAS_TERMINAIS)
// e nos scripts Python (generate-followup-clinicas.py, promote-pcmso-to-crm.py).
// Duplicado de propósito: cada arquivo é instalado/editado independentemente.
// updateCrmClinicaEtapa recusa qualquer valor fora desta lista.
var _CRM_ETAPAS_OFICIAIS = [
  "Não abordada",
  "Abordada",
  "Em conversa",
  "Pediu apresentação",
  "Aguardando retorno",
  "Proposta enviada",
  "Parceiro ativo",
  "Sem interesse",
  "Não contatar / bloqueou",
  "Sem canal válido",
  "Arquivada",
];

// Colunas novas de suporte ao modelo B2B na aba Leads — criadas sob demanda
// (a primeira vez que vincularLeadAClinica rodar) sem remover/reordenar as
// colunas antigas: lead_id, data_contato, nome, telefone_whatsapp,
// servico_interesse, origem, etapa, responsavel, proxima_acao,
// data_proxima_acao, observacao.
var _LEAD_COLUNAS_EXTRA_B2B = [
  "tipo_lead",
  "entidade_destino",
  "entidade_id",
  "data_conversao",
  "status_operacional",
];

// Cabeçalho da aba "CRM Contatos B2B" — nova aba, criada apenas quando um
// contato é vinculado pela primeira vez (vincularLeadAClinica → _upsertContatoB2B).
var _CRM_CONTATOS_B2B_CABECALHO = [
  "contato_id",
  "clinica_id",
  "nome_contato",
  "cargo",
  "telefone_whatsapp",
  "email",
  "papel",
  "status_relacionamento",
  "ultima_interacao",
  "proxima_acao",
  "data_proxima_acao",
  "responsavel",
  "observacao",
];

// Cabeçalhos de fallback para "Leads Convertidos" e "Log Conversões Leads" —
// usados apenas se essas abas ainda não existirem (não deveria acontecer, pois
// já são criadas por converter-lead-em-paciente.gs). Espelham exatamente
// _CV_LEADS_CABECALHO e _CV_HEADERS_LOG daquele arquivo.
var _B2B_LEADS_CONVERTIDOS_CABECALHO_FALLBACK = [
  "lead_id", "data_contato", "nome", "telefone_whatsapp", "servico_interesse",
  "origem", "etapa", "responsavel", "proxima_acao", "data_proxima_acao", "observacao",
];
var _B2B_LOG_CONVERSOES_CABECALHO_FALLBACK = [
  "data_hora", "lead_id", "nome", "etapa", "destinos_criados", "resultado", "followup_status",
];

// ── Organização visual da planilha (organizarAbasCommandCenter) ───────────────

// Abas operacionais — devem ficar visíveis, com cabeçalho destacado, linha 1
// congelada e filtro básico. Nomes literais (não fazem parte de _SHEETS
// porque não são lidas/escritas por este arquivo, só organizadas visualmente).
var _ABAS_OPERACIONAIS = [
  _SHEETS.LEADS,
  _SHEETS.CRM_CLINICAS,
  _SHEETS.CRM_CONTATOS_B2B,
  _SHEETS.PACIENTES,
  _SHEETS.CONSULTAS,
  _SHEETS.ESPIROMETRIA,
  _SHEETS.FOLLOWUP_WA,
  "Tarefas",
  "Financeiro",
  "Marketing Conteúdo",
];

// Abas técnicas — podem ficar ocultas depois de estáveis (auditoria/consolidação,
// não uso operacional do dia a dia). organizarAbasCommandCenter só oculta se
// dryRun=false, e sempre relata a ação no retorno.
var _ABAS_TECNICAS_OCULTAVEIS = [
  _SHEETS.LOG_CONVERSOES,
  _SHEETS.LEADS_CONVERTIDOS,
  "Resumo Dashboard",
];

// ── Entry point ───────────────────────────────────────────────────────────────

function doPost(e) {
  try {
    var token = PropertiesService.getScriptProperties().getProperty("API_TOKEN");
    if (!token) {
      return _err("API_TOKEN não configurado nas Propriedades do Script.", 500);
    }

    var body;
    try {
      body = JSON.parse(e.postData.contents);
    } catch (_) {
      return _err("Corpo da requisição inválido — JSON malformado.", 400);
    }

    if (!body.token || body.token !== token) {
      _logEntry({ acao: "autenticacao", status: "NEGADO", aba: "", id: "", resumo: "Token inválido." });
      return _err("Token inválido.", 401);
    }

    var action = String(body.action || "").trim();
    var data   = body.data || {};

    switch (action) {
      case "createLead":                  return _createLead(data);
      case "createPaciente":              return _createPaciente(data);
      case "createEspirometria":          return _createEspirometria(data);
      case "createConsulta":              return _createConsulta(data);
      case "createClinicaB2B":            return _createClinicaB2B(data);
      case "registrarInteracaoClinica":   return _registrarInteracaoClinica(data);
      case "registrarInteracaoPaciente":  return _registrarInteracaoPaciente(data);
      case "updateLeadStage":             return _updateLeadStage(data);
      case "updateCrmClinicaEtapa":       return _updateCrmClinicaEtapa(data);
      case "vincularLeadAClinica":        return _vincularLeadAClinica(data);
      case "dedupLeadsConvertidos":       return _dedupLeadsConvertidos(data);
      case "organizarAbasCommandCenter":  return _organizarAbasCommandCenter(data);
      case "registrarAtendimentoPastore": return _registrarAtendimentoPastore(data);
      default:
        return _err("Ação desconhecida: " + action, 400);
    }
  } catch (ex) {
    _logEntry({ acao: "erro-interno", status: "ERRO", aba: "", id: "", resumo: ex.message });
    return _err("Erro interno: " + ex.message, 500);
  }
}

// ── Ações de escrita ──────────────────────────────────────────────────────────

function _createLead(data) {
  _required(data, ["nome", "responsavel"]);

  var sheet = _getOrCreateSheet(_SHEETS.LEADS);
  var id    = _nextId(sheet, "LEAD");

  _appendRowSemValidacao(sheet, _buildRow(sheet, {
    lead_id:           id,
    data_contato:      _nowBr(),
    nome:              data.nome              || "",
    telefone_whatsapp: data.telefone_whatsapp || "",
    servico_interesse: data.servico_interesse || "",
    origem:            data.origem            || "",
    etapa:             data.etapa             || "Novo contato",
    responsavel:       data.responsavel       || "",
    proxima_acao:      data.proxima_acao      || "",
    data_proxima_acao: data.data_proxima_acao || "",
    observacao:        data.observacao        || "",
  }));

  // telefone_whatsapp e observacao nunca são registrados no log
  _logEntry({ acao: "createLead", status: "OK", aba: _SHEETS.LEADS, id: id, resumo: "Lead registrado." });
  return _ok({ id: id, message: "Lead registrado com sucesso." });
}

function _createPaciente(data) {
  _required(data, ["primeiro_nome", "responsavel"]);

  var sheet   = _getOrCreateSheet(_SHEETS.PACIENTES);
  var id      = _nextId(sheet, "PAC");
  var consent = _normalizeConsent(data.consentimento_whatsapp);

  _appendRowSemValidacao(sheet, _buildRow(sheet, {
    paciente_id:            id,
    data_cadastro:          _nowBr(),
    primeiro_nome:          data.primeiro_nome              || "",
    telefone:               data.telefone                   || "",
    ultimo_servico:         data.origem                     || "",
    status_relacionamento:  "Ativo",
    proximo_contato:        data.proximo_contato            || "",
    motivo_proximo_contato: "",
    canal:                  data.canal                      || "",
    responsavel:            data.responsavel                || "",
    consentimento_whatsapp: consent,
  }));

  _logEntry({ acao: "createPaciente", status: "OK", aba: _SHEETS.PACIENTES, id: id, resumo: "Paciente cadastrado." });
  return _ok({ id: id, message: "Paciente criado com sucesso." });
}

function _createEspirometria(data) {
  _required(data, ["primeiro_nome", "responsavel"]);

  var sheet   = _getOrCreateSheet(_SHEETS.ESPIROMETRIA);
  var id      = _nextId(sheet, "ESP");
  var consent = _normalizeConsent(data.consentimento_whatsapp);

  _appendRowSemValidacao(sheet, _buildRow(sheet, {
    exame_id:               id,
    data_entrada:           _nowBr(),
    primeiro_nome:          data.primeiro_nome              || "",
    telefone:               data.telefone                   || "",
    servico:                data.servico                    || "Espirometria",
    origem:                 data.origem                     || "",
    status_exame:           data.status_exame               || "Aguardando",
    data_exame:             data.data_exame                 || "",
    proximo_contato:        data.proximo_contato            || "",
    motivo_proximo_contato: data.motivo_proximo_contato     || "",
    canal:                  data.canal                      || "",
    responsavel:            data.responsavel                || "",
    consentimento_whatsapp: consent,
  }));

  _logEntry({ acao: "createEspirometria", status: "OK", aba: _SHEETS.ESPIROMETRIA, id: id, resumo: "Espirometria registrada." });
  return _ok({ id: id, message: "Espirometria registrada com sucesso." });
}

function _createConsulta(data) {
  _required(data, ["primeiro_nome", "responsavel"]);

  var sheet   = _getOrCreateSheet(_SHEETS.CONSULTAS);
  var id      = _nextId(sheet, "CON");
  var consent = _normalizeConsent(data.consentimento_whatsapp);

  _appendRowSemValidacao(sheet, _buildRow(sheet, {
    consulta_id:            id,
    data_entrada:           _nowBr(),
    primeiro_nome:          data.primeiro_nome              || "",
    telefone:               data.telefone                   || "",
    origem:                 data.origem                     || "",
    tipo_consulta:          data.tipo_consulta              || "",
    status:                 data.status                     || "Agendada",
    medica:                 data.medica                     || "",
    data_consulta:          data.data_consulta              || "",
    proximo_contato:        data.proximo_contato            || "",
    motivo_proximo_contato: data.motivo_proximo_contato     || "",
    canal:                  data.canal                      || "",
    responsavel:            data.responsavel                || "",
    consentimento_whatsapp: consent,
  }));

  _logEntry({ acao: "createConsulta", status: "OK", aba: _SHEETS.CONSULTAS, id: id, resumo: "Consulta registrada." });
  return _ok({ id: id, message: "Consulta registrada com sucesso." });
}

function _createClinicaB2B(data) {
  _required(data, ["nome_clinica", "status"]);

  // Escreve na aba PCMSO (prospecção)
  var pcmsoSheet = _getOrCreateSheet(_SHEETS.B2B_PCMSO);
  _appendRowSemValidacao(pcmsoSheet, _buildRow(pcmsoSheet, {
    "Nome da clínica/empresa": data.nome_clinica       || "",
    "Tipo":                    data.tipo_clinica        || "",
    "Bairro/Região":           _joinNotEmpty(data.bairro, data.regiao),
    "Telefone/WhatsApp":       data.telefone_whatsapp   || "",
    "Pessoa de contato":       "",
    "Origem da lista":         data.origem              || "",
    "Status":                  data.status              || "",
    "Interesse":               data.interesse           || "",
    "Próximo passo":           data.proximo_passo       || "",
    "Data do próximo passo":   data.data_proximo_passo  || "",
    "Observações":             "",
  }));

  // Escreve também no CRM Clinicas
  var crmSheet = _getOrCreateSheet(_SHEETS.CRM_CLINICAS);
  var crmId    = _nextId(crmSheet, "CLI");
  _appendRowSemValidacao(crmSheet, _buildRow(crmSheet, {
    clinica_id:        crmId,
    nome_clinica:      data.nome_clinica      || "",
    bairro:            data.bairro            || "",
    regiao:            data.regiao            || "",
    tipo_clinica:      data.tipo_clinica      || "",
    etapa:             data.status            || "Não abordado",
    ultima_interacao:  _nowBr(),
    proxima_acao:      data.proximo_passo     || "",
    data_proxima_acao: data.data_proximo_passo || "",
    responsavel:       data.responsavel       || "",
    prioridade:        data.prioridade        || "Normal",
    observacao:        "",
  }));

  _logEntry({ acao: "createClinicaB2B", status: "OK", aba: _SHEETS.B2B_PCMSO + " + " + _SHEETS.CRM_CLINICAS, id: crmId, resumo: "Clínica B2B cadastrada." });
  return _ok({ id: crmId, message: "Clínica cadastrada com sucesso." });
}

function _registrarInteracaoClinica(data) {
  _required(data, ["nome_clinica", "etapa", "responsavel"]);

  var sheet    = _getOrCreateSheet(_SHEETS.CRM_CLINICAS);
  var allData  = sheet.getDataRange().getValues();
  if (allData.length < 1) throw new Error("Aba " + _SHEETS.CRM_CLINICAS + " sem cabeçalho.");

  var headers  = allData[0].map(function(h) { return String(h).toLowerCase().trim(); });
  var nomeIdx  = headers.indexOf("nome_clinica");
  if (nomeIdx < 0) throw new Error("Coluna nome_clinica não encontrada.");

  var keyTarget = _keyNorm(data.nome_clinica);
  var rowIdx    = -1;
  for (var i = 1; i < allData.length; i++) {
    if (_keyNorm(String(allData[i][nomeIdx] || "")) === keyTarget) {
      rowIdx = i + 1; // índice 1-based do Sheets
      break;
    }
  }

  var updates = {
    etapa:             data.etapa              || "",
    ultima_interacao:  _nowBr(),
    proxima_acao:      data.proxima_acao        || "",
    data_proxima_acao: data.data_proxima_acao   || "",
    prioridade:        data.prioridade          || "",
    responsavel:       data.responsavel         || "",
  };

  if (rowIdx > 0) {
    var updateFailures = [];
    Object.keys(updates).forEach(function(col) {
      var colIdx = headers.indexOf(col);
      if (colIdx < 0) return;
      if (!_writeCellVerified(sheet, rowIdx, colIdx + 1, updates[col])) updateFailures.push(col);
    });
    if (updateFailures.length > 0) {
      _logEntry({ acao: "registrarInteracaoClinica", status: "ERRO-GRAVACAO", aba: _SHEETS.CRM_CLINICAS, id: "", resumo: "Falha ao gravar: " + updateFailures.join(", ") });
      return _err("Falha ao gravar campos: " + updateFailures.join(", "), 500);
    }
    _logEntry({ acao: "registrarInteracaoClinica", status: "OK", aba: _SHEETS.CRM_CLINICAS, id: "", resumo: "Interação atualizada." });
    return _ok({ message: "Interação registrada com sucesso.", updated: true });
  }

  // Clínica não encontrada: cria nova linha
  var newId = _nextId(sheet, "CLI");
  _appendRowSemValidacao(sheet, _buildRow(sheet, Object.assign({
    clinica_id:   newId,
    nome_clinica: data.nome_clinica || "",
    bairro: "", regiao: "", tipo_clinica: "", observacao: "",
  }, updates)));
  _logEntry({ acao: "registrarInteracaoClinica", status: "OK-NOVO", aba: _SHEETS.CRM_CLINICAS, id: newId, resumo: "Nova clínica criada via interação." });
  return _ok({ id: newId, message: "Clínica não encontrada — nova entrada criada.", created: true });
}

function _registrarInteracaoPaciente(data) {
  _required(data, ["primeiro_nome", "tipo_mensagem", "responsavel"]);

  var sheet   = _getOrCreateSheet(_SHEETS.FOLLOWUP_WA);
  var id      = _nextId(sheet, "FUP");
  var consent = _normalizeConsent(data.consentimento);

  _appendRowSemValidacao(sheet, _buildRow(sheet, {
    followup_id:    id,
    data_criacao:   _nowBr(),
    primeiro_nome:  data.primeiro_nome  || "",
    telefone:       data.telefone       || "",
    tipo_mensagem:  data.tipo_mensagem  || "",
    data_prevista:  data.data_prevista  || "",
    status:         "Pendente",
    canal:          data.canal          || "WhatsApp",
    responsavel:    data.responsavel    || "",
    template_usado: data.template_usado || "",
    consentimento:  consent,
  }));

  _logEntry({ acao: "registrarInteracaoPaciente", status: "OK", aba: _SHEETS.FOLLOWUP_WA, id: id, resumo: "Follow-up registrado." });
  return _ok({ id: id, message: "Interação registrada." });
}

/**
 * Registra um novo atendimento da Parceria Pastore direto na aba
 * "Parceria Pastore - Atendimentos", a partir do modal "Novo atendimento"
 * do painel (Parcerias → Pastore). Ver painel-soprolife/docs/parceria-pastore-planilha.md
 * para o modelo completo de campos e privacidade.
 *
 * Nome, WhatsApp e observação são privados por definição (nunca saem desta
 * planilha nem do arquivo data-private/parcerias-pastore.local.json — o
 * summary público do painel só lê agregados, nunca estes campos).
 *
 * receita_bruta / custo_total / resultado_liquido (colunas S/T/U) NÃO são
 * escritos aqui — a planilha real já calcula essas três colunas por fórmula
 * por linha (ver _ensureFormulaColumns). Deixar em branco e copiar a fórmula
 * da linha anterior é mais seguro do que tentar replicar o cálculo aqui e
 * divergir da fórmula real.
 */
function _registrarAtendimentoPastore(data) {
  _required(data, ["data_atendimento", "paciente_nome", "tipo_exame"]);

  var sheet = _getOrCreateSheet(_SHEETS.PARCERIA_PASTORE_ATENDIMENTOS);
  _ensureSheetHeader(sheet, _PARCERIA_PASTORE_ATENDIMENTOS_CABECALHO);

  var row = _buildRow(sheet, {
    data_atendimento:             data.data_atendimento             || "",
    unidade:                      data.unidade                      || "Pastore Ipanema",
    dia_semana:                   data.dia_semana                   || "",
    horario_inicio:               data.horario_inicio               || "",
    horario_fim:                  data.horario_fim                  || "",
    origem:                       data.origem                       || "Pastore",
    paciente_nome:                data.paciente_nome                || "",
    paciente_whatsapp:            data.paciente_whatsapp            || "",
    tipo_exame:                   data.tipo_exame                   || "",
    broncodilatador:              data.broncodilatador              || "Não",
    valor_cobrado:                data.valor_cobrado                || "",
    forma_pagamento:              data.forma_pagamento              || "",
    recebido_por:                 data.recebido_por                 || "SoproLife",
    repasse_pastore:              data.repasse_pastore !== undefined && data.repasse_pastore !== "" ? data.repasse_pastore : 0,
    custo_insumo:                 data.custo_insumo                 || "",
    custo_deslocamento:           data.custo_deslocamento !== undefined && data.custo_deslocamento !== "" ? data.custo_deslocamento : 0,
    custo_profissional:           data.custo_profissional !== undefined && data.custo_profissional !== "" ? data.custo_profissional : 0,
    outros_custos:                data.outros_custos !== undefined && data.outros_custos !== "" ? data.outros_custos : 0,
    status:                       data.status                       || "Realizado",
    followup_status:              data.followup_status              || "A definir",
    consentimento_contato_futuro: data.consentimento_contato_futuro || "A definir",
    // receita_bruta / custo_total / resultado_liquido: propositalmente ausentes
    // (ver docstring acima) — _buildRow grava "" nessas colunas, e
    // _ensureFormulaColumns substitui pela fórmula copiada da linha anterior.
  });

  // _buildRow() nunca escreve observacao_privada_minima (proteção padrão
  // aplicada a todas as abas). Aqui é intencional e seguro: o campo é privado
  // por definição nesta aba específica (nunca sai da planilha/data-private) e
  // o próprio formulário do painel pede essa observação.
  var headers = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 1)).getValues()[0]
    .map(function(h) { return String(h).trim(); });
  var obsIdx = headers.indexOf("observacao_privada_minima");
  if (obsIdx >= 0 && data.observacao_privada_minima) {
    row[obsIdx] = String(data.observacao_privada_minima);
  }

  var newRow = _appendRowSemValidacao(sheet, row);
  _ensureFormulaColumns(sheet, newRow, _PARCERIA_PASTORE_FORMULA_COLS);

  _logEntry({
    acao:   "registrarAtendimentoPastore",
    status: "OK",
    aba:    _SHEETS.PARCERIA_PASTORE_ATENDIMENTOS,
    id:     "linha " + newRow,
    resumo: "Atendimento Pastore registrado (" + (data.tipo_exame || "") + ", status " + (data.status || "Realizado") + ").",
    // nome, whatsapp e observação NUNCA entram no log — mesma regra do resto do arquivo
  });

  return _ok({ row: newRow, message: "Atendimento Pastore registrado com sucesso." });
}

/**
 * Atualiza a etapa de um lead já existente na aba Leads, identificado por lead_id.
 * Usado pelo botão "Mudar etapa" do painel (Leads e Agendamentos).
 *
 * Não cria uma linha nova se o lead_id não for encontrado — retorna erro,
 * já que uma atualização de etapa pressupõe um lead já cadastrado.
 *
 * Quando a nova etapa é uma das etapas de conversão ("Realizou consulta",
 * "Realizou espirometria", "Realizou consulta e espirometria"), reaproveita
 * o mesmo núcleo de conversão usado pelo gatilho onEdit manual
 * (_cvConverterLeadCore, definido em converter-lead-em-paciente.gs), para que
 * o resultado seja idêntico a editar a célula 'etapa' diretamente na planilha:
 * cria CRM Pacientes/Consultas/Espirometria e move o lead para 'Leads Convertidos'.
 * Se esse arquivo não estiver instalado no projeto, apenas atualiza a célula
 * de etapa (sem interromper a ação com erro).
 */
function _updateLeadStage(data) {
  _required(data, ["lead_id", "etapa"]);

  var novaEtapa = String(data.etapa).trim();
  var targetId  = String(data.lead_id).trim();

  var sheet = _getOrCreateSheet(_SHEETS.LEADS);
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  if (lastRow < 2 || lastCol < 1) {
    return _err("Nenhum lead cadastrado na aba " + _SHEETS.LEADS + ".", 404);
  }

  var allData = sheet.getRange(1, 1, lastRow, lastCol).getValues();
  var headers = allData[0].map(function(h) { return String(h).trim(); });

  var idIdx = headers.indexOf("lead_id");
  if (idIdx < 0) return _err("Coluna lead_id não encontrada na aba " + _SHEETS.LEADS + ".", 500);

  var etapaIdx = headers.indexOf("etapa");
  if (etapaIdx < 0) return _err("Coluna etapa não encontrada na aba " + _SHEETS.LEADS + ".", 500);

  var rowIdx = -1; // 1-based, índice de linha na planilha
  for (var i = 1; i < allData.length; i++) {
    if (String(allData[i][idIdx] || "").trim() === targetId) {
      rowIdx = i + 1;
      break;
    }
  }

  if (rowIdx < 0) {
    _logEntry({ acao: "updateLeadStage", status: "NAO-ENCONTRADO", aba: _SHEETS.LEADS, id: targetId, resumo: "Lead não encontrado para atualização de etapa." });
    return _err("Lead não encontrado: " + targetId, 404);
  }

  // Atualiza a etapa e verifica se a gravação realmente persistiu.
  // Alguns dropdowns/validações do Google Sheets podem impedir a alteração sem erro claro
  // (ex.: "Selecione uma opção da lista."). _writeEtapaVerified trata isso de forma segura.
  if (!_writeEtapaVerified(sheet, rowIdx, etapaIdx + 1, novaEtapa)) {
    var etapaLida = String(sheet.getRange(rowIdx, etapaIdx + 1).getDisplayValue() || "").trim();
    _logEntry({
      acao:   "updateLeadStage",
      status: "ERRO-GRAVACAO",
      aba:    _SHEETS.LEADS,
      id:     targetId,
      resumo: "Etapa desejada: " + novaEtapa + " | Etapa lida: " + etapaLida,
    });
    return _err("Falha ao gravar etapa. Etapa desejada: " + novaEtapa + " | Etapa lida: " + etapaLida, 500);
  }

  // Nota curta de histórico na coluna observacao, se existir — sem dados sensíveis
  var obsIdx = headers.indexOf("observacao");
  if (obsIdx >= 0) {
    var obsAtual = String(allData[rowIdx - 1][obsIdx] || "").trim();
    var nota     = "[" + _nowBr() + "] Etapa alterada para \"" + novaEtapa + "\" via painel.";
    var obsNovo  = obsAtual ? (obsAtual + " | " + nota) : nota;
    if (!_writeCellVerified(sheet, rowIdx, obsIdx + 1, obsNovo)) {
      _logEntry({ acao: "updateLeadStage", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS, id: targetId, resumo: "Falha ao gravar nota em observacao." });
      return _err("Etapa gravada, mas falha ao gravar nota em observacao.", 500);
    }
  }

  // Responsável pela mudança — só sobrescreve se enviado explicitamente
  var respIdx = headers.indexOf("responsavel");
  if (respIdx >= 0 && data.responsavel) {
    if (!_writeCellVerified(sheet, rowIdx, respIdx + 1, String(data.responsavel).trim())) {
      _logEntry({ acao: "updateLeadStage", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS, id: targetId, resumo: "Falha ao gravar responsavel." });
      return _err("Etapa gravada, mas falha ao gravar responsavel.", 500);
    }
  }

  var converted = false;
  if (_LEAD_ETAPAS_CONVERSAO.indexOf(novaEtapa) >= 0 && typeof _cvConverterLeadCore === "function") {
    var lock = LockService.getScriptLock();
    try {
      lock.waitLock(15000);
      _cvConverterLeadCore(_getWorkbook(), sheet, rowIdx, novaEtapa);
      converted = true;
    } catch (convErr) {
      _logEntry({ acao: "updateLeadStage", status: "ERRO-CONVERSAO", aba: _SHEETS.LEADS, id: targetId, resumo: convErr.message });
      return _err("Etapa atualizada, mas a conversão automática para CRM falhou: " + convErr.message, 500);
    } finally {
      lock.releaseLock();
    }
  }

  _logEntry({
    acao:   "updateLeadStage",
    status: "OK",
    aba:    _SHEETS.LEADS,
    id:     targetId,
    resumo: "Etapa alterada para " + novaEtapa + (converted ? " (convertido para CRM)" : ""),
  });

  return _ok({
    id: targetId,
    message: converted
      ? "Etapa atualizada e lead convertido para os CRMs de atendimento."
      : "Etapa atualizada com sucesso.",
    converted: converted,
  });
}

/**
 * Atualiza a etapa comercial de uma clínica/parceiro já existente em
 * "CRM Clinicas", identificada por clinica_id (nunca por telefone/nome
 * exposto no frontend — o painel já carrega clinica_id junto com cada linha
 * da tabela CRM Clínicas). Usado pelo botão "Mudar etapa" do painel
 * (CRM → Clínicas e Parceiros), espelhando updateLeadStage.
 *
 * Regra do funil (ver skill soprolife-b2b-pcmso-crm): Etapa é a única fonte
 * de verdade da fase comercial — Status/Próximo passo/Observação nunca
 * substituem a Etapa, e a cor da linha na planilha não é considerada aqui.
 *
 * Não cria uma linha nova se clinica_id não for encontrado (mesma regra de
 * updateLeadStage: atualização de etapa pressupõe uma clínica já cadastrada).
 * Nunca apaga nem move linhas.
 *
 * Efeito colateral best-effort: se a aba "Base Prospecção B2B PCMSO" existir
 * e tiver (ou puder ganhar, de forma idempotente) uma coluna "Etapa", e uma
 * linha com nome de clínica compatível for encontrada lá, a mesma etapa é
 * espelhada nessa linha também — mantendo a base de prospecção bruta
 * alinhada com o CRM Clínicas já promovido. Falha nesse espelhamento NUNCA
 * derruba a ação principal (a escrita em CRM Clinicas já terá sido
 * confirmada); o retorno informa se o espelhamento ocorreu.
 *
 * data esperado:
 *   clinica_id   (obrigatório) — CLIN-... / CLI-... já cadastrado em CRM Clinicas
 *   etapa        (obrigatório) — precisa estar em _CRM_ETAPAS_OFICIAIS
 *   responsavel  (opcional)    — só sobrescreve se enviado explicitamente
 */
function _updateCrmClinicaEtapa(data) {
  _required(data, ["clinica_id", "etapa"]);

  var novaEtapa = String(data.etapa).trim();
  var clinicaId = String(data.clinica_id).trim();

  if (_CRM_ETAPAS_OFICIAIS.indexOf(novaEtapa) < 0) {
    return _err("Etapa inválida: " + novaEtapa + ". Use uma das etapas oficiais.", 400);
  }

  var sheet = _getOrCreateSheet(_SHEETS.CRM_CLINICAS);
  var found = _findRowByIdColumn(sheet, "clinica_id", clinicaId);
  if (!found) {
    _logEntry({ acao: "updateCrmClinicaEtapa", status: "NAO-ENCONTRADO", aba: _SHEETS.CRM_CLINICAS, id: clinicaId, resumo: "Clínica não encontrada para atualização de etapa." });
    return _err("Clínica não encontrada: " + clinicaId, 404);
  }

  var headers  = found.headers;
  var rowIdx   = found.rowIdx;
  var etapaIdx = headers.indexOf("etapa");
  if (etapaIdx < 0) return _err("Coluna etapa não encontrada na aba " + _SHEETS.CRM_CLINICAS + ".", 500);

  if (!_writeEtapaVerified(sheet, rowIdx, etapaIdx + 1, novaEtapa, _CRM_ETAPAS_OFICIAIS)) {
    var etapaLida = String(sheet.getRange(rowIdx, etapaIdx + 1).getDisplayValue() || "").trim();
    _logEntry({
      acao:   "updateCrmClinicaEtapa",
      status: "ERRO-GRAVACAO",
      aba:    _SHEETS.CRM_CLINICAS,
      id:     clinicaId,
      resumo: "Etapa desejada: " + novaEtapa + " | Etapa lida: " + etapaLida,
    });
    return _err("Falha ao gravar etapa. Etapa desejada: " + novaEtapa + " | Etapa lida: " + etapaLida, 500);
  }

  // Nota curta de histórico em observacao, se a coluna existir — sem dados sensíveis.
  var obsIdx = headers.indexOf("observacao");
  if (obsIdx >= 0) {
    var obsAtual = String(found.rowData[obsIdx] || "").trim();
    var nota     = "[" + _nowBr() + "] Etapa alterada para \"" + novaEtapa + "\" via painel.";
    var obsNovo  = obsAtual ? (obsAtual + " | " + nota) : nota;
    if (!_writeCellVerified(sheet, rowIdx, obsIdx + 1, obsNovo)) {
      _logEntry({ acao: "updateCrmClinicaEtapa", status: "ERRO-GRAVACAO", aba: _SHEETS.CRM_CLINICAS, id: clinicaId, resumo: "Falha ao gravar nota em observacao." });
      return _err("Etapa gravada, mas falha ao gravar nota em observacao.", 500);
    }
  }

  // Mantém "última interação" fresca — mesmo campo que registrarInteracaoClinica atualiza.
  var ultimaIdx = headers.indexOf("ultima_interacao");
  if (ultimaIdx >= 0) {
    _writeCellVerified(sheet, rowIdx, ultimaIdx + 1, _nowBr());
  }

  // Responsável pela mudança — só sobrescreve se enviado explicitamente.
  var respIdx = headers.indexOf("responsavel");
  if (respIdx >= 0 && data.responsavel) {
    _writeCellVerified(sheet, rowIdx, respIdx + 1, String(data.responsavel).trim());
  }

  var nomeClinicaIdx = headers.indexOf("nome_clinica");
  var nomeClinica = nomeClinicaIdx >= 0 ? String(found.rowData[nomeClinicaIdx] || "").trim() : "";
  var pcmsoAtualizado = _mirrorEtapaParaPcmso(nomeClinica, novaEtapa);

  _logEntry({
    acao:   "updateCrmClinicaEtapa",
    status: "OK",
    aba:    _SHEETS.CRM_CLINICAS + (pcmsoAtualizado ? " + " + _SHEETS.B2B_PCMSO : ""),
    id:     clinicaId,
    resumo: "Etapa alterada para " + novaEtapa,
  });

  return _ok({
    id: clinicaId,
    message: "Etapa atualizada com sucesso." + (pcmsoAtualizado ? " Base PCMSO também atualizada." : ""),
    pcmsoAtualizado: pcmsoAtualizado,
  });
}

/**
 * Espelha (best-effort) a nova etapa na aba "Base Prospecção B2B PCMSO",
 * coluna "Etapa" — garante a coluna de forma idempotente (nunca apaga/reordena
 * colunas existentes) e localiza a linha por nome normalizado (mesma técnica
 * de _keyNorm usada em registrarInteracaoClinica e no matching de
 * promote-pcmso-to-crm.py), nunca por telefone. Se a aba não existir, o nome
 * não for encontrado, ou a coluna de nome não existir, retorna false
 * silenciosamente — isso NUNCA deve derrubar a ação principal
 * (updateCrmClinicaEtapa já confirmou a escrita em CRM Clinicas).
 */
function _mirrorEtapaParaPcmso(nomeClinica, novaEtapa) {
  if (!nomeClinica) return false;

  var ss = _getWorkbook();
  var sheet = ss.getSheetByName(_SHEETS.B2B_PCMSO);
  if (!sheet) return false;

  try {
    var lastRow = sheet.getLastRow();
    var lastCol = sheet.getLastColumn();
    if (lastRow < 2 || lastCol < 1) return false;

    var allData = sheet.getRange(1, 1, lastRow, lastCol).getValues();
    var headers = allData[0].map(function(h) { return String(h).trim(); });
    var headersLower = headers.map(function(h) { return h.toLowerCase(); });

    var nomeCandidatos = ["nome da clinica/empresa", "nome da clínica/empresa", "nome da clinica", "nome da clínica", "nome"];
    var nomeIdx = -1;
    for (var n = 0; n < nomeCandidatos.length; n++) {
      var idx = headersLower.indexOf(nomeCandidatos[n]);
      if (idx >= 0) { nomeIdx = idx; break; }
    }
    if (nomeIdx < 0) return false;

    var targetKey = _keyNorm(nomeClinica);
    var rowIdx = -1;
    for (var i = 1; i < allData.length; i++) {
      var candKey = _keyNorm(String(allData[i][nomeIdx] || ""));
      if (!candKey) continue;
      if (candKey === targetKey) { rowIdx = i + 1; break; }
      var minLen = Math.min(candKey.length, targetKey.length);
      if (minLen >= 5 && (candKey.indexOf(targetKey) >= 0 || targetKey.indexOf(candKey) >= 0)) {
        rowIdx = i + 1; // não interrompe: prefere igualdade exata se aparecer depois
      }
    }
    if (rowIdx < 0) return false;

    // Garante a coluna "Etapa" de forma idempotente (acrescenta ao final, nunca reordena/apaga).
    var headersAtualizados = _ensureExtraColumns(sheet, ["Etapa"]);
    var etapaIdx = headersAtualizados.indexOf("Etapa");
    if (etapaIdx < 0) return false;

    return _writeCellVerified(sheet, rowIdx, etapaIdx + 1, novaEtapa);
  } catch (e) {
    return false;
  }
}

/**
 * Vincula um lead B2B (clínica/parceiro) a uma clínica já existente em CRM Clínicas,
 * marcando o lead como convertido/arquivado — sem removê-lo da aba Leads.
 *
 * Usado pelo botão "Vincular a clínica" / "Converter em parceiro" do painel
 * (leads de serviço Clínicas / PCMSO). Diferente do fluxo de paciente
 * (_cvConverterLeadCore), que remove a linha da aba Leads: aqui o lead
 * continua na aba Leads como histórico, marcado por tipo_lead/status_operacional.
 *
 * IDEMPOTENTE: pode ser chamada várias vezes para o mesmo lead_id + clinica_id
 * sem criar duplicatas — nem na aba Leads (é sempre um update na mesma linha),
 * nem em "Leads Convertidos" (upsert por lead_id + entidade_id, ver
 * _upsertLeadConvertido), nem em "CRM Contatos B2B" (upsert por clinica_id +
 * nome_contato, ver _upsertContatoB2B). Só "Log Conversões Leads" registra uma
 * linha por chamada (é um log de auditoria, não um estado consolidado) — mas
 * deixa claro no campo 'resultado' se foi um vínculo novo, uma atualização ou
 * uma chamada repetida sem mudança nenhuma.
 *
 * Reaproveita 'Leads Convertidos' e 'Log Conversões Leads' (abas já existentes,
 * criadas por converter-lead-em-paciente.gs) para manter o mesmo padrão de
 * auditoria usado pela conversão de pacientes.
 *
 * Nunca inventa telefone/e-mail: telefone do contato vem do próprio lead
 * (telefone_whatsapp), e-mail fica em branco se não informado.
 *
 * data esperado:
 *   lead_id       (obrigatório) — LEAD-...
 *   clinica_id    (obrigatório) — CLIN-... já cadastrado em CRM Clínicas
 *   responsavel   (obrigatório)
 *   etapa         (opcional) — "Convertido em clínica/parceiro" (padrão) ou "Parceiro ativo"
 *   nome_contato, cargo, papel, proxima_acao, data_proxima_acao, email (opcionais,
 *   usados para criar/atualizar um registro em CRM Contatos B2B)
 */
function _vincularLeadAClinica(data) {
  _required(data, ["lead_id", "clinica_id", "responsavel"]);

  var leadId    = String(data.lead_id).trim();
  var clinicaId = String(data.clinica_id).trim();
  var novaEtapa = String(data.etapa || "Convertido em clínica/parceiro").trim();

  if (_LEAD_ETAPAS_B2B_TERMINAL.indexOf(novaEtapa) < 0) {
    return _err(
      "Etapa inválida para vínculo B2B. Use uma destas: " + _LEAD_ETAPAS_B2B_TERMINAL.join(" ou "),
      400
    );
  }

  // Confirma que a clínica de destino existe — nunca inventamos entidade_id.
  var crmSheet   = _getOrCreateSheet(_SHEETS.CRM_CLINICAS);
  var clinicaRow = _findRowByIdColumn(crmSheet, "clinica_id", clinicaId);
  if (!clinicaRow) {
    return _err("Clínica não encontrada em " + _SHEETS.CRM_CLINICAS + ": " + clinicaId, 404);
  }
  var nomeClinicaIdx = clinicaRow.headers.indexOf("nome_clinica");
  var nomeClinica    = nomeClinicaIdx >= 0 ? String(clinicaRow.rowData[nomeClinicaIdx] || "").trim() : clinicaId;

  // Localiza o lead na aba Leads.
  var leadsSheet = _getOrCreateSheet(_SHEETS.LEADS);
  var leadRow    = _findRowByIdColumn(leadsSheet, "lead_id", leadId);
  if (!leadRow) {
    return _err("Lead não encontrado: " + leadId, 404);
  }

  // Garante colunas novas (tipo_lead, entidade_destino, ...) sem tocar nas antigas.
  var headers = _ensureExtraColumns(leadsSheet, _LEAD_COLUNAS_EXTRA_B2B);
  var lastCol = leadsSheet.getLastColumn();
  var rowData = leadsSheet.getRange(leadRow.rowIdx, 1, 1, lastCol).getValues()[0];

  var etapaIdx  = headers.indexOf("etapa");
  var nomeIdx   = headers.indexOf("nome");
  var telIdx    = headers.indexOf("telefone_whatsapp");
  var obsIdx    = headers.indexOf("observacao");
  var respIdx0  = headers.indexOf("responsavel");
  var entIdx0   = headers.indexOf("entidade_id");
  var statusIdx0 = headers.indexOf("status_operacional");
  if (etapaIdx < 0) return _err("Coluna etapa não encontrada na aba " + _SHEETS.LEADS + ".", 500);

  var nomeLead = nomeIdx >= 0 ? String(rowData[nomeIdx] || "").trim() : "";
  var telLead  = telIdx  >= 0 ? String(rowData[telIdx]  || "").trim() : "";
  var hoje     = _nowBr();
  var obsCurta = "Vinculado à " + nomeClinica + " / " + clinicaId;

  // Estado ANTES desta chamada — usado para decidir se isso é um vínculo novo,
  // uma atualização de um vínculo já existente, ou uma repetição sem mudança
  // (idempotência: chamar de novo com os mesmos dados não deve criar duplicata
  // nem ser tratado como "novo vínculo" no log).
  var beforeEtapa       = String(rowData[etapaIdx] || "").trim();
  var beforeEntidadeId  = entIdx0 >= 0 ? String(rowData[entIdx0] || "").trim() : "";
  var beforeStatusOp    = statusIdx0 >= 0 ? String(rowData[statusIdx0] || "").trim() : "";
  var beforeResponsavel = respIdx0 >= 0 ? String(rowData[respIdx0] || "").trim() : "";
  var novoResponsavel   = data.responsavel ? String(data.responsavel).trim() : beforeResponsavel;

  var jaEstavaVinculado = (beforeEntidadeId === clinicaId && beforeStatusOp === "convertido");
  var conversionState;
  if (!jaEstavaVinculado) {
    conversionState = "vinculado";
  } else if (beforeEtapa !== novaEtapa || beforeResponsavel !== novoResponsavel) {
    conversionState = "atualizado";
  } else {
    conversionState = "sem-alteracao";
  }
  var resultadoLog = conversionState === "vinculado" ? "B2B — Vinculado"
    : conversionState === "atualizado" ? "B2B — Já vinculado / atualizado"
    : "B2B — Sem alteração";

  // 1) Grava a etapa com o mesmo mecanismo verificado usado por updateLeadStage
  //    (revalida o dropdown antes de escrever, confirma a gravação lendo de volta;
  //    se a validação não for um dropdown simples, cai em fallback e limpa a validação).
  if (!_writeEtapaVerified(leadsSheet, leadRow.rowIdx, etapaIdx + 1, novaEtapa)) {
    var etapaLida = String(leadsSheet.getRange(leadRow.rowIdx, etapaIdx + 1).getDisplayValue() || "").trim();
    _logEntry({ acao: "vincularLeadAClinica", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS, id: leadId, resumo: "Etapa desejada: " + novaEtapa + " | Etapa lida: " + etapaLida });
    _logConversaoLeads(leadId, nomeLead, novaEtapa, [], "B2B — Erro (falha ao gravar etapa)");
    return _err("Falha ao gravar etapa. Etapa desejada: " + novaEtapa + " | Etapa lida: " + etapaLida, 500);
  }

  // 2) Marca as colunas de conversão/arquivamento — lead permanece na aba Leads.
  // Toda escrita é verificada (lida de volta): se qualquer campo não gravar,
  // a ação inteira falha em vez de retornar ok:true com dado incompleto.
  var extraValues = {
    tipo_lead:          "b2b",
    entidade_destino:   _SHEETS.CRM_CLINICAS,
    entidade_id:        clinicaId,
    data_conversao:     hoje,
    status_operacional: "convertido",
  };
  var extraFailures = [];
  Object.keys(extraValues).forEach(function(col) {
    var idx = headers.indexOf(col);
    if (idx < 0) return;
    if (!_writeCellVerified(leadsSheet, leadRow.rowIdx, idx + 1, extraValues[col])) extraFailures.push(col);
  });
  if (extraFailures.length > 0) {
    _logEntry({ acao: "vincularLeadAClinica", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS, id: leadId, resumo: "Falha ao gravar colunas: " + extraFailures.join(", ") });
    _logConversaoLeads(leadId, nomeLead, novaEtapa, [], "B2B — Erro (falha ao gravar colunas de conversão)");
    return _err("Etapa gravada, mas falha ao gravar campos de conversão: " + extraFailures.join(", "), 500);
  }

  // 3) Observação curta de histórico (mesmo padrão de updateLeadStage).
  if (obsIdx >= 0) {
    var obsAtual = String(rowData[obsIdx] || "").trim();
    var nota     = "[" + hoje + "] " + obsCurta;
    var obsNovo  = obsAtual ? (obsAtual + " | " + nota) : nota;
    if (!_writeCellVerified(leadsSheet, leadRow.rowIdx, obsIdx + 1, obsNovo)) {
      _logEntry({ acao: "vincularLeadAClinica", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS, id: leadId, resumo: "Falha ao gravar nota em observacao." });
      _logConversaoLeads(leadId, nomeLead, novaEtapa, [], "B2B — Erro (falha ao gravar observação)");
      return _err("Etapa gravada, mas falha ao gravar nota em observacao.", 500);
    }
  }

  var respIdx = headers.indexOf("responsavel");
  if (respIdx >= 0 && data.responsavel) {
    if (!_writeCellVerified(leadsSheet, leadRow.rowIdx, respIdx + 1, String(data.responsavel).trim())) {
      _logEntry({ acao: "vincularLeadAClinica", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS, id: leadId, resumo: "Falha ao gravar responsavel." });
      _logConversaoLeads(leadId, nomeLead, novaEtapa, [], "B2B — Erro (falha ao gravar responsável)");
      return _err("Etapa gravada, mas falha ao gravar responsavel.", 500);
    }
  }

  // Releitura da linha já atualizada, para arquivar uma cópia fiel em Leads Convertidos.
  var rowFinal = leadsSheet.getRange(leadRow.rowIdx, 1, 1, lastCol).getValues()[0];
  var rowFinalMap = {};
  headers.forEach(function(h, i) { rowFinalMap[h] = rowFinal[i]; });

  // 4) Upsert em "Leads Convertidos" — aba já existente, reaproveitada (não recriada).
  //    Diferente do fluxo de paciente, NÃO removemos a linha da aba Leads.
  //    IDEMPOTENTE: identifica o registro consolidado por lead_id + entidade_id;
  //    se já existir (chamada repetida para o mesmo lead+clínica), atualiza a
  //    linha existente em vez de acrescentar uma nova (evita duplicatas).
  var convertidosSheet = _getOrCreateSheet(_SHEETS.LEADS_CONVERTIDOS);
  _ensureSheetHeader(convertidosSheet, _B2B_LEADS_CONVERTIDOS_CABECALHO_FALLBACK);
  // Garante que "Leads Convertidos" também tenha as colunas B2B (entidade_id etc.),
  // sem o quê não haveria como identificar duplicatas por clínica nessa aba.
  var convHeaders = _ensureExtraColumns(convertidosSheet, _LEAD_COLUNAS_EXTRA_B2B);

  var convValuesByHeader = {};
  convHeaders.forEach(function(h) {
    convValuesByHeader[h] = rowFinalMap.hasOwnProperty(h) ? rowFinalMap[h] : "";
  });

  var existingConvRow = _findRowByColumns(convertidosSheet, convHeaders, {
    lead_id:     leadId,
    entidade_id: clinicaId,
  });

  if (existingConvRow > 0) {
    var convFailures = [];
    convHeaders.forEach(function(h, idx) {
      if (!_writeCellVerified(convertidosSheet, existingConvRow, idx + 1, convValuesByHeader[h])) {
        convFailures.push(h);
      }
    });
    if (convFailures.length > 0) {
      _logEntry({ acao: "vincularLeadAClinica", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS_CONVERTIDOS, id: leadId, resumo: "Falha ao atualizar: " + convFailures.join(", ") });
      _logConversaoLeads(leadId, nomeLead, novaEtapa, [], "B2B — Erro (falha ao atualizar " + _SHEETS.LEADS_CONVERTIDOS + ")");
      return _err("Etapa gravada, mas falha ao atualizar " + _SHEETS.LEADS_CONVERTIDOS + ": " + convFailures.join(", "), 500);
    }
  } else {
    _appendRowSemValidacao(convertidosSheet, convHeaders.map(function(h) { return convValuesByHeader[h]; }));
  }

  // 5) Cria/atualiza contato B2B, se um nome de contato foi informado.
  //    IDEMPOTENTE: upsert por contato_id (se enviado) ou por clinica_id + nome
  //    normalizado — nunca cria Juan duplicado numa segunda chamada.
  var contatoId = "";
  if (data.nome_contato && String(data.nome_contato).trim()) {
    contatoId = _upsertContatoB2B({
      contatoId:   data.contato_id || "",
      clinicaId:   clinicaId,
      nomeContato: data.nome_contato,
      cargo:       data.cargo || "",
      telefone:    telLead, // vem do lead — nunca inventado
      email:       data.email || "",
      papel:       data.papel || "",
      responsavel: data.responsavel || "",
      proximaAcao: data.proxima_acao || "",
      dataProxima: data.data_proxima_acao || "",
      observacao:  obsCurta,
    });
  }

  // 6) Log técnico de conversão — aba já existente, reaproveitada (não recriada).
  //    Toda chamada gera uma linha aqui (é o histórico de auditoria, não um
  //    estado consolidado), mas o campo 'resultado' deixa claro se foi um
  //    vínculo novo, uma atualização, ou uma repetição sem mudança nenhuma.
  var destinosCriados = [_SHEETS.CRM_CLINICAS + ":" + clinicaId];
  if (contatoId) destinosCriados.push(_SHEETS.CRM_CONTATOS_B2B + ":" + contatoId);

  _logConversaoLeads(leadId, nomeLead, novaEtapa, destinosCriados, resultadoLog);

  // Log Centro Comando — apenas metadados, nunca telefone/observação privada.
  _logEntry({
    acao:   "vincularLeadAClinica",
    status: "OK",
    aba:    _SHEETS.LEADS + " + " + _SHEETS.LEADS_CONVERTIDOS + " + " + _SHEETS.LOG_CONVERSOES + (contatoId ? " + " + _SHEETS.CRM_CONTATOS_B2B : ""),
    id:     leadId,
    resumo: resultadoLog + " — clínica " + clinicaId + " (etapa: " + novaEtapa + ").",
  });

  var messageByState = {
    "vinculado":      "Lead vinculado com sucesso à clínica " + nomeClinica + ".",
    "atualizado":     "Vínculo com " + nomeClinica + " já existia — dados atualizados.",
    "sem-alteracao":  "Lead já estava vinculado à " + nomeClinica + " — nenhuma mudança necessária.",
  };

  return _ok({
    id:         leadId,
    clinica_id: clinicaId,
    contato_id: contatoId || null,
    status:     conversionState,
    message:    messageByState[conversionState],
  });
}

/**
 * Registra uma linha em "Log Conversões Leads" — aba já existente, reaproveitada
 * (não recriada). Chamada tanto no sucesso quanto em qualquer falha de gravação
 * de vincularLeadAClinica, para que o log fique interpretável ("o que aconteceu
 * nessa tentativa"), não só as tentativas bem-sucedidas.
 *
 * resultado esperado (padronizado): um destes quatro prefixos —
 *   "B2B — Vinculado"
 *   "B2B — Já vinculado / atualizado"
 *   "B2B — Sem alteração"
 *   "B2B — Erro (...)"
 *
 * Nunca recebe telefone nem observação privada — só metadados operacionais.
 * Falha no log nunca interrompe a ação principal (mesmo padrão de _logEntry).
 */
function _logConversaoLeads(leadId, nome, etapa, destinosCriados, resultado) {
  try {
    var logSheet = _getOrCreateSheet(_SHEETS.LOG_CONVERSOES);
    _ensureSheetHeader(logSheet, _B2B_LOG_CONVERSOES_CABECALHO_FALLBACK);
    _appendRowSemValidacao(logSheet, [
      _nowBr(),
      leadId || "",
      nome || "",
      etapa || "",
      (destinosCriados || []).join("; "),
      resultado || "",
      "",
    ]);
  } catch (_) {
    // Silencioso: falha no log não deve interromper a ação principal.
  }
}

/**
 * Cria ou atualiza um contato B2B em "CRM Contatos B2B", identificado por
 * clinica_id + nome_contato (chave normalizada). Aba criada sob demanda,
 * na primeira vez que um contato é vinculado.
 */
function _upsertContatoB2B(info) {
  var sheet = _getOrCreateSheet(_SHEETS.CRM_CONTATOS_B2B);
  _ensureSheetHeader(sheet, _CRM_CONTATOS_B2B_CABECALHO);

  var lastCol = sheet.getLastColumn();
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0]
    .map(function(h) { return String(h).trim(); });

  var clinicaIdx  = headers.indexOf("clinica_id");
  var nomeIdx     = headers.indexOf("nome_contato");
  var contatoIdIdx0 = headers.indexOf("contato_id");
  var lastRow     = sheet.getLastRow();

  // Identifica o contato existente por contato_id explícito (se enviado) ou,
  // por padrão, por clinica_id + nome_contato normalizado — nunca cria
  // duplicata para o mesmo par clínica/contato.
  var rowIdx = -1;
  if (lastRow >= 2 && info.contatoId && contatoIdIdx0 >= 0) {
    rowIdx = _findRowByColumns(sheet, headers, { contato_id: String(info.contatoId).trim() });
  }
  if (rowIdx < 0 && lastRow >= 2 && clinicaIdx >= 0 && nomeIdx >= 0) {
    var allData   = sheet.getRange(2, 1, lastRow - 1, lastCol).getValues();
    var keyTarget = _keyNorm(info.clinicaId) + "|" + _keyNorm(info.nomeContato);
    for (var i = 0; i < allData.length; i++) {
      var key = _keyNorm(String(allData[i][clinicaIdx] || "")) + "|" + _keyNorm(String(allData[i][nomeIdx] || ""));
      if (key === keyTarget) { rowIdx = i + 2; break; }
    }
  }

  var updates = {
    clinica_id:            info.clinicaId,
    nome_contato:          info.nomeContato,
    cargo:                 info.cargo || "",
    telefone_whatsapp:     info.telefone || "",
    email:                 info.email || "",
    papel:                 info.papel || "",
    status_relacionamento: "Ativo",
    ultima_interacao:      _nowBr(),
    proxima_acao:          info.proximaAcao || "",
    data_proxima_acao:     info.dataProxima || "",
    responsavel:           info.responsavel || "",
    observacao:            info.observacao || "",
  };

  if (rowIdx > 0) {
    var contatoUpdateFailures = [];
    Object.keys(updates).forEach(function(col) {
      var idx = headers.indexOf(col);
      if (idx < 0) return;
      if (!_writeCellVerified(sheet, rowIdx, idx + 1, updates[col])) contatoUpdateFailures.push(col);
    });
    if (contatoUpdateFailures.length > 0) {
      throw new Error("Falha ao gravar contato B2B — campos: " + contatoUpdateFailures.join(", "));
    }
    var contatoIdIdx = headers.indexOf("contato_id");
    return contatoIdIdx >= 0 ? String(sheet.getRange(rowIdx, contatoIdIdx + 1).getValue() || "") : "";
  }

  var newId = _nextId(sheet, "CONT");
  _appendRowSemValidacao(sheet, _buildRow(sheet, Object.assign({ contato_id: newId }, updates)));
  return newId;
}

// ── Helpers de infra ──────────────────────────────────────────────────────────


// IMPORTANTE: o serviço SpreadsheetApp enfileira (batch) as escritas — uma
// exceção de validação disparada por um setValue()/setValues() pode não
// aparecer na hora, e sim no PRÓXIMO SpreadsheetApp.flush() (que pode estar
// em outra função, escrevendo em outra célula). Por isso, TODA função abaixo
// dá flush() imediatamente após a própria escrita, dentro do mesmo try/catch
// — assim qualquer falha de validação é presa e identificada no ponto exato
// onde ocorreu, em vez de subir como "Selecione uma opção da lista." genérico
// lá na exceção não tratada do doPost.

function _setValuesSemValidacao(range, values) {
  range.clearDataValidations();
  range.setValues(values);
  SpreadsheetApp.flush();
}

function _appendRowSemValidacao(sheet, row) {
  var nextRow = sheet.getLastRow() + 1;
  var range = sheet.getRange(nextRow, 1, 1, row.length);
  try {
    _setValuesSemValidacao(range, [row]);
  } catch (e) {
    throw new Error("Falha ao adicionar linha na aba '" + sheet.getName() + "': " + e.message);
  }
  return nextRow;
}

function _setCellSemValidacao(cell, value) {
  cell.clearDataValidations();
  cell.setValue(value);
  SpreadsheetApp.flush();
}

/**
 * Escreve um valor em uma célula existente de forma segura contra qualquer
 * validação/dropdown ("Selecione uma opção da lista.", fórmula customizada,
 * etc.): remove a validação da célula antes de escrever, grava, confirma
 * (flush + leitura de volta) e retorna true/false conforme o valor lido bate
 * com o esperado. Nunca deixa uma exceção de validação subir sem tratamento
 * — o flush() acontece DENTRO do try, para capturar exceções de validação
 * que só aparecem nesse momento (ver nota acima).
 *
 * Usado para TODO campo que não seja a coluna 'etapa' da aba Leads (que tem
 * seu próprio helper, _writeEtapaVerified, para preservar o dropdown oficial).
 */
function _writeCellVerified(sheet, row, col, value) {
  var cell = sheet.getRange(row, col);
  try {
    cell.clearDataValidations();
    cell.setValue(value);
    SpreadsheetApp.flush();
  } catch (e) {
    return false;
  }

  var gravadoValue = cell.getValue();

  // Datas: o Google Sheets pode reformatar/reinterpretar um Date ao gravar em
  // uma coluna com formatação diferente da origem (ex.: copiando de 'Leads'
  // para 'Leads Convertidos'). Comparar por instante evita falso-negativo.
  if (value instanceof Date && gravadoValue instanceof Date) {
    return value.getTime() === gravadoValue.getTime();
  }

  var esperado = String(value === null || value === undefined ? "" : value).trim();

  // Caminho comum: valor gravado bate exatamente com o esperado.
  var gravadoStr = String(gravadoValue === null || gravadoValue === undefined ? "" : gravadoValue).trim();
  if (gravadoStr === esperado) return true;

  // Fallback: o Sheets pode ter convertido uma string parecida com data
  // (ex.: "25/06/2026") em um valor Date — nesse caso o texto exibido
  // (getDisplayValue) tende a preservar o formato original mais fielmente
  // que a representação padrão de Date.
  var display = String(cell.getDisplayValue() || "").trim();
  return display === esperado;
}

// etapasOficiais é opcional — por padrão preserva o comportamento original
// (dropdown de Leads). updateCrmClinicaEtapa passa _CRM_ETAPAS_OFICIAIS.
function _ensureLeadStageValidationAllows(cell, novaEtapa, etapasOficiais) {
  var etapas = (etapasOficiais || _LEAD_ETAPAS_OFICIAIS).slice();

  if (novaEtapa && etapas.indexOf(novaEtapa) < 0) {
    etapas.push(novaEtapa);
  }

  var rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(etapas, true)
    .setAllowInvalid(false)
    .build();

  cell.setDataValidation(rule);
}

/**
 * Escreve a etapa (de um lead OU de uma clínica CRM) de forma segura e
 * verificada. Primeiro tenta o caminho "inteligente": estende o dropdown
 * oficial de etapas (etapasOficiais) para incluir a nova etapa (preserva a
 * validação para uso manual futuro na planilha). Se, por qualquer motivo, a
 * validação da célula não for um dropdown simples (ex.: fórmula customizada,
 * validação legada) e a escrita ainda assim falhar, cai em um fallback que
 * remove qualquer validação da célula e tenta de novo. Sempre confirma a
 * gravação lendo o valor de volta.
 *
 * etapasOficiais é opcional — omitir preserva o comportamento original
 * (dropdown de Leads, _LEAD_ETAPAS_OFICIAIS).
 *
 * Retorna true/false — nunca deixa "Selecione uma opção da lista." (ou
 * qualquer outra exceção de validação) subir sem tratamento.
 */
function _writeEtapaVerified(sheet, row, col, novaEtapa, etapasOficiais) {
  var cell = sheet.getRange(row, col);

  try {
    _ensureLeadStageValidationAllows(cell, novaEtapa, etapasOficiais);
    cell.setValue(novaEtapa);
    SpreadsheetApp.flush();
  } catch (e1) {
    try {
      cell.clearDataValidations();
      cell.setValue(novaEtapa);
      SpreadsheetApp.flush();
    } catch (e2) {
      return false;
    }
  }

  var gravado = String(cell.getDisplayValue() || cell.getValue() || "").trim();
  return gravado === String(novaEtapa).trim();
}

function _getWorkbook() {
  var spreadsheetId = PropertiesService.getScriptProperties().getProperty("SPREADSHEET_ID");
  if (spreadsheetId && String(spreadsheetId).trim()) {
    return SpreadsheetApp.openById(String(spreadsheetId).trim());
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) {
    throw new Error("SPREADSHEET_ID não configurado e nenhuma planilha ativa encontrada.");
  }
  return ss;
}

function _getOrCreateSheet(name) {
  var ss    = _getWorkbook();
  var sheet = ss.getSheetByName(name);
  if (!sheet) sheet = ss.insertSheet(name);
  return sheet;
}

/**
 * Aplica um cabeçalho a uma aba apenas se ela ainda não tiver dados —
 * nunca sobrescreve um cabeçalho já existente (reaproveita abas existentes).
 */
function _ensureSheetHeader(sheet, headers) {
  if (sheet.getLastRow() === 0 || String(sheet.getRange(1, 1).getValue() || "").trim() === "") {
    var range = sheet.getRange(1, 1, 1, headers.length);
    try {
      range.clearDataValidations();
      range
        .setValues([headers])
        .setFontWeight("bold")
        .setFontColor("#ffffff")
        .setBackground("#08243d");
      SpreadsheetApp.flush();
    } catch (e) {
      throw new Error("Falha ao criar cabeçalho na aba '" + sheet.getName() + "': " + e.message);
    }
  }
}

/**
 * Garante que as colunas em extraHeaders existam no cabeçalho da aba,
 * adicionando-as ao final se ausentes — nunca reordena/remove colunas antigas.
 * Retorna o array de cabeçalhos (antigo + novo).
 */
function _ensureExtraColumns(sheet, extraHeaders) {
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0]
    .map(function(h) { return String(h).trim(); });

  extraHeaders.forEach(function(h) {
    if (headers.indexOf(h) < 0) {
      lastCol += 1;
      var headerCell = sheet.getRange(1, lastCol);
      try {
        headerCell.clearDataValidations();
        headerCell.setValue(h);
        SpreadsheetApp.flush();
      } catch (e) {
        throw new Error("Falha ao criar coluna '" + h + "' na aba '" + sheet.getName() + "': " + e.message);
      }
      headers.push(h);
    }
  });
  return headers;
}

/**
 * Localiza uma linha pelo valor de uma coluna identificada por nome (não por posição fixa).
 * Retorna { rowIdx (1-based), headers, rowData } ou null se não encontrado.
 */
function _findRowByIdColumn(sheet, idColName, targetId) {
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  if (lastRow < 2 || lastCol < 1) return null;

  var allData = sheet.getRange(1, 1, lastRow, lastCol).getValues();
  var headers = allData[0].map(function(h) { return String(h).trim(); });
  var idIdx   = headers.indexOf(idColName);
  if (idIdx < 0) return null;

  for (var i = 1; i < allData.length; i++) {
    if (String(allData[i][idIdx] || "").trim() === targetId) {
      return { rowIdx: i + 1, headers: headers, rowData: allData[i] };
    }
  }
  return null;
}

/**
 * Localiza a linha (1-based) cujas colunas batem TODAS com matchMap
 * (ex.: { lead_id: "LEAD-...", entidade_id: "CLIN-006" }). Usado para upsert
 * idempotente (evitar duplicatas) em abas onde a chave é composta por mais de
 * uma coluna. Se alguma coluna de matchMap não existir no cabeçalho, retorna
 * -1 (não há como garantir dedup sem a coluna — o chamador deve então
 * acrescentar uma linha nova, nunca sobrescrever algo incerto).
 */
function _findRowByColumns(sheet, headers, matchMap) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return -1;

  var colIdxs = {};
  var keys = Object.keys(matchMap);
  for (var k = 0; k < keys.length; k++) {
    var idx = headers.indexOf(keys[k]);
    if (idx < 0) return -1;
    colIdxs[keys[k]] = idx;
  }

  var lastCol = sheet.getLastColumn();
  var data = sheet.getRange(2, 1, lastRow - 1, lastCol).getValues();
  for (var i = 0; i < data.length; i++) {
    var match = true;
    for (var j = 0; j < keys.length; j++) {
      var col = keys[j];
      var v = String(data[i][colIdxs[col]] || "").trim();
      if (v !== String(matchMap[col]).trim()) { match = false; break; }
    }
    if (match) return i + 2; // 1-based, +1 para header +1 para índice 0-based
  }
  return -1;
}

/**
 * Ação de limpeza EXPLÍCITA e segura para remover duplicatas em
 * "Leads Convertidos" causadas por chamadas repetidas de vincularLeadAClinica
 * antes da deduplicação (ex.: múltiplos testes com curl). NUNCA apaga nada
 * automaticamente: por padrão roda em modo dry-run (só relata o que faria);
 * só remove de fato se data.confirm === true.
 *
 * Mantém sempre a ocorrência MAIS RECENTE (maior índice de linha — a inserida
 * por último) e remove as anteriores. Nunca remove a única ocorrência de um
 * lead (não há duplicata para remover nesse caso).
 *
 * data esperado:
 *   lead_id     (obrigatório) — remove duplicatas apenas deste lead
 *   clinica_id  (opcional)    — restringe a duplicatas lead_id + esta clínica
 *                                (coluna entidade_id); sem isso, considera
 *                                qualquer duplicata do mesmo lead_id
 *   confirm     (opcional, default false) — true para remover de fato
 */
/**
 * Conta quantas células de uma linha estão preenchidas — usado para escolher
 * a linha "mais completa" entre duplicatas (não apenas a mais recente).
 */
function _rowCompleteness(rowValues) {
  var n = 0;
  for (var i = 0; i < rowValues.length; i++) {
    var v = rowValues[i];
    if (v !== "" && v !== null && v !== undefined) n++;
  }
  return n;
}

/**
 * data esperado:
 *   lead_id     (obrigatório) — remove duplicatas apenas deste lead
 *   clinica_id  (opcional)    — restringe a duplicatas lead_id + esta clínica
 *                                (coluna entidade_id); sem isso, considera
 *                                qualquer duplicata do mesmo lead_id
 *   dryRun      (opcional, default true)  — false para remover de fato
 *   confirm     (opcional, alias legado)  — confirm:true equivale a dryRun:false
 *
 * Critério de escolha da linha a manter: primeiro a MAIS COMPLETA (mais
 * células preenchidas); em empate, a MAIS RECENTE (maior índice de linha).
 * Antes de remover cada duplicata, registra seus dados-chave (lead_id,
 * entidade_id, etapa) em "Log Conversões Leads" — o histórico não é
 * simplesmente apagado, fica rastreável ali.
 */
function _dedupLeadsConvertidos(data) {
  _required(data, ["lead_id"]);
  var leadId    = String(data.lead_id).trim();
  var clinicaId = data.clinica_id ? String(data.clinica_id).trim() : null;
  // dryRun é o nome pedido pela regra de negócio; confirm:true é aceito como
  // alias legado (usado em testes anteriores desta mesma sessão de trabalho).
  var dryRun = data.dryRun === false || data.confirm === true ? false : true;

  var sheet   = _getOrCreateSheet(_SHEETS.LEADS_CONVERTIDOS);
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  if (lastRow < 2 || lastCol < 1) {
    return _ok({ message: "Nenhum registro em " + _SHEETS.LEADS_CONVERTIDOS + ".", removed: 0, dryRun: dryRun });
  }

  var allData = sheet.getRange(1, 1, lastRow, lastCol).getValues();
  var headers = allData[0].map(function(h) { return String(h).trim(); });
  var idIdx    = headers.indexOf("lead_id");
  var entIdx   = headers.indexOf("entidade_id");
  var etapaIdx = headers.indexOf("etapa");
  if (idIdx < 0) return _err("Coluna lead_id não encontrada em " + _SHEETS.LEADS_CONVERTIDOS + ".", 500);

  var matching = []; // { rowNum (1-based), completeness, values }
  for (var i = 1; i < allData.length; i++) {
    var rowLeadId = String(allData[i][idIdx] || "").trim();
    if (rowLeadId !== leadId) continue;
    if (clinicaId) {
      if (entIdx < 0) continue; // sem coluna entidade_id, não dá pra filtrar por clínica
      var rowEnt = String(allData[i][entIdx] || "").trim();
      if (rowEnt !== clinicaId) continue;
    }
    matching.push({ rowNum: i + 1, completeness: _rowCompleteness(allData[i]), values: allData[i] });
  }

  if (matching.length <= 1) {
    return _ok({
      message: "Nenhuma duplicata encontrada para " + leadId + (clinicaId ? " / " + clinicaId : "") + ".",
      removed: 0,
      dryRun: dryRun,
    });
  }

  // Ordena por completude desc, depois por linha (mais recente) desc.
  // O primeiro da lista ordenada é o que fica; os demais são duplicatas.
  var ordenado = matching.slice().sort(function(a, b) {
    if (b.completeness !== a.completeness) return b.completeness - a.completeness;
    return b.rowNum - a.rowNum;
  });
  var keep      = ordenado[0];
  var toRemove  = ordenado.slice(1);

  if (dryRun) {
    return _ok({
      message: "Modo dry-run — envie dryRun:false (ou confirm:true) para remover de fato.",
      dryRun: true,
      lead_id: leadId,
      linhas_duplicadas: toRemove.length,
      manteria_linha: keep.rowNum,
      manteria_motivo: "mais completa (" + keep.completeness + " campos preenchidos)",
      removeria_linhas: toRemove.map(function(r) { return r.rowNum; }),
    });
  }

  // Registra cada duplicata removida em "Log Conversões Leads" ANTES de apagar —
  // preserva o rastro (lead_id, entidade_id, etapa) mesmo depois da limpeza.
  toRemove.forEach(function(r) {
    var entVal   = entIdx   >= 0 ? String(r.values[entIdx]   || "").trim() : "";
    var etapaVal = etapaIdx >= 0 ? String(r.values[etapaIdx] || "").trim() : "";
    _logConversaoLeads(
      leadId, "", etapaVal,
      entVal ? [_SHEETS.CRM_CLINICAS + ":" + entVal] : [],
      "B2B — Duplicata removida em " + _SHEETS.LEADS_CONVERTIDOS + " (linha " + r.rowNum + ", mantida linha " + keep.rowNum + ")"
    );
  });

  // Remove de trás para frente para não desalinhar índices de linha durante o loop.
  toRemove.slice().sort(function(a, b) { return b.rowNum - a.rowNum; }).forEach(function(r) {
    sheet.deleteRow(r.rowNum);
  });

  _logEntry({
    acao:   "dedupLeadsConvertidos",
    status: "OK",
    aba:    _SHEETS.LEADS_CONVERTIDOS,
    id:     leadId,
    resumo: "Removidas " + toRemove.length + " duplicata(s), mantendo a mais completa (linha original " + keep.rowNum + ").",
  });

  return _ok({
    message: "Duplicatas removidas.",
    removed: toRemove.length,
    dryRun: false,
    lead_id: leadId,
  });
}

/**
 * Organiza a apresentação visual das abas do Command Center — SEM alterar
 * nenhum valor de célula. Só mexe em: linha congelada, filtro básico, largura
 * de coluna, destaque de cabeçalho (formatação, não texto), ordem de colunas
 * técnicas (sempre para o final, nunca reordena colunas de dados originais) e
 * visibilidade da aba (mostrar operacionais, ocultar técnicas — reportado
 * sempre, mesmo em dry-run).
 *
 * data esperado:
 *   dryRun (opcional, default true) — false para aplicar de fato
 *
 * Retorna um relatório por aba com as ações identificadas/aplicadas, para que
 * dryRun:true sirva como preview fiel do que dryRun:false faria.
 */
function _organizarAbasCommandCenter(data) {
  data = data || {};
  var dryRun = data.dryRun === false ? false : true;

  var ss = _getWorkbook();
  var relatorio = [];

  _ABAS_OPERACIONAIS.forEach(function(nome) {
    relatorio.push(_organizarAba(ss, nome, {
      tornarVisivel: true,
      colunasTecnicas: nome === _SHEETS.LEADS ? _LEAD_COLUNAS_EXTRA_B2B : null,
      dryRun: dryRun,
    }));
  });

  _ABAS_TECNICAS_OCULTAVEIS.forEach(function(nome) {
    relatorio.push(_organizarAba(ss, nome, {
      tornarVisivel: false,
      colunasTecnicas: null,
      dryRun: dryRun,
    }));
  });

  _logEntry({
    acao:   "organizarAbasCommandCenter",
    status: dryRun ? "DRY-RUN" : "OK",
    aba:    "múltiplas",
    id:     "",
    resumo: (dryRun ? "Simulação" : "Execução") + " de organização — " + relatorio.length + " abas avaliadas.",
  });

  return _ok({ dryRun: dryRun, abas: relatorio });
}

/**
 * Avalia (e, se !dryRun, aplica) a organização de uma única aba.
 * Nunca cria a aba se ela não existir — só organiza o que já existe.
 */
function _organizarAba(ss, nome, opts) {
  var sheet = ss.getSheetByName(nome);
  if (!sheet) {
    return { aba: nome, existe: false, acoes: [], mensagem: "Aba não encontrada — nada a organizar." };
  }

  var acoes  = [];
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  var temDados = lastRow >= 1 && lastCol >= 1;

  // 1) Congelar linha 1 (cabeçalho sempre visível ao rolar)
  if (temDados && sheet.getFrozenRows() < 1) {
    acoes.push("congelar_linha_1");
    if (!opts.dryRun) sheet.setFrozenRows(1);
  }

  // 2) Filtro básico, se ainda não houver
  if (temDados && lastRow >= 2 && sheet.getFilter() === null) {
    acoes.push("criar_filtro");
    if (!opts.dryRun) {
      try { sheet.getDataRange().createFilter(); } catch (e) { /* algumas abas não suportam filtro — ignora */ }
    }
  }

  // 3) Destacar cabeçalho — formatação apenas, nunca altera o texto/valor
  if (temDados) {
    var headerRange = sheet.getRange(1, 1, 1, lastCol);
    if (headerRange.getFontWeight() !== "bold") {
      acoes.push("destacar_cabecalho");
      if (!opts.dryRun) {
        headerRange.setFontWeight("bold").setBackground("#0d2b4a").setFontColor("#ffffff");
      }
    }
  }

  // 4) Ajustar largura básica de colunas (autoResize — nunca perde dados)
  if (temDados) {
    acoes.push("ajustar_largura_colunas");
    if (!opts.dryRun) {
      try { sheet.autoResizeColumns(1, lastCol); } catch (e) { /* ignora falha pontual de autoresize */ }
    }
  }

  // 5) Mover colunas técnicas para o final, só quando fora de ordem
  if (opts.colunasTecnicas && temDados) {
    var plano = _planReorderTecnicas(sheet, opts.colunasTecnicas);
    if (plano.needed) {
      acoes.push("reordenar_colunas_tecnicas");
      if (!opts.dryRun) _executarReorderTecnicas(sheet, opts.colunasTecnicas);
    }
  }

  // 6) Visibilidade — sempre relatado, mesmo em dry-run
  var estavaOculta = sheet.isSheetHidden();
  if (opts.tornarVisivel === true && estavaOculta) {
    acoes.push("tornar_visivel");
    if (!opts.dryRun) sheet.showSheet();
  } else if (opts.tornarVisivel === false && !estavaOculta) {
    acoes.push("ocultar");
    if (!opts.dryRun) sheet.hideSheet();
  }

  return {
    aba: nome,
    existe: true,
    linhas: lastRow,
    colunas: lastCol,
    oculta_atualmente: estavaOculta,
    acoes: acoes,
    mudou: acoes.length > 0,
  };
}

/**
 * Verifica se as colunas técnicas já estão contíguas no final da aba.
 * Retorna { needed: bool } — needed=true significa que pelo menos uma delas
 * está fora da posição esperada e precisaria ser movida.
 */
function _planReorderTecnicas(sheet, colunasTecnicas) {
  var lastCol = sheet.getLastColumn();
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(function(h) { return String(h).trim(); });
  var idxs = colunasTecnicas
    .map(function(c) { return headers.indexOf(c); })
    .filter(function(i) { return i >= 0; });
  if (idxs.length === 0) return { needed: false };

  var expectedStart = lastCol - idxs.length; // 0-based
  var sortedIdxs = idxs.slice().sort(function(a, b) { return a - b; });
  var contiguousAtEnd = sortedIdxs.every(function(idx, i) { return idx === expectedStart + i; });
  return { needed: !contiguousAtEnd };
}

/**
 * Move cada coluna técnica (na ordem definida em colunasTecnicas) para o
 * final da aba, uma de cada vez. Reconsulta o cabeçalho a cada iteração,
 * porque os índices mudam conforme as colunas anteriores são movidas.
 * Nunca apaga ou reescreve valores — apenas reordena colunas inteiras.
 */
function _executarReorderTecnicas(sheet, colunasTecnicas) {
  colunasTecnicas.forEach(function(nomeCol) {
    var lastCol = sheet.getLastColumn();
    var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(function(h) { return String(h).trim(); });
    var idx = headers.indexOf(nomeCol);
    if (idx < 0) return; // coluna não existe nesta aba — pula
    var colPos = idx + 1; // 1-based
    if (colPos === lastCol) return; // já está na última posição
    var lastRowForMove = Math.max(sheet.getLastRow(), 1);
    var range = sheet.getRange(1, colPos, lastRowForMove, 1);
    sheet.moveColumns(range, lastCol + 1);
  });
}

function _nextId(sheet, prefix) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return prefix + "-0001";

  var ids = sheet.getRange(2, 1, lastRow - 1, 1)
    .getValues()
    .map(function(r) { return String(r[0]); })
    .filter(function(v) { return v.startsWith(prefix + "-"); });

  var max = 0;
  ids.forEach(function(v) {
    var n = parseInt(v.slice(prefix.length + 1), 10);
    if (!isNaN(n) && n > max) max = n;
  });

  return prefix + "-" + String(max + 1).padStart(4, "0");
}

function _buildRow(sheet, fieldMap) {
  var lastCol  = Math.max(sheet.getLastColumn(), 1);
  var headers  = sheet.getRange(1, 1, 1, lastCol).getValues()[0]
    .map(function(h) { return String(h).trim(); });

  return headers.map(function(h) {
    if (h === "observacao_privada_minima") return ""; // nunca escrevemos aqui
    return fieldMap.hasOwnProperty(h) ? String(fieldMap[h]) : "";
  });
}

/**
 * Garante que colunas calculadas por fórmula (ex.: receita_bruta/custo_total/
 * resultado_liquido da aba Parceria Pastore - Atendimentos) continuem com
 * fórmula na linha recém-inserida. appendRow/setValues NUNCA herda fórmula da
 * linha de cima automaticamente — por isso copiamos explicitamente a fórmula
 * da linha anterior (copyTo ajusta as referências relativas sozinho). Se a
 * linha anterior também não tiver fórmula (ex.: primeira linha de dados),
 * não faz nada — não inventa fórmula do zero.
 */
function _ensureFormulaColumns(sheet, newRow, cols) {
  if (newRow < 3) return; // linha 2 é a primeira de dados; não há linha anterior pra copiar
  cols.forEach(function(col) {
    var newCell = sheet.getRange(newRow, col);
    if (newCell.getFormula()) return; // já tem fórmula própria — não sobrescreve
    var aboveCell = sheet.getRange(newRow - 1, col);
    if (aboveCell.getFormula()) {
      aboveCell.copyTo(newCell, SpreadsheetApp.CopyPasteType.PASTE_FORMULA, false);
    }
  });
}

function _nowBr() {
  return Utilities.formatDate(new Date(), "America/Sao_Paulo", "dd/MM/yyyy HH:mm");
}

function _normalizeConsent(val) {
  if (!val) return "Não informado";
  var v = String(val).toLowerCase().trim();
  if (["sim", "s", "yes", "1", "aceito", "ok", "true"].indexOf(v) >= 0) return "Sim";
  if (["não", "nao", "n", "no", "0", "recusa", "false"].indexOf(v) >= 0) return "Não";
  return "Não informado";
}

function _joinNotEmpty(a, b) {
  return [a, b].filter(function(x) { return x && String(x).trim(); }).join(" / ");
}

function _keyNorm(name) {
  return String(name)
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/[^a-z0-9 ]/g, "").replace(/\s+/g, " ").trim();
}

function _required(data, fields) {
  var missing = fields.filter(function(f) { return !data[f] || !String(data[f]).trim(); });
  if (missing.length > 0) throw new Error("Campos obrigatórios ausentes: " + missing.join(", "));
}

function _logEntry(entry) {
  try {
    var sheet = _getOrCreateSheet(_SHEETS.LOG);

    if (sheet.getLastRow() < 1) {
      _appendRowSemValidacao(sheet, ["timestamp", "acao", "status", "aba_afetada", "id_gerado", "email_executor", "resumo"]);
      sheet.getRange(1, 1, 1, 7).setFontWeight("bold");
    }

    var email = "";
    try { email = Session.getActiveUser().getEmail() || ""; } catch (_) {}

    _appendRowSemValidacao(sheet, [
      _nowBr(),
      entry.acao    || "",
      entry.status  || "",
      entry.aba     || "",
      entry.id      || "",
      email,
      entry.resumo  || "",
      // telefone e observacao NUNCA registrados aqui
    ]);
  } catch (_) {
    // Silencioso: falha no log não deve interromper a ação principal
  }
}

function _ok(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(Object.assign({ ok: true }, payload)))
    .setMimeType(ContentService.MimeType.JSON);
}

function _err(message, code) {
  return ContentService
    .createTextOutput(JSON.stringify({ ok: false, error: message, code: code || 400 }))
    .setMimeType(ContentService.MimeType.JSON);
}

// ── Teste manual (executar no editor do Apps Script, nunca via HTTP) ──────────

function _testCreatePaciente() {
  var token = PropertiesService.getScriptProperties().getProperty("API_TOKEN");
  if (!token) throw new Error("API_TOKEN não configurado. Defina em Arquivo → Propriedades do projeto → Propriedades do script.");
  var mock  = {
    postData: {
      contents: JSON.stringify({
        token:  token,
        action: "createPaciente",
        data: {
          primeiro_nome:          "Teste",
          telefone:               "",
          canal:                  "Indicação",
          origem:                 "Teste manual",
          consentimento_whatsapp: "Sim",
          responsavel:            "Adeildo",
        },
      }),
    },
  };
  Logger.log(doPost(mock).getContent());
}

/**
 * Teste manual de _registrarAtendimentoPastore — executar no editor do Apps
 * Script (nunca via HTTP). Usa "TESTE - APAGAR" no nome do paciente, seguindo
 * a regra de teste documentada em soprolife-sheets-sync: rodar o fluxo
 * completo com linha fictícia evidente e remover manualmente depois de
 * confirmar no painel.
 */
function _testRegistrarAtendimentoPastore() {
  var token = PropertiesService.getScriptProperties().getProperty("API_TOKEN");
  if (!token) throw new Error("API_TOKEN não configurado. Defina em Arquivo → Propriedades do projeto → Propriedades do script.");
  var mock = {
    postData: {
      contents: JSON.stringify({
        token:  token,
        action: "registrarAtendimentoPastore",
        data: {
          data_atendimento: Utilities.formatDate(new Date(), "America/Sao_Paulo", "yyyy-MM-dd"),
          unidade:          "Pastore Ipanema",
          dia_semana:       "Terça-feira",
          horario_inicio:   "08:00",
          horario_fim:      "12:00",
          origem:           "Pastore",
          paciente_nome:    "TESTE - APAGAR",
          paciente_whatsapp: "",
          tipo_exame:       "Espirometria",
          broncodilatador:  "Não",
          valor_cobrado:    "150",
          forma_pagamento:  "Pix",
          recebido_por:     "SoproLife",
          repasse_pastore:  "0",
          custo_insumo:     "10",
          custo_deslocamento: "0",
          custo_profissional: "0",
          outros_custos:      "0",
          status:               "Realizado",
          followup_status:      "A definir",
          consentimento_contato_futuro: "A definir",
          observacao_privada_minima: "Linha de teste — apagar após validar.",
        },
      }),
    },
  };
  Logger.log(doPost(mock).getContent());
}
