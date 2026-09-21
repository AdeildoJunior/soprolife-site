"""M57 — a real NFS-e stops being recorded as a simulation.

Until M56 the fiscal queue had exactly one terminal success state,
``simulated``, minted by the mock foundation when no real provider existed. A
genuinely issued NFS-e (DPS #10, restricted, 2026-09-20) therefore landed in a
state whose name reads as "nothing happened". Migration ``a7d2c95e4f13`` adds
``issued`` and promotes only the rows the database itself proves were really
issued.

What these tests are actually for: the promotion rule is the dangerous part.
A migration that promoted every ``simulated`` row would assert, in the
accounting record, that mock runs produced real tax documents. So the rule is
conjunctive and derived from the domain, never from the state name, and each
test below removes exactly ONE criterion from an otherwise perfect row and
requires that the row stays ``simulated``.

Entirely offline: alembic against a temporary SQLite file. Nothing here
touches the restricted database, a certificate, or the network.
"""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
import pytest

ROOT = Path(__file__).resolve().parents[1]

M57 = 'a7d2c95e4f13'
BEFORE_M57 = '3a97d7535a49'

# A real government NFS-e access key shape (TSIdNFSe) — the same one the
# application's NFSE_ACCESS_KEY_PATTERN accepts.
REAL_ACCESS_KEY = 'NFS33045572263544026000110000000000000126094282476576'
assert len(REAL_ACCESS_KEY) == 53


def config(url):
    cfg = Config(str(ROOT / 'alembic.ini'))
    cfg.set_main_option('script_location', str(ROOT / 'migrations'))
    cfg.set_main_option('sqlalchemy.url', url)
    return cfg


def _now():
    return datetime.now(timezone.utc).isoformat()


def _seed_owner(conn, tag):
    """One user + one person, reused by every document in a fixture."""
    user_id, person_id = str(uuid4()), str(uuid4())
    now = _now()
    conn.execute(text(
        "INSERT INTO users (id, nome, email, password_hash, ativo, created_at, updated_at) "
        "VALUES (:id, 'Sintético M57', :email, 'x', 1, :now, :now)"
    ), {"id": user_id, "email": f"m57-{tag}@teste.invalid", "now": now})
    conn.execute(text(
        "INSERT INTO people (id, public_code, nome_completo, nome_normalizado, status, "
        "nao_contatar, arquivado, created_at, updated_at) "
        "VALUES (:id, :code, 'Pessoa Sintética', 'pessoa sintetica', 'ativo', 0, 0, :now, :now)"
    ), {"id": person_id, "code": f"PES-M57{tag}"[:20], "now": now})
    return user_id, person_id


def _make_document(conn, user_id, person_id, slug, *, environment,
                   attempt=True, artifact=True, **attempt_overrides):
    """One ``simulated`` document plus the evidence the promotion rule reads.

    With no overrides this is a perfectly proven real issuance. Each test
    passes exactly one override (or attempt/artifact=False) to break exactly
    one criterion.
    """
    now = _now()
    exam_id, document_id, prep_id = str(uuid4()), str(uuid4()), str(uuid4())
    conn.execute(text(
        "INSERT INTO spirometry_exams (id, public_code, person_id, status, "
        "data_exame_dia_assumido, created_at, updated_at) "
        "VALUES (:id, :code, :person_id, 'Realizado', 0, :now, :now)"
    ), {"id": exam_id, "code": f"ESP-M57{slug}"[:20], "person_id": person_id, "now": now})
    conn.execute(text(
        "INSERT INTO fiscal_documents (id, spirometry_exam_id, environment, state, eligibility, "
        "blocking_reasons, created_by, idempotency_key, idempotency_fingerprint, created_at, updated_at) "
        "VALUES (:id, :exam_id, :environment, 'simulated', 'eligible', '[]', :user_id, "
        ":key, :fingerprint, :now, :now)"
    ), {"id": document_id, "exam_id": exam_id, "environment": environment, "user_id": user_id,
        "key": f"m57-key-{slug}", "fingerprint": f"m57-fp-{slug}", "now": now})
    conn.execute(text(
        "INSERT INTO fiscal_preparations (id, document_id, flow, blocking_reasons, fingerprint, "
        "created_by, created_at) "
        "VALUES (:id, :document_id, 'proprio', '[]', :fingerprint, :user_id, :now)"
    ), {"id": prep_id, "document_id": document_id, "fingerprint": f"m57-prep-{slug}",
        "user_id": user_id, "now": now})

    if attempt:
        row = {
            "id": str(uuid4()), "document_id": document_id, "preparation_id": prep_id,
            "operation_id": str(uuid4()), "operation": "reconcile", "phase": "completed",
            "number": 1, "provider": "restricted", "environment": environment,
            "outcome": "issued", "external_id": REAL_ACCESS_KEY,
            "reconciliation_required": 0, "actor_id": user_id, "started_at": now,
            "completed_at": now, "key": f"m57-att-{slug}", "fingerprint": f"m57-attfp-{slug}",
        }
        row.update(attempt_overrides)
        conn.execute(text(
            "INSERT INTO fiscal_attempts (id, document_id, preparation_id, operation_id, operation, "
            "phase, number, provider, environment, outcome, external_id, reconciliation_required, "
            "actor_id, started_at, completed_at, idempotency_key, idempotency_fingerprint) "
            "VALUES (:id, :document_id, :preparation_id, :operation_id, :operation, :phase, :number, "
            ":provider, :environment, :outcome, :external_id, :reconciliation_required, :actor_id, "
            ":started_at, :completed_at, :key, :fingerprint)"
        ), row)

    if artifact:
        conn.execute(text(
            "INSERT INTO fiscal_artifacts (id, document_id, attempt_id, kind, storage_relative_path, "
            "sha256, size_bytes, created_by, created_at) "
            "VALUES (:id, :document_id, NULL, 'nfse_xml', :path, :sha, 9756, :user_id, :now)"
        ), {"id": str(uuid4()), "document_id": document_id,
            "path": f"fiscal/{document_id}/nfse.xml", "sha": "a" * 64,
            "user_id": user_id, "now": now})

    return document_id


