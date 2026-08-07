"""M25.7 — solicitações de assinatura qualificada (VIDaaS/IntegraICP).

Cria UMA tabela nova e não toca em nenhuma existente: o laudo, as versões, a
assinatura eletrônica interna e os adendos continuam exatamente como estão.
Isso mantém a migration compatível com a revisão já aplicada na VPS
(a3f1d7c25e90) e torna o downgrade trivialmente seguro — derrubar a tabela
não perde nada do fluxo de liberação institucional, que nunca a usa.

Revision ID: b6e2f94a17c3
Revises: a3f1d7c25e90
"""

from alembic import op
import sqlalchemy as sa

revision = "b6e2f94a17c3"
down_revision = "a3f1d7c25e90"
branch_labels = None
depends_on = None

_STATUSES = (
    "rascunho",
    "aguardando_autenticacao",
    "aguardando_autorizacao",
    "assinatura_recebida",
    "validando",
    "assinado_liberado",
    "recusado",
    "expirado",
    "falha_recuperavel",
    "falha_definitiva",
)


def upgrade() -> None:
    op.create_table(
        "qualified_signature_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "report_document_id",
            sa.String(length=36),
            sa.ForeignKey("report_documents.id"),
            nullable=False,
        ),
        sa.Column(
            "report_document_version_id",
            sa.String(length=36),
            sa.ForeignKey("report_document_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "physician_profile_id",
            sa.String(length=36),
            sa.ForeignKey("physician_profiles.id"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        # Uso único do retorno: só hashes, nunca os valores originais.
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("pkce_verifier_encrypted", sa.Text(), nullable=True),
        sa.Column("callback_consumed_at", sa.DateTime(timezone=True), nullable=True),
        # Os DOIS hashes, separados de propósito.
        sa.Column("prepared_sha256", sa.String(length=64), nullable=False),
        sa.Column("signed_digest_sha256", sa.String(length=64), nullable=False),
        sa.Column("final_sha256", sa.String(length=64), nullable=True),
        sa.Column("external_reference", sa.String(length=120), nullable=True),
        sa.Column("credential_id_hash", sa.String(length=64), nullable=True),
        sa.Column("signer_subject", sa.String(length=300), nullable=True),
        sa.Column("signer_issuer", sa.String(length=300), nullable=True),
        sa.Column("signer_serial", sa.String(length=80), nullable=True),
        sa.Column("certificate_not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("certificate_not_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pades_level", sa.String(length=20), nullable=True),
        sa.Column("clearance_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("error_message", sa.String(length=300), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        # NOT NULL para casar com o `TimestampMixin` do modelo: as colunas
        # são `Mapped[datetime]` não-opcional, e o teste de ciclo de
        # migrations compara o schema gerado com o schema declarado.
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN {}".format(_STATUSES),
            name="qualified_signature_status_valido",
        ),
        sa.CheckConstraint("attempts >= 0", name="qualified_signature_attempts"),
        # Nome seguindo a convenção do metadata (uq_<tabela>_<coluna>), que é
        # o que o modelo gera a partir de `unique=True` na coluna.
        sa.UniqueConstraint(
            "state_hash", name="uq_qualified_signature_requests_state_hash"
        ),
    )
    op.create_index(
        "ix_qualified_signature_requests_report_document_id",
        "qualified_signature_requests",
        ["report_document_id"],
    )
    op.create_index(
        "ix_qualified_signature_requests_status",
        "qualified_signature_requests",
        ["status"],
    )
    # Índice único PARCIAL: garante no banco que um laudo não tem duas
    # solicitações vencedoras. Não é regra de aplicação — é do schema.
    op.create_index(
        "uq_qualified_signature_liberado",
        "qualified_signature_requests",
        ["report_document_id"],
        unique=True,
        sqlite_where=sa.text("status = 'assinado_liberado'"),
        postgresql_where=sa.text("status = 'assinado_liberado'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_qualified_signature_liberado",
        table_name="qualified_signature_requests",
    )
    op.drop_index(
        "ix_qualified_signature_requests_status",
        table_name="qualified_signature_requests",
    )
    op.drop_index(
        "ix_qualified_signature_requests_report_document_id",
        table_name="qualified_signature_requests",
    )
    op.drop_table("qualified_signature_requests")
