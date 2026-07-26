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

# ── soprolife_validar_unit_update_data (M23.1 — lacuna do deploy) ──────────

echo "── soprolife_validar_unit_update_data ──"

UNIT_TMP="$TMP_DIR/soprolife-update-data.service"

cat >"$UNIT_TMP" <<'UNIT'
[Service]
User=soprolife
Group=soprolife
EnvironmentFile=/opt/soprolife/secrets/m15.env
Environment=SOPROLIFE_M15_PYTHON=/opt/soprolife/venvs/m15/bin/python
Environment=SOPROLIFE_MARKETING_PYTHON=/opt/soprolife/venvs/marketing/bin/python
UNIT
(export SOPROLIFE_PRIV_MODE=direct; soprolife_validar_unit_update_data "$UNIT_TMP") >/dev/null 2>&1
caso "unit válida é aceita (rc 0)" 0 $?

cat >"$UNIT_TMP" <<'UNIT'
[Service]
Group=soprolife
EnvironmentFile=/opt/soprolife/secrets/m15.env
Environment=SOPROLIFE_M15_PYTHON=/opt/soprolife/venvs/m15/bin/python
Environment=SOPROLIFE_MARKETING_PYTHON=/opt/soprolife/venvs/marketing/bin/python
UNIT
(export SOPROLIFE_PRIV_MODE=direct; soprolife_validar_unit_update_data "$UNIT_TMP") >/dev/null 2>&1
caso "unit sem User=soprolife é rejeitada (rc 1)" 1 $?

cat >"$UNIT_TMP" <<'UNIT'
[Service]
User=soprolife
Group=soprolife
EnvironmentFile=/opt/soprolife/secrets/m15.env
Environment=CLOUDSDK_CONFIG=/home/soprolife/.config/gcloud
Environment=SOPROLIFE_M15_PYTHON=/opt/soprolife/venvs/m15/bin/python
Environment=SOPROLIFE_MARKETING_PYTHON=/opt/soprolife/venvs/marketing/bin/python
UNIT
(export SOPROLIFE_PRIV_MODE=direct; soprolife_validar_unit_update_data "$UNIT_TMP") >/dev/null 2>&1
caso "unit com CLOUDSDK_CONFIG (ADC pessoal) é rejeitada (rc 1)" 1 $?

cat >"$UNIT_TMP" <<'UNIT'
[Service]
User=soprolife
Group=soprolife
EnvironmentFile=/opt/soprolife/secrets/m15.env
Environment=SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1
Environment=SOPROLIFE_M15_PYTHON=/opt/soprolife/venvs/m15/bin/python
Environment=SOPROLIFE_MARKETING_PYTHON=/opt/soprolife/venvs/marketing/bin/python
UNIT
(export SOPROLIFE_PRIV_MODE=direct; soprolife_validar_unit_update_data "$UNIT_TMP") >/dev/null 2>&1
caso "unit com escape legado de Sheets é rejeitada (rc 1)" 1 $?

(export SOPROLIFE_PRIV_MODE=direct
 soprolife_validar_unit_update_data "$TMP_DIR/nao-existe.service") >/dev/null 2>&1
caso "unit ausente falha fechado (rc 1)" 1 $?

# ── Bloqueador da revisão crítica do M23.1 ─────────────────────────────────
# A validação anterior fazia grep de substring no arquivo inteiro e a unit
# REAL de produção documenta em comentário que CLOUDSDK_CONFIG foi removido.
# Resultado: todo deploy oficial abortava num falso positivo. O caso abaixo é
# o que faltava — validar o arquivo que o deploy realmente instala, não só
# fixtures sintéticas sem prosa.

UNIT_REAL="$SCRIPT_DIR/../../systemd/soprolife-update-data.service"
(export SOPROLIFE_PRIV_MODE=direct
 soprolife_validar_unit_update_data "$UNIT_REAL") >/dev/null 2>&1
caso "unit REAL do repositório é aceita (rc 0)" 0 $?
grep -q 'CLOUDSDK_CONFIG' "$UNIT_REAL"
caso "a unit REAL de fato cita CLOUDSDK_CONFIG em prosa (o falso positivo)" 0 $?
grep -q '^Environment=CLOUDSDK_CONFIG=' "$UNIT_REAL"
caso "a unit REAL não tem Environment=CLOUDSDK_CONFIG ativo" 1 $?

