"""M66 — production NFS-e from the Command Center: the WEB side.

WHAT THE WEB PROCESS DOES, AND WHAT IT NEVER DOES

It lists facts, prepares the one a gestor/admin picked, shows exactly what
would be sent, and — after a second, explicit confirmation that names the
amount — records a ``FiscalIssuanceRequest`` and rings a doorbell. That is
all. It never holds the A1, never builds a production transport and never
has ``M15_NFSE_PRODUCTION_NETWORK_ENABLED``: the send happens in a separate
one-shot worker (``nfse_production_worker``), started by systemd, which
re-checks every fact against the database before its single POST.

THE CONFIRMATION IS BOUND TO WHAT THE HUMAN SAW

A request stores the preparation id and fingerprint, the amount, and a hash
of the tomador identity that were on screen. If any of them moves between
the click and the worker — a new financial value, a corrected CPF, a changed
municipality — the worker refuses and nothing is sent. The human confirms
again, against the new facts.

PRIVACY

The patient's name is shown to gestor/admin (it is the tomador on the NFS-e
they are about to issue). The CPF never leaves the server: the confirmation
shows only a masked form, and the request keeps a sha256 of the identity,
never the CPF itself. Audit rows carry codes, ids and amounts only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (FiscalAttempt, FiscalDocument, FiscalIssuanceRequest, Person,
                      SpirometryExam, utcnow)
from . import nfse
from .idempotency import payload_fingerprint
from .nfse_external_issuance import IMPORT_OPERATION
from .nfse_national.service_description import (ServiceDescriptionUndetermined,
                                                spirometry_service_description)
from .nfse_national.service_location import SUPPORTED_SERVICE_MUNICIPALITIES
from .nfse_validity import fiscal_validity

PRODUCTION = 'production'
ACTIVE_STATUSES = ('authorized', 'running')
# Well past the worker's own systemd TimeoutStartSec (300 s).
RUNNING_DEADLINE = timedelta(minutes=15)
SPOOL_NAME = re.compile(r'^(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
                        r'\.(?P<kind>issue|reconcile)$')

# Operator-facing labels, the single place the backend names a block. The
# frontend shows these verbatim; the codes stay the API.
BLOCK_LABELS = {
    'blocked_by_partner_model': 'Fluxo de parceria (Pastore) — não é emitido por exame',
    'recipient_fiscal_data_incomplete': 'Dados fiscais do paciente incompletos',
    'missing_service_location': 'Município de prestação não registrado',
    'missing_required_tax_configuration': 'Configuração tributária incompleta',
    'blocked_by_fiscal_policy': 'Sem política fiscal vigente',
    'blocked_by_financial_source': 'Receita ausente, duplicada ou não recebida',
    'requires_reprepare': 'Dados mudaram — preparar de novo',
    'pending_clinical_or_identity_data': 'Exame não realizado ou data imprecisa',
    'blocked_other': 'Outro motivo',
}

STATUS_LABELS = {
    'ready': 'Pronto para emitir',
    'blocked': 'Bloqueado',
    'authorized': 'Confirmado — aguardando o executor',
    'running': 'Emitindo…',
    'issued': 'Emitida',
    'imported': 'Emitida manualmente (importada)',
    'rejected': 'Rejeitada — sem nova tentativa',
    'uncertain': 'Incerta — reconciliar (só consulta)',
    'in_progress': 'Em andamento',
    'cancelled': 'Cancelada',
}


# ------------------------------------------------------------------ helpers

def _require_production(settings: Settings) -> None:
    if settings.nfse_environment != PRODUCTION:
        nfse.fail('production_environment_required')


def amount_label(amount: Decimal) -> str:
    """R$ 1.234,56 — built by hand so the server and the phrase the human
    types can never disagree because of a locale."""
    whole, cents = f'{Decimal(amount):.2f}'.split('.')
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f'R$ {".".join(groups)},{cents}'


def confirmation_phrase(amount: Decimal) -> str:
    return f'Confirmar emissão de {amount_label(amount)}'


def mask_cpf(cpf: str | None) -> str | None:
    if not cpf or not re.fullmatch(r'\d{11}', cpf):
        return None
    return f'***.***.***-{cpf[-2:]}'


def recipient_fingerprint(person: Person | None) -> str:
    """A hash of the identity that will be the tomador. Stored instead of the
    CPF so the worker can prove it is the one the human saw."""
    data = {'person_id': person.id if person else None,
            'nome': person.nome_completo if person else None,
            'cpf': person.cpf if person else None}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _production_document(db: Session, exam_id: str) -> FiscalDocument | None:
    return db.scalars(select(FiscalDocument).where(
        FiscalDocument.spirometry_exam_id == exam_id,
        FiscalDocument.environment == PRODUCTION)).first()


def _active_request(db: Session, document_id: str) -> FiscalIssuanceRequest | None:
    return db.scalars(select(FiscalIssuanceRequest).where(
        FiscalIssuanceRequest.document_id == document_id,
        FiscalIssuanceRequest.status.in_(ACTIVE_STATUSES))).first()


def _latest_request(db: Session, document_id: str) -> FiscalIssuanceRequest | None:
    return db.scalars(select(FiscalIssuanceRequest).where(
        FiscalIssuanceRequest.document_id == document_id
    ).order_by(FiscalIssuanceRequest.authorized_at.desc()).limit(1)).first()


def _is_imported(db: Session, document_id: str) -> bool:
    return db.scalar(select(FiscalAttempt.id).where(
        FiscalAttempt.document_id == document_id,
        FiscalAttempt.operation == IMPORT_OPERATION).limit(1)) is not None


def _issued_key(db: Session, document_id: str) -> str | None:
    return db.scalar(select(FiscalAttempt.external_id).where(
        FiscalAttempt.document_id == document_id, FiscalAttempt.phase == 'completed',
        FiscalAttempt.external_id.is_not(None),
    ).order_by(FiscalAttempt.number.desc()).limit(1))


def expire_stale_requests(db: Session) -> int:
    """Authorized requests past their deadline become ``expired``. Lazy, run
    on read; the worker refuses them on its own regardless."""
    now = utcnow()
    stale = db.scalars(select(FiscalIssuanceRequest).where(
        FiscalIssuanceRequest.status.in_(ACTIVE_STATUSES))).all()
    count = 0
    for row in stale:
        if row.status == 'authorized' and _aware(row.expires_at) <= now:
            row.status, row.finished_at, row.result_code = 'expired', now, 'authorization_expired'
            count += 1
        elif (row.status == 'running' and row.claimed_at is not None
              and _aware(row.claimed_at) <= now - RUNNING_DEADLINE):
            # The worker died mid-run (systemd killed it, the host rebooted).
            # 'interrupted' frees the live slot but — unlike 'refused' — still
            # counts as "sent once": the document can only be reconciled.
            row.status, row.finished_at, row.result_code = 'interrupted', now, 'worker_interrupted'
            count += 1
    if count:
        db.commit()
    return count


def _aware(value):
    """SQLite hands datetimes back naive; they were written as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def serialize_request(row: FiscalIssuanceRequest | None) -> dict | None:
    if row is None:
        return None
    return {k: getattr(row, k) for k in (
        'id', 'document_id', 'kind', 'status', 'authorized_by', 'authorized_at', 'expires_at',
        'claimed_at', 'finished_at', 'operation_id', 'result_code', 'external_id',
        'provider_post_count', 'provider_get_count')} | {
        'amount_confirmed': f'{row.amount_confirmed:.2f}'}


