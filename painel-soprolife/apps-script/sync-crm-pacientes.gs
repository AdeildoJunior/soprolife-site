/**
 * sync-crm-pacientes.gs
 * Versão: 2.0 — BLOQUEADO PELA M14.3A  |  SoproLife Command Center
 *
 * ═══════════════════════════════════════════════════════════════════
 *  ESTE SINCRONIZADOR ESTÁ BLOQUEADO. NÃO EXECUTAR. NÃO REATIVAR.
 * ═══════════════════════════════════════════════════════════════════
 *
 * O fluxo antigo (versões ≤ 1.1) era DESTRUTIVO e foi reprovado em
 * auditoria independente (M14.3A, achados BLOQ-01/BLOQ-02/BLOQ-04):
 *
 *   - apagava a aba "CRM Pacientes" inteira (clearContents) e a
 *     reconstruía do zero — perdendo colunas extras, fórmulas,
 *     validações, metadados e qualquer dado não modelado;
 *   - deduplicava automaticamente por telefone (prova absoluta) e,
 *     sem telefone, por PRIMEIRO NOME — podendo fundir homônimos e
 *     ocultar linhas com chave repetida;
 *   - recalculava paciente_id e convertia datas incompletas (MM/AAAA,
 *     "junho/2026") em dia 01 factual, e datas ausentes em "hoje".
 *
 * Arquitetura vigente (docs/arquitetura-canonica-abas.md +
 * core/contracts/registros-schemas.json):
 *   - CRM Pacientes é MESTRE PERSISTENTE: nenhuma reescrita integral,
 *     nenhuma linha eliminada, nenhum ID recalculado;
 *   - nome sozinho NUNCA vincula; telefone gera apenas CANDIDATO;
 *     telefone em mais de um cadastro = ambiguous; sem informação
 *     suficiente = unmatchable; candidato não confirmado = pending;
 *   - nenhuma fusão é automática — toda decisão é humana;
 *   - auditoria é read-only: scripts/reconciliar-historico.py
 *     (--audit/--dry-run) substitui este fluxo até a implementação
 *     incremental segura (M14.3B: upsert incremental com LockService,
 *     staging, validação por contrato e rollback).
 *
 * O código destrutivo foi REMOVIDO deste arquivo (histórico completo
 * no git: painel-soprolife-v01, versões anteriores a M14.3A).
 * Guarda estática: scripts/test-guardas-estaticas.py falha se
 * clearContents ou chave por nome voltarem a este arquivo.
 */

var _SYNC_CRM_PACIENTES_BLOQUEADO =
  "sincronizarCRMPacientesSoproLife foi BLOQUEADO pela M14.3A. " +
  "O fluxo antigo apagava e reconstruía a aba CRM Pacientes (mestre " +
  "persistente) e deduplicava por primeiro nome — comportamento proibido. " +
  "Use a auditoria read-only (scripts/reconciliar-historico.py --audit) e " +
  "aguarde a migração incremental segura (M14.3B). Nenhum dado foi alterado.";

/**
 * BLOQUEADO — lança erro sempre. Mantido apenas para que qualquer gatilho
 * ou menu antigo que ainda aponte para este nome falhe de forma explícita
 * em vez de executar o fluxo destrutivo.
 */
function sincronizarCRMPacientesSoproLife() {
  Logger.log(_SYNC_CRM_PACIENTES_BLOQUEADO);
  throw new Error(_SYNC_CRM_PACIENTES_BLOQUEADO);
}

// O item de menu "Sincronizar CRM Pacientes" foi removido de propósito
// (a função onOpen_syncCRM não existe mais). Se um acionador instalável
// antigo ainda chamar sincronizarCRMPacientesSoproLife, ele registrará o
// erro acima no log de execuções do Apps Script — remova o acionador em
// Extensões → Apps Script → Acionadores.
