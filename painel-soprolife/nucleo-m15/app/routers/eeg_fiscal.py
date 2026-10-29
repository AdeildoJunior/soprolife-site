"""Interface EEG do motor fiscal único. Todas as rotas exigem admin."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import FiscalDocument, EegExam, User
from ..pagination import PageParams, paginate
from ..security import require_eeg_access
from .fiscal import enabled
from ..services import nfse, nfse_eeg, nfse_production_issuance as production

router = APIRouter(prefix='/eeg/fiscal', tags=['eeg-fiscal'], dependencies=[Depends(require_eeg_access), Depends(enabled)])


def summary(db, doc):
    result = nfse.serialize_document(db, doc)
    result['marca'] = 'eeglife'
    result['eeg_exam_id'] = doc.eeg_exam_id
    result['confirmacao'] = production.confirmation_summary(db, doc)
    latest_request = production._latest_request(db, doc.id)
    result['pedido'] = production.get_request(db, latest_request.id) if latest_request else None
    if latest_request and latest_request.status not in {"refused", "expired"}:
        result['confirmacao']['can_confirm'] = False
        result['confirmacao']['confirmation_phrase'] = None
    return result


@router.get('')
def queue(db: Session = Depends(get_db), settings=Depends(enabled),
          params: PageParams = Depends(), user: User = Depends(require_eeg_access)):
    stmt = select(FiscalDocument).where(FiscalDocument.kind == 'eeg',
        FiscalDocument.environment == settings.nfse_environment).order_by(FiscalDocument.created_at.desc())
    return {**paginate(db, stmt, params, lambda doc: summary(db, doc)),
            'environment': settings.nfse_environment,
            'issuer': {'cnpj': nfse_eeg.ISSUER_CNPJ, 'name': nfse_eeg.ISSUER_NAME},
            'worker_configured': settings.nfse_production_worker_spool_dir is not None}


@router.post('/exames/{exam_id}/preparar')
def prepare(exam_id: str, request: Request, db: Session = Depends(get_db),
            settings=Depends(enabled), user: User = Depends(require_eeg_access)):
    # Documentos já enviados ou pedidos ativos são consultados: nunca se
    # reprepara algo que o usuário já confirmou para o worker.
    existing = db.scalar(select(FiscalDocument).where(FiscalDocument.eeg_exam_id == exam_id,
        FiscalDocument.environment == settings.nfse_environment))
    if existing and (existing.state not in {'pending', 'blocked'} or production._active_request(db, existing.id)):
        return summary(db, existing)
    # Prepara sem rede, número DPS, laudo ou transmissão.
    return summary(db, nfse_eeg.prepare(db, exam_id, settings, user.id, request.state.request_id))
