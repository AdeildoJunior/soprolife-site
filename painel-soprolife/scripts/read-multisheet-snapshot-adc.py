#!/usr/bin/env python3
"""
SoproLife OS Local Core — leitor multiaba ao vivo (ADC) -> snapshot imutável.

Lê as 12 abas reais aprovadas (ver painel-soprolife/nucleo-m15/app/migration/
adapters.py — ADAPTERS/KNOWN_SHEET_KINDS) usando Application Default
Credentials (gcloud), valida cabeçalhos e tipos de célula, e grava um
snapshot bruto multiaba imutável via write_immutable_raw_snapshot. Nunca
imprime spreadsheet_id, URLs privadas, tokens, credenciais, valores de
célula, nomes, telefones, e-mails ou notas privadas.

Escopo fechado (nenhuma outra aba é lida):
  CRM Pacientes, CRM Espirometria, CRM Consultas, Leads, CRM Clinicas,
  CRM Contatos B2B, Follow-up WhatsApp, Financeiro_Lancamentos,
  Parceria Pastore - Config, Parceria Pastore - Atendimentos,
  Parceria Pastore - Custos, Base Prospecção B2B PCMSO.

"Resumo Dashboard" e qualquer outra aba (log, backup, marketing, agenda,
manual, resumo agregado) permanecem FORA deste leitor por definição.

Pré-requisito:
    pip install -r painel-soprolife/requirements-google.txt
    gcloud auth application-default login \
        --scopes=https://www.googleapis.com/auth/spreadsheets.readonly

Uso (execute com cwd em painel-soprolife/nucleo-m15, mesmo diretório que o
CLI de migração usa, para que M15_IMPORT_PRIVATE_DIR resolva de forma
consistente):
    python3 ../scripts/read-multisheet-snapshot-adc.py --show-structure
    python3 ../scripts/read-multisheet-snapshot-adc.py --dry-run
    python3 ../scripts/read-multisheet-snapshot-adc.py --write
"""

import argparse
import string
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_NUCLEO_M15_DIR = _SCRIPT_DIR.parent / "nucleo-m15"
if str(_NUCLEO_M15_DIR) not in sys.path:
    sys.path.insert(0, str(_NUCLEO_M15_DIR))

_CONFIG_PATH = Path(
    "~/.config/soprolife/painel/google-sheets.local.json"
).expanduser()

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

# Alias de workbook sanitizado (NUNCA o spreadsheet_id/URL real). Nenhum
# envelope m15-6b/m15-7 anterior existe neste ambiente para recuperar um
# alias já usado — este é um rótulo interno estável, não um identificador.
DEFAULT_WORKBOOK_ALIAS = "soprolife-painel-operacional"

DEFAULT_LOCALE = "pt-BR"
DEFAULT_TIMEZONE = "America/Sao_Paulo"

# Escopo fechado: título REAL da aba -> sheet_kind do registro de adapters.
# Qualquer outra aba viva na planilha é ignorada por definição (fail-closed,
# nenhuma heurística de adivinhação).
APPROVED_TABS: dict[str, str] = {
    "CRM Pacientes": "crm_pacientes",
    "CRM Espirometria": "crm_espirometria",
    "CRM Consultas": "crm_consultas",
    "Leads": "leads",
    "CRM Clinicas": "crm_clinicas",
    "CRM Contatos B2B": "contatos_b2b",
    "Follow-up WhatsApp": "followup_whatsapp",
    "Financeiro_Lancamentos": "financeiro_lancamentos",
    "Parceria Pastore - Config": "pastore_config",
    "Parceria Pastore - Atendimentos": "pastore_atendimentos",
    "Parceria Pastore - Custos": "pastore_custos",
    "Base Prospecção B2B PCMSO": "pcmso_historico",
}


def _col_letter(n: int) -> str:
    """Converte contagem de colunas (1-based) para notação A1 (A, B, ..., Z, AA...)."""
    letters = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters.append(string.ascii_uppercase[rem])
    return "".join(reversed(letters))


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


