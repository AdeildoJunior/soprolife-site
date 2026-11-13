"""PostgreSQL migration preserves history, guards events and refuses fact loss."""
from alembic import command
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.models import Lead, FunnelEvent, utcnow
from tests.test_nfse_migrations import postgres_url,config


def test_funnel_preserves_history_and_immutable_evidence(postgres_url):
    cfg=config(postgres_url)
    command.upgrade(cfg,'a04f1c202611')
    engine=create_engine(postgres_url,hide_parameters=True)
    with engine.begin() as db:
        db.execute(text("INSERT INTO people(id,public_code,nome_completo,nome_normalizado,status,created_at,updated_at,arquivado,nao_contatar) VALUES('synthetic-person','PES-900901','Pessoa Sintetica','pessoa sintetica','ativo',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,false,false)"))
        db.execute(text("INSERT INTO leads(id,public_code,person_id,etapa,created_at,updated_at,data_primeiro_contato_dia_assumido) VALUES('synthetic-legacy-lead','LEA-900901','synthetic-person','novo',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,false)"))
        db.execute(text("INSERT INTO spirometry_exams(id,public_code,person_id,status,created_at,updated_at,data_exame_dia_assumido,reteste_sem_cobranca) VALUES('synthetic-legacy-exam','ESP-900901','synthetic-person','Realizado',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,false,false)"))
    command.upgrade(cfg,'head');command.check(cfg)
    with engine.connect() as db:
        assert db.scalar(text("SELECT primeiro_contato_em FROM leads WHERE id='synthetic-legacy-lead'")) is None
        assert db.scalar(text("SELECT lead_id FROM spirometry_exams WHERE id='synthetic-legacy-exam'")) is None
        assert db.scalar(text('SELECT count(*) FROM funnel_events'))==0
    command.downgrade(cfg,'a04f1c202611');command.upgrade(cfg,'head')
    with Session(engine) as db:
        l=Lead(id='synthetic-tracked-lead',public_code='LEA-900902',person_id='synthetic-person',primeiro_contato_em=utcnow(),status_funil='NOVO_CONTATO')
        db.add(l);db.flush();db.add(FunnelEvent(lead_id=l.id,event_type='NOVO_CONTATO'));db.commit()
    for query in ['UPDATE funnel_events SET event_type=event_type','DELETE FROM funnel_events']:
        with engine.begin() as db:
            with pytest.raises(DBAPIError,match='append-only'):db.execute(text(query))
    with pytest.raises(RuntimeError,match='evidência'):command.downgrade(cfg,'a04f1c202611')
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM people'))==1
        assert db.scalar(text('SELECT count(*) FROM funnel_events'))==1
    engine.dispose()
