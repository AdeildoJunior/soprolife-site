#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

# ---------------------------------------------------------------------------
# Pré-requisitos do conector Google Sheets via ADC
# ---------------------------------------------------------------------------
_VENV_PYTHON="$HOME/.local/share/soprolife/venvs/google-sheets/bin/python"
_SHEETS_CONFIG="$HOME/.config/soprolife/painel/google-sheets.local.json"
_ADC_CONFIG="$HOME/.config/gcloud/application_default_credentials.json"
_SHEETS_SCRIPT="painel-soprolife/scripts/read-sheets-summary-adc.py"
_CSV_PATH="${SOPROLIFE_SUMMARY_CSV:-$HOME/.config/soprolife/painel/resumo-dashboard.csv}"

# Verifica se todos os pré-requisitos do Google Sheets estão presentes
_sheets_available=false
if [ -f "$_SHEETS_CONFIG" ] && \
   [ -f "$_ADC_CONFIG" ] && \
   [ -f "$_SHEETS_SCRIPT" ] && \
   [ -f "$_VENV_PYTHON" ]; then
  _sheets_available=true
fi

# ---------------------------------------------------------------------------

echo "Atualizando dados locais seguros do Painel SoproLife..."
echo

echo "1/4 - Atualizando status seguro da fonte Google Sheets..."
painel-soprolife/scripts/generate-runtime-status.sh

echo
echo "2/4 - Atualizando resumo seguro..."

if [ "$_sheets_available" = true ]; then
  echo "Fonte: Google Sheets via ADC"
  echo

  # Falha segura: se o conector falhar, parar sem fallback silencioso para CSV.
  # Fallback para CSV mascararia erros de planilha ou de autenticação.
  if ! "$_VENV_PYTHON" "$_SHEETS_SCRIPT" --write; then
    echo
    echo "ERRO: falha ao ler Google Sheets via ADC."
    echo "  Corrija a planilha ou a autenticação e tente novamente."
    echo "  Fallback para CSV não é realizado quando Google Sheets está configurado."
    echo
    echo "  Diagnóstico seguro da aba:"
    echo "  $_VENV_PYTHON $_SHEETS_SCRIPT --show-structure"
    exit 1
  fi

  echo
  painel-soprolife/scripts/sync-dashboard-summary.sh

else
  echo "Google Sheets ADC não disponível. Usando fluxo CSV."
  echo

  if [ -f "$_CSV_PATH" ]; then
    echo "CSV encontrado."
    painel-soprolife/scripts/import-summary-csv.sh "$_CSV_PATH"
  else
    echo "CSV não encontrado."
    echo "Usando resumo-dashboard.json privado já existente, se disponível."
    painel-soprolife/scripts/sync-dashboard-summary.sh
  fi
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
