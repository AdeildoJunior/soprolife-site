"""M31 — separa o local da PRESTAÇÃO do serviço da incidência do ISSQN.

Aditiva e reversível: as duas colunas nascem nulas em todas as linhas
existentes e nenhum exame/preparo é alterado. `municipio_atendimento_ibge`
(spirometry_exams) é a fonte estruturada — nunca texto livre, nunca inferida
de endereço de paciente ou nome de clínica.

`service_municipio_ibge` (fiscal_preparations) é o snapshot copiado dela no
momento do preparo. IMPORTANTE: `fiscal_preparations` já é append-only por
gatilho desde a M15 (`fiscal_preparations_no_update`/`_no_delete` no SQLite,
`fiscal_preparations_immutable` no PostgreSQL — ver f6a1d9e28b40). No SQLite,
`batch_alter_table` com `create_check_constraint` reconstrói fisicamente a
tabela (copy-and-swap), o que DERRUBARIA esses gatilhos — por isso esta
coluna usa `op.add_column()` puro (ADD COLUMN simples é suportado
nativamente pelo SQLite, sem reconstrução) e SEM check constraint próprio: o
valor sempre chega aqui já validado na origem (o check constraint de
`spirometry_exams` abaixo, mais `assert_municipio_ibge`/`service_location.py`
na camada de aplicação). `spirometry_exams` não tem gatilho de imutabilidade,
então pode usar `batch_alter_table` normalmente.

Revision ID: a58f6c31d9e7
Revises: ba3afa480112
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a58f6c31d9e7"
down_revision = "ba3afa480112"
branch_labels = None
depends_on = None

_EXAM_CHECK = "municipio_atendimento_ibge_sete_digitos"


def upgrade() -> None:
    with op.batch_alter_table("spirometry_exams", schema=None) as batch_op:
        batch_op.add_column(sa.Column("municipio_atendimento_ibge", sa.String(length=7), nullable=True))
        batch_op.create_check_constraint(
            op.f(_EXAM_CHECK), "municipio_atendimento_ibge IS NULL OR length(municipio_atendimento_ibge) = 7"
        )
    # Plain ADD COLUMN — deliberately NOT batch_alter_table: see module
    # docstring (would rebuild the table on SQLite and drop its
    # immutability triggers).
    op.add_column("fiscal_preparations", sa.Column("service_municipio_ibge", sa.String(length=7), nullable=True))


def downgrade() -> None:
    op.drop_column("fiscal_preparations", "service_municipio_ibge")
    with op.batch_alter_table("spirometry_exams", schema=None) as batch_op:
        batch_op.drop_constraint(op.f(_EXAM_CHECK), type_="check")
        batch_op.drop_column("municipio_atendimento_ibge")
