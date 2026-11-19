"""Fiscal DIRECT compartilhado: bruto na nota, 70/30 somente econômico."""
from datetime import timedelta
from decimal import Decimal as D
import pytest
from sqlalchemy import select, func
from app.models import (PartnerCommercialRule, Partnership, Partner, PartnerUnit,
    ExamPartnerShare, FinancialEntry, FiscalDocument, FiscalAttempt, FiscalIssuanceRequest, FiscalPolicy)
from app.services.partner_share_setup import approve_alergocare_direct
from app.services.partner_shares import resolve_rule
from app.services import nfse
from tests.test_partner_attendance_ux import engine, configured
from tests.test_partner_shares import DAY
from tests.test_nfse_foundation import policy_payload
from tests.test_nfse_migrations import postgres_url


def create(client, auth, person, code, day=DAY, gross='123.45'):
    result=client.post('/api/v1/atendimentos',headers=auth('operacional'),json={
        'tipo':'espirometria_'+code,'person_id':person['id'],
        'espirometria':{'data_exame':str(day),'broncodilatador':True,
                       'municipio_atendimento_ibge':'3304557','recebido_por':'dividido'},
        'financeiro':{'espirometria':{'valor':gross,'status':'Recebido',
            'forma_pagamento':'Pix','data_recebimento':str(day)}}})
    assert result.status_code==201,result.text
    return result.json()


@pytest.mark.parametrize('code',['maclin','alergocare'])
@pytest.mark.parametrize('gross,own,partner',[('100.00','70.00','30.00'),('123.45','86.41','37.04'),('220.00','154.00','66.00')])
def test_both_use_same_direct_policy_and_gross(configured,client,auth,person,db,users,code,gross,own,partner):
    saved=create(client,auth,person,code,gross=gross)
    nfse.create_policy(db,policy_payload(flow='DIRECT'),users['admin'].id)
    share=saved['partilha_parceiro']
    assert (share['bruto'],share['parcela_soprolife'],share['parcela_parceiro'])==(gross,own,partner)
    fiscal=nfse.evaluate(db,share['exam_id'],'mock',for_update=False)
    assert fiscal['flow']=='DIRECT' and fiscal['amount_snapshot']==D(gross),fiscal
    assert not fiscal['blocking_reasons']
    assert db.get(FiscalPolicy,fiscal['policy_id']).configuration['issuer']=='SOPROLIFE'
    assert fiscal['financial_entry_id'] and db.get(FinancialEntry,fiscal['financial_entry_id']).valor==D(gross)
    assert db.scalar(select(func.count()).select_from(FinancialEntry))==1
    for model in (FiscalDocument,FiscalAttempt,FiscalIssuanceRequest):
        assert db.scalar(select(func.count()).select_from(model))==0
    cfg=client.get('/api/v1/atendimentos/configuracao',headers=auth()).json()['espirometria_soprolife']
    assert next(r for r in cfg['parcerias_comerciais'] if r['codigo']==code)['fiscal_flow']=='DIRECT'


def test_append_fiscal_version_preserves_old_shares_and_entities(configured,client,auth,person,db,users):
    original=db.scalar(select(PartnerCommercialRule).where(PartnerCommercialRule.codigo=='alergocare'))
    keys=('codigo','partnership_id','partner_unit_id','modalidade','vigencia_fim','percentual_parceiro','percentual_soprolife')
    legacy=PartnerCommercialRule(**{k:getattr(original,k) for k in keys},
        vigencia_inicio=DAY+timedelta(days=1),fiscal_flow=None,created_by_user_id=users['admin'].id)
    db.add(legacy)
    life=Partner(public_code='CLI-F-LIFE',nome='ALERGO LIFE',status='ativa');db.add(life);db.flush()
    life_unit=PartnerUnit(public_code='UNI-F-LIFE',partner_id=life.id,nome='ALERGO LIFE');db.add(life_unit);db.commit()
    legacy_id=legacy.id
    saved=create(client,auth,person,'alergocare',DAY+timedelta(days=1))
    old_share=saved['partilha_parceiro']['id']
    nfse.create_policy(db,policy_payload(flow='DIRECT'),users['admin'].id)
    before=nfse.evaluate(db,saved['espirometria']['id'],'mock',for_update=False)
    entities={cls:db.scalar(select(func.count()).select_from(cls)) for cls in (Partner,PartnerUnit,Partnership)}
    maclin_id=db.scalar(select(PartnerCommercialRule.id).where(PartnerCommercialRule.codigo=='maclin'))
    approval=approve_alergocare_direct(db,users['admin'].id);db.commit()
    active=resolve_rule(db,'alergocare',DAY+timedelta(days=1))[0]
    assert approval['regra_anterior']==legacy_id and active.id!=legacy_id and active.fiscal_flow=='DIRECT'
    assert all(getattr(active,k)==getattr(legacy,k) for k in keys)
    assert active.vigencia_inicio==legacy.vigencia_inicio
    assert db.get(PartnerCommercialRule,legacy_id).fiscal_flow is None
    assert db.get(ExamPartnerShare,old_share).rule_id==legacy_id
    assert nfse.evaluate(db,saved['espirometria']['id'],'mock',for_update=False)==before
    assert all(db.scalar(select(func.count()).select_from(cls))==count for cls,count in entities.items())
    assert db.get(Partner,life.id).nome=='ALERGO LIFE' and db.get(PartnerUnit,life_unit.id).partner_id==life.id
    assert resolve_rule(db,'maclin',DAY)[0].id==maclin_id
    count=db.scalar(select(func.count()).select_from(PartnerCommercialRule))
    approve_alergocare_direct(db,users['admin'].id);db.commit()
    assert db.scalar(select(func.count()).select_from(PartnerCommercialRule))==count
    db.rollback()
    new=create(client,auth,person,'alergocare',DAY+timedelta(days=1))
    assert db.get(ExamPartnerShare,new['partilha_parceiro']['id']).rule_id==active.id
    assert nfse.evaluate(db,new['espirometria']['id'],'mock',for_update=False)['amount_snapshot']==D('123.45')
