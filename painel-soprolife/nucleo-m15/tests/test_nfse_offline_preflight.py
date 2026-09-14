"""Offline automation foundation — the deterministic preflight pipeline
(FiscalDocument → ... → READY_TO_SEND or an exact blocker) and the batch
queue summary used by the Command Center's "Emitir pendentes" screen.

Every certificate is synthetic (``generate_synthetic_test_certificate``).
Nothing here calls a transport — ``preflight.py`` has no transport import at
all, which is itself part of what these tests lean on.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.config import Settings
from app.models import FinancialEntry, FiscalArtifact, Person, SpirometryExam
from app.services import nfse
from app.services.nfse_national.config import NationalDpsConfiguration
from app.services.nfse_national.dps_builder import Recipient
from app.services.nfse_national.preflight import Stage, run_offline_preflight
from app.services.nfse_national.signer import generate_synthetic_test_certificate, load_pkcs12_certificate
from tests.test_nfse_foundation import policy_payload


@pytest.fixture
def restricted_settings():
    return Settings(nfse_enabled=True, nfse_environment='restricted')


@pytest.fixture
def restricted_source(db, users):
    p = Person(public_code='PES-PRE', nome_completo='Pessoa Sintética Preflight',
              nome_normalizado='pessoa sintetica preflight')
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code='ESP-PRE', person_id=p.id, status='Realizado',
                       data_exame=date(2026, 8, 10), data_exame_precisao='dia',
                       modalidade='residencial', broncodilatador=True,
                       municipio_atendimento_ibge='3304557')
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code='LAN-PRE', tipo='receita', categoria='Espirometria',
                       valor=Decimal('220.00'), status='Recebido', spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    return e, f


@pytest.fixture
def restricted_ready(db, users, restricted_source, restricted_settings):
    nfse.create_policy(db, policy_payload(version='SYNTH-RESTRICTED-HOME', environment='restricted',
                                          flow='HOME'), users['admin'].id)
    return nfse.prepare(db, restricted_source[0].id, restricted_settings, users['gestor'].id)


@pytest.fixture
def national_config():
    return NationalDpsConfiguration(
        version='SYNTH-RESTRICTED-v1', layout_version='restricted-v1.01-20260727',
        issuer_cnpj='11222333000181', issuer_name='SOPROLIFE SAUDE LTDA (SINTETICO)',
        issuer_municipio_ibge='3304557', issuer_op_simp_nac=3, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional='140501',
        trib_issqn=1, tp_ret_issqn=1,
        amount_basis='financial_entry.valor', competence_rule='service_date',
        own_revenue_confirmed=True, validation_reference='SYNTHETIC-ONLY',
    )


@pytest.fixture
def recipient():
    return Recipient(nome='Paciente Sintético Um', sem_nif_motivo=1)


@pytest.fixture
def synthetic_certificate():
    p12_bytes, password = generate_synthetic_test_certificate()
    return load_pkcs12_certificate(p12_bytes, password)


def test_blocked_before_preparation_exists(db, restricted_settings, restricted_source, users):
    identity = {'exam_id': restricted_source[0].id, 'environment': 'restricted'}
    from app.services.idempotency import payload_fingerprint
    from app.models import FiscalDocument
    doc = FiscalDocument(spirometry_exam_id=restricted_source[0].id, environment='restricted',
                         created_by=users['gestor'].id, idempotency_key=payload_fingerprint(identity),
                         idempotency_fingerprint=payload_fingerprint(identity), state='pending',
                         eligibility='eligible')
    db.add(doc)
    db.commit()
    result = run_offline_preflight(db, doc.id, restricted_settings, users['gestor'].id)
    assert result.status == 'blocked'
    assert result.stage_reached == Stage.PREPARATION
    assert 'preparation_required' in result.blockers


def test_blocked_when_document_not_eligible(db, users, restricted_source, restricted_settings):
    # No policy created at all: prepare() will mark it blocked.
    doc = nfse.prepare(db, restricted_source[0].id, restricted_settings, users['gestor'].id)
    assert doc.eligibility == 'blocked'
    result = run_offline_preflight(db, doc.id, restricted_settings, users['gestor'].id)
    assert result.status == 'blocked'
    assert result.stage_reached == Stage.ELIGIBILITY
    assert 'policy_missing' in result.blockers


def test_blocked_at_national_config_when_none_supplied(db, users, restricted_ready, restricted_settings):
    result = run_offline_preflight(db, restricted_ready.id, restricted_settings, users['gestor'].id)
    assert result.status == 'blocked'
    assert result.stage_reached == Stage.NATIONAL_CONFIG
    assert result.blockers == ['national_dps_configuration_not_defined_for_any_real_document']


def test_blocked_at_signature_without_certificate(db, users, restricted_ready, restricted_settings,
                                                   national_config, recipient):
    result = run_offline_preflight(db, restricted_ready.id, restricted_settings, users['gestor'].id,
                                   national_config=national_config, recipient=recipient)
    assert result.status == 'blocked'
    assert result.stage_reached == Stage.SIGNATURE
    assert result.blockers == ['certificate_not_supplied']


def test_blocked_at_artifact_staging_when_storage_not_configured(db, users, restricted_ready,
                                                                  restricted_settings, national_config,
                                                                  recipient, synthetic_certificate):
    result = run_offline_preflight(db, restricted_ready.id, restricted_settings, users['gestor'].id,
                                   national_config=national_config, recipient=recipient,
                                   certificate=synthetic_certificate)
    assert result.status == 'blocked'
    assert result.stage_reached == Stage.ARTIFACT_STAGING


def test_full_chain_reaches_ready_to_send_and_stages_artifacts(
        db, users, restricted_ready, national_config, recipient, synthetic_certificate, tmp_path):
    settings = Settings(nfse_enabled=True, nfse_environment='restricted',
                        nfse_fiscal_artifacts_dir=tmp_path / 'fiscal-artifacts')
    result = run_offline_preflight(db, restricted_ready.id, settings, users['gestor'].id,
                                   national_config=national_config, recipient=recipient,
                                   certificate=synthetic_certificate)
    assert result.status == 'ready_to_send'
    assert result.stage_reached == Stage.READY
    assert result.blockers == []
    assert result.request_fingerprint is not None
    kinds = {a.kind for a in result.staged_artifacts}
    assert kinds == {'dps_unsigned_xml', 'dps_signed_xml'}
    for artifact in result.staged_artifacts:
        path = settings.resolved_fiscal_artifacts_storage_dir() / artifact.relative_path
        assert path.is_file()
        assert path.stat().st_size == artifact.size_bytes
    rows = db.query(FiscalArtifact).filter_by(document_id=restricted_ready.id).all()
    assert len(rows) == 2
    assert all(row.attempt_id is None for row in rows)


def test_preflight_never_mutates_document_state(db, users, restricted_ready, national_config,
                                                 recipient, synthetic_certificate, tmp_path):
    settings = Settings(nfse_enabled=True, nfse_environment='restricted',
                        nfse_fiscal_artifacts_dir=tmp_path / 'fiscal-artifacts')
    before_state = restricted_ready.state
    run_offline_preflight(db, restricted_ready.id, settings, users['gestor'].id,
                          national_config=national_config, recipient=recipient,
                          certificate=synthetic_certificate)
    refreshed = nfse.get_document(db, restricted_ready.id)
    assert refreshed.state == before_state  # still "pending" — preflight never issues anything


def test_preparation_stale_after_amount_changes(db, users, restricted_ready, restricted_settings,
                                                restricted_source):
    entry = restricted_source[1]
    entry.valor = Decimal('999.99')
    db.commit()
    result = run_offline_preflight(db, restricted_ready.id, restricted_settings, users['gestor'].id)
    assert result.status == 'blocked'
    assert result.stage_reached == Stage.PREPARATION
    assert 'preparation_stale' in result.blockers


# --------------------------------------------------------------- queue summary


def test_queue_summary_categorizes_blocked_reasons(db, users, restricted_settings):
    def make_exam(code, **overrides):
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

    # No policy at all yet: this one will be "blocked_by_fiscal_policy".
    e1 = make_exam('Q1')
    nfse.prepare(db, e1.id, restricted_settings, users['gestor'].id)

    # A partner flow (Pastore/SPLIT) is structurally unsupported: "blocked_by_partner_model".
    e2 = make_exam('Q2', modalidade='clinica_parceira')
    nfse.prepare(db, e2.id, restricted_settings, users['gestor'].id)

    # An eligible one: policy present, ledger entry present and valid.
    nfse.create_policy(db, policy_payload(version='SYNTH-RESTRICTED-Q3', environment='restricted',
                                          flow='HOME'), users['admin'].id)
    e3 = make_exam('Q3')
    f3 = FinancialEntry(public_code='LAN-Q3', tipo='receita', categoria='Espirometria',
                        valor=Decimal('220.00'), status='Recebido', spirometry_exam_id=e3.id,
                        data_competencia=date(2026, 9, 1))
    db.add(f3)
    db.commit()
    nfse.prepare(db, e3.id, restricted_settings, users['gestor'].id)

    summary = nfse.queue_summary(db, 'restricted')
    assert summary['eligible'] == 1
    assert summary['blocked_breakdown']['blocked_by_fiscal_policy'] == 1
    assert summary['blocked_breakdown']['blocked_by_partner_model'] == 1
    assert summary['blocked_total'] == 2
    assert summary['total'] == 3

