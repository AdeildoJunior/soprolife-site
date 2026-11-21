#!/usr/bin/env bash
# uninstall-backup-postgres.sh — Rollback do backup automático do PostgreSQL M15.
#
# Desabilita e remove o service + timer. NUNCA remove backups já gerados
# (.../backups/m15/daily/*, manual/*, <STAMP>/*): dados são preservados.
#
# Uso (na VPS, como root):
#   bash painel-soprolife/scripts/uninstall-backup-postgres.sh          # dry-run
#   bash painel-soprolife/scripts/uninstall-backup-postgres.sh --apply
set -euo pipefail

DST_DIR="/etc/systemd/system"
NAME="soprolife-backup-postgres"

DRY_RUN=1
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --apply)   DRY_RUN=0 ;;
    *) echo "ERRO: argumento desconhecido: $arg"; exit 2 ;;
  esac
done

run() {
  if [ "$DRY_RUN" -eq 1 ]; then echo "  [dry-run] $*"; else echo "  + $*"; "$@"; fi
}

if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  echo "ERRO: --apply exige root (sudo)."; exit 1
fi

echo "═══ SoproLife — desinstalador do backup automático PostgreSQL ═══"
echo "(backups já gerados NÃO são removidos)"
echo
run systemctl disable --now "$NAME.timer"
run rm -f "$DST_DIR/$NAME.timer"
run rm -f "$DST_DIR/$NAME.service"
run systemctl daemon-reload
echo
echo "Feito. Os arquivos .bak de units anteriores (se houver) foram preservados."
