#!/usr/bin/env bash
# update-local-data.sh — Atualização segura dos dados do Painel SoproLife.
#
# Garante que falhas em qualquer módulo isolado (Sheets, CSV, resumo, leads,
# marketing, follow-up, custos) não interrompam os demais.
# Retorna 0 se todas as falhas forem de aviso controlado.
# Retorna 1 somente se check-access.sh detectar problema de segurança crítico.

set -uo pipefail
# Nota: set -e REMOVIDO intencionalmente — cada etapa usa if/|| para controle
# granular de erros, evitando que uma falha isolada interrompa o script inteiro.

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

_security_fail=false

# ---------------------------------------------------------------------------

echo "Atualizando dados locais seguros do Painel SoproLife..."
echo

echo "1/10 - Atualizando status seguro da fonte Google Sheets..."
if ! painel-soprolife/scripts/generate-runtime-status.sh; then
  echo "AVISO: generate-runtime-status.sh falhou — painel usará status anterior."
  echo "  (os demais passos continuarão normalmente)"
fi

echo
echo "2/10 - Atualizando resumo seguro..."

if [ "$_sheets_available" = true ]; then
  echo "Fonte: Google Sheets via ADC"
  echo

  if ! "$_VENV_PYTHON" "$_SHEETS_SCRIPT" --write; then
    echo
    echo "AVISO: falha ao ler resumo do Google Sheets — painel pode mostrar dados desatualizados."
    echo "  Diagnóstico seguro da aba:"
    echo "  $_VENV_PYTHON $_SHEETS_SCRIPT --show-structure"
    echo "  (os demais passos continuarão normalmente)"
  fi

  echo
  if ! painel-soprolife/scripts/sync-dashboard-summary.sh; then
    echo
    echo "AVISO: sync-dashboard-summary.sh falhou — painel usará dados anteriores do resumo."
    echo "  (os demais passos continuarão normalmente)"
  fi

else
  echo "AVISO: Google Sheets ADC não disponível neste ambiente."
  echo "  Motivos possíveis:"
  [ ! -f "$_ADC_CONFIG" ]    && echo "    - ADC não configurado: $_ADC_CONFIG"
  [ ! -f "$_SHEETS_CONFIG" ] && echo "    - Config de planilha ausente: $_SHEETS_CONFIG"
  [ ! -f "$_VENV_PYTHON" ]   && echo "    - Python venv ausente: $_VENV_PYTHON"
  echo "  Execute painel-soprolife/scripts/check-vps-google-adc.sh para diagnóstico."
  echo

  if [ -f "$_CSV_PATH" ]; then
    echo "CSV encontrado — importando..."
    if ! painel-soprolife/scripts/import-summary-csv.sh "$_CSV_PATH"; then
      echo "AVISO: import CSV falhou — continuando com dados anteriores."
    fi
  else
    echo "CSV não encontrado. Tentando usar resumo privado já existente..."
    if ! painel-soprolife/scripts/sync-dashboard-summary.sh; then
      echo "AVISO: resumo privado não disponível — painel usará dados demonstrativos."
      echo "  (os demais passos continuarão normalmente)"
    fi
  fi
fi

echo
echo "3/10 - Atualizando CRM Clínicas seguro..."

if [ "$_sheets_available" = true ] && [ -f "$_CRM_SCRIPT" ]; then
  echo "Fonte: Google Sheets via ADC (aba CRM Clinicas)"
  echo

  if ! "$_VENV_PYTHON" "$_CRM_SCRIPT" --write; then
    echo
    echo "AVISO: falha ao ler aba CRM Clinicas. O painel usará os dados de exemplo."
    echo "  Diagnóstico: $_VENV_PYTHON $_CRM_SCRIPT --show-structure"
  else
    echo
    if ! painel-soprolife/scripts/sync-crm-clinicas.sh; then
      echo "AVISO: sync-crm-clinicas.sh falhou."
    fi
  fi
else
  echo "Google Sheets ADC não disponível ou script ausente."
  echo "CRM Clínicas usará dados de exemplo (crm-clinicas.json)."
fi

echo
echo "4/10 - Atualizando follow-up B2B de clínicas..."

_FOLLOWUP_CLINICAS_SCRIPT="painel-soprolife/scripts/generate-followup-clinicas.py"

