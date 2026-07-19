#!/usr/bin/env bash
# M15.5B — Ponte de go-live controlado do deploy produtivo do Núcleo M15.
#
# Carregada via source por deploy-producao-vps.sh. Um release com enabled=true
# permanece REJEITADO por padrão; só é aceito quando as DUAS variáveis de
# ambiente estiverem presentes e válidas:
#
#   SOPROLIFE_M15_GO_LIVE=YES                    (exatamente YES, maiúsculo)
#   SOPROLIFE_M15_HTTPS_BASE_URL=https://painel-privado.exemplo.ts.net/
#
# Toda validação pesada (forma da URL, checagens estáticas do release alvo e
# probes HTTPS pré/pós) fica em go_live_https_gate.py, no mesmo diretório.
# Nenhuma função configura Serve, Funnel, certificado, firewall ou ACL, e
# nenhum hostname real de tailnet aparece aqui. Falha = deploy abortado
# (fail-closed); enabled=false continua com o comportamento atual, sem exigir
# variável nenhuma.

SOPROLIFE_GO_LIVE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

soprolife_go_live_gate_py() {
  # SOPROLIFE_GO_LIVE_GATE_PY permite injetar um dublê nos testes de shell;
  # em produção usa sempre o gate versionado ao lado desta lib.
  python3 "${SOPROLIFE_GO_LIVE_GATE_PY:-$SOPROLIFE_GO_LIVE_LIB_DIR/go_live_https_gate.py}" "$@"
}

soprolife_go_live_flag_config() {
  # Lê a feature flag do release alvo. Imprime "true" ou "false"; qualquer
  # outro valor de enabled, ou api_base fora da mesma origem, falha fechado.
  python3 - "$1" <<'PY'
import json, pathlib, sys
cfg = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
enabled = cfg.get("enabled")
if enabled is not True and enabled is not False:
    raise SystemExit("feature flag M15 precisa ser exatamente true ou false")
if cfg.get("api_base") != "/painel-soprolife/api/m15":
    raise SystemExit("api_base M15 produtivo inesperado")
print("true" if enabled else "false")
PY
}

soprolife_go_live_exigir_autorizacao() {
  # Autorização explícita do go-live. Usa [ ... ] (builtin test, comparação
  # byte a byte) de propósito: [[ == ]] respeitaria shopt nocasematch e
  # aceitaria "yes". Rejeita ausente, vazio e qualquer valor != YES.
  local autorizacao="${SOPROLIFE_M15_GO_LIVE-}"
  local base_url="${SOPROLIFE_M15_HTTPS_BASE_URL-}"
  if [ -z "$autorizacao" ] || [ -z "$base_url" ]; then
    echo "ERRO GO-LIVE (fail-closed): release com enabled=true exige as DUAS" \
      "variáveis SOPROLIFE_M15_GO_LIVE=YES e SOPROLIFE_M15_HTTPS_BASE_URL" \
      "(https://painel-privado.exemplo.ts.net/)." >&2
    return 1
  fi
  if [ "$autorizacao" != "YES" ]; then
    echo "ERRO GO-LIVE (fail-closed): SOPROLIFE_M15_GO_LIVE deve ser" \
      "exatamente YES (maiúsculo); valores como yes/true/1/on são rejeitados." >&2
    return 1
  fi
  return 0
}

soprolife_go_live_validar_url_base() {
  soprolife_go_live_gate_py validate-url "$1"
}

soprolife_go_live_checar_fonte_alvo() {
  soprolife_go_live_gate_py check-source "$1"
}

soprolife_go_live_validar_https() {
  # $1 = pre|pos ; $2 = URL base HTTPS validada.
  case "$1" in
    pre|pos) ;;
    *)
      echo "ERRO GO-LIVE (fail-closed): fase desconhecida '$1'." >&2
      return 1
      ;;
  esac
  soprolife_go_live_gate_py "check-https-$1" "$2"
}
