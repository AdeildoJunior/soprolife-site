"""M26.36b — uma devolução para correção pode ser CANCELADA, nunca apagada.

POR QUE ESTA MIGRATION EXISTE (e por que a primeira tentativa estava errada)

A M26.36 nasceu para desfazer um clique equivocado em "Retornar para
laudadora": o laudo corretivo que o clique cria, vazio, precisa parar de
ocupar a fila. A primeira implementação REMOVIA esse documento e as linhas
que nasceram com ele. O banco recusou, e com razão: `report_assignments`,
`report_assignment_events` e `report_document_versions` são append-only por
gatilho (M24C, `m24c_reject_immutable_evidence`). A suíte não tinha pegado
isso porque roda em SQLite, onde `create_all` não cria gatilho nenhum.

O banco estava certo. Evidência clínica não se apaga — nem a de um engano.
A reversão correta é DECLARAR o cancelamento e deixar tudo no lugar.

AS DUAS COLUNAS

`devolucao_cancelada_em` / `devolucao_cancelada_por_user_id` seguem a mesma
forma do encerramento de exame (M25.24) e do arquivamento de pessoa: ou os
dois campos estão vazios, ou os dois estão preenchidos. Um cancelamento sem
quem e quando é exatamente o registro que ninguém consegue justificar
depois. O segundo CHECK diz que só uma CORRETIVA pode ser cancelada —
marcar um laudo-raiz assim não significaria nada.

O ÍNDICE ÚNICO PARCIAL

`uq_report_documents_corrects_document_id` garante que um laudo tenha no
máximo uma corretiva, inclusive sob transações concorrentes. Mantido como
estava, o documento cancelado continuaria ocupando essa vaga e o engano
BLOQUEARIA para sempre uma correção legítima do mesmo laudo. A trava passa
a valer só entre as corretivas vivas: `WHERE devolucao_cancelada_em IS
NULL`. Continua sendo o banco garantindo a unicidade — não a aplicação.

Nenhuma linha é reescrita: as duas colunas entram nulas e nenhum documento
existente é corretiva cancelada.
"""

from alembic import op
import sqlalchemy as sa

revision = "d7b2c4e86a91"
down_revision = "c3f8a1d7e529"
branch_labels = None
depends_on = None

UNIQUE_NAME = "uq_report_documents_corrects_document_id"
PARTIAL_INDEX = "uq_report_documents_corretiva_viva"
EVIDENCIA = (
    "(devolucao_cancelada_em IS NULL AND "
    "devolucao_cancelada_por_user_id IS NULL) OR "
    "(devolucao_cancelada_em IS NOT NULL AND "
    "devolucao_cancelada_por_user_id IS NOT NULL)"
)
SO_CORRETIVA = (
    "devolucao_cancelada_em IS NULL OR corrects_document_id IS NOT NULL"
)


def upgrade() -> None:
    op.add_column(
        "report_documents",
        sa.Column("devolucao_cancelada_em", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "report_documents",
        sa.Column("devolucao_cancelada_por_user_id", sa.String(36)),
    )
    with op.batch_alter_table("report_documents") as batch_op:
        batch_op.create_check_constraint(
            "devolucao_cancelada_com_evidencia", EVIDENCIA
        )
        batch_op.create_check_constraint(
            "devolucao_cancelada_so_em_corretiva", SO_CORRETIVA
        )
        batch_op.create_foreign_key(
            "fk_report_documents_devolucao_cancelada_por_user_id_users",
            "users",
            ["devolucao_cancelada_por_user_id"],
            ["id"],
        )
        # SQLite não tem DROP CONSTRAINT; dentro do batch a tabela é
        # recriada e a constraint simplesmente não é recopiada.
        batch_op.drop_constraint(UNIQUE_NAME, type_="unique")

    op.create_index(
        PARTIAL_INDEX,
        "report_documents",
        ["corrects_document_id"],
        unique=True,
        sqlite_where=sa.text("devolucao_cancelada_em IS NULL"),
        postgresql_where=sa.text("devolucao_cancelada_em IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(PARTIAL_INDEX, table_name="report_documents")
    with op.batch_alter_table("report_documents") as batch_op:
        batch_op.create_unique_constraint(
            UNIQUE_NAME, ["corrects_document_id"]
        )
        batch_op.drop_constraint(
            "fk_report_documents_devolucao_cancelada_por_user_id_users",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "devolucao_cancelada_so_em_corretiva", type_="check"
        )
        batch_op.drop_constraint(
            "devolucao_cancelada_com_evidencia", type_="check"
        )
    op.drop_column("report_documents", "devolucao_cancelada_por_user_id")
    op.drop_column("report_documents", "devolucao_cancelada_em")
