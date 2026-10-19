#!/usr/bin/env bash
# Deploy incremental Fase 2A autorizado. Não instala pacotes, reescreve segredos,
# altera units ou cria dados clínicos. Executar SOMENTE após gates locais verdes.
# Pode ser enviado via stdin a bash na VPS; só aceita o HEAD oficial revisado.
set -Eeuo pipefail
umask 077
EXPECTED="${1:?HEAD alvo}"
PREVIOUS="${2:?HEAD anterior}"
PUBLIC_EEG_URL="${3:?URL HTTPS do portal EEG}"
ROOT=/opt/soprolife/soprolife-site
CORE="$ROOT/painel-soprolife/nucleo-m15"
PY=/opt/soprolife/venvs/m15/bin/python
ENV_FILE=/opt/soprolife/secrets/m15.env
BACKUP="/opt/soprolife/backups/fase2a-eeg-$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$EXPECTED" =~ ^[a-f0-9]{40}$ && "$PREVIOUS" =~ ^[a-f0-9]{40}$ ]]
[[ "${SOPROLIFE_FASE2A_GATES_VERIFIED:-}" == YES ]]
[[ "$PUBLIC_EEG_URL" =~ ^https://[a-z0-9.-]+/p/v1/eeg/?$ ]]
[[ "$EUID" == 0 ]]
cd "$ROOT"
[[ -z "$(git status --porcelain)" ]]
[[ "$(git branch --show-current)" == painel-soprolife-v01 ]]
[[ "$(git rev-parse HEAD)" == "$PREVIOUS" ]]
source "$CORE/scripts/lib-deploy-hardening.sh"
soprolife_wait_health_ok http://127.0.0.1:8015/api/v1/health 'API antes' 10 1 20
soprolife_wait_health_ok http://127.0.0.1:8016/p/v1/health 'Portal antes' 10 1 20
git fetch origin painel-soprolife-v01
[[ "$(git rev-parse origin/painel-soprolife-v01)" == "$EXPECTED" ]]
git merge-base --is-ancestor "$PREVIOUS" "$EXPECTED"
echo 'Commits a implantar:'
git log --oneline "$PREVIOUS..$EXPECTED"
CURRENT_REV=$(sudo -u postgres psql -X -d soprolife_m15 -Atqc 'SELECT version_num FROM alembic_version')
[[ "$CURRENT_REV" == b70c4a2e9d15 ]]
# A primeira alteração persistente é um backup privado e validado.
install -d -m 0700 "$BACKUP"
trap 'code=$?; echo "Deploy interrompido; backup privado preservado em $BACKUP; código $code." >&2; exit "$code"' ERR
sudo -u postgres pg_dump -Fc soprolife_m15 > "$BACKUP/postgresql.dump"
chmod 0600 "$BACKUP/postgresql.dump"
[[ -s "$BACKUP/postgresql.dump" ]]
pg_restore --list "$BACKUP/postgresql.dump" > "$BACKUP/restore-list.txt"
[[ -s "$BACKUP/restore-list.txt" ]]
# Lê/descomprime também os blocos de dados sem emitir conteúdo ou restaurar.
pg_restore --file=/dev/null "$BACKUP/postgresql.dump"
sha256sum "$BACKUP/postgresql.dump" > "$BACKUP/SHA256SUMS"
git bundle create "$BACKUP/repository.bundle" --all > "$BACKUP/bundle-create.log" 2>&1
git bundle verify "$BACKUP/repository.bundle" > "$BACKUP/bundle-verify.log" 2>&1
cp -a "$ENV_FILE" "$BACKUP/m15.env.before"
cp -a /etc/nginx/sites-available/resultados-api.soprolife.com.br "$BACKUP/nginx.before"
chmod 0600 "$BACKUP/m15.env.before"
printf '%s\n' "$PREVIOUS" > "$BACKUP/HEAD.before"
cat > "$BACKUP/ROLLBACK.txt" <<EOF
Preservar PostgreSQL e schema EEG aditivo. Nunca executar downgrade com dados.
Reverter arquivos de código para $PREVIOUS (git restore --source no checkout
que estava limpo), restaurar m15.env.before preservando proprietário/permissões,
reiniciar API, proxy e portal e validar health HTTP 200/status ok.
Não usar reset --hard. Não restaurar dump sobre escritas posteriores sem revisão.
Restaurar também nginx.before para o vhost anterior, nginx -t e reload nginx.
Grants mínimos EEG podem permanecer; o processo anterior não consulta essas tabelas.
EOF
printf 'BACKUP_DIR=%s\nBACKUP_BYTES=%s\n' "$BACKUP" "$(stat -c %s "$BACKUP/postgresql.dump")"
# Código versionado precisa continuar legível pelos serviços sem mudar
# a umask privada usada para backups e segredos.
(umask 022; git merge --ff-only origin/painel-soprolife-v01)
[[ "$(git rev-parse HEAD)" == "$EXPECTED" ]]
run_internal() {
  sudo -u soprolife bash -c 'set -Eeuo pipefail; set -a; source "$1"; set +a; cd "$2"; shift 2; exec "$@"' bash "$ENV_FILE" "$CORE" "$@"
}
# Upgrade somente depois de backup validado. Nenhum seed/import/backfill.
run_internal /opt/soprolife/venvs/m15/bin/alembic upgrade head
run_internal /opt/soprolife/venvs/m15/bin/alembic check
sed -n '/^-- BEGIN FASE2A_EEG_PORTAL_GRANTS/,$p' "$CORE/scripts/sql/m26-4-portal-db-role.sql" | \
  sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d soprolife_m15
# Uma variável pública nova; todas as credenciais existentes ficam idênticas.
"$PY" - "$ENV_FILE" "$PUBLIC_EEG_URL" <<'PY'
import os, stat, sys, tempfile
from pathlib import Path
path = Path(sys.argv[1]); key='M15_PORTAL_EEG_PUBLIC_BASE_URL='
original=path.read_text(); st=path.stat()
lines=[l for l in original.splitlines() if not l.startswith(key)]
lines.append(key+sys.argv[2].rstrip('/'))
fd, name=tempfile.mkstemp(prefix='.fase2a-',dir=path.parent)
with os.fdopen(fd,'w') as f:
 f.write('\n'.join(lines)+'\n');f.flush();os.fsync(f.fileno())
os.chmod(name,stat.S_IMODE(st.st_mode));os.chown(name,st.st_uid,st.st_gid)
os.replace(name,path)
PY
# Queda momentânea comunicada antes da execução. Nenhuma unit é alterada.
systemctl restart soprolife-m15-api.service
soprolife_wait_health_ok http://127.0.0.1:8015/api/v1/health 'API depois' 30 1 45
systemctl restart soprolife-painel-loopback.service
soprolife_wait_health_ok http://127.0.0.1:8765/painel-soprolife/api/m15/health 'Proxy depois' 30 1 45
systemctl restart soprolife-portal-resultados.service
soprolife_wait_health_ok http://127.0.0.1:8016/p/v1/health 'Portal depois' 30 1 45
bash "$CORE/scripts/deploy-portal-resultados.sh" nginx > "$BACKUP/nginx-deploy.log" 2>&1
[[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8016/p/v1/eeg/)" == 200 ]]
[[ "$(curl -s -o /dev/null -w '%{http_code}' "$PUBLIC_EEG_URL/")" == 200 ]]
[[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8015/api/v1/eeg/exames)" == 401 ]]
bash "$ROOT/painel-soprolife/scripts/check-access.sh" > "$BACKUP/check-access.log" 2>&1
[[ -z "$(git status --porcelain)" ]]
printf 'HEAD_PRODUCAO=%s\nMIGRATION=%s\n' "$(git rev-parse HEAD)" "$(sudo -u postgres psql -X -d soprolife_m15 -Atqc 'SELECT version_num FROM alembic_version')"
echo 'Deploy concluído; sem dados clínicos de teste em produção.'
