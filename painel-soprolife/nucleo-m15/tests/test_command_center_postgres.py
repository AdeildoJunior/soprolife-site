"""Real PostgreSQL 16: indexed bounded search and physically read-only views."""
import json
import os
import time
from datetime import datetime,timezone
import pytest
from alembic import command
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import Session
from tests.test_nfse_migrations import postgres_url,config
from tests.test_command_center import person,exam,physician,document,config as feature_flags
from app.models import Person
from app.normalize import normalize_name
from app.services import command_center as c,command_search as s

pytestmark=pytest.mark.skipif(not os.environ.get('M15_TEST_POSTGRES_URL'),reason='Disposable PostgreSQL required')

@pytest.fixture
def engine(postgres_url):
    command.upgrade(config(postgres_url),'head')
    engine=create_engine(postgres_url,hide_parameters=True)
    yield engine
    engine.dispose()


def test_indexes_migration_and_no_fact_rewrite(engine,postgres_url):
    with Session(engine) as db:
        p=person(db);db.commit();before=db.scalar(text('SELECT md5(row_to_json(p)::text) FROM people p'))
    command.check(config(postgres_url))
    command.downgrade(config(postgres_url),'a191126direct')
    with engine.connect() as db:assert db.scalar(text('SELECT md5(row_to_json(p)::text) FROM people p'))==before
    command.upgrade(config(postgres_url),'head');command.check(config(postgres_url))
    with engine.connect() as db:
        assert db.scalar(text('SELECT md5(row_to_json(p)::text) FROM people p'))==before
        indexes=dict(db.execute(text("SELECT indexname,indexdef FROM pg_indexes WHERE indexname LIKE 'ix_cc_%'")).all())
        assert len(indexes)==4
        assert 'USING gin' in indexes['ix_cc_people_name_fts']
        assert 'text_pattern_ops' in indexes['ix_cc_partner_name_prefix']


def test_index_plan_bounded_search_and_readonly_home(db,users,engine):
    for n in range(1500):person(db,'Pessoa Sintética '+str(n),f'PES-{n+1:06}')
    rare=person(db,'Pessoa Termoexclusivo Sintética','PES-990001')
    e=exam(db,rare,status='Realizado',data_exame=c.today(),data_exame_precisao='dia')
    db.commit()
    with engine.begin() as conn:conn.execute(text('ANALYZE people'))
    with engine.connect() as conn:
        conn.execute(text('SET enable_seqscan=off'))
        plan=conn.scalar(text("EXPLAIN (FORMAT JSON) SELECT id FROM people WHERE to_tsvector('simple',nome_normalizado) @@ to_tsquery('simple','termoexclusivo:*') LIMIT 4"))
        assert 'ix_cc_people_name_fts' in json.dumps(plan)
    user=users['admin'];before=db.scalar(text('SELECT count(*) FROM financial_entries'))
    started=time.monotonic()
    found=s.search(db,user,'soprolife','Termoexclusivo')
    assert found['total'] and found['total']<=12
    assert found['grupos'][0]['itens'][0]['destino']['id']==rare.id
    assert time.monotonic()-started<3 # bounded indexed lookup on a real engine
    db.rollback();db.execute(text('SET TRANSACTION READ ONLY'))
    home=c.summary(db,user,'soprolife')
    assert home['mes']['exames']==1 and home['financeiro']['bruto'] is None
    s.lookup(db,user,'soprolife','exame',e.id)
    assert db.scalar(text('SELECT count(*) FROM financial_entries'))==before


def test_clinical_scope_and_no_unauthorized_record_lookup(db,users):
    u,profile=physician(db,users);p=person(db);e=exam(db,p);d=document(db,e,users,profile)
    unassigned=exam(db,p,'ESP-900002');db.commit()
    assert s.search(db,u,'soprolife',e.public_code)['total']
    assert not s.search(db,u,'soprolife',unassigned.public_code)['total']
    with pytest.raises(Exception) as denied:s.lookup(db,u,'soprolife','exame',unassigned.id)
    assert denied.value.status_code==404
    assert s.lookup(db,u,'soprolife','exame',e.id)['destino']['id']==d.id
