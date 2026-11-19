"""Produção simulada em DB sintético: preparar não emite; confirmação é humana."""
import pytest
from sqlalchemy import select,func
from app.models import FiscalIssuanceRequest,FiscalAttempt
from app.services.partner_share_setup import configure
from tests.test_nfse_m66_production_command_center import prod_env,spool,world,SERVICE_DAY
from tests.test_partner_fiscal_direct import create


@pytest.mark.parametrize('code',['maclin','alergocare'])
def test_same_human_confirmation_for_full_gross(prod_env,spool,world,client,auth,db,users,code):
    configure(db,code,SERVICE_DAY,users['admin'].id);db.commit()
    data=create(client,auth,{'id':world['ready'].person_id},code,SERVICE_DAY)
    exam=data['espirometria']['id'];db.rollback()
    route=f'/api/v1/fiscal/producao/exames/{exam}/preparar'
    assert client.post(route,headers=auth('operacional')).status_code==403
    response=client.post(route,headers=auth('gestor'));assert response.status_code==200,response.text
    summary=response.json()
    assert summary['amount']=='123.45' and summary['confirmation_phrase']=='Confirmar emissão de R$ 123,45'
    assert summary['can_confirm'] is True
    route=f'/api/v1/fiscal/producao/documentos/{summary["document_id"]}/confirmar-emissao'
    wrong={'preparation_id':summary['preparation_id'],'amount':'86.41',
           'confirmation':summary['confirmation_phrase'],'idempotency_key':'synthetic-wrong-net'}
    assert client.post(route,headers=auth('gestor'),json=wrong).status_code==409
    assert client.post(route,headers=auth('gestor'),json={**wrong,'amount':'123.45','confirmation':''}).status_code==422
    assert db.scalar(select(func.count()).select_from(FiscalIssuanceRequest))==0
    assert db.scalar(select(func.count()).select_from(FiscalAttempt).where(
        FiscalAttempt.document_id==summary['document_id']))==0
    assert list(spool.iterdir())==[]
