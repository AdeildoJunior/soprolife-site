"""Defaults de formulário não viram defaults de persistência ou backfill."""
import pytest
from sqlalchemy import select, func
from app.models import FiscalDocument, FiscalAttempt, FiscalIssuanceRequest
from app.services.nfse_national.service_location import SUPPORTED_SERVICE_MUNICIPALITIES

API = '/api/v1'


def test_configuracao_sugere_valores_editaveis_sem_escrita(client, auth, person, db):
    old = client.post(f'{API}/espirometrias', headers=auth(), json={
        'person_id': person['id'], 'modalidade': 'residencial', 'broncodilatador': False,
        'municipio_atendimento_ibge': '3303302', 'origem': 'Indicação',
    }).json()
    before = client.get(f'{API}/espirometrias', headers=auth()).json()
    config = client.get(f'{API}/atendimentos/configuracao', headers=auth()).json()['espirometria_soprolife']
    defaults = config['valores_iniciais']
    assert defaults == {'municipio_atendimento_ibge': '3304557', 'broncodilatador': True,
                        'modalidade': 'cowork', 'origem': 'SITE'}
    assert defaults['municipio_atendimento_ibge'] in SUPPORTED_SERVICE_MUNICIPALITIES
    places = next(m for m in config['modalidades'] if m['valor']=='cowork')['local_sugestoes']
    assert 'NOVA AMERICA' in places and 'SOPROLIFE DOWNTOWN' in places
    assert not any('NORTE' in p for p in places)  # não confirmado na auditoria
    assert client.get(f'{API}/espirometrias', headers=auth()).json() == before
    assert old['municipio_atendimento_ibge']=='3303302' and old['broncodilatador'] is False


@pytest.mark.parametrize('modalidade', ['cowork', 'residencial'])
@pytest.mark.parametrize('municipio,bd', [('3304557', True), ('3303302', False)])
def test_escolha_do_operador_persiste_sem_emissao(client, auth, person, db, modalidade, municipio, bd):
    response = client.post(f'{API}/atendimentos', headers=auth(), json={
        'person_id': person['id'], 'tipo': 'espirometria_soprolife',
        'espirometria': {'data_exame':'2026-10-01', 'status':'Realizado',
            'modalidade':modalidade, 'local_atendimento':'Espaço de demonstração',
            'municipio_atendimento_ibge':municipio, 'broncodilatador':bd, 'origem':'Indicação'},
    })
    assert response.status_code==201, response.text
    exam=response.json()['espirometria']
    assert exam['municipio_atendimento_ibge']==municipio and exam['broncodilatador']==bd
    assert exam['modalidade']==modalidade and exam['origem']=='Indicação'
    for model in [FiscalDocument, FiscalAttempt, FiscalIssuanceRequest]:
        assert db.scalar(select(func.count()).select_from(model))==0


def test_ausencia_na_api_nao_herda_default_do_formulario(client, auth, person):
    result=client.post(f'{API}/atendimentos', headers=auth(), json={
        'person_id':person['id'], 'tipo':'espirometria_soprolife',
        'espirometria':{'data_exame':'2026-10-01','status':'Realizado','modalidade':'residencial'},
    })
    assert result.status_code==201, result.text
    exam=result.json()['espirometria']
    assert exam['municipio_atendimento_ibge'] is None
    assert exam['broncodilatador'] is None and exam['origem'] is None
