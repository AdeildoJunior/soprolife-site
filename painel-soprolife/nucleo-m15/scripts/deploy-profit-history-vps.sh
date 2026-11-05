#!/usr/bin/env bash
# Deploy de lucro autorizado, sem mudança de schema, razão ou documentos.
# Política de data e mínimo de sala devem refletir confirmação explícita.
set -Eeuo pipefail
umask 077
EXPECTED="${1:?HEAD oficial revisado}"
PREVIOUS="${2:?HEAD produção conferido}"
POLICY="${3:?exact, month_boundary ou first_exact}"
MINIMUM_HOURS="${4:-}"
[[ "$EUID" == 0 && -t 0 && -t 1 ]]
[[ "$EXPECTED" =~ ^[a-f0-9]{40}$ && "$PREVIOUS" =~ ^[a-f0-9]{40}$ ]]
[[ "$POLICY" == exact || "$POLICY" == month_boundary || "$POLICY" == first_exact ]]
[[ "${SOPROLIFE_PROFIT_GATES_VERIFIED:-}" == YES ]]
ROOT=/opt/soprolife/soprolife-site
CORE="$ROOT/painel-soprolife/nucleo-m15"
ENV_FILE=/opt/soprolife/secrets/m15.env
PY=/opt/soprolife/venvs/m15/bin/python
BACKUP="/opt/soprolife/backups/lucro-historico-$(date -u +%Y%m%dT%H%M%SZ)"
STARTED="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
git_as() { sudo -u soprolife git -C "$ROOT" "$@"; }
run_api() { (set -a; source "$ENV_FILE"; set +a; cd "$CORE"; sudo -E -u soprolife "$@"); }
cd "$ROOT"
[[ -z "$(git_as status --porcelain)" ]]
[[ "$(git_as branch --show-current)" == painel-soprolife-v01 ]]
[[ "$(git_as rev-parse HEAD)" == "$PREVIOUS" ]]
[[ "$(find .git ! -user soprolife | wc -l)" == 0 ]]
source "$CORE/scripts/lib-deploy-hardening.sh"
soprolife_wait_health_ok http://127.0.0.1:8015/api/v1/health 'API antes' 10 1 20
if systemctl is-active --quiet soprolife-nfse-production-worker.service; then
  echo 'Worker fiscal em execução; deploy recusado.' >&2; exit 1
fi
[[ "$(sudo -u postgres psql -X -d soprolife_m15 -Atqc "SELECT count(*) FROM fiscal_issuance_requests WHERE status IN ('authorized','running')")" == 0 ]]
git_as fetch origin painel-soprolife-v01
[[ "$(git_as rev-parse origin/painel-soprolife-v01)" == "$EXPECTED" ]]
git_as merge-base --is-ancestor "$PREVIOUS" "$EXPECTED"
git_as log --oneline "$PREVIOUS..$EXPECTED"
# Este fluxo não permite mudanças de schema, dependências ou serviços.
if git_as diff --name-only "$PREVIOUS" "$EXPECTED" | grep -Eq 'migrations/versions/|requirements|systemd/'; then
  echo 'Escopo de deploy excedido; revisar fluxo.' >&2; exit 1
