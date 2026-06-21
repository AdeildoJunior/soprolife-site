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

check_extra_network_services() {
  echo
  echo "Verificando serviços extras visíveis na rede..."

  local ss_output
  ss_output=$(ss -tulpen 2>/dev/null) || true

  local found=0

  for pattern in "wsdd" "passim" ":3702" ":27500"; do
    if echo "$ss_output" | grep -q "$pattern"; then
      if [ "$found" -eq 0 ]; then
        echo "ATENÇÃO: serviços extras detectados (não fazem parte do painel SoproLife):"
        found=1
      fi
      echo "  - padrão encontrado: $pattern"
    fi
  done

  if [ "$found" -eq 0 ]; then
    echo "OK: nenhum serviço extra relevante detectado."
  else
    echo
    echo "Esses serviços não são necessariamente um problema, mas merecem"
    echo "revisão antes de compartilhar o painel via Tailscale."
    echo
    echo "Referência:"
    echo "  wsdd    = Web Services Dynamic Discovery (descoberta de dispositivos Windows/Samba)"
    echo "  passim  = cache de pacotes local do sistema (Fedora/systemd)"
    echo "  :3702   = porta UDP do protocolo WS-Discovery"
    echo "  :27500  = porta usada por alguns serviços de descoberta/multicast"
  fi
}

check_private_files() {
  echo
  echo "Verificando arquivos privados dentro da pasta servida..."

  local private_files
  private_files=$(find painel-soprolife/data-private -type f ! -name 'README.local.txt' 2>/dev/null)

  if [ -z "$private_files" ]; then
    echo "OK: nenhum arquivo privado encontrado em painel-soprolife/data-private/."
    return
  fi

  echo "INFO: arquivos privados locais presentes (uso esperado para painel local/Tailscale):"
  echo "$private_files" | while IFS= read -r f; do
    # Verifica se o arquivo está gitignored
    if git check-ignore -q "$f" 2>/dev/null; then
      echo "  OK (gitignored): $f"
    else
      echo "  ATENÇÃO (NÃO gitignored): $f — RISCO DE COMMIT COM DADOS PESSOAIS"
    fi
  done

  echo
  echo "IMPORTANTE: esses arquivos contêm dados de pacientes e são para uso local APENAS."
  echo "Eles estão gitignored e nunca devem ser enviados ao GitHub."
}

validate_followup_pacientes() {
  echo
  echo "Verificando follow-up de pacientes..."

  local private_file="painel-soprolife/data-private/followup-pacientes.local.json"
  local summary_file="painel-soprolife/data/followup-pacientes-summary.local.json"

  # Verifica arquivo privado
  if [ -f "$private_file" ]; then
    if git check-ignore -q "$private_file" 2>/dev/null; then
      echo "  OK (gitignored): $private_file"
    else
      echo "  ERRO CRÍTICO: $private_file NÃO está gitignored!"
      echo "  Execute: git rm --cached \"$private_file\" antes de qualquer commit."
    fi

    python3 - <<'PY'
from pathlib import Path
import json
import sys

path = Path("painel-soprolife/data-private/followup-pacientes.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler JSON privado: {exc}")
    sys.exit(1)

# Não imprime nenhum dado pessoal — apenas valida estrutura e conta
espi = data.get("espirometria", [])
cons = data.get("consultas", [])

blocked_keys = {"cpf", "rg", "data_nascimento", "endereco", "endereço",
                "observacao_privada", "pedido_medico", "laudo", "diagnostico",
                "senha", "token"}

errors = 0
for i, rec in enumerate(espi + cons):
    for k in rec.keys():
        if k.lower() in blocked_keys:
            print(f"  ERRO: campo proibido '{k}' no registro {i+1}.")
            errors += 1

if errors == 0:
    print(f"  OK: {len(espi)} espirometrias, {len(cons)} consultas. Nenhum campo proibido.")
else:
    print(f"  {errors} erros encontrados.")
    sys.exit(1)
PY
  else
    echo "  INFO: $private_file não existe (ainda não gerado)."
    echo "        Execute: python3 painel-soprolife/scripts/generate-followup-pacientes.py --write"
  fi

  # Verifica resumo público
  if [ -f "$summary_file" ]; then
    if git check-ignore -q "$summary_file" 2>/dev/null; then
      echo "  OK (gitignored): $summary_file"
    else
      echo "  ATENÇÃO: $summary_file não está gitignored."
    fi

    python3 - <<'PY'
from pathlib import Path
import json
import sys

path = Path("painel-soprolife/data/followup-pacientes-summary.local.json")
data = json.loads(path.read_text(encoding="utf-8"))

if data.get("safeToDisplay") is not True:
    print("  ERRO: followup-pacientes-summary não marcado como seguro.")
    sys.exit(1)
if data.get("containsPersonalData") is not False:
    print("  ERRO: followup-pacientes-summary pode conter dado pessoal.")
    sys.exit(1)

espi = data.get("espirometria", {})
cons = data.get("consultas", {})
print(f"  OK: resumo seguro — espirometrias={espi.get('total',0)}, consultas={cons.get('total',0)}.")
PY
  else
    echo "  INFO: $summary_file não existe."
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
    # Indicadores agregados de atendimento/CRM — valores numéricos totalizados pelo Apps Script;
    # nunca contêm nome, telefone, CPF ou dado clínico individual de paciente.
    "pacientesEmAcompanhamento",
    "examesEspirometriaRealizados",
    "teleconsultasRealizadas",
    "followupsPendentes",
    "lembretesWhatsAppPendentes",
    "recorrenciasAtivas",
    "consultasPrevistas",
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

# Varrer source e valores dos cards por dados sensíveis.
# Labels são strings fixas definidas em sync-dashboard-summary.sh e não provêm de dados
# externos — palavras como "paciente" e "whatsapp" em labels de indicadores agregados
# são rótulos institucionais, não dados pessoais.
scan_text = (
    json.dumps(source, ensure_ascii=False).lower() + " " +
    json.dumps([c.get("value") for c in cards], ensure_ascii=False).lower()
)
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
    if forbidden in scan_text:
        print("ERRO: possível dado sensível encontrado em resumo-dashboard.local.json.")
        sys.exit(1)

print("OK: resumo-dashboard.local.json contém apenas indicadores agregados seguros.")
PY
}

