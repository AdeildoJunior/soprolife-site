"""m27 fiscal artifacts

Revision ID: 9c310c422ce2
Revises: f6a1d9e28b40
Create Date: 2026-09-13 04:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '9c310c422ce2'
down_revision = 'f6a1d9e28b40'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('fiscal_artifacts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('document_id', sa.String(length=36), nullable=False),
    sa.Column('attempt_id', sa.String(length=36), nullable=True),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('storage_relative_path', sa.String(length=300), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("kind IN ('dps_unsigned_xml','dps_signed_xml','nfse_xml','event_xml','danfse_pdf')", name=op.f('ck_fiscal_artifacts_fiscal_artifact_kind')),
    sa.CheckConstraint('size_bytes >= 0', name=op.f('ck_fiscal_artifacts_fiscal_artifact_size_non_negative')),
    sa.ForeignKeyConstraint(['attempt_id'], ['fiscal_attempts.id'], name=op.f('fk_fiscal_artifacts_attempt_id_fiscal_attempts')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_fiscal_artifacts_created_by_users')),
    sa.ForeignKeyConstraint(['document_id'], ['fiscal_documents.id'], name=op.f('fk_fiscal_artifacts_document_id_fiscal_documents')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fiscal_artifacts'))
    )
    with op.batch_alter_table('fiscal_artifacts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fiscal_artifacts_document_id'), ['document_id'], unique=False)

    dialect = op.get_bind().dialect.name
    if dialect == 'postgresql':
        op.execute("CREATE TRIGGER fiscal_artifacts_immutable BEFORE UPDATE OR DELETE ON fiscal_artifacts FOR EACH ROW EXECUTE FUNCTION fiscal_reject_mutation()")
    elif dialect == 'sqlite':
        for action in ('UPDATE', 'DELETE'):
            op.execute(f"CREATE TRIGGER fiscal_artifacts_no_{action.lower()} BEFORE {action} ON fiscal_artifacts BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END")


def downgrade() -> None:
    with op.batch_alter_table('fiscal_artifacts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fiscal_artifacts_document_id'))
    op.drop_table('fiscal_artifacts')
