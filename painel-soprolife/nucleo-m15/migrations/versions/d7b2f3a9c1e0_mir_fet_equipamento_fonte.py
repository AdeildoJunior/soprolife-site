"""equipamento_fonte da espirometria (observação técnica de FET no MIR)

O CASO QUE PEDIU ISSO

O relatório do MIR traz o FET (Forced Expiratory Time). Médicos acostumados a
outros equipamentos podem comparar esse número diretamente ao tempo/cronômetro
que viram em outro sistema, o que não é tecnicamente apropriado: os critérios
de temporização e término da manobra podem diferir. O laudo nativo passa a
imprimir uma observação técnica discreta sobre isso — mas SOMENTE quando o
exame foi feito no MIR Spirobank Smart II.

A COLUNA

`equipamento_fonte` é o primeiro ponto canônico que diz em qual aparelho a
manobra foi registrada. Antes disso, o modelo só constava (quando constava)
dentro do próprio PDF técnico do equipamento. O valor é escolhido no
recebimento/registro do exame; nunca inferido de nome de arquivo, OCR do PDF,
nome de clínica ou texto livre.

ADITIVA E PARA FRENTE

Coluna nullable, sem server_default e sem backfill: toda linha histórica fica
NULL ("não informado") e nenhum laudo já gerado muda. A migration não tenta
descobrir retroativamente se um exame antigo foi MIR ou KoKo — fazê-lo seria
adivinhação. O CHECK restringe os valores aceitos aos três canônicos ou NULL.
"""

from alembic import op
import sqlalchemy as sa

revision = "d7b2f3a9c1e0"
down_revision = "a291029eeg01"
branch_labels = None
depends_on = None

_EXAM_CHECK = "spirometry_equipamento_fonte_valido"


def upgrade() -> None:
    with op.batch_alter_table("spirometry_exams", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("equipamento_fonte", sa.String(length=40), nullable=True)
        )
        batch_op.create_check_constraint(
            _EXAM_CHECK,
            "equipamento_fonte IS NULL OR equipamento_fonte IN "
            "('MIR_SPIROBANK_SMART_II','KOKO','OUTRO')",
        )


def downgrade() -> None:
    with op.batch_alter_table("spirometry_exams", schema=None) as batch_op:
        batch_op.drop_constraint(_EXAM_CHECK, type_="check")
        batch_op.drop_column("equipamento_fonte")
