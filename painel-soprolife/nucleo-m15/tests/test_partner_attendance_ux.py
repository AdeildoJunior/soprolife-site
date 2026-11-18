"""Cards explícitos, contexto canônico e centavos sem tocar no histórico."""
import pytest
from sqlalchemy import select, func
from decimal import Decimal
from app.models import FinancialEntry, SpirometryExam, PartnerUnit, Partner, FiscalDocument
from tests.test_partner_shares import DAY
from app.services.partner_share_setup import configure
from tests.test_nfse_migrations import postgres_url, config
from alembic import command
from sqlalchemy import create_engine
from app.db import Base


@pytest.fixture(params=['sqlite', 'postgresql'])
def engine(request, tmp_path):
    if request.param == 'postgresql':
        url = request.getfixturevalue('postgres_url')
        command.upgrade(config(url), 'head')
        eng = create_engine(url, hide_parameters=True)
    else:
        eng = create_engine(f'sqlite:///{tmp_path}/ux.db', connect_args={'check_same_thread': False})
        Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def configured(db, users):
    # Nomes institucionais reais têm sufixos; a origem operacional é estável.
    for code, name in [('maclin', 'MACLIN'), ('alergocare', 'ALERGOCARE (RESPONSÁVEL SINTÉTICO)')]:
        db.add(Partner(public_code='CLI-UX-' + code[0].upper(), nome=name, status='ativa'))
        db.flush()
        configure(db, code, DAY, users['admin'].id)
    db.commit()


@pytest.mark.parametrize('codigo', ['maclin', 'alergocare'])
@pytest.mark.parametrize('bruto,parceiro,proprio', [
    ('100.00', '30.00', '70.00'), ('220.00', '66.00', '154.00'),
    ('123.45', '37.04', '86.41'),
])
def test_card_resolve_ids_origem_e_centavos(configured, client, auth, person, db,
                                           codigo, bruto, parceiro, proprio):
    cfg = client.get('/api/v1/atendimentos/configuracao', headers=auth()).json()['espirometria_soprolife']
    contexto = next(p for p in cfg['parcerias_comerciais'] if p['codigo'] == codigo)
    payload = {'tipo': 'espirometria_' + codigo, 'person_id': person['id'],
               'espirometria': {'data_exame': str(DAY), 'recebido_por': 'dividido',
                               'origem': 'SITE', 'municipio_atendimento_ibge': '3304557'},
               'financeiro': {'espirometria': {'valor': bruto, 'status': 'Pendente'}}}
    assert client.post('/api/v1/atendimentos', headers=auth('leitura'), json=payload).status_code == 403
    assert client.post('/api/v1/atendimentos', json=payload).status_code == 401
    r = client.post('/api/v1/atendimentos', headers=auth('operacional'), json=payload)
    assert r.status_code == 201, r.text
    exame = r.json()['espirometria']
    partilha = r.json()['partilha_parceiro']
    assert (exame['partner_id'], exame['partner_unit_id']) == (contexto['partner_id'], contexto['partner_unit_id'])
    assert exame['modalidade'] == 'clinica_parceira' and exame['local_atendimento'] == contexto['unidade']
    assert exame['municipio_atendimento_ibge'] == '3304557'
    assert exame['origem'] == contexto['origem_operacional'] == codigo.capitalize()
    assert (partilha['bruto'], partilha['parcela_parceiro'], partilha['parcela_soprolife']) == (bruto, parceiro, proprio)
    # Releitura SQL, não apenas a resposta HTTP.
    db.expire_all()
    salvo = db.get(SpirometryExam, exame['id'])
    unidade = db.get(PartnerUnit, salvo.partner_unit_id)
    assert unidade.partner_id == salvo.partner_id
    assert 'alergo life' not in db.get(Partner, salvo.partner_id).nome.lower()
    entradas = db.scalars(select(FinancialEntry).where(FinancialEntry.spirometry_exam_id == salvo.id)).all()
    assert len(entradas) == 1 and entradas[0].valor == Decimal(bruto)
    assert db.scalar(select(func.count()).select_from(FiscalDocument)) == 0


@pytest.mark.parametrize('codigo,outro', [('maclin', 'alergocare'), ('alergocare', 'maclin')])
def test_card_nao_aceita_troca_de_entidade(configured, client, auth, person, codigo, outro):
    r = client.post('/api/v1/atendimentos', headers=auth('operacional'), json={
        'tipo': 'espirometria_' + codigo, 'person_id': person['id'],
        'espirometria': {'data_exame': str(DAY), 'parceria_comercial': outro, 'recebido_por': 'dividido'},
        'financeiro': {'espirometria': {'valor': '123.45', 'status': 'Pendente'}},
    })
    assert r.status_code == 422


def test_migracao_centavos_preserva_fatos_e_recusa_diferenca_de_um_centavo(postgres_url):
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import IntegrityError
    from app.models import User, Person, Partnership, ExamPartnerShare
    from app.services.partner_shares import create_share

    cfg = config(postgres_url)
    command.upgrade(cfg, 'p151126share')
    eng = create_engine(postgres_url, hide_parameters=True)
    with Session(eng) as db:
        user = User(id='ux-admin', nome='Sintético', email='ux@synthetic.invalid', password_hash='unusable')
        person = Person(id='ux-person', public_code='PES-UX', nome_completo='Pessoa Sintética', nome_normalizado='pessoa sintetica')
        db.add_all([user, person]); db.flush()
        rule = configure(db, 'maclin', DAY, user.id)
        partner = db.get(Partnership, rule.partnership_id).partner_id
        exam = SpirometryExam(public_code='ESP-UX', person_id=person.id, data_exame=DAY,
                              partner_id=partner, partner_unit_id=rule.partner_unit_id, modalidade=rule.modalidade)
        db.add(exam); db.flush()
        entry = FinancialEntry(public_code='LAN-UX', tipo='receita', valor=Decimal('123.45'), spirometry_exam_id=exam.id)
        db.add(entry); db.flush()
        share = create_share(db, exam, entry, 'maclin', 'dividido', user.id, 'synthetic-migration')
        share_id = share.id
        db.commit()
    with eng.connect() as db:
        before = db.scalar(text('SELECT md5(row_to_json(t)::text) FROM exam_partner_shares t'))
    command.upgrade(cfg, 'head'); command.check(cfg)
    with eng.connect() as db:
        assert db.scalar(text('SELECT md5(row_to_json(t)::text) FROM exam_partner_shares t')) == before
    with Session(eng) as db:
        old = db.get(ExamPartnerShare, share_id)
        values = {c.name: getattr(old, c.name) for c in old.__table__.columns if c.name != 'id'}
        exam = SpirometryExam(public_code='ESP-UX-BAD', person_id='ux-person', data_exame=DAY)
        db.add(exam); db.flush()
        entry = FinancialEntry(public_code='LAN-UX-BAD', tipo='receita', valor=Decimal('123.45'), spirometry_exam_id=exam.id)
        db.add(entry); db.flush()
        values.update(spirometry_exam_id=exam.id, financial_entry_id=entry.id, parcela_soprolife=Decimal('86.42'))
        db.add(ExamPartnerShare(**values))
        with pytest.raises(IntegrityError, match='share_amount'):
            db.flush()
        db.rollback()
    eng.dispose()
