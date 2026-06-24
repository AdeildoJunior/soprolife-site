/**
 * organizar-leads-operacionais.gs
 * Versão: 2.0  |  SoproLife Command Center
 *
 * Reorganiza a aba Leads para 14 colunas operacionais.
 * Preserva dados existentes mapeando pelo nome do cabeçalho.
 * Campos removidos (preferencia_atendimento, valor_informado, consentimento_whatsapp)
 * são incorporados à coluna `observacao` quando tiverem conteúdo.
 *
 * SEGURANÇA:
 *   - Não contém ID ou URL da planilha real.
 *   - Não contém tokens, senhas ou dados reais.
 *   - A função principal usa apenas Logger.log (sem getUi().alert).
 *
 * COMO EXECUTAR:
 *   1. Abra a planilha "SoproLife - Painel Interno - Dados Privados".
 *   2. Extensões → Apps Script → cole (ou abra) este arquivo.
 *   3. Selecione organizarLeadsOperacionaisSoproLife → Executar.
 *   4. Confirme permissões na primeira execução.
 *      Ou use o menu SoproLife → Leads → Organizar Leads Operacionais.
 */

// ── Constantes ────────────────────────────────────────────────────────────────

var _OP_LEADS_ABA = "Leads";

// Cabeçalho canônico — 14 colunas operacionais
var _CABECALHO_CANONICO = [
  "lead_id",
  "data_contato",
  "nome",
  "telefone_whatsapp",
  "servico_interesse",
  "origem",
  "canal",
  "bairro_regiao",
  "tem_pedido_medico",
  "etapa",
  "responsavel",
  "proxima_acao",
  "data_proxima_acao",
  "observacao",
];

// Aliases: nome da coluna antiga → nome canônico
// Usado no mapa de leitura para migração inteligente.
var _ALIASES_COLUNAS = {
  "data_entrada":              "data_contato",
  "data":                      "data_contato",
  "observacao_privada_minima": "observacao",
  "observacao_anonima":        "observacao",
  "status":                    "etapa",
  "servico":                   "servico_interesse",
  "lead":                      "nome",
  "proxima_acao":              "proxima_acao",
  "proximaAcao":               "proxima_acao",
  "dataProximaAcao":           "data_proxima_acao",
};

// Campos excluídos do novo cabeçalho: se tiverem conteúdo, são fundidos em `observacao`.
var _CAMPOS_COLAPSAR = [
  { chave: "preferencia_atendimento", label: "Pref. atendimento" },
  { chave: "valor_informado",         label: "Valor informado" },
  { chave: "consentimento_whatsapp",  label: "Consentimento WhatsApp" },
];

// Valores-padrão que não vale a pena preservar ao colapsar em observacao.
var _VALORES_IGNORAR_COLAPSO = [
  "a definir", "nao informado", "não informado",
  "nao se aplica", "não se aplica",
];

// Termos que identificam linhas demonstrativas/fake
var _OP_TERMOS_DEMO = [
  "fictício", "ficticio",
  "demonstrativo",
  "exemplo sem telefone real",
  "teste do painel",
  "registro demonstrativo",
  "lead fake",
  "dados fake",
];

// ── Dropdowns ─────────────────────────────────────────────────────────────────

var _OP_DROP_SERVICO = [
  "Espirometria",
  "Espirometria domiciliar",
  "Teleconsulta respiratória",
  "Consulta pneumologista",
  "Clínicas",
  "PCMSO / empresa",
];

var _OP_DROP_ETAPA = [
  "Novo contato",
  "Em conversa",
  "Aguardando retorno",
  "Agendado",
  "Não respondeu",
  "Desistiu",
  "Convertido em paciente",
  "Convertido em clínica/parceiro",
];

var _OP_DROP_CANAL = [
  "WhatsApp",
  "Site",
  "Google",
  "Instagram",
  "E-mail",
  "Telefone",
  "Indicação",
  "Presencial",
  "Outro",
];

var _OP_DROP_ORIGEM = [
  "Google",
  "WhatsApp",
  "Instagram",
  "Site",
  "Indicação",
  "Clínica parceira",
  "Tráfego pago",
  "Outro",
];

var _OP_DROP_PEDIDO = [
  "Sim",
  "Não",
  "Não informado",
  "Não se aplica",
];

// ── Notas nos cabeçalhos ──────────────────────────────────────────────────────

var _OP_NOTAS = {
  "lead_id":          "Identificador único (ex.: LEAD-20260601-001). Gerado manualmente ou pela equipe.",
  "data_contato":     "Data do primeiro contato recebido. Formato: dd/MM/yyyy.",
  "nome":             "Primeiro nome ou apelido. Não inserir nome completo aqui.",
  "telefone_whatsapp":"Telefone com DDD. Apenas dígitos. Ex.: 21999990000.",
  "servico_interesse":"Serviço de interesse. Selecione da lista. 'Clínicas' e 'PCMSO / empresa' são leads B2B — não vão para CRM Pacientes.",
  "origem":           "De onde veio o interesse: Google, Instagram, Indicação, etc.",
  "canal":            "Canal de comunicação usado: WhatsApp, Site, E-mail, etc.",
  "bairro_regiao":    "Bairro ou região do lead — útil para triagem de atendimento.",
  "tem_pedido_medico":"O lead possui pedido médico para espirometria? Selecione da lista.",
  "etapa":            "Estágio no funil. 'Convertido em paciente' ou 'Convertido em clínica/parceiro' = lead encerrado aqui.",
  "responsavel":      "Membro da equipe responsável pelo acompanhamento.",
  "proxima_acao":     "Ação concreta planejada para este lead.",
  "data_proxima_acao":"Data da próxima ação. Formato: dd/MM/yyyy.",
  "observacao":       "Observação operacional livre. Não inserir CPF, laudo, diagnóstico ou pedido médico.",
};