cat >"$UNIT_TMP" <<'UNIT'
# CLOUDSDK_CONFIG foi REMOVIDO: era a variável que mantinha vivo o ADC pessoal.
# SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION NÃO é definida aqui nem em outra unit.
[Unit]
Description=Comentário citando CLOUDSDK_CONFIG de propósito
[Service]
User=soprolife
Group=soprolife
; comentário estilo ponto-e-vírgula também citando SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION
EnvironmentFile=/opt/soprolife/secrets/m15.env
Environment=SOPROLIFE_M15_PYTHON=/opt/soprolife/venvs/m15/bin/python
Environment=SOPROLIFE_MARKETING_PYTHON=/opt/soprolife/venvs/marketing/bin/python
UNIT
(export SOPROLIFE_PRIV_MODE=direct; soprolife_validar_unit_update_data "$UNIT_TMP") >/dev/null 2>&1
caso "comentário explicativo com as duas variáveis é aceito (rc 0)" 0 $?

# Variantes ATIVAS que precisam continuar sendo recusadas.
unit_com_linha() {
  # Monta uma unit válida acrescentando a linha recebida em [Service].
  cat >"$UNIT_TMP" <<UNIT
[Service]
User=soprolife
Group=soprolife
EnvironmentFile=/opt/soprolife/secrets/m15.env
Environment=SOPROLIFE_M15_PYTHON=/opt/soprolife/venvs/m15/bin/python
Environment=SOPROLIFE_MARKETING_PYTHON=/opt/soprolife/venvs/marketing/bin/python
$1
UNIT
  (export SOPROLIFE_PRIV_MODE=direct
   soprolife_validar_unit_update_data "$UNIT_TMP") >/dev/null 2>&1
}

unit_com_linha 'Environment="CLOUDSDK_CONFIG=/home/soprolife/.config/gcloud"'
caso "Environment=CLOUDSDK_CONFIG citada com aspas é recusada (rc 1)" 1 $?

unit_com_linha 'Environment=   CLOUDSDK_CONFIG=/home/soprolife/.config/gcloud'
caso "Environment= com espaços antes da variável proibida é recusada (rc 1)" 1 $?

unit_com_linha 'Environment=OUTRA=1 CLOUDSDK_CONFIG=/x'
caso "variável proibida como 2º par da mesma Environment= é recusada (rc 1)" 1 $?

unit_com_linha 'Environment=CLOUDSDK_CONFIG=/x
Environment='
caso "proibida atribuída e depois resetada continua recusada (rc 1)" 1 $?

unit_com_linha 'Environment="SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1"'
caso "escape legado de Sheets citado com aspas é recusado (rc 1)" 1 $?

unit_com_linha 'Environment=CLOUDSDK_CONFIG=/x \'
caso "continuação de linha sem próxima linha falha fechado (rc 1)" 1 $?

unit_com_linha 'Environment=CLOUDSDK_CONFIG=/um \
   /dois'
caso "atribuição proibida quebrada em continuação é recusada (rc 1)" 1 $?

unit_com_linha 'Environment=CLOUDSDK\x5fCONFIG=/x'
caso "escape systemd em nome de variável falha fechado (rc 1)" 1 $?

unit_com_linha 'Environment=CLOUDSDK_CONFIG="/aberta'
caso "Environment= com citação não fechada falha fechado (rc 1)" 1 $?

unit_com_linha 'EnvironmentFile=/opt/soprolife/secrets/cloudsdk_config.env'
caso "EnvironmentFile nomeado pela variável proibida é recusado (rc 1)" 1 $?

unit_com_linha 'User=root'
caso "User=root sobrescrevendo soprolife é recusado (rc 1)" 1 $?

unit_com_linha 'EstaLinhaNaoTemIgual'
caso "linha ativa que não é Chave=Valor falha fechado (rc 1)" 1 $?

cat >"$UNIT_TMP" <<'UNIT'
User=soprolife
Group=soprolife
UNIT
(export SOPROLIFE_PRIV_MODE=direct; soprolife_validar_unit_update_data "$UNIT_TMP") >/dev/null 2>&1
caso "diretiva ativa fora de seção falha fechado (rc 1)" 1 $?

