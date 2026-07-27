"""Laudos PDF (M24A) — upload, revisão, composição e finalização fail-closed.

O recurso possui feature flag própria, desabilitada por padrão. Nenhum
provedor de assinatura digital está conectado: finalizar preserva
imutabilidade e registra somente ``assinatura_pendente``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..errors import ReportDomainError
from ..models import (
    ReportDocument,
    ReportDocumentVersion,
    ReportSignature,
    ReportTemplate,
    SpirometryExam,
    User,
)
from ..schemas import (
    ReportDocumentCompose,
    ReportReviewAdjustment,
    ReportTemplateCreate,
    ReportTemplateUpdate,
)
from ..security import ROLE_ADMIN, ROLE_GESTOR, ROLE_LEITURA, ROLE_OPERACIONAL, require_role
from ..serializers import ser_report_document, ser_report_signature, ser_report_template
from ..services.pdf_validation import InvalidPdfError, validate_pdf_bytes
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

KIND_ORIGINAL = "original"
KIND_RASCUNHO = "rascunho"
KIND_FINALIZADO = "finalizado"

STATUS_RASCUNHO = "rascunho"
STATUS_EM_REVISAO = "em_revisao"
STATUS_FINALIZADO = "finalizado"

SIGNATURE_STATUS_PENDENTE = "assinatura_pendente"
_UPLOAD_CHUNK_BYTES = 64 * 1024
_SAFE_EXAM_CODE_RE = re.compile(r"^ESP-\d{1,9}$")


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


def _storage_root(settings):
    """Mapeia configuração/permissão esperada para 503 sem caminho ou traceback."""

    try:
        return settings.resolved_reports_storage_dir()
    except (ValueError, OSError):
        raise ReportDomainError(
            503,
            "armazenamento_laudos_indisponivel",
            "Armazenamento de laudos indisponível.",
        ) from None


def _get_exam_or_404(db: Session, exam_id: str) -> SpirometryExam:
    exam = db.get(SpirometryExam, exam_id)
    if exam is None:
        raise ReportDomainError(
            404,
            "exame_nao_encontrado",
            "Exame de espirometria não encontrado.",
        )
    return exam


def _get_document_or_404(db: Session, document_id: str) -> ReportDocument:
    doc = db.get(ReportDocument, document_id)
    if doc is None:
        raise ReportDomainError(404, "laudo_nao_encontrado", "Laudo não encontrado.")
    return doc


def _lock_document_or_404(db: Session, document_id: str) -> ReportDocument:
    doc = db.execute(
        select(ReportDocument)
        .where(ReportDocument.id == document_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if doc is None:
        raise ReportDomainError(404, "laudo_nao_encontrado", "Laudo não encontrado.")
    return doc


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


def _next_version_number(db: Session, document_id: str) -> int:
    current = db.execute(
        select(ReportDocumentVersion.version_number)
        .where(ReportDocumentVersion.report_document_id == document_id)
        .order_by(ReportDocumentVersion.version_number.desc())
    ).scalars().first()
    return (current or 0) + 1


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


def _store_new_version(
    db: Session,
    *,
    publication: ReportPublicationTransaction,
    document: ReportDocument,
    exam_id: str,
    kind: str,
    data: bytes,
    created_by_user_id: str,
    template_id: str | None = None,
    template_code_snapshot: str | None = None,
    template_version_snapshot: int | None = None,
    template_text_snapshot: str | None = None,
    page_number: int | None = None,
    placement: str | None = None,
) -> ReportDocumentVersion:
    """Calcula metadados dos mesmos bytes publicados e confirma a releitura."""

    from ..ids import new_uuid

    settings = get_settings()
    try:
        version_number = _next_version_number(db, document.id)
    except IntegrityError:
        raise ReportDomainError(
            409,
            "numero_versao_concorrente",
            "Outra composição criou uma versão ao mesmo tempo; repita a operação.",
        ) from None
    try:
        validated = validate_pdf_bytes(
            data,
            max_size_bytes=settings.reports_max_upload_bytes,
        )
    except InvalidPdfError as exc:
        raise ReportDomainError(422, exc.codigo, exc.mensagem) from None

    snapshot_values = (
        template_code_snapshot,
        template_version_snapshot,
        template_text_snapshot,
    )
    if template_id is None:
        if any(value is not None for value in snapshot_values):
            raise ReportDomainError(
                409,
                "snapshot_template_incoerente",
                "Metadados do template estão incoerentes.",
            )
        template_text_sha256 = None
    else:
        if (
            template_code_snapshot is None
            or template_version_snapshot is None
            or template_version_snapshot < 1
            or template_text_snapshot is None
        ):
            raise ReportDomainError(
                409,
                "snapshot_template_incompleto",
                "Evidência imutável do template está incompleta.",
            )
        template_text_sha256 = _snapshot_hash(template_text_snapshot)

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
        # Confirma que os bytes efetivamente publicados são exatamente os que
        # produziram os metadados que entrarão no banco.
        read_and_validate_stored_pdf(
            path,
            root=storage_root,
            expected_sha256=validated.sha256,
            expected_size_bytes=validated.size_bytes,
            expected_page_count=validated.page_count,
            max_size_bytes=settings.reports_max_upload_bytes,
        )
    except StoredPdfIntegrityError as exc:
        raise ReportDomainError(503, exc.codigo, "Falha ao confirmar o PDF armazenado.") from None
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


async def _read_upload_bounded(file: UploadFile, *, max_size_bytes: int) -> bytes:
    """Lê no máximo ``max_size + 1``; o validador nunca recebe o excedente."""

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


# --------------------------------------------------------------- documentos


@router.post("", status_code=201)
async def upload_report_document(
    request: Request,
    exam_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Cria rascunho sem persistir nem devolver o filename fornecido."""

    exam = _get_exam_or_404(db, exam_id)
    settings = get_settings()
    raw = await _read_upload_bounded(
        file,
        max_size_bytes=settings.reports_max_upload_bytes,
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
            spirometry_exam_id=exam.id,
            status=STATUS_RASCUNHO,
            created_by_user_id=user.id,
        )
        db.add(document)
        db.flush()

        version = _store_new_version(
            db,
            publication=publication,
            document=document,
            exam_id=exam.id,
            kind=KIND_ORIGINAL,
            data=raw,
            created_by_user_id=user.id,
        )
        document.current_version_id = version.id
        audit(
            db,
            "laudo_original_enviado",
            entidade="report_documents",
            entidade_id=document.id,
            user_id=user.id,
            request_id=getattr(request.state, "request_id", None),
            detalhes={
                "public_code": exam.public_code,
                "status": document.status,
                "sha256": version.sha256,
            },
        )
        publication.commit()
    db.refresh(document)
    return ser_report_document(document, versions=[version])


