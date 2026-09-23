"""M66 — a human confirmation, recorded, that ONE production NFS-e may be sent.

The Command Center button never sends anything itself. It writes one of these
rows and rings a doorbell; a separate one-shot worker — the only process that
ever holds the A1 — claims the row and makes at most one POST for it.

Two database-level guarantees, independent of any application code:

- ``uq_fiscal_issuance_request_active``: at most one request per document is
  ever live (authorized or running) at a time. Two gestores clicking at once
  produce one request and one refusal, not two workers.
- ``uq_fiscal_issuance_request_claimed_issue``: an ISSUE request for a given
  document can be claimed by a worker at most once, EVER — unless the worker
  refused it before anything was attempted (``refused`` is written only when
  no POST was made and no ``started`` attempt exists). That is the
  "never a second POST" rule written where no bug in the worker, no
  re-delivered doorbell and no second click can reach around it. A document
  whose one attempt ended rejected or uncertain is not re-sent from the
  button; it is reconciled (GET only), or it becomes a new, explicit mission.

The row itself is a workflow record and is updated as it moves. The evidence
of what happened at SEFIN stays where it always was: the append-only
``fiscal_attempts``, ``fiscal_artifacts`` and ``audit_logs``.

Revision ID: e8b3d6a4f190
Revises: c4e8b1f37a92
Create Date: 2026-09-23 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = 'e8b3d6a4f190'
down_revision = 'c4e8b1f37a92'
branch_labels = None
depends_on = None

TABLE = 'fiscal_issuance_requests'
ACTIVE_WHERE = "status IN ('authorized','running')"
CLAIMED_ISSUE_WHERE = "kind = 'issue' AND claimed_at IS NOT NULL AND status <> 'refused'"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('spirometry_exam_id', sa.String(length=36), nullable=False),
        sa.Column('preparation_id', sa.String(length=36), nullable=False),
        sa.Column('preparation_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('recipient_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('amount_confirmed', sa.Numeric(12, 2), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('authorized_by', sa.String(length=36), nullable=False),
        sa.Column('authorized_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True)),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('operation_id', sa.String(length=36)),
        sa.Column('result_code', sa.String(length=60)),
        sa.Column('external_id', sa.String(length=100)),
        sa.Column('provider_post_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('provider_get_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.CheckConstraint("kind IN ('issue','reconcile')", name='fiscal_issuance_request_kind'),
        sa.CheckConstraint(
            "status IN ('authorized','running','issued','rejected','uncertain','reconciled',"
            "'refused','expired','interrupted')", name='fiscal_issuance_request_status'),
        sa.CheckConstraint('amount_confirmed > 0', name='fiscal_issuance_request_amount'),
        sa.CheckConstraint('provider_post_count <= 1', name='fiscal_issuance_request_one_post'),
        sa.CheckConstraint("kind = 'issue' OR provider_post_count = 0",
                           name='fiscal_issuance_request_reconcile_never_posts'),
        sa.ForeignKeyConstraint(['document_id'], ['fiscal_documents.id'],
                                name='fk_fiscal_issuance_requests_document_id_fiscal_documents'),
        sa.ForeignKeyConstraint(['spirometry_exam_id'], ['spirometry_exams.id'],
                                name='fk_fiscal_issuance_requests_spirometry_exam_id_spirometry_exams'),
        sa.ForeignKeyConstraint(['preparation_id'], ['fiscal_preparations.id'],
                                name='fk_fiscal_issuance_requests_preparation_id_fiscal_preparations'),
        sa.ForeignKeyConstraint(['authorized_by'], ['users.id'],
                                name='fk_fiscal_issuance_requests_authorized_by_users'),
        sa.PrimaryKeyConstraint('id', name='pk_fiscal_issuance_requests'),
        sa.UniqueConstraint('idempotency_key', name='uq_fiscal_issuance_requests_idempotency_key'),
    )
    op.create_index('ix_fiscal_issuance_requests_document_id', TABLE, ['document_id'])
    op.create_index('uq_fiscal_issuance_request_active', TABLE, ['document_id'], unique=True,
                    sqlite_where=sa.text(ACTIVE_WHERE), postgresql_where=sa.text(ACTIVE_WHERE))
    op.create_index('uq_fiscal_issuance_request_claimed_issue', TABLE, ['document_id'],
                    unique=True, sqlite_where=sa.text(CLAIMED_ISSUE_WHERE),
                    postgresql_where=sa.text(CLAIMED_ISSUE_WHERE))


def downgrade() -> None:
    conn = op.get_bind()
    claimed = conn.execute(sa.text(
        "SELECT count(*) FROM fiscal_issuance_requests WHERE claimed_at IS NOT NULL")).scalar_one()
    if claimed:
        raise RuntimeError(
            "Existem %d pedido(s) de emissão já executados pelo worker. Apagar a tabela "
            "apagaria a garantia de que o botão não reenvia esses documentos." % claimed)
    op.drop_index('uq_fiscal_issuance_request_claimed_issue', table_name=TABLE)
    op.drop_index('uq_fiscal_issuance_request_active', table_name=TABLE)
    op.drop_index('ix_fiscal_issuance_requests_document_id', table_name=TABLE)
    op.drop_table(TABLE)
