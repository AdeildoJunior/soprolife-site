"""M26.4 — as duas tabelas do portal de resultados do paciente.

Estado de ENTREGA em domínio próprio, e não mais uma coluna em
`external_signed_documents`. A razão é que as duas coisas mudam por motivos
diferentes: revogar um link não desfaz uma assinatura, e um PDF assinado novo
não apaga o fato de o paciente ter aberto o anterior. Um estado só teria de
responder às duas perguntas, e responderia mal às duas.

`patient_result_accesses` guarda apenas `sha256(token)`. O segredo do link
não existe em lugar nenhum do banco: o processo público compara hashes, e o
painel privado reconstrói o mesmo link derivando-o de `id` + `generation`
com uma chave que só ele tem. Regenerar é incrementar `generation` — o hash
muda, o link antigo morre, e a linha (com toda a trilha) permanece.

`patient_result_sessions` é a sessão curta pós-autenticação. Guarda hash do
segredo, prazo e revogação. Não guarda IP, user-agent nem identificador de
aparelho: o portal não precisa deles, e o que não é guardado não vaza.

Aditiva e reversível: nenhuma tabela existente é tocada.

Revision ID: c3a9e15f7d84
Revises: b1f4c72d9e08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3a9e15f7d84"
down_revision = "b1f4c72d9e08"
branch_labels = None
depends_on = None

_ACESSOS = "patient_result_accesses"
_SESSOES = "patient_result_sessions"
_STATUS = ("disponivel", "enviado", "acessado", "revogado")


def upgrade() -> None:
    op.create_table(
        _ACESSOS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.String(length=36), nullable=False),
        sa.Column("spirometry_exam_id", sa.String(length=36), nullable=False),
        sa.Column("report_document_id", sa.String(length=36), nullable=False),
        sa.Column("signed_document_id", sa.String(length=36), nullable=False),
        sa.Column(
            "report_document_version_id", sa.String(length=36), nullable=False
        ),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "generation", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="disponivel",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_access_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_access_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_download_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "download_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_motivo", sa.String(length=120), nullable=True),
        sa.Column("revoked_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "failed_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_patient_result_accesses"),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name="fk_patient_result_accesses_person_id_people",
        ),
        sa.ForeignKeyConstraint(
            ["spirometry_exam_id"],
            ["spirometry_exams.id"],
            name=(
                "fk_patient_result_accesses_spirometry_exam_id_spirometry_exams"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["report_document_id"],
            ["report_documents.id"],
            name=(
                "fk_patient_result_accesses_report_document_id_report_documents"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["signed_document_id"],
            ["external_signed_documents.id"],
            name=(
                "fk_patient_result_accesses_signed_document_id_"
                "external_signed_documents"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["report_document_version_id"],
            ["report_document_versions.id"],
            name=(
                "fk_patient_result_accesses_report_document_version_id_"
                "report_document_versions"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name="fk_patient_result_accesses_revoked_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_patient_result_accesses_created_by_user_id_users",
        ),
        sa.UniqueConstraint(
            "report_document_id",
            name="uq_patient_result_accesses_report_document_id",
        ),
        sa.CheckConstraint(
            f"status IN {_STATUS!r}",
            name="ck_patient_result_accesses_resultado_status_valido",
        ),
        sa.CheckConstraint(
            "generation >= 1",
            name="ck_patient_result_accesses_resultado_generation_positiva",
        ),
        sa.CheckConstraint(
            "failed_attempts >= 0",
            name="ck_patient_result_accesses_resultado_tentativas",
        ),
        sa.CheckConstraint(
            "download_count >= 0",
            name="ck_patient_result_accesses_resultado_downloads",
        ),
        sa.CheckConstraint(
            "(status <> 'revogado' AND revoked_at IS NULL) OR "
            "(status = 'revogado' AND revoked_at IS NOT NULL "
            "AND revoked_motivo IS NOT NULL)",
            name="ck_patient_result_accesses_resultado_revogacao_coerente",
        ),
    )
    op.create_index(
        "ix_patient_result_accesses_person_id", _ACESSOS, ["person_id"]
    )
    # Índice ÚNICO, e não constraint + índice separados: é a forma que o
    # modelo declara (`unique=True, index=True`) e a única que o autogenerate
    # do Alembic considera equivalente. Com as duas coisas, o teste de
    # paridade acusa diferença a cada execução.
    op.create_index(
        "ix_patient_result_accesses_token_sha256",
        _ACESSOS,
        ["token_sha256"],
        unique=True,
    )

    op.create_table(
        _SESSOES,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("access_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_patient_result_sessions"),
        sa.ForeignKeyConstraint(
            ["access_id"],
            ["patient_result_accesses.id"],
            name=(
                "fk_patient_result_sessions_access_id_patient_result_accesses"
            ),
        ),
    )
    op.create_index(
        "ix_patient_result_sessions_access_id", _SESSOES, ["access_id"]
    )
    op.create_index(
        "ix_patient_result_sessions_token_hash", _SESSOES, ["token_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_patient_result_sessions_token_hash", table_name=_SESSOES)
    op.drop_index("ix_patient_result_sessions_access_id", table_name=_SESSOES)
    op.drop_table(_SESSOES)
    op.drop_index("ix_patient_result_accesses_token_sha256", table_name=_ACESSOS)
    op.drop_index("ix_patient_result_accesses_person_id", table_name=_ACESSOS)
    op.drop_table(_ACESSOS)
