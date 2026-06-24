/**
 * converter-lead-em-paciente.gs
 * Versão: 4.2  |  SoproLife Command Center
 *
 * AUTOMAÇÃO (acionador instalável "Ao editar" — fluxo principal):
 *   Quando a coluna 'etapa' da aba Leads é alterada para um dos valores abaixo,
 *   o lead é convertido automaticamente e movido para 'Leads Convertidos':
 *
 *     "Realizou consulta"                → CRM Pacientes + CRM Consultas
 *     "Realizou espirometria"            → CRM Pacientes + CRM Espirometria
 *     "Realizou consulta e espirometria" → CRM Pacientes + CRM Consultas + CRM Espirometria
 *
 * CONVERSÃO MANUAL (menu SoproLife → Leads → Converter lead selecionado → CRM):
 *   Mesma lógica aplicada à linha atualmente selecionada. Exibe confirmação via UI.
 *
 * COMPATIBILIDADE COM CABEÇALHOS EXISTENTES:
 *   As funções de escrita nos CRMs lêem o cabeçalho atual de cada aba e mapeiam
 *   os campos pelo nome. Funciona com esquemas antigos e novos:
 *     - Antigo: primeiro_nome / telefone / data_cadastro / data_entrada / ...
 *     - Novo:   nome / telefone_whatsapp / data_registro / ...
 *   Se a aba não existir ou estiver vazia, é criada com o cabeçalho padrão novo.
 *
 * REGRAS:
 *   - LockService previne execução duplicada.
 *   - B2B (Clínicas / PCMSO): registra no log, não cria CRM Pacientes.
 *   - Não duplica paciente se o telefone já existir em CRM Pacientes.
 *   - Não inventa data de exame, médica, laudo — deixa campos em branco.
 *   - Sem getUi().alert() na função de automação.
 *   - Logger.log + aba 'Log Conversões Leads' para rastreabilidade.
 *   - Log gravado ANTES de remover o lead — histórico garantido mesmo em erro parcial.
 *
 * SEGURANÇA:
 *   - Sem ID ou URL de planilha real no código.
 *   - Sem dados reais hardcoded.
 *
 * COMO INSTALAR O ACIONADOR (automático):
 *   Este arquivo NÃO usa function onEdit() simples para evitar conflito com
 *   outros arquivos do projeto (ex.: Código.gs).
 *   Use um acionador instalável:
 *     1. Extensões → Apps Script → Acionadores (ícone de relógio)
 *     2. Adicionar acionador
 *     3. Função: onEditConversaoLeadsSoproLife
 *     4. Fonte do evento: Da planilha
 *     5. Tipo de evento: Ao editar
 *     6. Salvar
 *
 * COMO USAR (manual):
 *   Menu SoproLife → Leads → Converter lead selecionado → CRM
 */

// ── Nomes das abas ────────────────────────────────────────────────────────────

var _CV_ABA_LEADS       = "Leads";
var _CV_ABA_PAC         = "CRM Pacientes";
var _CV_ABA_CON         = "CRM Consultas";
var _CV_ABA_ESM         = "CRM Espirometria";
var _CV_ABA_LOG         = "Log Conversões Leads";
var _CV_ABA_CONVERTIDOS = "Leads Convertidos";
var _CV_ABA_FOLLOWUP    = "Follow-up WhatsApp";

// ── Etapas de conversão ───────────────────────────────────────────────────────

var _CV_ETAPA_CONSULTA     = "Realizou consulta";
var _CV_ETAPA_ESPIROMETRIA = "Realizou espirometria";
var _CV_ETAPA_AMBOS        = "Realizou consulta e espirometria";

var _CV_ETAPAS_CONVERSAO = [
  _CV_ETAPA_CONSULTA,
  _CV_ETAPA_ESPIROMETRIA,
  _CV_ETAPA_AMBOS,
];

// ── Serviços B2B — não geram CRM Pacientes ───────────────────────────────────

var _CV_ROTA_B2B = [
  "clínicas", "clinicas",
  "pcmso / empresa", "pcmso", "empresa",
];

// ── Cabeçalho canônico da aba Leads (11 colunas) ──────────────────────────────
// Usado para criar 'Leads Convertidos' com o mesmo esquema.

var _CV_LEADS_CABECALHO = [
  "lead_id",
  "data_contato",
  "nome",
  "telefone_whatsapp",
  "servico_interesse",
  "origem",
  "etapa",
  "responsavel",
  "proxima_acao",
  "data_proxima_acao",
  "observacao",
];

// ── Cabeçalhos padrão para abas CRM criadas do zero ──────────────────────────
// Usados apenas quando a aba não existe ou está vazia.
// Se a aba já existir com cabeçalhos diferentes (esquema antigo),
// os dados são mapeados pelo NOME da coluna — não sobrescreve a estrutura existente.

