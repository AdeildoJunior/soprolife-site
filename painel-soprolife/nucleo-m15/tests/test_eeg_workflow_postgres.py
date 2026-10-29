"""Provas PG16: evidência imutável e downgrade que preserva fatos EEG."""
from alembic import command
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
import pytest
from app.models import User,Person,EegEnrollment,EegExam,EegTechnicalFile,EegCase,EegCaseRevision,EegPreparation,EegDocument
from tests.test_nfse_migrations import postgres_url,config


def test_eeg_evidence_guards_and_non_destructive_downgrade(postgres_url):
    cfg=config(postgres_url);command.upgrade(cfg,'head');command.check(cfg)
    eng=create_engine(postgres_url)
    with Session(eng) as db:
        u=User(email='eeg-immutable@example.invalid',nome='Operador Sintetico',password_hash='unused');db.add(u)
        p=Person(public_code='PES-PGEEG',nome_completo='Pessoa Sintetica',nome_normalizado='pessoa sintetica');db.add(p);db.flush()
        v=EegEnrollment(person_id=p.id);db.add(v);db.flush()
        e=EegExam(public_code='EEG-PGEEG',enrollment_id=v.id,modalidade_codigo='vigilia');db.add(e);db.flush()
        f=EegTechnicalFile(exam_id=e.id,armazenamento_origem='equipamento');db.add(f);db.flush()
        c=EegCase(exam_id=e.id);db.add(c);db.flush()
        db.add(EegCaseRevision(case_id=c.id,numero=1,conteudo={'descricao':'Sintetico'},professional_snapshot={'nome':'Profissional Sintetico'},criado_por=u.id))
        db.add(EegPreparation(modalidade_codigo='vigilia',texto='Texto sintético',referencia_aprovacao='SYNTH-01',aprovado_por=u.id))
        db.add(EegDocument(titulo='Documento Sintetico',categoria='contratos',storage_path='eeg/synthetic.pdf',sha256='0'*64,size_bytes=1,mime_type='application/pdf',extensao='pdf',criado_por=u.id));db.commit()
    for table in ('eeg_case_revisions','eeg_preparations','eeg_technical_files','eeg_documents'):
        for action in (f'UPDATE {table} SET id=id',f'DELETE FROM {table}'):
            with eng.begin() as conn:
                with pytest.raises(DBAPIError,match='EEG evidence is append-only'):conn.execute(text(action))
    with pytest.raises(RuntimeError,match='downgrade destrutivo recusado'):command.downgrade(cfg,'f1e4a2b72029')
    with eng.connect() as conn:assert conn.scalar(text('SELECT count(*) FROM eeg_case_revisions'))==1
    eng.dispose()
