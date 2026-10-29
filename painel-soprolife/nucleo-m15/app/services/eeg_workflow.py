"""Guardas do fluxo EEG e projeção operacional sem conteúdo clínico."""
from sqlalchemy import select
from ..models import EegCase, EegProfessional, EegCaseRevision, EegExam
from ..security import user_effective_roles
from ..errors import ReportDomainError


def case_for(db, exam_id, *, create=False):
    case = db.scalar(select(EegCase).where(EegCase.exam_id == exam_id).with_for_update())
    if case is None and create:
        case = EegCase(exam_id=exam_id)
        db.add(case); db.flush()
    return case


def snapshot(p):
    return {'id': p.id, 'nome': p.nome, 'crm': p.crm, 'uf': p.uf, 'rqe': p.rqe, 'servico': p.servico, 'tipo': p.tipo}


def can_read_exam(db, user, exam_id):
    roles = user_effective_roles(user)
    if roles & {'admin','eeg_operador','eeg_tecnico'}: return True
    if 'medical_eeg' not in roles: return False
    return bool(db.scalar(select(EegCase.id).join(EegProfessional, EegProfessional.id == EegCase.professional_id).where(
        EegCase.exam_id == exam_id, EegProfessional.user_id == user.id, EegProfessional.ativo.is_(True))))


def require_exam(db, user, exam_id):
    if not can_read_exam(db,user,exam_id):
        raise ReportDomainError(403,'eeg_acesso_negado','Exame fora da sua atribuição EEG.')


def require_author(db, user, case):
    if 'medical_eeg' not in user_effective_roles(user):
        raise ReportDomainError(403,'eeg_autoria_negada','Autoria exige papel médico EEG explícito.')
    professional = db.get(EegProfessional, case.professional_id) if case.professional_id else None
    if not professional or not professional.ativo or professional.user_id != user.id:
        raise ReportDomainError(403,'eeg_atribuicao_ausente','Exame não atribuído a este médico EEG.')


def serialize(case):
    if not case: return {'status':'aguardando_arquivo'}
    return {'id':case.id,'status':case.status, 'arquivo_id':case.current_file_id,
        'medico':case.professional_snapshot, 'atribuido_em':case.atribuido_em,
        'conferido_em':case.conferido_em, 'concluido_em':case.concluido_em,
        'assinado_em':case.assinado_em, 'entregue_em':case.entregue_em,
        'reteste_sem_cobranca':case.reteste_sem_cobranca,
        'final_document_id':case.final_document_id,'assinado_document_id':case.assinado_document_id}
