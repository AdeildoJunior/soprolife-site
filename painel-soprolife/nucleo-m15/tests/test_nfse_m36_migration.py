"""M36 — migração dps_number_sequences/dps_number_allocations: cria, aloca é
mutável (é o contador), alocação é append-only (SQL-level), sobrevive a
downgrade/upgrade."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError
import pytest

ROOT = Path(__file__).resolve().parents[1]


def config(url):
    cfg = Config(str(ROOT / 'alembic.ini'))
    cfg.set_main_option('script_location', str(ROOT / 'migrations'))
    cfg.set_main_option('sqlalchemy.url', url)
    return cfg


def test_dps_number_tables_created_and_allocation_is_append_only(tmp_path, monkeypatch):
    monkeypatch.delenv('M15_DATABASE_URL', raising=False)
    url = f'sqlite:///{tmp_path}/dps_numbering.db'
    cfg = config(url)
    command.upgrade(cfg, 'head')
    command.check(cfg)
    eng = create_engine(url)
    tables = set(inspect(eng).get_table_names())
    assert 'dps_number_sequences' in tables
    assert 'dps_number_allocations' in tables

    user_id, person_id, exam_id, document_id = (str(uuid4()) for _ in range(4))
    now = datetime.now(timezone.utc).isoformat()
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, nome, email, password_hash, ativo, created_at, updated_at) "
            "VALUES (:id, 'Synthetic', 'synthetic-m36@teste.invalid', 'x', 1, :now, :now)"
        ), {"id": user_id, "now": now})
        conn.execute(text(
            "INSERT INTO people (id, public_code, nome_completo, nome_normalizado, "
            "status, nao_contatar, arquivado, created_at, updated_at) "
            "VALUES (:id, 'PES-DPSMIG', 'Pessoa Sintética', 'pessoa sintetica', 'ativo', 0, 0, :now, :now)"
        ), {"id": person_id, "now": now})
        conn.execute(text(
            "INSERT INTO spirometry_exams (id, public_code, person_id, status, "
            "data_exame_dia_assumido, created_at, updated_at) "
            "VALUES (:id, 'ESP-DPSMIG', :person_id, 'Realizado', 0, :now, :now)"
        ), {"id": exam_id, "person_id": person_id, "now": now})
        conn.execute(text(
            "INSERT INTO fiscal_documents (id, spirometry_exam_id, environment, state, "
            "eligibility, blocking_reasons, created_by, idempotency_key, "
            "idempotency_fingerprint, created_at, updated_at) "
            "VALUES (:id, :exam_id, 'restricted', 'pending', 'eligible', '[]', :user_id, "
            "'dpsmig-idem-key', 'dpsmig-fingerprint', :now, :now)"
        ), {"id": document_id, "exam_id": exam_id, "user_id": user_id, "now": now})

        # The sequence IS mutable — this is the counter itself, must accept UPDATE.
        conn.execute(text(
            "INSERT INTO dps_number_sequences (scope_key, next_value) VALUES ('3304557:2:63544026000110:00001', 1)"
        ))
        conn.execute(text(
            "UPDATE dps_number_sequences SET next_value = 2 WHERE scope_key = '3304557:2:63544026000110:00001'"
        ))

        conn.execute(text(
            "INSERT INTO dps_number_allocations (document_id, scope_key, dps_number, allocated_at) "
            "VALUES (:document_id, '3304557:2:63544026000110:00001', 1, :now)"
        ), {"document_id": document_id, "now": now})

    with pytest.raises(DatabaseError, match='immutable fiscal'):
        with eng.begin() as conn:
            conn.execute(text("UPDATE dps_number_allocations SET dps_number = 999"))
    with pytest.raises(DatabaseError, match='immutable fiscal'):
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM dps_number_allocations"))

    with eng.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM dps_number_allocations")) == 1
        assert conn.scalar(text("SELECT dps_number FROM dps_number_allocations WHERE document_id = :id"),
                           {"id": document_id}) == 1
        assert conn.scalar(text("SELECT next_value FROM dps_number_sequences")) == 2
    eng.dispose()


def test_dps_number_migration_survives_downgrade_upgrade(tmp_path, monkeypatch):
    monkeypatch.delenv('M15_DATABASE_URL', raising=False)
    url = f'sqlite:///{tmp_path}/dps_numbering_cycle.db'
    cfg = config(url)
    command.upgrade(cfg, 'head')
    command.downgrade(cfg, '-1')
    eng = create_engine(url)
    tables = set(inspect(eng).get_table_names())
    assert 'dps_number_sequences' not in tables
    assert 'dps_number_allocations' not in tables
    eng.dispose()
    command.upgrade(cfg, 'head')
    eng = create_engine(url)
    tables = set(inspect(eng).get_table_names())
    assert 'dps_number_sequences' in tables
    assert 'dps_number_allocations' in tables
    eng.dispose()
