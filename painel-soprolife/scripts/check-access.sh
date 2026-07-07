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

validate_followup_clinicas() {
  echo
  echo "Verificando follow-up B2B de clínicas..."

  local private_file="painel-soprolife/data-private/followup-clinicas.local.json"
  local summary_file="painel-soprolife/data/followup-clinicas-summary.local.json"

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

path = Path("painel-soprolife/data-private/followup-clinicas.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler JSON privado: {exc}")
    sys.exit(1)

clinicas = data.get("clinicas", [])

# Campos permitidos: apenas dados comerciais/institucionais B2B
ALLOWED_FIELDS = {
    "nome_clinica", "etapa", "prioridade", "proxima_acao",
    "data_proxima_acao", "status_followup", "tem_whatsapp",
    "telefone_whatsapp", "whatsapp_url",
}
# Campos proibidos: dados pessoais / clínicos / pacientes
BLOCKED_FIELDS = {
    "cpf", "rg", "data_nascimento", "endereco", "endereço",
    "observacao_privada", "pedido_medico", "laudo", "diagnostico",
    "nome_paciente", "paciente", "senha", "token",
    "nome_medico", "crm_medico",
}

errors = 0
for i, rec in enumerate(clinicas, start=1):
    if not isinstance(rec, dict):
        print(f"  ERRO: registro {i} não é um objeto.")
        errors += 1
        continue
    for k in rec.keys():
        if k.lower() in BLOCKED_FIELDS:
            print(f"  ERRO: campo proibido '{k}' no registro {i}.")
            errors += 1
        if k not in ALLOWED_FIELDS:
            print(f"  AVISO: campo inesperado '{k}' no registro {i}.")

if errors == 0:
    print(f"  OK: {len(clinicas)} clínicas B2B. Nenhum campo proibido.")
else:
    print(f"  {errors} erros encontrados.")
    sys.exit(1)
PY
  else
    echo "  INFO: $private_file não existe (ainda não gerado)."
    echo "        Execute: python3 painel-soprolife/scripts/generate-followup-clinicas.py --write"
  fi

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

path = Path("painel-soprolife/data/followup-clinicas-summary.local.json")
data = json.loads(path.read_text(encoding="utf-8"))

if data.get("safeToDisplay") is not True:
    print("  ERRO: followup-clinicas-summary não marcado como seguro.")
    sys.exit(1)
if data.get("containsPersonalData") is not False:
    print("  ERRO: followup-clinicas-summary pode conter dado pessoal.")
    sys.exit(1)

c = data.get("clinicas", {})
print(f"  OK: resumo seguro — total={c.get('total',0)}, comWhatsApp={c.get('comWhatsApp',0)}.")
PY
  else
    echo "  INFO: $summary_file não existe."
  fi
}

validate_command_center_config() {
  echo
  echo "Verificando configuração do Command Center..."

  local real_cfg="painel-soprolife/data-private/command-center-config.local.json"
  local example_cfg="painel-soprolife/apps-script/command-center-config.local.example.json"

  # Arquivo exemplo deve ser COMMITÁVEL (não gitignored)
  if [ -f "$example_cfg" ]; then
    if git check-ignore -q "$example_cfg" 2>/dev/null; then
      echo "  ATENÇÃO: $example_cfg está gitignored — deveria ser commitado."
    else
      echo "  OK (commitável): $example_cfg"
    fi
  else
    echo "  INFO: $example_cfg não encontrado."
  fi

  # Arquivo real deve estar GITIGNORED
  if [ -f "$real_cfg" ]; then
    if git check-ignore -q "$real_cfg" 2>/dev/null; then
      echo "  OK (gitignored): $real_cfg"
    else
      echo "  ERRO CRÍTICO: $real_cfg NÃO está gitignored — token pode vazar para o GitHub!"
    fi

    python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data-private/command-center-config.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler config: {exc}")
    sys.exit(1)

url   = data.get("webAppUrl", "")
token = data.get("apiToken",  "")

# Valida estrutura
if not url or not token:
    print("  ATENÇÃO: webAppUrl ou apiToken vazios.")
    sys.exit(0)

# Detecta valores de exemplo (não reais)
if "SEU_" in url or "SEU_" in token or "DEPLOYMENT_ID" in url:
    print("  ATENÇÃO: config contém valores de exemplo — preencha com valores reais.")
    sys.exit(0)

# Garante que o token não é trivial
if len(token) < 12:
    print("  ATENÇÃO: apiToken muito curto (mínimo 12 caracteres recomendado).")
    sys.exit(0)

# Garante que a URL é Apps Script (não expõe spreadsheet_id)
if "spreadsheets/d/" in url or "spreadsheet_id" in url:
    print("  ERRO: webAppUrl parece conter ID de planilha — use apenas a URL /exec do Apps Script.")
    sys.exit(1)

print(f"  OK: configuração válida (URL Apps Script + token com {len(token)} chars).")
PY
  else
    echo "  INFO: $real_cfg não existe ainda."
    echo "        Copie $example_cfg, renomeie para .local.json e preencha os valores reais."
  fi

  # Garante que nenhum arquivo commitado contém token ou URL secreta
  echo "  Verificando código-fonte por tokens ou URLs secretas commitadas..."
  local found=0
  if git grep -rq "apiToken\s*[=:]\s*\"[A-Za-z0-9_\-]\{12,\}\"" -- painel-soprolife/js/ painel-soprolife/css/ 2>/dev/null; then
    echo "  ATENÇÃO: possível token hardcoded encontrado em JS/CSS."
    found=1
  fi
  if git grep -rq "script\.google\.com/macros/s/[A-Za-z0-9_\-]\{20,\}" -- painel-soprolife/js/ painel-soprolife/css/ 2>/dev/null; then
    echo "  ATENÇÃO: URL de Apps Script hardcoded encontrada em JS/CSS."
    found=1
  fi
  if [ "$found" -eq 0 ]; then
    echo "  OK: nenhum token ou URL secreta hardcoded detectado."
  fi
}

