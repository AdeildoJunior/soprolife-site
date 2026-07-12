/**
 * pastore-staging.gs — Writer ISOLADO da aba "Parceria Pastore - Atendimentos"
 * Versão: M14.3A  |  SoproLife Command Center
 *
 * ═══════════════════════════════════════════════════════════════════
 *  STAGING / NÃO CANÔNICO — leia antes de usar.
 * ═══════════════════════════════════════════════════════════════════
 *
 * A aba "Parceria Pastore - Atendimentos" é STAGING do acordo comercial:
 *   - NÃO é base canônica de pessoas (o cadastro de pacientes é o
 *     CRM Pacientes, mestre persistente);
 *   - NÃO é fonte de valores do painel (a única fonte monetária é
 *     Financeiro_Lancamentos; a Config da parceria não é prova de preço);
 *   - Pastore, no modelo canônico, é DIMENSÃO do exame
 *     (local_atendimento=Parceiro, parceiro=Pastore, unidade=Pastore
 *     Ipanema em CRM Espirometria) — ver
 *     core/contracts/registros-schemas.json e
 *     docs/arquitetura-canonica-abas.md.
 *
 * Este writer foi separado do command-center-api.gs na M14.3A exatamente
 * para deixar essa fronteira explícita. A integração dos atendimentos ao
 * histórico central (id_atendimento + CRM Espirometria +
 * Financeiro_Lancamentos, via outbox idempotente) é a M14.3B — até lá,
 * cada linha gravada aqui aparece na auditoria read-only
 * (reconciliar-historico.py) como "fora do histórico central".
 *
 * Dependências (mesmo projeto Apps Script): command-center-api.gs
 * (_required, _getOrCreateSheet, _ensureSheetHeader, _buildRow,
 * _firstEmptyRowByHeaders, _writeRowSkippingColumnsSemValidacao,
 * _ensureFormulaColumns, _logEntry, _auditAcao, _ok) — o doPost daquele
 * arquivo é quem roteia a ação registrarAtendimentoPastore para cá, com
 * guarda explícita se este arquivo não estiver instalado.
 *
 * SEGURANÇA: paciente_nome/paciente_whatsapp/observação nunca saem desta
 * planilha nem do data-private; o summary público só lê agregados.
 */

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
  _required(data, ["data_atendimento", "paciente_nome", "tipo_exame", "status"]);
  if (typeof ctValidarDataComPrecisao !== "function") {
    return _err("contratos-canonicos.gs não está instalado — gravação de staging bloqueada (fail-closed).", 500);
  }

  // M14.3A (2ª rodada) — mesmo validador de data do contrato: data impossível
  // é erro; mês/ano preserva precisão; ausência nunca vira hoje (o campo é
  // obrigatório e vem sem pré-preenchimento na UI).
  var dataAtendimento = ctValidarDataComPrecisao(data.data_atendimento, data.data_atendimento_precisao);
  if (!dataAtendimento.ok) return _err("data_atendimento: " + dataAtendimento.erro, 400);

  // status é OBRIGATÓRIO e fechado — ausência não vira "Realizado".
  var statusPastore = String(data.status || "").trim();
  var statusValidos = ["Realizado", "Cancelado", "Reagendado", "Não compareceu"];
  if (statusValidos.indexOf(statusPastore) < 0) {
    return _err("status inválido para atendimento Pastore: " + (statusPastore || "(vazio)"), 400);
  }

  var auditRequestId = Utilities.getUuid();
  var auditT0        = Date.now();

  var sheet = _getOrCreateSheet(_SHEETS.PARCERIA_PASTORE_ATENDIMENTOS);
  _ensureSheetHeader(sheet, _PARCERIA_PASTORE_ATENDIMENTOS_CABECALHO);

  // AUSÊNCIA PERMANECE AUSENTE: repasse/custos não informados ficam em
  // BRANCO (nunca 0 factual automático — 0 só quando digitado). As colunas
  // calculadas (S/T/U) continuam por fórmula da planilha.
  function _numOuVazio(v) {
    return (v !== undefined && v !== null && String(v).trim() !== "") ? v : "";
  }

  var row = _buildRow(sheet, {
    data_atendimento:             dataAtendimento.valor,
    unidade:                      data.unidade                      || "Pastore Ipanema",
    dia_semana:                   data.dia_semana                   || "",
    horario_inicio:               data.horario_inicio               || "",
    horario_fim:                  data.horario_fim                  || "",
    origem:                       data.origem                       || "Pastore",
    paciente_nome:                data.paciente_nome                || "",
    paciente_whatsapp:            data.paciente_whatsapp            || "",
    tipo_exame:                   data.tipo_exame                   || "",
    broncodilatador:              data.broncodilatador              || "Não",
    valor_cobrado:                _numOuVazio(data.valor_cobrado),
    forma_pagamento:              data.forma_pagamento              || "",
    recebido_por:                 data.recebido_por                 || "SoproLife",
    repasse_pastore:              _numOuVazio(data.repasse_pastore),
    custo_insumo:                 _numOuVazio(data.custo_insumo),
    custo_deslocamento:           _numOuVazio(data.custo_deslocamento),
    custo_profissional:           _numOuVazio(data.custo_profissional),
    outros_custos:                _numOuVazio(data.outros_custos),
    status:                       statusPastore,
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

  var newRow = _firstEmptyRowByHeaders(sheet, ["data_atendimento", "paciente_nome", "tipo_exame"]);
  try {
    _writeRowSkippingColumnsSemValidacao(sheet, newRow, row, _PARCERIA_PASTORE_FORMULA_COLS);
    _ensureFormulaColumns(sheet, newRow, _PARCERIA_PASTORE_FORMULA_COLS);
  } catch (e) {
    // A aba não tem coluna de ID — a linha é o identificador (mesmo padrão de _logEntry abaixo).
    _auditAcao(auditRequestId, auditT0, "registrar_atendimento_pastore", "pastore", "linha-" + newRow, data, "ERROR: falha na gravacao");
    throw e;
  }

  _logEntry({
    acao:   "registrarAtendimentoPastore",
    status: "OK",
    aba:    _SHEETS.PARCERIA_PASTORE_ATENDIMENTOS,
    id:     "linha " + newRow,
    resumo: "Atendimento Pastore registrado (" + (data.tipo_exame || "") + ", status " + (data.status || "Realizado") + ").",
    // nome, whatsapp e observação NUNCA entram no log — mesma regra do resto do arquivo
  });
  _auditAcao(auditRequestId, auditT0, "registrar_atendimento_pastore", "pastore", "linha-" + newRow, data, "ok");

  return _ok({ row: newRow, message: "Atendimento Pastore registrado com sucesso." });
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
