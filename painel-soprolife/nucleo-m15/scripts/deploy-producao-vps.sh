#!/usr/bin/env bash
# Implantação manual e auditável do M15 na VPS. Não ativa a feature flag por
# conta própria, não cria dados e não importa arquivos. Execute no terminal da
# própria VPS.
#
# M15.5B — modo go-live controlado: um release com enabled=true no
# data/m15-config.json permanece REJEITADO por padrão. Ele só é aceito com as
# DUAS variáveis de ambiente presentes e válidas (fail-closed):
#   SOPROLIFE_M15_GO_LIVE=YES   (exatamente YES, maiúsculo)
#   SOPROLIFE_M15_HTTPS_BASE_URL=https://painel-privado.exemplo.ts.net/
# Nesse modo o endereço HTTPS privado é validado ANTES de qualquer mutação e
# novamente APÓS o deploy (painel 200, health status=ok, enabled=true servido,
# m15-security.js servido e carregado antes de m15-nucleo.js). Releases com
# enabled=false continuam com o comportamento atual, sem exigir variáveis.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly EXPECTED_COMMIT="${1:-}"
readonly EXPECTED_BRANCH="${2:-}"
TAILSCALE_IP="${3:-}"
readonly EXPECTED_REPO="/opt/soprolife/soprolife-site"
readonly M15_DIR="$EXPECTED_REPO/painel-soprolife/nucleo-m15"
readonly ENV_FILE="/opt/soprolife/secrets/m15.env"
readonly VENV_DIR="/opt/soprolife/venvs/m15"
readonly UNIT_SOURCE="$EXPECTED_REPO/painel-soprolife/systemd/soprolife-m15-api.service"
readonly UNIT_TARGET="/etc/systemd/system/soprolife-m15-api.service"
readonly LOOPBACK_UNIT_SOURCE="$EXPECTED_REPO/painel-soprolife/systemd/soprolife-painel-loopback.service"
readonly LOOPBACK_UNIT_TARGET="/etc/systemd/system/soprolife-painel-loopback.service"
readonly HARDENING_LIB="$M15_DIR/scripts/lib-deploy-hardening.sh"
readonly GO_LIVE_LIB="$M15_DIR/scripts/lib-go-live-gate.sh"
readonly DB_ROLE="soprolife_m15"
readonly DB_NAME="soprolife_m15"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly STAMP
readonly BACKUP_DIR="/opt/soprolife/backups/m15/$STAMP"
TEMP_ENV=""
TEMP_BUNDLE=""
MUTATION_STARTED=0

usage() {
  echo "Uso: $0 <commit-esperado-40-hex> <branch-esperada> [ip-tailscale]" >&2
  exit 2
}

fail() {
  echo "ERRO: $*" >&2
  exit 1
}

cleanup() {
  if [[ -n "$TEMP_ENV" && -f "$TEMP_ENV" ]]; then
    rm -f -- "$TEMP_ENV"
  fi
  if [[ -n "$TEMP_BUNDLE" && -f "$TEMP_BUNDLE" ]]; then
    rm -f -- "$TEMP_BUNDLE"
  fi
}

on_error() {
  local exit_code=$?
  trap - ERR
  if (( MUTATION_STARTED )); then
    sudo systemctl stop soprolife-m15-api.service >/dev/null 2>&1 || true
    local unit
    for unit in soprolife-m15-api soprolife-painel-loopback; do
      # shellcheck disable=SC2024  # redirect intencional do usuário atual em /tmp
      sudo journalctl -u "$unit.service" --since "@$(( $(date +%s) - 1800 ))" \
        --no-pager >"/tmp/soprolife-m15-failure-$unit-$STAMP.log" 2>/dev/null || true
      sudo install -m 0600 "/tmp/soprolife-m15-failure-$unit-$STAMP.log" \
        "$BACKUP_DIR/failure-journal-$unit.log" 2>/dev/null || true
      rm -f -- "/tmp/soprolife-m15-failure-$unit-$STAMP.log"
    done
  fi
  echo "" >&2
  echo "IMPLANTAÇÃO INTERROMPIDA (código $exit_code). Banco e backup foram preservados." >&2
  echo "Backup: $BACKUP_DIR" >&2
  echo "Rollback: consulte painel-soprolife/docs/m15-2-proxy-seguro-deploy-vps.md" >&2
  exit "$exit_code"
}

trap cleanup EXIT
trap on_error ERR

