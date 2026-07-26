"""Migrações Alembic: sobem do zero e batem com o metadata dos modelos."""

from decimal import Decimal
import pathlib

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Consultation, FinancialEntry, Person, SpirometryExam

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


def test_m23_1_tem_exatamente_uma_nova_head(tmp_path, monkeypatch):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    cfg = _alembic_config(f"sqlite:///{tmp_path}/heads.db")
    assert ScriptDirectory.from_config(cfg).get_heads() == ["c9d5f7a31b42"]


def _popular_financeiro_pre_m23_1(engine, *, com_conflitos: bool):
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    pessoa = Person(
        public_code="PES-910001",
        nome_completo="Pessoa Sintetica Migracao",
        nome_normalizado="pessoa sintetica migracao",
    )
    session.add(pessoa)
    session.flush()
    exam = SpirometryExam(public_code="ESP-910001", person_id=pessoa.id)
    consultation = Consultation(public_code="CON-910001", person_id=pessoa.id)
    session.add_all([exam, consultation])
    session.flush()

    rows = [
        FinancialEntry(
            public_code="LAN-910001",
            tipo="receita",
            categoria=" Espirometria ",
            valor=Decimal("100.00"),
            status="Pendente",
            spirometry_exam_id=exam.id,
        ),
        FinancialEntry(
            public_code="LAN-910010",
            tipo="receita",
            categoria="Outro",
            valor=Decimal("5.00"),
            status="Pendente",
            spirometry_exam_id=exam.id,
        ),
        FinancialEntry(
            public_code="LAN-910011",
            tipo="despesa",
            categoria="Espirometria",
            valor=Decimal("3.00"),
            status="Pendente",
            spirometry_exam_id=exam.id,
        ),
        FinancialEntry(
            public_code="LAN-910020",
            tipo="receita",
            categoria=None,
            valor=Decimal("200.00"),
            status="Pendente",
            consultation_id=consultation.id,
        ),
        FinancialEntry(
            public_code="LAN-910021",
            tipo="repasse",
            categoria="Repasse ao médico",
            valor=Decimal("50.00"),
            status="Pendente",
            consultation_id=consultation.id,
        ),
    ]
    if com_conflitos:
        rows.extend(
            [
                FinancialEntry(
                    public_code="LAN-910002",
                    tipo="receita",
                    categoria="Espirometria\ufe0f",
                    valor=Decimal("101.00"),
                    status="Pendente",
                    spirometry_exam_id=exam.id,
                ),
                FinancialEntry(
                    public_code="LAN-910022",
                    tipo="receita",
                    categoria="Ｃｏｎｓｕｌｔａ",
                    valor=Decimal("201.00"),
                    status="Pendente",
                    consultation_id=consultation.id,
                ),
            ]
        )
    session.add_all(rows)
    session.commit()
    snapshot = session.execute(
        select(
            FinancialEntry.id,
            FinancialEntry.public_code,
            FinancialEntry.tipo,
            FinancialEntry.categoria,
            FinancialEntry.valor,
            FinancialEntry.spirometry_exam_id,
            FinancialEntry.consultation_id,
        ).order_by(FinancialEntry.public_code)
    ).all()
    session.close()
    return snapshot


def test_migracao_aborta_com_conflitos_historicos_sem_alterar_linhas(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/historico-conflitante.db"
    cfg = _alembic_config(url)
    command.upgrade(cfg, "b8c4e6d21a90")
    engine = create_engine(url)
    before = _popular_financeiro_pre_m23_1(engine, com_conflitos=True)

    with pytest.raises(RuntimeError, match="receitas próprias históricas") as caught:
        command.upgrade(cfg, "head")

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    after = session.execute(
        select(
            FinancialEntry.id,
            FinancialEntry.public_code,
            FinancialEntry.tipo,
            FinancialEntry.categoria,
            FinancialEntry.valor,
            FinancialEntry.spirometry_exam_id,
            FinancialEntry.consultation_id,
        ).order_by(FinancialEntry.public_code)
    ).all()
    revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    session.close()
    indexes = {ix["name"] for ix in inspect(engine).get_indexes("financial_entries")}
    engine.dispose()

    assert after == before
    assert revision == "b8c4e6d21a90"
    assert "uq_financial_entries_receita_espirometria" not in indexes
    assert "uq_financial_entries_receita_consulta" not in indexes
    # A falha só identifica códigos públicos seguros — nunca texto livre,
    # pessoa, valor ou categoria histórica.
    mensagem = str(caught.value)
    assert "LAN-910001" in mensagem and "LAN-910022" in mensagem
    assert "Pessoa Sintetica Migracao" not in mensagem
    assert "101.00" not in mensagem


def test_migracao_preserva_banco_moldado_sem_duplicatas_e_cria_indices(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/historico-valido.db"
    cfg = _alembic_config(url)
    command.upgrade(cfg, "b8c4e6d21a90")
    engine = create_engine(url)
    before = _popular_financeiro_pre_m23_1(engine, com_conflitos=False)

    command.upgrade(cfg, "head")
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    after = session.execute(
        select(
            FinancialEntry.id,
            FinancialEntry.public_code,
            FinancialEntry.tipo,
            FinancialEntry.categoria,
            FinancialEntry.valor,
            FinancialEntry.spirometry_exam_id,
            FinancialEntry.consultation_id,
        ).order_by(FinancialEntry.public_code)
    ).all()
    assert after == before

    exam_id = next(r.spirometry_exam_id for r in after if r.public_code == "LAN-910001")
    session.add(
        FinancialEntry(
            public_code="LAN-910099",
            tipo="receita",
            categoria="espirometria",
            valor=Decimal("1.00"),
            status="Pendente",
            spirometry_exam_id=exam_id,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.close()

    indexes = {ix["name"] for ix in inspect(engine).get_indexes("financial_entries")}
    engine.dispose()
    assert "uq_financial_entries_receita_espirometria" in indexes
    assert "uq_financial_entries_receita_consulta" in indexes
