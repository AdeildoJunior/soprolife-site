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
from ..models import (
    PartnerUnit,
    Person,
    PhysicianProfile,
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
from ..normalize import contains_clinical_info, contains_pii_like
from ..schemas import (
    PhysicianProfileAdminUpdate,
    ReportCorrectiveCreate,
    ReportDocumentCompose,
    ReportReassignment,
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
    ser_physician_profile,
    ser_report_assignment,
    ser_report_document,
    ser_report_signature,
    ser_report_template,
    ser_user,
)
from ..services.pdf_validation import InvalidPdfError, validate_pdf_bytes
from ..services.report_catalog import (
    PROVISIONAL_CODES,
    PROVISIONAL_WARNING,
    TEST_FOOTER_CODE,
    ensure_m24c_catalog,
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
from ..services.signature_provider import (
    SIGNATURE_STATUS_PENDENTE,
    get_signature_provider,
)

KIND_ORIGINAL = "original"
KIND_RASCUNHO = "rascunho"
KIND_ASSINATURA_PENDENTE = "assinatura_pendente"
KIND_ASSINADO = "assinado"
KIND_FINALIZADO_LEGADO = "finalizado"

STATUS_ATRIBUIDO = "atribuido"
STATUS_EM_ELABORACAO = "em_elaboracao"
STATUS_ASSINATURA_PENDENTE = "assinatura_pendente"
STATUS_ASSINADO = "assinado"

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
    }
)
_UPLOAD_CHUNK_BYTES = 64 * 1024
_SAFE_EXAM_CODE_RE = re.compile(r"^ESP-\d{1,9}$")
_UNSIGNED_WARNING = (
    "MODELO DE TESTE — DOCUMENTO NÃO ASSINADO E SEM VALIDADE PARA LIBERAÇÃO"
)