var _CV_DEFAULT_HEADERS_PAC = [
  "paciente_id",
  "data_registro",
  "nome",
  "telefone_whatsapp",
  "origem",
  "servico",
  "status",
  "responsavel",
  "data_proximo_contato",
  "motivo_proximo_contato",
  "observacao",
  "ultimo_servico",
  "status_relacionamento",
  "historico_resumido",
];

var _CV_DEFAULT_HEADERS_CON = [
  "consulta_id",
  "data_registro",
  "nome",
  "telefone_whatsapp",
  "origem",
  "servico",
  "status",
  "responsavel",
  "data_proximo_contato",
  "motivo_proximo_contato",
  "observacao",
  "tipo_consulta",
  "medica",
  "data_consulta",
];

var _CV_DEFAULT_HEADERS_ESM = [
  "exame_id",
  "data_registro",
  "nome",
  "telefone_whatsapp",
  "origem",
  "servico",
  "status",
  "responsavel",
  "data_proximo_contato",
  "motivo_proximo_contato",
  "observacao",
  "tipo_exame",
  "data_exame",
  "status_exame",
];

var _CV_DEFAULT_HEADERS_FUP = [
  "followup_id",
  "data_criacao",
  "primeiro_nome",
  "telefone",
  "tipo_mensagem",
  "data_prevista",
  "status",
  "canal",
  "responsavel",
  "template_usado",
  "consentimento_whatsapp",
  "observacao_privada_minima",
];

var _CV_HEADERS_LOG = [
  "data_hora",
  "lead_id",
  "nome",
  "etapa",
  "destinos_criados",
  "resultado",
  "followup_status",
];

// ── Automação por edição (acionador instalável) ───────────────────────────────

/**
 * Função de automação "Ao editar" — deve ser registrada como acionador instalável,
 * NÃO como trigger simples onEdit(), para não conflitar com outros arquivos do projeto.
 *
 * Como instalar: Extensões → Apps Script → Acionadores → Adicionar acionador
 *   Função: onEditConversaoLeadsSoproLife | Evento: Da planilha → Ao editar
 *
 * Sai imediatamente se a edição não for relevante (sem chamadas de API extras).
 * NÃO chama getUi() — segura para acionadores automáticos.
 */
function onEditConversaoLeadsSoproLife(e) {
  if (!e || !e.range) return;

  // Verificações rápidas sem chamadas de API
  var sheet = e.range.getSheet();
  if (sheet.getName() !== _CV_ABA_LEADS) return;
  if (e.range.rowStart <= 1) return;

  var novoValor = String(e.value || "").trim();
  if (_CV_ETAPAS_CONVERSAO.indexOf(novoValor) < 0) return;

  // Verifica que a coluna editada é 'etapa'
  var lastCol = sheet.getLastColumn();
  if (lastCol < 1) return;
  var header    = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var etapaIdx  = -1;
  for (var i = 0; i < header.length; i++) {
    if (String(header[i] || "").trim() === "etapa") { etapaIdx = i; break; }
  }
  if (etapaIdx < 0 || e.range.columnStart !== etapaIdx + 1) return;

  // Lock para evitar execução duplicada
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(15000);
  } catch (err) {
    Logger.log("onEditConversaoLeads: lock não obtido após 15s — " + err.message);
    return;
  }

  try {
    _cvConverterLeadCore(e.source, sheet, e.range.rowStart, novoValor);
  } catch (err) {
    Logger.log("onEditConversaoLeads: erro — " + err.message);
  } finally {
    lock.releaseLock();
  }
}

// ── Conversão manual (menu) ───────────────────────────────────────────────────

/**
 * Acionado por: SoproLife → Leads → Converter lead selecionado → CRM
 * Útil quando o onEdit não disparou ou para re-converter com etapa já definida.
 */
