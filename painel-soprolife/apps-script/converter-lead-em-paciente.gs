/**
 * converter-lead-em-paciente.gs
 * Versão: 3.0  |  SoproLife Command Center
 *
 * Conversão MANUAL de lead selecionado → CRM Pacientes.
 *
 * DESIGN:
 *   - getUi() nunca é chamado diretamente na função principal.
 *   - _cvTentarUI() envolve getUi() em try/catch: se falhar (editor AS),
 *     registra orientação e aborta imediatamente.
 *   - Age somente na linha selecionada na aba Leads.
 *   - B2B (Clínicas / PCMSO): orienta manualmente → CRM Clínicas. Não cria CRM Pacientes.
 *   - Pessoa física: cria CRM Pacientes com mapeamento direto de campos.
 *   - NÃO cria CRM Espirometria nem CRM Consultas automaticamente.
 *     Se necessário no futuro, essas serão funções separadas.
 *   - Após converter, atualiza a etapa do lead para "Convertido em paciente".
 *   - Checa duplicata por telefone antes de criar.
 *
 * SEGURANÇA:
 *   - Sem ID ou URL de planilha real no código.
 *   - Sem dados reais hardcoded.
 *
 * COMO EXECUTAR:
 *   Menu SoproLife → Leads → Converter lead selecionado → CRM
 *
 * NOTA: Se executada pelo botão "Executar" no editor do Apps Script,
 *   a função abortará com log explicativo — isso é comportamento esperado.
 *   Sempre usar pelo menu SoproLife.
 */

// ── Nomes das abas ─────────────────────────────────────────────────────────────

var _CV_ABA_LEADS   = "Leads";
var _CV_ABA_PAC     = "CRM Pacientes";
var _CV_ABA_LOG     = "Log Centro Comando";

// ── Serviços B2B (não vão para CRM Pacientes) ─────────────────────────────────

var _CV_ROTA_B2B = [
  "clínicas", "clinicas",
  "pcmso / empresa", "pcmso", "empresa",
];

// ── Cabeçalho canônico do CRM Pacientes ───────────────────────────────────────

var _CV_HEADERS_PAC = [
  "paciente_id",
  "data_cadastro",
  "primeiro_nome",
  "telefone",
  "ultimo_servico",
  "status_relacionamento",
  "proximo_contato",
  "motivo_proximo_contato",
  "canal",
  "responsavel",
  "consentimento_whatsapp",
  "observacao_privada_minima",
];

// ── Função pública principal ───────────────────────────────────────────────────

/**
 * Converte o lead da linha selecionada para CRM Pacientes.
 * Acionada pelo menu SoproLife → Leads → Converter lead selecionado → CRM.
 *
 * NÃO chama SpreadsheetApp.getUi() diretamente.
 * Toda UI passa pelo helper _cvTentarUI(), que aborta se getUi() não estiver disponível.
 */
