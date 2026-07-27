#!/usr/bin/env bash
# M24D — preparação interativa e idempotente do piloto interno de laudos.
#
# Prepara SOMENTE os pré-requisitos seguros de ativação: storage privado,
# drop-in systemd de ReadWritePaths e backup coordenado verificado. NÃO
# habilita o piloto por si só — a ativação continua exigindo o gate
# dedicado (M15_REPORTS_MODE=pilot + M15_REPORTS_ENABLED=true +
# SOPROLIFE_REPORTS_PILOT_AUTHORIZATION="HABILITAR PILOTO DE LAUDOS" +
# HTTPS pré/pós) dentro de deploy-producao-vps.sh.
#
# Este script NÃO:
#   - altera /opt/soprolife/secrets/m15.env;
#   - habilita M15_REPORTS_ENABLED;
#   - reinicia nem habilita soprolife-m15-api.service;
#   - altera painel-soprolife/data/m15-config.json;
#   - faz deploy, pull ou checkout de código.
#
# Uso: bash prepare-reports-pilot-vps.sh [STORAGE_ROOT] [BACKUP_DEST_ROOT]
#   STORAGE_ROOT      padrão: /opt/soprolife/private/reports
#   BACKUP_DEST_ROOT  padrão: /opt/soprolife/backups/reports-pilot
set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly M15_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly REPO_ROOT="$(cd "$M15_DIR/../.." && pwd)"
readonly STORAGE_ROOT="${1:-/opt/soprolife/private/reports}"
readonly BACKUP_DEST_ROOT="${2:-/opt/soprolife/backups/reports-pilot}"
readonly DROPIN_SOURCE="$REPO_ROOT/painel-soprolife/systemd/soprolife-m15-api-reports-pilot.override.conf.example"
readonly DROPIN_DIR="/etc/systemd/system/soprolife-m15-api.service.d"
readonly DROPIN_TARGET="$DROPIN_DIR/reports-pilot.conf"
readonly UNIT_NAME="soprolife-m15-api.service"
readonly GATE_PY="$SCRIPT_DIR/reports_go_live_gate.py"
readonly BACKUP_SCRIPT="$SCRIPT_DIR/backup-reports-pilot.sh"

fail() {
  echo "ERRO: $*" >&2
  exit 1
}

[[ -t 0 && -t 1 ]] || fail "execute em terminal interativo"
command -v sudo >/dev/null || fail "sudo não encontrado"
command -v systemctl >/dev/null || fail "systemctl não encontrado"
id soprolife >/dev/null 2>&1 || fail "usuário de serviço soprolife não existe"
[[ -f "$DROPIN_SOURCE" ]] || fail "drop-in de exemplo ausente: $DROPIN_SOURCE"
[[ -x "$BACKUP_SCRIPT" ]] || fail "script de backup ausente ou não executável: $BACKUP_SCRIPT"
[[ "$STORAGE_ROOT" == /* ]] || fail "STORAGE_ROOT precisa ser absoluto"

echo "Preparação do piloto de laudos — somente pré-requisitos seguros."
echo "Nada é habilitado por este script: m15.env, a API e o config do"
echo "frontend não são tocados."
echo "  storage:  $STORAGE_ROOT"
echo "  backup:   $BACKUP_DEST_ROOT"
echo "  drop-in:  $DROPIN_TARGET"
echo ""
sudo -v

echo "== 1/5 raiz privada de storage (idempotente) =="
sudo install -d -o soprolife -g soprolife -m 0700 "$STORAGE_ROOT"

echo "== 2/5 drop-in systemd exato de ReadWritePaths (idempotente) =="
sudo install -d -o root -g root -m 0755 "$DROPIN_DIR"
sudo install -o root -g root -m 0644 "$DROPIN_SOURCE" "$DROPIN_TARGET"
sudo systemctl daemon-reload

echo "== 3/5 verificação do storage + ReadWritePaths efetivo =="
systemctl cat "$UNIT_NAME" | \
  python3 "$GATE_PY" verify-storage-contract "$REPO_ROOT" "$STORAGE_ROOT" \
  >/dev/null \
  || fail "storage/ReadWritePaths efetivo reprovado no contrato exato do piloto"
echo "  OK: raiz exata em ReadWritePaths, sem pai gravável mais amplo."

echo "== 4/5 backup coordenado (PostgreSQL + storage de laudos) =="
BACKUP_OUTPUT="$("$BACKUP_SCRIPT" "$STORAGE_ROOT" "$BACKUP_DEST_ROOT")"
echo "$BACKUP_OUTPUT"
MANIFEST_PATH="$(printf '%s\n' "$BACKUP_OUTPUT" | sed -n 's/^Manifesto: //p' | tail -n1)"
[[ -n "$MANIFEST_PATH" ]] || \
  fail "não foi possível localizar o caminho do manifesto na saída do backup"

echo "== 5/5 verificação do manifesto de backup =="
python3 "$GATE_PY" verify-backup-manifest "$MANIFEST_PATH" >/dev/null || \
  fail "manifesto de backup reprovado na verificação"
echo "  OK: manifesto com hashes reais e contagens verificados."

echo ""
echo "Preparação concluída. Nenhuma habilitação foi feita."
echo "MANIFEST_PATH=$MANIFEST_PATH"
echo ""
echo "Para ativar o piloto, aponte no ambiente do deploy:"
echo "  SOPROLIFE_REPORTS_BACKUP_MANIFEST=$MANIFEST_PATH"
echo "  M15_REPORTS_STORAGE_DIR=$STORAGE_ROOT"
echo "e rode deploy-producao-vps.sh com o release alvo em"
echo "M15_REPORTS_MODE=pilot / reports_enabled=true e a autorização exata"
echo "SOPROLIFE_REPORTS_PILOT_AUTHORIZATION=\"HABILITAR PILOTO DE LAUDOS\"."
