"""Offline automation foundation — new read-only/offline endpoints:
``/fiscal/fila-resumo``, ``/fiscal/documentos/{id}/preflight``, and the
extended (but backward-compatible) ``/fiscal/status`` readiness block.
"""
from datetime import date
from decimal import Decimal

from app.models import Person, SpirometryExam
import tests.test_nfse_foundation as _foundation
from tests.test_nfse_foundation import policy_payload

fiscal_enabled = _foundation.fiscal_enabled  # pytest fixture reuse


def test_status_gains_extended_readiness_without_breaking_old_shape(fiscal_enabled, client, auth):
    response = client.get('/api/v1/fiscal/status', headers=auth())
    assert response.status_code == 200, response.text
    block = response.json()['restricted_provider_foundation']
    # Pre-existing contract (M27) — unchanged.
    assert block['layout_version'] == 'restricted-v1.01-20260727'
    assert block['network_gate_enabled'] is False
    assert block['operational_network_possible'] is False
    assert set(block['missing_configuration']) == {
        'restricted_base_url', 'restricted_certificate_path',
        'restricted_certificate_password', 'restricted_network_gate_disabled',
    }
    # New, additive.
    assert block['readiness']['environment'] == 'restricted'
    assert block['readiness']['certificate_configured'] is False
    assert 'national_dps_configuration_not_defined_for_any_real_document' in block['readiness']['blockers']
    assert block['production_readiness']['all_satisfied'] is False
    by_name = {g['name']: g for g in block['production_readiness']['gates']}
    assert by_name['production_endpoint_correct']['satisfied'] is False


def test_fila_resumo_rbac_and_shape(fiscal_enabled, client, auth):
    assert client.get('/api/v1/fiscal/fila-resumo').status_code == 401
    response = client.get('/api/v1/fiscal/fila-resumo', headers=auth('leitura'))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        'environment': 'mock', 'total': 0, 'eligible': 0, 'blocked_total': 0,
        'blocked_breakdown': body['blocked_breakdown'],
        'issuing': 0, 'simulated': 0, 'failed': 0, 'uncertain': 0,
        'reconciling': 0, 'cancelled': 0, 'reconciliation_required': 0,
    }


def test_fila_resumo_reflects_prepared_documents(fiscal_enabled, client, auth, db, users):
    from app.models import FinancialEntry

    p = Person(public_code='PES-FR1', nome_completo='Pessoa Fila', nome_normalizado='pessoa fila')
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code='ESP-FR1', person_id=p.id, status='Realizado',
                       data_exame=date(2026, 8, 10), data_exame_precisao='dia',
                       modalidade='residencial', broncodilatador=True)
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code='LAN-FR1', tipo='receita', categoria='Espirometria',
                       valor=Decimal('220.00'), status='Recebido', spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    client.post('/api/v1/fiscal/politicas', json=policy_payload(
        version='SYNTH-FR1', environment='mock', flow='HOME').model_dump(mode='json'), headers=auth())
    resp = client.post('/api/v1/fiscal/preparar', json={'spirometry_exam_id': e.id}, headers=auth('gestor'))
    assert resp.json()['eligibility'] == 'eligible'

    summary = client.get('/api/v1/fiscal/fila-resumo', headers=auth('leitura')).json()
    assert summary['eligible'] == 1
    assert summary['total'] == 1


def test_preflight_endpoint_rbac_and_offline_default_blocker(fiscal_enabled, client, auth, db, users):
    from app.models import FinancialEntry
    p = Person(public_code='PES-PF1', nome_completo='Pessoa Preflight', nome_normalizado='pessoa preflight')
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code='ESP-PF1', person_id=p.id, status='Realizado',
                       data_exame=date(2026, 8, 10), data_exame_precisao='dia',
                       modalidade='residencial', broncodilatador=True)
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code='LAN-PF1', tipo='receita', categoria='Espirometria',
                       valor=Decimal('220.00'), status='Recebido', spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    client.post('/api/v1/fiscal/politicas', json=policy_payload(
        version='SYNTH-PF1', environment='mock', flow='HOME').model_dump(mode='json'), headers=auth())
    doc = client.post('/api/v1/fiscal/preparar', json={'spirometry_exam_id': e.id},
                      headers=auth('gestor')).json()

    for role in ['leitura', 'operacional']:
        assert client.post(f"/api/v1/fiscal/documentos/{doc['id']}/preflight",
                           headers=auth(role)).status_code == 403
    assert client.post(f"/api/v1/fiscal/documentos/{doc['id']}/preflight").status_code == 401

    response = client.post(f"/api/v1/fiscal/documentos/{doc['id']}/preflight", headers=auth('gestor'))
    assert response.status_code == 200, response.text
    body = response.json()
    # A real document today has no national DPS configuration source — the
    # preflight must stop there, never invent or guess one.
    assert body['status'] == 'blocked'
    assert body['stage_reached'] == 'national_config'
    assert body['blockers'] == ['national_dps_configuration_not_defined_for_any_real_document']
    assert body['staged_artifacts'] == []
