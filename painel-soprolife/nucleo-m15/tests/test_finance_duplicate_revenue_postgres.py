"""M23.1 — migração e corrida de receita duplicada em PostgreSQL 16 real."""

from decimal import Decimal
import os
import pathlib
import threading

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db import get_db
from app.main import create_app
from app.models import Consultation, FinancialEntry, Person, SpirometryExam
from app.routers import finance as finance_router
from app.security import issue_token
from tests.conftest import _make_user


PG_URL = os.environ.get("M15_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="PostgreSQL de teste indisponível — defina M15_TEST_POSTGRES_URL",
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
OLD_HEAD = "b8c4e6d21a90"


def _alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", PG_URL)
    return cfg


def _reset_schema(engine):
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="module")
def pg_engine():
    os.environ.pop("M15_DATABASE_URL", None)
    engine = create_engine(PG_URL)
    _reset_schema(engine)
    command.upgrade(_alembic_config(), "head")
    yield engine
    _reset_schema(engine)
    engine.dispose()


def _entry(code, category, *, exam_id=None, consultation_id=None):
    return FinancialEntry(
        public_code=code,
        tipo="receita",
        categoria=category,
        valor=Decimal("10.00"),
        moeda="BRL",
        status="Pendente",
        data_competencia_dia_assumido=False,
        spirometry_exam_id=exam_id,
        consultation_id=consultation_id,
    )


def test_postgres_migracao_aborta_conflito_e_depois_preserva_linha_valida(
    pg_engine,
):
    """Rehearsal real: conflito aborta sem DDL/UPDATE; removida apenas a
    fixture sintética conflitante pelo próprio teste, o mesmo banco sobe e a
    linha válida permanece byte a byte igual."""
    cfg = _alembic_config()
    expected_head = ScriptDirectory.from_config(cfg).get_current_head()
    command.downgrade(cfg, OLD_HEAD)
    SessionLocal = sessionmaker(bind=pg_engine, expire_on_commit=False)
    session = SessionLocal()
    person = Person(
        public_code="PES-920001",
        nome_completo="Pessoa Sintetica PG",
        nome_normalizado="pessoa sintetica pg",
    )
    session.add(person)
    session.flush()
    exam = SpirometryExam(public_code="ESP-920001", person_id=person.id)
    session.add(exam)
    session.flush()
    first = _entry("LAN-920001", "Espirometria", exam_id=exam.id)
    duplicate = _entry("LAN-920002", " espirometria ", exam_id=exam.id)
    session.add_all([first, duplicate])
    session.commit()
    before = session.execute(
        select(
            FinancialEntry.id,
            FinancialEntry.public_code,
            FinancialEntry.categoria,
            FinancialEntry.valor,
            FinancialEntry.spirometry_exam_id,
        ).where(FinancialEntry.public_code.in_(["LAN-920001", "LAN-920002"]))
        .order_by(FinancialEntry.public_code)
    ).all()
    session.close()

    with pytest.raises(RuntimeError, match="receitas próprias históricas") as caught:
        command.upgrade(cfg, "head")

    verify = SessionLocal()
    after = verify.execute(
        select(
            FinancialEntry.id,
            FinancialEntry.public_code,
            FinancialEntry.categoria,
            FinancialEntry.valor,
            FinancialEntry.spirometry_exam_id,
        ).where(FinancialEntry.public_code.in_(["LAN-920001", "LAN-920002"]))
        .order_by(FinancialEntry.public_code)
    ).all()
    revision = verify.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    indexes = {ix["name"] for ix in inspect(pg_engine).get_indexes("financial_entries")}
    assert after == before
    assert revision == OLD_HEAD
    assert "uq_financial_entries_receita_espirometria" not in indexes
    assert "uq_financial_entries_receita_consulta" not in indexes
    assert "Pessoa Sintetica PG" not in str(caught.value)
    assert "10.00" not in str(caught.value)

    # Limpa somente a segunda fixture conflitante; isso não faz parte da
    # migração e permite ensaiar o caso production-shaped sem duplicatas.
    verify.delete(verify.get(FinancialEntry, duplicate.id))
    verify.commit()
    valid_before = verify.execute(
        select(
            FinancialEntry.id,
            FinancialEntry.public_code,
            FinancialEntry.categoria,
            FinancialEntry.valor,
            FinancialEntry.spirometry_exam_id,
        ).where(FinancialEntry.id == first.id)
    ).one()
    verify.close()

    command.upgrade(cfg, "head")
    final = SessionLocal()
    valid_after = final.execute(
        select(
            FinancialEntry.id,
            FinancialEntry.public_code,
            FinancialEntry.categoria,
            FinancialEntry.valor,
            FinancialEntry.spirometry_exam_id,
        ).where(FinancialEntry.id == first.id)
    ).one()
    final_revision = final.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    final.close()
    indexes = {ix["name"] for ix in inspect(pg_engine).get_indexes("financial_entries")}
    assert valid_after == valid_before
    # `upgrade head` precisa alcançar a head única ATUAL. M24A adicionou uma
    # revisão depois da migração M23.1 exercitada aqui; fixar a antiga revisão
    # M23.1 como "head" produziria falso negativo mesmo com o banco correto.
    assert final_revision == expected_head
    assert "uq_financial_entries_receita_espirometria" in indexes
    assert "uq_financial_entries_receita_consulta" in indexes


