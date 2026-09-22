"""Fiscal migration cycle and SQL-level append-only constraints on disposable DB."""
from pathlib import Path
from datetime import date
import os
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session
from sqlalchemy.engine import make_url

from app.models import User, Person, SpirometryExam, FinancialEntry, FiscalPolicy, FiscalDocument
from app.config import Settings
from app.services import nfse
from tests.test_nfse_foundation import policy_payload

ROOT = Path(__file__).resolve().parents[1]


def config(url):
    cfg = Config(str(ROOT / 'alembic.ini'))
    cfg.set_main_option('script_location', str(ROOT / 'migrations'))
    cfg.set_main_option('sqlalchemy.url', url)
    return cfg


@pytest.fixture
def postgres_url(monkeypatch):
    """Create/drop only a uniquely named database on an explicit TEST server."""
    base = os.environ.get('M15_TEST_POSTGRES_URL')
    if not base:
        pytest.skip('Disposable PostgreSQL unavailable: M15_TEST_POSTGRES_URL')
    monkeypatch.delenv('M15_DATABASE_URL', raising=False)
    name = 'nfse_test_' + uuid4().hex
    url = make_url(base)
    assert url.get_backend_name() == 'postgresql'
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.mark.parametrize('backend', ['sqlite', 'postgresql'])
def test_cycle_and_database_evidence_guards(tmp_path, monkeypatch, request, backend):
    monkeypatch.delenv('M15_DATABASE_URL', raising=False)
    url = (request.getfixturevalue('postgres_url') if backend == 'postgresql'
           else f'sqlite:///{tmp_path}/fiscal.db')
    cfg = config(url)
    command.upgrade(cfg, 'head')
    command.check(cfg)
    eng = create_engine(url)
    with Session(eng, expire_on_commit=False) as db:
        user = User(nome='Synthetic operator', email='nfse@synthetic.invalid', password_hash='unusable')
        person = Person(public_code='PES-FISCAL', nome_completo='Pessoa Sintética', nome_normalizado='pessoa sintetica')
        db.add_all([user, person]); db.flush()
        exam = SpirometryExam(public_code='ESP-FISCAL', person_id=person.id,
                               status='Realizado', data_exame=date(2026, 8, 10), modalidade='residencial')
        db.add(exam); db.flush()
        db.add(FinancialEntry(public_code='LAN-FISCAL', tipo='receita', categoria='Espirometria',
                              valor=12, status='Recebido', spirometry_exam_id=exam.id))
        db.commit()
        nfse.create_policy(db, policy_payload(), user.id)
        settings = Settings(nfse_enabled=True)
        doc = nfse.prepare(db, exam.id, settings, user.id)
        nfse.operate(db, doc.id, 'issue', 'migration-issue', settings, user.id)
    for table in ['fiscal_policies', 'fiscal_preparations', 'fiscal_attempts']:
        for statement in [f'UPDATE {table} SET id=id', f'DELETE FROM {table}']:
            with pytest.raises(DatabaseError, match='immutable fiscal'):
                with eng.begin() as conn: conn.execute(text(statement))
    for statement in ["UPDATE fiscal_documents SET environment='production'", 'DELETE FROM fiscal_documents']:
        with pytest.raises(DatabaseError, match='immutable fiscal'):
            with eng.begin() as conn: conn.execute(text(statement))
    eng.dispose()
    # Downgrade one step, to the M26.9 parent (d6a9f20c3e41): the fiscal tables
    # go away, but physician_transfers (M26.9) must survive untouched.
    command.downgrade(cfg, 'd6a9f20c3e41')
    eng = create_engine(url)
    after_down = set(inspect(eng).get_table_names())
    assert not ({'fiscal_policies', 'fiscal_documents', 'fiscal_preparations', 'fiscal_attempts'} & after_down)
    assert 'physician_transfers' in after_down
    eng.dispose()
    command.upgrade(cfg, 'head')
    command.check(cfg)
    eng = create_engine(url)
    assert {'fiscal_policies', 'fiscal_documents', 'fiscal_preparations', 'fiscal_attempts', 'physician_transfers'} <= set(inspect(eng).get_table_names())
    with eng.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM financial_entries')) == 1
        assert conn.scalar(text('SELECT count(*) FROM fiscal_documents')) == 0
    eng.dispose()
