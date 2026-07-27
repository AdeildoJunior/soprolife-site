#!/usr/bin/env bash
# M24D — testes focados da primeira ativação atômica do piloto de laudos
# (activate-reports-pilot-vps.sh / lib-reports-pilot-activation.sh).
#
# Cobre, sem sudo/systemd/rede e sem tocar a VPS:
#   - preflight roda antes de qualquer mutação (checagem estrutural de ordem);
#   - o diff do commit alvo fica restrito a data/m15-config.json (função real,
#     repositório Git descartável);
#   - o preflight roda contra um worktree destacado (função real de
#     criação/remoção + checagem estrutural de uso no script principal);
#   - a API é ativada antes do fast-forward do frontend (ordem no script);
#   - o postflight exige o acordo COMPLETO do piloto (usa a variante
#     dedicada, não o gate genérico);
#   - toda falha após a mutação aciona rollback (MUTATION_STARTED + trap
#     ERR fiados ao rollback; corpo do rollback sem comando destrutivo);
#   - SHA inválido, worktree sujo, autorização errada e confirmação errada
#     falham fechado (funções reais);
#   - nenhum comando de exclusão de dado do PostgreSQL ou do storage de
#     laudos existe no script.
#
# Uso: bash painel-soprolife/nucleo-m15/scripts/test-reports-pilot-activation.sh
# Exit: 0 = todos os casos passaram | 1 = houve falha.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly ACTIVATION_SCRIPT="$SCRIPT_DIR/activate-reports-pilot-vps.sh"
# shellcheck source=lib-reports-pilot-activation.sh
source "$SCRIPT_DIR/lib-reports-pilot-activation.sh"

FALHAS=0
TMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TMP_DIR"' EXIT

caso() {
  local nome="$1" esperado="$2" obtido="$3"
  if [[ "$esperado" == "$obtido" ]]; then
    echo "  PASS: $nome"
  else
    FALHAS=$((FALHAS + 1))
    echo "  FAIL: $nome — esperado '$esperado', obtido '$obtido'"
  fi
}

CONFIG_REL="painel-soprolife/data/m15-config.json"

# ── repositório Git descartável para os testes de função real ───────────────

REPO="$TMP_DIR/repo"
mkdir -p "$REPO/painel-soprolife/data"
git init -q "$REPO"
git -C "$REPO" config user.email "test@example.com"
git -C "$REPO" config user.name "Teste M24D"

printf '{"reports_enabled": false, "reports_mode": "disabled", "api_base": "/painel-soprolife/api/m15"}' \
  >"$REPO/$CONFIG_REL"
echo "readme" >"$REPO/README.md"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "fiação (disabled)"
WIRING_SHA="$(git -C "$REPO" rev-parse HEAD)"

printf '{"reports_enabled": true, "reports_mode": "pilot", "api_base": "/painel-soprolife/api/m15"}' \
  >"$REPO/$CONFIG_REL"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "ativação (pilot) — só config"
TARGET_GOOD_SHA="$(git -C "$REPO" rev-parse HEAD)"

git -C "$REPO" checkout -q "$WIRING_SHA"
printf '{"reports_enabled": true, "reports_mode": "pilot", "api_base": "/painel-soprolife/api/m15"}' \
  >"$REPO/$CONFIG_REL"
echo "mudou também" >>"$REPO/README.md"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "ativação — escopo indevido (toca README também)"
TARGET_BAD_SCOPE_SHA="$(git -C "$REPO" rev-parse HEAD)"

git -C "$REPO" checkout -q "$WIRING_SHA"
printf '{"reports_enabled": true, "reports_mode": "production", "api_base": "/painel-soprolife/api/m15"}' \
  >"$REPO/$CONFIG_REL"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "ativação indevida — modo production"
TARGET_BAD_MODE_SHA="$(git -C "$REPO" rev-parse HEAD)"

git -C "$REPO" checkout -q "$WIRING_SHA"
printf '{"reports_enabled": false, "reports_mode": "disabled", "api_base": "/painel-soprolife/api/m15"}' \
  >"$REPO/$CONFIG_REL"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "ativação indevida — segue disabled"
TARGET_BAD_FLAG_SHA="$(git -C "$REPO" rev-parse HEAD)"

git -C "$REPO" checkout -q --orphan outra-linha 2>/dev/null
git -C "$REPO" rm -rf --quiet . >/dev/null 2>&1 || true
echo "sem relação" >"$REPO/outro.txt"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "commit sem relação (branch órfã)"
UNRELATED_SHA="$(git -C "$REPO" rev-parse HEAD)"

