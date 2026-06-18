#!/usr/bin/env python3
"""
SoproLife OS Local Core — Google Sheets connector (ADC).

Lê indicadores agregados da aba "Resumo Dashboard" usando
Application Default Credentials (gcloud). Nunca imprime
spreadsheet_id, URLs privadas, tokens ou credenciais.

Pré-requisito:
    pip install -r painel-soprolife/requirements-google.txt
    gcloud auth application-default login

Uso:
    # Inspecionar estrutura da aba de forma segura (diagnóstico)
    python3 painel-soprolife/scripts/read-sheets-summary-adc.py --show-structure

    # Validar sem gravar (padrão seguro)
    python3 painel-soprolife/scripts/read-sheets-summary-adc.py --dry-run

    # Validar e gravar em ~/.config/soprolife/painel/resumo-dashboard.json
    python3 painel-soprolife/scripts/read-sheets-summary-adc.py --write
"""

import argparse
import json
import sys
from pathlib import Path

_CONFIG_PATH = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
_OUT_PATH = Path("~/.config/soprolife/painel/resumo-dashboard.json").expanduser()

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

ALLOWED_KEYS = {
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

ORDERED_KEYS = [
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

FORBIDDEN_PATTERNS = [
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
    "nome completo",
    "data de nascimento",
]

_HEADER_SYNONYMS = {"key", "indicador", "metrica", "métrica", "campo", "chave", "label"}


def _load_google_libs():
    """Importa bibliotecas Google com mensagem de erro clara se ausentes."""
    try:
        from googleapiclient.discovery import build
        from google.auth import default as google_auth_default
        return build, google_auth_default
    except ImportError as exc:
        print(f"ERRO: dependências não instaladas — {exc}")
        print()
        print("Instale com:")
        print("  pip install -r painel-soprolife/requirements-google.txt")
        print()
        print("Ou em ambiente virtual isolado:")
        print("  python3 -m venv painel-soprolife/.venv")
        print("  source painel-soprolife/.venv/bin/activate")
        print("  pip install -r painel-soprolife/requirements-google.txt")
        sys.exit(1)


def _load_config() -> tuple[str, str]:
    """Lê configuração privada. Nunca imprime spreadsheet_id."""
    if not _CONFIG_PATH.exists():
        print("ERRO: configuração não encontrada.")
        print("  Esperado em: ~/.config/soprolife/painel/google-sheets.local.json")
        print("  Campos obrigatórios: spreadsheet_id, sheet_name")
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
    sheet = cfg.get("sheet_name", "").strip()

    if not sid:
        print("ERRO: spreadsheet_id ausente ou vazio na configuração.")
        sys.exit(1)
    if not sheet:
        print("ERRO: sheet_name ausente ou vazio na configuração.")
        sys.exit(1)

    # sid nunca é impresso
    return sid, sheet


def _check_forbidden(value: str) -> str | None:
    lower = value.lower()
    for pattern in FORBIDDEN_PATTERNS:
        if pattern in lower:
            return pattern
    return None


def _parse_number(raw: str) -> float:
    cleaned = raw.strip().replace("R$", "").replace("\xa0", "").replace(" ", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        raise ValueError(f"não é número: {raw!r}")
    if value < 0:
        raise ValueError("valor negativo não permitido")
    return value


def _as_display(value: float) -> int | float:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _fetch_rows(
    build,
    google_auth_default,
    spreadsheet_id: str,
    sheet_name: str,
) -> list[list[str]]:
    """Autentica via ADC, busca a aba e retorna as linhas brutas."""
    print("Conectando à API Google Sheets...")

    try:
        credentials, _ = google_auth_default(scopes=[SHEETS_SCOPE])
    except Exception as exc:
        print(f"ERRO: falha na autenticação ADC — {exc}")
        print()
        print("Execute para autenticar com o escopo correto:")
        print(f"  gcloud auth application-default login \\")
        print(f"      --scopes={SHEETS_SCOPE}")
        sys.exit(1)

    try:
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        print(f"ERRO: não foi possível inicializar o cliente Sheets — {exc}")
        sys.exit(1)

    range_notation = f"{sheet_name}!A:C"
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
            print(f"  Verifique o spreadsheet_id e o nome da aba: {sheet_name!r}")
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
    """Imprime somente a estrutura segura da aba — nunca valores de dados."""
    print(f"Estrutura da aba: {sheet_name}")
    print(f"  rows_total: {len(rows)}")

    if not rows:
        print("  (aba vazia)")
        return

    first_row = [str(c).strip() for c in rows[0]]
    print(f"  columns_in_row_1: {len(first_row)}")

    if first_row:
        headers_display = " | ".join(first_row)
        print(f"  headers: {headers_display}")

    print()
    print("Nota: somente a primeira linha foi inspecionada.")
    print("Os valores das linhas de dados não foram impressos.")
    print()

    # Orientação de diagnóstico
    found_expected = any(h.lower() in _HEADER_SYNONYMS for h in first_row)
    if found_expected:
        print("A primeira linha parece ser um cabeçalho reconhecido.")
        print("Se valid_indicators retornou 0, verifique se as chaves da coluna A")
        print("correspondem exatamente aos nomes esperados:")
    else:
        print("ATENÇÃO: a primeira linha não parece ser um cabeçalho reconhecido.")
        print("A aba precisa estar no formato:")
        print("  Coluna A: chave    (ex: totalLeads)")
        print("  Coluna B: rótulo   (ex: Total de leads)")
        print("  Coluna C: valor    (ex: 42)")
        print()
        print("Chaves esperadas:")

    for key in sorted(ALLOWED_KEYS):
        print(f"  {key}")


def _parse_indicators(rows: list) -> dict[str, float]:
    """Extrai indicadores válidos das linhas. Nunca imprime valores de dados."""
    summary: dict[str, float] = {}

    for i, row in enumerate(rows):
        if not row:
            continue

        key_raw = str(row[0]).strip()

        if key_raw.lower() in _HEADER_SYNONYMS:
            continue  # linha de cabeçalho — ignorar

        forbidden = _check_forbidden(key_raw)
        if forbidden:
            print(f"ERRO: palavra proibida '{forbidden}' detectada na coluna A, linha {i + 1}.")
            sys.exit(1)

        if key_raw not in ALLOWED_KEYS:
            continue  # chave desconhecida — ignorar silenciosamente

        if len(row) < 3:
            print(f"AVISO: chave '{key_raw}' na linha {i + 1} não tem coluna de valor (C); ignorada.")
            continue

        value_raw = str(row[2]).strip()

        forbidden = _check_forbidden(value_raw)
        if forbidden:
            print(f"ERRO: palavra proibida '{forbidden}' detectada no valor da linha {i + 1}.")
            sys.exit(1)

        try:
            summary[key_raw] = _parse_number(value_raw)
        except ValueError as exc:
            print(f"ERRO: valor inválido para '{key_raw}': {exc}")
            sys.exit(1)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Conector Google Sheets via ADC — SoproLife (sem chave privada)"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--show-structure",
        action="store_true",
        help="Inspeciona estrutura da aba de forma segura (diagnóstico, sem gravar)",
    )
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida leitura sem gravar (padrão)",
    )
    group.add_argument(
        "--write",
        action="store_true",
        help="Valida e grava em ~/.config/soprolife/painel/resumo-dashboard.json",
    )
    args = parser.parse_args()

    if args.show_structure:
        mode = "show-structure"
    elif args.write:
        mode = "write"
    else:
        mode = "dry-run"

    print("SoproLife OS Local Core — Google Sheets connector (ADC)")
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

    # --- dry-run ou write ---

    summary = _parse_indicators(rows)

    if len(summary) == 0:
        print()
        print("ERRO: nenhum indicador válido encontrado na aba.")
        print()
        print("A aba precisa estar no formato:")
        print("  Coluna A: chave    (ex: totalLeads)")
        print("  Coluna B: rótulo   (ex: Total de leads)")
        print("  Coluna C: valor    (ex: 42)")
        print()
        print("Chaves esperadas na coluna A:")
        for key in ORDERED_KEYS:
            print(f"  {key}")
        print()
        print("Use --show-structure para inspecionar a estrutura atual da aba.")
        return 1

    missing = ALLOWED_KEYS - set(summary.keys())
    print(f"valid_indicators: {len(summary)}")

    if missing:
        print(f"AVISO: indicadores ausentes na planilha: {', '.join(sorted(missing))}")
        print("  Indicadores ausentes serão preenchidos com 0.")
        for key in missing:
            summary[key] = 0

    print()
    print("Indicadores lidos:")
    for key in ORDERED_KEYS:
        print(f"  {key}: {_as_display(summary.get(key, 0))}")

    print()
    print("Validação concluída. Nenhum dado sensível detectado.")

    if mode == "dry-run":
        print()
        print("next_step: use --write para gravar em ~/.config/soprolife/painel/resumo-dashboard.json")
        return 0

    # Modo write: grava JSON privado fora do repositório
    output = {key: _as_display(summary.get(key, 0)) for key in ORDERED_KEYS}

    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _OUT_PATH.chmod(0o600)

    print()
    print("Gravado em: ~/.config/soprolife/painel/resumo-dashboard.json")
    print()
    print("Execute a seguir para atualizar o painel:")
    print("  painel-soprolife/scripts/sync-dashboard-summary.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
