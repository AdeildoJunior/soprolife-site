"""m24a — invariantes finais, snapshot clínico e remoção de filename

Migration corretiva aditiva sobre a revisão publicada 5f0aea639d3d:
- congela código, versão, texto e hash do template em cada versão composta;
- materializa a relação inversa única de versão corretiva;
- remove o nome original fornecido no upload, que pode conter PII.

Revision ID: 8d4b1a2c9f70
Revises: 5f0aea639d3d
Create Date: 2026-07-26 22:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "8d4b1a2c9f70"
down_revision = "5f0aea639d3d"
branch_labels = None
depends_on = None

UUID_LEN = 36

_SNAPSHOT_CHECK = (
    "("
    "template_code_snapshot IS NULL AND "
    "template_version_snapshot IS NULL AND "
    "template_text_snapshot IS NULL AND "
    "template_text_sha256 IS NULL"
    ") OR ("
    "template_code_snapshot IS NOT NULL AND "
    "template_version_snapshot IS NOT NULL AND "
    "template_version_snapshot > 0 AND "
    "template_text_snapshot IS NOT NULL AND "
    "template_text_sha256 IS NOT NULL"
    ")"
)


def upgrade() -> None:
    with op.batch_alter_table("report_documents", schema=None) as batch_op:
        # Compatibilidade com as poucas linhas existentes apenas em testes
        # sintéticos: o valor potencialmente identificável é descartado, não
        # copiado para outra tabela ou log.
        batch_op.drop_column("original_filename_display")
        batch_op.add_column(
            sa.Column("corrects_document_id", sa.String(length=UUID_LEN), nullable=True)
        )
        batch_op.create_foreign_key(
            op.f("fk_report_documents_corrects_document_id_report_documents"),
            "report_documents",
            ["corrects_document_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_report_documents_corrects_document_id",
            ["corrects_document_id"],
        )

    with op.batch_alter_table("report_document_versions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("template_code_snapshot", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column("template_version_snapshot", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("template_text_snapshot", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("template_text_sha256", sa.String(length=64), nullable=True)
        )
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_template_snapshot_completo"),
            _SNAPSHOT_CHECK,
        )


def downgrade() -> None:
    with op.batch_alter_table("report_document_versions", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_report_document_versions_template_snapshot_completo"),
            type_="check",
        )
        batch_op.drop_column("template_text_sha256")
        batch_op.drop_column("template_text_snapshot")
        batch_op.drop_column("template_version_snapshot")
        batch_op.drop_column("template_code_snapshot")

    with op.batch_alter_table("report_documents", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_report_documents_corrects_document_id",
            type_="unique",
        )
        batch_op.drop_constraint(
            op.f("fk_report_documents_corrects_document_id_report_documents"),
            type_="foreignkey",
        )
        batch_op.drop_column("corrects_document_id")
        # O downgrade restaura apenas o shape legado; valores removidos por
        # privacidade deliberadamente não são reconstruídos.
        batch_op.add_column(
            sa.Column("original_filename_display", sa.String(length=180), nullable=True)
        )
