#!/usr/bin/env bash
set -euo pipefail

# ===========================================================================
# M23 — ENSAIO ISOLADO DA ESTEIRA DE PRODUÇÃO
#
# Roda update-local-data.sh DE VERDADE, com check-access.sh de verdade e o
# exportador de verdade, numa árvore temporária com banco SQLite sintético.
# Existe porque os dois incidentes do M23 só apareceram em produção: o
# primeiro (divergência exportador × contrato) e o segundo (identificador de
# registro parecendo telefone + interpretador errado no Marketing) passaram
# pelos testes unitários e caíram na VPS.
#
# NÃO usa credencial, dado ou banco de produção. Não fala com a VPS, com o
# Google nem com a rede. Tudo o que grava fica sob um diretório temporário.
#
# Uso:
#   painel-soprolife/scripts/test-m23-pipeline-rehearsal.sh
#
# Requer um interpretador com as dependências do Núcleo M15:
#   SOPROLIFE_M15_PYTHON=/caminho/para/python  (padrão: nucleo-m15/.venv)
#
# Exit: 0 = ensaio aprovado | 1 = alguma prova falhou.
# ===========================================================================

REPO_RAIZ="$(cd "$(dirname "$0")/../../" && pwd)"
FALHAS=0

prova() {
  local nome="$1" cond="$2"
  if [ "$cond" = "0" ]; then
    echo "  PASS: $nome"
  else
    echo "  FAIL: $nome"
    FALHAS=$((FALHAS + 1))
  fi
}

# ------------------------------------------------------------ interpretadores
_M15_PY="${SOPROLIFE_M15_PYTHON:-$REPO_RAIZ/painel-soprolife/nucleo-m15/.venv/bin/python}"
if [ ! -x "$_M15_PY" ]; then
  echo "ERRO: interpretador do Núcleo M15 não encontrado ($_M15_PY)."
  echo "      Defina SOPROLIFE_M15_PYTHON apontando para um venv com as"
  echo "      dependências de painel-soprolife/nucleo-m15/requirements.txt."
  exit 1
fi
if ! "$_M15_PY" -c "import sqlalchemy, pydantic_settings" 2>/dev/null; then
  echo "ERRO: $_M15_PY não tem as dependências do Núcleo M15."
  exit 1
fi

TMP_RAIZ="$(mktemp -d "${TMPDIR:-/tmp}/soprolife-m23-ensaio.XXXXXX")"
trap 'rm -rf -- "$TMP_RAIZ"' EXIT

echo "── Ensaio M23 — esteira isolada ──"
echo "  árvore temporária: $TMP_RAIZ"
echo "  interpretador M15: $_M15_PY"
echo

# ------------------------------------------------------------- árvore isolada
ARVORE="$TMP_RAIZ/repo"
PAINEL="$ARVORE/painel-soprolife"
mkdir -p "$PAINEL"
for sub in scripts core systemd nucleo-m15; do
  cp -r "$REPO_RAIZ/painel-soprolife/$sub" "$PAINEL/$sub"
done
find "$PAINEL" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$PAINEL/nucleo-m15/.venv" "$PAINEL/nucleo-m15/var"
mkdir -p "$PAINEL/data" "$PAINEL/data-private" "$PAINEL/nucleo-m15/var"
printf 'painel-soprolife/data/*.local.json\npainel-soprolife/data-private/\n' \
  > "$ARVORE/.gitignore"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
git -C "$ARVORE" init -q .
git -C "$ARVORE" add -A >/dev/null

# ------------------------------------------------- banco sintético (SQLite)
# Nenhum dado real: pessoas fictícias e uma trilha de auditoria com os
# formatos de entidade_id que derrubaram o 2º deploy — inclusive o UUID que
# casa com o detector de telefone.
export M15_ENV=dev
export M15_DATABASE_URL="sqlite:///$TMP_RAIZ/ensaio.db"
export M15_AUTH_SECRET="ensaio-local-sem-valor-de-producao-0123456789"

