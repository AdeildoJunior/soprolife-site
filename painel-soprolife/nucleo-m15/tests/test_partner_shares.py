"""Cenários financeiros em banco sintético: receita única e acerto separado."""
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select, func
from app.models import (ExamPartnerShare, FinancialEntry, Partner, PartnerUnit, Partnership,
                        PartnerCommercialRule, PartnerShareMovement, SpirometryExam)
from app.services.partner_share_setup import configure
from app.services.partner_shares import create_share
from app.services.exam_profit import DEFAULT_RULES, session_cost
from app.snapshots import build_financeiro_summary

API = '/api/v1'
DAY = date(2026, 11, 9)


@pytest.fixture()
def configured(db, users):
    for codigo in ('maclin', 'alergocare'):
        configure(db, codigo, DAY, users['admin'].id)
    db.commit()


def attendance(client, auth, person, codigo='maclin', receiver='dividido', status='Recebido'):
    return client.post(API+'/atendimentos',headers=auth('operacional'),json={
        'tipo':'espirometria_soprolife','person_id':person['id'],
        'espirometria':{'data_exame':DAY.isoformat(),'parceria_comercial':codigo,'recebido_por':receiver,'broncodilatador':True},
        'financeiro':{'espirometria':{'valor':'100.00','status':status,'forma_pagamento':'Dinheiro',
            'data_competencia':DAY.isoformat(),'data_recebimento':DAY.isoformat() if status=='Recebido' else None}}})


def movement(client, auth, share_id, **changes):
    payload={'valor':'70.00','natureza':'regularizacao','direcao':'entrada','conta':'C6 PJ',
             'origem_bancaria':'Faustino','ocorrido_em':'2026-11-13T19:28:24-03:00',
             'forma_pagamento':'Pix','idempotency_key':'synthetic-regularization'}
    payload.update(changes)
    return client.post(API+f'/financeiro/partilhas/{share_id}/movimentos',headers=auth('gestor'),json=payload)


@pytest.mark.parametrize('codigo',['maclin','alergocare'])
@pytest.mark.parametrize('receiver,payable,receivable',[('soprolife','30.00','0.00'),('parceiro','0.00','70.00'),('dividido','0.00','0.00')])
def test_gross_dimensions_and_obligations(configured,client,auth,person,db,codigo,receiver,payable,receivable):
    r=attendance(client,auth,person,codigo,receiver);assert r.status_code==201,r.text
    share=r.json()['partilha_parceiro'];exam=db.get(SpirometryExam,share['exam_id'])
    assert (share['bruto'],share['parcela_parceiro'],share['parcela_soprolife'])==('100.00','30.00','70.00')
    assert (share['a_pagar'],share['a_receber'])==(payable,receivable)
    assert exam.modalidade=='clinica_parceira' and exam.partner_unit_id
    assert db.get(PartnerUnit,exam.partner_unit_id).partner_id==exam.partner_id
    assert share['recebido_por']==receiver and share['forma_pagamento']=='Dinheiro'
    assert db.scalar(select(func.sum(FinancialEntry.valor)).where(FinancialEntry.tipo=='receita'))==D(100)


def test_pix_not_second_revenue_and_idempotent(configured,client,auth,person,db):
    s=attendance(client,auth,person).json()['partilha_parceiro']
    r=movement(client,auth,s['id']);assert r.status_code==201,r.text
    assert movement(client,auth,s['id']).status_code==201
    assert r.json()['a_pagar']==r.json()['a_receber']=='0.00'
    assert r.json()['entradas_em_conta']=='70.00'
    assert db.scalar(select(func.count()).select_from(FinancialEntry))==2
    assert db.scalar(select(func.sum(FinancialEntry.valor)).where(FinancialEntry.tipo=='receita'))==D(100)
    assert db.scalar(select(func.count()).select_from(PartnerShareMovement))==1
    assert movement(client,auth,s['id'],idempotency_key='another-duplicate').status_code==422
    assert movement(client,auth,s['id'],valor='60').status_code==409
    summary=build_financeiro_summary(db)
    assert summary['totais']['receita_recebida']==100.0
    assert summary['por_status']==[{'status':'Recebido','quantidade':1,'valor':100.0}]


