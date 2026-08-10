"""M25.20 — central de assinatura externa em lote.

Aditiva e reversível. Cria duas tabelas novas, alarga uma coluna e
acrescenta um tipo de versão. Nenhuma linha existente é reescrita, nenhum
status de documento muda e nenhum PDF já gravado é tocado.

O que entra:

* `external_signature_batches` — um download ou uma devolução em lote
  (BAT-000001), para auditoria de "o que saiu junto, para quem, quando";
* `external_signed_documents` — o PDF assinado que voltou, pareado com
  segurança, com hash, origem, método de pareamento e ciclo de validação;
* `report_document_versions.kind` alargado de 20 para 40 caracteres e o
  CHECK recriado com `laudo_assinado_externo_recebido`.

O PDF assinado que volta é uma versão NOVA. A MIR original e o laudo
concluído para assinatura continuam intactos: esta migration não tem
UPDATE nem DELETE em nenhuma tabela de laudo.

Revision ID: e7c4b03a91df
Revises: d1e7b9c34a25
"""

from alembic import op
import sqlalchemy as sa

revision = "e7c4b03a91df"
down_revision = "d1e7b9c34a25"
branch_labels = None
depends_on = None

KIND_LEN_NOVO = 40
KIND_LEN_ANTIGO = 20

KIND_NOVO = "laudo_assinado_externo_recebido"
KIND_VALUES_ANTIGO = (
    "original",
    "rascunho",
    "assinatura_pendente",
    "assinado",
    "finalizado",
    "laudo_previa",
    "laudo_liberado",
    "laudo_adendo",
)
KIND_VALUES_NOVO = KIND_VALUES_ANTIGO + (KIND_NOVO,)

BATCH_DIRECAO_VALUES = ("download", "upload")
PAREAMENTO_VALUES = (
    "metadado_soprolife",
    "codigo_laudo_no_conteudo",
    "codigo_validacao_no_conteudo",
)
ASSINADO_STATUS_VALUES = (
    "em_conferencia",
    "recebido_validacao_pendente",
    "validado_externamente",
    "entregue",
)


