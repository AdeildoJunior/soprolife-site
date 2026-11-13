"""Prospective patient funnel. Additive, no historical classification/backfill."""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

revision = "f131126funil"
down_revision = "a04f1c202611"
branch_labels = None
depends_on = None

TIME_FIELDS = ('primeiro_contato_em', 'primeira_resposta_em', 'agendado_em', 'realizado_em')
TEXT_FIELDS = {'origem_aquisicao':40, 'unidade_pretendida':40, 'status_funil':30,
               'motivo_encerramento':40, 'idempotency_key':64, 'idempotency_fingerprint':64}

def upgrade():
    with op.batch_alter_table('leads') as b:
        for name in TIME_FIELDS:
            b.add_column(sa.Column(name, sa.DateTime(timezone=True), nullable=True))
        for name, size in TEXT_FIELDS.items():
            b.add_column(sa.Column(name, sa.String(size), nullable=True))
        b.add_column(sa.Column('exame_previsto_em', sa.Date(), nullable=True))
        b.add_column(sa.Column('partner_id', sa.String(36), nullable=True))
        b.add_column(sa.Column('partner_unit_id', sa.String(36), nullable=True))
        b.create_foreign_key('fk_leads_partner', 'partners', ['partner_id'], ['id'])
        b.create_foreign_key('fk_leads_unit', 'partner_units', ['partner_unit_id'], ['id'])
        b.create_unique_constraint('uq_leads_idempotency_key', ['idempotency_key'])
        b.create_index('ix_leads_primeiro_contato_em', ['primeiro_contato_em'])
    with op.batch_alter_table('spirometry_exams') as b:
        b.add_column(sa.Column('lead_id', sa.String(36), nullable=True))
        b.create_foreign_key('fk_exams_funnel_lead', 'leads', ['lead_id'], ['id'])
        b.create_index('ix_spirometry_exams_lead_id', ['lead_id'])
    op.create_table('funnel_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('lead_id', sa.String(36), sa.ForeignKey('leads.id'), nullable=False),
        sa.Column('event_type', sa.String(30), nullable=False),
        sa.Column('event_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id')),
        sa.Column('related_exam_id', sa.String(36), sa.ForeignKey('spirometry_exams.id')),
        sa.Column('previous_date', sa.Date()), sa.Column('scheduled_date', sa.Date()),
        sa.Column('reason_code', sa.String(40)), sa.Column('dimensions', sa.JSON()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_funnel_events_lead_id', 'funnel_events', ['lead_id'])
    op.create_table('funnel_deployment',
        sa.Column('key', sa.String(30), primary_key=True),
        sa.Column('deployed_at', sa.DateTime(timezone=True), nullable=False))
    op.bulk_insert(sa.table('funnel_deployment', sa.column('key',sa.String),
        sa.column('deployed_at',sa.DateTime(timezone=True))),
        [{'key':'patient_funnel', 'deployed_at':datetime.now(timezone.utc)}])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("""CREATE FUNCTION guard_funnel_events() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'funnel_events are append-only'; END; $$""")
        op.execute('CREATE TRIGGER funnel_events_immutable BEFORE UPDATE OR DELETE ON funnel_events FOR EACH ROW EXECUTE FUNCTION guard_funnel_events()')


def downgrade():
    bind=op.get_bind()
    # Refuse to discard new facts. Code rollback can keep this additive schema.
    if bind.execute(sa.text('SELECT count(*) FROM funnel_events')).scalar() or bind.execute(sa.text('SELECT count(*) FROM leads WHERE primeiro_contato_em IS NOT NULL')).scalar() or bind.execute(sa.text('SELECT count(*) FROM spirometry_exams WHERE lead_id IS NOT NULL')).scalar():
        raise RuntimeError('Funil contém evidência; preserve schema e faça rollback apenas do código.')
    if bind.dialect.name == 'postgresql':
        op.execute('DROP TRIGGER funnel_events_immutable ON funnel_events')
        op.execute('DROP FUNCTION guard_funnel_events()')
    op.drop_table('funnel_events')
    op.drop_table('funnel_deployment')
    with op.batch_alter_table('spirometry_exams') as b:
        b.drop_index('ix_spirometry_exams_lead_id')
        b.drop_constraint('fk_exams_funnel_lead', type_='foreignkey')
        b.drop_column('lead_id')
    with op.batch_alter_table('leads') as b:
        b.drop_index('ix_leads_primeiro_contato_em')
        b.drop_constraint('uq_leads_idempotency_key', type_='unique')
        b.drop_constraint('fk_leads_partner', type_='foreignkey')
        b.drop_constraint('fk_leads_unit', type_='foreignkey')
        for name in (*TIME_FIELDS, *TEXT_FIELDS, 'exame_previsto_em', 'partner_id','partner_unit_id'):
            b.drop_column(name)
