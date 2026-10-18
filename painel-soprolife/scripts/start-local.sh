#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

PROXY="painel-soprolife/scripts/command-center-local-server.py"

if [ ! -f "$PROXY" ]; then
  echo "ERRO: servidor autenticado do painel ausente: $PROXY" >&2
  echo "Abertura cancelada. Não use servidor estático para servir o painel." >&2
  exit 1
fi

# Este launcher é estritamente local. Não herdar um bind remoto do ambiente.
# O servidor oficial mantém sessão, gate de arquivos e proxy M15 inalterados.
export SOPROLIFE_PANEL_HOST=127.0.0.1
echo "Painel SoproLife — modo local com proxy Command Center"
echo "Acesso: http://127.0.0.1:${SOPROLIFE_PANEL_PORT:-8765}/painel-soprolife/"
echo "Desenvolvimento com dados sintéticos. Operação clínica real exige HTTPS."
exec python3 "$PROXY"
