#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# check-access.sh — Verifica segurança do Painel SoproLife antes de compartilhar
# ---------------------------------------------------------------------------

check_ports() {
  echo "Verificando portas relevantes abertas..."
  echo
  ss -tulpen | grep -E '(:22|:80|:443|:8765|python|tailscale|LISTEN)' || true
  echo
  echo "Interpretação rápida:"
  echo "- :8765 com python3 = painel SoproLife"
  echo "- :22 = SSH; se aparecer, atenção antes de compartilhar"
  echo "- :80/:443 = servidor web comum; conferir antes de compartilhar"
  echo "- tailscaled = serviço normal do Tailscale"
}

check_private_files() {
  echo
  echo "Verificando arquivos privados dentro da pasta servida..."

  if find painel-soprolife/data-private -type f ! -name 'README.local.txt' 2>/dev/null | grep -q .; then
    echo "ATENÇÃO: existem arquivos privados dentro de painel-soprolife/data-private/."
    echo "Antes de compartilhar via Tailscale, mova segredos para ~/.config/soprolife/painel/."
    find painel-soprolife/data-private -type f ! -name 'README.local.txt' 2>/dev/null
  else
    echo "OK: nenhum arquivo privado sensível encontrado em painel-soprolife/data-private/."
  fi
}

validate_runtime_status() {
  echo
  echo "Verificando status local seguro..."

  if [ ! -f painel-soprolife/data/runtime-status.local.json ]; then
    echo "OK: runtime-status.local.json não existe; painel deve tratar como não configurado."
    return
  fi

  python3 - <<'PY'
from pathlib import Path
import json
import sys

path = Path("painel-soprolife/data/runtime-status.local.json")
data = json.loads(path.read_text(encoding="utf-8"))

allowed_root = {"googleSheets"}
allowed_google = {
    "configured",
    "name",
    "type",
    "statusLabel",
    "secretLocation",
    "safeToDisplay",
    "configValid",
    "lastCheckedAt"
}

if set(data.keys()) - allowed_root:
    print("ERRO: runtime-status.local.json tem chaves raiz não permitidas.")
    sys.exit(1)

google = data.get("googleSheets", {})
if not isinstance(google, dict):
    print("ERRO: googleSheets deve ser objeto.")
    sys.exit(1)

if set(google.keys()) - allowed_google:
    print("ERRO: runtime-status.local.json tem campos não permitidos.")
    sys.exit(1)

text = json.dumps(data, ensure_ascii=False).lower()
for forbidden in [
    "https://docs.google.com",
    "/spreadsheets/d/",
    "spreadsheet_id",
    "access_token",
    "refresh_token",
    "private_key",
    "client_secret",
    "client_email",
    "begin private key"
]:
    if forbidden in text:
        print("ERRO: possível segredo encontrado em runtime-status.local.json.")
        sys.exit(1)

print("OK: runtime-status.local.json contém apenas metadados seguros.")
PY
}

validate_dashboard_summary() {
  echo
  echo "Verificando resumo local seguro..."

  if [ ! -f painel-soprolife/data/resumo-dashboard.local.json ]; then
    echo "OK: resumo-dashboard.local.json não existe; painel usará resumo.json padrão."
    return
  fi

  python3 - <<'PY'
from pathlib import Path
import json
import sys

path = Path("painel-soprolife/data/resumo-dashboard.local.json")
data = json.loads(path.read_text(encoding="utf-8"))

source = data.get("source", {})
cards = data.get("cards", [])

if source.get("safeToDisplay") is not True:
    print("ERRO: resumo-dashboard.local.json não está marcado como seguro.")
    sys.exit(1)

if source.get("containsPersonalData") is not False:
    print("ERRO: resumo-dashboard.local.json pode conter dado pessoal.")
    sys.exit(1)

if source.get("containsHealthData") is not False:
    print("ERRO: resumo-dashboard.local.json pode conter dado clínico.")
    sys.exit(1)

if not isinstance(cards, list):
    print("ERRO: cards precisa ser lista.")
    sys.exit(1)

allowed_card_keys = {
    "totalLeads",
    "leadsNovos",
    "leadsAgendados",
    "leadsConcluidos",
    "clinicasCadastradas",
    "tarefasPendentes",
    "receitaPrevista",
    "receitaRecebida",
    "conteudosPlanejados",
    "eventosAgendados",
}

for card in cards:
    if not isinstance(card, dict):
        print("ERRO: card inválido no resumo local.")
        sys.exit(1)

    if set(card.keys()) - {"key", "label", "value"}:
        print("ERRO: card do resumo local contém campos não permitidos.")
        sys.exit(1)

    if card.get("key") not in allowed_card_keys:
        print("ERRO: card do resumo local contém chave não permitida.")
        sys.exit(1)

text = json.dumps(data, ensure_ascii=False).lower()
for forbidden in [
    "cpf",
    "telefone",
    "whatsapp",
    "paciente",
    "pedido médico",
    "pedido medico",
    "laudo",
    "diagnóstico",
    "diagnostico",
    "endereço",
    "endereco",
    "https://docs.google.com",
    "/spreadsheets/d/",
    "spreadsheet_id",
    "access_token",
    "refresh_token",
    "private_key",
    "client_secret",
    "client_email"
]:
    if forbidden in text:
        print("ERRO: possível dado sensível encontrado em resumo-dashboard.local.json.")
        sys.exit(1)

print("OK: resumo-dashboard.local.json contém apenas indicadores agregados seguros.")
PY
}

main() {
  check_ports
  check_private_files
  validate_runtime_status
  validate_dashboard_summary
}

main