@pytest.mark.parametrize('receiver,amount,direction',[('soprolife','30.00','saida'),('parceiro','70.00','entrada')])
def test_settlement_clears_obligation_without_revenue(configured,client,auth,person,db,receiver,amount,direction):
    s=attendance(client,auth,person,receiver=receiver).json()['partilha_parceiro']
    r=movement(client,auth,s['id'],natureza='acerto',valor=amount,direcao=direction);assert r.status_code==201,r.text
    assert r.json()['a_pagar']==r.json()['a_receber']=='0.00' and r.json()['status_acerto']=='acertado'
    assert r.json()['data_acerto'].startswith('2026-11-13T19:28:24')
    assert db.scalar(select(func.sum(FinancialEntry.valor)).where(FinancialEntry.tipo=='receita'))==D(100)
    assert movement(client,auth,s['id'],natureza='acerto',valor=amount,direcao=direction,idempotency_key='overpayment').status_code==422


def test_pending_partial_and_divided_do_not_create_artificial_obligation(configured,client,auth,person):
    for status in ('Pendente','Parcial'):
        s=attendance(client,auth,person,receiver='parceiro',status=status).json()['partilha_parceiro']
        assert s['a_pagar']==s['a_receber']=='0.00' and s['status_acerto']=='aguardando_pagamento'
        assert movement(client,auth,s['id']).status_code==409
    s=attendance(client,auth,person).json()['partilha_parceiro']
    assert movement(client,auth,s['id'],natureza='acerto',valor='30',direcao='saida').status_code==422


def test_alergolife_never_reused(configured,db,client,auth,person,users):
    other=Partner(public_code='CLI-SYN-L',nome='ALERGO LIFE',status='ativa');db.add(other);db.flush()
    unit=PartnerUnit(public_code='UNI-SYN-L',partner_id=other.id,nome='ALERGO LIFE');db.add(unit);db.commit()
    before=(other.id,other.nome,unit.id,unit.nome)
    for code in ('maclin','alergocare'): configure(db,code,DAY,users['admin'].id)
    db.commit()
    cfg=client.get(API+'/atendimentos/configuracao',headers=auth()).json()['espirometria_soprolife']
    assert len(cfg['parcerias_comerciais'])==2 and 'ALERGO LIFE' not in str(cfg['parcerias_comerciais'])
    s=attendance(client,auth,person,codigo='alergocare').json()['partilha_parceiro']
    assert db.get(SpirometryExam,s['exam_id']).partner_id!=other.id
    assert before==(other.id,other.nome,unit.id,unit.nome)
    assert db.scalar(select(func.count()).select_from(PartnerCommercialRule))==2


def test_reuse_units_and_addresses(db,users):
    for code,label in [('maclin','MACLIN'),('alergocare','ALERGOCARE (DRA SINTETICA)')]:
        p=Partner(public_code='CLI-'+code,nome=label,status='ativa');db.add(p);db.flush()
        u=PartnerUnit(public_code='UNI-'+code,partner_id=p.id,nome=label+' Unidade',logradouro='Endereço institucional sintético');db.add(u);db.flush()
        rule=configure(db,code,DAY,users['admin'].id)
        assert rule.partner_unit_id==u.id and u.logradouro=='Endereço institucional sintético'
    assert db.scalar(select(func.count()).select_from(PartnerUnit))==2


def test_alergocare_has_no_automatic_travel_or_room_costs():
    rules={**DEFAULT_RULES,'alergocare.parking':D(10),'alergocare.fuel':D(10)}
    assert session_cost('alergocare',None,rules)=={'parking':0,'fuel':0,'room':0}
    assert session_cost('alergocare',None,rules,parking_override=D(10),fuel_override=D(10))=={'parking':1000,'fuel':1000,'room':0}


