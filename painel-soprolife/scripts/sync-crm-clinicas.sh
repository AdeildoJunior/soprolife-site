#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

# ---------------------------------------------------------------------------
# M23 — guarda de fonte canônica.
#
# Este utilitário grava um snapshot do painel a partir de um arquivo derivado
# de planilha. Desde o M23 esse mesmo arquivo é gerado pelo PostgreSQL
# (nucleo-m15: app.cli exportar-snapshots), e deixar os dois caminhos vivos
# significaria sobrescrever dado canônico com dado de planilha.
#
# Fail-closed: só roda com decisão humana explícita, para migração/forense.
# ---------------------------------------------------------------------------
if python3 painel-soprolife/scripts/data_source_mode.py --check >/dev/null 2>&1; then
  echo "BLOQUEADO (M23): $(basename "$0") sobrescreveria um snapshot canônico."
  echo
  echo "A fonte operacional é o PostgreSQL. Regenere os snapshots com:"
  echo "  bash painel-soprolife/scripts/update-local-data.sh"
  echo
  echo "Para migração/forense: export SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1"
  exit 3
fi

SOURCE_PATH="${SOPROLIFE_CRM_CLINICAS_CONFIG:-$HOME/.config/soprolife/painel/crm-clinicas.json}"
OUT_PATH="painel-soprolife/data/crm-clinicas.local.json"

export SOURCE_PATH
export OUT_PATH

python3 - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import re
import sys

source_path = Path(os.environ["SOURCE_PATH"])
out_path = Path(os.environ["OUT_PATH"])

ALLOWED_FIELDS = {
    "clinica_id",
    "nome_clinica",
    "bairro",
    "regiao",
    "tipo_clinica",
    "etapa",
    "ultima_interacao",
    # proxima_acao (texto livre) foi substituída pelo booleano derivado
    # tem_proxima_acao — ver read-crm-clinicas-adc.py (M2 Etapa 4).
    "tem_proxima_acao",
    "data_proxima_acao",
    "responsavel",
    "prioridade",
}

BLOCKED_FIELDS = {
    "observacao", "observação", "cpf", "telefone", "celular",
    "email", "e-mail", "whatsapp", "nome_paciente", "paciente",
    "pedido_medico", "pedido médico", "laudo", "diagnostico",
    "diagnóstico", "endereco", "endereço",
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

if not source_path.exists():
    print("ERRO: arquivo crm-clinicas.json não encontrado.")
    print(f"  Esperado em: {source_path}")
    print("  Execute: python3 painel-soprolife/scripts/read-crm-clinicas-adc.py --write")
    sys.exit(1)

try:
    records = json.loads(source_path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    print(f"ERRO: JSON inválido — {exc}")
    sys.exit(1)

if not isinstance(records, list):
    print("ERRO: crm-clinicas.json deve ser uma lista.")
    sys.exit(1)

safe_records = []
for i, record in enumerate(records, start=1):
    if not isinstance(record, dict):
        print(f"ERRO: registro {i} não é um objeto.")
        sys.exit(1)

    for field in record.keys():
        if field.lower() in BLOCKED_FIELDS:
            print(f"ERRO: campo proibido '{field}' encontrado no registro {i}.")
            sys.exit(1)

    extra = set(record.keys()) - ALLOWED_FIELDS
    if extra:
        print(f"AVISO: campos não reconhecidos ignorados no registro {i}: {', '.join(sorted(extra))}")

    # Booleans (tem_proxima_acao) preservados; demais valores viram string.
    safe_record = {k: (v if isinstance(v, bool) else str(v))
                   for k, v in record.items() if k in ALLOWED_FIELDS}

    record_text = json.dumps(safe_record, ensure_ascii=False).lower()
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

    safe_records.append(safe_record)

payload = {
    "source": {
        "type": "local_safe_crm_clinicas",
        "safeToDisplay": True,
        "containsPersonalData": False,
        "containsHealthData": False,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    },
    "clinicas": safe_records,
}

out_path.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

print("CRM Clínicas seguro sincronizado.")
print(f"Origem: {source_path}")
print(f"Saída: {out_path}")
print(f"Registros exportados: {len(safe_records)}")
print("Nenhum dado pessoal, clínico, CPF, telefone ou pedido médico foi exportado.")
PY

python3 -m json.tool "$OUT_PATH" >/dev/null