validate_leads() {
  echo
  echo "Verificando dados de Leads..."

  local private_file="painel-soprolife/data-private/leads.local.json"
  local summary_file="painel-soprolife/data/leads-summary.local.json"

  # Arquivo privado de leads
  if [ -f "$private_file" ]; then
    if git check-ignore -q "$private_file" 2>/dev/null; then
      echo "  OK (gitignored): $private_file"
    else
      echo "  ERRO CRÍTICO: $private_file NÃO está gitignored — dados com telefone podem vazar!"
      echo "  Execute: git rm --cached \"$private_file\" antes de qualquer commit."
    fi

    python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data-private/leads.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler JSON privado: {exc}")
    sys.exit(1)

leads = data.get("leads", [])

BLOCKED_FIELDS = {
    "cpf", "rg", "data_nascimento", "endereco", "endereço",
    "pedido_medico", "laudo", "diagnostico", "diagnóstico",
    "senha", "token", "nome_completo",
}

errors = 0
for i, rec in enumerate(leads, start=1):
    if not isinstance(rec, dict):
        print(f"  ERRO: registro {i} não é um objeto.")
        errors += 1
        continue
    for k in rec.keys():
        if k.lower() in BLOCKED_FIELDS:
            print(f"  ERRO: campo proibido '{k}' no registro {i}.")
            errors += 1

if errors == 0:
    print(f"  OK: {len(leads)} leads. Nenhum campo proibido.")
else:
    print(f"  {errors} erros encontrados.")
    sys.exit(1)
PY
  else
    echo "  INFO: $private_file não existe ainda (gerado manualmente ou por script futuro)."
  fi

  # Resumo seguro sem telefone
  if [ -f "$summary_file" ]; then
    if git check-ignore -q "$summary_file" 2>/dev/null; then
      echo "  OK (gitignored): $summary_file"
    else
      echo "  ATENÇÃO: $summary_file não está gitignored."
    fi

    python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data/leads-summary.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler resumo: {exc}")
    sys.exit(1)

source = data.get("source", {})
leads  = data.get("leads", [])

if source.get("safeToDisplay") is not True:
    print("  ERRO: leads-summary.local.json não marcado como safeToDisplay=true.")
    sys.exit(1)
if source.get("containsPersonalData") is not False:
    print("  ERRO: leads-summary.local.json pode conter dado pessoal (containsPersonalData deve ser false).")
    sys.exit(1)

_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
_CPF_RE  = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")

# Campos de PII que NUNCA devem aparecer no resumo seguro
PII_FIELDS = {
    "nome",
    "telefone_whatsapp",
    "observacao",
    "observacao_privada_minima",
    "consentimento_whatsapp",
}

errors = 0
for i, rec in enumerate(leads, start=1):
    if not isinstance(rec, dict):
        continue
    rec_text = json.dumps(rec, ensure_ascii=False).lower()

    for pii_field in PII_FIELDS:
        if pii_field in rec:
            print(f"  ERRO: campo PII '{pii_field}' presente no resumo (registro {i}) — dados pessoais não devem estar aqui.")
            errors += 1

    if _FONE_RE.search(rec_text):
        print(f"  ERRO: padrão de telefone detectado no resumo, registro {i}.")
        errors += 1
    if _CPF_RE.search(rec_text):
        print(f"  ERRO: padrão de CPF detectado no resumo, registro {i}.")
        errors += 1

# Conta por etapa para o relatório
etapas: dict[str, int] = {}
for rec in leads:
    e = rec.get("etapa", "(sem etapa)") if isinstance(rec, dict) else "(inválido)"
    etapas[e] = etapas.get(e, 0) + 1

if errors == 0:
    etapa_resumo = ", ".join(f"{e}={n}" for e, n in sorted(etapas.items()))
    print(f"  OK: leads-summary seguro — {len(leads)} leads, sem telefone nem dado pessoal.")
    if etapa_resumo:
        print(f"  Etapas: {etapa_resumo}")
else:
    print(f"  {errors} erro(s) encontrado(s) no resumo.")
    sys.exit(1)
PY
  else
    echo "  INFO: $summary_file não existe ainda."
    echo "  Execute: python3 painel-soprolife/scripts/read-leads-sheets.py --write"
    echo "  Painel usará leads.json demonstrativo enquanto o arquivo não existir."
  fi
}