[[ -t 0 && -t 1 ]] || fail "execute em terminal interativo"
[[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || usage
[[ -n "$EXPECTED_BRANCH" ]] || usage
command -v git >/dev/null || fail "git não encontrado"
command -v sudo >/dev/null || fail "sudo não encontrado"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || fail "fora de um worktree Git"
[[ "$(readlink -f "$REPO_ROOT")" == "$EXPECTED_REPO" ]] || \
  fail "repositório deve estar em $EXPECTED_REPO"
cd "$REPO_ROOT"

[[ -f "$HARDENING_LIB" ]] || fail "biblioteca de hardening ausente: $HARDENING_LIB"
# shellcheck source=lib-deploy-hardening.sh
source "$HARDENING_LIB"
[[ -f "$GO_LIVE_LIB" ]] || fail "biblioteca de go-live ausente: $GO_LIVE_LIB"
# shellcheck source=lib-go-live-gate.sh
source "$GO_LIVE_LIB"

CURRENT_BRANCH="$(git branch --show-current)"
CURRENT_COMMIT="$(git rev-parse HEAD)"
CURRENT_USER="$(id -un)"
CURRENT_HOST="$(hostname --fqdn 2>/dev/null || hostname)"

[[ "$CURRENT_BRANCH" == "$EXPECTED_BRANCH" ]] || \
  fail "branch atual não corresponde à esperada"
[[ "$CURRENT_COMMIT" == "$EXPECTED_COMMIT" ]] || \
  fail "HEAD não corresponde ao commit esperado"
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]] || \
  fail "worktree não está limpo"
id soprolife >/dev/null 2>&1 || fail "usuário de serviço soprolife não existe"

# M15.5B: enabled=true segue rejeitado por padrão; só passa pelo modo go-live
# controlado (autorização explícita + URL HTTPS validada + checagens estáticas
# do release alvo + probe HTTPS), tudo ANTES de qualquer mutação produtiva.
M15_FLAG="$(soprolife_go_live_flag_config \
  "$REPO_ROOT/painel-soprolife/data/m15-config.json")" || \
  fail "configuração M15 do release alvo inválida"
GO_LIVE_MODE=0
if [[ "$M15_FLAG" == "true" ]]; then
  soprolife_go_live_exigir_autorizacao || \
    fail "release enabled=true sem autorização de go-live"
  soprolife_go_live_validar_url_base "${SOPROLIFE_M15_HTTPS_BASE_URL-}" || \
    fail "URL base HTTPS do go-live inválida"
  soprolife_go_live_checar_fonte_alvo "$REPO_ROOT" || \
    fail "release alvo reprovado nas checagens estáticas do go-live"
  soprolife_go_live_validar_https pre "${SOPROLIFE_M15_HTTPS_BASE_URL-}" || \
    fail "endereço HTTPS privado reprovado na validação pré-deploy"
  GO_LIVE_MODE=1
fi

echo "Contexto confirmado:"
echo "  usuário: $CURRENT_USER"
echo "  hostname: $CURRENT_HOST"
echo "  branch: $CURRENT_BRANCH"
echo "  commit: $CURRENT_COMMIT"
if (( GO_LIVE_MODE )); then
  echo "  feature flag: true (go-live controlado autorizado e pré-validado)"
else
  echo "  feature flag: false"
fi
echo "  Docker/Podman: não serão instalados nem usados"
printf "Digite exatamente 'IMPLANTAR M15' para continuar: "
read -r CONFIRMATION
[[ "$CONFIRMATION" == "IMPLANTAR M15" ]] || fail "confirmação recusada"

sudo -v

# O backup é a primeira alteração persistente do procedimento.
sudo install -d -o root -g root -m 0700 "$BACKUP_DIR"
TEMP_BUNDLE="$(mktemp /tmp/soprolife-m15-bundle.XXXXXX)"
git bundle create "$TEMP_BUNDLE" HEAD >/dev/null
git bundle verify "$TEMP_BUNDLE" >/dev/null
sudo install -o root -g root -m 0600 "$TEMP_BUNDLE" "$BACKUP_DIR/repository.bundle"
rm -f -- "$TEMP_BUNDLE"
TEMP_BUNDLE=""
printf '%s\n' "$CURRENT_COMMIT" | sudo tee "$BACKUP_DIR/commit-before.txt" >/dev/null
sudo chmod 0600 "$BACKUP_DIR/commit-before.txt"
if sudo test -f "$UNIT_TARGET"; then
  sudo cp -a "$UNIT_TARGET" "$BACKUP_DIR/soprolife-m15-api.service.before"
fi
if sudo test -f "$LOOPBACK_UNIT_TARGET"; then
  sudo cp -a "$LOOPBACK_UNIT_TARGET" \
    "$BACKUP_DIR/soprolife-painel-loopback.service.before"
fi
if sudo test -f "$ENV_FILE"; then
  sudo cp -a "$ENV_FILE" "$BACKUP_DIR/m15.env.before"
  sudo chmod 0600 "$BACKUP_DIR/m15.env.before"
