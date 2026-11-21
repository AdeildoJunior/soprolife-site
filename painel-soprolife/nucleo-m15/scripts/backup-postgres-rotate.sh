#!/usr/bin/env bash
# backup-postgres-rotate.sh — Retenção rotativa dos backups DIÁRIOS do M15.
#
# NÃO é um segundo mecanismo de dump: apenas limpa os dumps periódicos antigos
# gerados pelo timer soprolife-backup-postgres. É deliberadamente restrito à
# pasta .../backups/m15/daily e recusa qualquer outro caminho — assim nunca
# toca em backups pré-deploy (.../m15/<STAMP>/) nem nos manuais (.../m15/manual/).
#
# Uso:
#   backup-postgres-rotate.sh <dir> [dias_retencao]
#   (padrão: 30 dias). Remove *.dump e *.sha256 mais antigos que N dias,
#   apenas no primeiro nível da pasta, nunca subdiretórios.
set -Eeuo pipefail
umask 077

readonly DIR="${1:-}"
readonly RETENTION="${2:-30}"

[[ -n "$DIR" ]] || { echo "ERRO: diretório não informado" >&2; exit 2; }

# Guarda de caminho: só a pasta de backups diários pode ser podada.
case "$DIR" in
  */backups/m15/daily) : ;;
  *) echo "ERRO: diretório de rotação inesperado (esperado .../backups/m15/daily): $DIR" >&2; exit 2 ;;
esac

[[ "$RETENTION" =~ ^[0-9]+$ ]] || { echo "ERRO: retenção inválida: $RETENTION" >&2; exit 2; }

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

if ! $SUDO test -d "$DIR"; then
  echo "Rotação: pasta $DIR ainda não existe — nada a fazer."
  exit 0
fi

echo "Rotação M15: removendo dumps diários com mais de ${RETENTION} dias em $DIR"
# -print mostra cada arquivo removido no journal; -delete remove em seguida.
removed="$($SUDO find "$DIR" -maxdepth 1 -type f \
  \( -name '*.dump' -o -name '*.sha256' \) \
  -mtime +"$RETENTION" -print -delete | wc -l)"
echo "Rotação M15 concluída: ${removed} arquivo(s) removido(s) (retenção=${RETENTION}d)."
