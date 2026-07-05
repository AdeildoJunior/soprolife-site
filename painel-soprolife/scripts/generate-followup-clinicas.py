#!/usr/bin/env python3
"""
SoproLife OS Local Core — Geração de Follow-up B2B Clínicas (ADC).

Lê CRM Clinicas + Base Prospecção B2B PCMSO via Google Sheets ADC.
Faz join por nome de clínica (via _key()) para obter telefone/WhatsApp.
Gera dois arquivos:
  - data-private/followup-clinicas.local.json   → privado, gitignored, tem telefone/URL
  - data/followup-clinicas-summary.local.json   → apenas contagens, seguro para o painel

Status:
  atrasado   → data_proxima_acao < hoje
  hoje       → data_proxima_acao == hoje
  em_breve   → hoje < data_proxima_acao <= hoje + 7 dias
  futuro     → data_proxima_acao > hoje + 7 dias
  sem_data   → data_proxima_acao não determinável

Segurança:
  - observações/dados de pacientes nunca são lidos
  - dry-run mostra apenas contagens (zero nomes nem telefones)
  - arquivos de saída são gitignored

Uso:
    python3 painel-soprolife/scripts/generate-followup-clinicas.py --dry-run
    python3 painel-soprolife/scripts/generate-followup-clinicas.py --write
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

_CONFIG_PATH = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

PRIVATE_OUT = Path("painel-soprolife/data-private/followup-clinicas.local.json")
SUMMARY_OUT = Path("painel-soprolife/data/followup-clinicas-summary.local.json")

DEFAULT_PCMSO_SHEET = "Base Prospecção B2B PCMSO"
DEFAULT_CRM_SHEET   = "CRM Clinicas"

# Colunas do PCMSO que não devem ser lidas (segurança)
_BLOCKED_PCMSO_COL_SUBSTRINGS = ("observa", "laudo", "diagnostico", "cpf")

_MESES_PT: dict[str, int] = {
    "janeiro": 1,  "fevereiro": 2, "marco": 3,    "abril": 4,
    "maio": 5,     "junho": 6,     "julho": 7,    "agosto": 8,
    "setembro": 9, "outubro": 10,  "novembro": 11, "dezembro": 12,
}

_SHEETS_EPOCH = date(1899, 12, 30)

# Mensagem institucional B2B — sem citar nome de pessoa física
MSG_B2B = (
    "Olá! Aqui é da SoproLife.\n\n"
    "Estamos passando para retomar o contato com {nome_clinica} e verificar "
    "se há interesse em avançarmos na parceria de espirometria e diagnósticos "
    "complementares.\n\n"
    "Se quiser conversar, é só responder esta mensagem ou agendar um horário "
    "com nossa equipe. Até breve!"
)


# ── Normalização e parse ───────────────────────────────────────────────────────

_ETAPA_ALIAS: dict[str, str] = {
    "parceira":       "Parceiro ativo",
    "parceiro":       "Parceiro ativo",
    "parceiro ativo": "Parceiro ativo",
    "implantação":    "Parceiro ativo",
    "implantacao":    "Parceiro ativo",
    "abordado":            "Abordada",
    "abordada":            "Abordada",
    "primeiro contato":    "Abordada",
    "não abordado":        "Não abordada",
    "nao abordado":        "Não abordada",
    "não abordada":        "Não abordada",
    "respondeu":           "Em conversa",
    "em conversa":         "Em conversa",
    "reunião":             "Pediu apresentação",
    "reuniao":             "Pediu apresentação",
    "pediu apresentação":  "Pediu apresentação",
    "pediu apresentacao":  "Pediu apresentação",
    "sem resposta":        "Aguardando retorno",
    "aguardando retorno":  "Aguardando retorno",
    "pausada":             "Aguardando retorno",
    "proposta":            "Proposta enviada",
    "proposta enviada":    "Proposta enviada",
    "sem interesse":               "Sem interesse",
    "não contatar / bloqueou":     "Não contatar / bloqueou",
    "não contatar":                "Não contatar / bloqueou",
    "bloqueou":                    "Não contatar / bloqueou",
    "sem canal válido":            "Sem canal válido",
    "sem canal valido":            "Sem canal válido",
    "arquivada":                   "Arquivada",
}

# Etapa terminal = fase comercial que já "fechou" (positiva ou negativamente)
# e por isso sai das listas de prospecção ativa (Atrasados, Alta prioridade,
# Em conversa). Mesmo vocabulário canônico usado em app.js e
# promote-pcmso-to-crm.py (duplicado de propósito, scripts independentes).
CRM_ETAPA_TERMINAL_POSITIVA = "Parceiro ativo"
CRM_ETAPAS_TERMINAIS_NEGATIVAS = [
    "Sem interesse",
    "Não contatar / bloqueou",
    "Sem canal válido",
    "Arquivada",
]
CRM_ETAPAS_TERMINAIS = [CRM_ETAPA_TERMINAL_POSITIVA] + CRM_ETAPAS_TERMINAIS_NEGATIVAS

_ETAPA_NEGATIVA_PATTERNS = [
    (re.compile(r"sem interesse", re.I), "Sem interesse"),
    (re.compile(r"n[aã]o contatar|bloqueou", re.I), "Não contatar / bloqueou"),
    (re.compile(r"encerrar contato|n[aã]o insistir|perdid[oa]", re.I), "Arquivada"),
    (re.compile(r"sem canal v[aá]lido|sem whatsapp|n[aã]o tem whatsapp", re.I), "Sem canal válido"),
]


def _infer_etapa_negativa(*textos: str) -> str | None:
    """Heurística de apoio: se a Etapa não resolver para um valor terminal
    conhecido, tenta inferir uma etapa terminal negativa a partir de texto
    livre (Status/Próximo passo/Observação). A cor da linha na planilha NÃO
    é usada como fonte de verdade — a regra precisa estar em texto."""
    joined = " ".join(t for t in textos if t).lower()
    for pattern, etapa in _ETAPA_NEGATIVA_PATTERNS:
        if pattern.search(joined):
            return etapa
    return None


def _normalize_etapa(etapa: str, *contexto_texto: str) -> str:
    """Normaliza variantes de etapa para o valor canônico do painel. Se não
    resolver para um valor terminal conhecido, tenta inferir uma etapa
    terminal negativa a partir de texto de apoio (contexto_texto)."""
    canon = _ETAPA_ALIAS.get(etapa.strip().lower(), etapa.strip())
    if canon not in CRM_ETAPAS_TERMINAIS:
        inferida = _infer_etapa_negativa(etapa, *contexto_texto)
        if inferida:
            return inferida
    return canon


def _is_etapa_terminal(etapa: str) -> bool:
    return etapa in CRM_ETAPAS_TERMINAIS


def _norm(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain.lower().strip())


def _key(name: str) -> str:
    """Chave de matching: normaliza, remove ruído, colapsa repetições."""
    n = _norm(name)
    for noise in ("clinica", "clinicas", "consultorio", "consultorios",
                  "hospital", "unidade", "centro", "medico", "saude",
                  "dr", "dra", "ltda", "me", "eireli", "s/a", "sa"):
        n = re.sub(rf"\b{noise}\b", "", n)
    n = re.sub(r"[^a-z0-9 ]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    n = re.sub(r"(.)\1{2,}", r"\1", n)
    return n


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


def _norm_phone(raw: str) -> str | None:
    """Normaliza telefone para formato internacional sem +."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) in (10, 11):
        return "55" + digits
    if len(digits) in (12, 13) and digits.startswith("55"):
        return digits
    return None


