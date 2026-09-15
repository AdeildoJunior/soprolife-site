"""M38 — the SEFIN Nacional wire envelope: JSON + GZip + Base64.

Root cause this module fixes: until M37 the provider sent the signed DPS XML
as the raw HTTP body, with no ``Content-Type``. The confirmed SEFIN Nacional
issuance route
(``https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse``) answered
that with **HTTP 415 Unsupported Media Type** on the real DPS #3 attempt
(2026-09-15), because its documented contract is:

    POST /nfse
      consumes: application/json
      produces: application/json
      request  schema: NFSePostRequest        -> required: dpsXmlGZipB64
      response schema: NFSePostResponseSucesso (201) / NFSePostResponseErro

``dpsXmlGZipB64``  = signed DPS XML  -> GZip -> Base64 -> JSON string.
``nfseXmlGZipB64`` = Base64 -> GZip decompress -> NFS-e XML.

Two properties are load-bearing and enforced by tests rather than by comment:

- **No XML reserialization after signing.** ``encode_xml_gzip_b64`` receives
  the exact bytes ``serialize_dps`` produced and ``sign_dps`` signed; it only
  compresses them. Re-parsing/re-serializing signed XML anywhere between the
  signature and the wire would invalidate the XMLDSig digest.
- **Fail closed on every decode step.** A 2xx status is never proof of
  issuance (the M26 uncertain-state model). Malformed JSON, a missing field,
  invalid Base64, invalid GZip, an NFS-e XML that is not a schema-shaped
  ``<NFSe>``, or a ``chaveAcesso`` that disagrees with the access key inside
  the returned XML all raise ``WireFormatError`` — which the provider maps to
  MALFORMED_RESPONSE -> UNCERTAIN, never to a silent success.

Nothing here logs, persists or returns raw response bytes: ``WireFormatError``
messages are fixed, content-free strings (see ``responses.safe_diagnostic_code``,
which in any case only ever records the HTTP status).
"""
from __future__ import annotations

import base64
import binascii
import gzip
import io
import json
import zlib
from dataclasses import dataclass

from .identifiers import (InvalidIdentifierError, extract_nfse_access_key,
                          find_nfse_access_key_best_effort)

MEDIA_TYPE_JSON = "application/json"

# Exactly the headers the documented contract requires. Kept as a module
# constant (copied at each call site) so no caller can mutate the shared dict.
JSON_REQUEST_HEADERS: dict[str, str] = {
    "Content-Type": MEDIA_TYPE_JSON,
    "Accept": MEDIA_TYPE_JSON,
}
# A GET carries no body, so it declares only what it accepts.
JSON_ACCEPT_HEADERS: dict[str, str] = {"Accept": MEDIA_TYPE_JSON}

FIELD_DPS_XML = "dpsXmlGZipB64"
FIELD_NFSE_XML = "nfseXmlGZipB64"
FIELD_ACCESS_KEY = "chaveAcesso"


class WireFormatError(ValueError):
    """A payload did not match the documented SEFIN JSON/GZip/Base64 envelope.

    Message text is always a fixed, content-free code — never response bytes.
    """


def encode_xml_gzip_b64(xml_bytes: bytes) -> str:
    """``xml_bytes`` -> GZip -> Base64, as a single unwrapped ASCII line.

    ``mtime=0`` (and a fixed compression level) keeps the output byte-identical
    for identical input, so tests can assert on the exact string instead of
    only on a roundtrip. ``base64.b64encode`` never inserts line breaks — the
    legacy ``encodestring``-style wrapping that would corrupt a JSON string
    value is impossible here by construction.
    """
    if not isinstance(xml_bytes, (bytes, bytearray)):
        raise WireFormatError("payload_xml_deve_ser_bytes")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=9, mtime=0) as stream:
        stream.write(xml_bytes)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def decode_b64_gzip_xml(value: str) -> bytes:
    """Inverse of :func:`encode_xml_gzip_b64`. Strict at both steps.

    ``validate=True`` makes Base64 decoding reject stray characters instead of
    silently discarding them, so a truncated/garbled field can never decode
    into "some" bytes that then happen to gunzip.
    """
    if not isinstance(value, str) or not value:
        raise WireFormatError("campo_base64_ausente_ou_vazio")
    try:
        compressed = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WireFormatError("base64_invalido") from exc
    try:
        return gzip.decompress(compressed)
    except (OSError, EOFError, zlib.error) as exc:
        # BadGzipFile (an OSError) for a bad magic/header, EOFError for a
        # truncated member, zlib.error for a corrupt deflate stream. All three
        # fail closed, and the raised error carries no payload content.
        raise WireFormatError("gzip_invalido") from exc


def build_issue_request_body(signed_xml: bytes) -> bytes:
    """The exact UTF-8 JSON body for ``POST /nfse``: ``NFSePostRequest``.

    Only ``dpsXmlGZipB64`` is emitted — the documented required property.
    ``ensure_ascii`` is irrelevant for a Base64 value but kept explicit, and
    the separators drop insignificant whitespace so the body is deterministic.
    """
    payload = {FIELD_DPS_XML: encode_xml_gzip_b64(signed_xml)}
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _parse_json_object(body: bytes) -> dict:
    if not body:
        raise WireFormatError("corpo_vazio")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WireFormatError("json_invalido") from exc
    if not isinstance(parsed, dict):
        raise WireFormatError("json_nao_e_objeto")
    return parsed