function converterLeadSelecionadoSoproLife() {
  var ui = _cvTentarUI();
  if (!ui) return;

  var ss         = SpreadsheetApp.getActiveSpreadsheet();
  var abaAtiva   = ss.getActiveSheet();
  var leadsSheet = ss.getSheetByName(_CV_ABA_LEADS);

  if (!leadsSheet) {
    ui.alert("Erro", "Aba '" + _CV_ABA_LEADS + "' não encontrada.", ui.ButtonSet.OK);
    return;
  }

  if (!abaAtiva || abaAtiva.getName() !== _CV_ABA_LEADS) {
    ui.alert(
      "Aba incorreta",
      "Selecione uma linha da aba '" + _CV_ABA_LEADS + "' antes de executar.",
      ui.ButtonSet.OK
    );
    return;
  }

  var activeRange = abaAtiva.getActiveRange();
  if (!activeRange) {
    ui.alert("Linha não selecionada", "Selecione uma linha da aba Leads.", ui.ButtonSet.OK);
    return;
  }

  var linha = activeRange.getRow();
  if (linha <= 1) {
    ui.alert("Linha inválida", "Selecione uma linha de dados (não o cabeçalho).", ui.ButtonSet.OK);
    return;
  }

  var lastCol = leadsSheet.getLastColumn();
  if (lastCol < 1) {
    ui.alert("Erro", "Aba Leads parece estar vazia.", ui.ButtonSet.OK);
    return;
  }
  var header  = leadsSheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var mapa    = _cvMapearHeader(header);
  var rowData = leadsSheet.getRange(linha, 1, 1, lastCol).getValues()[0];

  var etapa  = _cvVal(rowData, mapa, "etapa");
  var nome   = _cvVal(rowData, mapa, "nome");
  var leadId = _cvVal(rowData, mapa, "lead_id") || ("linha-" + linha);

  if (_CV_ETAPAS_CONVERSAO.indexOf(etapa) < 0) {
    ui.alert(
      "Etapa inválida para conversão",
      "A etapa atual deste lead é: '" + (etapa || "(vazia)") + "'\n\n" +
      "Para converter, mude a etapa para uma destas:\n" +
      "  • Realizou consulta\n" +
      "  • Realizou espirometria\n" +
      "  • Realizou consulta e espirometria\n\n" +
      "A conversão acontece automaticamente ao mudar a etapa.",
      ui.ButtonSet.OK
    );
    return;
  }

  var confirmMsg =
    "Converter este lead para os CRMs de atendimento?\n\n" +
    "Lead:  " + leadId               + "\n" +
    "Nome:  " + (nome || "(vazio)") + "\n" +
    "Etapa: " + etapa               + "\n\n" +
    "Após a conversão, o lead será movido para 'Leads Convertidos'.";

  var btn = ui.alert("Confirmar conversão", confirmMsg, ui.ButtonSet.YES_NO);
  if (btn !== ui.Button.YES) {
    Logger.log("[" + leadId + "] Conversão manual cancelada pelo usuário.");
    return;
  }

  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(15000);
  } catch (err) {
    ui.alert(
      "Erro de concorrência",
      "Outra operação está em andamento. Aguarde e tente novamente.",
      ui.ButtonSet.OK
    );
    return;
  }

  try {
    _cvConverterLeadCore(ss, leadsSheet, linha, etapa);
    ui.alert(
      "Conversão concluída",
      "Lead '" + leadId + "' convertido com sucesso.\n" +
      "Movido para '" + _CV_ABA_CONVERTIDOS + "'.\n" +
      "Log em: " + _CV_ABA_LOG,
      ui.ButtonSet.OK
    );
  } catch (err) {
    Logger.log("[" + leadId + "] Erro na conversão manual: " + err.message);
    ui.alert("Erro na conversão", err.message, ui.ButtonSet.OK);
  } finally {
    lock.releaseLock();
  }
}

// ── Núcleo da conversão ───────────────────────────────────────────────────────

/**
 * Lógica central. Usada pelo onEdit e pela função manual.
 * Não chama getUi() — segura para triggers automáticos.
 */