( cd "$PAINEL/nucleo-m15" && "$_M15_PY" - <<'PY'
from datetime import datetime, timedelta, timezone

from app.db import Base, get_engine, get_sessionmaker
from app.models import AuditLog, Person, Lead
from app.security import ensure_roles_exist

Base.metadata.create_all(get_engine())
db = get_sessionmaker()()
ensure_roles_exist(db)

db.add(Person(id="p-ensaio-1", public_code="PES-900001",
              nome_completo="Pessoa Ficticia Ensaio",
              nome_normalizado="pessoa ficticia ensaio"))
db.flush()
db.add(Lead(id="l-ensaio-1", public_code="LEA-900001", person_id="p-ensaio-1",
            origem="instagram", canal_entrada="direct",
            servico_interesse="espirometria", etapa="novo"))

agora = datetime.now(timezone.utc)
identificadores = [
    "3f688837-5450-491e-b949-623b90cf145f",  # UUID que casa com o detector
    "cd2d65e2-69c6-492f-9b96-417504270563",  # idem
    "0b9d5f2a-8c14-4e77-9a3b-1d6e0f4c8b25",
    "(21) 98877-6655",
    "123.456.789-09",
    "paciente.teste@exemplo.com.br",
    "PES-900001",
    None,
]
for i, ident in enumerate(identificadores):
    db.add(AuditLog(acao="lead.criado", entidade="leads", entidade_id=ident,
                    ts_utc=agora - timedelta(minutes=i),
                    detalhes={"codigo": "ENSAIO"}))
db.add(AuditLog(acao="auth.falha", ts_utc=agora - timedelta(minutes=30)))
db.commit()
print(f"  banco de ensaio populado: {db.query(AuditLog).count()} evento(s) de auditoria")
PY
) || { echo "ERRO: falha ao popular o banco de ensaio."; exit 1; }

# ------------------------------------------------------- Marketing isolado
# Config presente (o passo 3 executa) + interpretador SEM as bibliotecas do
# Google + credencial ausente. É o pior caso do Marketing, de propósito: a
# prova é que ele avisa e NÃO contamina o passo do PostgreSQL.
cat > "$PAINEL/data-private/marketing-seo-config.local.json" <<'JSON'
{ "site_url": "https://exemplo.invalido/", "ga4_property_id": "000000000" }
JSON

MKT_PY_DIR="$TMP_RAIZ/marketing-sem-deps"
"$_M15_PY" -m venv --without-pip "$MKT_PY_DIR" 2>/dev/null || python3 -m venv --without-pip "$MKT_PY_DIR"
MKT_PY="$MKT_PY_DIR/bin/python"
[ -x "$MKT_PY" ] || MKT_PY="$MKT_PY_DIR/bin/python3"

export SOPROLIFE_M15_PYTHON="$_M15_PY"
export SOPROLIFE_MARKETING_PYTHON="$MKT_PY"
export SOPROLIFE_MARKETING_CREDENTIALS="$TMP_RAIZ/credencial-inexistente.json"
export SOPROLIFE_UPDATE_LOCK="$TMP_RAIZ/ensaio.lock"
export SOPROLIFE_MARKETING_REFRESH_QUEUE="$PAINEL/nucleo-m15/var/marketing-refresh-request.json"
unset SOPROLIFE_UPDATE_LOCKED

# --------------------------------------------------------------- a execução
SAIDA="$TMP_RAIZ/esteira.log"
set +e
( cd "$ARVORE" && bash painel-soprolife/scripts/update-local-data.sh ) \
  > "$SAIDA" 2>&1
ESTEIRA_EXIT=$?
set -e

echo
echo "── Provas ──"
echo "  exit da esteira: $ESTEIRA_EXIT"

# 1. Todos os snapshots do PostgreSQL foram gravados.
FALTANDO=0
SNAPSHOTS="$("$_M15_PY" - <<PY
import sys
sys.path.insert(0, "$PAINEL/nucleo-m15")
from app.snapshots import SNAPSHOT_FILES
print(" ".join(SNAPSHOT_FILES))
PY
)"
for nome in $SNAPSHOTS; do
  [ -f "$PAINEL/data/$nome" ] || { echo "    ausente: $nome"; FALTANDO=1; }
