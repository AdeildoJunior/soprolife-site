"""m15.6a — snapshots privados de importação (readiness de migração Sheets)

Cria a tabela import_snapshots: registro imutável de snapshot privado com
identidade (workbook_alias, sheet_alias, sha256), versão de mapeamento e
vínculos com lotes de dry-run/execução. Sem PII: apenas aliases, hashes e
metadados estruturais.

Revision ID: b4f8a2c15d31
Revises: f2efc6e45b12
Create Date: 2026-07-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b4f8a2c15d31'
down_revision = 'f2efc6e45b12'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'import_snapshots',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('source_type', sa.String(length=40), nullable=False),
        sa.Column('workbook_alias', sa.String(length=120), nullable=False),
        sa.Column('sheet_alias', sa.String(length=120), nullable=False),
        sa.Column('snapshot_ts_utc', sa.DateTime(timezone=True), nullable=False),
        sa.Column('arquivo', sa.String(length=200), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('manifest_sha256', sa.String(length=64), nullable=False),
        sa.Column('schema_version', sa.String(length=40), nullable=False),
        sa.Column('mapping_version', sa.String(length=40), nullable=False),
        sa.Column('encoding', sa.String(length=20), nullable=False),
        sa.Column('delimiter', sa.String(length=4), nullable=False),
        sa.Column('headers', sa.JSON(), nullable=True),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('linha_inicial_dados', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('dry_run_batch_id', sa.String(length=36), nullable=True),
        sa.Column('execute_batch_id', sa.String(length=36), nullable=True),
        sa.Column('registered_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['dry_run_batch_id'], ['import_batches.id'],
            name=op.f('fk_import_snapshots_dry_run_batch_id_import_batches'),
        ),
        sa.ForeignKeyConstraint(
            ['execute_batch_id'], ['import_batches.id'],
            name=op.f('fk_import_snapshots_execute_batch_id_import_batches'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_import_snapshots')),
        sa.UniqueConstraint(
            'workbook_alias', 'sheet_alias', 'sha256',
            name='uq_import_snapshot_identidade',
        ),
    )
    with op.batch_alter_table('import_snapshots', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_import_snapshots_sha256'), ['sha256'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('import_snapshots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_import_snapshots_sha256'))
    op.drop_table('import_snapshots')