def _require_reports_enabled() -> None:
    if not get_settings().reports_enabled:
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
    try:
        validated = validate_pdf_bytes(
            data,
            max_size_bytes=settings.reports_max_upload_bytes,
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
            crm_number=profile.crm_number,
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


def _technical_report_row(
    document: ReportDocument,
    exam: SpirometryExam,
    assignment: ReportAssignment | None,
    *,
    include_assignment_ids: bool = False,
) -> dict:
    data = {
        "report_code": document.public_code,
        "document_id": document.id,
        "exam_code": exam.public_code,
        "exam_date": d(exam.data_exame),
        "origin_type": document.origin_type,
        "origin_label": document.origin_label,
        "assignment_timestamp": (
            assignment.assigned_at.isoformat() if assignment else None
        ),
        "status": document.status,
        "signature_status": document.signature_status,
        "releasable": False,
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
    }
    identity_changed = False
    changed_fields: list[str] = []
    for field in identity_fields:
        if field not in values:
            continue
        value = values[field]
        if field == "rqe":
            value = value or None
        if value != getattr(profile, field):
            setattr(profile, field, value)
            identity_changed = True
            changed_fields.append(field)

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
        profile.verification_status = requested_verification
        if requested_verification == "verified":
            profile.verified_at = datetime.now(timezone.utc)
            profile.verified_by_user_id = admin.id
        else:
            profile.verified_at = None
            profile.verified_by_user_id = None
            profile.active = False
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
    origin_type: str = Form(...),
    origin_label: str | None = Form(default=None),
    origin_partner_unit_id: str | None = Form(default=None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    operator: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    exam = _get_exam_by_code_or_404(db, exam_code)
    profile, _account = _profile_for_assignment(db, physician_profile_id)
    safe_origin, safe_label, safe_unit = _validate_origin(
        db,
        origin_type=origin_type,
        origin_label=origin_label,
        partner_unit_id=origin_partner_unit_id,
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
    db: Session = Depends(get_db),
    _operator: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    statement = (
        select(ReportDocument, SpirometryExam, ReportAssignment)
        .join(
            SpirometryExam,
            SpirometryExam.id == ReportDocument.spirometry_exam_id,
        )
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
    rows = db.execute(
        statement.order_by(ReportDocument.created_at.desc()).limit(200)
    ).all()
    return [
        _technical_report_row(
            document, exam, assignment, include_assignment_ids=True
        )
        for document, exam, assignment in rows
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


# --------------------------------------------------------- fila do médico


@router.get("/meus")
def list_my_report_queue(
    status: str | None = None,
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    profile = _require_active_physician(db, physician_user)
    if status and status not in CLINICAL_STATUSES:
        raise ReportDomainError(
            422, "status_laudo_invalido", "Status de laudo inválido."
        )
    statement = (
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
        .where(ReportAssignment.physician_profile_id == profile.id)
    )
    if status:
        statement = statement.where(ReportDocument.status == status)
    rows = db.execute(
        statement.order_by(ReportAssignment.assigned_at.desc()).limit(200)
    ).all()
    return [
        _technical_report_row(document, exam, assignment)
        for document, exam, assignment in rows
    ]


@router.get("/{document_id}")
def get_report_document(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    document = _get_document_or_404(db, document_id)
    if user_has_explicit_role(user, ROLE_MEDICO):
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
            "exam": {
                "public_code": exam.public_code,
                "exam_date": d(exam.data_exame),
            },
            # Somente este workspace atribuído contém identidade do paciente.
            # Ela nunca entra na fila, URL ou audit details.
            "patient": {
                "public_code": person.public_code,
                "full_name": person.nome_completo,
                "date_of_birth": d(person.data_nascimento),
            },
        }
    if ROLE_OPERACIONAL not in user_effective_roles(user):
        raise ReportDomainError(
            403, "permissao_insuficiente", "Permissão insuficiente."
        )
    exam = db.get(SpirometryExam, document.spirometry_exam_id)
    assignment = _active_assignment(db, document.id)
    if exam is None:
        raise ReportDomainError(
            409, "exame_nao_encontrado", "Exame do laudo não encontrado."
        )
    return _technical_report_row(document, exam, assignment)


@router.get("/{document_id}/versoes/{version_id}/conteudo")
def download_report_version(
    document_id: str,
    version_id: str,
    request: Request,
    modo: str = Query(default="inline", pattern="^(inline|download)$"),
    db: Session = Depends(get_db),
    physician_user: User = Depends(get_current_user),
):
    document = _get_document_or_404(db, document_id)
    _profile, _assignment = _require_assigned_physician(
        db, physician_user, document
    )
    version = _get_version_or_404(db, document_id, version_id)
    stored = _read_stored_version(version, missing_status=404)
    exam = db.get(SpirometryExam, document.spirometry_exam_id)
    exam_code = (
        exam.public_code
        if exam is not None and _SAFE_EXAM_CODE_RE.fullmatch(exam.public_code)
        else "ESP"
    )
    safe_kind = (
        version.kind
        if version.kind
        in {
            KIND_ORIGINAL,
            KIND_RASCUNHO,
            KIND_ASSINATURA_PENDENTE,
            KIND_ASSINADO,
            KIND_FINALIZADO_LEGADO,
        }
        else "documento"
    )
    safe_name = (
        f"laudo-{exam_code}-v{version.version_number}-{safe_kind}.pdf"
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
            "Content-Disposition": f'{disposition}; filename="{safe_name}"',
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

    footer = db.execute(
        select(ReportFooterTemplate).where(
            ReportFooterTemplate.code == TEST_FOOTER_CODE,
            ReportFooterTemplate.version == 1,
            ReportFooterTemplate.active.is_(True),
        )
    ).scalar_one_or_none()
    if footer is None:
        raise ReportDomainError(
            503,
            "rodape_teste_indisponivel",
            "O rodapé TESTE não está disponível.",
        )
    exam = db.get(SpirometryExam, document.spirometry_exam_id)
    if exam is None:
        raise ReportDomainError(
            409, "exame_nao_encontrado", "Exame do laudo não encontrado."
        )
    next_number = _next_version_number(db, document.id)
    issued_at = datetime.now(timezone.utc)
    footer_text = _render_test_footer(
        footer,
        profile=profile,
        document=document,
        exam=exam,
        issued_at=issued_at,
        version_number=next_number,
    )
    settings = get_settings()
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
    if (
        draft.template_text_sha256
        != _snapshot_hash(draft.template_text_snapshot or "")
        or draft.interpretation_text_sha256
        != _snapshot_hash(draft.interpretation_text_snapshot or "")
        or draft.footer_text_sha256
        != _snapshot_hash(draft.footer_text_snapshot or "")
        or _UNSIGNED_WARNING not in (draft.footer_text_snapshot or "")
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
        raise ReportDomainError(
            409,
            "rascunho_composto_ausente",
            "Gere uma prévia antes de preparar a assinatura.",
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
    if predecessor.status != STATUS_ASSINADO:
        raise ReportDomainError(
            409,
            "laudo_nao_assinado",
            "Somente um documento genuinamente assinado aceita correção.",
        )
    signed_version = db.get(
        ReportDocumentVersion, predecessor.current_version_id
    )
    signature = db.execute(
        select(ReportSignature).where(
            ReportSignature.report_document_version_id
            == predecessor.current_version_id
        )
    ).scalar_one_or_none()
    if (
        signed_version is None
        or signed_version.kind != KIND_ASSINADO
        or signature is None
        or not _qualified_signature_evidence(
            signature, signed_version, profile
        )
    ):
        raise ReportDomainError(
            409,
            "assinatura_qualificada_nao_comprovada",
            "A assinatura qualificada não possui evidência verificável.",
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
    document = _get_document_or_404(db, document_id)
    if user_has_explicit_role(user, ROLE_MEDICO):
        _require_assigned_physician(db, user, document)
    elif ROLE_OPERACIONAL not in user_effective_roles(user):
        raise ReportDomainError(
            403, "permissao_insuficiente", "Permissão insuficiente."
        )
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
