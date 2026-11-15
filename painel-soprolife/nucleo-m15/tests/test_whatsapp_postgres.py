"""Real PostgreSQL 16, isolated database, additive migration and concurrency."""
from concurrent.futures import ThreadPoolExecutor
from alembic import command
import pytest
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import Session
from app.models import Person, PersonContact, Lead, FunnelEvent, WhatsAppIntake, WhatsAppEvent
from app.config import Settings
from app.services import whatsapp as w
from tests.test_nfse_migrations import postgres_url, config
from tests.test_whatsapp import payload, SECRET, IDENTITY, STAMP


def test_additive_schema_no_backfill_and_safe_rollback(postgres_url):
    cfg=config(postgres_url)
    command.upgrade(cfg,'f131126funil')
    engine=create_engine(postgres_url,hide_parameters=True)
    with Session(engine) as db:
        db.add(Person(id='synthetic-person',public_code='PES-WAPG',nome_completo='Pessoa Sintética',nome_normalizado='pessoa sintetica'))
        db.commit()
    command.upgrade(cfg,'head');command.check(cfg)
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM people'))==1
        assert db.scalar(text('SELECT count(*) FROM whatsapp_events'))==0
        assert db.scalar(text('SELECT count(*) FROM whatsapp_intakes'))==0
    command.downgrade(cfg,'f131126funil');command.upgrade(cfg,'head')
    settings=Settings(whatsapp_integration_enabled=True,whatsapp_verify_token='synthetic-verify',
        whatsapp_app_secret=SECRET,whatsapp_identity_key=IDENTITY,
        whatsapp_phone_number_id='100000000000001',whatsapp_waba_id='100000000000002')
    with Session(engine) as db:w.process(db,w.parse(payload(),settings))
    with pytest.raises(RuntimeError,match='evidência'):
        command.downgrade(cfg,'f131126funil')
    engine.dispose()


def test_concurrent_retries_and_distinct_messages_one_contact(postgres_url):
    command.upgrade(config(postgres_url),'head')
    engine=create_engine(postgres_url,hide_parameters=True,pool_size=20,max_overflow=0)
    settings=Settings(whatsapp_integration_enabled=True,whatsapp_verify_token='synthetic-verify',
        whatsapp_app_secret=SECRET,whatsapp_identity_key=IDENTITY,
        whatsapp_phone_number_id='100000000000001',whatsapp_waba_id='100000000000002')
    with Session(engine) as db:
        p=Person(id='synthetic-person',public_code='PES-WAPG',nome_completo='Pessoa Sintética',nome_normalizado='pessoa sintetica')
        db.add(p);db.flush()
        db.add(PersonContact(person_id=p.id,tipo='whatsapp',valor='(21) 0000-9001',valor_normalizado='552100009001'))
        db.commit()
    def receive(item):
        with Session(engine) as db:w.process(db,w.parse(item,settings))
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(receive,[payload() for _ in range(20)]))
        list(pool.map(receive,[payload('synthetic-more-'+str(i),stamp=STAMP+i+1) for i in range(20)]))
        list(pool.map(receive,[payload('echo-retry',direction='out',stamp=STAMP+100) for _ in range(20)]))
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Person))==1
        assert db.scalar(select(func.count()).select_from(Lead))==1
        assert db.scalar(select(func.count()).select_from(WhatsAppIntake))==1
        assert db.scalar(select(func.count()).select_from(WhatsAppEvent))==22
        assert db.scalar(select(func.count()).select_from(FunnelEvent))==2
        assert db.scalar(select(Lead)).primeira_resposta_em.timestamp()==STAMP+100
    engine.dispose()