def _state(engine, document_id):
    with engine.connect() as conn:
        return conn.scalar(text("SELECT state FROM fiscal_documents WHERE id = :id"),
                           {"id": document_id})


@pytest.fixture
def at_pre_m57(tmp_path, monkeypatch):
    """A database upgraded to the revision JUST BEFORE M57, so the rows a test
    inserts are exactly the rows M57 will find."""
    monkeypatch.delenv('M15_DATABASE_URL', raising=False)
    url = f'sqlite:///{tmp_path}/m57.db'
    cfg = config(url)
    command.upgrade(cfg, BEFORE_M57)
    engine = create_engine(url)
    yield cfg, engine
    engine.dispose()


# --------------------------------------------------------- the schema half


def test_issued_is_admitted_and_simulated_is_kept(at_pre_m57):
    """``simulated`` is NOT removed: it is still the correct state for the mock
    environment, which issues nothing anywhere. M57 adds a second success
    state, it does not rename the only one."""
    cfg, engine = at_pre_m57
    command.upgrade(cfg, M57)
    with engine.connect() as conn:
        ddl = conn.scalar(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='fiscal_documents'"))
    check = next(line for line in ddl.splitlines() if 'state IN' in line)
    assert "'issued'" in check
    assert "'simulated'" in check


def test_the_identity_triggers_survive_the_table_rebuild(at_pre_m57):
    """SQLite has no ALTER for a CHECK constraint, so this migration rebuilds
    the table — which drops its triggers. Losing them silently would remove the
    database-level protection of fiscal identity, a far worse outcome than the
    naming defect being fixed, so the migration recreates them."""
    cfg, engine = at_pre_m57
    with engine.connect() as conn:
        before = dict(conn.execute(text(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='fiscal_documents'")).all())
    assert set(before) == {'fiscal_documents_no_delete', 'fiscal_documents_identity'}

    command.upgrade(cfg, M57)
    with engine.connect() as conn:
        after = dict(conn.execute(text(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='fiscal_documents'")).all())
    assert after == before  # verbatim, not merely present


def test_the_identity_trigger_still_bites_after_the_migration(at_pre_m57):
    """The triggers are not just present in sqlite_master — they still fire."""
    cfg, engine = at_pre_m57
    with engine.begin() as conn:
        user_id, person_id = _seed_owner(conn, 'bite')
        document_id = _make_document(conn, user_id, person_id, 'bite', environment='restricted')
    command.upgrade(cfg, M57)
    from sqlalchemy.exc import DatabaseError
    with pytest.raises(DatabaseError, match='immutable fiscal identity'):
        with engine.begin() as conn:
            conn.execute(text("UPDATE fiscal_documents SET environment = 'mock' WHERE id = :id"),
                         {"id": document_id})
    with pytest.raises(DatabaseError, match='immutable fiscal evidence'):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM fiscal_documents WHERE id = :id"), {"id": document_id})


# --------------------------------------------------------- the data half


def test_a_fully_proven_real_issuance_is_promoted(at_pre_m57):
    """The control. Everything the rule asks for is present, so the row moves."""
    cfg, engine = at_pre_m57
    with engine.begin() as conn:
        user_id, person_id = _seed_owner(conn, 'ctl')
        document_id = _make_document(conn, user_id, person_id, 'ctl', environment='restricted')
    command.upgrade(cfg, M57)
    assert _state(engine, document_id) == 'issued'


FAIL_CLOSED_CASES = {
    # Each case breaks exactly ONE criterion of the control above.
    'mock_environment': dict(environment='mock'),
    'no_attempt_at_all': dict(environment='restricted', attempt=False),
    'attempt_never_completed': dict(environment='restricted', phase='started'),
    'reconciliation_still_required': dict(environment='restricted', reconciliation_required=1),
    'external_id_missing': dict(environment='restricted', external_id=None),
    'external_id_is_a_mock_id': dict(environment='restricted',
                                     external_id='MOCK-' + str(uuid4())),
    'external_id_not_an_access_key': dict(environment='restricted',
                                          external_id='NFS-NOT-A-REAL-KEY'),
    'external_id_right_length_wrong_shape': dict(environment='restricted',
                                                 external_id='XYZ' + '0' * 50),
    'provider_was_the_mock': dict(environment='restricted', provider='mock'),
    'attempt_was_a_cancellation': dict(environment='restricted', operation='cancel'),
    'no_nfse_xml_artifact': dict(environment='restricted', artifact=False),
}


@pytest.mark.parametrize('case', sorted(FAIL_CLOSED_CASES), ids=sorted(FAIL_CLOSED_CASES))
def test_a_row_missing_any_single_criterion_stays_simulated(at_pre_m57, case):
    """Fail-closed, criterion by criterion. A row is never promoted on the
    strength of its label: 'simulated' is not evidence of anything."""
    cfg, engine = at_pre_m57
    kwargs = dict(FAIL_CLOSED_CASES[case])
    with engine.begin() as conn:
        user_id, person_id = _seed_owner(conn, case[:6])
        document_id = _make_document(conn, user_id, person_id, case[:8], **kwargs)
    command.upgrade(cfg, M57)
    assert _state(engine, document_id) == 'simulated'


def test_a_mock_row_is_never_promoted_even_beside_a_real_one(at_pre_m57):
    """The two live in the same table. Promoting the real one must not drag the
    mock one along — this is the whole point of the migration."""
    cfg, engine = at_pre_m57
    with engine.begin() as conn:
        user_id, person_id = _seed_owner(conn, 'both')
        real = _make_document(conn, user_id, person_id, 'real', environment='restricted')
        mock = _make_document(conn, user_id, person_id, 'mock', environment='mock')
    command.upgrade(cfg, M57)
    assert _state(engine, real) == 'issued'
    assert _state(engine, mock) == 'simulated'


def test_promotion_preserves_identity_evidence_and_adds_no_attempt(at_pre_m57):
    """The migration moves ``state`` and nothing else: no new FiscalAttempt, no
    new artifact, no new DPS number, and the access key untouched."""
    cfg, engine = at_pre_m57
    with engine.begin() as conn:
        user_id, person_id = _seed_owner(conn, 'presv')
        document_id = _make_document(conn, user_id, person_id, 'presv', environment='restricted')

    def snapshot():
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT spirometry_exam_id, environment, created_by, idempotency_key, "
                "idempotency_fingerprint, eligibility FROM fiscal_documents WHERE id = :id"
            ), {"id": document_id}).one()
            attempts = conn.execute(text(
                "SELECT id, operation, phase, provider, outcome, external_id "
                "FROM fiscal_attempts WHERE document_id = :id ORDER BY id"
            ), {"id": document_id}).all()
            artifacts = conn.execute(text(
                "SELECT id, kind, sha256, size_bytes FROM fiscal_artifacts "
                "WHERE document_id = :id ORDER BY id"), {"id": document_id}).all()
            audit = conn.scalar(text("SELECT count(*) FROM audit_logs"))
        return row, attempts, artifacts, audit

    before = snapshot()
    command.upgrade(cfg, M57)
    after = snapshot()
    assert after == before
    assert _state(engine, document_id) == 'issued'
    assert before[1][0].external_id == REAL_ACCESS_KEY


def test_the_migration_is_idempotent_across_a_downgrade_upgrade_cycle(at_pre_m57):
    """Re-running it must converge to the same place, and the downgrade must
    return every promoted row to the single pre-M57 success state before the
    constraint stops admitting ``issued``."""
    cfg, engine = at_pre_m57
    with engine.begin() as conn:
        user_id, person_id = _seed_owner(conn, 'cycle')
        real = _make_document(conn, user_id, person_id, 'creal', environment='restricted')
        mock = _make_document(conn, user_id, person_id, 'cmock', environment='mock')

    command.upgrade(cfg, M57)
    assert (_state(engine, real), _state(engine, mock)) == ('issued', 'simulated')

    command.downgrade(cfg, BEFORE_M57)
    assert (_state(engine, real), _state(engine, mock)) == ('simulated', 'simulated')

    command.upgrade(cfg, M57)
    assert (_state(engine, real), _state(engine, mock)) == ('issued', 'simulated')


def test_an_already_issued_row_is_not_re_promoted(at_pre_m57):
    """The data step only ever reads rows that are still ``simulated``, so a
    second run over an already-migrated database changes nothing."""
    cfg, engine = at_pre_m57
    with engine.begin() as conn:
        user_id, person_id = _seed_owner(conn, 'again')
        document_id = _make_document(conn, user_id, person_id, 'again', environment='restricted')
    command.upgrade(cfg, M57)
    with engine.connect() as conn:
        updated_at = conn.scalar(text("SELECT updated_at FROM fiscal_documents WHERE id = :id"),
                                 {"id": document_id})
    command.upgrade(cfg, 'head')  # no-op: already at head
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT updated_at FROM fiscal_documents WHERE id = :id"),
                           {"id": document_id}) == updated_at
    assert _state(engine, document_id) == 'issued'
