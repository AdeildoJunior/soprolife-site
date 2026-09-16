"""M40 — sanitize SEFIN structured validation errors before they are ever
persisted or shown anywhere (audit trail, report, API response).

Input here is ALWAYS the already-parsed, already-narrowed
``wire.SefinValidationError`` tuple (three documented string-or-None fields
per item) — this module never receives the raw response body, the signed
DPS XML, certificate material or Base64 request content, so none of those
can leak through it by construction: there is no parameter to carry them.

What this module adds on top of ``wire.decode_nfse_error_envelope`` is
purely defensive, for the two fields (`descricao`/`complemento`) that are
free text chosen by the government API, not a closed catalog like
``codigo`` usually is:

- caps how many errors survive (a hostile/broken provider could otherwise
  send an unbounded array);
- truncates every string to a fixed length;
- masks digit runs shaped like a CPF/CNPJ, a 44+ digit NFS-e access key, or
  a UUID — the only PII-shaped patterns plausible in government free text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .wire import SefinValidationError

# A hostile or malfunctioning provider could return an arbitrarily long
# ``erros`` array; only the first few are ever worth a human's attention,
# and an unbounded list is itself a minor resource/log-noise risk.
MAX_ERRORS = 5
MAX_STR_LEN = 200
# M44 — MensagemProcessamento.parametros is documented as an array; a
# hostile/broken provider could send an arbitrarily long one.
MAX_PARAMETROS = 10

# Order matters: the longest/most-specific pattern is masked first, so a
# 53-digit access key is replaced whole before a shorter CNPJ/CPF-length
# pattern could ever match a substring of it.
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
# The branded TSIdNFSe shape (see identifiers.NFSE_ACCESS_KEY_PATTERN):
# "NFS" + 9 digits + 14 alphanumeric + 27 digits, 53 chars total. Matched
# BEFORE the bare-digit-run pattern below, since "NFS" glued directly onto
# the digits (no separating space) would otherwise defeat a \b boundary.
_NFSE_ACCESS_KEY_RE = re.compile(r"\bNFS[0-9]{9}[0-9A-Za-z]{14}[0-9]{27}\b")
_ACCESS_KEY_RE = re.compile(r"\b\d{44,60}\b")
_CNPJ_RE = re.compile(r"\b\d{14}\b")
_CPF_RE = re.compile(r"\b\d{11}\b")


def _mask(text: str) -> str:
    text = _UUID_RE.sub("[UUID_MASCARADO]", text)
    text = _NFSE_ACCESS_KEY_RE.sub("[CHAVE_ACESSO_MASCARADA]", text)
    text = _ACCESS_KEY_RE.sub("[CHAVE_ACESSO_MASCARADA]", text)
    text = _CNPJ_RE.sub("[CNPJ_MASCARADO]", text)
    text = _CPF_RE.sub("[CPF_MASCARADO]", text)
    return text


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    return _mask(value[:MAX_STR_LEN])


def _clean_parametros(value: tuple | None) -> tuple | None:
    """M44 — sanitize MensagemProcessamento.parametros: cap length, mask/
    truncate every string element the same way as any other free-text
    field, pass numeric/boolean elements through unchanged (they cannot
    carry PII-shaped text). ``value`` is already type-narrowed to JSON
    primitives by ``wire._raw_parametros`` — nothing here can fail to
    coerce. Returns ``None`` for ``None``/empty input, never an empty
    tuple, so downstream truthiness checks treat "no parametros" uniformly
    with every other optional field."""
    if not value:
        return None
    cleaned = tuple(
        _mask(item[:MAX_STR_LEN]) if isinstance(item, str) else item
        for item in value[:MAX_PARAMETROS]
    )
    return cleaned or None


@dataclass(frozen=True)
class SanitizedValidationError:
    codigo: str | None
    descricao: str | None
    complemento: str | None
    # M41 — the two additional documented safe scalar fields (see
    # wire.SefinValidationError / response_diagnostics.py), sanitized the
    # same way as the other three.
    mensagem: str | None = None
    erro: str | None = None
    # M44 — MensagemProcessamento.parametros, sanitized (see
    # _clean_parametros above).
    parametros: tuple | None = None


def sanitize_sefin_errors(
    errors: tuple[SefinValidationError, ...],
) -> tuple[SanitizedValidationError, ...]:
    """Truncate + mask every field of up to ``MAX_ERRORS`` entries.

    Never raises: ``errors`` is already a tuple of ``SefinValidationError``
    (see ``wire.decode_nfse_error_envelope``), so every field is already
    ``str | None`` (or, for ``parametros``, a tuple of JSON primitives) —
    there is nothing left here that can fail to coerce.
    """
    return tuple(
        SanitizedValidationError(
            codigo=_clean(item.codigo),
            descricao=_clean(item.descricao),
            complemento=_clean(item.complemento),
            mensagem=_clean(item.mensagem),
            erro=_clean(item.erro),
            parametros=_clean_parametros(item.parametros),
        )
        for item in errors[:MAX_ERRORS]
    )
