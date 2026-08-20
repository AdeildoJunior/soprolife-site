"""M25.29H — estado `recebido_assinado` para o aceite automático.

O fluxo novo não passa por conferência administrativa: o PDF assinado que
volta é associado ao laudo final por evidência objetiva, submetido às guardas
documentais e, se passar, fica pronto para entrega na hora.

O estado registra exatamente isso — recebido e assinado — e nada além.
Continua sem afirmar validação criptográfica da cadeia ICP-Brasil, que a
SoproLife não realiza.

`recebido_validacao_pendente` permanece na `CHECK` de propósito: documentos
históricos estão nele e a auditoria precisa lê-los. O que muda é que nenhum
documento novo nasce ali.

Reversível: `downgrade` recoloca a `CHECK` anterior e falha se ainda houver
linha em `recebido_assinado` — reverter apagando o aceite de um documento
válido deixaria a fila mentindo sobre o estado dele.

Revision ID: c4a97b1e6d20
Revises: b8d3e2f7a145
"""

from __future__ import annotations

from alembic import op

revision = "c4a97b1e6d20"
down_revision = "b8d3e2f7a145"
branch_labels = None
depends_on = None

_TABELA = "external_signed_documents"
_CONSTRAINT = "assinado_status_valido"
# A segunda constraint tocada aqui. `entregue` exigia um validador humano
# porque a entrega só vinha depois da conferência. Sem conferência, exigir
# validador impediria de entregar justamente o que o sistema aprovou.
_CONSTRAINT_VALIDADOR = "status_validado_exige_validador"
_VALIDADOR_ANTES = (
    "status NOT IN ('validado_externamente', 'entregue') OR "
    "validated_by_user_id IS NOT NULL"
)
_VALIDADOR_DEPOIS = (
    "status <> 'validado_externamente' OR validated_by_user_id IS NOT NULL"
)

_ANTES = (
    "em_conferencia",
    "recebido_validacao_pendente",
    "validado_externamente",
    "entregue",
    "recusado",
)
_DEPOIS = (
    "em_conferencia",
    "recebido_validacao_pendente",
    "recebido_assinado",
    "validado_externamente",
    "entregue",
    "recusado",
)


def _sql_in(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{valor}'" for valor in valores)


def upgrade() -> None:
    with op.batch_alter_table(_TABELA) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(
            _CONSTRAINT, f"status IN ({_sql_in(_DEPOIS)})"
        )
        batch.drop_constraint(_CONSTRAINT_VALIDADOR, type_="check")
        batch.create_check_constraint(
            _CONSTRAINT_VALIDADOR, _VALIDADOR_DEPOIS
        )


def downgrade() -> None:
    conexao = op.get_bind()
    restantes = conexao.exec_driver_sql(
        f"SELECT COUNT(*) FROM {_TABELA} WHERE status = 'recebido_assinado'"
    ).scalar()
    if restantes:
        raise RuntimeError(
            f"{restantes} documento(s) em 'recebido_assinado'. Reverter os "
            "deixaria fora de qualquer estado válido da fila — decida caso a "
            "caso antes de fazer o downgrade."
        )
    entregues_sem_validador = conexao.exec_driver_sql(
        f"SELECT COUNT(*) FROM {_TABELA} WHERE status = 'entregue' "
        "AND validated_by_user_id IS NULL"
    ).scalar()
    if entregues_sem_validador:
        raise RuntimeError(
            f"{entregues_sem_validador} documento(s) entregues sem validador "
            "humano — entregas do fluxo automático. Reverter a constraint "
            "recusaria linhas que já existem."
        )
    with op.batch_alter_table(_TABELA) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(
            _CONSTRAINT, f"status IN ({_sql_in(_ANTES)})"
        )
        batch.drop_constraint(_CONSTRAINT_VALIDADOR, type_="check")
        batch.create_check_constraint(
            _CONSTRAINT_VALIDADOR, _VALIDADOR_ANTES
        )
