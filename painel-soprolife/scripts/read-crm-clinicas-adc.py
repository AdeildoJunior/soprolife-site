#!/usr/bin/env python3
"""
SoproLife OS Local Core — Conector CRM Clínicas (Google Sheets ADC).

Lê a aba "CRM Clinicas" usando Application Default Credentials (gcloud).
Exporta apenas dados institucionais/comerciais. Nunca exporta:
  - observacao (campo livre)
  - telefone, CPF, e-mail, nome de paciente, dado clínico

Pré-requisito:
    pip install -r painel-soprolife/requirements-google.txt
    gcloud auth application-default login

Uso:
    python3 painel-soprolife/scripts/read-crm-clinicas-adc.py --show-structure
    python3 painel-soprolife/scripts/read-crm-clinicas-adc.py --dry-run
    python3 painel-soprolife/scripts/read-crm-clinicas-adc.py --write
"""

import argparse
import json
import re
import sys
from pathlib import Path

_CONFIG_PATH = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
_OUT_PATH = Path("~/.config/soprolife/painel/crm-clinicas.json").expanduser()

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
DEFAULT_CRM_SHEET = "CRM Clinicas"

ALLOWED_COLUMNS = {
    "clinica_id",
    "nome_clinica",
    "bairro",
    "regiao",
    "tipo_clinica",
    "etapa",
    "ultima_interacao",
    "proxima_acao",
    "data_proxima_acao",
    "responsavel",
    "prioridade",
}

BLOCKED_COLUMNS = {
    "observacao",
    "observação",
    "cpf",
    "telefone",
    "celular",
    "email",
    "e-mail",
    "whatsapp",
    "nome_paciente",
    "paciente",
    "pedido_medico",
    "pedido médico",
    "laudo",
    "diagnostico",
    "diagnóstico",
    "endereco",
    "endereço",
}

FORBIDDEN_TERMS = [
    "cpf",
    "telefone",
    "celular",
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
    "nome completo",
]

_CPF_RE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def _load_google_libs():
    try:
        from googleapiclient.discovery import build
        from google.auth import default as google_auth_default
        return build, google_auth_default
    except ImportError as exc:
        print(f"ERRO: dependências não instaladas — {exc}")
        print()
        print("Instale com:")
        print("  pip install -r painel-soprolife/requirements-google.txt")
        sys.exit(1)


def _load_config() -> tuple[str, str]:
    if not _CONFIG_PATH.exists():
        print("ERRO: configuração não encontrada.")
        print("  Esperado em: ~/.config/soprolife/painel/google-sheets.local.json")
        print("  Campo obrigatório: spreadsheet_id")
        print("  Campo opcional: crm_clinicas_sheet_name (padrão: CRM Clinicas)")
        sys.exit(1)

    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERRO: configuração JSON inválida — {exc}")
        sys.exit(1)

    if not isinstance(cfg, dict):
        print("ERRO: configuração deve ser um objeto JSON.")
        sys.exit(1)

    sid = cfg.get("spreadsheet_id", "").strip()
    if not sid:
        print("ERRO: spreadsheet_id ausente ou vazio na configuração.")
        sys.exit(1)

    sheet = cfg.get("crm_clinicas_sheet_name", DEFAULT_CRM_SHEET).strip() or DEFAULT_CRM_SHEET
    return sid, sheet


def _check_value(value: str, col_name: str, row_num: int) -> None:
    lower = value.lower()
    for term in FORBIDDEN_TERMS:
        if term in lower:
            print(f"ERRO: termo proibido '{term}' na coluna '{col_name}', linha {row_num}.")
            sys.exit(1)
    if _CPF_RE.search(value):
        print(f"ERRO: padrão de CPF detectado na coluna '{col_name}', linha {row_num}.")
        sys.exit(1)
    if _FONE_RE.search(value):
        print(f"ERRO: padrão de telefone detectado na coluna '{col_name}', linha {row_num}.")
        sys.exit(1)
    if _EMAIL_RE.search(value):
        print(f"ERRO: padrão de e-mail detectado na coluna '{col_name}', linha {row_num}.")
        sys.exit(1)


