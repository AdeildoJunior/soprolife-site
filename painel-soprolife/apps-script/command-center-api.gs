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
  LEADS:        "Leads",
  PACIENTES:    "CRM Pacientes",
  ESPIROMETRIA: "CRM Espirometria",
  CONSULTAS:    "CRM Consultas",
  B2B_PCMSO:    "Base Prospecção B2B PCMSO",
  CRM_CLINICAS: "CRM Clinicas",
  FOLLOWUP_WA:  "Follow-up WhatsApp",
  LOG:          "Log Centro Comando",
};

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

  sheet.appendRow(_buildRow(sheet, {
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

  sheet.appendRow(_buildRow(sheet, {
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

  sheet.appendRow(_buildRow(sheet, {
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

  sheet.appendRow(_buildRow(sheet, {
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
  pcmsoSheet.appendRow(_buildRow(pcmsoSheet, {
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
  crmSheet.appendRow(_buildRow(crmSheet, {
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
    Object.keys(updates).forEach(function(col) {
      var colIdx = headers.indexOf(col);
      if (colIdx >= 0) sheet.getRange(rowIdx, colIdx + 1).setValue(updates[col]);
    });
    _logEntry({ acao: "registrarInteracaoClinica", status: "OK", aba: _SHEETS.CRM_CLINICAS, id: "", resumo: "Interação atualizada." });
    return _ok({ message: "Interação registrada com sucesso.", updated: true });
  }

  // Clínica não encontrada: cria nova linha
  var newId = _nextId(sheet, "CLI");
  sheet.appendRow(_buildRow(sheet, Object.assign({
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

  sheet.appendRow(_buildRow(sheet, {
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

// ── Helpers de infra ──────────────────────────────────────────────────────────

function _getOrCreateSheet(name) {
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(name);
  if (!sheet) sheet = ss.insertSheet(name);
  return sheet;
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
      sheet.appendRow(["timestamp", "acao", "status", "aba_afetada", "id_gerado", "email_executor", "resumo"]);
      sheet.getRange(1, 1, 1, 7).setFontWeight("bold");
    }

    var email = "";
    try { email = Session.getActiveUser().getEmail() || ""; } catch (_) {}

    sheet.appendRow([
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
