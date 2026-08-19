"""M25.29G — estado `recusado` para documento assinado inválido.

Um arquivo recebido pode não servir: uma prévia assinada, um PDF idêntico ao
final (devolvido sem assinar), um documento que não corresponde à versão
final. Antes não havia como dizer isso — os quatro estados existentes
descrevem só o caminho feliz, e a `CHECK` os impunha.

O estado é UM, genérico. O motivo detalhado vive na auditoria: criar um
status por modo de falha multiplicaria estados sem multiplicar informação.

Reversível: `downgrade` recoloca a `CHECK` anterior. Ele falha de propósito
se ainda houver linha em `recusado` — reverter apagando a classificação de um
documento inválido seria pior do que não reverter.

Revision ID: b8d3e2f7a145
Revises: a2f6c81d4b73
"""

from __future__ import annotations

from alembic import op

revision = "b8d3e2f7a145"
down_revision = "a2f6c81d4b73"
branch_labels = None
depends_on = None

_TABELA = "external_signed_documents"
_CONSTRAINT = "assinado_status_valido"

_ANTES = (
    "em_conferencia",
    "recebido_validacao_pendente",
    "validado_externamente",
    "entregue",
)
_DEPOIS = _ANTES + ("recusado",)


def _sql_in(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{valor}'" for valor in valores)


def upgrade() -> None:
    with op.batch_alter_table(_TABELA) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(
            _CONSTRAINT, f"status IN ({_sql_in(_DEPOIS)})"
        )


def downgrade() -> None:
    conexao = op.get_bind()
    restantes = conexao.exec_driver_sql(
        f"SELECT COUNT(*) FROM {_TABELA} WHERE status = 'recusado'"
    ).scalar()
    if restantes:
        raise RuntimeError(
            f"{restantes} documento(s) em 'recusado'. Reverter apagaria a "
            "classificação de documentos inválidos — decida caso a caso "
            "antes de fazer o downgrade."
        )
    with op.batch_alter_table(_TABELA) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(
            _CONSTRAINT, f"status IN ({_sql_in(_ANTES)})"
        )
