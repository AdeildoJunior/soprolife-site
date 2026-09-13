"""Administrative fiscal API. Independent flag, existing authentication/CSRF/RBAC."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..fiscal_schemas import PolicyCreate, PrepareRequest, OperationRequest, BatchRequest
from ..models import FiscalPolicy, FiscalDocument, FiscalAttempt, FiscalPreparation, User
from ..pagination import PageParams, paginate
from ..security import ROLE_ADMIN, ROLE_GESTOR, ROLE_LEITURA, require_role
from ..services import nfse
from ..services.idempotency import payload_fingerprint


def enabled():
    settings = get_settings()
    if not settings.nfse_enabled:
        nfse.fail('nfse_disabled', 503)
    return settings


router = APIRouter(prefix='/fiscal', tags=['fiscal'], dependencies=[Depends(enabled)])


def ser_policy(policy):
    return {k: getattr(policy, k) for k in ('id', 'version', 'environment', 'flow',
        'service', 'effective_from', 'effective_to', 'validation_state', 'configuration',
        'created_by', 'created_at')}


def _restricted_readiness(settings) -> dict:
    """Read-only diagnostic — never a control. There is no endpoint anywhere
    that flips `nfse_restricted_network_enabled`; it is an environment
    variable only, and this block never exposes a way to change it."""
    missing = []
    if not settings.nfse_restricted_base_url:
        missing.append('restricted_base_url')
    if not settings.nfse_restricted_certificate_path:
        missing.append('restricted_certificate_path')
    if not settings.nfse_restricted_certificate_password:
        missing.append('restricted_certificate_password')
    if not settings.nfse_restricted_network_enabled:
        missing.append('restricted_network_gate_disabled')
    return {
        'layout_version': settings.nfse_restricted_layout_version,
        'network_gate_enabled': settings.nfse_restricted_network_enabled,
        'missing_configuration': missing,
        'operational_network_possible': False,  # always: see RestrictedNfseProvider docstring
    }


@router.get('/status')
def status(settings=Depends(enabled), user: User = Depends(require_role(ROLE_LEITURA))):
    return {'enabled': settings.nfse_enabled, 'environment': settings.nfse_environment,
            'provider': 'mock' if settings.nfse_environment == 'mock' else 'unavailable',
            'real_issuance_available': False, 'supported_flows': ['DIRECT', 'HOME'],
            'restricted_provider_foundation': _restricted_readiness(settings)}


@router.get('/politicas')
def policies(params: PageParams = Depends(), db: Session = Depends(get_db),
             user: User = Depends(require_role(ROLE_GESTOR))):
    return paginate(db, select(FiscalPolicy).order_by(FiscalPolicy.created_at.desc()), params, ser_policy)


@router.post('/politicas', status_code=201)
def new_policy(payload: PolicyCreate, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_role(ROLE_ADMIN))):
    try:
        return ser_policy(nfse.create_policy(db, payload, user.id, request.state.request_id))
    except IntegrityError:
        db.rollback()
        nfse.fail('policy_version_conflict')


@router.get('/documentos')
def documents(state: str | None = None, params: PageParams = Depends(),
              db: Session = Depends(get_db), user: User = Depends(require_role(ROLE_LEITURA))):
    stmt = select(FiscalDocument).order_by(FiscalDocument.created_at.desc())
    if state:
        stmt = stmt.where(FiscalDocument.state == state)
    return paginate(db, stmt, params, lambda doc: nfse.serialize_document(db, doc))


@router.post('/preparar')
def prepare(payload: PrepareRequest, request: Request, db: Session = Depends(get_db),
            settings=Depends(enabled), user: User = Depends(require_role(ROLE_GESTOR))):
    return nfse.serialize_document(db, nfse.prepare(db, payload.spirometry_exam_id,
                                  settings, user.id, request.state.request_id))


@router.post('/emitir-pendentes')
def batch(payload: BatchRequest, request: Request, db: Session = Depends(get_db),
          settings=Depends(enabled), user: User = Depends(require_role(ROLE_GESTOR))):
    # Explicit bounded selection; each document is an independent transaction.
    results = []
    for document_id in dict.fromkeys(payload.document_ids):
        key = payload_fingerprint({'batch': payload.idempotency_key, 'document': document_id})
        try:
            doc = nfse.operate(db, document_id, 'issue', key, settings, user.id, request.state.request_id)
            results.append(nfse.serialize_document(db, doc))
        except HTTPException as exc:
            db.rollback()
            results.append({'id': document_id, 'error': exc.detail, 'status_code': exc.status_code})
    return {'itens': results, 'real_issuance_available': False}


@router.get('/documentos/{document_id}')
def inspect_document(document_id: str, db: Session = Depends(get_db),
                     user: User = Depends(require_role(ROLE_LEITURA))):
    return nfse.serialize_document(db, nfse.get_document(db, document_id))


@router.get('/documentos/{document_id}/preparacoes')
def preparations(document_id: str, params: PageParams = Depends(), db: Session = Depends(get_db),
                 user: User = Depends(require_role(ROLE_LEITURA))):
    nfse.get_document(db, document_id)
    def serialize(p):
        data = {c.name: getattr(p, c.name) for c in FiscalPreparation.__table__.columns}
        data['amount_snapshot'] = str(p.amount_snapshot) if p.amount_snapshot is not None else None
        return data
    return paginate(db, select(FiscalPreparation).where(FiscalPreparation.document_id == document_id)
                    .order_by(FiscalPreparation.created_at.desc()), params, serialize)


@router.get('/documentos/{document_id}/tentativas')
def attempts(document_id: str, params: PageParams = Depends(), db: Session = Depends(get_db),
             user: User = Depends(require_role(ROLE_LEITURA))):
    nfse.get_document(db, document_id)
    return paginate(db, select(FiscalAttempt).where(FiscalAttempt.document_id == document_id)
                    .order_by(FiscalAttempt.number, FiscalAttempt.phase.desc()), params,
                    lambda a: {c.name: getattr(a, c.name) for c in FiscalAttempt.__table__.columns
                               if c.name not in {'idempotency_key', 'idempotency_fingerprint'}})


def _operation(action, reprocess=False):
    def endpoint(document_id: str, payload: OperationRequest, request: Request,
                 db: Session = Depends(get_db), settings=Depends(enabled),
                 user: User = Depends(require_role(ROLE_GESTOR))):
        return nfse.serialize_document(db, nfse.operate(
            db, document_id, action, payload.idempotency_key, settings, user.id,
            request.state.request_id, reprocess=reprocess))
    return endpoint


for path, operation, retry in [('emitir-mock', 'issue', False),
                               ('reprocessar', 'issue', True),
                               ('reconciliar', 'reconcile', False),
                               ('cancelar-mock', 'cancel', False)]:
    router.add_api_route('/documentos/{document_id}/' + path, _operation(operation, retry),
                         methods=['POST'], name='fiscal_' + path)
