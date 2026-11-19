"""Read indexes only. No new facts, business columns or historical backfill."""
from alembic import op
import sqlalchemy as sa

revision='c191126cc2'
down_revision='a191126direct'
branch_labels=None
depends_on=None


def upgrade():
    if op.get_bind().dialect.name=='postgresql':
        op.execute("CREATE INDEX ix_cc_people_name_fts ON people USING gin (to_tsvector('simple', nome_normalizado))")
    if op.get_bind().dialect.name=='postgresql':
        op.execute('CREATE INDEX ix_cc_partner_name_prefix ON partners (lower(nome) text_pattern_ops)')
        op.execute('CREATE INDEX ix_cc_unit_name_prefix ON partner_units (lower(nome) text_pattern_ops)')
    else:
        op.create_index('ix_cc_partner_name_prefix','partners',[sa.text('lower(nome)')])
        op.create_index('ix_cc_unit_name_prefix','partner_units',[sa.text('lower(nome)')])
    op.create_index('ix_cc_fiscal_external','fiscal_attempts',['external_id'])


def downgrade():
    op.drop_index('ix_cc_fiscal_external',table_name='fiscal_attempts')
    op.drop_index('ix_cc_unit_name_prefix',table_name='partner_units')
    op.drop_index('ix_cc_partner_name_prefix',table_name='partners')
    if op.get_bind().dialect.name=='postgresql':
        op.drop_index('ix_cc_people_name_fts',table_name='people')
