"""Regressão M15.8 em PostgreSQL 16 REAL: import_batches.status VARCHAR(64).

O defeito só se manifestava em PostgreSQL (SQLite não impõe comprimento):
a transação única da execução multiaba abortava com
StringDataRightTruncation ao gravar STATUS_EXECUTADO. Estes testes provam,
contra PostgreSQL real, que a coluna aceita todos os status definidos, que a
execução multiaba completa comita, que a falha no meio continua atômica e
que a proteção de replay permanece. Pulado sem M15_TEST_POSTGRES_URL —
execute o ciclo oficial com `bash scripts/test-postgres-efemero.sh`.
"""

import os
import pathlib

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.migration import executor
from app.migration.executor import (
    confirmation_phrase_multiaba,
    execute_multi_sheet,
)
from app.models import ImportBatch, MigrationProvenance, Person

from tests.conftest import _make_user
from tests.test_import_batch_status import REVISAO_ANTERIOR, TODOS_OS_STATUS
from tests.test_multisheet_execute import (
    ADMIN,
    contar,
    executar_ok,
    preparar,
)

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


def _reset_schema(engine):
    """Zera o banco SEM passar pelo downgrade: dados destes testes incluem
    status > 20 caracteres, que o guard anti-truncamento de de497f0df152
    bloquearia (corretamente) num `alembic downgrade base`."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture()
def pg_engine(monkeypatch):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    engine = create_engine(PG_URL)
    _reset_schema(engine)
    command.upgrade(_alembic_config(), "head")
    yield engine
    _reset_schema(engine)  # banco limpo para os demais módulos PG
    engine.dispose()


@pytest.fixture()
def db(pg_engine):
    SessionLocal = sessionmaker(bind=pg_engine, expire_on_commit=False)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def users(db):
    return {
        "admin": _make_user(db, ADMIN, "admin"),
        "gestor": _make_user(db, "gestor@teste.local", "gestor"),
    }


def test_coluna_status_com_64_no_postgres(pg_engine):
    colunas = {c["name"]: c for c in inspect(pg_engine).get_columns("import_batches")}
    assert colunas["status"]["type"].length == 64
    assert colunas["status"]["type"].length == ImportBatch.status.type.length


def test_postgres_aceita_todos_os_status_definidos(pg_engine, db, users):
    batch = ImportBatch(
        source_type="multi_sheet", source_name="sintetico.json",
        sha256="abc123", modo="dry_run", status="dry_run")
    db.add(batch)
    db.commit()
    for status in TODOS_OS_STATUS:
        batch.status = status
        db.commit()  # VARCHAR imposto pelo PostgreSQL: commit real por valor
        db.refresh(batch)
        assert batch.status == status


def test_execucao_multiaba_real_comita_status_no_postgres(
    pg_engine, db, users, tmp_path,
):
    """O cenário exato que falhou em produção: execução multiaba completa,
    incluindo o UPDATE de status para STATUS_EXECUTADO, contra PostgreSQL."""
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    resultado = executar_ok(db, tmp_path, users, nome, batch_id, evidencia)
    assert resultado["ok"] is True
    assert resultado["status"] == executor.STATUS_EXECUTADO

    # sessão NOVA: prova que o status longo foi de fato comitado no banco
    verificacao = sessionmaker(bind=pg_engine)()
    try:
        exec_batch = verificacao.get(
            ImportBatch, resultado["batch_execucao_id"])
        assert exec_batch.status == executor.STATUS_EXECUTADO
        assert exec_batch.modo == "executado"
        assert contar(verificacao, Person) > 0
    finally:
        verificacao.close()

    # proteção de replay intacta: reexecutar o MESMO lote não duplica nada
    pessoas = contar(db, Person)
    segunda = execute_multi_sheet(
        db, nome, batch_id, confirmation_phrase_multiaba(batch_id),
        evidencia, admin_email=ADMIN, base_dir=tmp_path)
    assert segunda["status"] == "ja_executado"
    assert segunda["novas_linhas"] == 0
    assert contar(db, Person) == pessoas


def test_falha_no_meio_reverte_tudo_no_postgres(
    pg_engine, db, users, tmp_path, monkeypatch,
):
    nome, batch_id, evidencia = preparar(db, tmp_path, users)
    original = executor._instanciar

    def falha_sintetica(db_, op, refs, batch_id_):
        if op.entidade == "financial_entries":
            raise RuntimeError("falha sintetica no meio da execucao")
        return original(db_, op, refs, batch_id_)

    monkeypatch.setattr(executor, "_instanciar", falha_sintetica)
    with pytest.raises(RuntimeError):
        execute_multi_sheet(
            db, nome, batch_id, confirmation_phrase_multiaba(batch_id),
            evidencia, admin_email=ADMIN, base_dir=tmp_path)
    db.rollback()
    assert contar(db, MigrationProvenance) == 0
    assert contar(db, Person) == 0
    assert db.execute(select(ImportBatch).where(
        ImportBatch.modo == "executado")).scalars().first() is None


def test_downgrade_bloqueia_truncamento_no_postgres(pg_engine, db):
    batch = ImportBatch(
        source_type="multi_sheet", source_name="sintetico.json",
        sha256="abc123", modo="dry_run",
        status=executor.STATUS_EXECUTADO)
    db.add(batch)
    db.commit()
    batch_id = batch.id
    db.close()
    cfg = _alembic_config()
    with pytest.raises(RuntimeError, match="truncad"):
        command.downgrade(cfg, REVISAO_ANTERIOR)
    engine = create_engine(PG_URL)
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM import_batches WHERE id = :id"), {"id": batch_id})
    command.downgrade(cfg, REVISAO_ANTERIOR)
    colunas = {c["name"]: c for c in inspect(engine).get_columns("import_batches")}
    assert colunas["status"]["type"].length == 20
    engine.dispose()
