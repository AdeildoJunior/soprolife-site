"""M25.8 — rastreio da assinatura externa em lote.

Aditiva e reversível: só acrescenta colunas anuláveis. Nenhum dado existente
é reescrito, e o downgrade devolve o schema exatamente ao estado da M25.7.

O estado "Laudado — aguardando assinatura digital" NÃO precisa de migration:
`assinatura_pendente` já existe como status de documento e como tipo de
versão desde a M24A, e as duas CHECK constraints já o aceitam.

Revision ID: d4a71c88b2e6
Revises: b6e2f94a17c3
"""

from alembic import op
import sqlalchemy as sa

revision = "d4a71c88b2e6"
down_revision = "b6e2f94a17c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Quando o laudo foi congelado para assinatura e quando saiu no ZIP.
    # Servem para a tela distinguir "revisado" de "já baixado para assinar".
    op.add_column(
        "report_documents",
        sa.Column("signature_prepared_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "report_documents",
        sa.Column("signature_downloaded_at", sa.DateTime(timezone=True)),
    )
    # Certificado da médica, vinculado na PRIMEIRA assinatura validada e
    # conferido em todas as seguintes. Guarda o subject do certificado, que
    # é dado profissional público — nunca CPF, chave ou biometria.
    op.add_column(
        "physician_profiles",
        sa.Column("icp_signer_subject", sa.String(length=300)),
    )
    op.add_column(
        "physician_profiles",
        sa.Column("icp_signer_bound_at", sa.DateTime(timezone=True)),
    )

    # `ck_report_documents_release_evidence_coherent` exigia status
    # 'liberado' para QUALQUER campo de liberação — inclusive
    # `validation_code`. Isso impedia o fluxo externo: o código de validação
    # é IMPRESSO no PDF (texto e QR) e portanto precisa existir ANTES da
    # assinatura, quando o laudo ainda está em 'assinatura_pendente'. Depois
    # de assinado o PDF não pode mais ser alterado para receber o código.
    #
    # A constraint continua barrando o que realmente é evidência de
    # liberação — `released_at`, `released_by_user_id` e
    # `released_physician_profile_id` seguem exclusivos de 'liberado'. O
    # código de validação passa a ser permitido também em
    # 'assinatura_pendente', que é o estado em que o documento é congelado.
    with op.batch_alter_table("report_documents") as batch:
        batch.drop_constraint(
            "release_evidence_coherent", type_="check"
        )
        batch.create_check_constraint(
            "release_evidence_coherent",
            "status = 'liberado' OR ("
            "released_at IS NULL AND released_by_user_id IS NULL AND "
            "released_physician_profile_id IS NULL AND ("
            "validation_code IS NULL OR "
            "status IN ('assinatura_pendente', 'assinado')"
            "))",
        )


def downgrade() -> None:
    with op.batch_alter_table("report_documents") as batch:
        batch.drop_constraint(
            "release_evidence_coherent", type_="check"
        )
        batch.create_check_constraint(
            "release_evidence_coherent",
            "status = 'liberado' OR ("
            "released_at IS NULL AND released_by_user_id IS NULL AND "
            "released_physician_profile_id IS NULL AND "
            "validation_code IS NULL)",
        )
    op.drop_column("physician_profiles", "icp_signer_bound_at")
    op.drop_column("physician_profiles", "icp_signer_subject")
    op.drop_column("report_documents", "signature_downloaded_at")
    op.drop_column("report_documents", "signature_prepared_at")
