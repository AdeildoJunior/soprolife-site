#!/usr/bin/env python3
"""
SoproLife OS Local Core — Geração de Follow-up de Pacientes (ADC).

Lê CRM Espirometria e CRM Consultas via Google Sheets ADC.
Gera dois arquivos:
  - data-private/followup-pacientes.local.json   → privado, gitignored, tem nome/telefone/URL
  - data/followup-pacientes-summary.local.json   → apenas contagens, seguro para o painel

Regras:
  Espirometria: data_followup = max(data_exame + 5 meses, proximo_contato se preenchido)
  Consultas:    data_followup = proximo_contato se preenchido, senão sem_data

Status:
  atrasado   → data_followup < hoje
  hoje       → data_followup == hoje
  em_breve   → hoje < data_followup <= hoje + 7 dias
  futuro     → data_followup > hoje + 7 dias
  sem_data   → data_followup não determinável

Formatos de data suportados:
  DD/MM/YYYY · YYYY-MM-DD · DD-MM-YYYY · YYYY/MM/DD
  MM/YYYY    · YYYY/MM (mês/ano sem dia → dia 1)
  MMMM/YYYY  · MMMM YYYY  (mês por extenso em português)
  Serial numérico do Google Sheets

Segurança:
  - observacao_privada_minima NUNCA é lida
  - dry-run mostra apenas contagens (zero nomes nem telefones)
  - arquivos de saída são gitignored

Uso:
    python3 painel-soprolife/scripts/generate-followup-pacientes.py --dry-run
    python3 painel-soprolife/scripts/generate-followup-pacientes.py --write
"""

import argparse
import calendar
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

_CONFIG_PATH = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

PRIVATE_OUT  = Path("painel-soprolife/data-private/followup-pacientes.local.json")
SUMMARY_OUT  = Path("painel-soprolife/data/followup-pacientes-summary.local.json")

ABA_ESPI     = "CRM Espirometria"
ABA_CONSULTA = "CRM Consultas"

_BLOCKED_COLS = {"observacao_privada_minima", "observacao", "observação"}

_CONSENT_POSITIVO = {"sim", "s", "yes", "y", "1", "aceito", "ok", "ativo", "true"}
_CONSENT_NEGATIVO = {"não", "nao", "n", "no", "0", "recusa", "sair", "false"}

# Meses por extenso em português (normalizado: sem acento, minúsculo)
_MESES_PT: dict[str, int] = {
    "janeiro": 1,  "fevereiro": 2, "marco": 3,    "abril": 4,
    "maio": 5,     "junho": 6,     "julho": 7,    "agosto": 8,
    "setembro": 9, "outubro": 10,  "novembro": 11, "dezembro": 12,
}

# Epoch do Google Sheets: dia 1 = 1900-01-01 (com bug do ano bissexto)
_SHEETS_EPOCH = date(1899, 12, 30)

_FORBIDDEN_IN_MOTIVO = (
    "cpf", "pedido médico", "pedido medico", "laudo",
    "diagnóstico", "diagnostico", "endereço", "endereco",
)

MSG_ESPI = (
    "Olá, {nome}. Tudo bem?\n\n"
    "Aqui é da SoproLife. Estamos entrando em contato porque seu exame de "
    "espirometria foi realizado há alguns meses.\n\n"
    "Em alguns acompanhamentos respiratórios, o médico pode solicitar uma nova "
    "espirometria para comparar a evolução da função pulmonar.\n\n"
    "Caso seu médico tenha orientado acompanhamento ou queira verificar a "
    "possibilidade de nova avaliação, podemos ajudar no agendamento.\n\n"
    "Se não quiser receber lembretes da SoproLife, responda SAIR."
)

MSG_CONSULTA = (
    "Olá, {nome}. Tudo bem?\n\n"
    "Aqui é da SoproLife. Estamos passando para saber como você está após sua "
    "consulta e se precisa de apoio para agendar retorno, exame ou continuidade "
    "do acompanhamento.\n\n"
    "Se desejar falar com a equipe, é só responder esta mensagem.\n\n"
    "Se não quiser receber lembretes da SoproLife, responda SAIR."
)


# ── Normalização e parse de data ───────────────────────────────────────────────

