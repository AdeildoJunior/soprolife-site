/**
 * organizar-leads-operacionais.gs
 * Versão: 4.0 — BLOQUEADO PELA M14.3A (2ª rodada)  |  SoproLife Command Center
 *
 * ═══════════════════════════════════════════════════════════════════
 *  ESTA MIGRAÇÃO DESTRUTIVA ESTÁ BLOQUEADA. NÃO EXECUTAR. NÃO REATIVAR.
 * ═══════════════════════════════════════════════════════════════════
 *
 * Motivo (segunda auditoria independente, BLOQUEADOR-05): o fluxo antigo
 * (versões ≤ 3.x) executava a limpeza total (clear de conteúdo) da aba Leads e a
 * RECONSTRUÍA integralmente com o cabeçalho novo, além de remover linhas
 * marcadas como demo. Backup automático não transforma uma migração
 * destrutiva em manutenção diária segura: perda de colunas extras,
 * fórmulas, validações e linhas por falso positivo continuava possível
 * em um clique.
 *
 * A migração de cabeçalho da aba Leads é uma MIGRAÇÃO DESTRUTIVA e, pela
 * arquitetura M14.3A, só pode acontecer com:
 *   - backup criado E validado;
 *   - manifesto do que será alterado (dry-run comparado);
 *   - aprovação humana explícita;
 *   - ambiente controlado (M14.3B).
 *
 * Manutenção diária deve ser ADITIVA (ver limpar-leads-e-manual-abas.gs:
 * dropdowns, notas e Manual — sem remoção de linhas).
 *
 * O código antigo foi REMOVIDO deste arquivo (histórico completo no git).
 * Guarda estática: scripts/test-guardas-estaticas.py falha se
 * clearContents/reconstrução voltarem a este arquivo.
 */

var _OP_LEADS_BLOQUEADO =
  "organizarLeadsOperacionaisSoproLife foi BLOQUEADO pela M14.3A (2ª rodada): o fluxo " +
  "antigo limpava (clearContents) e reconstruía a aba Leads inteira — isso é migração " +
  "destrutiva e exige backup validado, manifesto, dry-run comparado e aprovação " +
  "explícita (M14.3B). Nenhum dado foi alterado.";

/**
 * BLOQUEADA — lança erro sempre. Mantida apenas para que qualquer menu ou
 * atalho antigo falhe de forma explícita em vez de executar o fluxo
 * destrutivo. O item de menu correspondente foi removido.
 */
function organizarLeadsOperacionaisSoproLife() {
  Logger.log(_OP_LEADS_BLOQUEADO);
  throw new Error(_OP_LEADS_BLOQUEADO);
}
