#!/usr/bin/env bash
# Deploy EEGLife autorizado: Git sempre como soprolife; sem seed ou emissão fiscal.
# Rodar em SSH com TTY, após os gates locais e integração fast-forward oficial.
set -Eeuo pipefail
umask 077
EXPECTED="${1:?HEAD oficial revisado}"
PREVIOUS="${2:?HEAD produção conferido}"
PREVIOUS_SCHEMA="${3:?revisão Alembic conferida}"
[[ "$EUID" == 0 && -t 0 && -t 1 ]]
[[ "$EXPECTED" =~ ^[a-f0-9]{40}$ && "$PREVIOUS" =~ ^[a-f0-9]{40}$ ]]
[[ "$PREVIOUS_SCHEMA" =~ ^[a-z0-9]+$ ]]
[[ "${SOPROLIFE_EEG_GATES_VERIFIED:-}" == YES ]]
ROOT=/opt/soprolife/soprolife-site
CORE="$ROOT/painel-soprolife/nucleo-m15"
ENV_FILE=/opt/soprolife/secrets/m15.env
BACKUP="/opt/soprolife/backups/eeglife-painel-completo-$(date -u +%Y%m%dT%H%M%SZ)"
PY=/opt/soprolife/venvs/m15/bin/python
STARTED="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
git_as() { sudo -u soprolife git -C "$ROOT" "$@"; }
run_api() { (set -a; source "$ENV_FILE"; set +a; cd "$CORE"; sudo -E -u soprolife "$@"); }
cd "$ROOT"
[[ "$(find .git ! -user soprolife | wc -l)" == 0 ]]
[[ -z "$(git_as status --porcelain)" ]]
[[ "$(git_as branch --show-current)" == painel-soprolife-v01 ]]
[[ "$(git_as rev-parse HEAD)" == "$PREVIOUS" ]]
source "$CORE/scripts/lib-deploy-hardening.sh"
soprolife_wait_health_ok http://127.0.0.1:8015/api/v1/health 'API antes' 10 1 20
soprolife_wait_health_ok http://127.0.0.1:8016/p/v1/health 'Portal antes' 10 1 20
[[ "$(sudo -u postgres psql -X -d soprolife_m15 -Atqc 'SELECT version_num FROM alembic_version')" == "$PREVIOUS_SCHEMA" ]]
if systemctl is-active --quiet soprolife-nfse-production-worker.service; then
  echo 'BLOQUEIO: worker fiscal em execução; não substituir código.' >&2; exit 1
fi
run_api "$PY" - <<'PY'
from app.db import get_engine
from sqlalchemy import text
with get_engine().connect() as db:
 db.execute(text('SET TRANSACTION READ ONLY'))
 n=db.scalar(text("SELECT count(*) FROM fiscal_issuance_requests WHERE status IN ('authorized','running')"))
 if n: raise SystemExit('BLOQUEIO: pedido fiscal em andamento.')
