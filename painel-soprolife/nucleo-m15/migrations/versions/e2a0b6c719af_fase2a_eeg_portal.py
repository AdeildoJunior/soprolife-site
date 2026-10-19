"""Fase 2A: portal do PDF final EEG, sem alterar domínio respiratório."""
from alembic import op
import sqlalchemy as sa

revision = "e2a0b6c719af"
down_revision = "c3f0a7b12e45"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('eeg_result_accesses',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('person_id', sa.String(length=36), nullable=False),
        sa.Column('exam_id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('token_sha256', sa.String(length=64), nullable=False),
        sa.Column('generation', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_access_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_access_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_download_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('download_count', sa.Integer(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_motivo', sa.String(length=120), nullable=True),
        sa.Column('revoked_by_user_id', sa.String(length=36), nullable=True),
        sa.Column('created_by_user_id', sa.String(length=36), nullable=True),
        sa.Column('failed_attempts', sa.Integer(), nullable=False),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('download_count >= 0', name=op.f('ck_eeg_result_accesses_resultado_downloads')),
        sa.CheckConstraint('generation >= 1', name=op.f('ck_eeg_result_accesses_resultado_generation_positiva')),
        sa.CheckConstraint("(status <> 'revogado' AND revoked_at IS NULL) OR (status = 'revogado' AND revoked_at IS NOT NULL AND revoked_motivo IS NOT NULL)", name=op.f('ck_eeg_result_accesses_resultado_revogacao_coerente')),
        sa.CheckConstraint("status IN ('disponivel', 'enviado', 'acessado', 'revogado')", name=op.f('ck_eeg_result_accesses_resultado_status_valido')),
        sa.CheckConstraint('failed_attempts >= 0', name=op.f('ck_eeg_result_accesses_resultado_tentativas')),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], name=op.f('fk_era_created_by')),
        sa.ForeignKeyConstraint(['document_id'], ['eeg_report_documents.id'], name=op.f('fk_era_doc')),
        sa.ForeignKeyConstraint(['exam_id'], ['eeg_exams.id'], name=op.f('fk_era_exam')),
        sa.ForeignKeyConstraint(['person_id'], ['people.id'], name=op.f('fk_era_person')),
        sa.ForeignKeyConstraint(['revoked_by_user_id'], ['users.id'], name=op.f('fk_era_revoked_by')),
        sa.PrimaryKeyConstraint(*['id'], name=op.f('pk_eeg_result_accesses')),
        sa.UniqueConstraint(*['document_id'], name=op.f('uq_eeg_result_accesses_document_id')),
    )
    op.create_index('ix_eeg_result_accesses_token_sha256', 'eeg_result_accesses', ['token_sha256'], unique=True)
    op.create_table('eeg_result_sessions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('access_id', sa.String(length=36), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['access_id'], ['eeg_result_accesses.id'], name=op.f('fk_ers_access')),
        sa.PrimaryKeyConstraint(*['id'], name=op.f('pk_eeg_result_sessions')),
    )
    op.create_index('ix_eeg_result_sessions_access_id', 'eeg_result_sessions', ['access_id'], unique=False)
    op.create_index('ix_eeg_result_sessions_token_hash', 'eeg_result_sessions', ['token_hash'], unique=False)


def downgrade():
    bind = op.get_bind()
    for table in ("eeg_result_sessions", "eeg_result_accesses", "eeg_report_documents",
                  "eeg_report_dispatches", "eeg_technical_files", "eeg_exams", "eeg_enrollments"):
        if bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Downgrade recusado: preservar acessos e auditoria EEG.")
    op.drop_table("eeg_result_sessions")
    op.drop_table("eeg_result_accesses")
