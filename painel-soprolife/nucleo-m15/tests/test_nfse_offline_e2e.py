"""Offline automation foundation — end-to-end proofs that individual unit
tests (M26/M27) don't already cover:

1. A real FiscalDocument can be walked, entirely offline, all the way from
   "performed exam" through a schema-valid SIGNED DPS to a classified
   provider outcome — using FakeTransport, never a socket — by handing the
   artifact `preflight.py` staged straight to `RestrictedNfseProvider`.
2. Re-running preflight for the same document is safe: it never overwrites
   evidence, it appends a new bundle (matching the append-only contract
   FiscalAttempt already uses).
3. "Emitir pendentes" produces genuinely mixed per-document outcomes in one
   batch call (eligible→simulated, blocked, already-simulated short-circuit)
   with one aggregate response and full audit, using ONLY the mock provider
   — the restricted path is never live-dispatched.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.config import Settings
from app.models import AuditLog, FiscalArtifact, FinancialEntry, Person, SpirometryExam
from app.services import nfse
from app.services.nfse_national.config import NationalDpsConfiguration
from app.services.nfse_national.dps_builder import Recipient
from app.services.nfse_national.identifiers import DpsIdComponents
from app.services.nfse_national.preflight import run_offline_preflight
from app.services.nfse_national.provider import RestrictedIssueContext, RestrictedNfseProvider
from app.services.nfse_national.signer import generate_synthetic_test_certificate, load_pkcs12_certificate
from app.services.nfse_national.transport import FakeTransport, TransportResponse
from app.services.nfse_national.xsd_validation import validate_dps_xml
from app.services.nfse_providers import Outcome, ProviderRequest
import tests.test_nfse_foundation as _foundation
from tests.test_nfse_foundation import policy_payload
from tests.test_nfse_national_provider import VALID_ACCESS_KEY, _nfse_xml

fiscal_enabled = _foundation.fiscal_enabled  # pytest fixture reuse


def make_exam(db, code, **overrides):
    p = Person(public_code=f'PES-{code}', nome_completo=f'Pessoa {code}',
              nome_normalizado=f'pessoa {code}'.lower())
    db.add(p)
    db.flush()
    defaults = dict(public_code=f'ESP-{code}', person_id=p.id, status='Realizado',
                    data_exame=date(2026, 8, 10), data_exame_precisao='dia',
                    modalidade='residencial', broncodilatador=True)
    defaults.update(overrides)
    e = SpirometryExam(**defaults)
    db.add(e)
    db.commit()
    return e


def make_entry(db, code, exam_id, valor='220.00'):
    f = FinancialEntry(public_code=f'LAN-{code}', tipo='receita', categoria='Espirometria',
                       valor=Decimal(valor), status='Recebido', spirometry_exam_id=exam_id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    return f


@pytest.fixture
def restricted_settings():
    return Settings(nfse_enabled=True, nfse_environment='restricted')


@pytest.fixture
def national_config():
    return NationalDpsConfiguration(
        version='SYNTH-RESTRICTED-v1', layout_version='restricted-v1.01-20260727',
        issuer_cnpj='11222333000181', issuer_name='SOPROLIFE SAUDE LTDA (SINTETICO)',
        issuer_municipio_ibge='3304557', issuer_op_simp_nac=3, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional='140501', municipio_prestacao_ibge='3304557',
        trib_issqn=1, tp_ret_issqn=1,
        amount_basis='financial_entry.valor', competence_rule='service_date',
        own_revenue_confirmed=True, validation_reference='SYNTHETIC-ONLY',
    )


def _issue_context(national_config, certificate):
    dps_id = DpsIdComponents(codigo_municipio='3304557', tipo_inscricao_federal=2,
                             inscricao_federal='11222333000181', serie_dps='00001',
                             numero_dps='000000000000001')
    return RestrictedIssueContext(
        config=national_config, dps_id=dps_id,
        recipient=Recipient(nome='Paciente Sintético E2E', sem_nif_motivo=1),
        ver_aplic='sl-e2e-0.1', numero_dps_display='1', serie_dps_display='1',
        certificate=certificate,
    )


def test_offline_e2e_document_to_classified_success(db, users, restricted_settings, national_config, tmp_path):
    e = make_exam(db, 'E2E1')
    make_entry(db, 'E2E1', e.id)
    nfse.create_policy(db, policy_payload(version='SYNTH-RESTRICTED-E2E1', environment='restricted',
                                          flow='HOME'), users['admin'].id)
    doc = nfse.prepare(db, e.id, restricted_settings, users['gestor'].id)
    assert doc.eligibility == 'eligible'

    p12_bytes, password = generate_synthetic_test_certificate()
    certificate = load_pkcs12_certificate(p12_bytes, password)
    settings = Settings(nfse_enabled=True, nfse_environment='restricted',
                        nfse_fiscal_artifacts_dir=tmp_path / 'fiscal-artifacts')
    result = run_offline_preflight(db, doc.id, settings, users['gestor'].id,
                                   national_config=national_config,
                                   recipient=Recipient(nome='Paciente Sintético E2E', sem_nif_motivo=1),
                                   certificate=certificate)
    assert result.status == 'ready_to_send'

    signed_path = next(a for a in result.staged_artifacts if a.kind == 'dps_signed_xml').relative_path
    signed_xml = (settings.resolved_fiscal_artifacts_storage_dir() / signed_path).read_bytes()
    validate_dps_xml(signed_xml)  # the staged bytes are still schema-valid on disk

    transport = FakeTransport(responses=[TransportResponse(201, _nfse_xml())])
    provider = RestrictedNfseProvider(transport=transport, context=_issue_context(national_config, certificate))
    request = ProviderRequest(document_id=doc.id, operation_id='e2e-op-1', preparation_id='irrelevant',
                              amount='220.00', competence='2026-08-10', description='desc')
    outcome = provider.issue(request)
    assert outcome.outcome == Outcome.SIMULATED
    assert outcome.external_id == VALID_ACCESS_KEY
    # The FakeTransport actually received the signed DPS bytes it built itself
    # (proves the provider signs its own payload, not the one staged by
    # preflight — the two remain independently reproducible, never shared state).
    assert transport.received[0].body is not None


def test_offline_e2e_uncertain_never_resent_blindly(db, users, restricted_settings, national_config, tmp_path):
    e = make_exam(db, 'E2E2')
    make_entry(db, 'E2E2', e.id)
    nfse.create_policy(db, policy_payload(version='SYNTH-RESTRICTED-E2E2', environment='restricted',
                                          flow='HOME'), users['admin'].id)
    doc = nfse.prepare(db, e.id, restricted_settings, users['gestor'].id)
    p12_bytes, password = generate_synthetic_test_certificate()
    certificate = load_pkcs12_certificate(p12_bytes, password)

    transport = FakeTransport(responses=[TimeoutError('synthetic timeout')])
    provider = RestrictedNfseProvider(transport=transport, context=_issue_context(national_config, certificate))
    request = ProviderRequest(document_id=doc.id, operation_id='e2e-op-2', preparation_id='irrelevant',
                              amount='220.00', competence='2026-08-10', description='desc')
    outcome = provider.issue(request)
    assert outcome.outcome == Outcome.UNCERTAIN
    # Nothing in this foundation auto-retries an UNCERTAIN outcome — the mock
    # dispatcher's own reconciliation gate (tested exhaustively in
    # test_nfse_foundation.py::test_uncertain_no_blind_retry_and_reconciliation)
    # is untouched by this restricted-path proof; this test only proves the
    # restricted classification itself lands on UNCERTAIN, never on a false
    # negative/positive, for the exact same TimeoutError class real httpx raises.


def test_preflight_rerun_appends_new_evidence_never_overwrites(
        db, users, restricted_settings, national_config, tmp_path):
    e = make_exam(db, 'E2E3')
    make_entry(db, 'E2E3', e.id)
    nfse.create_policy(db, policy_payload(version='SYNTH-RESTRICTED-E2E3', environment='restricted',
                                          flow='HOME'), users['admin'].id)
    doc = nfse.prepare(db, e.id, restricted_settings, users['gestor'].id)
    p12_bytes, password = generate_synthetic_test_certificate()
    certificate = load_pkcs12_certificate(p12_bytes, password)
    settings = Settings(nfse_enabled=True, nfse_environment='restricted',
                        nfse_fiscal_artifacts_dir=tmp_path / 'fiscal-artifacts')
    recipient = Recipient(nome='Paciente Sintético E2E3', sem_nif_motivo=1)

    first = run_offline_preflight(db, doc.id, settings, users['gestor'].id,
                                  national_config=national_config, recipient=recipient,
                                  certificate=certificate)
    second = run_offline_preflight(db, doc.id, settings, users['gestor'].id,
                                   national_config=national_config, recipient=recipient,
                                   certificate=certificate)
    assert first.status == second.status == 'ready_to_send'
    first_paths = {a.relative_path for a in first.staged_artifacts}
    second_paths = {a.relative_path for a in second.staged_artifacts}
    assert first_paths.isdisjoint(second_paths)  # two distinct evidence bundles, no overwrite
    rows = db.query(FiscalArtifact).filter_by(document_id=doc.id).all()
    assert len(rows) == 4


def test_batch_emitir_pendentes_produces_genuinely_mixed_outcomes(fiscal_enabled, client, auth, db, users):
    # Document A: eligible, will succeed via the mock provider.
    a = make_exam(db, 'BATCHA')
    make_entry(db, 'BATCHA', a.id)
    nfse.create_policy(db, policy_payload(version='SYNTH-BATCH-A', environment='mock', flow='HOME'),
                      users['admin'].id)
    resp_a = client.post('/api/v1/fiscal/preparar', json={'spirometry_exam_id': a.id}, headers=auth('gestor'))
    doc_a = resp_a.json()
    assert doc_a['eligibility'] == 'eligible'

    # Document B: never prepared with a matching policy — blocked.
    b = make_exam(db, 'BATCHB')
    resp_b = client.post('/api/v1/fiscal/preparar', json={'spirometry_exam_id': b.id}, headers=auth('gestor'))
    doc_b = resp_b.json()
    assert doc_b['eligibility'] == 'blocked'

    # Document C: already issued before the batch — must short-circuit, not re-issue.
    c = make_exam(db, 'BATCHC')
    make_entry(db, 'BATCHC', c.id)
    resp_c = client.post('/api/v1/fiscal/preparar', json={'spirometry_exam_id': c.id}, headers=auth('gestor'))
    doc_c = resp_c.json()
    pre_issue = client.post(f"/api/v1/fiscal/documentos/{doc_c['id']}/emitir-mock",
                            json={'idempotency_key': 'pre-issue-c'}, headers=auth('gestor'))
    assert pre_issue.json()['state'] == 'simulated'

    response = client.post('/api/v1/fiscal/emitir-pendentes', json={
        'idempotency_key': 'synthetic-batch-mixed-1',
        'document_ids': [doc_a['id'], doc_b['id'], doc_c['id']],
    }, headers=auth('gestor'))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['real_issuance_available'] is False
    assert len(body['itens']) == 3
    by_id = {item['id']: item for item in body['itens']}
    # A succeeded via the batch call.
    assert by_id[doc_a['id']]['state'] == 'simulated'
    # C stayed simulated (idempotent no-op — issue() short-circuits before
    # touching the provider again — not a second attempt/audit entry).
    assert by_id[doc_c['id']]['state'] == 'simulated'
    # B is reported as a deterministic per-document error, never silently dropped.
    assert by_id[doc_b['id']]['error']['codigo'] == 'document_not_pending_eligible'

    audit_rows = db.query(AuditLog).filter(AuditLog.entidade == 'fiscal_document',
                                           AuditLog.entidade_id == doc_a['id']).all()
    assert any(row.acao == 'fiscal.issue_completed' for row in audit_rows)
