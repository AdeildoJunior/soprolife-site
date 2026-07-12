/**
 * limpar-leads-e-manual-abas.gs
 * Versão: 1.2  |  SoproLife Command Center
 *
 * Padroniza dropdowns da aba Leads, adiciona notas de cabeçalho e atualiza a
 * aba "Manual das Abas" (delegando ao gerador do manifesto). Fluxo ADITIVO:
 * desde a M14.3A (2ª rodada) NENHUMA linha é removida automaticamente — a
 * antiga limpeza de "dados demonstrativos" (deleteRow por substring) foi
 * removida por risco de falso positivo; exclusão é decisão humana com backup.
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

  // 3. M14.3A (2ª rodada) — a remoção automática de linhas "demonstrativas"
  // foi REMOVIDA: deleteRow por substring em qualquer célula podia excluir
  // linha real por falso positivo (ex.: observação contendo a palavra
  // "teste"). Exclusão de linha é decisão humana, caso a caso, com backup
  // validado. Esta função hoje é ADITIVA: dropdowns, notas e Manual.
  var removidas = 0;
  Logger.log("Linhas demonstrativas: remoção automática BLOQUEADA (revisão humana).");

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
    "Linhas demo: remoção automática bloqueada (M14.3A — revisão humana)\n" +
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
  var hhmmss   = Utilities.formatDate(agora, "America/Sao_Paulo", "HHmmss");
  // M14.3A — nome realmente único (segundos + fragmento de UUID): duas
  // execuções no mesmo minuto geram backups DISTINTOS. Nenhum backup é
  // excluído automaticamente — retenção/limpeza é sempre ação humana
  // separada, nunca durante a criação de um backup novo.
  var sufixo = Utilities.getUuid().slice(0, 8);
  var nomeBackup = "_Backup_Leads_Demo_" + yyyymmdd + "_" + hhmmss + "_" + sufixo;

  if (ss.getSheetByName(nomeBackup)) {
    // Estatisticamente impossível; se acontecer, parar é mais seguro que apagar.
    throw new Error("Backup com nome já existente: " + nomeBackup + " — nada foi apagado.");
  }

  // copyTo() insere a cópia no final da planilha por padrão
  abaLeads.copyTo(ss).setName(nomeBackup);

  return nomeBackup;
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
 * Cria ou atualiza a aba "Manual das Abas".
 *
 * M14.3A — o Manual é gerado EXCLUSIVAMENTE a partir do manifesto canônico
 * (core/contracts/abas-manifest.json → generate-manual-abas-gs.py →
 * manual-das-abas.gs). O conteúdo legado que vivia aqui foi REMOVIDO: estava
 * desatualizado (citava a aba "Financeiro", removida na M14.x, e a antiga
 * deduplicação por nome) e gerar documentação sabidamente falsa com mensagem
 * de sucesso é pior do que falhar.
 *
 * Sem manual-das-abas.gs instalado, esta função LANÇA ERRO dizendo
 * exatamente qual arquivo instalar — nunca gera fallback.
 */
function _criarOuAtualizarManualAbas(ss) {
  if (typeof atualizarManualDasAbasSoproLife !== "function") {
    throw new Error(
      "Manual das Abas NÃO gerado: o arquivo apps-script/manual-das-abas.gs " +
      "não está instalado neste projeto Apps Script. Gere-o com " +
      "python3 painel-soprolife/scripts/generate-manual-abas-gs.py, cole no " +
      "editor do Apps Script e execute novamente. (O conteúdo legado foi " +
      "removido na M14.3A por estar desatualizado — não existe fallback.)"
    );
  }
  Logger.log("Manual das Abas: delegando para atualizarManualDasAbasSoproLife() (manifesto M14.3).");
  atualizarManualDasAbasSoproLife();
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
    .addItem("Padronizar Leads + Manual das Abas (aditivo)", "organizarLeadsEManualPlanilhasSoproLife")
    .addToUi();
}
