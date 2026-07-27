#!/usr/bin/env bash
# M24D — primeira ativação atômica, interativa e fail-closed do piloto
# interno controlado de laudos (disabled -> pilot).
#
# Este script NÃO substitui nenhum gate existente: ele orquestra, em torno do
# gate dedicado do piloto (reports_go_live_gate.py::check_pilot_preflight /
# check_pilot_postflight, via lib-reports-go-live-gate.sh), a parte que os
# gates não cobrem — identidade do worktree de produção, escopo exato do
# commit de ativação e a sequência segura de mutação com rollback.
#
# Uso:
#   activate-reports-pilot-vps.sh <commit-alvo-40hex> <commit-fiacao-40hex> \
#     <caminho-manifesto-backup> <url-https-base-privada>
#
# Variáveis de ambiente exigidas ANTES de rodar (não são argumentos):
#   SOPROLIFE_REPORTS_PILOT_AUTHORIZATION="HABILITAR PILOTO DE LAUDOS"
#
# Não altera fluxo clínico, schema, templates, RBAC, frontend ou o teto de
# assinatura — só o contrato de ativação do piloto (M15_REPORTS_MODE/
# M15_REPORTS_ENABLED/M15_REPORTS_STORAGE_DIR + data/m15-config.json).
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly M15_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly EXPECTED_REPO="/opt/soprolife/soprolife-site"
readonly EXPECTED_BRANCH="painel-soprolife-v01"
readonly ENV_FILE="/opt/soprolife/secrets/m15.env"
readonly VENV_DIR="/opt/soprolife/venvs/m15"
readonly STORAGE_ROOT="/opt/soprolife/private/reports"
readonly CONFIG_REL_PATH="painel-soprolife/data/m15-config.json"
readonly API_UNIT="soprolife-m15-api.service"
readonly LOOPBACK_UNIT="soprolife-painel-loopback.service"
readonly TAILSCALE_UNIT="soprolife-painel.service"
readonly HARDENING_LIB="$SCRIPT_DIR/lib-deploy-hardening.sh"
readonly REPORTS_GATE_LIB="$SCRIPT_DIR/lib-reports-go-live-gate.sh"
readonly ACTIVATION_LIB="$SCRIPT_DIR/lib-reports-pilot-activation.sh"

TARGET_SHA="${1-}"
WIRING_SHA="${2-}"
BACKUP_MANIFEST="${3-}"
HTTPS_BASE_URL="${4-}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly STAMP
readonly BACKUP_DIR="/opt/soprolife/backups/m15-reports-pilot-activation/$STAMP"

TMP_WORKTREE=""
TEMP_ENV_CONTENT=""
MUTATION_STARTED=0
FF_DONE=0

fail() {
  echo "ERRO: $*" >&2
  exit 1
}

usage() {
  echo "Uso: $0 <commit-alvo-40hex> <commit-fiacao-40hex> <manifesto-backup> <url-https-base>" >&2
  exit 2
}

cleanup() {
  # Sempre remove o worktree destacado e arquivos temporários, com sucesso ou
  # falha — nunca deixa resíduo do preflight na máquina.
  if [[ -n "$TMP_WORKTREE" ]]; then
    soprolife_reports_activation_worktree_remove "$REPO_ROOT" "$TMP_WORKTREE"
    rm -rf -- "$TMP_WORKTREE"
    TMP_WORKTREE=""
  fi
  if [[ -n "$TEMP_ENV_CONTENT" && -f "$TEMP_ENV_CONTENT" ]]; then
    rm -f -- "$TEMP_ENV_CONTENT"
  fi
}
trap cleanup EXIT

