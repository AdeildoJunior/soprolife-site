"""safe fiscal queue mock foundation

Revision ID: f6a1d9e28b40
Revises: d6a9f20c3e41
Create Date: 2026-09-11 00:08:31.326608

"""
from alembic import op
import sqlalchemy as sa


revision = 'f6a1d9e28b40'
down_revision = 'd6a9f20c3e41'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('fiscal_policies',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.String(length=60), nullable=False),
    sa.Column('environment', sa.String(length=20), nullable=False),
    sa.Column('flow', sa.String(length=20), nullable=False),
    sa.Column('service', sa.String(length=30), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('effective_to', sa.Date(), nullable=False),
    sa.Column('validation_state', sa.String(length=20), nullable=False),
    sa.Column('configuration', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("environment IN ('mock','restricted','production')", name=op.f('ck_fiscal_policies_fiscal_policy_environment')),
    sa.CheckConstraint("validation_state IN ('draft','validated')", name=op.f('ck_fiscal_policies_fiscal_policy_validation')),
    sa.CheckConstraint('effective_to >= effective_from', name=op.f('ck_fiscal_policies_fiscal_policy_validity')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_fiscal_policies_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fiscal_policies')),
    sa.UniqueConstraint('version', name=op.f('uq_fiscal_policies_version'))
    )
    op.create_table('fiscal_documents',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('spirometry_exam_id', sa.String(length=36), nullable=False),
    sa.Column('environment', sa.String(length=20), nullable=False),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('eligibility', sa.String(length=20), nullable=False),
    sa.Column('blocking_reasons', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=False),
    sa.Column('idempotency_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("environment IN ('mock','restricted','production')", name=op.f('ck_fiscal_documents_fiscal_document_environment')),
    sa.CheckConstraint("state IN ('blocked','pending','issuing','simulated','failed','uncertain','reconciling','cancelled')", name=op.f('ck_fiscal_documents_fiscal_document_state')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_fiscal_documents_created_by_users')),
    sa.ForeignKeyConstraint(['spirometry_exam_id'], ['spirometry_exams.id'], name=op.f('fk_fiscal_documents_spirometry_exam_id_spirometry_exams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fiscal_documents')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_fiscal_documents_idempotency_key')),
    sa.UniqueConstraint('spirometry_exam_id', 'environment', name='uq_fiscal_exam_environment')
    )
    op.create_table('fiscal_preparations',
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
    sa.CheckConstraint('amount_snapshot IS NULL OR (financial_entry_id IS NOT NULL AND policy_id IS NOT NULL AND amount_snapshot > 0)', name=op.f('ck_fiscal_preparations_fiscal_snapshot_source')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_fiscal_preparations_created_by_users')),
    sa.ForeignKeyConstraint(['document_id'], ['fiscal_documents.id'], name=op.f('fk_fiscal_preparations_document_id_fiscal_documents')),
    sa.ForeignKeyConstraint(['financial_entry_id'], ['financial_entries.id'], name=op.f('fk_fiscal_preparations_financial_entry_id_financial_entries')),
    sa.ForeignKeyConstraint(['policy_id'], ['fiscal_policies.id'], name=op.f('fk_fiscal_preparations_policy_id_fiscal_policies')),
    sa.ForeignKeyConstraint(['recipient_person_id'], ['people.id'], name=op.f('fk_fiscal_preparations_recipient_person_id_people')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fiscal_preparations'))
    )
    with op.batch_alter_table('fiscal_preparations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fiscal_preparations_document_id'), ['document_id'], unique=False)

    op.create_table('fiscal_attempts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('document_id', sa.String(length=36), nullable=False),
    sa.Column('preparation_id', sa.String(length=36), nullable=False),
    sa.Column('operation_id', sa.String(length=36), nullable=False),
    sa.Column('reconciles_operation_id', sa.String(length=36), nullable=True),
    sa.Column('operation', sa.String(length=20), nullable=False),
    sa.Column('phase', sa.String(length=20), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=30), nullable=False),
    sa.Column('environment', sa.String(length=20), nullable=False),
    sa.Column('outcome', sa.String(length=30), nullable=False),
    sa.Column('external_id', sa.String(length=100), nullable=True),
    sa.Column('error_code', sa.String(length=40), nullable=True),
    sa.Column('reconciliation_required', sa.Boolean(), nullable=False),
    sa.Column('actor_id', sa.String(length=36), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('idempotency_key', sa.String(length=64), nullable=False),
    sa.Column('idempotency_fingerprint', sa.String(length=64), nullable=False),
    sa.CheckConstraint("operation IN ('issue','reconcile','cancel')", name=op.f('ck_fiscal_attempts_fiscal_attempt_operation')),
    sa.CheckConstraint("phase IN ('started','completed')", name=op.f('ck_fiscal_attempts_fiscal_attempt_phase')),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_fiscal_attempts_actor_id_users')),
    sa.ForeignKeyConstraint(['document_id'], ['fiscal_documents.id'], name=op.f('fk_fiscal_attempts_document_id_fiscal_documents')),
    sa.ForeignKeyConstraint(['preparation_id'], ['fiscal_preparations.id'], name=op.f('fk_fiscal_attempts_preparation_id_fiscal_preparations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fiscal_attempts')),
    sa.UniqueConstraint('document_id', 'number', 'phase', name='uq_fiscal_attempt_number'),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_fiscal_attempts_idempotency_key')),
    sa.UniqueConstraint('operation_id', 'phase', name='uq_fiscal_attempt_phase')
    )
    with op.batch_alter_table('fiscal_attempts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fiscal_attempts_document_id'), ['document_id'], unique=False)


    dialect = op.get_bind().dialect.name
    if dialect == 'postgresql':
        op.execute("""CREATE FUNCTION fiscal_reject_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'immutable fiscal evidence'; END;
            $$ LANGUAGE plpgsql""")
    for table in ('fiscal_policies', 'fiscal_preparations', 'fiscal_attempts'):
        if dialect == 'postgresql':
            op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION fiscal_reject_mutation()")
        elif dialect == 'sqlite':
            for action in ('UPDATE', 'DELETE'):
                op.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END")
    if dialect == 'postgresql':
        op.execute("CREATE TRIGGER fiscal_documents_no_delete BEFORE DELETE ON fiscal_documents FOR EACH ROW EXECUTE FUNCTION fiscal_reject_mutation()")
        op.execute("""CREATE FUNCTION fiscal_protect_identity() RETURNS trigger AS $$
            BEGIN
            IF (NEW.spirometry_exam_id, NEW.environment, NEW.created_by,
                NEW.idempotency_key, NEW.idempotency_fingerprint) IS DISTINCT FROM
               (OLD.spirometry_exam_id, OLD.environment, OLD.created_by,
                OLD.idempotency_key, OLD.idempotency_fingerprint) THEN
                RAISE EXCEPTION 'immutable fiscal identity';
            END IF;
            RETURN NEW;
            END; $$ LANGUAGE plpgsql""")
        op.execute("CREATE TRIGGER fiscal_documents_identity BEFORE UPDATE ON fiscal_documents FOR EACH ROW EXECUTE FUNCTION fiscal_protect_identity()")
    elif dialect == 'sqlite':
        op.execute("CREATE TRIGGER fiscal_documents_no_delete BEFORE DELETE ON fiscal_documents BEGIN SELECT RAISE(ABORT, 'immutable fiscal evidence'); END")
        op.execute("""CREATE TRIGGER fiscal_documents_identity BEFORE UPDATE ON fiscal_documents
            WHEN NEW.spirometry_exam_id IS NOT OLD.spirometry_exam_id
              OR NEW.environment IS NOT OLD.environment OR NEW.created_by IS NOT OLD.created_by
              OR NEW.idempotency_key IS NOT OLD.idempotency_key
              OR NEW.idempotency_fingerprint IS NOT OLD.idempotency_fingerprint
            BEGIN SELECT RAISE(ABORT, 'immutable fiscal identity'); END""")


def downgrade() -> None:
    with op.batch_alter_table('fiscal_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fiscal_attempts_document_id'))

    op.drop_table('fiscal_attempts')
    with op.batch_alter_table('fiscal_preparations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fiscal_preparations_document_id'))

    op.drop_table('fiscal_preparations')
    op.drop_table('fiscal_documents')
    op.drop_table('fiscal_policies')
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP FUNCTION fiscal_protect_identity()')
        op.execute('DROP FUNCTION fiscal_reject_mutation()')
