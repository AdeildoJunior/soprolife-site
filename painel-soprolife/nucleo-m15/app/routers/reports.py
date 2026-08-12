"""M24C — atribuição médica e fluxo clínico de laudos, sempre fail-closed.

O recurso continua desligado por padrão. A conta operacional recebe e
atribui; somente o médico explicitamente autorizado, ativo, verificado e
atualmente atribuído vê o PDF/paciente e produz conteúdo clínico. Nenhum
papel administrativo implica autoria médica.

Não existe caminho de sucesso de assinatura nesta versão. A preparação cria
somente ``assinatura_pendente`` com provider ``unconfigured`` e o rodapé de
TESTE torna a ausência de validade inequívoca.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..errors import ReportDomainError
from ..ids import allocate_public_code
from ..ids import new_uuid
from ..models import (
    ASSINADO_EM_CONFERENCIA,
    ASSINADO_ENTREGUE,
    ASSINADO_RECEBIDO_VALIDACAO_PENDENTE,
    ASSINADO_VALIDADO_EXTERNAMENTE,
    BATCH_DIRECAO_DOWNLOAD,
    BATCH_DIRECAO_UPLOAD,
    PAREAMENTO_CODIGO_LAUDO,
    PAREAMENTO_CODIGO_VALIDACAO,
    PAREAMENTO_METADADO,
    ExternalSignatureBatch,
    ExternalSignedDocument,
    PartnerUnit,
    Person,
    PhysicianProfile,
    PhysicianSignatureAsset,
    ReportAddendum,
    ReportAssignment,
    ReportAssignmentEvent,
    ReportDocument,
    ReportDocumentVersion,
    ReportFooterTemplate,
    ReportSignature,
    ReportTemplate,
    SpirometryExam,
    User,
)
from ..normalize import (
    contains_clinical_info,
    contains_pii_like,
    crm_display_matches,
    normalize_name,
)
from ..schemas import (
    BatchDownloadRequest,
    ExamOperationalClosure,
    ExamOperationalReopen,
    ExternalValidationRequest,
    SignatureBatchConfirmRequest,
    SignatureBatchDownloadRequest,
    PhysicianProfileAdminUpdate,
    QualifiedSignatureCallback,
    ReportAddendumCreate,
    ReportCorrectiveCreate,
    ReportDocumentCompose,
    ReportNativeDraft,
    ReportPhysicianRecovery,
    ReportReassignment,
    ReportReleaseRequest,
    ReportTemplateCreate,
    ReportTemplateUpdate,
)
from ..security import (
    ROLE_ADMIN,
    ROLE_MEDICO,
    ROLE_OPERACIONAL,
    get_current_user,
    get_role,
    require_role,
    user_effective_roles,
    user_has_explicit_role,
)
from ..serializers import (
    d,
    iso,
    ser_physician_profile,
    ser_report_assignment,
    ser_report_document,
    ser_report_signature,
    ser_report_template,
    ser_user,
)
from ..services.exam_closure import (
    CLOSURE_REASONS,
    ExamClosureError,
    close_exam,
    closure_payload,
    reopen_exam,
)
from ..services.pdf_validation import InvalidPdfError, validate_pdf_bytes
from ..services.report_catalog import (
    PILOT_FOOTER_CODE,
    PILOT_WARNING,
    PROVISIONAL_CODES,
    PROVISIONAL_WARNING,
    TEST_FOOTER_CODE,
    ensure_m24c_catalog,
)
from ..services.native_report_builder import (
    NativeReportBuildError,
    active_signature_asset,
    build_native_content,
    generate_validation_code,
    load_addenda,
    resolve_document_context,
    resolve_signature_asset,
    text_sha256,
    to_display_timezone,
    validation_url,
)
from ..services.report_conclusions import (
    ConclusionCatalogError,
    catalog_payload,
    compose_default_conclusion_text,
    normalize_final_text,
    normalize_observations,
    resolve_bronchodilator_text,
    resolve_conclusion_text,
)
from ..services.report_native_pdf import (
    AddendumBlock,
    NativeReportPdfError,
    SIGNATURE_KIND_INSTITUTIONAL,
    SIGNATURE_KIND_QUALIFIED_ICP,
    build_native_report_pdf,
)
from ..services.crm_display import format_crm_full, format_crm_number
from ..services.report_compliance import relatorio_conformidade
from ..status_display import exam_status_display
from ..services.download_names import (
    SUFIXO_ASSINADO,
    SUFIXO_MIR,
    content_disposition,
    named_download_filename,
    report_download_filename,
)
from ..services.report_origin import (
    DerivedOrigin,
    OriginDerivationError,
    derive_report_origin,
    derived_origin_payload,
)
from ..services.report_locations import (
    resolve_exam_location_name,
    resolve_report_location,
)
from ..services.report_pdf import PdfCompositionError, compose_report_pdf
from ..services.report_publication import (
    ReportPublicationTransaction,
    report_publication_transaction,
)
from ..services.report_storage import (
    ReportStorageError,
    StoredPdf,
    StoredPdfIntegrityError,
    StoredPdfMissingError,
    read_and_validate_stored_pdf,
    version_storage_path,
)
from ..services.signature_asset import (
    SignatureAssetError,
    signature_asset_storage_path,
    validate_signature_png,
)
from ..services.signature_batch import (
    JA_RECEBIDO,
    NAO_IDENTIFICADO,
    PAREADO,
    RECUSADO,
    BatchFile,
    MatchVerdict,
    SignatureBatchError,
    batch_zip_filename,
    build_batch_zip,
    extract_signed_pdfs,
    normalize_for_compare,
    read_codes_from_content,
    read_markers_from_metadata,
    stamp_signing_metadata,
    summarize,
)
from ..services.signature_provider import (
    PROVIDER_INSTITUTIONAL_RELEASE,
    SIGNATURE_STATUS_ASSINADA,
    SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL,
    SIGNATURE_STATUS_PENDENTE,
    get_signature_provider,
    institutional_release_evidence,
)

KIND_ORIGINAL = "original"
KIND_RASCUNHO = "rascunho"
KIND_ASSINATURA_PENDENTE = "assinatura_pendente"
KIND_ASSINADO = "assinado"
KIND_FINALIZADO_LEGADO = "finalizado"
# M25.2 — espécies do laudo próprio da SoproLife (documento separado do
# PDF técnico da MIR, que permanece na versão `original`).
KIND_LAUDO_PREVIA = "laudo_previa"
KIND_LAUDO_LIBERADO = "laudo_liberado"
KIND_LAUDO_ADENDO = "laudo_adendo"
# M25.20 — o PDF assinado por fora que VOLTOU. Versão nova e separada: nem a
# MIR original nem o laudo concluído para assinatura são tocados.
KIND_LAUDO_ASSINADO_EXTERNO = "laudo_assinado_externo_recebido"


def _permite_campo_de_assinatura(kind: str) -> bool:
    """O perfil de validação de PDF que corresponde a este tipo de versão.

    Derivado do `kind` — e não passado por parâmetro — de propósito: assim a
    gravação e a releitura usam SEMPRE o mesmo perfil. Um PDF aceito com o
    campo de assinatura e relido sem ele seria declarado corrompido segundos
    depois de gravado.
    """

    return kind == KIND_LAUDO_ASSINADO_EXTERNO

STATUS_ATRIBUIDO = "atribuido"
STATUS_EM_ELABORACAO = "em_elaboracao"
STATUS_ASSINATURA_PENDENTE = "assinatura_pendente"
STATUS_ASSINADO = "assinado"
STATUS_LAUDO_LIBERADO = "liberado"

ORIGIN_TYPES = frozenset(
    {
        "pastore",
        "coworking",
        "residencial",
        "clinica_parceira",
        "empresa_pcmso",
        "outro",
    }
)
ORIGIN_LABELS = {
    "pastore": "Pastore",
    "coworking": "coworking",
    "residencial": "residencial",
    "clinica_parceira": "clínica parceira",
    "empresa_pcmso": "empresa / PCMSO",
    "outro": "outro",
}
CLINICAL_STATUSES = frozenset(
    {
        STATUS_ATRIBUIDO,
        STATUS_EM_ELABORACAO,
        STATUS_ASSINATURA_PENDENTE,
        STATUS_ASSINADO,
        STATUS_LAUDO_LIBERADO,
    }
)
_UPLOAD_CHUNK_BYTES = 64 * 1024
_SAFE_EXAM_CODE_RE = re.compile(r"^ESP-\d{1,9}$")
_UNSIGNED_WARNING = (
    "MODELO DE TESTE — DOCUMENTO NÃO ASSINADO E SEM VALIDADE PARA LIBERAÇÃO"
)


def _require_reports_enabled() -> None:
    settings = get_settings()
    # M24D — "production" nunca serve a API de laudos, mesmo com
    # M15_REPORTS_ENABLED=true: não existe assinatura qualificada nem
    # aprovação jurídica/clínica nesta versão. A checagem de modo vem antes
    # da checagem do flag geral para que a variável do M15 sozinha nunca
    # seja suficiente para servir nada em modo produção.
    if settings.reports_mode == "production":
        raise ReportDomainError(
            503,
            "relatorios_producao_bloqueada",
            "O modo produção de laudos permanece bloqueado nesta versão.",
        )
    if not settings.reports_enabled or settings.reports_mode == "disabled":
        raise ReportDomainError(
            503,
            "relatorios_desabilitados",
            "O recurso de laudos está desabilitado.",
        )


router = APIRouter(
    prefix="/laudos",
    tags=["laudos"],
    dependencies=[Depends(_require_reports_enabled)],
)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _storage_root(settings):
    try:
        return settings.resolved_reports_storage_dir()
    except (ValueError, OSError):
        raise ReportDomainError(
            503,
            "armazenamento_laudos_indisponivel",
            "Armazenamento de laudos indisponível.",
        ) from None


def _get_exam_by_code_or_404(db: Session, exam_code: str) -> SpirometryExam:
    normalized = exam_code.strip().upper()
    if not _SAFE_EXAM_CODE_RE.fullmatch(normalized):
        raise ReportDomainError(
            422,
            "codigo_exame_invalido",
            "Informe um código institucional de espirometria válido.",
        )
    exam = db.execute(
        select(SpirometryExam).where(SpirometryExam.public_code == normalized)
    ).scalar_one_or_none()
    if exam is None:
        raise ReportDomainError(
            404,
            "exame_nao_encontrado",
            "Exame de espirometria não encontrado.",
        )
    return exam


def _get_document_or_404(db: Session, document_id: str) -> ReportDocument:
    document = db.get(ReportDocument, document_id)
    if document is None:
        raise ReportDomainError(
            404, "laudo_nao_encontrado", "Laudo não encontrado."
        )
    return document


def _lock_document_or_404(db: Session, document_id: str) -> ReportDocument:
    document = db.execute(
        select(ReportDocument)
        .where(ReportDocument.id == document_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if document is None:
        raise ReportDomainError(
            404, "laudo_nao_encontrado", "Laudo não encontrado."
        )
    return document


def _get_version_or_404(
    db: Session, document_id: str, version_id: str
) -> ReportDocumentVersion:
    version = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.id == version_id,
            ReportDocumentVersion.report_document_id == document_id,
        )
    ).scalar_one_or_none()
    if version is None:
        raise ReportDomainError(
            404,
            "versao_laudo_nao_encontrada",
            "Versão do laudo não encontrada.",
        )
    return version


def _version_by_kind(
    db: Session, document_id: str, kind: str
) -> ReportDocumentVersion | None:
    return db.execute(
        select(ReportDocumentVersion)
        .where(
            ReportDocumentVersion.report_document_id == document_id,
            ReportDocumentVersion.kind == kind,
        )
        .order_by(ReportDocumentVersion.version_number.desc())
    ).scalars().first()


def _all_versions(
    db: Session, document_id: str
) -> list[ReportDocumentVersion]:
    return list(
        db.execute(
            select(ReportDocumentVersion)
            .where(ReportDocumentVersion.report_document_id == document_id)
            .order_by(ReportDocumentVersion.version_number)
        ).scalars()
    )


def _next_version_number(db: Session, document_id: str) -> int:
    current = db.execute(
        select(func.max(ReportDocumentVersion.version_number)).where(
            ReportDocumentVersion.report_document_id == document_id
        )
    ).scalar_one()
    return int(current or 0) + 1


def _read_stored_version(
    version: ReportDocumentVersion,
    *,
    missing_status: int = 409,
) -> StoredPdf:
    settings = get_settings()
    storage_root = _storage_root(settings)
    try:
        return read_and_validate_stored_pdf(
            storage_root / version.storage_path,
            root=storage_root,
            expected_sha256=version.sha256,
            expected_size_bytes=version.size_bytes,
            expected_page_count=version.page_count,
            max_size_bytes=settings.reports_max_upload_bytes,
            allow_signature_form=_permite_campo_de_assinatura(version.kind),
        )
    except StoredPdfMissingError:
        raise ReportDomainError(
            missing_status,
            "arquivo_laudo_ausente",
            "Arquivo do laudo não está disponível.",
        ) from None
    except StoredPdfIntegrityError as exc:
        raise ReportDomainError(409, exc.codigo, exc.mensagem) from None
    except (ReportStorageError, OSError, ValueError):
        raise ReportDomainError(
            503,
            "armazenamento_laudos_indisponivel",
            "Armazenamento de laudos indisponível.",
        ) from None


def _snapshot_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _complete_or_empty(values: tuple[object | None, ...]) -> bool:
    return all(value is None for value in values) or all(
        value is not None for value in values
    )


def _store_new_version(
    db: Session,
    *,
    publication: ReportPublicationTransaction,
    document: ReportDocument,
    exam_id: str,
    kind: str,
    data: bytes,
    created_by_user_id: str,
    expected_version_number: int | None = None,
    template_id: str | None = None,
    template_code_snapshot: str | None = None,
    template_version_snapshot: int | None = None,
    template_text_snapshot: str | None = None,
    interpretation_text_snapshot: str | None = None,
    physician_profile_id_snapshot: str | None = None,
    physician_name_snapshot: str | None = None,
    physician_crm_number_snapshot: str | None = None,
    physician_crm_state_snapshot: str | None = None,
    physician_rqe_snapshot: str | None = None,
    origin_type_snapshot: str | None = None,
    origin_label_snapshot: str | None = None,
    origin_partner_unit_id_snapshot: str | None = None,
    footer_template_id: str | None = None,
    footer_code_snapshot: str | None = None,
    footer_version_snapshot: int | None = None,
    footer_text_snapshot: str | None = None,
    issued_at_snapshot: datetime | None = None,
    page_number: int | None = None,
    placement: str | None = None,
    # ------------------------------------------------------------ M25.2
    # Evidência do laudo NATIVO. Só as versões `laudo_*` preenchem estes
    # campos; a versão `original` (PDF técnico da MIR) permanece sem eles.
    conclusion_code_snapshot: str | None = None,
    conclusion_text_snapshot: str | None = None,
    bronchodilator_code_snapshot: str | None = None,
    bronchodilator_text_snapshot: str | None = None,
    observations_snapshot: str | None = None,
    exam_has_post_bd_snapshot: bool | None = None,
    location_name_snapshot: str | None = None,
    location_address_snapshot: str | None = None,
    location_contact_snapshot: str | None = None,
    location_partner_unit_id_snapshot: str | None = None,
    location_source_snapshot: str | None = None,
    validation_code_snapshot: str | None = None,
    released_at_snapshot: datetime | None = None,
    signature_asset_id_snapshot: str | None = None,
    signature_asset_sha256_snapshot: str | None = None,
    addendum_sequence: int | None = None,
) -> ReportDocumentVersion:
    """Publica bytes novos e persiste somente snapshots coerentes."""

    from ..ids import new_uuid

    settings = get_settings()
    version_number = _next_version_number(db, document.id)
    if (
        expected_version_number is not None
        and version_number != expected_version_number
    ):
        raise ReportDomainError(
            409,
            "numero_versao_concorrente",
            "Outra composição criou uma versão ao mesmo tempo; repita a operação.",
        )
    permite_assinatura = _permite_campo_de_assinatura(kind)
    try:
        validated = validate_pdf_bytes(
            data,
            max_size_bytes=settings.reports_max_upload_bytes,
            allow_signature_form=permite_assinatura,
        )
    except InvalidPdfError as exc:
        raise ReportDomainError(422, exc.codigo, exc.mensagem) from None

    template_values = (
        template_code_snapshot,
        template_version_snapshot,
        template_text_snapshot,
    )
    if not _complete_or_empty(template_values):
        raise ReportDomainError(
            409,
            "snapshot_template_incompleto",
            "Evidência imutável do template está incompleta.",
        )
    template_text_sha256 = (
        _snapshot_hash(template_text_snapshot)
        if template_text_snapshot is not None
        else None
    )
    interpretation_text_sha256 = (
        _snapshot_hash(interpretation_text_snapshot)
        if interpretation_text_snapshot is not None
        else None
    )
    physician_values = (
        physician_profile_id_snapshot,
        physician_name_snapshot,
        physician_crm_number_snapshot,
        physician_crm_state_snapshot,
        origin_type_snapshot,
    )
    if not _complete_or_empty(physician_values):
        raise ReportDomainError(
            409,
            "snapshot_autoria_incompleto",
            "Evidência imutável de autoria e origem está incompleta.",
        )
    footer_values = (
        footer_template_id,
        footer_code_snapshot,
        footer_version_snapshot,
        footer_text_snapshot,
        issued_at_snapshot,
    )
    if not _complete_or_empty(footer_values):
        raise ReportDomainError(
            409,
            "snapshot_rodape_incompleto",
            "Evidência imutável do rodapé está incompleta.",
        )
    footer_text_sha256 = (
        _snapshot_hash(footer_text_snapshot)
        if footer_text_snapshot is not None
        else None
    )

    storage_root = _storage_root(settings)
    version_id = new_uuid()
    try:
        path = version_storage_path(
            storage_root,
            exam_id=exam_id,
            document_id=document.id,
            version_id=version_id,
        )
        publication.publish(path, data, root=storage_root)
        read_and_validate_stored_pdf(
            path,
            root=storage_root,
            expected_sha256=validated.sha256,
            expected_size_bytes=validated.size_bytes,
            expected_page_count=validated.page_count,
            max_size_bytes=settings.reports_max_upload_bytes,
            allow_signature_form=permite_assinatura,
        )
    except StoredPdfIntegrityError as exc:
        raise ReportDomainError(
            503, exc.codigo, "Falha ao confirmar o PDF armazenado."
        ) from None
    except (ReportStorageError, OSError, ValueError):
        raise ReportDomainError(
            503,
            "armazenamento_laudos_indisponivel",
            "Armazenamento de laudos indisponível.",
        ) from None

    version = ReportDocumentVersion(
        id=version_id,
        report_document_id=document.id,
        kind=kind,
        version_number=version_number,
        storage_path=str(path.relative_to(storage_root)),
        sha256=validated.sha256,
        size_bytes=validated.size_bytes,
        page_count=validated.page_count,
        template_id=template_id,
        template_code_snapshot=template_code_snapshot,
        template_version_snapshot=template_version_snapshot,
        template_text_snapshot=template_text_snapshot,
        template_text_sha256=template_text_sha256,
        interpretation_text_snapshot=interpretation_text_snapshot,
        interpretation_text_sha256=interpretation_text_sha256,
        physician_profile_id_snapshot=physician_profile_id_snapshot,
        physician_name_snapshot=physician_name_snapshot,
        physician_crm_number_snapshot=physician_crm_number_snapshot,
        physician_crm_state_snapshot=physician_crm_state_snapshot,
        physician_rqe_snapshot=physician_rqe_snapshot,
        origin_type_snapshot=origin_type_snapshot,
        origin_label_snapshot=origin_label_snapshot,
        origin_partner_unit_id_snapshot=origin_partner_unit_id_snapshot,
        footer_template_id=footer_template_id,
        footer_code_snapshot=footer_code_snapshot,
        footer_version_snapshot=footer_version_snapshot,
        footer_text_snapshot=footer_text_snapshot,
        footer_text_sha256=footer_text_sha256,
        issued_at_snapshot=issued_at_snapshot,
        page_number=page_number,
        placement=placement,
        conclusion_code_snapshot=conclusion_code_snapshot,
        conclusion_text_snapshot=conclusion_text_snapshot,
        bronchodilator_code_snapshot=bronchodilator_code_snapshot,
        bronchodilator_text_snapshot=bronchodilator_text_snapshot,
        observations_snapshot=observations_snapshot,
        exam_has_post_bd_snapshot=exam_has_post_bd_snapshot,
        location_name_snapshot=location_name_snapshot,
        location_address_snapshot=location_address_snapshot,
        location_contact_snapshot=location_contact_snapshot,
        location_partner_unit_id_snapshot=location_partner_unit_id_snapshot,
        location_source_snapshot=location_source_snapshot,
        validation_code_snapshot=validation_code_snapshot,
        released_at_snapshot=released_at_snapshot,
        signature_asset_id_snapshot=signature_asset_id_snapshot,
        signature_asset_sha256_snapshot=signature_asset_sha256_snapshot,
        addendum_sequence=addendum_sequence,
        created_by_user_id=created_by_user_id,
    )
    db.add(version)
    try:
        db.flush()
    except IntegrityError:
        raise ReportDomainError(
            409,
            "numero_versao_concorrente",
            "Outra composição criou uma versão ao mesmo tempo; repita a operação.",
        ) from None
    return version


async def _read_upload_bounded(
    file: UploadFile, *, max_size_bytes: int
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining_probe = max_size_bytes + 1 - total
        if remaining_probe <= 0:
            raise ReportDomainError(
                422,
                "pdf_excede_tamanho_maximo",
                f"O PDF excede o limite de {max_size_bytes} bytes.",
            )
        chunk = await file.read(min(_UPLOAD_CHUNK_BYTES, remaining_probe))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_size_bytes:
            raise ReportDomainError(
                422,
                "pdf_excede_tamanho_maximo",
                f"O PDF excede o limite de {max_size_bytes} bytes.",
            )
    return b"".join(chunks)


def _profile_for_user(
    db: Session, user: User, *, lock: bool = False
) -> PhysicianProfile | None:
    statement = select(PhysicianProfile).where(
        PhysicianProfile.user_id == user.id
    )
    if lock:
        statement = statement.with_for_update()
    return db.execute(statement).scalar_one_or_none()


def _require_active_physician(
    db: Session, user: User, *, lock: bool = False
) -> PhysicianProfile:
    if not user_has_explicit_role(user, ROLE_MEDICO):
        raise ReportDomainError(
            403,
            "papel_medico_explicito_necessario",
            "A autoria clínica exige o papel médico explícito.",
        )
    profile = _profile_for_user(db, user, lock=lock)
    if (
        profile is None
        or not profile.active
        or profile.verification_status != "verified"
        or profile.verified_at is None
        or profile.verified_by_user_id is None
        or not user.ativo
    ):
        raise ReportDomainError(
            403,
            "perfil_medico_indisponivel",
            "O perfil médico está inativo ou não verificado.",
        )
    return profile


def _profile_for_assignment(
    db: Session, profile_id: str, *, lock: bool = True
) -> tuple[PhysicianProfile, User]:
    statement = select(PhysicianProfile).where(
        PhysicianProfile.id == profile_id
    )
    if lock:
        statement = statement.with_for_update()
    profile = db.execute(statement).scalar_one_or_none()
    if profile is None:
        raise ReportDomainError(
            404, "perfil_medico_nao_encontrado", "Perfil médico não encontrado."
        )
    account = db.get(User, profile.user_id)
    if (
        account is None
        or not account.ativo
        or not user_has_explicit_role(account, ROLE_MEDICO)
        or not profile.active
        or profile.verification_status != "verified"
        or profile.verified_at is None
        or profile.verified_by_user_id is None
    ):
        raise ReportDomainError(
            409,
            "medico_nao_elegivel",
            "O médico selecionado não está ativo, verificado e autorizado.",
        )
    return profile, account


def _active_assignment(
    db: Session, document_id: str, *, lock: bool = False
) -> ReportAssignment | None:
    statement = select(ReportAssignment).where(
        ReportAssignment.report_document_id == document_id,
        ReportAssignment.active.is_(True),
    )
    if lock:
        statement = statement.with_for_update()
    return db.execute(statement).scalar_one_or_none()


def _require_assigned_physician(
    db: Session,
    user: User,
    document: ReportDocument,
    *,
    lock: bool = False,
) -> tuple[PhysicianProfile, ReportAssignment]:
    profile = _require_active_physician(db, user, lock=lock)
    assignment = _active_assignment(db, document.id, lock=lock)
    if assignment is None or assignment.physician_profile_id != profile.id:
        # 404 evita que outro médico use o endpoint como enumerador.
        raise ReportDomainError(
            404,
            "laudo_nao_atribuido",
            "Laudo não encontrado entre as atribuições do médico.",
        )
    return profile, assignment


def _create_assignment(
    db: Session,
    *,
    document: ReportDocument,
    profile: PhysicianProfile,
    performed_by_user_id: str,
    reason_code: str,
    event_type: str,
    previous: ReportAssignment | None = None,
) -> ReportAssignment:
    assignment = ReportAssignment(
        report_document_id=document.id,
        physician_profile_id=profile.id,
        active=True,
        assigned_by_user_id=performed_by_user_id,
        reason_code=reason_code,
        supersedes_assignment_id=previous.id if previous else None,
    )
    db.add(assignment)
    db.flush()
    db.add(
        ReportAssignmentEvent(
            report_document_id=document.id,
            assignment_id=assignment.id,
            event_type=event_type,
            physician_profile_id=profile.id,
            previous_physician_profile_id=(
                previous.physician_profile_id if previous else None
            ),
            reason_code=reason_code,
            performed_by_user_id=performed_by_user_id,
        )
    )
    db.flush()
    return assignment


def _physician_snapshot(
    profile: PhysicianProfile, document: ReportDocument
) -> dict:
    if not document.origin_type:
        raise ReportDomainError(
            409,
            "origem_laudo_ausente",
            "O laudo não possui origem controlada.",
        )
    return {
        "physician_profile_id_snapshot": profile.id,
        "physician_name_snapshot": profile.professional_name,
        "physician_crm_number_snapshot": profile.crm_number,
        "physician_crm_state_snapshot": profile.crm_state,
        "physician_rqe_snapshot": profile.rqe,
        "origin_type_snapshot": document.origin_type,
        "origin_label_snapshot": document.origin_label,
        "origin_partner_unit_id_snapshot": document.origin_partner_unit_id,
    }


def _validate_origin(
    db: Session,
    *,
    origin_type: str,
    origin_label: str | None,
    partner_unit_id: str | None,
) -> tuple[str, str | None, str | None]:
    normalized_type = origin_type.strip().lower()
    if normalized_type not in ORIGIN_TYPES:
        raise ReportDomainError(
            422, "origem_invalida", "Selecione uma origem controlada válida."
        )
    normalized_label = (origin_label or "").strip() or None
    if normalized_label and (
        len(normalized_label) > 120
        or contains_pii_like(normalized_label)
        or contains_clinical_info(normalized_label)
    ):
        raise ReportDomainError(
            422,
            "rotulo_origem_inseguro",
            "O rótulo de origem aceita somente identificação operacional segura.",
        )
    normalized_unit = (partner_unit_id or "").strip() or None
    if normalized_unit:
        if normalized_type != "clinica_parceira":
            raise ReportDomainError(
                422,
                "unidade_origem_incompativel",
                "A referência de unidade só se aplica à origem clínica parceira.",
            )
        unit = db.get(PartnerUnit, normalized_unit)
        if unit is None or not unit.ativo:
            raise ReportDomainError(
                422,
                "unidade_origem_invalida",
                "A unidade parceira selecionada não está ativa.",
            )
    return normalized_type, normalized_label, normalized_unit


def _derived_origin_or_422(db: Session, exam: SpirometryExam) -> DerivedOrigin:
    """Contexto do exame, ou uma recusa que diz onde corrigir o cadastro.

    M25.17 — 422 e não 409 porque, do ponto de vista de quem chamou, o
    pedido é impossível de atender com os dados atuais: falta informação no
    atendimento. A mensagem carrega `como_corrigir` para que o operador não
    fique tentando adivinhar combinações no formulário — que foi exatamente
    o que aconteceu no primeiro uso real.
    """

    try:
        return derive_report_origin(db, exam)
    except OriginDerivationError as exc:
        raise ReportDomainError(
            422,
            exc.codigo,
            f"{exc.mensagem} {exc.como_corrigir}",
        ) from None


def _resolve_origin_with_override(
    derived: DerivedOrigin,
    *,
    origin_type: str | None,
    partner_unit_id: str | None,
) -> tuple[str, str | None]:
    """Decide a origem final entre o que o exame sabe e o que veio no payload.

    O formulário não manda mais estes campos: quem os manda é um cliente
    antigo ou alguém montando a requisição à mão. A regra é quem tem
    autoridade sobre o dado:

    * **Exame com local registrado** (`completo`) — o exame ganha, e um
      payload divergente é recusado. Aceitar em silêncio uma unidade
      diferente da vinculada ao atendimento gravaria no laudo o endereço de
      uma clínica onde o exame não aconteceu, e creditaria o parceiro
      errado no financeiro.

    * **Exame sem local registrado** — não há nada a contradizer, e a
      origem explícita é a ÚNICA informação disponível sobre onde o exame
      foi feito. Recusá-la seria descartar o único dado existente em nome de
      uma autoridade que o exame não exerce. O valor ainda passa por
      `_validate_origin`, que continua barrando unidade fora de clínica
      parceira — a combinação inválida do primeiro uso real segue impossível.
    """

    enviado_tipo = (origin_type or "").strip().lower() or None
    enviado_unidade = (partner_unit_id or "").strip() or None

    if not derived.completo:
        return (
            enviado_tipo or derived.origin_type,
            enviado_unidade or derived.partner_unit_id,
        )

    if enviado_tipo is not None and enviado_tipo != derived.origin_type:
        raise ReportDomainError(
            422,
            "origem_divergente_do_exame",
            "A origem enviada não corresponde ao local registrado no exame.",
        )
    if enviado_unidade is not None and enviado_unidade != derived.partner_unit_id:
        raise ReportDomainError(
            422,
            "unidade_divergente_do_exame",
            "A unidade enviada não corresponde à unidade vinculada ao exame.",
        )
    return derived.origin_type, derived.partner_unit_id


def _origin_display(document: ReportDocument) -> str:
    base = ORIGIN_LABELS.get(document.origin_type or "", "origem não registrada")
    return f"{base} — {document.origin_label}" if document.origin_label else base


def _render_test_footer(
    footer: ReportFooterTemplate,
    *,
    profile: PhysicianProfile,
    document: ReportDocument,
    exam: SpirometryExam,
    issued_at: datetime,
    version_number: int,
) -> str:
    if (
        footer.code != TEST_FOOTER_CODE
        or footer.status != "test"
        or footer.production_approved
        or not footer.active
        or _UNSIGNED_WARNING not in footer.body_template
    ):
        raise ReportDomainError(
            503,
            "rodape_teste_indisponivel",
            "O rodapé TESTE não está disponível de forma segura.",
        )
    try:
        rendered = footer.body_template.format(
            physician_name=profile.professional_name,
            crm_state=profile.crm_state,
            # M25.15 — o rodapé imprime o CRM na MESMA apresentação
            # canônica da tela, do PDF e da rota de validação.
            crm_number=format_crm_number(
                profile.crm_number,
                profile.crm_state,
                crm_display=profile.crm_display,
            ),
            rqe=profile.rqe or "",
            exam_code=exam.public_code,
            origin=_origin_display(document),
            issued_at=issued_at.astimezone(timezone.utc).isoformat(),
            report_code=document.public_code,
            version_number=version_number,
        )
    except (KeyError, ValueError):
        raise ReportDomainError(
            503,
            "rodape_teste_invalido",
            "O rodapé TESTE possui placeholders inválidos.",
        ) from None
    if _UNSIGNED_WARNING not in rendered or "assinado digitalmente" in rendered.lower():
        raise ReportDomainError(
            503,
            "rodape_teste_invalido",
            "O rodapé TESTE não declara corretamente o estado não assinado.",
        )
    return rendered


def _render_pilot_footer(
    footer: ReportFooterTemplate,
    *,
    profile: PhysicianProfile,
    document: ReportDocument,
    exam: SpirometryExam,
    issued_at: datetime,
    version_number: int,
) -> str:
    """M24D — rodapé exclusivo do piloto interno controlado. Espelha
    ``_render_test_footer`` mas exige o aviso PILOTO INTERNO exato em vez do
    aviso de MODELO DE TESTE; nunca aprovado para produção."""

    if (
        footer.code != PILOT_FOOTER_CODE
        or footer.status != "test"
        or footer.production_approved
        or not footer.active
        or PILOT_WARNING not in footer.body_template
    ):
        raise ReportDomainError(
            503,
            "rodape_piloto_indisponivel",
            "O rodapé PILOTO INTERNO não está disponível de forma segura.",
        )
    try:
        rendered = footer.body_template.format(
            physician_name=profile.professional_name,
            crm_state=profile.crm_state,
            # M25.15 — o rodapé imprime o CRM na MESMA apresentação
            # canônica da tela, do PDF e da rota de validação.
            crm_number=format_crm_number(
                profile.crm_number,
                profile.crm_state,
                crm_display=profile.crm_display,
            ),
            rqe=profile.rqe or "",
            exam_code=exam.public_code,
            origin=_origin_display(document),
            issued_at=issued_at.astimezone(timezone.utc).isoformat(),
            report_code=document.public_code,
            version_number=version_number,
        )
    except (KeyError, ValueError):
        raise ReportDomainError(
            503,
            "rodape_piloto_invalido",
            "O rodapé PILOTO INTERNO possui placeholders inválidos.",
        ) from None
    if PILOT_WARNING not in rendered or "assinado digitalmente" in rendered.lower():
        raise ReportDomainError(
            503,
            "rodape_piloto_invalido",
            "O rodapé PILOTO INTERNO não declara corretamente o estado não assinado.",
        )
    return rendered


def _latest_template_version(db: Session, code: str) -> int:
    value = db.execute(
        select(func.max(ReportTemplate.versao)).where(
            ReportTemplate.codigo == code
        )
    ).scalar_one()
    return int(value or 0)


def _selectable_template(
    db: Session, template_id: str
) -> ReportTemplate:
    template = db.get(ReportTemplate, template_id)
    if template is None or not template.ativo:
        raise ReportDomainError(
            404,
            "template_nao_encontrado",
            "Template não encontrado ou inativo.",
        )
    settings = get_settings()
    test_override = (
        settings.env == "dev"
        and settings.reports_test_allow_provisional_templates
    )
    approved = template.status == "approved" and template.clinically_approved
    provisional_override = (
        test_override
        and template.status == "draft"
        and not template.clinically_approved
        and template.codigo in PROVISIONAL_CODES
        and template.texto_completo == PROVISIONAL_WARNING
    )
    if not approved and not provisional_override:
        raise ReportDomainError(
            409,
            "template_nao_aprovado",
            "O template não possui aprovação clínica e não pode ser selecionado.",
        )
    if template.versao != _latest_template_version(db, template.codigo):
        raise ReportDomainError(
            409,
            "template_versao_obsoleta",
            "Selecione a revisão mais recente do template.",
        )
    return template


def _queue_location(
    db: Session, document: ReportDocument, exam: SpirometryExam
) -> dict:
    """Unidade da linha da fila, para agrupar exames por local.

    M25.6 — reusa `resolve_report_location`, a MESMA função que decide o
    local impresso no laudo. Duplicar aqui a ordem de precedência criaria a
    chance de a fila agrupar por um local e o PDF imprimir outro.

    `chave` é o que o painel usa para agrupar: o id da unidade quando existe
    uma cadastrada, senão a origem controlada. Nunca o nome, que muda quando
    a clínica é renomeada.
    """

    local = resolve_report_location(db, document=document, exam=exam)
    chave = (
        f"unidade:{local.partner_unit_id}"
        if local.partner_unit_id
        else f"origem:{(document.origin_type or 'outro').strip().lower()}"
    )
    return {"chave": chave, "nome": local.name}


def _patient_reference(person: Person | None) -> dict | None:
    """Referência humana do paciente para uma linha de fila autenticada.

    M25.15. Até aqui a fila era só de códigos, e a identidade só existia
    dentro do workspace atribuído. Isso protegia bem e operava mal: quem
    recebe o PDF na recepção e quem vai laudar pensam em "a senhora que veio
    ontem", não em ESP-000016, e a conferência virava decorar código.

    O que muda é ONDE o nome aparece, não QUEM pode vê-lo: estas linhas só
    são produzidas por rotas que já exigem sessão e papel — a fila
    operacional (`require_role(ROLE_OPERACIONAL)`) e a fila da médica, que
    além do papel filtra pela atribuição ativa dela. A rota pública de
    validação NÃO usa esta função, e nenhum campo daqui entra em auditoria,
    URL ou log.

    O conteúdo é o mínimo para identificar e desambiguar homônimos junto com
    a data e a unidade que já viajam na linha: nome e o código do cadastro.
    Nascimento, contato e qualquer dado clínico continuam fora.
    """

    if person is None:
        return None
    return {
        "full_name": person.nome_completo,
        "public_code": person.public_code,
    }


def _technical_report_row(
    document: ReportDocument,
    exam: SpirometryExam,
    assignment: ReportAssignment | None,
    *,
    include_assignment_ids: bool = False,
    location: dict | None = None,
    person: Person | None = None,
) -> dict:
    data = {
        # M25.15 — `patient` é a referência humana principal da linha; os
        # códigos abaixo continuam presentes e obrigatórios, como metadado
        # de rastreabilidade. Fica `None` quando quem chamou não resolveu a
        # pessoa: nenhuma rota inventa identidade por conta própria.
        "patient": _patient_reference(person),
        "report_code": document.public_code,
        "document_id": document.id,
        "exam_code": exam.public_code,
        "exam_date": d(exam.data_exame),
        "origin_type": document.origin_type,
        "origin_label": document.origin_label,
        # Agrupamento por unidade na fila médica (M25.6). Ausente quando a
        # rota não resolve local — nunca inventado a partir do rótulo.
        "location_key": (location or {}).get("chave"),
        "location_name": (location or {}).get("nome"),
        "assignment_timestamp": (
            assignment.assigned_at.isoformat() if assignment else None
        ),
        "status": document.status,
        "signature_status": document.signature_status,
        "releasable": False,
        # M25.2 — sinalização de estado para a fila médica. Nenhum dado
        # clínico ou de paciente: só carimbos institucionais.
        "released_at": document.released_at.isoformat()
        if document.released_at
        else None,
        "locked": document.status == STATUS_LAUDO_LIBERADO,
        "is_corrective": document.corrects_document_id is not None,
        "validation_code": document.validation_code,
        # M25.24 — carimbo do encerramento operacional do EXAME. `None` na
        # fila ativa. Presente, a linha tem de aparecer marcada como
        # histórico: uma lista que junta encerrado e pendente sem etiqueta
        # é pior do que uma lista que só mostra pendente.
        "encerramento": closure_payload(exam),
    }
    if include_assignment_ids:
        data.update(
            {
                "assignment_id": assignment.id if assignment else None,
                "physician_profile_id": (
                    assignment.physician_profile_id if assignment else None
                ),
            }
        )
    return data


def _safe_template_payload(
    template: ReportTemplate, *, include_body: bool
) -> dict:
    data = ser_report_template(template)
    if not include_body:
        data.pop("texto_completo", None)
    return data


# -------------------------------------------------- administração de médicos


@router.get("/admin/medicos")
def list_physician_accounts(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(ROLE_ADMIN)),
):
    users = db.execute(select(User).order_by(User.created_at)).scalars().all()
    profiles = {
        profile.user_id: profile
        for profile in db.execute(select(PhysicianProfile)).scalars()
    }
    return [
        {
            "user": ser_user(user),
            "has_explicit_physician_role": user_has_explicit_role(
                user, ROLE_MEDICO
            ),
            "profile": (
                ser_physician_profile(profiles[user.id])
                if user.id in profiles
                else None
            ),
        }
        for user in users
    ]


@router.patch("/admin/medicos/{user_id}")
def update_physician_account(
    user_id: str,
    payload: PhysicianProfileAdminUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ROLE_ADMIN)),
):
    account = db.get(User, user_id)
    if account is None:
        raise ReportDomainError(
            404, "usuario_nao_encontrado", "Usuário não encontrado."
        )
    profile = _profile_for_user(db, account, lock=True)
    values = payload.model_dump(exclude_unset=True)
    if profile is None:
        required = ("professional_name", "crm_number", "crm_state")
        if any(not values.get(field) for field in required):
            raise ReportDomainError(
                422,
                "perfil_medico_incompleto",
                "Nome profissional, CRM e UF são obrigatórios.",
            )
        profile = PhysicianProfile(
            user_id=account.id,
            professional_name=values["professional_name"],
            crm_number=values["crm_number"],
            crm_state=values["crm_state"],
            rqe=values.get("rqe") or None,
            active=False,
            verification_status="pending",
        )
        db.add(profile)
        db.flush()

    identity_fields = {
        "professional_name",
        "crm_number",
        "crm_state",
        "rqe",
        # M25.3 — entram na identidade profissional: mudá-los reabre a
        # verificação exatamente como mudar o CRM ou o nome.
        "crm_display",
        "especialidade",
    }
    identity_changed = False
    changed_fields: list[str] = []
    for field in identity_fields:
        if field not in values:
            continue
        value = values[field]
        if field in ("rqe", "crm_display", "especialidade"):
            value = value or None
        if value != getattr(profile, field):
            setattr(profile, field, value)
            identity_changed = True
            changed_fields.append(field)

    # `crm_display` é só apresentação: os dígitos precisam bater com o CRM
    # canônico, senão a formatação viraria um caminho lateral para alterar o
    # registro profissional sem passar por nova verificação.
    if not crm_display_matches(profile.crm_display, profile.crm_number):
        raise ReportDomainError(
            422,
            "crm_display_divergente",
            "A formatação do CRM deve conter exatamente os mesmos dígitos do CRM.",
        )

    physician_role = get_role(db, ROLE_MEDICO)
    has_role = user_has_explicit_role(account, ROLE_MEDICO)
    grant_role = values.get("grant_physician_role", True)
    if grant_role and not has_role:
        account.roles.append(physician_role)
        changed_fields.append("physician_role")
        has_role = True
    elif not grant_role and has_role:
        account.roles.remove(physician_role)
        changed_fields.append("physician_role")
        has_role = False

    requested_verification = values.get("verification_status")
    if identity_changed and requested_verification is None:
        requested_verification = "pending"
    if requested_verification is not None:
        if requested_verification == "verified":
            # M24D (fecha F2) — segregação de funções: quem verifica nunca
            # pode ser o próprio perfil verificado, e a verificação exige
            # uma referência técnica segura (código/ID do processo de
            # checagem CRM/UF), nunca uma afirmação sem prova.
            if admin.id == account.id:
                raise ReportDomainError(
                    409,
                    "autoverificacao_medica_proibida",
                    "Quem verifica não pode ser o próprio perfil médico.",
                )
            reference = (values.get("verification_reference") or "").strip()
            if len(reference) < 4:
                raise ReportDomainError(
                    422,
                    "referencia_verificacao_obrigatoria",
                    "Verificação exige uma referência técnica segura.",
                )
            profile.verification_reference = reference
            profile.verified_at = datetime.now(timezone.utc)
            profile.verified_by_user_id = admin.id
        else:
            profile.verified_at = None
            profile.verified_by_user_id = None
            profile.verification_reference = None
            profile.active = False
        profile.verification_status = requested_verification
        changed_fields.append("verification_status")

    requested_active = values.get("active")
    if not grant_role:
        requested_active = False
    if requested_active is not None:
        if requested_active and (
            not account.ativo
            or not has_role
            or profile.verification_status != "verified"
            or profile.verified_at is None
            or profile.verified_by_user_id is None
            or not profile.professional_name.strip()
            or not profile.crm_number
            or not profile.crm_state
        ):
            raise ReportDomainError(
                409,
                "ativacao_medica_insegura",
                "A ativação exige conta ativa, papel médico explícito e perfil verificado completo.",
            )
        if profile.active != requested_active:
            profile.active = requested_active
            changed_fields.append("active")

    audit(
        db,
        "perfil_medico_administrado",
        entidade="physician_profiles",
        entidade_id=profile.id,
        user_id=admin.id,
        request_id=_request_id(request),
        detalhes={
            "target_user_id": account.id,
            "fields": sorted(set(changed_fields)),
            "physician_role": has_role,
            "active": profile.active,
            "verification_status": profile.verification_status,
            "verification_reference_present": profile.verification_reference
            is not None,
        },
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ReportDomainError(
            409,
            "crm_uf_ativo_duplicado",
            "Já existe perfil médico ativo com este CRM/UF.",
        ) from None
    db.refresh(profile)
    return {
        "user": ser_user(account),
        "has_explicit_physician_role": has_role,
        "profile": ser_physician_profile(profile),
    }


@router.get("/medicos-disponiveis")
def list_available_physicians(
    db: Session = Depends(get_db),
    _operator: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    profiles = db.execute(
        select(PhysicianProfile)
        .where(
            PhysicianProfile.active.is_(True),
            PhysicianProfile.verification_status == "verified",
        )
        .order_by(PhysicianProfile.professional_name)
    ).scalars().all()
    result = []
    for profile in profiles:
        account = db.get(User, profile.user_id)
        if (
            account is not None
            and account.ativo
            and user_has_explicit_role(account, ROLE_MEDICO)
        ):
            result.append(ser_physician_profile(profile))
    return result


# ------------------------------------------- localizador de exame (M25.15)

_SAFE_REPORT_CODE_RE = re.compile(r"^LAU-\d{1,9}$")
_EXAM_SEARCH_LIMIT = 40


@router.get("/exames")
def search_exams_for_report(
    q: str | None = Query(default=None, max_length=120),
    somente_sem_laudo: bool = Query(default=False),
    # M25.17 — modo técnico explícito. O padrão da operação é NÃO ver
    # cadastro interno de teste; quem precisa auditar um cenário antigo
    # pede por ele conscientemente.
    incluir_arquivados: bool = Query(default=False),
    # M25.24 — o mesmo desenho do modo técnico acima: quem precisa ver um
    # exame encerrado como histórico pede por ele conscientemente.
    incluir_encerrados: bool = Query(default=False),
    db: Session = Depends(get_db),
    _operator: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Localiza o exame que vai receber o PDF técnico.

    M25.15 — antes só existia busca por código exato, e o operador precisava
    saber de cor um ESP para anexar qualquer coisa. Agora o mesmo campo
    aceita as três formas como as pessoas de fato se referem a um exame: o
    código do exame, o código do laudo já criado e o NOME do paciente.

    Não é uma busca de pacientes: o resultado é sempre uma lista de exames,
    com o mínimo para decidir qual é o certo — nome, data, unidade e código.
    Entre homônimos, data + unidade + ESP separam sem obrigar ninguém a
    decorar código. Contato, nascimento e qualquer dado clínico ficam fora,
    e o termo pesquisado nunca vai para auditoria ou log.

    Rota AUTENTICADA e restrita a `ROLE_OPERACIONAL`, como o restante do
    recebimento. `q` viaja como query string; por isso a busca por nome é
    feita por termo normalizado e a resposta é limitada — nada aqui deve
    virar um jeito de listar a base inteira de pacientes.
    """

    statement = (
        select(SpirometryExam, Person, ReportDocument)
        .join(Person, Person.id == SpirometryExam.person_id)
        .outerjoin(
            ReportDocument,
            ReportDocument.spirometry_exam_id == SpirometryExam.id,
        )
    )
    termo = (q or "").strip()
    if termo:
        codigo = termo.upper()
        if _SAFE_EXAM_CODE_RE.fullmatch(codigo):
            statement = statement.where(
                SpirometryExam.public_code == codigo
            )
        elif _SAFE_REPORT_CODE_RE.fullmatch(codigo):
            statement = statement.where(ReportDocument.public_code == codigo)
        else:
            normalizado = normalize_name(termo)
            if len(normalizado) < 3:
                raise ReportDomainError(
                    422,
                    "termo_de_busca_curto",
                    "Informe ao menos 3 letras do nome, um código ESP ou "
                    "um código LAU.",
                )
            statement = statement.where(
                Person.nome_normalizado.like(f"%{normalizado}%")
            )
    if somente_sem_laudo:
        statement = statement.where(ReportDocument.id.is_(None))
    if not incluir_arquivados:
        statement = statement.where(Person.arquivado.is_(False))
    # M25.24 — "Espirometrias recentes sem laudo" é uma lista de TRABALHO. Um
    # exame encerrado como histórico já teve o laudo entregue por fora: ele
    # não é pendência, e continuar cobrando por ele todo dia é o defeito que
    # esta missão veio consertar. Continua achável por código exato e na
    # visão explícita de históricos.
    if not incluir_encerrados:
        statement = statement.where(
            SpirometryExam.encerramento_motivo.is_(None)
        )
    rows = db.execute(
        statement.order_by(SpirometryExam.created_at.desc()).limit(
            _EXAM_SEARCH_LIMIT
        )
    ).all()
    return [
        {
            # Referência humana primeiro; os códigos seguem logo abaixo e
            # continuam sendo o que identifica o registro sem ambiguidade.
            "patient": _patient_reference(person),
            "exam_code": exam.public_code,
            "exam_date": d(exam.data_exame),
            "exam_status": exam.status,
            "exam_status_display": exam_status_display(exam.status),
            "location_name": (
                _queue_location(db, document, exam)["nome"]
                if document is not None
                else resolve_exam_location_name(db, exam)
            ),
            # Já tem laudo no fluxo? É o que decide se este exame ainda pode
            # receber um PDF original ou se o operador está olhando para um
            # trabalho que já começou.
            "report_code": document.public_code if document else None,
            "report_document_id": document.id if document else None,
            "report_status": document.status if document else None,
            # M25.17 — o local do exame, já derivado, para a tela mostrar em
            # modo somente leitura em vez de pedir origem + unidade. Quando
            # o cadastro é inconsistente, vem `ok: false` com o que corrigir
            # — a linha continua aparecendo, porque é assim que o operador
            # descobre que existe algo a consertar.
            "origem_derivada": derived_origin_payload(db, exam),
            # M25.24 — `None` quando o exame está ativo. Presente, diz por
            # que ele não é mais trabalho e desde quando.
            "encerramento": closure_payload(exam),
        }
        for exam, person, document in rows
    ]


