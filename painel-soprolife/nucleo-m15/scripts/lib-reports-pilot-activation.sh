#!/usr/bin/env bash
# M24D — validações puras e coordenação de saída usadas pela primeira ativação
# atômica do piloto de laudos (activate-reports-pilot-vps.sh).
#
# As validações são testáveis isoladamente contra um repositório Git
# descartável em /tmp: nenhuma chama sudo/systemctl ou acessa rede. O handler
# de EXIT apenas coordena callbacks `do_rollback`/`cleanup` definidos pelo
# chamador e é testado com mocks. A implementação privilegiada de backup,
# restart e fast-forward continua em activate-reports-pilot-vps.sh.

readonly SOPROLIFE_REPORTS_PILOT_CONFIRMATION_PHRASE="ATIVAR PILOTO DE LAUDOS"
readonly SOPROLIFE_REPORTS_PILOT_AUTHORIZATION_PHRASE="HABILITAR PILOTO DE LAUDOS"

soprolife_reports_activation_handle_exit() {
  # Handler único para trap EXIT. Diferentemente de ERR, EXIT também cobre
  # falhas convertidas em `exit` explícito por wrappers `... || fail`.
  # ROLLBACK_ATTEMPTED impede repetição mesmo se cleanup/rollback falharem.
  local exit_code="$1"
  trap - EXIT ERR
  set +e
  if (( exit_code != 0 && MUTATION_STARTED && ! ROLLBACK_ATTEMPTED )); then
    ROLLBACK_ATTEMPTED=1
    do_rollback
  fi
  cleanup
  exit "$exit_code"
}

soprolife_reports_activation_is_sha40() {
  # Formato estrito: 40 hex minúsculos. Falha fechado em qualquer outra coisa
  # (SHA curto, maiúsculo, ref simbólica, string vazia).
  [[ "${1-}" =~ ^[0-9a-f]{40}$ ]]
}

soprolife_reports_activation_verify_branch() {
  local repo_root="$1" expected_branch="$2"
  [[ "$(git -C "$repo_root" branch --show-current)" == "$expected_branch" ]]
}

soprolife_reports_activation_verify_head() {
  local repo_root="$1" expected_sha="$2"
  [[ "$(git -C "$repo_root" rev-parse HEAD)" == "$expected_sha" ]]
}

soprolife_reports_activation_verify_clean() {
  local repo_root="$1"
  [[ -z "$(git -C "$repo_root" status --porcelain=v1 --untracked-files=all)" ]]
}

soprolife_reports_activation_verify_authorization() {
  # Frase exata da autorização dedicada do piloto — independente da
  # verificação equivalente (e redundante de propósito) dentro do gate
  # Python; falha fechado em qualquer divergência, inclusive espaço extra.
  [[ "${1-}" == "$SOPROLIFE_REPORTS_PILOT_AUTHORIZATION_PHRASE" ]]
}

soprolife_reports_activation_verify_confirmation() {
  # Confirmação interativa exata — só passa com o texto digitado idêntico.
  [[ "${1-}" == "$SOPROLIFE_REPORTS_PILOT_CONFIRMATION_PHRASE" ]]
}

soprolife_reports_activation_verify_ancestor() {
  # true somente se $2 (ancestral esperado) é ancestral real de $3 (alvo) no
  # histórico do repositório $1. Falha fechado se algum SHA não existir.
  local repo_root="$1" ancestor="$2" descendant="$3"
  git -C "$repo_root" cat-file -e "${ancestor}^{commit}" 2>/dev/null || return 1
  git -C "$repo_root" cat-file -e "${descendant}^{commit}" 2>/dev/null || return 1
  git -C "$repo_root" merge-base --is-ancestor "$ancestor" "$descendant"
}

soprolife_reports_activation_verify_diff_scope() {
  # true somente se o diff entre $2 e $3 tocar EXATAMENTE o caminho relativo
  # $4 — nem um arquivo a mais, nem zero arquivos.
  local repo_root="$1" base="$2" target="$3" allowed_path="$4"
  local changed
  changed="$(git -C "$repo_root" diff --name-only "$base" "$target" -- 2>/dev/null)" || return 1
  [[ "$changed" == "$allowed_path" ]]
}

soprolife_reports_activation_verify_target_frontend() {
  # Lê data/m15-config.json DIRETO do objeto Git do commit alvo (sem
  # checkout) e confirma reports_enabled=true e reports_mode=pilot. Checagem
  # de campo, redundante de propósito com o gate dedicado (que roda depois,
  # contra o worktree destacado) — nunca confia em um único caminho.
  local repo_root="$1" target="$2" config_path="$3" blob
  blob="$(git -C "$repo_root" show "${target}:${config_path}" 2>/dev/null)" || return 1
  printf '%s' "$blob" | python3 -c '
import json, sys
cfg = json.load(sys.stdin)
if cfg.get("reports_enabled") is not True:
    raise SystemExit("reports_enabled_not_true")
if cfg.get("reports_mode") != "pilot":
    raise SystemExit("reports_mode_not_pilot")
'
}

soprolife_reports_activation_worktree_add() {
  # Cria um worktree Git destacado (HEAD solto, sem branch) em $2 apontando
  # para o commit $3 — nunca move o worktree de produção nem cria branch.
  local repo_root="$1" worktree_dir="$2" target="$3"
  git -C "$repo_root" worktree add --detach "$worktree_dir" "$target" >/dev/null
}

soprolife_reports_activation_worktree_remove() {
  # Idempotente: some silenciosamente se o worktree já não existir.
  local repo_root="$1" worktree_dir="$2"
  if [[ -n "$worktree_dir" && -d "$worktree_dir" ]]; then
    git -C "$repo_root" worktree remove --force "$worktree_dir" 2>/dev/null || \
      rm -rf -- "$worktree_dir"
  fi
  git -C "$repo_root" worktree prune >/dev/null 2>&1 || true
}

soprolife_reports_activation_render_env() {
  # Lê o EnvironmentFile atual em $1 e imprime, em stdout, o MESMO conteúdo
  # com apenas M15_REPORTS_MODE/M15_REPORTS_ENABLED/M15_REPORTS_STORAGE_DIR
  # substituídos (ou acrescentados, se ausentes) — todo o resto (segredos,
  # ordem, comentários, linhas em branco) permanece byte a byte. Função pura:
  # não escreve em $1, só imprime o novo conteúdo para o chamador instalar.
  local env_file="$1" storage_root="$2"
  python3 - "$env_file" "$storage_root" <<'PY'
import pathlib
import sys

env_path, storage_root = sys.argv[1], sys.argv[2]
overrides = {
    "M15_REPORTS_MODE": "pilot",
    "M15_REPORTS_ENABLED": "true",
    "M15_REPORTS_STORAGE_DIR": storage_root,
}
lines = pathlib.Path(env_path).read_text(encoding="utf-8").splitlines()
seen = set()
output = []
for raw in lines:
    stripped = raw.strip()
    if not stripped or stripped.startswith("#") or "=" not in raw:
        output.append(raw)
        continue
    key = raw.split("=", 1)[0].strip()
    if key in overrides:
        output.append(f"{key}={overrides[key]}")
        seen.add(key)
    else:
        output.append(raw)
for key, value in overrides.items():
    if key not in seen:
        output.append(f"{key}={value}")
text = "\n".join(output)
if not text.endswith("\n"):
    text += "\n"
sys.stdout.write(text)
PY
}