cat >"$UNIT_TMP" <<'UNIT'
[Service
User=soprolife
UNIT
(export SOPROLIFE_PRIV_MODE=direct; soprolife_validar_unit_update_data "$UNIT_TMP") >/dev/null 2>&1
caso "cabeçalho de seção malformado falha fechado (rc 1)" 1 $?

cat >"$UNIT_TMP" <<'UNIT'
[Unit]
Environment=CLOUDSDK_CONFIG=/x
[Service]
User=soprolife
Group=soprolife
EnvironmentFile=/opt/soprolife/secrets/m15.env
Environment=SOPROLIFE_M15_PYTHON=/opt/soprolife/venvs/m15/bin/python
Environment=SOPROLIFE_MARKETING_PYTHON=/opt/soprolife/venvs/marketing/bin/python
UNIT
(export SOPROLIFE_PRIV_MODE=direct; soprolife_validar_unit_update_data "$UNIT_TMP") >/dev/null 2>&1
caso "Environment= fora de [Service] falha fechado (rc 1)" 1 $?

cat >"$UNIT_TMP" <<'UNIT'
[Service]
User=soprolife
Group=soprolife
EnvironmentFile=/opt/soprolife/secrets/m15.env
Environment=SOPROLIFE_M15_PYTHON=/opt/soprolife/venvs/m15/bin/python
Environment=SOPROLIFE_MARKETING_PYTHON=
UNIT
(export SOPROLIFE_PRIV_MODE=direct; soprolife_validar_unit_update_data "$UNIT_TMP") >/dev/null 2>&1
caso "interpretador de Marketing sem valor é recusado (rc 1)" 1 $?

cat >"$UNIT_TMP" <<'UNIT'
[Service]
User=soprolife
Group=soprolife
EnvironmentFile=-/opt/soprolife/secrets/m15.env
Environment=SOPROLIFE_M15_PYTHON=/opt/soprolife/venvs/m15/bin/python
Environment=SOPROLIFE_MARKETING_PYTHON=/opt/soprolife/venvs/marketing/bin/python
UNIT
(export SOPROLIFE_PRIV_MODE=direct; soprolife_validar_unit_update_data "$UNIT_TMP") >/dev/null 2>&1
caso "EnvironmentFile opcional ('-') não satisfaz o requisito (rc 1)" 1 $?

# ── soprolife_validar_unit_update_data_efetiva (M23.2 — achado L-1) ────────
# A validação anterior só lia o ARQUIVO recém-instalado; um drop-in systemd
# em /etc/systemd/system/<unit>.d/*.conf — o mecanismo PADRÃO de override —
# reativando o escape legado ficava invisível. `systemctl cat` é dublado
# aqui para devolver a saída MERGIDA (arquivo + drop-ins, com as linhas
# "# /caminho" que o systemd realmente imprime) e a função sob teste roda o
# MESMO parser fail-closed sobre esse resultado.

echo "── soprolife_validar_unit_update_data_efetiva ──"

UNIDADE_EFETIVA="soprolife-update-data.service"
EFETIVA_TMP="$TMP_DIR/systemctl-cat-efetiva.txt"

cat >"$STUB_DIR/systemctl" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  cat)
    if [[ -n "${STUB_SYSTEMCTL_CAT_FILE:-}" && -f "$STUB_SYSTEMCTL_CAT_FILE" ]]; then
      cat "$STUB_SYSTEMCTL_CAT_FILE"
    fi
    ;;
  *) echo "${STUB_MAINPID:-0}" ;;
esac
EOF
chmod 0755 "$STUB_DIR/systemctl"

UNIT_BASE='# /etc/systemd/system/soprolife-update-data.service
[Service]
User=soprolife
Group=soprolife
EnvironmentFile=/opt/soprolife/secrets/m15.env
Environment=SOPROLIFE_M15_PYTHON=/opt/soprolife/venvs/m15/bin/python
Environment=SOPROLIFE_MARKETING_PYTHON=/opt/soprolife/venvs/marketing/bin/python'

executa_efetiva() {
  (
    export PATH="$STUB_DIR:$PATH"
    export SOPROLIFE_PRIV_MODE=direct
    export STUB_SYSTEMCTL_CAT_FILE="$EFETIVA_TMP"
    soprolife_validar_unit_update_data_efetiva "$UNIDADE_EFETIVA"
  ) >/dev/null 2>&1
}

# Caso 1: sem nenhum drop-in — equivalente ao arquivo puro, deve aceitar.
printf '%s\n' "$UNIT_BASE" >"$EFETIVA_TMP"
executa_efetiva
caso "sem drop-in nenhum: configuração efetiva aceita (rc 0)" 0 $?

# Caso 2: drop-in limpo (só comentário + ajuste inofensivo) deve aceitar.
printf '%s\n\n# /etc/systemd/system/soprolife-update-data.service.d/99-local.conf\n[Service]\n# Ajuste local inofensivo, sem relação com o contrato de segredo.\nTimeoutStartSec=120\n' \
  "$UNIT_BASE" >"$EFETIVA_TMP"
executa_efetiva
caso "drop-in limpo (comentário + diretiva inofensiva) é aceito (rc 0)" 0 $?

# Caso 3: drop-in reativa CLOUDSDK_CONFIG (ADC pessoal) — o arquivo principal
# continua limpo; só a config EFETIVA expõe o escape.
printf '%s\n\n# /etc/systemd/system/soprolife-update-data.service.d/98-adc.conf\n[Service]\nEnvironment=CLOUDSDK_CONFIG=/home/soprolife/.config/gcloud\n' \
  "$UNIT_BASE" >"$EFETIVA_TMP"
executa_efetiva
caso "drop-in com CLOUDSDK_CONFIG ativo é recusado (rc 1)" 1 $?

