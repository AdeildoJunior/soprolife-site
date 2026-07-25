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
#   - snapshot válido anterior é sempre preservado em falha;
#   - nenhuma saída contém token, credencial ou path privado.
#
# M23 — a credencial de produção de Search Console/GA4 é uma CONTA DE SERVIÇO
# dedicada, somente leitura (SOPROLIFE_MARKETING_CREDENTIALS). O ADC pessoal
# deixou de ser dependência: ele vence, e um login vencido não pode derrubar
# a saúde do painel. Os modos de Apps Script/Manual das Abas são utilitários
# LEGADOS e ficam bloqueados enquanto a fonte canônica for o PostgreSQL.
#
# Exit codes (core/contracts/freshness-contract.json):
#   0=fresh · 10=stale · 11=autenticação · 12=schema · 13=indisponível
#   14=erro · 15=desconhecido · 2=uso incorreto

set -u
cd "$(dirname "$0")/../../" || exit 1

MARKETING_SCRIPT="painel-soprolife/scripts/read-marketing-seo-adc.py"
MANUAL_SCRIPT="painel-soprolife/scripts/generate-manual-abas-gs.py"
# Credencial durável de produção (conta de serviço, somente leitura).
MARKETING_CREDENTIAL="${SOPROLIFE_MARKETING_CREDENTIALS:-/opt/soprolife/secrets/marketing-readonly.json}"
# ADC pessoal: aceito SOMENTE como conveniência de desenvolvimento local.
# Nunca é requisito de produção e sua ausência não é falha.
ADC_FILE="$HOME/.config/gcloud/application_default_credentials.json"
SNAPSHOT="painel-soprolife/data/marketing-seo.local.json"
MODE_SCRIPT="painel-soprolife/scripts/data_source_mode.py"

# Bloqueia os modos legados de Apps Script quando a fonte canônica é o banco.
bloquear_legado() {
  if python3 "$MODE_SCRIPT" --check >/dev/null 2>&1; then
    echo "BLOQUEADO (M23): '$1' é um utilitário legado de Google Sheets/Apps"
    echo "Script e o painel opera em modo postgresql_only."
    echo
    echo "A fonte operacional é o PostgreSQL. Para uso humano pontual de"
    echo "migração/forense, exporte SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1."
    return 0
  fi
  return 1
}

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
  if [ -f "$MARKETING_CREDENTIAL" ]; then
    echo "  Credencial: conta de serviço dedicada (somente leitura)."
  else
    echo "  Credencial: conta de serviço ausente neste ambiente."
    echo "       Produção: instalar a chave em /opt/soprolife/secrets/."
    echo "       O ADC pessoal NÃO é requisito e não deve ser renovado."
  fi
  return $rc
}

status_manual() {
  titulo "Manual das Abas — manifesto / geração / publicação"
  # M23 — utilitário legado da planilha. Em modo postgresql_only ele não
  # descreve nenhuma fonte ativa, e mostrá-lo como estado operacional seria
  # exatamente a informação enganosa que o M23 elimina.
  if python3 "$MODE_SCRIPT" --check >/dev/null 2>&1; then
    echo "  Legado: a fonte canônica é o PostgreSQL; o Manual das Abas descreve"
    echo "  a planilha descomissionada e não é estado de produção."
    return 0
  fi
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

  if [ -f "$MARKETING_CREDENTIAL" ]; then
    export SOPROLIFE_MARKETING_CREDENTIALS="$MARKETING_CREDENTIAL"
    export SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT=1
    echo "Credencial: conta de serviço dedicada (somente leitura)."
  elif [ -f "$ADC_FILE" ]; then
    echo "Credencial: ADC de desenvolvimento local (não é caminho de produção)."
  else
    echo "CREDENCIAL AUSENTE: nenhuma conta de serviço de leitura encontrada."
    echo
    echo "Próximo passo humano (produção):"
    echo "  instalar a chave da conta de serviço em"
    echo "  /opt/soprolife/secrets/marketing-readonly.json (0600)."
    echo
    echo "Isto afeta SOMENTE Marketing/SEO. Os dados operacionais vêm do"
    echo "PostgreSQL e não dependem desta credencial."
    echo "Snapshot atual foi preservado."
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
  if python3 "$MODE_SCRIPT" --check >/dev/null 2>&1; then
    echo "  Fonte operacional: PostgreSQL (Núcleo M15) — Sheets descomissionado."
  else
    python3 "$MANUAL_SCRIPT" --status 2>/dev/null | sed 's/^/  /' || true
    echo
    echo "  Apps Script publicado nesta execução: não"
  fi
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
    # M23 — o Manual das Abas descreve a planilha legada. Em modo
    # postgresql_only ele não é dependência e não pode contaminar a saúde
    # geral do painel.
    if ! python3 "$MODE_SCRIPT" --check >/dev/null 2>&1; then
      check_manual; RC2=$?
      if [ "$RC2" -ne 0 ] && { [ "$RC1" -eq 0 ] || [ "$RC2" -gt "$RC1" ]; }; then RC1=$RC2; fi
    else
      echo
      echo "Manual das Abas: ignorado (utilitário legado; fonte canônica é o PostgreSQL)."
    fi
    exit $RC1
    ;;
  refresh-marketing)
    refresh_marketing
    exit $?
    ;;
  check-manual)
    bloquear_legado "check-manual" && exit 3
    check_manual
    exit $?
    ;;
  prepare-apps-script)
    bloquear_legado "prepare-apps-script" && exit 3
    prepare_apps_script
    exit $?
    ;;
  all)
    status_marketing; RC=$?
    if ! python3 "$MODE_SCRIPT" --check >/dev/null 2>&1; then
      status_manual
      check_manual || RC=$?
    else
      echo
      echo "Manual das Abas: ignorado (utilitário legado; fonte canônica é o PostgreSQL)."
    fi
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
