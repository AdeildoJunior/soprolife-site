#!/usr/bin/env bash
# SoproLife — M6 Quality Gate Seguro.
#
# Validação local ÚNICA para rodar antes de qualquer commit/push/deploy.
# 100% offline: sem SSH, sem VPS, sem rede, sem systemctl, sem credenciais,
# sem data-private. Não usa `set -e` (não fecha o terminal do usuário):
# acumula falhas, mostra resumo e retorna exit 0 (tudo OK) ou 1 (falhou).
#
# USO:  bash painel-soprolife/scripts/quality-gate-safe.sh
# Docs: painel-soprolife/docs/m6-quality-gate-safe.md

set -u
cd "$(dirname "$0")/../../" || exit 1

FAILS=0
INICIO=$(date +%s)

secao() { echo; echo "── $* ──"; }

# Executa um check; em falha mostra só o final da saída (sem segredos —
# todos os comandos daqui produzem saída segura por construção).
run() {
  local rotulo="$1"; shift
  printf '  %-55s' "$rotulo"
  local saida
  if saida=$("$@" 2>&1); then
    echo "OK"
  else
    echo "FALHOU"
    FAILS=$((FAILS + 1))
    printf '%s\n' "$saida" | tail -12 | sed 's/^/      | /'
  fi
}

echo "══ SoproLife Quality Gate (seguro/offline) — $(date '+%d/%m/%Y %H:%M') ══"

secao "1/10 Sintaxe JavaScript"
run "node --check js/app.js"                 node --check painel-soprolife/js/app.js
run "node --check js/operational-actions.js" node --check painel-soprolife/js/operational-actions.js
run "node --check js/b2b-actions.js"         node --check painel-soprolife/js/b2b-actions.js
run "node --check js/operational-brain.js"   node --check painel-soprolife/js/operational-brain.js
run "node --check js/daily-briefing.js"      node --check painel-soprolife/js/daily-briefing.js
run "node --check js/today-actions.js"       node --check painel-soprolife/js/today-actions.js
run "node --check js/espirometria-financeiro.js" node --check painel-soprolife/js/espirometria-financeiro.js

secao "2/10 Testes JS (M4 e M5)"
run "test-operational-actions (M4)" node painel-soprolife/scripts/test-operational-actions.js
run "test-b2b-actions (M5)"         node painel-soprolife/scripts/test-b2b-actions.js
run "test-operational-brain (M8)"   node painel-soprolife/scripts/test-operational-brain.js
run "test-daily-briefing (M9)"      node painel-soprolife/scripts/test-daily-briefing.js
run "test-today-actions (M10)"      node painel-soprolife/scripts/test-today-actions.js
run "test-espirometria-financeiro (M11)" node painel-soprolife/scripts/test-espirometria-financeiro.js
run "test-entrada-dados-ux (M12.1)"      node painel-soprolife/scripts/test-entrada-dados-ux.js

secao "3/10 Sintaxe Python"
run "py_compile generate-saude-operacional" python3 -m py_compile painel-soprolife/scripts/generate-saude-operacional.py
run "py_compile test-saude-operacional"     python3 -m py_compile painel-soprolife/scripts/test-saude-operacional.py
run "py_compile read-financeiro-lancamentos" python3 -m py_compile painel-soprolife/scripts/read-financeiro-lancamentos-adc.py
run "py_compile test-financeiro-summary"    python3 -m py_compile painel-soprolife/scripts/test-financeiro-summary.py

secao "4/10 Testes Python (M3 + M14.2)"
run "test-saude-operacional (M3)" python3 painel-soprolife/scripts/test-saude-operacional.py
run "test-financeiro-summary (M14.2)" python3 painel-soprolife/scripts/test-financeiro-summary.py

secao "5/10 Geração segura da Saúde Operacional"
# Escreve apenas o summary gitignored; sem rede, sem ADC, sem data-private.
run "generate --write --check-access-exit 0" \
  python3 painel-soprolife/scripts/generate-saude-operacional.py --write --check-access-exit 0

secao "6/10 JSONs demo commitáveis + summary de saúde"
run "json.tool saude-operacional.json" \
  python3 -m json.tool painel-soprolife/data/saude-operacional.json
