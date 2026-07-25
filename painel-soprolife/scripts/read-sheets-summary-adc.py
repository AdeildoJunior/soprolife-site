#!/usr/bin/env python3
"""
SoproLife OS Local Core — Google Sheets connector (ADC).

Lê indicadores agregados da aba "Resumo Dashboard" usando
Application Default Credentials (gcloud). Nunca imprime
spreadsheet_id, URLs privadas, tokens ou credenciais.

Suporta dois formatos de aba:
  Formato A (chave direta):
    Coluna A: chave técnica  (ex: totalLeads)
    Coluna B: rótulo         (ex: Total de leads)
    Coluna C: valor          (ex: 42)

  Formato B (área + indicador):
    Coluna A: área           (ex: Comercial)
    Coluna B: indicador      (ex: Total de leads)
    Coluna C: valor          (ex: 42)

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
import re
import sys
import unicodedata
from pathlib import Path

# Guarda de PII compartilhada (M2) — mesma pasta deste script.
import pii_guard

_CONFIG_PATH = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
_OUT_PATH = Path("~/.config/soprolife/painel/resumo-dashboard.json").expanduser()

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

# Indicadores antigos — sempre incluídos no JSON (0 se ausentes na planilha)
_OLD_KEYS = [
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

# Indicadores de atendimento/CRM — incluídos no JSON somente se presentes na planilha
_NEW_KEYS = [
    "pacientesEmAcompanhamento",
    "examesEspirometriaRealizados",
    "teleconsultasRealizadas",
    "followupsPendentes",
    "lembretesWhatsAppPendentes",
    "recorrenciasAtivas",
    "consultasPrevistas",
]

ALLOWED_KEYS = set(_OLD_KEYS) | set(_NEW_KEYS)
ORDERED_KEYS = _OLD_KEYS + _NEW_KEYS

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

_HEADER_SYNONYMS = {"key", "indicador", "metrica", "métrica", "campo", "chave", "label", "area", "área"}

# Cabeçalhos que identificam o Formato B (área | indicador | valor)
_AREA_HEADER_NORMS = {"area", "area", "modulo", "categoria"}
_INDICATOR_HEADER_NORMS = {"indicador", "metrica", "campo", "indicadores"}


def _normalize_label(s: str) -> str:
    """Normaliza rótulo para busca no _INDICATOR_MAP.

    Converte para minúsculas, remove acentos e suprime espaços/hífens/barras.
    """
    s = s.strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\s\-/_()]+", "", s)
    return s


# Mapeamento de rótulos normalizados → chave técnica.
# Aceita tanto camelCase (chave técnica em minúsculas) quanto rótulos em português.
_INDICATOR_MAP: dict[str, str] = {
    # camelCase (chave técnica, lowercased)
    "totalleads":                   "totalLeads",
    "leadsnovos":                   "leadsNovos",
    "leadsagendados":               "leadsAgendados",
    "leadsconcluidos":              "leadsConcluidos",
    "clinicascadastradas":          "clinicasCadastradas",
    "tarefaspendentes":             "tarefasPendentes",
    "receitaprevista":              "receitaPrevista",
    "receitarecebida":              "receitaRecebida",
    "conteudosplanejados":          "conteudosPlanejados",
    "eventosagendados":             "eventosAgendados",
    "pacientesemacompanhamento":    "pacientesEmAcompanhamento",
    "examesespirometriarealizados": "examesEspirometriaRealizados",
    "teleconsultasrealizadas":      "teleconsultasRealizadas",
    "followupspendentes":           "followupsPendentes",
    "lembreteswhatsapppendentes":   "lembretesWhatsAppPendentes",
    "recorrenciasativas":           "recorrenciasAtivas",
    "consultasprevistas":           "consultasPrevistas",
    # Rótulos em português — coluna B do Formato B
    "totaldeleads":                 "totalLeads",
    "espirometriasrealizadas":      "examesEspirometriaRealizados",
    "espirometriasrealizados":      "examesEspirometriaRealizados",
    "examesespirometria":           "examesEspirometriaRealizados",
    "followupspendente":            "followupsPendentes",
    "recorrenciaativa":             "recorrenciasAtivas",
}


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


def _detect_format(rows: list) -> str:
    """Detecta o formato da aba.

    Retorna 'B' se a primeira linha tiver 'area' na coluna A e 'indicador'/'metrica'
    na coluna B (Formato B). Caso contrário retorna 'A' (Formato A, comportamento original).
    """
    if not rows:
        return "A"
    first_row = [str(c).strip() for c in rows[0]]
    if not first_row:
        return "A"
    col_a_norm = _normalize_label(first_row[0])
    col_b_norm = _normalize_label(first_row[1]) if len(first_row) > 1 else ""
    if col_a_norm in _AREA_HEADER_NORMS and col_b_norm in _INDICATOR_HEADER_NORMS:
        return "B"
    return "A"


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

    detected = _detect_format(rows)
    print(f"  format_detected: {detected}")
    print()
    print("Nota: somente a primeira linha foi inspecionada.")
    print("Os valores das linhas de dados não foram impressos.")
    print()

    if detected == "B":
        print("Formato B detectado (área | indicador | valor).")
        print("O script mapeará a coluna B (indicador) para as chaves técnicas do painel.")
        print()
        print("Indicadores reconhecidos na coluna B (português ou camelCase):")
        labels_by_key: dict[str, list[str]] = {}
        for norm_label, tech_key in _INDICATOR_MAP.items():
            labels_by_key.setdefault(tech_key, []).append(norm_label)
        for key in ORDERED_KEYS:
            variants = labels_by_key.get(key, [])
            print(f"  {key}")
            if variants:
                sample = sorted(variants)[:2]
                print(f"    ex: {', '.join(sample)}")
    else:
        found_expected = any(h.lower() in _HEADER_SYNONYMS for h in first_row)
        if found_expected:
            print("Formato A: primeira linha parece ser um cabeçalho reconhecido.")
            print("Se valid_indicators retornou 0, verifique se as chaves da coluna A")
            print("correspondem exatamente aos nomes esperados:")
        else:
            print("Formato A não confirmado. A aba pode estar no Formato B.")
            print()
            print("Formatos aceitos:")
            print()
            print("  Formato A (chave direta):")
            print("    Coluna A: chave técnica  (ex: totalLeads)")
            print("    Coluna B: rótulo         (ex: Total de leads)")
            print("    Coluna C: valor          (ex: 42)")
            print()
            print("  Formato B (área + indicador):")
            print("    Coluna A: area           (ex: Comercial)")
            print("    Coluna B: indicador      (ex: Total de leads)")
            print("    Coluna C: valor          (ex: 42)")
            print()
            print("Chaves esperadas (Formato A) / indicadores mapeados (Formato B):")

        for key in sorted(ALLOWED_KEYS):
            print(f"  {key}")


def _parse_indicators_format_a(rows: list) -> dict[str, float]:
    """Formato A: chave técnica | rótulo | valor (coluna A = chave camelCase)."""
    summary: dict[str, float] = {}

    for i, row in enumerate(rows):
        if not row:
            continue

        key_raw = str(row[0]).strip()

        if key_raw.lower() in _HEADER_SYNONYMS:
            continue  # linha de cabeçalho — ignorar

        if key_raw not in ALLOWED_KEYS:
            # Chave desconhecida: verificar palavras proibidas antes de ignorar.
            # Chaves em ALLOWED_KEYS são indicadores agregados pré-aprovados e
            # bypassam essa verificação mesmo que contenham palavras como "paciente".
            forbidden = _check_forbidden(key_raw)
            if forbidden:
                print(f"ERRO: palavra proibida '{forbidden}' detectada na coluna A, linha {i + 1}.")
                sys.exit(1)
            continue

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


def _parse_indicators_format_b(rows: list) -> dict[str, float]:
    """Formato B: área | indicador | valor (coluna B = rótulo mapeado para chave técnica)."""
    summary: dict[str, float] = {}
    skipped_unmapped = 0

    for i, row in enumerate(rows):
        if not row:
            continue

        area_raw = str(row[0]).strip()
        indicator_raw = str(row[1]).strip() if len(row) > 1 else ""

        # Pular linha de cabeçalho
        if _normalize_label(area_raw) in _AREA_HEADER_NORMS:
            continue

        # Pular linhas sem indicador
        if not indicator_raw:
            continue

        # Mapear rótulo da coluna B → chave técnica
        norm = _normalize_label(indicator_raw)
        tech_key = _INDICATOR_MAP.get(norm)

        if tech_key is None:
            skipped_unmapped += 1
            continue

        if len(row) < 3:
            print(f"AVISO: indicador '{indicator_raw}' na linha {i + 1} sem valor (coluna C); ignorado.")
            continue

        value_raw = str(row[2]).strip()

        # Verificar palavras proibidas somente no valor (coluna C)
        forbidden = _check_forbidden(value_raw)
        if forbidden:
            print(f"ERRO: palavra proibida '{forbidden}' detectada no valor da linha {i + 1}.")
            sys.exit(1)

        try:
            summary[tech_key] = _parse_number(value_raw)
        except ValueError as exc:
            print(f"ERRO: valor inválido para '{tech_key}' (indicador: '{indicator_raw}'): {exc}")
            sys.exit(1)

    if skipped_unmapped > 0:
        print(f"INFO: {skipped_unmapped} linha(s) com indicador não mapeado ignorada(s).")

    return summary


def _parse_indicators(rows: list) -> dict[str, float]:
    """Detecta o formato e extrai os indicadores válidos."""
    fmt = _detect_format(rows)
    print(f"format_detected: {fmt}")
    if fmt == "B":
        print("Usando Formato B: área | indicador | valor")
        return _parse_indicators_format_b(rows)
    print("Usando Formato A: chave | rótulo | valor")
    return _parse_indicators_format_a(rows)


def main() -> int:
    # M23 — guarda de fonte canônica. O painel opera em modo
    # postgresql_only: nenhum leitor de Google Sheets pode ser executado
    # pelo pipeline automático nem pelo timer de produção. Só uma decisão
    # humana explícita (SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1) libera
    # este utilitário, e apenas para migração/forense pontual.
    import sys as _sys, pathlib as _pathlib
    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    import data_source_mode
    data_source_mode.block_legacy_sheets('read-sheets-summary-adc.py')

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
        print("A aba deve estar em um dos formatos abaixo:")
        print()
        print("  Formato A (chave direta):")
        print("    Coluna A: chave técnica  (ex: totalLeads)")
        print("    Coluna B: rótulo         (ex: Total de leads)")
        print("    Coluna C: valor          (ex: 42)")
        print()
        print("  Formato B (área + indicador)  ← detectado por cabeçalho 'area | indicador | valor':")
        print("    Coluna A: área           (ex: Comercial)")
        print("    Coluna B: indicador      (ex: Total de leads)")
        print("    Coluna C: valor          (ex: 42)")
        print()
        print("Chaves esperadas (Formato A) — ou nomes de indicadores mapeados (Formato B):")
        for key in ORDERED_KEYS:
            print(f"  {key}")
        print()
        print("Use --show-structure para inspecionar o formato atual da aba.")
        return 1

    missing_old = set(_OLD_KEYS) - set(summary.keys())
    missing_new = set(_NEW_KEYS) - set(summary.keys())
    print(f"valid_indicators: {len(summary)}")

    if missing_old:
        print(f"AVISO: indicadores ausentes na planilha: {', '.join(sorted(missing_old))}")
        print("  Indicadores ausentes serão preenchidos com 0.")
        for key in missing_old:
            summary[key] = 0

    if missing_new:
        print(f"AVISO: indicadores de atendimento não encontrados: {', '.join(sorted(missing_new))}")
        print("  Serão omitidos do JSON — adicione à aba Resumo Dashboard para ativá-los.")

    print()
    print("Indicadores lidos:")
    for key in ORDERED_KEYS:
        if key in summary:
            print(f"  {key}: {_as_display(summary[key])}")

    print()
    print("Validação concluída. Nenhum dado sensível detectado.")

    if mode == "dry-run":
        print()
        print("next_step: use --write para gravar em ~/.config/soprolife/painel/resumo-dashboard.json")
        return 0

    # Modo write: indicadores antigos sempre presentes; novos somente se lidos da planilha
    output = {key: _as_display(summary.get(key, 0)) for key in _OLD_KEYS}
    for key in _NEW_KEYS:
        if key in summary:
            output[key] = _as_display(summary[key])

    # Guarda de PII compartilhada (M2): o resumo do dashboard é só
    # {chave técnica: número} — regras padrão (default-fechado) bastam.
    # Aborta com exit 1 antes de gravar se algo vazar.
    pii_guard.ensure_summary_safe(output, context="resumo-dashboard")

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
