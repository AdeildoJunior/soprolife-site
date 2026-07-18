#!/usr/bin/env bash
# Backup manual, privado e verificado. Não remove backups antigos.
set -Eeuo pipefail
umask 077

readonly DB_NAME="soprolife_m15"
readonly DEST_ROOT="${1:-/opt/soprolife/backups/m15/manual}"
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly DEST="$DEST_ROOT/${DB_NAME}-$STAMP.dump"
readonly TEMP="${DEST}.partial"

cleanup() {
  if sudo test -f "$TEMP"; then
    sudo rm -f -- "$TEMP"
  fi
}
trap cleanup EXIT

[[ -t 0 && -t 1 ]] || { echo "ERRO: execute em terminal interativo" >&2; exit 1; }
command -v sudo >/dev/null || { echo "ERRO: sudo ausente" >&2; exit 1; }
sudo -v
sudo install -d -o root -g root -m 0700 "$DEST_ROOT"
sudo test ! -e "$DEST" || { echo "ERRO: destino já existe" >&2; exit 1; }

sudo -u postgres pg_dump --format=custom "$DB_NAME" | sudo tee "$TEMP" >/dev/null
sudo chmod 0600 "$TEMP"
sudo pg_restore --list "$TEMP" >/dev/null
sudo mv -- "$TEMP" "$DEST"
trap - EXIT

echo "Backup M15 criado e verificado: $DEST"
echo "Rotação é manual: revise data/tamanho e remova somente backups explicitamente aprovados."