git -C "$REPO" checkout -q "$WIRING_SHA"

NONEXISTENT_SHA="0123456789abcdef0123456789abcdef01234567"

echo "── formato de SHA (40 hex) ──"

soprolife_reports_activation_is_sha40 "$TARGET_GOOD_SHA"
caso "SHA real de 40 hex é aceito (rc 0)" 0 $?

for valor in "" "abc" "ABCDEF0123456789ABCDEF0123456789ABCDEF01" "main" \
  "0123456789abcdef0123456789abcdef0123456" \
  "0123456789abcdef0123456789abcdef012345678"; do
  soprolife_reports_activation_is_sha40 "$valor"
  caso "SHA inválido rejeitado: '$valor' (rc 1)" 1 $?
done

echo "── branch/HEAD/limpeza do worktree de produção ──"

soprolife_reports_activation_verify_branch "$REPO" "$(git -C "$REPO" branch --show-current)"
caso "branch atual é aceita (rc 0)" 0 $?

soprolife_reports_activation_verify_branch "$REPO" "branch-que-nao-existe"
caso "branch errada é rejeitada (rc 1)" 1 $?

soprolife_reports_activation_verify_head "$REPO" "$WIRING_SHA"
caso "HEAD igual ao esperado é aceito (rc 0)" 0 $?

soprolife_reports_activation_verify_head "$REPO" "$TARGET_GOOD_SHA"
caso "HEAD diferente do esperado é rejeitado (rc 1)" 1 $?

soprolife_reports_activation_verify_clean "$REPO"
caso "worktree limpo é aceito (rc 0)" 0 $?

echo "sujo" >"$REPO/sujeira.txt"
soprolife_reports_activation_verify_clean "$REPO"
caso "worktree sujo (arquivo não rastreado) é rejeitado (rc 1)" 1 $?
rm -f -- "$REPO/sujeira.txt"

echo "" >>"$REPO/README.md"
soprolife_reports_activation_verify_clean "$REPO"
caso "worktree sujo (arquivo modificado) é rejeitado (rc 1)" 1 $?
git -C "$REPO" checkout -q -- README.md

echo "── ancestralidade (commit alvo precisa descender da fiação) ──"

soprolife_reports_activation_verify_ancestor "$REPO" "$WIRING_SHA" "$TARGET_GOOD_SHA"
caso "commit alvo descendente é aceito (rc 0)" 0 $?

soprolife_reports_activation_verify_ancestor "$REPO" "$WIRING_SHA" "$UNRELATED_SHA"
caso "commit sem relação é rejeitado (rc 1)" 1 $?

soprolife_reports_activation_verify_ancestor "$REPO" "$WIRING_SHA" "$NONEXISTENT_SHA"
caso "SHA alvo inexistente é rejeitado (rc 1)" 1 $?

soprolife_reports_activation_verify_ancestor "$REPO" "$NONEXISTENT_SHA" "$TARGET_GOOD_SHA"
caso "SHA de fiação inexistente é rejeitado (rc 1)" 1 $?

echo "── escopo do diff do commit alvo (só data/m15-config.json) ──"

soprolife_reports_activation_verify_diff_scope \
  "$REPO" "$WIRING_SHA" "$TARGET_GOOD_SHA" "$CONFIG_REL"
caso "diff restrito ao config é aceito (rc 0)" 0 $?

soprolife_reports_activation_verify_diff_scope \
  "$REPO" "$WIRING_SHA" "$TARGET_BAD_SCOPE_SHA" "$CONFIG_REL"
caso "diff que também toca README é rejeitado (rc 1)" 1 $?

soprolife_reports_activation_verify_diff_scope \
  "$REPO" "$WIRING_SHA" "$WIRING_SHA" "$CONFIG_REL"
caso "diff vazio (mesmo commit) é rejeitado (rc 1)" 1 $?

echo "── config do commit alvo (reports_enabled=true e reports_mode=pilot) ──"

soprolife_reports_activation_verify_target_frontend "$REPO" "$TARGET_GOOD_SHA" "$CONFIG_REL"
caso "config alvo enabled=true/mode=pilot é aceita (rc 0)" 0 $?

soprolife_reports_activation_verify_target_frontend "$REPO" "$TARGET_BAD_MODE_SHA" "$CONFIG_REL"
caso "config alvo em modo production é rejeitada (rc 1)" 1 $?