# ------------------------------------------------------------------- queue

def _row_status(db: Session, doc: FiscalDocument | None, blockers: list[str]) -> str:
    if doc is not None:
        if doc.state == 'issued':
            return 'imported' if _is_imported(db, doc.id) else 'issued'
        if doc.state in ('issuing', 'reconciling'):
            return 'in_progress'
        if doc.state == 'uncertain':
            return 'uncertain'
        if doc.state == 'failed':
            return 'rejected'
        if doc.state == 'cancelled':
            return 'cancelled'
    return 'blocked' if blockers else 'ready'


def production_queue(db: Session, settings: Settings, *, limit: int = 200) -> dict:
    """Every performed exam, newest first, with what production would do with
    it right now. Read-only: evaluate() runs without row locks and nothing is
    created — a document is only created when a human picks a fact."""
    _require_production(settings)
    expire_stale_requests(db)
    exams = db.scalars(select(SpirometryExam).where(
        SpirometryExam.status.in_(nfse.PERFORMED)
    ).order_by(SpirometryExam.data_exame.desc(), SpirometryExam.public_code.desc())
        .limit(limit)).all()
    rows = []
    for exam in exams:
        doc = _production_document(db, exam.id)
        evaluation = nfse.evaluate(db, exam.id, PRODUCTION, for_update=False)
        blockers = list(evaluation['blocking_reasons'])
        status = _row_status(db, doc, blockers)
        person = db.get(Person, exam.person_id) if exam.person_id else None
        entry_amount = None
        if evaluation['financial_entry_id']:
            from ..models import FinancialEntry
            entry = db.get(FinancialEntry, evaluation['financial_entry_id'])
            entry_amount = f'{entry.valor:.2f}' if entry else None
        category = nfse._block_category(blockers) if status == 'blocked' else None
        request = _active_request(db, doc.id) if doc else None
        last_request = request or (_latest_request(db, doc.id) if doc else None)
        if request is not None:
            status = request.status
        rows.append({
            'exam_id': exam.id,
            'exam_code': exam.public_code,
            'patient_name': person.nome_completo if person else None,
            'service_date': exam.data_exame,
            'modality': exam.modalidade,
            'flow': evaluation['flow'],
            'municipio_ibge': exam.municipio_atendimento_ibge,
            'municipio_name': SUPPORTED_SERVICE_MUNICIPALITIES.get(exam.municipio_atendimento_ibge or ''),
            'amount': entry_amount,
            'status': status,
            'status_label': STATUS_LABELS.get(status, status),
            'blocking_reasons': blockers if status == 'blocked' else [],
            'block_category': category,
            'block_label': BLOCK_LABELS.get(category) if category else None,
            'document_id': doc.id if doc else None,
            'document_state': doc.state if doc else None,
            'fiscal_validity': fiscal_validity(db, doc) if doc else False,
            'external_id': _issued_key(db, doc.id) if doc and doc.state == 'issued' else None,
            'request': serialize_request(last_request),
            'can_issue': status == 'ready',
            'can_reconcile': status == 'uncertain' or (status == 'in_progress' and request is None),
        })
    counts: dict[str, int] = {}
    for row in rows:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    return {'environment': PRODUCTION, 'itens': rows, 'counts': counts,
            'batch_issuance_available': False,
            'worker_configured': settings.nfse_production_worker_spool_dir is not None}


