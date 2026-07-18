#!/usr/bin/env bash
# M15.3B — Testes offline das funções de hardening do deploy.
#
# Usa dublês (funções e comandos falsos) somente para provar a orquestração
# de shell: retry de health, fail-closed e resolução de conflito de porta.
# O probe HTTP real é exercitado contra um servidor efêmero em 127.0.0.1
# (porta aleatória), sem rede externa, sem sudo e sem systemd real.
#
# Uso: bash painel-soprolife/nucleo-m15/scripts/test-deploy-hardening.sh
# Exit: 0 = todos os casos passaram | 1 = houve falha.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-deploy-hardening.sh
source "$SCRIPT_DIR/lib-deploy-hardening.sh"

FALHAS=0
TMP_DIR="$(mktemp -d)"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$TMP_DIR"
}
trap cleanup EXIT

caso() {
  local nome="$1" esperado="$2" obtido="$3"
  if [[ "$esperado" == "$obtido" ]]; then
    echo "  PASS: $nome"
  else
    FALHAS=$((FALHAS + 1))
    echo "  FAIL: $nome — esperado '$esperado', obtido '$obtido'"
  fi
}

# ── Retry de health (probe dublê; só orquestração de shell) ──────────────────

CONTADOR="$TMP_DIR/contador"

probe_atrasada() {
  # Falha nas 3 primeiras chamadas e responde ok na quarta — simula unit
  # Type=simple cujo processo ainda está carregando imports.
  local n
  n="$(cat "$CONTADOR" 2>/dev/null || echo 0)"
  n=$((n + 1))
  printf '%s' "$n" >"$CONTADOR"
  (( n >= 4 ))
}

probe_sempre_falha() {
  local n
  n="$(cat "$CONTADOR" 2>/dev/null || echo 0)"
  printf '%s' "$((n + 1))" >"$CONTADOR"
  return 1
}

echo "── soprolife_wait_health_ok ──"

rm -f -- "$CONTADOR"
SOPROLIFE_HEALTH_PROBE=probe_atrasada \
  soprolife_wait_health_ok "http://127.0.0.1:1/health" "serviço tardio" 10 0 60 \
  >/dev/null 2>&1
caso "inicialização tardia é aceita (rc 0)" 0 $?
caso "sucesso veio na 4a tentativa" 4 "$(cat "$CONTADOR")"

rm -f -- "$CONTADOR"
SOPROLIFE_HEALTH_PROBE=probe_sempre_falha \
  soprolife_wait_health_ok "http://127.0.0.1:1/health" "serviço morto" 3 0 60 \
  >/dev/null 2>&1
caso "falha fechada após esgotar tentativas (rc 1)" 1 $?
caso "parou exatamente no máximo de tentativas" 3 "$(cat "$CONTADOR")"

rm -f -- "$CONTADOR"
MSG="$(SOPROLIFE_HEALTH_PROBE=probe_sempre_falha \
  soprolife_wait_health_ok "http://127.0.0.1:1/health" "serviço morto" 3 0 60 \
  2>&1 >/dev/null || true)"
case "$MSG" in
  *"não ficou pronto"*journalctl*) caso "diagnóstico final útil sem segredos" 0 0 ;;
  *) caso "diagnóstico final útil sem segredos" 0 1 ;;
esac

rm -f -- "$CONTADOR"
SOPROLIFE_HEALTH_PROBE=probe_sempre_falha \
  soprolife_wait_health_ok "http://127.0.0.1:1/health" "timeout total" 99 1 1 \
  >/dev/null 2>&1
RC=$?
TENTATIVAS="$(cat "$CONTADOR")"
caso "timeout total explícito derruba antes do máximo (rc 1)" 1 "$RC"
if (( TENTATIVAS <= 2 )); then
  caso "timeout total limitou as tentativas" 0 0
else
  caso "timeout total limitou as tentativas" "<=2" "$TENTATIVAS"
fi

# ── Probe HTTP real (servidor efêmero em loopback) ───────────────────────────

echo "── soprolife_probe_health_ok (HTTP real em loopback) ──"

