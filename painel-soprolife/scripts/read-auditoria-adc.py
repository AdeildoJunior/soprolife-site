#!/usr/bin/env python3
"""
SoproLife OS Local Core — Conector Log Auditoria (Google Sheets ADC).

Lê a aba "Log Auditoria" (M1 — trilha append-only das escritas do Command
Center) e gera UM único arquivo local, só com agregados e eventos saneados:

  painel-soprolife/data/auditoria-summary.local.json  ← resumo seguro (gitignored)
    safeToDisplay: true | containsPersonalData: false

Diferente dos demais conectores, NÃO gera arquivo em data-private/: a fonte
da verdade é a própria aba, e o summary não precisa de nenhum campo além dos
metadados seguros.

O que ENTRA no summary:
  - contagens por dia (últimos 14 dias), por ação, por operador e por origem;
  - total de eventos e total de erros (resultado != ok) — saúde do write-path;
  - últimos N eventos (padrão 20) APENAS com: timestamp, acao, entidade_tipo,
    entidade_id, operador, resultado.

O que NUNCA entra (mesmo a aba sendo desenhada sem PII):
  - valor_anterior / valor_novo / campo (menos superfície — ver spec M1 §7);
  - request_id / derivado_de / duration_ms / build_version;
  - qualquer texto que dispare os padrões de CPF/telefone/e-mail.

Configuração necessária:
    ~/.config/soprolife/painel/google-sheets.local.json
    Campo obrigatório: "spreadsheet_id"
    Campo opcional:    "log_auditoria_sheet_name" (padrão: "Log Auditoria")

Pré-requisito:
    pip install -r painel-soprolife/requirements-google.txt
    gcloud auth application-default login

Uso:
    python3 painel-soprolife/scripts/read-auditoria-adc.py --show-structure
    python3 painel-soprolife/scripts/read-auditoria-adc.py --dry-run
    python3 painel-soprolife/scripts/read-auditoria-adc.py --write
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Guarda de PII compartilhada (M2) — mesma pasta deste script.
# A validação local _validate_summary abaixo permanece como redundância.
import pii_guard

# Regras da guarda para este summary: nenhum campo de pessoa deve existir;
# operador/origem são identidade de instância/equipe (institucional), nunca
# de paciente. request_id/derivado_de/valor_* já são proibidos aqui por
# definição (ver docstring) — reforçados como chaves proibidas extras.
_PII_RULES = {
    "campos_pessoa": [],
    "campos_institucionais": ["operador", "origem"],
    "chaves_proibidas_extras": ["valor_anterior", "valor_novo", "derivado_de", "request_id"],
}

_CONFIG_PATH = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
_OUT_SUMMARY = Path("painel-soprolife/data/auditoria-summary.local.json")

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

DEFAULT_SHEET_AUDITORIA = "Log Auditoria"
ULTIMOS_EVENTOS_LIMITE = 20
DIAS_SERIE = 14

# Cabeçalho canônico da aba — mesma ordem de _LOG_AUDITORIA_CABECALHO em
# painel-soprolife/apps-script/command-center-api.gs.
CANONICAL_AUDITORIA = [
    "log_id", "request_id", "timestamp", "acao", "entidade_tipo", "entidade_id",
    "campo", "valor_anterior", "valor_novo", "origem", "operador", "trigger",
    "resultado", "derivado_de", "duration_ms", "build_version",
]

# Campos da aba que o summary usa. Tudo fora desta lista é lido mas nunca
# exportado (allowlist default-fechada, mesmo princípio do Apps Script).
SUMMARY_FIELDS = ["timestamp", "acao", "entidade_tipo", "entidade_id", "operador", "resultado"]

_CPF_RE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp_br(raw: str) -> Optional[datetime]:
    raw = str(raw or "").strip()
    if not raw:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


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

    sheet = cfg.get("log_auditoria_sheet_name", DEFAULT_SHEET_AUDITORIA).strip() or DEFAULT_SHEET_AUDITORIA
    return sid, sheet


def _get_credentials(google_auth_default):
    try:
        credentials, _ = google_auth_default(scopes=[SHEETS_SCOPE])
        return credentials
    except Exception as exc:
        print(f"ERRO: falha na autenticação ADC — {exc}")
        print()
        print("Execute para autenticar:")
        print(f"  gcloud auth application-default login --scopes={SHEETS_SCOPE}")
        sys.exit(1)


def _fetch_rows(build, spreadsheet_id: str, sheet_name: str, credentials):
    print(f"Lendo aba: {sheet_name}")

    try:
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A:P")
            .execute()
        )
    except Exception as exc:
        msg = str(exc)
        if "403" in msg or "PERMISSION_DENIED" in msg:
            print(f"ERRO: acesso negado à aba '{sheet_name}'.")
            sys.exit(1)
        elif "404" in msg or "NOT_FOUND" in msg:
            print(f"AVISO: aba '{sheet_name}' não encontrada — tratando como vazia.")
            print("  (normal enquanto o Apps Script com _logAudit não foi publicado)")
            return []
        elif "401" in msg or "UNAUTHENTICATED" in msg or "invalid_grant" in msg:
            print("ERRO: credenciais ADC inválidas ou expiradas.")
            print("  Execute: gcloud auth application-default login")
            sys.exit(1)
        else:
            print(f"ERRO: falha ao ler a aba '{sheet_name}' — {exc}")
            sys.exit(1)

    rows = result.get("values", [])
    print(f"  rows_read: {len(rows)}")
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
        hl = h.strip().lower()
        if hl in SUMMARY_FIELDS:
            print(f"  OK (usada no summary):        {h}")
        elif hl in CANONICAL_AUDITORIA:
            print(f"  LIDA MAS NUNCA EXPORTADA:     {h}")
        else:
            print(f"  IGNORADA (não reconhecida):   {h}")
    print()


def _parse_records(rows: list) -> list[dict]:
    """Lê a aba pelo cabeçalho (não por posição fixa)."""
    if not rows:
        return []

    raw_headers = [str(c).strip() for c in rows[0]]
    headers_norm = [h.strip().lower() for h in raw_headers]

    col_map: dict[str, int] = {}
    for i, hn in enumerate(headers_norm):
        if hn in CANONICAL_AUDITORIA and hn not in col_map:
            col_map[hn] = i

    if "timestamp" not in col_map or "acao" not in col_map:
        print("AVISO: cabeçalho sem timestamp/acao — tratando aba como vazia.")
        return []

    records = []
    for row in rows[1:]:
        rec: dict[str, str] = {}
        for canon, idx in col_map.items():
            rec[canon] = str(row[idx]).strip() if idx < len(row) else ""
        if any(rec.values()):
            records.append(rec)
    return records


def _build_summary(records: list[dict], now_iso: str) -> dict:
    total = len(records)
    erros = sum(1 for r in records if r.get("resultado", "").strip().lower() not in ("", "ok")
                and not r.get("resultado", "").strip().lower().startswith("ok"))

    por_acao: dict[str, int] = defaultdict(int)
    por_operador: dict[str, int] = defaultdict(int)
    por_origem: dict[str, int] = defaultdict(int)
    por_dia: dict[str, int] = defaultdict(int)

    hoje = datetime.now()
    limite_serie = hoje - timedelta(days=DIAS_SERIE - 1)

    for r in records:
        por_acao[r.get("acao") or "desconhecida"] += 1
        por_operador[r.get("operador") or "desconhecido"] += 1
        por_origem[r.get("origem") or "desconhecida"] += 1
        ts = _parse_timestamp_br(r.get("timestamp"))
        if ts and ts >= limite_serie:
            por_dia[ts.strftime("%d/%m")] += 1

    labels_dias = [(limite_serie + timedelta(days=i)).strftime("%d/%m") for i in range(DIAS_SERIE)]
    serie_por_dia = {"labels": labels_dias, "eventos": [por_dia.get(d, 0) for d in labels_dias]}

    # Últimos N eventos — a aba é append-only, então as últimas linhas são as
    # mais recentes. Só os campos de SUMMARY_FIELDS saem daqui.
    ultimos = []
    for r in records[-ULTIMOS_EVENTOS_LIMITE:][::-1]:
        ultimos.append({f: (r.get(f) or "") for f in SUMMARY_FIELDS})

    return {
        "source": {
            "type": "auditoria_summary",
            "safeToDisplay": True,
            "containsPersonalData": False,
            "containsHealthData": False,
            "generatedAt": now_iso,
        },
        "stats": {
            "total_eventos": total,
            "erros": erros,
            "por_acao": dict(sorted(por_acao.items(), key=lambda kv: -kv[1])),
            "por_operador": dict(sorted(por_operador.items(), key=lambda kv: -kv[1])),
            "por_origem": dict(sorted(por_origem.items(), key=lambda kv: -kv[1])),
        },
        "eventos_por_dia": serie_por_dia,
        "ultimos_eventos": ultimos,
    }


def _validate_summary(payload: dict) -> None:
    """Rede de segurança final — o summary nasce só de campos seguros, mas
    confere que nenhum termo/padrão pessoal ou de conteúdo vazou."""
    text = json.dumps(payload, ensure_ascii=False).lower()
    errors = 0

    FORBIDDEN_KEYS = [
        "valor_anterior", "valor_novo", "derivado_de", "request_id",
        "telefone", "whatsapp", "observacao", "observação",
        "nome_paciente", "paciente_nome", "primeiro_nome", "laudo",
        "pedido médico", "pedido medico", "diagnóstico", "diagnostico",
        "access_token", "private_key", "client_secret",
        "https://docs.google.com", "/spreadsheets/d/", "spreadsheet_id",
    ]
    for key in FORBIDDEN_KEYS:
        if key in text:
            print(f"ERRO interno: termo proibido '{key}' encontrado no resumo.")
            errors += 1

    if _CPF_RE.search(text):
        print("ERRO interno: padrão de CPF detectado no resumo.")
        errors += 1
    if _EMAIL_RE.search(text):
        print("ERRO interno: padrão de e-mail detectado no resumo.")
        errors += 1
    # Telefone: testar só os valores dos eventos (timestamps dd/mm/aaaa hh:mm
    # não disparam a regex, mas conferimos campo a campo por segurança).
    for evt in payload.get("ultimos_eventos", []):
        for k, v in evt.items():
            if k == "timestamp":
                continue
            if _FONE_RE.search(str(v)):
                print(f"ERRO interno: padrão de telefone no campo '{k}' de um evento.")
                errors += 1

    if errors:
        print(f"ERRO: {errors} violação(ões) no resumo. Abortando gravação.")
        sys.exit(1)


def _write_file(payload: dict) -> None:
    _OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    _OUT_SUMMARY.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 644: o summary é servido ao navegador pelo painel (sem PII por construção).
    _OUT_SUMMARY.chmod(0o644)


def main() -> int:
    # M23 — guarda de fonte canônica. O painel opera em modo
    # postgresql_only: nenhum leitor de Google Sheets pode ser executado
    # pelo pipeline automático nem pelo timer de produção. Só uma decisão
    # humana explícita (SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1) libera
    # este utilitário, e apenas para migração/forense pontual.
    import sys as _sys, pathlib as _pathlib
    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    import data_source_mode
    data_source_mode.block_legacy_sheets('read-auditoria-adc.py')

    parser = argparse.ArgumentParser(
        description="Conector Log Auditoria via Google Sheets ADC — SoproLife"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show-structure", action="store_true",
                       help="Inspeciona o cabeçalho da aba (diagnóstico, sem gravar)")
    group.add_argument("--dry-run", action="store_true",
                       help="Valida leitura e agregação sem gravar (padrão)")
    group.add_argument("--write", action="store_true",
                       help="Valida e grava data/auditoria-summary.local.json")
    args = parser.parse_args()

    mode = "show-structure" if args.show_structure else ("write" if args.write else "dry-run")

    print("SoproLife OS Local Core — Log Auditoria connector (ADC)")
    print(f"mode: {mode}")
    print("auth: Application Default Credentials")
    print()

    build, google_auth_default = _load_google_libs()
    spreadsheet_id, sheet_name = _load_config()
    credentials = _get_credentials(google_auth_default)

    print("config: carregada")
    print(f"sheet: {sheet_name!r}")
    print()

    rows = _fetch_rows(build, spreadsheet_id, sheet_name, credentials)

    if mode == "show-structure":
        _show_structure(rows, sheet_name)
        return 0

    records = _parse_records(rows)
    print(f"eventos: {len(records)} linha(s)")
    print()

    payload = _build_summary(records, _now_iso())

    print("Validando resumo seguro...")
    _validate_summary(payload)
    # 2ª validação: guarda de PII compartilhada (M2) — aborta com exit 1 se
    # encontrar violação; nunca imprime o valor sensível.
    pii_guard.ensure_summary_safe(payload, rules=_PII_RULES, context="auditoria-summary")
    print("Validação OK (local + pii_guard). Nenhum dado pessoal ou conteúdo de campo no resumo.")
    print()
    print(f"total_eventos: {payload['stats']['total_eventos']}")
    print(f"erros:         {payload['stats']['erros']}")
    print(f"ultimos:       {len(payload['ultimos_eventos'])} evento(s)")

    if mode == "dry-run":
        print()
        print("next_step: use --write para gravar o resumo local.")
        return 0

    _write_file(payload)

    print()
    print(f"Resumo gravado: {_OUT_SUMMARY}  (chmod 644 — servido ao navegador)")
    print()
    print("Próximo passo: painel-soprolife/scripts/check-access.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
