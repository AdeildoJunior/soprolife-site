"""Append a fiscal correction on the same day without rewriting an old rule."""
from alembic import op
import sqlalchemy as sa

revision = 'a191126direct'
down_revision = 'u181126ux'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('partner_commercial_rules') as batch:
        batch.drop_constraint('uq_commercial_rule_version', type_='unique')
        batch.create_unique_constraint('uq_commercial_rule_version', ['codigo', 'vigencia_inicio', 'created_at'])


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM (SELECT codigo, vigencia_inicio FROM partner_commercial_rules GROUP BY codigo, vigencia_inicio HAVING count(*) > 1) versions')):
        raise RuntimeError('Versões intradiárias contêm evidência; preserve schema e reverta somente o código.')
    with op.batch_alter_table('partner_commercial_rules') as batch:
        batch.drop_constraint('uq_commercial_rule_version', type_='unique')
        batch.create_unique_constraint('uq_commercial_rule_version', ['codigo', 'vigencia_inicio'])
