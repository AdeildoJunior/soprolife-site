"""M70 — a fiscal preparation may take its amount from the Pastore partnership.

Until M70 ``fiscal_snapshot_source`` required ``financial_entry_id`` for every
amount: the only revenue source was the exam's own ledger entry. A Pastore exam
has no such entry by design — what SoproLife receives per exam is the
partnership's ``valor_recebido_por_exame`` (see services/partner_pricing.py),
and inventing a FinancialEntry just to satisfy this CHECK would be a fictitious
revenue line. So the CHECK is widened to EXACTLY ONE of two sources:

- ``financial_entry_id`` and no ``partnership_id`` (DIRECT/HOME, unchanged), or
- ``partnership_id``, no ``financial_entry_id``, ``flow = 'PASTORE'`` and
  ``amount_source = 'partnership.valor_recebido_por_exame'``.

Three nullable columns are added. Every existing row keeps NULL in all three,
and every existing row already satisfies the new CHECK (a DIRECT/HOME amount
has its entry; EXTERNAL imports have no amount). No existing preparation is
modified — the table is append-only and stays that way.

SQLite cannot alter a CHECK, so the table is rebuilt (batch copy-and-swap).
The rebuild drops the append-only triggers and the document_id index; both are
put back explicitly, exactly as M57/M61 do for their tables. PostgreSQL alters
in place: ALTER TABLE does not fire the row-level immutability trigger.

Revision ID: b70c4a2e9d15
Revises: e8b3d6a4f190
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b70c4a2e9d15'
down_revision = 'e8b3d6a4f190'
branch_labels = None
depends_on = None

TABLE = 'fiscal_preparations'
# Physical name, created with op.f() by f6a1d9e28b40. PostgreSQL gets it
# through op.f() so no naming convention expands it a second time (the M62
# lesson); SQLite batch mode applies the convention itself, so it gets the
# SHORT logical name, exactly as M61 does.
CHECK_NAME = 'ck_fiscal_preparations_fiscal_snapshot_source'
CHECK_LOGICAL = 'fiscal_snapshot_source'
NAMING_CONVENTION = {
    'ix': 'ix_%(column_0_label)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}
DOCUMENT_ID_INDEX = 'ix_fiscal_preparations_document_id'
FK_PARTNERSHIP = 'fk_fiscal_preparations_partnership_id_partnerships'
FK_PARTNER_UNIT = 'fk_fiscal_preparations_partner_unit_id_partner_units'

CHECK_BEFORE = ('amount_snapshot IS NULL OR (financial_entry_id IS NOT NULL '
                'AND policy_id IS NOT NULL AND amount_snapshot > 0)')
CHECK_AFTER = (
    "amount_snapshot IS NULL OR (policy_id IS NOT NULL AND amount_snapshot > 0 AND ("
    "(financial_entry_id IS NOT NULL AND partnership_id IS NULL) OR "
    "(financial_entry_id IS NULL AND partnership_id IS NOT NULL AND flow = 'PASTORE' "
    "AND amount_source = 'partnership.valor_recebido_por_exame')))"
)

SQLITE_TRIGGERS = (
    "CREATE TRIGGER fiscal_preparations_no_update BEFORE UPDATE ON fiscal_preparations "
    "BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END",
    "CREATE TRIGGER fiscal_preparations_no_delete BEFORE DELETE ON fiscal_preparations "
    "BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END",
)


def _table_before() -> sa.Table:
    """The table as migrations left it at e8b3d6a4f190. SQLite does not
    reflect CHECK constraints, so batch mode is told about it explicitly."""
    return sa.Table(
        TABLE, sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('financial_entry_id', sa.String(length=36), nullable=True),
        sa.Column('policy_id', sa.String(length=36), nullable=True),
        sa.Column('recipient_person_id', sa.String(length=36), nullable=True),
        sa.Column('flow', sa.String(length=20), nullable=False),
        sa.Column('service_date', sa.Date(), nullable=True),
        sa.Column('competence', sa.Date(), nullable=True),
        sa.Column('amount_snapshot', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('description', sa.String(length=200), nullable=True),
        sa.Column('blocking_reasons', sa.JSON(), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.Column('created_by', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('service_municipio_ibge', sa.String(length=7), nullable=True),
        sa.CheckConstraint(CHECK_BEFORE, name=CHECK_LOGICAL),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'],
                                name='fk_fiscal_preparations_created_by_users'),
        sa.ForeignKeyConstraint(['document_id'], ['fiscal_documents.id'],
                                name='fk_fiscal_preparations_document_id_fiscal_documents'),
        sa.ForeignKeyConstraint(['financial_entry_id'], ['financial_entries.id'],
                                name='fk_fiscal_preparations_financial_entry_id_financial_entries'),
        sa.ForeignKeyConstraint(['policy_id'], ['fiscal_policies.id'],
                                name='fk_fiscal_preparations_policy_id_fiscal_policies'),
        sa.ForeignKeyConstraint(['recipient_person_id'], ['people.id'],
                                name='fk_fiscal_preparations_recipient_person_id_people'),
        sa.PrimaryKeyConstraint('id', name='pk_fiscal_preparations'),
    )


def _new_columns():
    return (
        sa.Column('amount_source', sa.String(length=40), nullable=True),
        sa.Column('partnership_id', sa.String(length=36), nullable=True),
        sa.Column('partner_unit_id', sa.String(length=36), nullable=True),
    )


def _sqlite_rebuild(*, upgrade: bool) -> None:
    for name in ('fiscal_preparations_no_update', 'fiscal_preparations_no_delete'):
        op.execute('DROP TRIGGER IF EXISTS %s' % name)
    table = _table_before()
    if not upgrade:
        for column in _new_columns():
            table.append_column(column)
        table.append_constraint(sa.ForeignKeyConstraint(
            ['partnership_id'], ['partnerships.id'], name=FK_PARTNERSHIP))
        table.append_constraint(sa.ForeignKeyConstraint(
            ['partner_unit_id'], ['partner_units.id'], name=FK_PARTNER_UNIT))
        for constraint in list(table.constraints):
            if isinstance(constraint, sa.CheckConstraint):
                constraint.sqltext = sa.text(CHECK_AFTER)
    with op.batch_alter_table(TABLE, copy_from=table) as batch_op:
        batch_op.drop_constraint(CHECK_LOGICAL, type_='check')
        if upgrade:
            for column in _new_columns():
                batch_op.add_column(column)
            batch_op.create_foreign_key(FK_PARTNERSHIP, 'partnerships', ['partnership_id'], ['id'])
            batch_op.create_foreign_key(FK_PARTNER_UNIT, 'partner_units', ['partner_unit_id'], ['id'])
            batch_op.create_check_constraint(CHECK_LOGICAL, CHECK_AFTER)
        else:
            batch_op.drop_constraint(FK_PARTNER_UNIT, type_='foreignkey')
            batch_op.drop_constraint(FK_PARTNERSHIP, type_='foreignkey')
            batch_op.drop_column('partner_unit_id')
            batch_op.drop_column('partnership_id')
            batch_op.drop_column('amount_source')
            batch_op.create_check_constraint(CHECK_LOGICAL, CHECK_BEFORE)
    # What the rebuild destroys, the migration puts back, visibly.
    op.create_index(DOCUMENT_ID_INDEX, TABLE, ['document_id'], unique=False, if_not_exists=True)
    for statement in SQLITE_TRIGGERS:
        op.execute(statement)


def upgrade() -> None:
    if op.get_bind().dialect.name == 'sqlite':
        _sqlite_rebuild(upgrade=True)
        return
    for column in _new_columns():
        op.add_column(TABLE, column)
    op.create_foreign_key(op.f(FK_PARTNERSHIP), TABLE, 'partnerships', ['partnership_id'], ['id'])
    op.create_foreign_key(op.f(FK_PARTNER_UNIT), TABLE, 'partner_units', ['partner_unit_id'], ['id'])
    op.drop_constraint(op.f(CHECK_NAME), TABLE, type_='check')
    op.create_check_constraint(op.f(CHECK_NAME), TABLE, CHECK_AFTER)


def downgrade() -> None:
    # Fail closed rather than discard evidence: a partner-share preparation
    # cannot exist under the old CHECK, and the table is append-only.
    conn = op.get_bind()
    count = conn.execute(sa.text(
        'SELECT count(*) FROM fiscal_preparations WHERE partnership_id IS NOT NULL')).scalar_one()
    if count:
        raise RuntimeError(
            f'{count} preparação(ões) com fonte de parceria existem; o downgrade da M70 '
            'apagaria evidência fiscal. Abortado.')
    if conn.dialect.name == 'sqlite':
        _sqlite_rebuild(upgrade=False)
        return
    op.drop_constraint(op.f(CHECK_NAME), TABLE, type_='check')
    op.create_check_constraint(op.f(CHECK_NAME), TABLE, CHECK_BEFORE)
    op.drop_constraint(op.f(FK_PARTNER_UNIT), TABLE, type_='foreignkey')
    op.drop_constraint(op.f(FK_PARTNERSHIP), TABLE, type_='foreignkey')
    op.drop_column(TABLE, 'partner_unit_id')
    op.drop_column(TABLE, 'partnership_id')
    op.drop_column(TABLE, 'amount_source')
