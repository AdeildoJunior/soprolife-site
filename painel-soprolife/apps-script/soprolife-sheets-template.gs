/**
 * Template seguro de Apps Script para a planilha privada do Painel SoproLife.
 *
 * Não inserir neste arquivo:
 * - URL da planilha real;
 * - ID da planilha real;
 * - dados reais de pacientes;
 * - tokens, senhas ou chaves de API.
 */

// Vocabulário canônico de Etapa (fase comercial) — mesmo usado em app.js e
// nos scripts Python (generate-followup-clinicas.py, promote-pcmso-to-crm.py).
// Etapa é independente de Status/Próximo passo/Origem — ver skill
// soprolife-b2b-pcmso-crm. "Parceiro ativo" é etapa terminal positiva; as
// quatro últimas são etapas terminais negativas.
const ETAPA_PCMSO_VALORES = [
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
  "Arquivada"
];

function _colLetter(colIndex1Based) {
  let idx = colIndex1Based;
  let letters = "";
  while (idx > 0) {
    const rem = (idx - 1) % 26;
    letters = String.fromCharCode(65 + rem) + letters;
    idx = Math.floor((idx - 1) / 26);
  }
  return letters;
}

/**
 * Adiciona a coluna "Etapa" na aba "Base Prospecção B2B PCMSO", SE ela ainda
 * não existir. Não apaga, não reordena e não sobrescreve nenhuma coluna ou
 * linha existente — apenas acrescenta uma coluna nova ao final com dropdown
 * de validação. Idempotente: rodar de novo não duplica a coluna.
 *
 * Não faz parte de setupSoproLifeSheetsLite() de propósito: aquela função
 * limpa (clearContents) as abas que gerencia, o que apagaria dados reais de
 * prospecção já existentes nesta aba.
 */
function adicionarColunaEtapaPcmso() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheetName = "Base Prospecção B2B PCMSO";
  const sheet = ss.getSheetByName(sheetName);
  if (!sheet) {
    Logger.log(`Aba "${sheetName}" não encontrada — nada foi alterado.`);
    return;
  }

  const lastCol = sheet.getLastColumn();
  const headers = lastCol > 0 ? sheet.getRange(1, 1, 1, lastCol).getValues()[0] : [];
  const jaExiste = headers.some((h) => String(h).trim().toLowerCase() === "etapa");
  if (jaExiste) {
    Logger.log(`Coluna "Etapa" já existe em "${sheetName}" — nada foi alterado.`);
    return;
  }

  const novaCol = lastCol + 1;
  const colLetter = _colLetter(novaCol);

  sheet.getRange(1, novaCol).setValue("Etapa");
  sheet.getRange(1, novaCol)
    .setFontWeight("bold")
    .setFontColor("#ffffff")
    .setBackground("#08243d");

  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(ETAPA_PCMSO_VALORES, true)
    .setAllowInvalid(false)
    .build();
  sheet.getRange(`${colLetter}2:${colLetter}1000`).setDataValidation(rule);

  SpreadsheetApp.flush();
  Logger.log(`Coluna "Etapa" adicionada em "${sheetName}" (coluna ${colLetter}). Linhas existentes não foram apagadas nem movidas; Etapa começa vazia e deve ser preenchida manualmente/por triagem.`);
}

