"""Migrações Alembic: sobem do zero e batem com o metadata dos modelos."""

from datetime import datetime, timezone
from decimal import Decimal
import pathlib
import uuid

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
    # Ancorado em `PREFIXES` e não numa cópia congelada: assim a asserção
    # prova o que interessa — TODO prefixo emitido pela aplicação tem
    # sequência preseedada — e uma entidade nova que esqueça a migration
    # falha aqui, em vez de só falhar na primeira alocação em produção.
    from app.ids import PREFIXES

    assert prefixes == sorted(PREFIXES.values())
    engine.dispose()


def test_m24a_auditoria_final_tem_exatamente_uma_head(tmp_path, monkeypatch):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    cfg = _alembic_config(f"sqlite:///{tmp_path}/heads.db")
    # A migração M26.4 (c3a9e15f7d84, tabelas do portal de resultados do
    # paciente) é a head atual — o valor esperado aqui é atualizado a cada
    # nova migration; o que a asserção realmente prova é continuar existindo
    # EXATAMENTE uma head (sem ponto de ramificação acidental).
    assert ScriptDirectory.from_config(cfg).get_heads() == ["c3a9e15f7d84"]


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


def test_m24c_preserva_report_templates_populada_ao_migrar(tmp_path, monkeypatch):
    """F1: report_templates com linha legada não pode falhar em SQLite.

    batch_alter_table recria a tabela em SQLite copiando as linhas com um
    INSERT...SELECT que não referencia colunas adicionadas depois no mesmo
    bloco. A remoção do server_default de status/clinically_approved precisa
    estar num segundo bloco, executado depois que as colunas já existem com
    default, para que a linha legada sobreviva à cópia.
    """
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/m24c-templates-populada.db"
    cfg = _alembic_config(url)
    command.upgrade(cfg, "8d4b1a2c9f70")
    engine = create_engine(url)
    legacy_id = "24c20000-0000-4000-8000-000000000001"
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO report_templates (
                    id, codigo, titulo, texto_tooltip, texto_completo,
                    versao, ativo, criado_por, created_at, updated_at
                ) VALUES (
                    :id, 'LEGADO', 'Template Legado Teste', NULL,
                    'texto legado sintetico', 1, true, NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {"id": legacy_id},
        )
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(url)
    with engine.connect() as connection:
        legacy_row = connection.execute(
            text(
                "SELECT status, clinically_approved FROM report_templates"
                " WHERE id = :id"
            ),
            {"id": legacy_id},
        ).one()
        assert legacy_row.status == "draft"
        assert bool(legacy_row.clinically_approved) is False

        provisional_codes = sorted(
            row[0]
            for row in connection.execute(
                text(
                    "SELECT codigo FROM report_templates WHERE id <> :id"
                ),
                {"id": legacy_id},
            )
        )
    engine.dispose()

    assert provisional_codes == [
        "INESPECIFICO_QUALIDADE_PROVISORIO",
        "MISTO_PROVISORIO",
        "NORMAL_PROVISORIO",
        "OBSTRUTIVO_BD_PROVISORIO",
        "OBSTRUTIVO_PROVISORIO",
        "SUGESTIVO_RESTRITIVO_PROVISORIO",
    ]