function _cvConverterLeadCore(ss, leadsSheet, linha, etapa) {
  var hoje     = Utilities.formatDate(new Date(), "America/Sao_Paulo", "dd/MM/yyyy");
  var hojeComp = Utilities.formatDate(new Date(), "America/Sao_Paulo", "yyyyMMdd");
  var agora    = Utilities.formatDate(new Date(), "America/Sao_Paulo", "dd/MM/yyyy HH:mm:ss");

  var lastCol = leadsSheet.getLastColumn();
  if (lastCol < 1) {
    Logger.log("ERRO: aba Leads sem colunas — conversão abortada.");
    return;
  }
  var header  = leadsSheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var mapa    = _cvMapearHeader(header);
  var rowData = leadsSheet.getRange(linha, 1, 1, lastCol).getValues()[0];

  var leadId   = _cvVal(rowData, mapa, "lead_id") || ("linha-" + linha);
  var nome     = _cvVal(rowData, mapa, "nome");
  var tel      = _cvVal(rowData, mapa, "telefone_whatsapp");
  var servico  = _cvVal(rowData, mapa, "servico_interesse");
  var origem   = _cvVal(rowData, mapa, "origem");
  var resp     = _cvVal(rowData, mapa, "responsavel");
  var proxAcao = _cvVal(rowData, mapa, "proxima_acao");
  var dataProx = _cvVal(rowData, mapa, "data_proxima_acao");
  var obs      = _cvVal(rowData, mapa, "observacao");

  // Data de conversão: usa data_contato do lead; cai em hoje se vazia ou fora do padrão dd/MM/yyyy
  var dataContato   = _cvVal(rowData, mapa, "data_contato");
  var dataConversao = /^\d{2}\/\d{2}\/\d{4}$/.test(dataContato) ? dataContato : hoje;

  var obsConv =
    "Convertido de lead " + leadId + " - " + dataConversao +
    (obs ? ". " + obs : "");

  var servicoNorm = String(servico || "").toLowerCase()
    .normalize("NFD").replace(/[̀-ͯ]/g, "");
  var ehB2B = _CV_ROTA_B2B.some(function(t) {
    return servicoNorm.indexOf(t) >= 0;
  });

  var log = {
    agora:          agora,
    leadId:         leadId,
    nome:           nome || "(sem nome)",
    etapa:          etapa,
    destinos:       [],
    resultado:      "",
    followupStatus: "",
  };

  Logger.log("[" + leadId + "] Conversão iniciada — etapa: " + etapa);

  // B2B: orienta cadastro manual, move para Leads Convertidos
  if (ehB2B) {
    log.resultado =
      "B2B — não convertido para CRM Pacientes. " +
      "Cadastrar manualmente em CRM Clínicas ou Base B2B/PCMSO.";
    log.destinos.push("CRM Clínicas/B2B (manual)");
    Logger.log("[" + leadId + "] B2B — " + log.resultado);
    _cvRegistrarLogConversoes(ss, log);
    _cvMoverParaConvertidos(ss, leadsSheet, linha, rowData);
    return;
  }

  // Nome obrigatório para pessoa física
  if (!nome) {
    log.resultado = "ERRO: campo 'nome' vazio — conversão abortada.";
    Logger.log("[" + leadId + "] " + log.resultado);
    _cvRegistrarLogConversoes(ss, log);
    return;
  }

  // Próximo contato = dataConversao + 5 meses
  var proxContato5m = _cvAddMeses(dataConversao, 5) || dataConversao;

  // Determina ultimo_servico e motivo de follow-up por etapa
  var ultimoServico, motivoFup;
  if (etapa === _CV_ETAPA_ESPIROMETRIA) {
    ultimoServico = "Espirometria";
    motivoFup     = "Follow-up pós-espirometria";
  } else if (etapa === _CV_ETAPA_CONSULTA) {
    ultimoServico = "Consulta";
    motivoFup     = "Follow-up pós-consulta";
  } else {
    ultimoServico = "Consulta + Espirometria";
    motivoFup     = "Follow-up pós-consulta e pós-espirometria";
  }

  // Pacote de dados comuns a todos os CRMs
  var d = {
    nome:          nome,
    tel:           tel,
    servico:       servico,
    origem:        origem || "Não informado",
    resp:          resp   || "Adeildo",
    dataProx:      dataProx,
    proxAcao:      proxAcao,
    obs:           obsConv,
    dataConversao: dataConversao,
    proxContato5m: proxContato5m,
  };

  // CRM Pacientes (verifica duplicata por telefone)
  var resPac = _cvCriarCRMPacientes(ss, hojeComp, dataConversao, d, {
    statusPadrao:  "Ativo",
    ultimoServico: ultimoServico,
    motivo:        motivoFup,
    dataProxCalc:  proxContato5m,
  });
  log.destinos.push(
    "CRM Pacientes: " + (resPac.criado ? resPac.id : "telefone duplicado — não criado")
  );

  // CRM Consultas
  if (etapa === _CV_ETAPA_CONSULTA || etapa === _CV_ETAPA_AMBOS) {
    var resCon = _cvCriarCRMConsultas(ss, hojeComp, dataConversao, d, {
      statusPadrao: "Consulta realizada",
      motivo:       "Follow-up pós-consulta",
      dataProxCalc: proxContato5m,
      dataConsulta: dataConversao,
    });
    log.destinos.push("CRM Consultas: " + resCon.id);
  }

  // CRM Espirometria
  if (etapa === _CV_ETAPA_ESPIROMETRIA || etapa === _CV_ETAPA_AMBOS) {
    var resEsm = _cvCriarCRMEspirometria(ss, hojeComp, dataConversao, d, {
      statusPadrao: "Exame realizado",
      servico:      "Espirometria",
      tipoExame:    "Espirometria",
      statusExame:  "Exame realizado",
      motivo:       "Follow-up pós-espirometria",
      dataProxCalc: proxContato5m,
      dataExame:    dataConversao,
    });
    log.destinos.push("CRM Espirometria: " + resEsm.id);
  }

  // Follow-up WhatsApp (apenas leads pessoa física com telefone)
  try {
    var resFup = _cvCriarFollowupWhatsapp(ss, hojeComp, dataConversao, d, leadId, etapa);
    log.followupStatus = resFup.status + (resFup.id ? " (" + resFup.id + ")" : "");
    log.destinos.push("Follow-up WhatsApp: " + log.followupStatus);
  } catch (errFup) {
    log.followupStatus = "erro: " + errFup.message;
    log.destinos.push("Follow-up WhatsApp: erro");
    Logger.log("[" + leadId + "] Follow-up erro: " + errFup.message);
  }

  log.resultado = "Convertido com sucesso.";
  Logger.log("[" + leadId + "] " + log.resultado + " | " + log.destinos.join(", "));

  // Log antes de remover — garante rastreabilidade
  _cvRegistrarLogConversoes(ss, log);

  // Move para Leads Convertidos e remove da aba Leads
  _cvMoverParaConvertidos(ss, leadsSheet, linha, rowData);
}

