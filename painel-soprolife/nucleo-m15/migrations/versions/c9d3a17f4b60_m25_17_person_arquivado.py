"""M25.17 — cadastro interno de teste fora da operação normal.

Acrescenta o sinalizador de arquivamento em `people`, na mesma convenção que
`partners.arquivado` (M20): o registro não é apagado, apenas sai das listas
operacionais. É aditiva e reversível — nenhuma linha existente muda de
comportamento, porque o padrão é `false`.

O CHECK amarra estado e evidência: arquivado exige data e motivo, e
não-arquivado exige os dois vazios. Sem isso seria possível gravar "arquivado
sem motivo", que é exatamente o registro que ninguém consegue explicar seis
meses depois.

Revision ID: c9d3a17f4b60
Revises: b8e4d2a71c53
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9d3a17f4b60"
down_revision = "b8e4d2a71c53"
branch_labels = None
depends_on = None

_CHECK_NAME = "arquivamento_com_evidencia"
_CHECK_SQL = (
    "(arquivado = false AND arquivado_em IS NULL "
    "AND arquivado_motivo IS NULL) OR "
    "(arquivado = true AND arquivado_em IS NOT NULL "
    "AND arquivado_motivo IS NOT NULL)"
)


def upgrade() -> None:
    # `batch_alter_table` porque o SQLite não faz ALTER de constraint; é a
    # mesma estratégia das migrations anteriores deste repositório, e no
    # PostgreSQL degrada para os ALTER nativos.
    with op.batch_alter_table("people", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "arquivado",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("arquivado_em", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("arquivado_motivo", sa.String(length=200), nullable=True)
        )
        batch_op.create_check_constraint(op.f(_CHECK_NAME), _CHECK_SQL)


def downgrade() -> None:
    with op.batch_alter_table("people", schema=None) as batch_op:
        batch_op.drop_constraint(op.f(_CHECK_NAME), type_="check")
        batch_op.drop_column("arquivado_motivo")
        batch_op.drop_column("arquivado_em")
        batch_op.drop_column("arquivado")
