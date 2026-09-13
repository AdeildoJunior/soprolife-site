"""Deterministic OFFLINE preflight: FiscalDocument → ... → READY_TO_SEND or an
exact blocker. Never sends a network request — there is no transport call
anywhere in this module, only the same builder/signer/XSD chain the
restricted provider (``provider.py``) already uses and that M27 already
proved schema-valid.

Pipeline, in order, matching the mission's own sequence:
    eligible FiscalDocument → current FiscalPreparation → national DPS
    configuration → deterministic unsigned DPS XML → XSD validation →
    signature boundary → request fingerprint → private artifact staging →
    READY_TO_SEND

Each stage either advances or returns the single, first, deterministic
blocker — this function never accumulates a partial pipeline result past the
first failure, so a caller always knows exactly which stage to fix next.

``national_config``/``recipient``/``certificate`` are never invented here.
Today NO real document has a national configuration or recipient identity
source wired up (that is future admin-UI work, out of scope for this
foundation — see readiness.py), so calling this for a real document without
supplying them always stops at NATIONAL_CONFIG. Tests exercise the full
chain by supplying synthetic values explicitly, the same pattern M27 already
used in ``test_nfse_national_provider.py``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from ...config import Settings
from ...models import FiscalArtifact, Person, SpirometryExam
from ...services import nfse
from ...services.idempotency import payload_fingerprint
from . import artifacts as artifact_storage
from . import fiscal_config
from .config import NationalDpsConfiguration
from .dps_builder import DpsBuildError, DpsInput, Recipient, build_dps_element, serialize_dps
from .identifiers import DpsIdComponents, InvalidIdentifierError
from .service_description import ServiceDescriptionUndetermined, spirometry_service_description
from .signer import LoadedCertificate, SignatureError, load_pkcs12_certificate, sign_dps, verify_dps_signature
from .xsd_validation import XsdValidationError, validate_dps_xml


class Stage:
    ELIGIBILITY = "eligibility"
    PREPARATION = "preparation"
    NATIONAL_CONFIG = "national_config"
    DPS_BUILD = "dps_build"
    XSD_VALIDATION = "xsd_validation"
    SIGNATURE = "signature"
    ARTIFACT_STAGING = "artifact_staging"
    READY = "ready"


@dataclass(frozen=True)
class StagedArtifact:
    kind: str
    sha256: str
    size_bytes: int
    relative_path: str


@dataclass(frozen=True)
class PreflightResult:
    document_id: str
    status: str  # "ready_to_send" | "blocked"
    stage_reached: str
    blockers: list[str] = field(default_factory=list)
    request_fingerprint: str | None = None
    staged_artifacts: list[StagedArtifact] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "status": self.status,
            "stage_reached": self.stage_reached,
            "blockers": self.blockers,
            "request_fingerprint": self.request_fingerprint,
            "staged_artifacts": [
                {"kind": a.kind, "sha256": a.sha256, "size_bytes": a.size_bytes,
                 "relative_path": a.relative_path}
                for a in self.staged_artifacts
            ],
        }


def _blocked(document_id: str, stage: str, blockers: list[str]) -> PreflightResult:
    return PreflightResult(document_id=document_id, status="blocked",
                           stage_reached=stage, blockers=sorted(set(blockers)))


def _try_load_settings_certificate(settings: Settings) -> LoadedCertificate | None:
    """Best-effort load from the configured external certificate path —
    never raises: an absent/unreadable/wrong-password certificate is just
    another reason to stay blocked at ``Stage.SIGNATURE``, exactly like the
    caller explicitly passing ``certificate=None`` always has been. The
    password itself never appears in any return value or exception here.
    """
    path = settings.nfse_restricted_certificate_path
    password = settings.nfse_restricted_certificate_password
    if not path or not password:
        return None
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    try:
        return load_pkcs12_certificate(raw, password)
    except SignatureError:
        return None


def run_offline_preflight(db: Session, document_id: str, settings: Settings, actor: str, *,
                          national_config: NationalDpsConfiguration | None = None,
                          recipient: Recipient | None = None,
                          numero_dps_display: str = "1", serie_dps_display: str = "1",
                          ver_aplic: str = "sl-preflight-0.1",
                          certificate: LoadedCertificate | None = None) -> PreflightResult:
    doc = nfse.get_document(db, document_id)

    # 1. Eligibility — reuse the exact state/eligibility contract nfse.py already enforces.
    if doc.eligibility != "eligible" or doc.state not in {"pending", "failed"}:
        reasons = doc.blocking_reasons or ["document_not_pending_eligible"]
        return _blocked(doc.id, Stage.ELIGIBILITY, reasons)

    # 2. Preparation — must exist and still match the current evaluation (no
    # stale money/policy silently carried forward, same rule as nfse.operate()).
    preparation = nfse.latest_preparation(db, doc.id)
    if not preparation:
        return _blocked(doc.id, Stage.PREPARATION, ["preparation_required"])
    current = nfse.evaluate(db, doc.spirometry_exam_id, doc.environment)
    if current["blocking_reasons"] or preparation.fingerprint != payload_fingerprint(current):
        return _blocked(doc.id, Stage.PREPARATION,
                        current["blocking_reasons"] + ["preparation_stale"])

    # 3. National DPS configuration — the concrete restricted-layout contract.
    # M29: resolved from the real, versioned admin-managed table
    # (fiscal_config.py) by the document's OWN competence date whenever the
    # caller does not explicitly supply one (tests still can, to exercise a
    # specific/synthetic contract in isolation without touching the DB).
    if national_config is None:
        national_config = fiscal_config.resolve_active_configuration(
            db, environment=doc.environment, as_of=preparation.competence)
    if national_config is None:
        return _blocked(doc.id, Stage.NATIONAL_CONFIG,
                        ["national_dps_configuration_not_defined_for_any_real_document"])
    if recipient is None:
        person = db.get(Person, preparation.recipient_person_id) if preparation.recipient_person_id else None
        if person and person.cpf:
            try:
                recipient = Recipient(nome=person.nome_completo, cpf=person.cpf)
            except DpsBuildError:
                return _blocked(doc.id, Stage.NATIONAL_CONFIG, ["recipient_identity_invalid"])
    if recipient is None:
        return _blocked(doc.id, Stage.NATIONAL_CONFIG, ["recipient_identity_not_supplied"])

    # 4. Deterministic DPS build (unsigned). The service description is
    # NEVER the mock-era free-text `preparation.description` — it comes only
    # from the structured, exam-level `broncodilatador` attribute (M29,
    # section B), failing closed rather than guessing the variant.
    exam = db.get(SpirometryExam, doc.spirometry_exam_id)
    try:
        descricao_servico = spirometry_service_description(exam.broncodilatador if exam else None)
    except ServiceDescriptionUndetermined:
        return _blocked(doc.id, Stage.DPS_BUILD, ["service_description_undetermined"])
    try:
        dps_id = DpsIdComponents(
            codigo_municipio=national_config.issuer_municipio_ibge, tipo_inscricao_federal=2,
            inscricao_federal=national_config.issuer_cnpj, serie_dps=serie_dps_display.rjust(5, "0"),
            numero_dps=numero_dps_display.rjust(15, "0"),
        )
        data = DpsInput(
            config=national_config, dps_id=dps_id, dh_emi=datetime.now(timezone.utc),
            ver_aplic=ver_aplic, numero_dps_display=numero_dps_display,
            serie_dps_display=serie_dps_display, competencia=preparation.competence,
            tomador=recipient, descricao_servico=descricao_servico,
            valor_servico=Decimal(str(preparation.amount_snapshot)),
        )
        unsigned_root = build_dps_element(data)
    except (DpsBuildError, InvalidIdentifierError) as exc:
        return _blocked(doc.id, Stage.DPS_BUILD, [f"dps_build_error:{type(exc).__name__}"])
    unsigned_xml = serialize_dps(unsigned_root)

    # 5. XSD validation (unsigned) — a schema-invalid draft never reaches signing.
    try:
        validate_dps_xml(unsigned_xml)
    except XsdValidationError:
        return _blocked(doc.id, Stage.XSD_VALIDATION, ["dps_schema_invalid"])

    staged: list[StagedArtifact] = []
    signed_xml = unsigned_xml
    if certificate is None:
        certificate = _try_load_settings_certificate(settings)
    if certificate is None:
        return PreflightResult(document_id=doc.id, status="blocked", stage_reached=Stage.SIGNATURE,
                               blockers=["certificate_not_supplied"],
                               request_fingerprint=None, staged_artifacts=[])

    # 6. Signature boundary.
    try:
        signed_root = sign_dps(unsigned_root, certificate)
        verify_dps_signature(signed_root, certificate.certificate_pem)
        signed_xml = serialize_dps(signed_root)
        validate_dps_xml(signed_xml)  # never stage a signed-but-schema-invalid document
    except (SignatureError, XsdValidationError):
        return _blocked(doc.id, Stage.SIGNATURE, ["dps_signature_failed"])

    # 7. Request fingerprint — stable identity of exactly this outbound payload.
    request_fingerprint = hashlib.sha256(
        signed_xml + doc.id.encode() + preparation.id.encode()
    ).hexdigest()

    # 8. Private artifact staging. Each run is its own append-only evidence
    # bundle (fresh internal UUID folder); the FiscalArtifact row's
    # attempt_id stays NULL because no FiscalAttempt exists yet at preflight
    # time — storage_relative_path alone is sufficient to retrieve it later.
    try:
        root = settings.resolved_fiscal_artifacts_storage_dir()
    except Exception:
        return _blocked(doc.id, Stage.ARTIFACT_STAGING, ["artifact_storage_not_ready"])
    run_id = str(uuid4())
    for kind, payload in (("dps_unsigned_xml", unsigned_xml), ("dps_signed_xml", signed_xml)):
        stored = artifact_storage.write_artifact(root, document_id=doc.id, attempt_id=run_id,
                                                 kind=kind, data=payload)
        db.add(FiscalArtifact(document_id=doc.id, attempt_id=None, kind=kind,
                              storage_relative_path=str(stored.relative_path),
                              sha256=stored.sha256, size_bytes=stored.size_bytes,
                              created_by=actor))
        staged.append(StagedArtifact(kind=kind, sha256=stored.sha256,
                                     size_bytes=stored.size_bytes,
                                     relative_path=str(stored.relative_path)))
    db.commit()

    return PreflightResult(document_id=doc.id, status="ready_to_send", stage_reached=Stage.READY,
                           blockers=[], request_fingerprint=request_fingerprint,
                           staged_artifacts=staged)
