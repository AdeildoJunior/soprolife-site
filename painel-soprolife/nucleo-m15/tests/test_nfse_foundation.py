"""Fiscal foundation: synthetic data, no transport, no credentials or clinical fixture."""
from datetime import date, timedelta
from decimal import Decimal
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.fiscal_schemas import PolicyCreate
from app.models import (Person, SpirometryExam, FinancialEntry, FiscalDocument,
                        FiscalPolicy, FiscalPreparation, FiscalAttempt, AuditLog)
from app.services import nfse
from app.services.nfse_providers import MockNfseProvider, Outcome, ProviderResult, get_provider


@pytest.fixture
def settings():
    return Settings(nfse_enabled=True, nfse_environment='mock')


def policy_payload(**changes):
    data = dict(version='SYNTH-MOCK-v1', environment='mock', flow='HOME',
                effective_from='2026-01-01', effective_to='2026-12-31', validation_state='validated',
                configuration={
                    'national_service_code': 'MOCK', 'municipal_service_code': 'MOCK',
                    'service_list_item': 'MOCK', 'tax_rate': '0', 'tax_regime': 'MOCK',
                    'withholding': False, 'enforceability': 'MOCK', 'incidence': 'MOCK',
                    'municipality': 'MOCK', 'ibs_cbs_treatment': 'MOCK',
                    'amount_basis': 'financial_entry.valor', 'competence_rule': 'service_date',
                    'issuer': 'SOPROLIFE', 'recipient': 'service_person',
                    'own_revenue_confirmed': True, 'validation_reference': 'SYNTHETIC-ONLY',
                })
    data.update(changes)
    return PolicyCreate.model_validate(data)


@pytest.fixture
def source(db, users):
    p = Person(public_code='PES-NFSE', nome_completo='Pessoa Sintética Fiscal',
               nome_normalizado='pessoa sintetica fiscal')
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code='ESP-NFSE', person_id=p.id, status='Realizado',
                       data_exame=date(2026, 8, 10), data_exame_precisao='dia',
                       modalidade='residencial', broncodilatador=True)
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code='LAN-NFSE', tipo='receita', categoria='Espirometria',
                       valor=Decimal('123.45'), status='Recebido', spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    return e, f


@pytest.fixture
def ready(db, users, source, settings):
    nfse.create_policy(db, policy_payload(), users['admin'].id)
    return nfse.prepare(db, source[0].id, settings, users['gestor'].id)


def issue(db, users, settings, doc, **kwargs):
    return nfse.operate(db, doc.id, 'issue', 'synthetic-issue-1', settings, users['gestor'].id, **kwargs)


def events(db, doc):
    return db.scalars(select(FiscalAttempt).where(FiscalAttempt.document_id == doc.id)
                      .order_by(FiscalAttempt.number, FiscalAttempt.phase.desc())).all()


def test_performed_amount_competence_description_idempotency(db, users, source, settings, ready):
    prep = nfse.latest_preparation(db, ready.id)
    assert ready.state == 'pending' and ready.eligibility == 'eligible'
    assert prep.financial_entry_id == source[1].id
    assert prep.amount_snapshot == Decimal('123.45')
    assert prep.competence == source[0].data_exame != source[1].data_competencia
    assert prep.description == 'Realização de exame de espirometria com broncodilatador em 10/08/2026.'
    assert nfse.prepare(db, source[0].id, settings, users['gestor'].id).id == ready.id
    assert db.scalar(select(func.count()).select_from(FiscalPreparation)) == 1
    assert db.scalar(select(func.count()).select_from(FinancialEntry)) == 1


@pytest.mark.parametrize('status', ['Aguardando', 'Agendado', 'Cancelado', 'Faltou', 'Ausente', 'Não realizado', 'no-show'])
def test_non_performed_blocked(db, source, status):
    source[0].status = status
    db.flush()
    assert 'service_not_performed' in nfse.evaluate(db, source[0].id, 'mock')['blocking_reasons']


