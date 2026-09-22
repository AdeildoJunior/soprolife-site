"""OFFLINE production preflight. Proves the technical path — never opens it.

There is no transport call anywhere in this module, and no import that
could reach one: it runs the same deterministic build/sign/XSD chain the
restricted cycle already proved (``preflight.run_offline_preflight``) and
then adds the checks production needs on top of it. Running it a thousand
times sends zero bytes.

Two answers, deliberately kept apart:

``technical_ready`` — everything a machine can verify locally is in order:
the environment names production, the endpoint is the official allowlisted
one, the certificate has renewal runway, the host clock is disciplined, the
signed DPS is schema- and signature-valid with zero namespace prefixes, its
``dhEmi`` carries the São Paulo civil offset, and a fiscal configuration
resolves for the document's own competence.

``authorization_ready`` — a human has explicitly authorized this production
issuance. Nothing in this module, in the settings, in the database or in
the environment can make it true. It is a parameter with a refusing
default, and M56 never supplies it.

``network_send_allowed`` is the conjunction, and it is additionally guarded:
even both together do not open the network, because
``nfse_providers.get_provider()`` still refuses environment='production'
outright. A green preflight is a statement about readiness, never a
permission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lxml import etree
from sqlalchemy.orm import Session

from ...config import Settings
from ...services import nfse as nfse_service
from . import artifacts as artifact_storage
from . import clock as clock_module
from . import fiscal_config
from .certificate_guard import CertificateMargin, evaluate_certificate_margin
from .dps_builder import EMISSION_TIMEZONE, NFSE_NS
from .preflight import PreflightResult, run_offline_preflight
from .readiness import compute_provider_readiness
from .transport import PRODUCTION_BASE_URL, NetworkGateClosedError, assert_production_base_url

PRODUCTION_ENVIRONMENT = "production"


def count_namespace_prefixes(xml_bytes: bytes) -> int:
    """Number of distinct namespace PREFIXES anywhere in the document.

    SEFIN rule E1228 ("Uso de prefixo de namespace não permitido na área de
    dados descompactada") rejected DPS #1-#8 until M45 fixed the signer, so
    this is a regression guard with a real rejection behind it, not a
    stylistic check. Counts both a prefix an element actually resolves to
    and any prefix merely DECLARED in an nsmap — a declaration alone is
    enough to trip the rule. The default (``None``) binding is not a prefix.
    """
    root = etree.fromstring(xml_bytes)
    prefixes: set[str] = set()
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue  # comments/PIs carry no namespace
        if element.prefix:
            prefixes.add(element.prefix)
        for prefix in element.nsmap:
            if prefix:
                prefixes.add(prefix)
    return len(prefixes)


def extract_dh_emi(xml_bytes: bytes) -> str | None:
    """The literal ``dhEmi`` text as it will be transmitted, or None."""
    root = etree.fromstring(xml_bytes)
    node = root.find(f".//{{{NFSE_NS}}}dhEmi")
    return node.text if node is not None else None


def dh_emi_matches_emission_timezone(dh_emi_text: str | None, *,
                                     reference: datetime | None = None) -> bool:
    """Is ``dhEmi`` expressed in the São Paulo civil offset?

    M49 — ``dhEmi`` is typed ``TSDateTimeUTC`` in the schema, but the name
    is a misnomer: SEFIN compares the wall-clock instant, and the accepted
    DPS #10 carried the local offset. Brazil has abolished DST but the
    offset is still read from the zone rather than hard-coded to ``-03:00``,
    so a future reinstatement changes one tzdata file and not this code.

    The offset is compared for the instant the DPS itself declares (parsed
    from the timestamp), never for "now": a document built seconds before a
    transition must be judged by the rule in force when it was stamped.
    """
    if not dh_emi_text:
        return False
    try:
        parsed = datetime.fromisoformat(dh_emi_text)
    except ValueError:
        return False
    if parsed.utcoffset() is None:
        return False
    moment = reference or parsed.replace(tzinfo=None)
    expected = EMISSION_TIMEZONE.utcoffset(moment)
    return parsed.utcoffset() == expected


@dataclass(frozen=True)
class ProductionPreflightResult:
    document_id: str
    environment_is_production: bool
    production_base_url: str | None
    production_endpoint_ok: bool
    network_gate_enabled: bool
    certificate: CertificateMargin
    clock: clock_module.ClockStatus
    dps_preflight_status: str
    dps_preflight_stage: str
    xsd_and_signature_ok: bool
    dh_emi: str | None
    dh_emi_timezone_ok: bool
    namespace_prefix_count: int | None
    fiscal_config_resolved: bool
    request_fingerprint: str | None
    human_authorization_present: bool
    blockers: list[str] = field(default_factory=list)

    @property
    def technical_ready(self) -> bool:
        """Every locally verifiable condition holds. Says nothing about
        permission — see ``authorization_ready``."""
        return not self.blockers

    @property
    def authorization_ready(self) -> bool:
        return self.human_authorization_present

    @property
    def network_send_allowed(self) -> bool:
        """Never true from technical readiness alone. Even when both parts
        are true, ``get_provider()`` still refuses production — this
        property reports the preflight's verdict, it does not grant
        anything."""
        return self.technical_ready and self.authorization_ready

    def as_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "environment_is_production": self.environment_is_production,
            "production_base_url": self.production_base_url,
            "production_endpoint_ok": self.production_endpoint_ok,
            "network_gate_enabled": self.network_gate_enabled,
            "certificate": self.certificate.as_dict(),
            "clock": self.clock.as_dict(),
            "dps_preflight_status": self.dps_preflight_status,
            "dps_preflight_stage": self.dps_preflight_stage,
            "xsd_and_signature_ok": self.xsd_and_signature_ok,
            "dh_emi": self.dh_emi,
            "dh_emi_timezone_ok": self.dh_emi_timezone_ok,
            "namespace_prefix_count": self.namespace_prefix_count,
            "fiscal_config_resolved": self.fiscal_config_resolved,
            "request_fingerprint": self.request_fingerprint,
            "human_authorization_present": self.human_authorization_present,
            "technical_ready": self.technical_ready,
            "authorization_ready": self.authorization_ready,
            "network_send_allowed": self.network_send_allowed,
            "blockers": self.blockers,
        }


def _staged_signed_xml(settings: Settings, result: PreflightResult) -> bytes | None:
    """Read back the signed DPS that was actually staged on disk.

    Deliberately re-read rather than kept in memory: the bytes inspected
    below are then provably the same ones the evidence bundle holds, not a
    parallel copy that could differ from it.
    """
    signed = next((a for a in result.staged_artifacts if a.kind == "dps_signed_xml"), None)
    if signed is None:
        return None
    try:
        root = settings.resolved_fiscal_artifacts_storage_dir()
        return artifact_storage.read_artifact(root, Path(signed.relative_path))
    except Exception:
        return None


def run_production_preflight(db: Session, document_id: str, settings: Settings, actor: str, *,
                             explicit_human_production_authorization: bool = False,
                             **preflight_kwargs) -> ProductionPreflightResult:
    """Offline production preflight for exactly one document. Sends nothing.

    ``explicit_human_production_authorization`` has a refusing default and
    is never read from settings, the environment or the database — the one
    condition in this whole pipeline that no amount of correct configuration
    can satisfy. M56 never passes it as True.
    """
    blockers: list[str] = []

    # 1. Environment. A production preflight run against a restricted or mock
    # configuration is a category error, not a near miss.
    environment_is_production = settings.nfse_environment == PRODUCTION_ENVIRONMENT
    if not environment_is_production:
        blockers.append("environment_is_not_production")

    # 2. Endpoint. The constant is the only candidate; the allowlist check
    # is the same one the transport applies per call, so preflight and
    # transport can never disagree about what "correct" means.
    production_base_url: str | None = None
    try:
        production_base_url = assert_production_base_url(PRODUCTION_BASE_URL)
        production_endpoint_ok = True
    except NetworkGateClosedError as exc:
        production_endpoint_ok = False
        blockers.append(f"production_endpoint_invalid:{exc}")

    # 3. Production network gate — reported, and its absence IS a blocker
    # for readiness, but note that having it open still sends nothing here.
    network_gate_enabled = bool(settings.nfse_production_network_enabled)
    if not network_gate_enabled:
        blockers.append("production_network_gate_disabled")

    # 4. Certificate, with the production-only renewal margin. The A1 in use
    # expires 2026-11-07 and must be renewed before the first production
    # issuance (M55 decision); this is where that decision becomes a gate
    # instead of a note in a report.
    readiness = compute_provider_readiness(db, settings, environment="restricted")
    certificate = evaluate_certificate_margin(
        readiness.certificate_summary,
        min_days_remaining=settings.nfse_production_certificate_min_days)
    if not certificate.valid:
        blockers.append(f"certificate:{certificate.reason}")

    # 5. Clock. M55 measured -14.3 minutes and could only prove it from a
    # government response after the fact; this reads the kernel's own NTP
    # discipline state before anything is sent.
    clock = clock_module.read_clock_status()
    if not clock.synchronized:
        blockers.append(f"clock:{clock.reason}")

    # 6. Fiscal configuration for this document's OWN competence, never
    # today's — a historical redo must resolve the version that was actually
    # in force then.
    fiscal_config_resolved = False
    try:
        doc = nfse_service.get_document(db, document_id)
        preparation = nfse_service.latest_preparation(db, doc.id)
        if preparation is not None:
            fiscal_config_resolved = fiscal_config.resolve_active_configuration(
                db, environment=doc.environment, as_of=preparation.competence) is not None
    except Exception:
        fiscal_config_resolved = False
    if not fiscal_config_resolved:
        blockers.append("fiscal_configuration_not_resolved")

    # 7. The deterministic build/sign/XSD chain, reused verbatim. This is
    # the step that produces the signed bytes everything below inspects.
    dps_result = run_offline_preflight(db, document_id, settings, actor, **preflight_kwargs)
    xsd_and_signature_ok = dps_result.status == "ready_to_send"
    if not xsd_and_signature_ok:
        blockers.extend(f"dps_preflight:{b}" for b in dps_result.blockers)

    # 8. Inspect the signed bytes that were actually staged.
    dh_emi: str | None = None
    dh_emi_timezone_ok = False
    namespace_prefix_count: int | None = None
    signed_xml = _staged_signed_xml(settings, dps_result) if xsd_and_signature_ok else None
    if signed_xml is None:
        if xsd_and_signature_ok:
            blockers.append("signed_dps_not_retrievable")
    else:
        try:
            namespace_prefix_count = count_namespace_prefixes(signed_xml)
            dh_emi = extract_dh_emi(signed_xml)
        except etree.XMLSyntaxError:
            blockers.append("signed_dps_not_parseable")
        else:
            dh_emi_timezone_ok = dh_emi_matches_emission_timezone(dh_emi)
            if namespace_prefix_count != 0:
                # E1228 again. Never a warning: this exact condition is what
                # eight consecutive DPS rejections were made of.
                blockers.append("dps_uses_namespace_prefix")
            if not dh_emi_timezone_ok:
                blockers.append("dh_emi_not_in_emission_timezone")

    # 9. Human authorization. Last, and unreachable by configuration.
    human_authorization_present = bool(explicit_human_production_authorization)
    if not human_authorization_present:
        blockers.append("explicit_human_authorization_absent")

    # `technical_ready` must not be dragged down by the authorization
    # blocker — the whole point is to report "technically ready, awaiting a
    # human" as a distinct, legible state. So authorization is tracked
    # separately and excluded from the technical blocker list.
    technical_blockers = sorted(set(b for b in blockers
                                    if b != "explicit_human_authorization_absent"))

    return ProductionPreflightResult(
        document_id=document_id,
        environment_is_production=environment_is_production,
        production_base_url=production_base_url,
        production_endpoint_ok=production_endpoint_ok,
        network_gate_enabled=network_gate_enabled,
        certificate=certificate,
        clock=clock,
        dps_preflight_status=dps_result.status,
        dps_preflight_stage=dps_result.stage_reached,
        xsd_and_signature_ok=xsd_and_signature_ok,
        dh_emi=dh_emi,
        dh_emi_timezone_ok=dh_emi_timezone_ok,
        namespace_prefix_count=namespace_prefix_count,
        fiscal_config_resolved=fiscal_config_resolved,
        request_fingerprint=dps_result.request_fingerprint,
        human_authorization_present=human_authorization_present,
        blockers=technical_blockers,
    )