@dataclass(frozen=True)
class DecodedNfseEnvelope:
    """A fully verified NFS-e success envelope.

    Reaching this object means the JSON parsed, both required fields were
    present, the Base64/GZip roundtrip produced well-formed NFS-e XML with a
    schema-shaped ``infNFSe/@Id``, and that key matched the envelope's
    ``chaveAcesso``. Only then may a caller treat the response as a success.
    """
    access_key: str
    nfse_xml: bytes


def decode_nfse_success_envelope(body: bytes) -> DecodedNfseEnvelope:
    """Decode+verify ``NFSePostResponseSucesso``. Raises ``WireFormatError``
    for anything short of a fully consistent envelope.

    The access key is extracted from the returned XML by the existing
    identifier logic (``extract_nfse_access_key`` -> ``infNFSe/@Id``,
    ``TSIdNFSe``) and then required to equal the JSON ``chaveAcesso``. The
    cross-check matters because the two values come from different layers of
    the same response: agreeing on both is what makes the key trustworthy
    enough to store as ``external_id``.

    Deliberately NOT a full ``NFSe_v1.01.xsd`` validation: a returned NFS-e
    that is well-formed and carries a valid access key but trips some optional
    element of the vendored restricted schema is a real issuance, and
    downgrading it to UNCERTAIN would be the dangerous direction of error.
    The identifier check is what this codebase already treats as proof.
    """
    payload = _parse_json_object(body)

    raw_key = payload.get(FIELD_ACCESS_KEY)
    if not isinstance(raw_key, str) or not raw_key:
        raise WireFormatError("chave_acesso_ausente")
    raw_nfse = payload.get(FIELD_NFSE_XML)
    if not isinstance(raw_nfse, str) or not raw_nfse:
        raise WireFormatError("nfse_xml_ausente")

    nfse_xml = decode_b64_gzip_xml(raw_nfse)
    try:
        key_from_xml = extract_nfse_access_key(nfse_xml)
    except InvalidIdentifierError as exc:
        raise WireFormatError("nfse_xml_invalido") from exc
    if key_from_xml != raw_key.strip():
        raise WireFormatError("chave_acesso_divergente_do_xml")
    return DecodedNfseEnvelope(access_key=key_from_xml, nfse_xml=nfse_xml)


def looks_like_nfse_envelope(body: bytes) -> bool:
    """True when ``body`` is a JSON object carrying ``nfseXmlGZipB64``.

    Used only by the reconciliation path, to tell "this IS the documented
    envelope and it is broken" (fail closed) apart from "this is some other
    shape entirely" (fall back to the legacy tolerant scan).
    """
    try:
        return FIELD_NFSE_XML in _parse_json_object(body)
    except WireFormatError:
        return False


def find_nfse_access_key_in_response(body: bytes) -> str | None:
    """Best-effort access-key extraction for reconciliation (``GET /dps/{id}``).

    M38 audit of the GET contract, stated honestly: the available evidence
    proves the JSON/GZip/Base64 envelope for ``POST /nfse`` only. The official
    contributor manual confirms ``GET /dps/{id}`` "recupera a chave de acesso
    da NFS-e" but documents no response schema for it, and the restricted
    Swagger that would define it requires an mTLS client certificate even to
    view (M30 addendum) — it was never reached. So this function does NOT
    assert a GET envelope; it stays tolerant, in this order:

    1. If the body IS a JSON object carrying ``nfseXmlGZipB64`` — the same
       service, the same field name, the same ``application/json`` production
       contract — decode it exactly like the POST envelope. This is the part
       M38 adds: without it, a gzipped+Base64 reconcile body would be
       undecodable, since the raw-byte scan in step 3 cannot see an access key
       through GZip compression. A body that declares the envelope and then
       fails to decode it returns ``None`` (UNCERTAIN), never falling through
       to a looser guess.
    2. ``chaveAcesso`` is cross-checked against the XML only when the envelope
       actually carries it — unlike the POST success schema, it is not proven
       to be part of this response, so requiring it here would risk turning a
       real, decodable NFS-e into a false UNCERTAIN.
    3. Otherwise, the pre-existing tolerant path (raw NFS-e XML, then a single
       unambiguous ``TSIdNFSe`` match anywhere in the body) is used unchanged.

    ``None`` always means "no trustworthy key" — the caller maps that to
    UNCERTAIN, never to absence.
    """
    if looks_like_nfse_envelope(body):
        try:
            payload = _parse_json_object(body)
            nfse_xml = decode_b64_gzip_xml(payload.get(FIELD_NFSE_XML))
            key_from_xml = extract_nfse_access_key(nfse_xml)
        except (WireFormatError, InvalidIdentifierError):
            return None
        declared = payload.get(FIELD_ACCESS_KEY)
        if isinstance(declared, str) and declared.strip() and declared.strip() != key_from_xml:
            return None
        return key_from_xml
    return find_nfse_access_key_best_effort(body)
