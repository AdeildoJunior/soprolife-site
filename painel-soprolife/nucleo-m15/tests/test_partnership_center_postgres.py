"""PostgreSQL real, read-only projection and exact cents over canonical shares."""
import os
from datetime import date
import pytest
from sqlalchemy import text
from app.services import partnership_center as pc
from tests.test_command_center_postgres import postgres_url,config,engine
from tests.test_partnership_center import canonical,exam
pytestmark=pytest.mark.skipif(not os.environ.get('M15_TEST_POSTGRES_URL'),reason='Disposable PostgreSQL required')


def test_real_pg_readonly_cents_and_identity(db,canonical,users):
    exam(db,canonical,users=users)
    exam(db,canonical,'alergocare',2,'220.00','parceiro',users=users);db.commit()
    before=db.scalar(text('SELECT count(*) FROM financial_entries'))
    db.rollback();db.execute(text('SET TRANSACTION READ ONLY'))
    data,rows=pc.project(db,True,date(2026,11,1),date(2026,11,30))
    assert data['consolidado']['bruto']=='343.45'
    assert data['consolidado']['soprolife']=='240.41'
    assert data['consolidado']['a_pagar']=='37.04'
    assert data['consolidado']['a_receber']=='154.00'
    assert all(r['partner_id']==canonical[r['codigo']]['partner_id'] for r in data['parcerias'])
    assert len(rows['maclin'])==1 and len(rows['alergocare'])==1
    assert before==db.scalar(text('SELECT count(*) FROM financial_entries'))
