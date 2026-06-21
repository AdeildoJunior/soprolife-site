#!/usr/bin/env python3
"""
SoproLife OS Local Core — Promoção Base PCMSO → CRM Clínicas (ADC).

Lê a aba "Base Prospecção B2B PCMSO" e promove registros qualificados
para a aba "CRM Clinicas" usando Application Default Credentials (gcloud).

Regras de promoção
──────────────────
Promove apenas Status:  Pediu apresentação · Abordado · Em conversa ·
                        Proposta enviada · Parceiro ativo
NÃO promove Status:     Não tem whatsapp · Sem interesse ou perdido ·
                        Não abordado · (em branco)
Dedup:                  Usa _key() para matching robusto — remove termos
                        genéricos, colapsa letras repetidas, tolera
                        variações como "Focosseg"/"FOCOSEG" e nomes
                        curtos vs nomes completos ("Fluxomed" /
                        "FLUXOMED Saúde e Segurança do Trabalho").
Atualização:            Se já existe, atualiza etapa / proxima_acao /
                        prioridade / ultima_interacao.
Segurança:              Nunca escreve no CRM: telefone, Instagram/Site,
                        pessoa de contato, Observações, CPF, e-mail.

Pré-requisito
─────────────
    pip install -r painel-soprolife/requirements-google.txt
    gcloud auth application-default login \
        --scopes=https://www.googleapis.com/auth/spreadsheets

Config: ~/.config/soprolife/painel/google-sheets.local.json
    Campo obrigatório: spreadsheet_id
    Campos opcionais:
        pcmso_sheet_name         (padrão: "Base Prospecção B2B PCMSO")
        crm_clinicas_sheet_name  (padrão: "CRM Clinicas")

Uso
───
    python3 promote-pcmso-to-crm.py --dry-run        # preview promoção
    python3 promote-pcmso-to-crm.py --write           # executa promoção
    python3 promote-pcmso-to-crm.py --dedup          # preview limpeza
    python3 promote-pcmso-to-crm.py --dedup --write  # executa limpeza
"""

import argparse
import json
import re
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

_CONFIG_PATH = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

DEFAULT_PCMSO_SHEET = "Base Prospecção B2B PCMSO"
DEFAULT_CRM_SHEET = "CRM Clinicas"

_STATUS_MAP: dict[str, tuple[str, str]] = {
    "pediu apresentacao": ("Em conversa", "Alta"),
    "pediu apresentação": ("Em conversa", "Alta"),
    "abordado": ("Primeiro contato", "Média"),
    "em conversa": ("Em conversa", "Alta"),
    "proposta enviada": ("Proposta enviada", "Alta"),
    "parceiro ativo": ("Parceiro ativo", "Alta"),
}

_STATUS_DATE_OFFSET: dict[str, int | None] = {
    "pediu apresentacao": 2,
    "pediu apresentação": 2,
    "abordado": 3,
    "em conversa": 1,
    "proposta enviada": 2,
    "parceiro ativo": None,
}

_STATUS_BLOCK = {
    "nao tem whatsapp",
    "não tem whatsapp",
    "sem interesse ou perdido",
    "nao abordado",
    "não abordado",
    "",
}

CRM_HEADERS = [
    "clinica_id", "nome_clinica", "bairro", "regiao", "tipo_clinica",
    "etapa", "ultima_interacao", "proxima_acao", "responsavel",
    "prioridade", "observacao",
]

_BLOCKED_COL_SUBSTRINGS = (
    "telefone", "whats", "instagram", "site",
    "pessoa de contato", "observa",
)

_FORBIDDEN_TERMS = (
    "cpf", "pedido médico", "pedido medico", "laudo",
    "diagnóstico", "diagnostico", "endereço", "endereco",
    "data de nascimento", "nome completo",
)

_CPF_RE  = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

# Termos descritivos genéricos removidos ao gerar a chave de matching
_NOISE: frozenset[str] = frozenset({
    "saude", "seguranca", "medicina", "ocupacional", "clinica", "clinicas",
    "servicos", "servico", "ltda", "eireli", "me", "sa",
    "e", "de", "do", "da", "dos", "das", "em", "com", "para",
    "trabalho", "ambiental", "grupo", "gestao", "consultoria",
    "centro", "medico", "medica", "laboratorio", "lab",
})


# ── Matching ──────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Minúsculo, sem acentos, sem espaços duplos."""
    nfkd = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain.lower().strip())


