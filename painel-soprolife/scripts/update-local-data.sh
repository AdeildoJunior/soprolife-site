#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

# ---------------------------------------------------------------------------
# Exclusão mútua (M21): o timer dispara a cada 10 minutos e uma execução lenta
# não pode se sobrepor à seguinte — duas escritas concorrentes nos mesmos
# snapshots é o caminho mais curto para corromper dados válidos.
# Reexecuta o script uma única vez sob flock; sem o lock, sai em silêncio
# (exit 0) porque "já está atualizando" não é falha.
# ---------------------------------------------------------------------------
_LOCK_FILE="${SOPROLIFE_UPDATE_LOCK:-${TMPDIR:-/tmp}/soprolife-update-local-data.lock}"
if [ -z "${SOPROLIFE_UPDATE_LOCKED:-}" ]; then
  if command -v flock >/dev/null 2>&1; then
    export SOPROLIFE_UPDATE_LOCKED=1
    # -E 99: código exclusivo para "lock ocupado", para não confundir com um
    # exit 1 legítimo do próprio script.
    _rc=0
    flock -n -E 99 "$_LOCK_FILE" "$0" "$@" || _rc=$?
    if [ "$_rc" -eq 99 ]; then
      echo "INFO: outra atualização já está em execução — nada a fazer."
      exit 0
    fi
    exit "$_rc"
  else
    echo "ERRO: flock indisponível — atualização cancelada para evitar concorrência."
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# Pré-requisitos do conector Google Sheets via ADC
# ---------------------------------------------------------------------------
_VENV_PYTHON="$HOME/.local/share/soprolife/venvs/google-sheets/bin/python"
_SHEETS_CONFIG="$HOME/.config/soprolife/painel/google-sheets.local.json"
_ADC_CONFIG="$HOME/.config/gcloud/application_default_credentials.json"
_SHEETS_SCRIPT="painel-soprolife/scripts/read-sheets-summary-adc.py"
_CRM_SCRIPT="painel-soprolife/scripts/read-crm-clinicas-adc.py"
_PARCERIA_PASTORE_SCRIPT="painel-soprolife/scripts/read-parcerias-pastore-adc.py"
_CSV_PATH="${SOPROLIFE_SUMMARY_CSV:-$HOME/.config/soprolife/painel/resumo-dashboard.csv}"

# Pré-requisitos do conector Marketing & SEO
_MARKETING_CONFIG="painel-soprolife/data-private/marketing-seo-config.local.json"
_MARKETING_SCRIPT="painel-soprolife/scripts/read-marketing-seo-adc.py"
# M21 — credencial DURÁVEL de leitura (conta de serviço dedicada). Fora do Git,
# fora do repositório, nunca enviada ao navegador. Quando existe, é ela que
# vale; o ADC pessoal deixa de ser dependência da atualização automática.
_MARKETING_CREDENTIAL="${SOPROLIFE_MARKETING_CREDENTIALS:-/opt/soprolife/secrets/marketing-readonly.json}"

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

# M21 — detecta o pedido manual enfileirado pela tela de Marketing. A marca
# permanece enquanto a consulta está rodando e só é consumida DEPOIS do passo
# de Marketing; assim a UI não anuncia conclusão antes do snapshot terminar.
_MKT_QUEUE="${SOPROLIFE_MARKETING_REFRESH_QUEUE:-painel-soprolife/nucleo-m15/var/marketing-refresh-request.json}"
_MKT_REQUESTED=0
_MKT_ATTEMPTED=0
if [ -f "$_MKT_QUEUE" ]; then
  echo "Pedido manual de atualização encontrado na fila."
  _MKT_REQUESTED=1
  echo
fi

echo "1/15 - Atualizando status seguro da fonte Google Sheets..."
painel-soprolife/scripts/generate-runtime-status.sh

echo
echo "2/15 - Atualizando resumo seguro..."