@pytest.mark.parametrize(
    ("componente", "person_code", "link_code", "categoria_a", "categoria_b"),
    [
        (
            "exame",
            "PES-920010",
            "ESP-920010",
            "espirometria",
            " \tESPIROMETRIA\n",
        ),
        (
            "consulta",
            "PES-920020",
            "CON-920020",
            "consulta",
            " Ｃｏｎｓｕｌｔａ ",
        ),
    ],
    ids=["exame", "consulta"],
)
def test_duas_requisicoes_concorrentes_so_criam_uma_receita(
    pg_engine,
    monkeypatch,
    componente,
    person_code,
    link_code,
    categoria_a,
    categoria_b,
):
    SessionLocal = sessionmaker(bind=pg_engine, expire_on_commit=False)
    setup = SessionLocal()
    person = Person(
        public_code=person_code,
        nome_completo=f"Pessoa Corrida {componente}",
        nome_normalizado=f"pessoa corrida {componente}",
    )
    setup.add(person)
    setup.flush()
    if componente == "exame":
        link = SpirometryExam(public_code=link_code, person_id=person.id)
        link_field = "spirometry_exam_id"
        canonical = "Espirometria"
    else:
        link = Consultation(public_code=link_code, person_id=person.id)
        link_field = "consultation_id"
        canonical = "Consulta"
    setup.add(link)
    user = _make_user(
        setup, f"gestor-corrida-{componente}@teste.local", "gestor"
    )
    token = issue_token(user.id, user.password_hash)
    setup.commit()
    link_id = link.id
    setup.close()

    app = create_app()

    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    barrier = threading.Barrier(2)
    local = threading.local()
    original_guard = finance_router._bloquear_receita_duplicada

    def synchronized_guard(*args, **kwargs):
        result = original_guard(*args, **kwargs)
        target = kwargs.get(link_field) == link_id and kwargs.get("tipo") == "receita"
        # Só sincroniza a verificação inicial. O perdedor chama o guarda outra
        # vez depois do rollback para traduzir a IntegrityError em 409.
        if target and not getattr(local, "initial_check_done", False):
            local.initial_check_done = True
            barrier.wait(timeout=15)
        return result

    monkeypatch.setattr(
        finance_router, "_bloquear_receita_duplicada", synchronized_guard
    )
    responses = []
    errors = []

    with TestClient(app) as client:
        def worker(category):
            try:
                responses.append(
                    client.post(
                        "/api/v1/lancamentos",
                        json={
                            "tipo": "receita",
                            "categoria": category,
                            "valor": "100.00",
                            link_field: link_id,
                        },
                        headers={"Authorization": f"Bearer {token}"},
                    )
                )
            except Exception as exc:  # pragma: no cover - diagnóstico útil
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(categoria_a,)),
            threading.Thread(target=worker, args=(categoria_b,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert all(not thread.is_alive() for thread in threads)

    assert not errors, errors
    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    detail = conflict.json()["erro"]["mensagem"]
    assert detail["codigo"] == "receita_ja_existe"
    assert set(detail) == {
        "codigo",
        "mensagem",
        "lancamento_existente",
        "lancamento_existente_id",
    }
    assert f"Pessoa Corrida {componente}" not in conflict.text

    verify = SessionLocal()
    query = select(FinancialEntry).where(
        getattr(FinancialEntry, link_field) == link_id,
        FinancialEntry.tipo == "receita",
        FinancialEntry.categoria == canonical,
    )
    rows = verify.execute(query).scalars().all()
    verify.close()
    assert len(rows) == 1