def _fetch_rows(build, google_auth_default, spreadsheet_id: str, sheet_name: str) -> list[list[str]]:
    print("Conectando à API Google Sheets...")

    try:
        credentials, _ = google_auth_default(scopes=[SHEETS_SCOPE])
    except Exception as exc:
        print(f"ERRO: falha na autenticação ADC — {exc}")
        print()
        print("Execute para autenticar:")
        print(f"  gcloud auth application-default login \\")
        print(f"      --scopes={SHEETS_SCOPE}")
        sys.exit(1)

    try:
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        print(f"ERRO: não foi possível inicializar o cliente Sheets — {exc}")
        sys.exit(1)

    range_notation = f"{sheet_name}!A:Z"
    print(f"Lendo aba: {sheet_name}")

    try:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_notation)
            .execute()
        )
    except Exception as exc:
        msg = str(exc)
        if "403" in msg or "PERMISSION_DENIED" in msg:
            print("ERRO: acesso negado à planilha.")
            print("  Verifique se o usuário do ADC tem permissão de leitura.")
        elif "404" in msg or "NOT_FOUND" in msg:
            print("ERRO: planilha ou aba não encontrada.")
            print(f"  Verifique o nome da aba: {sheet_name!r}")
        elif "401" in msg or "UNAUTHENTICATED" in msg:
            print("ERRO: credenciais inválidas ou expiradas.")
            print("  Execute: gcloud auth application-default login")
        else:
            print(f"ERRO: falha ao ler a planilha — {exc}")
        sys.exit(1)

    rows = result.get("values", [])
    print(f"rows_read: {len(rows)}")

    if not rows:
        print("ERRO: a aba está vazia.")
        sys.exit(1)

    return rows


def _show_structure(rows: list, sheet_name: str) -> None:
    print(f"Estrutura da aba: {sheet_name}")
    print(f"  rows_total: {len(rows)}")

    if not rows:
        print("  (aba vazia)")
        return

    headers = [str(c).strip() for c in rows[0]]
    print(f"  columns_count: {len(headers)}")
    print(f"  headers: {' | '.join(headers)}")
    print()

    for h in headers:
        h_lower = h.lower()
        if h_lower in BLOCKED_COLUMNS:
            print(f"  BLOQUEADA (não exportada): {h}")
        elif h_lower in ALLOWED_COLUMNS:
            print(f"  OK (será exportada):       {h}")
        else:
            print(f"  IGNORADA (não reconhecida): {h}")

    print()
    print("Nota: somente cabeçalhos foram inspecionados. Valores não foram impressos.")


def _parse_records(rows: list) -> list[dict]:
    headers = [str(c).strip().lower() for c in rows[0]]

    blocked_found = [h for h in headers if h in BLOCKED_COLUMNS]
    if blocked_found:
        print(f"AVISO: colunas bloqueadas detectadas e ignoradas: {', '.join(blocked_found)}")

    col_indices: dict[str, int] = {}
    for i, h in enumerate(headers):
        if h in ALLOWED_COLUMNS:
            col_indices[h] = i

    if not col_indices:
        print("ERRO: nenhuma coluna reconhecida encontrada no cabeçalho.")
        print(f"  Cabeçalhos encontrados: {' | '.join(headers)}")
        print(f"  Colunas esperadas: {' | '.join(sorted(ALLOWED_COLUMNS))}")
        sys.exit(1)

    print(f"Colunas exportadas: {', '.join(sorted(col_indices.keys()))}")

    records = []
    for row_num, row in enumerate(rows[1:], start=2):
        record: dict[str, str] = {}
        for col_name, idx in col_indices.items():
            raw = str(row[idx]).strip() if idx < len(row) else ""
            _check_value(raw, col_name, row_num)
            record[col_name] = raw

        if not any(record.values()):
            continue

        records.append(record)

    return records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Conector CRM Clínicas via Google Sheets ADC — SoproLife"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show-structure", action="store_true",
                       help="Inspeciona estrutura da aba (diagnóstico, sem gravar)")
    group.add_argument("--dry-run", action="store_true",
                       help="Valida leitura sem gravar (padrão)")
    group.add_argument("--write", action="store_true",
                       help="Valida e grava em ~/.config/soprolife/painel/crm-clinicas.json")
    args = parser.parse_args()

    if args.show_structure:
        mode = "show-structure"
    elif args.write:
        mode = "write"
    else:
        mode = "dry-run"

    print("SoproLife OS Local Core — CRM Clínicas connector (ADC)")
    print(f"mode: {mode}")
    print("auth: Application Default Credentials")
    print()

    build, google_auth_default = _load_google_libs()
    spreadsheet_id, sheet_name = _load_config()

    print("config: carregada")
    print(f"sheet_name: {sheet_name}")
    print()

    rows = _fetch_rows(build, google_auth_default, spreadsheet_id, sheet_name)

    if mode == "show-structure":
        _show_structure(rows, sheet_name)
        return 0

    records = _parse_records(rows)

    if not records:
        print("ERRO: nenhum registro válido encontrado na aba.")
        return 1

    print(f"records_valid: {len(records)}")
    print()
    print("Validação concluída. Nenhum dado sensível detectado.")

    if mode == "dry-run":
        print()
        print("next_step: use --write para gravar em ~/.config/soprolife/painel/crm-clinicas.json")
        return 0

    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _OUT_PATH.chmod(0o600)

    print()
    print("Gravado em: ~/.config/soprolife/painel/crm-clinicas.json")
    print()
    print("Execute a seguir para sincronizar com o painel:")
    print("  painel-soprolife/scripts/sync-crm-clinicas.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