validate_crm_contatos_b2b() {
  echo
  echo "Verificando dados de CRM Contatos B2B..."

  local private_file="painel-soprolife/data-private/crm-contatos-b2b.local.json"
  local summary_file="painel-soprolife/data/crm-contatos-b2b-summary.local.json"

  if [ -f "$private_file" ]; then
    if git check-ignore -q "$private_file" 2>/dev/null; then
      echo "  OK (gitignored): $private_file"
    else
      echo "  ERRO CRÍTICO: $private_file NÃO está gitignored — dados com telefone/e-mail podem vazar!"
      echo "  Execute: git rm --cached \"$private_file\" antes de qualquer commit."
    fi

    python3 - <<'PY'
from pathlib import Path
import json, sys

path = Path("painel-soprolife/data-private/crm-contatos-b2b.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler JSON privado: {exc}")
    sys.exit(1)

contatos = data.get("contatos", [])

BLOCKED_FIELDS = {
    "cpf", "rg", "data_nascimento", "endereco", "endereço",
    "senha", "token", "pedido_medico", "laudo", "diagnostico", "diagnóstico",
}

errors = 0
for i, rec in enumerate(contatos, start=1):
    if not isinstance(rec, dict):
        print(f"  ERRO: registro {i} não é um objeto.")
        errors += 1
        continue
    for k in rec.keys():
        if k.lower() in BLOCKED_FIELDS:
            print(f"  ERRO: campo proibido '{k}' no registro {i}.")
            errors += 1

if errors == 0:
    print(f"  OK: {len(contatos)} contatos B2B. Nenhum campo proibido.")
else:
    print(f"  {errors} erros encontrados.")
    sys.exit(1)
PY
  else
    echo "  INFO: $private_file não existe ainda (aba CRM Contatos B2B só é criada no primeiro vínculo)."
  fi

  if [ -f "$summary_file" ]; then
    if git check-ignore -q "$summary_file" 2>/dev/null; then
      echo "  OK (gitignored): $summary_file"
    else
      echo "  ATENÇÃO: $summary_file não está gitignored."
    fi

    python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data/crm-contatos-b2b-summary.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler resumo: {exc}")
    sys.exit(1)

source   = data.get("source", {})
contatos = data.get("contatos", [])

if source.get("safeToDisplay") is not True:
    print("  ERRO: crm-contatos-b2b-summary.local.json não marcado como safeToDisplay=true.")
    sys.exit(1)
if source.get("containsPersonalData") is not False:
    print("  ERRO: crm-contatos-b2b-summary.local.json pode conter dado pessoal.")
    sys.exit(1)

_FONE_RE  = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

PII_FIELDS = {"nome_contato", "telefone_whatsapp", "email", "observacao"}

errors = 0
for i, rec in enumerate(contatos, start=1):
    if not isinstance(rec, dict):
        continue
    rec_text = json.dumps(rec, ensure_ascii=False)

    for pii_field in PII_FIELDS:
        if pii_field in rec:
            print(f"  ERRO: campo PII '{pii_field}' presente no resumo (registro {i}).")
            errors += 1

    if _FONE_RE.search(rec_text):
        print(f"  ERRO: padrão de telefone detectado no resumo, registro {i}.")
        errors += 1
    if _EMAIL_RE.search(rec_text):
        print(f"  ERRO: padrão de e-mail detectado no resumo, registro {i}.")
        errors += 1

if errors == 0:
    print(f"  OK: crm-contatos-b2b-summary seguro — {len(contatos)} contatos, sem telefone/e-mail/nome.")
else:
    print(f"  {errors} erro(s) encontrado(s) no resumo.")
    sys.exit(1)
PY
  else
    echo "  INFO: $summary_file não existe ainda."
    echo "  Execute: python3 painel-soprolife/scripts/read-crm-contatos-b2b-adc.py --write"
  fi
}