def _norm(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain.lower().strip())


def _parse_date(raw: str) -> date | None:
    """
    Converte string para date. Suporta:
      - DD/MM/YYYY · YYYY-MM-DD · DD-MM-YYYY · YYYY/MM/DD  (dia completo)
      - MM/YYYY  ou  YYYY/MM                               (mês/ano → dia 1)
      - MMMM/YYYY · MMMM YYYY · MMMM-YYYY                 (mês PT → dia 1)
      - número inteiro (serial Google Sheets)
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None

    # 1. Formatos com dia completo
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass

    # 2. Serial numérico do Google Sheets (ex: "45000")
    if re.match(r"^\d{4,6}$", raw):
        try:
            serial = int(raw)
            if 10000 <= serial <= 100000:
                return _SHEETS_EPOCH + timedelta(days=serial)
        except (ValueError, OverflowError):
            pass

    # 3. MM/YYYY  (ex: "06/2026")
    m = re.match(r"^(\d{1,2})/(\d{4})$", raw)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 2000 <= year <= 2100:
            return date(year, month, 1)

    # 4. YYYY/MM  (ex: "2026/06")
    m = re.match(r"^(\d{4})/(\d{1,2})$", raw)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 2000 <= year <= 2100:
            return date(year, month, 1)

    # 5. Mês por extenso em português: "dezembro/2026", "dezembro 2026", "dezembro-2026"
    raw_n = _norm(raw)
    for sep in ("/", " ", "-"):
        parts = raw_n.split(sep, 1)
        if len(parts) == 2:
            mes_str = parts[0].strip()
            ano_str = parts[1].strip()
            month = _MESES_PT.get(mes_str)
            if month and re.match(r"^\d{4}$", ano_str):
                year = int(ano_str)
                if 2000 <= year <= 2100:
                    return date(year, month, 1)

    return None


def _add_months(d: date, months: int) -> date:
    month = d.month + months
    year  = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day   = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _followup_status(followup_date: date | None, today: date) -> str:
    if followup_date is None:
        return "sem_data"
    delta = (followup_date - today).days
    if delta < 0:
        return "atrasado"
    if delta == 0:
        return "hoje"
    if delta <= 7:
        return "em_breve"
    return "futuro"


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("0"):
        digits = digits[1:]
    if len(digits) in (10, 11):
        return "55" + digits
    if len(digits) in (12, 13) and digits.startswith("55"):
        return digits
    return digits


def _whatsapp_url(phone: str, msg: str) -> str:
    return f"https://wa.me/{phone}?text={quote(msg)}"


def _consent_ok(raw: str) -> bool:
    v = _norm(raw)
    if not v:
        return False
    if v in _CONSENT_NEGATIVO:
        return False
    return True


def _sanitize_motivo(raw: str, label: str) -> str:
    """Valida que motivo não contém dados sensíveis; retorna vazio em vez de abortar."""
    lower = raw.lower()
    for term in _FORBIDDEN_IN_MOTIVO:
        if term in lower:
            print(f"  AVISO: motivo sanitizado em {label} (termo: '{term}').")
            return ""
    return raw[:300]


def _summarize(records: list[dict]) -> dict:
    return {
        "total":         len(records),
        "hoje":          sum(1 for r in records if r["status_followup"] == "hoje"),
        "atrasados":     sum(1 for r in records if r["status_followup"] == "atrasado"),
        "proximos7dias": sum(1 for r in records if r["status_followup"] == "em_breve"),
        "futuro":        sum(1 for r in records if r["status_followup"] == "futuro"),
        "semData":       sum(1 for r in records if r["status_followup"] == "sem_data"),
    }


def _print_date_diag(label: str, total: int, preenchidas: int, parseáveis: int) -> None:
    pct_p = int(preenchidas / total * 100) if total else 0
    pct_a = int(parseáveis  / total * 100) if total else 0
    print(f"    {label}:")
    print(f"      total linhas: {total}")
    print(f"      preenchidas:  {preenchidas} ({pct_p}%)")
    print(f"      parseáveis:   {parseáveis} ({pct_a}%)")
    if preenchidas > parseáveis:
        print(f"      NÃO parseáveis: {preenchidas - parseáveis} "
              f"(verificar formato na planilha)")


# ── Google Sheets ─────────────────────────────────────────────────────────────

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
        print(f"ERRO: configuração não encontrada em {_CONFIG_PATH}")
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
        print(f"  Execute: gcloud auth application-default login --scopes={SHEETS_SCOPE}")
        sys.exit(1)


def _fetch_rows(service, sid: str, aba: str) -> list[list[str]]:
    print(f"  Lendo aba: {aba!r}")
    try:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sid, range=f"'{aba}'!A:Z")
            .execute()
        )
    except Exception as exc:
        msg = str(exc)
        if "404" in msg or "not found" in msg.lower() or "Unable to parse" in msg:
            print(f"  AVISO: aba '{aba}' não encontrada.")
            return []
        print(f"  ERRO ao ler '{aba}': {exc}")
        sys.exit(1)
    rows = result.get("values", [])
    print(f"    rows_read: {len(rows)}")
    return rows


# ── Parsers de aba ────────────────────────────────────────────────────────────

def _col_map(rows: list) -> dict[str, int]:
    if not rows:
        return {}
    return {_norm(str(h)): i for i, h in enumerate(rows[0])}


def _cell(row: list, idx: int | None, blocked: set[int]) -> str:
    if idx is None or idx in blocked:
        return ""
    return str(row[idx]).strip() if idx < len(row) else ""


def _blocked_indices(col_map: dict[str, int]) -> set[int]:
    return {i for h, i in col_map.items() if any(b in h for b in _BLOCKED_COLS)}


def parse_espirometria(rows: list, today: date) -> list[dict]:
    if len(rows) < 2:
        return []
    cm = _col_map(rows)
    blocked = _blocked_indices(cm)

    i_id        = cm.get("exame_id")
    i_nome      = cm.get("primeiro_nome")
    i_telefone  = cm.get("telefone")
    i_data_exame = cm.get("data_exame")
    i_prox_cont  = cm.get("proximo_contato")
    i_motivo     = cm.get("motivo_proximo_contato")
    i_consent    = cm.get("consentimento_whatsapp")

    records = []
    n_sem_consent  = 0
    n_data_preen   = 0   # data_exame preenchida (raw não vazio)
    n_data_parse   = 0   # data_exame parseável
    n_prox_preen   = 0   # proximo_contato preenchido
    n_prox_parse   = 0   # proximo_contato parseável
    n_sem_data     = 0   # nenhuma data determina followup
    n_recentes     = 0   # exame < 5 meses (incluso como futuro)

    for row_num, row in enumerate(rows[1:], start=2):
        def c(idx): return _cell(row, idx, blocked)

        nome     = c(i_nome)
        telefone = c(i_telefone)
        consent  = c(i_consent)
        exame_id = c(i_id) or f"EX{row_num:03d}"

        if not nome:
            continue

        if not _consent_ok(consent):
            n_sem_consent += 1
            continue

        # ── data_exame: diagnóstico + parse ──
        data_exame_raw = c(i_data_exame)
        if data_exame_raw:
            n_data_preen += 1
        data_exame = _parse_date(data_exame_raw)
        if data_exame:
            n_data_parse += 1

        # ── proximo_contato: diagnóstico + parse ──
        prox_raw = c(i_prox_cont)
        if prox_raw:
            n_prox_preen += 1
        prox_date = _parse_date(prox_raw)
        if prox_date:
            n_prox_parse += 1

        # ── Determina data de follow-up ──
        # Prioridade: 1) proximo_contato (equipe já agendou)
        #             2) data_exame + 5 meses (regra automática)
        if prox_date:
            followup_date = prox_date
            motivo_base   = f"exame em {data_exame.isoformat()[:7] if data_exame else '?'}"
        elif data_exame:
            followup_calc = _add_months(data_exame, 5)
            followup_date = followup_calc
            motivo_base   = f"espirometria realizada em {data_exame.isoformat()[:7]}"
            if followup_calc > today:
                n_recentes += 1
        else:
            followup_date = None
            motivo_base   = "data de exame não preenchida ou não reconhecida"
            n_sem_data   += 1

        motivo_extra = _sanitize_motivo(c(i_motivo), f"EX linha {row_num}")
        motivo = motivo_extra if motivo_extra else motivo_base

        status = _followup_status(followup_date, today)
        phone  = _normalize_phone(telefone)
        msg    = MSG_ESPI.format(nome=nome)
        wa_url = _whatsapp_url(phone, msg) if phone else ""

        records.append({
            "tipo_followup":   "espirometria",
            "id":              exame_id,
            "nome":            nome,
            "telefone":        phone,
            "data_base":       data_exame.isoformat() if data_exame else "",
            "data_followup":   followup_date.isoformat() if followup_date else "",
            "status_followup": status,
            "motivo":          motivo,
            "consentimento":   True,
            "whatsapp_url":    wa_url,
        })

    total_linhas = len(rows) - 1
    print(f"    total linhas de dados: {total_linhas}")
    print(f"    sem consentimento: {n_sem_consent}")
    print(f"    incluídos no follow-up: {len(records)}")
    _print_date_diag("data_exame",      total_linhas, n_data_preen, n_data_parse)
    _print_date_diag("proximo_contato", total_linhas, n_prox_preen, n_prox_parse)
    print(f"    exame recente (<5 meses, status futuro): {n_recentes}")
    print(f"    sem data determinável: {n_sem_data}")
    return records


def parse_consultas(rows: list, today: date) -> list[dict]:
    if len(rows) < 2:
        return []
    cm = _col_map(rows)
    blocked = _blocked_indices(cm)

    i_id         = cm.get("consulta_id")
    i_nome       = cm.get("primeiro_nome")
    i_telefone   = cm.get("telefone")
    i_data_cons  = cm.get("data_consulta")
    i_prox_cont  = cm.get("proximo_contato")
    i_motivo     = cm.get("motivo_proximo_contato")
    i_consent    = cm.get("consentimento_whatsapp")

    records = []
    n_sem_consent = 0
    n_cons_preen  = 0
    n_cons_parse  = 0
    n_prox_preen  = 0
    n_prox_parse  = 0
    n_sem_data    = 0

    for row_num, row in enumerate(rows[1:], start=2):
        def c(idx): return _cell(row, idx, blocked)

        nome     = c(i_nome)
        telefone = c(i_telefone)
        consent  = c(i_consent)
        cons_id  = c(i_id) or f"CS{row_num:03d}"

        if not nome:
            continue

        if not _consent_ok(consent):
            n_sem_consent += 1
            continue

        # ── data_consulta ──
        data_cons_raw = c(i_data_cons)
        if data_cons_raw:
            n_cons_preen += 1
        data_cons = _parse_date(data_cons_raw)
        if data_cons:
            n_cons_parse += 1

        # ── proximo_contato ──
        prox_raw  = c(i_prox_cont)
        if prox_raw:
            n_prox_preen += 1
        prox_date = _parse_date(prox_raw)
        if prox_date:
            n_prox_parse += 1

        # ── Determina data de follow-up ──
        if prox_date:
            followup_date = prox_date
            motivo_base   = f"consulta em {data_cons.isoformat()[:7] if data_cons else '?'}"
        elif data_cons:
            followup_date = data_cons  # sem regra fixa: usa data da consulta como base
            motivo_base   = f"consulta realizada em {data_cons.isoformat()[:7]}"
        else:
            followup_date = None
            motivo_base   = "data de consulta não preenchida ou não reconhecida"
            n_sem_data   += 1

        motivo_extra = _sanitize_motivo(c(i_motivo), f"CS linha {row_num}")
        motivo = motivo_extra if motivo_extra else motivo_base

        status = _followup_status(followup_date, today)
        phone  = _normalize_phone(telefone)
        msg    = MSG_CONSULTA.format(nome=nome)
        wa_url = _whatsapp_url(phone, msg) if phone else ""

        records.append({
            "tipo_followup":   "consulta",
            "id":              cons_id,
            "nome":            nome,
            "telefone":        phone,
            "data_base":       data_cons.isoformat() if data_cons else "",
            "data_followup":   followup_date.isoformat() if followup_date else "",
            "status_followup": status,
            "motivo":          motivo,
            "consentimento":   True,
            "whatsapp_url":    wa_url,
        })

    total_linhas = len(rows) - 1
    print(f"    total linhas de dados: {total_linhas}")
    print(f"    sem consentimento: {n_sem_consent}")
    print(f"    incluídos no follow-up: {len(records)}")
    _print_date_diag("data_consulta",   total_linhas, n_cons_preen, n_cons_parse)
    _print_date_diag("proximo_contato", total_linhas, n_prox_preen, n_prox_parse)
    print(f"    sem data determinável: {n_sem_data}")
    return records


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gerador de follow-up de pacientes — SoproLife"
    )
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Mostra contagens sem gravar (padrão)")
    parser.add_argument("--write", action="store_true",
                        help="Grava os arquivos de saída")
    args = parser.parse_args()

    write = args.write
    mode  = "write" if write else "dry-run"

    print("SoproLife OS Local Core — Follow-up de Pacientes")
    print(f"mode: {mode}")
    print("auth: Application Default Credentials")
    print("SEGURANÇA: observacao_privada_minima nunca é lida")
    print()

    build, auth_default = _load_libs()
    sid = _load_config()
    service = _build_service(build, auth_default)

    today = date.today()
    print(f"Data de referência: {today.isoformat()}")
    print()

    print("Lendo abas...")
    rows_espi     = _fetch_rows(service, sid, ABA_ESPI)
    rows_consulta = _fetch_rows(service, sid, ABA_CONSULTA)
    print()

    print("Processando CRM Espirometria...")
    espi_records = parse_espirometria(rows_espi, today)
    print()
    print("Processando CRM Consultas...")
    cons_records = parse_consultas(rows_consulta, today)
    print()

    summary_espi = _summarize(espi_records)
    summary_cons = _summarize(cons_records)

    print("─" * 55)
    print("Resumo — Espirometria:")
    print(f"  Total incluído:      {summary_espi['total']}")
    print(f"  Hoje:                {summary_espi['hoje']}")
    print(f"  Atrasados:           {summary_espi['atrasados']}")
    print(f"  Próximos 7 dias:     {summary_espi['proximos7dias']}")
    print(f"  Futuro (>7d):        {summary_espi['futuro']}")
    print(f"  Sem data:            {summary_espi['semData']}")
    print()
    print("Resumo — Consultas:")
    print(f"  Total incluído:      {summary_cons['total']}")
    print(f"  Hoje:                {summary_cons['hoje']}")
    print(f"  Atrasados:           {summary_cons['atrasados']}")
    print(f"  Próximos 7 dias:     {summary_cons['proximos7dias']}")
    print(f"  Futuro (>7d):        {summary_cons['futuro']}")
    print(f"  Sem data:            {summary_cons['semData']}")
    print("─" * 55)
    print()

    if not write:
        print("DRY RUN — nenhum arquivo gerado.")
        print("  Nomes e telefones NÃO foram exibidos.")
        print()
        print("Para gerar os arquivos: --write")
        return 0

    # ── Arquivo privado ────────────────────────────────────────────────────────
    private_payload = {
        "geradoEm":  datetime.now(timezone.utc).isoformat(),
        "referencia": today.isoformat(),
        "aviso":     "ARQUIVO PRIVADO — não commitar. Contém nome e telefone de pacientes.",
        "espirometria": espi_records,
        "consultas":    cons_records,
        "resumo": {
            "espirometria": summary_espi,
            "consultas":    summary_cons,
        },
    }

    PRIVATE_OUT.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE_OUT.write_text(
        json.dumps(private_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    PRIVATE_OUT.chmod(0o600)
    print(f"Arquivo privado gerado:  {PRIVATE_OUT}")
    print(f"  (chmod 600, gitignored via data-private/)")

    # ── Resumo público (apenas contagens) ─────────────────────────────────────
    summary_payload = {
        "geradoEm":          datetime.now(timezone.utc).isoformat(),
        "referencia":        today.isoformat(),
        "safeToDisplay":     True,
        "containsPersonalData": False,
        "containsHealthData":   False,
        "espirometria": summary_espi,
        "consultas":    summary_cons,
    }

    SUMMARY_OUT.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_OUT.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Resumo seguro gerado:    {SUMMARY_OUT}")
    print(f"  (gitignored via *.local.json)")
    print()
    print("Próximo passo: painel-soprolife/scripts/check-access.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