def _popular_financeiro_pre_m23_1(engine, *, com_conflitos: bool):
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    # `people` e `spirometry_exams` são populados por SQL explícito, e não
    # pelo ORM: este cenário roda contra uma revisão ANTIGA do schema, e o
    # ORM já conhece colunas criadas por migrations posteriores (M25.2
    # acrescentou people.sexo, spirometry_exams.hora_exame e
    # spirometry_exams.indicacao_clinica). Fixar as colunas aqui mantém o
    # teste preso à revisão que ele realmente quer exercitar.
    pessoa_id = str(uuid.uuid4())
    exam_id = str(uuid.uuid4())
    agora = datetime.now(timezone.utc)
    session.execute(
        text(
            "INSERT INTO people (id, public_code, nome_completo, "
            "nome_normalizado, status, nao_contatar, created_at, updated_at) "
            "VALUES (:id, :code, :nome, :norm, 'ativo', 0, :now, :now)"
        ),
        {
            "id": pessoa_id,
            "code": "PES-910001",
            "nome": "Pessoa Sintetica Migracao",
            "norm": "pessoa sintetica migracao",
            "now": agora,
        },
    )
    session.execute(
        text(
            "INSERT INTO spirometry_exams (id, public_code, person_id, "
            "status, data_exame_dia_assumido, created_at, updated_at) "
            "VALUES (:id, :code, :person_id, 'Aguardando', 0, :now, :now)"
        ),
        {
            "id": exam_id,
            "code": "ESP-910001",
            "person_id": pessoa_id,
            "now": agora,
        },
    )
    session.flush()
    consultation = Consultation(public_code="CON-910001", person_id=pessoa_id)
    session.add(consultation)
    session.flush()

    rows = [
        FinancialEntry(
            public_code="LAN-910001",
            tipo="receita",
            categoria=" Espirometria ",
            valor=Decimal("100.00"),
            status="Pendente",
            spirometry_exam_id=exam_id,
        ),
        FinancialEntry(
            public_code="LAN-910010",
            tipo="receita",
            categoria="Outro",
            valor=Decimal("5.00"),
            status="Pendente",
            spirometry_exam_id=exam_id,
        ),
        FinancialEntry(
            public_code="LAN-910011",
            tipo="despesa",
            categoria="Espirometria",
            valor=Decimal("3.00"),
            status="Pendente",
            spirometry_exam_id=exam_id,
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
                    spirometry_exam_id=exam_id,
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


def _semear_fechamento(connection, *, sequencia, unidade="unit-m26", valor=None):
    settlement_id = str(uuid.uuid4())
    agora = datetime.now(timezone.utc).isoformat()
    connection.execute(
        text(
            "INSERT INTO partner_settlements "
            "(id, partner_id, partner_unit_id, competencia, status, sequencia,"
            " valor_total, created_at, updated_at) VALUES "
            "(:id, 'partner-m26', :unidade, '2026-08-01', 'incluido', :seq,"
            " :valor, :agora, :agora)"
        ),
        {
            "id": settlement_id, "unidade": unidade, "seq": sequencia,
            "valor": valor, "agora": agora,
        },
    )
    return settlement_id


def test_m26_backfill_da_sequencia_e_chave_por_competencia(tmp_path, monkeypatch):
    """Fechamento já gravado vira sequência 1; um segundo passa a caber."""

    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/m26-sequencia.db"
    cfg = _alembic_config(url)
    command.upgrade(cfg, "c4a97b1e6d20")
    engine = create_engine(url)
    agora = datetime.now(timezone.utc).isoformat()
    antigo = str(uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO partner_settlements "
                "(id, partner_id, partner_unit_id, competencia, status,"
                " valor_total, created_at, updated_at) VALUES "
                "(:id, 'partner-m26', 'unit-m26', '2026-08-01', 'a_receber',"
                " 328.50, :agora, :agora)"
            ),
            {"id": antigo, "agora": agora},
        )

    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT sequencia, valor_total FROM partner_settlements "
                 "WHERE id = :id"),
            {"id": antigo},
        ).one() == (1, 328.50)

    # O complementar da mesma competência agora cabe...
    with engine.begin() as connection:
        _semear_fechamento(connection, sequencia=2)
    # ...e repetir a MESMA sequência continua proibido.
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _semear_fechamento(connection, sequencia=2)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _semear_fechamento(connection, sequencia=0)
    engine.dispose()


def test_m26_downgrade_recusa_apagar_fechamento_complementar(tmp_path, monkeypatch):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/m26-downgrade.db"
    cfg = _alembic_config(url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        _semear_fechamento(connection, sequencia=1)
        _semear_fechamento(connection, sequencia=2)

    with pytest.raises(RuntimeError, match="mais de um fechamento"):
        command.downgrade(cfg, "c4a97b1e6d20")

    # Sem complementar, o caminho de volta continua aberto.
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM partner_settlements WHERE sequencia = 2")
        )
    command.downgrade(cfg, "c4a97b1e6d20")
    assert "sequencia" not in {
        col["name"] for col in inspect(engine).get_columns("partner_settlements")
    }
    engine.dispose()


def test_m26_3_recebimento_por_exame_sobe_e_desce(tmp_path, monkeypatch):
    """A migração da regra Pastore é aditiva e reversível.

    Aditiva: nenhuma parceria já gravada muda de comportamento — todas descem
    da migração com `modelo_recebimento = 'indefinido'`, que é exatamente o
    "não há regra" que valia antes. Reversível: o downgrade devolve a tabela
    ao estado anterior sem tocar em linha nenhuma.
    """
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path}/m26-3.db"
    cfg = _alembic_config(url)

    command.upgrade(cfg, "a3f6b0d94c17")
    engine = create_engine(url)
    antes = {c["name"] for c in inspect(engine).get_columns("partnerships")}
    assert "modelo_recebimento" not in antes

    # Uma parceria gravada ANTES da migração, como a PAR-000001 de produção.
    with engine.begin() as conexao:
        conexao.execute(text(
            "INSERT INTO partnerships (id, public_code, partner_id, status,"
            " modelo_repasse, responsavel_followup, data_inicio_dia_assumido,"
            " created_at, updated_at)"
            " VALUES ('p-m263', 'PAR-M263Z', 'parceiro-x', 'em_negociacao',"
            " 'indefinido', 'soprolife', 0, :agora, :agora)"
        ), {"agora": datetime.now(timezone.utc)})
    engine.dispose()

    command.upgrade(cfg, "b1f4c72d9e08")
    engine = create_engine(url)
    depois = {c["name"] for c in inspect(engine).get_columns("partnerships")}
    assert {"modelo_recebimento", "valor_recebido_por_exame", "vigencia_inicio"} <= depois
    with engine.connect() as conexao:
        linha = conexao.execute(text(
            "SELECT modelo_recebimento, valor_recebido_por_exame, vigencia_inicio"
            " FROM partnerships WHERE id = 'p-m263'"
        )).one()
    assert linha == ("indefinido", None, None)
    engine.dispose()

    command.downgrade(cfg, "a3f6b0d94c17")
    engine = create_engine(url)
    revertido = {c["name"] for c in inspect(engine).get_columns("partnerships")}
    assert "modelo_recebimento" not in revertido
    assert "valor_recebido_por_exame" not in revertido
    assert "vigencia_inicio" not in revertido
    with engine.connect() as conexao:
        assert conexao.execute(text(
            "SELECT COUNT(*) FROM partnerships WHERE id = 'p-m263'"
        )).scalar() == 1
    engine.dispose()