# ------------------------------------------------------------ confirmation

def confirmation_summary(db: Session, doc: FiscalDocument) -> dict:
    """Exactly what the confirmation modal shows, and what the second click
    must echo back. Built from the immutable preparation snapshot, never from
    live fields the worker would not use."""
    prep = nfse.latest_preparation(db, doc.id)
    exam = db.get(SpirometryExam, doc.spirometry_exam_id)
    person = db.get(Person, prep.recipient_person_id) if prep and prep.recipient_person_id else None
    try:
        description = spirometry_service_description(exam.broncodilatador if exam else None)
    except ServiceDescriptionUndetermined:
        description = None
    amount = prep.amount_snapshot if prep else None
    ready = (doc.environment == PRODUCTION and doc.state == 'pending'
             and doc.eligibility == 'eligible' and amount is not None and description is not None)
    return {
        'document_id': doc.id,
        'preparation_id': prep.id if prep else None,
        'exam_code': exam.public_code if exam else None,
        'patient_name': person.nome_completo if person else None,
        'cpf_masked': mask_cpf(person.cpf if person else None),
        'service_date': prep.service_date if prep else None,
        'competence': prep.competence if prep else None,
        'flow': prep.flow if prep else None,
        'modality': exam.modalidade if exam else None,
        'municipio_ibge': prep.service_municipio_ibge if prep else None,
        'municipio_name': SUPPORTED_SERVICE_MUNICIPALITIES.get((prep.service_municipio_ibge or '') if prep else ''),
        'amount': f'{amount:.2f}' if amount is not None else None,
        'amount_label': amount_label(amount) if amount is not None else None,
        'service_description': description,
        'broncodilatador': exam.broncodilatador if exam else None,
        'state': doc.state,
        'eligibility': doc.eligibility,
        'blocking_reasons': doc.blocking_reasons or [],
        'block_label': (BLOCK_LABELS.get(nfse._block_category(doc.blocking_reasons))
                        if doc.blocking_reasons else None),
        'confirmation_phrase': confirmation_phrase(amount) if ready else None,
        'can_confirm': ready,
    }


