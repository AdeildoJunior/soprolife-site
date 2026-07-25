#!/usr/bin/env python3
"""
SoproLife OS Local Core — Google Sheets connector (dry-run).

Valida a estrutura de um arquivo de configuração local sem conectar à API
do Google Sheets e sem imprimir nenhum valor sensível.

Uso normal (exemplo seguro):
    python3 painel-soprolife/scripts/read-sheets-summary-dry-run.py \
        --config painel-soprolife/core/examples/google-sheets.local.example.json

Uso com config privada real (requer flag explícita):
    python3 painel-soprolife/scripts/read-sheets-summary-dry-run.py \
        --allow-private-config
"""

import argparse
import json
import sys
from pathlib import Path

_DEFAULT_PRIVATE_CONFIG = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
_PRIVATE_CONFIG_ROOT = Path("~/.config/soprolife").expanduser()

REQUIRED_KEYS = {"spreadsheet_id", "sheet_name", "service_account_file"}
# Valores dessas chaves nunca são impressos
SENSITIVE_KEYS = {"spreadsheet_id", "service_account_file"}


def _is_private_path(path: Path) -> bool:
    try:
        path.resolve().relative_to(_PRIVATE_CONFIG_ROOT.resolve())
        return True
    except ValueError:
        return False


def _validate_structure(data: dict) -> list[str]:
    errors = []
    for key in REQUIRED_KEYS:
        if key not in data:
            errors.append(f"chave ausente: {key}")
        elif not isinstance(data[key], str) or not data[key].strip():
            errors.append(f"chave vazia ou inválida: {key}")
    unknown = set(data.keys()) - REQUIRED_KEYS
    if unknown:
        errors.append(f"chaves não reconhecidas: {', '.join(sorted(unknown))}")
    return errors


def _present(data: dict, key: str) -> bool:
    return key in data and isinstance(data[key], str) and bool(data[key].strip())


def main() -> int:
    # M23 — guarda de fonte canônica. O painel opera em modo
    # postgresql_only: nenhum leitor de Google Sheets pode ser executado
    # pelo pipeline automático nem pelo timer de produção. Só uma decisão
    # humana explícita (SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1) libera
    # este utilitário, e apenas para migração/forense pontual.
    import sys as _sys, pathlib as _pathlib
    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    import data_source_mode
    data_source_mode.block_legacy_sheets('read-sheets-summary-dry-run.py')

    parser = argparse.ArgumentParser(
        description="Dry-run do conector Google Sheets — SoproLife (sem API real)"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_PRIVATE_CONFIG,
        metavar="CAMINHO",
        help="Arquivo JSON de configuração (padrão: ~/.config/soprolife/painel/google-sheets.local.json)",
    )
    parser.add_argument(
        "--allow-private-config",
        action="store_true",
        help="Permite ler o arquivo de configuração privado em ~/.config/soprolife/",
    )
    args = parser.parse_args()

    config_path = args.config.expanduser().resolve()

    print("SoproLife OS Local Core — Google Sheets connector")
    print("mode: dry-run")
    print()

    # Portão de segurança: recusa ler config privada sem flag explícita
    if _is_private_path(config_path) and not args.allow_private_config:
        print("config_detected: false")
        print("sheet_name_detected: false")
        print("credential_path_detected: false")
        print()
        print("AVISO: caminho de configuração privada detectado.")
        print("  O script recusa ler esse arquivo sem autorização explícita.")
        print()
        print("  Para testar com exemplo seguro:")
        print("    python3 painel-soprolife/scripts/read-sheets-summary-dry-run.py \\")
        print("        --config painel-soprolife/core/examples/google-sheets.local.example.json")
        print()
        print("  Para usar a configuração privada real (quando disponível):")
        print("    python3 painel-soprolife/scripts/read-sheets-summary-dry-run.py \\")
        print("        --allow-private-config")
        print()
        print("next_step: API Google ainda não conectada")
        return 0

    if not config_path.exists():
        print("config_detected: false")
        print("sheet_name_detected: false")
        print("credential_path_detected: false")
        print()
        print(f"ERRO: arquivo não encontrado: {config_path}")
        print()
        print("next_step: API Google ainda não conectada")
        return 1

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print("config_detected: false")
        print("sheet_name_detected: false")
        print("credential_path_detected: false")
        print()
        print(f"ERRO: JSON inválido — {exc}")
        print()
        print("next_step: API Google ainda não conectada")
        return 1

    if not isinstance(data, dict):
        print("config_detected: false")
        print("sheet_name_detected: false")
        print("credential_path_detected: false")
        print()
        print("ERRO: o arquivo de configuração deve ser um objeto JSON.")
        print()
        print("next_step: API Google ainda não conectada")
        return 1

    # Reporta presença das chaves — valores sensíveis nunca são impressos
    print(f"config_detected: {str(_present(data, 'spreadsheet_id')).lower()}")
    print(f"sheet_name_detected: {str(_present(data, 'sheet_name')).lower()}")
    print(f"credential_path_detected: {str(_present(data, 'service_account_file')).lower()}")
    print()

    errors = _validate_structure(data)
    if errors:
        print("ERRO: estrutura de configuração inválida:")
        for err in errors:
            print(f"  - {err}")
        print()
        print("next_step: API Google ainda não conectada")
        return 1

    print("Estrutura de configuração válida.")
    print()
    print("next_step: API Google ainda não conectada")
    return 0


if __name__ == "__main__":
    sys.exit(main())
