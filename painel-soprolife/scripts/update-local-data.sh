#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

# ===========================================================================
# M23 — ESTEIRA DE ATUALIZAÇÃO EM MODO POSTGRESQL-ONLY
#
# Decisão arquitetural permanente: PostgreSQL/API do Núcleo M15 é a ÚNICA
# fonte operacional do Command Center. Esta esteira NÃO executa mais nenhum
# leitor de Google Sheets, verificador de ADC pessoal, conector de Apps Script
# nem fallback derivado de planilha.
#
# O que ela faz:
#   1. status de runtime (declara a fonte canônica: PostgreSQL);
#   2. snapshots do painel gerados a partir do PostgreSQL;
#   3. Marketing & SEO (Search Console + GA4) pela conta de serviço dedicada
#      — integração deliberadamente PRESERVADA pelo M23;
#   4. timeline de lançamentos (deriva dos snapshots do passo 2);
#   5. check de segurança;
#   6. Saúde Operacional.
#
# Honestidade de falha: se o PostgreSQL/API não responder, os snapshots NÃO
# são regravados, o passo falha visivelmente e o painel continua exibindo o
# último snapshot válido com o frescor real. Em NENHUMA hipótese um dado
# operacional é substituído por exemplo, fixture, demo ou export de planilha.
#
# Os utilitários legados de Sheets continuam no repositório apenas para
# migração/forense manual e são bloqueados fail-closed por
# scripts/data_source_mode.py — o timer não consegue executá-los.
# ===========================================================================

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
# Fonte canônica: PostgreSQL via CLI do Núcleo M15
# ---------------------------------------------------------------------------
_NUCLEO_DIR="painel-soprolife/nucleo-m15"
_DATA_DIR="painel-soprolife/data"
# Python do núcleo (venv de produção) com fallback para o interpretador do
# sistema em ambiente de desenvolvimento.
_M15_PYTHON="${SOPROLIFE_M15_PYTHON:-/opt/soprolife/venvs/m15/bin/python}"
if [ ! -x "$_M15_PYTHON" ]; then
  _M15_PYTHON="python3"
fi

# ---------------------------------------------------------------------------
# Marketing & SEO — Search Console + GA4. EXPLICITAMENTE fora do
# descomissionamento do M23: é leitura de marketing por conta de serviço
# dedicada, não é dado de negócio e não depende de ADC pessoal.
# ---------------------------------------------------------------------------
_MARKETING_CONFIG="painel-soprolife/data-private/marketing-seo-config.local.json"
_MARKETING_SCRIPT="painel-soprolife/scripts/read-marketing-seo-adc.py"
_MARKETING_CREDENTIAL="${SOPROLIFE_MARKETING_CREDENTIALS:-/opt/soprolife/secrets/marketing-readonly.json}"
_MARKETING_PYTHON="python3"

_FALHAS=0

echo "Atualizando dados do Painel SoproLife (modo postgresql-only, M23)..."
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

echo "1/6 - Registrando a fonte canônica de dados..."
painel-soprolife/scripts/generate-runtime-status.sh

echo
echo "2/6 - Gerando snapshots do painel a partir do PostgreSQL..."
echo "Fonte: PostgreSQL (Núcleo M15) — única fonte operacional."
echo

# O guarda de modo confirma que nenhum leitor de planilha pode entrar aqui.
if ! python3 painel-soprolife/scripts/data_source_mode.py --check; then
  echo
  echo "ERRO: modo de fonte de dados não é postgresql_only — atualização abortada."
  echo "  A esteira automática nunca roda com o escape manual de migração ligado."
  exit 1
fi
echo

if [ ! -d "$_NUCLEO_DIR" ]; then
  echo "ERRO: núcleo M15 não encontrado em $_NUCLEO_DIR."
  echo "  Sem a fonte canônica não há o que atualizar. Nenhum dado de exemplo"
  echo "  será usado no lugar do dado operacional."
  _FALHAS=$((_FALHAS + 1))
elif ( cd "$_NUCLEO_DIR" && "$_M15_PYTHON" -m app.cli exportar-snapshots --saida ../data --write ); then
  echo "Snapshots atualizados a partir do banco."
  echo "  Diretório: $_DATA_DIR"
else
  echo
  echo "ERRO: falha ao gerar snapshots a partir do PostgreSQL."
  echo "  O painel continuará exibindo o ÚLTIMO SNAPSHOT VÁLIDO, com o frescor"
  echo "  real visível na Saúde Operacional. Nenhum dado de exemplo substitui"
  echo "  dado operacional."
  echo "  Diagnóstico: cd $_NUCLEO_DIR && $_M15_PYTHON -m app.cli exportar-snapshots"
  _FALHAS=$((_FALHAS + 1))
