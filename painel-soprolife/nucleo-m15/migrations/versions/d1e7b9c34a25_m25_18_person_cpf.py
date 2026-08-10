"""M25.18 — CPF opcional do paciente (CFM 2.381/2024).

Aditiva e reversível: a coluna nasce nula em todas as linhas existentes e
nenhum paciente é alterado. O índice único convive com
vários pacientes sem CPF: em SQL padrão NULL não é igual a NULL.

Revision ID: d1e7b9c34a25
Revises: c9d3a17f4b60
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1e7b9c34a25"
down_revision = "c9d3a17f4b60"
branch_labels = None
depends_on = None

_CHECK = "cpf_com_onze_digitos"
# Mesmo nome que `index=True` no modelo produz — a checagem de
# autogenerate compara os dois e acusa qualquer divergência.
_INDEX = "ix_people_cpf"


def upgrade() -> None:
    with op.batch_alter_table("people", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cpf", sa.String(length=11), nullable=True))
        batch_op.create_check_constraint(
            op.f(_CHECK), "cpf IS NULL OR length(cpf) = 11"
        )
    # Índice único COMUM, e não parcial: em SQL padrão NULL nunca é igual a
    # NULL, então qualquer quantidade de pacientes sem CPF convive no mesmo
    # índice — SQLite e PostgreSQL concordam nisso. Usar um índice parcial
    # aqui criaria divergência entre o que as migrations produzem e o que
    # `Base.metadata.create_all` produz nos testes, que é justamente o tipo
    # de diferença que só aparece em produção.
    op.create_index(_INDEX, "people", ["cpf"], unique=True)


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="people")
    with op.batch_alter_table("people", schema=None) as batch_op:
        batch_op.drop_constraint(op.f(_CHECK), type_="check")
        batch_op.drop_column("cpf")