def _key(text: str) -> str:
    """
    Chave de matching robusta:
      1. normaliza (minúsculo, sem acentos)
      2. remove pontuação não-alfanumérica
      3. filtra palavras genéricas (_NOISE)
      4. colapsa letras consecutivas repetidas (ex: ss→s, ll→l)
    Exemplos:
      "Focosseg"                          → "focoseg"
      "FOCOSEG Segurança e Saúde Ocupacional" → "focoseg"
      "Fluxomed"                          → "fluxomed"
      "FLUXOMED Saúde e Segurança do Trabalho" → "fluxomed"
    """
    base = _norm(text)
    base = re.sub(r"[^a-z0-9\s]", "", base)
    tokens = [t for t in base.split() if t not in _NOISE and len(t) > 1]
    joined = " ".join(tokens)
    # colapsa letras consecutivas repetidas: "ss"→"s", "ll"→"l", etc.
    collapsed = re.sub(r"(.)\1+", r"\1", joined)
    return collapsed.strip()


def _parse_date(raw: str) -> str:
    """Converte para YYYY-MM-DD se válido, caso contrário retorna vazio."""
    raw = raw.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    if re.match(r"^\d{2}/\d{2}/\d{4}$", raw):
        d, m, y = raw.split("/")
        return f"{y}-{m}-{d}"
    return ""


def _fallback_date(status_key: str) -> str:
    """Retorna data de próxima ação por status (YYYY-MM-DD), ou vazio."""
    offset = _STATUS_DATE_OFFSET.get(status_key)
    if offset is None:
        return ""
    return (date.today() + timedelta(days=offset)).isoformat()


def _matches(key_a: str, key_b: str) -> bool:
    """
    True se as chaves representam a mesma entidade.
    Critérios (em ordem):
      1. igualdade exata
      2. uma é substring da outra (mínimo 5 chars para evitar falsos positivos)
    """
    if key_a == key_b:
        return True
    min_len = min(len(key_a), len(key_b))
    if min_len >= 5 and (key_a in key_b or key_b in key_a):
        return True
    return False


# ── Segurança ─────────────────────────────────────────────────────────────────

def _check_safe(value: str, label: str) -> None:
    lower = value.lower()
    for term in _FORBIDDEN_TERMS:
        if term in lower:
            print(f"ERRO: termo proibido '{term}' em '{label}'.")
            sys.exit(1)
    if _CPF_RE.search(value):
        print(f"ERRO: padrão de CPF em '{label}'.")
        sys.exit(1)
    if _FONE_RE.search(value):
        print(f"ERRO: padrão de telefone em '{label}'.")
        sys.exit(1)
    if _EMAIL_RE.search(value):
        print(f"ERRO: padrão de e-mail em '{label}'.")
        sys.exit(1)


# ── Google Sheets ─────────────────────────────────────────────────────────────

def _load_google_libs():
    try:
        from googleapiclient.discovery import build
        from google.auth import default as google_auth_default
        return build, google_auth_default
    except ImportError as exc:
        print(f"ERRO: dependências não instaladas — {exc}")
        print("Instale: pip install -r painel-soprolife/requirements-google.txt")
        sys.exit(1)


def _load_config() -> tuple[str, str, str]:
    if not _CONFIG_PATH.exists():
        print(f"ERRO: configuração não encontrada em {_CONFIG_PATH}")
        sys.exit(1)
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERRO: configuração JSON inválida — {exc}")
        sys.exit(1)
    sid = cfg.get("spreadsheet_id", "").strip()
    if not sid:
        print("ERRO: spreadsheet_id ausente na configuração.")
        sys.exit(1)
    pcmso = cfg.get("pcmso_sheet_name", DEFAULT_PCMSO_SHEET).strip() or DEFAULT_PCMSO_SHEET
    crm   = cfg.get("crm_clinicas_sheet_name", DEFAULT_CRM_SHEET).strip() or DEFAULT_CRM_SHEET
    return sid, pcmso, crm


def _build_service(build, google_auth_default):
    try:
        creds, _ = google_auth_default(scopes=[SHEETS_SCOPE])
    except Exception as exc:
        print(f"ERRO: falha na autenticação ADC — {exc}")
        print(f"  gcloud auth application-default login --scopes={SHEETS_SCOPE}")
        sys.exit(1)
    try:
        return build("sheets", "v4", credentials=creds, cache_discovery=False)
    except Exception as exc:
        print(f"ERRO: não foi possível inicializar o cliente Sheets — {exc}")
        sys.exit(1)