soprolife_reports_activation_verify_target_frontend "$REPO" "$TARGET_BAD_FLAG_SHA" "$CONFIG_REL"
caso "config alvo ainda disabled é rejeitada (rc 1)" 1 $?

soprolife_reports_activation_verify_target_frontend "$REPO" "$WIRING_SHA" "$CONFIG_REL"
caso "config da própria fiação (disabled) é rejeitada (rc 1)" 1 $?

echo "── worktree destacado (usado pelo preflight) ──"

WORKTREE_DIR="$TMP_DIR/preflight-worktree"
soprolife_reports_activation_worktree_add "$REPO" "$WORKTREE_DIR" "$TARGET_GOOD_SHA"
caso "criação do worktree destacado (rc 0)" 0 $?
caso "worktree contém o config do commit ALVO (pilot)" \
  '{"reports_enabled": true, "reports_mode": "pilot", "api_base": "/painel-soprolife/api/m15"}' \
  "$(cat "$WORKTREE_DIR/$CONFIG_REL" 2>/dev/null)"
caso "worktree está com HEAD destacado (sem branch)" "" \
  "$(git -C "$WORKTREE_DIR" branch --show-current)"
caso "worktree de produção segue intacto na fiação" "$WIRING_SHA" \
  "$(git -C "$REPO" rev-parse HEAD)"

soprolife_reports_activation_worktree_remove "$REPO" "$WORKTREE_DIR"
caso "worktree destacado foi removido do disco" "ausente" \
  "$([[ -d "$WORKTREE_DIR" ]] && echo presente || echo ausente)"
caso "git worktree list não lista mais o worktree removido" "0" \
  "$(git -C "$REPO" worktree list --porcelain | grep -c "worktree $WORKTREE_DIR$")"

echo "── atualização atômica do m15.env (só as 3 chaves do piloto) ──"

ENV_ANTES="$TMP_DIR/m15.env.antes"
cat >"$ENV_ANTES" <<'EOF'
# comentário preservado
M15_ENV=prod
M15_AUTH_SECRET=segredo-super-secreto-que-nao-pode-mudar
M15_REPORTS_MODE=disabled
M15_REPORTS_ENABLED=false
EOF

ENV_DEPOIS="$(soprolife_reports_activation_render_env "$ENV_ANTES" "/opt/soprolife/private/reports")"

caso "comentário preservado" "1" \
  "$(grep -c '^# comentário preservado$' <<<"$ENV_DEPOIS")"
caso "segredo preexistente preservado byte a byte" "1" \
  "$(grep -c '^M15_AUTH_SECRET=segredo-super-secreto-que-nao-pode-mudar$' <<<"$ENV_DEPOIS")"
caso "M15_ENV preexistente preservado" "1" \
  "$(grep -c '^M15_ENV=prod$' <<<"$ENV_DEPOIS")"
caso "M15_REPORTS_MODE sobrescrito para pilot" "1" \
  "$(grep -c '^M15_REPORTS_MODE=pilot$' <<<"$ENV_DEPOIS")"
caso "M15_REPORTS_ENABLED sobrescrito para true" "1" \
  "$(grep -c '^M15_REPORTS_ENABLED=true$' <<<"$ENV_DEPOIS")"
caso "M15_REPORTS_STORAGE_DIR acrescentado (ausente antes)" "1" \
  "$(grep -c '^M15_REPORTS_STORAGE_DIR=/opt/soprolife/private/reports$' <<<"$ENV_DEPOIS")"
caso "nenhum valor antigo de disabled/false sobrou" "0" \
  "$(grep -cE '^M15_REPORTS_(MODE=disabled|ENABLED=false)$' <<<"$ENV_DEPOIS")"

ENV_MEIO="$TMP_DIR/m15.env.meio"
printf '%s\n' "$ENV_DEPOIS" >"$ENV_MEIO"
ENV_DEPOIS_2="$(soprolife_reports_activation_render_env "$ENV_MEIO" "/opt/soprolife/private/reports")"
caso "reescrita é idempotente (segunda passada é igual à primeira)" "$ENV_DEPOIS" "$ENV_DEPOIS_2"

echo "── autorização dedicada do piloto (frase exata) ──"

soprolife_reports_activation_verify_authorization "HABILITAR PILOTO DE LAUDOS"
caso "frase exata de autorização é aceita (rc 0)" 0 $?

