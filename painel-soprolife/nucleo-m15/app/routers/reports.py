"""Laudos PDF (M24A) — recebimento, revisão, composição e finalização.

Fluxo: rascunho -> em_revisao -> finalizado (imutável). Uma correção depois
de finalizado nasce como um NOVO ReportDocument (nunca muta o antigo).
Assinatura digital ICP-Brasil real NÃO está conectada nesta etapa — todo
laudo finalizado entra em signature_status="assinatura_pendente" e
permanece assim até um provedor real existir (app/services/
signature_provider.py). Nunca se declara "assinado digitalmente" sem uma
assinatura real.

RBAC (papéis já existentes do projeto — nenhum papel novo foi inventado):
- leitura: ver metadados, listar, baixar/visualizar PDF.
- operacional: enviar original, compor rascunho, submeter para revisão,
  abrir versão corretiva.
- gestor: finalizar (trava clínica). Não existe papel "médico" no projeto;
  gestor é o papel privilegiado equivalente já usado para outras ações de
  alto risco e irreversíveis (fechamentos financeiros, aprovação de
  importação) — ver decisão registrada no relatório do M24A.
- admin: administrar o registro de templates (abreviação + texto).
"""

import re
import unicodedata
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..models import (
    ReportDocument,
    ReportDocumentVersion,
    ReportSignature,
    ReportTemplate,
    SpirometryExam,
    User,
)
from ..schemas import ReportDocumentCompose, ReportTemplateCreate, ReportTemplateUpdate
from ..security import ROLE_ADMIN, ROLE_GESTOR, ROLE_LEITURA, ROLE_OPERACIONAL, require_role
from ..serializers import ser_report_document, ser_report_signature, ser_report_template
from ..services.pdf_validation import InvalidPdfError, validate_pdf_bytes
from ..services.report_pdf import PdfCompositionError, compose_report_pdf
from ..services.report_storage import (
    atomic_write_new_file,
    read_stored_pdf,
    version_storage_path,
)

router = APIRouter(prefix="/laudos", tags=["laudos"])

KIND_ORIGINAL = "original"
KIND_RASCUNHO = "rascunho"
KIND_FINALIZADO = "finalizado"

STATUS_RASCUNHO = "rascunho"
STATUS_EM_REVISAO = "em_revisao"
STATUS_FINALIZADO = "finalizado"

SIGNATURE_STATUS_PENDENTE = "assinatura_pendente"

_DISPLAY_NAME_STRIP_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize_display_filename(raw: str | None) -> str | None:
    """Nome de arquivo original SÓ para exibição — nunca usado no caminho de
    armazenamento (esse usa só UUIDs internos, ver report_storage.py).

    Mesmo sendo só decorativo, nunca preserva componente de diretório
    (`../`, `/etc/passwd`) nem caractere de controle — defesa em
    profundidade contra qualquer futuro uso indevido deste campo, e para
    não confundir quem olha a tela achando que é um caminho real.
    """
    if not raw:
        return None
    cleaned = unicodedata.normalize("NFKC", raw)
    # remove qualquer prefixo de diretório (POSIX e Windows) — só o nome
    # final do "arquivo enviado" sobrevive, nunca um caminho.
    cleaned = cleaned.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _DISPLAY_NAME_STRIP_RE.sub("", cleaned).strip()
    cleaned = cleaned.lstrip(".")
    if not cleaned:
        return None
    return cleaned[:180]


def _storage_root(settings):
    """Fail-closed explícito na borda da API: configuração de armazenamento
    ausente/insegura nunca vira um 500 genérico — vira um 503 claro, sem
    ecoar o caminho configurado na resposta."""
    try:
        return settings.resolved_reports_storage_dir()
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Armazenamento de laudos indisponível (configuração ausente ou inválida).",
        ) from exc


def _get_exam_or_404(db: Session, exam_id: str) -> SpirometryExam:
    exam = db.get(SpirometryExam, exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail="Exame de espirometria não encontrado.")
    return exam