def _load_migration_libs():
    """Importa a camada de migração do núcleo M15. Nunca traz FastAPI/DB."""
    from app.migration.adapters import ADAPTERS, ADAPTERS_VERSION, check_real_headers
    from app.migration.rawsnapshot import write_immutable_raw_snapshot
    return ADAPTERS, ADAPTERS_VERSION, check_real_headers, write_immutable_raw_snapshot


def _load_config() -> str:
    """Lê configuração privada. Nunca imprime spreadsheet_id."""
    if not _CONFIG_PATH.exists():
        print("ERRO: configuração não encontrada.")
        print("  Esperado em: ~/.config/soprolife/painel/google-sheets.local.json")
        sys.exit(1)
    import json
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERRO: configuração JSON inválida — {exc}")
        sys.exit(1)
    sid = str(cfg.get("spreadsheet_id", "")).strip()
    if not sid:
        print("ERRO: spreadsheet_id ausente ou vazio na configuração.")
        sys.exit(1)
    return sid  # nunca impresso


def _authenticate(build, google_auth_default):
    try:
        credentials, _ = google_auth_default(scopes=[SHEETS_SCOPE])
    except Exception as exc:
        print(f"ERRO: falha na autenticação ADC — {exc}")
        sys.exit(1)
    try:
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        print(f"ERRO: não foi possível inicializar o cliente Sheets — {exc}")
        sys.exit(1)


def _list_live_tabs(service, spreadsheet_id: str) -> dict[str, dict]:
    """Metadados somente: título + dimensões. Nenhum valor de célula é lido."""
    try:
        meta = (
            service.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                fields="sheets.properties.title,sheets.properties.gridProperties",
            )
            .execute()
        )
    except Exception as exc:
        print(f"ERRO: falha ao ler metadados da planilha — {_sanitize_api_error(exc)}")
        sys.exit(1)
    result: dict[str, dict] = {}
    for s in meta.get("sheets", []):
        props = s.get("properties", {})
        title = props.get("title")
        if not title:
            continue
        gp = props.get("gridProperties", {})
        result[title] = {
            "rowCount": gp.get("rowCount", 0),
            "columnCount": gp.get("columnCount", 0),
        }
    return result


def _sanitize_api_error(exc: Exception) -> str:
    """Classifica o erro sem nunca repetir spreadsheet_id, URL ou token."""
    msg = str(exc)
    if "403" in msg or "PERMISSION_DENIED" in msg:
        return "acesso negado (verifique permissão de leitura do usuário ADC)"
    if "404" in msg or "NOT_FOUND" in msg:
        return "planilha ou aba não encontrada"
    if "401" in msg or "UNAUTHENTICATED" in msg:
        return "credenciais inválidas/expiradas (rode gcloud auth application-default login)"
    return "falha de comunicação com a API do Google Sheets"


def _fetch_header_row(service, spreadsheet_id: str, title: str) -> list[str]:
    rng = f"'{title}'!1:1"
    try:
        result = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=rng,
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute()
        )
    except Exception as exc:
        raise RuntimeError(_sanitize_api_error(exc)) from exc
    rows = result.get("values", [])
    if not rows:
        return []
    return [str(c).strip() for c in rows[0]]


def _fetch_data_rows(
    service, spreadsheet_id: str, title: str, n_cols: int, max_row: int
) -> list[list[str]]:
    last_col = _col_letter(n_cols)
    rng = f"'{title}'!A2:{last_col}{max_row}"
    try:
        result = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=rng,
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute()
        )
    except Exception as exc:
        raise RuntimeError(_sanitize_api_error(exc)) from exc
    return result.get("values", [])