// ── Função principal ──────────────────────────────────────────────────────────

function organizarLeadsOperacionaisSoproLife() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  Logger.log("=== organizarLeadsOperacionaisSoproLife v2 iniciado ===");

  var abaLeads = ss.getSheetByName(_OP_LEADS_ABA);
  if (!abaLeads) {
    Logger.log("AVISO: aba '" + _OP_LEADS_ABA + "' não encontrada. Execute setupSoproLifeSheetsLite() primeiro.");
    return;
  }

  // 1. Backup
  var nomeBackup = _opCriarBackup(ss, abaLeads);
  Logger.log("Backup criado: " + nomeBackup);

  // 2. Migrar dados existentes para o novo cabeçalho
  var dadosMigrados = _opMigrarDados(abaLeads);
  Logger.log("Linhas lidas: " + dadosMigrados.length);

  // 3. Filtrar linhas claramente demonstrativas
  var linhasReais = dadosMigrados.filter(function(row) {
    return !_opLinhaEhDemo(row);
  });
  Logger.log("Linhas demonstrativas removidas: " + (dadosMigrados.length - linhasReais.length));

  // 4. Reescrever aba com novo cabeçalho
  _opReescreverAba(abaLeads, linhasReais);
  Logger.log("Cabeçalho canônico (14 colunas) gravado.");

  // 5. Dropdowns
  _opAplicarDropdowns(ss);
  Logger.log("Dropdowns aplicados.");

  // 6. Notas nos cabeçalhos
  _opAdicionarNotas(abaLeads);
  Logger.log("Notas nos cabeçalhos adicionadas.");

  SpreadsheetApp.flush();
  Logger.log(
    "=== Concluído. Backup: " + nomeBackup +
    " | Linhas preservadas: " + linhasReais.length + " ==="
  );
}

// ── Backup ────────────────────────────────────────────────────────────────────

function _opCriarBackup(ss, abaLeads) {
  var agora = new Date();
  var yyyymmdd = Utilities.formatDate(agora, "America/Sao_Paulo", "yyyyMMdd");
  var hhmm     = Utilities.formatDate(agora, "America/Sao_Paulo", "HHmm");
  var nome = "_Backup_Leads_Operacional_" + yyyymmdd + "_" + hhmm;

  var existente = ss.getSheetByName(nome);
  if (existente) ss.deleteSheet(existente);

  abaLeads.copyTo(ss).setName(nome);
  return nome;
}

// ── Migração de dados ─────────────────────────────────────────────────────────

/**
 * Lê dados da aba e mapeia para o novo cabeçalho de 14 colunas.
 * Usa nomes de colunas (não posições) para robustez com qualquer estrutura existente.
 * Campos excluídos do novo cabeçalho são mesclados em `observacao` quando têm conteúdo.
 */
function _opMigrarDados(sheet) {
  var ultimaLinha = sheet.getLastRow();
  if (ultimaLinha < 2) return [];
  var ultimaCol = sheet.getLastColumn();
  if (ultimaCol < 1) return [];

  // Cabeçalho original em minúsculas
  var headerOrig = sheet.getRange(1, 1, 1, ultimaCol).getValues()[0].map(function(c) {
    return String(c || "").trim();
  });
  var headerOrigLower = headerOrig.map(function(c) { return c.toLowerCase(); });

  // Mapa pelo nome original (lowercase): nome_original → índice
  var mapaOriginal = {};
  headerOrigLower.forEach(function(nome, idx) {
    if (nome && !mapaOriginal.hasOwnProperty(nome)) mapaOriginal[nome] = idx;
  });

  // Mapa canônico (com aliases): nome_canônico → índice
  var mapaCanônico = {};
  headerOrigLower.forEach(function(nome, idx) {
    var alias = _ALIASES_COLUNAS[nome];
    var nomeCanon = alias || nome;
    if (nomeCanon && !mapaCanônico.hasOwnProperty(nomeCanon)) {
      mapaCanônico[nomeCanon] = idx;
    }
  });

  var dados = sheet.getRange(2, 1, ultimaLinha - 1, ultimaCol).getValues();

  return dados.map(function(row) {
    return _CABECALHO_CANONICO.map(function(colCanonica) {

      if (colCanonica === "observacao") {
        return _opMontarObservacao(row, mapaCanônico, mapaOriginal);
      }

      var idx = mapaCanônico[colCanonica];
      if (idx === undefined) return "";
      var val = row[idx];
      return (val === undefined || val === null) ? "" : val;
    });
  });
}