@pytest.mark.parametrize('code',['ESP-000059','ESP-000069','ESP-000072'])
def test_protected_history_cannot_gain_share(configured,db,users,person,code):
    rule=db.scalar(select(PartnerCommercialRule).where(PartnerCommercialRule.codigo=='maclin'))
    p=db.get(Partnership,rule.partnership_id)
    e=SpirometryExam(public_code=code,person_id=person['id'],partner_id=p.partner_id,data_exame=DAY,modalidade='cowork');db.add(e);db.flush()
    f=FinancialEntry(public_code='LAN-SYN-H',tipo='receita',valor=D(70),spirometry_exam_id=e.id,status='Recebido');db.add(f);db.flush()
    with pytest.raises(Exception) as exc: create_share(db,e,f,'maclin','dividido',users['admin'].id,'test',confirmed_existing=True)
    assert exc.value.status_code==409 and f.valor==D(70)
    assert db.scalar(select(func.count()).select_from(ExamPartnerShare))==0


def test_manual_duplicate_and_cancellation_blocked(configured,client,auth,person,db):
    s=attendance(client,auth,person).json()['partilha_parceiro']
    r=client.post(API+'/lancamentos',headers=auth('gestor'),json={'tipo':'receita','categoria':'Outro','valor':'70','spirometry_exam_id':s['exam_id']})
    assert r.status_code==409,r.text
    assert movement(client,auth,s['id']).status_code==201
    for e in db.scalars(select(FinancialEntry).where(FinancialEntry.spirometry_exam_id==s['exam_id'])):
        assert client.patch(API+'/lancamentos/'+e.id,headers=auth('gestor'),json={'status':'Cancelado'}).status_code==409


def test_permissions_and_receiver_required(configured,client,auth,person):
    s=attendance(client,auth,person).json()['partilha_parceiro']
    assert client.get(API+'/financeiro/partilhas').status_code==401
    assert client.get(API+'/financeiro/partilhas',headers=auth('leitura')).status_code==200
    assert client.post(API+f'/financeiro/partilhas/{s["id"]}/movimentos',headers=auth('operacional'),json={}).status_code==403
    assert attendance(client,auth,person,receiver=None).status_code==422


def test_partner_receipt_is_not_own_cash_until_settlement(configured,client,auth,person,db):
    from app.services.profit_projection import build_projection
    s=attendance(client,auth,person,receiver='parceiro').json()['partilha_parceiro']
    row=build_projection(db,DAY,DAY)['exames'][0]
    assert row['receita']=='70.00' and row['recebido']=='0.00' and row['pendente']=='70.00'
    db.rollback()  # encerra a leitura SQLite antes da escrita pela outra sessão HTTP
    assert movement(client,auth,s['id'],natureza='acerto').status_code==201
    row=build_projection(db,DAY,DAY)['exames'][0]
    assert row['receita']=='70.00' and row['recebido']=='70.00' and row['pendente']=='0.00'


def test_generic_rate_comes_from_configuration_not_hardcoded_thirty(db,users,client,auth,person):
    from app.models import PartnerCommercialRule
    p=Partner(public_code='CLI-SYN-G',nome='Clínica Genérica Sintética',status='ativa');db.add(p);db.flush()
    u=PartnerUnit(public_code='UNI-SYN-G',nome='Unidade Genérica',partner_id=p.id);db.add(u);db.flush()
    a=Partnership(public_code='PAR-SYN-G',partner_id=p.id,status='ativa',modelo_repasse='percentual',percentual_repasse=D(25));db.add(a);db.flush()
    db.commit()
    response=client.post(API+f'/parcerias/{a.id}/regras-comerciais',headers=auth('gestor'),json={
        'codigo':'generica','partner_unit_id':u.id,'vigencia_inicio':DAY.isoformat(),
        'percentual_parceiro':'25','percentual_soprolife':'75'})
    assert response.status_code==201,response.text
    r=attendance(client,auth,person,codigo='generica');assert r.status_code==201,r.text
    s=r.json()['partilha_parceiro'];assert s['parcela_parceiro']=='25.00' and s['parcela_soprolife']=='75.00'


