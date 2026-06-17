#!/usr/bin/env bash
set -euo pipefail

echo "Verificando portas relevantes abertas..."
echo

ss -tulpen | grep -E '(:22|:80|:443|:8765|python|tailscale|LISTEN)' || true

echo
echo "Interpretação rápida:"
echo "- :8765 com python3 = painel SoproLife"
echo "- :22 = SSH; se aparecer, atenção antes de compartilhar"
echo "- :80/:443 = servidor web comum; conferir antes de compartilhar"
echo "- tailscaled = serviço normal do Tailscale"


echo
echo "Verificando arquivos privados dentro da pasta servida..."

if find painel-soprolife/data-private -type f ! -name 'README.local.txt' 2>/dev/null | grep -q .; then
  echo "ATENÇÃO: existem arquivos privados dentro de painel-soprolife/data-private/."
  echo "Antes de compartilhar via Tailscale, mova segredos para ~/.config/soprolife/painel/."
  find painel-soprolife/data-private -type f ! -name 'README.local.txt' 2>/dev/null
else
  echo "OK: nenhum arquivo privado sensível encontrado em painel-soprolife/data-private/."
fi