for valor in "" "habilitar piloto de laudos" "HABILITAR PILOTO DE LAUDOS " \
  " HABILITAR PILOTO DE LAUDOS" "HABILITAR PILOTO DE LAUDO" "SIM" "YES"; do
  soprolife_reports_activation_verify_authorization "$valor"
  caso "autorização inválida rejeitada: '$valor' (rc 1)" 1 $?
done

echo "── confirmação interativa do piloto (frase exata) ──"

soprolife_reports_activation_verify_confirmation "ATIVAR PILOTO DE LAUDOS"
caso "frase exata de confirmação é aceita (rc 0)" 0 $?

for valor in "" "ativar piloto de laudos" "ATIVAR PILOTO DE LAUDOS " \
  "IMPLANTAR M15" "HABILITAR PILOTO DE LAUDOS" "s" "SIM"; do
  soprolife_reports_activation_verify_confirmation "$valor"
  caso "confirmação inválida rejeitada: '$valor' (rc 1)" 1 $?
done

echo "── fiação estrutural de activate-reports-pilot-vps.sh ──"

fiacao() {
  local nome="$1" padrao="$2"
  if grep -q "$padrao" "$ACTIVATION_SCRIPT"; then
    caso "$nome" 0 0
  else
    caso "$nome" 0 1
  fi
}

fiacao "script carrega a lib de ativação do piloto" 'lib-reports-pilot-activation.sh'
fiacao "script carrega o gate dedicado do piloto" 'lib-reports-go-live-gate.sh'
fiacao "script usa o preflight dedicado do piloto" \
  'soprolife_reports_go_live_pilot_preflight'
fiacao "script usa o postflight dedicado do piloto" \
  'soprolife_reports_go_live_pilot_postflight'
if grep -q 'worktree add --detach' "$SCRIPT_DIR/lib-reports-pilot-activation.sh" && \
   grep -q 'soprolife_reports_activation_worktree_add' "$ACTIVATION_SCRIPT"; then
  caso "script cria worktree destacado (HEAD solto) para o preflight" 0 0
else
  caso "script cria worktree destacado (HEAD solto) para o preflight" 0 1
fi
fiacao "script exige confirmação interativa exata" \
  'soprolife_reports_activation_verify_confirmation'
fiacao "script valida a autorização dedicada do piloto" \
  'soprolife_reports_activation_verify_authorization'
fiacao "script valida o escopo do diff do commit alvo" \
  'soprolife_reports_activation_verify_diff_scope'
fiacao "script valida a ancestralidade do commit alvo" \
  'soprolife_reports_activation_verify_ancestor'
fiacao "script marca o início da mutação" 'MUTATION_STARTED=1'
fiacao "script registra rollback na trap de erro" 'trap on_error ERR'

PREFLIGHT_LINE="$(grep -n 'soprolife_reports_go_live_pilot_preflight "\$TMP_WORKTREE"' \
  "$ACTIVATION_SCRIPT" | head -1 | cut -d: -f1)"
WORKTREE_ADD_LINE="$(grep -n 'soprolife_reports_activation_worktree_add' \
  "$ACTIVATION_SCRIPT" | head -1 | cut -d: -f1)"
MUTATION_LINE="$(grep -n '^MUTATION_STARTED=1$' "$ACTIVATION_SCRIPT" | head -1 | cut -d: -f1)"
CONFIRMATION_LINE="$(grep -n 'soprolife_reports_activation_verify_confirmation' \
  "$ACTIVATION_SCRIPT" | head -1 | cut -d: -f1)"

if [[ -n "$WORKTREE_ADD_LINE" && -n "$PREFLIGHT_LINE" ]] \
   && (( WORKTREE_ADD_LINE < PREFLIGHT_LINE )); then
  caso "worktree destacado é criado ANTES do preflight rodar" 0 0
else
  caso "worktree destacado é criado ANTES do preflight rodar" 0 1
fi

if [[ -n "$PREFLIGHT_LINE" && -n "$CONFIRMATION_LINE" && -n "$MUTATION_LINE" ]] \
   && (( PREFLIGHT_LINE < CONFIRMATION_LINE && CONFIRMATION_LINE < MUTATION_LINE )); then
  caso "preflight roda ANTES da confirmação e de qualquer mutação" 0 0
else
  caso "preflight roda ANTES da confirmação e de qualquer mutação" 0 1
fi

