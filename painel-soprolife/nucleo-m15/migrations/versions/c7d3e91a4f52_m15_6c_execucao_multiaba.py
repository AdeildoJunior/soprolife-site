"""m15.6c — execução multiaba final: configuração de unidade + proveniência

Cria as tabelas partner_unit_configs (configuração operacional de unidade
parceira, SEM campos monetários — fronteira M14.2) e migration_provenance
(razão append-only da execução multiaba: lote, entidade criada, fingerprint
irreversível da origem, versão de mapeamento e chave de idempotência única
que garante zero linhas novas em reexecução e habilita rollback seletivo
em ordem reversa). Sem PII: nenhuma coluna carrega valor de origem.

Revision ID: c7d3e91a4f52
Revises: b4f8a2c15d31
Create Date: 2026-07-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c7d3e91a4f52'
down_revision = 'b4f8a2c15d31'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'partner_unit_configs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('partner_unit_id', sa.String(length=36), nullable=False),
        sa.Column('status', sa.String(length=40), nullable=True),
        sa.Column('dia_semana', sa.String(length=40), nullable=True),
        sa.Column('horario_inicio', sa.String(length=20), nullable=True),
        sa.Column('horario_fim', sa.String(length=20), nullable=True),
        sa.Column('capacidade_estimada_por_turno', sa.String(length=40), nullable=True),
        sa.Column('observacao', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['partner_unit_id'], ['partner_units.id'],
            name=op.f('fk_partner_unit_configs_partner_unit_id_partner_units'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_partner_unit_configs')),
    )
    with op.batch_alter_table('partner_unit_configs', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_partner_unit_configs_partner_unit_id'),
            ['partner_unit_id'], unique=False,
        )

    op.create_table(
        'migration_provenance',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('batch_id', sa.String(length=36), nullable=False),
        sa.Column('ordem', sa.Integer(), nullable=False),
        sa.Column('entidade', sa.String(length=60), nullable=False),
        sa.Column('entity_id', sa.String(length=36), nullable=False),
        sa.Column('source_domain', sa.String(length=60), nullable=False),
        sa.Column('source_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('mapping_version', sa.String(length=40), nullable=False),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['batch_id'], ['import_batches.id'],
            name=op.f('fk_migration_provenance_batch_id_import_batches'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_migration_provenance')),
        sa.UniqueConstraint(
            'batch_id', 'ordem', name='uq_migration_provenance_ordem',
        ),
        sa.UniqueConstraint(
            'idempotency_key',
            name=op.f('uq_migration_provenance_idempotency_key'),
        ),
    )
    with op.batch_alter_table('migration_provenance', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_migration_provenance_batch_id'), ['batch_id'],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_migration_provenance_entity_id'), ['entity_id'],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('migration_provenance', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_migration_provenance_entity_id'))
        batch_op.drop_index(batch_op.f('ix_migration_provenance_batch_id'))
    op.drop_table('migration_provenance')
    with op.batch_alter_table('partner_unit_configs', schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f('ix_partner_unit_configs_partner_unit_id'))
    op.drop_table('partner_unit_configs')