# --------------------------------- M25.24 — encerramento operacional
#
# Três rotas: encerrar, reabrir e listar os encerrados. Todas trabalham por
# CÓDIGO do exame (ESP-…), e não por id interno: quem opera isto está olhando
# para uma lista impressa ou para a tela, e o código é o que ela reconhece.
#
# Encerrar é `ROLE_OPERACIONAL`; REABRIR é `ROLE_ADMIN`. A assimetria é
# deliberada — tirar da fila é rotina de operação, devolver um exame ao
# trabalho clínico é decisão de gestão.


def _exam_by_code_or_404(db: Session, exam_code: str) -> SpirometryExam:
    codigo = (exam_code or "").strip().upper()
    if not _SAFE_EXAM_CODE_RE.fullmatch(codigo):
        raise ReportDomainError(
            422, "codigo_exame_invalido", "Código de exame inválido."
        )
    exam = db.execute(
        select(SpirometryExam).where(SpirometryExam.public_code == codigo)
    ).scalar_one_or_none()
    if exam is None:
        raise ReportDomainError(
            404, "exame_nao_encontrado", "Exame não encontrado."
        )
    return exam


@router.get("/exames/encerrados")
def list_closed_exams(
    db: Session = Depends(get_db),
    _operator: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """A visão explícita de "Históricos / encerrados".

    Sair da fila de trabalho não pode significar sumir. Sem este lugar, o
    encerramento seria indistinguível de uma exclusão para quem opera — e a
    primeira dúvida honesta ("cadê o exame do paciente X?") não teria
    resposta.
    """

    _require_reports_enabled()
    rows = db.execute(
        select(SpirometryExam, Person, ReportDocument)
        .join(Person, Person.id == SpirometryExam.person_id)
        .outerjoin(
            ReportDocument,
            ReportDocument.spirometry_exam_id == SpirometryExam.id,
        )
        .where(SpirometryExam.encerramento_motivo.is_not(None))
        .order_by(SpirometryExam.encerrado_em.desc())
        .limit(300)
    ).all()
    return {
        "total": len(rows),
        "itens": [
            {
                "patient": _patient_reference(person),
                "exam_code": exam.public_code,
                "exam_date": d(exam.data_exame),
                "exam_status": exam.status,
                "exam_status_display": exam_status_display(exam.status),
                "location_name": resolve_exam_location_name(db, exam),
                "report_code": document.public_code if document else None,
                "report_document_id": document.id if document else None,
                "report_status": document.status if document else None,
                "encerramento": closure_payload(exam),
            }
            for exam, person, document in rows
        ],
    }


@router.get("/exames/motivos-encerramento")
def list_closure_reasons(
    _operator: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """O catálogo fechado, para a tela montar o seletor sem repeti-lo."""

    _require_reports_enabled()
    return {
        "motivos": [
            {"chave": chave, "rotulo": rotulo}
            for chave, rotulo in CLOSURE_REASONS.items()
        ]
    }


@router.post("/exames/{exam_code}/encerramento")
def close_exam_operationally(
    exam_code: str,
    payload: ExamOperationalClosure,
    request: Request,
    db: Session = Depends(get_db),
    operator: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Tira UM exame da fila operacional, preservando tudo.

    Idempotente: repetir com o MESMO motivo devolve `alterado: false` e não
    reescreve data nem autoria da decisão original. Repetir com motivo
    diferente é recusado — a operação precisa reabrir primeiro, para que as
    duas decisões fiquem na trilha em vez de a segunda apagar a primeira.
    """

    _require_reports_enabled()
    exam = _exam_by_code_or_404(db, exam_code)
    try:
        alterado = close_exam(
            db,
            exam=exam,
            motivo=payload.motivo,
            observacao=payload.observacao,
            user_id=operator.id,
        )
    except ExamClosureError as exc:
        raise ReportDomainError(
            exc.http_status, exc.codigo, exc.mensagem
        ) from None
    if alterado:
        audit(
            db,
            "exame_encerrado_operacionalmente",
            entidade="spirometry_exams",
            entidade_id=exam.id,
            user_id=operator.id,
            request_id=_request_id(request),
            # Código institucional e valores fechados. A observação entra
            # porque é a evidência da decisão — e por isso ela nunca deve
            # receber nome, contato ou dado clínico de paciente.
            detalhes={
                "exam_code": exam.public_code,
                "reason_code": exam.encerramento_motivo,
                "motivo": exam.encerramento_observacao,
            },
        )
    db.commit()
    return {
        "exam_code": exam.public_code,
        "alterado": alterado,
        "encerramento": closure_payload(exam),
    }


@router.post("/exames/{exam_code}/reabertura")
def reopen_exam_operationally(
    exam_code: str,
    payload: ExamOperationalReopen,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ROLE_ADMIN)),
):
    """"Reabrir para laudo": devolve o exame às filas, exatamente como estava.

    Idempotente: reabrir um exame que já está aberto devolve
    `alterado: false`. Nada além dos quatro campos de encerramento é tocado —
    status do exame, laudos, versões e hashes continuam como estavam.
    """

    _require_reports_enabled()
    exam = _exam_by_code_or_404(db, exam_code)
    anterior = exam.encerramento_motivo
    alterado = reopen_exam(db, exam=exam)
    if alterado:
        audit(
            db,
            "exame_reaberto_para_laudo",
            entidade="spirometry_exams",
            entidade_id=exam.id,
            user_id=admin.id,
            request_id=_request_id(request),
            detalhes={
                "exam_code": exam.public_code,
                "reason_code": anterior,
                "motivo": payload.observacao[:200],
            },
        )
    db.commit()
    return {"exam_code": exam.public_code, "alterado": alterado}


# ----------------------------------------------------------- templates


@router.get("/templates")
def list_report_templates(
    catalog: str = Query(default="clinical", pattern="^(clinical|admin)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ensure_m24c_catalog(db)
    is_admin = ROLE_ADMIN in user_effective_roles(user)
    if catalog == "admin":
        if not is_admin:
            raise ReportDomainError(
                403, "permissao_insuficiente", "Permissão insuficiente."
            )
        templates = db.execute(
            select(ReportTemplate).order_by(
                ReportTemplate.codigo, ReportTemplate.versao.desc()
            )
        ).scalars().all()
        return [
            _safe_template_payload(template, include_body=True)
            for template in templates
        ]

    _require_active_physician(db, user)
    settings = get_settings()
    test_override = (
        settings.env == "dev"
        and settings.reports_test_allow_provisional_templates
    )
    templates = db.execute(
        select(ReportTemplate)
        .where(ReportTemplate.ativo.is_(True))
        .order_by(ReportTemplate.codigo, ReportTemplate.versao.desc())
    ).scalars().all()
    latest_by_code: dict[str, ReportTemplate] = {}
    for template in templates:
        latest_by_code.setdefault(template.codigo, template)
    selectable = []
    for template in latest_by_code.values():
        approved = (
            template.status == "approved" and template.clinically_approved
        )
        provisional = (
            test_override
            and template.codigo in PROVISIONAL_CODES
            and template.status == "draft"
            and not template.clinically_approved
            and template.texto_completo == PROVISIONAL_WARNING
        )
        if approved or provisional:
            selectable.append(
                _safe_template_payload(template, include_body=True)
            )
    return selectable


@router.post("/templates", status_code=201)
def create_report_template(
    payload: ReportTemplateCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ROLE_ADMIN)),
):
    ensure_m24c_catalog(db)
    if db.execute(
        select(ReportTemplate).where(ReportTemplate.codigo == payload.codigo)
    ).first():
        raise ReportDomainError(
            409,
            "template_codigo_duplicado",
            "Já existe uma categoria de template com este código.",
        )
    now = datetime.now(timezone.utc)
    template = ReportTemplate(
        codigo=payload.codigo,
        titulo=payload.titulo,
        texto_tooltip=payload.texto_tooltip,
        texto_completo=payload.texto_completo,
        versao=1,
        ativo=payload.ativo,
        status=payload.status,
        clinically_approved=payload.clinically_approved,
        approved_by_user_id=(
            admin.id if payload.clinically_approved else None
        ),
        approved_at=now if payload.clinically_approved else None,
        criado_por=admin.id,
    )
    db.add(template)
    db.flush()
    audit(
        db,
        "template_laudo_versao_criada",
        entidade="report_templates",
        entidade_id=template.id,
        user_id=admin.id,
        request_id=_request_id(request),
        detalhes={
            "codigo": template.codigo,
            "versao": template.versao,
            "status": template.status,
            "clinically_approved": template.clinically_approved,
        },
    )
    db.commit()
    return ser_report_template(template)


@router.patch("/templates/{template_id}", status_code=201)
def create_report_template_revision(
    template_id: str,
    payload: ReportTemplateUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ROLE_ADMIN)),
):
    ensure_m24c_catalog(db)
    previous = db.get(ReportTemplate, template_id)
    if previous is None:
        raise ReportDomainError(
            404, "template_nao_encontrado", "Template não encontrado."
        )
    latest = _latest_template_version(db, previous.codigo)
    if previous.versao != latest:
        raise ReportDomainError(
            409,
            "template_versao_obsoleta",
            "Crie a revisão a partir da versão mais recente.",
        )
    updates = payload.model_dump(exclude_unset=True)
    status = updates.get("status", previous.status)
    approved = updates.get(
        "clinically_approved", previous.clinically_approved
    )
    if approved != (status == "approved"):
        raise ReportDomainError(
            422,
            "aprovacao_template_incoerente",
            "Template aprovado exige status approved e aprovação clínica.",
        )
    now = datetime.now(timezone.utc)
    revision = ReportTemplate(
        codigo=previous.codigo,
        titulo=updates.get("titulo", previous.titulo),
        texto_tooltip=updates.get(
            "texto_tooltip", previous.texto_tooltip
        ),
        texto_completo=updates.get(
            "texto_completo", previous.texto_completo
        ),
        versao=previous.versao + 1,
        ativo=updates.get("ativo", previous.ativo),
        status=status,
        clinically_approved=approved,
        supersedes_template_id=previous.id,
        approved_by_user_id=admin.id if approved else None,
        approved_at=now if approved else None,
        criado_por=admin.id,
    )
    db.add(revision)
    try:
        db.flush()
    except IntegrityError:
        raise ReportDomainError(
            409,
            "template_revisao_concorrente",
            "Outra revisão deste template já foi criada.",
        ) from None
    audit(
        db,
        "template_laudo_versao_criada",
        entidade="report_templates",
        entidade_id=revision.id,
        user_id=admin.id,
        request_id=_request_id(request),
        detalhes={
            "codigo": revision.codigo,
            "versao": revision.versao,
            "status": revision.status,
            "clinically_approved": revision.clinically_approved,
            "supersedes_template_id": previous.id,
        },
    )
    db.commit()
    return ser_report_template(revision)


# ----------------------------------------------------- upload e atribuição


@router.post("", status_code=201)
async def upload_report_document(
    request: Request,
    exam_code: str = Form(...),
    physician_profile_id: str = Form(...),
    # M25.17 — origem e unidade deixaram de ser perguntadas ao operador e
    # passaram a sair do exame (ver `report_origin.derive_report_origin`).
    # Os campos continuam ACEITOS e opcionais por dois motivos: clientes
    # antigos não quebram, e quando vêm preenchidos servem de conferência —
    # divergir do que o exame diz é erro, não preferência do chamador.
    origin_type: str | None = Form(default=None),
    origin_label: str | None = Form(default=None),
    origin_partner_unit_id: str | None = Form(default=None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    operator: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    exam = _get_exam_by_code_or_404(db, exam_code)
    profile, _account = _profile_for_assignment(db, physician_profile_id)
    derived = _derived_origin_or_422(db, exam)
    origem_final, unidade_final = _resolve_origin_with_override(
        derived,
        origin_type=origin_type,
        partner_unit_id=origin_partner_unit_id,
    )
    # O rótulo livre continua sendo do operador (é texto operacional seguro,
    # não decide local nem dinheiro); origem e unidade agora são do exame.
    safe_origin, safe_label, safe_unit = _validate_origin(
        db,
        origin_type=origem_final,
        origin_label=origin_label,
        partner_unit_id=unidade_final,
    )
    settings = get_settings()
    raw = await _read_upload_bounded(
        file, max_size_bytes=settings.reports_max_upload_bytes
    )
    try:
        validate_pdf_bytes(
            raw,
            max_size_bytes=settings.reports_max_upload_bytes,
            declared_content_type=file.content_type,
        )
    except InvalidPdfError as exc:
        raise ReportDomainError(422, exc.codigo, exc.mensagem) from None

    with report_publication_transaction(db) as publication:
        document = ReportDocument(
            public_code=allocate_public_code(db, "report_documents"),
            spirometry_exam_id=exam.id,
            status=STATUS_ATRIBUIDO,
            origin_type=safe_origin,
            origin_label=safe_label,
            origin_partner_unit_id=safe_unit,
            created_by_user_id=operator.id,
        )
        db.add(document)
        db.flush()
        assignment = _create_assignment(
            db,
            document=document,
            profile=profile,
            performed_by_user_id=operator.id,
            reason_code="initial_assignment",
            event_type="assigned",
        )
        version = _store_new_version(
            db,
            publication=publication,
            document=document,
            exam_id=exam.id,
            kind=KIND_ORIGINAL,
            data=raw,
            created_by_user_id=operator.id,
            **_physician_snapshot(profile, document),
        )
        document.current_version_id = version.id
        audit(
            db,
            "laudo_original_atribuido",
            entidade="report_documents",
            entidade_id=document.id,
            user_id=operator.id,
            request_id=_request_id(request),
            detalhes={
                "report_code": document.public_code,
                "exam_code": exam.public_code,
                "status": document.status,
                "origin_type": document.origin_type,
                # M25.17 — de qual campo estruturado do exame o local saiu.
                # Sem isso, um laudo com endereço errado não teria como ser
                # rastreado até a decisão que o escolheu.
                "origin_source": derived.source,
                "origin_partner_unit_id": document.origin_partner_unit_id,
                "physician_profile_id": profile.id,
                "assignment_id": assignment.id,
                "report_version_id": version.id,
            },
        )
        publication.commit()
    return {
        **ser_report_document(document, versions=[version]),
        "assignment": ser_report_assignment(assignment),
        "exam_code": exam.public_code,
    }


@router.get("")
def list_report_documents_operational(
    status: str | None = None,
    exam_code: str | None = None,
    incluir_arquivados: bool = False,
    # M25.24 — o "Todos" desta fila não pode misturar pendência de hoje com
    # histórico encerrado sem distinção: quem olha para contar trabalho
    # contaria errado. O padrão é a fila ATIVA; os encerrados têm visão
    # própria (`somente_encerrados`), e `incluir_encerrados` junta as duas
    # para quem quiser o quadro completo — sempre com o carimbo em cada
    # linha, nunca misturados sem etiqueta.
    incluir_encerrados: bool = False,
    somente_encerrados: bool = False,
    db: Session = Depends(get_db),
    _operator: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    statement = (
        select(ReportDocument, SpirometryExam, ReportAssignment, Person)
        .join(
            SpirometryExam,
            SpirometryExam.id == ReportDocument.spirometry_exam_id,
        )
        # M25.15 — `outerjoin` de propósito: um exame sem pessoa resolvível é
        # um defeito de dado, mas sumir com a linha inteira do acompanhamento
        # operacional esconderia justamente o caso que precisa ser visto.
        .outerjoin(Person, Person.id == SpirometryExam.person_id)
        .outerjoin(
            ReportAssignment,
            (ReportAssignment.report_document_id == ReportDocument.id)
            & ReportAssignment.active.is_(True),
        )
    )
    if status:
        if status not in CLINICAL_STATUSES:
            raise ReportDomainError(
                422, "status_laudo_invalido", "Status de laudo inválido."
            )
        statement = statement.where(ReportDocument.status == status)
    if exam_code:
        normalized = exam_code.strip().upper()
        if not _SAFE_EXAM_CODE_RE.fullmatch(normalized):
            raise ReportDomainError(
                422, "codigo_exame_invalido", "Código de exame inválido."
            )
        statement = statement.where(SpirometryExam.public_code == normalized)
    if not incluir_arquivados:
        # M25.17 — `is_not(True)` e não `is_(False)`: o join com Person é
        # externo, então um laudo cujo exame perdeu a pessoa produz NULL
        # aqui. Esse caso é defeito de dado e precisa continuar VISÍVEL no
        # acompanhamento; `is_(False)` o esconderia junto com os testes.
        statement = statement.where(Person.arquivado.is_not(True))
    if somente_encerrados:
        statement = statement.where(
            SpirometryExam.encerramento_motivo.is_not(None)
        )
    elif not incluir_encerrados:
        statement = statement.where(
            SpirometryExam.encerramento_motivo.is_(None)
        )
    rows = db.execute(
        statement.order_by(ReportDocument.created_at.desc()).limit(200)
    ).all()
    return [
        _technical_report_row(
            document,
            exam,
            assignment,
            include_assignment_ids=True,
            person=person,
            location=_queue_location(db, document, exam),
        )
        for document, exam, assignment, person in rows
    ]


@router.post("/{document_id}/reatribuir")
def reassign_report_document(
    document_id: str,
    payload: ReportReassignment,
    request: Request,
    db: Session = Depends(get_db),
    operator: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    document = _lock_document_or_404(db, document_id)
    if (
        document.status != STATUS_ATRIBUIDO
        or document.clinical_started_at is not None
    ):
        raise ReportDomainError(
            409,
            "reatribuicao_clinica_bloqueada",
            "A reatribuição só é permitida antes do primeiro rascunho clínico.",
        )
    previous = _active_assignment(db, document.id, lock=True)
    if (
        previous is None
        or previous.id != payload.expected_assignment_id
    ):
        raise ReportDomainError(
            409,
            "atribuicao_desatualizada",
            "A atribuição mudou; atualize a fila antes de tentar novamente.",
        )
    if previous.physician_profile_id == payload.physician_profile_id:
        raise ReportDomainError(
            409,
            "medico_ja_atribuido",
            "O médico selecionado já é o responsável ativo.",
        )
    new_profile, _account = _profile_for_assignment(
        db, payload.physician_profile_id
    )
    try:
        now = datetime.now(timezone.utc)
        previous.active = False
        previous.ended_at = now
        assignment = _create_assignment(
            db,
            document=document,
            profile=new_profile,
            performed_by_user_id=operator.id,
            reason_code=payload.reason_code,
            event_type="reassigned",
            previous=previous,
        )
        audit(
            db,
            "laudo_reatribuido",
            entidade="report_documents",
            entidade_id=document.id,
            user_id=operator.id,
            request_id=_request_id(request),
            detalhes={
                "status": document.status,
                "reason_code": payload.reason_code,
                "previous_assignment_id": previous.id,
                "assignment_id": assignment.id,
                "physician_profile_id": new_profile.id,
            },
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ReportDomainError(
            409,
            "atribuicao_concorrente",
            "A atribuição mudou em outra operação.",
        ) from None
    return {
        "report_code": document.public_code,
        "status": document.status,
        "assignment": ser_report_assignment(assignment),
    }


@router.post("/{document_id}/recuperar-medico-suspenso")
def recover_report_after_physician_unavailable(
    document_id: str,
    payload: ReportPhysicianRecovery,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ROLE_ADMIN)),
):
    """M24D (fecha F3) — único caminho para destravar um laudo cujo médico
    ficou indisponível DEPOIS do primeiro rascunho clínico. A reatribuição
    comum (`/reatribuir`) permanece bloqueada nesse ponto por desenho; esta
    rota exige papel admin, um motivo fechado dedicado, prova de que o
    médico anterior deixou de ser elegível, e nunca reescreve a prévia
    clínica já gravada — apenas encerra a atribuição antiga e abre uma nova.
    """

    document = _lock_document_or_404(db, document_id)
    if (
        document.status not in (STATUS_EM_ELABORACAO, STATUS_ASSINATURA_PENDENTE)
        or document.clinical_started_at is None
    ):
        raise ReportDomainError(
            409,
            "recuperacao_nao_aplicavel",
            "A recuperação só se aplica a laudos em elaboração clínica.",
        )
    previous = _active_assignment(db, document.id, lock=True)
    if previous is None or previous.id != payload.expected_assignment_id:
        raise ReportDomainError(
            409,
            "atribuicao_desatualizada",
            "A atribuição mudou; atualize a fila antes de tentar novamente.",
        )
    previous_profile = db.get(PhysicianProfile, previous.physician_profile_id)
    previous_account = (
        db.get(User, previous_profile.user_id) if previous_profile else None
    )
    still_eligible = (
        previous_profile is not None
        and previous_account is not None
        and previous_account.ativo
        and user_has_explicit_role(previous_account, ROLE_MEDICO)
        and previous_profile.active
        and previous_profile.verification_status == "verified"
    )
    if still_eligible:
        raise ReportDomainError(
            409,
            "medico_ainda_elegivel",
            "O médico atribuído continua ativo e verificado; use a reatribuição comum.",
        )
    new_profile, _account = _profile_for_assignment(db, payload.physician_profile_id)
    if new_profile.id == previous.physician_profile_id:
        raise ReportDomainError(
            409,
            "medico_ja_atribuido",
            "O médico selecionado já é o responsável ativo.",
        )
    try:
        previous.active = False
        previous.ended_at = datetime.now(timezone.utc)
        assignment = _create_assignment(
            db,
            document=document,
            profile=new_profile,
            performed_by_user_id=admin.id,
            reason_code="physician_unavailable_after_draft",
            event_type="recovered_after_draft",
            previous=previous,
        )
        # A prévia clínica do médico anterior permanece imutável (guarda
        # ORM + trigger PostgreSQL); apenas o ponteiro de atribuição e o
        # status do documento voltam para "atribuido" para permitir que o
        # novo médico inicie sua própria elaboração do zero.
        document.status = STATUS_ATRIBUIDO
        document.clinical_started_at = None
        audit(
            db,
            "laudo_medico_recuperado",
            entidade="report_documents",
            entidade_id=document.id,
            user_id=admin.id,
            request_id=_request_id(request),
            detalhes={
                "status": document.status,
                "previous_assignment_id": previous.id,
                "assignment_id": assignment.id,
                "physician_profile_id": new_profile.id,
            },
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ReportDomainError(
            409,
            "atribuicao_concorrente",
            "A atribuição mudou em outra operação.",
        ) from None
    return {
        "report_code": document.public_code,
        "status": document.status,
        "assignment": ser_report_assignment(assignment),
    }


# --------------------------------------------------------- fila do médico


@router.get("/meus")
def list_my_report_queue(
    status: str | None = None,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    # M25.17 — a fila clínica NÃO tem modo técnico: a médica nunca deve
    # receber cadastro de teste, nem por engano nem por parâmetro de URL.
    # Auditar cenário antigo é trabalho de administração, em outra tela.
    profile = _require_active_physician(db, physician_user)
    if status and status not in CLINICAL_STATUSES:
        raise ReportDomainError(
            422, "status_laudo_invalido", "Status de laudo inválido."
        )
    statement = (
        select(ReportDocument, SpirometryExam, ReportAssignment, Person)
        .join(
            ReportAssignment,
            (ReportAssignment.report_document_id == ReportDocument.id)
            & ReportAssignment.active.is_(True),
        )
        .join(
            SpirometryExam,
            SpirometryExam.id == ReportDocument.spirometry_exam_id,
        )
        .outerjoin(Person, Person.id == SpirometryExam.person_id)
        # O isolamento continua sendo ESTE filtro, e ele não mudou com a
        # M25.15: a médica vê apenas as atribuições ativas do próprio perfil.
        # O nome do paciente entra na linha DEPOIS desse recorte, então não
        # amplia em nada o conjunto de laudos visível a ela.
        .where(ReportAssignment.physician_profile_id == profile.id)
        .where(Person.arquivado.is_not(True))
        # M25.24 — exame encerrado como histórico NUNCA volta a aparecer
        # para a médica como trabalho novo, em nenhum filtro, nem em
        # "Todos". Se a operação precisar dele de volta, o caminho é
        # reabrir explicitamente — e aí ele reaparece aqui.
        .where(SpirometryExam.encerramento_motivo.is_(None))
    )
    if status:
        statement = statement.where(ReportDocument.status == status)
    rows = db.execute(
        statement.order_by(ReportAssignment.assigned_at.desc()).limit(200)
    ).all()
    return [
        _technical_report_row(
            document,
            exam,
            assignment,
            location=_queue_location(db, document, exam),
            person=person,
        )
        for document, exam, assignment, person in rows
    ]


@router.get("/{document_id}")
def get_report_document(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # M24D (fecha F4) — a checagem de papel roda ANTES da busca por id, para
    # que um papel sem qualquer acesso a laudos (ex.: leitura) receba a
    # MESMA resposta para id existente e inexistente, em vez do padrão
    # anterior (403 para existente, 404 para inexistente) que servia de
    # oráculo de existência entre papéis.
    is_medico = user_has_explicit_role(user, ROLE_MEDICO)
    is_operacional = ROLE_OPERACIONAL in user_effective_roles(user)
    if not is_medico and not is_operacional:
        raise ReportDomainError(
            403, "permissao_insuficiente", "Permissão insuficiente."
        )
    document = _get_document_or_404(db, document_id)
    if is_medico:
        profile, assignment = _require_assigned_physician(db, user, document)
        versions = _all_versions(db, document.id)
        exam = db.get(SpirometryExam, document.spirometry_exam_id)
        person = db.get(Person, exam.person_id) if exam else None
        if exam is None or person is None:
            raise ReportDomainError(
                409,
                "identidade_documento_indisponivel",
                "A identidade necessária ao documento não está disponível.",
            )
        return {
            **ser_report_document(
                document, versions=versions, include_clinical=True
            ),
            "assignment": ser_report_assignment(assignment),
            "physician": ser_physician_profile(profile),
            # M25.7 — a tela da médica precisa saber se a assinatura
            # qualificada existe neste ambiente. Só o BOOLEANO: o diagnóstico
            # completo é admin-only, e nem ele expõe valores.
            "assinatura_qualificada_disponivel": (
                get_settings().integraicp_ready()
            ),
            "exam": {
                "public_code": exam.public_code,
                "exam_date": d(exam.data_exame),
                # M25.3 — a médica precisa ver o mesmo contexto de exame que
                # será impresso no laudo, antes de escolher a conclusão.
                "exam_time": exam.hora_exame,
                "post_bronchodilator": exam.broncodilatador,
                "clinical_indication": exam.indicacao_clinica,
            },
            # M25.3 — local de realização ESTRUTURADO (dado institucional da
            # clínica, nunca do paciente). Antes a tela mostrava apenas o
            # código técnico da origem, e o local só aparecia dentro do PDF.
            "location": resolve_report_location(
                db, document=document, exam=exam
            ).as_payload(),
            # Somente este workspace atribuído contém identidade do paciente.
            # Ela nunca entra na fila, URL ou audit details.
            "patient": {
                "public_code": person.public_code,
                "full_name": person.nome_completo,
                "date_of_birth": d(person.data_nascimento),
            },
        }
    exam = db.get(SpirometryExam, document.spirometry_exam_id)
    assignment = _active_assignment(db, document.id)
    if exam is None:
        raise ReportDomainError(
            409, "exame_nao_encontrado", "Exame do laudo não encontrado."
        )
    return _technical_report_row(
        document,
        exam,
        assignment,
        person=db.get(Person, exam.person_id),
        location=_queue_location(db, document, exam),
    )


@router.get("/{document_id}/versoes/{version_id}/conteudo")
def download_report_version(
    document_id: str,
    version_id: str,
    request: Request,
    modo: str = Query(default="inline", pattern="^(inline|download)$"),
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    # M24D (fecha F4) — checa o papel explícito ANTES da busca por id; sem
    # isso, um papel sem `medico` recebia 403 para id existente e 404 para
    # id inexistente, um oráculo de existência.
    if not user_has_explicit_role(physician_user, ROLE_MEDICO):
        raise ReportDomainError(
            403,
            "papel_medico_explicito_necessario",
            "O download exige o papel médico explícito.",
        )
    document = _get_document_or_404(db, document_id)
    _profile, _assignment = _require_assigned_physician(
        db, physician_user, document
    )
    version = _get_version_or_404(db, document_id, version_id)
    stored = _read_stored_version(version, missing_status=404)
    exam = db.get(SpirometryExam, document.spirometry_exam_id)
    person = db.get(Person, exam.person_id) if exam is not None else None
    # M25.17 — nome humano. Antes saía
    # `laudo-ESP-000017-v3-laudo_liberado.pdf`: correto e ilegível. Quem
    # recebe o arquivo reconhece o paciente, não o código da versão.
    #
    # O texto do PDF continua sendo a autoridade sobre o tipo de liberação:
    # "Assinado" aqui nomeia o ARQUIVO entregue, e não altera
    # `signature_status`, o selo nem a declaração impressa.
    # M25.20 — a versão que voltou assinada por fora é o arquivo "Assinado",
    # não "Para assinatura". Sem este ramo, a médica que reabrisse o
    # documento assinado baixaria um arquivo com o nome do que ela já tinha
    # levado para assinar — dois arquivos diferentes com o mesmo nome na
    # pasta de downloads é exatamente como se assina o documento errado.
    if version.kind == KIND_LAUDO_ASSINADO_EXTERNO:
        safe_name = named_download_filename(
            patient_name=person.nome_completo if person is not None else None,
            fallback_code=document.public_code,
            sufixo=SUFIXO_ASSINADO,
        )
    else:
        safe_name = report_download_filename(
            patient_name=person.nome_completo if person is not None else None,
            fallback_code=document.public_code,
            is_technical_exam=version.kind == KIND_ORIGINAL,
        )
    disposition = "attachment" if modo == "download" else "inline"
    audit(
        db,
        "laudo_conteudo_entregue",
        entidade="report_documents",
        entidade_id=document.id,
        user_id=physician_user.id,
        request_id=_request_id(request),
        detalhes={
            "report_version_id": version.id,
            "delivery_mode": modo,
            "institutional_status": document.status,
        },
    )
    db.commit()
    return Response(
        content=stored.data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                safe_name, disposition=disposition
            ),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ---------------------------------------------------------- fluxo clínico


@router.post("/{document_id}/compor")
def compose_report_document(
    document_id: str,
    payload: ReportDocumentCompose,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    document = _lock_document_or_404(db, document_id)
    profile, _assignment = _require_assigned_physician(
        db, physician_user, document, lock=True
    )
    if document.status not in {STATUS_ATRIBUIDO, STATUS_EM_ELABORACAO}:
        raise ReportDomainError(
            409,
            "laudo_fora_de_elaboracao",
            "O laudo não aceita nova prévia neste estado.",
        )
    original_version = _version_by_kind(db, document.id, KIND_ORIGINAL)
    if original_version is None:
        raise ReportDomainError(
            409, "pdf_original_ausente", "Laudo sem PDF original."
        )
    original = _read_stored_version(original_version)

    ensure_m24c_catalog(db)
    template: ReportTemplate | None = None
    if payload.template_id:
        template = _selectable_template(db, payload.template_id)
        template_id = template.id
        template_code = template.codigo
        template_version = template.versao
        template_text = template.texto_completo
    else:
        template_id = None
        template_code = "CONTROLLED_TEXT"
        template_version = 1
        template_text = ""

    settings = get_settings()
    # M24D — em modo piloto, todo PDF gerado carrega o aviso PILOTO INTERNO
    # (nunca o rodapé genérico de TESTE). O router só chega aqui com
    # reports_mode == "pilot" (disabled/production são bloqueados antes),
    # mas a checagem explícita é defensiva.
    is_pilot = settings.reports_mode == "pilot"
    footer_code = PILOT_FOOTER_CODE if is_pilot else TEST_FOOTER_CODE
    footer = db.execute(
        select(ReportFooterTemplate).where(
            ReportFooterTemplate.code == footer_code,
            ReportFooterTemplate.version == 1,
            ReportFooterTemplate.active.is_(True),
        )
    ).scalar_one_or_none()
    if footer is None:
        raise ReportDomainError(
            503,
            "rodape_indisponivel",
            "O rodapé exigido para este modo não está disponível.",
        )
    exam = db.get(SpirometryExam, document.spirometry_exam_id)
    if exam is None:
        raise ReportDomainError(
            409, "exame_nao_encontrado", "Exame do laudo não encontrado."
        )
    next_number = _next_version_number(db, document.id)
    issued_at = datetime.now(timezone.utc)
    render_footer = _render_pilot_footer if is_pilot else _render_test_footer
    footer_text = render_footer(
        footer,
        profile=profile,
        document=document,
        exam=exam,
        issued_at=issued_at,
        version_number=next_number,
    )
    try:
        composed = compose_report_pdf(
            original_bytes=original.data,
            page_number=payload.page_number,
            placement=payload.placement,
            interpretation_text=payload.interpretation_text,
            footer_text=footer_text,
            max_size_bytes=settings.reports_max_upload_bytes,
        )
    except PdfCompositionError as exc:
        raise ReportDomainError(422, exc.codigo, exc.mensagem) from None

    with report_publication_transaction(db) as publication:
        version = _store_new_version(
            db,
            publication=publication,
            document=document,
            exam_id=document.spirometry_exam_id,
            kind=KIND_RASCUNHO,
            data=composed.data,
            created_by_user_id=physician_user.id,
            expected_version_number=next_number,
            template_id=template_id,
            template_code_snapshot=template_code,
            template_version_snapshot=template_version,
            template_text_snapshot=template_text,
            interpretation_text_snapshot=payload.interpretation_text,
            footer_template_id=footer.id,
            footer_code_snapshot=footer.code,
            footer_version_snapshot=footer.version,
            footer_text_snapshot=footer_text,
            issued_at_snapshot=issued_at,
            page_number=payload.page_number,
            placement=payload.placement,
            **_physician_snapshot(profile, document),
        )
        document.current_version_id = version.id
        document.status = STATUS_EM_ELABORACAO
        if document.clinical_started_at is None:
            document.clinical_started_at = issued_at
        audit(
            db,
            "laudo_previa_clinica_criada",
            entidade="report_documents",
            entidade_id=document.id,
            user_id=physician_user.id,
            request_id=_request_id(request),
            detalhes={
                "status": document.status,
                "report_version_id": version.id,
                "template_id": template_id,
                "template_version": template_version,
                "page_number": payload.page_number,
                "placement": payload.placement,
            },
        )
        publication.commit()
    return ser_report_document(
        document, versions=[version], include_clinical=True
    )


def _validate_ready_snapshot(
    db: Session,
    *,
    draft: ReportDocumentVersion,
    profile: PhysicianProfile,
    document: ReportDocument,
) -> None:
    required_text = (
        draft.template_code_snapshot,
        draft.template_version_snapshot,
        draft.template_text_snapshot,
        draft.template_text_sha256,
        draft.interpretation_text_snapshot,
        draft.interpretation_text_sha256,
        draft.footer_template_id,
        draft.footer_code_snapshot,
        draft.footer_version_snapshot,
        draft.footer_text_snapshot,
        draft.footer_text_sha256,
        draft.issued_at_snapshot,
    )
    if any(value is None for value in required_text):
        raise ReportDomainError(
            409,
            "snapshot_clinico_incompleto",
            "A prévia não possui evidência clínica completa.",
        )
    # M24D — o aviso exigido no rodapé congelado depende de qual rodapé foi
    # usado na prévia: PILOTO INTERNO em modo piloto, MODELO DE TESTE fora
    # dele. Nunca aceita nenhum dos dois quando o código não bate.
    expected_warning = (
        PILOT_WARNING
        if draft.footer_code_snapshot == PILOT_FOOTER_CODE
        else _UNSIGNED_WARNING
    )
    if (
        draft.template_text_sha256
        != _snapshot_hash(draft.template_text_snapshot or "")
        or draft.interpretation_text_sha256
        != _snapshot_hash(draft.interpretation_text_snapshot or "")
        or draft.footer_text_sha256
        != _snapshot_hash(draft.footer_text_snapshot or "")
        or expected_warning not in (draft.footer_text_snapshot or "")
    ):
        raise ReportDomainError(
            409,
            "snapshot_clinico_invalido",
            "A evidência clínica imutável está divergente.",
        )
    current_identity = (
        profile.id,
        profile.professional_name,
        profile.crm_number,
        profile.crm_state,
        profile.rqe,
        document.origin_type,
        document.origin_label,
        document.origin_partner_unit_id,
    )
    draft_identity = (
        draft.physician_profile_id_snapshot,
        draft.physician_name_snapshot,
        draft.physician_crm_number_snapshot,
        draft.physician_crm_state_snapshot,
        draft.physician_rqe_snapshot,
        draft.origin_type_snapshot,
        draft.origin_label_snapshot,
        draft.origin_partner_unit_id_snapshot,
    )
    if current_identity != draft_identity:
        raise ReportDomainError(
            409,
            "snapshot_autoria_desatualizado",
            "O perfil médico ou a origem mudou; gere uma nova prévia.",
        )
    if draft.template_id:
        template = db.get(ReportTemplate, draft.template_id)
        if (
            template is None
            or not template.ativo
            or template.codigo != draft.template_code_snapshot
            or template.versao != draft.template_version_snapshot
        ):
            raise ReportDomainError(
                409,
                "template_snapshot_indisponivel",
                "O template da prévia não está mais disponível.",
            )
        settings = get_settings()
        test_override = (
            settings.env == "dev"
            and settings.reports_test_allow_provisional_templates
        )
        if not (
            template.status == "approved" and template.clinically_approved
        ) and not (
            test_override
            and template.codigo in PROVISIONAL_CODES
            and template.texto_completo == PROVISIONAL_WARNING
        ):
            raise ReportDomainError(
                409,
                "template_nao_aprovado",
                "O template não está aprovado para preparação de assinatura.",
            )


@router.post("/{document_id}/preparar-assinatura")
def prepare_report_signature(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    document = _lock_document_or_404(db, document_id)
    profile, _assignment = _require_assigned_physician(
        db, physician_user, document, lock=True
    )
    if document.status != STATUS_EM_ELABORACAO:
        raise ReportDomainError(
            409,
            "laudo_fora_de_elaboracao",
            "Somente uma prévia em elaboração pode seguir para assinatura.",
        )
    draft = db.get(ReportDocumentVersion, document.current_version_id)
    if (
        draft is None
        or draft.report_document_id != document.id
        or draft.kind != KIND_RASCUNHO
    ):
        # M25.14 — a mensagem antiga ("Gere uma prévia antes de preparar a
        # assinatura") era impossível de cumprir: a médica com a prévia do
        # laudo já gerada recebia ordem de gerar o que já existia. O que este
        # passo exige é o rascunho do OUTRO caminho, o da anotação sobre o PDF
        # da MIR.
        raise ReportDomainError(
            409,
            "rascunho_composto_ausente",
            "Este passo pertence ao fluxo de anotação técnica sobre o PDF da "
            "MIR e exige que essa anotação já esteja composta. A prévia do "
            "laudo SoproLife não se aplica aqui — para liberar o laudo "
            "próprio, use “Assinar e liberar laudo”.",
        )
    _validate_ready_snapshot(
        db, draft=draft, profile=profile, document=document
    )
    stored = _read_stored_version(draft)
    provider = get_signature_provider()
    provider_result = provider.request_signature(
        document_bytes=stored.data,
        document_sha256=draft.sha256,
        requested_by_user_id=physician_user.id,
    )
    if (
        provider.name != "unconfigured"
        or provider_result.provider != "unconfigured"
        or provider_result.status != SIGNATURE_STATUS_PENDENTE
        or provider_result.external_reference is not None
        or provider_result.verification_metadata is not None
    ):
        raise ReportDomainError(
            503,
            "provedor_assinatura_nao_autorizado",
            "Nenhum provedor de assinatura está configurado.",
        )

    with report_publication_transaction(db) as publication:
        pending = _store_new_version(
            db,
            publication=publication,
            document=document,
            exam_id=document.spirometry_exam_id,
            kind=KIND_ASSINATURA_PENDENTE,
            data=stored.data,
            created_by_user_id=physician_user.id,
            template_id=draft.template_id,
            template_code_snapshot=draft.template_code_snapshot,
            template_version_snapshot=draft.template_version_snapshot,
            template_text_snapshot=draft.template_text_snapshot,
            interpretation_text_snapshot=draft.interpretation_text_snapshot,
            physician_profile_id_snapshot=draft.physician_profile_id_snapshot,
            physician_name_snapshot=draft.physician_name_snapshot,
            physician_crm_number_snapshot=draft.physician_crm_number_snapshot,
            physician_crm_state_snapshot=draft.physician_crm_state_snapshot,
            physician_rqe_snapshot=draft.physician_rqe_snapshot,
            origin_type_snapshot=draft.origin_type_snapshot,
            origin_label_snapshot=draft.origin_label_snapshot,
            origin_partner_unit_id_snapshot=(
                draft.origin_partner_unit_id_snapshot
            ),
            footer_template_id=draft.footer_template_id,
            footer_code_snapshot=draft.footer_code_snapshot,
            footer_version_snapshot=draft.footer_version_snapshot,
            footer_text_snapshot=draft.footer_text_snapshot,
            issued_at_snapshot=draft.issued_at_snapshot,
            page_number=draft.page_number,
            placement=draft.placement,
        )
        now = datetime.now(timezone.utc)
        document.status = STATUS_ASSINATURA_PENDENTE
        document.signature_status = SIGNATURE_STATUS_PENDENTE
        document.ready_for_signature_at = now
        document.current_version_id = pending.id
        signature = ReportSignature(
            report_document_version_id=pending.id,
            provider="unconfigured",
            status=SIGNATURE_STATUS_PENDENTE,
            requested_by_user_id=physician_user.id,
            requested_at=now,
            error_message=provider_result.error_message,
        )
        db.add(signature)
        audit(
            db,
            "laudo_preparado_assinatura",
            entidade="report_documents",
            entidade_id=document.id,
            user_id=physician_user.id,
            request_id=_request_id(request),
            detalhes={
                "status": document.status,
                "signature_status": SIGNATURE_STATUS_PENDENTE,
                "provider": "unconfigured",
                "report_version_id": pending.id,
            },
        )
        publication.commit()
    return {
        **ser_report_document(
            document, versions=[pending], include_clinical=True
        ),
        "signature": ser_report_signature(signature),
    }


def _qualified_signature_evidence(
    signature: ReportSignature,
    version: ReportDocumentVersion,
    profile: PhysicianProfile,
) -> bool:
    metadata = signature.verification_metadata
    return bool(
        signature.status == "assinada"
        and signature.provider
        and signature.provider != "unconfigured"
        and signature.external_reference
        and signature.completed_at
        and isinstance(metadata, dict)
        and metadata.get("qualified_signature") is True
        and metadata.get("standard") == "PAdES"
        and metadata.get("trust_chain") == "ICP-Brasil"
        and metadata.get("signer_physician_profile_id") == profile.id
        and metadata.get("document_sha256") == version.sha256
    )


def _seal_signature_kind(
    db: Session,
    *,
    version: ReportDocumentVersion | None,
    profile: PhysicianProfile,
) -> str:
    """Tipo de assinatura que o SELO do laudo deve declarar.

    Lê a evidência realmente gravada, com o mesmo critério do portão
    `_qualified_signature_evidence`. Enquanto nenhum provedor ICP-Brasil
    estiver conectado, a resposta é sempre a liberação institucional — o
    selo não pode prometer o que o sistema ainda não faz. Quando o provedor
    entrar, esta função passa a devolver o tipo qualificado sozinha, sem
    nenhuma mudança no desenho do laudo.
    """

    if version is None:
        return SIGNATURE_KIND_INSTITUTIONAL
    signature = db.execute(
        select(ReportSignature).where(
            ReportSignature.report_document_version_id == version.id
        )
    ).scalar_one_or_none()
    if signature is not None and _qualified_signature_evidence(
        signature, version, profile
    ):
        return SIGNATURE_KIND_QUALIFIED_ICP
    return SIGNATURE_KIND_INSTITUTIONAL


# --------------------------------- M25.8 — assinatura externa em lote


def _laudos_aguardando_assinatura(db: Session, *, profile_id: str):
    """Laudos DESTA médica congelados e esperando assinatura externa.

    O filtro por médica é aplicado aqui, no servidor. A lista que o
    navegador manda só pode ESTREITAR esta seleção, nunca ampliá-la.
    """

    return db.execute(
        select(ReportDocument, SpirometryExam, ReportAssignment)
        .join(
            ReportAssignment,
            (ReportAssignment.report_document_id == ReportDocument.id)
            & ReportAssignment.active.is_(True),
        )
        .join(
            SpirometryExam,
            SpirometryExam.id == ReportDocument.spirometry_exam_id,
        )
        .where(
            ReportAssignment.physician_profile_id == profile_id,
            ReportDocument.status == STATUS_ASSINATURA_PENDENTE,
        )
        .order_by(ReportDocument.public_code)
    ).all()


@router.post("/{document_id}/finalizar-revisao", status_code=201)
def finalize_review_for_signature(
    document_id: str,
    payload: ReportReleaseRequest,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Congela o laudo revisado e o coloca em "aguardando assinatura".

    Este estado NÃO é assinado, NÃO é liberado e NÃO vai ao paciente. Ele
    existe para que a médica possa revisar vários exames em sequência e só
    depois assinar todos fora do painel, com o certificado dela.

    Exige as mesmas provas da liberação institucional — prévia atual e texto
    conferido —, porque a partir daqui o conteúdo fica imutável: o PDF
    congelado é exatamente o que será assinado.
    """

    from ..services.external_signature import (
        ExternalSignatureError,
        stamp_signing_metadata,
    )

    _require_reports_enabled()
    settings = get_settings()
    document = _lock_document_or_404(db, document_id)
    profile, _assignment = _require_assigned_physician(
        db, physician_user, document, lock=True
    )
    if document.status == STATUS_LAUDO_LIBERADO:
        raise ReportDomainError(
            409,
            "laudo_ja_liberado",
            "Este laudo já foi liberado; use adendo ou versão corretiva.",
        )
    if document.status == STATUS_ASSINATURA_PENDENTE:
        raise ReportDomainError(
            409,
            "laudo_ja_aguardando_assinatura",
            "Este laudo já está aguardando assinatura digital.",
        )
    if document.status != STATUS_EM_ELABORACAO:
        raise ReportDomainError(
            409,
            "laudo_fora_de_elaboracao",
            "Gere a prévia do laudo antes de finalizar a revisão.",
        )
    draft = db.get(ReportDocumentVersion, document.current_version_id)
    if (
        draft is None
        or draft.report_document_id != document.id
        or draft.kind != KIND_LAUDO_PREVIA
    ):
        raise ReportDomainError(
            409,
            "previa_laudo_ausente",
            "Gere a prévia do laudo antes de finalizar a revisão.",
        )
    if payload.expected_version_id != draft.id:
        raise ReportDomainError(
            409,
            "previa_desatualizada",
            "A prévia mudou desde a conferência; revise antes de finalizar.",
        )
    if payload.expected_text_sha256 != (draft.interpretation_text_sha256 or ""):
        raise ReportDomainError(
            409,
            "conteudo_divergente_da_previa",
            "O texto conferido não corresponde ao texto atual da prévia.",
        )

    exam, person, location = _document_context(db, document)
    resolved_signature = _resolve_signature(db, profile.id)
    now = datetime.now(timezone.utc)
    version_number = _next_version_number(db, document.id)
    # O código entra no PDF (texto e QR), então precisa existir ANTES de
    # congelar. Mas só pode ser GRAVADO no documento junto com a mudança de
    # status: a constraint de coerência recusa código em laudo que ainda
    # está em elaboração. Por isso ele vive numa variável até lá.
    validation_code = document.validation_code or generate_validation_code()

    content = build_native_content(
        db,
        document=document,
        exam=exam,
        person=person,
        profile=profile,
        location=location,
        version_number=version_number,
        conclusion_text=draft.interpretation_text_snapshot or "",
        observations=draft.observations_snapshot,
        issued_at=now,
        released=True,
        released_at=now,
        validation_code=validation_code,
        signature_image=(
            resolved_signature.image if resolved_signature else None
        ),
        # Ainda não há assinatura qualificada: o selo declara a liberação
        # institucional até a assinatura externa ser validada de volta.
        signature_kind=SIGNATURE_KIND_INSTITUTIONAL,
        addenda=load_addenda(db, document.id),
        pilot_warning=_pilot_warning(),
    )
    try:
        carimbado = stamp_signing_metadata(
            _native_pdf_bytes(content),
            document_code=document.public_code,
            version_number=version_number,
            physician_name=profile.professional_name,
            crm=f"CRM-{profile.crm_state} {profile.crm_display or profile.crm_number}",
        )
    except ExternalSignatureError as exc:
        raise ReportDomainError(500, exc.codigo, exc.mensagem) from None

    with report_publication_transaction(db) as publication:
        congelada = _publish_native_version(
            db,
            publication=publication,
            document=document,
            exam=exam,
            profile=profile,
            kind=KIND_ASSINATURA_PENDENTE,
            data=carimbado,
            created_by_user_id=physician_user.id,
            final_text=draft.interpretation_text_snapshot or "",
            extra={},
        )
        # Status primeiro: só depois dele o código de validação é aceito
        # pela constraint de coerência de liberação.
        document.status = STATUS_ASSINATURA_PENDENTE
        document.validation_code = validation_code
        document.current_version_id = congelada.id
        document.signature_prepared_at = now
        document.ready_for_signature_at = now
        audit(
            db,
            "laudo_revisao_finalizada_para_assinatura",
            entidade="report_documents",
            entidade_id=document.id,
            user_id=physician_user.id,
            request_id=_request_id(request),
            detalhes={
                "versao": congelada.id,
                "hash_preparado": congelada.sha256,
            },
        )
        publication.commit()
    db.commit()
    return {
        "document_id": document.id,
        "report_code": document.public_code,
        "status": document.status,
        "versao_preparada_id": congelada.id,
        "hash_preparado_sha256": congelada.sha256,
        "versao": version_number,
    }


@router.post("/lote/baixar")
def download_signing_batch(
    payload: BatchDownloadRequest,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Devolve um ZIP com os laudos revisados prontos para assinatura.

    Só entram laudos JÁ revisados e atribuídos a quem pediu — um exame ainda
    em elaboração nunca cai no pacote, mesmo que o id venha na lista.
    """

    from ..services.external_signature import (
        BatchEntry,
        ExternalSignatureError,
        build_signing_package,
    )

    _require_reports_enabled()
    settings = get_settings()
    profile = _require_active_physician(db, physician_user)
    linhas = _laudos_aguardando_assinatura(db, profile_id=profile.id)
    escolhidos = set(payload.document_ids)
    if escolhidos:
        linhas = [linha for linha in linhas if linha[0].id in escolhidos]
    if not linhas:
        raise ReportDomainError(
            409,
            "lote_vazio",
            "Nenhum laudo revisado e aguardando assinatura foi encontrado.",
        )

    entradas: list[BatchEntry] = []
    for document, exam, _assignment in linhas:
        versao = db.get(ReportDocumentVersion, document.current_version_id)
        if versao is None or versao.kind != KIND_ASSINATURA_PENDENTE:
            continue
        person = db.get(Person, exam.person_id)
        mir = None
        if payload.incluir_mir:
            original = db.execute(
                select(ReportDocumentVersion)
                .where(
                    ReportDocumentVersion.report_document_id == document.id,
                    ReportDocumentVersion.kind == KIND_ORIGINAL,
                )
                .order_by(ReportDocumentVersion.version_number)
            ).scalars().first()
            if original is not None:
                mir = _read_stored_version(original).data
        entradas.append(BatchEntry(
            document_code=document.public_code,
            version_number=versao.version_number,
            patient_reference=(person.public_code if person else "SEM-REGISTRO"),
            prepared_sha256=versao.sha256,
            exam_code=exam.public_code,
            physician_name=profile.professional_name,
            crm=f"CRM-{profile.crm_state} {profile.crm_display or profile.crm_number}",
            rqe=profile.rqe,
            prepared_at=document.signature_prepared_at,
            pdf=_read_stored_version(versao).data,
            mir_pdf=mir,
        ))

    try:
        pacote = build_signing_package(
            entradas,
            include_mir=payload.incluir_mir,
            validation_base_url=settings.reports_validation_base_url,
        )
    except ExternalSignatureError as exc:
        raise ReportDomainError(409, exc.codigo, exc.mensagem) from None

    agora = datetime.now(timezone.utc)
    for document, _exam, _assignment in linhas:
        document.signature_downloaded_at = agora
    audit(
        db,
        "laudos_baixados_para_assinatura",
        entidade="report_documents",
        entidade_id=None,
        user_id=physician_user.id,
        request_id=_request_id(request),
        detalhes={
            "quantidade": len(entradas),
            "incluiu_mir": payload.incluir_mir,
        },
    )
    db.commit()
    nome = f"laudos-para-assinar-{agora.strftime('%Y%m%d-%H%M')}.zip"
    return Response(
        content=pacote,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/lote/enviar")
async def upload_signed_batch(
    request: Request,
    arquivos: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Recebe os PDFs assinados e valida UM A UM.

    Um arquivo ruim nunca derruba o lote: cada um recebe seu próprio
    veredito, e só os aprovados mudam para "assinado e liberado". Os demais
    ficam esperando correção, com a versão anterior intacta.
    """

    from ..services.external_signature import (
        VALIDATION_DUPLICADO,
        VALIDATION_FALHA_TECNICA,
        VALIDATION_NAO_ENCONTRADO,
        FileVerdict,
        extract_pdfs,
        read_signing_marker,
        summarize,
        verify_signed_pdf,
    )

    _require_reports_enabled()
    settings = get_settings()
    profile = _require_active_physician(db, physician_user)

    brutos: list[tuple[str, bytes]] = []
    for enviado in arquivos:
        dados = await _read_upload_bounded(
            enviado, max_size_bytes=settings.reports_max_upload_bytes
        )
        brutos.append((enviado.filename or "arquivo.pdf", dados))
    itens = extract_pdfs(brutos)
    if not itens:
        raise ReportDomainError(
            422, "envio_vazio", "Nenhum PDF foi encontrado no envio."
        )

    veredictos: list[FileVerdict] = []
    ja_vistos: set[str] = set()
    for nome, dados in itens:
        marcador = read_signing_marker(dados)
        if marcador is None:
            veredictos.append(FileVerdict(
                filename=nome,
                outcome=VALIDATION_NAO_ENCONTRADO,
                message="O arquivo não traz a identificação da SoproLife.",
            ))
            continue
        if marcador.document_code in ja_vistos:
            veredictos.append(FileVerdict(
                filename=nome,
                outcome=VALIDATION_DUPLICADO,
                document_code=marcador.document_code,
                message="Este laudo apareceu duas vezes no mesmo envio.",
            ))
            continue
        ja_vistos.add(marcador.document_code)

        document = db.execute(
            select(ReportDocument)
            .where(ReportDocument.public_code == marcador.document_code)
            .with_for_update()
        ).scalar_one_or_none()
        if document is None:
            veredictos.append(FileVerdict(
                filename=nome,
                outcome=VALIDATION_NAO_ENCONTRADO,
                document_code=marcador.document_code,
                message="Laudo não encontrado neste painel.",
            ))
            continue
        try:
            dono, _atribuicao = _require_assigned_physician(
                db, physician_user, document, lock=True
            )
        except ReportDomainError:
            veredictos.append(FileVerdict(
                filename=nome,
                outcome=VALIDATION_NAO_ENCONTRADO,
                document_code=marcador.document_code,
                message="Este laudo não está atribuído a você.",
            ))
            continue
        # Reenvio de algo já fechado — assinado com certificado OU liberado
        # institucionalmente. Nos dois casos é duplicata, não "não
        # encontrado": a médica precisa saber que aquele laudo já está
        # pronto, e não que o arquivo dela é estranho ao sistema.
        if document.status in (STATUS_ASSINADO, STATUS_LAUDO_LIBERADO):
            veredictos.append(FileVerdict(
                filename=nome,
                outcome=VALIDATION_DUPLICADO,
                document_code=marcador.document_code,
                message="Este laudo já foi assinado e liberado.",
            ))
            continue
        if document.status != STATUS_ASSINATURA_PENDENTE:
            veredictos.append(FileVerdict(
                filename=nome,
                outcome=VALIDATION_NAO_ENCONTRADO,
                document_code=marcador.document_code,
                message="Este laudo não está aguardando assinatura.",
            ))
            continue

        preparada = db.get(ReportDocumentVersion, document.current_version_id)
        if preparada is None or preparada.kind != KIND_ASSINATURA_PENDENTE:
            veredictos.append(FileVerdict(
                filename=nome,
                outcome=VALIDATION_FALHA_TECNICA,
                document_code=marcador.document_code,
                message="A versão preparada deste laudo não está disponível.",
            ))
            continue
        preparado_bytes = _read_stored_version(preparada).data
        esperado = read_signing_marker(preparado_bytes)
        veredicto = verify_signed_pdf(
            filename=nome,
            signed=dados,
            prepared=preparado_bytes,
            expected_marker=esperado,
            expected_signer_subject=dono.icp_signer_subject,
        )
        if not veredicto.ok:
            veredictos.append(veredicto)
            continue

        exam, _person, _location = _document_context(db, document)
        agora = datetime.now(timezone.utc)
        with report_publication_transaction(db) as publication:
            liberada = _publish_native_version(
                db,
                publication=publication,
                document=document,
                exam=exam,
                profile=dono,
                kind=KIND_LAUDO_LIBERADO,
                data=dados,
                created_by_user_id=physician_user.id,
                final_text=preparada.interpretation_text_snapshot or "",
                extra={},
            )
            # Assinatura QUALIFICADA termina em 'assinado', não em
            # 'liberado'. `liberado` é, por desenho da M25.2, a liberação
            # institucional — e a constraint de coerência clínica exige que
            # ele venha com `signed_at` NULO e assinatura institucional.
            # Misturar os dois estados apagaria justamente a distinção que o
            # projeto criou entre assinar com certificado e liberar sem ele.
            document.status = STATUS_ASSINADO
            document.current_version_id = liberada.id
            document.signed_at = agora
            document.signature_status = SIGNATURE_STATUS_ASSINADA
            # Vínculo do certificado na PRIMEIRA assinatura validada: a
            # partir daqui, um laudo assinado por outro certificado é
            # recusado sem precisar de cadastro manual.
            if not dono.icp_signer_subject and veredicto.signer_subject:
                dono.icp_signer_subject = veredicto.signer_subject[:300]
                dono.icp_signer_bound_at = agora
            db.add(ReportSignature(
                report_document_version_id=liberada.id,
                provider="vidaas_externo",
                status=SIGNATURE_STATUS_ASSINADA,
                external_reference=veredicto.signed_sha256,
                requested_by_user_id=physician_user.id,
                requested_at=document.signature_prepared_at or agora,
                completed_at=agora,
                verification_metadata={
                    "qualified_signature": True,
                    "standard": "PAdES",
                    "trust_chain": "ICP-Brasil",
                    "signer_physician_profile_id": dono.id,
                    "prepared_sha256": preparada.sha256,
                    "document_sha256": liberada.sha256,
                    "signer_subject": veredicto.signer_subject,
                    "assinado_fora_do_painel": True,
                },
            ))
            audit(
                db,
                "laudo_assinado_externamente_validado",
                entidade="report_documents",
                entidade_id=document.id,
                user_id=physician_user.id,
                request_id=_request_id(request),
                detalhes={
                    "versao_liberada": liberada.id,
                    "arquivo": nome,
                },
            )
            publication.commit()
        veredicto.version_number = liberada.version_number
        veredictos.append(veredicto)

    db.commit()
    return {
        "resumo": summarize(veredictos),
        "arquivos": [
            {
                "arquivo": v.filename,
                "resultado": v.outcome,
                "codigo_laudo": v.document_code,
                "versao": v.version_number,
                "mensagem": v.message,
                "ok": v.ok,
            }
            for v in veredictos
        ],
    }


# ------------------------ M25.20 — central de assinatura externa em lote
#
# A médica lauda um a um; o que vira lote é o trabalho burocrático depois da
# conclusão. A central opera sobre o laudo CONCLUÍDO (`liberado`) — o estado
# que a M25.18 já rotula "Concluído — aguardando assinatura qualificada" —,
# sem tocar em nada do fluxo individual aprovado.


# Código de verificação como impresso no laudo: 12 caracteres de um alfabeto
# sem letras ambíguas (sem I, L, O, 0, 1).
_VALIDATION_CODE_RE = re.compile(r"\b[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{12}\b")


def _aguardando_assinatura_externa(db: Session, *, profile_id: str):
    """Laudos DESTA médica concluídos e ainda sem assinado confirmado.

    O filtro por médica é aplicado aqui, no servidor. A lista que o navegador
    manda só pode ESTREITAR esta seleção, nunca ampliá-la.

    Cadastro arquivado (o cenário de teste da M25.17) fica de fora: um laudo
    de teste nunca deve entrar num pacote que vai ser assinado com
    certificado real.
    """

    ja_recebidos = select(ExternalSignedDocument.report_document_id).where(
        ExternalSignedDocument.status != ASSINADO_EM_CONFERENCIA
    )
    return db.execute(
        select(ReportDocument, SpirometryExam, Person)
        .join(
            ReportAssignment,
            (ReportAssignment.report_document_id == ReportDocument.id)
            & ReportAssignment.active.is_(True),
        )
        .join(
            SpirometryExam,
            SpirometryExam.id == ReportDocument.spirometry_exam_id,
        )
        .outerjoin(Person, Person.id == SpirometryExam.person_id)
        .where(
            ReportAssignment.physician_profile_id == profile_id,
            ReportDocument.status == STATUS_LAUDO_LIBERADO,
            Person.arquivado.is_not(True),
            # M25.24 — os cinco atendimentos Pastore já tinham laudo
            # entregue por fora; os laudos que existem aqui foram a médica
            # exercitando o fluxo. Pedir assinatura com certificado real
            # sobre um documento que ninguém vai entregar é trabalho
            # inventado. Os documentos continuam intactos — o que sai é a
            # COBRANÇA por eles.
            SpirometryExam.encerramento_motivo.is_(None),
            ReportDocument.id.not_in(ja_recebidos),
        )
        .order_by(ReportDocument.released_at.desc())
    ).all()


def _versao_para_assinatura(
    db: Session, document: ReportDocument
) -> ReportDocumentVersion | None:
    """A versão concluída que a médica leva para assinar."""

    versao = db.get(ReportDocumentVersion, document.current_version_id)
    if versao is None or versao.report_document_id != document.id:
        return None
    if versao.kind not in (KIND_LAUDO_LIBERADO, KIND_LAUDO_ADENDO):
        return None
    return versao


def _linha_assinatura_externa(
    document: ReportDocument,
    exam: SpirometryExam,
    person: Person | None,
    *,
    location: dict | None = None,
) -> dict:
    """Uma linha da central. Nome primeiro, códigos por último."""

    return {
        "document_id": document.id,
        "patient": _patient_reference(person),
        "report_code": document.public_code,
        "exam_code": exam.public_code,
        "exam_date": d(exam.data_exame),
        "location_name": (location or {}).get("nome"),
        "released_at": iso(document.released_at),
        "validation_code": document.validation_code,
    }


@router.get("/assinatura-externa/pendentes")
def list_pending_external_signature(
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """A central da médica: o que está aguardando assinatura qualificada."""

    _require_reports_enabled()
    profile = _require_active_physician(db, physician_user)
    linhas = _aguardando_assinatura_externa(db, profile_id=profile.id)
    return {
        "total": len(linhas),
        "laudos": [
            _linha_assinatura_externa(
                document,
                exam,
                person,
                location=_queue_location(db, document, exam),
            )
            for document, exam, person in linhas
        ],
    }


def _abrir_lote(
    db: Session,
    *,
    direction: str,
    profile: PhysicianProfile,
    user: User,
    document_count: int,
) -> ExternalSignatureBatch:
    """Abre o registro do lote e o torna DURÁVEL antes de qualquer arquivo.

    O commit aqui não é cerimônia. Cada arquivo recebido é gravado dentro de
    `report_publication_transaction`, que faz `db.commit()` no sucesso e
    `db.rollback()` na falha — e esse rollback é da SESSÃO inteira. Com o
    lote apenas em `flush()`, o primeiro arquivo que falhasse levaria a linha
    do lote junto, e o arquivo seguinte, válido, tentaria gravar apontando
    para um `batch_id` que não existe mais.

    O lote também é o registro de que um envio ACONTECEU, mesmo que nenhum
    arquivo dele acabe aproveitado. Ele não deveria depender do resultado.
    """

    lote = ExternalSignatureBatch(
        public_code=allocate_public_code(db, "external_signature_batches"),
        direction=direction,
        physician_profile_id=profile.id,
        created_by_user_id=user.id,
        document_count=document_count,
        created_at=datetime.now(timezone.utc),
    )
    db.add(lote)
    db.commit()
    db.refresh(lote)
    return lote


@router.post("/assinatura-externa/baixar")
def download_external_signature_batch(
    payload: SignatureBatchDownloadRequest,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Entrega os laudos selecionados para assinatura externa.

    UM documento sai como PDF direto, com o nome que a médica reconhece.
    Obrigar um ZIP de um arquivo só criaria um passo de extração no iPhone
    sem nenhum ganho. DOIS ou mais saem num ZIP plano.

    O ZIP contém dado médico: é gerado sob demanda, em memória, entregue
    com `no-store` e nunca escrito em disco, em webroot, em log ou em Git.
    """

    _require_reports_enabled()
    profile = _require_active_physician(db, physician_user)

    # O servidor decide o que é elegível; o pedido só escolhe dentro disso.
    elegiveis = {
        document.id: (document, exam, person)
        for document, exam, person in _aguardando_assinatura_externa(
            db, profile_id=profile.id
        )
    }
    escolhidos = [
        elegiveis[document_id]
        for document_id in payload.document_ids
        if document_id in elegiveis
    ]
    if not escolhidos:
        raise ReportDomainError(
            409,
            "lote_vazio",
            "Nenhum laudo concluído e aguardando assinatura foi encontrado "
            "na seleção.",
        )

    arquivos: list[BatchFile] = []
    for document, _exam, person in escolhidos:
        versao = _versao_para_assinatura(db, document)
        if versao is None:
            continue
        arquivos.append(BatchFile(
            document_code=document.public_code,
            patient_name=person.nome_completo if person else None,
            pdf=_read_stored_version(versao).data,
        ))
    if not arquivos:
        raise ReportDomainError(
            409,
            "versao_concluida_ausente",
            "Os laudos selecionados não possuem versão concluída disponível.",
        )

    agora = datetime.now(timezone.utc)
    lote = _abrir_lote(
        db,
        direction=BATCH_DIRECAO_DOWNLOAD,
        profile=profile,
        user=physician_user,
        document_count=len(arquivos),
    )
    for document, _exam, _person in escolhidos:
        document.signature_downloaded_at = agora
    audit(
        db,
        "laudos_baixados_para_assinatura_externa",
        entidade="report_documents",
        entidade_id=None,
        user_id=physician_user.id,
        request_id=_request_id(request),
        # Sem nome de paciente e sem nome de arquivo: a allowlist de
        # `app/audit.py` já os descartaria, e não há motivo para tentar.
        detalhes={
            "batch_id": lote.public_code,
            "total": len(arquivos),
            "physician_profile_id": profile.id,
        },
    )
    db.commit()

    if len(arquivos) == 1:
        unico = arquivos[0]
        return Response(
            content=unico.pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": content_disposition(
                    unico.filename, disposition="attachment"
                ),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    try:
        pacote = build_batch_zip(arquivos)
    except SignatureBatchError as exc:
        raise ReportDomainError(409, exc.codigo, exc.mensagem) from None
    nome_zip = batch_zip_filename(
        generated_on=to_display_timezone(agora).date()
    )
    return Response(
        content=pacote,
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(
                nome_zip, disposition="attachment"
            ),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _documentos_da_medica(db: Session, *, profile_id: str) -> dict:
    """TODOS os laudos atribuídos a esta médica, por id.

    O pareamento procura aqui, e não só entre os pendentes: um laudo que já
    recebeu o assinado saiu da lista de pendências, mas o arquivo dele
    continua sendo dela. Procurar só nos pendentes faria um reenvio — o caso
    real de quem manda o lote de novo por não ter certeza se funcionou —
    voltar como "não identificado", que é a mensagem errada e assustadora.

    O recorte por médica continua absoluto: laudo de outra médica nunca
    entra neste dicionário, então nunca chega a ser pareado.
    """

    linhas = db.execute(
        select(ReportDocument, SpirometryExam, Person)
        .join(
            ReportAssignment,
            (ReportAssignment.report_document_id == ReportDocument.id)
            & ReportAssignment.active.is_(True),
        )
        .join(
            SpirometryExam,
            SpirometryExam.id == ReportDocument.spirometry_exam_id,
        )
        .outerjoin(Person, Person.id == SpirometryExam.person_id)
        .where(ReportAssignment.physician_profile_id == profile_id)
    ).all()
    return {
        document.id: (document, exam, person)
        for document, exam, person in linhas
    }


def _parear_documento(
    dados: bytes, *, universo: dict
) -> tuple[ReportDocument | None, str | None]:
    """Encontra o laudo de origem de UM PDF devolvido.

    A ordem é a da confiança, e o nome do arquivo não entra nela em momento
    algum: um arquivo chamado "Maria Souza.pdf" pode ser de qualquer Maria,
    e associar por semelhança de nome é o jeito mais fácil de anexar o laudo
    assinado à paciente errada.
    """

    metadados = read_markers_from_metadata(dados)

    # 1. Metadado carimbado pela SoproLife — o caminho normal.
    if metadados.report_code:
        alvo = _por_codigo(metadados.report_code, universo)
        if alvo is not None:
            return alvo, PAREAMENTO_METADADO
    if metadados.validation_code:
        alvo = _por_validacao(metadados.validation_code, universo)
        if alvo is not None:
            return alvo, PAREAMENTO_METADADO

    # 2 e 3. Compatibilidade retroativa: laudos concluídos ANTES da M25.20
    # não têm carimbo, mas sempre tiveram os dois códigos impressos.
    conteudo = read_codes_from_content(
        dados, validation_code_pattern=_VALIDATION_CODE_RE
    )
    if conteudo.report_code:
        alvo = _por_codigo(conteudo.report_code, universo)
        if alvo is not None:
            return alvo, PAREAMENTO_CODIGO_LAUDO
    if conteudo.validation_code:
        alvo = _por_validacao(conteudo.validation_code, universo)
        if alvo is not None:
            return alvo, PAREAMENTO_CODIGO_VALIDACAO

    return None, None


def _por_codigo(codigo: str, universo: dict) -> ReportDocument | None:
    normalizado = normalize_for_compare(codigo)
    for document, _exam, _person in universo.values():
        if normalize_for_compare(document.public_code) == normalizado:
            return document
    return None


def _por_validacao(codigo: str, universo: dict) -> ReportDocument | None:
    normalizado = normalize_for_compare(codigo)
    for document, _exam, _person in universo.values():
        if (
            document.validation_code
            and normalize_for_compare(document.validation_code) == normalizado
        ):
            return document
    return None


@router.post("/assinatura-externa/enviar")
async def upload_external_signature_batch(
    request: Request,
    arquivos: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Recebe os PDFs assinados e monta a tela de conferência.

    Aceita vários PDFs OU um ZIP com todos — as duas formas que um iPhone
    oferece sem drag-and-drop. Cada arquivo recebe seu próprio veredito: um
    arquivo com problema nunca derruba os outros, e nunca entra em silêncio.

    NADA é confirmado aqui. Os arquivos identificados ficam `em_conferencia`
    e só saem desse estado quando a médica confirmar o lote — uma vez.
    """

    _require_reports_enabled()
    settings = get_settings()
    profile = _require_active_physician(db, physician_user)

    brutos: list[tuple[str, bytes]] = []
    for enviado in arquivos:
        dados = await _read_upload_bounded(
            enviado, max_size_bytes=settings.reports_max_upload_bytes
        )
        brutos.append((enviado.filename or "arquivo.pdf", dados))

    extracao = extract_signed_pdfs(
        brutos, max_pdf_bytes=settings.reports_max_upload_bytes
    )
    veredictos: list[MatchVerdict] = [
        MatchVerdict(
            filename=nome, outcome=RECUSADO, message=motivo
        )
        for nome, motivo in extracao.rejected
    ]
    if not extracao.files and not veredictos:
        raise ReportDomainError(
            422, "envio_vazio", "Nenhum PDF foi encontrado no envio."
        )

    # O universo de busca é o que É DELA; a elegibilidade para RECEBER é
    # mais estreita. Separar os dois é o que permite responder "este laudo
    # já foi recebido" em vez de "não identificado".
    universo = _documentos_da_medica(db, profile_id=profile.id)
    elegiveis = {
        document.id
        for document, _exam, _person in _aguardando_assinatura_externa(
            db, profile_id=profile.id
        )
    }
    lote = _abrir_lote(
        db,
        direction=BATCH_DIRECAO_UPLOAD,
        profile=profile,
        user=physician_user,
        document_count=len(extracao.files),
    )

    recebidos: list[dict] = []
    vistos_no_envio: set[str] = set()
    for arquivo in extracao.files:
        document, metodo = _parear_documento(arquivo.data, universo=universo)
        if document is None or metodo is None:
            veredictos.append(MatchVerdict(
                filename=arquivo.filename,
                outcome=NAO_IDENTIFICADO,
                message=(
                    "Não foi possível identificar com segurança a qual laudo "
                    "este arquivo pertence."
                ),
            ))
            continue

        _doc, exam_do_doc, person = universo[document.id]
        nome_paciente = person.nome_completo if person else None

        if document.id in vistos_no_envio:
            veredictos.append(MatchVerdict(
                filename=arquivo.filename,
                outcome=JA_RECEBIDO,
                report_code=document.public_code,
                patient_name=nome_paciente,
                message="Este laudo apareceu duas vezes no mesmo envio.",
            ))
            continue

        digest = hashlib.sha256(arquivo.data).hexdigest()
        # Idempotência: o mesmo arquivo, reenviado, não vira segunda versão.
        # É o caso real de quem manda o lote de novo por não ter certeza se
        # o primeiro envio funcionou.
        existente = db.execute(
            select(ExternalSignedDocument).where(
                ExternalSignedDocument.report_document_id == document.id,
                ExternalSignedDocument.sha256 == digest,
            )
        ).scalar_one_or_none()
        if existente is not None:
            veredictos.append(MatchVerdict(
                filename=arquivo.filename,
                outcome=JA_RECEBIDO,
                report_code=document.public_code,
                patient_name=nome_paciente,
                message="Este arquivo assinado já havia sido recebido.",
            ))
            continue

        # O laudo é dela, mas não está aguardando assinatura: já recebeu um
        # assinado, ainda está em elaboração, ou o cadastro foi arquivado.
        # A mensagem diz o que aconteceu em vez de fingir desconhecimento.
        if document.id not in elegiveis:
            ja_tem = _assinado_mais_recente(db, document.id)
            veredictos.append(MatchVerdict(
                filename=arquivo.filename,
                outcome=JA_RECEBIDO if ja_tem else RECUSADO,
                report_code=document.public_code,
                patient_name=nome_paciente,
                message=(
                    "Este laudo já recebeu um arquivo assinado."
                    if ja_tem
                    else "Este laudo não está aguardando assinatura."
                ),
            ))
            continue

        origem = _versao_para_assinatura(db, document)
        if origem is None:
            veredictos.append(MatchVerdict(
                filename=arquivo.filename,
                outcome=RECUSADO,
                report_code=document.public_code,
                patient_name=nome_paciente,
                message="A versão concluída deste laudo não está disponível.",
            ))
            continue
        try:
            with report_publication_transaction(db) as publication:
                versao = _store_new_version(
                    db,
                    publication=publication,
                    document=document,
                    exam_id=document.spirometry_exam_id,
                    kind=KIND_LAUDO_ASSINADO_EXTERNO,
                    data=arquivo.data,
                    created_by_user_id=physician_user.id,
                )
                assinado = ExternalSignedDocument(
                    report_document_id=document.id,
                    report_document_version_id=versao.id,
                    source_version_id=origem.id,
                    source_sha256=origem.sha256,
                    batch_id=lote.id,
                    physician_profile_id=profile.id,
                    uploader_user_id=physician_user.id,
                    sha256=versao.sha256,
                    size_bytes=versao.size_bytes,
                    received_filename=arquivo.filename[:260],
                    match_method=metodo,
                    status=ASSINADO_EM_CONFERENCIA,
                    received_at=datetime.now(timezone.utc),
                )
                db.add(assinado)
                db.flush()
                publication.commit()
        except ReportDomainError as exc:
            veredictos.append(MatchVerdict(
                filename=arquivo.filename,
                outcome=RECUSADO,
                report_code=document.public_code,
                patient_name=nome_paciente,
                message=exc.mensagem,
            ))
            continue

        vistos_no_envio.add(document.id)
        veredictos.append(MatchVerdict(
            filename=arquivo.filename,
            outcome=PAREADO,
            report_code=document.public_code,
            patient_name=nome_paciente,
            match_method=metodo,
            message=_rotulo_pareamento(metodo),
        ))
        recebidos.append({
            "signed_document_id": assinado.id,
            "document_id": document.id,
            "report_code": document.public_code,
            "patient": _patient_reference(person),
            "exam_code": exam_do_doc.public_code,
            "arquivo": arquivo.filename,
            "pareado_por": metodo,
            "pareado_por_rotulo": _rotulo_pareamento(metodo),
        })

    audit(
        db,
        "laudos_assinados_recebidos_para_conferencia",
        entidade="report_documents",
        entidade_id=None,
        user_id=physician_user.id,
        request_id=_request_id(request),
        detalhes={
            "batch_id": lote.public_code,
            "total": len(veredictos),
            "validas": len(recebidos),
            "rejeitadas": len(veredictos) - len(recebidos),
            "physician_profile_id": profile.id,
        },
    )
    db.commit()
    return {
        "batch_id": lote.id,
        "resumo": summarize(veredictos),
        "identificados": recebidos,
        "arquivos": [_ser_veredicto(v) for v in veredictos],
    }


def _rotulo_pareamento(metodo: str) -> str:
    return {
        PAREAMENTO_METADADO: "identificado pelo código interno",
        PAREAMENTO_CODIGO_LAUDO: "identificado pelo código do laudo impresso",
        PAREAMENTO_CODIGO_VALIDACAO: (
            "identificado pelo código de verificação impresso"
        ),
    }.get(metodo, metodo)


def _ser_veredicto(veredicto: MatchVerdict) -> dict:
    return {
        "arquivo": veredicto.filename,
        "resultado": veredicto.outcome,
        "codigo_laudo": veredicto.report_code,
        "paciente": veredicto.patient_name,
        "pareado_por": veredicto.match_method,
        "mensagem": veredicto.message,
        "ok": veredicto.ok,
    }


@router.post("/assinatura-externa/confirmar")
def confirm_external_signature_batch(
    payload: SignatureBatchConfirmRequest,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """A médica confirma o lote conferido — uma vez.

    Só entra o que ELA viu identificado na tela de conferência, do lote dela.
    A partir daqui o laudo aparece para a administração como "assinado
    recebido — validação pendente"; ninguém precisa avisar por WhatsApp.

    O que este endpoint NÃO faz: declarar assinatura válida. Nenhuma cadeia
    ICP-Brasil foi verificada criptograficamente por este sistema, e o
    estado gravado diz exatamente isso.
    """

    _require_reports_enabled()
    profile = _require_active_physician(db, physician_user)

    lote = db.get(ExternalSignatureBatch, payload.batch_id)
    if (
        lote is None
        or lote.physician_profile_id != profile.id
        or lote.direction != BATCH_DIRECAO_UPLOAD
    ):
        raise ReportDomainError(
            404, "lote_nao_encontrado", "Lote de envio não encontrado."
        )

    pendentes = db.execute(
        select(ExternalSignedDocument)
        .where(
            ExternalSignedDocument.batch_id == lote.id,
            ExternalSignedDocument.id.in_(payload.signed_document_ids),
            ExternalSignedDocument.physician_profile_id == profile.id,
            ExternalSignedDocument.status == ASSINADO_EM_CONFERENCIA,
        )
        .with_for_update()
    ).scalars().all()
    if not pendentes:
        raise ReportDomainError(
            409,
            "nada_a_confirmar",
            "Nenhum documento deste lote está aguardando confirmação.",
        )

    agora = datetime.now(timezone.utc)
    for assinado in pendentes:
        assinado.status = ASSINADO_RECEBIDO_VALIDACAO_PENDENTE
        assinado.confirmed_at = agora
        audit(
            db,
            "laudo_assinado_externo_confirmado",
            entidade="report_documents",
            entidade_id=assinado.report_document_id,
            user_id=physician_user.id,
            request_id=_request_id(request),
            detalhes={
                "batch_id": lote.public_code,
                "report_version_id": assinado.report_document_version_id,
                "sha256": assinado.sha256,
                "marcador": assinado.match_method,
                "physician_profile_id": profile.id,
                # Deliberadamente FALSO: receber não é validar.
                "qualified_signature": False,
            },
        )
    db.commit()
    return {
        "confirmados": len(pendentes),
        "status": ASSINADO_RECEBIDO_VALIDACAO_PENDENTE,
        "observacao": (
            "Arquivos assinados recebidos e armazenados. A validação da "
            "assinatura digital ainda está pendente."
        ),
    }


# ------------------------------- M25.20 — fila administrativa de entrega
#
# Os cinco estados que a administração acompanha. Eles descrevem o percurso
# do documento até o paciente, e são DERIVADOS do que está gravado — nenhum
# deles é um campo que alguém marca à mão.

FILA_AGUARDANDO_LAUDO = "aguardando_laudo"
FILA_AGUARDANDO_ASSINATURA = "aguardando_assinatura"
FILA_ASSINADO_RECEBIDO = "assinado_recebido_validacao_pendente"
FILA_PRONTO_PARA_ENTREGA = "pronto_para_entrega"
FILA_ENTREGUE = "entregue"

FILA_ROTULOS = {
    FILA_AGUARDANDO_LAUDO: "Aguardando laudo",
    FILA_AGUARDANDO_ASSINATURA: "Aguardando assinatura",
    FILA_ASSINADO_RECEBIDO: "Assinado recebido — validação pendente",
    FILA_PRONTO_PARA_ENTREGA: "Pronto para entrega",
    FILA_ENTREGUE: "Entregue",
}

_FILA_POR_ASSINADO = {
    ASSINADO_RECEBIDO_VALIDACAO_PENDENTE: FILA_ASSINADO_RECEBIDO,
    ASSINADO_VALIDADO_EXTERNAMENTE: FILA_PRONTO_PARA_ENTREGA,
    ASSINADO_ENTREGUE: FILA_ENTREGUE,
}


def _estado_de_entrega(
    document: ReportDocument, assinado: ExternalSignedDocument | None
) -> str:
    """O estado do documento na fila de entrega.

    Um documento em conferência ainda NÃO conta: a médica não confirmou o
    lote, e a administração não deve começar a trabalhar num arquivo que
    pode ser descartado na tela seguinte.
    """

    if assinado is not None and assinado.status != ASSINADO_EM_CONFERENCIA:
        return _FILA_POR_ASSINADO.get(assinado.status, FILA_ASSINADO_RECEBIDO)
    if document.status == STATUS_LAUDO_LIBERADO:
        return FILA_AGUARDANDO_ASSINATURA
    return FILA_AGUARDANDO_LAUDO


def _assinado_mais_recente(
    db: Session, document_id: str
) -> ExternalSignedDocument | None:
    return db.execute(
        select(ExternalSignedDocument)
        .where(ExternalSignedDocument.report_document_id == document_id)
        .order_by(ExternalSignedDocument.received_at.desc())
        .limit(1)
    ).scalar_one_or_none()


@router.get("/assinatura-externa/fila")
def list_delivery_queue(
    estado: str | None = None,
    db: Session = Depends(get_db),
    _operator: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """A fila de entrega, com os cinco estados do percurso do documento.

    A transição para "assinado recebido" é AUTOMÁTICA: ela acontece quando a
    médica confirma o lote devolvido. Ninguém precisa avisar a administração
    por WhatsApp de que os laudos voltaram.
    """

    _require_reports_enabled()
    if estado is not None and estado not in FILA_ROTULOS:
        raise ReportDomainError(
            422, "estado_fila_invalido", "Estado de fila inválido."
        )

    linhas = db.execute(
        select(ReportDocument, SpirometryExam, Person, ReportAssignment)
        .join(
            SpirometryExam,
            SpirometryExam.id == ReportDocument.spirometry_exam_id,
        )
        .outerjoin(Person, Person.id == SpirometryExam.person_id)
        .outerjoin(
            ReportAssignment,
            (ReportAssignment.report_document_id == ReportDocument.id)
            & ReportAssignment.active.is_(True),
        )
        .where(Person.arquivado.is_not(True))
        # M25.24 — a fila de ENTREGA também é lista de trabalho. Um exame
        # encerrado como histórico não tem entrega a fazer.
        .where(SpirometryExam.encerramento_motivo.is_(None))
        .order_by(ReportDocument.created_at.desc())
        .limit(300)
    ).all()

    itens = []
    for document, exam, person, assignment in linhas:
        assinado = _assinado_mais_recente(db, document.id)
        atual = _estado_de_entrega(document, assinado)
        if estado is not None and atual != estado:
            continue
        itens.append({
            "document_id": document.id,
            "patient": _patient_reference(person),
            "report_code": document.public_code,
            "exam_code": exam.public_code,
            "exam_date": d(exam.data_exame),
            "estado": atual,
            "estado_rotulo": FILA_ROTULOS[atual],
            "status_clinico": document.status,
            "released_at": iso(document.released_at),
            "physician_profile_id": (
                assignment.physician_profile_id if assignment else None
            ),
            "assinado": _ser_assinado(assinado) if assinado else None,
        })

    contagem = {chave: 0 for chave in FILA_ROTULOS}
    for item in itens:
        contagem[item["estado"]] += 1
    return {
        "estados": [
            {"chave": chave, "rotulo": rotulo, "total": contagem[chave]}
            for chave, rotulo in FILA_ROTULOS.items()
        ],
        "itens": itens,
    }


def _ser_assinado(assinado: ExternalSignedDocument) -> dict:
    """Evidência do arquivo assinado recebido. Sem bytes, sem caminho."""

    return {
        "signed_document_id": assinado.id,
        "status": assinado.status,
        "sha256": assinado.sha256,
        "size_bytes": assinado.size_bytes,
        "received_at": iso(assinado.received_at),
        "confirmed_at": iso(assinado.confirmed_at),
        "pareado_por": assinado.match_method,
        "pareado_por_rotulo": _rotulo_pareamento(assinado.match_method),
        # O que o sistema NÃO fez. Fica na resposta de propósito: uma tela
        # que mostre "assinado" sem esta linha convida a conclusão errada.
        "assinatura_verificada_criptograficamente": False,
        "validado_externamente_em": iso(assinado.validated_at),
        "validacao_metodo": assinado.validation_method,
        "validacao_referencia": assinado.validation_reference,
        "entregue_em": iso(assinado.delivered_at),
    }


@router.post("/assinatura-externa/{signed_document_id}/validacao-externa")
def register_external_validation(
    signed_document_id: str,
    payload: ExternalValidationRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_role(ROLE_ADMIN)),
):
    """Registra que alguém conferiu a assinatura FORA da SoproLife.

    Enquanto não houver validador criptográfico integrado, esta é a única
    afirmação honesta possível: uma pessoa identificada abriu o arquivo no
    Validar ITI (ou equivalente) e registrou o resultado aqui, com hora e
    método. Isto NÃO é validação criptográfica realizada pela SoproLife, e o
    registro nunca é apresentado como tal.

    Nenhuma senha, certificado ou chave é armazenada.
    """

    _require_reports_enabled()
    assinado = db.execute(
        select(ExternalSignedDocument)
        .where(ExternalSignedDocument.id == signed_document_id)
        .with_for_update()
    ).scalar_one_or_none()
    if assinado is None:
        raise ReportDomainError(
            404,
            "documento_assinado_nao_encontrado",
            "Documento assinado não encontrado.",
        )
    if assinado.status == ASSINADO_EM_CONFERENCIA:
        raise ReportDomainError(
            409,
            "conferencia_da_medica_pendente",
            "A médica ainda não confirmou o recebimento deste arquivo.",
        )
    if assinado.status != ASSINADO_RECEBIDO_VALIDACAO_PENDENTE:
        raise ReportDomainError(
            409,
            "validacao_ja_registrada",
            "A conferência externa deste documento já foi registrada.",
        )

    agora = datetime.now(timezone.utc)
    assinado.status = ASSINADO_VALIDADO_EXTERNAMENTE
    assinado.validated_by_user_id = admin_user.id
    assinado.validated_at = agora
    assinado.validation_method = payload.metodo
    assinado.validation_reference = payload.referencia
    audit(
        db,
        "assinatura_conferida_externamente",
        entidade="report_documents",
        entidade_id=assinado.report_document_id,
        user_id=admin_user.id,
        request_id=_request_id(request),
        detalhes={
            "sha256": assinado.sha256,
            "modo": payload.metodo,
            "resultado": ASSINADO_VALIDADO_EXTERNAMENTE,
            # A SoproLife continua sem verificar a cadeia ICP-Brasil.
            "qualified_signature": False,
        },
    )
    db.commit()
    return {
        "signed_document_id": assinado.id,
        "status": assinado.status,
        "estado": FILA_PRONTO_PARA_ENTREGA,
        "estado_rotulo": FILA_ROTULOS[FILA_PRONTO_PARA_ENTREGA],
        "observacao": (
            "Conferência externa registrada. A SoproLife não realizou "
            "validação criptográfica da assinatura."
        ),
    }


@router.post("/assinatura-externa/{signed_document_id}/entrega")
def register_delivery(
    signed_document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    operator_user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Marca que o laudo assinado foi entregue pelos canais atuais.

    Não existe envio automático nesta etapa: nenhum canal seguro de entrega
    ao paciente foi definido. O administrador baixa o arquivo, entrega pelos
    meios que a SoproLife já usa, e registra aqui que entregou — o que fecha
    a fila sem inventar um envio que o sistema não fez.
    """

    _require_reports_enabled()
    assinado = db.execute(
        select(ExternalSignedDocument)
        .where(ExternalSignedDocument.id == signed_document_id)
        .with_for_update()
    ).scalar_one_or_none()
    if assinado is None:
        raise ReportDomainError(
            404,
            "documento_assinado_nao_encontrado",
            "Documento assinado não encontrado.",
        )
    if assinado.status != ASSINADO_VALIDADO_EXTERNAMENTE:
        raise ReportDomainError(
            409,
            "entrega_fora_de_ordem",
            "Registre a conferência da assinatura antes de marcar a entrega.",
        )

    assinado.status = ASSINADO_ENTREGUE
    assinado.delivered_at = datetime.now(timezone.utc)
    assinado.delivered_by_user_id = operator_user.id
    audit(
        db,
        "laudo_assinado_entregue",
        entidade="report_documents",
        entidade_id=assinado.report_document_id,
        user_id=operator_user.id,
        request_id=_request_id(request),
        detalhes={"sha256": assinado.sha256, "resultado": ASSINADO_ENTREGUE},
    )
    db.commit()
    return {
        "signed_document_id": assinado.id,
        "status": assinado.status,
        "estado": FILA_ENTREGUE,
        "estado_rotulo": FILA_ROTULOS[FILA_ENTREGUE],
    }


def _entregar_pdf(
    db: Session,
    *,
    request: Request,
    document: ReportDocument,
    version: ReportDocumentVersion,
    user: User,
    sufixo: str,
    evento: str,
) -> Response:
    """Entrega um PDF com nome humano e sem cache. Caminho único."""

    stored = _read_stored_version(version, missing_status=404)
    exam = db.get(SpirometryExam, document.spirometry_exam_id)
    person = db.get(Person, exam.person_id) if exam is not None else None
    nome = named_download_filename(
        patient_name=person.nome_completo if person is not None else None,
        fallback_code=document.public_code,
        sufixo=sufixo,
    )
    audit(
        db,
        evento,
        entidade="report_documents",
        entidade_id=document.id,
        user_id=user.id,
        request_id=_request_id(request),
        detalhes={
            "report_version_id": version.id,
            "delivery_mode": "download",
            "institutional_status": document.status,
        },
    )
    db.commit()
    return Response(
        content=stored.data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(
                nome, disposition="attachment"
            ),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{document_id}/assinado/conteudo")
def download_signed_report_admin(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    operator_user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """``<Paciente> - Assinado.pdf`` para quem entrega o documento."""

    _require_reports_enabled()
    document = _get_document_or_404(db, document_id)
    assinado = _assinado_mais_recente(db, document.id)
    if assinado is None or assinado.status == ASSINADO_EM_CONFERENCIA:
        raise ReportDomainError(
            404,
            "laudo_assinado_indisponivel",
            "Nenhum laudo assinado foi recebido para este documento.",
        )
    versao = db.get(
        ReportDocumentVersion, assinado.report_document_version_id
    )
    if versao is None:
        raise ReportDomainError(
            404,
            "arquivo_laudo_ausente",
            "Arquivo do laudo assinado não está disponível.",
        )
    return _entregar_pdf(
        db,
        request=request,
        document=document,
        version=versao,
        user=operator_user,
        sufixo=SUFIXO_ASSINADO,
        evento="laudo_assinado_entregue_para_download",
    )


@router.get("/{document_id}/exame-tecnico/conteudo")
def download_technical_exam_admin(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    operator_user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """``<Paciente> - Exame técnico.pdf`` — a MIR original, intacta."""

    _require_reports_enabled()
    document = _get_document_or_404(db, document_id)
    original = _version_by_kind(db, document.id, KIND_ORIGINAL)
    if original is None:
        raise ReportDomainError(
            404,
            "exame_tecnico_indisponivel",
            "O PDF técnico deste exame não está disponível.",
        )
    return _entregar_pdf(
        db,
        request=request,
        document=document,
        version=original,
        user=operator_user,
        sufixo=SUFIXO_MIR,
        evento="exame_tecnico_entregue_para_download",
    )


# ------------------------------------------- M25.7 — assinatura qualificada


def _qualified_or_404(db: Session, document: ReportDocument):
    from ..services import qualified_signature as qs

    pedido = qs.active_request(db, document_id=document.id)
    if pedido is None:
        raise ReportDomainError(
            404,
            "solicitacao_inexistente",
            "Não há solicitação de assinatura em andamento para este laudo.",
        )
    return qs.expire_if_due(db, pedido)


def _qualified_payload(pedido) -> dict:
    """Estado para a tela da médica. Sem segredo, sem hash interno cru."""

    return {
        "id": pedido.id,
        "status": pedido.status,
        "provedor": pedido.provider,
        "tentativas": pedido.attempts,
        "expira_em": (
            pedido.clearance_expires_at.isoformat()
            if pedido.clearance_expires_at else None
        ),
        "concluido_em": (
            pedido.completed_at.isoformat() if pedido.completed_at else None
        ),
        "nivel_pades": pedido.pades_level,
        # Motivo legível quando houver; nunca o corpo devolvido pela AC.
        "erro": pedido.error_message,
        "pode_tentar_novamente": pedido.status == "falha_recuperavel",
    }


@router.get("/admin/assinatura-qualificada/diagnostico")
def qualified_signature_diagnostics(
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_role(ROLE_ADMIN)),
):
    """Diagnóstico da integração — apenas booleanos, nunca valores."""

    from ..services import qualified_signature as qs

    _require_reports_enabled()
    return qs.diagnostics(get_settings())


@router.get("/{document_id}/conformidade-cfm")
def report_cfm_compliance(
    document_id: str,
    db: Session = Depends(get_db),
    _admin_user: User = Depends(require_role(ROLE_ADMIN)),
):
    """Confere um laudo concreto contra a Resolução CFM 2.381/2024.

    M25.15 — é o GATE objetivo que a missão exige antes de qualquer troca de
    `M15_REPORTS_MODE`. Monta o MESMO conteúdo que iria para o PDF e devolve
    requisito a requisito o que está atendido, o que falta e uma das duas
    conclusões possíveis.

    Admin-only e somente leitura: não gera versão, não grava nada e não
    altera o documento. A conclusão é calculada, nunca digitada — não existe
    caminho por onde alguém declare conformidade sem que os requisitos a
    sustentem.
    """

    document = _get_document_or_404(db, document_id)
    exam, person, location = _document_context(db, document)
    profile = (
        db.get(PhysicianProfile, document.released_physician_profile_id)
        if document.released_physician_profile_id
        else None
    )
    if profile is None:
        assignment = _active_assignment(db, document.id)
        profile = (
            db.get(PhysicianProfile, assignment.physician_profile_id)
            if assignment
            else None
        )
    if profile is None:
        raise ReportDomainError(
            409,
            "medico_do_laudo_indisponivel",
            "O laudo não tem médico atribuído nem liberador para conferir.",
        )
    version = (
        db.get(ReportDocumentVersion, document.current_version_id)
        if document.current_version_id
        else None
    )
    content = build_native_content(
        db,
        document=document,
        exam=exam,
        person=person,
        profile=profile,
        location=location,
        version_number=version.version_number if version else 1,
        # A conferência é de ESTRUTURA do documento, não do texto clínico.
        # Um marcador explícito evita que a ausência de conclusão numa
        # versão ainda em elaboração seja lida como campo faltando.
        conclusion_text=(
            "[conferência de conformidade — texto clínico não avaliado]"
        ),
        observations=None,
        issued_at=datetime.now(timezone.utc),
        released=document.status == STATUS_LAUDO_LIBERADO,
        released_at=document.released_at,
        validation_code=document.validation_code,
        signature_kind=_seal_signature_kind(
            db, version=version, profile=profile
        ),
        pilot_warning=_pilot_warning(),
    )
    return {
        "report_code": document.public_code,
        "status": document.status,
        "reports_mode": get_settings().reports_mode,
        **relatorio_conformidade(content),
    }


@router.post("/{document_id}/assinatura-qualificada/iniciar", status_code=201)
def start_qualified_signature(
    document_id: str,
    payload: ReportReleaseRequest,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Prepara o laudo e devolve o endereço para autorizar no VIDaaS.

    Exige exatamente as mesmas provas da liberação institucional — prévia
    atual e texto conferido —, porque assinar qualificado é MAIS sério, não
    menos. Nenhum byte do PDF e nenhum dado do paciente sai daqui: só o
    digest viaja, e só depois, no retorno.
    """

    from ..services import qualified_signature as qs
    from ..services import qualified_signature_store as qstore

    _require_reports_enabled()
    settings = get_settings()
    document = _lock_document_or_404(db, document_id)
    profile, _assignment = _require_assigned_physician(
        db, physician_user, document, lock=True
    )
    if document.status == STATUS_LAUDO_LIBERADO:
        raise ReportDomainError(
            409,
            "laudo_ja_liberado",
            "Este laudo já foi liberado; use adendo ou versão corretiva.",
        )
    if document.status != STATUS_EM_ELABORACAO:
        raise ReportDomainError(
            409,
            "laudo_fora_de_elaboracao",
            "Gere a prévia do laudo antes de assinar.",
        )
    draft = db.get(ReportDocumentVersion, document.current_version_id)
    if (
        draft is None
        or draft.report_document_id != document.id
        or draft.kind != KIND_LAUDO_PREVIA
    ):
        raise ReportDomainError(
            409, "previa_laudo_ausente", "Gere a prévia do laudo antes de assinar."
        )
    if payload.expected_version_id != draft.id:
        raise ReportDomainError(
            409,
            "previa_desatualizada",
            "A prévia mudou desde a conferência; revise antes de assinar.",
        )
    if payload.expected_text_sha256 != (draft.interpretation_text_sha256 or ""):
        raise ReportDomainError(
            409,
            "conteudo_divergente_da_previa",
            "O texto conferido não corresponde ao texto atual da prévia.",
        )

    exam, person, location = _document_context(db, document)
    resolved_signature = _resolve_signature(db, profile.id)
    now = datetime.now(timezone.utc)
    content = build_native_content(
        db,
        document=document,
        exam=exam,
        person=person,
        profile=profile,
        location=location,
        version_number=_next_version_number(db, document.id),
        conclusion_text=draft.interpretation_text_snapshot or "",
        observations=draft.observations_snapshot,
        issued_at=now,
        released=True,
        released_at=now,
        validation_code=document.validation_code or generate_validation_code(),
        signature_image=(
            resolved_signature.image if resolved_signature else None
        ),
        # O selo só declara ICP-Brasil DEPOIS da validação. Aqui ainda não
        # houve assinatura nenhuma.
        signature_kind=SIGNATURE_KIND_INSTITUTIONAL,
        addenda=load_addenda(db, document.id),
        pilot_warning=_pilot_warning(),
    )
    try:
        iniciada = qs.start(
            db,
            settings=settings,
            document=document,
            version=draft,
            prepared_pdf=_native_pdf_bytes(content),
            physician_profile_id=profile.id,
            requested_by_user_id=physician_user.id,
            reason="Laudo de espirometria - SoproLife",
            location=location.name,
        )
    except qs.QualifiedSignatureError as exc:
        raise ReportDomainError(exc.http_status, exc.codigo, exc.mensagem) from None

    qstore.store_prepared_pdf(
        _storage_root(settings),
        request_id=iniciada.request.id,
        data=iniciada.prepared.data,
    )
    audit(
        db,
        "laudo_assinatura_qualificada_iniciada",
        entidade="report_documents",
        entidade_id=document.id,
        user_id=physician_user.id,
        request_id=_request_id(request),
        detalhes={"solicitacao_id": iniciada.request.id, "provedor": "integraicp"},
    )
    db.commit()
    return {
        **_qualified_payload(iniciada.request),
        "url_autorizacao": iniciada.authorization_url,
    }


@router.get("/{document_id}/assinatura-qualificada")
def get_qualified_signature(
    document_id: str,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Acompanhamento do estado — é o que a tela consulta enquanto espera."""

    _require_reports_enabled()
    document = _lock_document_or_404(db, document_id)
    _require_assigned_physician(db, physician_user, document, lock=False)
    pedido = _qualified_or_404(db, document)
    db.commit()
    return _qualified_payload(pedido)


@router.post("/{document_id}/assinatura-qualificada/cancelar")
def cancel_qualified_signature(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Cancelamento consciente pela própria médica."""

    from ..services import qualified_signature as qs
    from ..services import qualified_signature_store as qstore

    _require_reports_enabled()
    document = _lock_document_or_404(db, document_id)
    _require_assigned_physician(db, physician_user, document, lock=True)
    pedido = _qualified_or_404(db, document)
    try:
        qs.cancel(db, pedido)
    except qs.QualifiedSignatureError as exc:
        raise ReportDomainError(exc.http_status, exc.codigo, exc.mensagem) from None
    qstore.discard_prepared_pdf(
        _storage_root(get_settings()), request_id=pedido.id
    )
    audit(
        db,
        "laudo_assinatura_qualificada_cancelada",
        entidade="report_documents",
        entidade_id=document.id,
        user_id=physician_user.id,
        request_id=_request_id(request),
        detalhes={"solicitacao_id": pedido.id},
    )
    db.commit()
    return _qualified_payload(pedido)


@router.post("/{document_id}/assinatura-qualificada/retorno")
def resolve_qualified_signature(
    document_id: str,
    payload: QualifiedSignatureCallback,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Consome o retorno do VIDaaS, valida e libera — ou não libera nada.

    O laudo só é marcado como assinado depois de `validate_pades` passar. Se
    a validação falhar, a solicitação vai para falha e o documento continua
    em elaboração, intocado.
    """

    from ..services import qualified_signature as qs
    from ..services import qualified_signature_store as qstore

    _require_reports_enabled()
    settings = get_settings()
    document = _lock_document_or_404(db, document_id)
    profile, _assignment = _require_assigned_physician(
        db, physician_user, document, lock=True
    )
    try:
        pedido = qs.resolve_callback(
            db,
            settings=settings,
            state=payload.state,
            nonce=payload.nonce,
            credential_id=payload.credential_id,
            acting_user_id=physician_user.id,
        )
    except qs.QualifiedSignatureError as exc:
        db.commit()
        raise ReportDomainError(exc.http_status, exc.codigo, exc.mensagem) from None
    if pedido.report_document_id != document.id:
        raise ReportDomainError(
            403, "retorno_de_outro_laudo", "Retorno de assinatura inválido."
        )
    if pedido.physician_profile_id != profile.id:
        raise ReportDomainError(
            403,
            "retorno_de_outra_medica",
            "Este retorno pertence a outra médica.",
        )

    root = _storage_root(settings)
    prepared_bytes = qstore.read_prepared_pdf(root, request_id=pedido.id)
    prepared = qs.rebuild_prepared(prepared_bytes)
    try:
        final_pdf = qs.complete(
            db,
            settings=settings,
            request=pedido,
            prepared=prepared,
            credential_id=payload.credential_id,
        )
    except qs.QualifiedSignatureError as exc:
        db.commit()
        raise ReportDomainError(exc.http_status, exc.codigo, exc.mensagem) from None

    draft = db.get(ReportDocumentVersion, document.current_version_id)
    exam, _person, _location = _document_context(db, document)
    now = datetime.now(timezone.utc)
    with report_publication_transaction(db) as publication:
        released = _publish_native_version(
            db,
            publication=publication,
            document=document,
            exam=exam,
            profile=profile,
            kind=KIND_LAUDO_LIBERADO,
            data=final_pdf,
            created_by_user_id=physician_user.id,
            final_text=draft.interpretation_text_snapshot or "",
            extra={},
        )
        # Igual ao fluxo externo: assinatura qualificada termina em
        # 'assinado'. Ver a constraint ck_report_documents_clinical_state.
        document.status = STATUS_ASSINADO
        document.current_version_id = released.id
        document.signed_at = now
        document.signature_status = SIGNATURE_STATUS_ASSINADA
        signature = ReportSignature(
            report_document_version_id=released.id,
            provider="integraicp",
            status=SIGNATURE_STATUS_ASSINADA,
            external_reference=pedido.external_reference or pedido.id,
            requested_by_user_id=physician_user.id,
            requested_at=pedido.created_at,
            completed_at=now,
            verification_metadata={
                "qualified_signature": True,
                "standard": "PAdES",
                "trust_chain": "ICP-Brasil",
                "pades_level": pedido.pades_level,
                "signer_physician_profile_id": profile.id,
                # Os DOIS hashes, nomeados sem ambiguidade.
                "prepared_sha256": pedido.prepared_sha256,
                "signed_digest_sha256": pedido.signed_digest_sha256,
                "document_sha256": released.sha256,
                "signer_serial": pedido.signer_serial,
                "signer_issuer": pedido.signer_issuer,
            },
        )
        db.add(signature)
        audit(
            db,
            "laudo_assinado_icp_brasil",
            entidade="report_documents",
            entidade_id=document.id,
            user_id=physician_user.id,
            request_id=_request_id(request),
            detalhes={
                "solicitacao_id": pedido.id,
                "nivel_pades": pedido.pades_level,
                "versao_liberada": released.id,
            },
        )
        publication.commit()

    qstore.discard_prepared_pdf(root, request_id=pedido.id)
    db.commit()
    return {
        **_qualified_payload(pedido),
        "versao_liberada_id": released.id,
        "validation_code": document.validation_code,
    }


@router.post("/{document_id}/nova-versao-corretiva", status_code=201)
def open_corrective_document(
    document_id: str,
    payload: ReportCorrectiveCreate,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    predecessor = _lock_document_or_404(db, document_id)
    profile, _assignment = _require_assigned_physician(
        db, physician_user, predecessor, lock=True
    )
    # Correção posterior é permitida a partir de um documento realmente
    # fechado: assinado com evidência QUALIFICADA (caminho futuro) ou
    # LIBERADO institucionalmente (caminho M25.2). Nos dois casos o
    # predecessor é preservado integralmente — nunca reescrito nem apagado.
    if predecessor.status not in {STATUS_ASSINADO, STATUS_LAUDO_LIBERADO}:
        raise ReportDomainError(
            409,
            "laudo_nao_fechado",
            "Somente um laudo assinado ou liberado aceita correção.",
        )
    closed_version = db.get(
        ReportDocumentVersion, predecessor.current_version_id
    )
    signature = db.execute(
        select(ReportSignature).where(
            ReportSignature.report_document_version_id
            == predecessor.current_version_id
        )
    ).scalar_one_or_none()
    if predecessor.status == STATUS_ASSINADO:
        if (
            closed_version is None
            or closed_version.kind != KIND_ASSINADO
            or signature is None
            or not _qualified_signature_evidence(
                signature, closed_version, profile
            )
        ):
            raise ReportDomainError(
                409,
                "assinatura_qualificada_nao_comprovada",
                "A assinatura qualificada não possui evidência verificável.",
            )
    elif (
        closed_version is None
        or closed_version.kind
        not in {KIND_LAUDO_LIBERADO, KIND_LAUDO_ADENDO}
        or not predecessor.released_at
        or predecessor.released_physician_profile_id != profile.id
        or predecessor.signature_status
        != SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL
    ):
        raise ReportDomainError(
            409,
            "liberacao_institucional_nao_comprovada",
            "A liberação institucional não possui evidência verificável.",
        )
    existing = db.execute(
        select(ReportDocument).where(
            ReportDocument.corrects_document_id == predecessor.id
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ReportDomainError(
            409,
            "laudo_ja_possui_corretiva",
            "Este laudo já possui documento corretivo.",
        )
    original_version = _version_by_kind(
        db, predecessor.id, KIND_ORIGINAL
    )
    if original_version is None:
        raise ReportDomainError(
            409,
            "pdf_original_ausente",
            "O PDF original não está disponível para correção.",
        )
    original = _read_stored_version(original_version)

    try:
        with report_publication_transaction(db) as publication:
            corrective = ReportDocument(
                public_code=allocate_public_code(db, "report_documents"),
                spirometry_exam_id=predecessor.spirometry_exam_id,
                status=STATUS_ATRIBUIDO,
                origin_type=predecessor.origin_type,
                origin_label=predecessor.origin_label,
                origin_partner_unit_id=predecessor.origin_partner_unit_id,
                corrects_document_id=predecessor.id,
                correction_reason_code=payload.reason_code,
                created_by_user_id=physician_user.id,
            )
            db.add(corrective)
            db.flush()
            assignment = _create_assignment(
                db,
                document=corrective,
                profile=profile,
                performed_by_user_id=physician_user.id,
                reason_code="corrective_document",
                event_type="corrective_assigned",
            )
            new_original = _store_new_version(
                db,
                publication=publication,
                document=corrective,
                exam_id=corrective.spirometry_exam_id,
                kind=KIND_ORIGINAL,
                data=original.data,
                created_by_user_id=physician_user.id,
                **_physician_snapshot(profile, corrective),
            )
            corrective.current_version_id = new_original.id
            audit(
                db,
                "laudo_corretivo_aberto",
                entidade="report_documents",
                entidade_id=corrective.id,
                user_id=physician_user.id,
                request_id=_request_id(request),
                detalhes={
                    "status": corrective.status,
                    "reason_code": payload.reason_code,
                    "predecessor_document_id": predecessor.id,
                    "assignment_id": assignment.id,
                },
            )
            publication.commit()
    except IntegrityError:
        raise ReportDomainError(
            409,
            "laudo_ja_possui_corretiva",
            "Este laudo já possui documento corretivo.",
        ) from None
    return {
        **ser_report_document(
            corrective, versions=[new_original], include_clinical=True
        ),
        "assignment": ser_report_assignment(assignment),
    }


@router.get("/{document_id}/assinatura")
def get_report_signature_status(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # M24D (fecha F4) — mesmo tratamento de get_report_document: checa papel
    # antes de saber se o id existe, para não servir de oráculo.
    is_medico = user_has_explicit_role(user, ROLE_MEDICO)
    is_operacional = ROLE_OPERACIONAL in user_effective_roles(user)
    if not is_medico and not is_operacional:
        raise ReportDomainError(
            403, "permissao_insuficiente", "Permissão insuficiente."
        )
    document = _get_document_or_404(db, document_id)
    if is_medico:
        _require_assigned_physician(db, user, document)
    if document.signature_status is None:
        return {
            "status": None,
            "provider": "unconfigured",
            "releasable": False,
            "message": "Conteúdo clínico ainda não preparado para assinatura.",
        }
    signature = db.execute(
        select(ReportSignature).where(
            ReportSignature.report_document_version_id
            == document.current_version_id
        )
    ).scalar_one_or_none()
    if signature is None:
        return {
            "status": SIGNATURE_STATUS_PENDENTE,
            "provider": "unconfigured",
            "releasable": False,
            "message": "Assinatura qualificada pendente.",
        }
    result = ser_report_signature(signature)
    # Sem adapter autorizado, nunca há liberação nesta versão.
    result["releasable"] = False
    return result


# ============================================================== M25.2
#
# Laudo médico PRÓPRIO da SoproLife.
#
# Dois documentos SEPARADOS convivem no mesmo caso de laudo:
#
#   - versão `original`  -> PDF técnico da MIR. Intacto, jamais recomposto,
#     jamais assinado por cima, sempre baixável por conta própria;
#   - versões `laudo_*`  -> laudo médico gerado nativamente pelo Centro de
#     Comando (app/services/report_native_pdf.py).
#
# A assinatura manuscrita só entra depois da ação consciente "Assinar e
# liberar laudo", executada pela médica atribuída na PRÓPRIA sessão. O
# caminho de assinatura QUALIFICADA (PAdES/ICP-Brasil) permanece intocado e
# continua exigindo um provedor real: nada aqui o simula.


def _exam_has_post_bd(exam: SpirometryExam) -> bool:
    """Fase pós-BD é um fato do exame, nunca inferido do texto clínico."""

    return exam.broncodilatador is True


def _native_version_kinds() -> frozenset[str]:
    return frozenset(
        {KIND_LAUDO_PREVIA, KIND_LAUDO_LIBERADO, KIND_LAUDO_ADENDO}
    )


def _latest_native_version(
    db: Session, document_id: str
) -> ReportDocumentVersion | None:
    return db.execute(
        select(ReportDocumentVersion)
        .where(
            ReportDocumentVersion.report_document_id == document_id,
            ReportDocumentVersion.kind.in_(_native_version_kinds()),
        )
        .order_by(ReportDocumentVersion.version_number.desc())
        .limit(1)
    ).scalar_one_or_none()


def _conclusion_snapshot(
    *,
    conclusion_code: str,
    conclusion_text: str,
    bronchodilator_code: str | None,
    bronchodilator_text: str,
    observations: str | None,
    exam: SpirometryExam,
    location,
    validation_code: str | None = None,
    released_at: datetime | None = None,
    signature_asset_id: str | None = None,
    signature_asset_sha256: str | None = None,
    addendum_sequence: int | None = None,
) -> dict:
    """Evidência clínica congelada de uma versão de laudo nativo."""

    snapshot = {
        "conclusion_code_snapshot": conclusion_code,
        "conclusion_text_snapshot": conclusion_text,
        "bronchodilator_code_snapshot": bronchodilator_code,
        "bronchodilator_text_snapshot": (
            bronchodilator_text if bronchodilator_code else None
        ),
        "observations_snapshot": observations,
        "exam_has_post_bd_snapshot": exam.broncodilatador,
        "validation_code_snapshot": validation_code,
        "released_at_snapshot": released_at,
        "signature_asset_id_snapshot": signature_asset_id,
        "signature_asset_sha256_snapshot": signature_asset_sha256,
        "addendum_sequence": addendum_sequence,
    }
    snapshot.update(location.as_snapshot())
    return snapshot


def _publish_native_version(
    db: Session,
    *,
    publication: ReportPublicationTransaction,
    document: ReportDocument,
    exam: SpirometryExam,
    profile: PhysicianProfile,
    kind: str,
    data: bytes,
    created_by_user_id: str,
    final_text: str,
    extra: dict,
) -> ReportDocumentVersion:
    """Grava uma versão de laudo nativo com toda a evidência imutável.

    Reaproveita `_store_new_version`, que revalida os bytes, publica de
    forma atômica, relê o arquivo e confere hash/tamanho/páginas.
    """

    return _store_new_version(
        db,
        publication=publication,
        document=document,
        exam_id=document.spirometry_exam_id,
        kind=kind,
        data=data,
        created_by_user_id=created_by_user_id,
        interpretation_text_snapshot=final_text,
        **_physician_snapshot(profile, document),
        **extra,
    )


def _native_pdf_bytes(content) -> bytes:
    try:
        return build_native_report_pdf(content)
    except NativeReportPdfError as exc:
        raise ReportDomainError(422, exc.codigo, exc.mensagem) from None
    except (OSError, ValueError, MemoryError):
        # Nunca vaza traceback, caminho ou conteúdo clínico na resposta.
        raise ReportDomainError(
            500,
            "falha_geracao_laudo",
            "Não foi possível gerar o PDF do laudo.",
        ) from None


def _pilot_warning() -> str | None:
    """M25.18 — o laudo nativo não carrega mais faixa de piloto.

    A faixa dizia "PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO
    PACIENTE" e ocupava o topo de todo PDF. Ela era verdadeira quando o
    fluxo era um protótipo; hoje o documento é produzido em atendimento real
    e segue para assinatura qualificada externa, então a faixa passou a
    descrever errado o que o papel é.

    O que a substitui não é omissão: o bloco de assinatura declara, por
    escrito, que o documento foi concluído pela médica no sistema e que a
    autenticidade da assinatura digital se verifica no arquivo
    eletronicamente assinado. `reports_mode` continua `pilot`, e continua
    sendo o que impede o modo produção — só deixou de virar faixa.

    A função continua existindo (e continua devolvendo `None` de propósito)
    para manter um único ponto de decisão sobre avisos de topo do laudo.
    """

    return None


def _document_context(db: Session, document: ReportDocument):
    try:
        return resolve_document_context(db, document)
    except NativeReportBuildError as exc:
        raise ReportDomainError(409, exc.codigo, exc.mensagem) from None


def _resolve_signature(db: Session, profile_id: str):
    settings = get_settings()
    try:
        return resolve_signature_asset(
            db, profile_id, storage_root=_storage_root(settings)
        )
    except NativeReportBuildError as exc:
        raise ReportDomainError(409, exc.codigo, exc.mensagem) from None


@router.get("/{document_id}/catalogo-conclusoes")
def get_conclusion_catalog(
    document_id: str,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Catálogo fechado de conclusões e complementos pós-BD do exame.

    Complementos incompatíveis simplesmente não são oferecidos quando o
    exame não tem fase pós-broncodilatador.
    """

    if not user_has_explicit_role(physician_user, ROLE_MEDICO):
        raise ReportDomainError(
            403,
            "papel_medico_explicito_necessario",
            "O catálogo clínico exige o papel médico explícito.",
        )
    document = _get_document_or_404(db, document_id)
    _require_assigned_physician(db, physician_user, document)
    exam = db.get(SpirometryExam, document.spirometry_exam_id)
    if exam is None:
        raise ReportDomainError(
            409, "exame_nao_encontrado", "Exame do laudo não encontrado."
        )
    return catalog_payload(has_post_bd=_exam_has_post_bd(exam))


@router.post("/{document_id}/laudo/previa")
def compose_native_report_preview(
    document_id: str,
    payload: ReportNativeDraft,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Gera a prévia EXATA do laudo que será assinado.

    A prévia usa o mesmo gerador do documento final; só muda a tarja de
    estado, a ausência de código de validação e a ausência da assinatura
    manuscrita.
    """

    document = _lock_document_or_404(db, document_id)
    profile, _assignment = _require_assigned_physician(
        db, physician_user, document, lock=True
    )
    if document.status not in {STATUS_ATRIBUIDO, STATUS_EM_ELABORACAO}:
        raise ReportDomainError(
            409,
            "laudo_bloqueado_para_edicao",
            "Este laudo não aceita mais edição de conteúdo clínico.",
        )
    exam, person, location = _document_context(db, document)
    has_post_bd = _exam_has_post_bd(exam)

    try:
        conclusion_text = resolve_conclusion_text(
            conclusion_code=payload.conclusion_code,
            custom_text=payload.conclusion_custom_text,
        )
        bronchodilator_text = resolve_bronchodilator_text(
            bronchodilator_code=payload.bronchodilator_code,
            has_post_bd=has_post_bd,
        )
        if payload.final_text is None:
            final_text = compose_default_conclusion_text(
                conclusion_code=payload.conclusion_code,
                custom_text=payload.conclusion_custom_text,
                bronchodilator_code=payload.bronchodilator_code,
                has_post_bd=has_post_bd,
            )
        else:
            final_text = normalize_final_text(payload.final_text)
        observations = normalize_observations(payload.observations)
    except ConclusionCatalogError as exc:
        raise ReportDomainError(422, exc.codigo, exc.mensagem) from None

    now = datetime.now(timezone.utc)
    version_number = _next_version_number(db, document.id)
    content = build_native_content(
        db,
        document=document,
        exam=exam,
        person=person,
        profile=profile,
        location=location,
        version_number=version_number,
        conclusion_text=final_text,
        observations=observations,
        issued_at=now,
        released=False,
        addenda=load_addenda(db, document.id),
        pilot_warning=_pilot_warning(),
    )
    data = _native_pdf_bytes(content)

    with report_publication_transaction(db) as publication:
        version = _publish_native_version(
            db,
            publication=publication,
            document=document,
            exam=exam,
            profile=profile,
            kind=KIND_LAUDO_PREVIA,
            data=data,
            created_by_user_id=physician_user.id,
            final_text=final_text,
            extra=_conclusion_snapshot(
                conclusion_code=payload.conclusion_code,
                conclusion_text=conclusion_text,
                bronchodilator_code=payload.bronchodilator_code,
                bronchodilator_text=bronchodilator_text,
                observations=observations,
                exam=exam,
                location=location,
            ),
        )
        if document.status == STATUS_ATRIBUIDO:
            document.status = STATUS_EM_ELABORACAO
            document.clinical_started_at = now
        document.current_version_id = version.id
        audit(
            db,
            "laudo_nativo_previa_gerada",
            entidade="report_documents",
            entidade_id=document.id,
            user_id=physician_user.id,
            request_id=_request_id(request),
            # Somente identificadores técnicos e códigos de catálogo:
            # nenhum texto clínico, paciente, caminho ou byte de PDF.
            detalhes={
                "status": document.status,
                "report_version_id": version.id,
                "version_number": version.version_number,
                "conclusion_code": payload.conclusion_code,
                "bronchodilator_code": payload.bronchodilator_code,
                "location_source": location.source,
                "document_sha256": version.sha256,
            },
        )
        publication.commit()

    return {
        **ser_report_document(
            document, versions=[version], include_clinical=True
        ),
        "preview_version_id": version.id,
        "final_text": final_text,
        "final_text_sha256": version.interpretation_text_sha256,
        "location": location.as_payload(),
        "exam_has_post_bd": has_post_bd,
    }


@router.post("/{document_id}/assinar-e-liberar")
def sign_and_release_report(
    document_id: str,
    payload: ReportReleaseRequest,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Ação consciente da médica atribuída: assina e libera o laudo.

    Proteções contra assinatura automática ou às cegas:

    - papel médico explícito, perfil ativo/verificado e atribuição ativa,
      tudo na sessão individual da própria médica;
    - confirmação textual obrigatória no corpo do pedido;
    - `expected_version_id` precisa ser a prévia atual;
    - `expected_text_sha256` precisa bater com o texto daquela prévia, de
      modo que conteúdo trocado por concorrência nunca seja assinado.
    """

    document = _lock_document_or_404(db, document_id)
    profile, _assignment = _require_assigned_physician(
        db, physician_user, document, lock=True
    )
    if document.status == STATUS_LAUDO_LIBERADO:
        raise ReportDomainError(
            409,
            "laudo_ja_liberado",
            "Este laudo já foi liberado; use adendo ou versão corretiva.",
        )
    if document.status != STATUS_EM_ELABORACAO:
        raise ReportDomainError(
            409,
            "laudo_fora_de_elaboracao",
            "Gere a prévia do laudo antes de assinar e liberar.",
        )
    draft = db.get(ReportDocumentVersion, document.current_version_id)
    if (
        draft is None
        or draft.report_document_id != document.id
        or draft.kind != KIND_LAUDO_PREVIA
    ):
        raise ReportDomainError(
            409,
            "previa_laudo_ausente",
            "Gere a prévia do laudo antes de assinar e liberar.",
        )
    if payload.expected_version_id != draft.id:
        raise ReportDomainError(
            409,
            "previa_desatualizada",
            "A prévia mudou desde a conferência; revise antes de assinar.",
        )
    if payload.expected_text_sha256 != (draft.interpretation_text_sha256 or ""):
        raise ReportDomainError(
            409,
            "conteudo_divergente_da_previa",
            "O texto conferido não corresponde ao texto atual da prévia.",
        )
    if not draft.conclusion_code_snapshot or not draft.interpretation_text_snapshot:
        raise ReportDomainError(
            409,
            "evidencia_previa_incompleta",
            "A prévia não possui evidência clínica completa.",
        )

    exam, person, location = _document_context(db, document)
    resolved_signature = _resolve_signature(db, profile.id)
    now = datetime.now(timezone.utc)
    version_number = _next_version_number(db, document.id)
    validation_code = generate_validation_code()

    content = build_native_content(
        db,
        document=document,
        exam=exam,
        person=person,
        profile=profile,
        location=location,
        version_number=version_number,
        conclusion_text=draft.interpretation_text_snapshot,
        observations=draft.observations_snapshot,
        issued_at=now,
        released=True,
        released_at=now,
        validation_code=validation_code,
        signature_image=(
            resolved_signature.image if resolved_signature else None
        ),
        # Na liberação a evidência ainda não foi gravada — ela nasce logo
        # abaixo, junto da versão. O selo declara a liberação institucional
        # porque é exatamente isso que está acontecendo aqui: uma assinatura
        # qualificada viria de um provedor, por outro caminho.
        signature_kind=SIGNATURE_KIND_INSTITUTIONAL,
        addenda=load_addenda(db, document.id),
        pilot_warning=_pilot_warning(),
    )
    # M25.20 — o laudo concluído sai daqui já CARIMBADO com a identificação
    # da SoproLife nos metadados. É esse carimbo que permite reconhecer o
    # arquivo quando ele voltar assinado, mesmo depois de o iPhone, o
    # assinador ou a própria médica o renomearem.
    #
    # Carimbar só mexe no dicionário de metadados: nenhum pixel, nenhuma
    # linha de texto e nenhum valor impresso muda. O PDF não imprime o
    # próprio hash — só menciona que ele existe —, então nada no papel fica
    # desatualizado por causa deste passo.
    try:
        data = stamp_signing_metadata(
            _native_pdf_bytes(content),
            document_code=document.public_code,
            validation_code=validation_code,
            version_number=version_number,
            physician_name=profile.professional_name,
            crm=format_crm_full(
                profile.crm_number,
                profile.crm_state,
                crm_display=profile.crm_display,
            ),
        )
    except SignatureBatchError as exc:
        raise ReportDomainError(500, exc.codigo, exc.mensagem) from None

    try:
        with report_publication_transaction(db) as publication:
            released = _publish_native_version(
                db,
                publication=publication,
                document=document,
                exam=exam,
                profile=profile,
                kind=KIND_LAUDO_LIBERADO,
                data=data,
                created_by_user_id=physician_user.id,
                final_text=draft.interpretation_text_snapshot,
                extra=_conclusion_snapshot(
                    conclusion_code=draft.conclusion_code_snapshot,
                    conclusion_text=draft.conclusion_text_snapshot,
                    bronchodilator_code=draft.bronchodilator_code_snapshot,
                    bronchodilator_text=(
                        draft.bronchodilator_text_snapshot or ""
                    ),
                    observations=draft.observations_snapshot,
                    exam=exam,
                    location=location,
                    validation_code=validation_code,
                    released_at=now,
                    signature_asset_id=(
                        resolved_signature.asset.id
                        if resolved_signature
                        else None
                    ),
                    signature_asset_sha256=(
                        resolved_signature.asset.sha256
                        if resolved_signature
                        else None
                    ),
                ),
            )
            document.status = STATUS_LAUDO_LIBERADO
            document.signature_status = SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL
            document.ready_for_signature_at = now
            document.released_at = now
            document.released_by_user_id = physician_user.id
            document.released_physician_profile_id = profile.id
            document.validation_code = validation_code
            document.current_version_id = released.id
            signature = ReportSignature(
                report_document_version_id=released.id,
                provider=PROVIDER_INSTITUTIONAL_RELEASE,
                status=SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL,
                requested_by_user_id=physician_user.id,
                requested_at=now,
                completed_at=now,
                external_reference=validation_code,
                verification_metadata=institutional_release_evidence(
                    physician_profile_id=profile.id,
                    document_sha256=released.sha256,
                    signed_text_sha256=(
                        released.interpretation_text_sha256 or ""
                    ),
                    signature_asset_sha256=(
                        resolved_signature.asset.sha256
                        if resolved_signature
                        else None
                    ),
                ),
            )
            db.add(signature)
            audit(
                db,
                "laudo_assinado_e_liberado",
                entidade="report_documents",
                entidade_id=document.id,
                user_id=physician_user.id,
                request_id=_request_id(request),
                detalhes={
                    "status": document.status,
                    "signature_status": document.signature_status,
                    "provider": PROVIDER_INSTITUTIONAL_RELEASE,
                    "physician_profile_id": profile.id,
                    "report_version_id": released.id,
                    "version_number": released.version_number,
                    "conclusion_code": draft.conclusion_code_snapshot,
                    "bronchodilator_code": draft.bronchodilator_code_snapshot,
                    # Hash do PDF final e do texto efetivamente assinado.
                    "document_sha256": released.sha256,
                    "signed_text_sha256": (
                        released.interpretation_text_sha256
                    ),
                    "validation_code": validation_code,
                    "handwritten_signature_applied": bool(resolved_signature),
                    "qualified_signature": False,
                },
            )
            publication.commit()
    except IntegrityError:
        raise ReportDomainError(
            409,
            "liberacao_concorrente",
            "Outra liberação ocorreu ao mesmo tempo; recarregue o laudo.",
        ) from None

    return {
        **ser_report_document(
            document, versions=[released], include_clinical=True
        ),
        "released_version_id": released.id,
        "validation_code": validation_code,
        "validation_url": validation_url(validation_code),
        "document_sha256": released.sha256,
        "handwritten_signature_applied": bool(resolved_signature),
        "qualified_signature": False,
    }


@router.post("/{document_id}/adendo", status_code=201)
def add_report_addendum(
    document_id: str,
    payload: ReportAddendumCreate,
    request: Request,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Adendo append-only sobre laudo liberado.

    A versão liberada anterior NUNCA é apagada, sobrescrita ou alterada:
    o adendo produz uma versão NOVA que contém o laudo mais os adendos
    acumulados, e o histórico completo permanece baixável.
    """

    document = _lock_document_or_404(db, document_id)
    profile, _assignment = _require_assigned_physician(
        db, physician_user, document, lock=True
    )
    if document.status != STATUS_LAUDO_LIBERADO:
        raise ReportDomainError(
            409,
            "laudo_nao_liberado",
            "Somente um laudo liberado aceita adendo.",
        )
    try:
        body_text = normalize_final_text(payload.body_text)
    except ConclusionCatalogError as exc:
        raise ReportDomainError(422, exc.codigo, exc.mensagem) from None

    released_version = db.get(
        ReportDocumentVersion, document.current_version_id
    )
    if released_version is None or released_version.kind not in {
        KIND_LAUDO_LIBERADO,
        KIND_LAUDO_ADENDO,
    }:
        raise ReportDomainError(
            409,
            "versao_liberada_ausente",
            "A versão liberada do laudo não está disponível.",
        )

    exam, person, location = _document_context(db, document)
    resolved_signature = _resolve_signature(db, profile.id)
    now = datetime.now(timezone.utc)
    next_sequence = int(
        db.execute(
            select(func.max(ReportAddendum.sequence)).where(
                ReportAddendum.report_document_id == document.id
            )
        ).scalar_one()
        or 0
    ) + 1
    version_number = _next_version_number(db, document.id)

    addenda = load_addenda(db, document.id) + (
        AddendumBlock(
            sequence=next_sequence,
            body_text=body_text,
            created_at=to_display_timezone(now),
        ),
    )
    content = build_native_content(
        db,
        document=document,
        exam=exam,
        person=person,
        profile=profile,
        location=location,
        version_number=version_number,
        conclusion_text=released_version.interpretation_text_snapshot or "",
        observations=released_version.observations_snapshot,
        issued_at=now,
        released=True,
        released_at=document.released_at,
        validation_code=document.validation_code,
        signature_image=(
            resolved_signature.image if resolved_signature else None
        ),
        # O adendo reemite o laudo JÁ liberado: o selo precisa continuar
        # declarando a mesma assinatura que fechou o documento original.
        signature_kind=_seal_signature_kind(
            db, version=released_version, profile=profile
        ),
        addenda=addenda,
        pilot_warning=_pilot_warning(),
    )
    data = _native_pdf_bytes(content)

    try:
        with report_publication_transaction(db) as publication:
            version = _publish_native_version(
                db,
                publication=publication,
                document=document,
                exam=exam,
                profile=profile,
                kind=KIND_LAUDO_ADENDO,
                data=data,
                created_by_user_id=physician_user.id,
                final_text=(
                    released_version.interpretation_text_snapshot or ""
                ),
                extra=_conclusion_snapshot(
                    conclusion_code=released_version.conclusion_code_snapshot,
                    conclusion_text=released_version.conclusion_text_snapshot,
                    bronchodilator_code=(
                        released_version.bronchodilator_code_snapshot
                    ),
                    bronchodilator_text=(
                        released_version.bronchodilator_text_snapshot or ""
                    ),
                    observations=released_version.observations_snapshot,
                    exam=exam,
                    location=location,
                    validation_code=document.validation_code,
                    released_at=document.released_at,
                    signature_asset_id=(
                        resolved_signature.asset.id
                        if resolved_signature
                        else None
                    ),
                    signature_asset_sha256=(
                        resolved_signature.asset.sha256
                        if resolved_signature
                        else None
                    ),
                    addendum_sequence=next_sequence,
                ),
            )
            addendum = ReportAddendum(
                report_document_id=document.id,
                sequence=next_sequence,
                body_text=body_text,
                body_sha256=text_sha256(body_text),
                physician_profile_id=profile.id,
                created_by_user_id=physician_user.id,
                report_document_version_id=version.id,
                created_at=now,
            )
            db.add(addendum)
            # O documento permanece LIBERADO; muda apenas qual versão é a
            # corrente. Todas as anteriores continuam preservadas.
            document.current_version_id = version.id
            audit(
                db,
                "laudo_adendo_publicado",
                entidade="report_documents",
                entidade_id=document.id,
                user_id=physician_user.id,
                request_id=_request_id(request),
                detalhes={
                    "status": document.status,
                    "physician_profile_id": profile.id,
                    "report_version_id": version.id,
                    "version_number": version.version_number,
                    "addendum_sequence": next_sequence,
                    "addendum_sha256": addendum.body_sha256,
                    "document_sha256": version.sha256,
                    "supersedes_version_id": released_version.id,
                },
            )
            publication.commit()
    except IntegrityError:
        raise ReportDomainError(
            409,
            "adendo_concorrente",
            "Outro adendo foi publicado ao mesmo tempo; recarregue o laudo.",
        ) from None

    return {
        **ser_report_document(
            document, versions=[version], include_clinical=True
        ),
        "addendum_sequence": next_sequence,
        "addendum_version_id": version.id,
    }


@router.get("/{document_id}/documentos")
def list_report_documents_for_delivery(
    document_id: str,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    """Os DOIS documentos do caso, para download separado.

    - `tecnico_mir`: PDF original do equipamento, intacto;
    - `laudo_soprolife`: laudo médico próprio (prévia enquanto não
      liberado; versão liberada/adendo depois).
    """

    if not user_has_explicit_role(physician_user, ROLE_MEDICO):
        raise ReportDomainError(
            403,
            "papel_medico_explicito_necessario",
            "A consulta exige o papel médico explícito.",
        )
    document = _get_document_or_404(db, document_id)
    _require_assigned_physician(db, physician_user, document)
    original = _version_by_kind(db, document.id, KIND_ORIGINAL)
    native = _latest_native_version(db, document.id)

    def _entry(version: ReportDocumentVersion | None) -> dict | None:
        if version is None:
            return None
        return {
            "version_id": version.id,
            "kind": version.kind,
            "version_number": version.version_number,
            "sha256": version.sha256,
            "size_bytes": version.size_bytes,
            "page_count": version.page_count,
            "created_at": iso(version.created_at),
            "download_path": (
                f"/laudos/{document.id}/versoes/{version.id}/conteudo"
            ),
        }

    exam = db.get(SpirometryExam, document.spirometry_exam_id)
    person = db.get(Person, exam.person_id) if exam else None
    return {
        "document_id": document.id,
        "report_code": document.public_code,
        # M25.15 — de quem são estes dois PDFs. Baixar e enviar o laudo da
        # pessoa errada é falha grave, e até aqui a tela de download mostrava
        # só LAU-xxxxxx. Rota já restrita ao médico ATRIBUÍDO a este
        # documento, que enxerga a mesma identidade dentro da bancada.
        "patient": _patient_reference(person),
        "exam_code": exam.public_code if exam else None,
        "status": document.status,
        "locked": document.status == STATUS_LAUDO_LIBERADO,
        "validation_code": document.validation_code,
        # Documento 1 — nunca alterado, nunca assinado por cima.
        "tecnico_mir": _entry(original),
        # Documento 2 — laudo médico gerado nativamente.
        "laudo_soprolife": _entry(native),
        "observacao": (
            "O PDF técnico da MIR e o laudo médico da SoproLife são "
            "documentos separados e devem ser baixados separadamente."
        ),
    }


@router.get("/validacao/{codigo}")
def validate_released_report(
    codigo: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Confere um código impresso no laudo.

    Responde SOMENTE com dados institucionais e técnicos: nada de
    paciente, texto clínico ou conclusão. A rota exige sessão autenticada
    porque o Centro de Comando não expõe verificação anônima; uma futura
    verificação pública precisa de decisão de privacidade própria.
    """

    normalized = (codigo or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{8,24}", normalized):
        raise ReportDomainError(
            422, "codigo_validacao_invalido", "Código de validação inválido."
        )
    document = db.execute(
        select(ReportDocument).where(
            ReportDocument.validation_code == normalized
        )
    ).scalar_one_or_none()
    if document is None or document.status != STATUS_LAUDO_LIBERADO:
        # Mesma resposta para código inexistente e não liberado.
        raise ReportDomainError(
            404,
            "laudo_nao_localizado",
            "Nenhum laudo liberado corresponde a este código.",
        )
    version = db.get(ReportDocumentVersion, document.current_version_id)
    profile = (
        db.get(PhysicianProfile, document.released_physician_profile_id)
        if document.released_physician_profile_id
        else None
    )
    return {
        "validation_code": document.validation_code,
        "report_code": document.public_code,
        "status": document.status,
        "released_at": iso(document.released_at),
        "version_number": version.version_number if version else None,
        "document_sha256": version.sha256 if version else None,
        "physician_name": profile.professional_name if profile else None,
        # M25.15 — mesma apresentação canônica da tela e do PDF. Conferir um
        # laudo é comparar o que está no papel com o que a rota devolve; um
        # CRM formatado de outro jeito aqui faria a conferência falhar por
        # divergência puramente cosmética.
        "physician_crm": (
            format_crm_full(
                profile.crm_number,
                profile.crm_state,
                crm_display=profile.crm_display,
            )
            if profile
            else None
        ),
        "physician_rqe": profile.rqe if profile else None,
        # Declaração honesta da natureza da liberação.
        "qualified_signature": False,
        "signature_kind": PROVIDER_INSTITUTIONAL_RELEASE,
        "observacao": (
            "Liberação institucional autenticada. Não constitui, por si "
            "só, assinatura digital qualificada ICP-Brasil."
        ),
    }


# ------------------------------- ativo de assinatura manuscrita (admin)
#
# A imagem autorizada é cadastrada por ADMIN e nunca por quem opera ou por
# quem assina. Ela vive apenas na raiz privada de laudos: não entra no Git,
# não é servida ao navegador, não vira URL, não aparece em log e não é
# devolvida por nenhuma resposta desta API — só é desenhada dentro do PDF
# do laudo já liberado.


def _ser_signature_asset(asset: PhysicianSignatureAsset) -> dict:
    """Metadados técnicos apenas — nunca bytes, nunca caminho."""

    return {
        "id": asset.id,
        "physician_profile_id": asset.physician_profile_id,
        "sha256": asset.sha256,
        "size_bytes": asset.size_bytes,
        "mime_type": asset.mime_type,
        "image_width": asset.image_width,
        "image_height": asset.image_height,
        "active": asset.active,
        "created_at": iso(asset.created_at),
        "revoked_at": iso(asset.revoked_at),
    }


@router.get("/admin/medicos/{profile_id}/assinatura")
def get_signature_asset_status(
    profile_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(ROLE_ADMIN)),
):
    profile = db.get(PhysicianProfile, profile_id)
    if profile is None:
        raise ReportDomainError(
            404, "perfil_medico_nao_encontrado", "Perfil médico não encontrado."
        )
    asset = active_signature_asset(db, profile.id)
    return {
        "physician_profile_id": profile.id,
        "configurada": asset is not None,
        "ativo": _ser_signature_asset(asset) if asset else None,
        "observacao": (
            "Uma imagem de assinatura manuscrita é elemento visual de "
            "identificação e não constitui assinatura digital qualificada."
        ),
    }


@router.post("/admin/medicos/{profile_id}/assinatura", status_code=201)
async def upload_signature_asset(
    profile_id: str,
    request: Request,
    arquivo: UploadFile = File(...),
    confirmacao: str = Form(...),
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_role(ROLE_ADMIN)),
):
    """Cadastra a imagem autorizada da assinatura de um perfil médico.

    Exige confirmação explícita de que o arquivo é o ativo autorizado
    daquela médica: nenhuma assinatura é desenhada, simulada ou inferida
    pelo sistema.
    """

    if confirmacao.strip() != "ATIVO DE ASSINATURA AUTORIZADO":
        raise ReportDomainError(
            422,
            "confirmacao_assinatura_ausente",
            "Confirme explicitamente que o arquivo é o ativo autorizado.",
        )
    profile = db.get(PhysicianProfile, profile_id)
    if profile is None:
        raise ReportDomainError(
            404, "perfil_medico_nao_encontrado", "Perfil médico não encontrado."
        )
    settings = get_settings()
    storage_root = _storage_root(settings)
    raw = await _read_upload_bounded(
        arquivo, max_size_bytes=settings.reports_signature_max_bytes
    )
    try:
        validated = validate_signature_png(
            raw, max_size_bytes=settings.reports_signature_max_bytes
        )
    except SignatureAssetError as exc:
        raise ReportDomainError(422, exc.codigo, exc.mensagem) from None

    now = datetime.now(timezone.utc)
    asset_id = new_uuid()
    try:
        with report_publication_transaction(db) as publication:
            previous = db.execute(
                select(PhysicianSignatureAsset)
                .where(
                    PhysicianSignatureAsset.physician_profile_id == profile.id,
                    PhysicianSignatureAsset.active.is_(True),
                )
                .with_for_update()
            ).scalar_one_or_none()
            if previous is not None:
                # A imagem anterior é REVOGADA, nunca apagada: laudos já
                # liberados continuam apontando para o hash que usaram.
                previous.active = False
                previous.revoked_at = now
                previous.revoked_by_user_id = admin_user.id
                db.flush()

            path = signature_asset_storage_path(
                storage_root,
                physician_profile_id=profile.id,
                asset_id=asset_id,
            )
            publication.publish(path, validated.data, root=storage_root)
            asset = PhysicianSignatureAsset(
                id=asset_id,
                physician_profile_id=profile.id,
                storage_path=str(path.relative_to(storage_root)),
                sha256=validated.sha256,
                size_bytes=validated.size_bytes,
                mime_type=validated.mime_type,
                image_width=validated.width,
                image_height=validated.height,
                active=True,
                created_by_user_id=admin_user.id,
                created_at=now,
            )
            db.add(asset)
            audit(
                db,
                "assinatura_medica_cadastrada",
                entidade="physician_signature_assets",
                entidade_id=asset_id,
                user_id=admin_user.id,
                request_id=_request_id(request),
                # Sem filename enviado, sem caminho, sem bytes.
                detalhes={
                    "physician_profile_id": profile.id,
                    "sha256": validated.sha256,
                    "size_bytes": validated.size_bytes,
                    "image_width": validated.width,
                    "image_height": validated.height,
                    "revoked_previous_asset_id": (
                        previous.id if previous else None
                    ),
                },
            )
            publication.commit()
    except (ReportStorageError, OSError) as exc:
        raise ReportDomainError(
            503,
            "armazenamento_laudos_indisponivel",
            "Armazenamento de laudos indisponível.",
        ) from exc
    except IntegrityError:
        raise ReportDomainError(
            409,
            "assinatura_concorrente",
            "Outro cadastro de assinatura ocorreu ao mesmo tempo.",
        ) from None
    return _ser_signature_asset(asset)


@router.delete("/admin/medicos/{profile_id}/assinatura")
def revoke_signature_asset(
    profile_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_role(ROLE_ADMIN)),
):
    """Revoga o ativo ativo. O arquivo e os laudos anteriores permanecem."""

    profile = db.get(PhysicianProfile, profile_id)
    if profile is None:
        raise ReportDomainError(
            404, "perfil_medico_nao_encontrado", "Perfil médico não encontrado."
        )
    asset = db.execute(
        select(PhysicianSignatureAsset)
        .where(
            PhysicianSignatureAsset.physician_profile_id == profile.id,
            PhysicianSignatureAsset.active.is_(True),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if asset is None:
        raise ReportDomainError(
            404,
            "assinatura_nao_configurada",
            "Não há ativo de assinatura configurado para este perfil.",
        )
    asset.active = False
    asset.revoked_at = datetime.now(timezone.utc)
    asset.revoked_by_user_id = admin_user.id
    audit(
        db,
        "assinatura_medica_revogada",
        entidade="physician_signature_assets",
        entidade_id=asset.id,
        user_id=admin_user.id,
        request_id=_request_id(request),
        detalhes={
            "physician_profile_id": profile.id,
            "sha256": asset.sha256,
        },
    )
    db.commit()
    return _ser_signature_asset(asset)