/**
 * Monta o valor da coluna `observacao` mesclando o conteúdo original
 * com campos que foram removidos do novo cabeçalho (se tiverem conteúdo relevante).
 */
function _opMontarObservacao(row, mapaCanônico, mapaOriginal) {
  var partes = [];

  // Base: valor já existente em `observacao` (via alias: observacao_privada_minima, observacao_anonima)
  var idxObs = mapaCanônico["observacao"];
  if (idxObs !== undefined) {
    var vObs = String(row[idxObs] || "").trim();
    if (vObs) partes.push(vObs);
  }

  // Campos colapsados: acrescenta somente quando têm conteúdo não-trivial
  _CAMPOS_COLAPSAR.forEach(function(campo) {
    var idx = mapaOriginal[campo.chave];
    if (idx === undefined) return;
    var vOrig = String(row[idx] || "").trim();
    if (!vOrig) return;
    var vNorm = vOrig.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    var ignorar = _VALORES_IGNORAR_COLAPSO.some(function(v) { return vNorm === v; });
    if (!ignorar) {
      partes.push(campo.label + ": " + vOrig);
    }
  });

  return partes.join(" | ");
}

// ── Remoção de linhas demonstrativas ─────────────────────────────────────────

function _opLinhaEhDemo(row) {
  return row.some(function(celula) {
    var texto = String(celula || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    return _OP_TERMOS_DEMO.some(function(termo) {
      var t = termo.normalize("NFD").replace(/[̀-ͯ]/g, "");
      return texto.indexOf(t) >= 0;
    });
  });
}

// ── Reescrita da aba ──────────────────────────────────────────────────────────

function _opReescreverAba(sheet, linhasReais) {
  sheet.clearContents();
  sheet.getRange(1, 1, 1, _CABECALHO_CANONICO.length).setValues([_CABECALHO_CANONICO]);
  if (linhasReais.length > 0) {
    sheet.getRange(2, 1, linhasReais.length, _CABECALHO_CANONICO.length).setValues(linhasReais);
  }
}

// ── Dropdowns ─────────────────────────────────────────────────────────────────

function _opAplicarDropdowns(ss) {
  var colDrops = [
    { nome: "servico_interesse", valores: _OP_DROP_SERVICO },
    { nome: "etapa",             valores: _OP_DROP_ETAPA },
    { nome: "canal",             valores: _OP_DROP_CANAL },
    { nome: "origem",            valores: _OP_DROP_ORIGEM },
    { nome: "tem_pedido_medico", valores: _OP_DROP_PEDIDO },
  ];

  var sheet = ss.getSheetByName(_OP_LEADS_ABA);
  if (!sheet) return;

  var colIdx = _opMapearColunas(sheet);

  colDrops.forEach(function(cfg) {
    var idx = colIdx[cfg.nome];
    if (idx === undefined) return;
    var colLetra = _opIndexParaLetra(idx + 1);
    var range = sheet.getRange(colLetra + "2:" + colLetra + "1000");
    var regra = SpreadsheetApp.newDataValidation()
      .requireValueInList(cfg.valores, true)
      .setAllowInvalid(false)
      .setHelpText("Selecione uma opção da lista.")
      .build();
    range.setDataValidation(regra);
  });
}

function _opMapearColunas(sheet) {
  var lastCol = sheet.getLastColumn();
  if (lastCol < 1) return {};
  var header = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var mapa = {};
  header.forEach(function(nome, idx) {
    var n = String(nome || "").trim();
    if (n && !mapa.hasOwnProperty(n)) mapa[n] = idx;
  });
  return mapa;
}

function _opIndexParaLetra(idx) {
  var letra = "";
  while (idx > 0) {
    idx--;
    letra = String.fromCharCode(65 + (idx % 26)) + letra;
    idx = Math.floor(idx / 26);
  }
  return letra;
}

// ── Notas nos cabeçalhos ──────────────────────────────────────────────────────

function _opAdicionarNotas(sheet) {
  var lastCol = sheet.getLastColumn();
  if (lastCol < 1) return;
  var header = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  header.forEach(function(nome, idx) {
    var n = String(nome || "").trim();
    var nota = _OP_NOTAS[n];
    if (nota) sheet.getRange(1, idx + 1).setNote(nota);
  });
}

// ── Menu SoproLife → Leads ────────────────────────────────────────────────────

/**
 * Cria o menu SoproLife → Leads com as duas funções operacionais.
 * Nomear exatamente `onOpen` para disparo automático ao abrir a planilha.
 * Se já houver outro onOpen no projeto, mescle os addItem aqui.
 */
function onOpen() {
  var ui = SpreadsheetApp.getUi();
  ui.createMenu("SoproLife")
    .addSubMenu(
      ui.createMenu("Leads")
        .addItem("Organizar Leads Operacionais", "organizarLeadsOperacionaisSoproLife")
        .addItem("Converter lead selecionado → CRM", "converterLeadSelecionadoSoproLife")
    )
    .addToUi();
}