function setupSoproLifeSheetsLite() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  ss.rename("SoproLife - Painel Interno - Dados Privados");

  const sheets = {
    "Leads": [
      "lead_id", "data_entrada", "origem", "canal", "servico_interesse",
      "etapa", "responsavel", "proxima_acao", "data_proxima_acao", "observacao_anonima"
    ],
    "CRM Clinicas": [
      "clinica_id", "nome_clinica", "bairro", "regiao", "tipo_clinica",
      "etapa", "ultima_interacao", "proxima_acao", "responsavel", "prioridade", "observacao"
    ],
    "Tarefas": [
      "tarefa_id", "area", "titulo", "prioridade", "status",
      "responsavel", "prazo", "origem", "observacao"
    ],
    "Financeiro": [
      "lancamento_id", "data", "tipo", "categoria", "servico", "origem",
      "valor_estimado", "valor_recebido", "status", "forma_pagamento", "observacao_anonima"
    ],
    "Marketing Conteudo": [
      "conteudo_id", "canal", "tema", "formato", "etapa",
      "data_planejada", "data_publicacao", "cta", "status", "metrica_agregada", "observacao"
    ],
    "Agenda Operacional": [
      "evento_id", "data", "hora", "tipo_evento", "local", "responsavel", "status", "observacao_anonima"
    ],
    "Rateio Sócios": [
      "item_id", "item", "categoria", "valor_total", "valor_mensal",
      "pago_adeildo", "pago_faustino", "pendente_adeildo", "pendente_faustino",
      "status", "observacao"
    ],
    "Resumo": [
      "area", "indicador", "valor", "observacao"
    ]
  };

  Object.entries(sheets).forEach(([name, headers]) => {
    let sheet = ss.getSheetByName(name);
    if (!sheet) sheet = ss.insertSheet(name);

    sheet.clearContents();
    sheet.clearFormats();

    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    sheet.getRange(1, 1, 1, headers.length)
      .setFontWeight("bold")
      .setFontColor("#ffffff")
      .setBackground("#08243d");

    sheet.setFrozenRows(1);
  });

  ["Página1", "Sheet1"].forEach((defaultName) => {
    const defaultSheet = ss.getSheetByName(defaultName);
    if (defaultSheet && ss.getSheets().length > 1) {
      ss.deleteSheet(defaultSheet);
    }
  });

  Logger.log("Planilha mestre da SoproLife criada com sucesso.");
}