def prepare_for_confirmation(db: Session, exam_id: str, settings: Settings, actor: str,
                             request_id: str | None = None) -> dict:
    """The first click. Creates/refreshes the production document for ONE
    exam (no network, no DPS number) and returns what the modal shows."""
    _require_production(settings)
    existing = _production_document(db, exam_id)
    if existing is not None and existing.state not in ('pending', 'blocked'):
        nfse.fail('document_not_issuable')
    doc = nfse.prepare(db, exam_id, settings, actor, request_id)
    return confirmation_summary(db, doc)


def _ring_doorbell(settings: Settings, row: FiscalIssuanceRequest) -> None:
    """Drop ``<request-id>.<kind>`` into the spool. The content is nothing but
    the id: the worker trusts the DATABASE row, never the file."""
    spool = Path(settings.nfse_production_worker_spool_dir)
    path = spool / f'{row.id}.{row.kind}'
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    with os.fdopen(fd, 'w') as handle:
        handle.write(row.id + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def _create_request(db: Session, settings: Settings, doc: FiscalDocument, prep, *, kind: str,
                    amount: Decimal, idempotency_key: str, actor: str,
                    request_id: str | None) -> FiscalIssuanceRequest:
    key = payload_fingerprint({'m66': idempotency_key, 'kind': kind})
    replay = db.scalars(select(FiscalIssuanceRequest).where(
        FiscalIssuanceRequest.idempotency_key == key)).first()
    if replay is not None:
        if replay.document_id != doc.id or replay.kind != kind:
            nfse.fail('idempotency_conflict')
        return replay
    person = db.get(Person, prep.recipient_person_id) if prep.recipient_person_id else None
    now = utcnow()
    row = FiscalIssuanceRequest(
        document_id=doc.id, spirometry_exam_id=doc.spirometry_exam_id, preparation_id=prep.id,
        preparation_fingerprint=prep.fingerprint, recipient_fingerprint=recipient_fingerprint(person),
        amount_confirmed=amount, kind=kind, status='authorized', authorized_by=actor,
        authorized_at=now,
        expires_at=now + timedelta(minutes=settings.nfse_production_confirmation_ttl_minutes),
        idempotency_key=key)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        nfse.fail('issuance_request_already_active')
    exam = db.get(SpirometryExam, doc.spirometry_exam_id)
    nfse.record(db, f'production_{kind}_authorized', 'fiscal_document', doc.id, actor, request_id,
                issuance_request_id=row.id, exam_code=exam.public_code if exam else None,
                valor_documentado=f'{amount:.2f}', operation=kind, environment=PRODUCTION,
                status='authorized')
    db.commit()
    try:
        _ring_doorbell(settings, row)
    except OSError:
        row.status, row.finished_at, row.result_code = 'refused', utcnow(), 'worker_doorbell_failed'
        nfse.record(db, f'production_{kind}_refused', 'fiscal_document', doc.id, actor, request_id,
                    issuance_request_id=row.id, codigo='worker_doorbell_failed')
        db.commit()
        nfse.fail('production_worker_unavailable', 503)
    return row


def authorize_issue(db: Session, document_id: str, *, preparation_id: str, amount: str,
                    confirmation: str, idempotency_key: str, settings: Settings, actor: str,
                    request_id: str | None = None) -> dict:
    """The second click. Every check here is repeated by the worker right
    before the POST; this copy exists so a stale or wrong confirmation is
    refused in front of the human instead of silently at the worker."""
    _require_production(settings)
    if settings.nfse_production_worker_spool_dir is None:
        nfse.fail('production_worker_unavailable', 503)
    doc = nfse.get_document(db, document_id, lock=True)
    if doc.environment != PRODUCTION:
        nfse.fail('document_environment_mismatch')
    replay = db.scalars(select(FiscalIssuanceRequest).where(
        FiscalIssuanceRequest.idempotency_key == payload_fingerprint(
            {'m66': idempotency_key, 'kind': 'issue'}))).first()
    if replay is not None:
        if replay.document_id != doc.id:
            nfse.fail('idempotency_conflict')
        return serialize_request(replay)
    if doc.state != 'pending' or doc.eligibility != 'eligible' or doc.blocking_reasons:
        nfse.fail('document_not_pending_eligible')
    prep = nfse.latest_preparation(db, doc.id)
    if prep is None or prep.id != preparation_id:
        nfse.fail('confirmation_stale')
    try:
        confirmed = Decimal(amount)
    except (InvalidOperation, TypeError):
        nfse.fail('confirmation_amount_mismatch', 422)
    if prep.amount_snapshot is None or confirmed != prep.amount_snapshot:
        nfse.fail('confirmation_amount_mismatch')
    if confirmation != confirmation_phrase(prep.amount_snapshot):
        nfse.fail('confirmation_phrase_mismatch')
    current = nfse.evaluate(db, doc.spirometry_exam_id, PRODUCTION)
    if current['blocking_reasons'] or prep.fingerprint != payload_fingerprint(current):
        nfse.fail('confirmation_stale')
    if db.scalar(select(FiscalAttempt.id).where(FiscalAttempt.document_id == doc.id).limit(1)):
        nfse.fail('document_already_attempted')
    if db.scalar(select(FiscalIssuanceRequest.id).where(
            FiscalIssuanceRequest.document_id == doc.id, FiscalIssuanceRequest.kind == 'issue',
            FiscalIssuanceRequest.claimed_at.is_not(None),
            FiscalIssuanceRequest.status != 'refused').limit(1)):
        nfse.fail('document_already_sent_once')
    if _active_request(db, doc.id) is not None:
        nfse.fail('issuance_request_already_active')
    row = _create_request(db, settings, doc, prep, kind='issue', amount=prep.amount_snapshot,
                          idempotency_key=idempotency_key, actor=actor, request_id=request_id)
    return serialize_request(row)


def authorize_reconcile(db: Session, document_id: str, *, idempotency_key: str,
                        settings: Settings, actor: str, request_id: str | None = None) -> dict:
    """Asks the worker for a READ-ONLY reconciliation (GET /dps, GET /nfse).
    A reconcile request can never POST: the worker's transport refuses, and
    the table's own CHECK refuses to record one."""
    _require_production(settings)
    if settings.nfse_production_worker_spool_dir is None:
        nfse.fail('production_worker_unavailable', 503)
    doc = nfse.get_document(db, document_id, lock=True)
    if doc.environment != PRODUCTION:
        nfse.fail('document_environment_mismatch')
    if doc.state not in nfse.IN_FLIGHT | {'uncertain'}:
        nfse.fail('reconciliation_not_required')
    if _active_request(db, doc.id) is not None:
        nfse.fail('issuance_request_already_active')
    prep = nfse.latest_preparation(db, doc.id)
    if prep is None or prep.amount_snapshot is None:
        nfse.fail('preparation_required')
    row = _create_request(db, settings, doc, prep, kind='reconcile', amount=prep.amount_snapshot,
                          idempotency_key=idempotency_key, actor=actor, request_id=request_id)
    return serialize_request(row)


def get_request(db: Session, request_id: str) -> dict:
    expire_stale_requests(db)
    row = db.get(FiscalIssuanceRequest, request_id)
    if row is None:
        nfse.fail('issuance_request_not_found', 404)
    data = serialize_request(row)
    doc = db.get(FiscalDocument, row.document_id)
    data['document_state'] = doc.state if doc else None
    data['fiscal_validity'] = fiscal_validity(db, doc) if doc else False
    return data