def _wa_url(phone_normalized: str, nome_clinica: str) -> str:
    msg = MSG_B2B.format(nome_clinica=nome_clinica)
    return f"https://wa.me/{phone_normalized}?text={quote(msg)}"


def _followup_status(d: date | None, today: date) -> str:
    if d is None:
        return "sem_data"
    if d < today:
        return "atrasado"
    if d == today:
        return "hoje"
    if d <= today + timedelta(days=7):
        return "em_breve"
    return "futuro"


# ── Infra Sheets ───────────────────────────────────────────────────────────────

def _load_libs():
    try:
        from googleapiclient.discovery import build
        from google.auth import default as auth_default
        return build, auth_default
    except ImportError as exc:
        print(f"ERRO: dependências não instaladas — {exc}")
        sys.exit(1)


def _load_config():
    if not _CONFIG_PATH.exists():
        print(f"ERRO: config não encontrada em {_CONFIG_PATH}")
        sys.exit(1)
    cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    sid = cfg.get("spreadsheet_id", "").strip()
    if not sid:
        print("ERRO: spreadsheet_id ausente na configuração.")
        sys.exit(1)
    pcmso = cfg.get("pcmso_sheet_name", DEFAULT_PCMSO_SHEET).strip() or DEFAULT_PCMSO_SHEET
    crm   = cfg.get("crm_clinicas_sheet_name", DEFAULT_CRM_SHEET).strip() or DEFAULT_CRM_SHEET
    return sid, pcmso, crm