def _get_document_or_404(db: Session, document_id: str) -> ReportDocument:
    doc = db.get(ReportDocument, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Laudo não encontrado.")
    return doc


def _get_version_or_404(db: Session, document_id: str, version_id: str) -> ReportDocumentVersion:
    version = db.execute(
        select(ReportDocumentVersion).where(
            ReportDocumentVersion.id == version_id,
            ReportDocumentVersion.report_document_id == document_id,
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Versão do laudo não encontrada.")
    return version


def _version_by_kind(db: Session, document_id: str, kind: str) -> ReportDocumentVersion | None:
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


def _store_new_version(
    db: Session,
    *,
    document: ReportDocument,
    exam_id: str,
    kind: str,
    data: bytes,
    sha256: str,
    size_bytes: int,
    page_count: int,
    created_by_user_id: str,
    template_id: str | None = None,
    page_number: int | None = None,
    placement: str | None = None,
) -> ReportDocumentVersion:
    from ..ids import new_uuid

    settings = get_settings()
    storage_root = _storage_root(settings)
    version_id = new_uuid()
    path = version_storage_path(
        storage_root, exam_id=exam_id, document_id=document.id, version_id=version_id
    )
    atomic_write_new_file(path, data)

    version = ReportDocumentVersion(
        id=version_id,
        report_document_id=document.id,
        kind=kind,
        version_number=_next_version_number(db, document.id),
        storage_path=str(path.relative_to(storage_root)),
        sha256=sha256,
        size_bytes=size_bytes,
        page_count=page_count,
        template_id=template_id,
        page_number=page_number,
        placement=placement,
        created_by_user_id=created_by_user_id,
    )
    db.add(version)
    db.flush()
    return version


# --------------------------------------------------------------- documentos

@router.post("", status_code=201)
def upload_report_document(
    request: Request,
    exam_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Cria um novo laudo (rascunho) a partir do PDF original enviado."""
    exam = _get_exam_or_404(db, exam_id)
    settings = get_settings()

    raw = file.file.read()
    try:
        validated = validate_pdf_bytes(
            raw,
            max_size_bytes=settings.reports_max_upload_bytes,
            declared_content_type=file.content_type,
        )
    except InvalidPdfError as exc:
        raise HTTPException(status_code=422, detail={"codigo": exc.codigo, "mensagem": exc.mensagem})

    document = ReportDocument(
        spirometry_exam_id=exam.id,
        status=STATUS_RASCUNHO,
        original_filename_display=_sanitize_display_filename(file.filename),
        created_by_user_id=user.id,
    )
    db.add(document)
    db.flush()

    version = _store_new_version(
        db,
        document=document,
        exam_id=exam.id,
        kind=KIND_ORIGINAL,
        data=raw,
        sha256=validated.sha256,
        size_bytes=validated.size_bytes,
        page_count=validated.page_count,
        created_by_user_id=user.id,
    )
    document.current_version_id = version.id

    audit(
        db, "laudo_original_enviado", entidade="report_documents", entidade_id=document.id,
        user_id=user.id, request_id=getattr(request.state, "request_id", None),
        detalhes={"public_code": exam.public_code, "status": document.status, "sha256": version.sha256},
    )
    db.commit()
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
    stmt = stmt.order_by(ReportDocument.created_at.desc()).limit(200)
    docs = db.execute(stmt).scalars().all()
    return [ser_report_document(d) for d in docs]


# ---------------------------------------------------------------- templates
#
# Registradas ANTES de "/{document_id}" de propósito: rotas estáticas
# precisam vir antes das dinâmicas no Starlette, senão "/laudos/templates"
# seria interpretado como "/laudos/{document_id}" com document_id="templates".

@router.get("/templates")
def list_report_templates(
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    templates = db.execute(select(ReportTemplate).order_by(ReportTemplate.codigo)).scalars().all()
    return [ser_report_template(t) for t in templates]


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
        raise HTTPException(status_code=409, detail="Já existe um template com este código.")
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
        db, "template_laudo_criado", entidade="report_templates", entidade_id=template.id,
        user_id=user.id, request_id=getattr(request.state, "request_id", None),
        detalhes={"codigo": template.codigo, "status": "ativo" if template.ativo else "inativo"},
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
        raise HTTPException(status_code=404, detail="Template não encontrado.")
    updates = payload.model_dump(exclude_unset=True)
    texto_mudou = "texto_completo" in updates and updates["texto_completo"] != template.texto_completo
    for field, value in updates.items():
        setattr(template, field, value)
    if texto_mudou:
        template.versao += 1

    audit(
        db, "template_laudo_atualizado", entidade="report_templates", entidade_id=template.id,
        user_id=user.id, request_id=getattr(request.state, "request_id", None),
        detalhes={"codigo": template.codigo, "status": "ativo" if template.ativo else "inativo"},
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
    doc = _get_document_or_404(db, document_id)
    versions = db.execute(
        select(ReportDocumentVersion)
        .where(ReportDocumentVersion.report_document_id == doc.id)
        .order_by(ReportDocumentVersion.version_number)
    ).scalars().all()
    return ser_report_document(doc, versions=versions)


@router.get("/{document_id}/versoes/{version_id}/conteudo")
def download_report_version(
    document_id: str,
    version_id: str,
    modo: str = Query(default="inline", pattern="^(inline|download)$"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Entrega autenticada do PDF — nunca expõe caminho de sistema de
    arquivos; o nome de download é montado só a partir de IDs/códigos
    públicos, nunca do nome de arquivo original enviado pelo usuário."""
    doc = _get_document_or_404(db, document_id)
    version = _get_version_or_404(db, document_id, version_id)
    exam = db.get(SpirometryExam, doc.spirometry_exam_id)

    settings = get_settings()
    storage_root = _storage_root(settings)
    path = storage_root / version.storage_path
    try:
        data = read_stored_pdf(path, root=storage_root)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail="Arquivo do laudo não encontrado.") from exc

    codigo = exam.public_code if exam else doc.spirometry_exam_id
    safe_name = f"laudo-{codigo}-v{version.version_number}-{version.kind}.pdf"
    disposition = "attachment" if modo == "download" else "inline"
    return Response(
        content=data,
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
    doc = _get_document_or_404(db, document_id)
    if doc.status != STATUS_RASCUNHO:
        raise HTTPException(
            status_code=409,
            detail="Só é possível compor um laudo em rascunho.",
        )
    template = db.get(ReportTemplate, payload.template_id)
    if template is None or not template.ativo:
        raise HTTPException(status_code=404, detail="Template não encontrado ou inativo.")

    original_version = _version_by_kind(db, doc.id, KIND_ORIGINAL)
    if original_version is None:
        raise HTTPException(status_code=409, detail="Laudo sem PDF original.")

    settings = get_settings()
    storage_root = _storage_root(settings)
    original_bytes = read_stored_pdf(storage_root / original_version.storage_path, root=storage_root)

    try:
        composed = compose_report_pdf(
            original_bytes=original_bytes,
            page_number=payload.page_number,
            placement=payload.placement,
            interpretation_text=template.texto_completo or None,
            max_size_bytes=settings.reports_max_upload_bytes,
        )
    except PdfCompositionError as exc:
        raise HTTPException(status_code=422, detail={"codigo": exc.codigo, "mensagem": exc.mensagem})

    version = _store_new_version(
        db,
        document=doc,
        exam_id=doc.spirometry_exam_id,
        kind=KIND_RASCUNHO,
        data=composed.data,
        sha256=composed.validated.sha256,
        size_bytes=composed.validated.size_bytes,
        page_count=composed.validated.page_count,
        created_by_user_id=user.id,
        template_id=template.id,
        page_number=payload.page_number,
        placement=payload.placement,
    )
    doc.current_version_id = version.id

    audit(
        db, "laudo_rascunho_composto", entidade="report_documents", entidade_id=doc.id,
        user_id=user.id, request_id=getattr(request.state, "request_id", None),
        detalhes={"status": doc.status, "sha256": version.sha256, "codigo": template.codigo},
    )
    db.commit()
    db.refresh(doc)
    return ser_report_document(doc, versions=[version])


@router.post("/{document_id}/revisao")
def submit_for_review(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    doc = _get_document_or_404(db, document_id)
    if doc.status != STATUS_RASCUNHO:
        raise HTTPException(status_code=409, detail="Só um laudo em rascunho pode ser submetido para revisão.")
    draft_version = _version_by_kind(db, doc.id, KIND_RASCUNHO)
    if draft_version is None:
        raise HTTPException(
            status_code=409, detail="Componha um rascunho (com template/página/posição) antes de submeter."
        )
    doc.status = STATUS_EM_REVISAO
    doc.submitted_for_review_at = datetime.now(timezone.utc)

    audit(
        db, "laudo_submetido_revisao", entidade="report_documents", entidade_id=doc.id,
        user_id=user.id, request_id=getattr(request.state, "request_id", None),
        detalhes={"status": doc.status},
    )
    db.commit()
    db.refresh(doc)
    return ser_report_document(doc)


@router.post("/{document_id}/finalizar")
def finalize_report_document(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    """Trava clinicamente o laudo (imutável a partir daqui).

    Não existe assinatura digital real conectada nesta etapa — o laudo
    finalizado nasce com signature_status=assinatura_pendente
    (equivalente ao estado "signing-required") e um ReportSignature de
    fronteira é criado, sem nenhum provedor acionado.
    """
    doc = _get_document_or_404(db, document_id)
    if doc.status != STATUS_EM_REVISAO:
        raise HTTPException(status_code=409, detail="Só um laudo em revisão pode ser finalizado.")
    draft_version = _version_by_kind(db, doc.id, KIND_RASCUNHO)
    if draft_version is None:
        raise HTTPException(status_code=409, detail="Laudo em revisão sem rascunho composto.")

    settings = get_settings()
    storage_root = _storage_root(settings)
    draft_bytes = read_stored_pdf(storage_root / draft_version.storage_path, root=storage_root)
    # Reverificação estrutural completa antes de gravar a versão imutável —
    # nunca confia que os bytes gravados como rascunho continuam válidos.
    try:
        validate_pdf_bytes(draft_bytes, max_size_bytes=settings.reports_max_upload_bytes)
    except InvalidPdfError as exc:
        raise HTTPException(status_code=422, detail={"codigo": exc.codigo, "mensagem": exc.mensagem})

    final_version = _store_new_version(
        db,
        document=doc,
        exam_id=doc.spirometry_exam_id,
        kind=KIND_FINALIZADO,
        data=draft_bytes,
        sha256=draft_version.sha256,
        size_bytes=draft_version.size_bytes,
        page_count=draft_version.page_count,
        created_by_user_id=user.id,
        template_id=draft_version.template_id,
        page_number=draft_version.page_number,
        placement=draft_version.placement,
    )
    now = datetime.now(timezone.utc)
    doc.status = STATUS_FINALIZADO
    doc.signature_status = SIGNATURE_STATUS_PENDENTE
    doc.current_version_id = final_version.id
    doc.reviewer_user_id = doc.reviewer_user_id or user.id
    doc.finalized_by_user_id = user.id
    doc.finalized_at = now

    db.add(ReportSignature(
        report_document_version_id=final_version.id,
        status=SIGNATURE_STATUS_PENDENTE,
    ))

    audit(
        db, "laudo_finalizado", entidade="report_documents", entidade_id=doc.id,
        user_id=user.id, request_id=getattr(request.state, "request_id", None),
        detalhes={"status": doc.status, "sha256": final_version.sha256},
    )
    db.commit()
    db.refresh(doc)
    return ser_report_document(doc, versions=[final_version])


@router.post("/{document_id}/nova-versao-corretiva", status_code=201)
def open_corrective_version(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Correção de um laudo já finalizado: abre um NOVO ReportDocument.

    O documento finalizado antigo nunca é alterado — só ganha
    `superseded_by_id` apontando para o novo. O PDF original antigo é
    relido e regravado como um arquivo NOVO (novo UUID) no documento novo,
    nunca reaproveitando o caminho antigo.
    """
    old_doc = _get_document_or_404(db, document_id)
    if old_doc.status != STATUS_FINALIZADO:
        raise HTTPException(status_code=409, detail="Só um laudo finalizado pode gerar uma versão corretiva.")
    if old_doc.superseded_by_id:
        raise HTTPException(status_code=409, detail="Este laudo já foi substituído por uma versão corretiva.")

    original_version = _version_by_kind(db, old_doc.id, KIND_ORIGINAL)
    if original_version is None:
        raise HTTPException(status_code=409, detail="Laudo original ausente — não é possível corrigir.")

    settings = get_settings()
    storage_root = _storage_root(settings)
    original_bytes = read_stored_pdf(storage_root / original_version.storage_path, root=storage_root)

    new_doc = ReportDocument(
        spirometry_exam_id=old_doc.spirometry_exam_id,
        status=STATUS_RASCUNHO,
        original_filename_display=old_doc.original_filename_display,
        created_by_user_id=user.id,
    )
    db.add(new_doc)
    db.flush()

    new_version = _store_new_version(
        db,
        document=new_doc,
        exam_id=new_doc.spirometry_exam_id,
        kind=KIND_ORIGINAL,
        data=original_bytes,
        sha256=original_version.sha256,
        size_bytes=original_version.size_bytes,
        page_count=original_version.page_count,
        created_by_user_id=user.id,
    )
    new_doc.current_version_id = new_version.id
    old_doc.superseded_by_id = new_doc.id

    audit(
        db, "laudo_versao_corretiva_aberta", entidade="report_documents", entidade_id=new_doc.id,
        user_id=user.id, request_id=getattr(request.state, "request_id", None),
        detalhes={"status": new_doc.status},
    )
    db.commit()
    db.refresh(new_doc)
    return ser_report_document(new_doc, versions=[new_version])


@router.get("/{document_id}/assinatura")
def get_report_signature_status(
    document_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Só leitura — nunca aciona um provedor real (nenhum está configurado
    nesta etapa). Devolve o estado de fronteira de assinatura."""
    doc = _get_document_or_404(db, document_id)
    if doc.status != STATUS_FINALIZADO:
        return {"status": None, "mensagem": "Laudo ainda não finalizado — assinatura não se aplica."}
    signature = db.execute(
        select(ReportSignature).where(ReportSignature.report_document_version_id == doc.current_version_id)
    ).scalar_one_or_none()
    if signature is None:
        return {"status": SIGNATURE_STATUS_PENDENTE, "mensagem": "Assinatura digital pendente."}
    return ser_report_signature(signature)
