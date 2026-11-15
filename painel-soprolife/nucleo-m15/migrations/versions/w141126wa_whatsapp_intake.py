"""Minimal WhatsApp intake/idempotency; additive, no history or payload backfill."""
from alembic import op
import sqlalchemy as sa

revision = 'w141126wa'
down_revision = 'f131126funil'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('whatsapp_peers', sa.Column('sender_key', sa.String(64), primary_key=True))
    op.create_table('whatsapp_intakes',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('sender_key', sa.String(64), sa.ForeignKey('whatsapp_peers.sender_key'), nullable=False),
        sa.Column('phone_suffix', sa.String(4), nullable=False),
        sa.Column('first_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lead_id', sa.String(36), sa.ForeignKey('leads.id')),
        sa.Column('state', sa.String(30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('unknown','ambiguous_person','ambiguous_lead','identified','chronology_review')", name='state'))
    op.create_table('whatsapp_events',
        sa.Column('event_key', sa.String(64), primary_key=True),
        sa.Column('sender_key', sa.String(64), sa.ForeignKey('whatsapp_peers.sender_key'), nullable=False),
        sa.Column('intake_id', sa.String(36), sa.ForeignKey('whatsapp_intakes.id')),
        sa.Column('direction', sa.String(3), nullable=False),
        sa.Column('event_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.CheckConstraint("direction IN ('in','out')", name='direction'),
        sa.CheckConstraint("state IN ('pending','processed','ignored')", name='state'))
    for table, columns in [('whatsapp_intakes', ('sender_key','lead_id')), ('whatsapp_events', ('sender_key','intake_id','received_at'))]:
        for column in columns:
            op.create_index(f'ix_{table}_{column}', table, [column])


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM whatsapp_events')) or op.get_bind().scalar(sa.text('SELECT count(*) FROM whatsapp_intakes')):
        raise RuntimeError('WhatsApp contém evidência; preserve schema e desative a integração.')
    for table in ('whatsapp_events','whatsapp_intakes','whatsapp_peers'):
        op.drop_table(table)
