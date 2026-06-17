#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale não encontrado."
  exit 1
fi

TS_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"

if [ -z "$TS_IP" ]; then
  echo "Tailscale sem IP. Rode: sudo tailscale up"
  exit 1
fi

echo "Painel SoproLife - modo Tailscale privado"
echo "IP Tailscale: $TS_IP"
echo "Acesso:"
echo "http://$TS_IP:8765/painel-soprolife/"
echo
echo "Pressione Ctrl+C para desligar."
echo

python3 -m http.server 8765 --bind "$TS_IP"