@router.get("")
def list_report_documents(
    exam_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(ReportDocument)
    if exam_id:
        stmt = stmt.where(ReportDocument.spirometry_exam_id == exam_id)
    if status:
        stmt = stmt.where(ReportDocument.status == status)
    docs = db.execute(
        stmt.order_by(ReportDocument.created_at.desc()).limit(200)
    ).scalars().all()
    return [ser_report_document(document) for document in docs]


# Rotas estáticas de template precisam preceder "/{document_id}".


@router.get("/templates")
def list_report_templates(
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    templates = db.execute(
        select(ReportTemplate).order_by(ReportTemplate.codigo)
    ).scalars().all()
    return [ser_report_template(template) for template in templates]


@router.post("/templates", status_code=201)
def create_report_template(
    payload: ReportTemplateCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN)),
):
    existing = db.execute(
        select(ReportTemplate).where(ReportTemplate.codigo == payload.codigo)
    ).scalar_one_or_none()
    if existing is not None:
        raise ReportDomainError(
            409,
            "template_codigo_duplicado",
            "Já existe um template com este código.",
        )
    template = ReportTemplate(
        codigo=payload.codigo,
        titulo=payload.titulo,
        texto_tooltip=payload.texto_tooltip,
        texto_completo=payload.texto_completo,
        ativo=payload.ativo,
        versao=1,
        criado_por=user.id,
    )
    db.add(template)
    audit(
        db,
        "template_laudo_criado",
        entidade="report_templates",
        entidade_id=template.id,
        user_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        detalhes={
            "codigo": template.codigo,
            "status": "ativo" if template.ativo else "inativo",
        },
    )
    db.commit()
    db.refresh(template)
    return ser_report_template(template)


