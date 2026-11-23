#!/usr/bin/env bash
# Médico solicitante: código + migration aditiva. Não altera dado de exame,
# m15.env, units, financeiro nem fiscal. Execute como root, em terminal.
set -Eeuo pipefail
umask 077
EXPECTED="${1:?HEAD oficial esperado}"
PREVIOUS="${2:?HEAD de produção verificado}"
[[ "$EUID" == 0 && -t 0 && -t 1 ]]
[[ "$EXPECTED" =~ ^[a-f0-9]{40}$ && "$PREVIOUS" =~ ^[a-f0-9]{40}$ ]]
ROOT=/opt/soprolife/soprolife-site
CORE="$ROOT/painel-soprolife/nucleo-m15"
ENV_FILE=/opt/soprolife/secrets/m15.env
BACKUP="/opt/soprolife/backups/medico-solicitante-$(date -u +%Y%m%dT%H%M%SZ)"
git_as() { sudo -u soprolife git -C "$ROOT" "$@"; }
run_api() { (set -a; source "$ENV_FILE"; set +a; cd "$CORE"; sudo -E -u soprolife env PYTHONPATH="$CORE" "$@"); }
schema() { sudo -u postgres psql -X -d soprolife_m15 -Atqc 'SELECT version_num FROM alembic_version'; }
cd "$ROOT"
[[ -z "$(git_as status --porcelain)" ]]
[[ "$(git_as branch --show-current)" == painel-soprolife-v01 ]]
[[ "$(git_as rev-parse HEAD)" == "$PREVIOUS" ]]
[[ "$(find .git ! -user soprolife | wc -l)" == 0 ]]
[[ "$(schema)" == c191126cc2 ]]
source "$CORE/scripts/lib-deploy-hardening.sh"
soprolife_wait_health_ok http://127.0.0.1:8015/api/v1/health 'API antes' 10 1 20
if systemctl is-active --quiet soprolife-nfse-production-worker.service; then
  echo 'Worker fiscal em execução; deploy recusado.' >&2; exit 1
fi
git_as fetch origin painel-soprolife-v01
[[ "$(git_as rev-parse origin/painel-soprolife-v01)" == "$EXPECTED" ]]
git_as merge-base --is-ancestor "$PREVIOUS" "$EXPECTED"
bash "$CORE/scripts/backup-postgresql-m15.sh" "$BACKUP"
pg_restore --list "$BACKUP"/*.dump > "$BACKUP/restore-list.txt"
git_as bundle create - --all > "$BACKUP/repository.bundle"
git_as bundle verify - < "$BACKUP/repository.bundle" > "$BACKUP/bundle-verify.log" 2>&1
printf '%s\n' "$PREVIOUS" > "$BACKUP/HEAD.before"
trap 'code=$?; echo "Deploy interrompido (exit $code); backup: $BACKUP" >&2; exit "$code"' ERR
(umask 022; git_as merge --ff-only origin/painel-soprolife-v01)
run_api /opt/soprolife/venvs/m15/bin/alembic upgrade head
[[ "$(schema)" == e231126msol ]]
run_api /opt/soprolife/venvs/m15/bin/alembic check
[[ "$(sudo -u postgres psql -X -d soprolife_m15 -Atqc 'SELECT count(*) FROM spirometry_exams WHERE medico_solicitante_nome IS NOT NULL')" == 0 ]]
systemctl restart soprolife-m15-api.service
soprolife_wait_health_ok http://127.0.0.1:8015/api/v1/health 'API depois' 30 1 45
soprolife_wait_health_ok http://127.0.0.1:8765/painel-soprolife/api/m15/health 'Proxy depois' 30 1 45
soprolife_wait_health_ok http://127.0.0.1:8016/p/v1/health 'Portal depois' 10 1 20
[[ "$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/painel-soprolife/)" == 200 ]]
[[ -z "$(git_as status --porcelain)" ]]
echo "Deploy concluído em $(git_as rev-parse HEAD). Backup: $BACKUP"
