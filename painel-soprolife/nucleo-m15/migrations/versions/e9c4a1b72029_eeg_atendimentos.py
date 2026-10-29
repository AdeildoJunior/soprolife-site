"""EEGLife: atendimento e vínculo técnico no razão compartilhado, sem backfill.

Revision ID: e9c4a1b72029
Revises: d7b2c4e86a91
"""
from alembic import op
import sqlalchemy as sa

revision = "e9c4a1b72029"
down_revision = "d7b2c4e86a91"
branch_labels = depends_on = None

SCOPE = "eeg_exam_id IS NULL OR (spirometry_exam_id IS NULL AND consultation_id IS NULL AND partner_referral_id IS NULL AND partner_settlement_id IS NULL AND tipo = 'receita' AND categoria IS NOT NULL AND categoria = 'EEG')"


def upgrade():
    with op.batch_alter_table("eeg_enrollments") as batch:
        batch.add_column(sa.Column("consentimento_whatsapp", sa.String(20), nullable=False, server_default="desconhecido"))
        batch.create_check_constraint("eeg_consentimento_valido", "consentimento_whatsapp IN ('concedido','revogado','desconhecido')")
    with op.batch_alter_table("eeg_exams") as batch:
        for name, size in (("local_atendimento", 200), ("municipio_atendimento_ibge", 7), ("origem_canal", 30), ("idempotency_key", 64), ("idempotency_fingerprint", 64)):
            batch.add_column(sa.Column(name, sa.String(size), nullable=True))
        batch.create_unique_constraint("uq_eeg_exams_idempotency_key", ["idempotency_key"])
    with op.batch_alter_table("financial_entries") as batch:
        batch.add_column(sa.Column("eeg_exam_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_financial_entries_eeg_exam_id_eeg_exams", "eeg_exams", ["eeg_exam_id"], ["id"])
        batch.create_check_constraint("financial_eeg_scope", SCOPE)
        batch.create_index("ix_financial_entries_eeg_exam_id", ["eeg_exam_id"], unique=True)


def downgrade():
    conn = op.get_bind()
    if (conn.execute(sa.text("SELECT count(*) FROM eeg_enrollments WHERE consentimento_whatsapp != 'desconhecido'")).scalar_one()
            or conn.execute(sa.text("SELECT count(*) FROM financial_entries WHERE eeg_exam_id IS NOT NULL")).scalar_one()
            or conn.execute(sa.text("SELECT count(*) FROM eeg_exams WHERE local_atendimento IS NOT NULL OR municipio_atendimento_ibge IS NOT NULL OR origem_canal IS NOT NULL OR idempotency_key IS NOT NULL")).scalar_one()):
        raise RuntimeError("Receitas EEG existentes: downgrade apagaria vínculos; abortado.")
    with op.batch_alter_table("financial_entries") as batch:
        batch.drop_index("ix_financial_entries_eeg_exam_id")
        batch.drop_constraint("financial_eeg_scope", type_="check")
        batch.drop_constraint("fk_financial_entries_eeg_exam_id_eeg_exams", type_="foreignkey")
        batch.drop_column("eeg_exam_id")
    with op.batch_alter_table("eeg_exams") as batch:
        batch.drop_constraint("uq_eeg_exams_idempotency_key", type_="unique")
        for name in ("local_atendimento", "municipio_atendimento_ibge", "origem_canal", "idempotency_key", "idempotency_fingerprint"):
            batch.drop_column(name)

    with op.batch_alter_table("eeg_enrollments") as batch:
        batch.drop_constraint("eeg_consentimento_valido", type_="check")
        batch.drop_column("consentimento_whatsapp")
