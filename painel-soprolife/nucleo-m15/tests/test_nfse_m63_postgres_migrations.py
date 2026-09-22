"""M63 — the fiscal migrations, on PostgreSQL, along the path production took.

WHY THIS FILE EXISTS

M62 found that M57 and M61 failed on PostgreSQL: their PostgreSQL branch
handed ``op.drop_constraint`` the already-expanded constraint name, the naming
convention was applied a second time, and the upgrade died with
``UndefinedObject``. The whole suite had passed, because it runs on SQLite,
where those migrations take the batch branch instead.

The repository already had a PostgreSQL harness (``M15_TEST_POSTGRES_URL``,
see ``test_nfse_migrations.py``). M63 ran its existing test against the
pre-fix commit and it fails with exactly the M62 error. The bug was never
invisible to the tests — the tests were simply never pointed at PostgreSQL.

So this file does two things. It pins the specific regression, and it
replays the exact route the operational database took on 2026-09-22:

    d6a9f20c3e41 (with real-shaped data already in it)
      -> seven fiscal migrations
      -> c4e8b1f37a92
      -> upgrade head again: a no-op

It skips without ``M15_TEST_POSTGRES_URL``, like the rest of the harness. The
M63 report and the deploy checklist make running it a precondition of any
deploy, which is the actual fix for "the tests never ran on PostgreSQL".
"""
from datetime import date

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.test_nfse_migrations import config, postgres_url  # noqa: F401 — fixture reuse

# The revision the operational database carried before M62, and the head it
# carries now. Both are facts about production, not choices.
PRE_M62_OPERATIONAL = 'd6a9f20c3e41'
OPERATIONAL_HEAD = 'c4e8b1f37a92'

FISCAL_MIGRATIONS = ('f6a1d9e28b40', '9c310c422ce2', 'ba3afa480112', 'a58f6c31d9e7',
                     '3a97d7535a49', 'a7d2c95e4f13', 'c4e8b1f37a92')


def _current(url):
    engine = create_engine(url)
    with engine.connect() as conn:
        revision = conn.scalar(text('select version_num from alembic_version'))
    engine.dispose()
    return revision


def _seed_pre_m62(url):
    """Rows shaped like the operational data that existed before the fiscal
    tables did: a user, a person, a performed exam and its revenue. Invented
    values only."""
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(
            "insert into users (id, nome, email, password_hash, ativo, created_at, updated_at) "
            "values ('u-m63', 'Operador Sintético', 'm63@teste.invalid', 'x', true, now(), now())"))
        conn.execute(text(
            "insert into people (id, public_code, nome_completo, nome_normalizado, status, "
            "nao_contatar, arquivado, created_at, updated_at) values ('p-m63', 'PES-M63', "
            "'Pessoa Sintética', 'pessoa sintetica', 'ativo', false, false, now(), now())"))
        conn.execute(text(
            "insert into spirometry_exams (id, public_code, person_id, status, data_exame, "
            "data_exame_dia_assumido, modalidade, created_at, updated_at) values ('e-m63', "
            "'ESP-M63', 'p-m63', 'Realizado', :d, false, 'residencial', now(), now())"),
            {"d": date(2026, 9, 15)})
    engine.dispose()


# ----------------------------------------------------- the chain itself


def test_the_head_is_the_operational_head_and_is_unique():
    """The integrated code must know EXACTLY the revision production is at,
    and nothing beyond it — otherwise the first deploy would try to migrate a
    database that is already where it should be."""
    script = ScriptDirectory.from_config(config('sqlite://'))
    assert script.get_heads() == [OPERATIONAL_HEAD]
    chain, revision = [], script.get_revision(OPERATIONAL_HEAD)
    while revision is not None and revision.revision != PRE_M62_OPERATIONAL:
        chain.append(revision.revision)
        revision = script.get_revision(revision.down_revision)
    assert revision is not None, 'pre-M62 operational revision is not an ancestor of head'
    assert tuple(reversed(chain)) == FISCAL_MIGRATIONS


# ----------------------------------------------------- the M62 regression


