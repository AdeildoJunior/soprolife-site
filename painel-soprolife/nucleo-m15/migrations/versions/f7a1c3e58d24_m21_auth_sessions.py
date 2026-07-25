"""m21 — sessões de navegador (auth_sessions)

Puramente aditivo: cria UMA tabela nova e não toca em nenhuma existente.
Nenhuma linha de paciente, exame, consulta, follow-up ou financeiro é lida ou
alterada aqui.

A tabela guarda apenas hashes (sha256 do segredo do cookie e do csrf), o
fingerprint da senha vigente e marcas de tempo — nunca o segredo em claro,
nunca e-mail, nunca qualquer PII. `revoked_at` é o que dá ao logout poder
real de invalidação, que o token bearer stateless não tinha.

Revision ID: f7a1c3e58d24
Revises: e5b2d47c9a13
Create Date: 2026-07-25 01:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f7a1c3e58d24"
down_revision = "e5b2d47c9a13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("password_fingerprint", sa.String(length=32), nullable=False),
        sa.Column(
            "persistente", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_motivo", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
