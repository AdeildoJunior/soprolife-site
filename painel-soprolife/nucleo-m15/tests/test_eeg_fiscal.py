"""Fiscal EEG: dados sintéticos, provider mock e transporte httpx stub, zero rede."""
from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import select, func
from fastapi import HTTPException
from app.config import Settings, get_settings
from app.models import (EegEnrollment, EegExam, Person, FinancialEntry, FiscalDocument,
                        FiscalPreparation, FiscalAttempt, NationalDpsConfigurationVersion,
                        FiscalArtifact)
from app.services import nfse, nfse_eeg
from app.services.nfse_national import fiscal_config
from app.services.nfse_national.document_description import document_service_description
from app.services.nfse_national.service_description import ServiceDescriptionUndetermined
from app.fiscal_schemas import PolicyCreate
from tests.test_nfse_foundation import policy_payload
from tests.test_nfse_m29_national_config_router import synthetic_configuration_payload
from tests.test_nfse_m66_production_command_center import (
    prod_env, spool, world, worker, GOOD_NAME, synthetic_cpf, envelope)


@pytest.fixture(autouse=True)
def fixed_auth_secret(monkeypatch):
    monkeypatch.setenv('M15_AUTH_SECRET', 'eeg-fiscal-synthetic-test-secret-20261029')
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def eeg_source(db, users):
    p = Person(public_code='PES-EEGFISC', nome_completo=GOOD_NAME,
        nome_normalizado=GOOD_NAME.lower(), cpf=synthetic_cpf(987))
    db.add(p); db.flush()
    v = EegEnrollment(person_id=p.id, criado_por=users['admin'].id)
    db.add(v); db.flush()
    e = EegExam(public_code='EEG-EEGFISC', enrollment_id=v.id, modalidade_codigo='vigilia',
        status='realizado', realizado_em=datetime(2026, 9, 16, 13, tzinfo=timezone.utc),
        municipio_atendimento_ibge='3304557', local_atendimento='Local Sintetico')
    db.add(e); db.flush()
    f = FinancialEntry(public_code='LAN-EEGFISC', tipo='receita', categoria='EEG',
        valor=Decimal('250.00'), moeda='BRL', status='Recebido', eeg_exam_id=e.id)
    db.add(f); db.commit()
    return e, f


def eeg_policy(db, users, environment='mock'):
    base_env = 'restricted' if environment == 'mock' else environment
    row = db.scalar(select(NationalDpsConfigurationVersion).where(
        NationalDpsConfigurationVersion.environment == base_env))
    if not row:
        data = synthetic_configuration_payload(environment=base_env)
        from app.services.nfse_national.config import NationalDpsConfiguration
        cfg = data['configuration']; cfg['tp_amb'] = 1 if environment == 'production' else 2
        row = fiscal_config.create_version(db, environment=base_env, effective_from=date(2026, 1, 1),
            validation_state='validated', configuration=NationalDpsConfiguration.model_validate(cfg), actor=users['admin'].id)
    config = policy_payload().configuration.model_dump(mode='json')
    config.update(national_service_code='040299', municipal_service_code='999',
        eeg_nbs_code='', eeg_national_configuration_id=row.id, eeg_modalidades=['vigilia', 'com-sono', 'privacao-sono'])
    payload = policy_payload(version='EEG-SYNTHETIC-ONLY-v1', environment=environment, service='eeg', flow='EEG', configuration=config)
    return nfse.create_policy(db, payload, users['admin'].id)


@pytest.mark.parametrize('code,description', list(nfse_eeg.DESCRIPTIONS.items()))
def test_descricao_fiscal_por_modalidade(code, description):
    assert nfse_eeg.service_description(code) == description
    assert 'espirometria' not in description.lower()
    assert 'broncodilatador' not in description.lower()


def test_especial_sem_nomenclatura_confirmada_bloqueia():
    with pytest.raises(ServiceDescriptionUndetermined):
        nfse_eeg.service_description('especial')


