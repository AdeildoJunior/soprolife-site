"""Migrações Alembic: sobem do zero e batem com o metadata dos modelos."""

import pathlib

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.db import Base

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_upgrade_head_cria_todas_as_tabelas(tmp_path, monkeypatch):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/migracao.db"
    command.upgrade(_alembic_config(url), "head")
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    expected = set(Base.metadata.tables.keys())
    assert tables == expected, f"faltam: {expected - tables}; sobram: {tables - expected}"
    engine.dispose()


def test_indices_parciais_followup_pendente(tmp_path, monkeypatch):
    """Dois índices: origem preenchida e origem NULL (dedupe completo)."""
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/migracao2.db"
    command.upgrade(_alembic_config(url), "head")
    engine = create_engine(url)
    indexes = inspect(engine).get_indexes("followups")
    names = {ix["name"] for ix in indexes}
    assert "uq_followup_pendente_origem" in names
    assert "uq_followup_pendente_sem_origem" in names
    engine.dispose()


def test_downgrade_base_remove_tudo(tmp_path, monkeypatch):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/migracao3.db"
    cfg = _alembic_config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set()
    engine.dispose()


def test_ciclo_repetido_upgrade_downgrade(tmp_path, monkeypatch):
    """upgrade -> downgrade -> upgrade repetidos sem resíduo nem erro."""
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/migracao4.db"
    cfg = _alembic_config(url)
    for _ in range(2):
        command.upgrade(cfg, "head")
        command.check(cfg)
        command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables.keys())
    engine.dispose()


def test_preseed_das_sequencias(tmp_path, monkeypatch):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/migracao5.db"
    command.upgrade(_alembic_config(url), "head")
    engine = create_engine(url)
    from sqlalchemy import text

    with engine.connect() as conn:
        prefixes = sorted(r[0] for r in conn.execute(text("SELECT prefix FROM code_sequences")))
    assert prefixes == sorted(
        ["PES", "LEA", "ESP", "CON", "CLI", "UNI", "CTT", "PAR", "ENC", "INT", "FUP", "LAN"]
    )
    engine.dispose()
