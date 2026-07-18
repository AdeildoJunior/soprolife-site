"""Migração e concorrência em PostgreSQL 16 REAL.

Pulado apenas quando não há runtime/serviço disponível — defina
M15_TEST_POSTGRES_URL (ex.: postgresql+psycopg://postgres:senha@127.0.0.1:55432/m15_teste).
Execute o ciclo oficial com `bash scripts/test-postgres-efemero.sh`; ele usa e
remove exclusivamente o container descartável `m15-pg-teste`.
"""

import os
import pathlib
import threading

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.ids import allocate_public_code
from app.models import FinancialEntry, Person
from app.normalize import normalize_name
from app.services.followup import schedule_followup
from app.services.idempotency import idempotent_create, payload_fingerprint

PG_URL = os.environ.get("M15_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="PostgreSQL de teste indisponível — defina M15_TEST_POSTGRES_URL",
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", PG_URL)
    return cfg


@pytest.fixture(scope="module")
def pg_engine():
    os.environ.pop("M15_DATABASE_URL", None)
    cfg = _alembic_config()
    command.downgrade(cfg, "base")   # estado limpo
    command.upgrade(cfg, "head")
    engine = create_engine(PG_URL)
    yield engine
    engine.dispose()


def test_ciclo_upgrade_downgrade_upgrade_pg(pg_engine):
    cfg = _alembic_config()
    command.downgrade(cfg, "base")
    engine = create_engine(PG_URL)
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set()
    command.upgrade(cfg, "head")
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables.keys())
    # alembic check: nenhum drift entre modelos e migração
    command.check(cfg)
    engine.dispose()


def test_fk_ciclica_e_preseed_pg(pg_engine):
    insp = inspect(pg_engine)
    fks = {fk["name"] for fk in insp.get_foreign_keys("partner_referrals")}
    assert "fk_partner_referrals_financial_entry_id_financial_entries" in fks
    with pg_engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM code_sequences")).scalar()
        assert count == 12


def test_audit_append_only_trigger_pg(pg_engine):
    with pg_engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO audit_logs (ts_utc, acao) VALUES (now(), 'teste-trigger')"
        ))
        conn.commit()
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("UPDATE audit_logs SET acao='x' WHERE acao='teste-trigger'"))
        conn.rollback()
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("DELETE FROM audit_logs WHERE acao='teste-trigger'"))
        conn.rollback()


def test_codigos_publicos_concorrentes_pg(pg_engine):
    """4 threads × 5 alocações simultâneas -> 20 códigos únicos."""
    SessionLocal = sessionmaker(bind=pg_engine)
    results: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def worker():
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            for _ in range(5):
                code = allocate_public_code(session, "interactions")
                session.commit()
                results.append(code)
        except Exception as exc:  # pragma: no cover - diagnóstico
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert len(results) == 20
    assert len(set(results)) == 20


def test_idempotency_key_concorrente_pg(pg_engine):
    """Duas transações com a MESMA chave: uma cria, a outra recebe a existente."""
    SessionLocal = sessionmaker(bind=pg_engine)
    payload = {"tipo": "receita", "valor": "123.00"}
    key = "LAN-RACE-000001"
    outcome: list[tuple[str, bool]] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def worker():
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)

            def factory(k, fingerprint):
                entry = FinancialEntry(
                    public_code=allocate_public_code(session, "financial_entries"),
                    tipo="receita", valor=123, moeda="BRL",
                    data_competencia_dia_assumido=False,
                    status="Pendente",
                    idempotency_key=k, idempotency_fingerprint=fingerprint,
                )
                session.add(entry)
                session.flush()
                return entry

            obj, ja_existia = idempotent_create(
                session, FinancialEntry, key, payload, factory)
            session.commit()
            outcome.append((obj.id, ja_existia))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert len(outcome) == 2
    ids = {o[0] for o in outcome}
    assert len(ids) == 1  # mesmo registro para as duas
    assert sorted(o[1] for o in outcome) == [False, True]
    assert payload_fingerprint(payload)  # fingerprint estável


def test_followup_nulo_concorrente_pg(pg_engine):
    """Duas transações criando follow-up manual (origem NULL) -> um só pendente."""
    SessionLocal = sessionmaker(bind=pg_engine)
    setup = SessionLocal()
    person = Person(
        public_code=allocate_public_code(setup, "people"),
        nome_completo="Corrida Followup PG",
        nome_normalizado=normalize_name("Corrida Followup PG"),
    )
    setup.add(person)
    setup.commit()
    person_id = person.id
    setup.close()

    outcome: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def worker():
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            p = session.get(Person, person_id)
            fup, motivo = schedule_followup(session, p, "manual")
            session.commit()
            outcome.append(f"{fup.id}:{motivo}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert len(outcome) == 2
    ids = {o.split(":")[0] for o in outcome}
    assert len(ids) == 1
    motivos = sorted(o.split(":")[1] for o in outcome)
    assert motivos == ["criado", "ja_existente"]