@pytest.mark.parametrize('mutation,reason', [
    ('missing_finance', 'financial_entry_missing'), ('unlinked', 'financial_entry_missing'),
    ('unreceived', 'financial_revenue_not_received_or_invalid'),
    ('cancelled_money', 'financial_revenue_not_received_or_invalid'),
    ('imprecise', 'service_date_missing_or_imprecise'),
    ('missing_date', 'service_date_missing_or_imprecise'),
    ('future', 'service_date_in_future'), ('unknown_flow', 'commercial_flow_unsupported'),
    ('pastore', 'commercial_flow_unsupported'),
])
def test_eligibility_fail_closed(db, source, mutation, reason):
    exam, entry = source
    if mutation == 'missing_finance': db.delete(entry)
    elif mutation == 'unlinked': entry.spirometry_exam_id = None
    elif mutation == 'unreceived': entry.status = 'Pendente'
    elif mutation == 'cancelled_money': entry.status = 'Cancelado'
    elif mutation == 'imprecise': exam.data_exame_dia_assumido = True
    elif mutation == 'missing_date': exam.data_exame = None
    elif mutation == 'future': exam.data_exame = date.today() + timedelta(days=1)
    elif mutation == 'unknown_flow': exam.modalidade = None
    elif mutation == 'pastore': exam.modalidade = 'clinica_parceira'
    db.flush()
    assert reason in nfse.evaluate(db, exam.id, 'mock')['blocking_reasons']


def test_missing_stable_id_and_no_identity_linkage(db, users, source, settings):
    with pytest.raises(HTTPException) as error:
        nfse.prepare(db, '00000000-0000-0000-0000-000000000000', settings, users['gestor'].id)
    assert error.value.detail['codigo'] == 'missing_stable_exam_link'
    source[1].spirometry_exam_id = None
    source[1].descricao = source[0].public_code  # text is not a relationship
    db.flush()
    assert 'financial_entry_missing' in nfse.evaluate(db, source[0].id, 'mock')['blocking_reasons']


def test_missing_incomplete_outside_policy(db, users, source):
    exam = source[0]
    assert 'policy_missing' in nfse.evaluate(db, exam.id, 'mock')['blocking_reasons']
    nfse.create_policy(db, policy_payload(configuration={}, validation_state='draft'), users['admin'].id)
    assert 'policy_incomplete' in nfse.evaluate(db, exam.id, 'mock')['blocking_reasons']
    exam.data_exame = date(2025, 1, 1)
    db.flush()
    assert 'policy_outside_validity' in nfse.evaluate(db, exam.id, 'mock')['blocking_reasons']


def test_policy_lookup_and_ambiguous_versions(db, users, source):
    old = nfse.create_policy(db, policy_payload(effective_to='2026-06-30'), users['admin'].id)
    new = nfse.create_policy(db, policy_payload(version='SYNTH-v2', effective_from='2026-07-01'), users['admin'].id)
    assert nfse.evaluate(db, source[0].id, 'mock')['policy_id'] == new.id
    source[0].data_exame = date(2026, 6, 30)
    assert nfse.evaluate(db, source[0].id, 'mock')['policy_id'] == old.id
    nfse.create_policy(db, policy_payload(version='SYNTH-overlap'), users['admin'].id)
    assert 'policy_ambiguous' in nfse.evaluate(db, source[0].id, 'mock')['blocking_reasons']


def test_direct_and_pastore_policy_cannot_enable_partner(db, users, source):
    source[0].modalidade = 'cowork'
    nfse.create_policy(db, policy_payload(flow='DIRECT'), users['admin'].id)
    assert not nfse.evaluate(db, source[0].id, 'mock')['blocking_reasons']
    source[0].modalidade = 'clinica_parceira'
    nfse.create_policy(db, policy_payload(version='SYNTH-PASTORE', flow='PASTORE_A'), users['admin'].id)
    assert 'commercial_flow_unsupported' in nfse.evaluate(db, source[0].id, 'mock')['blocking_reasons']


