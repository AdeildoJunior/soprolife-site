#!/usr/bin/env bash
# M15.3B — Funções reutilizáveis de hardening operacional do deploy.
#
# 1) soprolife_wait_health_ok: espera prontidão real de um serviço HTTP após
#    systemctl start/restart. Units Type=simple retornam antes de Python,
#    Pydantic, FastAPI e Uvicorn terminarem de carregar; testar o health
#    imediatamente causa "conexão recusada" falsa. A espera usa tentativas
#    finitas, intervalo curto, timeout total explícito, exige HTTP 200 com
#    body JSON status=ok e falha fechada com diagnóstico ao esgotar limites.
#    Não usa sleep fixo como única solução e não mascara erro persistente:
#    quem não responde dentro dos limites derruba o deploy.
#
# 2) soprolife_garantir_porta_loopback_livre: prepara 127.0.0.1:8765 para a
#    unit soprolife-painel-loopback.service. Só encerra um processo depois de
#    validar PID, usuário, comando, cgroup e listener; qualquer conflito
#    desconhecido falha fechado sem matar nada.
#
# 3) soprolife_validar_unit_update_data / soprolife_estado_timer (M23.1):
#    o deploy oficial (deploy-producao-vps.sh) só instalava as units da API e
#    do proxy loopback; soprolife-update-data.service ficava de fora, e um
#    deploy real precisou de cópia manual do operador depois do script
#    "concluído" (achado do terceiro deploy do M23). Estas funções permitem
#    que o script valide o conteúdo da unit instalada — sem depender de
#    systemd real — e capture/compare o estado enabled/active do timer antes
#    e depois da instalação, para provar que nenhum efeito colateral ativou
#    o pipeline sozinho.
#
# Este arquivo é carregado via source pelo deploy e pelos testes. Nenhuma
# função imprime segredos: as URLs de health locais não têm querystring nem
# token. Variáveis SOPROLIFE_* permitem injetar dublês nos testes de shell;
# em produção os padrões (sudo + comandos reais) permanecem.

soprolife_probe_health_ok() {
  # Aceita somente HTTP 200 com body JSON {"status": "ok"}.
  python3 - "$1" <<'PY'
import json
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=5) as response:
        if response.status != 200:
            raise SystemExit(1)
        body = json.load(response)
except SystemExit:
    raise
except Exception:
    raise SystemExit(1)
if not isinstance(body, dict) or body.get("status") != "ok":
    raise SystemExit(1)
PY
}

soprolife_wait_health_ok() {
  local url="$1"
  local descricao="$2"
  local max_tentativas="${3:-${SOPROLIFE_HEALTH_MAX_TENTATIVAS:-30}}"
  local intervalo_s="${4:-${SOPROLIFE_HEALTH_INTERVALO_S:-2}}"
  local timeout_total_s="${5:-${SOPROLIFE_HEALTH_TIMEOUT_TOTAL_S:-90}}"
  local probe="${SOPROLIFE_HEALTH_PROBE:-soprolife_probe_health_ok}"
  local inicio agora tentativa=0
  inicio="$(date +%s)"
  while :; do
    tentativa=$((tentativa + 1))
    if "$probe" "$url"; then
      echo "OK: ${descricao} pronto (HTTP 200 status=ok, tentativa ${tentativa})."
      return 0
    fi
    agora="$(date +%s)"
    if (( tentativa >= max_tentativas )); then
      break
    fi
    if (( agora - inicio + intervalo_s > timeout_total_s )); then
      break
    fi
    sleep "$intervalo_s"
  done
  agora="$(date +%s)"
  {
    echo "ERRO: ${descricao} não ficou pronto após ${tentativa} tentativa(s)" \
      "em $((agora - inicio))s (limites: ${max_tentativas} tentativas," \
      "${timeout_total_s}s no total)."
    echo "Exigido: HTTP 200 com body status=ok em ${url}"
    echo "Diagnóstico: confira 'systemctl status' e 'journalctl -u' do" \
      "serviço associado antes de reexecutar."
  } >&2
  return 1
}

