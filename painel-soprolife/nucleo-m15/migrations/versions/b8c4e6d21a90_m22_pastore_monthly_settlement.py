"""m22 — fechamento mensal Pastore e proveniência do rateio histórico

Estende o PartnerSettlement existente, sem criar um segundo financeiro:
unidade + competência, itens individuais por exame e um recibo agregado
opcional em financial_entries. Nenhum valor Pastore é inferido.

A atualização de proveniência é estritamente guardada para os dez códigos,
vínculos e valores já confirmados pelo operador. Divergência aborta o upgrade.

Revision ID: b8c4e6d21a90
Revises: f7a1c3e58d24
Create Date: 2026-07-25 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b8c4e6d21a90"
down_revision = "f7a1c3e58d24"
branch_labels = None
depends_on = None

PROVENANCE = (
    "Rateio gerencial provisório do total histórico — "
    "valor individual não comprovado."
)
EXPECTED = {
    "LAN-000004": ("238.58", "ESP-000001"),
    "LAN-000005": ("238.58", "ESP-000002"),
    "LAN-000006": ("238.58", "ESP-000003"),
    "LAN-000007": ("238.58", "ESP-000004"),
    "LAN-000008": ("238.58", "ESP-000005"),
    "LAN-000009": ("238.58", "ESP-000006"),
    "LAN-000010": ("238.58", "ESP-000007"),
    "LAN-000011": ("238.58", "ESP-000008"),
    "LAN-000012": ("238.58", "ESP-000009"),
    "LAN-000013": ("238.57", "ESP-000010"),
}


def _record_provenance() -> None:
    bind = op.get_bind()
    codes = tuple(EXPECTED)
    placeholders = ", ".join(f":c{i}" for i in range(len(codes)))
    params = {f"c{i}": code for i, code in enumerate(codes)}
    rows = bind.execute(
        sa.text(
            f"""
            SELECT f.public_code, f.valor, f.descricao, f.origem_preco,
                   e.public_code AS exam_public_code,
                   p.nome AS partner_nome
              FROM financial_entries f
              JOIN spirometry_exams e ON e.id = f.spirometry_exam_id
         LEFT JOIN partners p ON p.id = e.partner_id
             WHERE f.public_code IN ({placeholders})
          ORDER BY f.public_code
            """
        ),
        params,
    ).mappings().all()
    if not rows:
        return  # banco novo/de teste, sem alocação histórica
    if len(rows) != len(EXPECTED):
        raise RuntimeError(
            "M22: conjunto parcial dos dez rateios históricos; upgrade abortado."
        )
    for row in rows:
        expected_value, expected_exam = EXPECTED[row["public_code"]]
        if (
            str(row["valor"]) != expected_value
            or row["exam_public_code"] != expected_exam
            or row["descricao"] is not None
            or row["origem_preco"] is not None
            or (row["partner_nome"] or "").strip().casefold() == "pastore"
        ):
            raise RuntimeError(
                "M22: rateio histórico divergiu de valor/vínculo/proveniência; "
                "upgrade abortado."
            )
    bind.execute(
        sa.text(
            f"""
            UPDATE financial_entries
               SET descricao = :provenance
             WHERE public_code IN ({placeholders})
            """
        ),
        {**params, "provenance": PROVENANCE},
    )


def upgrade() -> None:
    op.add_column(
        "partner_settlements",
        sa.Column("partner_unit_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "partner_settlements",
        sa.Column("competencia", sa.Date(), nullable=True),
    )
    op.add_column(
        "partner_settlements",
        sa.Column("data_envio", sa.Date(), nullable=True),
    )
    with op.batch_alter_table("partner_settlements") as batch_op:
        batch_op.create_foreign_key(
            "fk_partner_settlements_partner_unit_id_partner_units",
            "partner_units",
            ["partner_unit_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_partner_settlement_competencia_unidade",
            ["partner_id", "partner_unit_id", "competencia"],
        )
        batch_op.create_index(
            "ix_partner_settlements_partner_unit_id",
            ["partner_unit_id"],
        )
        batch_op.create_index(
            "ix_partner_settlements_competencia",
            ["competencia"],
        )

    op.create_table(
        "partner_settlement_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("settlement_id", sa.String(length=36), nullable=False),
        sa.Column("spirometry_exam_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["partner_settlements.id"],
            name="fk_partner_settlement_items_settlement_id_partner_settlements",
        ),
        sa.ForeignKeyConstraint(
            ["spirometry_exam_id"],
            ["spirometry_exams.id"],
            name="fk_partner_settlement_items_spirometry_exam_id_spirometry_exams",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_partner_settlement_items"),
        sa.UniqueConstraint(
            "spirometry_exam_id",
            name="uq_partner_settlement_items_spirometry_exam_id",
        ),
    )
    op.create_index(
        "ix_partner_settlement_items_settlement_id",
        "partner_settlement_items",
        ["settlement_id"],
    )

    op.add_column(
        "financial_entries",
        sa.Column("partner_settlement_id", sa.String(length=36), nullable=True),
    )
    with op.batch_alter_table("financial_entries") as batch_op:
        batch_op.create_foreign_key(
            "fk_financial_entries_partner_settlement_id_partner_settlements",
            "partner_settlements",
            ["partner_settlement_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_financial_entries_partner_settlement_id",
            ["partner_settlement_id"],
        )

    _record_provenance()


def downgrade() -> None:
    bind = op.get_bind()
    codes = tuple(EXPECTED)
    placeholders = ", ".join(f":c{i}" for i in range(len(codes)))
    params = {f"c{i}": code for i, code in enumerate(codes)}
    bind.execute(
        sa.text(
            f"""
            UPDATE financial_entries
               SET descricao = NULL
             WHERE public_code IN ({placeholders})
               AND descricao = :provenance
            """
        ),
        {**params, "provenance": PROVENANCE},
    )

    with op.batch_alter_table("financial_entries") as batch_op:
        batch_op.drop_constraint(
            "uq_financial_entries_partner_settlement_id",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_financial_entries_partner_settlement_id_partner_settlements",
            type_="foreignkey",
        )
        batch_op.drop_column("partner_settlement_id")

    op.drop_index(
        "ix_partner_settlement_items_settlement_id",
        table_name="partner_settlement_items",
    )
    op.drop_table("partner_settlement_items")

    with op.batch_alter_table("partner_settlements") as batch_op:
        batch_op.drop_index("ix_partner_settlements_competencia")
        batch_op.drop_index("ix_partner_settlements_partner_unit_id")
        batch_op.drop_constraint(
            "uq_partner_settlement_competencia_unidade",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_partner_settlements_partner_unit_id_partner_units",
            type_="foreignkey",
        )
        batch_op.drop_column("data_envio")
        batch_op.drop_column("competencia")
        batch_op.drop_column("partner_unit_id")
