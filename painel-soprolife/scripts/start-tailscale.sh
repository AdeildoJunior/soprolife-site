#!/usr/bin/env bash
set -euo pipefail

# Entry point legado preservado para explicar a recusa a quem ainda o usa.
# O servidor estático anterior ignorava o gate de sessão e de arquivos.
# Não configurar Tailscale, iniciar serviço ou abrir porta por este caminho.
echo "ERRO: launcher HTTP estático via Tailscale desabilitado." >&2
echo "Use o acesso HTTPS privado já provisionado com o servidor autenticado." >&2
echo "Para desenvolvimento local: bash painel-soprolife/scripts/start-local.sh" >&2
echo "Consulte painel-soprolife/README.md; este script não altera serviços ou rede." >&2
exit 1