fi
if command -v psql >/dev/null 2>&1 && \
   sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -qx 1; then
  sudo -u postgres pg_dump --format=custom "$DB_NAME" | \
    sudo tee "$BACKUP_DIR/${DB_NAME}.dump" >/dev/null
  sudo pg_restore --list "$BACKUP_DIR/${DB_NAME}.dump" >/dev/null
  sudo chmod 0600 "$BACKUP_DIR/${DB_NAME}.dump"
fi
MUTATION_STARTED=1

sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  postgresql-16 postgresql-client-16 python3-venv
sudo systemctl enable --now postgresql.service

# Primeira instalação gera segredos fortes. Reexecução valida e reutiliza o
# EnvironmentFile existente para que uma falha de update nunca desencontre a
# senha do banco do backup. Valores ficam apenas em memória e stdin.
if sudo test -f "$ENV_FILE"; then
  EXISTING_SECRETS="$(sudo python3 - "$ENV_FILE" "$DB_ROLE" "$DB_NAME" <<'PY'
import pathlib, re, sys
from urllib.parse import unquote, urlsplit

values = {}
for raw in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if not raw or raw.lstrip().startswith("#") or "=" not in raw:
        continue
    key, value = raw.split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    values[key.strip()] = value
url = urlsplit(values.get("M15_DATABASE_URL", ""))
password = unquote(url.password or "")
secret = values.get("M15_AUTH_SECRET", "")
if (
    url.scheme != "postgresql+psycopg"
    or url.username != sys.argv[2]
    or url.hostname != "127.0.0.1"
    or (url.port or 5432) != 5432
    or url.path != "/" + sys.argv[3]
    or not re.fullmatch(r"[A-Za-z0-9_-]{48,}", password)
    or not re.fullmatch(r"[A-Za-z0-9_-]{64,}", secret)
    or len(set(secret)) < 10
):
    raise SystemExit("EnvironmentFile M15 anterior é incompatível; rollback manual necessário")
print(password)
print(secret)
PY
)"
  DB_PASSWORD="${EXISTING_SECRETS%%$'\n'*}"
  AUTH_SECRET="${EXISTING_SECRETS#*$'\n'}"
  EXISTING_SECRETS=""
else
  DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  AUTH_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(64))')"
fi
ROLE_EXISTS="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_ROLE'")"
if [[ "$ROLE_EXISTS" == "1" ]]; then
  printf "ALTER ROLE %s WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '%s';\n" \
    "$DB_ROLE" "$DB_PASSWORD" | sudo -u postgres psql -v ON_ERROR_STOP=1 >/dev/null
else
  printf "CREATE ROLE %s WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '%s';\n" \
    "$DB_ROLE" "$DB_PASSWORD" | sudo -u postgres psql -v ON_ERROR_STOP=1 >/dev/null
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -qx 1; then
  sudo -u postgres createdb --owner="$DB_ROLE" "$DB_NAME"
fi

TEMP_ENV="$(mktemp /tmp/soprolife-m15-env.XXXXXX)"
chmod 0600 "$TEMP_ENV"
{
  printf 'M15_ENV=prod\n'
  printf 'M15_DATABASE_URL=postgresql+psycopg://%s:%s@127.0.0.1:5432/%s\n' \
    "$DB_ROLE" "$DB_PASSWORD" "$DB_NAME"
  printf 'M15_AUTH_SECRET=%s\n' "$AUTH_SECRET"
  printf 'M15_TOKEN_TTL_MINUTES=60\n'
  printf 'M15_API_HOST=127.0.0.1\n'
  printf 'M15_API_PORT=8015\n'
  printf "M15_CORS_ORIGINS='[\"http://127.0.0.1:8765\",\"http://localhost:8765\"]'\n"
  printf 'M15_DISPLAY_TIMEZONE=America/Sao_Paulo\n'
} >"$TEMP_ENV"
sudo install -d -o root -g soprolife -m 0750 /opt/soprolife/secrets
sudo install -o root -g soprolife -m 0640 "$TEMP_ENV" "$ENV_FILE"
DB_PASSWORD=""
AUTH_SECRET=""
rm -f -- "$TEMP_ENV"
TEMP_ENV=""

sudo install -d -o soprolife -g soprolife -m 0750 /opt/soprolife/venvs
sudo install -d -o soprolife -g soprolife -m 0700 "$M15_DIR/var"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  sudo -u soprolife python3 -m venv "$VENV_DIR"
fi
sudo -u soprolife "$VENV_DIR/bin/pip" install -r "$M15_DIR/requirements.lock"
sudo -u soprolife "$VENV_DIR/bin/pip" check

run_m15_env() {
  sudo -u soprolife bash -c '
    set -Eeuo pipefail
    set -a
    source "$1"
    set +a
    cd "$2"
    shift 2
    exec "$@"
  ' bash "$ENV_FILE" "$M15_DIR" "$@"
}

