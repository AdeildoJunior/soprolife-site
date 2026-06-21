#!/usr/bin/env python3
"""
SoproLife OS Local Core — Inspeção segura de abas CRM Pacientes.

Inspeciona sem expor nomes, telefones, CPF nem dados clínicos.
Imprime apenas:
  - nome da aba, total de linhas, cabeçalhos e classificação
  - diagnóstico de datas: quantas preenchidas e parseáveis (sem imprimir valores)

Uso:
    python3 painel-soprolife/scripts/inspect-crm-pacientes.py
"""

import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

_CONFIG_PATH = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

ABAS_ALVO = [
    "CRM Espirometria",
    "CRM Consultas",
    "CRM Pacientes",
    "Follow-up WhatsApp",
]

# Colunas suspeitas de data (só para classificação visual do cabeçalho)
_DATE_HINTS  = ("data", "date", "dt_", "_dt", "dia", "agendamento", "realizado",
                "exame", "consulta", "followup", "follow", "retorno", "nascimento",
                "vencimento", "prazo", "proxima", "próxima", "ultima", "última",
                "entrada", "prevista", "criacao", "criação")
_NAME_HINTS  = ("nome", "name", "paciente", "cliente", "responsavel", "responsável")
_PHONE_HINTS = ("telefone", "fone", "celular", "whatsapp", "whats")

# Meses PT (para parse de diagnóstico de data)
_MESES_PT: dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "marco": 3,    "abril": 4,
    "maio": 5,    "junho": 6,     "julho": 7,    "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
_SHEETS_EPOCH = date(1899, 12, 30)


def _norm(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain.lower().strip())


def _parse_date(raw: str) -> date | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    if re.match(r"^\d{4,6}$", raw):
        try:
            serial = int(raw)
            if 10000 <= serial <= 100000:
                return _SHEETS_EPOCH + timedelta(days=serial)
        except (ValueError, OverflowError):
            pass
    m = re.match(r"^(\d{1,2})/(\d{4})$", raw)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 2000 <= year <= 2100:
            return date(year, month, 1)
    m = re.match(r"^(\d{4})/(\d{1,2})$", raw)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 2000 <= year <= 2100:
            return date(year, month, 1)
    raw_n = _norm(raw)
    for sep in ("/", " ", "-"):
        parts = raw_n.split(sep, 1)
        if len(parts) == 2:
            mes_str, ano_str = parts[0].strip(), parts[1].strip()
            month = _MESES_PT.get(mes_str)
            if month and re.match(r"^\d{4}$", ano_str):
                year = int(ano_str)
                if 2000 <= year <= 2100:
                    return date(year, month, 1)
    return None


def _classify_col(header: str) -> str:
    h = _norm(header)
    if any(hint in h for hint in _PHONE_HINTS):
        return "TELEFONE"
    if any(hint in h for hint in _DATE_HINTS):
        return "DATA"
    if any(hint in h for hint in _NAME_HINTS):
        return "NOME"
    return "outro"


def _load_libs():
    try:
        from googleapiclient.discovery import build
        from google.auth import default as auth_default
        return build, auth_default
    except ImportError as exc:
        print(f"ERRO: dependências não instaladas — {exc}")
        sys.exit(1)


def _load_config() -> str:
    if not _CONFIG_PATH.exists():
        print(f"ERRO: config não encontrada em {_CONFIG_PATH}")
        sys.exit(1)
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    sid = cfg.get("spreadsheet_id", "").strip()
    if not sid:
        print("ERRO: spreadsheet_id ausente.")
        sys.exit(1)
    return sid


def _build_service(build, auth_default):
    try:
        creds, _ = auth_default(scopes=[SHEETS_SCOPE])
        return build("sheets", "v4", credentials=creds, cache_discovery=False)
    except Exception as exc:
        print(f"ERRO: autenticação falhou — {exc}")
        sys.exit(1)