@router.patch("/templates/{template_id}")
def update_report_template(
    template_id: str,
    payload: ReportTemplateUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN)),
):
    template = db.get(ReportTemplate, template_id)
    if template is None:
        raise ReportDomainError(
            404,
            "template_nao_encontrado",
            "Template não encontrado.",
        )
    updates = payload.model_dump(exclude_unset=True)
    texto_mudou = (
        "texto_completo" in updates
        and updates["texto_completo"] != template.texto_completo
    )
    for field, value in updates.items():
        setattr(template, field, value)
    if texto_mudou:
        template.versao += 1

    audit(
        db,
        "template_laudo_atualizado",
        entidade="report_templates",
        entidade_id=template.id,
        user_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        detalhes={
            "codigo": template.codigo,
            "status": "ativo" if template.ativo else "inativo",
        },
    )
    db.commit()
    db.refresh(template)
    return ser_report_template(template)


@router.get("/{document_id}")
def get_report_document(
    document_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    document = _get_document_or_404(db, document_id)
    versions = db.execute(
        select(ReportDocumentVersion)
        .where(ReportDocumentVersion.report_document_id == document.id)
        .order_by(ReportDocumentVersion.version_number)
    ).scalars().all()
    return ser_report_document(document, versions=versions)


@router.get("/{document_id}/versoes/{version_id}/conteudo")
def download_report_version(
    document_id: str,
    version_id: str,
    request: Request,
    modo: str = Query(default="inline", pattern="^(inline|download)$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Entrega somente depois de validar integridade e gravar o audit event."""

    document = _get_document_or_404(db, document_id)
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
        if version.kind in {KIND_ORIGINAL, KIND_RASCUNHO, KIND_FINALIZADO}
        else "documento"
    )
    safe_name = f"laudo-{exam_code}-v{version.version_number}-{safe_kind}.pdf"
    disposition = "attachment" if modo == "download" else "inline"

    audit(
        db,
        "laudo_conteudo_entregue",
        entidade="report_documents",
        entidade_id=document.id,
        user_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        detalhes={
            "report_version_id": version.id,
            "delivery_mode": modo,
            "institutional_status": document.status,
        },
    )
    # Se a trilha append-only não puder ser confirmada, a entrega não ocorre.
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


@router.post("/{document_id}/compor")
def compose_report_document(
    document_id: str,
    payload: ReportDocumentCompose,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    # O lock serializa a leitura do próximo número de versão no PostgreSQL.
    document = _lock_document_or_404(db, document_id)
    if document.status != STATUS_RASCUNHO:
        raise ReportDomainError(
            409,
            "laudo_nao_esta_em_rascunho",
            "Só é possível compor um laudo em rascunho.",
        )
    template = db.get(ReportTemplate, payload.template_id)
    if template is None or not template.ativo:
        raise ReportDomainError(
            404,
            "template_nao_encontrado",
            "Template não encontrado ou inativo.",
        )
    original_version = _version_by_kind(db, document.id, KIND_ORIGINAL)
    if original_version is None:
        raise ReportDomainError(
            409,
            "pdf_original_ausente",
            "Laudo sem PDF original.",
        )

    original = _read_stored_version(original_version)
    # Snapshot local antes da composição: uma edição posterior do registro
    # ReportTemplate não altera estes valores.
    snapshot_code = template.codigo
    snapshot_version = template.versao
    snapshot_text = template.texto_completo
    settings = get_settings()
    try:
        composed = compose_report_pdf(
            original_bytes=original.data,
            page_number=payload.page_number,
            placement=payload.placement,
            interpretation_text=snapshot_text or None,
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
            created_by_user_id=user.id,
            template_id=template.id,
            template_code_snapshot=snapshot_code,
            template_version_snapshot=snapshot_version,
            template_text_snapshot=snapshot_text,
            page_number=payload.page_number,
            placement=payload.placement,
        )
        document.current_version_id = version.id
        audit(
            db,
            "laudo_rascunho_composto",
            entidade="report_documents",
            entidade_id=document.id,
            user_id=user.id,
            request_id=getattr(request.state, "request_id", None),
            detalhes={
                "status": document.status,
                "sha256": version.sha256,
                "codigo": snapshot_code,
            },
        )
        publication.commit()
    db.refresh(document)
    return ser_report_document(document, versions=[version])


@router.post("/{document_id}/revisao")
def submit_for_review(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    document = _lock_document_or_404(db, document_id)
    if document.status != STATUS_RASCUNHO:
        raise ReportDomainError(
            409,
            "laudo_nao_esta_em_rascunho",
            "Só um laudo em rascunho pode ser submetido para revisão.",
        )
    draft_version = _version_by_kind(db, document.id, KIND_RASCUNHO)
    if draft_version is None:
        raise ReportDomainError(
            409,
            "rascunho_composto_ausente",
            "Componha um rascunho antes de submeter para revisão.",
        )
    document.status = STATUS_EM_REVISAO
    document.submitted_for_review_at = datetime.now(timezone.utc)
    audit(
        db,
        "laudo_submetido_revisao",
        entidade="report_documents",
        entidade_id=document.id,
        user_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        detalhes={"status": document.status},
    )
    db.commit()
    db.refresh(document)
    return ser_report_document(document)


@router.post("/{document_id}/devolver-para-ajuste")
def return_report_for_adjustment(
    document_id: str,
    payload: ReportReviewAdjustment,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    """Transição auditada gestor-only; motivo fechado não comporta PII."""

    document = _lock_document_or_404(db, document_id)
    if document.status != STATUS_EM_REVISAO:
        raise ReportDomainError(
            409,
            "laudo_nao_esta_em_revisao",
            "Só um laudo em revisão pode voltar para ajuste.",
        )
    document.status = STATUS_RASCUNHO
    document.reviewer_user_id = user.id
    audit(
        db,
        "laudo_devolvido_para_ajuste",
        entidade="report_documents",
        entidade_id=document.id,
        user_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        detalhes={
            "status": document.status,
            "reason_code": payload.reason_code,
        },
    )
    db.commit()
    db.refresh(document)
    return ser_report_document(document)


@router.post("/{document_id}/finalizar")
def finalize_report_document(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    """Finaliza bytes revalidados; versão anterior e snapshot não são mutados."""

    document = _lock_document_or_404(db, document_id)
    if document.status != STATUS_EM_REVISAO:
        raise ReportDomainError(
            409,
            "laudo_nao_esta_em_revisao",
            "Só um laudo em revisão pode ser finalizado.",
        )
    draft_version = _version_by_kind(db, document.id, KIND_RASCUNHO)
    if draft_version is None:
        raise ReportDomainError(
            409,
            "rascunho_composto_ausente",
            "Laudo em revisão sem rascunho composto.",
        )
    draft = _read_stored_version(draft_version)
    if (
        draft_version.template_id is None
        or draft_version.template_code_snapshot is None
        or draft_version.template_version_snapshot is None
        or draft_version.template_text_snapshot is None
        or draft_version.template_text_sha256
        != _snapshot_hash(draft_version.template_text_snapshot)
    ):
        raise ReportDomainError(
            409,
            "snapshot_template_invalido",
            "Evidência imutável do template está ausente ou divergente.",
        )

    with report_publication_transaction(db) as publication:
        final_version = _store_new_version(
            db,
            publication=publication,
            document=document,
            exam_id=document.spirometry_exam_id,
            kind=KIND_FINALIZADO,
            data=draft.data,
            created_by_user_id=user.id,
            template_id=draft_version.template_id,
            template_code_snapshot=draft_version.template_code_snapshot,
            template_version_snapshot=draft_version.template_version_snapshot,
            template_text_snapshot=draft_version.template_text_snapshot,
            page_number=draft_version.page_number,
            placement=draft_version.placement,
        )
        now = datetime.now(timezone.utc)
        document.status = STATUS_FINALIZADO
        document.signature_status = SIGNATURE_STATUS_PENDENTE
        document.current_version_id = final_version.id
        document.reviewer_user_id = document.reviewer_user_id or user.id
        document.finalized_by_user_id = user.id
        document.finalized_at = now
        db.add(
            ReportSignature(
                report_document_version_id=final_version.id,
                status=SIGNATURE_STATUS_PENDENTE,
            )
        )
        audit(
            db,
            "laudo_finalizado",
            entidade="report_documents",
            entidade_id=document.id,
            user_id=user.id,
            request_id=getattr(request.state, "request_id", None),
            detalhes={"status": document.status, "sha256": final_version.sha256},
        )
        publication.commit()
    db.refresh(document)
    return ser_report_document(document, versions=[final_version])


@router.post("/{document_id}/nova-versao-corretiva", status_code=201)
def open_corrective_version(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Cria sucessor único, após revalidar o original e recalcular metadados."""

    old_document = _lock_document_or_404(db, document_id)
    if old_document.status != STATUS_FINALIZADO:
        raise ReportDomainError(
            409,
            "laudo_nao_finalizado",
            "Só um laudo finalizado pode gerar uma versão corretiva.",
        )
    if old_document.superseded_by_id:
        raise ReportDomainError(
            409,
            "laudo_ja_possui_corretiva",
            "Este laudo já foi substituído por uma versão corretiva.",
        )
    original_version = _version_by_kind(db, old_document.id, KIND_ORIGINAL)
    if original_version is None:
        raise ReportDomainError(
            409,
            "pdf_original_ausente",
            "Laudo original ausente; não é possível corrigir.",
        )
    original = _read_stored_version(original_version)

    try:
        with report_publication_transaction(db) as publication:
            new_document = ReportDocument(
                spirometry_exam_id=old_document.spirometry_exam_id,
                status=STATUS_RASCUNHO,
                corrects_document_id=old_document.id,
                created_by_user_id=user.id,
            )
            db.add(new_document)
            db.flush()
            new_version = _store_new_version(
                db,
                publication=publication,
                document=new_document,
                exam_id=new_document.spirometry_exam_id,
                kind=KIND_ORIGINAL,
                data=original.data,
                created_by_user_id=user.id,
            )
            new_document.current_version_id = new_version.id
            old_document.superseded_by_id = new_document.id
            audit(
                db,
                "laudo_versao_corretiva_aberta",
                entidade="report_documents",
                entidade_id=new_document.id,
                user_id=user.id,
                request_id=getattr(request.state, "request_id", None),
                detalhes={"status": new_document.status},
            )
            publication.commit()
    except IntegrityError:
        raise ReportDomainError(
            409,
            "laudo_ja_possui_corretiva",
            "Este laudo já possui uma versão corretiva.",
        ) from None
    db.refresh(new_document)
    return ser_report_document(new_document, versions=[new_version])


@router.get("/{document_id}/assinatura")
def get_report_signature_status(
    document_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    document = _get_document_or_404(db, document_id)
    if document.status != STATUS_FINALIZADO:
        return {
            "status": None,
            "mensagem": "Laudo ainda não finalizado; assinatura não se aplica.",
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
            "mensagem": "Assinatura digital pendente.",
        }
    return ser_report_signature(signature)