function setupValidacoesSoproLife() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  function dropdown(sheetName, colLetter, values) {
    const sheet = ss.getSheetByName(sheetName);
    if (!sheet) return;

    const range = sheet.getRange(`${colLetter}2:${colLetter}1000`);
    const rule = SpreadsheetApp.newDataValidation()
      .requireValueInList(values, true)
      .setAllowInvalid(false)
      .build();

    range.setDataValidation(rule);
  }

  function dateColumn(sheetName, colLetter) {
    const sheet = ss.getSheetByName(sheetName);
    if (!sheet) return;
    sheet.getRange(`${colLetter}2:${colLetter}1000`).setNumberFormat("dd/mm/yyyy");
  }

  function moneyColumn(sheetName, colLetter) {
    const sheet = ss.getSheetByName(sheetName);
    if (!sheet) return;
    sheet.getRange(`${colLetter}2:${colLetter}1000`).setNumberFormat("R$ #,##0.00");
  }

  dropdown("Leads", "C", ["WhatsApp", "Google", "Instagram", "Indicação", "Clínica parceira", "LinkedIn", "Outro"]);
  dropdown("Leads", "D", ["WhatsApp", "Site", "Telefone", "Instagram", "E-mail", "Presencial", "Outro"]);
  dropdown("Leads", "E", ["Espirometria", "Teleconsulta", "Consulta pneumologia", "Espirometria domiciliar", "Parceria clínica", "Outro"]);
  dropdown("Leads", "F", ["Novo", "Em contato", "Agendado", "Concluído", "Perdido", "Retorno futuro"]);
  dropdown("Leads", "G", ["Adeildo", "Raquel", "Médica", "Administrativo", "A definir"]);
  dateColumn("Leads", "B");
  dateColumn("Leads", "I");

  dropdown("CRM Clinicas", "D", ["Barra", "Recreio", "Jacarepaguá", "Zona Norte", "Zona Sul", "Centro", "Baixada", "Outro"]);
  dropdown("CRM Clinicas", "E", ["Clínica médica", "Clínica ocupacional", "Academia", "Consultório", "Coworking médico", "Empresa", "Outro"]);
  dropdown("CRM Clinicas", "F", ETAPA_PCMSO_VALORES);
  dropdown("CRM Clinicas", "I", ["Adeildo", "Raquel", "Comercial", "A definir"]);
  dropdown("CRM Clinicas", "J", ["Alta", "Média", "Baixa"]);
  dateColumn("CRM Clinicas", "G");

  dropdown("Tarefas", "B", ["Operação", "Comercial", "Marketing", "SEO", "Documentos", "Financeiro", "Tecnologia", "Atendimento"]);
  dropdown("Tarefas", "D", ["Alta", "Média", "Baixa"]);
  dropdown("Tarefas", "E", ["Pendente", "Em andamento", "Concluída", "Aguardando", "Cancelada"]);
  dropdown("Tarefas", "F", ["Adeildo", "Raquel", "Médica", "Administrativo", "A definir"]);
  dateColumn("Tarefas", "G");

  dropdown("Financeiro", "C", ["Receita", "Despesa", "Previsão"]);
  dropdown("Financeiro", "D", ["Espirometria", "Teleconsulta", "Consulta", "Parceria", "Marketing", "Operacional", "Documento", "Outro"]);
  dropdown("Financeiro", "E", ["Espirometria", "Teleconsulta", "Consulta pneumologia", "Espirometria domiciliar", "Outro"]);
  dropdown("Financeiro", "F", ["Paciente", "Clínica parceira", "Empresa", "Campanha", "Outro"]);
  dropdown("Financeiro", "I", ["Previsto", "Pendente", "Recebido", "Cancelado"]);
  dropdown("Financeiro", "J", ["PIX", "Cartão", "Dinheiro", "Transferência", "Boleto", "Outro"]);
  dateColumn("Financeiro", "B");
  moneyColumn("Financeiro", "G");
  moneyColumn("Financeiro", "H");

  dropdown("Marketing Conteudo", "B", ["Instagram", "LinkedIn", "Google Perfil", "Site", "WhatsApp", "E-mail", "Outro"]);
  dropdown("Marketing Conteudo", "D", ["Post único", "Carrossel", "Reels", "Story", "Artigo", "Página", "Campanha"]);
  dropdown("Marketing Conteudo", "E", ["Ideia", "Roteiro", "Arte", "Revisão", "Agendado", "Publicado", "Pausado"]);
  dropdown("Marketing Conteudo", "I", ["Planejado", "Em produção", "Publicado", "Revisar", "Cancelado"]);
  dateColumn("Marketing Conteudo", "F");
  dateColumn("Marketing Conteudo", "G");

  dropdown("Agenda Operacional", "D", ["Espirometria", "Teleconsulta", "Consulta", "Reunião clínica", "Visita comercial", "Tarefa interna", "Outro"]);
  dropdown("Agenda Operacional", "F", ["Adeildo", "Raquel", "Médica", "Administrativo", "A definir"]);
  dropdown("Agenda Operacional", "G", ["Agendado", "Confirmado", "Realizado", "Remarcar", "Cancelado"]);
  dateColumn("Agenda Operacional", "B");

  dropdown("Rateio Sócios", "C", ["Recorrente", "Infraestrutura", "Equipamento", "Regularização", "Outro"]);
  dropdown("Rateio Sócios", "J", ["ativo", "parcelado", "pago", "pendente"]);
  moneyColumn("Rateio Sócios", "D");
  moneyColumn("Rateio Sócios", "E");
  moneyColumn("Rateio Sócios", "F");
  moneyColumn("Rateio Sócios", "G");
  moneyColumn("Rateio Sócios", "H");
  moneyColumn("Rateio Sócios", "I");

  SpreadsheetApp.flush();
  Logger.log("Validações da planilha SoproLife aplicadas com sucesso.");
}

