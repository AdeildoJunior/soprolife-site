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
#    o pipeline sozinho. A validação analisa a CONFIGURAÇÃO ATIVA (seções,
#    comentários e continuação de linha como o systemd os lê), nunca a prosa
#    do arquivo: a unit real documenta em comentário quais variáveis foram
#    removidas, e a primeira versão desta função reprovava a própria unit de
#    produção por causa desse texto.
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
  # Confirma que o ARQUIVO da unit soprolife-update-data.service instalado no
  # destino ($1) preserva os contornos exigidos pelo M23: usuário/grupo de
  # serviço, EnvironmentFile do núcleo M15, interpretadores dedicados de API e
  # Marketing, e ausência de qualquer variável que reative ADC pessoal
  # (CLOUDSDK_CONFIG) ou o escape manual de migração legada de Sheets.
  #
  # M23.1 (correção do bloqueador da revisão crítica): a versão anterior fazia
  # grep de substring no ARQUIVO INTEIRO. A unit real de produção documenta em
  # comentário que CLOUDSDK_CONFIG foi REMOVIDO — a prosa citava o nome da
  # variável e derrubava todo deploy oficial com um falso positivo. O que
  # importa é a CONFIGURAÇÃO ATIVA, não o texto explicativo: a validação agora
  # interpreta a unit como o systemd interpreta (seções, comentários,
  # continuação de linha, múltiplos pares por Environment=) e só olha
  # diretivas Environment=/EnvironmentFile= ativas.
  #
  # L-1 (achado da revisão crítica final): isto valida só o ARQUIVO — não vê
  # drop-ins em /etc/systemd/system/<unit>.d/*.conf. Use
  # `soprolife_validar_unit_update_data_efetiva` (abaixo) depois do
  # daemon-reload para validar a configuração REALMENTE carregada.
  local target="$1"
  local conteudo
  conteudo="$(soprolife_priv cat "$target" 2>/dev/null)" || {
    echo "ERRO: não foi possível ler a unit instalada em $target." >&2
    return 1
  }
  soprolife_validar_conteudo_unit "$target" "$conteudo"
}

soprolife_conteudo_efetivo_unit() {
  # Imprime a configuração MERGIDA que o systemd realmente carrega para a
  # unit ($1): o arquivo principal MAIS todo drop-in ativo em
  # <unit>.d/*.conf, na ordem em que o systemd os aplica. `systemctl cat`
  # é a própria ferramenta do systemd para isto — cada fragmento vem
  # precedido de uma linha "# /caminho/do/fragmento" que o parser de
  # `soprolife_validar_conteudo_unit` já trata como comentário, então os
  # fragmentos concatenados são validados como uma única configuração ativa.
  soprolife_priv systemctl cat "$1" 2>/dev/null
}

soprolife_validar_unit_update_data_efetiva() {
  # M23.2 (achado L-1 da revisão crítica final): a validação anterior só lia
  # o ARQUIVO da unit recém-instalada; um drop-in systemd em
  # /etc/systemd/system/<unit>.d/*.conf — o mecanismo PADRÃO de override —
  # reativando CLOUDSDK_CONFIG (ADC pessoal) ou
  # SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION (escape legado de Sheets) passava
  # disfarçado: o arquivo instalado ficava limpo, o deploy reportava o
  # pipeline como "hardened", mas a unit CARREGADA pelo systemd continuava
  # com o escape ativo.
  #
  # Correção: chamar isto DEPOIS de `systemctl daemon-reload`, com o NOME da
  # unit (não o caminho do arquivo) — usa `systemctl cat`, que imprime a
  # configuração efetiva (arquivo + drop-ins) exatamente como o systemd a
  # interpreta, e roda o MESMO parser fail-closed de
  # `soprolife_validar_unit_update_data` sobre o resultado. Drop-ins
  # limpos (só comentário, ou diretivas alheias ao contrato) continuam
  # aceitos; qualquer atribuição ATIVA das variáveis proibidas — em
  # QUALQUER fragmento — falha fechado, mesmo que um fragmento posterior
  # tente "resetar" o ambiente (a proibição conta toda ocorrência ativa,
  # nunca só o estado final). Nunca imprime VALOR de diretiva.
  local unit="$1"
  local conteudo
  conteudo="$(soprolife_conteudo_efetivo_unit "$unit")"
  if [[ -z "$conteudo" ]]; then
    echo "ERRO: não foi possível obter a configuração efetiva de '$unit' via" \
      "'systemctl cat' (unit ausente, ilegível ou não carregada — rode" \
      "'systemctl daemon-reload' antes)." >&2
    return 1
  fi
  soprolife_validar_conteudo_unit "$unit (configuração efetiva)" "$conteudo"
}