done
prova "todos os snapshots do PostgreSQL foram gravados" "$FALTANDO"

# 2. auditoria-summary.local.json existe.
[ -f "$PAINEL/data/auditoria-summary.local.json" ]
prova "auditoria-summary.local.json gerado" "$?"

# 3. Nenhum identificador sensível no snapshot de auditoria.
VAZOU=0
for valor in "3f688837-5450-491e-b949-623b90cf145f" \
             "cd2d65e2-69c6-492f-9b96-417504270563" \
             "0b9d5f2a-8c14-4e77-9a3b-1d6e0f4c8b25" \
             "98877-6655" "123.456.789-09" "paciente.teste@exemplo.com.br"; do
  if grep -qF "$valor" "$PAINEL/data/auditoria-summary.local.json" 2>/dev/null; then
    echo "    vazou: $valor"
    VAZOU=1
  fi
done
prova "nenhum identificador sensível no snapshot de auditoria" "$VAZOU"

# 4. check-access.sh aprovou a auditoria (e nada nele reprovou).
grep -q "auditoria-summary seguro" "$SAIDA"
prova "check-access.sh aprovou o resumo de auditoria" "$?"
if grep -q "check-access.sh falhou" "$SAIDA"; then
  prova "check-access.sh terminou com exit 0" 1
else
  prova "check-access.sh terminou com exit 0" 0
fi
if grep -q "padrão de telefone no campo 'entidade_id'" "$SAIDA"; then
  prova "falha de produção (telefone em entidade_id) não recorre" 1
else
  prova "falha de produção (telefone em entidade_id) não recorre" 0
fi

# 5. Marketing usou o interpretador pretendido.
grep -qF "Interpretador: $MKT_PY" "$SAIDA"
prova "Marketing usou o interpretador declarado (não o do PATH)" "$?"

# 6. Falha do Marketing ficou isolada.
grep -q "AVISO: Marketing & SEO falhou" "$SAIDA"
prova "falha do Marketing virou AVISO, não erro da esteira" "$?"
grep -q "não afeta os dados operacionais" "$SAIDA"
prova "esteira declara que o Marketing não afeta o dado operacional" "$?"
if grep -q "ERRO: falha ao gerar snapshots" "$SAIDA"; then
  prova "passo do PostgreSQL não foi contaminado pelo Marketing" 1
else
  prova "passo do PostgreSQL não foi contaminado pelo Marketing" 0
fi

# 7. Nenhum leitor de planilha, ADC pessoal ou fallback de exemplo.
PROIBIDO=0
for padrao in "read-auditoria-adc" "read-crm-clinicas-adc" "read-leads-sheets" \
              "read-financeiro-lancamentos-adc" "read-parcerias-pastore-adc" \
              "read-sheets-summary-adc" "gcloud auth application-default" \
              "spreadsheets/d/" "dados de exemplo"; do
  if grep -qF "$padrao" "$SAIDA"; then
    echo "    encontrado na saída: $padrao"
    PROIBIDO=1
  fi
done
prova "nenhum leitor de Sheets, ADC pessoal ou fallback de exemplo" "$PROIBIDO"
grep -q "nenhum leitor de planilha pode rodar" "$SAIDA"
prova "guarda de modo confirmou postgresql_only" "$?"

# 8. Exit final correto: Marketing indisponível NÃO derruba a esteira.
[ "$ESTEIRA_EXIT" -eq 0 ]
prova "esteira terminou com exit 0 (Marketing indisponível não é falha dela)" "$?"
grep -q "Concluído. Fonte operacional: PostgreSQL" "$SAIDA"
prova "esteira declarou o PostgreSQL como fonte operacional" "$?"

echo
if [ "$FALHAS" -gt 0 ]; then
  echo "RESULTADO: $FALHAS prova(s) falharam. Saída completa:"
  echo "────────────────────────────────────────────────────────────"
  cat "$SAIDA"
  echo "────────────────────────────────────────────────────────────"
  exit 1
fi
echo "RESULTADO: ensaio aprovado — todas as provas passaram."