function converterLeadSelecionadoSoproLife() {
  // 1. UI: se não disponível (ex.: chamada pelo editor AS), aborta com log claro.
  var ui = _cvTentarUI();
  if (!ui) return;

  var ss = SpreadsheetApp.getActiveSpreadsheet();

  // 2. Busca a aba Leads explicitamente (não assume sheet ativa).
  var leadsSheet = ss.getSheetByName(_CV_ABA_LEADS);
  if (!leadsSheet) {
    Logger.log("ERRO: aba '" + _CV_ABA_LEADS + "' não encontrada.");
    ui.alert("Erro", "Aba '" + _CV_ABA_LEADS + "' não encontrada na planilha.", ui.ButtonSet.OK);
    return;
  }

  // 3. Verifica se o usuário está na aba Leads.
  var abaAtiva = ss.getActiveSheet();
  if (!abaAtiva || abaAtiva.getName() !== _CV_ABA_LEADS) {
    Logger.log("Selecione uma linha da aba Leads antes de converter.");
    ui.alert(
      "Aba incorreta",
      "Selecione uma linha da aba '" + _CV_ABA_LEADS + "' antes de executar esta função.",
      ui.ButtonSet.OK
    );
    return;
  }

  // 4. Obtém a linha ativa com segurança.
  var activeRange = abaAtiva.getActiveRange();
  if (!activeRange) {
    Logger.log("Selecione uma linha da aba Leads antes de converter.");
    ui.alert("Linha não selecionada", "Selecione uma linha da aba Leads antes de converter.", ui.ButtonSet.OK);
    return;
  }

  var linha = activeRange.getRow();
  if (linha <= 1) {
    Logger.log("Selecione uma linha da aba Leads antes de converter.");
    ui.alert("Linha inválida", "Selecione uma linha de dados (não o cabeçalho).", ui.ButtonSet.OK);
    return;
  }

  // 5. Lê os dados da linha selecionada.
  var lastCol = leadsSheet.getLastColumn();
  if (lastCol < 1) {
    Logger.log("Aba Leads sem colunas.");
    ui.alert("Erro", "Aba Leads parece estar vazia.", ui.ButtonSet.OK);
    return;
  }

  var header = leadsSheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var mapa = {};
  header.forEach(function(nome, idx) {
    var n = String(nome || "").trim();
    if (n && !mapa.hasOwnProperty(n)) mapa[n] = idx;
  });

  var rowData = leadsSheet.getRange(linha, 1, 1, lastCol).getValues()[0];

  // 6. Extrai campos relevantes do lead.
  var leadId       = _cvValArr(rowData, mapa, "lead_id")           || ("linha-" + linha);
  var nome         = _cvValArr(rowData, mapa, "nome");
  var tel          = _cvValArr(rowData, mapa, "telefone_whatsapp");
  var servico      = _cvValArr(rowData, mapa, "servico_interesse");
  var origem       = _cvValArr(rowData, mapa, "origem");
  var canal        = _cvValArr(rowData, mapa, "canal");
  var resp         = _cvValArr(rowData, mapa, "responsavel");
  var proxAcao     = _cvValArr(rowData, mapa, "proxima_acao");
  var dataProxAcao = _cvValArr(rowData, mapa, "data_proxima_acao");
  var obs          = _cvValArr(rowData, mapa, "observacao");

  var hoje = Utilities.formatDate(new Date(), "America/Sao_Paulo", "dd/MM/yyyy");

  var servicoNorm = String(servico).toLowerCase()
    .normalize("NFD").replace(/[̀-ͯ]/g, "");

  // 7. Roteamento B2B: não vai para CRM Pacientes.
  if (_cvEhB2B(servicoNorm)) {
    var msgB2B =
      "Este lead tem serviço '" + (servico || "B2B") + "', que é B2B (clínica ou empresa).\n\n" +
      "Leads B2B não são cadastrados no CRM Pacientes.\n\n" +
      "Ação recomendada:\n" +
      "  1. Vá para a aba 'CRM Clinicas' ou 'Base Prospecção B2B PCMSO'.\n" +
      "  2. Cadastre manualmente:\n" +
      "     • Nome: "    + (nome   || "(não informado)") + "\n" +
      "     • Serviço: " + (servico || "")              + "\n" +
      "     • Origem: "  + (origem  || "(não informado)");
    Logger.log("[" + leadId + "] B2B — orientação exibida. Não criado em CRM Pacientes.");
    ui.alert("Lead B2B — CRM Clínicas", msgB2B, ui.ButtonSet.OK);
    return;
  }

  // 8. Valida nome (obrigatório para pessoa física).
  if (!nome) {
    Logger.log("[" + leadId + "] Campo 'nome' vazio. Preencha antes de converter.");
    ui.alert("Dado ausente", "O campo 'nome' está vazio nesta linha. Preencha antes de converter.", ui.ButtonSet.OK);
    return;
  }

  // 9. Aviso se telefone ausente (não bloqueia, mas alerta).
  if (!tel) {
    var btnSemTel = ui.alert(
      "Telefone ausente",
      "O campo 'telefone_whatsapp' está vazio.\n\n" +
      "Sem telefone, a checagem de duplicata não funcionará.\n\n" +
      "Deseja continuar assim mesmo?",
      ui.ButtonSet.YES_NO
    );
    if (btnSemTel !== ui.Button.YES) {
      Logger.log("[" + leadId + "] Conversão cancelada: sem telefone.");
      return;
    }
  }

  // 10. Confirmação de conversão.
  var msgConf =
    "Converter este lead para CRM Pacientes?\n\n" +
    "Lead: "       + leadId                      + "\n" +
    "Nome: "       + nome                        + "\n" +
    "Telefone: "   + (tel     || "(vazio)")       + "\n" +
    "Serviço: "    + (servico || "(não informado)") + "\n\n" +
    "Após a conversão:\n" +
    "  • Entrada criada em CRM Pacientes (se não existir)\n" +
    "  • Etapa do lead atualizada para 'Convertido em paciente'\n" +
    "  • CRM Espirometria e CRM Consultas NÃO são criados aqui";

  var btnConf = ui.alert("Confirmar conversão", msgConf, ui.ButtonSet.YES_NO);
  if (btnConf !== ui.Button.YES) {
    Logger.log("[" + leadId + "] Conversão cancelada pelo usuário.");
    return;
  }

  // 11. Observação com origem da conversão.
  var obsComOrigem =
    "Convertido a partir da aba Leads (" + leadId + " · " + hoje + ")" +
    (obs ? "\n" + obs : "");

  // 12. Cria entrada em CRM Pacientes (se não existir por telefone).
  var criadoPac = _cvCriarCRMPacientes(ss, hoje, {
    nome:            nome,
    telefone:        tel,
    servico:         servico,
    canal:           canal,
    responsavel:     resp,
    proximo_contato: dataProxAcao,
    motivo:          proxAcao,
    obs:             obsComOrigem,
  });

  // 13. Atualiza a etapa do lead para "Convertido em paciente".
  var idxEtapa = mapa["etapa"];
  if (idxEtapa !== undefined) {
    leadsSheet.getRange(linha, idxEtapa + 1).setValue("Convertido em paciente");
    Logger.log("[" + leadId + "] Etapa atualizada para 'Convertido em paciente'.");
  } else {
    Logger.log("[" + leadId + "] AVISO: coluna 'etapa' não encontrada — etapa não atualizada.");
  }

  // 14. Registra no Log Centro Comando.
  _cvRegistrarLogManual(ss, hoje, leadId, nome, criadoPac);

  // 15. Resumo final ao usuário.
  var resumo =
    "Conversão concluída para: " + nome + " (" + leadId + ")\n\n" +
    (criadoPac
      ? "✔ CRM Pacientes: criado\n"
      : "— CRM Pacientes: telefone já existia — não duplicado\n") +
    "✔ Etapa do lead: 'Convertido em paciente'\n\n" +
    "Para registrar exame ou consulta realizada:\n" +
    "use as funções de CRM Espirometria / CRM Consultas separadamente.";

  ui.alert("Conversão concluída", resumo, ui.ButtonSet.OK);
}

