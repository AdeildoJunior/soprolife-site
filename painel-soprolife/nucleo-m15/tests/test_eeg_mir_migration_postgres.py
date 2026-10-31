"""MIR additive migration retains both brands and leaves historical source NULL."""
import pytest
from alembic import command
from sqlalchemy import create_engine,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from app.models import Person,SpirometryExam,EegEnrollment,EegExam
from tests.test_nfse_migrations import postgres_url,config


def test_mir_upgrade_keeps_existing_eeg_and_spirometry(postgres_url):
    cfg=config(postgres_url);command.upgrade(cfg,'head')
    engine=create_engine(postgres_url,hide_parameters=True)
    with Session(engine) as db:
        p=Person(public_code='PES-009800',nome_completo='Pessoa Sintetica Migration',nome_normalizado='pessoa sintetica migration')
        db.add(p);db.flush()
        e=SpirometryExam(person_id=p.id,public_code='ESP-009800',status='Realizado')
        v=EegEnrollment(person_id=p.id);db.add_all([e,v]);db.flush()
        eeg=EegExam(enrollment_id=v.id,public_code='EEG-009800',modalidade_codigo='vigilia',status='agendado')
        db.add(eeg);db.commit()
        person_id,spirometry_id,eeg_id=p.id,e.id,eeg.id
    # Disposable-only: reproduce the exact pre-MIR schema with historical facts.
    command.downgrade(cfg,'a291029eeg01')
    with engine.connect() as db:
        before={table:db.scalar(text('SELECT count(*) FROM '+table)) for table in ('people','spirometry_exams','eeg_enrollments','eeg_exams','financial_entries','report_documents','eeg_report_documents')}
    command.upgrade(cfg,'head');command.check(cfg)
    with Session(engine) as db:
        assert db.get(SpirometryExam,spirometry_id).equipamento_fonte is None
        assert db.get(EegExam,eeg_id).modalidade_codigo=='vigilia'
        assert db.get(EegEnrollment,db.get(EegExam,eeg_id).enrollment_id).person_id==person_id
        assert db.get(SpirometryExam,spirometry_id).person_id==person_id
        for table,count in before.items():
            assert db.scalar(text('SELECT count(*) FROM '+table))==count
    with engine.begin() as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE spirometry_exams SET equipamento_fonte='NANO_EEG' WHERE id=:id"),{'id':spirometry_id})
    engine.dispose()
