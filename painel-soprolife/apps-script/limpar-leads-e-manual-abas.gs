/**
 * limpar-leads-e-manual-abas.gs
 * Versão: 1.2  |  SoproLife Command Center
 *
 * Limpa dados demonstrativos da aba Leads, padroniza dropdowns
 * e cria/atualiza a aba "Manual das Abas" com documentação operacional.
 *
 * SEGURANÇA — o que NÃO está neste arquivo:
 *   - ID ou URL da planilha real
 *   - tokens, senhas ou chaves de API
 *   - dados reais de pacientes
 *   - CPF, telefone real, pedido médico ou dado clínico identificável
 *
 * COMO EXECUTAR:
 *   1. Abra a planilha "SoproLife - Painel Interno - Dados Privados".
 *   2. Extensões → Apps Script → cole (ou abra) este arquivo.
 *   3. Selecione a função organizarLeadsEManualPlanilhasSoproLife e clique em "Executar".
 *   4. Confirme permissões na primeira execução.
 */

// ── Constantes ────────────────────────────────────────────────────────────────

var _LEADS_ABA = "Leads";
var _MANUAL_ABA = "Manual das Abas";

// Termos que identificam linhas demonstrativas/fake (lowercase para comparação)
var _TERMOS_DEMO = [
  "fictício",
  "ficticio",
  "demonstrativo",
  "exemplo sem telefone real",
  "teste do painel",
  "registro demonstrativo",
  "lead fake",
  "dados fake",
];

// Dropdowns padronizados para a aba Leads
var _DROP_SERVICO_INTERESSE = [
  "Espirometria",
  "Espirometria domiciliar",
  "Teleconsulta respiratória",
  "Consulta pneumologista",
  "Clínicas",
  "PCMSO / empresa",
];

var _DROP_ETAPA = [
  "Novo contato",
  "Em conversa",
  "Agendado",
  "Não respondeu",
  "Desistiu",
  "Convertido em paciente",
];

var _DROP_CANAL = [
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

var _DROP_ORIGEM = [
  "Google",
  "WhatsApp",
  "Instagram",
  "Site",
  "Indicação",
  "Clínica parceira",
  "Tráfego pago",
  "Outro",
];

// Notas explicativas para cada coluna de Leads (posição = índice 0)
var _NOTAS_LEADS = [
  { col: 1,  nota: "Identificador único do lead (ex.: LEAD-20260601-001). Gerado automaticamente ou inserido manualmente." },
  { col: 2,  nota: "Data em que o contato foi recebido pela primeira vez. Formato: dd/MM/yyyy." },
  { col: 3,  nota: "De onde veio o interesse: Google, WhatsApp, Instagram, Indicação, Site, etc." },
  { col: 4,  nota: "Canal de comunicação utilizado: WhatsApp, Telefone, Site, E-mail, Presencial, etc." },
  { col: 5,  nota: "Serviço que o lead demonstrou interesse. Usar lista suspensa padronizada." },
  { col: 6,  nota: "Estágio atual no funil: Novo contato → Em conversa → Agendado → Convertido em paciente." },
  { col: 7,  nota: "Membro da equipe responsável pelo atendimento deste lead." },
  { col: 8,  nota: "Descrição objetiva da próxima ação a ser realizada com este lead." },
  { col: 9,  nota: "Data prevista para a próxima ação. Formato: dd/MM/yyyy." },
  { col: 10, nota: "Observações gerais. Não inserir dados sensíveis de saúde, CPF, telefone ou laudo." },
];

// ── Função principal ──────────────────────────────────────────────────────────

/**
 * Ponto de entrada principal.
 * Executa backup, limpeza de dados demo, padronização de dropdowns,
 * notas nos cabeçalhos e atualização do Manual das Abas.
 */
function organizarLeadsEManualPlanilhasSoproLife() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  Logger.log("=== organizarLeadsEManualPlanilhasSoproLife iniciado ===");

  // 1. Verificar se a aba Leads existe
  var abaLeads = ss.getSheetByName(_LEADS_ABA);
  if (!abaLeads) {
    Logger.log("AVISO: aba '" + _LEADS_ABA + "' não encontrada. Nenhuma ação realizada.");
    Logger.log("Execute setupSoproLifeSheetsLite() primeiro para criar a estrutura.");
    return;
  }

  // 2. Criar backup antes de qualquer alteração
  var nomeBackup = _criarBackupLeads(ss, abaLeads);
  Logger.log("Backup criado: " + nomeBackup);

  // 3. Remover linhas demonstrativas/fake
  var removidas = _removerLinhasDemonstrativas(abaLeads);
  Logger.log("Linhas demonstrativas removidas: " + removidas);

  // 4. Padronizar dropdowns da aba Leads
  _padronizarDropdownsLeads(ss);
  Logger.log("Dropdowns de Leads padronizados.");

  // 5. Adicionar notas explicativas nos cabeçalhos
  _adicionarNotasCabecalhosLeads(abaLeads);
  Logger.log("Notas nos cabeçalhos de Leads adicionadas.");

  // 6. Criar/atualizar Manual das Abas
  _criarOuAtualizarManualAbas(ss);
  Logger.log("Manual das Abas criado/atualizado.");

  SpreadsheetApp.flush();

  var msg =
    "organizarLeadsEManualPlanilhasSoproLife concluído.\n\n" +
    "Backup criado: " + nomeBackup + "\n" +
    "Linhas demonstrativas removidas: " + removidas + "\n" +
    "Dropdowns padronizados: servico_interesse, etapa, canal, origem\n" +
    "Manual das Abas: criado/atualizado.";

  Logger.log("=== " + msg + " ===");

  mostrarAlertaSeguroSoproLife(msg);
}

