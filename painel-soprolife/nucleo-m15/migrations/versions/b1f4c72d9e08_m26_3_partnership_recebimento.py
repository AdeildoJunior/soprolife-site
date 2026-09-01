"""M26.3 — a parceria passa a declarar quanto a SoproLife RECEBE por exame.

Até aqui o sistema não tinha onde guardar isso. `modelo_repasse`,
`percentual_repasse` e `valor_repasse_fixo` já existiam, mas descrevem dinheiro
que SAI da SoproLife para o parceiro — `docs/parceria-pastore-planilha.md`
define `repasse_pastore` como "valor repassado À Pastore" e o soma em
`custo_total`. Gravar recebimento ali inverteria a direção do dinheiro e
contaminaria todo relatório de custo. Por isso: campos próprios.

`recebimento_por_exame_completo` é o que impede a metade de uma regra: quem
declara `valor_por_exame` declara junto QUANTO e A PARTIR DE QUANDO. Sem os
dois o painel exibiria um previsto que ninguém consegue justificar contra o
extrato do parceiro.

Nada é preenchido aqui. A regra vigente da Pastore entra por script de
regularização auditável, não por migração — migração cria estrutura, gestor
decide valor.

Reversível sem perda de operação: as três colunas são aditivas e só existem
para esta regra.

Revision ID: b1f4c72d9e08
Revises: a3f6b0d94c17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1f4c72d9e08"
down_revision = "a3f6b0d94c17"
branch_labels = None
depends_on = None

_TABELA = "partnerships"
_CHECK_VALOR = "valor_recebido_nao_negativo"
_CHECK_COMPLETO = "recebimento_por_exame_completo"


def upgrade() -> None:
    op.add_column(
        _TABELA,
        sa.Column(
            "modelo_recebimento",
            sa.String(length=20),
            nullable=False,
            server_default="indefinido",
        ),
    )
    op.add_column(
        _TABELA, sa.Column("valor_recebido_por_exame", sa.Numeric(12, 2), nullable=True)
    )
    op.add_column(_TABELA, sa.Column("vigencia_inicio", sa.Date(), nullable=True))
    with op.batch_alter_table(_TABELA) as batch:
        batch.create_check_constraint(
            _CHECK_VALOR,
            "valor_recebido_por_exame IS NULL OR valor_recebido_por_exame >= 0",
        )
        batch.create_check_constraint(
            _CHECK_COMPLETO,
            "modelo_recebimento <> 'valor_por_exame' OR ("
            " valor_recebido_por_exame IS NOT NULL AND vigencia_inicio IS NOT NULL)",
        )
    # O server_default cumpriu o backfill das linhas existentes; a coluna não
    # o mantém, para que criar parceria sem modelo explícito seja erro do
    # servidor e não um "indefinido" silencioso gravado pelo banco.
    with op.batch_alter_table(_TABELA) as batch:
        batch.alter_column("modelo_recebimento", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table(_TABELA) as batch:
        batch.drop_constraint(_CHECK_COMPLETO, type_="check")
        batch.drop_constraint(_CHECK_VALOR, type_="check")
        batch.drop_column("vigencia_inicio")
        batch.drop_column("valor_recebido_por_exame")
        batch.drop_column("modelo_recebimento")