// ── Criação nos CRMs (compatível com esquemas antigo e novo) ──────────────────

function _cvCriarCRMPacientes(ss, hojeComp, hoje, d, extras) {
  extras = extras || {};
  var sheet = _cvObterOuCriarAba(ss, _CV_ABA_PAC, _CV_DEFAULT_HEADERS_PAC);

  // Checa duplicata por telefone
  if (d.tel) {
    var telDigitos = d.tel.replace(/\D/g, "");
    if (telDigitos && _cvConjuntoTelefones(ss, _CV_ABA_PAC).has(telDigitos)) {
      Logger.log("CRM Pacientes: telefone já existe — não duplicado. (" + d.nome + ")");
      return { id: "(duplicado)", criado: false };
    }
  }

  var lastCol = sheet.getLastColumn();
  var headers = lastCol > 0
    ? sheet.getRange(1, 1, 1, lastCol).getValues()[0]
    : _CV_DEFAULT_HEADERS_PAC;
  var id = "PAC-" + hojeComp + "-" + String(Math.max(sheet.getLastRow(), 1)).padStart(3, "0");

  sheet.appendRow(_cvConstruirLinhaParaCRM(headers, d, id, hoje, extras));
  Logger.log("CRM Pacientes: criado " + id + " — " + d.nome);
  return { id: id, criado: true };
}

function _cvCriarCRMConsultas(ss, hojeComp, hoje, d, extras) {
  extras = extras || {};
  var sheet = _cvObterOuCriarAba(ss, _CV_ABA_CON, _CV_DEFAULT_HEADERS_CON);

  var lastCol = sheet.getLastColumn();
  var headers = lastCol > 0
    ? sheet.getRange(1, 1, 1, lastCol).getValues()[0]
    : _CV_DEFAULT_HEADERS_CON;
  var id = "CON-" + hojeComp + "-" + String(Math.max(sheet.getLastRow(), 1)).padStart(3, "0");

  sheet.appendRow(_cvConstruirLinhaParaCRM(headers, d, id, hoje, extras));
  Logger.log("CRM Consultas: criado " + id + " — " + d.nome);
  return { id: id };
}

function _cvCriarCRMEspirometria(ss, hojeComp, hoje, d, extras) {
  extras = extras || {};
  var sheet = _cvObterOuCriarAba(ss, _CV_ABA_ESM, _CV_DEFAULT_HEADERS_ESM);

  var lastCol = sheet.getLastColumn();
  var headers = lastCol > 0
    ? sheet.getRange(1, 1, 1, lastCol).getValues()[0]
    : _CV_DEFAULT_HEADERS_ESM;
  var id = "ESM-" + hojeComp + "-" + String(Math.max(sheet.getLastRow(), 1)).padStart(3, "0");

  sheet.appendRow(_cvConstruirLinhaParaCRM(headers, d, id, hoje, extras));
  Logger.log("CRM Espirometria: criado " + id + " — " + d.nome);
  return { id: id };
}

// ── Follow-up WhatsApp ────────────────────────────────────────────────────────

/**
 * Cria linha na aba 'Follow-up WhatsApp' após conversão de lead.
 * Não cria follow-up se: sem telefone, B2B (verificado pelo chamador), ou duplicata.
 * Retorna { status, id? } — status: "criado" | "ja_existia" | "sem_telefone".
 */