def test_snapshot_immutable_and_reprepare_stale_money(db, users, source, settings, ready):
    old = nfse.latest_preparation(db, ready.id)
    source[1].valor = Decimal('140.00')
    db.commit()
    assert issue(db, users, settings, ready).state == 'blocked'
    assert not events(db, ready)
    nfse.prepare(db, source[0].id, settings, users['gestor'].id)
    assert old.amount_snapshot == Decimal('123.45')
    assert nfse.latest_preparation(db, ready.id).amount_snapshot == Decimal('140.00')
    assert issue(db, users, settings, ready).state == 'simulated'


def test_mock_issue_idempotent_and_audited(db, users, settings, ready):
    assert issue(db, users, settings, ready).state == 'simulated'
    assert issue(db, users, settings, ready).state == 'simulated'
    rows = events(db, ready)
    assert len(rows) == 2
    assert rows[0].phase == 'started' and rows[0].completed_at is None
    assert rows[1].phase == 'completed' and rows[1].completed_at is not None
    assert rows[0].operation_id == rows[1].operation_id
    logs = db.scalars(select(AuditLog).where(AuditLog.acao.like('fiscal.%'))).all()
    assert {'fiscal.prepared', 'fiscal.issue_started', 'fiscal.issue_completed'} <= {x.acao for x in logs}
    assert all(x.user_id and x.ts_utc for x in logs)
    assert all('cpf' not in json.dumps(x.detalhes) for x in logs)
    assert nfse.serialize_document(db, ready)['fiscal_validity'] is False


class TimeoutProvider(MockNfseProvider):
    def __init__(self, query_result=Outcome.UNCERTAIN):
        self.calls = 0
        self.query_result = query_result
    def issue(self, request):
        self.calls += 1
        raise TimeoutError('do not persist private provider error')
    def query(self, request, operation):
        if self.query_result == Outcome.SIMULATED:
            return ProviderResult(self.query_result, 'MOCK-' + request.document_id)
        return ProviderResult(self.query_result)


@pytest.mark.parametrize('outcome,state', [(Outcome.UNCERTAIN, 'uncertain'),
                                         (Outcome.SIMULATED, 'simulated'),
                                         (Outcome.NOT_FOUND, 'failed')])
def test_uncertain_no_blind_retry_and_reconciliation(db, users, settings, ready, outcome, state):
    provider = TimeoutProvider(outcome)
    assert issue(db, users, settings, ready, provider=provider).state == 'uncertain'
    assert issue(db, users, settings, ready, provider=provider).state == 'uncertain'
    with pytest.raises(HTTPException):
        nfse.operate(db, ready.id, 'issue', 'new-key-no-retry', settings, users['gestor'].id,
                     reprocess=True, provider=provider)
    assert provider.calls == 1
    nfse.operate(db, ready.id, 'reconcile', 'query-key-1', settings, users['gestor'].id, provider=provider)
    assert ready.state == state
    assert len(events(db, ready)) == 4
    if state == 'failed':
        with pytest.raises(HTTPException):
            nfse.operate(db, ready.id, 'issue', 'normal-retry', settings, users['gestor'].id)
        nfse.operate(db, ready.id, 'issue', 'explicit-safe-retry', settings, users['gestor'].id, reprocess=True)
        assert ready.state == 'simulated'
        assert len(events(db, ready)) == 6
    assert 'private provider error' not in str([x.error_code for x in events(db, ready)])


def test_started_event_committed_before_provider_and_crash_blocks(db, engine, users, settings, ready):
    class Crash(MockNfseProvider):
        def issue(self, request):
            with sessionmaker(bind=engine)() as observer:
                assert observer.scalar(select(func.count()).select_from(FiscalAttempt)) == 1
                assert observer.get(FiscalDocument, request.document_id).state == 'issuing'
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        issue(db, users, settings, ready, provider=Crash())
    assert ready.state == 'issuing' and len(events(db, ready)) == 1
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, ready.id, 'reconcile', 'crash-query', settings, users['gestor'].id)
    assert error.value.detail['codigo'] == 'operation_in_progress'


