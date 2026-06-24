#!/usr/bin/env python3
"""
SoproLife OS Local Core — Conector Leads (Google Sheets ADC).

Lê a aba "Leads" da planilha privada e gera dois arquivos locais:

  1. painel-soprolife/data-private/leads.local.json   ← dados completos (gitignored)
     Contém: nome, telefone_whatsapp, observacao + todos os campos seguros.
     safeToDisplay: false | containsPersonalData: true

  2. painel-soprolife/data/leads-summary.local.json   ← resumo seguro (gitignored)
     Contém: lead_id, etapa, servico_interesse, origem, canal, bairro_regiao,
             tem_pedido_medico, responsavel, proxima_acao, data_proxima_acao, data_contato.
     Nunca contém: nome, telefone_whatsapp, observacao.
     safeToDisplay: true | containsPersonalData: false

Configuração necessária:
    ~/.config/soprolife/painel/google-sheets.local.json
    Campo obrigatório: "spreadsheet_id"
    Campo opcional:   "leads_sheet_name"  (padrão: "Leads")

Pré-requisito:
    pip install -r painel-soprolife/requirements-google.txt
    gcloud auth application-default login

Uso:
    python3 painel-soprolife/scripts/read-leads-sheets.py --show-structure
    python3 painel-soprolife/scripts/read-leads-sheets.py --dry-run
    python3 painel-soprolife/scripts/read-leads-sheets.py --write
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Configuração ───────────────────────────────────────────────────────────────

_CONFIG_PATH   = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
_OUT_PRIVATE   = Path("painel-soprolife/data-private/leads.local.json")
_OUT_SUMMARY   = Path("painel-soprolife/data/leads-summary.local.json")

SHEETS_SCOPE   = "https://www.googleapis.com/auth/spreadsheets.readonly"
DEFAULT_SHEET  = "Leads"

# ── Colunas canônicas (14-col v2) ─────────────────────────────────────────────

CANONICAL_COLUMNS = [
    "lead_id",
    "data_contato",
    "nome",
    "telefone_whatsapp",
    "servico_interesse",
    "origem",
    "canal",
    "bairro_regiao",
    "tem_pedido_medico",
    "etapa",
    "responsavel",
    "proxima_acao",
    "data_proxima_acao",
    "observacao",
]

# Aliases: nome antigo (lowercase) → nome canônico
COLUMN_ALIASES = {
    "data_entrada":              "data_contato",
    "data":                      "data_contato",
    "observacao_privada_minima": "observacao",
    "observacao_anonima":        "observacao",
    "status":                    "etapa",
    "servico":                   "servico_interesse",
    "lead":                      "nome",
    "proximaacao":               "proxima_acao",
    "proxima_acao":              "proxima_acao",
    "dataproximaacao":           "data_proxima_acao",
    "data_proxima_acao":         "data_proxima_acao",
    "preferencia_atendimento":   None,   # coluna removida — ignorada
    "valor_informado":           None,
    "consentimento_whatsapp":    None,
}

# Campos privados: apenas em data-private, nunca no resumo seguro
PRIVATE_FIELDS = {"nome", "telefone_whatsapp", "observacao"}

# Campos seguros: podem ir no resumo público
SAFE_FIELDS = {
    "lead_id", "data_contato", "servico_interesse", "origem", "canal",
    "bairro_regiao", "tem_pedido_medico", "etapa", "responsavel",
    "proxima_acao", "data_proxima_acao",
}

# Campos bloqueados: nunca devem aparecer em NENHUM arquivo de saída
BLOCKED_FIELDS = {
    "cpf", "rg", "data_nascimento", "endereco", "endereço",
    "pedido_medico", "pedido médico", "laudo", "diagnostico", "diagnóstico",
    "senha", "token", "access_token", "private_key", "client_secret",
    "nome_completo", "cliente_secret",
}

_CPF_RE   = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


# ── Helpers ────────────────────────────────────────────────────────────────────

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
        print("  Campo opcional:    leads_sheet_name (padrão: Leads)")
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

    sheet = cfg.get("leads_sheet_name", DEFAULT_SHEET).strip() or DEFAULT_SHEET
    return sid, sheet


def _fetch_rows(build, google_auth_default, spreadsheet_id: str, sheet_name: str) -> list:
    print(f"Conectando à API Google Sheets...")

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

    return rows


def _show_structure(rows: list, sheet_name: str) -> None:
    print(f"\nEstrutura da aba: {sheet_name}")
    print(f"  rows_total: {len(rows)}")

    if not rows:
        print("  (aba vazia)")
        return

    headers = [str(c).strip() for c in rows[0]]
    print(f"  columns_count: {len(headers)}")
    print(f"  headers: {' | '.join(headers)}")
    print()

    for h in headers:
        hl = h.lower().replace(" ", "_")
        alias = COLUMN_ALIASES.get(hl)
        if hl in BLOCKED_FIELDS:
            print(f"  BLOQUEADA (nunca exportada):  {h}")
        elif alias is None and hl in COLUMN_ALIASES:
            print(f"  REMOVIDA (ignorada):          {h}")
        elif alias is not None and alias != hl:
            dest = alias
            if dest in PRIVATE_FIELDS:
                print(f"  PRIVADA (→ {dest}, só em data-private): {h}")
            else:
                print(f"  OK (→ {dest}, será exportada):          {h}")
        elif hl in SAFE_FIELDS:
            print(f"  OK (segura, será exportada):  {h}")
        elif hl in PRIVATE_FIELDS:
            print(f"  PRIVADA (só em data-private): {h}")
        else:
            print(f"  IGNORADA (não reconhecida):   {h}")

    print()
    print("Nota: apenas cabeçalhos inspecionados. Valores não foram impressos.")


def _parse_records(rows: list) -> list[dict]:
    """
    Mapeia os cabeçalhos para os nomes canônicos e converte cada linha em um dict.
    Retorna lista de dicts com os campos canônicos disponíveis.
    Valida que nenhum campo bloqueado (cpf, senha, laudo) está presente.
    """
    raw_headers = [str(c).strip() for c in rows[0]]
    headers_lower = [h.lower().replace(" ", "_") for h in raw_headers]

    # Mapa: nome_canônico → índice na linha
    col_map: dict[str, int] = {}
    skipped = []

    for i, hl in enumerate(headers_lower):
        # Verifica campo completamente bloqueado
        if hl in BLOCKED_FIELDS:
            print(f"ERRO: coluna bloqueada '{raw_headers[i]}' encontrada na aba Leads.")
            print("  Remova CPFs, senhas, laudos e dados médicos antes de exportar.")
            sys.exit(1)

        # Resolve alias
        alias = COLUMN_ALIASES.get(hl)
        if alias is None and hl in COLUMN_ALIASES:
            # Mapeado explicitamente para None = coluna removida, ignorar
            skipped.append(raw_headers[i])
            continue

        canon = alias if alias is not None else hl

        if canon not in set(CANONICAL_COLUMNS):
            # Coluna não reconhecida — ignora com aviso
            continue

        if canon not in col_map:
            col_map[canon] = i

    if skipped:
        print(f"AVISO: colunas removidas (ignoradas): {', '.join(skipped)}")

    if not col_map:
        print("ERRO: nenhuma coluna reconhecida no cabeçalho da aba Leads.")
        print(f"  Cabeçalhos encontrados: {' | '.join(raw_headers)}")
        sys.exit(1)

    print(f"Colunas mapeadas: {', '.join(sorted(col_map.keys()))}")

    records = []
    for row_num, row in enumerate(rows[1:], start=2):
        rec: dict[str, str] = {}
        for canon, idx in col_map.items():
            val = str(row[idx]).strip() if idx < len(row) else ""
            rec[canon] = val

        # Linha vazia → pula
        meaningful = {k: v for k, v in rec.items() if v and k != "lead_id"}
        if not meaningful:
            continue

        # Valida: nunca CPF em nenhum campo (mesmo no privado)
        full_text = " ".join(rec.values())
        if _CPF_RE.search(full_text):
            print(f"ERRO: padrão de CPF detectado na linha {row_num}.")
            print("  CPF nunca deve ser inserido na aba Leads.")
            sys.exit(1)

        records.append(rec)

    return records


def _build_outputs(records: list[dict], now_iso: str) -> tuple[dict, dict]:
    """
    Retorna (payload_privado, payload_resumo).

    Privado: todos os campos canônicos, incluindo nome, telefone, observacao.
    Resumo: apenas SAFE_FIELDS (sem nome, sem telefone, sem observacao).
    """
    private_leads = []
    summary_leads = []

    for rec in records:
        # Privado: tudo
        private_leads.append({k: v for k, v in rec.items() if v != ""})

        # Resumo: apenas campos seguros (sem PII)
        safe_rec = {k: v for k, v in rec.items() if k in SAFE_FIELDS and v != ""}
        summary_leads.append(safe_rec)

    payload_private = {
        "source": {
            "type": "google_sheets_adc",
            "safeToDisplay": False,
            "containsPersonalData": True,
            "containsHealthData": False,
            "generatedAt": now_iso,
        },
        "leads": private_leads,
    }

    payload_summary = {
        "source": {
            "type": "google_sheets_adc_summary",
            "safeToDisplay": True,
            "containsPersonalData": False,
            "containsHealthData": False,
            "generatedAt": now_iso,
        },
        "leads": summary_leads,
    }

    return payload_private, payload_summary


def _validate_summary(payload_summary: dict) -> None:
    """
    Valida que o resumo seguro não contém PII antes de gravar.
    """
    _FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
    leads = payload_summary.get("leads", [])
    errors = 0

    for i, rec in enumerate(leads, start=1):
        if "nome" in rec:
            print(f"ERRO interno: campo 'nome' vazou para o resumo no registro {i}.")
            errors += 1
        if "telefone_whatsapp" in rec:
            print(f"ERRO interno: campo 'telefone_whatsapp' vazou para o resumo no registro {i}.")
            errors += 1
        if "observacao" in rec:
            print(f"ERRO interno: campo 'observacao' vazou para o resumo no registro {i}.")
            errors += 1

        rec_text = json.dumps(rec, ensure_ascii=False)
        if _FONE_RE.search(rec_text):
            print(f"ERRO interno: padrão de telefone detectado no resumo, registro {i}.")
            errors += 1
        if _CPF_RE.search(rec_text):
            print(f"ERRO interno: padrão de CPF detectado no resumo, registro {i}.")
            errors += 1
        if _EMAIL_RE.search(rec_text):
            print(f"AVISO: padrão de e-mail detectado no resumo, registro {i}.")

    if errors:
        print(f"ERRO: {errors} violação(ões) no resumo. Abortando gravação.")
        sys.exit(1)


def _write_files(payload_private: dict, payload_summary: dict) -> None:
    # Privado
    _OUT_PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PRIVATE.write_text(
        json.dumps(payload_private, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _OUT_PRIVATE.chmod(0o600)

    # Resumo
    _OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    _OUT_SUMMARY.write_text(
        json.dumps(payload_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _OUT_SUMMARY.chmod(0o600)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Conector Leads via Google Sheets ADC — SoproLife"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--show-structure", action="store_true",
        help="Inspeciona cabeçalhos da aba (diagnóstico, sem gravar)",
    )
    group.add_argument(
        "--dry-run", action="store_true",
        help="Valida leitura e sanitização sem gravar (padrão)",
    )
    group.add_argument(
        "--write", action="store_true",
        help="Valida e grava data-private/leads.local.json + data/leads-summary.local.json",
    )
    args = parser.parse_args()

    mode = "show-structure" if args.show_structure else ("write" if args.write else "dry-run")

    print("SoproLife OS Local Core — Leads connector (ADC)")
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

    if not records:
        now_iso = datetime.now(timezone.utc).isoformat()
        payload_private, payload_summary = _build_outputs([], now_iso)
        if mode == "dry-run":
            print()
            print("OK: aba Leads vazia — 0 leads encontrados.")
            print("next_step: use --write para gravar arquivos locais vazios.")
            return 0
        _write_files(payload_private, payload_summary)
        print()
        print("OK: aba Leads vazia — gerado arquivo local com 0 leads.")
        print(f"  Privado:  {_OUT_PRIVATE}  (0 leads, chmod 600)")
        print(f"  Resumo:   {_OUT_SUMMARY}  (0 leads, chmod 600)")
        return 0

    print(f"records_valid: {len(records)}")

    now_iso = datetime.now(timezone.utc).isoformat()
    payload_private, payload_summary = _build_outputs(records, now_iso)

    print()
    print("Validando resumo seguro...")
    _validate_summary(payload_summary)
    print("Validação OK. Nenhum dado pessoal no resumo.")

    n_priv  = len(payload_private["leads"])
    n_summ  = len(payload_summary["leads"])

    if mode == "dry-run":
        print()
        print(f"Privado:  {n_priv} leads (com nome/telefone/observacao)")
        print(f"Resumo:   {n_summ} leads (sem dados pessoais)")
        print()
        print("next_step: use --write para gravar os dois arquivos locais.")
        return 0

    # --write
    _write_files(payload_private, payload_summary)

    print()
    print(f"Privado gravado:  {_OUT_PRIVATE}  ({n_priv} leads, chmod 600)")
    print(f"Resumo gravado:   {_OUT_SUMMARY}  ({n_summ} leads, chmod 600)")
    print()

    # Etapas para referência rápida
    etapas: dict[str, int] = {}
    for lead in payload_summary["leads"]:
        e = lead.get("etapa", "(sem etapa)")
        etapas[e] = etapas.get(e, 0) + 1
    print("Por etapa:")
    for e, cnt in sorted(etapas.items()):
        print(f"  {e}: {cnt}")

    print()
    print("Próximo passo: painel-soprolife/scripts/check-access.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
