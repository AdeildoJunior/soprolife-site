"""Restricted NFS-e provider: composes builder + signer + transport +
response classification behind the same ``NfseProvider`` shape used by the
mock queue (``app.services.nfse_providers.NfseProvider``).

INTENTIONALLY NOT WIRED into ``app.services.nfse_providers.get_provider()``.
That function is the M26-approved fail-closed gate and still unconditionally
refuses every non-mock environment; this class exists so the restricted
pipeline is fully buildable and testable end-to-end (with a fake transport)
without adding any live activation path. Wiring it into the real dispatch —
and therefore into ``nfse.operate()``'s hard ``provider.environment != 'mock'``
gate — is a deliberate future step requiring its own review, not part of
this foundation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from ..nfse_providers import Outcome, ProviderRequest, ProviderResult
from .config import NationalDpsConfiguration, tp_amb_for_environment
from .dps_builder import DpsInput, Recipient, build_dps_element, serialize_dps
from .error_sanitizer import sanitize_sefin_errors
from .identifiers import DpsIdComponents, build_dps_id
from .response_diagnostics import (classify_erros_array, decode_documented_error_fields,
                                   summarize_response_shape)
from .responses import (TransportOutcome, classify_issue_response, classify_reconcile_response,
                        safe_diagnostic_code, to_provider_outcome)
from .signer import LoadedCertificate, sign_dps, verify_dps_signature
from .transport import PATH_GET_DPS, PATH_ISSUE_NFSE, NationalTransport, TransportRequest
from .wire import (JSON_ACCEPT_HEADERS, JSON_REQUEST_HEADERS, WireFormatError,
                   build_issue_request_body, decode_nfse_document_response,
                   decode_nfse_success_envelope, extract_processing_timestamp,
                   find_nfse_access_key_in_response, looks_like_nfse_envelope)
from .xsd_validation import XsdValidationError, validate_dps_xml

# M40 — a 4xx/5xx body is only ever worth parsing for structured detail on
# these two transport outcomes; a 2xx (however malformed) is never treated
# as if it might also be an error envelope, and a timeout/connection error
# has no body to parse in the first place.
_ERROR_BODY_OUTCOMES = (TransportOutcome.HTTP_CLIENT_ERROR, TransportOutcome.HTTP_SERVER_ERROR)


class NationalProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class NationalIssueContext:
    """Everything needed to build+sign+send one DPS, beyond the generic
    ``ProviderRequest`` the mock queue already carries. The queue's own
    ``ProviderRequest`` intentionally has no fiscal/tax fields — those come
    only from ``NationalDpsConfiguration`` (a versioned, explicit contract),
    never invented here.
    """
    config: NationalDpsConfiguration
    dps_id: DpsIdComponents
    recipient: Recipient
    ver_aplic: str
    numero_dps_display: str
    serie_dps_display: str
    certificate: LoadedCertificate
    # M31 — per-document service location (TCLocPrest/cLocPrestacao), already
    # validated by the caller (dispatch.resolve_national_provider) via
    # service_location.spirometry_service_municipio_ibge. Never part of
    # ``config``: see the M31 note on NationalDpsConfiguration.
    municipio_prestacao_ibge: str


# M56 — the two environments this provider may be bound to. The provider
# itself is environment-agnostic by construction: build -> sign -> POST ->
# parse -> keep evidence is identical fiscal work whether tpAmb says
# homologation or production, and duplicating it for production would mean
# two parsers to keep in step with SEFIN instead of one. WHERE the request
# goes, and whether it may go at all, is decided entirely by the transport
# handed in here (``HttpxRestrictedTransport`` vs ``HttpxProductionTransport``,
# each with its own independent gate) — never by this class.
#
# Being constructible for production is NOT the same as being reachable:
# ``nfse_providers.get_provider()`` still refuses environment='production'
# outright, so ``nfse.operate()`` has no path to one. See
# test_nfse_m56_production_gates.py.
SUPPORTED_ENVIRONMENTS = ("restricted", "production")


class NationalNfseProvider:
    """The ONE provider for the national NFS-e API, in either real environment.

    M59 renamed this from ``RestrictedNfseProvider``. The old name had been
    wrong since M56, when the class started accepting
    ``environment='production'``: it does not implement Produção Restrita,
    it implements the national API, and which environment it addresses is a
    constructor argument.

    There is deliberately no second, production-shaped implementation. Build
    -> sign -> POST -> parse -> keep evidence is identical fiscal work under
    tpAmb=1 and tpAmb=2, so a parallel class would mean two XML builders,
    two signers, two response parsers, two idempotency rules and two
    reconciliation paths to keep in step with SEFIN — and the production one
    would be the untested copy. What differs between the environments is
    exactly two things, and neither lives in this class: the tpAmb the
    builder derives from ``self.environment``, and the transport handed in
    here (``HttpxRestrictedTransport`` vs ``HttpxProductionTransport``, each
    with its own independent, per-call gate).
    """

    def __init__(self, *, transport: NationalTransport, context: NationalIssueContext,
                 environment: str = "restricted"):
        if environment not in SUPPORTED_ENVIRONMENTS:
            raise NationalProviderError(
                "NationalNfseProvider só opera em environment='restricted' ou 'production'.")
        self.environment = environment
        # Kept in lockstep with `environment` so `nfse.operate()`'s
        # name-vs-environment cross-check cannot be satisfied by a provider
        # bound to the other one.
        self.name = environment
        self._transport = transport
        self._context = context

    @property
    def expected_tp_amb(self) -> int:
        """The tpAmb this provider's requests carry, and therefore the one
        its responses must declare. Derived from the same single source of
        truth the builder uses, so the request and the cross-check on its
        response can never disagree."""
        return tp_amb_for_environment(self.environment)

    def _build_signed_dps(self, request: ProviderRequest) -> bytes:
        ctx = self._context
        data = DpsInput(
            config=ctx.config,
            # M59 — the provider's OWN binding decides tpAmb. A provider bound
            # to production builds tpAmb=1, one bound to restricted builds
            # tpAmb=2, and the builder refuses if the resolved configuration
            # disagrees with either.
            environment=self.environment,
            dps_id=ctx.dps_id,
            dh_emi=datetime.now(timezone.utc),
            ver_aplic=ctx.ver_aplic,
            numero_dps_display=ctx.numero_dps_display,
            serie_dps_display=ctx.serie_dps_display,
            competencia=datetime.fromisoformat(request.competence).date(),
            tomador=ctx.recipient,
            descricao_servico=request.description,
            valor_servico=Decimal(request.amount),
            municipio_prestacao_ibge=ctx.municipio_prestacao_ibge,
        )
        root = build_dps_element(data)
        signed = sign_dps(root, ctx.certificate)
        verify_dps_signature(signed, ctx.certificate.certificate_pem)
        signed_xml = serialize_dps(signed)
        validate_dps_xml(signed_xml)  # fail closed: never send a schema-invalid DPS
        return signed_xml

    def issue(self, request: ProviderRequest) -> ProviderResult:
        try:
            signed_xml = self._build_signed_dps(request)
        except XsdValidationError as exc:
            raise NationalProviderError(f"DPS construída não é válida contra o XSD: {exc}") from exc

        # M38 — the documented SEFIN contract is JSON in / JSON out, never raw
        # XML: the body is {"dpsXmlGZipB64": gzip+base64(signed XML)} with
        # Content-Type/Accept: application/json. Sending the signed XML
        # directly is what produced HTTP 415 on DPS #3. The signed bytes are
        # compressed as-is — never re-parsed or re-serialized, which would
        # break the XMLDSig digest.
        #
        # Encoded OUTSIDE the try below on purpose: a failure here is a local
        # bug, not a transport event, and must raise instead of being
        # misclassified as a connection error (i.e. as UNCERTAIN, which would
        # then block the document behind a reconciliation it never needed).
        request_body = build_issue_request_body(signed_xml)

        exc: Exception | None = None
        response = None
        try:
            response = self._transport.send(TransportRequest(
                method="POST", path=PATH_ISSUE_NFSE, body=request_body,
                headers=dict(JSON_REQUEST_HEADERS)))
        except Exception as caught:  # network/timeout/gate errors, never proof of anything
            exc = caught

        # A 2xx alone is never proof of issuance. The success envelope
        # (NFSePostResponseSucesso) must parse as JSON, carry both chaveAcesso
        # and nfseXmlGZipB64, roundtrip base64->gzip into a well-formed <NFSe>
        # with a schema-shaped infNFSe/@Id (TSIdNFSe), and both access keys
        # must agree. Anything less is malformed, never a silent success.
        access_key: str | None = None
        returned_document: bytes | None = None
        if response is not None and 200 <= response.status_code < 300 and response.body:
            try:
                decoded = decode_nfse_success_envelope(
                    response.body, expected_tp_amb=self.expected_tp_amb)
            except WireFormatError:
                access_key = None
            else:
                access_key = decoded.access_key
                # M55 — keep the document the government actually returned, so a
                # success leaves the same forensic trail a rejection already did.
                returned_document = decoded.nfse_xml
        classified = classify_issue_response(
            http_status=response.status_code if response else None, exc=exc,
            body_valid=access_key is not None,
        )
        outcome = to_provider_outcome(classified, operation="issue")
        external_id = access_key if outcome == Outcome.ISSUED else None
        # M40/M41/M44 — the ONLY place a 4xx/5xx body is ever parsed. Never
        # changes `classified`/`outcome` above (those are HTTP-status-only,
        # unchanged since M35): this is purely additive, already-sanitized
        # diagnostic detail for a human to read, never consulted by the
        # state machine.
        validation_errors = None
        response_shape = None
        # M55 — summarize the SHAPE of every response, 2xx included. This block
        # used to run only for 4xx/5xx, so a 201 whose body we failed to decode
        # recorded nothing at all: no content_type, no sha256, no top-level key
        # names. That is precisely why `provider_malformed_response:http_201`
        # was undiagnosable from the audit trail alone and needed four
        # missions and a live re-query to explain. Shape is bounded metadata
        # (type/length/hash/key NAMES) — never body content, never PII — and,
        # like every other diagnostic here, never touches `classified`/
        # `outcome`.
        if response is not None and classified.transport_outcome not in _ERROR_BODY_OUTCOMES:
            success_shape = summarize_response_shape(response.body or b"", response.content_type)
            response_shape = {
                "content_type": success_shape.content_type,
                "content_length": success_shape.content_length,
                "sha256": success_shape.sha256,
                "body_kind": success_shape.body_kind,
                "top_level_keys": success_shape.top_level_keys,
            }
        if response is not None and classified.transport_outcome in _ERROR_BODY_OUTCOMES:
            # M41 — captured for EVERY 4xx/5xx, even one with no usable
            # erros[]/ResponseErro (DPS #5's exact case): shape/hash/keys
            # only, never content, so a future blind 400 is no longer blind.
            shape = summarize_response_shape(response.body or b"", response.content_type)
            response_shape = {
                "content_type": shape.content_type,
                "content_length": shape.content_length,
                "sha256": shape.sha256,
                "body_kind": shape.body_kind,
                "top_level_keys": shape.top_level_keys,
            }
            if response.body:
                # M44 — the explicit empty/decoded/unrecognized distinction
                # DPS #6/#7 needed: an `erros[]` array can be present with
                # items that are structurally real but simply didn't decode
                # (the bug this milestone fixes) versus genuinely empty.
                erros_summary = classify_erros_array(response.body)
                response_shape["erros_status"] = erros_summary.status
                response_shape["erros_count"] = erros_summary.item_count
                response_shape["erros_item_field_names"] = erros_summary.item_field_names
                sanitized = sanitize_sefin_errors(decode_documented_error_fields(response.body))
                if sanitized:
                    validation_errors = tuple(
                        {"codigo": e.codigo, "descricao": e.descricao, "complemento": e.complemento,
                         "mensagem": e.mensagem, "erro": e.erro, "parametros": e.parametros}
                        for e in sanitized
                    )
        # M55 — Sefin's own processing timestamp, read from the documented
        # top-level `dataHoraProcessamento`. A plain timestamp: no PII, no
        # payload. Absent or malformed simply yields None.
        processed_at = extract_processing_timestamp(response.body) if response is not None else None
        return ProviderResult(outcome, external_id, safe_diagnostic_code(classified),
                              validation_errors, response_shape,
                              submitted_document=signed_xml,
                              returned_document=returned_document,
                              provider_processed_at=processed_at)

    def query(self, request: ProviderRequest, operation: str) -> ProviderResult:
        # Reconciliation/query is keyed by the OFFICIAL DPS identifier
        # (TSIdDPS) this provider itself built for the original submission —
        # never `request.operation_id` (an internal idempotency UUID with no
        # fiscal meaning to the government API).
        dps_id_value = build_dps_id(self._context.dps_id)
        exc: Exception | None = None
        response = None
        try:
            response = self._transport.send(TransportRequest(
                method="GET", path=PATH_GET_DPS.format(dps_id=dps_id_value),
                headers=dict(JSON_ACCEPT_HEADERS)))
        except Exception as caught:
            exc = caught

        # M38 — the service produces application/json, so this path now decodes
        # the nfseXmlGZipB64 envelope when the body carries it (a compressed
        # key is invisible to the legacy raw-byte scan). See
        # wire.find_nfse_access_key_in_response for the audit of what the GET
        # contract does and does not prove.
        access_key: str | None = None
        returned_document: bytes | None = None
        if response is not None and 200 <= response.status_code < 300 and response.body:
            # M59 — when the body IS the documented envelope, decode it
            # properly instead of scanning it. Two things were being lost
            # here, and both matter once production is wired:
            #
            #  - the NFS-e document itself. `issue` has kept it since M55,
            #    `query` threw it away, so a document settled by
            #    RECONCILE ended up with no `nfse_xml` artifact — and the
            #    M58 fiscal-validity contract requires one. A real
            #    production issuance recovered from a timeout would have
            #    been permanently, silently unusable.
            #  - the tipoAmbiente cross-check, which `issue` applies and
            #    this path did not, leaving reconciliation as the way in
            #    for an answer from the wrong environment.
            #
            # The tolerant scan stays as the fallback for the undocumented
            # GET shapes (see wire.find_nfse_access_key_in_response and the
            # M53 audit note) — this only changes what happens when the
            # response is the envelope we do have a contract for.
            if looks_like_nfse_envelope(response.body):
                try:
                    decoded = decode_nfse_document_response(
                        response.body, expected_tp_amb=self.expected_tp_amb)
                except WireFormatError:
                    access_key = None   # fail closed: a broken envelope is not proof
                else:
                    access_key = decoded.access_key
                    returned_document = decoded.nfse_xml
            else:
                access_key = find_nfse_access_key_in_response(response.body)
        classified = classify_reconcile_response(
            http_status=response.status_code if response else None, exc=exc,
            body_valid=access_key is not None,
        )
        outcome = to_provider_outcome(classified, operation=operation)
        external_id = access_key if outcome in (Outcome.ISSUED, Outcome.CANCELLED) else None
        processed_at = extract_processing_timestamp(response.body) if response is not None else None
        return ProviderResult(outcome, external_id, safe_diagnostic_code(classified),
                              returned_document=returned_document,
                              provider_processed_at=processed_at)

    def cancel(self, request: ProviderRequest) -> ProviderResult:
        # No official event/cancellation contract has been verified as
        # sufficiently established for this foundation (see the report's
        # "intentionally unsupported operations" section) — fail closed
        # rather than construct an unverified cancellation request.
        raise NationalProviderError("cancelamento_nao_suportado_nesta_fundacao")


# M59 — compatibility aliases under the pre-rename names. Kept so that any
# caller outside this tree (an operator's one-off script, a saved notebook)
# keeps importing successfully; nothing inside the repository uses them.
RestrictedNfseProvider = NationalNfseProvider
RestrictedIssueContext = NationalIssueContext
RestrictedProviderError = NationalProviderError
