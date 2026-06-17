#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../" || exit 1

CONFIG_PATH="${SOPROLIFE_SHEETS_CONFIG:-$HOME/.config/soprolife/painel/google-sheets.local.json}"
OUT_PATH="painel-soprolife/data/runtime-status.local.json"

export CONFIG_PATH
export OUT_PATH

python3 - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import json
import os

config_path = Path(os.environ["CONFIG_PATH"])
out_path = Path(os.environ["OUT_PATH"])

configured = False
config_valid = False

if config_path.exists():
    try:
        json.loads(config_path.read_text(encoding="utf-8"))
        configured = True
        config_valid = True
    except Exception:
        configured = False
        config_valid = False

status = {
    "googleSheets": {
        "configured": configured,
        "name": "SoproLife - Painel Interno - Dados Privados" if configured else "Google Sheets privado",
        "type": "google_sheets_privado",
        "statusLabel": "Configurado localmente" if configured else "Não configurado neste ambiente",
        "secretLocation": "external_config",
        "safeToDisplay": True,
        "configValid": config_valid,
        "lastCheckedAt": datetime.now(timezone.utc).isoformat()
    }
}

out_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

print("Status seguro gerado.")
print(f"Arquivo: {out_path}")
print("Nenhuma URL, ID ou token foi copiado.")
PY

python3 -m json.tool "$OUT_PATH" >/dev/null