validate_financeiro() {
  echo
  echo "Verificando dados financeiros..."

  local private_file="painel-soprolife/data-private/financeiro.local.json"
  local summary_file="painel-soprolife/data/financeiro-summary.local.json"

  # Arquivo privado
  if [ -f "$private_file" ]; then
    if git check-ignore -q "$private_file" 2>/dev/null; then
      echo "  OK (gitignored): $private_file"
    else
      echo "  ERRO CRÍTICO: $private_file NÃO está gitignored — dados financeiros podem vazar!"
      echo "  Execute: git rm --cached \"$private_file\" antes de qualquer commit."
    fi

    python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data-private/financeiro.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler financeiro privado: {exc}")
    sys.exit(1)

entradas = data.get("entradas", [])

BLOCKED = {
    "cpf", "rg", "nome_pagador", "nome_completo", "numero_conta",
    "agencia", "banco", "pix_key_real", "chave_pix", "senha", "token",
}
_CPF_RE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")

errors = 0
for i, rec in enumerate(entradas, start=1):
    for k in rec.keys():
        if k.lower() in BLOCKED:
            print(f"  ERRO: campo proibido '{k}' no lançamento {i}.")
            errors += 1
    text = json.dumps(rec, ensure_ascii=False).lower()
    if _CPF_RE.search(text):
        print(f"  ERRO: padrão CPF detectado no lançamento {i}.")
        errors += 1

if errors == 0:
    print(f"  OK: {len(entradas)} lançamentos. Nenhum dado bancário identificador.")
else:
    print(f"  {errors} erros encontrados.")
    sys.exit(1)
PY
  else
    echo "  INFO: $private_file não existe ainda."
    echo "        Crie com os lançamentos reais (sem nome de pagador, CPF ou dado bancário)."
  fi

  # Resumo público
  if [ -f "$summary_file" ]; then
    if git check-ignore -q "$summary_file" 2>/dev/null; then
      echo "  OK (gitignored): $summary_file"
    else
      echo "  ATENÇÃO: $summary_file não está gitignored — verifique se não contém dados sensíveis."
    fi

    python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data/financeiro-summary.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler resumo financeiro: {exc}")
    sys.exit(1)

source = data.get("source", {})
if source.get("safeToDisplay") is not True:
    print("  ERRO: financeiro-summary não marcado como safeToDisplay=true.")
    sys.exit(1)
if source.get("containsPersonalData") is not False:
    print("  ERRO: financeiro-summary pode conter dado pessoal.")
    sys.exit(1)

text = json.dumps(data, ensure_ascii=False).lower()
FORBIDDEN = [
    "nome_pagador", "nome_completo", "agencia", "numero_conta",
    "chave_pix", "access_token", "refresh_token", "private_key",
    "cpf",
]
_CPF_RE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")

errors = 0
for f in FORBIDDEN:
    if f in text:
        print(f"  ERRO: termo proibido '{f}' em financeiro-summary.")
        errors += 1
if _CPF_RE.search(text):
    print("  ERRO: padrão CPF em financeiro-summary.")
    errors += 1
if _FONE_RE.search(text):
    print("  ERRO: padrão telefone em financeiro-summary.")
    errors += 1

if errors == 0:
    total = data.get("total_lancamentos", 0)
    receita = data.get("receita_exames", 0)
    saldo = data.get("saldo_operacional", 0)
    print(f"  OK: resumo financeiro seguro — {total} lanç., receita_exames=R${receita:.2f}, saldo=R${saldo:.2f}.")
else:
    print(f"  {errors} erros encontrados.")
    sys.exit(1)
PY
  else
    echo "  INFO: $summary_file não existe ainda."
    echo "        Crie com valores agregados (sem nomes de pagadores)."
  fi
}

validate_marketing_seo() {
  echo
  echo "Verificando Marketing & SEO..."

  local real_cfg="painel-soprolife/data-private/marketing-seo-config.local.json"
  local local_data="painel-soprolife/data/marketing-seo.local.json"

  # Config real deve estar gitignored
  if [ -f "$real_cfg" ]; then
    if git check-ignore -q "$real_cfg" 2>/dev/null; then
      echo "  OK (gitignored): $real_cfg"
    else
      echo "  ERRO CRÍTICO: $real_cfg NÃO está gitignored — credenciais podem vazar para o GitHub!"
    fi

    python3 - <<'PY'
from pathlib import Path
import json, sys

path = Path("painel-soprolife/data-private/marketing-seo-config.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler config: {exc}")
    sys.exit(1)

forbidden = {"client_secret", "refresh_token", "access_token", "private_key", "api_key", "apikey"}
text_lower = json.dumps(data, ensure_ascii=False).lower()
for f in forbidden:
    if f in text_lower:
        print(f"  ERRO: padrão proibido '{f}' encontrado na config de Marketing & SEO.")
        sys.exit(1)

ga4 = str(data.get("ga4PropertyId", ""))
sc  = str(data.get("searchConsoleSiteUrl", ""))
if "SEU_" in ga4 or "SEU_" in sc or "DEPLOYMENT_ID" in ga4:
    print("  AVISO: config contém valores de exemplo (SEU_...) — preencha com valores reais.")
    sys.exit(0)

print("  OK: marketing-seo-config.local.json existe e não contém segredos.")
PY
  else
    echo "  INFO: $real_cfg não existe — Marketing & SEO ainda não configurado."
    echo "        Copie painel-soprolife/config-examples/marketing-seo.local.example.json"
    echo "        para esse caminho e preencha os valores reais."
  fi

  # Verifica dados exportados
  if [ -f "$local_data" ]; then
    if git check-ignore -q "$local_data" 2>/dev/null; then
      echo "  OK (gitignored): $local_data"
    else
      echo "  ATENÇÃO: $local_data não está gitignored."
    fi

    python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data/marketing-seo.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler dados: {exc}")
    sys.exit(1)

meta = data.get("meta", {})
if meta.get("containsPersonalData") is not False:
    print("  ERRO: marketing-seo.local.json não declara containsPersonalData=false.")
    sys.exit(1)
if meta.get("safeToDisplay") is not True:
    print("  ERRO: marketing-seo.local.json não declara safeToDisplay=true.")
    sys.exit(1)

text = json.dumps(data, ensure_ascii=False).lower()

forbidden_patterns = [
    "cpf", "refresh_token", "access_token", "client_secret",
    "private_key", "api_key", "apikey",
]
_CPF_RE   = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE  = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

for p in forbidden_patterns:
    if p in text:
        print(f"  ERRO: padrão proibido '{p}' encontrado em marketing-seo.local.json.")
        sys.exit(1)
if _CPF_RE.search(text):
    print("  ERRO: padrão de CPF detectado em marketing-seo.local.json.")
    sys.exit(1)
if _FONE_RE.search(text):
    print("  ERRO: padrão de telefone detectado em marketing-seo.local.json.")
    sys.exit(1)
if _EMAIL_RE.search(text):
    print("  ERRO: padrão de e-mail detectado em marketing-seo.local.json.")
    sys.exit(1)

configured = meta.get("configured", False)
sc_ok  = data.get("searchConsole") is not None
ga4_ok = data.get("ga4") is not None
print(f"  OK: marketing-seo.local.json seguro — "
      f"configured={configured}, SC={sc_ok}, GA4={ga4_ok}.")
PY
  else
    echo "  INFO: $local_data não existe — painel usará dados demonstrativos."
  fi

  # Garante que nenhum código commitado contém property ID real de GA4
  echo "  Verificando código-fonte por IDs sensíveis de Marketing & SEO..."
  local found=0
  if git grep -rqE '"ga4PropertyId"\s*:\s*"[0-9]{6,}"' \
      -- painel-soprolife/js/ painel-soprolife/css/ 2>/dev/null; then
    echo "  ATENÇÃO: possível property ID de GA4 hardcoded encontrado em JS/CSS."
    found=1
  fi
  if git grep -rqE '"searchConsoleSiteUrl"\s*:\s*"https://' \
      -- painel-soprolife/js/ painel-soprolife/css/ 2>/dev/null; then
    echo "  ATENÇÃO: possível URL de Search Console hardcoded encontrada em JS/CSS."
    found=1
  fi
  if [ "$found" -eq 0 ]; then
    echo "  OK: nenhum ID ou URL sensível de Marketing & SEO hardcoded detectado."
  fi
}

