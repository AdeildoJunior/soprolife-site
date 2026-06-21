#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

PROXY="painel-soprolife/scripts/command-center-local-server.py"

if [ -f "$PROXY" ]; then
  echo "Painel SoproLife — modo local com proxy Command Center"
  echo "Acesso: http://127.0.0.1:8765/painel-soprolife/"
  echo
  python3 "$PROXY"
else
  echo "Painel SoproLife — modo estático (sem proxy)"
  echo "Acesso: http://127.0.0.1:8765/painel-soprolife/"
  echo
  echo "Pressione Ctrl+C para desligar."
  echo
  python3 -m http.server 8765 --bind 127.0.0.1
fi