run "json.tool cerebro-operacional.json" \
  python3 -m json.tool painel-soprolife/data/cerebro-operacional.json
run "json.tool briefing-diario.json" \
  python3 -m json.tool painel-soprolife/data/briefing-diario.json
run "json.tool saude-operacional-summary.local.json" \
  python3 -m json.tool painel-soprolife/data/saude-operacional-summary.local.json
# Todos os demos commitáveis de data/ (exclui *.local.json, que são gerados):
DEMO_JSON_FALHOU=0
while IFS= read -r f; do
  if ! python3 -m json.tool "$f" >/dev/null 2>&1; then
    echo "      | JSON inválido: $f"
    DEMO_JSON_FALHOU=1
  fi
done < <(find painel-soprolife/data -maxdepth 1 -name '*.json' ! -name '*.local.json')
printf '  %-55s' "demais demos commitáveis (data/*.json)"
if [ "$DEMO_JSON_FALHOU" -eq 0 ]; then echo "OK"; else echo "FALHOU"; FAILS=$((FAILS + 1)); fi

secao "7/10 check-access.sh (auditoria de segurança completa)"
run "check-access.sh" bash painel-soprolife/scripts/check-access.sh

secao "8/10 M14.3A — contratos, guardas estáticas e reconciliação"
run "test-guardas-estaticas (anti-regressão M14.3A)" python3 painel-soprolife/scripts/test-guardas-estaticas.py
run "test-contratos (fail-closed/patch/idempotência)" node painel-soprolife/scripts/test-contratos.js
run "test-writers-comportamentais (writers REAIS c/ mocks)" node painel-soprolife/scripts/test-writers-comportamentais.js
run "test-normalizacao (datas/IDs/enums)" python3 painel-soprolife/scripts/test-normalizacao.py
run "test-reconciliar-historico (estados/privacidade)" python3 painel-soprolife/scripts/test-reconciliar-historico.py
run "test-manual-abas (manifesto → .gs em dia)" python3 painel-soprolife/scripts/test-manual-abas.py
run "generate-manual-abas --check" python3 painel-soprolife/scripts/generate-manual-abas-gs.py --check

secao "9/10 Git — whitespace e guard rails de staging"
run "git diff --check" git diff --check

STAGED=$(git diff --cached --name-only)
if [ -z "$STAGED" ]; then
  echo "  (nada staged — guard rails de staging sem o que checar)"
else
  # NUNCA commitáveis: data-private, *.local.json e nomes com cara de segredo.
  GUARD_FALHOU=0
  while IFS= read -r f; do
    case "$f" in
      painel-soprolife/data-private/*)
        echo "  FALHOU: staged em data-private/: $f"; GUARD_FALHOU=1 ;;
      *.local.json)
        echo "  FALHOU: *.local.json staged: $f"; GUARD_FALHOU=1 ;;
    esac
    if printf '%s' "$f" | grep -qiE 'token|secret|credentials|application_default'; then
      echo "  FALHOU: nome de arquivo suspeito staged: $f"; GUARD_FALHOU=1
    fi
  done <<< "$STAGED"
  if [ "$GUARD_FALHOU" -eq 0 ]; then
    echo "  OK: nenhum arquivo proibido staged ($(printf '%s\n' "$STAGED" | wc -l) arquivo(s))"
  else
    FAILS=$((FAILS + 1))
    echo "  Use: git restore --staged <arquivo>  para tirar do stage."
  fi
fi

secao "10/10 Arquivos modificados (informativo — não bloqueia)"
_ST=$(git status --short)
if [ -z "$_ST" ]; then
  echo "  working tree limpo"
else
  printf '%s\n' "$_ST" | sed 's/^/  /'
fi

echo
echo "══ RESUMO ══"
DURACAO=$(( $(date +%s) - INICIO ))
if [ "$FAILS" -gt 0 ]; then
  echo "  ✗ QUALITY GATE FALHOU — $FAILS check(s) com problema (${DURACAO}s)."
  echo "  Corrigir ANTES de commit/push/deploy."
  exit 1
fi
echo "  ✓ QUALITY GATE PASSOU — todos os checks OK (${DURACAO}s)."
echo "  Liberado para commit (deploy continua exigindo revisão + fluxo da VPS)."
exit 0