do_rollback() {
  # Só é chamado depois que MUTATION_STARTED=1 — antes disso, nenhuma
  # mutação real aconteceu e não há nada a desfazer. Nunca apaga dados do
  # PostgreSQL nem o storage de laudos: restaura só o EnvironmentFile e o
  # worktree de código, e reinicia os mesmos serviços.
  echo "" >&2
  echo "FALHA APÓS MUTAÇÃO — iniciando rollback." >&2
  local rollback_ok=1

  if [[ -f "$BACKUP_DIR/m15.env.before" ]]; then
    soprolife_priv install -o root -g soprolife -m 0640 \
      "$BACKUP_DIR/m15.env.before" "$ENV_FILE" || rollback_ok=0
  else
    rollback_ok=0
  fi

  if (( FF_DONE )); then
    if [[ "$(git -C "$REPO_ROOT" rev-parse HEAD)" == "$TARGET_SHA" ]]; then
      git -C "$REPO_ROOT" reset --hard "$WIRING_SHA" || rollback_ok=0
    fi
  fi

  soprolife_priv systemctl restart "$API_UNIT" || rollback_ok=0
  soprolife_wait_health_ok "http://127.0.0.1:8015/api/v1/health" \
    "API M15 direta (rollback)" || rollback_ok=0
  soprolife_priv systemctl restart "$LOOPBACK_UNIT" || rollback_ok=0
  soprolife_priv systemctl restart "$TAILSCALE_UNIT" || rollback_ok=0

  if python3 - "$SCRIPT_DIR" "$HTTPS_BASE_URL" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import reports_go_live_gate as gate
gate.check_https_workspace(sys.argv[2], expected_enabled=False)
PY
  then
    echo "ROLLBACK: frontend/API confirmados desabilitados de novo." >&2
  else
    rollback_ok=0
    echo "ROLLBACK: não foi possível reconfirmar frontend/API desabilitados (verificar manualmente)." >&2
  fi

  if (( rollback_ok )); then
    echo "ROLLBACK CONCLUÍDO: m15.env restaurado, worktree em $WIRING_SHA, serviços reiniciados." >&2
    echo "PostgreSQL e storage de laudos preservados (nenhum comando de exclusão foi executado)." >&2
  else
    echo "ROLLBACK COM PENDÊNCIAS — revisar manualmente antes de tentar de novo." >&2
  fi
}

on_error() {
  local exit_code=$?
  trap - ERR
  if (( MUTATION_STARTED )); then
    do_rollback
  fi
  exit "$exit_code"
}
trap on_error ERR

[[ -f "$HARDENING_LIB" ]] || fail "biblioteca de hardening ausente: $HARDENING_LIB"
# shellcheck source=lib-deploy-hardening.sh
source "$HARDENING_LIB"
[[ -f "$REPORTS_GATE_LIB" ]] || fail "biblioteca do gate de laudos ausente: $REPORTS_GATE_LIB"
# shellcheck source=lib-reports-go-live-gate.sh
source "$REPORTS_GATE_LIB"
[[ -f "$ACTIVATION_LIB" ]] || fail "biblioteca de ativação do piloto ausente: $ACTIVATION_LIB"
# shellcheck source=lib-reports-pilot-activation.sh
source "$ACTIVATION_LIB"

[[ -t 0 && -t 1 ]] || fail "execute em terminal interativo"
command -v git >/dev/null || fail "git não encontrado"
command -v sudo >/dev/null || fail "sudo não encontrado"
command -v systemctl >/dev/null || fail "systemctl não encontrado"
id soprolife >/dev/null 2>&1 || fail "usuário de serviço soprolife não existe"

