#!/usr/bin/env bash
# Sobe um PostgreSQL 16 EFÊMERO (podman rootless ou docker), roda o ciclo de
# migração + a suíte completa (incluindo testes de concorrência PG) e derruba
# o container. Sem sudo, sem VPS, sem tocar em dados reais.
set -euo pipefail
cd "$(dirname "$0")/.."

NAME=m15-pg-teste
PORT="${M15_PG_TEST_PORT:-55432}"
PASS="m15-teste-efemero-$RANDOM"
URL="postgresql+psycopg://postgres:${PASS}@127.0.0.1:${PORT}/m15_teste"
VENV_DIR="${M15_VENV_DIR:-.venv}"
ALEMBIC="$VENV_DIR/bin/alembic"
PYTHON="$VENV_DIR/bin/python"

if command -v podman >/dev/null 2>&1; then RUNTIME=podman
elif command -v docker >/dev/null 2>&1; then RUNTIME=docker
else
  echo "ERRO: nenhum runtime de container (podman/docker); PostgreSQL real é obrigatório." >&2
  exit 1
fi

if [[ ! -x "$ALEMBIC" || ! -x "$PYTHON" ]]; then
  echo "ERRO: ambiente Python não encontrado em $VENV_DIR (defina M15_VENV_DIR)." >&2
  exit 1
fi

cleanup() { "$RUNTIME" rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

"$RUNTIME" run -d --name "$NAME" \
  -e POSTGRES_PASSWORD="$PASS" -e POSTGRES_DB=m15_teste \
  -p "127.0.0.1:${PORT}:5432" docker.io/library/postgres:16-alpine >/dev/null

ready=0
for _ in $(seq 1 60); do
  # O entrypoint oficial abre primeiro um servidor temporário só no socket
  # Unix para inicializar o cluster e depois o encerra. Testar 127.0.0.1 evita
  # o falso "ready" nesse servidor temporário e a corrida "database is
  # shutting down" no psql imediatamente seguinte.
  if "$RUNTIME" exec "$NAME" pg_isready -h 127.0.0.1 -U postgres \
       -d m15_teste >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" != 1 ]]; then
  echo "ERRO: PostgreSQL efêmero não ficou pronto em 60 segundos." >&2
  "$RUNTIME" logs "$NAME" >&2 || true
  exit 1
fi

PG_MAJOR=$("$RUNTIME" exec "$NAME" psql -h 127.0.0.1 -U postgres -d m15_teste -Atqc \
  "SHOW server_version_num" | cut -c1-2)
if [[ "$PG_MAJOR" != 16 ]]; then
  echo "ERRO: servidor iniciado não é PostgreSQL 16 (major=$PG_MAJOR)." >&2
  exit 1
fi
echo "PostgreSQL major confirmado: $PG_MAJOR"

echo "== ciclo de migração no PostgreSQL 16 real =="
M15_DATABASE_URL="$URL" "$ALEMBIC" upgrade head
M15_DATABASE_URL="$URL" "$ALEMBIC" check
M15_DATABASE_URL="$URL" "$ALEMBIC" downgrade base
M15_DATABASE_URL="$URL" "$ALEMBIC" upgrade head

echo "== suíte completa (SQLite + testes PG) =="
env -u M15_DATABASE_URL M15_TEST_POSTGRES_URL="$URL" \
  "$PYTHON" -m pytest tests/ -q

cleanup
trap - EXIT
echo "OK — PostgreSQL efêmero derrubado."