def _fetch_rows(service, spreadsheet_id: str, sheet_name: str) -> list[list[str]]:
    print(f"  Lendo aba: {sheet_name}")
    try:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{sheet_name}'!A:Z")
            .execute()
        )
    except Exception as exc:
        msg = str(exc)
        if "403" in msg or "PERMISSION_DENIED" in msg:
            print(f"ERRO: acesso negado à aba '{sheet_name}'.")
        elif "404" in msg or "NOT_FOUND" in msg:
            print(f"ERRO: aba '{sheet_name}' não encontrada.")
        elif "401" in msg or "UNAUTHENTICATED" in msg:
            print("ERRO: credenciais expiradas. Execute: gcloud auth application-default login")
        else:
            print(f"ERRO: falha ao ler '{sheet_name}' — {exc}")
        sys.exit(1)
    rows = result.get("values", [])
    print(f"    rows_read: {len(rows)}")
    return rows


def _get_sheet_id(service, spreadsheet_id: str, sheet_name: str) -> int:
    """Retorna o ID numérico da aba (necessário para deleteDimension)."""
    try:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    except Exception as exc:
        print(f"ERRO: não foi possível obter metadados — {exc}")
        sys.exit(1)
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == sheet_name:
            return s["properties"]["sheetId"]
    print(f"ERRO: aba '{sheet_name}' não encontrada nos metadados.")
    sys.exit(1)


def _col_letter(idx: int) -> str:
    letters = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


# ── Parsing ───────────────────────────────────────────────────────────────────

def _first_col(col_map: dict[str, int], *names: str) -> int | None:
    for name in names:
        if name in col_map:
            return col_map[name]
    return None


def _parse_pcmso(rows: list) -> list[dict]:
    """Filtra e sanitiza registros elegíveis. Nunca expõe campos bloqueados."""
    if not rows:
        print("ERRO: aba PCMSO está vazia.")
        sys.exit(1)

    headers_norm = [_norm(str(c)) for c in rows[0]]
    col_map = {h: i for i, h in enumerate(headers_norm)}

    blocked_idx: set[int] = {
        i for h, i in col_map.items()
        if any(sub in h for sub in _BLOCKED_COL_SUBSTRINGS)
    }

    nome_idx     = _first_col(col_map, "nome da clinica/empresa", "nome da clinica", "nome")
    tipo_idx     = _first_col(col_map, "tipo")
    bairro_idx   = _first_col(col_map, "bairro/regiao", "bairro/região", "bairro")
    status_idx   = _first_col(col_map, "status")
    interesse_idx = _first_col(col_map, "interesse")
    prox_idx     = _first_col(col_map, "proximo passo", "próximo passo")
    data_idx     = _first_col(col_map, "data do proximo passo", "data do próximo passo")

    if nome_idx is None:
        print("ERRO: coluna 'Nome da clínica/empresa' não encontrada.")
        sys.exit(1)
    if status_idx is None:
        print("ERRO: coluna 'Status' não encontrada.")
        sys.exit(1)

    def cell(row: list, idx: int | None) -> str:
        if idx is None or idx in blocked_idx:
            return ""
        return str(row[idx]).strip() if idx < len(row) else ""

    today_str = date.today().isoformat()
    records: list[dict] = []
    skipped_block = 0
    skipped_unknown = 0

    for row_num, row in enumerate(rows[1:], start=2):
        nome = cell(row, nome_idx)
        if not nome:
            continue

        status_key = _norm(cell(row, status_idx))

        if status_key in _STATUS_BLOCK:
            skipped_block += 1
            continue
        if status_key not in _STATUS_MAP:
            skipped_unknown += 1
            continue

        etapa, prioridade = _STATUS_MAP[status_key]

        bairro       = cell(row, bairro_idx)
        tipo         = cell(row, tipo_idx)
        proxima_acao = cell(row, prox_idx)
        data_raw     = cell(row, data_idx)
        data_proxima_acao = _parse_date(data_raw) or _fallback_date(status_key)
        ultima       = today_str

        interesse = cell(row, interesse_idx)
        observacao = ""
        if interesse:
            _check_safe(interesse, f"Interesse linha {row_num}")
            observacao = interesse[:200]

        _check_safe(nome,        f"Nome linha {row_num}")
        _check_safe(bairro,      f"Bairro linha {row_num}")
        _check_safe(tipo,        f"Tipo linha {row_num}")
        _check_safe(proxima_acao, f"Próximo passo linha {row_num}")

        records.append({
            "_key": _key(nome),
            "nome_clinica":      nome,
            "bairro":            bairro,
            "regiao":            "",
            "tipo_clinica":      tipo,
            "etapa":             etapa,
            "ultima_interacao":  ultima,
            "proxima_acao":      proxima_acao,
            "data_proxima_acao": data_proxima_acao,
            "responsavel":       "Comercial",
            "prioridade":        prioridade,
            "observacao":        observacao,
        })

    print(f"    qualificados: {len(records)}")
    print(f"    ignorados (status não elegível): {skipped_block}")
    if skipped_unknown:
        print(f"    ignorados (status desconhecido): {skipped_unknown}")
    return records


