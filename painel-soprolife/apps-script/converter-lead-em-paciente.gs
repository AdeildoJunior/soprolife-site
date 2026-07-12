/**
 * converter-lead-em-paciente.gs
 * Versão: 5.0 — BLOQUEADO PELA M14.3A (2ª rodada)  |  SoproLife Command Center
 *
 * ═══════════════════════════════════════════════════════════════════
 *  A CONVERSÃO AUTOMÁTICA DE LEADS ESTÁ BLOQUEADA. NÃO REATIVAR.
 * ═══════════════════════════════════════════════════════════════════
 *
 * Motivo (segunda auditoria independente, BLOQUEADOR-04): o fluxo antigo
 * (versões ≤ 4.x) contornava a arquitetura canônica:
 *
 *   - deduplicava paciente por TELEFONE como prova absoluta (telefone
 *     existente ⇒ "mesma pessoa", sem decisão humana) — pessoas que
 *     compartilham telefone eram tratadas como uma só;
 *   - aceitava data impossível (ex.: 31/02/2026) como data factual de
 *     exame/consulta, por validar só com regex de formato;
 *   - escrevia nos CRMs sem validar o cabeçalho pelo contrato fail-closed
 *     (append em schema incompatível);
 *   - criava exames/consultas SEM propagar o paciente_id — eventos novos
 *     nasciam sem vínculo explícito com a pessoa;
 *   - removia a linha do lead (deleteRow) como parte do fluxo.
 *
 * A conversão correta (telefone apenas candidato, ambiguidade explícita,
 * paciente_id propagado, contrato fail-closed, datas valor+precisão) será
 * reimplementada na M14.3B sobre os writers canônicos do Command Center.
 * Até lá:
 *
 *   - converta manualmente: registre o atendimento pela tela "Nova
 *     Espirometria"/"Nova Consulta" do painel (writers canônicos) e
 *     atualize a etapa do lead;
 *   - a auditoria read-only (scripts/reconciliar-historico.py) mostra os
 *     vínculos pendentes/ambíguos para decisão humana.
 *
 * O código antigo foi REMOVIDO deste arquivo (histórico completo no git).
 * Guarda estática: scripts/test-guardas-estaticas.py falha se deleteRow,
 * dedupe por telefone ou datas por regex voltarem a este arquivo.
 */

var _CV_CONVERSAO_BLOQUEADA =
  "A conversão automática de leads foi BLOQUEADA pela M14.3A (2ª rodada): o fluxo " +
  "antigo deduplicava pessoas por telefone, aceitava datas impossíveis, gravava sem " +
  "contrato fail-closed e não propagava paciente_id. Registre o atendimento pela tela " +
  "'Nova Espirometria'/'Nova Consulta' do painel e aguarde a conversão canônica (M14.3B). " +
  "Nenhum dado foi alterado.";

/**
 * BLOQUEADA — acionador instalável antigo. Não lança erro (contexto de
 * trigger), mas não faz NADA além de registrar o bloqueio no log.
 * Remova o acionador em Extensões → Apps Script → Acionadores.
 */
function onEditConversaoLeadsSoproLife(e) {
  Logger.log(_CV_CONVERSAO_BLOQUEADA);
}

/**
 * BLOQUEADA — antiga conversão manual via menu. Informa o bloqueio na UI
 * quando disponível e nunca converte.
 */
function converterLeadSelecionadoSoproLife() {
  Logger.log(_CV_CONVERSAO_BLOQUEADA);
  try {
    SpreadsheetApp.getUi().alert("Conversão bloqueada (M14.3A)", _CV_CONVERSAO_BLOQUEADA,
                                 SpreadsheetApp.getUi().ButtonSet.OK);
  } catch (err) {
    // Sem UI (editor/trigger): o log acima já registrou o bloqueio.
  }
}

/**
 * BLOQUEADA — núcleo antigo chamado por updateLeadStage (command-center-api).
 * Lança erro explícito para que a ação do painel responda com a causa real
 * em vez de fingir que converteu.
 */
function _cvConverterLeadCore(ss, leadsSheet, linha, etapa) {
  throw new Error(_CV_CONVERSAO_BLOQUEADA);
}