function _cvCriarFollowupWhatsapp(ss, hojeComp, hoje, d, leadId, etapa) {
  if (!d.tel) {
    Logger.log("[" + leadId + "] Follow-up: sem telefone — não criado.");
    return { status: "sem_telefone" };
  }

  var tipoMsg;
  if (etapa === _CV_ETAPA_CONSULTA) {
    tipoMsg = "Follow-up pós-consulta";
  } else if (etapa === _CV_ETAPA_ESPIROMETRIA) {
    tipoMsg = "Follow-up pós-espirometria";
  } else {
    tipoMsg = "Follow-up pós-consulta e pós-espirometria";
  }

  var dataPrevista = d.proxContato5m || (function() {
    var dataObj = new Date();
    dataObj.setMonth(dataObj.getMonth() + 5);
    return Utilities.formatDate(dataObj, "America/Sao_Paulo", "dd/MM/yyyy");
  })();

  var sheet = _cvObterOuCriarAba(ss, _CV_ABA_FOLLOWUP, _CV_DEFAULT_HEADERS_FUP);

  if (_cvFollowupJaExiste(sheet, d.tel, tipoMsg, leadId)) {
    Logger.log("[" + leadId + "] Follow-up: já existe — não duplicado. (" + tipoMsg + ")");
    return { status: "ja_existia" };
  }

  var fupId = "FUP-" + hojeComp + "-" + String(Math.max(sheet.getLastRow(), 1)).padStart(3, "0");

  var lastCol = sheet.getLastColumn();
  var headers = lastCol > 0
    ? sheet.getRange(1, 1, 1, lastCol).getValues()[0]
    : _CV_DEFAULT_HEADERS_FUP;

  var v = {
    "followup_id":               fupId,
    "data_criacao":              hoje,
    "primeiro_nome":             d.nome || "",
    "telefone":                  d.tel  || "",
    "tipo_mensagem":             tipoMsg,
    "data_prevista":             dataPrevista,
    "status":                    "Pendente",
    "canal":                     "WhatsApp",
    "responsavel":               d.resp,
    "template_usado":            "Follow-up 5 meses",
    "consentimento_whatsapp":    "Confirmado",
    "observacao_privada_minima": "Criado automaticamente a partir do lead " + leadId + ". Revisar antes de enviar.",
  };

  var linha = headers.map(function(col) {
    var key = String(col || "").trim();
    return v.hasOwnProperty(key) ? v[key] : "";
  });

  sheet.appendRow(linha);
  Logger.log("[" + leadId + "] Follow-up criado: " + fupId + " — " + tipoMsg);
  return { status: "criado", id: fupId };
}

/**
 * Verifica se já existe follow-up pendente para o mesmo telefone, tipo_mensagem e lead_id.
 * Evita duplicidade quando a conversão é re-executada.
 */
function _cvFollowupJaExiste(sheet, tel, tipoMsg, leadId) {
  if (sheet.getLastRow() < 2) return false;

  var lastCol = sheet.getLastColumn();
  if (lastCol < 1) return false;

  var header = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var mapa   = _cvMapearHeader(header);

  var idxTel    = mapa["telefone"];
  var idxTipo   = mapa["tipo_mensagem"];
  var idxStatus = mapa["status"];
  var idxObs    = mapa["observacao_privada_minima"];

  if (idxTel === undefined || idxTipo === undefined) return false;

  var telDigitos = String(tel).replace(/\D/g, "");
  var dados = sheet.getRange(2, 1, sheet.getLastRow() - 1, lastCol).getValues();

  return dados.some(function(row) {
    var rowTel    = String(row[idxTel]  || "").replace(/\D/g, "");
    var rowTipo   = String(row[idxTipo] || "").trim();
    var rowStatus = idxStatus !== undefined ? String(row[idxStatus] || "").trim() : "";
    var rowObs    = idxObs    !== undefined ? String(row[idxObs]    || "")        : "";
    return rowTel === telDigitos &&
           rowTipo === tipoMsg &&
           rowStatus === "Pendente" &&
           rowObs.indexOf(leadId) >= 0;
  });
}

// ── Mover para Leads Convertidos ──────────────────────────────────────────────

function _cvMoverParaConvertidos(ss, leadsSheet, linha, rowData) {
  var destSheet = ss.getSheetByName(_CV_ABA_CONVERTIDOS);

  if (!destSheet) {
    destSheet = ss.insertSheet(_CV_ABA_CONVERTIDOS);
    destSheet.getRange(1, 1, 1, _CV_LEADS_CABECALHO.length)
      .setValues([_CV_LEADS_CABECALHO])
      .setFontWeight("bold")
      .setFontColor("#ffffff")
      .setBackground("#08243d");
    Logger.log("Aba '" + _CV_ABA_CONVERTIDOS + "' criada.");
  } else if (
    destSheet.getLastRow() === 0 ||
    String(destSheet.getRange(1, 1).getValue() || "").trim() === ""
  ) {
    destSheet.getRange(1, 1, 1, _CV_LEADS_CABECALHO.length)
      .setValues([_CV_LEADS_CABECALHO]);
  }

  // Ajusta tamanho de rowData ao cabeçalho de destino
  var row = _CV_LEADS_CABECALHO.map(function(_, idx) {
    return idx < rowData.length ? rowData[idx] : "";
  });

  destSheet.appendRow(row);
  leadsSheet.deleteRow(linha);
  Logger.log(
    "Lead movido para '" + _CV_ABA_CONVERTIDOS +
    "' — linha " + linha + " removida de Leads."
  );
}

// ── Log de Conversões ─────────────────────────────────────────────────────────

