"""Portal do PDF final EEG. Só hashes de segredos e referências, sem traçado.

Reutiliza os controles de nascimento, expiração e sessão do portal corporativo;
as referências clínicas e as tabelas EEG continuam independentes.
"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..errors import ReportDomainError
from ..ids import new_uuid
from ..models import EegEnrollment, EegExam, EegReportDocument, EegResultAccess
from . import patient_results as prs


def public_url(acesso):
    from urllib.parse import urlsplit
    base = get_settings().portal_eeg_public_base_url
    parsed = urlsplit(base or "")
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
        raise ReportDomainError(503, "portal_eeg_nao_configurado", "Endereço público do portal EEG não configurado.")
    return base.rstrip("/") + "/#t=" + token(acesso)


def token(acesso):
    return "eeg_" + prs.derive_token("eeg:" + acesso.id, acesso.generation)


def valid_document(db: Session, acesso):
    # Narrow columns match the portal DB role. Revalidate ownership and release
    # for every read, including already authenticated sessions.
    row = db.execute(select(
        EegReportDocument.id, EegReportDocument.exam_id, EegReportDocument.versao,
        EegReportDocument.storage_path, EegReportDocument.sha256,
        EegReportDocument.size_bytes, EegReportDocument.page_count,
    ).join(EegExam, EegExam.id == EegReportDocument.exam_id)
      .join(EegEnrollment, EegEnrollment.id == EegExam.enrollment_id)
      .where(EegReportDocument.id == acesso.document_id,
             EegReportDocument.exam_id == acesso.exam_id,
             EegReportDocument.status == "vigente",
             EegExam.status == "laudo_disponivel",
             EegEnrollment.person_id == acesso.person_id,
             EegEnrollment.status == "ativo")).first()
    if not row:
        return None
    return prs.VersaoPortal(row.id, row.id, "laudo_final_eeg", row.versao,
                            row.storage_path, row.sha256, row.size_bytes, row.page_count)


def issue(db: Session, document_id: str, user_id: str, *, regenerate=False):
    settings = get_settings()
    if not settings.portal_enabled:
        raise ReportDomainError(503, "portal_desabilitado", "Portal de resultados desabilitado.")
    # Validate configuration before writing an access row.
    settings.resolved_portal_token_key()
    doc = db.get(EegReportDocument, document_id, with_for_update=True)
    if not doc:
        raise ReportDomainError(404, "laudo_ausente", "Laudo não encontrado.")
    exam = db.get(EegExam, doc.exam_id)
    enrollment = db.get(EegEnrollment, exam.enrollment_id)
    person = prs.load_patient(db, enrollment.person_id)
    if not person or person.arquivado or not person.data_nascimento:
        raise ReportDomainError(409, "cadastro_incompleto", "Informe a data de nascimento no cadastro corporativo antes de gerar o acesso.")
    access = db.execute(select(EegResultAccess).where(
        EegResultAccess.document_id == doc.id)).scalar_one_or_none()
    if access is None:
        access = EegResultAccess(id=new_uuid(), document_id=doc.id, exam_id=exam.id,
                                 person_id=person.id, generation=1,
                                 created_by_user_id=user_id)
        db.add(access)
    with db.no_autoflush:
        valid = valid_document(db, access)
    if not valid:
        raise ReportDomainError(409, "laudo_indisponivel", "Disponibilize o PDF final antes de gerar o acesso.")
    if access.token_sha256 and not regenerate:
        if access.status == "revogado" or prs.is_expired(access):
            raise ReportDomainError(409, "acesso_inativo", "Gere um novo link para renovar o acesso.")
    else:
        if access.token_sha256:
            prs.revoke_sessions(db, access)
            access.generation += 1
        access.token_sha256 = prs.token_digest(token(access))
        access.status = "disponivel"
        access.revoked_at = access.revoked_motivo = access.revoked_by_user_id = None
        access.failed_attempts = 0
        access.locked_until = None
        access.expires_at = prs.agora() + timedelta(days=settings.portal_access_ttl_days)
        audit(db, "eeg.resultado_acesso_gerado", "eeg_result_accesses", access.id,
              user_id=user_id, detalhes={"version": access.generation, "status": access.status})
    db.flush()
    return access