soprolife_reports_activation_is_sha40 "$TARGET_SHA" || usage
soprolife_reports_activation_is_sha40 "$WIRING_SHA" || usage
[[ -n "$BACKUP_MANIFEST" && "$BACKUP_MANIFEST" == /* ]] || usage
[[ -n "$HTTPS_BASE_URL" ]] || usage
[[ "$TARGET_SHA" != "$WIRING_SHA" ]] || fail "commit alvo é igual ao commit de fiação — nada a ativar"

REPO_ROOT="$(cd "$EXPECTED_REPO" && git rev-parse --show-toplevel)" || \
  fail "repositório de produção ausente ou inválido: $EXPECTED_REPO"
[[ "$(readlink -f "$REPO_ROOT")" == "$EXPECTED_REPO" ]] || \
  fail "repositório deve estar em $EXPECTED_REPO"
readonly REPO_ROOT

# ── Antes de qualquer mutação: identidade e limpeza do worktree de produção ──
soprolife_reports_activation_verify_branch "$REPO_ROOT" "$EXPECTED_BRANCH" || \
  fail "branch de produção não é $EXPECTED_BRANCH (atual: $(git -C "$REPO_ROOT" branch --show-current))"

soprolife_reports_activation_verify_head "$REPO_ROOT" "$WIRING_SHA" || \
  fail "HEAD de produção não é o commit de fiação esperado (atual: $(git -C "$REPO_ROOT" rev-parse HEAD))"

soprolife_reports_activation_verify_clean "$REPO_ROOT" || \
  fail "worktree de produção não está limpo"

soprolife_reports_activation_verify_ancestor "$REPO_ROOT" "$WIRING_SHA" "$TARGET_SHA" || \
  fail "commit alvo não existe ou não descende do commit de fiação"

soprolife_reports_activation_verify_diff_scope \
  "$REPO_ROOT" "$WIRING_SHA" "$TARGET_SHA" "$CONFIG_REL_PATH" || \
  fail "commit alvo altera algo além de $CONFIG_REL_PATH"

soprolife_reports_activation_verify_target_frontend \
  "$REPO_ROOT" "$TARGET_SHA" "$CONFIG_REL_PATH" || \
  fail "config do commit alvo não tem reports_enabled=true e reports_mode=pilot"

soprolife_reports_activation_verify_authorization "${SOPROLIFE_REPORTS_PILOT_AUTHORIZATION-}" || \
  fail "SOPROLIFE_REPORTS_PILOT_AUTHORIZATION ausente ou incorreta"

# Estado alvo do M24D: intrínseco a este script (só existe para ativar o
# piloto) — nunca lido de fora, ao contrário da autorização e da URL HTTPS.
export M15_REPORTS_MODE="pilot"
export M15_REPORTS_ENABLED="true"
export M15_REPORTS_STORAGE_DIR="$STORAGE_ROOT"
export SOPROLIFE_REPORTS_BACKUP_MANIFEST="$BACKUP_MANIFEST"
export SOPROLIFE_M15_HTTPS_BASE_URL="$HTTPS_BASE_URL"

echo "== verificando que o frontend/API servidos hoje estão desabilitados =="
python3 - "$SCRIPT_DIR" "$HTTPS_BASE_URL" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import reports_go_live_gate as gate
gate.check_https_workspace(sys.argv[2], expected_enabled=False)
PY

# ── Preflight do piloto contra worktree destacado no commit ALVO ────────────
# O worktree de produção permanece no commit de fiação (desabilitado)
# durante todo o preflight; nada aqui pode mutar produção.
TMP_WORKTREE="$(mktemp -d /tmp/soprolife-reports-pilot-preflight.XXXXXX)"
rmdir "$TMP_WORKTREE"
soprolife_reports_activation_worktree_add "$REPO_ROOT" "$TMP_WORKTREE" "$TARGET_SHA" || \
  fail "não foi possível criar o worktree destacado de preflight"

echo "== preflight dedicado do piloto (worktree destacado em $TARGET_SHA) =="
PREFLIGHT_RESULT="$(soprolife_reports_go_live_pilot_preflight "$TMP_WORKTREE" "$API_UNIT")" || \
  fail "preflight dedicado do piloto reprovou o commit alvo"
[[ "$PREFLIGHT_RESULT" == "true" ]] || \
  fail "preflight dedicado do piloto não confirmou habilitação (resultado: $PREFLIGHT_RESULT)"
echo "  OK: preflight aprovado."

soprolife_reports_activation_worktree_remove "$REPO_ROOT" "$TMP_WORKTREE"
rm -rf -- "$TMP_WORKTREE"
TMP_WORKTREE=""

# ── Confirmação interativa — nenhuma mutação aconteceu até aqui ─────────────
echo ""
echo "Preflight aprovado. Resumo:"
echo "  branch:          $EXPECTED_BRANCH"
echo "  fiação (atual):  $WIRING_SHA"
echo "  alvo (ativação): $TARGET_SHA"
echo "  storage:         $STORAGE_ROOT"
echo "  manifesto:       $BACKUP_MANIFEST"
printf "Digite exatamente '%s' para continuar: " \
  "$SOPROLIFE_REPORTS_PILOT_CONFIRMATION_PHRASE"
read -r CONFIRMATION
soprolife_reports_activation_verify_confirmation "$CONFIRMATION" || fail "confirmação recusada"

sudo -v

# ═══════════════════════════ MUTAÇÃO A PARTIR DAQUI ════════════════════════
MUTATION_STARTED=1

echo "== 1/6 backup do m15.env atual =="
soprolife_priv install -d -o root -g root -m 0700 "$BACKUP_DIR"
soprolife_priv install -o root -g root -m 0600 "$ENV_FILE" "$BACKUP_DIR/m15.env.before"

echo "== 2/6 atualização atômica do m15.env (só as 3 chaves do piloto) =="
TEMP_ENV_CONTENT="$(mktemp /tmp/soprolife-m15-reports-pilot-env.XXXXXX)"
chmod 0600 "$TEMP_ENV_CONTENT"
soprolife_reports_activation_render_env "$ENV_FILE" "$STORAGE_ROOT" >"$TEMP_ENV_CONTENT"
soprolife_priv install -o root -g soprolife -m 0640 "$TEMP_ENV_CONTENT" "$ENV_FILE"
rm -f -- "$TEMP_ENV_CONTENT"
TEMP_ENV_CONTENT=""

echo "== 3/6 restart da API M15 e prova de saúde direta =="
soprolife_priv systemctl restart "$API_UNIT"
soprolife_wait_health_ok "http://127.0.0.1:8015/api/v1/health" "API M15 direta (127.0.0.1:8015)"

echo "== 4/6 fast-forward do worktree de produção até o commit alvo =="
[[ "$(git -C "$REPO_ROOT" rev-parse HEAD)" == "$WIRING_SHA" ]] || \
  fail "HEAD de produção mudou de forma inesperada antes do fast-forward"
git -C "$REPO_ROOT" merge --ff-only "$TARGET_SHA"
FF_DONE=1

echo "== 5/6 restart do painel loopback e do painel Tailscale =="
soprolife_garantir_porta_loopback_livre "$LOOPBACK_UNIT" "soprolife"
soprolife_priv systemctl restart "$LOOPBACK_UNIT"
soprolife_wait_health_ok "http://127.0.0.1:8765/painel-soprolife/api/m15/health" \
  "proxy M15 loopback (127.0.0.1:8765)"
soprolife_priv systemctl restart "$TAILSCALE_UNIT"
soprolife_priv systemctl is-active --quiet "$TAILSCALE_UNIT" || \
  fail "painel Tailscale não ficou ativo após restart"

echo "== 6/6 postflight do piloto e provas finais =="
POSTFLIGHT_RESULT="$(soprolife_reports_go_live_pilot_postflight "$REPO_ROOT")" || \
  fail "postflight do piloto reprovou o estado servido pós-ativação"
[[ "$POSTFLIGHT_RESULT" == "true" ]] || \
  fail "postflight do piloto não confirmou o acordo completo (resultado: $POSTFLIGHT_RESULT)"

python3 - "$REPO_ROOT/$CONFIG_REL_PATH" <<'PY'
import json, pathlib, sys
cfg = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert cfg.get("reports_enabled") is True, "reports_enabled != true no config servido"
assert cfg.get("reports_mode") == "pilot", "reports_mode != pilot no config servido"
PY

python3 - "$SCRIPT_DIR" "$HTTPS_BASE_URL" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import reports_go_live_gate as gate
frontend_enabled = gate.check_https_workspace(
    sys.argv[2], expected_enabled=True, expected_mode="pilot"
)
assert frontend_enabled is True
PY

run_m15_env() {
  if [[ "${SOPROLIFE_PRIV_MODE:-sudo}" == "direct" ]]; then
    (
      set -Eeuo pipefail
      set -a
      # shellcheck disable=SC1090
      source "$ENV_FILE"
      set +a
      cd "$M15_DIR"
      exec "$@"
    )
  else
    sudo -u soprolife bash -c '
      set -Eeuo pipefail
      set -a
      source "$1"
      set +a
      cd "$2"
      shift 2
      exec "$@"
    ' bash "$ENV_FILE" "$M15_DIR" "$@"
  fi
}
run_m15_env "$VENV_DIR/bin/alembic" current
run_m15_env "$VENV_DIR/bin/alembic" check
soprolife_wait_health_ok "http://127.0.0.1:8015/api/v1/health" "API M15 direta (prova final)"

echo ""
echo "PILOTO DE LAUDOS ATIVADO (modo pilot; nunca alcança assinado/finalizado/liberado)."
echo "  commit servido: $(git -C "$REPO_ROOT" rev-parse HEAD)"
echo "  backup do m15.env anterior: $BACKUP_DIR/m15.env.before"
echo "Nenhum dado de paciente real, PostgreSQL ou storage foi apagado."