inicia_servidor() {
  # $1 = status HTTP, $2 = corpo JSON
  local portfile="$TMP_DIR/porta"
  rm -f -- "$portfile"
  python3 - "$1" "$2" "$portfile" <<'PY' &
import http.server
import sys

status = int(sys.argv[1])
body = sys.argv[2].encode("utf-8")
portfile = sys.argv[3]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
with open(portfile, "w", encoding="utf-8") as fh:
    fh.write(str(server.server_address[1]))
server.serve_forever()
PY
  SERVER_PID=$!
  local _tick
  for _tick in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    : "$_tick"
    [[ -s "$portfile" ]] && break
    sleep 0.2
  done
  PORTA="$(cat "$portfile")"
}

para_servidor() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
  fi
}

inicia_servidor 200 '{"status": "ok"}'
soprolife_probe_health_ok "http://127.0.0.1:$PORTA/health" >/dev/null 2>&1
caso "HTTP 200 status=ok é aceito" 0 $?
para_servidor

inicia_servidor 200 '{"status": "iniciando"}'
soprolife_probe_health_ok "http://127.0.0.1:$PORTA/health" >/dev/null 2>&1
caso "HTTP 200 sem status=ok é rejeitado" 1 $?
para_servidor

inicia_servidor 500 '{"status": "ok"}'
soprolife_probe_health_ok "http://127.0.0.1:$PORTA/health" >/dev/null 2>&1
caso "HTTP 500 é rejeitado mesmo com status=ok" 1 $?
para_servidor

soprolife_probe_health_ok "http://127.0.0.1:$PORTA/health" >/dev/null 2>&1
caso "conexão recusada é rejeitada" 1 $?

# ── Conflito de porta loopback (comandos dublês; nada real é morto) ──────────

echo "── soprolife_garantir_porta_loopback_livre ──"

STUB_DIR="$TMP_DIR/stubs"
mkdir -p "$STUB_DIR"
export STUB_DIR

cat >"$STUB_DIR/ss" <<'EOF'
#!/usr/bin/env bash
if [[ -f "$STUB_DIR/killed" && -f "$STUB_DIR/kill-libera" ]]; then
  exit 0
fi
cat "$STUB_DIR/ss-fixture" 2>/dev/null
exit 0
EOF

cat >"$STUB_DIR/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "${STUB_MAINPID:-0}"
EOF

cat >"$STUB_DIR/ps" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *"-o user="*) echo "${STUB_PS_USER:-}" ;;
  *"-o args="*) echo "${STUB_PS_ARGS:-}" ;;
esac
EOF