// ── Helper de UI seguro ───────────────────────────────────────────────────────

/**
 * Tenta obter SpreadsheetApp.getUi().
 * Se falhar (contexto sem UI, ex.: editor do Apps Script ou trigger automático),
 * registra orientação e retorna null — a função chamadora deve checar e abortar.
 *
 * IMPORTANTE: esta é a ÚNICA função do arquivo que chama SpreadsheetApp.getUi().
 */
function _cvTentarUI() {
  try {
    return SpreadsheetApp.getUi();
  } catch (e) {
    Logger.log(
      "UI não disponível. Esta função deve ser executada pelo menu SoproLife. " +
      "Abra a planilha → SoproLife → Leads → Converter lead selecionado → CRM. " +
      "Erro: " + e.message
    );
    return null;
  }
}

// ── Criação em CRM Pacientes ───────────────────────────────────────────────────

/**
 * Mapeamento de campos (Leads → CRM Pacientes):
 *   nome              → primeiro_nome
 *   telefone_whatsapp → telefone
 *   servico_interesse → ultimo_servico
 *   canal             → canal
 *   responsavel       → responsavel
 *   data_proxima_acao → proximo_contato
 *   proxima_acao      → motivo_proximo_contato
 *   observacao        → observacao_privada_minima (prefixada com origem)
 */
