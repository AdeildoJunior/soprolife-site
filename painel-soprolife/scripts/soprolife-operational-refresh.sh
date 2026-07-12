#!/usr/bin/env bash
# soprolife-operational-refresh.sh — Comando operacional único de frescor (M14.3A.1)
#
# Distingue com honestidade: dado atualizado, dado vencido, dado indisponível,
# sincronização com erro, Apps Script pendente de publicação.
#
# USO:
#   bash painel-soprolife/scripts/soprolife-operational-refresh.sh <modo>
#
# MODOS:
#   status             visão geral (100% offline, sem rede)
#   check              validação com exit codes do contrato (offline)
#   refresh-marketing  sincroniza Marketing/SEO (ÚNICO modo com rede;
#                      requer autorização humana — nunca roda sozinho)
#   check-manual       confere manifesto ↔ manual-das-abas.gs ↔ status
#   prepare-apps-script  regenera o .gs e mostra instruções de publicação
#   all                status + check
#
# GARANTIAS:
#   - NUNCA publica Apps Script (publicação é sempre humana, no editor);
#   - NUNCA abre navegador nem executa autenticação;
#   - detecta ADC ausente/expirado e explica o próximo passo humano;
#   - snapshot válido anterior é sempre preservado em falha;
#   - nenhuma saída contém token, credencial ou path privado.
#
# Exit codes (core/contracts/freshness-contract.json):
#   0=fresh · 10=stale · 11=autenticação · 12=schema · 13=indisponível
#   14=erro · 15=desconhecido · 2=uso incorreto

set -u
cd "$(dirname "$0")/../../" || exit 1

MARKETING_SCRIPT="painel-soprolife/scripts/read-marketing-seo-adc.py"
MANUAL_SCRIPT="painel-soprolife/scripts/generate-manual-abas-gs.py"
ADC_FILE="$HOME/.config/gcloud/application_default_credentials.json"
SNAPSHOT="painel-soprolife/data/marketing-seo.local.json"

# Python do venv quando existir (mesma regra do update-local-data.sh).
VENV_PYTHON="$HOME/.local/share/soprolife/venvs/google-sheets/bin/python"
if [ -f "$VENV_PYTHON" ]; then PYTHON="$VENV_PYTHON"; else PYTHON="python3"; fi

uso() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; }

titulo() { echo; echo "══ $* ══"; }

status_marketing() {
  titulo "Marketing/SEO — frescor do snapshot local"
  python3 "$MARKETING_SCRIPT" --status
  local rc=$?
  if [ -f "$SNAPSHOT" ]; then
    echo "  Snapshot preservado: sim"
  else
    echo "  Snapshot preservado: não (nunca sincronizado neste ambiente)"
  fi
  if [ ! -f "$ADC_FILE" ]; then
    echo "  ADC: ausente — próxima sincronização exigirá login humano:"
    echo "       gcloud auth application-default login (com escopos SC/GA4)"
  fi
  return $rc
}

status_manual() {
  titulo "Manual das Abas — manifesto / geração / publicação"
  python3 "$MANUAL_SCRIPT" --status
  echo "  Apps Script publicado nesta execução: não (nunca é automático)"
}

check_marketing() {
  titulo "Marketing/SEO — check de contrato e frescor"
  python3 "$MARKETING_SCRIPT" --check
}

check_manual() {
  titulo "Manual das Abas — check de sincronização"
  python3 "$MANUAL_SCRIPT" --check
}

refresh_marketing() {
  titulo "Marketing/SEO — sincronização (com rede, autorizada pelo humano)"

  if [ ! -f "$ADC_FILE" ]; then
    echo "AUTENTICAÇÃO NECESSÁRIA: ADC não encontrado."
    echo
    echo "Próximo passo humano (este comando NUNCA abre navegador sozinho):"
    echo "  gcloud auth application-default login --no-launch-browser \\"
    echo "    --scopes=\"https://www.googleapis.com/auth/webmasters.readonly,https://www.googleapis.com/auth/analytics.readonly\""
    echo
    echo "Snapshot atual foi preservado. Depois de autenticar, rode novamente:"
    echo "  bash painel-soprolife/scripts/soprolife-operational-refresh.sh refresh-marketing"
    return 11
  fi

  echo "Validando com dry-run antes de gravar..."
  if ! "$PYTHON" "$MARKETING_SCRIPT" --dry-run; then
    echo
    echo "Dry-run indicou problema (ver estados acima). Gravando mesmo assim"
    echo "seria seguro (snapshot é preservado), mas o estado ficará registrado."
  fi
  echo
  echo "Gravando snapshot (escrita atômica; snapshot anterior preservado em falha)..."
  "$PYTHON" "$MARKETING_SCRIPT" --write
  local rc=$?
  echo
  status_marketing || true
  return $rc
}

prepare_apps_script() {
  titulo "Manual das Abas — preparação do Apps Script (NUNCA publica)"
  python3 "$MANUAL_SCRIPT"
  local rc=$?
  echo
  echo "PUBLICAÇÃO (passos humanos, fora deste repositório):"
  echo "  1. Abra a planilha privada no Google Sheets."
  echo "  2. Extensões → Apps Script → substitua o conteúdo pelo arquivo:"
  echo "       painel-soprolife/apps-script/manual-das-abas.gs"
  echo "  3. Selecione atualizarManualDasAbasSoproLife e clique em Executar."
  echo "  4. Confira a aba 'Manual das Abas' (linha 'Atualizado em' deve ter a data de hoje)."
  echo "  5. Registre a publicação no repositório:"
  echo "       python3 $MANUAL_SCRIPT --mark-published"
  echo
  echo "Se o Web App do Apps Script também mudou: Implantar → Gerenciar"
  echo "implantações → Nova versão (salvar no editor NÃO basta)."
  return $rc
}

resumo_final() {
  titulo "Resumo operacional"
  python3 "$MARKETING_SCRIPT" --status 2>/dev/null | sed 's/^/  /' || true
  echo
  python3 "$MANUAL_SCRIPT" --status 2>/dev/null | sed 's/^/  /' || true
  echo
  echo "  Apps Script publicado nesta execução: não"
}

MODO="${1:-}"
case "$MODO" in
  status)
    status_marketing; RC=$?
    status_manual
    resumo_final
    exit $RC
    ;;
  check)
    check_marketing; RC1=$?
    check_manual;    RC2=$?
    if [ "$RC2" -ne 0 ] && { [ "$RC1" -eq 0 ] || [ "$RC2" -gt "$RC1" ]; }; then RC1=$RC2; fi
    exit $RC1
    ;;
  refresh-marketing)
    refresh_marketing
    exit $?
    ;;
  check-manual)
    check_manual
    exit $?
    ;;
  prepare-apps-script)
    prepare_apps_script
    exit $?
    ;;
  all)
    status_marketing; RC=$?
    status_manual
    check_manual || RC=$?
    resumo_final
    exit $RC
    ;;
  -h|--help|help)
    uso; exit 0
    ;;
  *)
    echo "ERRO: modo inválido ou ausente: '${MODO}'"
    echo
    uso
    exit 2
    ;;
esac
