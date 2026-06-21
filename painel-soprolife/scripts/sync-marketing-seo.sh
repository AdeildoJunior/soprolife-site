#!/usr/bin/env bash
# sync-marketing-seo.sh — Sincroniza dados de Marketing & SEO para o painel

set -euo pipefail
cd "$(dirname "$0")/../../" || exit 1

PRIVATE_OUT="$HOME/.config/soprolife/painel/marketing-seo.json"
PUBLIC_OUT="painel-soprolife/data/marketing-seo.local.json"

if [ ! -f "$PRIVATE_OUT" ]; then
  echo "INFO: $PRIVATE_OUT não existe. Execute read-marketing-seo-adc.py --write primeiro."
  exit 0
fi

cp "$PRIVATE_OUT" "$PUBLIC_OUT"
chmod 600 "$PUBLIC_OUT"
echo "OK: marketing-seo sincronizado → $PUBLIC_OUT"
