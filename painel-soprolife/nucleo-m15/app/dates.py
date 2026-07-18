"""Normalização de datas incompletas com preservação de metadados.

Decisão do proprietário (M15):
  06/2026          -> 2026-06-01  (precisao=mes, dia_assumido=True)
  dezembro/2026    -> 2026-12-01  (precisao=mes, dia_assumido=True)
  2026-08          -> 2026-08-01  (precisao=mes, dia_assumido=True)
  2026             -> 2026-01-01  (precisao=ano, dia_assumido=True)
Sempre preservar: data_original, precisao_original, dia_assumido.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

MONTH_NAMES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

PRECISAO_DIA = "dia"
PRECISAO_MES = "mes"
PRECISAO_ANO = "ano"
PRECISAO_DESCONHECIDA = "desconhecida"


@dataclass(frozen=True)
class NormalizedDate:
    value: date | None
    original: str
    precision: str
    day_assumed: bool


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_incomplete_date(raw: str | None) -> NormalizedDate:
    original = (raw or "").strip()
    if not original:
        return NormalizedDate(None, original, PRECISAO_DESCONHECIDA, False)
    text = original.lower()

    # completa: DD/MM/AAAA
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        d = _valid(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if d:
            return NormalizedDate(d, original, PRECISAO_DIA, False)
    # completa ISO: AAAA-MM-DD
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        d = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            return NormalizedDate(d, original, PRECISAO_DIA, False)
    # mês/ano numérico: MM/AAAA
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", text)
    if m:
        d = _valid(int(m.group(2)), int(m.group(1)), 1)
        if d:
            return NormalizedDate(d, original, PRECISAO_MES, True)
    # ISO parcial: AAAA-MM
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
    if m:
        d = _valid(int(m.group(1)), int(m.group(2)), 1)
        if d:
            return NormalizedDate(d, original, PRECISAO_MES, True)
    # nome do mês: dezembro/2026, dez/2026, dezembro de 2026
    m = re.fullmatch(r"([a-zç]+)\s*(?:/|de\s+)\s*(\d{4})", text)
    if m and m.group(1) in MONTH_NAMES:
        d = _valid(int(m.group(2)), MONTH_NAMES[m.group(1)], 1)
        if d:
            return NormalizedDate(d, original, PRECISAO_MES, True)
    # só ano: AAAA
    m = re.fullmatch(r"(\d{4})", text)
    if m:
        d = _valid(int(m.group(1)), 1, 1)
        if d:
            return NormalizedDate(d, original, PRECISAO_ANO, True)

    return NormalizedDate(None, original, PRECISAO_DESCONHECIDA, False)


def add_months(base: date, months: int) -> date:
    """Soma meses de calendário; dia estoura para o último dia do mês quando preciso."""
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    # último dia do mês destino
    if month == 12:
        last_day = 31
    else:
        last_day = (date(year, month + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(base.day, last_day))
