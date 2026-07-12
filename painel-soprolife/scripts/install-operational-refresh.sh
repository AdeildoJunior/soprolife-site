#!/usr/bin/env bash
# install-operational-refresh.sh — Instala (FUTURAMENTE, por decisão humana)
# o timer systemd do frescor operacional na VPS. M14.3A.1.
#
# PADRÃO É --dry-run: mostra o que faria sem tocar no sistema.
#
# Uso (na VPS, como root, dentro de /opt/soprolife/soprolife-site):
#   bash painel-soprolife/scripts/install-operational-refresh.sh            # dry-run
#   bash painel-soprolife/scripts/install-operational-refresh.sh --apply    # instala (não habilita)
#   bash painel-soprolife/scripts/install-operational-refresh.sh --apply --enable-timer
#
# O que ele NUNCA faz:
#   - habilitar o timer sem --enable-timer explícito;
#   - publicar Apps Script;
#   - gravar segredo em /etc ou no journal.
#
# Rollback: scripts/uninstall-operational-refresh.sh

set -euo pipefail
cd "$(dirname "$0")/../../" || exit 1

SRC_DIR="painel-soprolife/systemd"
DST_DIR="/etc/systemd/system"
ENV_DST="/etc/soprolife/operational-refresh.env"
NAME="soprolife-operational-refresh"

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

echo "═══ SoproLife — instalador do frescor operacional (systemd) ═══"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "MODO: dry-run (nada será alterado). Use --apply para instalar."
else
  echo "MODO: apply"
fi
echo

for f in "$SRC_DIR/$NAME.service" "$SRC_DIR/$NAME.timer" "$SRC_DIR/operational-refresh.env.example"; do
  if [ ! -f "$f" ]; then
    echo "ERRO: arquivo não encontrado: $f"
    exit 1
  fi
done

if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  echo "ERRO: --apply exige root (sudo)."
  exit 1
fi

echo "1. Unidades systemd:"
run cp "$SRC_DIR/$NAME.service" "$DST_DIR/$NAME.service"
run cp "$SRC_DIR/$NAME.timer"   "$DST_DIR/$NAME.timer"

echo
echo "2. EnvironmentFile (sem segredos; só criado se não existir):"
if [ -f "$ENV_DST" ]; then
  echo "  já existe: $ENV_DST (preservado — não sobrescrevo configuração humana)"
else
  run mkdir -p /etc/soprolife
  run cp "$SRC_DIR/operational-refresh.env.example" "$ENV_DST"
  run chmod 644 "$ENV_DST"
fi

echo
echo "3. daemon-reload:"
run systemctl daemon-reload

echo
if [ "$ENABLE_TIMER" -eq 1 ]; then
  echo "4. Habilitando timer (autorizado por --enable-timer):"
  run systemctl enable --now "$NAME.timer"
else
  echo "4. Timer NÃO habilitado (padrão seguro)."
  echo "   Para habilitar depois, decisão humana explícita:"
  echo "     sudo systemctl enable --now $NAME.timer"
fi

echo
echo "Verificação (após --apply):"
echo "  systemctl status $NAME.timer"
echo "  journalctl -u $NAME.service -n 50 --no-pager"
echo
echo "Rollback: bash painel-soprolife/scripts/uninstall-operational-refresh.sh"
