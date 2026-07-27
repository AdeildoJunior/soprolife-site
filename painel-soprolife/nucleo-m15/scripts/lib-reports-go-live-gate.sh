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