print('Fiscal em repouso; nenhuma chamada ao provedor.')
PY
git_as fetch origin painel-soprolife-v01
[[ "$(git_as rev-parse origin/painel-soprolife-v01)" == "$EXPECTED" ]]
git_as merge-base --is-ancestor "$PREVIOUS" "$EXPECTED"
echo 'Commits revisados para deploy:'
git_as log --oneline "$PREVIOUS..$EXPECTED"
API_CHANGED=NO
PROXY_CHANGED=NO
PORTAL_CHANGED=NO
MIGRATIONS_CHANGED=NO
while IFS= read -r changed; do
  case "$changed" in painel-soprolife/nucleo-m15/app/*.py|painel-soprolife/nucleo-m15/app/*/*.py|painel-soprolife/nucleo-m15/migrations/versions/*) API_CHANGED=YES;; esac
  case "$changed" in painel-soprolife/scripts/command-center-local-server.py|painel-soprolife/scripts/panel_access_gate.py) PROXY_CHANGED=YES;; esac
  case "$changed" in painel-soprolife/nucleo-m15/app/portal/*|painel-soprolife/nucleo-m15/app/db.py|painel-soprolife/nucleo-m15/app/config.py|painel-soprolife/nucleo-m15/app/security.py|painel-soprolife/nucleo-m15/app/models.py|painel-soprolife/nucleo-m15/app/services/eeg_results.py|painel-soprolife/nucleo-m15/app/services/patient_results.py) PORTAL_CHANGED=YES;; esac
  case "$changed" in painel-soprolife/nucleo-m15/migrations/versions/*) MIGRATIONS_CHANGED=YES;; esac
done < <(git_as diff --name-only "$PREVIOUS" "$EXPECTED")
# Recarregamento explícito de módulo compartilhado já publicado, sem editar unit/env.
if [[ "${SOPROLIFE_EEG_RELOAD_PORTAL:-}" == YES ]]; then PORTAL_CHANGED=YES; fi

# Primeira escrita persistente: backup pelo script corporativo versionado.
bash "$CORE/scripts/backup-postgresql-m15.sh" "$BACKUP"
DUMP="$(find "$BACKUP" -maxdepth 1 -name '*.dump' -type f)"
[[ -n "$DUMP" && -s "$DUMP" ]]
pg_restore --list "$DUMP" > "$BACKUP/restore-list.txt"
pg_restore --file=/dev/null "$DUMP"
git_as bundle create - --all > "$BACKUP/repository.bundle"
git_as bundle verify - < "$BACKUP/repository.bundle" > "$BACKUP/bundle-verify.log" 2>&1
chmod 0600 "$DUMP" "$BACKUP/repository.bundle"
sha256sum "$DUMP" "$BACKUP/repository.bundle" > "$BACKUP/SHA256SUMS"
printf '%s\n' "$PREVIOUS" > "$BACKUP/HEAD.before"
printf '%s\n' "$PREVIOUS_SCHEMA" > "$BACKUP/schema.before"
cat > "$BACKUP/ROLLBACK.txt" <<EOF
Desativar operação EEG antes de eventual rollback. Não executar downgrade com fatos.
Preservar banco e arquivos privados. Reverter código somente por commit revisado,
fast-forward, sem reset --hard. Validar compatibilidade de schema antes do reinício.
Backup PostgreSQL validado, Git bundle e hashes neste diretório. Não restaurar sobre
escritas posteriores sem plano separado. Nenhum segredo ou dado histórico foi alterado.
EOF
trap 'code=$?; echo "Deploy interrompido; backup preservado: $BACKUP (exit $code)." >&2; exit "$code"' ERR
printf 'BACKUP_PATH=%s\nBACKUP_BYTES=%s\n' "$DUMP" "$(stat -c %s "$DUMP")"
cat "$BACKUP/SHA256SUMS"
# Arquivos de código legíveis pelos serviços; backups continuam privados.
(umask 022; git_as merge --ff-only origin/painel-soprolife-v01)
[[ "$(git_as rev-parse HEAD)" == "$EXPECTED" ]]
if [[ "$MIGRATIONS_CHANGED" == YES ]]; then run_api /opt/soprolife/venvs/m15/bin/alembic upgrade head; fi
run_api /opt/soprolife/venvs/m15/bin/alembic check
# Fonte privada de custos preservada; somente resumos por marca são regenerados.
[[ -f "$ROOT/painel-soprolife/data-private/custos-investimentos.local.json" ]]
sudo -u soprolife "$PY" "$ROOT/painel-soprolife/scripts/generate-custos-investimentos.py" > "$BACKUP/custos-generate.log" 2>&1
# Somente processos afetados; o worker continua oneshot e não é iniciado.
if [[ "$API_CHANGED" == YES ]]; then systemctl restart soprolife-m15-api.service; fi
soprolife_wait_health_ok http://127.0.0.1:8015/api/v1/health 'API depois' 30 1 45
if [[ "$PROXY_CHANGED" == YES ]]; then systemctl restart soprolife-painel-loopback.service; fi
soprolife_wait_health_ok http://127.0.0.1:8765/painel-soprolife/api/m15/health 'Proxy depois' 30 1 45
if [[ "$PORTAL_CHANGED" == YES ]]; then systemctl restart soprolife-portal-resultados.service; fi
soprolife_wait_health_ok http://127.0.0.1:8016/p/v1/health 'Portal depois' 10 1 20
[[ "$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/painel-soprolife/)" == 200 ]]
[[ "$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8015/api/v1/eeg/exames)" == 401 ]]
sudo -u soprolife bash "$ROOT/painel-soprolife/scripts/check-access.sh" > "$BACKUP/check-access.log" 2>&1
run_api "$PY" - <<'PY'
from app.db import get_engine
from sqlalchemy import text
with get_engine().connect() as db:
 db.execute(text('SET TRANSACTION READ ONLY'))
 n=db.scalar(text("SELECT count(*) FROM fiscal_policies WHERE service='eeg' AND validation_state='validated' AND environment='production'"))
 if n: raise SystemExit('BLOQUEIO: política EEG real inesperada; revisão exigida.')
print('EEG fiscal fail-closed: nenhuma política real validada.')
PY
journalctl -u soprolife-m15-api.service -u soprolife-painel-loopback.service -u soprolife-portal-resultados.service --since "$STARTED" -p err --no-pager > "$BACKUP/journal-errors.log"
JOURNAL_ERRORS=$("$PY" - "$BACKUP/journal-errors.log" <<'PY_JOURNAL'
from pathlib import Path
import sys
print(sum(bool(line.strip()) and not line.startswith('-- ') for line in Path(sys.argv[1]).read_text().splitlines()))
PY_JOURNAL
)
[[ "$JOURNAL_ERRORS" == 0 ]]
printf 'RESTART_API=%s\nRESTART_PROXY=%s\nRESTART_PORTAL=%s\n' "$API_CHANGED" "$PROXY_CHANGED" "$PORTAL_CHANGED"
[[ -z "$(git_as status --porcelain)" ]]
[[ "$(find .git ! -user soprolife | wc -l)" == 0 ]]
printf 'HEAD_PRODUCAO=%s\nSCHEMA=%s\nCHECK_ACCESS=OK\nJOURNAL_CRITICAL=0\n' "$(git_as rev-parse HEAD)" "$(sudo -u postgres psql -X -d soprolife_m15 -Atqc 'SELECT version_num FROM alembic_version')"
echo 'Deploy validado. Nenhum paciente de teste criado em produção.'
