"""m20 — consolidação de parceiro duplicado (arquivado + merged_into)

Aditivo e conservador: nenhuma linha é apagada e nenhum código público é
renumerado. `arquivado` tira a duplicata das listas operacionais comuns e
`merged_into_partner_id` faz o código antigo resolver para o parceiro
canônico. Registros existentes ficam arquivado=false / merged_into=NULL.

Revision ID: e5b2d47c9a13
Revises: a3c8e1f6b2d4
Create Date: 2026-07-24 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "e5b2d47c9a13"
down_revision = "a3c8e1f6b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "partners",
        sa.Column("arquivado", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "partners",
        sa.Column("merged_into_partner_id", sa.String(length=36), nullable=True),
    )
    with op.batch_alter_table("partners") as batch_op:
        batch_op.create_foreign_key(
            "fk_partners_merged_into_partner_id_partners",
            "partners",
            ["merged_into_partner_id"],
            ["id"],
        )
        batch_op.create_check_constraint(
            "partner_nao_mescla_em_si",
            "merged_into_partner_id IS NULL OR merged_into_partner_id <> id",
        )


def downgrade() -> None:
    with op.batch_alter_table("partners") as batch_op:
        batch_op.drop_constraint("partner_nao_mescla_em_si", type_="check")
        batch_op.drop_constraint(
            "fk_partners_merged_into_partner_id_partners", type_="foreignkey"
        )
        batch_op.drop_column("merged_into_partner_id")
        batch_op.drop_column("arquivado")
