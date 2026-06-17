#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

echo "Painel SoproLife - modo local"
echo "Acesso:"
echo "http://127.0.0.1:8765/painel-soprolife/"
echo
echo "Pressione Ctrl+C para desligar."
echo

python3 -m http.server 8765 --bind 127.0.0.1
