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
 * Não faz parte de setupSoproLifeSheetsLite() de propósito: mantém a
 * fronteira entre instalação de estrutura e manutenção incremental de uma
 * aba que já contém dados reais de prospecção.
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
    // A aba financeira NÃO entra neste mapa de propósito (M14.2):
    // 1) a antiga aba "Financeiro" foi removida da planilha e não deve ser
    //    recriada por nenhum fluxo;
    // 2) a aba oficial "Financeiro_Lancamentos" é criada e gerenciada pelo
    //    command-center-api.gs (upsert por id_atendimento) — a fonte
    //    financeira única tem um dono só.
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

  // M14.3A — instalador com FRONTEIRA SEGURA: só cria abas que não existem e
  // só escreve cabeçalho em aba VAZIA. Aba existente com qualquer dado é
  // pulada com log — este setup nunca limpa (o clearContents/clearFormats
  // antigo apagaria dados reais) e nunca exclui aba (nem as padrão
  // "Página1"/"Sheet1": remoção de aba é sempre ação humana).
  const puladas = [];
  Object.entries(sheets).forEach(([name, headers]) => {
    let sheet = ss.getSheetByName(name);
    if (!sheet) {
      sheet = ss.insertSheet(name);
    } else if (sheet.getLastRow() > 0) {
      puladas.push(name);
      Logger.log(`Aba "${name}" já tem conteúdo — pulada (setup nunca limpa dados).`);
      return;
    }

    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    sheet.getRange(1, 1, 1, headers.length)
      .setFontWeight("bold")
      .setFontColor("#ffffff")
      .setBackground("#08243d");

    sheet.setFrozenRows(1);
  });

  ["Página1", "Sheet1"].forEach((defaultName) => {
    if (ss.getSheetByName(defaultName)) {
      Logger.log(`Aba padrão "${defaultName}" existe — remova manualmente se quiser (o setup não exclui abas).`);
    }
  });

  Logger.log(
    "Setup concluído sem tocar em dados existentes." +
    (puladas.length ? " Abas puladas por já terem conteúdo: " + puladas.join(", ") : "")
  );
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

  // Financeiro_Lancamentos (fonte financeira única — M14.2): validações e
  // formatos NÃO-destrutivos sobre o cabeçalho oficial de 17 colunas criado
  // pelo command-center-api.gs. dropdown/dateColumn/moneyColumn pulam a aba
  // se ela ainda não existir — nada aqui cria ou limpa a aba.
  // Colunas: A id_lancamento | B id_atendimento | C criado_em | D data_exame |
  // E tipo_movimento | F servico | G local_atendimento | H valor_tabela |
  // I valor_cobrado | J valor_recebido | K desconto | L status_exame |
  // M status_pagamento | N forma_pagamento | O origem_preco |
  // P observacao_financeira | Q fonte. Enums = js/espirometria-financeiro.js.
  dropdown("Financeiro_Lancamentos", "G", ["Domiciliar", "Clínica", "Empresa / PCMSO", "Parceiro", "Outro"]);
  dropdown("Financeiro_Lancamentos", "L", ["Aguardando", "Realizado", "Cancelado", "Remarcado"]);
  dropdown("Financeiro_Lancamentos", "M", ["Recebido", "Pendente", "Parcial", "Cortesia", "Cancelado"]);
  dropdown("Financeiro_Lancamentos", "N", ["Pix", "Dinheiro", "Cartão", "Outro"]);
  dropdown("Financeiro_Lancamentos", "O", ["Tabela", "Promoção", "Parceria", "Negociação", "PCMSO", "Cortesia"]);
  dateColumn("Financeiro_Lancamentos", "D");
  moneyColumn("Financeiro_Lancamentos", "H");
  moneyColumn("Financeiro_Lancamentos", "I");
  moneyColumn("Financeiro_Lancamentos", "J");
  moneyColumn("Financeiro_Lancamentos", "K");

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

  const leads = getSheetRows("Leads");
  const crm = getSheetRows("CRM Clinicas");
  const tarefas = getSheetRows("Tarefas");
  // Fonte financeira única (M14.2): leitura READ-ONLY da aba oficial —
  // getSheetRows retorna [] se a aba ainda não existir, nunca a cria.
  // Colunas (0-based): 8 valor_cobrado | 9 valor_recebido | 11 status_exame |
  // 12 status_pagamento. Mesmas regras defensivas do gerador Python
  // (read-financeiro-lancamentos-adc.py): Pendente/Cortesia/Cancelado nunca
  // contam como receita recebida.
  const financeiroLanc = getSheetRows("Financeiro_Lancamentos");
  const marketing = getSheetRows("Marketing Conteudo");
  const agenda = getSheetRows("Agenda Operacional");

  function financeiroReceitas(rows) {
    let recebida = 0;
    let prevista = 0;
    rows.forEach(row => {
      const statusExame = String(row[11] || "").trim();
      const statusPag = String(row[12] || "").trim();
      const cobrado = Number(row[8]) || 0;
      const recebido = Number(row[9]) || 0;
      if (statusExame === "Cancelado" || statusPag === "Cancelado" || statusPag === "Cortesia") return;
      if (statusPag === "Recebido") {
        recebida += recebido;
      } else if (statusPag === "Parcial") {
        recebida += recebido;
        prevista += Math.max(0, cobrado - recebido);
      } else if (statusPag === "Pendente") {
        prevista += cobrado;
      }
    });
    return { recebida: recebida, prevista: prevista };
  }
  const receitas = financeiroReceitas(financeiroLanc);

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
    ["Financeiro", "Receita prevista", receitas.prevista, "Financeiro_Lancamentos", "A receber: Pendente + restante de Parcial"],
    ["Financeiro", "Receita recebida", receitas.recebida, "Financeiro_Lancamentos", "Soma de valor_recebido (Recebido/Parcial)"],
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
    "Financeiro_Lancamentos",
    "Marketing Conteudo",
    "Agenda Operacional"
  ];

  if (!monitoredSheets.includes(sheetName)) return;

  atualizarResumoDashboardSoproLife();
}
