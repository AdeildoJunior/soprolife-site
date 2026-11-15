"""Generic versioned commercial terms and settlements; no historical backfill."""
from alembic import op
import sqlalchemy as sa

revision = 'p151126share'
down_revision = 'w141126wa'
branch_labels = None
depends_on = None


def fk(name, target, **kw):
    return sa.Column(name, sa.String(36), sa.ForeignKey(target), **kw)


def upgrade():
    op.create_table('partner_commercial_rules',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('codigo', sa.String(40), nullable=False),
        fk('partnership_id', 'partnerships.id', nullable=False),
        fk('partner_unit_id', 'partner_units.id', nullable=False),
        sa.Column('modalidade', sa.String(30), nullable=False),
        sa.Column('vigencia_inicio', sa.Date, nullable=False),
        sa.Column('vigencia_fim', sa.Date),
        sa.Column('percentual_parceiro', sa.Numeric(5, 2), nullable=False),
        sa.Column('percentual_soprolife', sa.Numeric(5, 2), nullable=False),
        sa.Column('fiscal_flow', sa.String(10)),
        fk('created_by_user_id', 'users.id', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('codigo', 'vigencia_inicio', name='uq_commercial_rule_version'),
        sa.CheckConstraint('percentual_parceiro >= 0 AND percentual_soprolife >= 0 AND percentual_parceiro + percentual_soprolife = 100', name='rule_percent'),
        sa.CheckConstraint('vigencia_fim IS NULL OR vigencia_fim >= vigencia_inicio', name='rule_dates'),
        sa.CheckConstraint("fiscal_flow IS NULL OR fiscal_flow = 'DIRECT'", name='rule_fiscal_flow'))
    op.create_index('ix_partner_commercial_rules_codigo', 'partner_commercial_rules', ['codigo'])
    op.create_table('exam_partner_shares',
        sa.Column('id', sa.String(36), primary_key=True),
        fk('rule_id', 'partner_commercial_rules.id', nullable=False),
        fk('spirometry_exam_id', 'spirometry_exams.id', nullable=False, unique=True),
        fk('financial_entry_id', 'financial_entries.id', nullable=False, unique=True),
        fk('partner_id', 'partners.id', nullable=False),
        fk('partner_unit_id', 'partner_units.id', nullable=False),
        sa.Column('modalidade', sa.String(30), nullable=False),
        sa.Column('vigencia_inicio', sa.Date, nullable=False),
        sa.Column('percentual_parceiro', sa.Numeric(5, 2), nullable=False),
        sa.Column('percentual_soprolife', sa.Numeric(5, 2), nullable=False),
        sa.Column('valor_bruto', sa.Numeric(12, 2), nullable=False),
        sa.Column('parcela_parceiro', sa.Numeric(12, 2), nullable=False),
        sa.Column('parcela_soprolife', sa.Numeric(12, 2), nullable=False),
        sa.Column('recebido_por', sa.String(20), nullable=False),
        fk('created_by_user_id', 'users.id', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('percentual_parceiro >= 0 AND percentual_soprolife >= 0 AND percentual_parceiro + percentual_soprolife = 100', name='share_percent'),
        sa.CheckConstraint('valor_bruto > 0 AND parcela_parceiro >= 0 AND parcela_soprolife >= 0 AND parcela_parceiro + parcela_soprolife = valor_bruto', name='share_amount'),
        sa.CheckConstraint("recebido_por IN ('soprolife','parceiro','dividido')", name='share_receiver'))
    op.create_table('partner_share_movements',
        sa.Column('id', sa.String(36), primary_key=True),
        fk('share_id', 'exam_partner_shares.id', nullable=False),
        fk('financial_entry_id', 'financial_entries.id', nullable=False, unique=True),
        sa.Column('natureza', sa.String(30), nullable=False),
        sa.Column('direcao', sa.String(10), nullable=False),
        sa.Column('conta', sa.String(60), nullable=False),
        sa.Column('origem_bancaria', sa.String(120)),
        sa.Column('ocorrido_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('idempotency_key', sa.String(64), nullable=False, unique=True),
        fk('created_by_user_id', 'users.id', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("natureza IN ('acerto','regularizacao')", name='movement_kind'),
        sa.CheckConstraint("direcao IN ('entrada','saida')", name='movement_direction'))
    op.create_index('ix_partner_share_movements_share_id', 'partner_share_movements', ['share_id'])

    if op.get_bind().dialect.name == 'postgresql':
        op.execute("""CREATE FUNCTION guard_partner_share_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN RAISE EXCEPTION 'Commercial evidence is immutable'; END; $$""")
        for table in ('partner_commercial_rules', 'exam_partner_shares', 'partner_share_movements'):
            op.execute(f'CREATE TRIGGER immutable_partner_share BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION guard_partner_share_evidence()')


def downgrade():
    for table in ('partner_share_movements', 'exam_partner_shares', 'partner_commercial_rules'):
        if op.get_bind().scalar(sa.text(f'SELECT count(*) FROM {table}')):
            raise RuntimeError('Parcerias contêm evidência; preserve schema e reverta somente o código.')
    for table in ('partner_share_movements', 'exam_partner_shares', 'partner_commercial_rules'):
        op.drop_table(table)

    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP FUNCTION guard_partner_share_evidence()')
