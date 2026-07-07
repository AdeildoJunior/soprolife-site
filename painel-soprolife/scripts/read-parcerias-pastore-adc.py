#!/usr/bin/env python3
"""
SoproLife OS Local Core — Conector Parceria Pastore (Google Sheets ADC).

Lê as três abas da parceria Pastore e gera dois arquivos locais, no mesmo
padrão de read-crm-contatos-b2b-adc.py:

  1. painel-soprolife/data-private/parcerias-pastore.local.json  ← completo (gitignored)
     Pode conter: paciente_nome, paciente_whatsapp e todos os demais campos.
     safeToDisplay: false | containsPersonalData: true

  2. painel-soprolife/data/parcerias-pastore-summary.local.json  ← resumo seguro (gitignored)
     Contém apenas agregados: contagens, somas, percentuais e séries por
     data/período — nunca nome, telefone, WhatsApp, CPF, e-mail ou observação.
     safeToDisplay: true | containsPersonalData: false

Abas lidas (nomes configuráveis, ver _load_config):
  - "Parceria Pastore - Atendimentos"
  - "Parceria Pastore - Custos"
  - "Parceria Pastore - Config"

Ver painel-soprolife/docs/parceria-pastore-planilha.md para o modelo completo
de campos, privacidade por campo e as fórmulas abaixo.

Configuração necessária:
    ~/.config/soprolife/painel/google-sheets.local.json
    Campo obrigatório: "spreadsheet_id"
    Campos opcionais:
      "parceria_pastore_atendimentos_sheet_name" (padrão: "Parceria Pastore - Atendimentos")
      "parceria_pastore_custos_sheet_name"       (padrão: "Parceria Pastore - Custos")
      "parceria_pastore_config_sheet_name"       (padrão: "Parceria Pastore - Config")

Pré-requisito:
    pip install -r painel-soprolife/requirements-google.txt
    gcloud auth application-default login

Uso:
    python3 painel-soprolife/scripts/read-parcerias-pastore-adc.py --show-structure
    python3 painel-soprolife/scripts/read-parcerias-pastore-adc.py --dry-run
    python3 painel-soprolife/scripts/read-parcerias-pastore-adc.py --write
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

# Guarda de PII compartilhada (M2) — mesma pasta deste script.
# A validação local _validate_summary abaixo permanece como redundância.
import pii_guard

# Regras da guarda para o parcerias-pastore-summary:
# - institucionais: nome/unidade/chips ("Parceria Pastore", "Pastore
#   Ipanema") são nomes de parceria/unidade, não de pessoa; dia_semana/
#   horario/servico/status_label são rótulos gerados;
# - observacao (em financeiro_parametros) é EXCEÇÃO: frase-template gerada
#   por este script (nunca texto digitado) — a chave não bloqueia, mas o
#   valor continua passando por todos os scans.
_PII_RULES = {
    "campos_pessoa": [],
    "campos_institucionais": ["nome", "unidade", "chips", "dia_semana",
                              "horario", "servico", "status_label"],
    "chaves_permitidas_excecao": ["observacao"],
}

_CONFIG_PATH = Path("~/.config/soprolife/painel/google-sheets.local.json").expanduser()
_OUT_PRIVATE = Path("painel-soprolife/data-private/parcerias-pastore.local.json")
_OUT_SUMMARY = Path("painel-soprolife/data/parcerias-pastore-summary.local.json")

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

DEFAULT_SHEET_ATENDIMENTOS = "Parceria Pastore - Atendimentos"
DEFAULT_SHEET_CUSTOS = "Parceria Pastore - Custos"
DEFAULT_SHEET_CONFIG = "Parceria Pastore - Config"

# ---------------------------------------------------------------------------
# Colunas canônicas por aba (ver painel-soprolife/templates/parceria-pastore-*.csv
# e painel-soprolife/docs/parceria-pastore-planilha.md)
# ---------------------------------------------------------------------------

CANONICAL_ATENDIMENTOS = [
    "data_atendimento", "unidade", "dia_semana", "horario_inicio", "horario_fim",
    "origem", "paciente_nome", "paciente_whatsapp", "tipo_exame", "broncodilatador",
    "valor_cobrado", "forma_pagamento", "recebido_por", "repasse_pastore",
    "custo_insumo", "custo_deslocamento", "custo_profissional", "outros_custos",
    "receita_bruta", "custo_total", "resultado_liquido", "status", "followup_status",
    "consentimento_contato_futuro", "observacao_privada_minima",
]

# Campos que nunca podem sair do arquivo privado
PRIVATE_ONLY_ATENDIMENTOS = {
    "paciente_nome", "paciente_whatsapp", "forma_pagamento",
    "consentimento_contato_futuro", "observacao_privada_minima",
}

CANONICAL_CUSTOS = [
    "data", "unidade", "tipo_custo", "descricao", "valor", "pago_por",
    "recorrente", "observacao_privada_minima",
]

PRIVATE_ONLY_CUSTOS = {"observacao_privada_minima"}

CANONICAL_CONFIG = [
    "unidade", "status", "dia_semana", "horario_inicio", "horario_fim",
    "capacidade_estimada_por_turno", "valor_exame_sem_bd", "valor_exame_com_bd",
    "repasse_pastore_tipo", "repasse_pastore_valor", "custo_insumo_padrao",
    "custo_deslocamento_padrao", "custo_profissional_padrao", "observacao",
]

BLOCKED_FIELDS = {
    "cpf", "rg", "data_nascimento", "endereco", "endereço",
    "senha", "token", "access_token", "private_key", "client_secret",
    "chave_pix", "numero_cartao", "cvv",
}

# Status que contam como exame realizado (ver instrução do usuário: "Agendado"
# nunca conta como realizado).
STATUS_REALIZADO = {"realizado", "laudo enviado"}

# Mapeia tipo_custo (aba Custos) para o mesmo bucket usado em financeiro_parametros,
# para não precisar de campo novo no summary/app.js.
CUSTO_BUCKET_MAP = {
    "insumo": "custo_insumos",
    "deslocamento": "custo_deslocamento",
    "profissional": "custo_profissional",
}

# Plural curto para montar o chip de dias da semana (ex.: "Terças e sábados")
DIA_SEMANA_PLURAL = {
    "segunda-feira": "segundas", "segunda": "segundas",
    "terca-feira": "terças", "terça-feira": "terças", "terca": "terças", "terça": "terças",
    "quarta-feira": "quartas", "quarta": "quartas",
    "quinta-feira": "quintas", "quinta": "quintas",
    "sexta-feira": "sextas", "sexta": "sextas",
    "sabado": "sábados", "sábado": "sábados",
    "domingo": "domingos",
}

_CPF_RE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_FONE_RE = re.compile(r"\(?\d{2}\)?\s?\d{4,5}-?\d{4}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm(s: str) -> str:
    return _strip_accents(str(s or "")).strip().lower()


def _parse_number(raw: str) -> Optional[float]:
    """Mesma lógica de read-sheets-summary-adc.py, mas retorna None em vez de
    lançar exceção — muitos campos financeiros por linha são opcionais."""
    if raw is None:
        return None
    cleaned = str(raw).strip().replace("R$", "").replace("\xa0", "").replace(" ", "")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date_br(raw: str) -> Optional[date]:
    raw = str(raw or "").strip()
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_phone(raw: str) -> str:
    return re.sub(r"\D", "", str(raw or ""))


def _fmt_hora(raw: str) -> str:
    raw = str(raw or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{1,2}", raw):
        return f"{int(raw):02d}h"
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if m:
        h, mi = m.groups()
        return f"{int(h):02d}h" if mi == "00" else f"{int(h):02d}h{mi}"
    return raw


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _load_config() -> tuple[str, str, str, str]:
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

    atend = cfg.get("parceria_pastore_atendimentos_sheet_name", DEFAULT_SHEET_ATENDIMENTOS).strip() or DEFAULT_SHEET_ATENDIMENTOS
    custos = cfg.get("parceria_pastore_custos_sheet_name", DEFAULT_SHEET_CUSTOS).strip() or DEFAULT_SHEET_CUSTOS
    config = cfg.get("parceria_pastore_config_sheet_name", DEFAULT_SHEET_CONFIG).strip() or DEFAULT_SHEET_CONFIG
    return sid, atend, custos, config


def _fetch_rows(build, spreadsheet_id: str, sheet_name: str, credentials):
    print(f"Lendo aba: {sheet_name}")

    try:
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A:Z")
            .execute()
        )
    except Exception as exc:
        msg = str(exc)
        if "403" in msg or "PERMISSION_DENIED" in msg:
            print(f"ERRO: acesso negado à aba '{sheet_name}'.")
            sys.exit(1)
        elif "404" in msg or "NOT_FOUND" in msg:
            print(f"AVISO: aba '{sheet_name}' não encontrada — tratando como vazia.")
            return []
        elif "401" in msg or "UNAUTHENTICATED" in msg:
            print("ERRO: credenciais inválidas ou expiradas.")
            print("  Execute: gcloud auth application-default login")
            sys.exit(1)
        else:
            print(f"ERRO: falha ao ler a aba '{sheet_name}' — {exc}")
            sys.exit(1)

    rows = result.get("values", [])
    print(f"  rows_read: {len(rows)}")
    return rows


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


def _show_structure(rows: list, sheet_name: str, canonical: list, private_only: set) -> None:
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
        hl = _norm(h).replace(" ", "_")
        if hl in BLOCKED_FIELDS:
            print(f"  BLOQUEADA (nunca exportada):  {h}")
        elif hl in private_only:
            print(f"  PRIVADA (só em data-private): {h}")
        elif hl in canonical:
            print(f"  OK (pode virar summary):      {h}")
        else:
            print(f"  IGNORADA (não reconhecida):   {h}")
    print()


def _parse_generic_records(rows: list, canonical: list, sheet_label: str) -> list[dict]:
    """Lê uma aba genérica pelo cabeçalho (não por posição fixa), igual ao
    padrão de read-crm-contatos-b2b-adc.py. Bloqueia colunas proibidas."""
    if not rows:
        return []

    raw_headers = [str(c).strip() for c in rows[0]]
    headers_norm = [_norm(h).replace(" ", "_") for h in raw_headers]

    col_map: dict[str, int] = {}
    for i, hn in enumerate(headers_norm):
        if hn in BLOCKED_FIELDS:
            print(f"ERRO: coluna bloqueada '{raw_headers[i]}' encontrada na aba {sheet_label}.")
            sys.exit(1)
        if hn in canonical and hn not in col_map:
            col_map[hn] = i

    if not col_map:
        print(f"AVISO: nenhuma coluna reconhecida no cabeçalho da aba {sheet_label} — tratando como vazia.")
        return []

    records = []
    for row_num, row in enumerate(rows[1:], start=2):
        rec: dict[str, str] = {}
        for canon, idx in col_map.items():
            rec[canon] = str(row[idx]).strip() if idx < len(row) else ""

        if not any(rec.values()):
            continue

        full_text = " ".join(rec.values())
        if _CPF_RE.search(full_text):
            print(f"ERRO: padrão de CPF detectado na aba {sheet_label}, linha {row_num}.")
            sys.exit(1)

        records.append(rec)

    return records


# ---------------------------------------------------------------------------
# Cálculos por linha (aba Atendimentos)
# ---------------------------------------------------------------------------

def _compute_row_financials(rec: dict) -> dict:
    """Aplica as regras de cálculo pedidas:
    - receita_bruta: campo receita_bruta > valor_cobrado > 0 (só para cálculo)
    - custo_total: campo custo_total > soma dos custos + repasse quando numérico
    - resultado_liquido: campo resultado_liquido > receita_bruta - custo_total
    """
    receita_bruta = _parse_number(rec.get("receita_bruta"))
    if receita_bruta is None:
        receita_bruta = _parse_number(rec.get("valor_cobrado"))
    if receita_bruta is None:
        receita_bruta = 0.0

    custo_total = _parse_number(rec.get("custo_total"))
    if custo_total is None:
        partes = [
            _parse_number(rec.get("custo_insumo")),
            _parse_number(rec.get("custo_deslocamento")),
            _parse_number(rec.get("custo_profissional")),
            _parse_number(rec.get("outros_custos")),
            _parse_number(rec.get("repasse_pastore")),
        ]
        custo_total = sum(p for p in partes if p is not None)

    resultado_liquido = _parse_number(rec.get("resultado_liquido"))
    if resultado_liquido is None:
        resultado_liquido = receita_bruta - custo_total

    return {
        "receita_bruta": receita_bruta,
        "custo_total": custo_total,
        "resultado_liquido": resultado_liquido,
        "custo_insumo": _parse_number(rec.get("custo_insumo")) or 0.0,
        "custo_deslocamento": _parse_number(rec.get("custo_deslocamento")) or 0.0,
        "custo_profissional": _parse_number(rec.get("custo_profissional")) or 0.0,
        "outros_custos": _parse_number(rec.get("outros_custos")) or 0.0,
    }


def _dedupe_key(rec: dict) -> Optional[str]:
    phone = _normalize_phone(rec.get("paciente_whatsapp", ""))
    if phone:
        return f"tel:{phone}"
    nome = _norm(rec.get("paciente_nome", ""))
    return f"nome:{nome}" if nome else None


# ---------------------------------------------------------------------------
# Agregação (summary seguro)
# ---------------------------------------------------------------------------

def _build_agenda(config_records: list[dict]) -> list[dict]:
    if not config_records:
        # Agenda conhecida hoje (ver docs/parceria-pastore-planilha.md) — nunca
        # deixar a seção sem agenda só porque a aba Config ainda está vazia.
        print("AVISO: aba Config vazia — usando agenda padrão conhecida (Terça/Sábado, 08h às 12h).")
        return [
            {"unidade": "Pastore Ipanema", "dia_semana": "Terça-feira", "horario": "08h às 12h",
             "status": "planejada", "capacidade_estimada_por_turno": None},
            {"unidade": "Pastore Ipanema", "dia_semana": "Sábado", "horario": "08h às 12h",
             "status": "planejada", "capacidade_estimada_por_turno": None},
        ]

    agenda = []
    for rec in config_records:
        horario_inicio = _fmt_hora(rec.get("horario_inicio", ""))
        horario_fim = _fmt_hora(rec.get("horario_fim", ""))
        horario = f"{horario_inicio} às {horario_fim}" if horario_inicio and horario_fim else (horario_inicio or horario_fim or "A definir")
        agenda.append({
            "unidade": rec.get("unidade") or "Pastore Ipanema",
            "dia_semana": rec.get("dia_semana") or "A definir",
            "horario": horario,
            "status": _norm(rec.get("status")) or "planejada",
            "capacidade_estimada_por_turno": _parse_number(rec.get("capacidade_estimada_por_turno")),
        })
    return agenda


def _build_chips(agenda: list[dict]) -> list[str]:
    unidades = {r.get("unidade") for r in agenda if r.get("unidade")}
    unidade_chip = " + ".join(sorted(unidades)) if unidades else "Pastore Ipanema"

    dias_plural = []
    for r in agenda:
        chave = _norm(r.get("dia_semana", ""))
        plural = DIA_SEMANA_PLURAL.get(chave)
        if plural and plural not in dias_plural:
            dias_plural.append(plural)
    dias_chip = " e ".join(dias_plural).capitalize() if dias_plural else "Agenda a definir"

    horarios = {r.get("horario") for r in agenda if r.get("horario")}
    horario_chip = next(iter(horarios)) if len(horarios) == 1 else "Horários variados"

    return [unidade_chip, dias_chip, horario_chip]


def _build_outputs(atendimentos: list[dict], custos: list[dict], config_records: list[dict], now_iso: str) -> tuple[dict, dict]:
    # --- privado -------------------------------------------------------
    payload_private = {
        "source": {
            "type": "google_sheets_adc",
            "safeToDisplay": False,
            "containsPersonalData": True,
            "containsHealthData": False,
            "generatedAt": now_iso,
        },
        "atendimentos": atendimentos,
        "custos": custos,
        "config": config_records,
    }

    # --- agregação -------------------------------------------------------
    realizados = [r for r in atendimentos if _norm(r.get("status")) in STATUS_REALIZADO]
    exames_realizados = len(realizados)

    financials = [_compute_row_financials(r) for r in realizados]

    receita_total = sum(f["receita_bruta"] for f in financials) if exames_realizados > 0 else None
    custo_atendimentos_total = sum(f["custo_total"] for f in financials)

    custo_avulso_total = 0.0
    bucket_totais = {"custo_insumos": 0.0, "custo_deslocamento": 0.0, "custo_profissional": 0.0, "outros_custos": 0.0}
    for f in financials:
        bucket_totais["custo_insumos"] += f["custo_insumo"]
        bucket_totais["custo_deslocamento"] += f["custo_deslocamento"]
        bucket_totais["custo_profissional"] += f["custo_profissional"]
        bucket_totais["outros_custos"] += f["outros_custos"]

    for c in custos:
        valor = _parse_number(c.get("valor")) or 0.0
        custo_avulso_total += valor
        bucket = CUSTO_BUCKET_MAP.get(_norm(c.get("tipo_custo")), "outros_custos")
        bucket_totais[bucket] += valor

    custo_total_geral = custo_atendimentos_total + custo_avulso_total

    resultado_liquido_total = (receita_total - custo_total_geral) if receita_total is not None else None
    margem_estimada_pct = (
        round(resultado_liquido_total / receita_total * 100, 1)
        if (receita_total is not None and receita_total > 0)
        else None
    )

    # produção por data (apenas realizados)
    por_data: dict[str, int] = defaultdict(int)
    for r in realizados:
        d = _parse_date_br(r.get("data_atendimento"))
        if d:
            por_data[d.strftime("%d/%m")] += 1
    datas_ordenadas = sorted(por_data.keys(), key=lambda s: datetime.strptime(s, "%d/%m"))
    producao_por_data = {"labels": datas_ordenadas, "exames": [por_data[d] for d in datas_ordenadas]}

    # financeiro por período (mês) — realizados (receita/custo de atendimento) + custos avulsos
    receita_mes: dict[str, float] = defaultdict(float)
    custo_mes: dict[str, float] = defaultdict(float)
    for r, f in zip(realizados, financials):
        d = _parse_date_br(r.get("data_atendimento"))
        if d:
            chave = d.strftime("%m/%Y")
            receita_mes[chave] += f["receita_bruta"]
            custo_mes[chave] += f["custo_total"]
    for c in custos:
        d = _parse_date_br(c.get("data"))
        if d:
            chave = d.strftime("%m/%Y")
            custo_mes[chave] += _parse_number(c.get("valor")) or 0.0

    meses = sorted(set(receita_mes) | set(custo_mes), key=lambda s: datetime.strptime(s, "%m/%Y"))
    financeiro_por_periodo = {
        "labels": meses,
        "receita": [round(receita_mes.get(m, 0.0), 2) for m in meses],
        "custos": [round(custo_mes.get(m, 0.0), 2) for m in meses],
        "resultado": [round(receita_mes.get(m, 0.0) - custo_mes.get(m, 0.0), 2) for m in meses],
    }

    # agenda / config
    agenda = _build_agenda(config_records)
    chips = _build_chips(agenda)

    capacidades = [a["capacidade_estimada_por_turno"] for a in agenda if a["capacidade_estimada_por_turno"]]
    if capacidades:
        capacidade_media = sum(capacidades) / len(capacidades)
        datas_ocorridas = {_parse_date_br(r.get("data_atendimento")) for r in atendimentos if _parse_date_br(r.get("data_atendimento"))}
        turnos_ocorridos = len(datas_ocorridas)
        if turnos_ocorridos == 0:
            ocupacao_agenda_pct = 0
        else:
            capacidade_total = capacidade_media * turnos_ocorridos
            ocupacao_agenda_pct = round(exames_realizados / capacidade_total * 100, 1) if capacidade_total > 0 else 0
    else:
        ocupacao_agenda_pct = None

    # parâmetros comerciais de referência (aba Config) — só repasse percentual
    # vira campo numérico aqui; repasse fixo fica só no privado (ver docstring
    # do módulo e docs/parceria-pastore-planilha.md, pergunta em aberto nº 1/2).
    valor_sem_bd = valor_com_bd = repasse_pct = None
    for rec in config_records:
        if valor_sem_bd is None:
            valor_sem_bd = _parse_number(rec.get("valor_exame_sem_bd"))
        if valor_com_bd is None:
            valor_com_bd = _parse_number(rec.get("valor_exame_com_bd"))
        if repasse_pct is None and _norm(rec.get("repasse_pastore_tipo")) == "percentual":
            repasse_pct = _parse_number(rec.get("repasse_pastore_valor"))

    if exames_realizados == 0 and custo_total_geral == 0:
        observacao = "Ainda sem produção ou custos registrados para esta parceria."
    elif exames_realizados == 0:
        observacao = "Custos operacionais já registrados; aguardando início da produção para apurar receita."
    else:
        observacao = "Valores calculados a partir dos atendimentos e custos registrados na planilha."

    # pacientes (dedupe por telefone normalizado; senão por nome normalizado)
    pacientes_count: dict[str, int] = defaultdict(int)
    for r in realizados:
        key = _dedupe_key(r)
        if key:
            pacientes_count[key] += 1

    total_atendidos = len(pacientes_count)
    recorrentes = sum(1 for n in pacientes_count.values() if n > 1)
    followup_pendente = sum(1 for r in atendimentos if _norm(r.get("followup_status")) == "pendente")

    dist_sem_bd = sum(1 for r in realizados if _norm(r.get("broncodilatador")) in ("nao", "não", ""))
    dist_com_bd = sum(1 for r in realizados if _norm(r.get("broncodilatador")) in ("sim",))

    status_geral = "ativa" if exames_realizados > 0 else "planejada"
    status_label = "Parceria em produção" if exames_realizados > 0 else "Estrutura inicial da parceria"

    payload_summary = {
        "source": {
            "type": "parcerias_pastore_summary",
            "safeToDisplay": True,
            "containsPersonalData": False,
            "generatedAt": now_iso,
        },
        "parceria": {
            "nome": "Parceria Pastore",
            "unidade": chips[0],
            "servico": "Espirometria com ou sem broncodilatador",
            "status": status_geral,
            "status_label": status_label,
            "chips": chips,
        },
        "kpis": {
            "exames_realizados": exames_realizados,
            "receita_estimada": round(receita_total, 2) if receita_total is not None else None,
            "resultado_liquido_estimado": round(resultado_liquido_total, 2) if resultado_liquido_total is not None else None,
            "ocupacao_agenda_pct": ocupacao_agenda_pct,
        },
        "producao_por_data": producao_por_data,
        "financeiro_por_periodo": financeiro_por_periodo,
        "agenda": agenda,
        "financeiro_parametros": {
            "valor_exame_sem_broncodilatador": valor_sem_bd,
            "valor_exame_com_broncodilatador": valor_com_bd,
            "repasse_percentual_pastore": repasse_pct,
            "custo_deslocamento": round(bucket_totais["custo_deslocamento"], 2),
            "custo_insumos": round(bucket_totais["custo_insumos"], 2),
            "custo_profissional": round(bucket_totais["custo_profissional"], 2),
            "outros_custos": round(bucket_totais["outros_custos"], 2),
            "receita_bruta": round(receita_total, 2) if receita_total is not None else None,
            "custo_total": round(custo_total_geral, 2),
            "resultado_liquido": round(resultado_liquido_total, 2) if resultado_liquido_total is not None else None,
            "margem_estimada_pct": margem_estimada_pct,
            "observacao": observacao,
        },
        "pacientes_pastore": {
            "total_atendidos": total_atendidos,
            "followup_pendente": followup_pendente,
            "recorrentes": recorrentes,
            "distribuicao_tipo_exame": {
                "sem_broncodilatador": dist_sem_bd,
                "com_broncodilatador": dist_com_bd,
            },
        },
    }

    return payload_private, payload_summary


def _validate_summary(payload_summary: dict) -> None:
    """Rede de segurança final — mesmo com o resumo já construído só com
    campos agregados, confere que nenhum termo/padrão pessoal vazou."""
    text = json.dumps(payload_summary, ensure_ascii=False).lower()
    errors = 0

    FORBIDDEN_KEYS = [
        "paciente_nome", "paciente_whatsapp", "forma_pagamento",
        "observacao_privada_minima", "consentimento_contato_futuro",
        "nome_paciente", "telefone", "whatsapp", "e-mail", "email",
    ]
    for key in FORBIDDEN_KEYS:
        if key in text:
            print(f"ERRO interno: termo proibido '{key}' encontrado no resumo.")
            errors += 1

    if _CPF_RE.search(text):
        print("ERRO interno: padrão de CPF detectado no resumo.")
        errors += 1
    if _FONE_RE.search(text):
        print("ERRO interno: padrão de telefone detectado no resumo.")
        errors += 1
    if _EMAIL_RE.search(text):
        print("ERRO interno: padrão de e-mail detectado no resumo.")
        errors += 1

    if errors:
        print(f"ERRO: {errors} violação(ões) no resumo. Abortando gravação.")
        sys.exit(1)


def _write_files(payload_private: dict, payload_summary: dict) -> None:
    _OUT_PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PRIVATE.write_text(json.dumps(payload_private, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _OUT_PRIVATE.chmod(0o600)

    _OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    _OUT_SUMMARY.write_text(json.dumps(payload_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _OUT_SUMMARY.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Conector Parceria Pastore via Google Sheets ADC — SoproLife"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--show-structure", action="store_true",
                        help="Inspeciona cabeçalhos das 3 abas (diagnóstico, sem gravar)")
    group.add_argument("--dry-run", action="store_true",
                        help="Valida leitura e cálculos sem gravar (padrão)")
    group.add_argument("--write", action="store_true",
                        help="Valida e grava data-private/parcerias-pastore.local.json + "
                             "data/parcerias-pastore-summary.local.json")
    args = parser.parse_args()

    mode = "show-structure" if args.show_structure else ("write" if args.write else "dry-run")

    print("SoproLife OS Local Core — Parceria Pastore connector (ADC)")
    print(f"mode: {mode}")
    print("auth: Application Default Credentials")
    print()

    build, google_auth_default = _load_google_libs()
    spreadsheet_id, sheet_atend, sheet_custos, sheet_config = _load_config()
    credentials = _get_credentials(google_auth_default)

    print("config: carregada")
    print(f"sheets: {sheet_atend!r} | {sheet_custos!r} | {sheet_config!r}")
    print()

    rows_atend = _fetch_rows(build, spreadsheet_id, sheet_atend, credentials)
    rows_custos = _fetch_rows(build, spreadsheet_id, sheet_custos, credentials)
    rows_config = _fetch_rows(build, spreadsheet_id, sheet_config, credentials)

    if mode == "show-structure":
        _show_structure(rows_atend, sheet_atend, CANONICAL_ATENDIMENTOS, PRIVATE_ONLY_ATENDIMENTOS)
        _show_structure(rows_custos, sheet_custos, CANONICAL_CUSTOS, PRIVATE_ONLY_CUSTOS)
        _show_structure(rows_config, sheet_config, CANONICAL_CONFIG, set())
        return 0

    atendimentos = _parse_generic_records(rows_atend, CANONICAL_ATENDIMENTOS, sheet_atend)
    custos = _parse_generic_records(rows_custos, CANONICAL_CUSTOS, sheet_custos)
    config_records = _parse_generic_records(rows_config, CANONICAL_CONFIG, sheet_config)

    print(f"atendimentos: {len(atendimentos)} linha(s)")
    print(f"custos:       {len(custos)} linha(s)")
    print(f"config:       {len(config_records)} linha(s)")
    print()

    now_iso = _now_iso()
    payload_private, payload_summary = _build_outputs(atendimentos, custos, config_records, now_iso)

    print("Validando resumo seguro...")
    _validate_summary(payload_summary)
    # 2ª validação: guarda de PII compartilhada (M2) — aborta com exit 1 se
    # encontrar violação; nunca imprime o valor sensível.
    pii_guard.ensure_summary_safe(payload_summary, rules=_PII_RULES, context="parcerias-pastore-summary")
    print("Validação OK (local + pii_guard). Nenhum dado pessoal no resumo.")
    print()
    print(f"exames_realizados: {payload_summary['kpis']['exames_realizados']}")
    print(f"receita_estimada:  {payload_summary['kpis']['receita_estimada']}")
    print(f"resultado_liquido: {payload_summary['kpis']['resultado_liquido_estimado']}")
    print(f"ocupacao_agenda:   {payload_summary['kpis']['ocupacao_agenda_pct']}")

    if mode == "dry-run":
        print()
        print("next_step: use --write para gravar os dois arquivos locais.")
        return 0

    _write_files(payload_private, payload_summary)

    print()
    print(f"Privado gravado:  {_OUT_PRIVATE}  (chmod 600)")
    print(f"Resumo gravado:   {_OUT_SUMMARY}  (chmod 600)")
    print()
    print("Próximo passo: painel-soprolife/scripts/check-access.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