validate_crm_clinicas() {
  echo
  echo "Verificando CRM Clínicas local seguro..."

  if [ ! -f painel-soprolife/data/crm-clinicas.local.json ]; then
    echo "OK: crm-clinicas.local.json não existe; painel usará crm-clinicas.json de exemplo."
    return
  fi

  python3 - <<'PY'
from pathlib import Path
import json
import re
import sys

path = Path("painel-soprolife/data/crm-clinicas.local.json")
data = json.loads(path.read_text(encoding="utf-8"))

source = data.get("source", {})
clinicas = data.get("clinicas", [])

if source.get("safeToDisplay") is not True:
    print("ERRO: crm-clinicas.local.json não está marcado como seguro.")
    sys.exit(1)

if source.get("containsPersonalData") is not False:
    print("ERRO: crm-clinicas.local.json pode conter dado pessoal.")
    sys.exit(1)

if source.get("containsHealthData") is not False:
    print("ERRO: crm-clinicas.local.json pode conter dado clínico.")
    sys.exit(1)

if not isinstance(clinicas, list):
    print("ERRO: clinicas precisa ser lista.")
    sys.exit(1)

ALLOWED_FIELDS = {
    "clinica_id", "nome_clinica", "bairro", "regiao", "tipo_clinica",
    "etapa", "ultima_interacao", "proxima_acao", "data_proxima_acao",
    "responsavel", "prioridade",
}
BLOCKED_FIELDS = {
    "observacao", "observação", "cpf", "telefone", "celular", "email",
    "e-mail", "whatsapp", "nome_paciente", "paciente", "pedido_medico",
    "laudo", "diagnostico", "diagnóstico", "endereco", "endereço",
}
FORBIDDEN_TERMS = [
    "cpf", "telefone", "celular", "whatsapp",
    "pedido médico", "pedido medico", "laudo",
    "diagnóstico", "diagnostico", "endereço", "endereco",
    "data de nascimento", "nome completo",
    "https://docs.google.com", "/spreadsheets/d/",
    "access_token", "refresh_token", "private_key",
    "client_secret", "client_email",
]
_CPF_RE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

for i, record in enumerate(clinicas, start=1):
    if not isinstance(record, dict):
        print(f"ERRO: registro {i} não é um objeto.")
        sys.exit(1)

    extra = set(record.keys()) - ALLOWED_FIELDS
    if extra:
        print(f"ERRO: campos não permitidos no registro {i}: {', '.join(sorted(extra))}")
        sys.exit(1)

    for field in record.keys():
        if field.lower() in BLOCKED_FIELDS:
            print(f"ERRO: campo proibido '{field}' no registro {i}.")
            sys.exit(1)

    record_text = json.dumps(record, ensure_ascii=False).lower()
    for term in FORBIDDEN_TERMS:
        if term in record_text:
            print(f"ERRO: termo proibido '{term}' detectado no registro {i}.")
            sys.exit(1)
    if _CPF_RE.search(record_text):
        print(f"ERRO: padrão de CPF detectado no registro {i}.")
        sys.exit(1)
    if _FONE_RE.search(record_text):
        print(f"ERRO: padrão de telefone detectado no registro {i}.")
        sys.exit(1)
    if _EMAIL_RE.search(record_text):
        print(f"ERRO: padrão de e-mail detectado no registro {i}.")
        sys.exit(1)

print(f"OK: crm-clinicas.local.json contém apenas dados institucionais seguros ({len(clinicas)} registros).")
PY
}

main() {
  check_ports
  check_extra_network_services
  check_private_files
  validate_runtime_status
  validate_dashboard_summary
  validate_crm_clinicas
  validate_followup_pacientes
}

main
