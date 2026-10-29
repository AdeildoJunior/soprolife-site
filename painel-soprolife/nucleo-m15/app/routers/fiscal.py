"""Administrative fiscal API. Independent flag, existing authentication/CSRF/RBAC."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..fiscal_schemas import (PolicyCreate, PrepareRequest, OperationRequest, BatchRequest,
                              GroupedPrepareRequest, GroupedRecipientChange,
                              ProductionIssueConfirmation)
from ..models import FiscalPolicy, FiscalDocument, FiscalAttempt, FiscalPreparation, User
from ..pagination import PageParams, paginate
from ..security import ROLE_ADMIN, ROLE_GESTOR, ROLE_LEITURA, require_role, user_has_explicit_role
from ..services import nfse
from ..services import nfse_grouped as grouped
from ..services import nfse_production_issuance as production
from ..services.idempotency import payload_fingerprint
from ..services.nfse_national import fiscal_config
from ..services.nfse_national.config import NationalDpsConfigurationVersionCreate
from ..services.nfse_national.preflight import run_offline_preflight
from ..services.nfse_national.production_gates import compute_production_readiness
from ..services.nfse_national.readiness import compute_provider_readiness


def enabled():
    settings = get_settings()
    if not settings.nfse_enabled:
        nfse.fail('nfse_disabled', 503)
    return settings


router = APIRouter(prefix='/fiscal', tags=['fiscal'], dependencies=[Depends(enabled)])


def _document_access(db, document_id, user):
    doc = nfse.get_document(db, document_id)
    if doc.kind == 'eeg' and not user_has_explicit_role(user, ROLE_ADMIN):
        nfse.fail('eeg_access_denied', 403)
    return doc


def _refuse_in_production(settings):
    """M66 — in production the web process never calls a provider: not in a
    batch, not as a mock/reprocess/reconcile/cancel shortcut. The only way to
    SEFIN is one confirmed request, executed by the one-shot worker. The web
    process has no certificate or network gate anyway, so these would fail
    later; refusing here says why, before anything is recorded."""
    if settings.nfse_environment == 'production':
        nfse.fail('production_operation_only_via_confirmed_worker')


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
        'operational_network_possible': False,  # always: see NationalNfseProvider docstring
    }


@router.get('/status')
def status(db: Session = Depends(get_db), settings=Depends(enabled),
           user: User = Depends(require_role(ROLE_LEITURA))):
    block = _restricted_readiness(settings)
    # Extended, read-only readiness detail (schema fingerprint, certificate
    # summary, policy/artifact-storage completeness, exhaustive blocker
    # list). Kept as a nested key so the pre-existing `missing_configuration`
    # contract above never changes shape for an older caller/test.
    block['readiness'] = compute_provider_readiness(db, settings, environment='restricted').as_dict()
    block['production_readiness'] = compute_production_readiness(db, settings).as_dict()
    return {'enabled': settings.nfse_enabled, 'environment': settings.nfse_environment,
            'provider': 'mock' if settings.nfse_environment == 'mock' else 'unavailable',
            'real_issuance_available': False, 'supported_flows': ['DIRECT', 'HOME'],
            'restricted_provider_foundation': block,
            # M66 — the button's own readiness. Never a control: the web
            # process only records confirmations; the worker sends.
            'production_issuance': {
                'available': (settings.nfse_environment == 'production'
                              and settings.nfse_production_worker_spool_dir is not None),
                'worker_configured': settings.nfse_production_worker_spool_dir is not None,
                'batch_available': False,
                'confirmation_ttl_minutes': settings.nfse_production_confirmation_ttl_minutes,
            }}


@router.get('/fila-resumo')
def queue_summary(db: Session = Depends(get_db), settings=Depends(enabled),
                  user: User = Depends(require_role(ROLE_LEITURA))):
    return nfse.queue_summary(db, settings.nfse_environment)


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


# ---------------------------------------------------- M29 — national DPS tax configuration


@router.get('/configuracao-nacional')
def national_configurations(environment: str | None = None, db: Session = Depends(get_db),
                            user: User = Depends(require_role(ROLE_LEITURA))):
    """Every version, newest-effective-first — including drafts and
    superseded ones (immutable history), never just "the active one"."""
    return [fiscal_config.safe_summary(row)
            for row in fiscal_config.list_versions(db, environment=environment)]


@router.get('/configuracao-nacional/ativa')
def active_national_configuration(environment: str = 'restricted',
                                  db: Session = Depends(get_db),
                                  user: User = Depends(require_role(ROLE_LEITURA))):
    """The row that would be used for a document dated TODAY. A real
    document at issuance time always resolves by its OWN competence date
    instead (see fiscal_config.resolve_active_configuration) — this
    endpoint is a read-only convenience for the admin screen, never itself
    a source a DPS is built from."""
    row = fiscal_config.resolve_active_version(db, environment=environment,
                                               as_of=datetime.now(timezone.utc).date())
    if row is None:
        nfse.fail('national_dps_configuration_not_defined_for_any_real_document', 404)
    return fiscal_config.safe_summary(row)


@router.post('/configuracao-nacional', status_code=201)
def new_national_configuration(payload: NationalDpsConfigurationVersionCreate, request: Request,
                               db: Session = Depends(get_db),
                               user: User = Depends(require_role(ROLE_ADMIN))):
    """Creates a NEW effective version. Never mutates an existing one — a
    same-version resubmission with a DIFFERENT payload is refused
    (``national_dps_configuration_version_immutable``), matching the
    ``FiscalPolicy`` versioning contract this table deliberately mirrors."""
    try:
        row = fiscal_config.create_version(
            db, environment=payload.environment, effective_from=payload.effective_from,
            validation_state=payload.validation_state, configuration=payload.configuration,
            actor=user.id, request_id=request.state.request_id,
        )
    except IntegrityError:
        db.rollback()
        nfse.fail('national_dps_configuration_version_conflict')
    return fiscal_config.safe_summary(row)


@router.get('/documentos')
def documents(state: str | None = None, params: PageParams = Depends(),
              db: Session = Depends(get_db), user: User = Depends(require_role(ROLE_LEITURA))):
    stmt = select(FiscalDocument).where(FiscalDocument.kind != "eeg").order_by(FiscalDocument.created_at.desc())
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
    _refuse_in_production(settings)
    # Explicit bounded selection; each document is an independent transaction.
    results = []
    for document_id in dict.fromkeys(payload.document_ids):
        key = payload_fingerprint({'batch': payload.idempotency_key, 'document': document_id})
        try:
            _document_access(db, document_id, user)
            doc = nfse.operate(db, document_id, 'issue', key, settings, user.id, request.state.request_id)
            results.append(nfse.serialize_document(db, doc))
        except HTTPException as exc:
            db.rollback()
            results.append({'id': document_id, 'error': exc.detail, 'status_code': exc.status_code})
    return {'itens': results, 'real_issuance_available': False}


@router.get('/documentos/{document_id}')
def inspect_document(document_id: str, db: Session = Depends(get_db),
                     user: User = Depends(require_role(ROLE_LEITURA))):
    return nfse.serialize_document(db, _document_access(db, document_id, user))


@router.get('/documentos/{document_id}/preparacoes')
def preparations(document_id: str, params: PageParams = Depends(), db: Session = Depends(get_db),
                 user: User = Depends(require_role(ROLE_LEITURA))):
    _document_access(db, document_id, user)
    def serialize(p):
        data = {c.name: getattr(p, c.name) for c in FiscalPreparation.__table__.columns}
        data['amount_snapshot'] = str(p.amount_snapshot) if p.amount_snapshot is not None else None
        return data
    return paginate(db, select(FiscalPreparation).where(FiscalPreparation.document_id == document_id)
                    .order_by(FiscalPreparation.created_at.desc()), params, serialize)


@router.post('/documentos/{document_id}/preflight')
def preflight(document_id: str, db: Session = Depends(get_db), settings=Depends(enabled),
             user: User = Depends(require_role(ROLE_GESTOR))):
    """OFFLINE only — never sends a network request. Walks the document
    through the deterministic build/XSD/signature chain as far as it can go
    and reports the exact stage/blocker. No real document has a national tax
    configuration or recipient identity source wired up yet (see
    ``nfse_national.readiness``), so this always stops at ``national_config``
    for a real document today; it exists so the Command Center can show
    operators WHY, staged for the day a real configuration source exists.
    """
    _document_access(db, document_id, user)
    return run_offline_preflight(db, document_id, settings, user.id).as_dict()


@router.get('/documentos/{document_id}/tentativas')
def attempts(document_id: str, params: PageParams = Depends(), db: Session = Depends(get_db),
             user: User = Depends(require_role(ROLE_LEITURA))):
    _document_access(db, document_id, user)
    return paginate(db, select(FiscalAttempt).where(FiscalAttempt.document_id == document_id)
                    .order_by(FiscalAttempt.number, FiscalAttempt.phase.desc()), params,
                    lambda a: {c.name: getattr(a, c.name) for c in FiscalAttempt.__table__.columns
                               if c.name not in {'idempotency_key', 'idempotency_fingerprint'}})


def _operation(action, reprocess=False):
    def endpoint(document_id: str, payload: OperationRequest, request: Request,
                 db: Session = Depends(get_db), settings=Depends(enabled),
                 user: User = Depends(require_role(ROLE_GESTOR))):
        _refuse_in_production(settings)
        _document_access(db, document_id, user)
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


# ------------------------------------- M66 — production issuance, one at a time
#
# Everything below needs gestor or admin. Nothing here sends: the confirmation
# endpoint records a request for the one-shot worker, which is the only
# process holding the A1. There is no batch variant, on purpose.


@router.get('/producao/fila')
def production_queue(db: Session = Depends(get_db), settings=Depends(enabled),
                     user: User = Depends(require_role(ROLE_GESTOR))):
    return production.production_queue(db, settings)


@router.post('/producao/exames/{exam_id}/preparar')
def production_prepare(exam_id: str, request: Request, db: Session = Depends(get_db),
                       settings=Depends(enabled), user: User = Depends(require_role(ROLE_GESTOR))):
    return production.prepare_for_confirmation(db, exam_id, settings, user.id,
                                               request.state.request_id)


@router.get('/producao/documentos/{document_id}/confirmacao')
def production_confirmation(document_id: str, db: Session = Depends(get_db),
                            settings=Depends(enabled),
                            user: User = Depends(require_role(ROLE_GESTOR))):
    production._require_production(settings)
    return production.confirmation_summary(db, _document_access(db, document_id, user))


@router.post('/producao/documentos/{document_id}/confirmar-emissao', status_code=202)
def production_confirm(document_id: str, payload: ProductionIssueConfirmation, request: Request,
                       db: Session = Depends(get_db), settings=Depends(enabled),
                       user: User = Depends(require_role(ROLE_GESTOR))):
    _document_access(db, document_id, user)
    return production.authorize_issue(
        db, document_id, preparation_id=payload.preparation_id, amount=payload.amount,
        confirmation=payload.confirmation, idempotency_key=payload.idempotency_key,
        settings=settings, actor=user.id, request_id=request.state.request_id)


@router.post('/producao/documentos/{document_id}/solicitar-reconciliacao', status_code=202)
def production_reconcile(document_id: str, payload: OperationRequest, request: Request,
                         db: Session = Depends(get_db), settings=Depends(enabled),
                         user: User = Depends(require_role(ROLE_GESTOR))):
    _document_access(db, document_id, user)
    return production.authorize_reconcile(
        db, document_id, idempotency_key=payload.idempotency_key, settings=settings,
        actor=user.id, request_id=request.state.request_id)


@router.get('/producao/pedidos/{request_id}')
def production_request(request_id: str, db: Session = Depends(get_db),
                       user: User = Depends(require_role(ROLE_GESTOR))):
    result = production.get_request(db, request_id)
    _document_access(db, result["document_id"], user)
    return result


# ── M26.34 — NFS-e agrupada: vários exames, um tomador ────────────────────
#
# Rotas próprias, sob o mesmo papel (gestor/admin) e o mesmo flag do resto do
# fiscal. Nenhuma delas chama o provedor: a emissão de produção continua
# passando exclusivamente pela confirmação dupla e pelo worker one-shot.


@router.post('/producao/agrupados/previa')
def grouped_preview(payload: GroupedPrepareRequest, db: Session = Depends(get_db),
                    settings=Depends(enabled),
                    user: User = Depends(require_role(ROLE_GESTOR))):
    """O que a nota agrupada seria AGORA. Só leitura: nada é criado.

    É o que a tela mostra antes do primeiro clique — itens, valores, total e,
    se houver, por que ainda não dá. Sem `for_update`: um painel que apenas
    consulta nunca deve segurar linha do banco operacional.
    """
    data = grouped.evaluate_group(db, payload.spirometry_exam_ids,
                                  payload.recipient_person_id,
                                  settings.nfse_environment, for_update=False)
    return grouped.serialize_group_preview(db, data)


@router.post('/producao/agrupados', status_code=201)
def grouped_prepare(payload: GroupedPrepareRequest, request: Request,
                    db: Session = Depends(get_db), settings=Depends(enabled),
                    user: User = Depends(require_role(ROLE_GESTOR))):
    doc = grouped.prepare_group(db, payload.spirometry_exam_ids,
                                payload.recipient_person_id, settings, user.id,
                                request.state.request_id)
    return nfse.serialize_document(db, doc)


@router.get('/producao/agrupados')
def grouped_documents(db: Session = Depends(get_db), settings=Depends(enabled),
                      user: User = Depends(require_role(ROLE_LEITURA))):
    docs = db.scalars(select(FiscalDocument).where(
        FiscalDocument.kind == 'grouped',
        FiscalDocument.environment == settings.nfse_environment,
    ).order_by(FiscalDocument.created_at.desc())).all()
    return {'itens': [nfse.serialize_document(db, doc) for doc in docs]}


@router.post('/producao/agrupados/{document_id}/repreparar')
def grouped_reprepare(document_id: str, payload: GroupedPrepareRequest, request: Request,
                      db: Session = Depends(get_db), settings=Depends(enabled),
                      user: User = Depends(require_role(ROLE_GESTOR))):
    doc = grouped.prepare_group(db, payload.spirometry_exam_ids,
                                payload.recipient_person_id, settings, user.id,
                                request.state.request_id, document_id=document_id)
    return nfse.serialize_document(db, doc)


@router.patch('/producao/agrupados/{document_id}/tomador')
def grouped_change_recipient(document_id: str, payload: GroupedRecipientChange,
                             request: Request, db: Session = Depends(get_db),
                             settings=Depends(enabled),
                             user: User = Depends(require_role(ROLE_GESTOR))):
    """Troca o tomador. NÃO reprepara: a preparação anterior fica stale de
    propósito, e a emissão exige preparar e conferir de novo."""
    doc = grouped.set_recipient(db, document_id, payload.recipient_person_id,
                                settings, user.id, request.state.request_id)
    return nfse.serialize_document(db, doc)


@router.get('/documentos/{document_id}/arquivos')
def document_files(document_id: str, db: Session = Depends(get_db),
                   user: User = Depends(require_role(ROLE_GESTOR))):
    from ..models import FiscalArtifact
    _document_access(db, document_id, user)
    rows = db.scalars(select(FiscalArtifact).where(FiscalArtifact.document_id == document_id,
        FiscalArtifact.kind.in_(['nfse_xml', 'danfse_pdf'])).order_by(FiscalArtifact.created_at.desc())).all()
    return {'itens': [{'id': a.id, 'kind': a.kind, 'size_bytes': a.size_bytes} for a in rows]}


@router.get('/documentos/{document_id}/arquivos/{artifact_id}/conteudo')
def download_file(document_id: str, artifact_id: str, request: Request,
                  db: Session = Depends(get_db), settings=Depends(enabled),
                  user: User = Depends(require_role(ROLE_GESTOR))):
    from hashlib import sha256
    from pathlib import Path
    from fastapi.responses import Response
    from ..models import FiscalArtifact
    from ..services.nfse_national.artifacts import read_artifact, FiscalArtifactStorageError
    _document_access(db, document_id, user)
    artifact = db.get(FiscalArtifact, artifact_id)
    if not artifact or artifact.document_id != document_id or artifact.kind not in {'nfse_xml', 'danfse_pdf'}:
        nfse.fail('fiscal_artifact_not_found', 404)
    try:
        data = read_artifact(settings.resolved_fiscal_artifacts_storage_dir(), Path(artifact.storage_relative_path))
    except (FiscalArtifactStorageError, ValueError, OSError):
        nfse.fail('fiscal_artifact_unavailable', 409)
    if len(data) != artifact.size_bytes or sha256(data).hexdigest() != artifact.sha256:
        nfse.fail('fiscal_artifact_integrity_failed', 409)
    pdf = artifact.kind == 'danfse_pdf'
    nfse.record(db, 'artifact_downloaded', 'fiscal_document', document_id, user.id,
                request.state.request_id, tipo=artifact.kind, sha256=artifact.sha256)
    db.commit()
    return Response(data, media_type='application/pdf' if pdf else 'application/xml', headers={
        'Content-Disposition': f'attachment; filename="nfse-{document_id}.{"pdf" if pdf else "xml"}"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
