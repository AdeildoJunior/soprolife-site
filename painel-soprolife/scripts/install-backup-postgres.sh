#!/usr/bin/env bash
# install-backup-postgres.sh — Instala (por decisão humana) o service + timer
# systemd do backup automático do PostgreSQL M15 na VPS.
#
# PADRÃO É --dry-run: mostra o que faria sem tocar no sistema.
#
# Uso (na VPS, como root, dentro de /opt/soprolife/soprolife-site):
#   bash painel-soprolife/scripts/install-backup-postgres.sh             # dry-run
#   bash painel-soprolife/scripts/install-backup-postgres.sh --apply     # instala (não habilita)
#   bash painel-soprolife/scripts/install-backup-postgres.sh --apply --enable-timer
#
# O que ele NUNCA faz:
#   - habilitar o timer sem --enable-timer explícito;
#   - remover backups existentes;
#   - sobrescrever uma unit sem antes guardar backup datado (.bak).
#
# Rollback: scripts/uninstall-backup-postgres.sh
set -euo pipefail
cd "$(dirname "$0")/../../" || exit 1

SRC_DIR="painel-soprolife/systemd"
DST_DIR="/etc/systemd/system"
NAME="soprolife-backup-postgres"
DAILY_DIR="/opt/soprolife/backups/m15/daily"

DRY_RUN=1
ENABLE_TIMER=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --apply)   DRY_RUN=0 ;;
    --enable-timer) ENABLE_TIMER=1 ;;
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

echo "═══ SoproLife — instalador do backup automático PostgreSQL (systemd) ═══"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "MODO: dry-run (nada será alterado). Use --apply para instalar."
else
  echo "MODO: apply"
fi
echo

for f in "$SRC_DIR/$NAME.service" "$SRC_DIR/$NAME.timer"; do
  [ -f "$f" ] || { echo "ERRO: arquivo não encontrado: $f"; exit 1; }
done

if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  echo "ERRO: --apply exige root (sudo)."; exit 1
fi

echo "0. Pré-checagens (anti-duplicação e fuso):"
echo "   Timers de backup já presentes no sistema:"
(systemctl list-timers --all 2>/dev/null | grep -i backup) || echo "     (nenhum timer com 'backup' encontrado)"
echo "   Units com nome parecido (não deve haver um segundo mecanismo):"
(systemctl list-unit-files 2>/dev/null | grep -iE 'backup.*postgres|postgres.*backup') || echo "     (nenhuma)"
echo "   Fuso do sistema (OnCalendar usa America/Sao_Paulo; systemd >= 252):"
(timedatectl 2>/dev/null | grep -i 'time zone') || true
(systemctl --version 2>/dev/null | head -1) || true
echo

echo "1. Pasta de backups diários (0700 root, separada de deploy/manual):"
run install -d -o root -g root -m 0700 "$DAILY_DIR"
echo

echo "2. Unidades systemd (com backup datado se já existirem):"
for u in "$NAME.service" "$NAME.timer"; do
  if [ -f "$DST_DIR/$u" ]; then
    run cp -a "$DST_DIR/$u" "$DST_DIR/$u.bak.$(date +%Y%m%d-%H%M%S)"
  fi
  run cp "$SRC_DIR/$u" "$DST_DIR/$u"
done
echo

echo "3. daemon-reload:"
run systemctl daemon-reload
echo

if [ "$ENABLE_TIMER" -eq 1 ]; then
  echo "4. Habilitando timer (autorizado por --enable-timer):"
  run systemctl enable --now "$NAME.timer"
else
  echo "4. Timer NÃO habilitado (padrão seguro)."
  echo "   Para habilitar depois (decisão humana explícita):"
  echo "     sudo systemctl enable --now $NAME.timer"
fi
echo

echo "Verificação (após --apply):"
echo "  # Uma rodada manual antes de confiar no timer:"
echo "  sudo systemctl start $NAME.service"
echo "  systemctl status $NAME.service --no-pager"
echo "  journalctl -u $NAME.service -n 80 --no-pager"
echo "  ls -l $DAILY_DIR"
echo "  systemctl list-timers | grep -i $NAME"
echo
echo "Rollback: bash painel-soprolife/scripts/uninstall-backup-postgres.sh"
