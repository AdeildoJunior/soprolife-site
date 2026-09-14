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
from .config import NationalDpsConfiguration
from .dps_builder import DpsInput, Recipient, build_dps_element, serialize_dps
from .identifiers import (DpsIdComponents, InvalidIdentifierError, build_dps_id,
                          extract_nfse_access_key, find_nfse_access_key_best_effort)
from .responses import classify_issue_response, classify_reconcile_response, to_provider_outcome
from .signer import LoadedCertificate, sign_dps, verify_dps_signature
from .transport import PATH_GET_DPS, PATH_ISSUE_NFSE, RestrictedTransport, TransportRequest
from .xsd_validation import XsdValidationError, validate_dps_xml


class RestrictedProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class RestrictedIssueContext:
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


class RestrictedNfseProvider:
    """Not registered anywhere by default — see module docstring."""

    name = "restricted"

    def __init__(self, *, transport: RestrictedTransport, context: RestrictedIssueContext,
                 environment: str = "restricted"):
        if environment != "restricted":
            raise RestrictedProviderError("RestrictedNfseProvider só opera em environment='restricted'.")
        self.environment = environment
        self._transport = transport
        self._context = context

    def _build_signed_dps(self, request: ProviderRequest) -> bytes:
        ctx = self._context
        data = DpsInput(
            config=ctx.config,
            dps_id=ctx.dps_id,
            dh_emi=datetime.now(timezone.utc),
            ver_aplic=ctx.ver_aplic,
            numero_dps_display=ctx.numero_dps_display,
            serie_dps_display=ctx.serie_dps_display,
            competencia=datetime.fromisoformat(request.competence).date(),
            tomador=ctx.recipient,
            descricao_servico=request.description,
            valor_servico=Decimal(request.amount),
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
            raise RestrictedProviderError(f"DPS construída não é válida contra o XSD: {exc}") from exc

        exc: Exception | None = None
        response = None
        try:
            response = self._transport.send(TransportRequest(method="POST", path=PATH_ISSUE_NFSE,
                                                              body=signed_xml))
        except Exception as caught:  # network/timeout/gate errors, never proof of anything
            exc = caught

        # A 2xx alone is never proof of issuance — the official manual states
        # the success body IS the generated NFS-e XML (§1.3.2.a). Anything
        # that isn't a well-formed <NFSe> with a schema-shaped access key
        # (infNFSe/@Id, TSIdNFSe) is malformed, never a silent success.
        access_key: str | None = None
        if response is not None and 200 <= response.status_code < 300 and response.body:
            try:
                access_key = extract_nfse_access_key(response.body)
            except InvalidIdentifierError:
                access_key = None
        classified = classify_issue_response(
            http_status=response.status_code if response else None, exc=exc,
            body_valid=access_key is not None,
        )
        outcome = to_provider_outcome(classified, operation="issue")
        external_id = access_key if outcome == Outcome.SIMULATED else None
        return ProviderResult(outcome, external_id)

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
                method="GET", path=PATH_GET_DPS.format(dps_id=dps_id_value)))
        except Exception as caught:
            exc = caught

        access_key: str | None = None
        if response is not None and 200 <= response.status_code < 300 and response.body:
            access_key = find_nfse_access_key_best_effort(response.body)
        classified = classify_reconcile_response(
            http_status=response.status_code if response else None, exc=exc,
            body_valid=access_key is not None,
        )
        outcome = to_provider_outcome(classified, operation=operation)
        external_id = access_key if outcome in (Outcome.SIMULATED, Outcome.CANCELLED) else None
        return ProviderResult(outcome, external_id)

    def cancel(self, request: ProviderRequest) -> ProviderResult:
        # No official event/cancellation contract has been verified as
        # sufficiently established for this foundation (see the report's
        # "intentionally unsupported operations" section) — fail closed
        # rather than construct an unverified cancellation request.
        raise RestrictedProviderError("cancelamento_nao_suportado_nesta_fundacao")