def test_sem_classificacao_bloqueia_sem_preparacao_automatica(db, users, eeg_source):
    settings = Settings(nfse_enabled=True, nfse_environment='mock')
    assert db.scalar(select(func.count()).select_from(FiscalDocument)) == 0
    doc = nfse_eeg.prepare(db, eeg_source[0].id, settings, users['admin'].id)
    assert doc.state == 'blocked' and 'eeg_tax_classification_pending' in doc.blocking_reasons
    assert nfse_eeg.prepare(db, eeg_source[0].id, settings, users['admin'].id).id == doc.id
    assert db.scalar(select(func.count()).select_from(FiscalPreparation)) == 1
    with pytest.raises(HTTPException):
        nfse.operate(db, doc.id, 'issue', 'no-send-blocked', settings, users['admin'].id)
    assert db.scalar(select(func.count()).select_from(FiscalAttempt)) == 0


def test_mock_reusa_motor_sem_segundo_emissor_e_sem_dupla_emissao(db, users, eeg_source):
    settings = Settings(nfse_enabled=True, nfse_environment='mock')
    policy = eeg_policy(db, users)
    exam = eeg_source[0]
    doc = nfse_eeg.prepare(db, exam.id, settings, users['admin'].id)
    assert doc.kind == 'eeg' and doc.state == 'pending'
    prep = nfse.latest_preparation(db, doc.id)
    assert prep.amount_snapshot == 250 and prep.financial_entry_id == eeg_source[1].id
    assert document_service_description(db, doc) == 'Eletroencefalograma (EEG) de rotina'
    concrete = fiscal_config.resolve_document_configuration(db, doc, prep)
    assert concrete.issuer_cnpj == nfse_eeg.ISSUER_CNPJ
    assert concrete.codigo_tributacao_nacional == '040299'
    assert concrete.codigo_tributacao_municipal == '999'
    nfse.operate(db, doc.id, 'issue', 'synthetic-eeg-issue-01', settings, users['admin'].id)
    assert doc.state == 'simulated'
    nfse.operate(db, doc.id, 'issue', 'synthetic-eeg-issue-02', settings, users['admin'].id)
    assert db.scalar(select(func.count()).select_from(FiscalAttempt).where(FiscalAttempt.phase == 'started')) == 1
    with pytest.raises(HTTPException):
        nfse_eeg.prepare(db, exam.id, settings, users['admin'].id)


def test_revalidacao_bloqueia_dinheiro_alterado(db, users, eeg_source):
    settings = Settings(nfse_enabled=True, nfse_environment='mock')
    eeg_policy(db, users)
    doc = nfse_eeg.prepare(db, eeg_source[0].id, settings, users['admin'].id)
    eeg_source[1].valor = Decimal('251.00'); db.commit()
    nfse.operate(db, doc.id, 'issue', 'synthetic-stale-01', settings, users['admin'].id)
    assert doc.state == 'blocked' and 'preparation_stale' in doc.blocking_reasons


def test_rotas_fiscais_existentes_nao_contornam_admin(client, auth, db, users, eeg_source, monkeypatch):
    monkeypatch.setenv('M15_NFSE_ENABLED', 'true')
    monkeypatch.setenv('M15_NFSE_ENVIRONMENT', 'mock')
    get_settings.cache_clear()
    doc = nfse_eeg.prepare(db, eeg_source[0].id, Settings(nfse_enabled=True, nfse_environment='mock'), users['admin'].id)
    did = doc.id; db.rollback()
    for role in ['gestor', 'operacional', 'leitura']:
        assert client.get('/api/v1/eeg/fiscal', headers=auth(role)).status_code == 403
        assert client.get('/api/v1/fiscal/documentos/' + did, headers=auth(role)).status_code == 403
        assert client.get('/api/v1/fiscal/documentos/' + did + '/arquivos', headers=auth(role)).status_code == 403
        assert client.post('/api/v1/fiscal/documentos/' + did + '/preflight', headers=auth(role)).status_code == 403
    assert client.get('/api/v1/fiscal/documentos', headers=auth('gestor')).json()['total'] == 0
    assert client.get('/api/v1/eeg/fiscal', headers=auth()).json()['itens'][0]['marca'] == 'eeglife'
    get_settings.cache_clear()