@pytest.mark.parametrize('model', [FiscalPolicy, FiscalPreparation, FiscalAttempt])
@pytest.mark.parametrize('action', ['update', 'delete'])
def test_append_only_orm(db, users, settings, ready, model, action):
    issue(db, users, settings, ready)
    row = db.scalars(select(model)).first()
    if action == 'delete': db.delete(row)
    elif model == FiscalPolicy: row.version = 'overwrite'
    elif model == FiscalPreparation: row.amount_snapshot = Decimal('999')
    else: row.outcome = 'overwrite'
    with pytest.raises(ValueError, match='fiscal'):
        db.flush()
    db.rollback()


def test_cancellation_mock_and_no_reissue(db, users, settings, ready):
    issue(db, users, settings, ready)
    nfse.operate(db, ready.id, 'cancel', 'cancel-mock-1', settings, users['gestor'].id)
    assert ready.state == 'cancelled'
    nfse.operate(db, ready.id, 'cancel', 'cancel-mock-1', settings, users['gestor'].id)
    assert len(events(db, ready)) == 4
    with pytest.raises(HTTPException):
        nfse.operate(db, ready.id, 'issue', 'issue-after-cancel', settings, users['gestor'].id)


@pytest.mark.parametrize('environment', ['restricted', 'production'])
@pytest.mark.parametrize('real,path', [(False, None), (True, None), (True, '/tmp/nonexistent-private-certificate')])
def test_real_provider_fail_closed(environment, real, path):
    settings = Settings(nfse_enabled=True, nfse_environment=environment,
                        nfse_real_enabled=real, nfse_credentials_path=path)
    with pytest.raises(HTTPException) as error:
        get_provider(settings)
    assert error.value.status_code == 503


def test_default_off():
    settings = Settings()
    assert not settings.nfse_enabled and not settings.nfse_real_enabled
    assert settings.nfse_environment == 'mock'
    with pytest.raises(HTTPException): get_provider(settings)


@pytest.fixture
def fiscal_enabled(monkeypatch):
    monkeypatch.setenv('M15_NFSE_ENABLED', 'true')
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_api_rbac_privacy_and_batch(fiscal_enabled, client, auth, source):
    for role in ['leitura', 'operacional', 'gestor']:
        assert client.post('/api/v1/fiscal/politicas', json=policy_payload().model_dump(mode='json'),
                           headers=auth(role)).status_code == 403
    assert client.post('/api/v1/fiscal/politicas', json=policy_payload().model_dump(mode='json'),
                       headers=auth()).status_code == 201
    for role in ['leitura', 'operacional']:
        assert client.post('/api/v1/fiscal/preparar', json={'spirometry_exam_id': source[0].id}, headers=auth(role)).status_code == 403
    response = client.post('/api/v1/fiscal/preparar', json={'spirometry_exam_id': source[0].id}, headers=auth('gestor'))
    assert response.status_code == 200, response.text
    doc = response.json()
    for action in ['emitir-mock', 'cancelar-mock', 'reprocessar', 'reconciliar']:
        for role in ['leitura', 'operacional']:
            assert client.post(f"/api/v1/fiscal/documentos/{doc['id']}/{action}",
                               json={'idempotency_key': 'synthetic-key'}, headers=auth(role)).status_code == 403
    assert client.get('/api/v1/fiscal/documentos', headers=auth('leitura')).status_code == 200
    assert client.get('/api/v1/fiscal/documentos').status_code == 401
    response = client.post('/api/v1/fiscal/emitir-pendentes', json={
        'idempotency_key': 'synthetic-batch-1', 'document_ids': [doc['id'], doc['id']]}, headers=auth('gestor'))
    assert response.status_code == 200, response.text
    assert len(response.json()['itens']) == 1
    assert response.json()['itens'][0]['state'] == 'simulated'
    response = client.get(f"/api/v1/fiscal/documentos/{doc['id']}/tentativas", headers=auth('leitura'))
    assert response.json()['total'] == 2
    assert 'nome_completo' not in response.text and 'cpf' not in response.text