fi
# Primeira escrita persistente: backup corporativo privado e validado.
bash "$CORE/scripts/backup-postgresql-m15.sh" "$BACKUP"
DUMP="$(find "$BACKUP" -maxdepth 1 -name '*.dump' -type f)"
[[ -n "$DUMP" && -s "$DUMP" ]]
pg_restore --list "$DUMP" > "$BACKUP/restore-list.txt"
pg_restore --file=/dev/null "$DUMP"
git_as bundle create - --all > "$BACKUP/repository.bundle"
git_as bundle verify - < "$BACKUP/repository.bundle" > "$BACKUP/bundle-verify.log" 2>&1
sha256sum "$DUMP" "$BACKUP/repository.bundle" > "$BACKUP/SHA256SUMS"
printf '%s\n' "$PREVIOUS" > "$BACKUP/HEAD.before"
# Hash de tabelas protegidas: só agregados/hashes, nenhum conteúdo privado.
fingerprint() {
  run_api "$PY" - <<'PY'
import hashlib,json
from sqlalchemy import inspect,text
from app.db import get_engine
with get_engine().connect() as db:
 db.execute(text('SET TRANSACTION READ ONLY'))
 tables=inspect(db).get_table_names()
 names=[t for t in tables if t in {'people','spirometry_exams','financial_entries','physician_transfers','partner_transfers','partner_settlements','partner_settlement_items','partnerships','profit_revenue_basis','operational_sessions','operational_session_exams'} or t.startswith(('report_','fiscal_'))]
 out={}
 for table in sorted(names):
  hashes=db.execute(text(f'SELECT md5(row_to_json(t)::text) FROM "{table}" t ORDER BY 1')).scalars().all()
  out[table]={'count':len(hashes),'sha256':hashlib.sha256(''.join(hashes).encode()).hexdigest()}
 print(json.dumps(out,sort_keys=True))
PY
}
files_fingerprint() {
 find /opt/soprolife/private/reports /opt/soprolife/private/nfse-production -type f -print0 | sort -z | xargs -0 -r sha256sum | sha256sum
}
fingerprint > "$BACKUP/protected.before.json"
files_fingerprint > "$BACKUP/files.before.sha256"
trap 'code=$?; echo "Deploy interrompido; backup preservado: $BACKUP (exit $code)." >&2; exit "$code"' ERR
(umask 022; git_as merge --ff-only origin/painel-soprolife-v01)
[[ "$(git_as rev-parse HEAD)" == "$EXPECTED" ]]
ARGS=(--as-of 2026-11-05 --first-date-policy "$POLICY")
if [[ -n "$MINIMUM_HOURS" ]]; then ARGS+=(--minimum-room-hours "$MINIMUM_HOURS"); fi
run_api "$PY" scripts/append_profit_history.py "${ARGS[@]}" > "$BACKUP/history-plan.json"
PLAN_HASH="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' "$BACKUP/history-plan.json")"
run_api "$PY" scripts/append_profit_history.py "${ARGS[@]}" --execute --expected-plan-sha256 "$PLAN_HASH" > "$BACKUP/history-applied.json"
fingerprint > "$BACKUP/protected.after.json"
files_fingerprint > "$BACKUP/files.after.sha256"
cmp "$BACKUP/protected.before.json" "$BACKUP/protected.after.json"
cmp "$BACKUP/files.before.sha256" "$BACKUP/files.after.sha256"
systemctl restart soprolife-m15-api.service
soprolife_wait_health_ok http://127.0.0.1:8015/api/v1/health 'API depois' 30 1 45
soprolife_wait_health_ok http://127.0.0.1:8765/painel-soprolife/api/m15/health 'Proxy depois' 30 1 45
[[ "$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/painel-soprolife/)" == 200 ]]
[[ "$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8015/api/v1/financeiro/lucro/painel?start=2026-09-01\&end=2026-09-30)" == 401 ]]
sudo -u soprolife bash "$ROOT/painel-soprolife/scripts/check-access.sh" > "$BACKUP/check-access.log" 2>&1
run_api "$PY" - <<'PY' > "$BACKUP/profit-smoke.json"
import json
from urllib.request import Request,urlopen
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from app.db import get_engine
from app.models import User
from app.security import issue_token
with Session(get_engine()) as db:
 db.execute(text('SET TRANSACTION READ ONLY'))
 user=db.scalar(select(User).where(User.ativo.is_(True),User.roles.any(name='admin')).order_by(User.id))
 if user is None: raise SystemExit('Administrador ativo não encontrado para smoke GET.')
 token=issue_token(user.id,user.password_hash,ttl_minutes=5)
 output={}
 for label,start,end in [('september','2026-09-01','2026-09-30'),('november','2026-11-01','2026-11-30')]:
  query=f'?start={start}&end={end}'
  for name,base in [('api','http://127.0.0.1:8015/api/v1'),('proxy','http://127.0.0.1:8765/painel-soprolife/api/m15')]:
   request=Request(base+'/financeiro/lucro/painel'+query,headers={'Authorization':'Bearer '+token})
   with urlopen(request,timeout=20) as response:
    assert response.status == 200
    output[label+'_'+name]=json.load(response)
  assert output[label+'_api'] == output[label+'_proxy']
 print(json.dumps(output,ensure_ascii=False))
PY
journalctl -u soprolife-m15-api.service --since "$STARTED" -p err --no-pager > "$BACKUP/journal-errors.log"
JOURNAL_ERRORS="$("$PY" -c 'import pathlib,sys; print(sum(bool(s.strip()) and not s.startswith("-- ") for s in pathlib.Path(sys.argv[1]).read_text().splitlines()))' "$BACKUP/journal-errors.log")"
[[ "$JOURNAL_ERRORS" == 0 ]]
[[ -z "$(git_as status --porcelain)" ]]
printf 'HEAD_PRODUCAO=%s\nBACKUP=%s\nPROTECTED_TABLES=UNCHANGED\nPROTECTED_FILES=UNCHANGED\n' "$(git_as rev-parse HEAD)" "$BACKUP"
echo 'Deploy e smoke read-only concluídos. Sem emissão fiscal ou lançamento financeiro.'
