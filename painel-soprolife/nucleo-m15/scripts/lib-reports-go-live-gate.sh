#!/usr/bin/env bash
# M24B — independent reports go-live contract.
#
# General SOPROLIFE_M15_GO_LIVE authorization is intentionally irrelevant.
# This library performs no mutation and provisions no storage.

SOPROLIFE_REPORTS_GATE_LIB_DIR="$(
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
)"

soprolife_reports_gate_py() {
  python3 "$SOPROLIFE_REPORTS_GATE_LIB_DIR/reports_go_live_gate.py" "$@"
}

soprolife_reports_go_live_preflight() {
  local repo_root="$1"
  local unit_name="$2"
  if [ "${M15_REPORTS_ENABLED-false}" = "true" ]; then
    systemctl cat "$unit_name" | \
      soprolife_reports_gate_py preflight "$repo_root"
  else
    soprolife_reports_gate_py preflight "$repo_root" </dev/null
  fi
}

soprolife_reports_go_live_postflight() {
  soprolife_reports_gate_py postflight "$1" </dev/null
}

# M24D — piloto interno controlado. Gate independente e dedicado: nem a
# variável geral do M15 nem M15_REPORTS_ENABLED sozinho bastam; exige
# M15_REPORTS_MODE=pilot, SOPROLIFE_REPORTS_PILOT_AUTHORIZATION exata,
# storage privado, ReadWritePaths exato e manifesto de backup verificado.
soprolife_reports_go_live_pilot_preflight() {
  local repo_root="$1"
  local unit_name="$2"
  if [ "${M15_REPORTS_ENABLED-false}" = "true" ]; then
    systemctl cat "$unit_name" | \
      soprolife_reports_gate_py preflight-pilot "$repo_root"
  else
    soprolife_reports_gate_py preflight-pilot "$repo_root" </dev/null
  fi
}

soprolife_reports_go_live_pilot_postflight() {
  soprolife_reports_gate_py postflight-pilot "$1" </dev/null
}

# M24D — lê e valida (fail-closed) o modo alvo versionado
# (disabled/pilot/production) ANTES de qualquer gate rodar, para que o
# deploy escolha o gate certo sem depender de nenhum gate já ter executado.
soprolife_reports_go_live_read_target_mode() {
  soprolife_reports_gate_py read-target-mode "$1" </dev/null
}
