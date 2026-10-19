"""Fase 2A — fundação operacional da EEGLife.

ADITIVA, e só. Cinco tabelas novas, duas sequências de código novas, e
NENHUMA alteração em tabela existente: `people`, `partners`, `partner_units`,
`spirometry_exams`, `report_documents` e todas as demais saem desta migration
exatamente como entraram. Não há `brand_id`, não há coluna nova em tabela
antiga, e não há backfill — nenhuma linha histórica é tocada.

POR QUE UM VÍNCULO EM VEZ DE UMA COLUNA DE MARCA
------------------------------------------------
A pessoa é identidade CORPORATIVA: quem fez espirometria e depois fizer um EEG
continua sendo a mesma pessoa, com o mesmo cadastro. Marcar `people` com uma
marca obrigaria a escolher uma marca para cada pessoa histórica — um
`UPDATE ... SET brand='soprolife'` em toda a base, que é exatamente o que esta
fase não pode fazer, e que seria falso para quem vier a ser das duas.

`eeg_enrollments` resolve pelo outro lado: a Central da EEGLife consulta o
VÍNCULO, não `people`. Quem nunca teve exame de EEG não aparece lá, sem que
nenhuma linha de `people` precise mudar.

ARQUIVO TÉCNICO
---------------
`eeg_technical_files` guarda METADADOS. Não existe coluna de blob, e isso é
decisão, não esquecimento: um traçado tem dezenas a centenas de MB e
monitorização prolongada chega a GB. `storage_provider`/`object_key` nascem
vazios, prontos para object storage privado futuro.

REVERSIBILIDADE
---------------
`downgrade()` derruba as cinco tabelas e remove as duas sequências. Como nada
existente foi alterado, o downgrade devolve o schema ao estado anterior sem
perder dado algum de SoproLife. O downgrade recusa tabelas com dados EEG: evidências operacionais não
podem ser apagadas para reverter código. Em produção, reverter somente o
código e preservar o schema aditivo.

Revision ID: c3f0a7b12e45
Revises: b70c4a2e9d15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c3f0a7b12e45'
down_revision = 'b70c4a2e9d15'
branch_labels = None
depends_on = None

UUID_LEN = 36
NOVOS_PREFIXOS = ('EEG', 'LEG')


def upgrade() -> None:
    op.create_table(
        'eeg_enrollments',
        sa.Column('id', sa.String(UUID_LEN), nullable=False),
        sa.Column('person_id', sa.String(UUID_LEN), nullable=False),
        sa.Column('status', sa.String(20), server_default='ativo', nullable=False),
        sa.Column('origem', sa.String(40), nullable=True),
        sa.Column('observacao', sa.Text(), nullable=True),
        sa.Column('criado_por', sa.String(UUID_LEN), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['person_id'], ['people.id'],
                                name=op.f('fk_eeg_enrollments_person_id_people')),
        sa.ForeignKeyConstraint(['criado_por'], ['users.id'],
                                name=op.f('fk_eeg_enrollments_criado_por_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_eeg_enrollments')),
        sa.CheckConstraint("status IN ('ativo','arquivado')",
                           name=op.f('ck_eeg_enrollments_eeg_enrollment_status_valido')),
    )
    op.create_index(op.f('ix_eeg_enrollments_person_id'), 'eeg_enrollments',
                    ['person_id'], unique=True)

    op.create_table(
        'eeg_exams',
        sa.Column('id', sa.String(UUID_LEN), nullable=False),
        sa.Column('public_code', sa.String(12), nullable=False),
        sa.Column('enrollment_id', sa.String(UUID_LEN), nullable=False),
        sa.Column('modalidade_codigo', sa.String(40), nullable=False),
        sa.Column('origem_tipo', sa.String(20), server_default='direto', nullable=False),
        sa.Column('partner_unit_id', sa.String(UUID_LEN), nullable=True),
        sa.Column('status', sa.String(30), server_default='cadastro', nullable=False),
        sa.Column('agendado_para', sa.DateTime(timezone=True), nullable=True),
        sa.Column('realizado_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('enviado_para_laudo_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('laudo_recebido_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('disponibilizado_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelado_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tecnico_responsavel', sa.String(200), nullable=True),
        sa.Column('observacao', sa.Text(), nullable=True),
        sa.Column('criado_por', sa.String(UUID_LEN), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['enrollment_id'], ['eeg_enrollments.id'],
                                name=op.f('fk_eeg_exams_enrollment_id_eeg_enrollments')),
        sa.ForeignKeyConstraint(['partner_unit_id'], ['partner_units.id'],
                                name=op.f('fk_eeg_exams_partner_unit_id_partner_units')),
        sa.ForeignKeyConstraint(['criado_por'], ['users.id'],
                                name=op.f('fk_eeg_exams_criado_por_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_eeg_exams')),
        sa.UniqueConstraint('public_code', name=op.f('uq_eeg_exams_public_code')),
        sa.CheckConstraint(
            "status IN ('cadastro','agendado','confirmado','realizado',"
            "'aguardando_envio_laudo','enviado_para_laudo','em_laudo',"
            "'laudo_recebido','laudo_disponivel','cancelado')",
            name=op.f('ck_eeg_exams_eeg_exam_status_valido')),
        sa.CheckConstraint("origem_tipo IN ('direto','parceiro')",
                           name=op.f('ck_eeg_exams_eeg_exam_origem_valida')),
        sa.CheckConstraint(
            "(origem_tipo = 'parceiro' AND partner_unit_id IS NOT NULL) OR "
            "(origem_tipo = 'direto' AND partner_unit_id IS NULL)",
            name=op.f('ck_eeg_exams_eeg_exam_origem_coerente')),
    )
    op.create_index(op.f('ix_eeg_exams_enrollment_id'), 'eeg_exams', ['enrollment_id'])
    op.create_index(op.f('ix_eeg_exams_partner_unit_id'), 'eeg_exams', ['partner_unit_id'])
    op.create_index(op.f('ix_eeg_exams_status'), 'eeg_exams', ['status'])

    op.create_table(
        'eeg_technical_files',
        sa.Column('id', sa.String(UUID_LEN), nullable=False),
        sa.Column('exam_id', sa.String(UUID_LEN), nullable=False),
        sa.Column('armazenamento_origem', sa.String(40), nullable=False),
        sa.Column('referencia_externa', sa.String(200), nullable=True),
        sa.Column('nome_logico', sa.String(200), nullable=True),
        sa.Column('formato', sa.String(40), nullable=True),
        sa.Column('mime_type', sa.String(80), nullable=True),
        sa.Column('tamanho_bytes', sa.Integer(), nullable=True),
        sa.Column('sha256', sa.String(64), nullable=True),
        sa.Column('duracao_segundos', sa.Integer(), nullable=True),
        sa.Column('canais', sa.Integer(), nullable=True),
        sa.Column('frequencia_hz', sa.Integer(), nullable=True),
        sa.Column('equipamento', sa.String(200), nullable=True),
        sa.Column('storage_provider', sa.String(40), nullable=True),
        sa.Column('object_key', sa.String(300), nullable=True),
        sa.Column('observacao', sa.Text(), nullable=True),
        sa.Column('criado_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('criado_por', sa.String(UUID_LEN), nullable=True),
        sa.ForeignKeyConstraint(['exam_id'], ['eeg_exams.id'],
                                name=op.f('fk_eeg_technical_files_exam_id_eeg_exams')),
        sa.ForeignKeyConstraint(['criado_por'], ['users.id'],
                                name=op.f('fk_eeg_technical_files_criado_por_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_eeg_technical_files')),
        sa.CheckConstraint(
            "armazenamento_origem IN ('equipamento','plataforma_externa',"
            "'midia_fisica','object_storage','outro')",
            name=op.f('ck_eeg_technical_files_eeg_tecnico_armazenamento_valido')),
        sa.CheckConstraint("tamanho_bytes IS NULL OR tamanho_bytes >= 0",
                           name=op.f('ck_eeg_technical_files_eeg_tecnico_tamanho_nao_negativo')),
    )
    op.create_index(op.f('ix_eeg_technical_files_exam_id'), 'eeg_technical_files', ['exam_id'])

    op.create_table(
        'eeg_report_dispatches',
        sa.Column('id', sa.String(UUID_LEN), nullable=False),
        sa.Column('exam_id', sa.String(UUID_LEN), nullable=False),
        sa.Column('destino_tipo', sa.String(20), nullable=False),
        sa.Column('destino_nome', sa.String(200), nullable=False),
        sa.Column('metodo', sa.String(40), nullable=False),
        sa.Column('protocolo_externo', sa.String(200), nullable=True),
        sa.Column('status', sa.String(20), server_default='enviado', nullable=False),
        sa.Column('observacao', sa.Text(), nullable=True),
        sa.Column('enviado_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('enviado_por', sa.String(UUID_LEN), nullable=True),
        sa.ForeignKeyConstraint(['exam_id'], ['eeg_exams.id'],
                                name=op.f('fk_eeg_report_dispatches_exam_id_eeg_exams')),
        sa.ForeignKeyConstraint(['enviado_por'], ['users.id'],
                                name=op.f('fk_eeg_report_dispatches_enviado_por_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_eeg_report_dispatches')),
        sa.CheckConstraint("destino_tipo IN ('empresa','medico')",
                           name=op.f('ck_eeg_report_dispatches_eeg_envio_destino_valido')),
        sa.CheckConstraint(
            "metodo IN ('plataforma_externa','upload_manual_externo','outro')",
            name=op.f('ck_eeg_report_dispatches_eeg_envio_metodo_valido')),
        sa.CheckConstraint("status IN ('enviado','cancelado','substituido')",
                           name=op.f('ck_eeg_report_dispatches_eeg_envio_status_valido')),
    )
    op.create_index(op.f('ix_eeg_report_dispatches_exam_id'), 'eeg_report_dispatches', ['exam_id'])

    op.create_table(
        'eeg_report_documents',
        sa.Column('id', sa.String(UUID_LEN), nullable=False),
        sa.Column('public_code', sa.String(12), nullable=False),
        sa.Column('exam_id', sa.String(UUID_LEN), nullable=False),
        sa.Column('versao', sa.Integer(), server_default='1', nullable=False),
        sa.Column('storage_path', sa.String(300), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('page_count', sa.Integer(), nullable=False),
        sa.Column('mime_type', sa.String(40), server_default='application/pdf', nullable=False),
        sa.Column('origem', sa.String(40), server_default='laudador_externo', nullable=False),
        sa.Column('medico_laudador', sa.String(200), nullable=True),
        sa.Column('status', sa.String(20), server_default='recebido', nullable=False),
        sa.Column('recebido_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('recebido_por', sa.String(UUID_LEN), nullable=True),
        sa.Column('disponibilizado_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('observacao', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['exam_id'], ['eeg_exams.id'],
                                name=op.f('fk_eeg_report_documents_exam_id_eeg_exams')),
        sa.ForeignKeyConstraint(['recebido_por'], ['users.id'],
                                name=op.f('fk_eeg_report_documents_recebido_por_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_eeg_report_documents')),
        sa.UniqueConstraint('public_code', name=op.f('uq_eeg_report_documents_public_code')),
        sa.UniqueConstraint('exam_id', 'versao', name='eeg_laudo_versao_unica'),
        sa.CheckConstraint("status IN ('recebido','vigente','substituido')",
                           name=op.f('ck_eeg_report_documents_eeg_laudo_status_valido')),
        sa.CheckConstraint("versao >= 1", name=op.f('ck_eeg_report_documents_eeg_laudo_versao_positiva')),
        sa.CheckConstraint("size_bytes > 0", name=op.f('ck_eeg_report_documents_eeg_laudo_tamanho_positivo')),
    )
    op.create_index(op.f('ix_eeg_report_documents_exam_id'), 'eeg_report_documents', ['exam_id'])

    # Sequências dos códigos públicos novos. `allocate_public_code` sabe criar
    # a linha que faltar, mas preseedar mantém o padrão da base e evita um
    # INSERT concorrente logo no primeiro exame.
    code_sequences = sa.table(
        'code_sequences',
        sa.column('prefix', sa.String),
        sa.column('next_value', sa.Integer),
    )
    op.bulk_insert(code_sequences,
                   [{'prefix': p, 'next_value': 1} for p in NOVOS_PREFIXOS])


def downgrade() -> None:
    bind = op.get_bind()
    for table in ('eeg_report_documents', 'eeg_report_dispatches',
                  'eeg_technical_files', 'eeg_exams', 'eeg_enrollments'):
        if bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Downgrade recusado: preservar dados operacionais EEG.")
    for prefixo in NOVOS_PREFIXOS:
        bind.execute(
            sa.text("DELETE FROM code_sequences WHERE prefix = :p"), {'p': prefixo}
        )
    op.drop_index(op.f('ix_eeg_report_documents_exam_id'), table_name='eeg_report_documents')
    op.drop_table('eeg_report_documents')
    op.drop_index(op.f('ix_eeg_report_dispatches_exam_id'), table_name='eeg_report_dispatches')
    op.drop_table('eeg_report_dispatches')
    op.drop_index(op.f('ix_eeg_technical_files_exam_id'), table_name='eeg_technical_files')
    op.drop_table('eeg_technical_files')
    op.drop_index(op.f('ix_eeg_exams_status'), table_name='eeg_exams')
    op.drop_index(op.f('ix_eeg_exams_partner_unit_id'), table_name='eeg_exams')
    op.drop_index(op.f('ix_eeg_exams_enrollment_id'), table_name='eeg_exams')
    op.drop_table('eeg_exams')
    op.drop_index(op.f('ix_eeg_enrollments_person_id'), table_name='eeg_enrollments')
    op.drop_table('eeg_enrollments')
