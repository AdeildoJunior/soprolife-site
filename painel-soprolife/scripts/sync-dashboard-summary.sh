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

SOURCE_PATH="${SOPROLIFE_SUMMARY_CONFIG:-$HOME/.config/soprolife/painel/resumo-dashboard.json}"
OUT_PATH="painel-soprolife/data/resumo-dashboard.local.json"

export SOURCE_PATH
export OUT_PATH

python3 - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import sys

source_path = Path(os.environ["SOURCE_PATH"])
out_path = Path(os.environ["OUT_PATH"])

allowed_keys = {
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
    "pacientesEmAcompanhamento",
    "examesEspirometriaRealizados",
    "teleconsultasRealizadas",
    "followupsPendentes",
    "lembretesWhatsAppPendentes",
    "recorrenciasAtivas",
    "consultasPrevistas",
}

labels = {
    "totalLeads": "Total de leads",
    "leadsNovos": "Leads novos",
    "leadsAgendados": "Leads agendados",
    "leadsConcluidos": "Leads concluídos",
    "clinicasCadastradas": "Clínicas cadastradas",
    "tarefasPendentes": "Tarefas pendentes",
    "receitaPrevista": "Receita prevista",
    "receitaRecebida": "Receita recebida",
    "conteudosPlanejados": "Conteúdos planejados",
    "eventosAgendados": "Eventos agendados",
    "pacientesEmAcompanhamento": "Pacientes em acompanhamento",
    "examesEspirometriaRealizados": "Espirometrias realizadas",
    "teleconsultasRealizadas": "Teleconsultas realizadas",
    "followupsPendentes": "Follow-ups pendentes",
    "lembretesWhatsAppPendentes": "Lembretes WhatsApp pendentes",
    "recorrenciasAtivas": "Recorrências ativas",
    "consultasPrevistas": "Consultas previstas",
}

if not source_path.exists():
    print("ERRO: arquivo local de resumo não encontrado.")
    print("Esperado em: ~/.config/soprolife/painel/resumo-dashboard.json")
    sys.exit(1)

raw = json.loads(source_path.read_text(encoding="utf-8"))

extra = set(raw.keys()) - allowed_keys
if extra:
    print("ERRO: resumo-dashboard.json contém chaves não permitidas.")
    print("Chaves extras:", ", ".join(sorted(extra)))
    sys.exit(1)

summary = {}
for key in allowed_keys:
    value = raw.get(key, 0)
    if not isinstance(value, (int, float)):
        print(f"ERRO: {key} precisa ser número.")
        sys.exit(1)
    if value < 0:
        print(f"ERRO: {key} não pode ser negativo.")
        sys.exit(1)
    summary[key] = value

_old_card_keys = [
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
]
_new_card_keys = [
    "pacientesEmAcompanhamento",
    "examesEspirometriaRealizados",
    "teleconsultasRealizadas",
    "followupsPendentes",
    "lembretesWhatsAppPendentes",
    "recorrenciasAtivas",
    "consultasPrevistas",
]

safe_payload = {
    "source": {
        "type": "local_safe_summary",
        "safeToDisplay": True,
        "containsPersonalData": False,
        "containsHealthData": False,
        "generatedAt": datetime.now(timezone.utc).isoformat()
    },
    "cards": [
        {"key": key, "label": labels[key], "value": summary[key]}
        for key in _old_card_keys
    ] + [
        {"key": key, "label": labels[key], "value": summary[key]}
        for key in _new_card_keys
        if key in raw
    ]
}

out_path.write_text(
    json.dumps(safe_payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8"
)

print("Resumo seguro sincronizado.")
print(f"Origem: {source_path}")
print(f"Saída: {out_path}")
print("Nenhum dado pessoal, clínico, CPF, telefone ou pedido médico foi exportado.")
PY

python3 -m json.tool "$OUT_PATH" >/dev/null