function _cvRegistrarLogConversoes(ss, log) {
  var sheet = _cvGarantirAba(ss, _CV_ABA_LOG, _CV_HEADERS_LOG);
  sheet.appendRow([
    log.agora,
    log.leadId,
    log.nome,
    log.etapa,
    log.destinos.join("; "),
    log.resultado,
    log.followupStatus || "",
  ]);
  Logger.log(
    "Log: " + log.leadId +
    " | etapa: " + log.etapa +
    " | resultado: " + log.resultado
  );
}

// ── Helpers gerais ────────────────────────────────────────────────────────────

/**
 * Adiciona N meses a uma data em formato dd/MM/yyyy.
 * Retorna string dd/MM/yyyy, ou "" se a data não for parseável.
 */
function _cvAddMeses(dataStr, meses) {
  if (!dataStr) return "";
  var partes = dataStr.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (!partes) return "";
  var d = new Date(parseInt(partes[3]), parseInt(partes[2]) - 1, parseInt(partes[1]));
  d.setMonth(d.getMonth() + meses);
  return (
    String(d.getDate()).padStart(2, "0")   + "/" +
    String(d.getMonth() + 1).padStart(2, "0") + "/" +
    d.getFullYear()
  );
}

/**
 * Mapeador de dados Lead → linha CRM.
 * Lê o cabeçalho ATUAL da aba e mapeia cada coluna pelo nome.
 * Compatível com esquemas antigos (primeiro_nome, telefone, data_cadastro...)
 * e novos (nome, telefone_whatsapp, data_registro...).
 *
 * @param {Array}  headers  Cabeçalho atual da aba CRM (array de strings).
 * @param {Object} d        Dados do lead: nome, tel, servico, origem, resp, obs, ...
 * @param {string} id       ID gerado para o novo registro.
 * @param {string} hoje     Data de conversão em dd/MM/yyyy.
 * @param {Object} [extras] Extras por etapa: statusPadrao, servico, tipoExame,
 *                          ultimoServico, motivo, dataProxCalc, dataExame,
 *                          statusExame, dataConsulta.
 * @returns {Array} Linha de valores alinhada ao cabeçalho.
 */
function _cvConstruirLinhaParaCRM(headers, d, id, hoje, extras) {
  extras = extras || {};

  var v = {
    // IDs — apenas o correspondente existirá no cabeçalho de cada aba
    "paciente_id":               id,
    "consulta_id":               id,
    "exame_id":                  id,

    // Datas de registro (nomes antigos e novos) — usa data de conversão
    "data_registro":             hoje,
    "data_cadastro":             hoje,
    "data_entrada":              hoje,

    // Nome (esquema antigo: primeiro_nome / novo: nome)
    "nome":                      d.nome || "",
    "primeiro_nome":             d.nome || "",

    // Telefone (antigo: telefone / novo: telefone_whatsapp)
    "telefone_whatsapp":         d.tel  || "",
    "telefone":                  d.tel  || "",

    // Serviço
    "servico":                   extras.servico       || d.servico || "",
    "servico_interesse":         d.servico || "",
    "ultimo_servico":            extras.ultimoServico || d.servico || "",
    "tipo_consulta":             d.servico || "",
    "tipo_exame":                extras.tipoExame     || "Espirometria",

    // Origem
    "origem":                    d.origem || "Não informado",

    // Responsável — fallback garantido no objeto d
    "responsavel":               d.resp,

    // Próximo contato — usa cálculo automático (+5 meses) quando disponível
    "proximo_contato":           extras.dataProxCalc  || d.dataProx || "",
    "data_proximo_contato":      extras.dataProxCalc  || d.dataProx || "",
    "motivo_proximo_contato":    extras.motivo        || d.proxAcao || "",

    // Observação
    "observacao":                d.obs || "",
    "observacao_privada_minima": d.obs || "",

    // Status
    "status":                    extras.statusPadrao  || "A confirmar",
    "status_relacionamento":     "Em acompanhamento",
    "status_exame":              extras.statusExame   || "A confirmar",

    // Datas de atendimento — preenchidas pela automação quando informadas
    "medica":                    "",
    "data_consulta":             extras.dataConsulta  || "",
    "data_exame":                extras.dataExame     || "",
    "historico_resumido":        "",

    // Canal e consentimento — definidos na conversão automática
    "canal":                     "WhatsApp",
    "consentimento_whatsapp":    "Confirmado",
  };

  return headers.map(function(col) {
    var key = String(col || "").trim();
    return v.hasOwnProperty(key) ? v[key] : "";
  });
}

/**
 * Obtém ou cria uma aba CRM.
 * Se a aba já existir com dados, NÃO altera o cabeçalho.
 * Se a aba não existir ou estiver vazia, cria/aplica o cabeçalho padrão.
 */
