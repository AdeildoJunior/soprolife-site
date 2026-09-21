"""M57 — 'issued': a real NFS-e stops being recorded as a simulation.

Until now the fiscal queue had exactly one terminal success state,
``simulated``, minted by the mock foundation (f6a1d9e28b40) when no real
provider existed. M27–M56 wired a real one, and on 2026-09-20 DPS #10 was
issued for real under tpAmb=2 and reconciled locally — and landed in
``state='simulated'``. A human reading that row concludes nothing was
emitted. That is the defect this migration closes, before production
wiring makes it routine.

Two parts:

1. SCHEMA — widen ``ck_fiscal_documents_fiscal_document_state`` to admit
   ``issued``. ``simulated`` is deliberately KEPT: it is still the correct
   state for the mock environment, which issues nothing anywhere.

2. DATA — promote existing ``simulated`` rows to ``issued`` ONLY where the
   database itself already holds proof of a real issuance. The criteria are
   conjunctive and derived from the domain, never from the state name:

   a. ``environment`` is 'restricted' or 'production' (a mock row can never
      qualify, whatever else it carries);
   b. a COMPLETED attempt exists for the document, operation 'issue' or
      'reconcile', from a provider named 'restricted'/'production', with
      ``reconciliation_required`` false and a non-null ``external_id``;
   c. that ``external_id`` is a government NFS-e access key — matched
      against the same TSIdNFSe shape the application enforces
      (``nfse_national.identifiers.NFSE_ACCESS_KEY_PATTERN``, inlined here
      so the migration never depends on importable application code); a
      ``MOCK-<uuid>`` identifier fails it;
   d. a ``fiscal_artifacts`` row of kind ``nfse_xml`` exists for the
      document — the NFS-e document the authority actually returned.

   Anything that misses any criterion stays ``simulated`` and is counted as
   ambiguous: this migration never upgrades a row on the strength of its
   label alone. Re-running it is a no-op (idempotent by construction — it
   only ever reads rows that are still ``simulated``).

WHAT THIS MIGRATION DOES NOT TOUCH, on purpose: ``fiscal_attempts``,
``fiscal_artifacts``, ``fiscal_preparations`` and ``audit_logs``. Those are
append-only evidence, enforced at the database level by
``fiscal_*_no_update``/``no_delete`` triggers — the record of what a
provider reported at the time it reported it, in the vocabulary of that
time. Rewriting history to make it agree with today's words would destroy
the guarantee that makes the history worth having. Only
``fiscal_documents.state``, which the state machine mutates by design,
moves.

Revision ID: a7d2c95e4f13
Revises: 3a97d7535a49
Create Date: 2026-09-21 20:00:00.000000
"""
from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


revision = 'a7d2c95e4f13'
down_revision = '3a97d7535a49'
branch_labels = None
depends_on = None


# The LOGICAL check-constraint name. The project's naming convention
# ("ck": "ck_%(table_name)s_%(constraint_name)s", app/db.py) expands it to the
# physical name below; batch mode applies that convention itself, so it must be
# given the short form or it would look for the name doubled.
STATE_CONSTRAINT = 'fiscal_document_state'
STATE_CONSTRAINT_PHYSICAL = 'ck_fiscal_documents_' + STATE_CONSTRAINT
# Reproduces app.db.NAMING_CONVENTION, so the rebuilt SQLite table carries
# exactly the constraint names the original one had.
NAMING_CONVENTION = {
    'ix': 'ix_%(column_0_label)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}
STATES_BEFORE = ('blocked', 'pending', 'issuing', 'simulated', 'failed',
                 'uncertain', 'reconciling', 'cancelled')
STATES_AFTER = ('blocked', 'pending', 'issuing', 'issued', 'simulated', 'failed',
                'uncertain', 'reconciling', 'cancelled')

# Same shape as nfse_national.identifiers.NFSE_ACCESS_KEY_PATTERN. Duplicated
# rather than imported: a migration must keep running against a database
# written by any past or future version of the application code.
NFSE_ACCESS_KEY = re.compile(r'^NFS[0-9]{9}[0-9A-Z]{14}[0-9]{27}$')

REAL_ENVIRONMENTS = ('restricted', 'production')

# The two SQLite triggers that guard fiscal_documents. A batch (table-rebuild)
# migration drops them with the old table and Alembic does not put them back,
# so they are recreated verbatim below. Losing them silently would remove the
# database-level protection of fiscal identity — a far worse outcome than the
# naming defect this migration fixes.
SQLITE_TRIGGERS = (
    "CREATE TRIGGER fiscal_documents_no_delete BEFORE DELETE ON fiscal_documents "
    "BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END",
    """CREATE TRIGGER fiscal_documents_identity BEFORE UPDATE ON fiscal_documents
            WHEN NEW.spirometry_exam_id IS NOT OLD.spirometry_exam_id
              OR NEW.environment IS NOT OLD.environment OR NEW.created_by IS NOT OLD.created_by
              OR NEW.idempotency_key IS NOT OLD.idempotency_key
              OR NEW.idempotency_fingerprint IS NOT OLD.idempotency_fingerprint
            BEGIN SELECT RAISE(ABORT, 'immutable fiscal identity'); END""",
)


def _check_sql(states) -> str:
    return 'state IN (%s)' % ','.join("'%s'" % s for s in states)


def _fiscal_documents_table(states) -> sa.Table:
    """The table as it exists at this revision, needed by SQLite batch mode:
    SQLite does not reflect CHECK constraints, so they must be declared."""
    return sa.Table(
        'fiscal_documents', sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('spirometry_exam_id', sa.String(length=36), nullable=False),
        sa.Column('environment', sa.String(length=20), nullable=False),
        sa.Column('state', sa.String(length=30), nullable=False),
        sa.Column('eligibility', sa.String(length=20), nullable=False),
        sa.Column('blocking_reasons', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(length=36), nullable=False),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('idempotency_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("environment IN ('mock','restricted','production')",
                           name='fiscal_document_environment'),
        sa.CheckConstraint(_check_sql(states), name=STATE_CONSTRAINT),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'],
                                name='fk_fiscal_documents_created_by_users'),
        sa.ForeignKeyConstraint(['spirometry_exam_id'], ['spirometry_exams.id'],
                                name='fk_fiscal_documents_spirometry_exam_id_spirometry_exams'),
        sa.PrimaryKeyConstraint('id', name='pk_fiscal_documents'),
        sa.UniqueConstraint('idempotency_key', name='uq_fiscal_documents_idempotency_key'),
        sa.UniqueConstraint('spirometry_exam_id', 'environment', name='uq_fiscal_exam_environment'),
    )


