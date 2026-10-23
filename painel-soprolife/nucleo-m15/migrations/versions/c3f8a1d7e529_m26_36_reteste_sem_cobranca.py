"""M26.36 — reteste de controle, sem cobrança e sem repasse médico.

O CASO QUE PEDIU ISSO

Um exame domiciliar foi laudado, assinado e entregue. Depois a família
questionou o resultado, e um SEGUNDO exame foi feito no outro espirômetro,
só para conferência — sem cobrar nada do paciente, sem nota fiscal nova e
sem receita nova. O reteste é um exame legítimo e precisa continuar no
prontuário, com o seu PDF técnico e o seu laudo próprio.

O PROBLEMA

Não havia como DIZER isso ao sistema. A ausência de receita já impedia
cobrança e nota — nada no fluxo de laudo cria lançamento financeiro, que
só nasce no cadastro do atendimento. Mas o repasse médico não olha para
receita nenhuma: `eligible_report_count` conta documentos-raiz liberados
na competência (services/medical_transfers.py). Assim que a laudadora
liberasse o laudo do reteste, ele entraria na contagem como produção
remunerada — e `create_physician_transfer` exige que o número informado
pelo administrador seja IGUAL ao contado, de modo que nem havia como
corrigir o valor na mão.

A COLUNA

`reteste_sem_cobranca` é uma declaração do operador sobre o EXAME: este
exame é um reteste de controle, não foi cobrado, e portanto não remunera
produção. Mora no exame (e não no laudo) porque é a propriedade do ATO —
vale para qualquer documento que nasça dele, inclusive uma corretiva
futura.

O default é `false` e o server_default é `false`: nenhuma linha histórica
muda de significado, e nenhum exame existente sai ou entra em repasse por
causa desta migration. O efeito é exclusivamente para frente, e só para o
exame que alguém marcar explicitamente.
"""

from alembic import op
import sqlalchemy as sa

revision = "c3f8a1d7e529"
down_revision = "b5e2f7a91c38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "spirometry_exams",
        sa.Column(
            "reteste_sem_cobranca",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("spirometry_exams", "reteste_sem_cobranca")