def _assemble_sheet_payload(
    title: str,
    sheet_kind: str,
    headers: list[str],
    raw_rows: list[list[str]],
    n_cols: int,
) -> tuple[dict, int]:
    """Monta {sheet_alias, sheet_kind, headers, linha_inicial_dados, rows}.

    Linha original preservada (cabeçalho = linha 1, dados a partir da linha
    2). Linhas totalmente vazias são puladas (mantendo ordem crescente).
    Células ausentes ao final da linha são tratadas como texto vazio — nunca
    fórmula, nunca valor não-textual.
    """
    rows_out = []
    for offset, raw_row in enumerate(raw_rows):
        linha = offset + 2
        if not raw_row:
            continue
        for v in raw_row:
            if not isinstance(v, str):
                raise RuntimeError(
                    f"linha {linha} contém valor não-textual (fórmula/objeto)"
                )
        valores = list(raw_row)
        if len(valores) < n_cols:
            valores = valores + [""] * (n_cols - len(valores))
        elif len(valores) > n_cols:
            raise RuntimeError(
                f"linha {linha} tem mais colunas que o cabeçalho validado"
            )
        rows_out.append({"linha": linha, "valores": valores})
    payload = {
        "sheet_alias": title,
        "sheet_kind": sheet_kind,
        "headers": headers,
        "linha_inicial_dados": 2,
        "rows": rows_out,
    }
    return payload, len(rows_out)


def _print_structure(live_tabs: dict[str, dict]) -> None:
    print("Escopo aprovado (12 abas) x planilha ao vivo:")
    for title, sheet_kind in APPROVED_TABS.items():
        info = live_tabs.get(title)
        if info is None:
            print(f"  [AUSENTE] {title!r} (sheet_kind={sheet_kind}) — não encontrada ao vivo")
        else:
            print(
                f"  [OK] {title!r} -> sheet_kind={sheet_kind} "
                f"(rows={info['rowCount']}, cols={info['columnCount']})"
            )
    extras = sorted(set(live_tabs) - set(APPROVED_TABS))
    print()
    print(f"Abas ao vivo fora do escopo aprovado (ignoradas): {len(extras)}")
    for title in extras:
        print(f"  - {title!r} (fora do escopo, nunca lida)")
    print()
    print("Nenhum valor de célula foi lido ou impresso nesta operação.")