def test_api_closed_schema_and_immutable_versions(fiscal_enabled, client, auth):
    payload = policy_payload().model_dump(mode='json')
    assert client.post('/api/v1/fiscal/politicas', json=payload, headers=auth()).status_code == 201
    payload['configuration']['tax_rate'] = '99'
    assert client.post('/api/v1/fiscal/politicas', json=payload, headers=auth()).status_code == 409
    payload['version'] = 'INCOMPLETE'
    payload['configuration'] = {}
    assert client.post('/api/v1/fiscal/politicas', json=payload, headers=auth()).status_code == 422
    assert client.post('/api/v1/fiscal/preparar', json={
        'spirometry_exam_id': '0'*36, 'patient_name': 'Pessoa Sintética'}, headers=auth()).status_code == 422


@pytest.mark.parametrize('field', list(policy_payload().configuration.model_dump()))
def test_every_policy_field_required_for_validation(field):
    from pydantic import ValidationError
    payload = policy_payload().model_dump(mode='json')
    payload['configuration'].pop(field)
    with pytest.raises(ValidationError):
        PolicyCreate.model_validate(payload)


@pytest.mark.parametrize('result', [None, ProviderResult(Outcome.NOT_FOUND),
    ProviderResult(Outcome.SIMULATED),
    ProviderResult(Outcome.SIMULATED, 'unsafe-external-identifier'),
    ProviderResult(Outcome.SIMULATED, 'MOCK-00000000-0000-0000-0000-000000000000')])
def test_invalid_provider_responses_become_uncertain(db, users, settings, ready, result):
    class Invalid(MockNfseProvider):
        def issue(self, request): return result
    assert issue(db, users, settings, ready, provider=Invalid()).state == 'uncertain'
    assert events(db, ready)[-1].reconciliation_required


def test_rejection_explicit_retry_and_idempotency_payload_conflict(db, users, settings, ready):
    class Rejected(MockNfseProvider):
        def issue(self, request): return ProviderResult(Outcome.REJECTED)
    assert issue(db, users, settings, ready, provider=Rejected()).state == 'failed'
    with pytest.raises(HTTPException) as error:
        issue(db, users, settings, ready, reprocess=True)
    assert error.value.detail['codigo'] == 'idempotency_conflict'
    nfse.operate(db, ready.id, 'issue', 'explicit-reprocess', settings, users['gestor'].id, reprocess=True)
    assert ready.state == 'simulated' and len(events(db, ready)) == 4


def test_crash_reconciliation_after_window(db, users, settings, ready, monkeypatch):
    now = nfse.utcnow()
    class Crash(MockNfseProvider):
        def issue(self, request): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        issue(db, users, settings, ready, provider=Crash())
    monkeypatch.setattr(nfse, 'utcnow', lambda: now + timedelta(minutes=6))
    assert nfse.operate(db, ready.id, 'reconcile', 'query-after-crash', settings,
                        users['gestor'].id).state == 'simulated'
    assert len(events(db, ready)) == 3  # unmatched start retained, not rewritten


def test_policy_history_preserved_and_source_change_blocks(db, users, settings, source, ready):
    prep = nfse.latest_preparation(db, ready.id)
    nfse.create_policy(db, policy_payload(version='SYNTH-overlap-2'), users['admin'].id)
    assert issue(db, users, settings, ready).state == 'blocked'
    assert 'policy_ambiguous' in ready.blocking_reasons
    assert db.get(FiscalPolicy, prep.policy_id).version == 'SYNTH-MOCK-v1'
    assert prep.amount_snapshot == Decimal('123.45')


def test_api_default_off(client, auth):
    assert client.get('/api/v1/fiscal/status', headers=auth()).status_code == 503


