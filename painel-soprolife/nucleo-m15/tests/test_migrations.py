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


def test_m24a_remove_filename_sintetico_e_migration_e_reversivel(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/m24a-privacy.db"
    cfg = _alembic_config(url)
    command.upgrade(cfg, "5f0aea639d3d")
    engine = create_engine(url)
    synthetic_id = "10000000-0000-0000-0000-000000000001"
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO report_documents (
                    id, spirometry_exam_id, status,
                    original_filename_display, created_by_user_id,
                    created_at, updated_at
                ) VALUES (
                    :id, :exam_id, 'rascunho',
                    'TESTE-APAGAR-nome-potencial.pdf', :user_id,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": synthetic_id,
                "exam_id": "20000000-0000-0000-0000-000000000001",
                "user_id": "30000000-0000-0000-0000-000000000001",
            },
        )
    command.upgrade(cfg, "head")
    columns = {column["name"] for column in inspect(engine).get_columns("report_documents")}
    assert "original_filename_display" not in columns
    assert "corrects_document_id" in columns
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM report_documents WHERE id=:id"),
            {"id": synthetic_id},
        ).scalar_one() == 1

    command.downgrade(cfg, "5f0aea639d3d")
    columns = {column["name"] for column in inspect(engine).get_columns("report_documents")}
    assert "original_filename_display" in columns
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT original_filename_display FROM report_documents WHERE id=:id"
            ),
            {"id": synthetic_id},
        ).scalar_one() is None
    command.upgrade(cfg, "head")
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
        [
            "PES",
            "LEA",
            "ESP",
            "CON",
            "CLI",
            "UNI",
            "CTT",
            "PAR",
            "ENC",
            "INT",
            "FUP",
            "LAN",
            "LAU",
        ]
    )
    engine.dispose()


def test_m24a_auditoria_final_tem_exatamente_uma_head(tmp_path, monkeypatch):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    cfg = _alembic_config(f"sqlite:///{tmp_path}/heads.db")
    # A migração M24C (4c9e2f7a6b31) é a head atual — o
    # valor esperado aqui é atualizado a cada nova migration; o que a
    # asserção realmente prova é continuar existindo EXATAMENTE uma head
    # (sem ponto de ramificação acidental).
    assert ScriptDirectory.from_config(cfg).get_heads() == ["4c9e2f7a6b31"]


def test_downgrade_m24c_falha_fechado_com_perfil_profissional(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/m24c-downgrade-guard.db"
    cfg = _alembic_config(url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, email, nome, password_hash, ativo,
                    created_at, updated_at
                ) VALUES (
                    :id, 'perfil-m24c@teste.local',
                    'TESTE APAGAR Perfil Migração',
                    'hash-sintetico-nao-utilizavel', true,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": "24c10000-0000-4000-8000-000000000001"},
        )
        connection.execute(
            text(
                """
                INSERT INTO physician_profiles (
                    id, user_id, professional_name, crm_number, crm_state,
                    active, verification_status, created_at, updated_at
                ) VALUES (
                    :id, :user_id, 'TESTE APAGAR Perfil',
                    '810001', 'AC', false, 'pending',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": "24c10000-0000-4000-8000-000000000002",
                "user_id": "24c10000-0000-4000-8000-000000000001",
            },
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="Downgrade M24C recusado"):
        command.downgrade(cfg, "8d4b1a2c9f70")

    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "4c9e2f7a6b31"
        assert connection.execute(
            text("SELECT count(*) FROM physician_profiles")
        ).scalar_one() == 1
    engine.dispose()


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