API_RESTART_LINE="$(grep -n '^soprolife_priv systemctl restart "$API_UNIT"$' "$ACTIVATION_SCRIPT" | head -1 | cut -d: -f1)"
FAST_FORWARD_LINE="$(grep -n 'git -C "\$REPO_ROOT" merge --ff-only' "$ACTIVATION_SCRIPT" | head -1 | cut -d: -f1)"
if [[ -n "$API_RESTART_LINE" && -n "$FAST_FORWARD_LINE" && -n "$MUTATION_LINE" ]] \
   && (( MUTATION_LINE < API_RESTART_LINE && API_RESTART_LINE < FAST_FORWARD_LINE )); then
  caso "API é reiniciada e ativada ANTES do fast-forward do frontend" 0 0
else
  caso "API é reiniciada e ativada ANTES do fast-forward do frontend" 0 1
fi

POSTFLIGHT_LINE="$(grep -n 'soprolife_reports_go_live_pilot_postflight "\$REPO_ROOT"' \
  "$ACTIVATION_SCRIPT" | head -1 | cut -d: -f1)"
if [[ -n "$FAST_FORWARD_LINE" && -n "$POSTFLIGHT_LINE" ]] \
   && (( FAST_FORWARD_LINE < POSTFLIGHT_LINE )); then
  caso "postflight (dedicado do piloto) roda depois do fast-forward" 0 0
else
  caso "postflight (dedicado do piloto) roda depois do fast-forward" 0 1
fi

# O gate único/genérico (produção) nunca deve ser confundido com o dedicado
# do piloto nesta orquestração — a linha só pode conter a variante "_pilot_".
if grep -nE 'soprolife_reports_go_live_(pre|post)flight[^_]' "$ACTIVATION_SCRIPT" >/dev/null; then
  caso "postflight/preflight genéricos (não-piloto) não são usados aqui" 0 1
else
  caso "postflight/preflight genéricos (não-piloto) não são usados aqui" 0 0
fi

echo "── rollback é acionado em toda falha após a mutação ──"

ON_ERROR_BODY="$(awk '/^on_error\(\) \{/,/^}/' "$ACTIVATION_SCRIPT")"
if grep -q 'MUTATION_STARTED' <<<"$ON_ERROR_BODY" && grep -q 'do_rollback' <<<"$ON_ERROR_BODY"; then
  caso "on_error só chama do_rollback quando MUTATION_STARTED estiver ligado" 0 0
else
  caso "on_error só chama do_rollback quando MUTATION_STARTED estiver ligado" 0 1
fi

DO_ROLLBACK_BODY="$(awk '/^do_rollback\(\) \{/,/^}/' "$ACTIVATION_SCRIPT")"
for padrao in 'm15.env.before' 'reset --hard "$WIRING_SHA"' 'restart "$API_UNIT"' \
  'restart "$LOOPBACK_UNIT"' 'restart "$TAILSCALE_UNIT"' 'expected_enabled=False'; do
  if grep -q "$padrao" <<<"$DO_ROLLBACK_BODY"; then
    caso "rollback restaura: $padrao" 0 0
  else
    caso "rollback restaura: $padrao" 0 1
  fi
done

echo "── nenhum comando de exclusão de PostgreSQL/storage de laudos existe ──"

for padrao in 'dropdb' 'DROP DATABASE' 'DROP TABLE' 'TRUNCATE' 'DELETE FROM' \
  'pg_dump.*--clean' 'rm -rf.*private/reports' \
  'rm -rf.*STORAGE_ROOT' 'rm -rf "$STORAGE_ROOT"'; do
  if grep -qE "$padrao" "$ACTIVATION_SCRIPT"; then
    caso "comando destrutivo AUSENTE: $padrao" 0 1
  else
    caso "comando destrutivo AUSENTE: $padrao" 0 0
  fi
done

echo "── sintaxe e permissões ──"

bash -n "$ACTIVATION_SCRIPT" >/dev/null 2>&1
caso "bash -n em activate-reports-pilot-vps.sh (rc 0)" 0 $?

bash -n "$SCRIPT_DIR/lib-reports-pilot-activation.sh" >/dev/null 2>&1
caso "bash -n em lib-reports-pilot-activation.sh (rc 0)" 0 $?

[[ -x "$ACTIVATION_SCRIPT" ]]
caso "activate-reports-pilot-vps.sh é executável" 0 $?

echo
if (( FALHAS )); then
  echo "RESULTADO: $FALHAS falha(s)."
  exit 1
fi
echo "RESULTADO: todos os casos passaram."
