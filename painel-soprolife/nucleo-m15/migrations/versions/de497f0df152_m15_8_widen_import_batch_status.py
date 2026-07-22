"""m15.8 — alarga import_batches.status para comportar os status reais

O executor multiaba grava "executado_aguardando_reconciliacao" (34) e
"executado_com_divergencia" (25), ambos maiores que o VARCHAR(20)
original — em PostgreSQL isso aborta a transação de execução
(StringDataRightTruncation).

Revision ID: de497f0df152
Revises: d91e7a2b4c68
Create Date: 2026-07-22 23:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "de497f0df152"
down_revision = "d91e7a2b4c68"
branch_labels = None
depends_on = None

STATUS_LEN_NOVO = 64
STATUS_LEN_ANTIGO = 20


def upgrade() -> None:
    with op.batch_alter_table("import_batches") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=STATUS_LEN_ANTIGO),
            type_=sa.String(length=STATUS_LEN_NOVO),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Nunca truncar silenciosamente: se houver status persistido maior que
    # o limite antigo, o downgrade destruiria dado — falhar com clareza.
    conn = op.get_bind()
    longos = conn.execute(
        sa.text(
            "SELECT id, status FROM import_batches "
            f"WHERE length(status) > {STATUS_LEN_ANTIGO}"
        )
    ).fetchall()
    if longos:
        detalhe = "; ".join(f"{r.id}={r.status}" for r in longos)
        raise RuntimeError(
            "downgrade abortado: import_batches.status contém valores com "
            f"mais de {STATUS_LEN_ANTIGO} caracteres que seriam truncados: "
            f"{detalhe}"
        )
    with op.batch_alter_table("import_batches") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=STATUS_LEN_NOVO),
            type_=sa.String(length=STATUS_LEN_ANTIGO),
            existing_nullable=False,
        )
