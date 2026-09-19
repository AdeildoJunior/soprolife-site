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

from .identifiers import (InvalidIdentifierError, access_key_from_location_header,
                          extract_nfse_access_key, find_nfse_access_key_best_effort,
                          nfse_access_key_id, nfse_access_keys_match,
                          normalize_nfse_access_key)

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
FIELD_ERROR_LIST = "erros"
FIELD_ERROR_CODE = "codigo"
FIELD_ERROR_DESCRIPTION = "descricao"
FIELD_ERROR_COMPLEMENT = "complemento"


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
    # M52.1 — compare the two channels as IDENTIFIERS, not as strings.
    # ``infNFSe/@Id`` is the 53-character ``NFS``-prefixed form; the JSON
    # ``chaveAcesso`` is the bare 50-character form of the very same key. The
    # previous ``!=`` demanded byte equality between two different encodings
    # of one identifier, so this cross-check could never pass against the real
    # API — it rejected genuine successes as "malformed". Normalizing both
    # sides keeps the check exactly as strict (a genuinely different key still
    # fails) while letting the true encoding difference through.
    if not nfse_access_keys_match(key_from_xml, raw_key):
        raise WireFormatError("chave_acesso_divergente_do_xml")
    # A chave devolvida continua sendo a forma TSIdNFSe vinda do XML — a
    # convencao de armazenamento nao muda com esta correcao.
    return DecodedNfseEnvelope(access_key=key_from_xml, nfse_xml=nfse_xml)


def decode_nfse_document_response(body: bytes, *, expected_access_key: str | None = None
                                  ) -> DecodedNfseEnvelope:
    """Decode a ``GET /nfse/{chaveAcesso}`` response into the NFS-e XML.

    M53 — the live restricted API answers this endpoint with
    ``application/json`` carrying the SAME envelope as ``POST /nfse``:

        {"tipoAmbiente": 2, "versaoAplicativo": "...",
         "dataHoraProcessamento": "...", "chaveAcesso": "<50>",
         "nfseXmlGZipB64": "<gzip+base64 of the NFS-e XML>"}

    It is NOT raw XML. Assuming XML and calling an XML parser straight on the
    bytes is what made M52.1 report "fetch failed" on a perfectly good HTTP
    200 — the document was there the whole time, one base64+gzip hop away.

    Because the contributor manual does not pin the envelope for this
    endpoint, both shapes are accepted: the JSON envelope above, or a raw
    NFS-e XML document. Anything else fails closed with ``WireFormatError``.

    ``expected_access_key`` (either encoding) is cross-checked against the
    envelope and the XML when supplied; disagreement fails closed.
    """
    stripped = body.lstrip() if body else b""
    if not stripped:
        raise WireFormatError("corpo_vazio")

    if stripped[:1] == b"<":
        # Raw NFS-e XML — the other documented possibility.
        try:
            key_from_xml = extract_nfse_access_key(body)
        except InvalidIdentifierError as exc:
            raise WireFormatError("nfse_xml_invalido") from exc
        if expected_access_key is not None and not nfse_access_keys_match(
                key_from_xml, expected_access_key):
            raise WireFormatError("chave_acesso_divergente_do_esperado")
        return DecodedNfseEnvelope(access_key=key_from_xml, nfse_xml=body)

    # JSON envelope. Reuse the POST success decoder so there is exactly ONE
    # implementation of the base64+gzip+cross-check logic.
    decoded = decode_nfse_success_envelope(body)
    if expected_access_key is not None and not nfse_access_keys_match(
            decoded.access_key, expected_access_key):
        raise WireFormatError("chave_acesso_divergente_do_esperado")
    return decoded


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


def find_nfse_access_key_in_response(body: bytes, *, location_header: str | None = None) -> str | None:
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
        if isinstance(declared, str) and declared.strip():
            # M52.1 — identifier comparison, not string comparison (see
            # decode_nfse_success_envelope).
            if not nfse_access_keys_match(key_from_xml, declared):
                return None
        return key_from_xml

    # M52.1 — the REAL ``GET /dps/{idDPS}`` shape, observed live on
    # 2026-09-19 against Produção Restrita:
    #
    #   200 application/json
    #   Location: …/SefinNacional/nfse/<50-char key>
    #   {"chaveAcesso": "<50-char key>", "dataHoraProcessamento": "…",
    #    "tipoAmbiente": 2, "versaoAplicativo": "…"}
    #
    # There is no ``nfseXmlGZipB64`` here — this endpoint returns the KEY, as
    # the contributor manual says ("recupera a chave de acesso da NFS-e"), not
    # the document. The previous code fell straight through to the raw-byte
    # scan, which only looks for the 53-character ``NFS``-prefixed form and
    # therefore found nothing in a body that plainly carried the key.
    #
    # When ``location_header`` is supplied it must AGREE. Disagreement between
    # the two channels returns None (fail closed) rather than picking one.
    location_key = access_key_from_location_header(location_header)
    try:
        payload = _parse_json_object(body)
    except WireFormatError:
        payload = None
    if isinstance(payload, dict):
        declared = payload.get(FIELD_ACCESS_KEY)
        if isinstance(declared, str) and declared.strip():
            try:
                declared_key = normalize_nfse_access_key(declared)
            except InvalidIdentifierError:
                return None
            if location_key is not None and location_key != declared_key:
                return None
            # Devolvido na MESMA convencao dos demais caminhos (TSIdNFSe,
            # prefixado). Use normalize_nfse_access_key() para montar a URL
            # /nfse/{chave}, que usa a forma nua.
            return nfse_access_key_id(declared_key)

    # No usable key in the body. A Location header alone is corroboration,
    # not a response contract, so it is never promoted to "the key" on its own.
    return find_nfse_access_key_best_effort(body)