function atualizarResumoDashboardSoproLife() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  function getSheetRows(sheetName) {
    const sheet = ss.getSheetByName(sheetName);
    if (!sheet) return [];
    const values = sheet.getDataRange().getValues();
    return values.slice(1).filter(row => row[0] !== "");
  }

  function countByColumn(rows, colIndex, value) {
    return rows.filter(row => row[colIndex] === value).length;
  }

  function sumByCondition(rows, conditionCol, conditionValue, valueCol) {
    return rows
      .filter(row => row[conditionCol] === conditionValue)
      .reduce((total, row) => total + (Number(row[valueCol]) || 0), 0);
  }

  const leads = getSheetRows("Leads");
  const crm = getSheetRows("CRM Clinicas");
  const tarefas = getSheetRows("Tarefas");
  const financeiro = getSheetRows("Financeiro");
  const marketing = getSheetRows("Marketing Conteudo");
  const agenda = getSheetRows("Agenda Operacional");

  let sheet = ss.getSheetByName("Resumo Dashboard");
  if (!sheet) sheet = ss.insertSheet("Resumo Dashboard");

  sheet.clear();

  const rows = [
    ["area", "indicador", "valor", "base", "observacao"],
    ["Leads", "Total de leads", leads.length, "Leads", "Quantidade total de leads cadastrados"],
    ["Leads", "Leads novos", countByColumn(leads, 5, "Novo"), "Leads etapa", "Leads na etapa Novo"],
    ["Leads", "Leads agendados", countByColumn(leads, 5, "Agendado"), "Leads etapa", "Leads já agendados"],
    ["Leads", "Leads concluídos", countByColumn(leads, 5, "Concluído"), "Leads etapa", "Leads concluídos"],
    ["CRM", "Clínicas cadastradas", crm.length, "CRM Clinicas", "Total de clínicas no CRM"],
    ["CRM", "Clínicas em proposta", countByColumn(crm, 5, "Proposta enviada"), "CRM etapa", "Clínicas na etapa Proposta enviada"],
    ["CRM", "Clínicas parceiras", countByColumn(crm, 5, "Parceiro ativo"), "CRM etapa", "Etapa terminal positiva — não conta como prospecção ativa"],
    ["CRM", "Clínicas perdidas/arquivadas", countByColumn(crm, 5, "Sem interesse") + countByColumn(crm, 5, "Não contatar / bloqueou") + countByColumn(crm, 5, "Sem canal válido") + countByColumn(crm, 5, "Arquivada"), "CRM etapa", "Etapas terminais negativas — fora do funil ativo"],
    ["Tarefas", "Tarefas pendentes", countByColumn(tarefas, 4, "Pendente"), "Tarefas status", "Tarefas pendentes"],
    ["Tarefas", "Tarefas em andamento", countByColumn(tarefas, 4, "Em andamento"), "Tarefas status", "Tarefas em andamento"],
    ["Financeiro", "Receita prevista", sumByCondition(financeiro, 2, "Receita", 6), "Financeiro", "Soma de receitas estimadas"],
    ["Financeiro", "Receita recebida", sumByCondition(financeiro, 8, "Recebido", 7), "Financeiro", "Soma de valores recebidos"],
    ["Marketing", "Conteúdos planejados", countByColumn(marketing, 8, "Planejado"), "Marketing status", "Conteúdos planejados"],
    ["Agenda", "Eventos agendados", countByColumn(agenda, 6, "Agendado"), "Agenda status", "Eventos agendados"]
  ];

  sheet.getRange(1, 1, rows.length, rows[0].length).setValues(rows);

  sheet.getRange(1, 1, 1, rows[0].length)
    .setFontWeight("bold")
    .setFontColor("#ffffff")
    .setBackground("#08243d");

  sheet.getRange("C:C").setNumberFormat("#,##0.00");
  sheet.setFrozenRows(1);
  sheet.autoResizeColumns(1, rows[0].length);

  SpreadsheetApp.flush();
  Logger.log("Resumo Dashboard atualizado com valores calculados.");
}

/**
 * Menu interno da planilha e atualização automática do Resumo Dashboard.
 *
 * onOpen cria o menu "SoproLife".
 * onEdit atualiza o Resumo Dashboard quando uma aba operacional é editada.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("SoproLife")
    .addItem("Atualizar Resumo Dashboard", "atualizarResumoDashboardSoproLife")
    .addItem("Aplicar validações", "setupValidacoesSoproLife")
    .addItem("Adicionar coluna Etapa (PCMSO)", "adicionarColunaEtapaPcmso")
    .addToUi();
}

function onEdit(e) {
  if (!e || !e.range) return;

  const sheetName = e.range.getSheet().getName();

  const monitoredSheets = [
    "Leads",
    "CRM Clinicas",
    "Tarefas",
    "Financeiro",
    "Marketing Conteudo",
    "Agenda Operacional"
  ];

  if (!monitoredSheets.includes(sheetName)) return;

  atualizarResumoDashboardSoproLife();
}
