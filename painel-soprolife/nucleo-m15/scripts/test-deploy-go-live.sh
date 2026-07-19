#!/usr/bin/env bash
# M15.5B — Testes offline da ponte de go-live do deploy produtivo.
#
# Cobre o lado shell da ponte: matriz de autorização (somente YES maiúsculo),
# leitura fail-closed da feature flag, validação da URL base via CLI do gate
# Python (sem rede: só parsing), fiação do deploy-producao-vps.sh e a garantia
# de que o release integrado M15.5C (enabled=true) é um alvo válido do
# check-source, com a ponte seguindo fail-closed. Os probes HTTPS e as
# checagens estáticas profundas são cobertos em test_go_live_https_gate.py
# com rede mockada.
#
# Uso: bash painel-soprolife/nucleo-m15/scripts/test-deploy-go-live.sh
# Exit: 0 = todos os casos passaram | 1 = houve falha.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# shellcheck source=lib-go-live-gate.sh
source "$SCRIPT_DIR/lib-go-live-gate.sh"

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

autorizacao() {
  # Executa a checagem de autorização em subshell com ambiente controlado.
  (
    unset SOPROLIFE_M15_GO_LIVE SOPROLIFE_M15_HTTPS_BASE_URL
    if [[ "${1-__unset__}" != "__unset__" ]]; then
      export SOPROLIFE_M15_GO_LIVE="$1"
    fi
    if [[ "${2-__unset__}" != "__unset__" ]]; then
      export SOPROLIFE_M15_HTTPS_BASE_URL="$2"
    fi
    soprolife_go_live_exigir_autorizacao
  ) >/dev/null 2>&1
}

URL_OK="https://painel-privado.exemplo.ts.net/"

echo "── autorização explícita do go-live (fail-closed) ──"

autorizacao "YES" "$URL_OK"
caso "YES exato com URL presente é aceito (rc 0)" 0 $?

autorizacao __unset__ __unset__
caso "nenhuma variável definida é rejeitado (rc 1)" 1 $?

autorizacao "YES" __unset__
caso "falta a URL base é rejeitado (rc 1)" 1 $?

autorizacao __unset__ "$URL_OK"
caso "falta a autorização é rejeitado (rc 1)" 1 $?

autorizacao "" "$URL_OK"
caso "autorização vazia é rejeitada (rc 1)" 1 $?

autorizacao "YES" ""
caso "URL base vazia é rejeitada (rc 1)" 1 $?

for valor in yes Yes true TRUE 1 on ON " YES" "YES " "Y" "SIM" "NO"; do
  autorizacao "$valor" "$URL_OK"
  caso "autorização '$valor' é rejeitada (rc 1)" 1 $?
done

(
  shopt -s nocasematch
  unset SOPROLIFE_M15_HTTPS_BASE_URL
  export SOPROLIFE_M15_GO_LIVE="yes" SOPROLIFE_M15_HTTPS_BASE_URL="$URL_OK"
  soprolife_go_live_exigir_autorizacao
) >/dev/null 2>&1
caso "'yes' segue rejeitado mesmo com shopt nocasematch (rc 1)" 1 $?

echo "── leitura fail-closed da feature flag do release alvo ──"

CFG="$TMP_DIR/m15-config.json"

printf '{"enabled": false, "api_base": "/painel-soprolife/api/m15"}' >"$CFG"
SAIDA="$(soprolife_go_live_flag_config "$CFG" 2>/dev/null)"
caso "enabled=false lê 'false' (fluxo atual preservado)" "false" "$SAIDA"

printf '{"enabled": true, "api_base": "/painel-soprolife/api/m15"}' >"$CFG"
SAIDA="$(soprolife_go_live_flag_config "$CFG" 2>/dev/null)"
caso "enabled=true lê 'true' (entra no modo go-live)" "true" "$SAIDA"

printf '{"enabled": "yes", "api_base": "/painel-soprolife/api/m15"}' >"$CFG"
soprolife_go_live_flag_config "$CFG" >/dev/null 2>&1
caso "enabled não-booleano falha fechado (rc != 0)" 1 $((! ! $?))

