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


def sanitize_sefin_errors(
    errors: tuple[SefinValidationError, ...],
) -> tuple[SanitizedValidationError, ...]:
    """Truncate + mask every field of up to ``MAX_ERRORS`` entries.

    Never raises: ``errors`` is already a tuple of ``SefinValidationError``
    (see ``wire.decode_nfse_error_envelope``), so every field is already
    ``str | None`` — there is nothing left here that can fail to coerce.
    """
    return tuple(
        SanitizedValidationError(
            codigo=_clean(item.codigo),
            descricao=_clean(item.descricao),
            complemento=_clean(item.complemento),
            mensagem=_clean(item.mensagem),
            erro=_clean(item.erro),
        )
        for item in errors[:MAX_ERRORS]
    )