def main() -> int:
    # M23 — guarda de fonte canônica. O painel opera em modo
    # postgresql_only: nenhum leitor de Google Sheets pode ser executado
    # pelo pipeline automático nem pelo timer de produção. Só uma decisão
    # humana explícita (SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1) libera
    # este utilitário, e apenas para migração/forense pontual.
    import sys as _sys, pathlib as _pathlib
    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    import data_source_mode
    data_source_mode.block_legacy_sheets('read-multisheet-snapshot-adc.py')

    parser = argparse.ArgumentParser(
        description="Leitor multiaba ao vivo (ADC) -> snapshot bruto imutável"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show-structure", action="store_true",
                        help="Lista só metadados (título/dimensões); nenhum valor de célula")
    group.add_argument("--dry-run", action="store_true",
                        help="Lê e valida as 12 abas sem gravar snapshot (padrão)")
    group.add_argument("--write", action="store_true",
                        help="Lê, valida e grava o snapshot imutável (falha fechada em qualquer erro)")
    args = parser.parse_args()

    mode = "show-structure" if args.show_structure else ("write" if args.write else "dry-run")

    print("SoproLife OS Local Core — leitor multiaba ao vivo (ADC)")
    print(f"mode: {mode}")
    print("auth: Application Default Credentials")
    print()

    build, google_auth_default = _load_google_libs()
    ADAPTERS, ADAPTERS_VERSION, check_real_headers, write_immutable_raw_snapshot = (
        _load_migration_libs()
    )
    spreadsheet_id = _load_config()  # nunca impresso

    service = _authenticate(build, google_auth_default)
    live_tabs = _list_live_tabs(service, spreadsheet_id)
    print(f"abas_ao_vivo_encontradas: {len(live_tabs)}")
    print(f"abas_no_escopo_aprovado: {len(APPROVED_TABS)}")
    print()

    if mode == "show-structure":
        _print_structure(live_tabs)
        return 0

    # --- dry-run ou write: ler e validar as 12 abas aprovadas ---
    missing = [t for t in APPROVED_TABS if t not in live_tabs]
    if missing:
        print(f"ERRO: {len(missing)} aba(s) aprovada(s) ausente(s) na planilha ao vivo.")
        print("Nada foi lido ou gravado.")
        return 1

    sheets_payload: list[dict] = []
    total_rows = 0
    for title, sheet_kind in APPROVED_TABS.items():
        adapter = ADAPTERS.get(sheet_kind)
        if adapter is None:
            print(f"ERRO: sheet_kind {sheet_kind!r} não registrado em ADAPTERS. Nada gravado.")
            return 1
        try:
            headers = _fetch_header_row(service, spreadsheet_id, title)
        except RuntimeError as exc:
            print(f"ERRO ao ler cabeçalho de {title!r}: {exc}. Nada gravado.")
            return 1
        if not headers:
            print(f"ERRO: aba aprovada {title!r} está vazia (sem cabeçalho). Nada gravado.")
            return 1
        erros = check_real_headers(adapter, headers)
        if erros:
            print(f"ERRO: cabeçalhos inválidos em {title!r}: {'; '.join(erros)}")
            print("Nada foi lido ou gravado.")
            return 1

        info = live_tabs[title]
        max_row = min(info["rowCount"], 50_000 + 1)
        try:
            raw_rows = _fetch_data_rows(
                service, spreadsheet_id, title, len(headers), max_row
            )
        except RuntimeError as exc:
            print(f"ERRO ao ler dados de {title!r}: {exc}. Nada gravado.")
            return 1

        try:
            payload, n_rows = _assemble_sheet_payload(
                title, sheet_kind, headers, raw_rows, len(headers)
            )
        except RuntimeError as exc:
            print(f"ERRO ao montar aba {title!r}: {exc}. Nada gravado.")
            return 1

        if n_rows == 0:
            print(f"ERRO: aba aprovada {title!r} não tem nenhuma linha de dados. Nada gravado.")
            return 1

        sheets_payload.append(payload)
        total_rows += n_rows
        print(f"  validado: {title!r} -> {sheet_kind} ({n_rows} linhas de dados)")

    print()
    print(f"sheets_validadas: {len(sheets_payload)}")
    print(f"linhas_totais: {total_rows}")

    if mode == "dry-run":
        print()
        print("Validação concluída. Nenhum snapshot foi gravado (--dry-run).")
        print("next_step: use --write para gravar o snapshot imutável.")
        return 0

    # --- write ---
    snapshot_id = f"live-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    snapshot_ts_utc = datetime.now(timezone.utc).isoformat()
    try:
        receipt = write_immutable_raw_snapshot(
            snapshot_id=snapshot_id,
            workbook_alias=DEFAULT_WORKBOOK_ALIAS,
            snapshot_ts_utc=snapshot_ts_utc,
            locale=DEFAULT_LOCALE,
            timezone=DEFAULT_TIMEZONE,
            mapping_version=ADAPTERS_VERSION,
            sheets=sheets_payload,
            known_kinds=frozenset(ADAPTERS),
        )
    except Exception as exc:
        print(f"ERRO ao gravar snapshot imutável: {exc}")
        print("Nada foi gravado (ou foi revertido).")
        return 1

    print()
    print("Snapshot imutável gravado e revalidado com sucesso.")
    print(f"snapshot_ts_utc: {receipt.snapshot_ts_utc}")
    print(f"sheet_count: {receipt.sheet_count}")
    print(f"linhas_totais: {total_rows}")
    print(f"envelope_name: {receipt.envelope_name}")
    print(f"envelope_sha256: {receipt.envelope_sha256}")
    print()
    print("Nenhum valor de célula, identificador de planilha ou credencial foi impresso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