def _fetch(service, spreadsheet_id: str, sheet: str) -> list[list[str]]:
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"'{sheet}'!A:Z"
        ).execute()
        rows = result.get("values", [])
        return [[str(c) for c in row] for row in rows]
    except Exception as exc:
        print(f"ERRO ao ler '{sheet}': {exc}")
        sys.exit(1)


def _cell(row: list[str], idx: int) -> str:
    return row[idx].strip() if idx < len(row) else ""


# ── Parse das abas ────────────────────────────────────────────────────────────

def parse_crm(rows: list[list[str]]) -> dict[str, dict]:
    """Retorna dict  chave_nome → {nome_clinica, etapa, proxima_acao, data_proxima_acao, prioridade}."""
    if not rows:
        return {}
    headers = [_norm(h) for h in rows[0]]

    def idx(name: str) -> int:
        try:
            return headers.index(name)
        except ValueError:
            return -1

    nome_idx  = idx("nome_clinica")
    etapa_idx = idx("etapa")
    acao_idx  = idx("proxima_acao")
    data_idx  = idx("data_proxima_acao")
    prio_idx  = idx("prioridade")

    if nome_idx < 0:
        print("AVISO: coluna nome_clinica não encontrada no CRM.")
        return {}

    result = {}
    for row in rows[1:]:
        nome = _cell(row, nome_idx)
        if not nome:
            continue
        k = _key(nome)
        if not k:
            continue
        result[k] = {
            "nome_clinica":      nome,
            "etapa":             _cell(row, etapa_idx) if etapa_idx >= 0 else "",
            "proxima_acao":      _cell(row, acao_idx)  if acao_idx  >= 0 else "",
            "data_proxima_acao": _cell(row, data_idx)  if data_idx  >= 0 else "",
            "prioridade":        _cell(row, prio_idx)  if prio_idx  >= 0 else "",
        }
    return result


def parse_pcmso_phones(rows: list[list[str]]) -> dict[str, str]:
    """Retorna dict  chave_nome → telefone_normalizado. Nunca imprime valores."""
    if not rows:
        return {}
    headers = [_norm(h) for h in rows[0]]

    def idx(substrings: tuple) -> int:
        for i, h in enumerate(headers):
            if any(s in h for s in substrings):
                return i
        return -1

    nome_idx  = idx(("nome", "empresa", "clinica"))
    phone_idx = idx(("telefone", "whats", "celular", "fone"))

    if nome_idx < 0:
        print("AVISO: coluna de nome não encontrada no PCMSO.")
        return {}
    if phone_idx < 0:
        print("AVISO: coluna de telefone/WhatsApp não encontrada no PCMSO.")
        return {}

    result = {}
    total = 0
    sem_phone = 0
    invalido = 0

    for row in rows[1:]:
        nome = _cell(row, nome_idx)
        if not nome:
            continue
        total += 1
        k = _key(nome)
        if not k:
            continue
        phone_raw = _cell(row, phone_idx)
        if not phone_raw:
            sem_phone += 1
            continue
        phone_norm = _norm_phone(phone_raw)
        if phone_norm:
            result[k] = phone_norm
        else:
            invalido += 1

    print(f"  PCMSO: {total} linhas, {len(result)} com telefone válido, "
          f"{sem_phone} sem telefone, {invalido} com formato inválido.")
    return result


# ── Geração dos registros de follow-up ────────────────────────────────────────

def build_records(
    crm: dict[str, dict],
    phones: dict[str, str],
    today: date,
    dry_run: bool,
) -> list[dict]:
    records = []
    sem_match = 0

    for k, info in crm.items():
        phone = phones.get(k)
        if phone is None:
            # Tenta substring matching como fallback
            for pk, pv in phones.items():
                if (k in pk or pk in k) and max(len(k), len(pk)) > 3:
                    phone = pv
                    break

        data_raw = info["data_proxima_acao"]
        data_dt  = _parse_date(data_raw)
        status   = _followup_status(data_dt, today)
        etapa    = _normalize_etapa(info["etapa"], info["proxima_acao"])

        rec: dict = {
            "nome_clinica":      info["nome_clinica"],
            "etapa":             etapa,
            "etapa_terminal":    _is_etapa_terminal(etapa),
            "prioridade":        info["prioridade"],
            "proxima_acao":      info["proxima_acao"],
            "data_proxima_acao": data_dt.isoformat() if data_dt else "",
            "status_followup":   status,
            "tem_whatsapp":      phone is not None,
        }

        if not dry_run and phone:
            rec["telefone_whatsapp"] = phone
            rec["whatsapp_url"]      = _wa_url(phone, info["nome_clinica"])
        elif dry_run:
            # Não inclui dados de contato no dry-run
            pass

        if phone is None:
            sem_match += 1

        records.append(rec)

    if sem_match:
        print(f"  {sem_match} clínicas sem telefone correspondente no PCMSO.")

    return records


