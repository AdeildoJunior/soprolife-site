#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

# ---------------------------------------------------------------------------
# M23 — status seguro da FONTE CANÔNICA de dados.
#
# Até o M22 este arquivo descrevia o Google Sheets como fonte do painel. A
# partir do M23 a fonte operacional é o PostgreSQL do Núcleo M15, e o bloco
# googleSheets existe apenas para declarar, explicitamente, que a integração
# foi descomissionada e NÃO é dependência de produção.
#
# Nenhuma URL, ID de planilha, token ou credencial é copiado — só metadados.
# ---------------------------------------------------------------------------

OUT_PATH="painel-soprolife/data/runtime-status.local.json"

export OUT_PATH

python3 - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import sys

sys.path.insert(0, "painel-soprolife/scripts")
import data_source_mode  # noqa: E402

out_path = Path(os.environ["OUT_PATH"])

modo = data_source_mode.current_mode()
postgres_only = data_source_mode.is_postgres_only()
agora = datetime.now(timezone.utc).isoformat()

status = {
    "dataSource": {
        "mode": modo,
        "canonical": "postgresql",
        "name": "PostgreSQL — Núcleo Operacional M15",
        "type": "postgresql_api",
        "statusLabel": ("Fonte única operacional"
                        if postgres_only else "Escape manual de migração ativo"),
        "writePath": "API do Núcleo M15 (loopback)",
        "safeToDisplay": True,
        "lastCheckedAt": agora,
    },
    "googleSheets": {
        # Declaração explícita para que nenhuma tela volte a apresentar o
        # Google Sheets como fonte autorizada ou como saúde de produção.
        "decommissioned": True,
        "requiredForProduction": False,
        "name": "Google Sheets (descomissionado no M23)",
        "type": "legado_descomissionado",
        "statusLabel": "Não é mais fonte do painel",
        "safeToDisplay": True,
        "lastCheckedAt": agora,
    },
    "marketing": {
        # Search Console e GA4 seguem ativos por decisão explícita do M23.
        "searchConsole": data_source_mode.marketing_integrations_enabled(),
        "ga4": data_source_mode.marketing_integrations_enabled(),
        "credential": "conta de servico dedicada, somente leitura",
        "safeToDisplay": True,
        "lastCheckedAt": agora,
    },
}

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

print("Status seguro gerado.")
print(f"Arquivo: {out_path}")
print(f"Fonte canonica: PostgreSQL (modo {modo}).")
print("Nenhuma URL, ID ou token foi copiado.")
PY

python3 -m json.tool "$OUT_PATH" >/dev/null
