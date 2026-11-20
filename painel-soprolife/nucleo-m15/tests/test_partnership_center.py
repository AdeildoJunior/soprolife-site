"""Synthetic partnership center: identity, known/unknown money and no new revenue."""
from datetime import date,datetime,timezone
from decimal import Decimal
import json
import pytest
from sqlalchemy import select,func
from app.models import (FinancialEntry,Partner,PartnerUnit,Partnership,PartnerSettlement,
    PartnerSettlementItem,Person,SpirometryExam,User)
from app.ids import allocate_public_code
from app.security import get_role,hash_password,issue_token
from app.services import partnership_center as pc,partner_shares
from app.services.partner_share_setup import configure

DAY=date(2026,11,10)
PERIOD='inicio=2026-11-01&fim=2026-11-30'
BASE='/api/v1/parcerias/central'

@pytest.fixture
def canonical(db,users):
    for code in ('maclin','alergocare'):configure(db,code,date(2026,1,1),users['admin'].id)
    p=Partner(public_code='CLI-PC-P',nome='Pastore',status='ativa');db.add(p);db.flush()
    u=PartnerUnit(public_code='UNI-PC-P',partner_id=p.id,nome='Pastore Ipanema');db.add(u);db.flush()
    db.add(Partnership(public_code='PAR-PC-P',partner_id=p.id,modelo_recebimento='valor_por_exame',valor_recebido_por_exame=Decimal('109.50'),vigencia_inicio=date(2026,1,1),status='ativa'))
    db.commit();return {c['codigo']:c for c in pc.catalog(db)}


def exam(db,canonical,code='maclin',number=1,gross='123.45',receiver='soprolife',share=True,users=None):
    c=canonical[code];p=Person(public_code=f'PES-{number:06}',nome_completo='Pessoa Sintética',nome_normalizado='pessoa sintetica');db.add(p);db.flush()
    e=SpirometryExam(public_code=f'ESP-{number:06}',person_id=p.id,partner_id=c['partner_id'],partner_unit_id=c['unidades'][0]['id'],modalidade='clinica_parceira',data_exame=DAY,data_exame_precisao='dia',status='Realizado',broncodilatador=True,local_atendimento=c['unidades'][0]['nome']);db.add(e);db.flush()
    s=None
    if gross is not None:
        entry=FinancialEntry(public_code=allocate_public_code(db,'financial_entries'),tipo='receita',valor=Decimal(gross),spirometry_exam_id=e.id,status='Recebido',forma_pagamento='Pix',data_competencia=DAY);db.add(entry);db.flush()
        if share:s=partner_shares.create_share(db,e,entry,code,receiver,users['admin'].id,'synthetic')
    return e,s


def test_auth_missing_config_empty(client,auth,db):
    assert client.get(BASE).status_code==401
    r=client.get(BASE,headers=auth());assert r.status_code==200
    assert r.json()['consolidado']['exames'] is None
    assert all(p['estado']=='unavailable' and p['financeiro']['bruto'] is None for p in r.json()['parcerias'])
    assert r.headers['cache-control']=='private, no-store'


def test_cards_canonical_not_alergolife(canonical,client,auth,db,users):
    other=Partner(public_code='CLI-PC-L',nome='Alergo Life');db.add(other);db.flush()
    db.add(PartnerUnit(public_code='UNI-PC-L',partner_id=other.id,nome='Alergo Life'))
    a,_=exam(db,canonical,users=users);b,_=exam(db,canonical,'alergocare',2,'220.00','parceiro',users=users);db.commit()
    data=client.get(BASE+'?'+PERIOD,headers=auth()).json()
    assert {p['codigo'] for p in data['parcerias']}=={'pastore','maclin','alergocare'}
    assert data['consolidado']['exames']==2
    assert data['consolidado']['bruto']=='343.45'
    assert data['consolidado']['soprolife']=='240.41' and data['consolidado']['parceiro']=='103.04'
    assert data['consolidado']['a_pagar']=='37.04' and data['consolidado']['a_receber']=='154.00'
    assert data['consolidado']['acertos_pendentes']==2
    assert all(c['partner_id']!=other.id for c in data['parcerias'])
    detail=client.get(BASE+'/alergocare?'+PERIOD,headers=auth()).json()
    assert [r['id'] for r in detail['itens']]==[b.id]
    assert detail['parcerias'][0]['regra']=={'soprolife':'70.00','parceiro':'30.00','fiscal':'DIRECT'}


def test_unknown_history_not_recalculated(canonical,client,auth,db,users):
    exam(db,canonical,gross='100.00',share=False,users=users);db.commit()
    d=client.get(BASE+'/maclin?'+PERIOD,headers=auth()).json()
    assert d['consolidado']['a_receber'] is None and d['consolidado']['acertos_pendentes'] is None
    assert d['itens'][0]['share_id'] is None and d['itens'][0]['soprolife'] is None