def test_m57_and_m61_upgrade_and_downgrade_on_postgresql(postgres_url):  # noqa: F811
    """The exact bug M62 found, pinned where it lives. Both migrations cross
    their PostgreSQL branch in both directions."""
    cfg = config(postgres_url)
    command.upgrade(cfg, '3a97d7535a49')
    command.upgrade(cfg, OPERATIONAL_HEAD)          # M57 + M61 up
    assert _current(postgres_url) == OPERATIONAL_HEAD
    command.downgrade(cfg, '3a97d7535a49')          # M61 + M57 down
    assert _current(postgres_url) == '3a97d7535a49'
    command.upgrade(cfg, OPERATIONAL_HEAD)          # and up again
    command.check(cfg)


def test_the_constraints_carry_single_not_doubled_names(postgres_url):  # noqa: F811
    command.upgrade(config(postgres_url), 'head')
    engine = create_engine(postgres_url)
    with engine.connect() as conn:
        names = set(conn.scalars(text(
            "select conname from pg_constraint where contype = 'c' and conrelid in "
            "('fiscal_documents'::regclass, 'fiscal_attempts'::regclass)")))
        state_def = conn.scalar(text(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conname = 'ck_fiscal_documents_fiscal_document_state'"))
        operation_def = conn.scalar(text(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conname = 'ck_fiscal_attempts_fiscal_attempt_operation'"))
    engine.dispose()
    assert 'ck_fiscal_documents_fiscal_document_state' in names
    assert 'ck_fiscal_attempts_fiscal_attempt_operation' in names
    assert not [n for n in names if n.count('ck_fiscal_') > 1]
    assert "'issued'" in state_def and "'simulated'" in state_def
    assert "'import'" in operation_def


# ----------------------------------------------------- production's route


def test_the_route_production_took_from_pre_m62_with_data(postgres_url):  # noqa: F811
    """d6a9f20c3e41 with rows already present -> head. The existing rows
    survive, the new column arrives empty, and nothing is inferred."""
    cfg = config(postgres_url)
    command.upgrade(cfg, PRE_M62_OPERATIONAL)
    _seed_pre_m62(postgres_url)
    command.upgrade(cfg, 'head')
    command.check(cfg)
    assert _current(postgres_url) == OPERATIONAL_HEAD
    engine = create_engine(postgres_url)
    with engine.connect() as conn:
        assert conn.scalar(text("select count(*) from spirometry_exams")) == 1
        # M31: additive and nullable. Nothing fills it in — the service
        # municipality is a human decision, never an inference.
        assert conn.scalar(text(
            "select municipio_atendimento_ibge from spirometry_exams where id = 'e-m63'")) is None
        for table in ('fiscal_documents', 'fiscal_attempts', 'fiscal_policies',
                      'national_dps_configurations', 'dps_number_allocations'):
            assert conn.scalar(text(f"select count(*) from {table}")) == 0
    engine.dispose()


def test_a_database_already_at_head_is_a_no_op(postgres_url):  # noqa: F811
    """What the first deploy will do to the operational database: nothing."""
    cfg = config(postgres_url)
    command.upgrade(cfg, 'head')
    engine = create_engine(postgres_url)
    with engine.connect() as conn:
        before = conn.scalar(text(
            "select md5(string_agg(table_name || ':' || column_name || ':' || data_type, ',' "
            "order by table_name, column_name)) from information_schema.columns "
            "where table_schema = 'public'"))
    command.upgrade(cfg, 'head')
    command.check(cfg)
    with engine.connect() as conn:
        after = conn.scalar(text(
            "select md5(string_agg(table_name || ':' || column_name || ':' || data_type, ',' "
            "order by table_name, column_name)) from information_schema.columns "
            "where table_schema = 'public'"))
    engine.dispose()
    assert before == after
    assert _current(postgres_url) == OPERATIONAL_HEAD


@pytest.mark.parametrize('table', ['fiscal_attempts', 'fiscal_artifacts',
                                   'fiscal_preparations', 'fiscal_policies'])
def test_append_only_triggers_exist_on_postgresql(postgres_url, table):  # noqa: F811
    """The triggers are the reason the fiscal history is worth trusting. This
    only asserts they EXIST on PostgreSQL; that they actually refuse an edit
    is proven on real rows in the import test below, because a trigger on an
    empty table never fires and a test that pretended otherwise would pass
    whatever the trigger did."""
    command.upgrade(config(postgres_url), 'head')
    engine = create_engine(postgres_url)
    with engine.connect() as conn:
        triggers = set(conn.scalars(text(
            "select tgname from pg_trigger where not tgisinternal "
            "and tgrelid = cast(:t as regclass)"), {"t": table}))
    engine.dispose()
    assert triggers, f'{table} has no trigger on PostgreSQL'


def test_an_imported_note_survives_and_cannot_be_rewritten(postgres_url):  # noqa: F811
    """The anti-duplication record M62 wrote in production, replayed on
    PostgreSQL: it can be read, and the append-only guard refuses to edit or
    delete it."""
    from sqlalchemy.orm import Session

    from app.models import FinancialEntry, Person, SpirometryExam, User
    from app.services.nfse_external_issuance import (ExternalIssuance,
                                                     register_external_issuance)
    from app.services.nfse_validity import fiscal_validity

    command.upgrade(config(postgres_url), 'head')
    engine = create_engine(postgres_url)
    with Session(engine, expire_on_commit=False) as db:
        user = User(nome='Operador', email='m63-import@teste.invalid', password_hash='x')
        person = Person(public_code='PES-M63I', nome_completo='Pessoa Sintética',
                        nome_normalizado='pessoa sintetica')
        db.add_all([user, person]); db.flush()
        exam = SpirometryExam(public_code='ESP-M63I', person_id=person.id, status='Realizado',
                              data_exame=date(2026, 9, 15), modalidade='residencial',
                              broncodilatador=True)
        db.add(exam); db.flush()
        db.add(FinancialEntry(public_code='LAN-M63I', tipo='receita', categoria='Espirometria',
                              valor=279, status='Recebido', spirometry_exam_id=exam.id))
        db.commit()
        document = register_external_issuance(
            db, ExternalIssuance(exam.id, '33045572263544026000110000000000000626096136136469',
                                 '2026-09-15'), user.id)
        assert document.state == 'issued'
        assert fiscal_validity(db, document) is False

    for statement in ("update fiscal_attempts set outcome = 'uncertain'",
                      "delete from fiscal_attempts",
                      "update fiscal_preparations set flow = 'HOME'",
                      "delete from fiscal_preparations",
                      "delete from fiscal_documents",
                      "update fiscal_documents set environment = 'mock'"):
        with pytest.raises(DBAPIError):
            with engine.begin() as conn:
                conn.execute(text(statement))
    with engine.connect() as conn:
        assert conn.scalar(text(
            "select count(*) from fiscal_attempts where operation = 'import'")) == 1
    engine.dispose()


# ----------------------------------------------------- the read-only scanner


def test_evaluate_without_locks_runs_inside_a_read_only_transaction(postgres_url):  # noqa: F811
    """The candidate scanner reads the OPERATIONAL database. It must take no
    row locks there and be unable to write. On PostgreSQL both are enforced by
    the database: a READ ONLY transaction refuses writes and refuses
    SELECT ... FOR UPDATE — which is why the locking read the preparation
    path needs is refused, and the scanner's unlocked read is not."""
    from sqlalchemy.orm import Session

    from app.services import nfse

    cfg = config(postgres_url)
    command.upgrade(cfg, 'head')
    _seed_pre_m62(postgres_url)
    engine = create_engine(postgres_url)

    with Session(engine) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        result = nfse.evaluate(db, 'e-m63', 'production', for_update=False)
        assert 'blocking_reasons' in result
        with pytest.raises(DBAPIError, match='read-only transaction'):
            db.execute(text("update spirometry_exams set status = status"))
        db.rollback()

    with Session(engine) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        with pytest.raises(DBAPIError, match='read-only transaction'):
            nfse.evaluate(db, 'e-m63', 'production')          # default: locks
        db.rollback()
    engine.dispose()