soprolife_priv() {
  # Em produção executa via sudo; nos testes SOPROLIFE_PRIV_MODE=direct
  # executa os dublês colocados no PATH.
  if [[ "${SOPROLIFE_PRIV_MODE:-sudo}" == "direct" ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

soprolife_listener_loopback_8765() {
  # Imprime a(s) linha(s) de listener TCP exatamente em 127.0.0.1:8765.
  soprolife_priv ss -ltnpH "sport = :8765" | awk '$4 == "127.0.0.1:8765"'
}

soprolife_garantir_porta_loopback_livre() {
  local unit="${1:-soprolife-painel-loopback.service}"
  local usuario_esperado="${2:-soprolife}"
  local linha pid unit_pid proc_user proc_args proc_cgroup tentativa

  linha="$(soprolife_listener_loopback_8765 || true)"
  if [[ -z "$linha" ]]; then
    echo "Porta 127.0.0.1:8765 livre."
    return 0
  fi

  pid="$(sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' <<<"$linha" | head -n 1)"
  if [[ -z "$pid" ]]; then
    echo "ERRO: listener em 127.0.0.1:8765 sem PID identificável;" \
      "conflito desconhecido — abortando sem matar processo (fail-closed)." >&2
    return 1
  fi

  unit_pid="$(soprolife_priv systemctl show --property=MainPID --value "$unit" \
    2>/dev/null || echo 0)"
  if [[ -n "$unit_pid" && "$unit_pid" != "0" && "$pid" == "$unit_pid" ]]; then
    echo "Porta 127.0.0.1:8765 já pertence a ${unit} (PID ${pid})."
    return 0
  fi

  # Processo legado conhecido: servidor manual do painel iniciado fora do
  # systemd (caso encontrado no deploy produtivo de 18/07/2026). Validação
  # obrigatória de usuário, comando, cgroup e listener antes de encerrar.
  proc_user="$(soprolife_priv ps -o user= -p "$pid" | tr -d '[:space:]')"
  proc_args="$(soprolife_priv ps -o args= -p "$pid")"
  proc_cgroup="$(soprolife_priv cat "/proc/${pid}/cgroup" 2>/dev/null || true)"

  if [[ "$proc_user" != "$usuario_esperado" ]]; then
    echo "ERRO: listener PID ${pid} pertence ao usuário '${proc_user}'," \
      "não a '${usuario_esperado}' — conflito desconhecido (fail-closed)." >&2
    return 1
  fi
  case "$proc_args" in
    *command-center-local-server.py*|*"-m http.server"*) ;;
    *)
      echo "ERRO: listener PID ${pid} não é um servidor conhecido do painel" \
        "— conflito desconhecido (fail-closed)." >&2
      return 1
      ;;
  esac
  if grep -q '\.service' <<<"$proc_cgroup"; then
    echo "ERRO: listener PID ${pid} é gerenciado por outra unit systemd;" \
      "não será encerrado por este deploy (fail-closed)." >&2
    return 1
  fi
  if soprolife_priv ss -ltnpH "sport = :8765" | grep "pid=${pid}," | \
     awk '{print $4}' | grep -qv '^127\.0\.0\.1:8765$'; then
    echo "ERRO: listener PID ${pid} também escuta 8765 fora do loopback;" \
      "conflito desconhecido (fail-closed)." >&2
    return 1
  fi

  echo "Processo legado conhecido em 127.0.0.1:8765 (PID ${pid}," \
    "usuário ${usuario_esperado}); encerrando com SIGTERM."
  soprolife_priv "${SOPROLIFE_KILL_CMD:-kill}" -TERM "$pid"
  for tentativa in 1 2 3 4 5 6 7 8 9 10; do
    sleep "${SOPROLIFE_KILL_INTERVALO_S:-1}"
    if [[ -z "$(soprolife_listener_loopback_8765 || true)" ]]; then
      echo "Porta 127.0.0.1:8765 liberada (verificação ${tentativa})."
      return 0
    fi
  done
  echo "ERRO: processo legado não liberou 127.0.0.1:8765 após SIGTERM;" \
    "intervenção manual necessária (fail-closed)." >&2
  return 1
}

soprolife_validar_unit_update_data() {
  # Confirma que a unit soprolife-update-data.service instalada no destino
  # ($1) preserva os contornos exigidos pelo M23: usuário/grupo de serviço,
  # EnvironmentFile do núcleo M15, interpretadores dedicados de API e
  # Marketing, e ausência de qualquer variável que reative ADC pessoal
  # (CLOUDSDK_CONFIG) ou o escape manual de migração legada de Sheets.
  # Falha fechada: unit ausente, ilegível ou sem um requisito é erro.
  local target="$1"
  local conteudo
  conteudo="$(soprolife_priv cat "$target" 2>/dev/null)" || {
    echo "ERRO: não foi possível ler a unit instalada em $target." >&2
    return 1
  }
  local requerido
  for requerido in \
    'User=soprolife' \
    'Group=soprolife' \
    'EnvironmentFile=/opt/soprolife/secrets/m15.env' \
    'SOPROLIFE_M15_PYTHON=' \
    'SOPROLIFE_MARKETING_PYTHON='
  do
    if ! grep -qF -- "$requerido" <<<"$conteudo"; then
      echo "ERRO: unit de atualização instalada não contém '$requerido'." >&2
      return 1
    fi
  done
  local proibido
  for proibido in 'CLOUDSDK_CONFIG' 'SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION'; do
    if grep -qF -- "$proibido" <<<"$conteudo"; then
      echo "ERRO: unit de atualização instalada contém '$proibido'" \
        "(escape legado não pode estar ativo em produção)." >&2
      return 1
    fi
  done
  return 0
}

soprolife_estado_timer() {
  # Imprime "enabled-state:active-state" de uma unit (tipicamente o timer de
  # atualização), usando soprolife_priv para aceitar dublês nos testes.
  # "desconhecido" substitui um código de saída não-zero do systemctl (por
  # exemplo, unit ainda não instalada) sem derrubar o script chamador.
  local unit="$1" habilitado ativo
  habilitado="$(soprolife_priv systemctl is-enabled "$unit" 2>/dev/null)" || \
    habilitado="desconhecido"
  ativo="$(soprolife_priv systemctl is-active "$unit" 2>/dev/null)" || \
    ativo="desconhecido"
  printf '%s:%s' "$habilitado" "$ativo"
}
