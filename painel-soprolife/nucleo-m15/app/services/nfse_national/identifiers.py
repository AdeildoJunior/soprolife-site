"""Textual identifiers for the National NFS-e restricted layout (Annex I v1.01).

Every identifier here is a TEXTUAL type in the official schema, never numeric.
CNPJ is validated against ``TSCNPJ`` (``[0-9A-Z]{14}``, restricted schema,
``tiposSimples_v1.01.xsd``): the national platform accepted alphanumeric CNPJ
as of August 2026 (see OFFICIAL_NORMATIVE_BRIEF.md), so no regex here may be
narrowed to digits-only or ever pass through ``int()``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

CNPJ_PATTERN = re.compile(r"^[0-9A-Z]{14}$")
CPF_PATTERN = re.compile(r"^[0-9]{11}$")
MUNICIPIO_IBGE_PATTERN = re.compile(r"^[0-9]{7}$")
# TSIdDPS (tiposSimples_v1.01.xsd): "DPS" + cMun(7) + tipoInsc(1) + inscricao(14) + serie(5) + numero(15)
DPS_ID_PATTERN = re.compile(r"^DPS[0-9]{7}(1[0-9]{14}|2[0-9A-Z]{14})[0-9]{20}$")
# TSIdNFSe (tiposSimples_v1.01.xsd): "NFS" + cMun(7) + ambGer(1) + tipoInsc(1) +
# inscricao(14) + numNFSe(13) + anoMesEmis(4) + codNum(9) + DV(1) = 53 chars.
# This is the "chave de acesso" — the value of the ``Id`` attribute on
# ``infNFSe`` in the NFS-e XML the API returns for a successful ``POST /nfse``
# (manual dos contribuintes, §1.3.2.a: "...ou o arquivo XML da NFS-e gerada").
NFSE_ACCESS_KEY_PATTERN = re.compile(r"^NFS[0-9]{9}[0-9A-Z]{14}[0-9]{27}$")
_NFSE_ACCESS_KEY_SCAN_PATTERN = re.compile(rb"NFS[0-9]{9}[0-9A-Z]{14}[0-9]{27}")
# M52.1 — the SAME key without the literal ``NFS`` prefix: 50 characters.
#
# This is not a variant we invented: it is the form the live restricted API
# actually returns. ``GET /dps/{idDPS}`` answered (2026-09-19) with
# ``{"chaveAcesso": "<50 chars>", ...}`` and a ``Location`` header ending in
# ``/nfse/<the same 50 chars>``. The 53-character ``TSIdNFSe`` above is the
# ``infNFSe/@Id`` ATTRIBUTE inside the NFS-e XML; the ``chaveAcesso`` field
# and the queryable ``/nfse/{chave}`` path segment are that same identifier
# WITHOUT the prefix. Both describe one NFS-e; they are two encodings of it.
#
# Treating them as if they had to be string-equal is exactly what made the
# codebase unable to read a successful response (see
# ``normalize_nfse_access_key`` and ``nfse_access_keys_match``).
NFSE_ACCESS_KEY_BARE_PATTERN = re.compile(r"^[0-9]{9}[0-9A-Z]{14}[0-9]{27}$")
NFSE_ACCESS_KEY_PREFIX = "NFS"
NFSE_XML_NS = "http://www.sped.fazenda.gov.br/nfse"


class InvalidIdentifierError(ValueError):
    """A textual fiscal identifier failed its official-schema pattern."""


def assert_cnpj(value: str) -> str:
    """Validate transport/storage shape only (syntactic), never business rules."""
    if not isinstance(value, str) or not CNPJ_PATTERN.fullmatch(value):
        raise InvalidIdentifierError("CNPJ deve ter 14 caracteres alfanuméricos (TSCNPJ).")
    return value


def assert_cpf(value: str) -> str:
    if not isinstance(value, str) or not CPF_PATTERN.fullmatch(value):
        raise InvalidIdentifierError("CPF deve ter 11 dígitos (TSCPF).")
    return value


def assert_municipio_ibge(value: str) -> str:
    if not isinstance(value, str) or not MUNICIPIO_IBGE_PATTERN.fullmatch(value):
        raise InvalidIdentifierError("Código de município IBGE deve ter 7 dígitos.")
    return value


def assert_nfse_access_key(value: str) -> str:
    """Validate the NFS-e access key (chave de acesso) shape only — TSIdNFSe,
    53 chars, prefix ``NFS``. Never proof of issuance by itself; callers still
    need a confirmed provider response before trusting this."""
    if not isinstance(value, str) or not NFSE_ACCESS_KEY_PATTERN.fullmatch(value):
        raise InvalidIdentifierError(
            "Chave de acesso da NFS-e deve seguir o padrão TSIdNFSe "
            "(53 posições, prefixo 'NFS')."
        )
    return value


def normalize_nfse_access_key(value: str) -> str:
    """Return the CANONICAL access key: the bare 50-character form.

    Accepts either encoding of the one identifier — the 53-character
    ``TSIdNFSe`` (``NFS`` + 50) as it appears in ``infNFSe/@Id``, or the bare
    50-character value as it appears in the JSON ``chaveAcesso`` field and in
    the ``Location``/``GET /nfse/{chave}`` path segment.

    The bare form is canonical on purpose: it is what the API itself hands
    back and the only form that can be used to query ``/nfse/{chave}``.

    Raises ``InvalidIdentifierError`` for anything else — never guesses,
    never truncates, never pads.
    """
    if not isinstance(value, str):
        raise InvalidIdentifierError("Chave de acesso deve ser uma string.")
    candidate = value.strip()
    if NFSE_ACCESS_KEY_PATTERN.fullmatch(candidate):
        return candidate[len(NFSE_ACCESS_KEY_PREFIX):]
    if NFSE_ACCESS_KEY_BARE_PATTERN.fullmatch(candidate):
        return candidate
    raise InvalidIdentifierError(
        "Chave de acesso da NFS-e deve seguir o padrão TSIdNFSe "
        "(53 posições com prefixo 'NFS') ou a forma nua de 50 posições."
    )


def nfse_access_key_id(value: str) -> str:
    """Return the key in the ``TSIdNFSe`` form: ``NFS`` + the 50 characters.

    This is the form this codebase already stores as ``external_id`` and the
    form that appears as ``infNFSe/@Id``. Kept as the storage/interop
    convention so M52.1's parser fix does not silently change what a
    successful issuance records — use :func:`normalize_nfse_access_key` when
    you need the bare form for a ``/nfse/{chave}`` URL.
    """
    return NFSE_ACCESS_KEY_PREFIX + normalize_nfse_access_key(value)


def nfse_access_keys_match(*values: str | None) -> bool:
    """True only when every supplied value is a VALID access key and all of
    them normalize to the same canonical key.

    Fails closed: any ``None``, any malformed value, or any disagreement
    returns False. Used to cross-check the key that arrives by several
    independent channels at once (JSON body, ``Location`` header, the NFS-e
    XML's own ``infNFSe/@Id``) — agreement across channels is what makes the
    key trustworthy enough to record.
    """
    normalized = set()
    for value in values:
        if value is None:
            return False
        try:
            normalized.add(normalize_nfse_access_key(value))
        except InvalidIdentifierError:
            return False
    return len(normalized) == 1


def access_key_from_location_header(location: str | None) -> str | None:
    """Canonical key from a ``Location`` header pointing at ``…/nfse/{chave}``.

    Returns ``None`` — never raises — when the header is absent, is not an
    ``/nfse/`` location, or does not end in a valid key. The caller decides
    what an absent corroborating channel means.
    """
    if not isinstance(location, str) or not location.strip():
        return None
    tail = location.strip().rstrip("/").rsplit("/", 1)[-1]
    if not tail:
        return None
    try:
        return normalize_nfse_access_key(tail)
    except InvalidIdentifierError:
        return None


def extract_nfse_access_key(xml_bytes: bytes) -> str:
    """Parse a returned NFS-e XML (``POST /nfse`` success body, per the
    official contributor manual §1.3.2.a) and return its access key —
    ``infNFSe/@Id`` (``TSIdNFSe``). Raises ``InvalidIdentifierError`` for
    anything that isn't a well-formed ``<NFSe>`` document with a
    schema-shaped access key; never guesses a value.
    """
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        raise InvalidIdentifierError(f"Corpo não é XML bem formado: {exc}") from exc
    if root.tag != f"{{{NFSE_XML_NS}}}NFSe":
        raise InvalidIdentifierError("Elemento raiz não é <NFSe> no namespace oficial.")
    inf = root.find(f"{{{NFSE_XML_NS}}}infNFSe")
    if inf is None:
        raise InvalidIdentifierError("infNFSe ausente na NFS-e retornada.")
    raw_id = inf.get("Id")
    if not raw_id:
        raise InvalidIdentifierError("Atributo Id ausente em infNFSe.")
    return assert_nfse_access_key(raw_id)


def find_nfse_access_key_best_effort(body: bytes) -> str | None:
    """Tolerant access-key extraction for an UNCOMPRESSED reconciliation body.

    M38: this is no longer the reconciliation entry point — it is the fallback
    tier of ``wire.find_nfse_access_key_in_response``, which first decodes the
    documented ``nfseXmlGZipB64`` envelope (a GZip-compressed key is invisible
    to the raw scan below). Everything this function already proved still
    holds; nothing here was weakened.

    The official contributor manual confirms this endpoint "recupera a chave
    de acesso da NFS-e" but — unlike ``POST /nfse`` — does not document the
    exact response envelope (JSON vs. the same NFS-e XML), and the restricted
    Swagger (``adn.producaorestrita.nfse.gov.br``) requires an mTLS client
    certificate at the TLS layer even to view, so it could not be reached to
    confirm the exact schema (see OFFICIAL_SOURCES_USED.md M30 addendum).

    Rather than guess a JSON field name, this tries the one confirmed shape
    (the full NFS-e XML) first, then falls back to locating the single
    occurrence of the officially-defined ``TSIdNFSe`` pattern anywhere in the
    raw body. Zero or ambiguous (more than one distinct) matches return
    ``None`` — fail closed, never a guess.
    """
    try:
        return extract_nfse_access_key(body)
    except InvalidIdentifierError:
        pass
    matches = {m.decode("ascii") for m in _NFSE_ACCESS_KEY_SCAN_PATTERN.findall(body)}
    if len(matches) == 1:
        return matches.pop()
    return None


@dataclass(frozen=True)
class DpsIdComponents:
    """Every component of ``TSIdDPS``, kept explicit instead of a single blob.

    ``inscricao_federal`` is the ISSUER's CNPJ (14 chars) or CPF (11 digits,
    left-padded with zeros to 14 per the schema note). Both remain textual.
    """
    codigo_municipio: str
    tipo_inscricao_federal: int  # 1 = CPF, 2 = CNPJ (schema note on TSIdDPS)
    inscricao_federal: str
    serie_dps: str
    numero_dps: str

    def __post_init__(self):
        assert_municipio_ibge(self.codigo_municipio)
        if self.tipo_inscricao_federal not in (1, 2):
            raise InvalidIdentifierError("Tipo de inscrição federal deve ser 1 (CPF) ou 2 (CNPJ).")
        if self.tipo_inscricao_federal == 1:
            assert_cpf(self.inscricao_federal)  # exactly 11 digits; padding happens only in build_dps_id
        else:
            assert_cnpj(self.inscricao_federal)
        if not re.fullmatch(r"[0-9]{5}", self.serie_dps):
            raise InvalidIdentifierError("Série da DPS deve ter 5 dígitos.")
        if not re.fullmatch(r"[0-9]{15}", self.numero_dps):
            raise InvalidIdentifierError("Número da DPS deve ter 15 dígitos.")

    @property
    def inscricao_federal_padded(self) -> str:
        """14-char field as TSIdDPS requires: CNPJ as-is, CPF left-padded with zeros."""
        if self.tipo_inscricao_federal == 1:
            return self.inscricao_federal.rjust(14, "0")
        return self.inscricao_federal


def build_dps_id(components: DpsIdComponents) -> str:
    """Deterministic ``Id`` attribute for ``infDPS``, per ``TSIdDPS``."""
    dps_id = (
        "DPS"
        + components.codigo_municipio
        + str(components.tipo_inscricao_federal)
        + components.inscricao_federal_padded
        + components.serie_dps
        + components.numero_dps
    )
    if not DPS_ID_PATTERN.fullmatch(dps_id):
        raise InvalidIdentifierError(f"Id de DPS construído não confere com TSIdDPS: {dps_id!r}")
    return dps_id
