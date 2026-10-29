"""Missão 29/10: somente identidades sintéticas e banco efêmero."""
import pytest
from sqlalchemy import select, func
from app.models import Person, EegExam, EegReportDocument, FinancialEntry, Consent

API = '/api/v1/eeg'


def pessoa(client, auth):
    r = client.post(API + '/pessoas', headers=auth(), json={
        'nome_completo': 'Pessoa Sintetica EEG Cadastros', 'cpf': '52998224725',
        'data_nascimento': '2001-01-02', 'sexo': 'outro', 'telefone': '(21) 0000-3344',
        'email': 'sintetico@example.invalid', 'consentimento_whatsapp': 'revogado'})
    assert r.status_code == 201, r.text
    return r.json()


def atendimento(v):
    return dict(enrollment_id=v['id'], modalidade_codigo='vigilia',
        agendado_para='2026-10-28T10:30:00-03:00', local_atendimento='Local Sintetico',
        municipio_atendimento_ibge='3304557', origem_canal='site', valor='250.00',
        pagamento_status='Recebido', idempotency_key='eeg-sintetico-atendimento-01')


@pytest.mark.parametrize('q', ['sintetica eeg', '529.982.247-25', '(21) 0000-3344', 'code'])
def test_busca_nome_cpf_telefone_pes(client, auth, q):
    v = pessoa(client, auth)
    q = v['pessoa']['public_code'] if q == 'code' else q
    r = client.post(API + '/pessoas/busca', headers=auth(), json={'q': q})
    assert r.status_code == 200
    assert [p['pessoa']['id'] for p in r.json()['itens']] == [v['pessoa']['id']]


def test_vinculo_preserva_identidade_corporativa_e_consentimento(client, auth, db):
    r = client.post('/api/v1/pessoas', headers=auth(), json={'nome_completo': 'Pessoa Sintetica Compartilhada'})
    p = r.json()
    assert client.post(API + '/pessoas/busca', headers=auth(), json={}).json()['total'] == 0
    resultado = client.post(API + '/pessoas/busca', headers=auth(), json={'q': p['public_code'], 'corporativo': True}).json()
    assert resultado['itens'][0]['ja_vinculada'] is False
    a = client.post(API + '/pessoas/vincular', headers=auth(), json={'person_id': p['id']})
    b = client.post(API + '/pessoas/vincular', headers=auth(), json={'person_id': p['id']})
    assert a.json()['id'] == b.json()['id']
    assert db.scalar(select(func.count()).select_from(Person)) == 1
    db.rollback()
    v = pessoa(client, auth)
    assert v['consentimento_whatsapp'] == 'revogado'
    assert db.scalar(select(Consent.status).where(Consent.person_id == v['pessoa']['id'])) is None
    from app.services.followup import whatsapp_consent_status
    assert whatsapp_consent_status(db, v['pessoa']['id']) == 'desconhecido'
    db.rollback()
    dup = client.post(API + '/pessoas', headers=auth(), json={
        'nome_completo': 'Outra Pessoa Sintetica', 'cpf': '52998224725', 'confirmar_nova_pessoa': True})
    assert dup.status_code == 409


def test_atendimento_atomico_idempotente_razao_separado_sem_laudo(client, auth, db):
    v = pessoa(client, auth)
    payload = atendimento(v)
    a = client.post(API + '/atendimentos', headers=auth(), json=payload)
    assert a.status_code == 201, a.text
    b = client.post(API + '/atendimentos', headers=auth(), json=payload)
    assert b.json()['id'] == a.json()['id']
    assert a.json()['local_nome'] == payload['local_atendimento']
    assert a.json()['arquivo_tecnico'] == {'registrado': False}
    assert 'valor' not in a.json()
    assert db.scalar(select(func.count()).select_from(EegReportDocument)) == 0
    assert db.scalar(select(func.count()).select_from(EegExam)) == 1
    e = db.scalar(select(FinancialEntry))
    entry_id = e.id
    assert e.valor == 250 and e.eeg_exam_id == a.json()['id']
    db.rollback()
    assert client.get('/api/v1/lancamentos', headers=auth()).json()['total'] == 0
    assert client.post('/api/v1/lancamentos/busca', headers=auth(), json={}).json()['total'] == 0
    assert client.patch('/api/v1/lancamentos/' + entry_id, headers=auth(), json={'status': 'Pendente'}).status_code == 404
    assert client.get(API + '/financeiro', headers=auth()).json()['total'] == 1
    payload['valor'] = '260.00'
    assert client.post(API + '/atendimentos', headers=auth(), json=payload).status_code == 409


@pytest.mark.parametrize('role', ['gestor', 'operacional', 'leitura'])
def test_autorizacao_eeg_em_todas_as_entradas(client, auth, role):
    assert client.post(API + '/pessoas/busca', headers=auth(role), json={'q': 'Sintetica', 'corporativo': True}).status_code == 403
    assert client.get(API + '/financeiro', headers=auth(role)).status_code == 403
    assert client.post(API + '/atendimentos', headers=auth(role), json={}).status_code == 403


def test_validacoes_e_catalogo_tecnico(client, auth, db):
    v = pessoa(client, auth)
    payload = atendimento(v)
    for field, value in [('valor', '-1'), ('modalidade_codigo', 'video-eeg'), ('agendado_para', '2026-10-28T10:30:00'), ('municipio_atendimento_ibge', 'xxx')]:
        r = client.post(API + '/atendimentos', headers=auth(), json={**payload, field: value})
        assert r.status_code == 422, r.text
    assert db.scalar(select(func.count()).select_from(EegExam)) == 0
    db.rollback()
    itens = client.get(API + '/modalidades', headers=auth()).json()['itens']
    assert all(m['validacao_tecnica'] == 'pendente' for m in itens)
    assert not next(m for m in itens if m['codigo'] == 'hospitalar-beira-leito')['ativo']