fi

echo
echo "3/6 - Atualizando Marketing & SEO (Search Console + GA4)..."

if [ ! -f "$_MARKETING_CONFIG" ]; then
  echo "Marketing & SEO não configurado neste ambiente."
  echo "(Crie $_MARKETING_CONFIG a partir de"
  echo " painel-soprolife/config-examples/marketing-seo.local.example.json)"
elif [ ! -f "$_MARKETING_SCRIPT" ]; then
  echo "AVISO: conector de Marketing & SEO não encontrado ($_MARKETING_SCRIPT)."
  _FALHAS=$((_FALHAS + 1))
else
  # A credencial durável (conta de serviço, somente leitura) é a única aceita
  # em produção. Sem ela o conector grava o estado credential_pending e
  # preserva o último snapshot válido — nunca cai para ADC pessoal.
  if [ -f "$_MARKETING_CREDENTIAL" ]; then
    echo "Fonte: Google Search Console + GA4 (conta de serviço, somente leitura)"
    export SOPROLIFE_MARKETING_CREDENTIALS="$_MARKETING_CREDENTIAL"
    export SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT=1
  elif [ "${SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT:-}" = "1" ]; then
    echo "Fonte: conta de serviço obrigatória (configuração pendente)"
  else
    echo "Fonte: credencial de desenvolvimento local"
  fi
  echo
  _MKT_ATTEMPTED=1
  if ! "$_MARKETING_PYTHON" "$_MARKETING_SCRIPT" --write 2>&1; then
    echo
    echo "AVISO: Marketing & SEO falhou — último snapshot válido preservado."
    echo "  Diagnóstico: $_MARKETING_PYTHON $_MARKETING_SCRIPT --credential-check"
    echo "  (não afeta os dados operacionais, que vêm do PostgreSQL)"
  else
    echo "Marketing & SEO atualizado."
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
echo "4/6 - Atualizando timeline de últimos lançamentos..."

_LANCAMENTOS_SCRIPT="painel-soprolife/scripts/generate-ultimos-lancamentos.py"

if [ -f "$_LANCAMENTOS_SCRIPT" ]; then
  if ! python3 "$_LANCAMENTOS_SCRIPT" --write 2>&1; then
    echo
    echo "AVISO: falha ao gerar timeline de últimos lançamentos."
    echo "  Diagnóstico: python3 $_LANCAMENTOS_SCRIPT --dry-run"
  else
    echo "Timeline de lançamentos atualizada."
  fi
else
  echo "AVISO: script não encontrado ($_LANCAMENTOS_SCRIPT)."
fi

echo
echo "5/6 - Verificando segurança..."
# Captura o exit sem abortar: o resultado alimenta a Saúde Operacional e a
# falha continua derrubando o update ao final (mesma semântica de antes).
_CHECK_ACCESS_EXIT=0
painel-soprolife/scripts/check-access.sh || _CHECK_ACCESS_EXIT=$?

echo
echo "6/6 - Gerando Saúde Operacional..."

_SAUDE_SCRIPT="painel-soprolife/scripts/generate-saude-operacional.py"

if [ -f "$_SAUDE_SCRIPT" ]; then
  if ! python3 "$_SAUDE_SCRIPT" --write --check-access-exit "$_CHECK_ACCESS_EXIT" 2>&1; then
    echo
    echo "AVISO: falha ao gerar Saúde Operacional."
    echo "  Diagnóstico: python3 $_SAUDE_SCRIPT --dry-run"
  else
    echo "Saúde Operacional atualizada."
  fi
else
  echo "AVISO: script de Saúde Operacional não encontrado."
fi

if [ "$_CHECK_ACCESS_EXIT" -ne 0 ]; then
  echo
  echo "ERRO: check-access.sh falhou (exit $_CHECK_ACCESS_EXIT) — ver saída acima."
  exit "$_CHECK_ACCESS_EXIT"
fi

if [ "$_FALHAS" -gt 0 ]; then
  echo
  echo "ERRO: $_FALHAS passo(s) da fonte canônica falharam — ver saída acima."
  echo "  O painel NÃO foi preenchido com dados de exemplo. O estado real fica"
  echo "  visível na Saúde Operacional."
  exit 1
fi

echo
echo "Concluído. Fonte operacional: PostgreSQL (Núcleo M15)."
echo
echo "Para abrir localmente:"
echo "painel-soprolife/scripts/start-local.sh"
echo
echo "Para abrir via Tailscale:"
echo "painel-soprolife/scripts/start-tailscale.sh"