if [ "$_sheets_available" = true ]; then
  echo "Fonte: Google Sheets via ADC"
  echo

  if ! "$_VENV_PYTHON" "$_SHEETS_SCRIPT" --write; then
    echo
    echo "AVISO: falha ao ler resumo do Google Sheets — painel pode mostrar dados desatualizados."
    echo "  Diagnóstico: $_VENV_PYTHON $_SHEETS_SCRIPT --show-structure"
    echo "  (os demais passos continuarão normalmente)"
  else
    echo
    painel-soprolife/scripts/sync-dashboard-summary.sh || {
      echo
      echo "AVISO: sync-dashboard-summary.sh falhou — painel usará resumo anterior, se disponível."
      echo "  (os demais passos continuarão normalmente)"
    }
  fi

else
  echo "Google Sheets ADC não disponível. Usando fluxo CSV."
  echo

  if [ -f "$_CSV_PATH" ]; then
    echo "CSV encontrado."
    painel-soprolife/scripts/import-summary-csv.sh "$_CSV_PATH" || {
      echo
      echo "AVISO: import-summary-csv.sh falhou — continuando."
    }
  else
    echo "CSV não encontrado."
    echo "Usando resumo-dashboard.json privado já existente, se disponível."
    painel-soprolife/scripts/sync-dashboard-summary.sh || {
      echo
      echo "AVISO: sync-dashboard-summary.sh falhou — continuando."
    }
  fi
fi

echo
echo "3/15 - Atualizando CRM Clínicas seguro..."

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
echo "4/15 - Atualizando follow-up B2B de clínicas..."

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
    echo "Script de follow-up B2B não encontrado ($FOLLOWUP_CLINICAS_SCRIPT)."
  else
    echo "Google Sheets ADC não disponível — follow-up B2B de clínicas pulado."
    echo "  Execute manualmente se precisar atualizar:"
    echo "  python3 $_FOLLOWUP_CLINICAS_SCRIPT --write"
  fi
fi

echo
echo "5/15 - Atualizando Marketing & SEO..."

if [ ! -f "$_MARKETING_CONFIG" ]; then
  echo "Marketing & SEO não configurado — usando dados demonstrativos."
  echo "(Crie $_MARKETING_CONFIG a partir de"
  echo " painel-soprolife/config-examples/marketing-seo.local.example.json)"
elif [ ! -f "$_MARKETING_SCRIPT" ]; then
  echo "AVISO: script de Marketing & SEO não encontrado ($_MARKETING_SCRIPT)."
elif [ ! -f "$_MARKETING_CREDENTIAL" ] && [ ! -f "$_ADC_CONFIG" ] && \
     [ "${SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT:-}" != "1" ]; then
  # Nem credencial durável, nem ADC de desenvolvimento: nada a tentar.
  echo "AVISO: nenhuma credencial de leitura disponível. Marketing & SEO pulado."
  echo "  Produção: conta de serviço de leitura em /opt/soprolife/secrets/."
  echo "  Diagnóstico: $_MARKETING_PYTHON $_MARKETING_SCRIPT --credential-check"
else
  # M21 — a credencial durável (conta de serviço, somente leitura) tem
  # precedência sobre o ADC pessoal, que vence e derrubava a atualização
  # automática. O script decide e informa o TIPO de credencial usado.
  if [ -f "$_MARKETING_CREDENTIAL" ]; then
    echo "Fonte: Google Search Console + GA4 (conta de serviço, somente leitura)"
    export SOPROLIFE_MARKETING_CREDENTIALS="$_MARKETING_CREDENTIAL"
    export SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT=1
  elif [ "${SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT:-}" = "1" ]; then
    # Produção sem a chave ainda precisa EXECUTAR o conector: ele grava o
    # estado credential_pending e preserva o último snapshot válido. Pular
    # aqui deixaria para sempre a mensagem antiga de ADC pessoal expirado.
    echo "Fonte: conta de serviço obrigatória (configuração pendente)"
    export SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT=1
  else
    echo "Fonte: Google Search Console + GA4 via ADC de desenvolvimento"
  fi
  echo
  _MKT_ATTEMPTED=1
  if ! "$_MARKETING_PYTHON" "$_MARKETING_SCRIPT" --write 2>&1; then
    echo
    echo "AVISO: Marketing & SEO atualização falhou — último snapshot válido preservado."
    echo "  Diagnóstico: $_MARKETING_PYTHON $_MARKETING_SCRIPT --credential-check"
  else
    echo "Marketing & SEO atualizado com dados reais/agregados."
  fi