@dataclass(frozen=True)
class SefinValidationError:
    """One documented SEFIN error entry.

    M44 — the official ``NFSePostResponseErro.erros[]`` item type is
    ``MensagemProcessamento``, which carries ALL FIVE of ``mensagem``,
    ``parametros``, ``codigo``, ``descricao``, ``complemento``. Until this
    fix, :func:`decode_nfse_error_envelope` only ever read
    ``codigo``/``descricao``/``complemento`` per item — an item shaped
    exactly like ``{"mensagem": "..."}`` (no other key) silently decoded to
    an ALL-``None`` entry, which still counted as "found" (a non-empty
    tuple) and therefore was never reported as unrecognized either: this is
    the exact blind spot behind DPS #6/#7's HTTP 400 with a present but
    seemingly-empty ``erros[]``.

    ``erro`` (M43) is the one field that is NOT part of
    ``MensagemProcessamento`` — it only ever comes from the separate flat/
    nested ``ResponseErro`` shape (see
    ``response_diagnostics.decode_documented_error_fields``), so it stays
    ``None`` for every ``erros[]`` item. ``parametros`` is the raw
    (type-narrowed, not yet sanitized) list of JSON primitive scalars from
    the item, or ``None``/``()`` when absent or unusable — sanitization
    (truncation, PII masking, count cap) happens downstream in
    ``error_sanitizer.sanitize_sefin_errors``. Anything else in the JSON,
    at any level, is silently ignored and never reaches this object.
    """
    codigo: str | None
    descricao: str | None
    complemento: str | None
    mensagem: str | None = None
    erro: str | None = None
    parametros: tuple | None = None


def _ci_field(obj: dict, name: str):
    """Case-insensitive-by-convention lookup for ONE documented field name:
    tries the exact name first, then its Capitalized form (e.g. "codigo"
    then "Codigo"). These are the only two casings ever observed from a
    SEFIN endpoint anywhere in this codebase — the erros[]/ResponseErro
    fields are documented lowercase, and the CNC consulta API (a
    different but also-official SEFIN endpoint, M42/M43) uses PascalCase
    for every field it returns (``TipoAmbiente``, ``SituacaoCadastral``,
    ...). Never guesses any OTHER casing, and never invents a field name
    not already in ``obj``.
    """
    if name in obj:
        return obj[name]
    capitalized = name[:1].upper() + name[1:]
    return obj.get(capitalized)


def _raw_parametros(value) -> tuple | None:
    """Type-narrow (never sanitize — that is error_sanitizer's job) a
    'parametros' field into a tuple of JSON primitive scalars, or None
    when the field is absent/not a list. A nested object/array/None INSIDE
    the list is silently dropped, never coerced to text."""
    if not isinstance(value, list):
        return None
    return tuple(v for v in value if isinstance(v, (str, int, float, bool)))


def decode_nfse_error_envelope(body: bytes) -> tuple[SefinValidationError, ...]:
    """M40/M44 — best-effort decode of ``NFSePostResponseErro`` (4xx/5xx
    bodies). Each item of ``erros[]`` is a ``MensagemProcessamento``:
    ``mensagem``, ``parametros``, ``codigo``, ``descricao``,
    ``complemento`` — ALL FIVE, not just the three this function read
    before M44. This function is the only place that body is ever parsed.

    M44 root cause fixed here: an item shaped exactly like
    ``{"mensagem": "..."}`` (documented, and the actual shape behind DPS
    #6/#7's HTTP 400) used to decode to an all-``None`` entry — silently
    dropping the one field it had, while still counting as "found" (a
    non-empty tuple), so nothing downstream ever flagged it as
    unrecognized either. See ``response_diagnostics.classify_erros_array``
    for the new explicit empty/decoded/unrecognized distinction.

    Deliberately tolerant, never raises: an unparseable/unexpected shape
    (not JSON, not an object, no ``erros`` array, a non-list ``erros``, a
    non-object item) simply yields fewer/no entries — this is diagnostics
    only and must never risk being mistaken for classification (outcome/
    state are decided entirely by HTTP status elsewhere, see
    ``responses.py`` — unchanged by this function's result).

    Every scalar value that is not literally a string is dropped (mapped
    to ``None``) rather than coerced, so a hostile/malformed field (e.g.
    an object or array where a string is documented) can never smuggle
    structured data through as if it were text.
    """
    try:
        payload = _parse_json_object(body)
    except WireFormatError:
        return ()
    raw_errors = payload.get(FIELD_ERROR_LIST)
    if not isinstance(raw_errors, list):
        return ()

    def _text(value) -> str | None:
        return value if isinstance(value, str) else None

    errors = []
    for item in raw_errors:
        if not isinstance(item, dict):
            continue
        errors.append(SefinValidationError(
            codigo=_text(_ci_field(item, FIELD_ERROR_CODE)),
            descricao=_text(_ci_field(item, FIELD_ERROR_DESCRIPTION)),
            complemento=_text(_ci_field(item, FIELD_ERROR_COMPLEMENT)),
            mensagem=_text(_ci_field(item, "mensagem")),
            parametros=_raw_parametros(_ci_field(item, "parametros")),
        ))
    return tuple(errors)
