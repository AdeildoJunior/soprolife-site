"""Same corporate identity, two independent clinical/financial domains."""
from pathlib import Path
import pytest
from sqlalchemy import select,func
from app.models import Person,EegEnrollment,EegExam,SpirometryExam,FinancialEntry,FiscalDocument
from app.services import nfse_eeg
from app.services.nfse_national.service_description import ServiceDescriptionUndetermined
from tests.test_eeg_inline_attendance import payload as eeg_payload

API='/api/v1'


@pytest.mark.parametrize('equipment',['MIR_SPIROBANK_SMART_II','KOKO','OUTRO',None])
def test_shared_person_with_spirometry_source_and_eeg(client,auth,db,equipment):
    person=client.post(API+'/pessoas',headers=auth(),json={'nome_completo':'Pessoa Integracao MIR EEG Sintetica'}).json()
    sopro={'person_id':person['id'],'tipo':'espirometria_soprolife',
        'idempotency_key':'integration-sopro-synthetic-01',
        'espirometria':{'data_exame':'2026-10-20','status':'Realizado','equipamento_fonte':equipment},
        'financeiro':{'espirometria':{'valor':'220.00','status':'Pendente'}}}
    first=client.post(API+'/atendimentos',headers=auth(),json=sopro)
    assert first.status_code==201,first.text
    assert first.json()['espirometria']['equipamento_fonte']==equipment
    data=eeg_payload(person_id=person['id']);data.pop('nova_pessoa')
    a=client.post(API+'/eeg/atendimentos',headers=auth(),json=data)
    b=client.post(API+'/eeg/atendimentos',headers=auth(),json=data)
    assert a.status_code==b.status_code==201,(a.text,b.text)
    assert a.json()['id']==b.json()['id']
    assert a.json()['paciente']['id']==person['id']
    assert a.json()['paciente']['public_code']==person['public_code']
    assert 'equipamento_fonte' not in a.json()
    assert db.scalar(select(func.count()).select_from(Person))==1
    for model in (SpirometryExam,EegExam,EegEnrollment):
        assert db.scalar(select(func.count()).select_from(model))==1
    assert db.scalar(select(SpirometryExam.equipamento_fonte))==equipment
    assert db.scalar(select(func.count()).select_from(FinancialEntry))==2
    assert db.scalar(select(func.count()).select_from(FiscalDocument))==0
    db.rollback()
    assert client.get(API+'/lancamentos',headers=auth()).json()['total']==1
    assert client.get(API+'/eeg/financeiro',headers=auth()).json()['total']==1
    assert client.get(API+'/espirometrias',headers=auth()).json()['itens'][0]['equipamento_fonte']==equipment


def test_domains_reject_each_others_fields(client,auth):
    bad=eeg_payload(equipamento_fonte='MIR_SPIROBANK_SMART_II')
    assert client.post(API+'/eeg/atendimentos',headers=auth(),json=bad).status_code==422
    person=client.post(API+'/pessoas',headers=auth(),json={'nome_completo':'Pessoa Separacao Sintetica'}).json()
    assert client.post(API+'/espirometrias',headers=auth(),json={
        'person_id':person['id'],'modalidade_codigo':'vigilia'}).status_code==422


def test_assets_keep_both_cache_versions():
    panel=Path(__file__).resolve().parents[2]
    html=(panel/'index.html').read_text()
    assert 'central-cadastros.js?v=2026103001' in html
    assert 'eeg-operacao.js?v=2026103001' in html
    assert 'life-command-center.css?v=2026102911' in html


def test_one_legal_issuer_and_eeg_descriptions_only():
    assert nfse_eeg.ISSUER_CNPJ=='63544026000110'
    assert 'SOPRO LIFE' in nfse_eeg.ISSUER_NAME
    assert 'rotina' in nfse_eeg.service_description('vigilia')
    with pytest.raises(ServiceDescriptionUndetermined):
        nfse_eeg.service_description('MIR_SPIROBANK_SMART_II')
