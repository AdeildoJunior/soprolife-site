"""M27 — migração fiscal_artifacts: cria, é append-only, sobrevive a downgrade/upgrade."""
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


def test_fiscal_artifacts_table_created_and_append_only(tmp_path, monkeypatch):
    monkeypatch.delenv('M15_DATABASE_URL', raising=False)
    url = f'sqlite:///{tmp_path}/fiscal_artifacts.db'
    cfg = config(url)
    command.upgrade(cfg, 'head')
    command.check(cfg)
    eng = create_engine(url)
    assert 'fiscal_artifacts' in set(inspect(eng).get_table_names())

    user_id, person_id, exam_id, policy_id, document_id, prep_id, attempt_id = (
        str(uuid4()) for _ in range(7)
    )
    now = datetime.now(timezone.utc).isoformat()
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, nome, email, password_hash, ativo, created_at, updated_at) "
            "VALUES (:id, 'Synthetic', 'synthetic-m27@teste.invalid', 'x', 1, :now, :now)"
        ), {"id": user_id, "now": now})
        conn.execute(text(
            "INSERT INTO people (id, public_code, nome_completo, nome_normalizado, "
            "status, nao_contatar, arquivado, created_at, updated_at) "
            "VALUES (:id, 'PES-ART', 'Pessoa Sintética', 'pessoa sintetica', 'ativo', 0, 0, :now, :now)"
        ), {"id": person_id, "now": now})
        conn.execute(text(
            "INSERT INTO spirometry_exams (id, public_code, person_id, status, "
            "data_exame_dia_assumido, created_at, updated_at) "
            "VALUES (:id, 'ESP-ART', :person_id, 'Realizado', 0, :now, :now)"
        ), {"id": exam_id, "person_id": person_id, "now": now})
        conn.execute(text(
            "INSERT INTO fiscal_policies (id, version, environment, flow, service, "
            "effective_from, effective_to, validation_state, configuration, created_by, created_at) "
            "VALUES (:id, 'ART-v1', 'mock', 'HOME', 'spirometry', '2026-01-01', '2026-12-31', "
            "'draft', '{}', :user_id, :now)"
        ), {"id": policy_id, "user_id": user_id, "now": now})
        conn.execute(text(
            "INSERT INTO fiscal_documents (id, spirometry_exam_id, environment, state, "
            "eligibility, blocking_reasons, created_by, idempotency_key, "
            "idempotency_fingerprint, created_at, updated_at) "
            "VALUES (:id, :exam_id, 'mock', 'blocked', 'blocked', '[]', :user_id, "
            "'art-idem-key', 'art-fingerprint', :now, :now)"
        ), {"id": document_id, "exam_id": exam_id, "user_id": user_id, "now": now})
        conn.execute(text(
            "INSERT INTO fiscal_preparations (id, document_id, flow, blocking_reasons, "
            "fingerprint, created_by, created_at) "
            "VALUES (:id, :document_id, 'HOME', '[]', 'prep-fp', :user_id, :now)"
        ), {"id": prep_id, "document_id": document_id, "user_id": user_id, "now": now})
        conn.execute(text(
            "INSERT INTO fiscal_attempts (id, document_id, preparation_id, operation_id, "
            "operation, phase, number, provider, environment, outcome, reconciliation_required, "
            "actor_id, started_at, idempotency_key, idempotency_fingerprint) "
            "VALUES (:id, :document_id, :prep_id, 'op-1', 'issue', 'started', 1, 'mock', "
            "'mock', 'started', 0, :user_id, :now, 'attempt-idem', 'attempt-fp')"
        ), {"id": attempt_id, "document_id": document_id, "prep_id": prep_id,
            "user_id": user_id, "now": now})
        conn.execute(text(
            "INSERT INTO fiscal_artifacts (id, document_id, attempt_id, kind, "
            "storage_relative_path, sha256, size_bytes, created_by, created_at) "
            "VALUES (:id, :document_id, :attempt_id, 'dps_signed_xml', "
            "'fiscal/x/y/dps_signed_xml.xml', 'a' || substr(hex(randomblob(32)), 1, 63), "
            "42, :user_id, :now)"
        ), {"id": str(uuid4()), "document_id": document_id, "attempt_id": attempt_id,
            "user_id": user_id, "now": now})

    with pytest.raises(DatabaseError, match='immutable fiscal'):
        with eng.begin() as conn:
            conn.execute(text("UPDATE fiscal_artifacts SET size_bytes = 999"))
    with pytest.raises(DatabaseError, match='immutable fiscal'):
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM fiscal_artifacts"))

    with eng.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM fiscal_artifacts")) == 1
    eng.dispose()

    command.downgrade(cfg, 'f6a1d9e28b40')
    eng = create_engine(url)
    assert 'fiscal_artifacts' not in set(inspect(eng).get_table_names())
    eng.dispose()

    command.upgrade(cfg, 'head')
    command.check(cfg)
    eng = create_engine(url)
    assert 'fiscal_artifacts' in set(inspect(eng).get_table_names())
    with eng.connect() as conn:
        # Downgrade dropped the table (and its row) — re-upgrade starts empty.
        assert conn.scalar(text("SELECT count(*) FROM fiscal_artifacts")) == 0
    eng.dispose()


def test_fiscal_artifacts_kind_check_constraint(tmp_path, monkeypatch):
    monkeypatch.delenv('M15_DATABASE_URL', raising=False)
    url = f'sqlite:///{tmp_path}/fiscal_artifacts_kind.db'
    cfg = config(url)
    command.upgrade(cfg, 'head')
    eng = create_engine(url)
    now = datetime.now(timezone.utc).isoformat()
    user_id = str(uuid4())
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, nome, email, password_hash, ativo, created_at, updated_at) "
            "VALUES (:id, 'Synthetic', 'synthetic-kind@teste.invalid', 'x', 1, :now, :now)"
        ), {"id": user_id, "now": now})
    fake_sha256 = "a" * 64
    with pytest.raises(DatabaseError):
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO fiscal_artifacts (id, document_id, kind, storage_relative_path, "
                "sha256, size_bytes, created_by, created_at) VALUES "
                "(:id, :doc, 'not_a_real_kind', 'x', :sha256, 1, :user_id, :now)"
            ), {"id": str(uuid4()), "doc": str(uuid4()), "sha256": fake_sha256,
                "user_id": user_id, "now": now})
    eng.dispose()