cat >"$STUB_DIR/cat" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  /proc/*/cgroup) echo "${STUB_CGROUP:-}" ;;
  *) exec /bin/cat "$@" ;;
esac
EOF

cat >"$STUB_DIR/kill" <<'EOF'
#!/usr/bin/env bash
echo "$@" >"$STUB_DIR/kill-args"
touch "$STUB_DIR/killed"
EOF

chmod 0755 "$STUB_DIR"/ss "$STUB_DIR"/systemctl "$STUB_DIR"/ps \
  "$STUB_DIR"/cat "$STUB_DIR"/kill

FIXTURE_LEGADO='LISTEN 0 5 127.0.0.1:8765 0.0.0.0:* users:(("python3",pid=4242,fd=3))'

executa_conflito() {
  # Executa a função com o ambiente dublê em subshell isolado.
  (
    export PATH="$STUB_DIR:$PATH"
    export SOPROLIFE_PRIV_MODE=direct
    export SOPROLIFE_KILL_CMD="$STUB_DIR/kill"
    export SOPROLIFE_KILL_INTERVALO_S=0
    soprolife_garantir_porta_loopback_livre \
      "soprolife-painel-loopback.service" "soprolife"
  ) >/dev/null 2>&1
}

kill_foi_chamado() {
  # Imprime 0 se o dublê de kill foi invocado; 1 caso contrário.
  if [[ -f "$STUB_DIR/kill-args" ]]; then
    echo 0
  else
    echo 1
  fi
}

reset_stub() {
  rm -f -- "$STUB_DIR/ss-fixture" "$STUB_DIR/killed" \
    "$STUB_DIR/kill-args" "$STUB_DIR/kill-libera"
  unset STUB_MAINPID STUB_PS_USER STUB_PS_ARGS STUB_CGROUP
}

reset_stub
: >"$STUB_DIR/ss-fixture"
executa_conflito
caso "porta livre segue em frente (rc 0)" 0 $?

reset_stub
echo "$FIXTURE_LEGADO" >"$STUB_DIR/ss-fixture"
export STUB_MAINPID=4242
executa_conflito
RC=$?
caso "porta da própria unit é aceita sem kill (rc 0)" 0 "$RC"
caso "própria unit: kill não foi chamado" 1 "$(kill_foi_chamado)"

reset_stub
echo "$FIXTURE_LEGADO" >"$STUB_DIR/ss-fixture"
export STUB_MAINPID=0
export STUB_PS_USER=soprolife
export STUB_PS_ARGS="python3 painel-soprolife/scripts/command-center-local-server.py"
export STUB_CGROUP="0::/user.slice/user-1000.slice/session-4.scope"
touch "$STUB_DIR/kill-libera"
executa_conflito
RC=$?
caso "legado validado é encerrado e porta liberada (rc 0)" 0 "$RC"
caso "legado validado: SIGTERM no PID correto" "-TERM 4242" \
  "$(cat "$STUB_DIR/kill-args" 2>/dev/null || echo ausente)"

reset_stub
echo "$FIXTURE_LEGADO" >"$STUB_DIR/ss-fixture"
export STUB_MAINPID=0
export STUB_PS_USER=root
export STUB_PS_ARGS="python3 painel-soprolife/scripts/command-center-local-server.py"
export STUB_CGROUP="0::/user.slice/user-0.slice/session-9.scope"
executa_conflito
RC=$?
caso "usuário inesperado falha fechado (rc 1)" 1 "$RC"
caso "usuário inesperado: kill não foi chamado" 1 "$(kill_foi_chamado)"

reset_stub
echo "$FIXTURE_LEGADO" >"$STUB_DIR/ss-fixture"
export STUB_MAINPID=0
export STUB_PS_USER=soprolife
export STUB_PS_ARGS="nc -l 8765"
export STUB_CGROUP="0::/user.slice/user-1000.slice/session-4.scope"
executa_conflito
RC=$?
caso "comando desconhecido falha fechado (rc 1)" 1 "$RC"
caso "comando desconhecido: kill não foi chamado" 1 "$(kill_foi_chamado)"

reset_stub
echo "$FIXTURE_LEGADO" >"$STUB_DIR/ss-fixture"
export STUB_MAINPID=0
export STUB_PS_USER=soprolife
export STUB_PS_ARGS="python3 painel-soprolife/scripts/command-center-local-server.py"
export STUB_CGROUP="0::/system.slice/soprolife-painel.service"
executa_conflito
RC=$?
caso "processo de outra unit systemd falha fechado (rc 1)" 1 "$RC"
caso "outra unit: kill não foi chamado" 1 "$(kill_foi_chamado)"

reset_stub
echo 'LISTEN 0 5 127.0.0.1:8765 0.0.0.0:*' >"$STUB_DIR/ss-fixture"
executa_conflito
caso "listener sem PID identificável falha fechado (rc 1)" 1 $?

reset_stub
echo "$FIXTURE_LEGADO" >"$STUB_DIR/ss-fixture"
export STUB_MAINPID=0
export STUB_PS_USER=soprolife
export STUB_PS_ARGS="python3 painel-soprolife/scripts/command-center-local-server.py"
export STUB_CGROUP="0::/user.slice/user-1000.slice/session-4.scope"
# sem kill-libera: o ss dublê continua mostrando a porta ocupada após SIGTERM
executa_conflito
RC=$?
caso "legado que não libera a porta falha fechado (rc 1)" 1 "$RC"

echo
if (( FALHAS )); then
  echo "RESULTADO: $FALHAS falha(s)."
  exit 1
fi
echo "RESULTADO: todos os casos passaram."
