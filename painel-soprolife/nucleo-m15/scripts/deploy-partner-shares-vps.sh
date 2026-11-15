#!/usr/bin/env bash
# Reviewed generic partner shares only; no data setup, no fiscal issuance.
set -Eeuo pipefail
umask 077
EXPECTED="${1:?Reviewed official HEAD required}"
PREVIOUS="${2:?Verified production HEAD required}"
[[ "$EUID" == 0 && -t 0 && -t 1 ]]
[[ "$EXPECTED" =~ ^[a-f0-9]{40}$ && "$PREVIOUS" =~ ^[a-f0-9]{40}$ ]]
[[ "${SOPROLIFE_PARTNER_GATES_VERIFIED:-}" == YES ]]
ROOT=/opt/soprolife/soprolife-site
CORE="$ROOT/painel-soprolife/nucleo-m15"
ENV_FILE=/opt/soprolife/secrets/m15.env
PY=/opt/soprolife/venvs/m15/bin/python
BACKUP="/opt/soprolife/backups/parcerias-maclin-alergocare-$(date -u +%Y%m%dT%H%M%SZ)"
git_as() { sudo -u soprolife git -C "$ROOT" "$@"; }
run_api() { (set -a; source "$ENV_FILE"; set +a; cd "$CORE"; sudo -E -u soprolife env PYTHONPATH="$CORE" "$@"); }
cd "$ROOT"
[[ -z "$(git_as status --porcelain)" ]]
[[ "$(git_as branch --show-current)" == painel-soprolife-v01 ]]
[[ "$(git_as rev-parse HEAD)" == "$PREVIOUS" ]]
[[ "$(find .git ! -user soprolife | wc -l)" == 0 ]]
[[ "$(sudo -u postgres psql -X -d soprolife_m15 -Atqc 'SELECT version_num FROM alembic_version')" == w141126wa ]]
source "$CORE/scripts/lib-deploy-hardening.sh"
soprolife_wait_health_ok http://127.0.0.1:8015/api/v1/health 'API preflight' 10 1 20
soprolife_wait_health_ok http://127.0.0.1:8016/p/v1/health 'Portal preflight' 10 1 20
if systemctl is-active --quiet soprolife-nfse-production-worker.service; then
  echo 'Worker fiscal em execução; deploy recusado.' >&2; exit 1
fi
[[ "$(sudo -u postgres psql -X -d soprolife_m15 -Atqc "SELECT count(*) FROM fiscal_issuance_requests WHERE status IN ('authorized','running')")" == 0 ]]
git_as fetch origin painel-soprolife-v01
[[ "$(git_as rev-parse origin/painel-soprolife-v01)" == "$EXPECTED" ]]
git_as merge-base --is-ancestor "$PREVIOUS" "$EXPECTED"
git_as log --oneline "$PREVIOUS..$EXPECTED"
bash "$CORE/scripts/backup-postgresql-m15.sh" "$BACKUP"
DUMP="$(find "$BACKUP" -maxdepth 1 -type f -name '*.dump')"
[[ -n "$DUMP" && -s "$DUMP" ]]
pg_restore --list "$DUMP" > "$BACKUP/restore-list.txt"
pg_restore --file=/dev/null "$DUMP"
git_as bundle create - --all > "$BACKUP/repository.bundle"
git_as bundle verify - < "$BACKUP/repository.bundle" > "$BACKUP/bundle-verify.log" 2>&1
printf '%s\n' "$PREVIOUS" > "$BACKUP/HEAD.before"
sha256sum "$DUMP" "$BACKUP/repository.bundle" > "$BACKUP/SHA256SUMS"
trap 'code=$?; echo "Deploy interrompido; backup privado preservado: $BACKUP (exit $code)." >&2; exit "$code"' ERR
# Run the new hash-only check with the old code before schema changes.
git_as show "$EXPECTED:painel-soprolife/nucleo-m15/scripts/partner_deploy_checks.py" > "$BACKUP/checks.py"
run_api "$PY" - fingerprint < "$BACKUP/checks.py" > "$BACKUP/protected.before.json"
(umask 022; git_as merge --ff-only origin/painel-soprolife-v01)
[[ "$(git_as rev-parse HEAD)" == "$EXPECTED" ]]
run_api /opt/soprolife/venvs/m15/bin/alembic upgrade head
run_api /opt/soprolife/venvs/m15/bin/alembic check
run_api "$PY" scripts/partner_deploy_checks.py fingerprint > "$BACKUP/protected.schema.json"
(cd "$CORE"; PYTHONPATH="$CORE" "$PY" scripts/partner_deploy_checks.py compare --before "$BACKUP/protected.before.json" --after "$BACKUP/protected.schema.json")
systemctl restart soprolife-m15-api.service
soprolife_wait_health_ok http://127.0.0.1:8015/api/v1/health 'API depois' 30 1 45
soprolife_wait_health_ok http://127.0.0.1:8765/painel-soprolife/api/m15/health 'Proxy depois' 30 1 45
soprolife_wait_health_ok http://127.0.0.1:8016/p/v1/health 'Portal depois' 10 1 20
[[ "$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/painel-soprolife/)" == 200 ]]
run_api "$PY" scripts/partner_deploy_checks.py smoke > "$BACKUP/smoke.json"
sudo -u soprolife bash "$ROOT/painel-soprolife/scripts/check-access.sh" > "$BACKUP/check-access.log" 2>&1
run_api "$PY" scripts/partner_deploy_checks.py fingerprint > "$BACKUP/protected.after.json"
(cd "$CORE"; PYTHONPATH="$CORE" "$PY" scripts/partner_deploy_checks.py compare --before "$BACKUP/protected.before.json" --after "$BACKUP/protected.after.json")
systemctl is-active --quiet soprolife-update-data.timer
systemctl is-active --quiet soprolife-nfse-production-worker.path
[[ -z "$(git_as status --porcelain)" ]]
echo "Deploy concluído; cadastros/caso real exigem plano próprio. Backup: $BACKUP"
