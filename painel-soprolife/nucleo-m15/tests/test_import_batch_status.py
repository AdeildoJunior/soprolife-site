"""Regressão M15.8: comprimento de import_batches.status.

O defeito original: STATUS_EXECUTADO ("executado_aguardando_reconciliacao",
34 caracteres) estourava o VARCHAR(20) original e abortava a transação única
da execução multiaba em PostgreSQL (StringDataRightTruncation) — invisível
em SQLite, que não impõe comprimento declarado. Aqui ficam as verificações
independentes de backend; as de PostgreSQL real estão em
test_import_batch_status_postgres.py.
"""

import pathlib

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.migration import executor
from app.models import ImportBatch

ROOT = pathlib.Path(__file__).resolve().parents[1]

REVISAO_ANTERIOR = "d91e7a2b4c68"

# Vocabulário completo de ImportBatch.status hoje: literais gravados por
# csv_import/multisheet/executor + constantes do executor multiaba.
TODOS_OS_STATUS = (
    "dry_run",
    "dry_run_bloqueado",
    "processando",
    "executando",
    "executado",
    executor.STATUS_EXECUTADO,
    executor.STATUS_CONCLUIDO,
    executor.STATUS_DIVERGENTE,
    executor.STATUS_REVERTIDO,
)


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_todos_os_status_definidos_cabem_na_coluna():
    limite = ImportBatch.status.type.length
    assert limite == 64
    for status in TODOS_OS_STATUS:
        assert len(status) <= limite, (
            f"status {status!r} ({len(status)}) excede VARCHAR({limite})")


def test_status_executado_nao_cabia_na_coluna_antiga():
    """Documenta o defeito: o valor real não cabia no VARCHAR(20) original."""
    assert len(executor.STATUS_EXECUTADO) == 34
    assert len(executor.STATUS_DIVERGENTE) == 25


def _inserir_batch(engine, status: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO import_batches (id, source_type, source_name, "
            "sha256, modo, status, total_rows, valid_rows, rejected_rows, "
            "ambiguous_rows, created_at) VALUES ('teste-status-longo', "
            "'multi_sheet', 'sintetico.json', 'abc123', 'dry_run', "
            f"'{status}', 0, 0, 0, 0, CURRENT_TIMESTAMP)"
        ))


def test_downgrade_bloqueia_truncamento_de_status(tmp_path, monkeypatch):
    """Downgrade nunca trunca silenciosamente: com status > 20 persistido,
    a revisão de497f0df152 falha com clareza em vez de destruir o valor."""
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/status_longo.db"
    cfg = _alembic_config(url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    _inserir_batch(engine, executor.STATUS_EXECUTADO)
    with pytest.raises(RuntimeError, match="truncad"):
        command.downgrade(cfg, REVISAO_ANTERIOR)
    # removido o valor longo, o downgrade volta a ser permitido
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM import_batches WHERE id = 'teste-status-longo'"))
    command.downgrade(cfg, REVISAO_ANTERIOR)
    engine.dispose()
