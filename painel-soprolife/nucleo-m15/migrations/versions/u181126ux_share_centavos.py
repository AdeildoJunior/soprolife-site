"""Validate shares in integer cents, including SQLite dev; no fact changes."""
from alembic import op
import sqlalchemy as sa

revision = 'u181126ux'
down_revision = 'p151126share'
branch_labels = None
depends_on = None

EXACT = 'valor_bruto > 0 AND parcela_parceiro >= 0 AND parcela_soprolife >= 0 AND parcela_parceiro + parcela_soprolife = valor_bruto'
CENTS = 'valor_bruto > 0 AND parcela_parceiro >= 0 AND parcela_soprolife >= 0 AND round(parcela_parceiro * 100) + round(parcela_soprolife * 100) = round(valor_bruto * 100)'


def replace(expression):
    with op.batch_alter_table('exam_partner_shares') as batch:
        batch.drop_constraint('share_amount', type_='check')
        batch.create_check_constraint('share_amount', expression)


def upgrade():
    replace(CENTS)


def downgrade():
    # Não restaurar a comparação antiga sobre fatos com centavos já gravados.
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM exam_partner_shares')):
        raise RuntimeError('Partilhas contêm evidência; preserve schema e reverta somente o código.')
    replace(EXACT)
