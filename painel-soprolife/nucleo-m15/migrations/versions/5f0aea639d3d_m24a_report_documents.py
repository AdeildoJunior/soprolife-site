"""m24a — laudos PDF: templates, documentos, versões e fronteira de assinatura

Cria cinco tabelas novas (report_templates, report_documents,
report_document_versions, report_signatures) para o fluxo seguro de
recebimento/revisão/composição/finalização de laudos em PDF. Nenhuma tabela
ou linha de negócio existente é lida, alterada ou apagada — a migration só
adiciona estrutura nova.

Revision ID: 5f0aea639d3d
Revises: c9d5f7a31b42
Create Date: 2026-07-26 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "5f0aea639d3d"
down_revision = "c9d5f7a31b42"
branch_labels = None
depends_on = None

UUID_LEN = 36

STATUS_LAUDO_VALUES = ("rascunho", "em_revisao", "finalizado")
VERSAO_TIPO_VALUES = ("original", "rascunho", "finalizado")
SIGNATURE_STATUS_VALUES = ("assinatura_pendente", "assinada", "rejeitada")


def upgrade() -> None:
    op.create_table(
        "report_templates",
        sa.Column("id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("codigo", sa.String(length=20), nullable=False),
        sa.Column("titulo", sa.String(length=200), nullable=False),
        sa.Column("texto_tooltip", sa.String(length=240), nullable=True),
        sa.Column("texto_completo", sa.Text(), nullable=False),
        sa.Column("versao", sa.Integer(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("criado_por", sa.String(length=UUID_LEN), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_templates")),
        sa.UniqueConstraint("codigo", name=op.f("uq_report_templates_codigo")),
    )

    op.create_table(
        "report_documents",
        sa.Column("id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("spirometry_exam_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("signature_status", sa.String(length=20), nullable=True),
        sa.Column("original_filename_display", sa.String(length=180), nullable=True),
        sa.Column("current_version_id", sa.String(length=UUID_LEN), nullable=True),
        sa.Column("superseded_by_id", sa.String(length=UUID_LEN), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("reviewer_user_id", sa.String(length=UUID_LEN), nullable=True),
        sa.Column("finalized_by_user_id", sa.String(length=UUID_LEN), nullable=True),
        sa.Column("submitted_for_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN {STATUS_LAUDO_VALUES!r}", name=op.f("ck_report_documents_status_valido")
        ),
        sa.ForeignKeyConstraint(
            ["spirometry_exam_id"], ["spirometry_exams.id"],
            name=op.f("fk_report_documents_spirometry_exam_id_spirometry_exams"),
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"], ["report_documents.id"],
            name=op.f("fk_report_documents_superseded_by_id_report_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name=op.f("fk_report_documents_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"], ["users.id"],
            name=op.f("fk_report_documents_reviewer_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["finalized_by_user_id"], ["users.id"],
            name=op.f("fk_report_documents_finalized_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_documents")),
    )
    with op.batch_alter_table("report_documents", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_report_documents_spirometry_exam_id"), ["spirometry_exam_id"], unique=False
        )

    op.create_table(
        "report_document_versions",
        sa.Column("id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("report_document_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(length=300), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=40), nullable=False),
        sa.Column("template_id", sa.String(length=UUID_LEN), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("placement", sa.String(length=20), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"kind IN {VERSAO_TIPO_VALUES!r}", name=op.f("ck_report_document_versions_kind_valido")
        ),
        sa.CheckConstraint(
            "size_bytes > 0", name=op.f("ck_report_document_versions_size_bytes_positivo")
        ),
        sa.CheckConstraint(
            "page_count > 0", name=op.f("ck_report_document_versions_page_count_positivo")
        ),
        sa.ForeignKeyConstraint(
            ["report_document_id"], ["report_documents.id"],
            name=op.f("fk_report_document_versions_report_document_id_report_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["template_id"], ["report_templates.id"],
            name=op.f("fk_report_document_versions_template_id_report_templates"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name=op.f("fk_report_document_versions_created_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_document_versions")),
        sa.UniqueConstraint(
            "report_document_id", "version_number", name="uq_versao_numero"
        ),
    )
    with op.batch_alter_table("report_document_versions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_report_document_versions_report_document_id"),
            ["report_document_id"],
            unique=False,
        )

    op.create_table(
        "report_signatures",
        sa.Column("id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("report_document_version_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("provider", sa.String(length=60), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("external_reference", sa.String(length=120), nullable=True),
        sa.Column("requested_by_user_id", sa.String(length=UUID_LEN), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_metadata", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN {SIGNATURE_STATUS_VALUES!r}", name=op.f("ck_report_signatures_status_valido")
        ),
        sa.ForeignKeyConstraint(
            ["report_document_version_id"], ["report_document_versions.id"],
            name=op.f(
                "fk_report_signatures_report_document_version_id_report_document_versions"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"],
            name=op.f("fk_report_signatures_requested_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_signatures")),
        sa.UniqueConstraint(
            "report_document_version_id",
            name=op.f("uq_report_signatures_report_document_version_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("report_signatures")
    with op.batch_alter_table("report_document_versions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_report_document_versions_report_document_id"))
    op.drop_table("report_document_versions")
    with op.batch_alter_table("report_documents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_report_documents_spirometry_exam_id"))
    op.drop_table("report_documents")
    op.drop_table("report_templates")