function _cvObterOuCriarAba(ss, nomeAba, headersDefault) {
  var sheet = ss.getSheetByName(nomeAba);
  if (!sheet) {
    sheet = ss.insertSheet(nomeAba);
    sheet.getRange(1, 1, 1, headersDefault.length)
      .setValues([headersDefault])
      .setFontWeight("bold")
      .setFontColor("#ffffff")
      .setBackground("#08243d");
    Logger.log("Aba '" + nomeAba + "' criada com cabeçalho padrão.");
  } else if (
    sheet.getLastRow() === 0 ||
    String(sheet.getRange(1, 1).getValue() || "").trim() === ""
  ) {
    sheet.getRange(1, 1, 1, headersDefault.length)
      .setValues([headersDefault])
      .setFontWeight("bold")
      .setFontColor("#ffffff")
      .setBackground("#08243d");
    Logger.log("Cabeçalho padrão aplicado em '" + nomeAba + "' (aba estava vazia).");
  }
  return sheet;
}

/**
 * Obtém ou cria uma aba de suporte (log, etc.) e aplica cabeçalho se ausente.
 * Sempre cria com o cabeçalho informado se a aba não tiver dados.
 */
function _cvGarantirAba(ss, nomeAba, headers) {
  var sheet = ss.getSheetByName(nomeAba);
  if (!sheet) {
    sheet = ss.insertSheet(nomeAba);
    Logger.log("Aba '" + nomeAba + "' criada.");
  }
  if (
    sheet.getLastRow() === 0 ||
    String(sheet.getRange(1, 1).getValue() || "").trim() === ""
  ) {
    sheet.getRange(1, 1, 1, headers.length)
      .setValues([headers])
      .setFontWeight("bold")
      .setFontColor("#ffffff")
      .setBackground("#08243d");
  }
  return sheet;
}

/**
 * Mapeia array de cabeçalho para { nome_coluna: índice_base0 }.
 */
function _cvMapearHeader(header) {
  var mapa = {};
  header.forEach(function(nome, idx) {
    var n = String(nome || "").trim();
    if (n && !mapa.hasOwnProperty(n)) mapa[n] = idx;
  });
  return mapa;
}

/**
 * Lê valor de uma coluna pelo nome usando mapa de índices.
 */
function _cvVal(row, mapa, campo) {
  var idx = mapa[campo];
  if (idx === undefined) return "";
  var v = row[idx];
  return (v === undefined || v === null) ? "" : String(v).trim();
}

/**
 * Retorna conjunto de dígitos de telefone presentes em uma aba CRM.
 * Aceita colunas 'telefone_whatsapp' (novo) ou 'telefone' (antigo).
 */
function _cvConjuntoTelefones(ss, nomeAba) {
  var conj  = _cvSetFallback();
  var sheet = ss.getSheetByName(nomeAba);
  if (!sheet || sheet.getLastRow() < 2) return conj;

  var lastCol = sheet.getLastColumn();
  if (lastCol < 1) return conj;

  var header = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var mapa   = _cvMapearHeader(header);
  var idx    = mapa["telefone_whatsapp"] !== undefined
    ? mapa["telefone_whatsapp"]
    : mapa["telefone"];
  if (idx === undefined) return conj;

  var dados = sheet.getRange(2, 1, sheet.getLastRow() - 1, lastCol).getValues();
  dados.forEach(function(row) {
    var digits = String(row[idx] || "").replace(/\D/g, "");
    if (digits) conj.add(digits);
  });
  return conj;
}

/**
 * Set simples compatível com Apps Script (sem depender de Set nativo).
 */
function _cvSetFallback() {
  var store = {};
  return {
    has: function(k) { return store.hasOwnProperty(k); },
    add: function(k) { store[k] = true; },
  };
}

/**
 * Tenta obter SpreadsheetApp.getUi().
 * Retorna null em contextos sem UI (trigger automático, editor Apps Script).
 */
function _cvTentarUI() {
  try {
    return SpreadsheetApp.getUi();
  } catch (e) {
    Logger.log(
      "UI não disponível. Use o menu SoproLife → Leads → Converter lead selecionado → CRM. " +
      "Erro: " + e.message
    );
    return null;
  }
}

// ── Utilitário: ocultar abas técnicas ─────────────────────────────────────────

/**
 * Oculta as abas técnicas de leads ('Leads Convertidos' e 'Log Conversões Leads')
 * sem apagá-las. Útil para manter a planilha limpa para o uso diário.
 * Execute via menu ou pelo editor Apps Script.
 */
function ocultarAbasTecnicasLeadsSoproLife() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var abasParaOcultar = [_CV_ABA_CONVERTIDOS, _CV_ABA_LOG];

  abasParaOcultar.forEach(function(nomeAba) {
    var sheet = ss.getSheetByName(nomeAba);
    if (sheet) {
      sheet.hideSheet();
      Logger.log("Aba '" + nomeAba + "' ocultada.");
    } else {
      Logger.log("Aba '" + nomeAba + "' não encontrada — ignorada.");
    }
  });

  Logger.log("ocultarAbasTecnicasLeadsSoproLife concluído.");
}
