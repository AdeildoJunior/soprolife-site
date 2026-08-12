"""M25.24 — encerramento operacional por exame.

Acrescenta a `spirometry_exams` os quatro campos que registram uma decisão
explícita: este exame não gera mais trabalho operacional, por este motivo,
decidido por esta pessoa, nesta data.

ADITIVA e sem efeito sobre uma única linha existente: o padrão é NULL nos
quatro campos, e NULL significa exatamente o comportamento de hoje — o exame
continua na fila. Nenhum dado clínico, status de exame, laudo, versão ou hash
é tocado aqui.

O CHECK amarra estado e evidência, na mesma convenção da
`arquivamento_com_evidencia` de `people` (M25.17): ou os quatro campos estão
vazios, ou os quatro estão preenchidos. Não existe "encerrado sem motivo" nem
"encerrado por ninguém".

Revision ID: a2f6c81d4b73
Revises: e7c4b03a91df
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a2f6c81d4b73"
down_revision = "e7c4b03a91df"
branch_labels = None
depends_on = None

_CHECK_NAME = "encerramento_com_evidencia"
_CHECK_SQL = (
    "(encerramento_motivo IS NULL AND encerrado_em IS NULL "
    "AND encerrado_por_user_id IS NULL "
    "AND encerramento_observacao IS NULL) OR "
    "(encerramento_motivo IS NOT NULL AND encerrado_em IS NOT NULL "
    "AND encerrado_por_user_id IS NOT NULL "
    "AND encerramento_observacao IS NOT NULL)"
)
_FK_NAME = "fk_spirometry_exams_encerrado_por_user_id_users"


def upgrade() -> None:
    # `batch_alter_table` porque o SQLite não faz ALTER de constraint; no
    # PostgreSQL degrada para os ALTER nativos. Mesma estratégia das
    # migrations anteriores deste repositório.
    with op.batch_alter_table("spirometry_exams", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("encerramento_motivo", sa.String(length=40), nullable=True)
        )
        batch_op.add_column(
            sa.Column("encerrado_em", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("encerrado_por_user_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "encerramento_observacao", sa.String(length=200), nullable=True
            )
        )
        batch_op.create_foreign_key(
            op.f(_FK_NAME), "users", ["encerrado_por_user_id"], ["id"]
        )
        batch_op.create_check_constraint(op.f(_CHECK_NAME), _CHECK_SQL)


def downgrade() -> None:
    with op.batch_alter_table("spirometry_exams", schema=None) as batch_op:
        batch_op.drop_constraint(op.f(_CHECK_NAME), type_="check")
        batch_op.drop_constraint(op.f(_FK_NAME), type_="foreignkey")
        batch_op.drop_column("encerramento_observacao")
        batch_op.drop_column("encerrado_por_user_id")
        batch_op.drop_column("encerrado_em")
        batch_op.drop_column("encerramento_motivo")