fi

if [ "$_MKT_REQUESTED" -eq 1 ] && [ "$_MKT_ATTEMPTED" -eq 1 ]; then
  # A tentativa terminou (com sucesso ou preservando o último snapshot).
  # Remover só esta marca mínima não toca em nenhum dado válido.
  rm -f -- "$_MKT_QUEUE"
  echo "Pedido manual de Marketing consumido após a tentativa."
elif [ "$_MKT_REQUESTED" -eq 1 ]; then
  echo "Pedido manual de Marketing mantido na fila: conector não chegou a executar."
fi

echo
echo "6/15 - Atualizando Leads..."

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
echo "7/15 - Atualizando CRM Contatos B2B..."

_CONTATOS_B2B_SCRIPT="painel-soprolife/scripts/read-crm-contatos-b2b-adc.py"

if [ -f "$_CONTATOS_B2B_SCRIPT" ] && [ "$_sheets_available" = true ]; then
  echo "Fonte: Google Sheets via ADC (aba CRM Contatos B2B)"
  echo "Saída: data-private/crm-contatos-b2b.local.json + data/crm-contatos-b2b-summary.local.json"
  echo
  if ! "$_VENV_PYTHON" "$_CONTATOS_B2B_SCRIPT" --write 2>&1; then
    echo
    echo "AVISO: falha ao ler aba CRM Contatos B2B."
    echo "  Isso é esperado se a aba ainda não foi criada (só existe após o primeiro"
    echo "  vínculo via vincularLeadAClinica no painel)."
    echo "  Diagnóstico: $_VENV_PYTHON $_CONTATOS_B2B_SCRIPT --show-structure"
  else
    echo "CRM Contatos B2B atualizado."
    echo "  Privado:  painel-soprolife/data-private/crm-contatos-b2b.local.json"
    echo "  Resumo:   painel-soprolife/data/crm-contatos-b2b-summary.local.json"
  fi
else
  if [ ! -f "$_CONTATOS_B2B_SCRIPT" ]; then
    echo "Script de CRM Contatos B2B não encontrado — painel não mostrará contatos vinculados."
  else
    echo "Google Sheets ADC não disponível — painel não mostrará contatos vinculados."
  fi
fi

echo
echo "8/15 - Atualizando follow-up de pacientes..."

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
echo "9/15 - Atualizando Financeiro (fonte única: Financeiro_Lancamentos)..."

_FINANCEIRO_SCRIPT="painel-soprolife/scripts/read-financeiro-lancamentos-adc.py"

if [ -f "$_FINANCEIRO_SCRIPT" ] && [ "$_sheets_available" = true ]; then
  echo "Fonte: Google Sheets via ADC (aba Financeiro_Lancamentos)"
  echo
  if ! "$_VENV_PYTHON" "$_FINANCEIRO_SCRIPT" --write 2>&1; then
    echo
    echo "AVISO: falha ao ler a aba Financeiro_Lancamentos."
    echo "  Isso é esperado se a aba ainda não existe (é criada pelo Apps Script"
    echo "  na primeira gravação da Nova Espirometria)."
    echo "  Diagnóstico: $_VENV_PYTHON $_FINANCEIRO_SCRIPT --show-structure"
  else
    echo "Financeiro atualizado."
    echo "  Privado:  painel-soprolife/data-private/financeiro-lancamentos.local.json"
    echo "  Resumo:   painel-soprolife/data/financeiro-summary.local.json"
  fi
else
  if [ ! -f "$_FINANCEIRO_SCRIPT" ]; then
    echo "Script do Financeiro não encontrado — resumo financeiro não será atualizado."
  else
    echo "Google Sheets ADC não disponível — resumo financeiro não será atualizado."
  fi
fi

echo
echo "10/15 - Atualizando timeline de últimos lançamentos..."

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
echo "11/15 - Atualizando Parceria Pastore..."