validate_command_center_proxy() {
  echo
  echo "Verificando servidor proxy do Command Center..."

  local proxy_script="painel-soprolife/scripts/command-center-local-server.py"

  if [ ! -f "$proxy_script" ]; then
    echo "  ATENÇÃO: $proxy_script não encontrado."
    echo "           Sem o proxy, o formulário de Entrada de Dados não funciona."
    return
  fi

  echo "  OK: $proxy_script existe."

  # Confirma que o servidor só escuta em 127.0.0.1
  if grep -q '127\.0\.0\.1' "$proxy_script"; then
    echo "  OK: proxy configurado para 127.0.0.1 (localhost only)."
  else
    echo "  ATENÇÃO: 127.0.0.1 não encontrado no proxy — verifique o HOST configurado."
  fi

  # Confirma que não há token ou URL hardcoded no script do proxy
  if grep -qE '"[A-Za-z0-9_\-]{20,}"' "$proxy_script" 2>/dev/null; then
    echo "  ATENÇÃO: possível token ou ID hardcoded detectado no proxy."
  else
    echo "  OK: nenhum segredo hardcoded no proxy."
  fi

  # Confirma que o proxy lê o token do arquivo local (nunca do código)
  if grep -q 'apiToken' "$proxy_script" && grep -q '_CONFIG_PATH\|read_text\|json.loads' "$proxy_script"; then
    echo "  OK: proxy lê o token do arquivo de configuração local (não hardcoded)."
  else
    echo "  ATENÇÃO: não foi possível confirmar que o token é lido do arquivo de configuração."
  fi

  # Garante que app.js não faz fetch direto para o Apps Script
  if grep -q 'script\.google\.com' painel-soprolife/js/app.js 2>/dev/null; then
    echo "  ATENÇÃO: app.js parece chamar script.google.com diretamente — verifique."
  else
    echo "  OK: app.js não contém URL do Apps Script (usa proxy local)."
  fi

  # Confirma que app.js não carrega o token no browser
  if grep -q 'apiToken' painel-soprolife/js/app.js 2>/dev/null; then
    echo "  ATENÇÃO: 'apiToken' encontrado em app.js — token pode estar exposto no browser."
  else
    echo "  OK: token não carregado no browser."
  fi
}

