"""M26 — sequência do fechamento dentro da mesma competência Pastore.

A chave (parceiro, unidade, competência) assumia que um mês fecha uma vez só.
A operação desmentiu: o fechamento de 2026-08 foi criado em 11/08 com os 3
exames existentes, e os outros 14 do mesmo mês — realizados entre 15 e 29/08 —
ficaram sem destino, porque a competência já estava ocupada e não existe rota
para anexar exame a fechamento existente.

A correção adiciona `sequencia`. Fechamentos já gravados viram sequência 1 —
backfill idempotente, sem tocar em valor, status ou item nenhum. Fechamentos
complementares nascem com a sequência seguinte e valor próprio, para que um
número conferido contra extrato nunca passe a cobrir exames que ele não cobria.

Reversível: `downgrade` recusa reverter se existir qualquer competência com
mais de um fechamento — a chave antiga rejeitaria linhas que já existem, e
apagar um fechamento para caber na chave antiga seria destruir operação real.

Revision ID: a3f6b0d94c17
Revises: c4a97b1e6d20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3f6b0d94c17"
down_revision = "c4a97b1e6d20"
branch_labels = None
depends_on = None

_TABELA = "partner_settlements"
_UNIQUE = "uq_partner_settlement_competencia_unidade"
_CHECK = "sequencia_minima"
_COLUNAS_ANTES = ["partner_id", "partner_unit_id", "competencia"]
_COLUNAS_DEPOIS = [*_COLUNAS_ANTES, "sequencia"]


def upgrade() -> None:
    op.add_column(
        _TABELA,
        sa.Column("sequencia", sa.Integer(), nullable=False, server_default="1"),
    )
    with op.batch_alter_table(_TABELA) as batch:
        batch.drop_constraint(_UNIQUE, type_="unique")
        batch.create_unique_constraint(_UNIQUE, _COLUNAS_DEPOIS)
        batch.create_check_constraint(_CHECK, "sequencia >= 1")
    # O server_default cumpriu o backfill das linhas existentes; a coluna não
    # o mantém, para que criar fechamento sem sequência explícita seja erro
    # do servidor e não um 1 silencioso gravado pelo banco.
    with op.batch_alter_table(_TABELA) as batch:
        batch.alter_column("sequencia", server_default=None)


def downgrade() -> None:
    conexao = op.get_bind()
    conflitantes = conexao.exec_driver_sql(
        "SELECT COUNT(*) FROM ("
        f"  SELECT 1 FROM {_TABELA}"
        "   GROUP BY partner_id, partner_unit_id, competencia"
        "  HAVING COUNT(*) > 1"
        ") AS duplicadas"
    ).scalar()
    if conflitantes:
        raise RuntimeError(
            f"{conflitantes} competência(s) com mais de um fechamento. A chave "
            "anterior recusaria linhas que já existem, e apagar um fechamento "
            "para caber nela destruiria operação real — decida caso a caso "
            "antes de reverter."
        )
    with op.batch_alter_table(_TABELA) as batch:
        batch.drop_constraint(_CHECK, type_="check")
        batch.drop_constraint(_UNIQUE, type_="unique")
        batch.create_unique_constraint(_UNIQUE, _COLUNAS_ANTES)
        batch.drop_column("sequencia")