def test_worker_eeg_mesmo_caminho_um_post_com_stub(worker, db, users, eeg_source):
    """Mesmo A1 sintético, allocator, spool e httpx.MockTransport da suíte M66."""
    eeg_policy(db, users, 'production')
    doc = nfse_eeg.prepare(db, eeg_source[0].id, get_settings(), users['admin'].id)
    from app.services import nfse_production_issuance as production
    summary = production.confirmation_summary(db, doc)
    assert summary['can_confirm'], summary
    request = production.authorize_issue(db, doc.id, preparation_id=summary['preparation_id'],
        amount=summary['amount'], confirmation=summary['confirmation_phrase'],
        idempotency_key='eeg-worker-stub-01', settings=get_settings(), actor=users['admin'].id)
    db.rollback()
    worker['sefin']['queue'].append((201, envelope()))
    result = worker['run'](request['id'])
    assert result['status'] == 'issued', result
    assert [r[0] for r in worker['sefin']['requests']] == ['POST']
    assert worker['run'](request['id'])['claimed'] is False
    sent = worker['sefin']['requests'][0][2]
    import base64, gzip, json
    xml = gzip.decompress(base64.b64decode(json.loads(sent)['dpsXmlGZipB64'])).decode()
    assert 'Eletroencefalograma (EEG) de rotina' in xml
    assert 'Espirometria' not in xml and 'EEGLife</xNome>' not in xml
    assert '040299' in xml and '63544026000110' in xml


def test_arquivos_fiscais_privados_integridade_e_autorizacao(client, auth, db, users, eeg_source, tmp_path, monkeypatch):
    from app.services.nfse_national.artifacts import write_artifact
    from app.ids import new_uuid
    monkeypatch.setenv('M15_NFSE_ENABLED', 'true')
    monkeypatch.setenv('M15_NFSE_ENVIRONMENT', 'mock')
    root = tmp_path / 'fiscal'; root.mkdir(mode=0o700)
    monkeypatch.setenv('M15_NFSE_FISCAL_ARTIFACTS_DIR', str(root))
    get_settings.cache_clear()
    doc = nfse_eeg.prepare(db, eeg_source[0].id, get_settings(), users['admin'].id)
    content = b'<NFSe>SYNTHETIC ONLY</NFSe>'
    stored = write_artifact(root, document_id=doc.id, attempt_id=new_uuid(), kind='nfse_xml', data=content)
    artifact = FiscalArtifact(document_id=doc.id, kind='nfse_xml', storage_relative_path=str(stored.relative_path),
        sha256=stored.sha256, size_bytes=stored.size_bytes, created_by=users['admin'].id)
    db.add(artifact); db.commit()
    did, aid = doc.id, artifact.id
    db.rollback()
    base = '/api/v1/fiscal/documentos/' + did + '/arquivos'
    listing = client.get(base, headers=auth()).json()
    assert listing['itens'][0]['id'] == aid and 'path' not in str(listing)
    assert client.get(base + '/' + aid + '/conteudo', headers=auth('gestor')).status_code == 403
    response = client.get(base + '/' + aid + '/conteudo', headers=auth())
    assert response.status_code == 200 and response.content == content
    assert response.headers['content-type'].startswith('application/xml')
    assert response.headers['cache-control'] == 'no-store'
    (root / stored.relative_path).write_bytes(b'changed')
    assert client.get(base + '/' + aid + '/conteudo', headers=auth()).status_code == 409
