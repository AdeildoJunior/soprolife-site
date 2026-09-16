"""M29 — resolves a fully-bound ``RestrictedNfseProvider`` for exactly one
document, called ONLY from ``nfse.operate()`` and ONLY once
``nfse_providers.get_provider()`` has already returned a
``RestrictedProviderPending`` marker (every STRUCTURAL/settings-only gate
already satisfied). This module never trusts that first check alone: every
gate is re-evaluated here against the database and the specific document
being issued, exactly like ``HttpxRestrictedTransport._assert_gate_open()``
re-checks the network gate on every call rather than trusting a caller.

Minimum required gates (mission section F), all fail-closed:
- environment == 'restricted' (re-checked, never assumed);
- real provider explicitly enabled (``nfse_real_enabled``);
- restricted network explicitly enabled (``nfse_restricted_network_enabled``);
- valid fiscal policy for this document's flow/environment;
- active NationalDpsConfiguration for this document's OWN competence date
  (never "today's", so a historical redo of a past competence always
  resolves the configuration that was actually active then);
- certificate path configured, credential secret present, certificate
  readable and not expired;
- private artifact storage configured;
- recipient identity (CPF) known;
- structured service description determinable (never guessed);
- structured, supported service LOCATION determinable from the immutable
  preparation snapshot (M31, never guessed/defaulted — see
  ``service_location.py``).

``M15_NFSE_RESTRICTED_NETWORK_ENABLED`` stays ``False`` by default (see
``app/config.py``), so in the M29 default configuration this module never
gets past the first re-check — which is exactly the point.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...config import Settings
from ...models import FiscalDocument, FiscalPreparation, Person, SpirometryExam
from . import dps_numbering, fiscal_config
from .dps_builder import Recipient
from .identifiers import DpsIdComponents, InvalidIdentifierError
from .provider import RestrictedIssueContext, RestrictedNfseProvider
from .readiness import compute_provider_readiness
from .service_description import ServiceDescriptionUndetermined, spirometry_service_description
from .service_location import (
    ServiceLocationUndetermined,
    ServiceLocationUnsupported,
    spirometry_service_municipio_ibge,
)
from .signer import LoadedCertificate, SignatureError, load_pkcs12_certificate, private_key_pem
from .transport import HttpxRestrictedTransport, RestrictedTransport


def fail(code: str, status: int = 503):
    raise HTTPException(status, detail={"codigo": code})


def _load_certificate(settings: Settings) -> LoadedCertificate:
    # M45 — closes two unhandled-exception gaps found while diagnosing a
    # local HTTP 500 on DPS #9 (2026-09-16): a path of ``None`` raises
    # ``TypeError`` from ``Path(None)`` (not ``OSError``), and a missing
    # password raises ``ValueError`` from
    # ``resolved_nfse_restricted_certificate_password()`` — neither was
    # caught here, so either would have escaped as an unhandled 500
    # instead of the same clean, fail-closed blocker every other
    # misconfiguration in this function already produces. `readiness`
    # (called by every real caller before this) is expected to catch both
    # first, but this function must degrade the same way on its own if it
    # is ever reached with either missing — never propagate a raw
    # exception past this boundary.
    if settings.nfse_restricted_certificate_path is None:
        fail("restricted_certificate_path_not_configured")
    try:
        raw = Path(settings.nfse_restricted_certificate_path).read_bytes()
    except OSError:
        fail("restricted_certificate_path_unreadable")
    try:
        password = settings.resolved_nfse_restricted_certificate_password()
    except ValueError:
        fail("restricted_certificate_password_missing")
    try:
        return load_pkcs12_certificate(raw, password)
    except SignatureError:
        fail("restricted_certificate_unreadable")


def _recipient(db: Session, preparation: FiscalPreparation) -> Recipient:
    person = db.get(Person, preparation.recipient_person_id) if preparation.recipient_person_id else None
    if person is None or not person.cpf:
        fail("recipient_identity_not_supplied", 409)
    try:
        return Recipient(nome=person.nome_completo, cpf=person.cpf)
    except Exception:  # DpsBuildError/InvalidIdentifierError — malformed stored CPF
        fail("recipient_identity_invalid", 409)


def _service_description(db: Session, doc: FiscalDocument) -> str:
    exam = db.get(SpirometryExam, doc.spirometry_exam_id)
    try:
        return spirometry_service_description(exam.broncodilatador if exam else None)
    except ServiceDescriptionUndetermined:
        fail("service_description_undetermined", 409)


def _service_location(preparation: FiscalPreparation) -> str:
    # M31 — from the IMMUTABLE preparation snapshot, never a live re-read of
    # the exam: a service municipality changed after preparation is already
    # caught upstream by nfse.operate()'s own preparation-staleness check
    # (preparation.fingerprint vs. a fresh evaluate()), exactly like
    # amount/policy/flow.
    try:
        return spirometry_service_municipio_ibge(preparation.service_municipio_ibge)
    except ServiceLocationUndetermined:
        fail("service_location_missing", 409)
    except ServiceLocationUnsupported:
        fail("service_location_unsupported", 409)


def resolve_restricted_provider(db: Session, settings: Settings, doc: FiscalDocument,
                                preparation: FiscalPreparation, actor: str, *,
                                transport: RestrictedTransport | None = None) -> RestrictedNfseProvider:
    """Builds a ``RestrictedNfseProvider`` bound to exactly this document, or
    fails closed with a specific blocker code. ``transport`` is a test-only
    seam (a ``FakeTransport``); production callers never pass it, and the
    default is always the real, gate-checked ``HttpxRestrictedTransport``.
    """
    if settings.nfse_environment != "restricted":
        fail("provider_environment_mismatch")
    if not settings.nfse_real_enabled:
        fail("real_provider_disabled")
    if not settings.nfse_restricted_network_enabled:
        fail("restricted_network_gate_disabled")

    readiness = compute_provider_readiness(db, settings, environment="restricted")
    # `readiness.blockers` already covers: certificate presence/validity,
    # secret presence, base URL, network gate, fiscal policy completeness,
    # artifact storage, and whether ANY national configuration exists for
    # TODAY. It is the single source of truth for every gate it names; only
    # the per-document national-configuration-as-of-competence check below
    # is re-derived here, since a document's own competence date may differ
    # from today.
    if readiness.blockers:
        fail(sorted(readiness.blockers)[0])

    national_config = fiscal_config.resolve_active_configuration(
        db, environment="restricted", as_of=preparation.competence)
    if national_config is None:
        fail("national_dps_configuration_not_defined_for_any_real_document")

    recipient = _recipient(db, preparation)
    _service_description(db, doc)  # validated here too: fail closed before ever returning a provider
    municipio_prestacao_ibge = _service_location(preparation)
    certificate = _load_certificate(settings)

    # M36 — durable, globally-unique numero_dps, keyed by document_id alone:
    # the SAME call, for the SAME document, on any later attempt (retry,
    # reconcile #2/#3, ...) always returns the number already allocated on
    # the first — never a fresh one derived from this attempt's own
    # FiscalAttempt.number (that was the M36 root cause). See
    # dps_numbering.allocate_dps_number().
    dps_number = dps_numbering.allocate_dps_number(
        db, document_id=doc.id, codigo_municipio=national_config.issuer_municipio_ibge,
        tipo_inscricao_federal=2, inscricao_federal=national_config.issuer_cnpj,
        serie_dps="00001",
    )
    try:
        dps_id = DpsIdComponents(
            codigo_municipio=national_config.issuer_municipio_ibge, tipo_inscricao_federal=2,
            inscricao_federal=national_config.issuer_cnpj, serie_dps="00001",
            numero_dps=str(dps_number).rjust(15, "0"),
        )
    except InvalidIdentifierError:
        fail("national_dps_configuration_invalid")

    context = RestrictedIssueContext(
        config=national_config, dps_id=dps_id, recipient=recipient,
        ver_aplic="soprolife-m29-0.1", numero_dps_display=str(dps_number), serie_dps_display="1",
        certificate=certificate, municipio_prestacao_ibge=municipio_prestacao_ibge,
    )
    live_transport = transport or HttpxRestrictedTransport(
        base_url=settings.nfse_restricted_base_url,
        network_enabled=settings.nfse_restricted_network_enabled,
        environment=settings.nfse_environment,
        mtls_certificate_pem=certificate.certificate_pem,
        mtls_key_pem=private_key_pem(certificate),
    )
    return RestrictedNfseProvider(transport=live_transport, context=context, environment="restricted")


def resolve_service_description(db: Session, doc: FiscalDocument) -> str:
    """Public wrapper so ``nfse.operate()`` can compute the real,
    structured service description for the outbound ``ProviderRequest``
    BEFORE calling ``resolve_restricted_provider`` (which needs it too, for
    its own fail-closed validation) — computed once, used for both.
    """
    return _service_description(db, doc)
