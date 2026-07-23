"""m16 — coluna broncodilatador em spirometry_exams (Central de Cadastros)

Campo de negócio real do exame (com/sem broncodilatador), já presente nos
registros históricos da parceria Pastore. Aditivo e anulável: registros
existentes ficam como "não informado" (NULL) — nada é inferido.

Revision ID: a3c8e1f6b2d4
Revises: de497f0df152
Create Date: 2026-07-23 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "a3c8e1f6b2d4"
down_revision = "de497f0df152"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "spirometry_exams",
        sa.Column("broncodilatador", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("spirometry_exams") as batch_op:
        batch_op.drop_column("broncodilatador")
