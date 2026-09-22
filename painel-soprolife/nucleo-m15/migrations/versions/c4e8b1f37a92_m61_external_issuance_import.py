"""M61 — 'import': an NFS-e this system did not issue, recorded so it never issues it again.

Two real NFS-e were issued by hand in PRODUCTION on 2026-09-15, before any of
this automation existed. The facts that generated them live in the operational
database like any other. Nothing currently stops the automation from looking at
one of those facts, finding no fiscal document, and issuing a SECOND invoice
for a service that was already invoiced.

That is the duplication this migration exists to make impossible.

WHY A NEW OPERATION AND NOT AN 'issue' ROW

The obvious shortcut is to write a completed ``issue`` attempt carrying the
known access key. It would work, and it would be a lie: this system never
issued those notes, never built a DPS for them, never called a provider and
never received that key from one. ``fiscal_attempts`` is append-only evidence
of what actually happened at the provider boundary, and filling it with an
issuance that did not happen corrupts exactly the record that makes the
history worth having.

So ``import`` joins ``issue``/``reconcile``/``cancel`` as a distinct operation,
meaning: "a document exists at the tax authority; we learned of it out of
band; here is its key and nothing more".

WHAT THAT BUYS, FOR FREE

Because ``import`` is not ``issue`` or ``reconcile``, and because the importing
provider is not ``production``, the M58 fiscal-validity contract already
refuses to call such a document fiscally valid — on two independent counts,
plus a third (no ``nfse_xml`` artifact, because we do not have the XML and
will not invent one). No part of M58 needs weakening or special-casing. A
document imported this way reads exactly as what it is: issued, real, and not
something this system can vouch for.

And anti-duplication follows from the state machine as it already stands:
``nfse.operate(..., 'issue')`` short-circuits on a document already in a
terminal success state, and ``uq_fiscal_exam_environment`` permits only one
production document per exam.

SCOPE

Widens one CHECK constraint. Writes no rows, reads no rows, and touches no
other table. ``cancel`` is untouched and still unsupported at the provider.

Revision ID: c4e8b1f37a92
Revises: a7d2c95e4f13
Create Date: 2026-09-22 04:10:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'c4e8b1f37a92'
down_revision = 'a7d2c95e4f13'
branch_labels = None
depends_on = None


# Logical name; the project's naming convention (app/db.py) expands it, and
# batch mode applies that convention itself, so it must be the short form.
OPERATION_CONSTRAINT = 'fiscal_attempt_operation'
OPERATION_CONSTRAINT_PHYSICAL = 'ck_fiscal_attempts_' + OPERATION_CONSTRAINT

NAMING_CONVENTION = {
    'ix': 'ix_%(column_0_label)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}

DOCUMENT_ID_INDEX = 'ix_fiscal_attempts_document_id'

OPERATIONS_BEFORE = ('issue', 'reconcile', 'cancel')
OPERATIONS_AFTER = ('issue', 'reconcile', 'cancel', 'import')

# Recreated verbatim after the SQLite table rebuild, which drops them. Losing
# the append-only guarantee on fiscal evidence would be far worse than the
# duplication risk this migration closes.
SQLITE_TRIGGERS = (
    "CREATE TRIGGER fiscal_attempts_no_update BEFORE UPDATE ON fiscal_attempts "
    "BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END",
    "CREATE TRIGGER fiscal_attempts_no_delete BEFORE DELETE ON fiscal_attempts "
    "BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END",
)


def _check_sql(operations) -> str:
    return 'operation IN (%s)' % ','.join("'%s'" % o for o in operations)


def _fiscal_attempts_table(operations) -> sa.Table:
    """The table as it exists at this revision. SQLite does not reflect CHECK
    constraints, so batch mode has to be told about them."""
    return sa.Table(
        'fiscal_attempts', sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('preparation_id', sa.String(length=36), nullable=False),
        sa.Column('operation_id', sa.String(length=36), nullable=False),
        sa.Column('reconciles_operation_id', sa.String(length=36)),
        sa.Column('operation', sa.String(length=20), nullable=False),
        sa.Column('phase', sa.String(length=20), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('environment', sa.String(length=20), nullable=False),
        sa.Column('outcome', sa.String(length=30), nullable=False),
        sa.Column('external_id', sa.String(length=100)),
        sa.Column('error_code', sa.String(length=40)),
        sa.Column('reconciliation_required', sa.Boolean(), nullable=False),
        sa.Column('actor_id', sa.String(length=36), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('idempotency_fingerprint', sa.String(length=64), nullable=False),
        sa.CheckConstraint(_check_sql(operations), name=OPERATION_CONSTRAINT),
        sa.CheckConstraint("phase IN ('started','completed')", name='fiscal_attempt_phase'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'],
                                name='fk_fiscal_attempts_actor_id_users'),
        sa.ForeignKeyConstraint(['document_id'], ['fiscal_documents.id'],
                                name='fk_fiscal_attempts_document_id_fiscal_documents'),
        sa.ForeignKeyConstraint(['preparation_id'], ['fiscal_preparations.id'],
                                name='fk_fiscal_attempts_preparation_id_fiscal_preparations'),
        sa.PrimaryKeyConstraint('id', name='pk_fiscal_attempts'),
        sa.UniqueConstraint('document_id', 'number', 'phase', name='uq_fiscal_attempt_number'),
        sa.UniqueConstraint('idempotency_key', name='uq_fiscal_attempts_idempotency_key'),
        sa.UniqueConstraint('operation_id', 'phase', name='uq_fiscal_attempt_phase'),
    )


def _replace_operation_constraint(operations_from, operations_to) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == 'sqlite':
        for name in ('fiscal_attempts_no_update', 'fiscal_attempts_no_delete'):
            op.execute('DROP TRIGGER IF EXISTS %s' % name)
        with op.batch_alter_table('fiscal_attempts',
                                  copy_from=_fiscal_attempts_table(operations_from)) as batch_op:
            batch_op.drop_constraint(OPERATION_CONSTRAINT, type_='check')
            batch_op.create_check_constraint(OPERATION_CONSTRAINT, _check_sql(operations_to))
        # A batch rebuild recreates the table and takes its INDEXES with it.
        # The first version of this migration silently dropped
        # ix_fiscal_attempts_document_id (index=True on the model) — `alembic
        # check` caught the drift. Recreated explicitly here, for the same
        # reason the triggers are: what the rebuild destroys, the migration
        # must put back, visibly.
        op.create_index(DOCUMENT_ID_INDEX, 'fiscal_attempts', ['document_id'],
                        unique=False, if_not_exists=True)
        for statement in SQLITE_TRIGGERS:
            op.execute(statement)
    else:
        op.drop_constraint(OPERATION_CONSTRAINT_PHYSICAL, 'fiscal_attempts', type_='check')
        op.create_check_constraint(OPERATION_CONSTRAINT_PHYSICAL, 'fiscal_attempts',
                                   _check_sql(operations_to))


def upgrade() -> None:
    _replace_operation_constraint(OPERATIONS_BEFORE, OPERATIONS_AFTER)


def downgrade() -> None:
    # Fail closed rather than silently discard evidence: if any import row
    # exists, narrowing the constraint would either error obscurely or, on a
    # permissive engine, leave rows the schema says cannot exist.
    conn = op.get_bind()
    imported = conn.execute(sa.text(
        "SELECT count(*) FROM fiscal_attempts WHERE operation = 'import'")).scalar_one()
    if imported:
        raise RuntimeError(
            "Existem %d tentativa(s) com operation='import'. Fazer downgrade "
            "apagaria a prova de que essas NFS-e externas já existem, e a "
            "automação voltaria a poder emiti-las em duplicado. Remova-as "
            "deliberadamente antes, se for mesmo isso que se pretende." % imported)
    _replace_operation_constraint(OPERATIONS_AFTER, OPERATIONS_BEFORE)
