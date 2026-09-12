"""M26.9 — fechamento mensal de repasses médicos.

A tabela é aditiva e não recebe backfill: nenhum repasse real nasce, nenhum
pagamento é marcado e nenhuma receita existente é alterada.

Revision ID: d6a9f20c3e41
Revises: c3a9e15f7d84
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d6a9f20c3e41"
down_revision = "c3a9e15f7d84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "physician_transfers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("physician_profile_id", sa.String(length=36), nullable=False),
        sa.Column("competencia", sa.Date(), nullable=False),
        sa.Column("eligible_report_count", sa.Integer(), nullable=False),
        sa.Column("unit_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("reference_total", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "paid_amount", sa.Numeric(12, 2), nullable=False, server_default="0.00"
        ),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="Pendente"
        ),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "payment_registered_by_user_id", sa.String(length=36), nullable=True
        ),
        sa.Column(
            "payment_registered_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_physician_transfers"),
        sa.ForeignKeyConstraint(
            ["physician_profile_id"],
            ["physician_profiles.id"],
            name="fk_physician_transfer_profile",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_physician_transfer_creator"
        ),
        sa.ForeignKeyConstraint(
            ["payment_registered_by_user_id"],
            ["users.id"],
            name="fk_physician_transfer_payment_user",
        ),
        sa.UniqueConstraint(
            "physician_profile_id",
            "competencia",
            name="uq_physician_transfer_profile_competencia",
        ),
        sa.CheckConstraint(
            "eligible_report_count > 0", name="ck_physician_transfers_quantidade_laudos_positiva"
        ),
        sa.CheckConstraint(
            "unit_amount > 0", name="ck_physician_transfers_valor_unitario_positivo"
        ),
        sa.CheckConstraint(
            "reference_total > 0", name="ck_physician_transfers_total_referencia_positivo"
        ),
        sa.CheckConstraint(
            "reference_total = eligible_report_count * unit_amount",
            name="ck_physician_transfers_total_referencia_calculado",
        ),
        sa.CheckConstraint(
            "paid_amount >= 0", name="ck_physician_transfers_valor_pago_nao_negativo"
        ),
        sa.CheckConstraint(
            "(status = 'Pendente' AND paid_amount = 0 AND payment_date IS NULL "
            "AND payment_registered_by_user_id IS NULL AND payment_registered_at IS NULL) "
            "OR (status = 'Pago' AND paid_amount > 0 AND payment_date IS NOT NULL "
            "AND payment_registered_by_user_id IS NOT NULL "
            "AND payment_registered_at IS NOT NULL)",
            name="ck_physician_transfers_estado_pagamento_coerente",
        ),
    )
    op.create_index(
        "ix_physician_transfers_physician_profile_id",
        "physician_transfers",
        ["physician_profile_id"],
    )
    op.create_index(
        "ix_physician_transfers_competencia",
        "physician_transfers",
        ["competencia"],
    )


def downgrade() -> None:
    op.drop_index("ix_physician_transfers_competencia", table_name="physician_transfers")
    op.drop_index("ix_physician_transfers_physician_profile_id", table_name="physician_transfers")
    op.drop_table("physician_transfers")