validate_ultimos_lancamentos() {
  echo
  echo "Verificando timeline de últimos lançamentos..."

  local private_file="painel-soprolife/data-private/ultimos-lancamentos.local.json"
  local summary_file="painel-soprolife/data/ultimos-lancamentos-summary.local.json"

  # Arquivo privado
  if [ -f "$private_file" ]; then
    if git check-ignore -q "$private_file" 2>/dev/null; then
      echo "  OK (gitignored): $private_file"
    else
      echo "  ERRO CRÍTICO: $private_file NÃO está gitignored!"
      echo "  Execute: git rm --cached \"$private_file\" antes de qualquer commit."
    fi
  else
    echo "  INFO: $private_file não existe (normal — gerado por generate-ultimos-lancamentos.py)."
  fi

  # Resumo seguro
  if [ ! -f "$summary_file" ]; then
    echo "  INFO: $summary_file não existe ainda."
    echo "        Execute: python3 painel-soprolife/scripts/generate-ultimos-lancamentos.py --write"
    return
  fi

  if git check-ignore -q "$summary_file" 2>/dev/null; then
    echo "  OK (gitignored): $summary_file"
  else
    echo "  ATENÇÃO: $summary_file não está gitignored."
  fi

  python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data/ultimos-lancamentos-summary.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler JSON: {exc}")
    sys.exit(1)

source = data.get("source", {})
eventos = data.get("eventos", [])

if source.get("safeToDisplay") is not True:
    print("  ERRO: ultimos-lancamentos-summary não marcado como safeToDisplay=true.")
    sys.exit(1)
if source.get("containsPersonalData") is not False:
    print("  ERRO: ultimos-lancamentos-summary pode conter dado pessoal.")
    sys.exit(1)
if source.get("containsHealthData") is not False:
    print("  ERRO: ultimos-lancamentos-summary pode conter dado clínico.")
    sys.exit(1)

FORBIDDEN = [
    "cpf", "telefone", "celular",
    # "whatsapp" permitido como nome de canal agregado ("Com WhatsApp: 4 clínicas")
    # — a regex _FONE_RE abaixo já bloqueia números de telefone/WhatsApp.
    "pedido médico", "pedido medico", "laudo",
    "diagnóstico", "diagnostico", "endereço", "endereco",
    "data de nascimento", "nome completo",
    "access_token", "refresh_token", "private_key",
    "client_secret", "client_email",
    "https://docs.google.com", "/spreadsheets/d/", "spreadsheet_id",
]
# Campos que nunca devem aparecer como chave em eventos individuais.
# proxima_acao é excluído porque é texto livre e pode conter dados sensíveis.
BLOCKED_EVENT_KEYS = {"nome", "telefone", "telefone_whatsapp", "celular",
                       "cpf", "observacao", "observação", "laudo", "diagnostico",
                       "proxima_acao", "data_proxima_acao"}
_CPF_RE  = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")

errors = 0

# Verifica termos proibidos no payload completo
text = json.dumps(data, ensure_ascii=False).lower()
for f in FORBIDDEN:
    if f in text:
        print(f"  ERRO: termo proibido '{f}' detectado em ultimos-lancamentos-summary.")
        errors += 1
if _CPF_RE.search(text):
    print("  ERRO: padrão de CPF detectado em ultimos-lancamentos-summary.")
    errors += 1
if _FONE_RE.search(text):
    print("  ERRO: padrão de telefone detectado em ultimos-lancamentos-summary.")
    errors += 1

# Verifica campos proibidos em cada evento individualmente
for i, evt in enumerate(eventos):
    for key in evt.keys():
        if key.lower() in BLOCKED_EVENT_KEYS:
            print(f"  ERRO: campo proibido '{key}' em evento [{i}] id={evt.get('id','?')}.")
            errors += 1
    if evt.get("safeToDisplay") is not True:
        print(f"  ERRO: evento [{i}] id={evt.get('id','?')} não marcado como safeToDisplay=true.")
        errors += 1

# Verifica estrutura mínima dos eventos
campos_obrigatorios = {"id", "titulo", "descricao", "categoria", "timestamp"}
for i, evt in enumerate(eventos):
    ausentes = campos_obrigatorios - set(evt.keys())
    if ausentes:
        print(f"  AVISO: evento [{i}] faltando campos: {ausentes}.")

if errors == 0:
    stats = data.get("stats", {})
    cats = stats.get("por_categoria", {})
    cats_txt = ", ".join(f"{k}:{v}" for k, v in cats.items()) if cats else "—"
    print(f"  OK: ultimos-lancamentos-summary seguro — {len(eventos)} eventos "
          f"(hoje={stats.get('hoje',0)}, alta={stats.get('prioridade_alta',0)}, "
          f"pendencias={stats.get('pendencias',0)}) · categorias: {cats_txt}.")
else:
    print(f"  {errors} erro(s) encontrado(s).")
    sys.exit(1)
PY
}

validate_custos_investimentos() {
  echo
  echo "Verificando Custos & Investimentos..."

  local private_file="painel-soprolife/data-private/custos-investimentos.local.json"
  local summary_file="painel-soprolife/data/custos-investimentos-summary.local.json"

  if [ -f "$private_file" ]; then
    if git check-ignore -q "$private_file" 2>/dev/null; then
      echo "  OK (gitignored): $private_file"
    else
      echo "  ERRO CRÍTICO: $private_file NÃO está gitignored!"
      echo "  Execute: git rm --cached \"$private_file\" antes de qualquer commit."
    fi

    python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data-private/custos-investimentos.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler JSON privado: {exc}")
    sys.exit(1)

BLOCKED = {
    "cpf", "rg", "senha", "token", "access_token", "refresh_token",
    "private_key", "client_secret", "numero_conta", "agencia", "banco",
    "chave_pix", "numero_cartao", "cvv", "login",
}
_CPF_RE  = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")

text = json.dumps(data, ensure_ascii=False).lower()
errors = 0

for blocked in BLOCKED:
    if f'"{blocked}"' in text or f': "{blocked}"' in text:
        print(f"  ERRO: chave proibida '{blocked}' detectada.")
        errors += 1

if _CPF_RE.search(text):
    print("  ERRO: padrão de CPF detectado.")
    errors += 1
if _FONE_RE.search(text):
    print("  ERRO: padrão de telefone detectado.")
    errors += 1

itens = data.get("itens", [])
pendencias = data.get("pendencias", [])
if errors == 0:
    print(f"  OK: {len(itens)} itens, {len(pendencias)} pendências. Nenhum dado sensível.")
else:
    print(f"  {errors} erro(s) encontrado(s).")
    sys.exit(1)
PY
  else
    echo "  INFO: $private_file não existe ainda."
    echo "        Crie com os itens de custo (sem CPF, token, dados bancários)."
  fi

  if [ -f "$summary_file" ]; then
    if git check-ignore -q "$summary_file" 2>/dev/null; then
      echo "  OK (gitignored): $summary_file"
    else
      echo "  ATENÇÃO: $summary_file não está gitignored — verifique se não contém dados sensíveis."
    fi

    python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data/custos-investimentos-summary.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler summary de custos: {exc}")
    sys.exit(1)

source = data.get("source", {})
if source.get("safeToDisplay") is not True:
    print("  ERRO: custos-summary não marcado como safeToDisplay=true.")
    sys.exit(1)
if source.get("containsPersonalData") is not False:
    print("  ERRO: custos-summary pode conter dado pessoal.")
    sys.exit(1)

FORBIDDEN = [
    "cpf", "telefone", "celular", "nome_pagador", "nome_completo",
    "numero_conta", "agencia", "banco", "chave_pix", "numero_cartao",
    "access_token", "refresh_token", "private_key", "client_secret", "senha",
]
_CPF_RE  = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")

text = json.dumps(data, ensure_ascii=False).lower()
errors = 0
for f in FORBIDDEN:
    if f in text:
        print(f"  ERRO: termo proibido '{f}' em custos-summary.")
        errors += 1
if _CPF_RE.search(text):
    print("  ERRO: padrão CPF em custos-summary.")
    errors += 1
if _FONE_RE.search(text):
    print("  ERRO: padrão telefone em custos-summary.")
    errors += 1

if errors == 0:
    total = data.get("total_mensal_atual", 0)
    itens = len(data.get("itens", []))
    pend  = data.get("pendencias_cadastro", 0)
    print(f"  OK: custos-summary seguro — {itens} itens, total_mensal=R${total:.2f}, pendências={pend}.")
else:
    print(f"  {errors} erro(s) encontrado(s).")
    sys.exit(1)
PY
  else
    echo "  INFO: $summary_file não existe ainda."
    echo "        Crie com valores agregados de custos e investimentos."
  fi
}