function _cvCriarCRMPacientes(ss, hoje, d) {
  var sheet = ss.getSheetByName(_CV_ABA_PAC);
  if (!sheet) {
    Logger.log("AVISO: aba '" + _CV_ABA_PAC + "' não encontrada. CRM Pacientes não criado.");
    return false;
  }

  _cvGarantirCabecalho(sheet, _CV_HEADERS_PAC);

  // Checa duplicata por telefone (dígitos apenas).
  if (d.telefone) {
    var telDigitos = d.telefone.replace(/\D/g, "");
    var telefonesExistentes = _cvConjuntoTelefones(ss, _CV_ABA_PAC);
    if (telefonesExistentes.has(telDigitos)) {
      Logger.log("CRM Pacientes: telefone '" + d.telefone + "' já existe — não duplicado. (" + d.nome + ")");
      return false;
    }
  }

  // Gera ID sequencial.
  var lastRow  = sheet.getLastRow();
  var seq      = String(Math.max(lastRow, 1)).padStart(3, "0");
  var hojeComp = hoje.replace(/\//g, "");
  var id       = "PAC-" + hojeComp + "-" + seq;

  sheet.appendRow([
    id,
    hoje,
    d.nome             || "",
    d.telefone         || "",
    d.servico          || "",
    "Em acompanhamento",
    d.proximo_contato  || "",
    d.motivo           || "",
    d.canal            || "",
    d.responsavel      || "",
    "",
    d.obs              || "",
  ]);

  Logger.log("CRM Pacientes: criado " + id + " — " + d.nome);
  return true;
}

// ── Roteamento B2B ────────────────────────────────────────────────────────────

function _cvEhB2B(servicoNorm) {
  return _CV_ROTA_B2B.some(function(t) { return servicoNorm.indexOf(t) >= 0; });
}

// ── Helpers de leitura ─────────────────────────────────────────────────────────

/**
 * Lê valor de uma coluna pelo nome, usando array de dados + mapa de índices.
 */
function _cvValArr(row, mapa, nomeCampo) {
  var idx = mapa[nomeCampo];
  if (idx === undefined) return "";
  var v = row[idx];
  return (v === undefined || v === null) ? "" : String(v).trim();
}

/**
 * Lê a aba e retorna { rows, mapa } onde mapa é { nome_coluna: índice }.
 */
function _cvLerAbaComMapa(sheet) {
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  if (lastRow < 1 || lastCol < 1) return null;

  var header = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var mapa = {};
  header.forEach(function(nome, idx) {
    var n = String(nome || "").trim();
    if (n && !mapa.hasOwnProperty(n)) mapa[n] = idx;
  });

  if (lastRow < 2) return { rows: [], mapa: mapa };
  var dados = sheet.getRange(2, 1, lastRow - 1, lastCol).getValues();
  return { rows: dados, mapa: mapa };
}

/**
 * Retorna Set de dígitos de telefone presentes em uma aba (campo "telefone").
 */
function _cvConjuntoTelefones(ss, nomeAba) {
  var conj = _cvSetFallback();
  var sheet = ss.getSheetByName(nomeAba);
  if (!sheet) return conj;

  var data = _cvLerAbaComMapa(sheet);
  if (!data) return conj;

  data.rows.forEach(function(row) {
    var idx = data.mapa["telefone"];
    if (idx === undefined) idx = data.mapa["telefone_whatsapp"];
    if (idx === undefined) return;
    var digits = String(row[idx] || "").replace(/\D/g, "");
    if (digits) conj.add(digits);
  });

  return conj;
}

function _cvNormalizar(s) {
  return String(s || "").toLowerCase()
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/\s+/g, " ").trim();
}

/**
 * Fallback para ambientes Apps Script sem Set nativo.
 */
function _cvSetFallback() {
  var store = {};
  return {
    has: function(k) { return store.hasOwnProperty(k); },
    add: function(k) { store[k] = true; },
  };
}

// ── Garantir cabeçalho ────────────────────────────────────────────────────────

function _cvGarantirCabecalho(sheet, headers) {
  var primeiraLinha = sheet.getRange(1, 1, 1, headers.length).getValues()[0];
  var temCabecalho  = primeiraLinha.some(function(c) { return String(c || "").trim() !== ""; });
  if (!temCabecalho) {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    sheet.getRange(1, 1, 1, headers.length)
      .setFontWeight("bold").setFontColor("#ffffff").setBackground("#08243d");
    Logger.log("Cabeçalho criado em: " + sheet.getName());
  }
}

// ── Log Centro Comando ─────────────────────────────────────────────────────────

function _cvRegistrarLogManual(ss, hoje, leadId, nome, criadoPac) {
  var abaLog = ss.getSheetByName(_CV_ABA_LOG);
  if (!abaLog) {
    Logger.log("AVISO: aba '" + _CV_ABA_LOG + "' não encontrada. Log não registrado.");
    return;
  }

  if (abaLog.getLastRow() === 0 || abaLog.getRange(1, 1).getValue() === "") {
    abaLog.getRange(1, 1, 1, 4).setValues([["data", "funcao", "lead", "resultado"]]);
  }

  var resultado =
    "PAC=" + (criadoPac ? "criado" : "ja-existia") +
    " | etapa=Convertido em paciente";

  abaLog.appendRow([hoje, "converterLeadSelecionadoSoproLife", leadId + " — " + nome, resultado]);
  Logger.log("Log registrado: " + leadId + " | " + resultado);
}
