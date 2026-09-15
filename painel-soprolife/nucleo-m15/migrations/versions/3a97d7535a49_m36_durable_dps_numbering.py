"""M36 — durable, globally-unique DPS numbering (fixes M35's documented defect).

Root cause: ``numero_dps`` (part of TSIdDPS, the DPS's official identifier —
see ``nfse_national.identifiers.DPS_ID_PATTERN``) was derived from
``FiscalAttempt.number``, an append-only sequence scoped PER
``FiscalDocument``. Every document's first real dispatch attempt therefore
minted ``numero_dps=1`` — for the same issuer municipality/tipo de
inscrição/inscrição federal/série, two different documents could produce
the identical TSIdDPS.

Two new tables, both purely additive (existing tables/data untouched):

- ``dps_number_sequences`` — a durable, mutable counter, one row per scope
  (issuer municipality + tipo_inscricao_federal + inscricao_federal padded
  to 14 + série DPS — exactly the TSIdDPS components besides the number
  itself). Mirrors ``code_sequences``/``ids.allocate_public_code`` exactly,
  including the row-lock allocation pattern (genuinely race-safe on
  PostgreSQL; SQLite is single-writer, used for deterministic tests only —
  same caveat as every other sequence in this codebase, see M23).
- ``dps_number_allocations`` — append-only: the ONE ``numero_dps`` a
  document will ever use within its scope, written once on first dispatch
  and re-read (never re-allocated) on every later attempt for that same
  document. Enforced immutable at the database level below, matching
  ``fiscal_preparations``/``fiscal_attempts`` (see f6a1d9e28b40). The
  ``uq_dps_number_scope`` unique constraint is an additional, independent
  database-level guarantee that no two documents can ever hold the same
  (scope, number) pair, regardless of application logic.

Revision ID: 3a97d7535a49
Revises: a58f6c31d9e7
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '3a97d7535a49'
down_revision = 'a58f6c31d9e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('dps_number_sequences',
    sa.Column('scope_key', sa.String(length=64), nullable=False),
    sa.Column('next_value', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('scope_key', name=op.f('pk_dps_number_sequences'))
    )
    op.create_table('dps_number_allocations',
    sa.Column('document_id', sa.String(length=36), nullable=False),
    sa.Column('scope_key', sa.String(length=64), nullable=False),
    sa.Column('dps_number', sa.Integer(), nullable=False),
    sa.Column('allocated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['fiscal_documents.id'], name=op.f('fk_dps_number_allocations_document_id_fiscal_documents')),
    sa.PrimaryKeyConstraint('document_id', name=op.f('pk_dps_number_allocations')),
    sa.UniqueConstraint('scope_key', 'dps_number', name='uq_dps_number_scope')
    )
    with op.batch_alter_table('dps_number_allocations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dps_number_allocations_scope_key'), ['scope_key'], unique=False)

    # dps_number_sequences stays MUTABLE (it is the counter itself) — no
    # trigger, exactly like code_sequences. dps_number_allocations is
    # append-only fiscal evidence — same immutability contract as
    # fiscal_preparations/fiscal_attempts (f6a1d9e28b40).
    dialect = op.get_bind().dialect.name
    if dialect == 'postgresql':
        op.execute(
            "CREATE TRIGGER dps_number_allocations_immutable "
            "BEFORE UPDATE OR DELETE ON dps_number_allocations "
            "FOR EACH ROW EXECUTE FUNCTION fiscal_reject_mutation()"
        )
    elif dialect == 'sqlite':
        for action in ('UPDATE', 'DELETE'):
            op.execute(
                f"CREATE TRIGGER dps_number_allocations_no_{action.lower()} "
                f"BEFORE {action} ON dps_number_allocations "
                "BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END"
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == 'postgresql':
        op.execute("DROP TRIGGER IF EXISTS dps_number_allocations_immutable ON dps_number_allocations")
    elif dialect == 'sqlite':
        op.execute("DROP TRIGGER IF EXISTS dps_number_allocations_no_update")
        op.execute("DROP TRIGGER IF EXISTS dps_number_allocations_no_delete")

    with op.batch_alter_table('dps_number_allocations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dps_number_allocations_scope_key'))

    op.drop_table('dps_number_allocations')
    op.drop_table('dps_number_sequences')
