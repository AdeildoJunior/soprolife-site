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
_CRM_SCRIPT="painel-soprolife/scripts/read-crm-clinicas-adc.py"
_CSV_PATH="${SOPROLIFE_SUMMARY_CSV:-$HOME/.config/soprolife/painel/resumo-dashboard.csv}"

# Pré-requisitos do conector Marketing & SEO
_MARKETING_CONFIG="painel-soprolife/data-private/marketing-seo-config.local.json"
_MARKETING_SCRIPT="painel-soprolife/scripts/read-marketing-seo-adc.py"

# Python a usar para Marketing & SEO: venv se disponível, senão system python3
if [ -f "$_VENV_PYTHON" ]; then
  _MARKETING_PYTHON="$_VENV_PYTHON"
else
  _MARKETING_PYTHON="python3"
fi

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

echo "1/7 - Atualizando status seguro da fonte Google Sheets..."
painel-soprolife/scripts/generate-runtime-status.sh

echo
echo "2/7 - Atualizando resumo seguro..."

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
echo "3/7 - Atualizando CRM Clínicas seguro..."

if [ "$_sheets_available" = true ] && [ -f "$_CRM_SCRIPT" ]; then
  echo "Fonte: Google Sheets via ADC (aba CRM Clinicas)"
  echo

  if ! "$_VENV_PYTHON" "$_CRM_SCRIPT" --write; then
    echo
    echo "AVISO: falha ao ler aba CRM Clinicas. O painel usará os dados de exemplo."
    echo "  Diagnóstico: $_VENV_PYTHON $_CRM_SCRIPT --show-structure"
  else
    echo
    painel-soprolife/scripts/sync-crm-clinicas.sh
  fi
else
  echo "Google Sheets ADC não disponível ou script ausente."
  echo "CRM Clínicas usará dados de exemplo (crm-clinicas.json)."
fi

echo
echo "4/7 - Atualizando Marketing & SEO..."

if [ ! -f "$_MARKETING_CONFIG" ]; then
  echo "Marketing & SEO não configurado — usando dados demonstrativos."
  echo "(Crie $_MARKETING_CONFIG a partir de"
  echo " painel-soprolife/config-examples/marketing-seo.local.example.json)"
elif [ ! -f "$_MARKETING_SCRIPT" ]; then
  echo "AVISO: script de Marketing & SEO não encontrado ($MARKETING_SCRIPT)."
elif [ ! -f "$_ADC_CONFIG" ]; then
  echo "AVISO: ADC não configurado (gcloud). Marketing & SEO pulado."
  echo "  Execute: gcloud auth application-default login"
else
  echo "Fonte: Google Search Console + GA4 via ADC"
  echo
  if ! "$_MARKETING_PYTHON" "$_MARKETING_SCRIPT" --write 2>&1; then
    echo
    echo "AVISO: Marketing & SEO atualização falhou — usando dados demonstrativos."
    echo "  Diagnóstico: $_MARKETING_PYTHON $_MARKETING_SCRIPT --dry-run"
  else
    echo "Marketing & SEO atualizado com dados reais/agregados."
  fi
fi

echo
echo "5/7 - Atualizando Leads..."

_LEADS_SCRIPT="painel-soprolife/scripts/read-leads-sheets.py"

if [ -f "$_LEADS_SCRIPT" ] && [ "$_sheets_available" = true ]; then
  echo "Fonte: Google Sheets via ADC (aba Leads)"
  echo "Saída: data-private/leads.local.json + data/leads-summary.local.json"
  echo
  if ! "$_VENV_PYTHON" "$_LEADS_SCRIPT" --write 2>&1; then
    echo
    echo "AVISO: falha ao ler aba Leads. Painel usará leads.json demonstrativo."
    echo "  Diagnóstico: $_VENV_PYTHON $_LEADS_SCRIPT --show-structure"
    echo "  Dry-run:     $_VENV_PYTHON $_LEADS_SCRIPT --dry-run"
  else
    echo "Leads atualizados."
    echo "  Privado:  painel-soprolife/data-private/leads.local.json"
    echo "  Resumo:   painel-soprolife/data/leads-summary.local.json"
  fi
else
  if [ ! -f "$_LEADS_SCRIPT" ]; then
    echo "Script de Leads não encontrado — painel usa leads.json demonstrativo."
  else
    echo "Google Sheets ADC não disponível — painel usa leads.json demonstrativo."
  fi
fi

echo
echo "6/7 - Verificando segurança..."
painel-soprolife/scripts/check-access.sh

echo
echo "7/7 - Concluído."
echo
echo "Para abrir localmente:"
echo "painel-soprolife/scripts/start-local.sh"
echo
echo "Para abrir via Tailscale:"
echo "painel-soprolife/scripts/start-tailscale.sh"