def _summarize(records: list[dict]) -> dict:
    """Atrasados/hoje/proximos7dias/futuro/semData contam apenas prospecção
    ativa (etapa_terminal=False) — Parceiro ativo e etapas terminais
    negativas não entram como "atraso de prospecção comum"."""
    ativos = [r for r in records if not r["etapa_terminal"]]
    return {
        "total":              len(records),
        "atrasados":          sum(1 for r in ativos if r["status_followup"] == "atrasado"),
        "hoje":               sum(1 for r in ativos if r["status_followup"] == "hoje"),
        "proximos7dias":      sum(1 for r in ativos if r["status_followup"] == "em_breve"),
        "futuro":             sum(1 for r in ativos if r["status_followup"] == "futuro"),
        "semData":            sum(1 for r in ativos if r["status_followup"] == "sem_data"),
        "comWhatsApp":        sum(1 for r in records if r["tem_whatsapp"]),
        "parceirosAtivos":    sum(1 for r in records if r["etapa"] == CRM_ETAPA_TERMINAL_POSITIVA),
        "perdidasArquivadas": sum(1 for r in records if r["etapa"] in CRM_ETAPAS_TERMINAIS_NEGATIVAS),
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def run(dry_run: bool) -> int:
    today = date.today()
    print(f"Hoje: {today.isoformat()}  |  modo: {'dry-run' if dry_run else 'write'}")
    print()

    build, auth_default = _load_libs()
    sid, pcmso_sheet, crm_sheet = _load_config()

    creds, _ = auth_default(scopes=[SHEETS_SCOPE])
    service  = build("sheets", "v4", credentials=creds, cache_discovery=False)

    print(f"Lendo CRM: '{crm_sheet}'")
    crm_rows = _fetch(service, sid, crm_sheet)
    crm = parse_crm(crm_rows)
    print(f"  CRM Clinicas: {len(crm)} registros")

    print(f"Lendo PCMSO: '{pcmso_sheet}'")
    pcmso_rows = _fetch(service, sid, pcmso_sheet)
    phones = parse_pcmso_phones(pcmso_rows)

    print()
    records = build_records(crm, phones, today, dry_run)
    summary = _summarize(records)

    print("Resumo follow-up B2B:")
    print(f"  total:          {summary['total']}")
    print(f"  atrasados:      {summary['atrasados']}")
    print(f"  hoje:           {summary['hoje']}")
    print(f"  próximos 7d:    {summary['proximos7dias']}")
    print(f"  futuro:         {summary['futuro']}")
    print(f"  sem data:       {summary['semData']}")
    print(f"  com WhatsApp:   {summary['comWhatsApp']}")
    print()

    if dry_run:
        print("Dry-run concluído. Nenhum arquivo gerado.")
        print("Use --write para gerar os arquivos locais.")
        return 0

    # Arquivo privado (com telefone/URL)
    PRIVATE_OUT.parent.mkdir(parents=True, exist_ok=True)
    private_data = {
        "geradoEm":              datetime.now(timezone.utc).isoformat(),
        "safeToDisplay":         False,
        "containsPersonalData":  False,
        "containsCommercialData": True,
        "fonte":                 crm_sheet,
        "clinicas":              records,
    }
    PRIVATE_OUT.write_text(
        json.dumps(private_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    os.chmod(PRIVATE_OUT, 0o600)
    print(f"Arquivo privado gerado: {PRIVATE_OUT}  (chmod 600)")

    # Resumo público (apenas contagens)
    SUMMARY_OUT.parent.mkdir(parents=True, exist_ok=True)
    summary_data = {
        "geradoEm":             datetime.now(timezone.utc).isoformat(),
        "safeToDisplay":        True,
        "containsPersonalData": False,
        "clinicas":             summary,
    }
    SUMMARY_OUT.write_text(
        json.dumps(summary_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"Resumo seguro gerado:   {SUMMARY_OUT}")
    print()
    print("Concluído. Arquivos são gitignored e não serão commitados.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera follow-up B2B de clínicas com WhatsApp assistido."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true",
                       help="Mostra apenas contagens sem gerar arquivos.")
    group.add_argument("--write", action="store_true",
                       help="Gera os arquivos privado e de resumo locais.")
    args = parser.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
