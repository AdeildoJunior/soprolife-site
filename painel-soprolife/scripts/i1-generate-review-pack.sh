#!/usr/bin/env bash
# I1 — Gera o pacote de revisão padrão para o GPT (skill soprolife-review-pack).
#
# 100% local: sem SSH, sem VPS. Saída no padrão do projeto: um .tar.gz em
# ~/Documents/SoproLife/_REVISOES_GPT contendo:
#   review.txt              (status/log/diff stat/diff check/resumo)
#   full.diff               (git diff completo dos tracked)
#   untracked-content.txt   (lista + conteúdo integral dos untracked)
#   changed-files/          (cópia dos arquivos alterados/untracked, na
#                            mesma estrutura de diretórios)
#
# Segurança: staging em mktemp (limpo via trap), umask 077 (tar.gz 600),
# varredura de padrões sensíveis ANTES de empacotar — hit = quarentena do
# staging (nada é entregue) e exit 1.
#
# USO: bash painel-soprolife/scripts/i1-generate-review-pack.sh [rotulo]
#      (rótulo padrão: i1-etapa)
# Exit: 0 = pacote pronto | 1 = falha/quarentena.

set -u
umask 077

ROTULO="${1:-i1-etapa}"
TS="$(date +%Y%m%d-%H%M%S)"
PACK_DIR="$HOME/Documents/SoproLife/_REVISOES_GPT"
PACK_TGZ="$PACK_DIR/revisao-${ROTULO}-${TS}.tar.gz"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$PACK_DIR" "$STAGE/changed-files"

# ── review.txt ───────────────────────────────────────────────────────────────
{
  echo "# Pacote de revisão — ${ROTULO}"
  echo "gerado em: $(date -Is)"
  echo "branch: $(git branch --show-current 2>/dev/null)"
  echo "HEAD: $(git log --oneline -1 2>/dev/null)"
  echo "tags I1: $(git tag -l 'checkpoint-i1-*' | tr '\n' ' ')"
  echo
  echo "## git status --short"
  git status --short
  echo
  echo "## git log --oneline --decorate -8"
  git log --oneline --decorate -8
  echo
  echo "## git diff --stat"
  git diff --stat
  echo
  echo "## git diff --check"
  git diff --check && echo "(sem problemas de whitespace)"
  echo
  echo "## untracked (lista)"
  git ls-files --others --exclude-standard
} > "$STAGE/review.txt"

# ── full.diff ────────────────────────────────────────────────────────────────
git diff > "$STAGE/full.diff"

# ── untracked-content.txt + changed-files/ ──────────────────────────────────
: > "$STAGE/untracked-content.txt"
while IFS= read -r f; do
  [ -f "$f" ] || continue
  {
    echo "===================================================================="
    echo "### UNTRACKED: $f"
    echo "===================================================================="
    cat "$f"
    echo
  } >> "$STAGE/untracked-content.txt"
  mkdir -p "$STAGE/changed-files/$(dirname "$f")"
  cp "$f" "$STAGE/changed-files/$f"
done < <(git ls-files --others --exclude-standard)

while IFS= read -r f; do
  [ -f "$f" ] || continue
  mkdir -p "$STAGE/changed-files/$(dirname "$f")"
  cp "$f" "$STAGE/changed-files/$f"
done < <(git diff --name-only)

# ── Varredura de segurança do staging ANTES de empacotar ─────────────────────
SENSIVEIS='AIza[A-Za-z0-9_-]{10,}|ya29\.[A-Za-z0-9._-]{5,}|AKfycb[A-Za-z0-9_-]{10,}|script\.google\.com/macros/s/[A-Za-z0-9_-]{10,}|/spreadsheets/d/[A-Za-z0-9_-]{15,}|Bearer [A-Za-z0-9._-]{8,}|\b100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b'
if grep -rnE "$SENSIVEIS" "$STAGE" >/dev/null 2>&1; then
  QUAR="$PACK_DIR/QUARENTENA-${ROTULO}-${TS}"
  mv "$STAGE" "$QUAR"
  trap - EXIT
  echo "ERRO: padrão sensível detectado no pacote — staging movido para quarentena:"
  echo "  $QUAR"
  echo "  Arquivos com hit:"
  grep -rlE "$SENSIVEIS" "$QUAR" | sed 's/^/    /'
  echo "  Revisar manualmente ANTES de qualquer envio. Nada foi entregue."
  exit 1
fi

# ── Empacotar ────────────────────────────────────────────────────────────────
tar -czf "$PACK_TGZ" -C "$STAGE" review.txt full.diff untracked-content.txt changed-files

echo "Pacote de revisão pronto (sem padrões sensíveis):"
echo "  $PACK_TGZ"
echo "Conteúdo: review.txt, full.diff, untracked-content.txt, changed-files/"
echo "Anexar ao GPT junto com o relatório do precheck da VPS, se houver."
exit 0
