#!/usr/bin/env bash
# uninstall-operational-refresh.sh — Rollback do timer de frescor operacional.
# M14.3A.1. Remove unidades e desabilita o timer; NÃO apaga o EnvironmentFile
# (configuração humana) a menos que --purge-env seja passado.
#
# Uso (na VPS, como root):
#   bash painel-soprolife/scripts/uninstall-operational-refresh.sh            # dry-run
#   bash painel-soprolife/scripts/uninstall-operational-refresh.sh --apply
#   bash painel-soprolife/scripts/uninstall-operational-refresh.sh --apply --purge-env

set -euo pipefail

DST_DIR="/etc/systemd/system"
ENV_DST="/etc/soprolife/operational-refresh.env"
NAME="soprolife-operational-refresh"

DRY_RUN=1
PURGE_ENV=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --apply)   DRY_RUN=0 ;;
    --purge-env) PURGE_ENV=1 ;;
    *) echo "ERRO: argumento desconhecido: $arg"; exit 2 ;;
  esac
done

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] $*"
  else
    echo "  + $*"
    "$@"
  fi
}

echo "═══ SoproLife — rollback do frescor operacional (systemd) ═══"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "MODO: dry-run (nada será alterado). Use --apply para executar."
fi
echo

if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  echo "ERRO: --apply exige root (sudo)."
  exit 1
fi

echo "1. Parando e desabilitando timer/serviço (ignora se não existirem):"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "  [dry-run] systemctl disable --now $NAME.timer"
  echo "  [dry-run] systemctl stop $NAME.service"
else
  systemctl disable --now "$NAME.timer" 2>/dev/null || true
  systemctl stop "$NAME.service" 2>/dev/null || true
fi

echo
echo "2. Removendo unidades:"
run rm -f "$DST_DIR/$NAME.service" "$DST_DIR/$NAME.timer"

echo
echo "3. daemon-reload:"
run systemctl daemon-reload

echo
if [ "$PURGE_ENV" -eq 1 ]; then
  echo "4. Removendo EnvironmentFile (autorizado por --purge-env):"
  run rm -f "$ENV_DST"
else
  echo "4. EnvironmentFile preservado: $ENV_DST"
fi

echo
echo "Rollback concluído. O painel continua funcionando com o último snapshot"
echo "válido; apenas a checagem periódica automática deixa de rodar."