def upgrade() -> None:
    # 1. `laudo_assinado_externo_recebido` tem 31 caracteres e não cabia em
    #    VARCHAR(20). Alargar não reescreve dado: os valores existentes
    #    continuam idênticos, e quem decide o que é aceito é o CHECK.
    with op.batch_alter_table("report_document_versions") as batch_op:
        batch_op.alter_column(
            "kind",
            existing_type=sa.String(length=KIND_LEN_ANTIGO),
            type_=sa.String(length=KIND_LEN_NOVO),
            existing_nullable=False,
        )
        batch_op.drop_constraint(
            op.f("ck_report_document_versions_kind_valido"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_kind_valido"),
            f"kind IN {KIND_VALUES_NOVO!r}",
        )

    # 2. Lote: um download ou uma devolução.
    op.create_table(
        "external_signature_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("public_code", sa.String(length=12), nullable=False),
        sa.Column("direction", sa.String(length=12), nullable=False),
        sa.Column("physician_profile_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "document_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_signature_batches")),
        sa.UniqueConstraint(
            "public_code", name=op.f("uq_external_signature_batches_public_code")
        ),
        sa.ForeignKeyConstraint(
            ["physician_profile_id"],
            ["physician_profiles.id"],
            name=op.f("fk_external_signature_batches_physician_profile_id"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_external_signature_batches_created_by_user_id"),
        ),
        sa.CheckConstraint(
            f"direction IN {BATCH_DIRECAO_VALUES!r}",
            name=op.f("ck_external_signature_batches_direction_valida"),
        ),
        sa.CheckConstraint(
            "document_count >= 0",
            name=op.f("ck_external_signature_batches_document_count_nao_negativo"),
        ),
    )
    op.create_index(
        op.f("ix_external_signature_batches_physician_profile_id"),
        "external_signature_batches",
        ["physician_profile_id"],
    )

    # 3. O PDF assinado que voltou.
    op.create_table(
        "external_signed_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_document_id", sa.String(length=36), nullable=False),
        sa.Column(
            "report_document_version_id", sa.String(length=36), nullable=False
        ),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("physician_profile_id", sa.String(length=36), nullable=False),
        sa.Column("uploader_user_id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("received_filename", sa.String(length=260), nullable=True),
        sa.Column("match_method", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validated_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_method", sa.String(length=40), nullable=True),
        sa.Column("validation_reference", sa.String(length=200), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_signed_documents")),
        sa.UniqueConstraint(
            "report_document_version_id",
            name=op.f("uq_external_signed_documents_report_document_version_id"),
        ),
        # Idempotência: o mesmo arquivo reenviado para o mesmo laudo não
        # cria uma segunda versão.
        sa.UniqueConstraint(
            "report_document_id",
            "sha256",
            name=op.f("uq_assinado_documento_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["report_document_id"],
            ["report_documents.id"],
            name=op.f("fk_external_signed_documents_report_document_id"),
        ),
        sa.ForeignKeyConstraint(
            ["report_document_version_id"],
            ["report_document_versions.id"],
            name=op.f("fk_external_signed_documents_report_document_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["report_document_versions.id"],
            name=op.f("fk_external_signed_documents_source_version_id"),
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["external_signature_batches.id"],
            name=op.f("fk_external_signed_documents_batch_id"),
        ),
        sa.ForeignKeyConstraint(
            ["physician_profile_id"],
            ["physician_profiles.id"],
            name=op.f("fk_external_signed_documents_physician_profile_id"),
        ),
        sa.ForeignKeyConstraint(
            ["uploader_user_id"],
            ["users.id"],
            name=op.f("fk_external_signed_documents_uploader_user_id"),
        ),
        sa.ForeignKeyConstraint(
            ["validated_by_user_id"],
            ["users.id"],
            name=op.f("fk_external_signed_documents_validated_by_user_id"),
        ),
        sa.ForeignKeyConstraint(
            ["delivered_by_user_id"],
            ["users.id"],
            name=op.f("fk_external_signed_documents_delivered_by_user_id"),
        ),
        sa.CheckConstraint(
            f"match_method IN {PAREAMENTO_VALUES!r}",
            name=op.f("ck_external_signed_documents_match_method_valido"),
        ),
        sa.CheckConstraint(
            f"status IN {ASSINADO_STATUS_VALUES!r}",
            name=op.f("ck_external_signed_documents_assinado_status_valido"),
        ),
        sa.CheckConstraint(
            "size_bytes > 0",
            name=op.f("ck_external_signed_documents_assinado_size_bytes_positivo"),
        ),
        sa.CheckConstraint(
            "("
            "validated_by_user_id IS NULL AND validated_at IS NULL AND "
            "validation_method IS NULL"
            ") OR ("
            "validated_by_user_id IS NOT NULL AND validated_at IS NOT NULL AND "
            "validation_method IS NOT NULL"
            ")",
            name=op.f("ck_external_signed_documents_validacao_externa_coerente"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('validado_externamente', 'entregue') OR "
            "validated_by_user_id IS NOT NULL",
            name=op.f(
                "ck_external_signed_documents_status_validado_exige_validador"
            ),
        ),
    )
    for coluna in (
        "report_document_id",
        "batch_id",
        "physician_profile_id",
    ):
        op.create_index(
            op.f(f"ix_external_signed_documents_{coluna}"),
            "external_signed_documents",
            [coluna],
        )

    # 4. Sequência do código público do lote. `allocate_public_code` cria a
    #    linha sozinho se faltar, mas preseedar mantém o comportamento igual
    #    ao das demais entidades e evita a corrida do primeiro uso.
    op.execute(
        sa.text(
            "INSERT INTO code_sequences (prefix, next_value) "
            "VALUES ('BAT', 1) ON CONFLICT (prefix) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM code_sequences WHERE prefix = 'BAT'"))
    for coluna in (
        "report_document_id",
        "batch_id",
        "physician_profile_id",
    ):
        op.drop_index(
            op.f(f"ix_external_signed_documents_{coluna}"),
            table_name="external_signed_documents",
        )
    op.drop_table("external_signed_documents")
    op.drop_index(
        op.f("ix_external_signature_batches_physician_profile_id"),
        table_name="external_signature_batches",
    )
    op.drop_table("external_signature_batches")

    # Nunca truncar silenciosamente. Se existir versão gravada com o tipo
    # novo, estreitar a coluna destruiria a evidência de que um laudo
    # assinado foi recebido — falhar com clareza é a única saída honesta.
    conn = op.get_bind()
    existentes = conn.execute(
        sa.text(
            "SELECT count(*) FROM report_document_versions "
            "WHERE length(kind) > :limite"
        ),
        {"limite": KIND_LEN_ANTIGO},
    ).scalar_one()
    if existentes:
        raise RuntimeError(
            "downgrade abortado: existem "
            f"{existentes} versão(ões) de laudo com tipo maior que "
            f"{KIND_LEN_ANTIGO} caracteres (assinatura externa recebida). "
            "Estreitar a coluna truncaria evidência clínica."
        )
    with op.batch_alter_table("report_document_versions") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_report_document_versions_kind_valido"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_kind_valido"),
            f"kind IN {KIND_VALUES_ANTIGO!r}",
        )
        batch_op.alter_column(
            "kind",
            existing_type=sa.String(length=KIND_LEN_NOVO),
            type_=sa.String(length=KIND_LEN_ANTIGO),
            existing_nullable=False,
        )