def _parse_crm(rows: list) -> tuple[list[dict], int]:
    """
    Retorna (lista de registros CRM com metadados, próximo ID numérico).
    Cada item: sheet_row (1-based), clinica_id, nome_clinica, _key, col_map, raw_row.
    """
    if len(rows) < 2:
        return [], 1

    headers_norm = [_norm(str(c)) for c in rows[0]]
    col_map = {h: i for i, h in enumerate(headers_norm)}

    id_idx   = col_map.get("clinica_id", 0)
    nome_idx = col_map.get("nome_clinica", 1)

    crm_records: list[dict] = []
    max_id = 0

    for sheet_row, row in enumerate(rows[1:], start=2):
        def cell(i: int) -> str:
            return str(row[i]).strip() if i < len(row) else ""

        nome   = cell(nome_idx)
        raw_id = cell(id_idx)
        if not nome:
            continue

        m = re.search(r"\d+", raw_id)
        if m:
            max_id = max(max_id, int(m.group()))

        crm_records.append({
            "sheet_row":   sheet_row,
            "clinica_id":  raw_id,
            "nome_clinica": nome,
            "_key":        _key(nome),
            "col_map":     col_map,
            "raw_row":     list(row),
        })

    return crm_records, max_id + 1


def _find_crm_match(pcmso_key: str, crm_records: list[dict]) -> dict | None:
    """Retorna o registro CRM que casa com a chave PCMSO, ou None."""
    for rec in crm_records:
        if _matches(pcmso_key, rec["_key"]):
            return rec
    return None


# ── Modo: promoção ────────────────────────────────────────────────────────────

