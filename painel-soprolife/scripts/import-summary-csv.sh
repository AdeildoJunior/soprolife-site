#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

CSV_PATH="${1:-$HOME/.config/soprolife/painel/resumo-dashboard.csv}"
OUT_PATH="$HOME/.config/soprolife/painel/resumo-dashboard.json"

export CSV_PATH
export OUT_PATH

python3 - <<'PY'
from pathlib import Path
import csv
import json
import os
import re
import sys

csv_path = Path(os.environ["CSV_PATH"]).expanduser()
out_path = Path(os.environ["OUT_PATH"]).expanduser()

label_to_key = {
    "total de leads": "totalLeads",
    "leads novos": "leadsNovos",
    "leads agendados": "leadsAgendados",
    "leads concluídos": "leadsConcluidos",
    "leads concluidos": "leadsConcluidos",
    "clínicas cadastradas": "clinicasCadastradas",
    "clinicas cadastradas": "clinicasCadastradas",
    "tarefas pendentes": "tarefasPendentes",
    "receita prevista": "receitaPrevista",
    "receita recebida": "receitaRecebida",
    "conteúdos planejados": "conteudosPlanejados",
    "conteudos planejados": "conteudosPlanejados",
    "eventos agendados": "eventosAgendados",
}

required_keys = {
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

forbidden_words = [
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
    "data de nascimento",
    "nascimento",
    "nome completo",
]

if not csv_path.exists():
    print("ERRO: CSV não encontrado.")
    print(f"Coloque o arquivo em: {csv_path}")
    print("Ou rode: painel-soprolife/scripts/import-summary-csv.sh /caminho/do/arquivo.csv")
    sys.exit(1)

text = csv_path.read_text(encoding="utf-8-sig")
lower_text = text.lower()

for word in forbidden_words:
    if word in lower_text:
        print("ERRO: o CSV parece conter dado sensível ou coluna proibida.")
        print("Exporte apenas a aba Resumo Dashboard com indicadores agregados.")
        sys.exit(1)

def normalize_label(value):
    return str(value or "").strip().lower()

def parse_number(value):
    raw = str(value or "").strip()

    if not raw:
        return 0

    cleaned = raw.replace("R$", "").replace(" ", "")

    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")

    cleaned = re.sub(r"[^0-9.\-]", "", cleaned)

    if cleaned in {"", "-", ".", "-."}:
        return 0

    number = float(cleaned)

    if number < 0:
        raise ValueError("valor negativo não permitido")

    if number.is_integer():
        return int(number)

    return number

rows = list(csv.reader(text.splitlines()))

if not rows:
    print("ERRO: CSV vazio.")
    sys.exit(1)

summary = {}

for row in rows:
    if len(row) < 2:
        continue

    label = normalize_label(row[0])
    value = row[1]

    if label in {"indicador", "métrica", "metrica", "campo", "label"}:
        continue

    key = label_to_key.get(label)

    if not key:
        continue

    try:
        summary[key] = parse_number(value)
    except Exception:
        print(f"ERRO: valor inválido para indicador permitido: {row[0]}")
        sys.exit(1)

missing = required_keys - set(summary.keys())
if missing:
    print("ERRO: faltam indicadores obrigatórios no CSV:")
    for key in sorted(missing):
        print(f"- {key}")
    sys.exit(1)

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
out_path.chmod(0o600)

print("Resumo local atualizado com segurança.")
print(f"Origem CSV: {csv_path}")
print(f"Saída JSON privada: {out_path}")
print("Somente indicadores agregados permitidos foram importados.")
PY

painel-soprolife/scripts/sync-dashboard-summary.sh