@pytest.mark.parametrize('tipo,categoria', [
    ('repasse', 'Repasse ao médico'), ('despesa', 'Repasse ao médico'),
    ('repasse', 'Espirometria'), ('despesa', 'Espirometria'),
    ('receita', 'Repasse ao médico'), ('receita', 'Consulta'),
    ('receita', 'Ajuste'), ('receita', 'Recebimento parceiro'),
])
def test_physician_payable_and_wrong_revenue_category_blocked(db, users, source, settings, tipo, categoria):
    source[1].tipo, source[1].categoria = tipo, categoria
    db.commit()
    nfse.create_policy(db, policy_payload(), users['admin'].id)
    doc = nfse.prepare(db, source[0].id, settings, users['gestor'].id)
    prep = nfse.latest_preparation(db, doc.id)
    assert doc.state == 'blocked'
    assert 'financial_entry_missing' in doc.blocking_reasons
    assert prep.financial_entry_id is None and prep.amount_snapshot is None


@pytest.mark.parametrize('category', [None, ' esPIROMETRIA ', 'Espi\u200brometria'])
def test_reuses_canonical_ledger_category_contract(db, users, source, settings, category):
    source[1].categoria = category
    db.commit()
    nfse.create_policy(db, policy_payload(), users['admin'].id)
    doc = nfse.prepare(db, source[0].id, settings, users['gestor'].id)
    assert doc.eligibility == 'eligible'
    assert nfse.latest_preparation(db, doc.id).financial_entry_id == source[1].id


def test_explicit_revenue_selected_among_payables_and_other_exams(db, users, source, settings, ready):
    exam, revenue = source
    other = SpirometryExam(public_code='ESP-OTHER', person_id=exam.person_id,
                           status='Realizado', data_exame=exam.data_exame, modalidade='residencial')
    db.add(other)
    db.flush()
    db.add_all([
        FinancialEntry(public_code='LAN-PAYABLE', tipo='repasse', categoria='Repasse ao médico',
                       valor=Decimal('80'), status='Pago', spirometry_exam_id=exam.id),
        FinancialEntry(public_code='LAN-EXPENSE', tipo='despesa', categoria='Espirometria',
                       valor=Decimal('40'), status='Pago', spirometry_exam_id=exam.id),
        FinancialEntry(public_code='LAN-OTHER', tipo='receita', categoria='Espirometria',
                       valor=Decimal('500'), status='Recebido', spirometry_exam_id=other.id),
    ])
    db.commit()
    data = nfse.evaluate(db, exam.id, 'mock')
    assert not data['blocking_reasons']
    assert data['financial_entry_id'] == revenue.id
    assert data['amount_snapshot'] == Decimal('123.45')
    revenue.spirometry_exam_id = None
    db.commit()
    data = nfse.evaluate(db, exam.id, 'mock')
    assert data['financial_entry_id'] is None
    assert 'financial_entry_missing' in data['blocking_reasons']


def test_incoherent_financial_relationship_blocked(db, users, source, settings):
    from app.models import Consultation
    consultation = Consultation(public_code='CON-NFSE', person_id=source[0].person_id)
    db.add(consultation)
    db.flush()
    source[1].consultation_id = consultation.id
    db.commit()
    nfse.create_policy(db, policy_payload(), users['admin'].id)
    doc = nfse.prepare(db, source[0].id, settings, users['gestor'].id)
    assert 'financial_link_incoherent' in doc.blocking_reasons
    assert nfse.latest_preparation(db, doc.id).amount_snapshot is None