def run_promote(service, spreadsheet_id: str, pcmso_sheet: str, crm_sheet: str,
                write: bool) -> int:
    mode = "write" if write else "dry-run"
    print(f"── Promoção PCMSO → CRM ({mode}) ──")
    print()

    print("Lendo abas...")
    pcmso_rows = _fetch_rows(service, spreadsheet_id, pcmso_sheet)
    crm_rows   = _fetch_rows(service, spreadsheet_id, crm_sheet)
    print()

    print("Processando base PCMSO...")
    pcmso_records = _parse_pcmso(pcmso_rows)
    crm_records, next_id_num = _parse_crm(crm_rows)
    print(f"    existentes no CRM: {len(crm_records)}")
    print()

    crm_col_map: dict[str, int] = {}
    if crm_rows:
        crm_col_map = {_norm(str(c)): i for i, c in enumerate(crm_rows[0])}

    to_create: list[dict] = []
    to_update: list[tuple[dict, dict]] = []

    for rec in pcmso_records:
        match = _find_crm_match(rec["_key"], crm_records)
        if match:
            to_update.append((match, rec))
        else:
            to_create.append(rec)

    print("─" * 52)
    print(f"  Novas clínicas a criar:  {len(to_create)}")
    print(f"  Clínicas a atualizar:    {len(to_update)}")
    print("─" * 52)

    if not to_create and not to_update:
        print("Nenhuma alteração necessária.")
        return 0

    if not write:
        print()
        print("DRY RUN — nenhuma alteração na planilha.")
        if to_create:
            print(f"\nSeriam criadas ({len(to_create)}):")
            for r in to_create:
                data_tag = f"  data={r['data_proxima_acao']}" if r.get("data_proxima_acao") else "  sem data"
                print(f"  + [{r['etapa']:20s}] [{r['prioridade']:5s}]  {r['nome_clinica'][:40]}{data_tag}")
        if to_update:
            print(f"\nSeriam atualizadas ({len(to_update)}):")
            for crm_rec, rec in to_update:
                data_tag = f"  data={rec['data_proxima_acao']}" if rec.get("data_proxima_acao") else "  sem data"
                print(f"  ~ [{rec['etapa']:20s}] [{rec['prioridade']:5s}]  {rec['nome_clinica'][:40]}{data_tag}")
                print(f"    ↳ CRM atual: {crm_rec['nome_clinica'][:50]}")
        all_recs = list(to_create) + [rec for _, rec in to_update]
        n_date = sum(1 for r in all_recs if r.get("data_proxima_acao"))
        n_empty = len(all_recs) - n_date
        print()
        print(f"  data_proxima_acao preenchida: {n_date}  /  vazia (Parceiro ativo): {n_empty}")
        print()
        print("Para executar: --write")
        return 0

    sheets_api = service.spreadsheets()

    if to_update:
        print(f"\nAtualizando {len(to_update)} clínicas...")
        batch: list[dict] = []
        for crm_rec, rec in to_update:
            row_num = crm_rec["sheet_row"]
            col_map = crm_rec["col_map"]
            for field, value in {
                "etapa":             rec["etapa"],
                "ultima_interacao":  rec["ultima_interacao"],
                "proxima_acao":      rec["proxima_acao"],
                "data_proxima_acao": rec["data_proxima_acao"],
                "prioridade":        rec["prioridade"],
            }.items():
                col_idx = col_map.get(field)
                if col_idx is None:
                    continue
                batch.append({
                    "range": f"'{crm_sheet}'!{_col_letter(col_idx)}{row_num}",
                    "values": [[value]],
                })
        if batch:
            sheets_api.values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": batch},
            ).execute()
            print(f"  {len(to_update)} atualizadas.")

    if to_create:
        print(f"\nCriando {len(to_create)} novas clínicas...")
        new_rows = []
        num_cols = len(crm_col_map) if crm_col_map else 12
        for i, rec in enumerate(to_create):
            cid = f"CL{next_id_num + i:03d}"
            field_values = {
                "clinica_id":        cid,
                "nome_clinica":      rec["nome_clinica"],
                "bairro":            rec["bairro"],
                "regiao":            rec["regiao"],
                "tipo_clinica":      rec["tipo_clinica"],
                "etapa":             rec["etapa"],
                "ultima_interacao":  rec["ultima_interacao"],
                "proxima_acao":      rec["proxima_acao"],
                "data_proxima_acao": rec["data_proxima_acao"],
                "responsavel":       rec["responsavel"],
                "prioridade":        rec["prioridade"],
                "observacao":        rec["observacao"],
            }
            if crm_col_map:
                row_data: list[str] = [""] * num_cols
                for field, value in field_values.items():
                    col_idx = crm_col_map.get(field)
                    if col_idx is not None and col_idx < num_cols:
                        row_data[col_idx] = value
            else:
                row_data = list(field_values.values())
            new_rows.append(row_data)
        last_col = _col_letter(num_cols - 1) if crm_col_map else "L"
        sheets_api.values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{crm_sheet}'!A:{last_col}",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": new_rows},
        ).execute()
        print(f"  {len(to_create)} criadas.")

    print()
    print("Promoção concluída.")
    print("Não exportado: telefone, Instagram/Site, pessoa de contato, Observações, CPF, e-mail.")
    print()
    print("Próximo passo: painel-soprolife/scripts/update-local-data.sh")
    return 0


# ── Modo: deduplicação ────────────────────────────────────────────────────────

