#!/usr/bin/env python3
"""
SoproLife OS Local Core — Biblioteca de normalização canônica (M14.3).

Funções PURAS compartilhadas pelas ferramentas de auditoria/reconciliação e
pelos geradores: datas com precisão explícita, classificação de IDs, enums
canônicos com aliases legados e chave de deduplicação de paciente.

Princípios (ver core/contracts/*.json e docs/arquitetura-canonica-abas.md):
  - NUNCA inventar dado: data "06/2026" vira (2026-06-01, precisao="mes") —
    o dia 01 é apenas âncora de ordenação e só existe acompanhado da precisão;
  - normalização de enum é SUGESTÃO de auditoria — nenhuma ferramenta aplica
    a correção no dado real automaticamente;
  - IDs legados são válidos e preservados; formato de ID nunca decide se um
    registro é real ou teste;
  - nomes/telefones nunca saem em claro de relatórios commitáveis — usar
    hash_protegido().

Sem rede, sem leitura de planilha, sem dependência externa.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple, Optional

_CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "core" / "contracts"
ENUMS_PATH = _CONTRACTS_DIR / "enums-canonicos.json"
IDS_PATH = _CONTRACTS_DIR / "ids-canonicos.json"

# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------


def norm_texto(s) -> str:
    """minúsculas, sem acentos, espaços colapsados — regra única de comparação."""
    nfkd = unicodedata.normalize("NFKD", str(s or ""))
    plain = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain.lower().strip())


def norm_nome(nome) -> str:
    """Mesma regra de _normalizarNome (sync-crm-pacientes.gs): só [a-z0-9 ]."""
    base = norm_texto(nome)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", base)).strip()


def norm_telefone(tel) -> str:
    """Mesma regra de _normalizarTelefone: apenas dígitos."""
    return re.sub(r"\D", "", str(tel or ""))


# ---------------------------------------------------------------------------
# Datas com precisão
# ---------------------------------------------------------------------------

PRECISAO_DIA = "dia"
PRECISAO_MES = "mes"
PRECISAO_ANO = "ano"
PRECISAO_DESCONHECIDA = "desconhecida"


class DataFlex(NamedTuple):
    """Resultado do parse tolerante de data histórica.

    iso      — YYYY-MM-DD para ordenação (âncora dia 01 quando precisao=mes,
               01/01 quando precisao=ano) ou None;
    precisao — dia | mes | ano | desconhecida;
    valida   — True quando algo foi reconhecido (mesmo que incompleto).
    """

    iso: Optional[str]
    precisao: str
    valida: bool


_MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
_SHEETS_EPOCH = date(1899, 12, 30)


def _data_segura(ano: int, mes: int, dia: int) -> Optional[date]:
    try:
        d = date(ano, mes, dia)
    except ValueError:
        return None
    return d if 2000 <= ano <= 2100 else None


def parse_data_flex(raw) -> DataFlex:
    """Interpreta datas nos formatos históricos da planilha SEM inventar dia.

    Aceita: DD/MM/AAAA, AAAA-MM-DD, ISO com hora, DD-MM-AAAA, DD/MM/AA,
    MM/AAAA, AAAA/MM, "junho/2026", "junho 2026", AAAA sozinho e serial do
    Sheets. Qualquer coisa fora disso → precisao=desconhecida, valida=False.
    """
    s = str(raw or "").strip()
    if not s:
        return DataFlex(None, PRECISAO_DESCONHECIDA, False)

    # ISO com ou sem hora: 2026-07-02 / 2026-07-02T14:30:55(.mmm)(Z)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})([T\s].*)?$", s)
    if m:
        d = _data_segura(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return DataFlex(d.isoformat(), PRECISAO_DIA, True) if d else DataFlex(None, PRECISAO_DESCONHECIDA, False)

    # BR completo: DD/MM/AAAA ou DD-MM-AAAA (com hora opcional)
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})(\s.*)?$", s)
    if m:
        d = _data_segura(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return DataFlex(d.isoformat(), PRECISAO_DIA, True) if d else DataFlex(None, PRECISAO_DESCONHECIDA, False)

    # BR curto: DD/MM/AA → século 2000
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2})$", s)
    if m:
        d = _data_segura(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return DataFlex(d.isoformat(), PRECISAO_DIA, True) if d else DataFlex(None, PRECISAO_DESCONHECIDA, False)

    # Só mês/ano: MM/AAAA — o dia NÃO existe; âncora 01 só para ordenar.
    m = re.match(r"^(\d{1,2})/(\d{4})$", s)
    if m:
        d = _data_segura(int(m.group(2)), int(m.group(1)), 1)
        return DataFlex(d.isoformat(), PRECISAO_MES, True) if d else DataFlex(None, PRECISAO_DESCONHECIDA, False)

    # AAAA/MM
    m = re.match(r"^(\d{4})[/-](\d{1,2})$", s)
    if m:
        d = _data_segura(int(m.group(1)), int(m.group(2)), 1)
        return DataFlex(d.isoformat(), PRECISAO_MES, True) if d else DataFlex(None, PRECISAO_DESCONHECIDA, False)

    # Mês em português: "junho/2026", "junho 2026", "junho-2026"
    m = re.match(r"^([a-zçãéêíóôúüáàâ]+)[/\s-](\d{4})$", norm_texto(s))
    if m and m.group(1) in _MESES_PT:
        d = _data_segura(int(m.group(2)), _MESES_PT[m.group(1)], 1)
        return DataFlex(d.isoformat(), PRECISAO_MES, True) if d else DataFlex(None, PRECISAO_DESCONHECIDA, False)

    # Só o ano: AAAA
    m = re.match(r"^(\d{4})$", s)
    if m and 2000 <= int(m.group(1)) <= 2100:
        return DataFlex(f"{int(m.group(1)):04d}-01-01", PRECISAO_ANO, True)

    # Serial do Google Sheets (número de dias desde 30/12/1899)
    if re.match(r"^\d{5}$", s):
        serial = int(s)
        if 36526 <= serial <= 73415:  # 2000-01-01 .. 2100-12-31
            d = _SHEETS_EPOCH + timedelta(days=serial)
            return DataFlex(d.isoformat(), PRECISAO_DIA, True)

    return DataFlex(None, PRECISAO_DESCONHECIDA, False)


def formatar_data_br(iso: Optional[str], precisao: str = PRECISAO_DIA) -> str:
    """Exibição brasileira honesta com a precisão: mes → 'MM/AAAA', ano → 'AAAA'."""
    if not iso:
        return "—"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", iso)
    if not m:
        return "—"
    ano, mes, dia = m.group(1), m.group(2), m.group(3)
    if precisao == PRECISAO_MES:
        return f"{mes}/{ano}"
    if precisao == PRECISAO_ANO:
        return ano
    return f"{dia}/{mes}/{ano}"


# ---------------------------------------------------------------------------
# IDs
# ---------------------------------------------------------------------------

# Formato canônico M14.3A: prefixo de entidade + UUID opaco emitido pelo
# SERVIDOR (ctNovoIdServidor em contratos-canonicos.gs) — nunca lastRow,
# contador ou relógio do navegador.
_ID_CANONICO_RX = re.compile(
    r"^[A-Z]{3,4}-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Chave gerada no NAVEGADOR (ex.: ESP-20260709-143055-ABC123): aceita apenas
# como idempotency_key legada; o timestamp embutido é criação técnica do
# formulário, nunca a data do exame.
_ID_CHAVE_NAVEGADOR_RX = re.compile(r"^[A-Z]{3,4}-\d{8}-\d{6}-[A-Z0-9]{4,8}$")
# Sequencial do antigo _nextId do Apps Script: PREFIXO-NNNN.
_ID_SEQUENCIAL_RX = re.compile(r"^([A-Z]{3,4})-(\d{4})$")
# PAC-AAAAMMDD-NNN / LEAD-AAAAMMDD-NNN (sync/manual antigos).
_ID_DATA_SEQ_RX = re.compile(r"^([A-Z]{3,4})-(\d{8})-(\d{3})$")
# Padrão antigo ESM-...
_ID_ESM_RX = re.compile(r"^ESM-.+$")


class IdInfo(NamedTuple):
    """formato: canonico | chave_navegador | sequencial | data_seq |
    legado_esm | ausente | irregular. Todos os formatos legados são VÁLIDOS
    e preservados — a classificação existe para rastreabilidade, nunca para
    presumir teste ou recalcular ID."""

    formato: str
    prefixo: str
    canonico: bool


def classificar_id(raw) -> IdInfo:
    v = str(raw or "").strip()
    if not v:
        return IdInfo("ausente", "", False)
    if _ID_CANONICO_RX.match(v):
        return IdInfo("canonico", v.split("-", 1)[0], True)
    if _ID_CHAVE_NAVEGADOR_RX.match(v):
        return IdInfo("chave_navegador", v.split("-", 1)[0], False)
    if _ID_ESM_RX.match(v):
        return IdInfo("legado_esm", "ESM", False)
    m = _ID_SEQUENCIAL_RX.match(v)
    if m:
        return IdInfo("sequencial", m.group(1), False)
    m = _ID_DATA_SEQ_RX.match(v)
    if m:
        return IdInfo("data_seq", m.group(1), False)
    return IdInfo("irregular", "", False)


# ---------------------------------------------------------------------------
# Enums canônicos
# ---------------------------------------------------------------------------

_enums_cache: Optional[dict] = None


def carregar_enums(path: Optional[Path] = None) -> dict:
    global _enums_cache
    if path is None and _enums_cache is not None:
        return _enums_cache
    data = json.loads((path or ENUMS_PATH).read_text(encoding="utf-8"))
    dominios = data.get("dominios", {})
    if path is None:
        _enums_cache = dominios
    return dominios


class EnumResultado(NamedTuple):
    """canonico: valor canônico (exato/alias) ou candidato (decisao_manual);
    via: exato | alias | decisao_manual | None (não reconhecido).

    Regra M14.3A: 'alias' cobre APENAS equivalência lexical real (mesma
    palavra/flexão) e é o único caminho que uma migração autorizada pode
    aplicar em lote. 'decisao_manual' indica mudança de significado/estágio/
    local/consentimento/resultado — o canonico retornado é só o CANDIDATO
    mais provável e nunca pode ser aplicado sem decisão humana caso a caso.
    """

    canonico: Optional[str]
    via: Optional[str]


def normalizar_enum(dominio: str, valor, enums: Optional[dict] = None) -> EnumResultado:
    """Compara com o vocabulário canônico. NUNCA aplica a correção — só sugere."""
    dominios = enums if enums is not None else carregar_enums()
    dom = dominios.get(dominio)
    if dom is None:
        raise KeyError(f"domínio de enum desconhecido: {dominio!r}")
    v_norm = norm_texto(valor)
    for oficial in dom.get("valores", []):
        if norm_texto(oficial) == v_norm:
            return EnumResultado(oficial, "exato")
    alias = dom.get("aliases", {}).get(v_norm)
    if alias is not None:
        return EnumResultado(alias, "alias")
    candidato = dom.get("decisao_manual", {}).get(v_norm)
    if candidato is not None:
        return EnumResultado(candidato, "decisao_manual")
    return EnumResultado(None, None)


# ---------------------------------------------------------------------------
# Chave de paciente e proteção de PII em relatórios
# ---------------------------------------------------------------------------


def chave_paciente(telefone, nome) -> Optional[str]:
    """Mesma regra de _chaveDeduplicacao (sync-crm-pacientes.gs):
    telefone normalizado tem prioridade; sem telefone, nome normalizado."""
    tel = norm_telefone(telefone)
    if tel:
        return f"tel:{tel}"
    n = norm_nome(nome)
    if n:
        return f"nome:{n}"
    return None


def hash_protegido(valor, contexto: str = "soprolife-reconciliacao") -> str:
    """Identificador protegido e estável para relatórios commitáveis.
    Nunca reversível na prática (SHA-256 truncado com contexto fixo)."""
    v = norm_texto(valor)
    if not v:
        return "h:vazio"
    digest = hashlib.sha256(f"{contexto}|{v}".encode("utf-8")).hexdigest()
    return f"h:{digest[:10]}"


def primeiro_nome(nome) -> str:
    partes = norm_nome(nome).split(" ")
    return partes[0] if partes and partes[0] else ""


def nomes_compativeis(a, b) -> bool:
    """Heurística conservadora de 'possível mesma pessoa' por nome:
    nomes normalizados iguais, ou um é prefixo de tokens do outro
    (ex.: 'maria' vs 'maria silva'). Primeiro nome igual sozinho NÃO
    basta para fundir — só para sinalizar conflito a decidir por humano."""
    na, nb = norm_nome(a), norm_nome(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = na.split(" "), nb.split(" ")
    menor, maior = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return maior[: len(menor)] == menor


if __name__ == "__main__":
    # Uso direto: smoke check rápido (sem dados reais).
    exemplos = ["02/07/2026", "2026-07-02T14:30:55", "06/2026", "junho/2026", "2026", "sem data"]
    for e in exemplos:
        print(f"{e!r:28s} → {parse_data_flex(e)}")