def test_failed_reprepare_cannot_bypass_explicit_retry(db, users, source, settings, ready):
    class Rejected(MockNfseProvider):
        def issue(self, request): return ProviderResult(Outcome.REJECTED)
    issue(db, users, settings, ready, provider=Rejected())
    source[1].status = 'Pendente'
    db.commit()
    assert nfse.prepare(db, source[0].id, settings, users['gestor'].id).state == 'blocked'
    source[1].status = 'Recebido'
    db.commit()
    assert nfse.prepare(db, source[0].id, settings, users['gestor'].id).state == 'failed'
    latest_audit = db.scalar(select(AuditLog).where(AuditLog.acao == 'fiscal.prepared')
                            .order_by(AuditLog.id.desc()).limit(1))
    assert latest_audit.detalhes['status'] == 'failed'
    with pytest.raises(HTTPException):
        nfse.operate(db, ready.id, 'issue', 'cannot-bypass-retry', settings, users['gestor'].id)
    assert nfse.operate(db, ready.id, 'issue', 'explicit-retry-after-prepare', settings,
                        users['gestor'].id, reprocess=True).state == 'simulated'


def test_description_excludes_operational_and_clinical_free_text(db, source):
    source[0].observacao = 'SYNTHETIC_PRIVATE_EXAM_TEXT'
    source[1].descricao = 'SYNTHETIC_PRIVATE_FINANCE_TEXT'
    data = nfse.evaluate(db, source[0].id, 'mock')
    assert 'SYNTHETIC_PRIVATE' not in data['description']


@pytest.mark.parametrize('outcome', ['simulated', ['invalid']])
def test_malformed_outcome_is_uncertain(db, users, settings, ready, outcome):
    class Invalid(MockNfseProvider):
        def issue(self, request):
            return ProviderResult(outcome, 'MOCK-' + request.document_id)
    assert issue(db, users, settings, ready, provider=Invalid()).state == 'uncertain'
    assert events(db, ready)[-1].outcome == 'uncertain'


def test_production_gate_cannot_be_bypassed_by_injected_mock(db, users, settings, ready):
    settings.nfse_environment = 'production'
    with pytest.raises(HTTPException) as error:
        issue(db, users, settings, ready, provider=MockNfseProvider())
    assert error.value.status_code == 503
    assert not events(db, ready)


def test_medical_role_cannot_read_or_mutate_fiscal_queue(fiscal_enabled, client, db, source):
    from tests.conftest import _make_user
    from app.security import issue_token
    doctor = _make_user(db, 'physician-fiscal@synthetic.invalid', 'medico')
    headers = {'Authorization': 'Bearer ' + issue_token(doctor.id, doctor.password_hash)}
    for path in ['status', 'politicas', 'documentos']:
        assert client.get('/api/v1/fiscal/' + path, headers=headers).status_code == 403
    assert client.post('/api/v1/fiscal/preparar', headers=headers,
                       json={'spirometry_exam_id': source[0].id}).status_code == 403
    assert client.post('/api/v1/fiscal/politicas', headers=headers,
                       json=policy_payload().model_dump(mode='json')).status_code == 403


def test_cancel_timeout_queries_original_operation_before_retry(db, users, settings, ready):
    issue(db, users, settings, ready)
    requests = []
    class UncertainCancel(MockNfseProvider):
        def cancel(self, request):
            requests.append(request)
            raise TimeoutError('SYNTHETIC_PRIVATE_CANCEL_ERROR')
        def query(self, request, operation):
            assert operation == 'cancel'
            assert request.operation_id == requests[0].operation_id
            assert request.preparation_id == requests[0].preparation_id
            return ProviderResult(Outcome.CANCELLED, 'MOCK-' + request.document_id)
    provider = UncertainCancel()
    actor = users['gestor'].id
    assert nfse.operate(db, ready.id, 'cancel', 'uncertain-cancel', settings, actor,
                        provider=provider).state == 'uncertain'
    with pytest.raises(HTTPException):
        nfse.operate(db, ready.id, 'cancel', 'blind-cancel-retry', settings, actor,
                     provider=provider)
    assert len(requests) == 1
    assert nfse.operate(db, ready.id, 'reconcile', 'query-cancellation', settings, actor,
                        provider=provider).state == 'cancelled'
    assert len(events(db, ready)) == 6
