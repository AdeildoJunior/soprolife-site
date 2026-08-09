"""m25.14 — alarga as colunas de status de assinatura do laudo

A liberação institucional grava "liberada_institucional" (22 caracteres) em
DOIS lugares, dentro da mesma transação:

    report_documents.signature_status   VARCHAR(20)
    report_signatures.status            VARCHAR(20)

Em PostgreSQL isso aborta com StringDataRightTruncation e a API devolve 500 —
comprovado em produção na M25.13, onde nenhum laudo podia ser liberado. Em
SQLite o defeito é invisível, porque lá o limite de VARCHAR não é aplicado; por
isso a suíte passava.

As CHECK constraints que EXIGEM esse valor
(`ck_report_documents_clinical_state_coherent` e
`ck_report_signatures_status_valido`) continuam intactas: o que estava errado
era a capacidade da coluna, não a regra clínica. Nenhum valor funcional foi
encurtado para "fazer caber".

Revision ID: b8e4d2a71c53
Revises: d4a71c88b2e6
Create Date: 2026-08-09 17:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b8e4d2a71c53"
down_revision = "d4a71c88b2e6"
branch_labels = None
depends_on = None

LEN_NOVO = 40
LEN_ANTIGO = 20

# (tabela, coluna, nullable) — os dois pontos que a liberação toca.
ALVOS = (
    ("report_documents", "signature_status", True),
    ("report_signatures", "status", False),
)


def upgrade() -> None:
    for tabela, coluna, nullable in ALVOS:
        with op.batch_alter_table(tabela) as batch_op:
            batch_op.alter_column(
                coluna,
                existing_type=sa.String(length=LEN_ANTIGO),
                type_=sa.String(length=LEN_NOVO),
                existing_nullable=nullable,
            )


def downgrade() -> None:
    # Nunca truncar em silêncio: se já houver laudo liberado, o valor
    # "liberada_institucional" não cabe no limite antigo e o downgrade
    # destruiria evidência clínica. Falhar com clareza é o comportamento
    # correto — voltar exige antes decidir o que fazer com esses registros.
    conn = op.get_bind()
    for tabela, coluna, _nullable in ALVOS:
        longos = conn.execute(
            sa.text(
                f"SELECT id, {coluna} AS valor FROM {tabela} "
                f"WHERE length({coluna}) > :limite"
            ),
            {"limite": LEN_ANTIGO},
        ).fetchall()
        if longos:
            detalhe = "; ".join(f"{r.id}={r.valor}" for r in longos)
            raise RuntimeError(
                f"downgrade abortado: {tabela}.{coluna} contém valores com "
                f"mais de {LEN_ANTIGO} caracteres que seriam truncados: "
                f"{detalhe}"
            )

    for tabela, coluna, nullable in ALVOS:
        with op.batch_alter_table(tabela) as batch_op:
            batch_op.alter_column(
                coluna,
                existing_type=sa.String(length=LEN_NOVO),
                type_=sa.String(length=LEN_ANTIGO),
                existing_nullable=nullable,
            )