run_m15_env "$VENV_DIR/bin/alembic" upgrade head
run_m15_env "$VENV_DIR/bin/alembic" current
run_m15_env "$VENV_DIR/bin/alembic" check

sudo install -o root -g root -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
sudo install -o root -g root -m 0644 "$LOOPBACK_UNIT_SOURCE" "$LOOPBACK_UNIT_TARGET"
sudo systemctl daemon-reload

# Restart (e não apenas enable --now) garante que API já ativa passe a rodar
# o código do commit implantado. Units Type=simple retornam antes do processo
# ficar pronto; a espera com retry evita a corrida de inicialização que
# interrompeu o primeiro deploy produtivo (falso "conexão recusada").
sudo systemctl enable soprolife-m15-api.service
sudo systemctl restart soprolife-m15-api.service
soprolife_wait_health_ok "http://127.0.0.1:8015/api/v1/health" \
  "API M15 direta (127.0.0.1:8015)"

# Proxy loopback do painel: a unit versionada substitui qualquer processo
# manual antigo, mas somente após validação (usuário, comando, cgroup e
# listener); conflito desconhecido aborta o deploy sem matar nada.
soprolife_garantir_porta_loopback_livre "soprolife-painel-loopback.service" \
  "soprolife"
sudo systemctl enable soprolife-painel-loopback.service
sudo systemctl restart soprolife-painel-loopback.service
soprolife_wait_health_ok \
  "http://127.0.0.1:8765/painel-soprolife/api/m15/health" \
  "proxy M15 loopback (127.0.0.1:8765)"

http_200() {
  python3 - "$1" <<'PY'
import sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=8) as response:
    if response.status != 200:
        raise SystemExit(1)
    response.read(1024)
PY
}

if [[ -z "$TAILSCALE_IP" ]] && command -v tailscale >/dev/null 2>&1; then
  TAILSCALE_IP="$(tailscale ip -4 | head -n 1)"
fi
[[ "$TAILSCALE_IP" =~ ^100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || \
  fail "informe o IP Tailscale como terceiro argumento"

# Unit Tailscale existente não é substituída. Só reinicia se ainda não estiver
# servindo a rota M15 do commit implantado, e sempre aguarda a prontidão.
if ! soprolife_probe_health_ok \
     "http://$TAILSCALE_IP:8765/painel-soprolife/api/m15/health"; then
  sudo systemctl is-active --quiet soprolife-painel.service || \
    fail "serviço do painel Tailscale não está ativo"
  sudo systemctl restart soprolife-painel.service
  soprolife_wait_health_ok \
    "http://$TAILSCALE_IP:8765/painel-soprolife/api/m15/health" \
    "proxy M15 via Tailscale ($TAILSCALE_IP:8765)"
fi

http_200 "http://127.0.0.1:8765/painel-soprolife/"
http_200 "http://$TAILSCALE_IP:8765/painel-soprolife/"

API_LISTENERS="$(sudo ss -ltnH | awk '$4 ~ /:8015$/ {print $4}')"
[[ "$API_LISTENERS" == "127.0.0.1:8015" ]] || \
  fail "API não está restrita exclusivamente a 127.0.0.1:8015"
if sudo ss -ltnH | awk '$4 ~ /:5432$/ {print $4}' | \
   grep -Eq '^(0\.0\.0\.0|\[::\]|\*|100\.)'; then
  fail "PostgreSQL aparenta estar exposto externamente"
fi
if sudo ss -ltnH | awk '$4 ~ /:8015$/ {print $4}' | grep -Fq "$TAILSCALE_IP"; then
  fail "API está exposta diretamente no IP Tailscale"
fi

python3 - "$REPO_ROOT/painel-soprolife/data/m15-config.json" "$M15_FLAG" <<'PY'
import json, pathlib, sys
cfg = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert cfg["enabled"] is (sys.argv[2] == "true")
assert cfg["api_base"] == "/painel-soprolife/api/m15"
PY

# M15.5B: em go-live, revalida o endereço HTTPS privado APÓS o deploy —
# painel 200, health status=ok, enabled=true servido, m15-security.js servido
# e carregado antes de m15-nucleo.js no index.html publicado.
if (( GO_LIVE_MODE )); then
  soprolife_go_live_validar_https pos "${SOPROLIFE_M15_HTTPS_BASE_URL-}" || \
    fail "validação HTTPS pós-deploy do go-live falhou"
fi

sudo systemctl --no-pager --full status soprolife-m15-api.service
sudo systemctl --no-pager --full status soprolife-painel-loopback.service
if (( GO_LIVE_MODE )); then
  echo "Implantação técnica concluída; go-live controlado validado (enabled=true)."
else
  echo "Implantação técnica concluída; feature flag permanece false."
fi
echo "Backup verificado: $BACKUP_DIR"
echo "Nenhum usuário, dado demo ou dado real foi criado/importado."
