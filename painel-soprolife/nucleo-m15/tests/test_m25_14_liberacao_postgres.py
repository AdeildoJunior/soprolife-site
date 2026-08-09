"""M25.14 — a liberação do laudo precisa funcionar em PostgreSQL DE VERDADE.

Por que este arquivo existe separado da suíte: o resto dos testes roda em
SQLite, que **ignora** o limite de VARCHAR. O defeito da M25.13 era exatamente
uma coluna estreita demais (`VARCHAR(20)` para um valor de 22 caracteres), então
o SQLite dava verde enquanto a produção devolvia 500. Prova de largura de coluna
só vale em PostgreSQL.

O banco é criado do zero e migrado com Alembic (`upgrade head`), de modo que o
que está sob teste é a migration real, não `create_all` a partir do modelo.

Como rodar:

    podman run -d --rm --name m25-14-pg -e POSTGRES_PASSWORD=... \
        -e POSTGRES_USER=m25test -e POSTGRES_DB=m25test \
        -p 127.0.0.1:55432:5432 docker.io/library/postgres:16-alpine
    M25_14_TEST_DATABASE_URL=postgresql+psycopg://m25test:...@127.0.0.1:55432/m25test \
        .venv/bin/pytest tests/test_m25_14_liberacao_postgres.py

Sem a variável, os testes são pulados com aviso — nunca passam por omissão.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    Person,
    PhysicianProfile,
    ReportDocument,
    ReportDocumentVersion,
    ReportSignature,
    SpirometryExam,
    User,
    SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL,
    STATUS_LAUDO_ATRIBUIDO,
    STATUS_LAUDO_LIBERADO,
)

RAIZ = Path(__file__).resolve().parents[1]
URL_ENV = "M25_14_TEST_DATABASE_URL"

pytestmark = pytest.mark.skipif(
    not os.environ.get(URL_ENV),
    reason=(
        f"{URL_ENV} não definida — a prova de largura de coluna exige "
        "PostgreSQL real; SQLite ignora limite de VARCHAR."
    ),
)


def _com_database(url: str, nome: str) -> str:
    """Mesma instância, outro database.

    `str(URL)` mascara a senha como `***`; para uma URL utilizável é preciso
    `render_as_string(hide_password=False)`.
    """
    return sa.engine.make_url(url).set(database=nome).render_as_string(
        hide_password=False
    )


@pytest.fixture(scope="module")
def pg_url():
    base = os.environ[URL_ENV]
    nome = f"m25_14_{uuid.uuid4().hex[:12]}"
    admin = sa.create_engine(
        _com_database(base, "postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{nome}"'))
    destino = _com_database(base, nome)

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "migrations"))
    cfg.set_main_option("sqlalchemy.url", destino)
    command.upgrade(cfg, "head")

    yield destino

    with admin.connect() as conn:
        conn.execute(
            sa.text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ),
            {"n": nome},
        )
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{nome}"'))
    admin.dispose()


@pytest.fixture()
def sessao(pg_url):
    engine = sa.create_engine(pg_url)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.rollback()
    s.close()
    engine.dispose()


def _cenario(sessao) -> dict:
    """Cenário mínimo e fictício para exercitar a liberação."""
    sufixo = uuid.uuid4().hex[:6]
    # CRM fictício único por cenário: há índice único (crm_state, crm_number)
    # entre perfis ativos.
    crm = f"{uuid.uuid4().int % 100_000_000:08d}"
    usuario = User(
        email=f"medica.{sufixo}@teste.local",
        nome="Médica de Teste M25.14",
        password_hash="x" * 60,
    )
    # A verificação do perfil não pode ser feita pelo próprio médico
    # (`ck_physician_profiles_verification_not_self`).
    verificador = User(
        email=f"admin.{sufixo}@teste.local",
        nome="Admin de Teste M25.14",
        password_hash="x" * 60,
    )
    sessao.add(verificador)
    pessoa = Person(
        public_code=f"PES-T{sufixo}",
        nome_completo="Paciente Fictício M25.14",
        nome_normalizado="paciente ficticio m25 14",
        status="ativo",
    )
    sessao.add_all([usuario, pessoa])
    sessao.flush()

    agora = datetime.now(timezone.utc)
    perfil = PhysicianProfile(
        user_id=usuario.id,
        professional_name="Médica de Teste M25.14",
        crm_number=crm,
        crm_state="RJ",
        active=True,
        # A constraint de evidência exige o conjunto completo quando o
        # perfil está verificado — nada aqui é dado real.
        verification_status="verified",
        verified_at=agora,
        verified_by_user_id=verificador.id,
        verification_reference="TESTE-M25.14",
    )
    exame = SpirometryExam(
        public_code=f"ESP-T{sufixo}",
        person_id=pessoa.id,
        data_exame=date(2026, 8, 9),
        data_exame_dia_assumido=False,
        status="Realizado",
    )
    sessao.add_all([perfil, exame])
    sessao.flush()

    documento = ReportDocument(
        public_code=f"LAU-T{sufixo}",
        spirometry_exam_id=exame.id,
        status=STATUS_LAUDO_ATRIBUIDO,
        created_by_user_id=usuario.id,
    )
    sessao.add(documento)
    sessao.flush()

    # `ReportSignature` aponta para a VERSÃO do documento, não para o
    # documento — por isso o cenário precisa de uma versão persistida.
    versao = ReportDocumentVersion(
        report_document_id=documento.id,
        kind="original",
        version_number=1,
        storage_path=f"laudos/teste-m25-14/{sufixo}.pdf",
        sha256="0" * 64,
        size_bytes=1024,
        page_count=1,
        mime_type="application/pdf",
        created_by_user_id=usuario.id,
    )
    sessao.add(versao)
    sessao.commit()
    return {
        "documento": documento,
        "versao": versao,
        "usuario": usuario,
        "perfil": perfil,
        "exame": exame,
    }


def _liberar(documento, usuario, perfil, agora=None) -> None:
    """Aplica no documento o mesmo estado terminal que a API grava."""
    agora = agora or datetime.now(timezone.utc)
    documento.status = STATUS_LAUDO_LIBERADO
    documento.signature_status = SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL
    documento.clinical_started_at = agora
    documento.ready_for_signature_at = agora
    documento.released_at = agora
    documento.released_by_user_id = usuario.id
    documento.released_physician_profile_id = perfil.id
    documento.validation_code = uuid.uuid4().hex[:12].upper()


# --------------------------------------------------------------------------
# 1. A largura da coluna comporta o valor exigido
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tabela,coluna",
    [("report_documents", "signature_status"), ("report_signatures", "status")],
)
def test_coluna_comporta_liberada_institucional(sessao, tabela, coluna):
    largura = sessao.execute(
        sa.text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": tabela, "c": coluna},
    ).scalar_one()
    assert largura is not None, f"{tabela}.{coluna} deveria ser VARCHAR"
    assert largura >= len(SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL), (
        f"{tabela}.{coluna} é VARCHAR({largura}), estreito demais para "
        f"{SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL!r} "
        f"({len(SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL)} caracteres)"
    )


# --------------------------------------------------------------------------
# 2. A transição para "liberado" funciona de ponta a ponta
# --------------------------------------------------------------------------

def test_transicao_para_liberado_persiste(sessao):
    ctx = _cenario(sessao)
    documento = ctx["documento"]
    _liberar(documento, ctx["usuario"], ctx["perfil"])
    sessao.add(
        ReportSignature(
            report_document_version_id=ctx["versao"].id,
            status=SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL,
            requested_by_user_id=ctx["usuario"].id,
        )
    )
    sessao.commit()

    lido = sessao.execute(
        sa.text(
            "SELECT status, signature_status, released_at, validation_code "
            "FROM report_documents WHERE id = :i"
        ),
        {"i": documento.id},
    ).one()
    assert lido.status == STATUS_LAUDO_LIBERADO
    assert lido.signature_status == SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL
    assert lido.released_at is not None
    assert lido.validation_code

    status_assinatura = sessao.execute(
        sa.text(
            "SELECT status FROM report_signatures "
            "WHERE report_document_version_id = :i"
        ),
        {"i": ctx["versao"].id},
    ).scalar_one()
    assert status_assinatura == SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL


# --------------------------------------------------------------------------
# 3. A regra clínica continua valendo (constraint não foi enfraquecida)
# --------------------------------------------------------------------------

def test_check_constraint_recusa_liberado_incoerente(sessao):
    ctx = _cenario(sessao)
    documento = ctx["documento"]
    _liberar(documento, ctx["usuario"], ctx["perfil"])
    # Estado incoerente de propósito: liberado sem evidência de liberação.
    documento.released_at = None

    with pytest.raises(sa.exc.IntegrityError):
        sessao.commit()
    sessao.rollback()


def test_check_constraint_recusa_signature_status_desconhecido(sessao):
    ctx = _cenario(sessao)
    documento = ctx["documento"]
    _liberar(documento, ctx["usuario"], ctx["perfil"])
    # Agora cabe na coluna — mas continua não sendo um valor do domínio.
    documento.signature_status = "valor_invalido_que_cabe_em_40"

    with pytest.raises(sa.exc.IntegrityError):
        sessao.commit()
    sessao.rollback()


# --------------------------------------------------------------------------
# 4. Rollback não deixa estado parcial
# --------------------------------------------------------------------------

def test_rollback_nao_deixa_estado_parcial(sessao):
    ctx = _cenario(sessao)
    documento = ctx["documento"]
    _liberar(documento, ctx["usuario"], ctx["perfil"])
    sessao.add(
        ReportSignature(
            report_document_version_id=ctx["versao"].id,
            status=SIGNATURE_STATUS_LIBERADA_INSTITUCIONAL,
            requested_by_user_id=ctx["usuario"].id,
        )
    )
    sessao.flush()          # já escreveu na transação…
    sessao.rollback()       # …e desiste

    estado = sessao.execute(
        sa.text(
            "SELECT status, signature_status, released_at FROM report_documents "
            "WHERE id = :i"
        ),
        {"i": documento.id},
    ).one()
    assert estado.status == STATUS_LAUDO_ATRIBUIDO
    assert estado.signature_status is None
    assert estado.released_at is None

    assinaturas = sessao.execute(
        sa.text(
            "SELECT count(*) FROM report_signatures "
            "WHERE report_document_version_id = :i"
        ),
        {"i": ctx["versao"].id},
    ).scalar_one()
    assert assinaturas == 0


# --------------------------------------------------------------------------
# 5. O modelo e o banco não podem divergir de novo
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tabela,coluna",
    [("report_documents", "signature_status"), ("report_signatures", "status")],
)
def test_banco_reflete_a_largura_declarada_no_modelo(sessao, tabela, coluna):
    declarada = Base.metadata.tables[tabela].columns[coluna].type.length
    no_banco = sessao.execute(
        sa.text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": tabela, "c": coluna},
    ).scalar_one()
    assert no_banco == declarada, (
        f"{tabela}.{coluna}: modelo declara {declarada}, banco migrado tem "
        f"{no_banco} — migration e modelo divergiram"
    )
