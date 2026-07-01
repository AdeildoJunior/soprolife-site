#!/usr/bin/env bash
# install-vps-auto-update.sh
#
# Instala o timer systemd de atualização automática do Painel SoproLife na VPS.
# Deve ser executado como root na VPS.
#
# Uso (na VPS, dentro do repositório):
#   bash painel-soprolife/scripts/install-vps-auto-update.sh
#
# Pré-requisitos:
#   - systemd disponível (sudo systemctl)
#   - repo clonado em /opt/soprolife/soprolife-site
#   - Google Sheets ADC configurado (opcional — o timer roda mesmo sem ADC)
#
# Não contém segredos, tokens, URLs privadas ou IDs de planilha.

set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

# ---------------------------------------------------------------------------
# Variáveis
# ---------------------------------------------------------------------------
SYSTEMD_SRC="painel-soprolife/systemd"
SYSTEMD_DST="/etc/systemd/system"
SERVICE_NAME="soprolife-update-data"
REPO_DIR="$(pwd)"

# ---------------------------------------------------------------------------
# Verificações iniciais
# ---------------------------------------------------------------------------
echo "======================================================="
echo " SoproLife — Instalador do auto-update (systemd)"
echo "======================================================="
echo " Diretório do repo: $REPO_DIR"
echo " Destino systemd:   $SYSTEMD_DST"
echo

if [ "$(id -u)" -ne 0 ]; then
  echo "ERRO: este script deve ser executado como root."
  echo "  Execute: sudo bash painel-soprolife/scripts/install-vps-auto-update.sh"
  exit 1
fi

if ! command -v systemctl &>/dev/null; then
  echo "ERRO: systemctl não encontrado — systemd não disponível neste ambiente."
  exit 1
fi

if [ "$REPO_DIR" != "/opt/soprolife/soprolife-site" ]; then
  echo "AVISO: o script está sendo executado fora de /opt/soprolife/soprolife-site."
  echo "  Diretório atual: $REPO_DIR"
  echo "  Os arquivos systemd apontam para /opt/soprolife/soprolife-site."
  echo
  read -r -p "Deseja continuar mesmo assim? (s/N) " _resp
  if [ "$_resp" != "s" ] && [ "$_resp" != "S" ]; then
    echo "Instalação cancelada."
    exit 0
  fi
fi

for _f in "$SYSTEMD_SRC/${SERVICE_NAME}.service" "$SYSTEMD_SRC/${SERVICE_NAME}.timer"; do
  if [ ! -f "$_f" ]; then
    echo "ERRO: arquivo não encontrado: $_f"
    echo "  Clone o repositório atualizado primeiro."
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# Instalação
# ---------------------------------------------------------------------------
echo "1. Copiando arquivos systemd..."
cp -v "$SYSTEMD_SRC/${SERVICE_NAME}.service" "$SYSTEMD_DST/${SERVICE_NAME}.service"
cp -v "$SYSTEMD_SRC/${SERVICE_NAME}.timer"   "$SYSTEMD_DST/${SERVICE_NAME}.timer"
echo

echo "2. Recarregando daemon systemd..."
systemctl daemon-reload
echo "   OK."
echo

echo "3. Habilitando timer (inicia automaticamente no boot)..."
systemctl enable "${SERVICE_NAME}.timer"
echo

echo "4. Iniciando timer agora..."
systemctl start "${SERVICE_NAME}.timer"
echo

echo "5. Status do timer:"
systemctl status "${SERVICE_NAME}.timer" --no-pager -l || true
echo

echo "6. Timers SoproLife ativos:"
systemctl list-timers --no-pager | grep soprolife || echo "  (nenhum encontrado — verifique manualmente)"
echo

# ---------------------------------------------------------------------------
# Verificação pós-instalação
# ---------------------------------------------------------------------------
echo "======================================================="
echo " Instalação concluída."
echo "======================================================="
echo
echo "Para verificar logs de atualização:"
echo "  journalctl -u ${SERVICE_NAME}.service -f"
echo "  journalctl -u ${SERVICE_NAME}.service --since '1 hour ago'"
echo
echo "Para forçar atualização imediata:"
echo "  systemctl start ${SERVICE_NAME}.service"
echo
echo "Para verificar ADC e pré-requisitos Google Sheets:"
echo "  cd $REPO_DIR"
echo "  painel-soprolife/scripts/check-vps-google-adc.sh"
echo
echo "Para desinstalar:"
echo "  systemctl stop ${SERVICE_NAME}.timer"
echo "  systemctl disable ${SERVICE_NAME}.timer"
echo "  rm $SYSTEMD_DST/${SERVICE_NAME}.service $SYSTEMD_DST/${SERVICE_NAME}.timer"
echo "  systemctl daemon-reload"
