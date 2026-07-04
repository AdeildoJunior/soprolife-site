#!/usr/bin/env python3
"""
SoproLife OS Local Core — Conector CRM Contatos B2B (Google Sheets ADC).

Lê a aba "CRM Contatos B2B" (criada sob demanda pelo Apps Script,
ver vincularLeadAClinica em apps-script/command-center-api.gs) e gera
dois arquivos locais, no mesmo padrão de read-leads-sheets.py:

  1. painel-soprolife/data-private/crm-contatos-b2b.local.json  ← completo (gitignored)
     Contém: nome_contato, telefone_whatsapp, email + todos os campos seguros.
     safeToDisplay: false | containsPersonalData: true

  2. painel-soprolife/data/crm-contatos-b2b-summary.local.json  ← resumo seguro (gitignored)
     Contém: contato_id, clinica_id, cargo, papel, status_relacionamento,
             ultima_interacao, proxima_acao, data_proxima_acao, responsavel.
     Nunca contém: nome_contato, telefone_whatsapp, email, observacao.
     safeToDisplay: true | containsPersonalData: false

Nota: nome_contato é um contato institucional B2B (ex.: diretor médico de uma
clínica parceira), não um paciente — mas segue o mesmo padrão de cautela dos
demais conectores: nome/telefone/e-mail ficam fora do resumo público.

Configuração necessária:
    ~/.config/soprolife/painel/google-sheets.local.json
    Campo obrigatório: "spreadsheet_id"
    Campo opcional:    "crm_contatos_b2b_sheet_name" (padrão: "CRM Contatos B2B")

Pré-requisito:
    pip install -r painel-soprolife/requirements-google.txt
    gcloud auth application-default login

Uso:
    python3 painel-soprolife/scripts/read-crm-contatos-b2b-adc.py --show-structure
    python3 painel-soprolife/scripts/read-crm-contatos-b2b-adc.py --dry-run
    python3 painel-soprolife/scripts/read-crm-contatos-b2b-adc.py --write
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_CONFIG_PATH = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
_OUT_PRIVATE = Path("painel-soprolife/data-private/crm-contatos-b2b.local.json")
_OUT_SUMMARY = Path("painel-soprolife/data/crm-contatos-b2b-summary.local.json")

SHEETS_SCOPE  = "https://www.googleapis.com/auth/spreadsheets.readonly"
DEFAULT_SHEET = "CRM Contatos B2B"

CANONICAL_COLUMNS = [
    "contato_id",
    "clinica_id",
    "nome_contato",
    "cargo",
    "telefone_whatsapp",
    "email",
    "papel",
    "status_relacionamento",
    "ultima_interacao",
    "proxima_acao",
    "data_proxima_acao",
    "responsavel",
    "observacao",
]

# Campos privados: apenas em data-private, nunca no resumo seguro
PRIVATE_FIELDS = {"nome_contato", "telefone_whatsapp", "email", "observacao"}

# Campos seguros: podem ir no resumo público
SAFE_FIELDS = {
    "contato_id", "clinica_id", "cargo", "papel", "status_relacionamento",
    "ultima_interacao", "proxima_acao", "data_proxima_acao", "responsavel",
}

BLOCKED_FIELDS = {
    "cpf", "rg", "data_nascimento", "endereco", "endereço",
    "senha", "token", "access_token", "private_key", "client_secret",
}

_CPF_RE   = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE  = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
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
        print(f"  Esperado em: {_CONFIG_PATH}")
        print("  Campo obrigatório: spreadsheet_id")
        print("  Campo opcional:    crm_contatos_b2b_sheet_name (padrão: CRM Contatos B2B)")
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

    sheet = cfg.get("crm_contatos_b2b_sheet_name", DEFAULT_SHEET).strip() or DEFAULT_SHEET
    return sid, sheet


def _fetch_rows(build, google_auth_default, spreadsheet_id: str, sheet_name: str) -> list:
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
        elif "404" in msg or "NOT_FOUND" in msg:
            print("ERRO: planilha ou aba não encontrada.")
            print(f"  Verifique o nome da aba: {sheet_name!r}")
            print("  Isso é esperado se a aba 'CRM Contatos B2B' ainda não foi criada")
            print("  (ela só existe depois do primeiro vínculo via vincularLeadAClinica).")
        elif "401" in msg or "UNAUTHENTICATED" in msg:
            print("ERRO: credenciais inválidas ou expiradas.")
            print("  Execute: gcloud auth application-default login")
        else:
            print(f"ERRO: falha ao ler a planilha — {exc}")
        sys.exit(1)

    rows = result.get("values", [])
    print(f"rows_read: {len(rows)}")
    return rows


def _show_structure(rows: list, sheet_name: str) -> None:
    print(f"\nEstrutura da aba: {sheet_name}")
    print(f"  rows_total: {len(rows)}")

    if not rows:
        print("  (aba vazia ou inexistente)")
        return

    headers = [str(c).strip() for c in rows[0]]
    print(f"  columns_count: {len(headers)}")
    print(f"  headers: {' | '.join(headers)}")
    print()

    for h in headers:
        hl = h.lower().replace(" ", "_")
        if hl in BLOCKED_FIELDS:
            print(f"  BLOQUEADA (nunca exportada):  {h}")
        elif hl in PRIVATE_FIELDS:
            print(f"  PRIVADA (só em data-private): {h}")
        elif hl in SAFE_FIELDS:
            print(f"  OK (segura, será exportada):  {h}")
        else:
            print(f"  IGNORADA (não reconhecida):   {h}")

    print()
    print("Nota: apenas cabeçalhos inspecionados. Valores não foram impressos.")


def _parse_records(rows: list) -> list[dict]:
    raw_headers = [str(c).strip() for c in rows[0]]
    headers_lower = [h.lower().replace(" ", "_") for h in raw_headers]

    col_map: dict[str, int] = {}
    for i, hl in enumerate(headers_lower):
        if hl in BLOCKED_FIELDS:
            print(f"ERRO: coluna bloqueada '{raw_headers[i]}' encontrada na aba {DEFAULT_SHEET}.")
            sys.exit(1)
        if hl in CANONICAL_COLUMNS and hl not in col_map:
            col_map[hl] = i

    if not col_map:
        print(f"ERRO: nenhuma coluna reconhecida no cabeçalho da aba {DEFAULT_SHEET}.")
        print(f"  Cabeçalhos encontrados: {' | '.join(raw_headers)}")
        sys.exit(1)

    print(f"Colunas mapeadas: {', '.join(sorted(col_map.keys()))}")

    records = []
    for row_num, row in enumerate(rows[1:], start=2):
        rec: dict[str, str] = {}
        for canon, idx in col_map.items():
            val = str(row[idx]).strip() if idx < len(row) else ""
            rec[canon] = val

        meaningful = {k: v for k, v in rec.items() if v and k != "contato_id"}
        if not meaningful:
            continue

        full_text = " ".join(rec.values())
        if _CPF_RE.search(full_text):
            print(f"ERRO: padrão de CPF detectado na linha {row_num}.")
            sys.exit(1)

        records.append(rec)

    return records


def _build_outputs(records: list[dict], now_iso: str) -> tuple[dict, dict]:
    private_contatos = []
    summary_contatos = []

    for rec in records:
        private_contatos.append({k: v for k, v in rec.items() if v != ""})
        safe_rec = {k: v for k, v in rec.items() if k in SAFE_FIELDS and v != ""}
        summary_contatos.append(safe_rec)

    payload_private = {
        "source": {
            "type": "google_sheets_adc",
            "safeToDisplay": False,
            "containsPersonalData": True,
            "containsHealthData": False,
            "generatedAt": now_iso,
        },
        "contatos": private_contatos,
    }

    payload_summary = {
        "source": {
            "type": "google_sheets_adc_summary",
            "safeToDisplay": True,
            "containsPersonalData": False,
            "containsHealthData": False,
            "generatedAt": now_iso,
        },
        "contatos": summary_contatos,
    }

    return payload_private, payload_summary


def _validate_summary(payload_summary: dict) -> None:
    contatos = payload_summary.get("contatos", [])
    errors = 0

    for i, rec in enumerate(contatos, start=1):
        for field in PRIVATE_FIELDS:
            if field in rec:
                print(f"ERRO interno: campo '{field}' vazou para o resumo no registro {i}.")
                errors += 1

        rec_text = json.dumps(rec, ensure_ascii=False)
        if _FONE_RE.search(rec_text):
            print(f"ERRO interno: padrão de telefone detectado no resumo, registro {i}.")
            errors += 1
        if _CPF_RE.search(rec_text):
            print(f"ERRO interno: padrão de CPF detectado no resumo, registro {i}.")
            errors += 1
        if _EMAIL_RE.search(rec_text):
            print(f"ERRO interno: padrão de e-mail detectado no resumo, registro {i}.")
            errors += 1

    if errors:
        print(f"ERRO: {errors} violação(ões) no resumo. Abortando gravação.")
        sys.exit(1)


def _write_files(payload_private: dict, payload_summary: dict) -> None:
    _OUT_PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PRIVATE.write_text(
        json.dumps(payload_private, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _OUT_PRIVATE.chmod(0o600)

    _OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    _OUT_SUMMARY.write_text(
        json.dumps(payload_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _OUT_SUMMARY.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Conector CRM Contatos B2B via Google Sheets ADC — SoproLife"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show-structure", action="store_true",
                        help="Inspeciona cabeçalhos da aba (diagnóstico, sem gravar)")
    group.add_argument("--dry-run", action="store_true",
                        help="Valida leitura e sanitização sem gravar (padrão)")
    group.add_argument("--write", action="store_true",
                        help="Valida e grava data-private/crm-contatos-b2b.local.json + "
                             "data/crm-contatos-b2b-summary.local.json")
    args = parser.parse_args()

    mode = "show-structure" if args.show_structure else ("write" if args.write else "dry-run")

    print("SoproLife OS Local Core — CRM Contatos B2B connector (ADC)")
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

    records = _parse_records(rows) if rows else []
    now_iso = datetime.now(timezone.utc).isoformat()

    if not records:
        payload_private, payload_summary = _build_outputs([], now_iso)
        if mode == "dry-run":
            print()
            print("OK: aba vazia — 0 contatos encontrados.")
            print("next_step: use --write para gravar arquivos locais vazios.")
            return 0
        _write_files(payload_private, payload_summary)
        print()
        print("OK: aba vazia — gerado arquivo local com 0 contatos.")
        return 0

    print(f"records_valid: {len(records)}")

    payload_private, payload_summary = _build_outputs(records, now_iso)

    print()
    print("Validando resumo seguro...")
    _validate_summary(payload_summary)
    print("Validação OK. Nenhum dado pessoal no resumo.")

    n_priv = len(payload_private["contatos"])
    n_summ = len(payload_summary["contatos"])

    if mode == "dry-run":
        print()
        print(f"Privado:  {n_priv} contatos (com nome/telefone/email)")
        print(f"Resumo:   {n_summ} contatos (sem dados pessoais)")
        print()
        print("next_step: use --write para gravar os dois arquivos locais.")
        return 0

    _write_files(payload_private, payload_summary)

    print()
    print(f"Privado gravado:  {_OUT_PRIVATE}  ({n_priv} contatos, chmod 600)")
    print(f"Resumo gravado:   {_OUT_SUMMARY}  ({n_summ} contatos, chmod 600)")
    print()
    print("Próximo passo: painel-soprolife/scripts/check-access.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