// ── Backup ────────────────────────────────────────────────────────────────────

/**
 * Cria uma cópia da aba Leads com nome _Backup_Leads_Demo_YYYYMMDD_HHMM.
 * Retorna o nome da aba de backup criada.
 *
 * Não usa getActiveSheet/moveActiveSheet: copyTo() já posiciona a cópia
 * no final da planilha sem exigir aba ativa selecionada na UI.
 */
function _criarBackupLeads(ss, abaLeads) {
  var agora = new Date();
  var yyyymmdd = Utilities.formatDate(agora, "America/Sao_Paulo", "yyyyMMdd");
  var hhmm     = Utilities.formatDate(agora, "America/Sao_Paulo", "HHmm");
  var nomeBackup = "_Backup_Leads_Demo_" + yyyymmdd + "_" + hhmm;

  // Remover backup anterior com mesmo nome se já existir (raro, mas seguro)
  var existente = ss.getSheetByName(nomeBackup);
  if (existente) ss.deleteSheet(existente);

  // copyTo() insere a cópia no final da planilha por padrão
  abaLeads.copyTo(ss).setName(nomeBackup);

  return nomeBackup;
}

// ── Remoção de linhas demonstrativas ─────────────────────────────────────────

/**
 * Remove linhas da aba Leads que contenham qualquer termo demonstrativo
 * em qualquer coluna. Preserva a linha de cabeçalho (linha 1).
 * Percorre de baixo para cima para evitar desalinhamento de índices.
 * Retorna o número de linhas removidas.
 */
function _removerLinhasDemonstrativas(sheet) {
  var ultimaLinha = sheet.getLastRow();
  if (ultimaLinha < 2) return 0;

  var ultimaCol = sheet.getLastColumn();
  if (ultimaCol < 1) return 0;

  var dados = sheet.getRange(2, 1, ultimaLinha - 1, ultimaCol).getValues();
  var removidas = 0;

  // Percorrer de baixo para cima
  for (var i = dados.length - 1; i >= 0; i--) {
    if (_linhaEhDemonstrativa(dados[i])) {
      sheet.deleteRow(i + 2); // +2: offset do índice 0 + linha de cabeçalho
      removidas++;
    }
  }

  return removidas;
}

