#!/usr/bin/env bash
# SoproLife — M7 Prontidão LOCAL para deploy (read-only, sem SSH/VPS).
#
# Responde: "esta máquina está pronta para iniciar a janela de deploy?"
# Roda o quality gate + verificações de git PURAMENTE LOCAIS (sem fetch).
#
# USO:
#   bash painel-soprolife/scripts/m7-local-deploy-readiness.sh                # modo deploy (estrito)
#   bash painel-soprolife/scripts/m7-local-deploy-readiness.sh --allow-dirty  # modo revisão (tree sujo = aviso)
# Exit: 0 = GO local | 1 = NO-GO.

set -u
cd "$(dirname "$0")/../../" || exit 1

BRANCH_ESPERADA="painel-soprolife-v01"
ALLOW_DIRTY=0
[ "${1:-}" = "--allow-dirty" ] && ALLOW_DIRTY=1

FAILS=0; WARNS=0
fail() { echo "  FAIL: $*"; FAILS=$((FAILS+1)); }
warn() { echo "  WARN: $*"; WARNS=$((WARNS+1)); }
ok()   { echo "  OK:   $*"; }

echo "══ M7 — prontidão local para deploy — $(date '+%d/%m/%Y %H:%M') ══"

echo
echo "[1] Branch"
_b=$(git branch --show-current 2>/dev/null)
[ "$_b" = "$BRANCH_ESPERADA" ] && ok "branch $_b" || fail "branch '$_b' (esperada: $BRANCH_ESPERADA)"

echo
echo "[2] Working tree (deploy exige tudo commitado)"
_st=$(git status --short 2>/dev/null)
if [ -z "$_st" ]; then
  ok "working tree limpo"
elif [ "$ALLOW_DIRTY" = "1" ]; then
  warn "tree sujo — aceito em --allow-dirty (revisão de etapa):"
  printf '%s\n' "$_st" | sed 's/^/    /'
else
  fail "tree com pendências (commitar antes do deploy; para revisão use --allow-dirty):"
  printf '%s\n' "$_st" | sed 's/^/    /'
fi

echo
echo "[3] HEAD e checkpoint"
git log --oneline --decorate -1 | sed 's/^/  /'
if git tag --points-at HEAD | grep -q '^checkpoint-'; then
  ok "HEAD tagueado ($(git tag --points-at HEAD | tr '\n' ' '))"
else
  warn "HEAD sem tag checkpoint-* — taguear antes do deploy"
fi

echo
echo "[4] Sincronia com origin (só refs locais — sem rede)"
if git rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
  _ahead=$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo "?")
  _behind=$(git rev-list --count 'HEAD..@{u}' 2>/dev/null || echo "?")
  [ "$_ahead" = "0" ] && ok "sem commits à frente do origin (push em dia)" \
                      || warn "$_ahead commit(s) locais ainda não pushados — push antes da janela"
  [ "$_behind" = "0" ] && ok "sem commits atrás do origin (segundo o último fetch)" \
                       || warn "$_behind commit(s) atrás do origin — investigar antes do deploy"
else
  warn "branch sem upstream configurado"
fi

echo
echo "[5] Quality Gate (M6)"
if bash painel-soprolife/scripts/quality-gate-safe.sh >/dev/null 2>&1; then
  ok "quality-gate-safe.sh PASSOU"
else
  fail "quality-gate-safe.sh FALHOU — rodar direto para ver os detalhes"
fi

echo
echo "══ RESUMO — FAILs: $FAILS | WARNs: $WARNS ══"
if [ "$FAILS" -gt 0 ]; then
  echo "  NO-GO local: resolver FAILs antes da janela de deploy."
  exit 1
fi
echo "  GO local. Próximo: revisão do ChatGPT sobre docs/m7-deploy-pack-vps.md"
echo "  e então a janela (pré-check remoto read-only → pull --ff-only)."
exit 0
