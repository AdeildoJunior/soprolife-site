"""m29 national dps configurations

Revision ID: ba3afa480112
Revises: 9c310c422ce2
Create Date: 2026-09-13 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'ba3afa480112'
down_revision = '9c310c422ce2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('national_dps_configurations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.String(length=60), nullable=False),
    sa.Column('environment', sa.String(length=20), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('validation_state', sa.String(length=20), nullable=False),
    sa.Column('configuration', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("environment IN ('restricted','production')", name=op.f('ck_national_dps_configurations_national_dps_config_environment')),
    sa.CheckConstraint("validation_state IN ('draft','validated')", name=op.f('ck_national_dps_configurations_national_dps_config_validation')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_national_dps_configurations_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_national_dps_configurations')),
    sa.UniqueConstraint('version', name=op.f('uq_national_dps_configurations_version'))
    )
    with op.batch_alter_table('national_dps_configurations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_national_dps_configurations_environment'), ['environment'], unique=False)
        batch_op.create_index(batch_op.f('ix_national_dps_configurations_effective_from'), ['effective_from'], unique=False)

    # Append-only, same contract as fiscal_artifacts/fiscal_policies: a new
    # accountant decision is always a new row. Reuses the same
    # fiscal_reject_mutation() PL/pgSQL function created by
    # f6a1d9e28b40 (already used by 9c310c422ce2) on Postgres.
    dialect = op.get_bind().dialect.name
    if dialect == 'postgresql':
        op.execute("CREATE TRIGGER national_dps_configurations_immutable BEFORE UPDATE OR DELETE ON national_dps_configurations FOR EACH ROW EXECUTE FUNCTION fiscal_reject_mutation()")
    elif dialect == 'sqlite':
        for action in ('UPDATE', 'DELETE'):
            op.execute(f"CREATE TRIGGER national_dps_configurations_no_{action.lower()} BEFORE {action} ON national_dps_configurations BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END")


def downgrade() -> None:
    with op.batch_alter_table('national_dps_configurations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_national_dps_configurations_effective_from'))
        batch_op.drop_index(batch_op.f('ix_national_dps_configurations_environment'))
    op.drop_table('national_dps_configurations')
