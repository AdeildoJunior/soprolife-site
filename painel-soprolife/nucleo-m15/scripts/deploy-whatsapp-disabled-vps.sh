#!/usr/bin/env bash
# Authorized preparation only: fail closed, zero Meta I/O, private verified backup.
set -Eeuo pipefail
umask 077
EXPECTED="${1:?Reviewed official HEAD required}"
PREVIOUS="${2:?Verified production HEAD required}"
[[ "$EUID" == 0 && -t 0 && -t 1 ]]
[[ "$EXPECTED" =~ ^[a-f0-9]{40}$ && "$PREVIOUS" =~ ^[a-f0-9]{40}$ ]]
[[ "${SOPROLIFE_WHATSAPP_GATES_VERIFIED:-}" == YES ]]
ROOT=/opt/soprolife/soprolife-site
CORE="$ROOT/painel-soprolife/nucleo-m15"
ENV_FILE=/opt/soprolife/secrets/m15.env
PY=/opt/soprolife/venvs/m15/bin/python
BACKUP="/opt/soprolife/backups/whatsapp-preparacao-$(date -u +%Y%m%dT%H%M%SZ)"
git_as() { sudo -u soprolife git -C "$ROOT" "$@"; }
run_api() { (set -a; source "$ENV_FILE"; set +a; cd "$CORE"; sudo -E -u soprolife env PYTHONPATH="$CORE" "$@"); }
cd "$ROOT"
[[ -z "$(git_as status --porcelain)" ]]
[[ "$(git_as branch --show-current)" == painel-soprolife-v01 ]]
[[ "$(git_as rev-parse HEAD)" == "$PREVIOUS" ]]
[[ "$(find .git ! -user soprolife | wc -l)" == 0 ]]
[[ "$(sudo -u postgres psql -X -d soprolife_m15 -Atqc 'SELECT version_num FROM alembic_version')" == f131126funil ]]
source "$CORE/scripts/lib-deploy-hardening.sh"
soprolife_wait_health_ok http://127.0.0.1:8015/api/v1/health 'API preflight' 10 1 20
soprolife_wait_health_ok http://127.0.0.1:8016/p/v1/health 'Portal preflight' 10 1 20
if systemctl is-active --quiet soprolife-nfse-production-worker.service; then
  echo 'Worker fiscal em execução; deploy recusado.' >&2; exit 1
fi
[[ "$(sudo -u postgres psql -X -d soprolife_m15 -Atqc "SELECT count(*) FROM fiscal_issuance_requests WHERE status IN ('authorized','running')")" == 0 ]]
run_api "$PY" - <<'PY'
import os
assert os.environ.get('WHATSAPP_INTEGRATION_ENABLED','false').lower()=='false', 'WhatsApp já ativo; preparação recusada'
assert not any(os.environ.get('WHATSAPP_'+k) for k in ('APP_SECRET','VERIFY_TOKEN','IDENTITY_KEY','WABA_ID','PHONE_NUMBER_ID')), 'Configuração WhatsApp preexistente; preparação recusada'
print('WhatsApp sem configuração; nenhuma credencial será instalada.')
PY
git_as fetch origin painel-soprolife-v01
[[ "$(git_as rev-parse origin/painel-soprolife-v01)" == "$EXPECTED" ]]
git_as merge-base --is-ancestor "$PREVIOUS" "$EXPECTED"
git_as log --oneline "$PREVIOUS..$EXPECTED"
# Existing corporate backup path; no seed/import/fiscal requests.
bash "$CORE/scripts/backup-postgresql-m15.sh" "$BACKUP"
DUMP="$(find "$BACKUP" -maxdepth 1 -type f -name '*.dump')"
[[ -n "$DUMP" && -s "$DUMP" ]]
pg_restore --list "$DUMP" > "$BACKUP/restore-list.txt"
pg_restore --file=/dev/null "$DUMP"
git_as bundle create - --all > "$BACKUP/repository.bundle"
git_as bundle verify - < "$BACKUP/repository.bundle" > "$BACKUP/bundle-verify.log" 2>&1
cp -a "$ENV_FILE" "$BACKUP/m15.env.before"
printf '%s\n' "$PREVIOUS" > "$BACKUP/HEAD.before"
sha256sum "$DUMP" "$BACKUP/repository.bundle" > "$BACKUP/SHA256SUMS"
trap 'code=$?; echo "Deploy interrompido; backup privado preservado: $BACKUP (exit $code)." >&2; exit "$code"' ERR
# Fingerprint tool comes from the reviewed target commit, executed with old code.
git_as show "$EXPECTED:painel-soprolife/nucleo-m15/scripts/whatsapp_deploy_checks.py" > "$BACKUP/checks.py"
run_api "$PY" - fingerprint < "$BACKUP/checks.py" > "$BACKUP/protected.before.json"
(umask 022; git_as merge --ff-only origin/painel-soprolife-v01)
[[ "$(git_as rev-parse HEAD)" == "$EXPECTED" ]]
# Append only the explicit off switch. Never append fictitious or actual secrets.
# Replacing any previous switch avoids accidental override by duplicate entries.
"$PY" - <<'PY'
from pathlib import Path
import os,re,tempfile
p=Path('/opt/soprolife/secrets/m15.env')
s=p.read_text()
s='\n'.join(line for line in s.splitlines() if not re.match(r'^(?:export\s+)?WHATSAPP_INTEGRATION_ENABLED=',line))
fd,name=tempfile.mkstemp(dir=p.parent,prefix='.m15-whatsapp-')
with os.fdopen(fd,'w') as stream:
 stream.write(s+'\nWHATSAPP_INTEGRATION_ENABLED=false\n')
 os.fchmod(stream.fileno(),0o600)
os.chown(name,p.stat().st_uid,p.stat().st_gid)
os.replace(name,p)
PY
run_api /opt/soprolife/venvs/m15/bin/alembic upgrade head
run_api /opt/soprolife/venvs/m15/bin/alembic check
run_api "$PY" scripts/whatsapp_deploy_checks.py fingerprint > "$BACKUP/protected.after.json"
cmp "$BACKUP/protected.before.json" "$BACKUP/protected.after.json"
systemctl restart soprolife-m15-api.service
soprolife_wait_health_ok http://127.0.0.1:8015/api/v1/health 'API depois' 30 1 45
soprolife_wait_health_ok http://127.0.0.1:8765/painel-soprolife/api/m15/health 'Proxy depois' 30 1 45
soprolife_wait_health_ok http://127.0.0.1:8016/p/v1/health 'Portal depois' 10 1 20
[[ "$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/painel-soprolife/)" == 200 ]]
run_api "$PY" scripts/whatsapp_deploy_checks.py smoke | tee "$BACKUP/smoke.json"
sudo -u soprolife bash "$ROOT/painel-soprolife/scripts/check-access.sh" > "$BACKUP/check-access.log" 2>&1
run_api "$PY" scripts/whatsapp_deploy_checks.py fingerprint > "$BACKUP/protected.final.json"
cmp "$BACKUP/protected.before.json" "$BACKUP/protected.final.json"
systemctl is-active --quiet soprolife-update-data.timer
systemctl is-active --quiet soprolife-nfse-production-worker.path
[[ -z "$(git_as status --porcelain)" ]]
echo "Deploy WhatsApp concluído DESLIGADO. Backup: $BACKUP"
echo 'Sem configuração pública nginx/firewall, sem segredos WhatsApp, sem eventos ou pacientes novos.'
