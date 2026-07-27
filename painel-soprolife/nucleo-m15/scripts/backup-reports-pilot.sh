#!/usr/bin/env bash
# M24D — backup coordenado (PostgreSQL + storage de laudos) exigido ANTES de
# habilitar o piloto interno controlado. Manual, privado, interativo.
#
# Verifica cada artefato ANTES de gravar o manifesto: nenhuma "mutação" (o
# manifesto que o gate de go-live consome como evidência) acontece se o
# dump ou o arquivo do storage falharem a verificação.
set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DB_NAME="soprolife_m15"
readonly STORAGE_ROOT="${1:?uso: backup-reports-pilot.sh STORAGE_ROOT [DEST_ROOT]}"
readonly DEST_ROOT="${2:-/opt/soprolife/backups/reports-pilot}"
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly DUMP_DEST="$DEST_ROOT/${DB_NAME}-${STAMP}.dump"
readonly DUMP_TEMP="${DUMP_DEST}.partial"
readonly ARCHIVE_DEST="$DEST_ROOT/reports-storage-${STAMP}.tar"
readonly ARCHIVE_TEMP="${ARCHIVE_DEST}.partial"
readonly MANIFEST_DEST="$DEST_ROOT/manifest-${STAMP}.json"

cleanup() {
  if sudo test -f "$DUMP_TEMP"; then sudo rm -f -- "$DUMP_TEMP"; fi
  if sudo test -f "$ARCHIVE_TEMP"; then sudo rm -f -- "$ARCHIVE_TEMP"; fi
}
trap cleanup EXIT

[[ -t 0 && -t 1 ]] || { echo "ERRO: execute em terminal interativo" >&2; exit 1; }
command -v sudo >/dev/null || { echo "ERRO: sudo ausente" >&2; exit 1; }
[[ "$STORAGE_ROOT" == /* ]] || { echo "ERRO: STORAGE_ROOT precisa ser absoluto" >&2; exit 1; }
sudo test -d "$STORAGE_ROOT" || { echo "ERRO: STORAGE_ROOT não existe ou não é diretório" >&2; exit 1; }
sudo -v
sudo install -d -o root -g root -m 0700 "$DEST_ROOT"
sudo test ! -e "$DUMP_DEST" || { echo "ERRO: destino do dump já existe" >&2; exit 1; }
sudo test ! -e "$ARCHIVE_DEST" || { echo "ERRO: destino do arquivo de storage já existe" >&2; exit 1; }
sudo test ! -e "$MANIFEST_DEST" || { echo "ERRO: manifesto já existe" >&2; exit 1; }

echo "Gerando dump PostgreSQL..."
sudo -u postgres pg_dump --format=custom "$DB_NAME" | sudo tee "$DUMP_TEMP" >/dev/null
sudo chmod 0600 "$DUMP_TEMP"
sudo pg_restore --list "$DUMP_TEMP" >/dev/null
sudo mv -- "$DUMP_TEMP" "$DUMP_DEST"

echo "Arquivando storage de laudos ($STORAGE_ROOT)..."
sudo tar --create --preserve-permissions --file "$ARCHIVE_TEMP" -C "$(dirname "$STORAGE_ROOT")" "$(basename "$STORAGE_ROOT")"
sudo chmod 0600 "$ARCHIVE_TEMP"
sudo tar tvf "$ARCHIVE_TEMP" >/dev/null
sudo mv -- "$ARCHIVE_TEMP" "$ARCHIVE_DEST"

echo "Contando linhas técnicas..."
COUNT_DOCS="$(sudo -u postgres psql -X -tA -d "$DB_NAME" -c 'SELECT count(*) FROM report_documents')"
COUNT_VERSIONS="$(sudo -u postgres psql -X -tA -d "$DB_NAME" -c 'SELECT count(*) FROM report_document_versions')"
COUNT_PROFILES="$(sudo -u postgres psql -X -tA -d "$DB_NAME" -c 'SELECT count(*) FROM physician_profiles')"

echo "Gravando manifesto verificado..."
sudo python3 "$SCRIPT_DIR/reports_pilot_backup.py" \
  --manifest "$MANIFEST_DEST" \
  --pg-dump "$DUMP_DEST" \
  --storage-archive "$ARCHIVE_DEST" \
  --count-report-documents "$COUNT_DOCS" \
  --count-report-document-versions "$COUNT_VERSIONS" \
  --count-physician-profiles "$COUNT_PROFILES"

trap - EXIT
echo "Backup do piloto de laudos criado e verificado."
echo "Manifesto: $MANIFEST_DEST"
echo "Aponte SOPROLIFE_REPORTS_BACKUP_MANIFEST para este caminho antes do go-live do piloto."