validate_parcerias_pastore() {
  echo "Verificando Parceria Pastore..."

  local private_file="painel-soprolife/data-private/parcerias-pastore.local.json"
  local summary_file="painel-soprolife/data/parcerias-pastore-summary.local.json"
  local fallback_file="painel-soprolife/data/parcerias-pastore-summary.json"

  # O arquivo privado PODE conter nome/WhatsApp de paciente (é o objetivo dele) —
  # só precisa estar gitignored e nunca ter CPF, token ou dado bancário.
  if [ -f "$private_file" ]; then
    if git check-ignore -q "$private_file" 2>/dev/null; then
      echo "  OK (gitignored): $private_file"
    else
      echo "  ERRO CRÍTICO: $private_file NÃO está gitignored!"
      echo "  Execute: git rm --cached \"$private_file\" antes de qualquer commit."
    fi

    python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data-private/parcerias-pastore.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler JSON privado: {exc}")
    sys.exit(1)

# Nome e WhatsApp são esperados aqui — só bloqueia o que nunca pode existir
# em nenhum arquivo do painel, nem no privado.
BLOCKED = {
    "cpf", "rg", "senha", "token", "access_token", "refresh_token",
    "private_key", "client_secret", "numero_conta", "agencia", "banco",
    "chave_pix", "numero_cartao", "cvv", "login",
}
_CPF_RE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")

text = json.dumps(data, ensure_ascii=False).lower()
errors = 0

for blocked in BLOCKED:
    if f'"{blocked}"' in text or f': "{blocked}"' in text:
        print(f"  ERRO: chave proibida '{blocked}' detectada.")
        errors += 1

if _CPF_RE.search(text):
    print("  ERRO: padrão de CPF detectado.")
    errors += 1

atend = data.get("atendimentos", [])
custos = data.get("custos", [])
config = data.get("config", [])
if errors == 0:
    print(f"  OK: {len(atend)} atendimento(s), {len(custos)} custo(s), {len(config)} linha(s) de config. Nenhum dado proibido.")
else:
    print(f"  {errors} erro(s) encontrado(s).")
    sys.exit(1)
PY
  else
    echo "  INFO: $private_file não existe ainda."
    echo "        Gere com: python3 painel-soprolife/scripts/read-parcerias-pastore-adc.py --write"
  fi

  # O resumo NUNCA pode ter nome, telefone, WhatsApp, CPF, e-mail ou observação.
  if [ -f "$summary_file" ]; then
    if git check-ignore -q "$summary_file" 2>/dev/null; then
      echo "  OK (gitignored): $summary_file"
    else
      echo "  ATENÇÃO: $summary_file não está gitignored — verifique se não contém dados sensíveis."
    fi

    python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data/parcerias-pastore-summary.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler summary da Parceria Pastore: {exc}")
    sys.exit(1)

source = data.get("source", {})
if source.get("safeToDisplay") is not True:
    print("  ERRO: parcerias-pastore-summary não marcado como safeToDisplay=true.")
    sys.exit(1)
if source.get("containsPersonalData") is not False:
    print("  ERRO: parcerias-pastore-summary pode conter dado pessoal.")
    sys.exit(1)

FORBIDDEN = [
    "paciente_nome", "nome_paciente", "paciente_whatsapp", "whatsapp",
    "telefone", "celular", "cpf", "email", "e-mail",
    "observacao_privada_minima", "observação privada",
    "consentimento_contato_futuro", "forma_pagamento",
    "access_token", "refresh_token", "private_key", "client_secret", "senha",
]
_CPF_RE   = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE  = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

text = json.dumps(data, ensure_ascii=False).lower()
errors = 0
for f in FORBIDDEN:
    if f in text:
        print(f"  ERRO: termo proibido '{f}' em parcerias-pastore-summary.")
        errors += 1
if _CPF_RE.search(text):
    print("  ERRO: padrão de CPF em parcerias-pastore-summary.")
    errors += 1
if _FONE_RE.search(text):
    print("  ERRO: padrão de telefone em parcerias-pastore-summary.")
    errors += 1
if _EMAIL_RE.search(text):
    print("  ERRO: padrão de e-mail em parcerias-pastore-summary.")
    errors += 1

if errors == 0:
    exames = data.get("kpis", {}).get("exames_realizados", 0)
    receita = data.get("kpis", {}).get("receita_estimada")
    ocupacao = data.get("kpis", {}).get("ocupacao_agenda_pct")
    print(f"  OK: parcerias-pastore-summary seguro — exames_realizados={exames}, receita_estimada={receita}, ocupacao_agenda_pct={ocupacao}.")
else:
    print(f"  {errors} erro(s) encontrado(s).")
    sys.exit(1)
PY
  else
    echo "  INFO: $summary_file não existe ainda (dado real ainda não sincronizado)."
    if [ -f "$fallback_file" ]; then
      echo "  OK: painel usará o fallback commitável ($fallback_file)."
    else
      echo "  ATENÇÃO: fallback commitável ($fallback_file) também não existe."
    fi
  fi
}