printf '{"enabled": true, "api_base": "/outra/api"}' >"$CFG"
soprolife_go_live_flag_config "$CFG" >/dev/null 2>&1
caso "api_base alterado falha fechado (rc != 0)" 1 $((! ! $?))

printf 'não é json' >"$CFG"
soprolife_go_live_flag_config "$CFG" >/dev/null 2>&1
caso "config malformada falha fechado (rc != 0)" 1 $((! ! $?))

echo "── validação da forma da URL base (CLI do gate, sem rede) ──"

valida_url() {
  soprolife_go_live_validar_url_base "$1" >/dev/null 2>&1
}

valida_url "$URL_OK"
caso "URL HTTPS raiz válida é aceita (rc 0)" 0 $?

for url in \
  "http://painel-privado.exemplo.ts.net/" \
  "https://usuario:senha@painel-privado.exemplo.ts.net/" \
  "https://painel-privado.exemplo.ts.net/?q=1" \
  "https://painel-privado.exemplo.ts.net/#frag" \
  "https://painel-privado.exemplo.ts.net/painel-soprolife/" \
  "https://" \
  "https://host:porta/" \
  "painel-privado.exemplo.ts.net"; do
  valida_url "$url"
  caso "URL inválida rejeitada: $url" 1 $?
done

echo "── fase desconhecida da validação HTTPS ──"

soprolife_go_live_validar_https durante "$URL_OK" >/dev/null 2>&1
caso "fase desconhecida falha fechado (rc 1)" 1 $?

echo "── fiação do deploy-producao-vps.sh (anti-regressão) ──"

DEPLOY="$SCRIPT_DIR/deploy-producao-vps.sh"

fiacao() {
  local nome="$1" padrao="$2"
  if grep -q "$padrao" "$DEPLOY"; then
    caso "$nome" 0 0
  else
    caso "$nome" 0 1
  fi
}

fiacao "deploy carrega a lib da ponte de go-live" 'lib-go-live-gate.sh'
fiacao "deploy decide pelo flag lido fail-closed" 'soprolife_go_live_flag_config'
fiacao "deploy exige autorização explícita no enabled=true" \
  'soprolife_go_live_exigir_autorizacao'
fiacao "deploy valida a forma da URL base" 'soprolife_go_live_validar_url_base'
fiacao "deploy checa estaticamente o release alvo" \
  'soprolife_go_live_checar_fonte_alvo'
fiacao "deploy valida HTTPS antes da mutação" \
  'soprolife_go_live_validar_https pre'
fiacao "deploy revalida HTTPS após o deploy" \
  'soprolife_go_live_validar_https pos'

# As proteções existentes não podem ter sido removidas pela ponte.
for padrao in \
  'pg_dump --format=custom' \
  'git bundle create' \
  'soprolife_wait_health_ok' \
  'soprolife_garantir_porta_loopback_livre' \
  'alembic. upgrade head' \
  '127.0.0.1:8015' \
  'IMPLANTAR M15' \
  'MUTATION_STARTED=1'; do
  if grep -q "$padrao" "$DEPLOY"; then
    caso "proteção existente preservada: $padrao" 0 0
  else
    caso "proteção existente preservada: $padrao" 0 1
  fi
done

echo "── release integrado M15.5C: enabled=true e alvo aprovado no check-source ──"

# M15.5C: o release integrado (ponte + go-live) tem enabled=true; a ponte
# segue fail-closed no deploy e o próprio repositório é um alvo válido.
SAIDA="$(soprolife_go_live_flag_config \
  "$REPO_ROOT/painel-soprolife/data/m15-config.json" 2>/dev/null)"
caso "data/m15-config.json deste release lê 'true'" "true" "$SAIDA"

if soprolife_go_live_checar_fonte_alvo "$REPO_ROOT" >/dev/null 2>&1; then
  caso "release integrado passa no check-source do gate" 0 0
else
  caso "release integrado passa no check-source do gate" 0 1
fi

echo
if (( FALHAS )); then
  echo "RESULTADO: $FALHAS falha(s)."
  exit 1
fi
echo "RESULTADO: todos os casos passaram."
