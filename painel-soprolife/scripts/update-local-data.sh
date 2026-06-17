#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

CSV_PATH="${SOPROLIFE_SUMMARY_CSV:-$HOME/.config/soprolife/painel/resumo-dashboard.csv}"

echo "Atualizando dados locais seguros do Painel SoproLife..."
echo

echo "1/4 - Atualizando status seguro da fonte Google Sheets..."
painel-soprolife/scripts/generate-runtime-status.sh

echo
echo "2/4 - Atualizando resumo seguro..."
if [ -f "$CSV_PATH" ]; then
  echo "CSV encontrado: $CSV_PATH"
  painel-soprolife/scripts/import-summary-csv.sh "$CSV_PATH"
else
  echo "CSV não encontrado em: $CSV_PATH"
  echo "Usando resumo-dashboard.json privado já existente, se disponível."
  painel-soprolife/scripts/sync-dashboard-summary.sh
fi

echo
echo "3/4 - Verificando segurança..."
painel-soprolife/scripts/check-access.sh

echo
echo "4/4 - Concluído."
echo
echo "Para abrir localmente:"
echo "painel-soprolife/scripts/start-local.sh"
echo
echo "Para abrir via Tailscale:"
echo "painel-soprolife/scripts/start-tailscale.sh"