/**
 * Retorna true se qualquer célula da linha contiver um termo demonstrativo.
 */
function _linhaEhDemonstrativa(row) {
  return row.some(function(celula) {
    var texto = String(celula || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    return _TERMOS_DEMO.some(function(termo) {
      var termoNorm = termo.normalize("NFD").replace(/[̀-ͯ]/g, "");
      return texto.indexOf(termoNorm) >= 0;
    });
  });
}

// ── Dropdowns ─────────────────────────────────────────────────────────────────

/**
 * Aplica validações de lista suspensa nas colunas de Leads.
 * Baseado no cabeçalho canônico:
 *   A=lead_id, B=data_entrada, C=origem, D=canal,
 *   E=servico_interesse, F=etapa, G=responsavel,
 *   H=proxima_acao, I=data_proxima_acao, J=observacao_anonima
 */
function _padronizarDropdownsLeads(ss) {
  _aplicarDropdown(ss, _LEADS_ABA, "C", _DROP_ORIGEM);
  _aplicarDropdown(ss, _LEADS_ABA, "D", _DROP_CANAL);
  _aplicarDropdown(ss, _LEADS_ABA, "E", _DROP_SERVICO_INTERESSE);
  _aplicarDropdown(ss, _LEADS_ABA, "F", _DROP_ETAPA);
}

/**
 * Aplica dropdown em uma coluna a partir da linha 2 até a linha 1000.
 * Permite valores inválidos = false (força uso da lista).
 */
function _aplicarDropdown(ss, nomeAba, colLetra, valores) {
  var sheet = ss.getSheetByName(nomeAba);
  if (!sheet) return;

  var range = sheet.getRange(colLetra + "2:" + colLetra + "1000");
  var regra = SpreadsheetApp.newDataValidation()
    .requireValueInList(valores, true)
    .setAllowInvalid(false)
    .setHelpText("Selecione uma opção da lista.")
    .build();

  range.setDataValidation(regra);
}

// ── Notas nos cabeçalhos ──────────────────────────────────────────────────────

/**
 * Adiciona notas (comentários) nas células de cabeçalho da aba Leads,
 * explicando o propósito de cada coluna.
 */
function _adicionarNotasCabecalhosLeads(sheet) {
  _NOTAS_LEADS.forEach(function(item) {
    sheet.getRange(1, item.col).setNote(item.nota);
  });
}

// ── Manual das Abas ───────────────────────────────────────────────────────────

/**
 * Cria ou substitui a aba "Manual das Abas" com documentação operacional
 * de todas as abas da planilha SoproLife.
 */
function _criarOuAtualizarManualAbas(ss) {
  var sheet = ss.getSheetByName(_MANUAL_ABA);
  if (!sheet) {
    sheet = ss.insertSheet(_MANUAL_ABA);
  } else {
    sheet.clear();
  }

  var linhas = _conteudoManualAbas();

  sheet.getRange(1, 1, linhas.length, linhas[0].length).setValues(linhas);

  // Formatação do cabeçalho principal (linha 1)
  sheet.getRange(1, 1, 1, linhas[0].length)
    .setFontWeight("bold")
    .setFontSize(12)
    .setFontColor("#ffffff")
    .setBackground("#08243d");

  // Destaque nas linhas de título de seção (coluna A começa com "▶")
  var nLinhas = linhas.length;
  for (var i = 0; i < nLinhas; i++) {
    var val = String(linhas[i][0]);
    if (val.indexOf("▶") === 0) {
      sheet.getRange(i + 1, 1, 1, linhas[i].length)
        .setFontWeight("bold")
        .setFontColor("#ffffff")
        .setBackground("#0b6e9e");
    }
  }

  sheet.setColumnWidth(1, 220);
  sheet.setColumnWidth(2, 520);
  sheet.setFrozenRows(1);
  sheet.autoResizeRows(1, nLinhas);

  SpreadsheetApp.flush();
  Logger.log("Aba '" + _MANUAL_ABA + "' escrita com " + nLinhas + " linha(s).");
}

/**
 * Retorna o conteúdo do Manual das Abas como array de arrays [coluna A, coluna B].
 * Coluna A = nome/rótulo, Coluna B = descrição detalhada.
 */
function _conteudoManualAbas() {
  return [
    // Cabeçalho da aba
    ["Aba / Campo", "Descrição e Regras Operacionais"],

    // ── LEADS ──────────────────────────────────────────────────────────────
    ["▶ Leads", "Contatos recebidos que ainda NÃO se tornaram pacientes. Pré-atendimento / funil de interesse."],
    ["  Finalidade", "Registrar todo contato externo que demonstrou interesse em algum serviço da SoproLife, independente de já ter agendado ou não."],
    ["  Regra principal",
      "Um lead só migra para CRM Pacientes quando realizar o primeiro atendimento (espirometria, consulta, etc.). " +
      "Enquanto não atendido, permanece aqui como lead."],
    ["  Etapas do funil",
      "Novo contato → Em conversa → Agendado → Convertido em paciente.\n" +
      "Use 'Não respondeu' para inativos e 'Desistiu' para contatos descartados."],
    ["  O que NÃO inserir",
      "CPF, telefone real de paciente, pedido médico, laudo, resultado de exame ou qualquer dado clínico identificável. " +
      "A coluna observacao_anonima deve conter apenas anotações genéricas."],

    // ── CRM PACIENTES ───────────────────────────────────────────────────────
    ["▶ CRM Pacientes", "Carteira-mãe de pacientes que já realizaram pelo menos um atendimento com a SoproLife."],
    ["  Finalidade",
      "Uma linha por pessoa. Representa o relacionamento geral com o paciente, " +
      "não um atendimento específico. É o ponto central de gestão da carteira ativa."],
    ["  Regra principal",
      "Alimentada automaticamente pela função sincronizarCRMPacientesSoproLife(), " +
      "que consolida dados de CRM Espirometria e CRM Consultas. " +
      "Não editar manualmente a menos que necessário."],
    ["  Chave de deduplicação",
      "1. Telefone normalizado (sem espaços/símbolos) tem prioridade.\n" +
      "2. Se não houver telefone, o primeiro_nome normalizado é usado.\n" +
      "3. Dois registros com a mesma chave = mesmo paciente."],
    ["  O que NÃO inserir",
      "Diagnósticos, laudos, resultados de exames ou qualquer dado clínico sensível. " +
      "Use apenas dados de relacionamento e logística de atendimento."],

    // ── CRM ESPIROMETRIA ────────────────────────────────────────────────────
    ["▶ CRM Espirometria", "Histórico de exames de espirometria realizados. Uma linha por exame."],
    ["  Finalidade",
      "Registrar cada exame de espirometria individualmente, seja presencial ou domiciliar. " +
      "Alimenta automaticamente o CRM Pacientes via sincronização."],
    ["  Regra principal",
      "Cada linha representa um exame, não um paciente. " +
      "O mesmo paciente pode ter múltiplas linhas se fez vários exames."],
    ["  Campos importantes",
      "exame_id, data_exame, servico (Espirometria / Espirometria domiciliar), " +
      "status_exame, proximo_contato, motivo_proximo_contato."],

    // ── CRM CONSULTAS ───────────────────────────────────────────────────────
    ["▶ CRM Consultas", "Histórico de consultas e teleconsultas realizadas. Uma linha por consulta."],
    ["  Finalidade",
      "Registrar cada consulta médica ou teleconsulta individualmente. " +
      "Alimenta automaticamente o CRM Pacientes via sincronização."],
    ["  Regra principal",
      "Cada linha representa uma consulta, não um paciente. " +
      "Um paciente pode ter múltiplas consultas ao longo do tempo."],
    ["  Campos importantes",
      "consulta_id, data_consulta, tipo_consulta, status, medica, " +
      "proximo_contato, motivo_proximo_contato."],

    // ── FOLLOW-UP WHATSAPP ─────────────────────────────────────────────────
    ["▶ Follow-up WhatsApp", "Fila de mensagens e contatos futuros a serem enviados via WhatsApp."],
    ["  Finalidade",
      "Organizar a agenda de follow-ups ativos: retornos pendentes, confirmações de agendamento, " +
      "pós-atendimento e reengajamento de inativos."],
    ["  Regra principal",
      "Diferente do CRM Pacientes, esta aba é uma fila de ações futuras, não um cadastro permanente. " +
      "Cada linha representa uma mensagem ou contato planejado, com data prevista e status."],
    ["  Campos importantes",
      "followup_id, data_prevista, tipo_mensagem, status (Pendente / Enviado / Cancelado), " +
      "template_usado, consentimento."],
    ["  Privacidade",
      "Manter consentimento_whatsapp atualizado para cada contato. " +
      "Não enviar mensagens sem consentimento explícito registrado."],

    // ── CRM CLINICAS ────────────────────────────────────────────────────────
    ["▶ CRM Clinicas", "Gestão de relacionamento com clínicas, consultórios e parceiros comerciais."],
    ["  Finalidade",
      "Controlar o pipeline B2B com clínicas e consultórios: abordagem, negociação, proposta e parceria ativa. " +
      "Diferente dos leads de pacientes, aqui o foco é institucional."],
    ["  Etapas",
      "Prospectar → Abordada → Respondeu → Reunião → Proposta → Parceira → Sem resposta → Pausada."],
    ["  Campos importantes",
      "clinica_id, nome_clinica, bairro, regiao, tipo_clinica, etapa, " +
      "ultima_interacao, proxima_acao, prioridade."],

    // ── BASE PROSPECÇÃO B2B PCMSO ──────────────────────────────────────────
    ["▶ Base Prospecção B2B PCMSO",
      "Lista de clínicas e empresas ainda em prospecção para PCMSO e medicina do trabalho."],
    ["  Finalidade",
      "Registrar potenciais parceiros/clientes empresariais que ainda não foram abordados ou estão em fase inicial de contato. " +
      "Diferente do CRM Clinicas, aqui estão os alvos que ainda não responderam nem confirmaram interesse."],
    ["  Regra principal",
      "Quando uma empresa responde e avança na negociação, migrar para CRM Clinicas. " +
      "Esta aba funciona como funil de entrada B2B."],

    // ── TAREFAS ─────────────────────────────────────────────────────────────
    ["▶ Tarefas", "Lista de pendências operacionais, comerciais, de marketing e documentos."],
    ["  Finalidade",
      "Centralizar todas as tarefas da equipe SoproLife em uma única visão, " +
      "com prioridade, responsável e prazo."],
    ["  Áreas",
      "Operação, Comercial, Marketing, SEO, Documentos, Financeiro, Tecnologia, Atendimento."],
    ["  Status",
      "Pendente → Em andamento → Concluída. Use 'Aguardando' para dependências externas " +
      "e 'Cancelada' para tarefas descartadas."],

    // ── FINANCEIRO ──────────────────────────────────────────────────────────
    ["▶ Financeiro", "Registro de receitas, despesas, pendências e previsões financeiras."],
    ["  Finalidade",
      "Acompanhar o fluxo financeiro da operação: o que foi recebido, o que está pendente " +
      "e o que está previsto para entrar."],
    ["  Tipos",
      "Receita (valor gerado), Despesa (custo operacional), Previsão (estimativa futura)."],
    ["  Privacidade",
      "Não identificar pacientes por pagamento. Usar categorias agregadas. " +
      "Campo observacao_anonima para notas sem dados identificáveis."],

    // ── RESUMO DASHBOARD ────────────────────────────────────────────────────
    ["▶ Resumo Dashboard", "Aba gerada automaticamente com indicadores consolidados para o painel."],
    ["  Finalidade",
      "Agregar métricas de todas as abas operacionais em um formato leve para leitura pelo Painel SoproLife. " +
      "Não editar manualmente."],
    ["  Como atualizar",
      "Executar a função atualizarResumoDashboardSoproLife() ou usar o menu SoproLife → Atualizar Resumo Dashboard. " +
      "Também atualiza automaticamente via gatilho onEdit nas abas monitoradas."],

    // ── MARKETING CONTEUDO ─────────────────────────────────────────────────
    ["▶ Marketing Conteudo", "Planejamento e controle de posts, campanhas, SEO e publicações."],
    ["  Finalidade",
      "Organizar o calendário editorial da SoproLife: temas, formatos, datas planejadas " +
      "e datas de publicação em cada canal."],
    ["  Canais",
      "Instagram, LinkedIn, Google Perfil, Site (blog/SEO), WhatsApp (broadcast), E-mail."],
    ["  Status",
      "Ideia → Roteiro → Arte → Revisão → Agendado → Publicado."],

    // ── AGENDA OPERACIONAL ─────────────────────────────────────────────────
    ["▶ Agenda Operacional", "Registro de exames, consultas, reuniões e visitas da operação."],
    ["  Finalidade",
      "Organizar a agenda diária/semanal da equipe: atendimentos, visitas comerciais, " +
      "reuniões com parceiros e tarefas presenciais."],
    ["  Privacidade",
      "Não inserir nome completo de pacientes. Usar primeiro nome ou código de evento. " +
      "Dados clínicos ficam em CRM Espirometria / CRM Consultas."],
    ["  Status",
      "Agendado → Confirmado → Realizado → Remarcar → Cancelado."],

    // ── LOG CENTRO COMANDO ──────────────────────────────────────────────────
    ["▶ Log Centro Comando", "Histórico de automações, sincronizações e ações do SoproLife Command Center."],
    ["  Finalidade",
      "Registrar cada execução automática: quando rodou, o que fez, quantos registros processou " +
      "e se houve erros. Permite auditoria e rastreabilidade."],
    ["  Regra principal",
      "Aba somente para leitura após preenchimento automático. " +
      "Não editar manualmente exceto para adicionar observações de suporte."],

    // ── RODAPÉ ──────────────────────────────────────────────────────────────
    ["", ""],
    ["Gerado por", "organizarLeadsEManualPlanilhasSoproLife()  |  SoproLife Command Center"],
    ["Atualizado em", Utilities.formatDate(new Date(), "America/Sao_Paulo", "dd/MM/yyyy HH:mm")],
  ];
}

// ── Alerta seguro ─────────────────────────────────────────────────────────────

/**
 * Exibe um alerta via getUi() quando disponível (execução por menu/UI do Sheets).
 * Em contexto de execução direta pelo editor de script, getUi() lança exceção;
 * o try/catch garante que a função principal não quebre — o log já registrou tudo.
 */
function mostrarAlertaSeguroSoproLife(mensagem) {
  try {
    SpreadsheetApp.getUi().alert(mensagem);
  } catch (e) {
    Logger.log("(alerta via UI indisponível neste contexto — veja o log acima)");
  }
}

// ── Menu e integração ─────────────────────────────────────────────────────────

/**
 * Adiciona o item ao menu SoproLife na abertura da planilha.
 * Compatível com o onOpen de soprolife-sheets-template.gs:
 * combine os dois addItem no mesmo createMenu ou use este separadamente.
 */
function onOpen_limparLeads() {
  SpreadsheetApp.getUi()
    .createMenu("SoproLife")
    .addItem("Limpar Leads demo + Manual das Abas", "organizarLeadsEManualPlanilhasSoproLife")
    .addToUi();
}