def run_dedup(service, spreadsheet_id: str, crm_sheet: str, write: bool) -> int:
    mode = "write" if write else "dry-run"
    print(f"── Deduplicação CRM Clínicas ({mode}) ──")
    print()

    print("Lendo CRM...")
    crm_rows = _fetch_rows(service, spreadsheet_id, crm_sheet)
    crm_records, _ = _parse_crm(crm_rows)
    print(f"  total de registros: {len(crm_records)}")
    print()

    # Agrupar por chave de matching
    groups: dict[str, list[dict]] = {}
    for rec in crm_records:
        placed = False
        for existing_key in list(groups.keys()):
            if _matches(rec["_key"], existing_key):
                groups[existing_key].append(rec)
                placed = True
                break
        if not placed:
            groups[rec["_key"]] = [rec]

    duplicates_found = {k: v for k, v in groups.items() if len(v) > 1}

    if not duplicates_found:
        print("Nenhuma duplicata encontrada. CRM está limpo.")
        return 0

    print(f"Grupos com duplicatas: {len(duplicates_found)}")
    print()

    # Para cada grupo: keeper = linha mais antiga (menor sheet_row)
    # dupes = linhas mais recentes a deletar
    keepers_to_update: list[tuple[dict, dict]] = []  # (keeper, fonte_dos_dados)
    rows_to_delete: list[int] = []                    # sheet_row (1-based)

    for group_key, members in duplicates_found.items():
        members_sorted = sorted(members, key=lambda r: r["sheet_row"])
        keeper = members_sorted[0]
        dupes  = members_sorted[1:]

        # Usar os dados do dupe mais recente (última linha) para atualizar o keeper
        latest = members_sorted[-1]

        print(f"  Grupo '{group_key}':")
        print(f"    MANTER  → linha {keeper['sheet_row']}: {keeper['nome_clinica'][:50]}")
        for d in dupes:
            print(f"    DELETAR → linha {d['sheet_row']}: {d['nome_clinica'][:50]}")

        keepers_to_update.append((keeper, latest))
        rows_to_delete.extend(d["sheet_row"] for d in dupes)

    print()
    print(f"Linhas a deletar: {sorted(rows_to_delete)}")

    if not write:
        print()
        print("DRY RUN — nenhuma alteração na planilha.")
        print("Para executar: --dedup --write")
        return 0

    sheet_id = _get_sheet_id(service, spreadsheet_id, crm_sheet)
    sheets_api = service.spreadsheets()

    # 1. Atualizar keepers com dados mais recentes antes de deletar
    print("\nAtualizando registros originais com dados mais recentes...")
    batch: list[dict] = []
    for keeper, latest in keepers_to_update:
        row_num = keeper["sheet_row"]
        col_map = keeper["col_map"]
        raw_latest = latest["raw_row"]
        # Atualiza: etapa, ultima_interacao, proxima_acao, prioridade
        for field in ("etapa", "ultima_interacao", "proxima_acao", "prioridade"):
            col_idx = col_map.get(field)
            if col_idx is None:
                continue
            value = raw_latest[col_idx] if col_idx < len(raw_latest) else ""
            batch.append({
                "range": f"'{crm_sheet}'!{_col_letter(col_idx)}{row_num}",
                "values": [[value]],
            })
    if batch:
        sheets_api.values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": batch},
        ).execute()
        print(f"  {len(keepers_to_update)} registros originais atualizados.")

    # 2. Deletar linhas duplicadas de baixo para cima (preserva índices)
    print(f"\nDeletando {len(rows_to_delete)} linhas duplicadas...")
    delete_requests = []
    for row_num in sorted(rows_to_delete, reverse=True):
        zero_idx = row_num - 1  # planilha é 1-based, API é 0-based
        delete_requests.append({
            "deleteDimension": {
                "range": {
                    "sheetId":    sheet_id,
                    "dimension":  "ROWS",
                    "startIndex": zero_idx,
                    "endIndex":   zero_idx + 1,
                }
            }
        })

    if delete_requests:
        sheets_api.batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": delete_requests},
        ).execute()
        print(f"  {len(rows_to_delete)} linhas deletadas.")

    print()
    print("Deduplicação concluída.")
    return 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="SoproLife — Promoção PCMSO → CRM / Deduplicação CRM"
    )
    parser.add_argument("--dedup", action="store_true",
                        help="Modo deduplicação: consolida duplicatas no CRM")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Preview sem gravar (padrão quando --write omitido)")
    parser.add_argument("--write", action="store_true",
                        help="Executa as alterações na planilha")
    args = parser.parse_args()

    mode_label = ("dedup" if args.dedup else "promoção") + (" write" if args.write else " dry-run")
    print("SoproLife OS Local Core — Promoção PCMSO → CRM Clínicas")
    print(f"mode: {mode_label}")
    print("auth: Application Default Credentials")
    print()

    build, google_auth_default = _load_google_libs()
    spreadsheet_id, pcmso_sheet, crm_sheet = _load_config()

    print("config: carregada")
    if not args.dedup:
        print(f"aba origem:  {pcmso_sheet}")
    print(f"aba destino: {crm_sheet}")
    print()

    print("Conectando à API Google Sheets...")
    service = _build_service(build, google_auth_default)
    print()

    if args.dedup:
        return run_dedup(service, spreadsheet_id, crm_sheet, write=args.write)
    else:
        return run_promote(service, spreadsheet_id, pcmso_sheet, crm_sheet, write=args.write)


if __name__ == "__main__":
    sys.exit(main())
