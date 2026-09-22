"""Safe fiscal queue, using the current financial ledger and technical exam IDs.

Mutating entry points own their commits: a provider call is never made until a
started event and its audit record are durably committed. No real adapter exists.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import re

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import Settings
from ..fiscal_schemas import PolicyCreate, TaxConfiguration
from ..finance_categories import CATEGORIA_ESPIROMETRIA, e_receita_propria_do_componente
from ..ids import new_uuid
from ..models import (FiscalPolicy, FiscalDocument, FiscalPreparation, FiscalAttempt,
                      FiscalArtifact, FinancialEntry, SpirometryExam, Person, utcnow)
from .idempotency import idempotent_create, payload_fingerprint
from .nfse_national import artifacts as artifact_storage
from .nfse_national import dispatch as national_dispatch
from .nfse_national.identifiers import NFSE_ACCESS_KEY_PATTERN
from .nfse_providers import (get_provider, ProviderRequest, ProviderResult, Outcome,
                             REAL_ENVIRONMENTS)
from .nfse_validity import fiscal_validity


PERFORMED = {'Realizado', 'Laudo Liberado'}
IN_FLIGHT = {'issuing', 'reconciling'}
# M57 defined its own copy of REAL_ENVIRONMENTS here and M59 removed it:
# the name is imported from `nfse_providers` above, which is the single
# definition. (The M57 comment said production was "listed for the state
# vocabulary only, since get_provider() refuses to build one" — M59 wires
# it, so that caveat is gone; the membership test is the same either way.)
# The two terminal success states. They are distinct on purpose: 'issued' means
# an NFS-e exists at SEFIN, 'simulated' means the mock invented an identifier
# and nothing exists anywhere. Every place that used to ask "is this document
# successfully issued?" by comparing to the single old value 'simulated' now
# asks it of this set, so neither state can be silently forgotten.
SUCCESS_STATES = {'issued', 'simulated'}


def success_state(environment: str) -> str:
    """The terminal success state a document in this environment may reach.

    Derived from the environment, never from a provider-supplied label, so a
    misbehaving provider cannot promote a mock run to a real issuance.
    """
    return 'issued' if environment in REAL_ENVIRONMENTS else 'simulated'


def fail(code, status=409):
    raise HTTPException(status, detail={'codigo': code})


def record(db, action, entity, entity_id, actor, request_id=None, **details):
    audit(db, 'fiscal.' + action, entity, entity_id, user_id=actor,
          request_id=request_id, detalhes=details)


def get_document(db, document_id, lock=False):
    stmt = select(FiscalDocument).where(FiscalDocument.id == document_id)
    if lock:
        stmt = stmt.with_for_update()
    obj = db.scalar(stmt.execution_options(populate_existing=True))
    if obj is None:
        fail('fiscal_document_not_found', 404)
    return obj


def latest_preparation(db, document_id):
    return db.scalar(select(FiscalPreparation).where(
        FiscalPreparation.document_id == document_id
    ).order_by(FiscalPreparation.created_at.desc(), FiscalPreparation.id.desc()).limit(1))


def create_policy(db, payload: PolicyCreate, actor, request_id=None):
    existing = db.scalar(select(FiscalPolicy).where(FiscalPolicy.version == payload.version))
    data = payload.model_dump(mode='json')
    if existing:
        same = all(getattr(existing, k) == getattr(payload, k) for k in (
            'environment', 'flow', 'service', 'effective_from', 'effective_to', 'validation_state'
        )) and existing.configuration == data['configuration']
        if not same:
            fail('policy_version_immutable')
        return existing
    policy = FiscalPolicy(**{**data, 'effective_from': payload.effective_from,
                            'effective_to': payload.effective_to}, created_by=actor)
    # Overlaps remain visible; selection blocks ambiguous validated versions.
    # This also fails closed under concurrent policy creation, without guessing
    # that the highest version or most recent creation must be authoritative.
    db.add(policy)
    db.flush()
    record(db, 'policy_created', 'fiscal_policy', policy.id, actor, request_id,
           version=policy.version, status=policy.validation_state)
    db.commit()
    return policy


def evaluate(db: Session, exam_id: str, environment: str, *, for_update: bool = True) -> dict:
    """Fiscal eligibility of one exam.

    ``for_update`` defaults to True and every real preparation path keeps it:
    the rows it reads are locked until the caller commits, so nothing changes
    underneath a preparation. M63 — read-only callers (the production
    candidate scanner) pass False. They must never take row locks on the
    operational database, and PostgreSQL refuses SELECT ... FOR UPDATE inside
    a READ ONLY transaction anyway, which is exactly the guarantee they want.
    """
    def locked(stmt):
        return stmt.with_for_update() if for_update else stmt

    exam = db.scalar(locked(select(SpirometryExam).where(SpirometryExam.id == exam_id)))
    if exam is None:
        fail('missing_stable_exam_link', 422)
    reasons = []
    if exam.status not in PERFORMED:
        reasons.append('service_not_performed')
    service_date = exam.data_exame
    if (service_date is None or exam.data_exame_dia_assumido or
            exam.data_exame_precisao not in (None, 'dia')):
        reasons.append('service_date_missing_or_imprecise')
    if service_date and service_date > datetime.now(ZoneInfo("America/Sao_Paulo")).date():
        reasons.append('service_date_in_future')
    person = db.get(Person, exam.person_id)
    if not person or person.arquivado:
        reasons.append('recipient_missing_or_archived')
    # Structured modality + partner IDs only. No clinic-name classification.
    partner_flow = bool(exam.partner_id or exam.partner_unit_id or exam.modalidade == 'clinica_parceira')
    flow = 'PASTORE' if partner_flow else {'residencial': 'HOME', 'cowork': 'DIRECT'}.get(exam.modalidade, 'UNSUPPORTED')
    if flow not in {'DIRECT', 'HOME'}:
        reasons.append('commercial_flow_unsupported')
    entries = db.scalars(locked(select(FinancialEntry).where(
        FinancialEntry.spirometry_exam_id == exam.id,
        FinancialEntry.tipo == 'receita',
    ))).all()
    # Reuse the ledger's own-revenue contract, including its legacy category
    # normalization. Physician transfers (M26.9) are a separate table; ledger
    # repasses/expenses and unrelated revenue categories never qualify here.
    entries = [entry for entry in entries if e_receita_propria_do_componente(
        tipo=entry.tipo, categoria=entry.categoria,
        spirometry_exam_id=entry.spirometry_exam_id,
        consultation_id=entry.consultation_id,
    )[0] == CATEGORIA_ESPIROMETRIA]
    entry = entries[0] if len(entries) == 1 else None
    if not entries:
        reasons.append('financial_entry_missing')
    elif len(entries) != 1:
        reasons.append('financial_entry_ambiguous')
    if entry and (entry.consultation_id or entry.partner_referral_id or entry.partner_settlement_id):
        reasons.append('financial_link_incoherent')
    if entry and (entry.status != 'Recebido' or entry.moeda != 'BRL' or entry.valor <= 0):
        reasons.append('financial_revenue_not_received_or_invalid')
    policies = db.scalars(select(FiscalPolicy).where(
        FiscalPolicy.environment == environment, FiscalPolicy.flow == flow,
        FiscalPolicy.service == 'spirometry',
    )).all()
    valid_dates = [p for p in policies if service_date and p.effective_from <= service_date <= p.effective_to]
    candidates = [p for p in valid_dates if p.validation_state == 'validated']
    policy = candidates[0] if len(candidates) == 1 else None
    if len(candidates) > 1:
        reasons.append('policy_ambiguous')
    elif not policy:
        reasons.append('policy_incomplete' if valid_dates else 'policy_outside_validity' if policies else 'policy_missing')
    if policy:
        try:
            configuration = TaxConfiguration.model_validate(policy.configuration)
            if configuration.missing_fields():
                reasons.append('policy_incomplete')
        except ValidationError:
            reasons.append('policy_invalid')
    description = None
    if service_date:
        bd = {True: ' com broncodilatador', False: ' sem broncodilatador', None: ''}[exam.broncodilatador]
        description = f'Realização de exame de espirometria{bd} em {service_date:%d/%m/%Y}.'
    return dict(financial_entry_id=entry.id if entry else None,
                policy_id=policy.id if policy else None,
                recipient_person_id=person.id if person else None,
                flow=flow, service_date=service_date, competence=service_date,
                # M31 — raw structured snapshot, copied as-is (never validated
                # or defaulted here). Universal gate for ALL environments
                # would be scope creep: whether a value is required/supported
                # is a REAL-DOCUMENT concern, checked only where the national
                # DPS is actually built (preflight.py/dispatch.py), exactly
                # like `broncodilatador`/service_description.py already is.
                service_municipio_ibge=exam.municipio_atendimento_ibge,
                amount_snapshot=entry.valor if entry and policy and not reasons else None,
                description=description, blocking_reasons=sorted(set(reasons)))


def prepare(db, exam_id, settings, actor, request_id=None):
    if not settings.nfse_enabled:
        fail('nfse_disabled', 503)
    environment = settings.nfse_environment
    # Check stable reference before creating a queue row/FK.
    if db.get(SpirometryExam, exam_id) is None:
        fail('missing_stable_exam_link', 422)
    identity = {'exam_id': exam_id, 'environment': environment}
    key = payload_fingerprint(identity)
    def factory(key, fingerprint):
        obj = FiscalDocument(spirometry_exam_id=exam_id, environment=environment,
                             created_by=actor, idempotency_key=key,
                             idempotency_fingerprint=fingerprint)
        db.add(obj)
        db.flush()
        return obj
    doc, _ = idempotent_create(db, FiscalDocument, key, identity, factory)
    doc = get_document(db, doc.id, lock=True)
    if doc.state not in {'blocked', 'pending', 'failed'}:
        fail('document_frozen_after_attempt')
    data = evaluate(db, exam_id, environment)
    fingerprint = payload_fingerprint(data)
    latest = latest_preparation(db, doc.id)
    preparation_changed = latest is None or latest.fingerprint != fingerprint
    if preparation_changed:
        db.add(FiscalPreparation(document_id=doc.id, created_by=actor,
                                 fingerprint=fingerprint, **data))
    doc.blocking_reasons = data['blocking_reasons']
    doc.eligibility = 'blocked' if doc.blocking_reasons else 'eligible'
    # A known failed issuance is retried only by the explicit reprocess action.
    issued_before = db.scalar(select(FiscalAttempt.id).where(
        FiscalAttempt.document_id == doc.id, FiscalAttempt.operation == 'issue',
        FiscalAttempt.phase == 'started',
    ).limit(1))
    doc.state = ('blocked' if doc.blocking_reasons else
                 'failed' if issued_before else 'pending')
    if preparation_changed:
        record(db, 'prepared', 'fiscal_document', doc.id, actor, request_id,
               status=doc.state, motivo=doc.blocking_reasons)
    db.commit()
    return doc


def _request(doc, preparation, operation_id, *, description=None):
    return ProviderRequest(doc.id, operation_id, preparation.id,
                           str(preparation.amount_snapshot), preparation.competence.isoformat(),
                           description or preparation.description)


def _normalized(result, operation, document_id, provider_name=None):
    allowed = {Outcome.UNCERTAIN, Outcome.REJECTED}
    if operation in {'issue', 'reconcile'}:
        # M57 — a success outcome is admissible only in the vocabulary of the
        # provider that reported it: a real provider says ISSUED, the mock
        # says SIMULATED, and neither may speak for the other. This is the
        # structural reason a mock run can never be mistaken for, or
        # upgraded into, a real issuance — and the reason a real provider
        # that reported the old 'simulated' label would now be treated as
        # malfunctioning (UNCERTAIN, requiring reconciliation) rather than
        # silently recorded as a success under the wrong word.
        allowed.add(Outcome.ISSUED if provider_name in REAL_ENVIRONMENTS
                    else Outcome.SIMULATED)
    if operation in {'cancel', 'reconcile'}:
        allowed.add(Outcome.CANCELLED)
    if operation == 'reconcile':
        allowed.add(Outcome.NOT_FOUND)
    if (not isinstance(result, ProviderResult) or
            not isinstance(result.outcome, Outcome) or result.outcome not in allowed):
        return ProviderResult(Outcome.UNCERTAIN)
    if result.external_id:
        if provider_name in REAL_ENVIRONMENTS:
            # Government-issued NFS-e access key (TSIdNFSe) — a national
            # identifier, never derived from our own document_id. M56 — the
            # same shape in production: the key format is set by SEFIN, not
            # by the environment, so leaving 'production' out here would
            # have sent a genuine production key down the mock branch below
            # and downgraded a real issuance to UNCERTAIN.
            if not NFSE_ACCESS_KEY_PATTERN.fullmatch(result.external_id):
                return ProviderResult(Outcome.UNCERTAIN)
        # The only other implemented provider uses technical UUID IDs. Raw
        # responses, errors, personal document numbers and arbitrary
        # external IDs never persist.
        elif (not re.fullmatch(r'MOCK-[0-9a-f-]{36}', result.external_id) or
              result.external_id != 'MOCK-' + document_id):
            return ProviderResult(Outcome.UNCERTAIN)
    if result.outcome in {Outcome.ISSUED, Outcome.SIMULATED, Outcome.CANCELLED} and not result.external_id:
        return ProviderResult(Outcome.UNCERTAIN)
    return result


def operate(db, document_id, operation, key, settings: Settings, actor,
            request_id=None, *, reprocess=False, provider=None):
    # Gate first, even when an operation key is replayed or provider is injected.
    selected = get_provider(settings)
    injected = provider is not None
    provider = provider or selected
    # "The provider must match the CONFIGURED environment, by both name and
    # environment." M59 — this used to be a lookup table that simply had no
    # entry for production, so any production provider was refused here. That
    # was the M56 belt, and it is removed now that the path is wired: the rule
    # is the same for all three environments, and production is held to it
    # exactly like the others rather than being excluded from it.
    #
    # The check still bites in both directions — a provider bound to one
    # environment can never act for a document configured for another, which
    # is what stops a restricted-named provider reporting a success that
    # would be recorded against a production document.
    expected_name = settings.nfse_environment
    if provider.environment != settings.nfse_environment or provider.name != expected_name:
        fail('provider_environment_mismatch', 503)
    doc = get_document(db, document_id, lock=True)
    if doc.environment != settings.nfse_environment:
        fail('document_environment_mismatch')
    identity = {'document_id': doc.id, 'operation': operation, 'reprocess': reprocess}
    op_key = payload_fingerprint({'key': key, 'operation': operation})
    previous = db.scalar(select(FiscalAttempt).where(FiscalAttempt.idempotency_key == op_key))
    if previous:
        if previous.idempotency_fingerprint != payload_fingerprint(identity):
            fail('idempotency_conflict')
        return doc
    preparation = latest_preparation(db, doc.id)
    if not preparation:
        fail('preparation_required')
    target = None
    if operation == 'issue':
        if doc.state in SUCCESS_STATES:
            return doc
        allowed = {'failed'} if reprocess else {'pending'}
        if doc.state not in allowed or doc.eligibility != 'eligible':
            fail('reconciliation_required' if doc.state in IN_FLIGHT | {'uncertain'} else 'document_not_pending_eligible')
        current = evaluate(db, doc.spirometry_exam_id, doc.environment)
        if current['blocking_reasons'] or preparation.fingerprint != payload_fingerprint(current):
            doc.blocking_reasons = sorted(set(current['blocking_reasons'] + ['preparation_stale']))
            doc.eligibility, doc.state = 'blocked', 'blocked'
            record(db, 'issue_blocked', 'fiscal_document', doc.id, actor, request_id,
                   motivo=doc.blocking_reasons)
            db.commit()
            return doc
    elif operation == 'cancel':
        if doc.state == 'cancelled':
            return doc
        if doc.state not in SUCCESS_STATES:
            fail('only_issued_document_can_be_cancelled')
    elif operation == 'reconcile':
        if doc.state not in IN_FLIGHT | {'uncertain'}:
            fail('reconciliation_not_required')
        target = db.scalar(select(FiscalAttempt).where(
            FiscalAttempt.document_id == doc.id, FiscalAttempt.phase == 'started',
            FiscalAttempt.operation.in_(['issue', 'cancel']),
        ).order_by(FiscalAttempt.number.desc()).limit(1))
        last_start = db.scalar(select(FiscalAttempt).where(
            FiscalAttempt.document_id == doc.id, FiscalAttempt.phase == 'started'
        ).order_by(FiscalAttempt.number.desc()).limit(1))
        if not target:
            fail('reconciliation_target_missing')
        if doc.state in IN_FLIGHT and last_start.started_at.replace(tzinfo=timezone.utc) > utcnow() - timedelta(minutes=5):
            fail('operation_in_progress')
    else:
        fail('unsupported_operation', 422)
    number = (db.scalar(select(func.max(FiscalAttempt.number)).where(FiscalAttempt.document_id == doc.id)) or 0) + 1
    description_override = None
    if not injected and settings.nfse_environment in REAL_ENVIRONMENTS:
        # M29 wiring: turn the structural-only RealProviderPending marker
        # into a fully-bound, per-document NationalNfseProvider.
        #
        # M59 — this said `== 'restricted'`, which was the last place the
        # production path stopped: get_provider() would hand back a
        # production marker and operate() would then try to call issue() on
        # the marker itself, which raises by design. Both real environments
        # resolve the same way now; which transport gets built, and whether
        # it may send, is decided inside dispatch and by the transport's own
        # gate — not by this condition.
        # Re-checks every gate against the database and THIS document —
        # never trusts get_provider()'s settings-only check alone. A test
        # that injects its own `provider=` bypasses this entirely, exactly
        # like the mock path always has.
        #
        # M33/M36 — the official DPS number (TSIdDPS numero_dps) is NOT the
        # same thing as `number` (this attempt's own append-only audit
        # sequence) and is no longer derived from it at all: dispatch now
        # allocates/reuses a durable, globally-unique number keyed only by
        # `doc.id` (see nfse_national.dps_numbering) — every attempt for
        # this document, issue or reconcile, first or repeated, converges on
        # the same number without needing to distinguish `target` here.
        provider = national_dispatch.resolve_national_provider(
            db, settings, doc, preparation, actor)
        description_override = national_dispatch.resolve_service_description(db, doc)
    operation_id = new_uuid()
    started_at = utcnow()
    common = dict(document_id=doc.id, preparation_id=preparation.id,
                  operation_id=operation_id, reconciles_operation_id=target.operation_id if target else None,
                  operation=operation, number=number, provider=provider.name,
                  environment=settings.nfse_environment,
                  actor_id=actor, started_at=started_at)
    def factory(key, fingerprint):
        event = FiscalAttempt(**common, phase='started', outcome='started',
                              reconciliation_required=True, idempotency_key=key,
                              idempotency_fingerprint=fingerprint)
        db.add(event)
        db.flush()
        return event
    started, replay = idempotent_create(db, FiscalAttempt, op_key, identity, factory)
    if replay:
        return doc
    doc.state = 'reconciling' if operation == 'reconcile' else 'issuing'
    record(db, operation + '_started', 'fiscal_document', doc.id, actor, request_id,
           provider=provider.name, status=doc.state, sequencia=number)
    # M47 — this commit must escalate SQLite to an EXCLUSIVE lock. It fails
    # with "database is locked" if ANY other connection still holds even a
    # SHARED one — including a caller that opened its own session and kept it
    # open across the very HTTP request it is waiting on. That was the real
    # DPS #9 failure; the caller-side rule and its regression test live in
    # scripts/nfse_m45_dps9_restricted_issue.py and
    # tests/test_nfse_orchestrator_self_lock.py.
    db.commit()  # Durable intent before crossing the provider boundary.
    request = _request(doc, preparation, target.operation_id if target else operation_id,
                       description=description_override)
    access_key_conflict = False
    try:
        result = (provider.query(request, target.operation) if target else
                  provider.issue(request) if operation == 'issue' else provider.cancel(request))
        result = _normalized(result, operation, doc.id, provider.name)
        if target and ((target.operation == 'issue' and result.outcome == Outcome.CANCELLED) or
                       (target.operation == 'cancel' and
                        result.outcome in {Outcome.ISSUED, Outcome.SIMULATED})):
            result = ProviderResult(Outcome.UNCERTAIN)
        if result.external_id:
            # Immutable external fiscal evidence: a document's access key,
            # once recorded by any past completed attempt, may never be
            # silently replaced by a different one from a later call.
            existing_external_id = db.scalar(select(FiscalAttempt.external_id).where(
                FiscalAttempt.document_id == doc.id, FiscalAttempt.phase == 'completed',
                FiscalAttempt.external_id.is_not(None),
            ).order_by(FiscalAttempt.number.desc()).limit(1))
            if existing_external_id and existing_external_id != result.external_id:
                result = ProviderResult(Outcome.UNCERTAIN)
                access_key_conflict = True
    except Exception:
        # Never persist exception text or guess that a timeout means rejection.
        result = ProviderResult(Outcome.UNCERTAIN)
    doc = get_document(db, doc.id, lock=True)
    newer = db.scalar(select(FiscalAttempt.id).where(
        FiscalAttempt.document_id == doc.id, FiscalAttempt.number > number,
        FiscalAttempt.phase == 'started').limit(1))
    uncertain = result.outcome == Outcome.UNCERTAIN or bool(newer)
    if uncertain:
        state = 'uncertain'
    elif result.outcome in {Outcome.ISSUED, Outcome.SIMULATED}:
        # M57 — the state comes from the document's OWN environment, not from
        # the outcome's label. _normalized() has already refused any success
        # outcome that does not match the provider kind, so these two always
        # agree here; deriving from the environment keeps that true even if a
        # future provider is wired in carelessly.
        state = success_state(doc.environment)
    elif result.outcome == Outcome.CANCELLED:
        state = 'cancelled'
    elif operation == 'reconcile' and result.outcome == Outcome.NOT_FOUND:
        # A cancellation that the authority never registered leaves the
        # document where it was: successfully issued.
        state = 'failed' if target.operation == 'issue' else success_state(doc.environment)
    elif result.outcome == Outcome.REJECTED and operation == 'issue':
        state = 'failed'
    elif result.outcome == Outcome.REJECTED and operation == 'cancel':
        # The cancellation was refused — the NFS-e is still issued.
        state = success_state(doc.environment)
    else:
        uncertain, state = True, 'uncertain'
    # M35 — same classification as before; when the provider boundary
    # captured a safe transport diagnostic (HTTP status only, never body —
    # see nfse_national.responses.safe_diagnostic_code()), it replaces the
    # generic label with a more specific one (e.g. "provider_rejected:http_400").
    # Diagnostic-only: never influences `uncertain`/`state` above.
    diagnostic = result.diagnostic_code
    error = ('access_key_conflict' if access_key_conflict else
             (diagnostic or 'provider_uncertain') if uncertain else
             (diagnostic or 'provider_rejected') if result.outcome == Outcome.REJECTED else None)
    completed_event = FiscalAttempt(
        **common, phase='completed', outcome=result.outcome.value,
        completed_at=utcnow(), external_id=result.external_id,
        error_code=error, reconciliation_required=uncertain,
        idempotency_key=payload_fingerprint({'completed': operation_id}),
        idempotency_fingerprint=payload_fingerprint(identity))
    db.add(completed_event)
    db.flush()
    doc.state = state
    # M40/M41/M44 — already-sanitized SEFIN validation/shape detail (see
    # nfse_national.error_sanitizer / response_diagnostics), if the provider
    # captured any: recorded through the SAME append-only, allowlisted audit
    # trail as every other event here (app/audit.py — truncates/sanitizes
    # independently, a second layer on top of the provider boundary's own
    # sanitizer). Never affects `error_code`/`state`/`uncertain` above, and
    # never the raw response body.
    sefin_detail = {}
    for field, audit_key in (('codigo', 'sefin_erro_codigos'),
                             ('descricao', 'sefin_erro_descricoes'),
                             ('complemento', 'sefin_erro_complementos'),
                             ('mensagem', 'sefin_erro_mensagens'),
                             ('erro', 'sefin_erro_erros')):
        values = [e[field] for e in (result.validation_errors or ()) if e.get(field)]
        if values:
            sefin_detail[audit_key] = values
    # M44 — parametros is a tuple-per-item; flatten across every error item
    # into one list of scalars, matching the shape the audit allowlist's
    # own sanitizer already expects for every other list field here.
    parametros_flat = [
        item for e in (result.validation_errors or ()) for item in (e.get('parametros') or ())
    ]
    if parametros_flat:
        sefin_detail['sefin_erro_parametros'] = parametros_flat
    shape = result.response_shape
    if shape:
        sefin_detail['sefin_resposta_tipo_corpo'] = shape.get('body_kind')
        sefin_detail['sefin_resposta_content_type'] = shape.get('content_type')
        sefin_detail['sefin_resposta_tamanho'] = shape.get('content_length')
        sefin_detail['sefin_resposta_sha256'] = shape.get('sha256')
        if shape.get('top_level_keys'):
            sefin_detail['sefin_resposta_chaves_json'] = list(shape['top_level_keys'])
        if shape.get('erros_status'):
            sefin_detail['sefin_erros_status'] = shape['erros_status']
        if shape.get('erros_count') is not None:
            sefin_detail['sefin_erros_contagem'] = shape['erros_count']
        if shape.get('erros_item_field_names'):
            sefin_detail['sefin_erros_nomes_campos'] = list(shape['erros_item_field_names'])
    # M55 — evidence of a SUCCESS, persisted with the same append-only
    # discipline a rejection already had. The bytes we submitted and the
    # document the government returned are written as FiscalArtifacts; only
    # their digests reach the audit trail.
    #
    # Deliberately BEST-EFFORT: evidence must never be able to fail a fiscal
    # operation. A full disk or a permissions problem here would otherwise
    # roll back an attempt the provider has already completed — turning a
    # recorded success into a phantom. Any failure is itself recorded, as a
    # boolean, and the fiscal outcome stands.
    if result.provider_processed_at:
        sefin_detail['sefin_data_hora_processamento'] = result.provider_processed_at
    evidence = [('dps_signed_xml', result.submitted_document),
                ('nfse_xml', result.returned_document)]
    if any(payload for _, payload in evidence):
        try:
            root = settings.resolved_fiscal_artifacts_storage_dir()
            for kind, payload in evidence:
                if not payload:
                    continue
                stored = artifact_storage.write_artifact(
                    root, document_id=doc.id, attempt_id=completed_event.id,
                    kind=kind, data=payload)
                db.add(FiscalArtifact(document_id=doc.id, attempt_id=completed_event.id,
                                      kind=kind, storage_relative_path=str(stored.relative_path),
                                      sha256=stored.sha256, size_bytes=stored.size_bytes,
                                      created_by=actor))
                sefin_detail['evidencia_dps_enviado_sha256' if kind == 'dps_signed_xml'
                             else 'evidencia_nfse_recebida_sha256'] = stored.sha256
        except Exception:
            # Never re-raise: see the note above. The boolean is the signal.
            sefin_detail['evidencia_persistencia_falhou'] = True
    record(db, operation + '_completed', 'fiscal_document', doc.id, actor, request_id,
           provider=provider.name, resultado=result.outcome.value, status=state, sequencia=number,
           **sefin_detail)
    db.commit()
    return doc


# Priority-ordered: each blocked document is counted in exactly one category,
# the first one below whose reason set intersects its blocking_reasons. Order
# matters (e.g. a missing tax config takes priority over a generic "policy
# missing" framing, since it points the operator at the more specific fix).
BLOCK_CATEGORIES = [
    # A partner flow with no fiscal/contractual model authorized (Pastore/
    # SPLIT) is a business-model blocker, not a missing-policy-row blocker —
    # even though `evaluate()` also reports `policy_missing` for it (no
    # policy will ever exist for an unsupported flow). Checked first so the
    # operator sees the real fix ("this flow needs its own model"), not
    # "create a policy" for a flow that structurally cannot have one yet.
    ('blocked_by_partner_model', {'commercial_flow_unsupported'}),
    ('missing_required_tax_configuration', {'policy_incomplete', 'policy_invalid'}),
    ('blocked_by_fiscal_policy', {'policy_missing', 'policy_outside_validity', 'policy_ambiguous'}),
    ('blocked_by_financial_source', {'financial_entry_missing', 'financial_entry_ambiguous',
                                     'financial_link_incoherent', 'financial_revenue_not_received_or_invalid'}),
    ('requires_reprepare', {'preparation_stale'}),
    ('pending_clinical_or_identity_data', {'service_not_performed', 'service_date_missing_or_imprecise',
                                           'service_date_in_future', 'recipient_missing_or_archived',
                                           'missing_stable_exam_link'}),
]


def _block_category(blocking_reasons):
    reasons = set(blocking_reasons or [])
    for label, causes in BLOCK_CATEGORIES:
        if reasons & causes:
            return label
    return 'blocked_other'


def queue_summary(db: Session, environment: str) -> dict:
    """Aggregate counts for the batch UI: how many documents are eligible,
    how many are blocked (broken down by WHY), and how many sit in every
    other queue state. Never returns document identities or amounts — only
    counts — so this stays cheap to poll and free of anything sensitive.
    """
    rows = db.execute(select(FiscalDocument.state, FiscalDocument.blocking_reasons)
                      .where(FiscalDocument.environment == environment)).all()
    counts = {
        'eligible': 0, 'issuing': 0, 'issued': 0, 'simulated': 0, 'failed': 0,
        'uncertain': 0, 'reconciling': 0, 'cancelled': 0,
    }
    blocked_breakdown = {label: 0 for label, _ in BLOCK_CATEGORIES}
    blocked_breakdown['blocked_other'] = 0
    blocked_total = 0
    for state, blocking_reasons in rows:
        if state == 'pending':
            counts['eligible'] += 1
        elif state == 'blocked':
            blocked_total += 1
            blocked_breakdown[_block_category(blocking_reasons)] += 1
        elif state in counts:
            counts[state] += 1
    return {'environment': environment, 'total': len(rows), 'eligible': counts['eligible'],
            'blocked_total': blocked_total, 'blocked_breakdown': blocked_breakdown,
            'issuing': counts['issuing'], 'issued': counts['issued'],
            # M57 — 'simulated' now counts ONLY genuine mock runs. It stays in
            # the payload (it is still a real queue state in the mock
            # environment) but no longer carries real issuances: those are
            # 'issued'. A caller that reads only 'simulated' therefore reports
            # zero for a restricted/production queue instead of silently
            # mislabelling official NFS-e as simulations.
            'simulated': counts['simulated'],
            'failed': counts['failed'], 'uncertain': counts['uncertain'],
            'reconciling': counts['reconciling'], 'cancelled': counts['cancelled'],
            'reconciliation_required': counts['uncertain'] + counts['reconciling']}


def serialize_document(db, doc):
    prep = latest_preparation(db, doc.id)
    data = {k: getattr(doc, k) for k in ('id', 'spirometry_exam_id', 'environment',
            'state', 'eligibility', 'blocking_reasons', 'created_at', 'updated_at')}
    data['reconciliation_required'] = doc.state in IN_FLIGHT | {'uncertain'}
    data['monetary_source'] = 'Financeiro_Lancamentos / financial_entries'
    # M58 — the hard-coded False is gone, replaced by the explicit contract
    # M57 said this field needed. It is still False for everything that
    # exists today, but now BECAUSE OF A RULE rather than by fiat: mock
    # invents identifiers, and restricted (tpAmb=2) issues real documents
    # that carry no fiscal effect. Only a production issuance with complete,
    # coherent evidence can be true. See services/nfse_validity.py — that is
    # the single source of truth and nothing else may compute its own answer.
    data['fiscal_validity'] = fiscal_validity(db, doc)
    if prep:
        data['preparation'] = {k: getattr(prep, k) for k in (
            'id', 'financial_entry_id', 'policy_id', 'recipient_person_id', 'flow',
            'service_date', 'competence', 'service_municipio_ibge', 'description', 'created_at')}
        data['preparation']['amount_snapshot'] = str(prep.amount_snapshot) if prep.amount_snapshot is not None else None
        # No CPF/name lookup is needed for a technical fiscal foundation.
    return data