def inspect_aba(service, spreadsheet_id: str, aba: str) -> None:
    print(f"\n{'═' * 62}")
    print(f"  Aba: {aba!r}")
    print(f"{'═' * 62}")
    try:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{aba}'!A:Z")
            .execute()
        )
        rows = result.get("values", [])
    except Exception as exc:
        msg = str(exc)
        if "404" in msg or "Unable to parse range" in msg or "not found" in msg.lower():
            print("  → Aba não encontrada nesta planilha. Pulando.")
        else:
            print(f"  → Erro ao ler: {exc}")
        return

    if not rows:
        print("  → Aba vazia.")
        return

    header_row  = rows[0]
    data_rows   = rows[1:]
    total_dados = len(data_rows)

    print(f"  Linhas de dados: {total_dados}")
    print(f"  Colunas ({len(header_row)}):")

    date_col_indices = []

    for i, h in enumerate(header_row):
        kind   = _classify_col(str(h))
        letter = chr(65 + i) if i < 26 else f"A{chr(65 + i - 26)}"
        marker = {"DATA": "📅 DATA", "NOME": "👤 NOME",
                  "TELEFONE": "📞 TELEFONE", "outro": "   outro"}[kind]
        print(f"    {letter:3s}  {marker:15s}  {h}")
        if kind == "DATA":
            date_col_indices.append((i, str(h)))

    # ── Diagnóstico de datas (sem imprimir valores) ────────────────────────────
    if date_col_indices and total_dados > 0:
        print()
        print("  Diagnóstico de datas (contagens — sem imprimir valores):")
        for col_idx, col_name in date_col_indices:
            values = [
                str(row[col_idx]).strip() if col_idx < len(row) else ""
                for row in data_rows
            ]
            n_preen = sum(1 for v in values if v)
            n_parse = sum(1 for v in values if _parse_date(v))
            n_vazia = total_dados - n_preen

            # Mostra exemplos de formatos encontrados SEM dados pessoais
            # (só se forem apenas datas/mês-ano, não textos longos)
            formatos: dict[str, int] = {}
            for v in values:
                if not v:
                    continue
                # Classifica o formato sem revelar o conteúdo
                if re.match(r"^\d{2}/\d{2}/\d{4}$", v):
                    fmt_key = "DD/MM/YYYY"
                elif re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                    fmt_key = "YYYY-MM-DD"
                elif re.match(r"^\d{1,2}/\d{4}$", v):
                    fmt_key = "MM/YYYY"
                elif re.match(r"^\d{4}/\d{1,2}$", v):
                    fmt_key = "YYYY/MM"
                elif re.match(r"^\d{5,6}$", v):
                    fmt_key = "serial-Sheets"
                elif re.match(r"^[a-záéíóúàâêôãõç]+[/\s\-]\d{4}$", _norm(v)):
                    fmt_key = "mês-PT/YYYY"
                elif len(v) <= 15:
                    fmt_key = f"outro-curto ({len(v)}c)"
                else:
                    fmt_key = f"texto-longo ({len(v)}c)"
                formatos[fmt_key] = formatos.get(fmt_key, 0) + 1

            status = "✅" if n_parse == n_preen == total_dados else \
                     "⚠️" if n_parse < n_preen else "❓"
            print(f"    {status} '{col_name}':")
            print(f"       total={total_dados}  preenchidas={n_preen}  "
                  f"parseáveis={n_parse}  vazias={n_vazia}")
            if formatos:
                fmt_str = "  ".join(f"{k}×{v}" for k, v in formatos.items())
                print(f"       formatos: {fmt_str}")
            if n_preen > n_parse:
                print(f"       ATENÇÃO: {n_preen - n_parse} valores não parseáveis.")

    print()
    print("  SEGURANÇA: nenhum valor de paciente foi lido ou impresso.")


def main() -> int:
    print("SoproLife — Inspeção segura de abas CRM Pacientes")
    print("Modo: cabeçalhos + contagem de linhas + diagnóstico de datas")
    print("Nenhum valor de paciente (nome, telefone, dados clínicos) será impresso.\n")

    build, auth_default = _load_libs()
    sid = _load_config()
    service = _build_service(build, auth_default)

    for aba in ABAS_ALVO:
        inspect_aba(service, sid, aba)

    print(f"\n{'─' * 62}")
    print("Inspeção concluída. Nenhum dado pessoal foi exposto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