if [ -f "$_PARCERIA_PASTORE_SCRIPT" ] && [ "$_sheets_available" = true ]; then
  echo "Fonte: Google Sheets via ADC (Atendimentos + Custos + Config)"
  echo
  if ! "$_VENV_PYTHON" "$_PARCERIA_PASTORE_SCRIPT" --write 2>&1; then
    echo
    echo "AVISO: falha ao ler as abas da Parceria Pastore."
    echo "  Painel usará o fallback commitável (data/parcerias-pastore-summary.json)."
    echo "  Diagnóstico: $_VENV_PYTHON $_PARCERIA_PASTORE_SCRIPT --show-structure"
    echo "  (os demais passos continuarão normalmente)"
  else
    echo "Parceria Pastore atualizada."
    echo "  Privado:  painel-soprolife/data-private/parcerias-pastore.local.json"
    echo "  Resumo:   painel-soprolife/data/parcerias-pastore-summary.local.json"
  fi
else
  if [ ! -f "$_PARCERIA_PASTORE_SCRIPT" ]; then
    echo "Script não encontrado — painel usará o fallback commitável."
  else
    echo "Google Sheets ADC não disponível — painel usará o fallback commitável"
    echo "(data/parcerias-pastore-summary.json)."
  fi
fi

echo
echo "12/15 - Atualizando resumo de auditoria (Log Auditoria)..."

_AUDITORIA_SCRIPT="painel-soprolife/scripts/read-auditoria-adc.py"

if [ -f "$_AUDITORIA_SCRIPT" ] && [ "$_sheets_available" = true ]; then
  echo "Fonte: Google Sheets via ADC (aba Log Auditoria)"
  echo
  if ! "$_VENV_PYTHON" "$_AUDITORIA_SCRIPT" --write 2>&1; then
    echo
    echo "AVISO: falha ao ler a aba Log Auditoria."
    echo "  Isso é esperado se a aba ainda não existe (só é criada após a"
    echo "  primeira escrita auditada via painel — M1 Etapas 1-4 publicadas)."
    echo "  Diagnóstico: $_VENV_PYTHON $_AUDITORIA_SCRIPT --show-structure"
  else
    echo "Resumo de auditoria atualizado."
    echo "  Resumo: painel-soprolife/data/auditoria-summary.local.json"
  fi
else
  if [ ! -f "$_AUDITORIA_SCRIPT" ]; then
    echo "Script de auditoria não encontrado — resumo de auditoria pulado."
  else
    echo "Google Sheets ADC não disponível — resumo de auditoria pulado."
  fi
fi

echo
echo "13/15 - Verificando segurança..."
# Captura o exit sem abortar: o resultado alimenta a Saúde Operacional e a
# falha continua derrubando o update ao final (mesma semântica de antes).
_CHECK_ACCESS_EXIT=0
painel-soprolife/scripts/check-access.sh || _CHECK_ACCESS_EXIT=$?

echo
echo "14/15 - Gerando Saúde Operacional (M3)..."

_SAUDE_SCRIPT="painel-soprolife/scripts/generate-saude-operacional.py"

if [ -f "$_SAUDE_SCRIPT" ]; then
  if ! python3 "$_SAUDE_SCRIPT" --write --check-access-exit "$_CHECK_ACCESS_EXIT" 2>&1; then
    echo
    echo "AVISO: falha ao gerar Saúde Operacional — painel usará o demo."
    echo "  Diagnóstico: python3 $_SAUDE_SCRIPT --dry-run"
  else
    echo "Saúde Operacional atualizada."
    echo "  Resumo: painel-soprolife/data/saude-operacional-summary.local.json"
  fi
else
  echo "Script de Saúde Operacional não encontrado — painel usará o demo."
fi

if [ "$_CHECK_ACCESS_EXIT" -ne 0 ]; then
  echo
  echo "ERRO: check-access.sh falhou (exit $_CHECK_ACCESS_EXIT) — ver saída acima."
  exit "$_CHECK_ACCESS_EXIT"
fi

echo
echo "15/15 - Concluído."
echo
echo "Para abrir localmente:"
echo "painel-soprolife/scripts/start-local.sh"
echo
echo "Para abrir via Tailscale:"
echo "painel-soprolife/scripts/start-tailscale.sh"
