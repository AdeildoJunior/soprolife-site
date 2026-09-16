"""M41 — safe RESPONSE-SHAPE diagnostics for a 4xx/5xx that carries no usable
``erros[]`` (see DPS #5, 2026-09-15: HTTP 400 with nothing extractable by
``wire.decode_nfse_error_envelope``).

Root problem this fixes: after M40, a rejection body that isn't the exact
documented ``NFSePostResponseErro`` shape yields NOTHING — not even enough
to know whether the body was empty, HTML, plain text, or JSON with
differently-named fields. This module adds a strictly bounded SHAPE summary
(never content) plus support for the flatter documented ``ResponseErro``
shape (top-level ``codigo``/``descricao``/``complemento``/``mensagem``/
``erro`` instead of a nested ``erros[]`` array).

Never persisted here: the raw body, the signed/unsigned XML, Base64, any
certificate material, the request body, or any header other than the single
``Content-Type`` value already carried on ``transport.TransportResponse``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .wire import (FIELD_ERROR_LIST, SefinValidationError, WireFormatError,
                   _parse_json_object)

MAX_TOP_LEVEL_KEYS = 20
MAX_KEY_LEN = 60
MAX_CONTENT_TYPE_LEN = 100

# The complete set of documented safe scalar error fields across BOTH known
# SEFIN error shapes (NFSePostResponseErro's erros[] items, and the flatter
# ResponseErro object). Anything outside this set is never read by
# ``decode_documented_error_fields`` below, at any nesting level.
DOCUMENTED_ERROR_FIELD_NAMES = ("codigo", "descricao", "complemento", "mensagem", "erro")


def _truncate(value: str, max_len: int) -> str:
    # Strip control/non-printable characters defensively: this is
    # server-controlled input (a response header or a JSON key name),
    # never validated by anything upstream of this function.
    cleaned = "".join(ch for ch in value if ch.isprintable())
    return cleaned[:max_len]


def classify_body_kind(body: bytes, content_type: str | None) -> str:
    """One of: empty, json_object, json_array, text, html, binary.

    Never raises. Order: empty check first, then JSON (the documented
    contract), then a content-type/sniff-based fallback for anything else.
    """
    if not body:
        return "empty"
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    else:
        if isinstance(parsed, dict):
            return "json_object"
        if isinstance(parsed, list):
            return "json_array"
        # A bare JSON scalar/string/number is technically valid JSON but not
        # one of the two documented container shapes — fall through to the
        # text/html/binary classification below, since there is nothing
        # structured here to report on anyway.
    ct = (content_type or "").lower()
    if "html" in ct:
        return "html"
    stripped = body.lstrip()[:15].lower()
    if stripped.startswith(b"<!doctype") or stripped.startswith(b"<html"):
        return "html"
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    # Mostly-printable UTF-8 text with no JSON/HTML shape is still useful to
    # know about ("text"); a body that decodes but is mostly control/binary
    # noise is reported as binary instead.
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    if not text or printable / len(text) < 0.9:
        return "binary"
    return "text"


def safe_top_level_json_keys(body: bytes) -> tuple[str, ...] | None:
    """Key NAMES only (never values) of a top-level JSON object, capped in
    count and per-key length. ``None`` for anything that is not a JSON
    object (including malformed JSON) — never raises."""
    try:
        payload = _parse_json_object(body)
    except WireFormatError:
        return None
    return tuple(_truncate(str(k), MAX_KEY_LEN) for k in list(payload.keys())[:MAX_TOP_LEVEL_KEYS])


@dataclass(frozen=True)
class ResponseShapeSummary:
    content_type: str | None
    content_length: int
    sha256: str
    body_kind: str
    top_level_keys: tuple[str, ...] | None


def summarize_response_shape(body: bytes, content_type: str | None) -> ResponseShapeSummary:
    """The complete M41 shape summary for one response body. Every field is
    either a count, a hash, or a bounded list of key NAMES — never content."""
    safe_content_type = _truncate(content_type, MAX_CONTENT_TYPE_LEN) if content_type else None
    kind = classify_body_kind(body, content_type)
    keys = safe_top_level_json_keys(body) if kind == "json_object" else None
    return ResponseShapeSummary(
        content_type=safe_content_type,
        content_length=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        body_kind=kind,
        top_level_keys=keys,
    )


def _extract_documented_fields_from(obj: dict) -> dict:
    values = {name: obj.get(name) for name in DOCUMENTED_ERROR_FIELD_NAMES}
    return {k: v for k, v in values.items() if isinstance(v, str)}


def decode_documented_error_fields(body: bytes) -> tuple[SefinValidationError, ...]:
    """Support every documented SEFIN error shape seen so far, in one call:

    1. ``NFSePostResponseErro`` — a top-level ``erros`` array of objects,
       each with ``codigo``/``descricao``/``complemento`` (see
       ``wire.decode_nfse_error_envelope``, unchanged, reused here).
    2. ``ResponseErro`` (flat) — the same kind of fields directly at the
       top level, optionally also ``mensagem``/``erro`` as STRINGS.
    3. ``ResponseErro`` (nested under ``erro``) — M43 addition: a
       top-level ``erro`` key whose VALUE is itself an object carrying the
       documented fields (observed on ``GET /nfse/{chaveAcesso}`` in
       production, 2026-09-16) rather than a plain string.

    Never raises; an unrecognized shape yields an empty tuple, exactly like
    ``wire.decode_nfse_error_envelope`` already does. Only the five
    documented field names are ever read, at either shape or nesting level
    — never anything else in the JSON, and never a value that is not
    already a plain string.
    """
    from .wire import decode_nfse_error_envelope  # local import: avoid a cycle at module load

    nested = decode_nfse_error_envelope(body)
    if nested:
        return nested
    try:
        payload = _parse_json_object(body)
    except WireFormatError:
        return ()
    if FIELD_ERROR_LIST in payload:
        return ()  # an `erros` key existed but decode_nfse_error_envelope already tried it

    values = _extract_documented_fields_from(payload)
    erro_value = payload.get("erro")
    if isinstance(erro_value, dict):
        # Shape 3: don't let the flat scan above have already claimed a
        # string "erro" from a sibling key with the same name — merge, with
        # the nested object's own fields taking precedence since they are
        # more specific.
        values = {**values, **_extract_documented_fields_from(erro_value)}
    if not values:
        return ()
    return (SefinValidationError(
        codigo=values.get("codigo"), descricao=values.get("descricao"),
        complemento=values.get("complemento"), mensagem=values.get("mensagem"),
        erro=values.get("erro"),
    ),)