def test_transfer_not_revenue(canonical,client,auth,db,users):
    _,s=exam(db,canonical,users=users);db.commit()
    response=client.post('/api/v1/financeiro/partilhas/'+s.id+'/movimentos',headers=auth(),json={'valor':'37.04','natureza':'acerto','direcao':'saida','forma_pagamento':'Pix','conta':'Conta Sintética','ocorrido_em':'2026-11-11T12:00:00-03:00','idempotency_key':'synthetic-partnership-move'})
    assert response.status_code==201,response.text
    d=client.get(BASE+'/maclin?'+PERIOD,headers=auth()).json()
    assert d['consolidado']['bruto']=='123.45' and d['consolidado']['a_pagar']=='0.00'
    assert d['consolidado']['acertos_pendentes']==0
    assert db.scalar(select(func.count()).select_from(FinancialEntry).where(FinancialEntry.tipo=='receita'))==1


def test_pastore_batch_confirmed_and_partial_unknown(canonical,client,auth,db,users):
    e,_=exam(db,canonical,'pastore',gross=None,users=users)
    settlement=PartnerSettlement(partner_id=e.partner_id,partner_unit_id=e.partner_unit_id,competencia=DAY.replace(day=1),periodo_inicio=DAY.replace(day=1),periodo_fim=date(2026,11,30),valor_total=Decimal('109.50'),status='a_receber');db.add(settlement);db.flush();db.add(PartnerSettlementItem(settlement_id=settlement.id,spirometry_exam_id=e.id));db.commit()
    d=client.get(BASE+'/pastore?'+PERIOD,headers=auth()).json()
    assert d['consolidado']['soprolife']=='109.50' and d['consolidado']['a_receber']=='109.50'
    assert d['consolidado']['bruto'] is None and d['consolidado']['parceiro'] is None
    assert d['consolidado']['a_pagar'] is None and d['consolidado']['acertos_pendentes']==1
    settlement.valor_total=None;db.commit()
    d=client.get(BASE+'/pastore?'+PERIOD,headers=auth()).json()
    assert d['consolidado']['soprolife'] is None and d['consolidado']['a_receber'] is None
    assert d['consolidado']['acertos_pendentes']==1


@pytest.mark.parametrize('role',['leitura','operacional'])
def test_no_financial_payload(canonical,client,auth,db,users,role):
    exam(db,canonical,users=users);db.commit()
    for endpoint in (BASE,BASE+'/maclin'):
        d=client.get(endpoint+'?'+PERIOD,headers=auth(role)).json()
        assert d['com_financeiro'] is False and 'bruto' not in d['consolidado']
        assert all('financeiro' not in c for c in d['parcerias'])
        assert all('bruto' not in row for row in d.get('itens',[]))


def test_doctor_denied_and_search_links(canonical,client,auth,db,users):
    u=User(email='doctor@pc.synthetic',nome='Perfil Sintético',password_hash=hash_password('synthetic-password'),roles=[get_role(db,'medico')]);db.add(u);db.commit()
    h={'Authorization':'Bearer '+issue_token(u.id,u.password_hash)}
    assert client.get(BASE,headers=h).status_code==403 and client.get(BASE+'/maclin',headers=h).status_code==403
    for code in ('maclin','alergocare'):
        result=client.post('/api/v1/comando/busca',headers=auth(),json={'termo':pc.LABELS[code]}).json()
        link=next(i['ver_parceria'] for g in result['grupos'] for i in g['itens'] if 'ver_parceria' in i)
        assert link['id']==canonical[code]['partner_id']
        d=client.post('/api/v1/comando/registro',headers=auth(),json={'tipo':'parceria','id':link['id']}).json()
        assert d['dados']['codigo']==code
        assert client.post('/api/v1/comando/registro',headers=h,json={'tipo':'parceria','id':link['id']}).status_code in (403,404)


def test_period_pagination_and_readonly(canonical,client,auth,db,users):
    exam(db,canonical,users=users);db.commit()
    before=db.scalar(select(func.count()).select_from(FinancialEntry))
    r=client.get(BASE+'/maclin?'+PERIOD+'&limite=1',headers=auth());assert r.status_code==200
    assert len(r.json()['itens'])==1
    assert client.get(BASE+'?inicio=2026-11-20&fim=2026-11-01',headers=auth()).status_code==422
    assert client.get(BASE+'?inicio=2020-01-01&fim=2026-01-01',headers=auth()).status_code==422
    assert client.get(BASE+'/alergolife?'+PERIOD,headers=auth()).status_code==422
    assert before==db.scalar(select(func.count()).select_from(FinancialEntry))
