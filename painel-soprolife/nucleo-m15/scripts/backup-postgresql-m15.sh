#!/usr/bin/env bash
# Backup manual, privado e verificado. Não remove backups antigos.
set -Eeuo pipefail
umask 077

readonly DB_NAME="soprolife_m15"
readonly DEST_ROOT="${1:-/opt/soprolife/backups/m15/manual}"
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly DEST="$DEST_ROOT/${DB_NAME}-$STAMP.dump"
readonly TEMP="${DEST}.partial"
readonly SHA="${DEST}.sha256"
readonly SHA_TEMP="${SHA}.partial"
readonly DUMP_BASE="${DB_NAME}-$STAMP.dump"

cleanup() {
  if sudo test -f "$TEMP"; then
    sudo rm -f -- "$TEMP"
  fi
  if sudo test -f "$SHA_TEMP"; then
    sudo rm -f -- "$SHA_TEMP"
  fi
}
trap cleanup EXIT

# Por padrão exige terminal interativo (uso manual/pré-deploy). A execução
# automatizada via systemd (timer diário) é a única exceção e precisa declarar
# explicitamente SOPROLIFE_BACKUP_ALLOW_NONINTERACTIVE=1 — nunca é o padrão.
if [[ "${SOPROLIFE_BACKUP_ALLOW_NONINTERACTIVE:-0}" != "1" ]]; then
  [[ -t 0 && -t 1 ]] || { echo "ERRO: execute em terminal interativo (ou defina SOPROLIFE_BACKUP_ALLOW_NONINTERACTIVE=1 para execução automatizada via systemd)" >&2; exit 1; }
fi
command -v sudo >/dev/null || { echo "ERRO: sudo ausente" >&2; exit 1; }
sudo -v
sudo install -d -o root -g root -m 0700 "$DEST_ROOT"
sudo test ! -e "$DEST" || { echo "ERRO: destino já existe" >&2; exit 1; }

sudo -u postgres pg_dump --format=custom "$DB_NAME" | sudo tee "$TEMP" >/dev/null
sudo chmod 0600 "$TEMP"
sudo pg_restore --list "$TEMP" >/dev/null
sudo mv -- "$TEMP" "$DEST"

# Evidência SHA-256 (m15.backup-evidencia): companheiro do dump, na mesma
# pasta, gravando apenas o basename para permitir `sha256sum -c` rodado de
# dentro da pasta. Gerada e conferida antes de promover o arquivo final.
sudo sh -c "cd '$DEST_ROOT' && sha256sum '$DUMP_BASE' > '$DUMP_BASE.sha256.partial'"
sudo chmod 0600 "$SHA_TEMP"
sudo sh -c "cd '$DEST_ROOT' && sha256sum -c '$DUMP_BASE.sha256.partial'" >/dev/null
sudo mv -- "$SHA_TEMP" "$SHA"
trap - EXIT

echo "Backup M15 criado e verificado: $DEST"
echo "SHA-256 registrado e verificado: $SHA"
echo "Rotação periódica (timer diário) atua somente na pasta .../m15/daily; este"
echo "script nunca remove backups — pré-deploy/manual são preservados."
