"""M26.33: terceiro autorizado a receber o resultado, sem tocar tutela.

Puramente aditiva: uma tabela nova. Nenhuma coluna, constraint ou linha de
`person_relationships`, `people` ou `patient_result_accesses` é alterada, e
nenhum backfill é feito — nenhum vínculo histórico passa a existir.
"""
from alembic import op
import sqlalchemy as sa

revision = "a1c7e5d9b204"
down_revision = "e2a0b6c719af"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "result_delivery_authorizations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("patient_person_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_person_id", sa.String(length=36), nullable=False),
        sa.Column("relationship_type", sa.String(length=30), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("registered_by_user_id", sa.String(length=36), nullable=False),
        sa.CheckConstraint(
            "patient_person_id <> recipient_person_id",
            name=op.f("ck_result_delivery_authorizations_destinatario_distinto_do_paciente"),
        ),
        sa.CheckConstraint(
            "relationship_type IN ('daughter','son','spouse','parent','sibling','other')",
            name=op.f("ck_result_delivery_authorizations_destinatario_relacao_valida"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_person_id"], ["people.id"], name=op.f("fk_rda_patient")
        ),
        sa.ForeignKeyConstraint(
            ["recipient_person_id"], ["people.id"], name=op.f("fk_rda_recipient")
        ),
        sa.ForeignKeyConstraint(
            ["registered_by_user_id"], ["users.id"], name=op.f("fk_rda_registered_by")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_result_delivery_authorizations")),
        sa.UniqueConstraint(
            "patient_person_id",
            "recipient_person_id",
            name=op.f("uq_result_delivery_authorization_par"),
        ),
    )
    op.create_index(
        op.f("ix_result_delivery_authorizations_patient_person_id"),
        "result_delivery_authorizations",
        ["patient_person_id"],
    )
    op.create_index(
        op.f("ix_result_delivery_authorizations_recipient_person_id"),
        "result_delivery_authorizations",
        ["recipient_person_id"],
    )


def downgrade():
    # Mesma regra das migrations de evidência (Fase 2A): uma autorização já
    # registrada é decisão humana sobre a quem um resultado pode ser
    # entregue. Desfazer o esquema com linhas dentro apagaria essa decisão
    # em silêncio, então o downgrade só passa com a tabela vazia.
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT 1 FROM result_delivery_authorizations LIMIT 1")
    ).first():
        raise RuntimeError(
            "Downgrade recusado: preservar autorizações de entrega de resultado."
        )
    op.drop_index(
        op.f("ix_result_delivery_authorizations_recipient_person_id"),
        table_name="result_delivery_authorizations",
    )
    op.drop_index(
        op.f("ix_result_delivery_authorizations_patient_person_id"),
        table_name="result_delivery_authorizations",
    )
    op.drop_table("result_delivery_authorizations")
