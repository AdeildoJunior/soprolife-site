"""médico solicitante do exame (opcional) e snapshot na versão do laudo

Nenhum campo existente serve: `responsavel` é o técnico, `origem` é aquisição,
`observacao` é texto livre e `indicacao_clinica` é dado clínico. O solicitante
é informado por exame; nunca inferido de parceiro, unidade ou outro exame.

ADITIVA E PARA FRENTE: colunas nullable, sem server_default e sem backfill.
Nenhum exame histórico, versão ou PDF já emitido muda.
"""

from alembic import op
import sqlalchemy as sa

revision = "e231126msol"
down_revision = "c191126cc2"
branch_labels = None
depends_on = None

_EXAM_CHECK = "medico_solicitante_crm_exige_nome"


def upgrade() -> None:
    with op.batch_alter_table("spirometry_exams", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("medico_solicitante_nome", sa.String(length=120), nullable=True)
        )
        batch_op.add_column(
            sa.Column("medico_solicitante_crm", sa.String(length=20), nullable=True)
        )
        batch_op.create_check_constraint(
            _EXAM_CHECK,
            "medico_solicitante_crm IS NULL OR medico_solicitante_nome IS NOT NULL",
        )
    # ADD COLUMN direto: a tabela é append-only e não pode ser recriada.
    op.add_column(
        "report_document_versions",
        sa.Column("medico_solicitante_snapshot", sa.String(length=160), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    for tabela, coluna in (
        ("spirometry_exams", "medico_solicitante_nome"),
        ("report_document_versions", "medico_solicitante_snapshot"),
    ):
        if bind.execute(
            sa.text(f"SELECT 1 FROM {tabela} WHERE {coluna} IS NOT NULL LIMIT 1")
        ).first():
            raise RuntimeError(
                f"downgrade recusado: há médico solicitante gravado em {tabela}."
            )
    op.drop_column("report_document_versions", "medico_solicitante_snapshot")
    with op.batch_alter_table("spirometry_exams", schema=None) as batch_op:
        batch_op.drop_constraint(_EXAM_CHECK, type_="check")
        batch_op.drop_column("medico_solicitante_crm")
        batch_op.drop_column("medico_solicitante_nome")