def _replace_state_constraint(states_from, states_to) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == 'sqlite':
        for name in ('fiscal_documents_no_delete', 'fiscal_documents_identity'):
            op.execute('DROP TRIGGER IF EXISTS %s' % name)
        with op.batch_alter_table('fiscal_documents',
                                  copy_from=_fiscal_documents_table(states_from)) as batch_op:
            batch_op.drop_constraint(STATE_CONSTRAINT, type_='check')
            batch_op.create_check_constraint(STATE_CONSTRAINT, _check_sql(states_to))
        for statement in SQLITE_TRIGGERS:
            op.execute(statement)
    else:
        op.drop_constraint(STATE_CONSTRAINT_PHYSICAL, 'fiscal_documents', type_='check')
        op.create_check_constraint(STATE_CONSTRAINT_PHYSICAL, 'fiscal_documents',
                                   _check_sql(states_to))


def _proven_real_issuances(conn) -> list[str]:
    """Document ids currently 'simulated' that the database itself proves were
    really issued. Every criterion must hold; ambiguity leaves the row alone.

    Built with Core expressions rather than raw SQL so the boolean test on
    ``reconciliation_required`` renders correctly on both SQLite (integer 0/1)
    and PostgreSQL (real boolean).
    """
    meta = sa.MetaData()
    documents = sa.Table('fiscal_documents', meta,
                         sa.Column('id', sa.String(36)),
                         sa.Column('environment', sa.String(20)),
                         sa.Column('state', sa.String(30)))
    attempts = sa.Table('fiscal_attempts', meta,
                        sa.Column('document_id', sa.String(36)),
                        sa.Column('operation', sa.String(20)),
                        sa.Column('phase', sa.String(20)),
                        sa.Column('provider', sa.String(30)),
                        sa.Column('external_id', sa.String(120)),
                        sa.Column('reconciliation_required', sa.Boolean()))
    artifacts = sa.Table('fiscal_artifacts', meta,
                         sa.Column('document_id', sa.String(36)),
                         sa.Column('kind', sa.String(30)))
    stmt = (
        sa.select(documents.c.id, attempts.c.external_id)
        .select_from(documents.join(attempts, attempts.c.document_id == documents.c.id))
        .where(
            documents.c.state == 'simulated',
            documents.c.environment.in_(REAL_ENVIRONMENTS),
            attempts.c.phase == 'completed',
            attempts.c.operation.in_(('issue', 'reconcile')),
            attempts.c.provider.in_(REAL_ENVIRONMENTS),
            attempts.c.external_id.is_not(None),
            attempts.c.reconciliation_required.is_(False),
            sa.exists().where(sa.and_(artifacts.c.document_id == documents.c.id,
                                      artifacts.c.kind == 'nfse_xml')),
        )
    )
    return sorted({row[0] for row in conn.execute(stmt)
                   if NFSE_ACCESS_KEY.fullmatch(row[1] or '')})


def upgrade() -> None:
    _replace_state_constraint(STATES_BEFORE, STATES_AFTER)
    conn = op.get_bind()
    promoted = _proven_real_issuances(conn)
    for document_id in promoted:
        conn.execute(sa.text(
            "UPDATE fiscal_documents SET state = 'issued' "
            "WHERE id = :id AND state = 'simulated'"
        ), {'id': document_id})
    remaining = conn.execute(sa.text(
        "SELECT count(*) FROM fiscal_documents WHERE state = 'simulated' "
        "AND environment IN ('restricted','production')"
    )).scalar_one()
    print('M57: %d documento(s) promovido(s) a issued; %d ainda simulated em '
          'ambiente real (sem prova suficiente — failed closed).'
          % (len(promoted), remaining))


def downgrade() -> None:
    # Reverses exactly what upgrade() did: every 'issued' row goes back to the
    # single pre-M57 success state before the constraint stops admitting it.
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE fiscal_documents SET state = 'simulated' WHERE state = 'issued'"))
    _replace_state_constraint(STATES_AFTER, STATES_BEFORE)
