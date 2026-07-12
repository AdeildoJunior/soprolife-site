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

# Cópia atômica: valida o JSON antes e nunca substitui um snapshot válido
# por um arquivo truncado/corrompido (contrato de frescor M14.3A.1).
if ! python3 -m json.tool "$PRIVATE_OUT" >/dev/null 2>&1; then
  echo "ERRO: $PRIVATE_OUT não é JSON válido — snapshot atual preservado."
  exit 1
fi
TMP_OUT="$(mktemp "${PUBLIC_OUT}.XXXXXX.tmp")"
cp "$PRIVATE_OUT" "$TMP_OUT"
chmod 600 "$TMP_OUT"
mv "$TMP_OUT" "$PUBLIC_OUT"
echo "OK: marketing-seo sincronizado → $PUBLIC_OUT"