soprolife_validar_conteudo_unit() {
  # Parser fail-closed compartilhado entre soprolife_validar_unit_update_data
  # (só o arquivo) e soprolife_validar_unit_update_data_efetiva (arquivo +
  # drop-ins via `systemctl cat`). $1 = rótulo p/ mensagens de erro,
  # $2 = conteúdo já lido (nunca ecoa VALOR de diretiva, só nome/linha).
  #
  # Falha fechada em: conteúdo vazio; linha ativa que não é Chave=Valor;
  # diretiva ativa fora de seção; Environment= com citação aberta ou par sem
  # 'NOME=VALOR'; Environment=/EnvironmentFile= fora de [Service]; requisito
  # ausente; qualquer atribuição ativa das variáveis proibidas.
  local rotulo="$1"
  local conteudo="$2"
  SOPROLIFE_UNIT_ALVO="$rotulo" SOPROLIFE_UNIT_CONTEUDO="$conteudo" python3 - <<'PY'
import os
import shlex
import sys

ALVO = os.environ.get("SOPROLIFE_UNIT_ALVO", "(unit)")
CONTEUDO = os.environ.get("SOPROLIFE_UNIT_CONTEUDO", "")

PROIBIDAS = ("CLOUDSDK_CONFIG", "SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION")
PROIBIDAS_UP = {nome.upper() for nome in PROIBIDAS}
ENV_FILE_OBRIGATORIO = "/opt/soprolife/secrets/m15.env"
INTERPRETADORES = ("SOPROLIFE_M15_PYTHON", "SOPROLIFE_MARKETING_PYTHON")


def erro(mensagem):
    print(f"ERRO: {mensagem}", file=sys.stderr)
    raise SystemExit(1)


def diretivas_ativas(texto):
    """Diretivas ATIVAS como (secao, chave, valor, linha).

    Ignora linhas vazias e comentários ('#' ou ';' no início, a única forma
    de comentário que o systemd reconhece — não existe comentário no fim de
    linha). Junta continuações terminadas em '\\'.
    """
    secao = None
    pendente = None  # (secao, corpo_parcial, linha_inicial)
    saida = []
    for numero, bruta in enumerate(texto.splitlines(), start=1):
        linha = bruta.strip()
        if pendente is None:
            if not linha or linha[0] in "#;":
                continue
            if linha.startswith("["):
                if not linha.endswith("]") or len(linha) < 3:
                    erro(f"unit {ALVO}: cabeçalho de seção malformado na linha {numero}.")
                secao = linha[1:-1].strip()
                continue
            if secao is None:
                erro(f"unit {ALVO}: diretiva fora de qualquer seção na linha {numero}.")
            corpo, inicial = linha, numero
        else:
            if not linha or linha[0] in "#;":
                erro(
                    f"unit {ALVO}: continuação de linha ambígua na linha {numero} "
                    "(comentário/linha vazia dentro de uma diretiva continuada)."
                )
            secao, parcial, inicial = pendente
            pendente = None
            corpo = f"{parcial} {linha}"
        if corpo.endswith("\\"):
            pendente = (secao, corpo[:-1].rstrip(), inicial)
            continue
        chave, sep, valor = corpo.partition("=")
        if not sep or not chave.strip():
            erro(f"unit {ALVO}: linha {inicial} não é 'Chave=Valor'.")
        saida.append((secao, chave.strip(), valor.strip(), inicial))
    if pendente is not None:
        erro(f"unit {ALVO}: continuação de linha ('\\') sem linha seguinte.")
    return saida


def nomes_de_environment(valor, linha):
    """Nomes atribuídos por uma diretiva Environment=.

    O systemd aceita vários pares por diretiva, com citação: devolve a lista
    de nomes; devolve None para Environment= vazia (que reseta a lista).
    """
    if valor == "":
        return None
    if "\\" in valor:
        erro(
            f"unit {ALVO}: Environment= contém sequência de escape ambígua "
            f"na linha {linha}; recusada por segurança."
        )
    try:
        tokens = shlex.split(valor)
    except ValueError:
        erro(f"unit {ALVO}: Environment= com citação não fechada na linha {linha}.")
    if not tokens:
        erro(f"unit {ALVO}: Environment= sem atribuição utilizável na linha {linha}.")
    nomes = []
    for token in tokens:
        nome, sep, _ = token.partition("=")
        if not sep or not nome.strip():
            erro(
                f"unit {ALVO}: Environment= malformada na linha {linha} "
                "(par sem a forma 'NOME=VALOR')."
            )
        nomes.append(nome.strip())
    return nomes


diretivas = diretivas_ativas(CONTEUDO)
if not any(secao == "Service" for secao, _c, _v, _l in diretivas):
    erro(f"unit {ALVO}: nenhuma diretiva ativa em [Service].")

env_efetivo = {}      # nome -> (valor, linha) com semântica de reset
env_qualquer = {}     # nome -> linha, em QUALQUER Environment= ativa
env_files = []        # caminhos acumulados
env_files_vistos = []
usuario = grupo = None

for secao, chave, valor, linha in diretivas:
    if chave in ("Environment", "EnvironmentFile") and secao != "Service":
        erro(
            f"unit {ALVO}: {chave}= na seção [{secao}] (linha {linha}) — "
            "ambiente só tem efeito em [Service]; recusado por segurança."
        )
    if chave == "Environment":
        nomes = nomes_de_environment(valor, linha)
        if nomes is None:
            env_efetivo.clear()
            continue
        pares = shlex.split(valor)
        for nome, par in zip(nomes, pares):
            env_efetivo[nome] = (par.partition("=")[2], linha)
            env_qualquer.setdefault(nome, linha)
    elif chave == "EnvironmentFile":
        if valor == "":
            env_files.clear()
            continue
        env_files.append(valor)
        env_files_vistos.append((valor, linha))
    elif secao == "Service" and chave == "User":
        usuario = valor  # systemd aplica a ÚLTIMA atribuição
    elif secao == "Service" and chave == "Group":
        grupo = valor

# Proibições: qualquer atribuição ativa conta, mesmo que resetada depois.
for nome, linha in env_qualquer.items():
    if nome.upper() in PROIBIDAS_UP:
        erro(
            f"unit {ALVO}: Environment= ativa atribui '{nome}' na linha {linha} "
            "(escape legado não pode estar ativo em produção)."
        )
for caminho, linha in env_files_vistos:
    alvo_arquivo = caminho.lstrip("-").upper()
    for proibida in PROIBIDAS_UP:
        if proibida in alvo_arquivo:
            erro(
                f"unit {ALVO}: EnvironmentFile= ativa na linha {linha} carrega "
                f"arquivo nomeado por '{proibida}' (escape legado)."
            )

# Requisitos: precisam estar ATIVOS em [Service], não apenas citados.
if usuario != "soprolife":
    erro(f"unit {ALVO}: [Service] não define 'User=soprolife' ativo (efetivo: {usuario!r}).")
if grupo != "soprolife":
    erro(f"unit {ALVO}: [Service] não define 'Group=soprolife' ativo (efetivo: {grupo!r}).")
if ENV_FILE_OBRIGATORIO not in env_files:
    erro(
        f"unit {ALVO}: [Service] não carrega 'EnvironmentFile={ENV_FILE_OBRIGATORIO}' "
        "ativo (obrigatório e não opcional)."
    )
for variavel in INTERPRETADORES:
    valor, _linha = env_efetivo.get(variavel, (None, None))
    if not valor:
        erro(
            f"unit {ALVO}: [Service] não define '{variavel}=' ativo com valor "
            "(interpretador implícito é bug conhecido do M23)."
        )
PY
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