if [ -f "$_FOLLOWUP_CLINICAS_SCRIPT" ] && [ "$_sheets_available" = true ]; then
  echo "Fonte: Google Sheets via ADC (CRM Clinicas + Base Prospecção B2B PCMSO)"
  echo
  if ! python3 "$_FOLLOWUP_CLINICAS_SCRIPT" --write 2>&1; then
    echo
    echo "AVISO: falha ao gerar follow-up B2B de clínicas."
    echo "  Diagnóstico: python3 $_FOLLOWUP_CLINICAS_SCRIPT --dry-run"
    echo "  (os demais passos continuarão normalmente)"
  else
    echo "Follow-up B2B de clínicas atualizado."
    echo "  Privado:  painel-soprolife/data-private/followup-clinicas.local.json"
    echo "  Resumo:   painel-soprolife/data/followup-clinicas-summary.local.json"
  fi
else
  if [ ! -f "$_FOLLOWUP_CLINICAS_SCRIPT" ]; then
    echo "Script de follow-up B2B não encontrado."
  else
    echo "Google Sheets ADC não disponível — follow-up B2B de clínicas pulado."
    echo "  Execute manualmente se precisar atualizar:"
    echo "  python3 $_FOLLOWUP_CLINICAS_SCRIPT --write"
  fi
fi

echo
echo "5/10 - Atualizando Marketing & SEO..."

if [ ! -f "$_MARKETING_CONFIG" ]; then
  echo "Marketing & SEO não configurado — usando dados demonstrativos."
  echo "(Crie $_MARKETING_CONFIG a partir de"
  echo " painel-soprolife/config-examples/marketing-seo.local.example.json)"
elif [ ! -f "$_MARKETING_SCRIPT" ]; then
  echo "AVISO: script de Marketing & SEO não encontrado ($_MARKETING_SCRIPT)."
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
echo "6/10 - Atualizando Leads..."

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
echo "7/10 - Atualizando follow-up de pacientes..."

_FOLLOWUP_SCRIPT="painel-soprolife/scripts/generate-followup-pacientes.py"

if [ -f "$_FOLLOWUP_SCRIPT" ] && [ "$_sheets_available" = true ]; then
  echo "Fonte: Google Sheets via ADC (CRM Espirometria + CRM Consultas)"
  echo
  if ! "$_VENV_PYTHON" "$_FOLLOWUP_SCRIPT" --write 2>&1; then
    echo
    echo "AVISO: falha ao gerar follow-up de pacientes."
    echo "  Diagnóstico: $_VENV_PYTHON $_FOLLOWUP_SCRIPT --dry-run"
  else
    echo "Follow-up de pacientes atualizado."
    echo "  Privado:  painel-soprolife/data-private/followup-pacientes.local.json"
    echo "  Resumo:   painel-soprolife/data/followup-pacientes-summary.local.json"
  fi
else
  if [ ! -f "$_FOLLOWUP_SCRIPT" ]; then
    echo "Script de follow-up não encontrado."
  else
    echo "Google Sheets ADC não disponível — follow-up de pacientes pulado."
  fi
fi

echo
echo "8/10 - Atualizando timeline de últimos lançamentos..."

_LANCAMENTOS_SCRIPT="painel-soprolife/scripts/generate-ultimos-lancamentos.py"

if [ -f "$_LANCAMENTOS_SCRIPT" ]; then
  if ! python3 "$_LANCAMENTOS_SCRIPT" --write 2>&1; then
    echo
    echo "AVISO: falha ao gerar timeline de últimos lançamentos."
    echo "  Diagnóstico: python3 $_LANCAMENTOS_SCRIPT --dry-run"
  else
    echo "Timeline de lançamentos atualizada."
    echo "  Resumo: painel-soprolife/data/ultimos-lancamentos-summary.local.json"
  fi
else
  echo "Script não encontrado ($_LANCAMENTOS_SCRIPT)."
fi

echo
echo "9/10 - Verificando segurança..."
if ! painel-soprolife/scripts/check-access.sh; then
  echo
  echo "AVISO: check-access.sh reportou problema(s) de segurança."
  echo "  Verifique os ERROs acima antes de compartilhar o painel."
  _security_fail=true
fi

echo
echo "10/10 - Concluído."
echo

if [ "$_security_fail" = true ]; then
  echo "ATENÇÃO: problemas de segurança detectados — veja saída do passo 9."
fi

echo "Para abrir localmente:"
echo "painel-soprolife/scripts/start-local.sh"
echo
echo "Para abrir via Tailscale:"
echo "painel-soprolife/scripts/start-tailscale.sh"

# Retorna 1 somente se check-access detectou problema crítico de segurança
if [ "$_security_fail" = true ]; then
  exit 1
fi
exit 0