# Caso 4: drop-in reativa o escape legado de migração de Sheets.
printf '%s\n\n# /etc/systemd/system/soprolife-update-data.service.d/97-legado.conf\n[Service]\nEnvironment=SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1\n' \
  "$UNIT_BASE" >"$EFETIVA_TMP"
executa_efetiva
caso "drop-in com escape legado de Sheets ativo é recusado (rc 1)" 1 $?

# Caso 5 (semântica de reset): um drop-in anterior ativa a variável proibida
# e um drop-in POSTERIOR reseta Environment= por completo — a proibição
# conta toda ocorrência ativa, nunca só o estado final, então continua
# recusado mesmo depois do reset.
printf '%s\n\n# .../98-adc.conf\n[Service]\nEnvironment=CLOUDSDK_CONFIG=/home/soprolife/.config/gcloud\n\n# .../99-reset.conf\n[Service]\nEnvironment=\nEnvironment=SOPROLIFE_M15_PYTHON=/opt/soprolife/venvs/m15/bin/python\nEnvironment=SOPROLIFE_MARKETING_PYTHON=/opt/soprolife/venvs/marketing/bin/python\n' \
  "$UNIT_BASE" >"$EFETIVA_TMP"
executa_efetiva
caso "variável proibida ativada e depois resetada por outro drop-in continua recusada (rc 1)" 1 $?

# Caso 6 (semântica de reset legítima): um drop-in substitui só os
# interpretadores por um valor errado e OUTRO drop-in reseta e redefine
# corretamente — nada proibido envolvido, deve aceitar com o valor FINAL.
printf '%s\n\n# .../50-experimental.conf\n[Service]\nEnvironment=SOPROLIFE_M15_PYTHON=/tmp/errado/python\n\n# .../60-correcao.conf\n[Service]\nEnvironment=\nEnvironment=SOPROLIFE_M15_PYTHON=/opt/soprolife/venvs/m15/bin/python\nEnvironment=SOPROLIFE_MARKETING_PYTHON=/opt/soprolife/venvs/marketing/bin/python\n' \
  "$UNIT_BASE" >"$EFETIVA_TMP"
executa_efetiva
caso "reset+redefinição legítima entre drop-ins é aceita com o valor final (rc 0)" 0 $?

# Caso 7: diretiva malformada dentro do PRÓPRIO drop-in falha fechado.
printf '%s\n\n# .../96-malformado.conf\n[Service]\nEstaLinhaNaoTemIgual\n' \
  "$UNIT_BASE" >"$EFETIVA_TMP"
executa_efetiva
caso "diretiva malformada num drop-in falha fechado (rc 1)" 1 $?

# Caso 8: `systemctl cat` não devolve nada (unit ausente/não carregada) falha
# fechado em vez de aceitar um "arquivo vazio" por omissão.
: >"$EFETIVA_TMP"
executa_efetiva
caso "'systemctl cat' vazio (unit ausente/não carregada) falha fechado (rc 1)" 1 $?

# ── soprolife_estado_timer (M23.1 — provar que instalar a unit não liga/desliga o timer sozinho) ──

echo "── soprolife_estado_timer ──"

cat >"$STUB_DIR/systemctl" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  is-enabled) echo "${STUB_TIMER_ENABLED-enabled}"; exit "${STUB_TIMER_ENABLED_RC:-0}" ;;
  is-active) echo "${STUB_TIMER_ACTIVE-active}"; exit "${STUB_TIMER_ACTIVE_RC:-0}" ;;
  *) echo "${STUB_MAINPID:-0}" ;;
esac
EOF
chmod 0755 "$STUB_DIR/systemctl"

RESULT="$(
  export PATH="$STUB_DIR:$PATH"
  export SOPROLIFE_PRIV_MODE=direct
  export STUB_TIMER_ENABLED=enabled
  export STUB_TIMER_ACTIVE=active
  soprolife_estado_timer soprolife-update-data.timer
)"
caso "timer habilitado e ativo é relatado corretamente" "enabled:active" "$RESULT"

RESULT="$(
  export PATH="$STUB_DIR:$PATH"
  export SOPROLIFE_PRIV_MODE=direct
  export STUB_TIMER_ENABLED=disabled
  export STUB_TIMER_ENABLED_RC=1
  export STUB_TIMER_ACTIVE=inactive
  export STUB_TIMER_ACTIVE_RC=3
  soprolife_estado_timer soprolife-update-data.timer
)"
caso "timer desabilitado/inativo também é relatado (sem falhar a captura)" \
  "desconhecido:desconhecido" "$RESULT"

echo
if (( FALHAS )); then
  echo "RESULTADO: $FALHAS falha(s)."
  exit 1
fi
echo "RESULTADO: todos os casos passaram."