def test_next_version_is_prospective_and_old_snapshot_stays_unchanged(configured,client,auth,person,db,users):
    from app.services.partner_shares import resolve_rule
    first=attendance(client,auth,person).json()['partilha_parceiro']
    old=db.scalar(select(PartnerCommercialRule).where(PartnerCommercialRule.codigo=='maclin'))
    db.add(PartnerCommercialRule(codigo='maclin',partnership_id=old.partnership_id,partner_unit_id=old.partner_unit_id,
        modalidade=old.modalidade,vigencia_inicio=date(2026,11,10),percentual_parceiro=D(35),
        percentual_soprolife=D(65),fiscal_flow='DIRECT',created_by_user_id=users['admin'].id));db.commit()
    assert resolve_rule(db,'maclin',DAY)[0].percentual_parceiro==D(30)
    assert resolve_rule(db,'maclin',date(2026,11,11))[0].percentual_parceiro==D(35)
    old_share=db.get(ExamPartnerShare,first['id'])
    assert old_share.percentual_parceiro==D(30) and old_share.parcela_soprolife==D(70)


def test_admin_confirmation_reuses_existing_gross_and_preserves_exam(configured,db,users,person):
    from types import SimpleNamespace
    from scripts.partner_share_admin import perform
    rule=db.scalar(select(PartnerCommercialRule).where(PartnerCommercialRule.codigo=='maclin'))
    partner=db.get(Partnership,rule.partnership_id)
    e=SpirometryExam(public_code='ESP-SYN-EXT',person_id=person['id'],data_exame=DAY,
        modalidade='cowork',origem='MACLIN',local_atendimento='MACLIN',partner_id=partner.partner_id,status='Realizado')
    db.add(e);db.flush()
    f=FinancialEntry(public_code='LAN-SYN-EXT',tipo='receita',valor=D(100),spirometry_exam_id=e.id,status='Recebido',forma_pagamento='Dinheiro')
    db.add(f);db.commit()
    before=(e.modalidade,e.partner_unit_id,e.local_atendimento,e.updated_at,f.valor)
    args=SimpleNamespace(mode='confirm-existing',exam_code=e.public_code,entry_code=f.public_code,service_date=DAY)
    first=perform(db,args,users['admin']);db.commit()
    again=perform(db,args,users['admin']);db.commit()
    assert first==again and first['bruto']=='100.00' and first['entradas_em_conta']=='70.00'
    assert before==(e.modalidade,e.partner_unit_id,e.local_atendimento,e.updated_at,f.valor)
    assert db.scalar(select(func.count()).select_from(ExamPartnerShare))==1
    assert db.scalar(select(func.count()).select_from(FinancialEntry))==2


def test_expired_latest_rule_does_not_reactivate_old_percentage(configured,db,users):
    from app.services.partner_shares import resolve_rule
    old=db.scalar(select(PartnerCommercialRule).where(PartnerCommercialRule.codigo=='maclin'))
    db.add(PartnerCommercialRule(codigo='maclin',partnership_id=old.partnership_id,partner_unit_id=old.partner_unit_id,
        modalidade=old.modalidade,vigencia_inicio=date(2026,11,10),vigencia_fim=date(2026,11,11),
        percentual_parceiro=D(35),percentual_soprolife=D(65),created_by_user_id=users['admin'].id));db.commit()
    with pytest.raises(Exception) as error:resolve_rule(db,'maclin',date(2026,11,12))
    assert error.value.status_code==422
