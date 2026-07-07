#!/usr/bin/env bash
# I1 — Validação LOCAL antes de qualquer janela na VPS.
#
# 100% local e somente-leitura: sem SSH, sem VPS, sem escrita alguma.
# Imprime GO/NO-GO local (item 1 do i1-go-no-go-checklist.md).
#
# USO: bash painel-soprolife/scripts/i1-validate-local-before-vps.sh [--allow-dirty]
#   --allow-dirty: working tree sujo vira WARN (modo REVISÃO DE ETAPA,
#                  antes do commit). Para a JANELA REAL, rodar SEM a flag —
#                  tree sujo é FAIL/NO-GO.
# Exit: 0 = GO local | 1 = NO-GO (há FAIL).

set -u

BRANCH_ESPERADA="painel-soprolife-v01"
ALLOW_DIRTY=0
[ "${1:-}" = "--allow-dirty" ] && ALLOW_DIRTY=1
FAILS=0; WARNS=0
fail() { echo "  FAIL: $*"; FAILS=$((FAILS+1)); }
warn() { echo "  WARN: $*"; WARNS=$((WARNS+1)); }
ok()   { echo "  OK:   $*"; }

echo "== I1 validação local pré-VPS — $(date -Is) =="

echo
echo "[1] Branch"
_branch=$(git branch --show-current 2>/dev/null)
if [ "$_branch" = "$BRANCH_ESPERADA" ]; then
  ok "branch $_branch"
else
  fail "branch atual '$_branch' (esperada: $BRANCH_ESPERADA)"
fi

echo
echo "[2] Working tree (para a JANELA REAL deve estar limpo)"
_st=$(git status --short 2>/dev/null)
if [ -z "$_st" ]; then
  ok "working tree limpo"
elif [ "$ALLOW_DIRTY" = "1" ]; then
  warn "working tree sujo — aceito em modo --allow-dirty (revisão de etapa):"
  printf '%s\n' "$_st" | sed 's/^/    /'
else
  fail "working tree com pendências (commitar/descartar antes da janela; para revisão use --allow-dirty):"
  printf '%s\n' "$_st" | sed 's/^/    /'
fi

echo
echo "[3] Artefatos I1 presentes"
ARTEFATOS=(
  "painel-soprolife/systemd/soprolife-update-data.service.example"
  "painel-soprolife/systemd/soprolife-update-data.timer.example"
  "painel-soprolife/docs/i1-timer-sem-root-planejamento.md"
  "painel-soprolife/docs/i1-timer-sem-root-execucao.md"
  "painel-soprolife/docs/i1-precheck-vps.md"
  "painel-soprolife/docs/i1-go-no-go-checklist.md"
  "painel-soprolife/docs/i1-execucao-assistida-f1-f5.md"
  "painel-soprolife/scripts/i1-precheck-vps-readonly.sh"
)
for f in "${ARTEFATOS[@]}"; do
  if [ -f "$f" ]; then ok "$f"; else fail "ausente: $f"; fi
done

echo
echo "[4] Sintaxe dos scripts I1 (bash -n)"
for s in painel-soprolife/scripts/i1-*.sh; do
  if bash -n "$s" 2>/dev/null; then ok "bash -n $s"; else fail "sintaxe inválida: $s"; fi
done

echo
echo "[5] Padrões sensíveis nos artefatos I1 (docs/templates/scripts)"
# Padrões com comprimento mínimo para NÃO casar com os regexes do
# sanitizador do precheck (que contêm os prefixos como literais).
SENSIVEIS='AIza[A-Za-z0-9_-]{10,}|ya29\.[A-Za-z0-9._-]{5,}|AKfycb[A-Za-z0-9_-]{10,}|script\.google\.com/macros/s/[A-Za-z0-9_-]{10,}|/spreadsheets/d/[A-Za-z0-9_-]{15,}|Bearer [A-Za-z0-9._-]{8,}|\b100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b'
_hits=$(grep -rnE "$SENSIVEIS" \
  painel-soprolife/docs/i1-*.md \
  painel-soprolife/systemd/*.example \
  painel-soprolife/scripts/i1-*.sh 2>/dev/null)
if [ -n "$_hits" ]; then
  fail "padrão sensível encontrado (linha NÃO exibida — abrir o arquivo indicado):"
  printf '%s\n' "$_hits" | cut -d: -f1,2 | sed 's/^/    /'
else
  ok "nenhum token/URL/ID/IP sensível nos artefatos I1"
fi

echo
echo "[6] Checkpoints I1 esperados (tags)"
for t in checkpoint-i1-planejamento-timer-sem-root-v01 \
         checkpoint-i1-templates-timer-sem-root-v01 \
         checkpoint-i1-precheck-vps-readonly-v01; do
  if git tag -l "$t" | grep -q .; then ok "tag $t"; else warn "tag ausente: $t"; fi
done

echo
echo "== RESUMO — FAILs: $FAILS | WARNs: $WARNS =="
if [ "$FAILS" -gt 0 ]; then
  echo "  NO-GO local: resolver FAILs antes de preencher o checklist GO/NO-GO."
  exit 1
fi
echo "  GO local. Próximo: precheck da VPS + checklist i1-go-no-go-checklist.md."
exit 0