validate_auditoria() {
  echo
  echo "Verificando resumo de auditoria (Log Auditoria)..."

  local summary_file="painel-soprolife/data/auditoria-summary.local.json"

  if [ ! -f "$summary_file" ]; then
    echo "  INFO: $summary_file não existe ainda."
    echo "        (normal antes da primeira escrita auditada — M1 publicado)"
    echo "        Execute: python3 painel-soprolife/scripts/read-auditoria-adc.py --write"
    return
  fi

  if git check-ignore -q "$summary_file" 2>/dev/null; then
    echo "  OK (gitignored): $summary_file"
  else
    echo "  ERRO CRÍTICO: $summary_file NÃO está gitignored!"
  fi

  python3 - <<'PY'
from pathlib import Path
import json, re, sys

path = Path("painel-soprolife/data/auditoria-summary.local.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"  ERRO ao ler JSON: {exc}")
    sys.exit(1)

source = data.get("source", {})
eventos = data.get("ultimos_eventos", [])

if source.get("safeToDisplay") is not True:
    print("  ERRO: auditoria-summary não marcado como safeToDisplay=true.")
    sys.exit(1)
if source.get("containsPersonalData") is not False:
    print("  ERRO: auditoria-summary pode conter dado pessoal.")
    sys.exit(1)

# O summary de auditoria NUNCA carrega conteúdo de campo nem PII —
# só os 6 campos da allowlist abaixo em cada evento.
ALLOWED_EVENT_KEYS = {"timestamp", "acao", "entidade_tipo", "entidade_id",
                      "operador", "resultado"}
FORBIDDEN = [
    "valor_anterior", "valor_novo", "derivado_de", "request_id",
    "telefone", "whatsapp", "observacao", "observação",
    "paciente_nome", "primeiro_nome", "nome completo", "laudo",
    "pedido médico", "pedido medico", "cpf",
    "access_token", "private_key", "client_secret",
    "https://docs.google.com", "/spreadsheets/d/", "spreadsheet_id",
]
_CPF_RE  = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")

errors = 0

text = json.dumps(data, ensure_ascii=False).lower()
for f in FORBIDDEN:
    if f in text:
        print(f"  ERRO: termo proibido '{f}' detectado em auditoria-summary.")
        errors += 1
if _CPF_RE.search(text):
    print("  ERRO: padrão de CPF detectado em auditoria-summary.")
    errors += 1

for i, evt in enumerate(eventos):
    extras = set(evt.keys()) - ALLOWED_EVENT_KEYS
    if extras:
        print(f"  ERRO: campo(s) fora da allowlist em evento [{i}]: {sorted(extras)}.")
        errors += 1
    for k, v in evt.items():
        if k == "timestamp":
            continue
        if _FONE_RE.search(str(v)):
            print(f"  ERRO: padrão de telefone no campo '{k}' do evento [{i}].")
            errors += 1

if errors == 0:
    stats = data.get("stats", {})
    print(f"  OK: auditoria-summary seguro — {stats.get('total_eventos', 0)} evento(s), "
          f"{stats.get('erros', 0)} erro(s) de escrita, últimos={len(eventos)}.")
else:
    print(f"  {errors} erro(s) encontrado(s).")
    sys.exit(1)
PY
}

main() {
  check_ports
  check_extra_network_services
  check_private_files
  validate_runtime_status
  validate_dashboard_summary
  validate_crm_clinicas
  validate_leads
  validate_crm_contatos_b2b
  validate_followup_pacientes
  validate_followup_clinicas
  validate_financeiro
  validate_custos_investimentos
  validate_parcerias_pastore
  validate_command_center_config
  validate_command_center_proxy
  validate_marketing_seo
  validate_ultimos_lancamentos
  validate_auditoria
}

main
