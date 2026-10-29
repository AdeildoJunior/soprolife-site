"""EEG no motor fiscal corporativo. Não instala classificação nem ativa envio.

Revision ID: f1e4a2b72029
Revises: e9c4a1b72029
"""
from importlib import import_module
from alembic import op
import sqlalchemy as sa

revision = 'f1e4a2b72029'
down_revision = 'e9c4a1b72029'
branch_labels = depends_on = None
previous = import_module('migrations.versions.b5e2f7a91c38_m26_34_grouped_fiscal_document')
KIND = "kind IN ('single','grouped','eeg')"
SHAPE = ("(kind = 'single' AND eeg_exam_id IS NULL AND spirometry_exam_id IS NOT NULL AND recipient_person_id IS NULL) OR "
         "(kind = 'grouped' AND eeg_exam_id IS NULL AND spirometry_exam_id IS NULL AND recipient_person_id IS NOT NULL) OR "
         "(kind = 'eeg' AND eeg_exam_id IS NOT NULL AND spirometry_exam_id IS NULL AND recipient_person_id IS NULL)")
FK = 'fk_fiscal_documents_eeg_exam_id_eeg_exams'


def _change(up):
    sqlite = op.get_bind().dialect.name == 'sqlite'
    table = previous._documents_table(after=True)
    if not up:
        for constraint in list(table.constraints):
            if isinstance(constraint, sa.CheckConstraint) and constraint.name in (
                    'ck_fiscal_documents_fiscal_document_kind', 'ck_fiscal_documents_fiscal_document_shape'):
                table.constraints.remove(constraint)
        table.append_column(sa.Column('eeg_exam_id', sa.String(36), nullable=True))
        table.append_constraint(sa.CheckConstraint(KIND, name='fiscal_document_kind'))
        table.append_constraint(sa.CheckConstraint(SHAPE, name='fiscal_document_shape'))
        table.append_constraint(sa.ForeignKeyConstraint(['eeg_exam_id'], ['eeg_exams.id'], name=FK))
        table.append_constraint(sa.UniqueConstraint('eeg_exam_id', 'environment', name='uq_fiscal_eeg_environment'))
    if sqlite:
        for name in ('fiscal_documents_no_delete', 'fiscal_documents_identity'):
            op.execute('DROP TRIGGER IF EXISTS ' + name)
    with op.batch_alter_table('fiscal_documents', copy_from=table if sqlite else None) as batch:
        batch.drop_constraint('fiscal_document_kind', type_='check')
        batch.drop_constraint('fiscal_document_shape', type_='check')
        if up:
            batch.add_column(sa.Column('eeg_exam_id', sa.String(36), nullable=True))
            batch.create_foreign_key(FK, 'eeg_exams', ['eeg_exam_id'], ['id'])
            batch.create_unique_constraint('uq_fiscal_eeg_environment', ['eeg_exam_id', 'environment'])
        else:
            batch.drop_constraint('uq_fiscal_eeg_environment', type_='unique')
            batch.drop_constraint(FK, type_='foreignkey')
            batch.drop_column('eeg_exam_id')
        batch.create_check_constraint('fiscal_document_kind', KIND if up else previous.KIND_CHECK)
        batch.create_check_constraint('fiscal_document_shape', SHAPE if up else previous.SHAPE_CHECK)
    if sqlite:
        for statement in previous.DOCUMENT_TRIGGERS:
            if up and 'BEFORE UPDATE' in statement:
                statement = statement.replace('WHEN NEW.spirometry_exam_id', 'WHEN NEW.eeg_exam_id IS NOT OLD.eeg_exam_id OR NEW.spirometry_exam_id')
            op.execute(statement)
    else:
        statement = previous.PG_IDENTITY_AFTER
        if up:
            statement = statement.replace('NEW.spirometry_exam_id,', 'NEW.eeg_exam_id, NEW.spirometry_exam_id,').replace('OLD.spirometry_exam_id,', 'OLD.eeg_exam_id, OLD.spirometry_exam_id,')
        op.execute(statement)


def upgrade():
    _change(True)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM fiscal_documents WHERE kind = 'eeg'")).scalar_one():
        raise RuntimeError('Documentos fiscais EEG existentes: downgrade apagaria evidência; abortado.')
    _change(False)
