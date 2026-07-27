"""m24c — perfil médico, atribuição e fluxo clínico controlado

Mudança aditiva sobre M24B:
- papel médico explicitamente isolado da hierarquia administrativa;
- perfil profissional verificável e CRM/UF único enquanto ativo;
- origem controlada e exatamente uma atribuição ativa por documento;
- eventos de atribuição append-only;
- snapshots imutáveis de autoria, origem, interpretação e rodapé;
- templates clínicos revisionados e seis categorias provisórias bloqueadas;
- um único rodapé TESTE, sem qualquer alegação de assinatura;
- estados de preparação para assinatura, sem caminho de sucesso.

O downgrade falha fechado quando existem dados clínicos M24C. Ele só remove
estrutura e seeds quando nenhuma linha de perfil/atribuição/composição M24C
seria perdida.

Revision ID: 4c9e2f7a6b31
Revises: 8d4b1a2c9f70
Create Date: 2026-07-27 12:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "4c9e2f7a6b31"
down_revision = "8d4b1a2c9f70"
branch_labels = None
depends_on = None

UUID_LEN = 36
UF_VALUES = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT",
    "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO",
    "RR", "SC", "SP", "SE", "TO",
)
ORIGIN_VALUES = (
    "pastore", "coworking", "residencial", "clinica_parceira",
    "empresa_pcmso", "outro",
)
REPORT_STATUS_VALUES = (
    "atribuido", "em_elaboracao", "assinatura_pendente", "assinado",
    "rascunho", "em_revisao", "finalizado",
)
VERSION_KIND_VALUES = (
    "original", "rascunho", "assinatura_pendente", "assinado", "finalizado",
)
ASSIGNMENT_REASON_VALUES = (
    "initial_assignment", "assignment_correction", "physician_unavailable",
    "profile_suspended", "operational_redistribution", "corrective_document",
)
ASSIGNMENT_EVENT_VALUES = ("assigned", "reassigned", "corrective_assigned")
PROVISIONAL_WARNING = (
    "TEXTO CLÍNICO PENDENTE DE APROVAÇÃO — NÃO UTILIZAR EM PRODUÇÃO"
)
PROVISIONAL_TEMPLATES = (
    (
        "24c00000-0000-4000-8000-000000000001",
        "NORMAL_PROVISORIO",
        "Dentro dos limites da normalidade",
    ),
    (
        "24c00000-0000-4000-8000-000000000002",
        "OBSTRUTIVO_PROVISORIO",
        "Distúrbio ventilatório obstrutivo",
    ),
    (
        "24c00000-0000-4000-8000-000000000003",
        "OBSTRUTIVO_BD_PROVISORIO",
        "Distúrbio obstrutivo com avaliação pós-broncodilatador",
    ),
    (
        "24c00000-0000-4000-8000-000000000004",
        "SUGESTIVO_RESTRITIVO_PROVISORIO",
        "Redução de volumes sugestiva de restrição",
    ),
    (
        "24c00000-0000-4000-8000-000000000005",
        "MISTO_PROVISORIO",
        "Padrão ventilatório misto",
    ),
    (
        "24c00000-0000-4000-8000-000000000006",
        "INESPECIFICO_QUALIDADE_PROVISORIO",
        "Padrão inespecífico ou exame com limitação de qualidade",
    ),
)
TEST_FOOTER_ID = "24c00000-0000-4000-8000-000000000100"
TEST_FOOTER_CODE = "TESTE_NAO_ASSINADO"
TEST_FOOTER_BODY = """SoproLife Diagnósticos e Soluções em Saúde
Laudo de espirometria
Médico: {physician_name}
CRM/{crm_state}: {crm_number}
RQE: {rqe}
Exame: {exam_code}
Origem: {origin}
Emissão: {issued_at}
Laudo/versão: {report_code}/v{version_number}
Estado da assinatura: NÃO ASSINADO — PREPARAÇÃO PENDENTE
MODELO DE TESTE — DOCUMENTO NÃO ASSINADO E SEM VALIDADE PARA LIBERAÇÃO"""


def _dialect() -> str:
    return op.get_bind().dialect.name


def _create_immutable_trigger(table_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_{table_name}_m24c_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION m24c_reject_immutable_evidence()
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()

    # Papel explícito: inserir não altera nenhum vínculo de usuário.
    bind.execute(
        sa.text(
            """
            INSERT INTO roles (name, description)
            SELECT 'medico', 'Autor clínico de laudos atribuídos'
            WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'medico')
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO code_sequences (prefix, next_value)
            SELECT 'LAU', 1
            WHERE NOT EXISTS (SELECT 1 FROM code_sequences WHERE prefix = 'LAU')
            """
        )
    )

    op.create_table(
        "physician_profiles",
        sa.Column("id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("user_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("professional_name", sa.String(length=220), nullable=False),
        sa.Column("crm_number", sa.String(length=12), nullable=False),
        sa.Column("crm_state", sa.String(length=2), nullable=False),
        sa.Column("rqe", sa.String(length=30), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("verification_status", sa.String(length=20), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_user_id", sa.String(length=UUID_LEN), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(professional_name)) >= 2",
            name=op.f("ck_physician_profiles_professional_name_present"),
        ),
        sa.CheckConstraint(
            "length(crm_number) BETWEEN 1 AND 12",
            name=op.f("ck_physician_profiles_crm_number_length"),
        ),
        sa.CheckConstraint(
            f"crm_state IN {UF_VALUES!r}",
            name=op.f("ck_physician_profiles_crm_state_valid"),
        ),
        sa.CheckConstraint(
            "verification_status IN ('pending', 'verified', 'rejected')",
            name=op.f("ck_physician_profiles_verification_status_valid"),
        ),
        sa.CheckConstraint(
            "("
            "verification_status = 'verified' AND "
            "verified_at IS NOT NULL AND verified_by_user_id IS NOT NULL"
            ") OR ("
            "verification_status <> 'verified' AND "
            "verified_at IS NULL AND verified_by_user_id IS NULL"
            ")",
            name=op.f("ck_physician_profiles_verification_evidence_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_physician_profiles_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"], ["users.id"],
            name=op.f("fk_physician_profiles_verified_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_physician_profiles")),
    )
    op.create_index(
        op.f("ix_physician_profiles_user_id"),
        "physician_profiles",
        ["user_id"],
        unique=True,
    )
    op.create_index(
        "uq_physician_profiles_active_crm_uf",
        "physician_profiles",
        ["crm_state", "crm_number"],
        unique=True,
        sqlite_where=sa.text("active = 1"),
        postgresql_where=sa.text("active = true"),
    )

    # Catálogo clínico passa a ser revisionado: conteúdo antigo nunca é
    # reescrito; cada atualização cria outra linha (codigo, versao).
    with op.batch_alter_table("report_templates", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("uq_report_templates_codigo"), type_="unique"
        )
        batch_op.alter_column(
            "codigo",
            existing_type=sa.String(length=20),
            type_=sa.String(length=48),
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default="draft",
            )
        )
        batch_op.add_column(
            sa.Column(
                "clinically_approved",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "supersedes_template_id",
                sa.String(length=UUID_LEN),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "approved_by_user_id",
                sa.String(length=UUID_LEN),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_foreign_key(
            op.f("fk_report_templates_supersedes_template_id_report_templates"),
            "report_templates",
            ["supersedes_template_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            op.f("fk_report_templates_approved_by_user_id_users"),
            "users",
            ["approved_by_user_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_report_template_code_version", ["codigo", "versao"]
        )
        batch_op.create_unique_constraint(
            "uq_report_template_supersedes_template_id",
            ["supersedes_template_id"],
        )
        batch_op.create_check_constraint(
            op.f("ck_report_templates_version_positive"),
            "versao > 0",
        )
        batch_op.create_check_constraint(
            op.f("ck_report_templates_status_valid"),
            "status IN ('draft', 'approved', 'retired')",
        )
        batch_op.create_check_constraint(
            op.f("ck_report_templates_clinical_approval_evidence"),
            "("
            "clinically_approved = true AND status = 'approved' AND "
            "approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL"
            ") OR ("
            "clinically_approved = false AND status <> 'approved' AND "
            "approved_by_user_id IS NULL AND approved_at IS NULL"
            ")",
        )
        batch_op.alter_column("status", server_default=None)
        batch_op.alter_column("clinically_approved", server_default=None)

    now_sql = "CURRENT_TIMESTAMP"
    for template_id, code, title in PROVISIONAL_TEMPLATES:
        bind.execute(
            sa.text(
                f"""
                INSERT INTO report_templates (
                    id, codigo, titulo, texto_tooltip, texto_completo,
                    versao, ativo, status, clinically_approved,
                    created_at, updated_at
                ) VALUES (
                    :id, :code, :title, :tooltip, :body,
                    1, true, 'draft', false,
                    {now_sql}, {now_sql}
                )
                """
            ),
            {
                "id": template_id,
                "code": code,
                "title": title,
                "tooltip": "PROVISÓRIO — bloqueado para uso clínico",
                "body": PROVISIONAL_WARNING,
            },
        )

    op.create_table(
        "report_footer_templates",
        sa.Column("id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("production_approved", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=UUID_LEN), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version > 0",
            name=op.f("ck_report_footer_templates_version_positive"),
        ),
        sa.CheckConstraint(
            "status IN ('test', 'draft', 'approved', 'retired')",
            name=op.f("ck_report_footer_templates_status_valid"),
        ),
        sa.CheckConstraint(
            "production_approved = false OR status = 'approved'",
            name=op.f(
                "ck_report_footer_templates_production_approval_coherent"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name=op.f("fk_report_footer_templates_created_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_report_footer_templates")
        ),
        sa.UniqueConstraint(
            "code", "version", name="uq_report_footer_code_version"
        ),
    )
    bind.execute(
        sa.text(
            f"""
            INSERT INTO report_footer_templates (
                id, code, version, body_template, status,
                production_approved, active, created_at, updated_at
            ) VALUES (
                :id, :code, 1, :body, 'test',
                false, true, {now_sql}, {now_sql}
            )
            """
        ),
        {"id": TEST_FOOTER_ID, "code": TEST_FOOTER_CODE, "body": TEST_FOOTER_BODY},
    )

    with op.batch_alter_table("report_documents", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_report_documents_status_valido"), type_="check"
        )
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=20),
            type_=sa.String(length=24),
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column("public_code", sa.String(length=12), nullable=True)
        )
        batch_op.add_column(
            sa.Column("origin_type", sa.String(length=30), nullable=True)
        )
        batch_op.add_column(
            sa.Column("origin_label", sa.String(length=120), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "origin_partner_unit_id",
                sa.String(length=UUID_LEN),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "clinical_started_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "ready_for_signature_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "correction_reason_code", sa.String(length=40), nullable=True
            )
        )

    rows = bind.execute(
        sa.text("SELECT id FROM report_documents ORDER BY created_at, id")
    ).fetchall()
    for index, row in enumerate(rows, start=1):
        bind.execute(
            sa.text(
                "UPDATE report_documents SET public_code=:code WHERE id=:id"
            ),
            {"code": f"LAU-{index:06d}", "id": row[0]},
        )
    bind.execute(
        sa.text(
            "UPDATE code_sequences SET next_value=:next_value WHERE prefix='LAU'"
        ),
        {"next_value": len(rows) + 1},
    )

    with op.batch_alter_table("report_documents", schema=None) as batch_op:
        batch_op.alter_column(
            "public_code",
            existing_type=sa.String(length=12),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            op.f("uq_report_documents_public_code"), ["public_code"]
        )
        batch_op.create_foreign_key(
            op.f(
                "fk_report_documents_origin_partner_unit_id_partner_units"
            ),
            "partner_units",
            ["origin_partner_unit_id"],
            ["id"],
        )
        batch_op.create_check_constraint(
            op.f("ck_report_documents_status_valido"),
            f"status IN {REPORT_STATUS_VALUES!r}",
        )
        batch_op.create_check_constraint(
            op.f("ck_report_documents_origin_type_valid"),
            f"origin_type IS NULL OR origin_type IN {ORIGIN_VALUES!r}",
        )
        batch_op.create_check_constraint(
            op.f("ck_report_documents_signed_state_complete"),
            "("
            "status = 'assinado' AND signature_status = 'assinada' AND "
            "signed_at IS NOT NULL"
            ") OR status <> 'assinado'",
        )
        batch_op.create_check_constraint(
            op.f("ck_report_documents_clinical_state_coherent"),
            "("
            "status NOT IN ("
            "'atribuido', 'em_elaboracao', "
            "'assinatura_pendente', 'assinado'"
            ")"
            ") OR ("
            "status = 'atribuido' AND "
            "clinical_started_at IS NULL AND "
            "ready_for_signature_at IS NULL AND "
            "signed_at IS NULL AND signature_status IS NULL"
            ") OR ("
            "status = 'em_elaboracao' AND "
            "clinical_started_at IS NOT NULL AND "
            "ready_for_signature_at IS NULL AND "
            "signed_at IS NULL AND signature_status IS NULL"
            ") OR ("
            "status = 'assinatura_pendente' AND "
            "clinical_started_at IS NOT NULL AND "
            "ready_for_signature_at IS NOT NULL AND "
            "signed_at IS NULL AND "
            "signature_status = 'assinatura_pendente'"
            ") OR ("
            "status = 'assinado' AND "
            "clinical_started_at IS NOT NULL AND "
            "ready_for_signature_at IS NOT NULL AND "
            "signed_at IS NOT NULL AND signature_status = 'assinada'"
            ")",
        )

    op.create_table(
        "report_assignments",
        sa.Column("id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("report_document_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("physician_profile_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_by_user_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("reason_code", sa.String(length=40), nullable=False),
        sa.Column(
            "supersedes_assignment_id", sa.String(length=UUID_LEN), nullable=True
        ),
        sa.CheckConstraint(
            f"reason_code IN {ASSIGNMENT_REASON_VALUES!r}",
            name=op.f("ck_report_assignments_reason_code_valid"),
        ),
        sa.CheckConstraint(
            "(active = true AND ended_at IS NULL) OR "
            "(active = false AND ended_at IS NOT NULL)",
            name=op.f("ck_report_assignments_active_period_coherent"),
        ),
        sa.CheckConstraint(
            "("
            "reason_code IN ('initial_assignment', 'corrective_document') AND "
            "supersedes_assignment_id IS NULL"
            ") OR ("
            "reason_code NOT IN ('initial_assignment', 'corrective_document') AND "
            "supersedes_assignment_id IS NOT NULL"
            ")",
            name=op.f(
                "ck_report_assignments_reason_supersession_coherent"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["report_document_id"], ["report_documents.id"],
            name=op.f(
                "fk_report_assignments_report_document_id_report_documents"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["physician_profile_id"], ["physician_profiles.id"],
            name=op.f(
                "fk_report_assignments_physician_profile_id_physician_profiles"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_user_id"], ["users.id"],
            name=op.f("fk_report_assignments_assigned_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_assignment_id"], ["report_assignments.id"],
            name=op.f(
                "fk_report_assignments_supersedes_assignment_id_report_assignments"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_assignments")),
        sa.UniqueConstraint(
            "supersedes_assignment_id",
            name="uq_report_assignment_supersedes_assignment_id",
        ),
    )
    op.create_index(
        op.f("ix_report_assignments_report_document_id"),
        "report_assignments",
        ["report_document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_report_assignments_physician_profile_id"),
        "report_assignments",
        ["physician_profile_id"],
        unique=False,
    )
    op.create_index(
        "uq_report_assignments_one_active_per_document",
        "report_assignments",
        ["report_document_id"],
        unique=True,
        sqlite_where=sa.text("active = 1"),
        postgresql_where=sa.text("active = true"),
    )

    op.create_table(
        "report_assignment_events",
        sa.Column("id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("report_document_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("assignment_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("physician_profile_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column(
            "previous_physician_profile_id",
            sa.String(length=UUID_LEN),
            nullable=True,
        ),
        sa.Column("reason_code", sa.String(length=40), nullable=False),
        sa.Column("performed_by_user_id", sa.String(length=UUID_LEN), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"event_type IN {ASSIGNMENT_EVENT_VALUES!r}",
            name=op.f("ck_report_assignment_events_event_type_valid"),
        ),
        sa.CheckConstraint(
            f"reason_code IN {ASSIGNMENT_REASON_VALUES!r}",
            name=op.f("ck_report_assignment_events_reason_code_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["report_document_id"], ["report_documents.id"],
            name=op.f(
                "fk_report_assignment_events_report_document_id_report_documents"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"], ["report_assignments.id"],
            name=op.f(
                "fk_report_assignment_events_assignment_id_report_assignments"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["physician_profile_id"], ["physician_profiles.id"],
            name=op.f(
                "fk_report_assignment_events_physician_profile_id_physician_profiles"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["previous_physician_profile_id"], ["physician_profiles.id"],
            name=op.f(
                "fk_report_assignment_events_previous_physician_profile_id_physician_profiles"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["performed_by_user_id"], ["users.id"],
            name=op.f(
                "fk_report_assignment_events_performed_by_user_id_users"
            ),
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_report_assignment_events")
        ),
        sa.UniqueConstraint(
            "assignment_id",
            name=op.f("uq_report_assignment_events_assignment_id"),
        ),
    )
    op.create_index(
        op.f("ix_report_assignment_events_report_document_id"),
        "report_assignment_events",
        ["report_document_id"],
        unique=False,
    )

    with op.batch_alter_table("report_document_versions", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_report_document_versions_kind_valido"), type_="check"
        )
        batch_op.add_column(
            sa.Column("interpretation_text_snapshot", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "interpretation_text_sha256", sa.String(length=64), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "physician_profile_id_snapshot",
                sa.String(length=UUID_LEN),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "physician_name_snapshot", sa.String(length=220), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "physician_crm_number_snapshot",
                sa.String(length=12),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "physician_crm_state_snapshot",
                sa.String(length=2),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "physician_rqe_snapshot", sa.String(length=30), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "origin_type_snapshot", sa.String(length=30), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "origin_label_snapshot", sa.String(length=120), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "origin_partner_unit_id_snapshot",
                sa.String(length=UUID_LEN),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "footer_template_id", sa.String(length=UUID_LEN), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "footer_code_snapshot", sa.String(length=40), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column("footer_version_snapshot", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("footer_text_snapshot", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "footer_text_sha256", sa.String(length=64), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "issued_at_snapshot", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch_op.create_foreign_key(
            op.f(
                "fk_report_document_versions_footer_template_id_report_footer_templates"
            ),
            "report_footer_templates",
            ["footer_template_id"],
            ["id"],
        )
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_kind_valido"),
            f"kind IN {VERSION_KIND_VALUES!r}",
        )
        batch_op.create_check_constraint(
            op.f(
                "ck_report_document_versions_interpretation_snapshot_complete"
            ),
            "("
            "interpretation_text_snapshot IS NULL AND "
            "interpretation_text_sha256 IS NULL"
            ") OR ("
            "interpretation_text_snapshot IS NOT NULL AND "
            "interpretation_text_sha256 IS NOT NULL"
            ")",
        )
        batch_op.create_check_constraint(
            op.f(
                "ck_report_document_versions_physician_origin_snapshot_complete"
            ),
            "("
            "physician_profile_id_snapshot IS NULL AND "
            "physician_name_snapshot IS NULL AND "
            "physician_crm_number_snapshot IS NULL AND "
            "physician_crm_state_snapshot IS NULL AND "
            "origin_type_snapshot IS NULL"
            ") OR ("
            "physician_profile_id_snapshot IS NOT NULL AND "
            "physician_name_snapshot IS NOT NULL AND "
            "physician_crm_number_snapshot IS NOT NULL AND "
            f"physician_crm_state_snapshot IN {UF_VALUES!r} AND "
            f"origin_type_snapshot IN {ORIGIN_VALUES!r}"
            ")",
        )
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_footer_snapshot_complete"),
            "("
            "footer_template_id IS NULL AND "
            "footer_code_snapshot IS NULL AND "
            "footer_version_snapshot IS NULL AND "
            "footer_text_snapshot IS NULL AND "
            "footer_text_sha256 IS NULL AND "
            "issued_at_snapshot IS NULL"
            ") OR ("
            "footer_template_id IS NOT NULL AND "
            "footer_code_snapshot IS NOT NULL AND "
            "footer_version_snapshot IS NOT NULL AND "
            "footer_version_snapshot > 0 AND "
            "footer_text_snapshot IS NOT NULL AND "
            "footer_text_sha256 IS NOT NULL AND "
            "issued_at_snapshot IS NOT NULL"
            ")",
        )

    bind.execute(
        sa.text(
            """
            UPDATE report_signatures
            SET provider='unconfigured'
            WHERE provider IS NULL AND status='assinatura_pendente'
            """
        )
    )

    if _dialect() == "postgresql":
        op.execute(
            """
            CREATE FUNCTION m24c_reject_immutable_evidence()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'M24C append-only: immutable clinical evidence';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table_name in (
            "report_document_versions",
            "report_assignment_events",
            "report_templates",
            "report_footer_templates",
        ):
            _create_immutable_trigger(table_name)
        op.execute(
            """
            CREATE FUNCTION m24c_validate_physician_profile()
            RETURNS trigger AS $$
            BEGIN
                IF NEW.crm_number !~ '^[0-9]{1,12}$' THEN
                    RAISE EXCEPTION 'CRM must contain digits only';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_physician_profiles_m24c_validate
            BEFORE INSERT OR UPDATE ON physician_profiles
            FOR EACH ROW EXECUTE FUNCTION m24c_validate_physician_profile()
            """
        )
        op.execute(
            """
            CREATE FUNCTION m24c_validate_active_assignment()
            RETURNS trigger AS $$
            BEGIN
                IF NEW.active THEN
                    PERFORM 1
                    FROM physician_profiles p
                    JOIN users u ON u.id = p.user_id
                    JOIN user_roles ur ON ur.user_id = u.id
                    JOIN roles r ON r.id = ur.role_id
                    WHERE p.id = NEW.physician_profile_id
                      AND p.active
                      AND p.verification_status = 'verified'
                      AND u.ativo
                      AND r.name = 'medico';
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'active verified explicit physician required';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_report_assignments_m24c_validate
            BEFORE INSERT OR UPDATE ON report_assignments
            FOR EACH ROW EXECUTE FUNCTION m24c_validate_active_assignment()
            """
        )
        op.execute(
            """
            CREATE FUNCTION m24c_guard_assignment_history()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'M24C append-only: assignment cannot be deleted';
                END IF;
                IF OLD.id IS DISTINCT FROM NEW.id
                   OR OLD.report_document_id IS DISTINCT FROM NEW.report_document_id
                   OR OLD.physician_profile_id IS DISTINCT FROM NEW.physician_profile_id
                   OR OLD.assigned_at IS DISTINCT FROM NEW.assigned_at
                   OR OLD.assigned_by_user_id IS DISTINCT FROM NEW.assigned_by_user_id
                   OR OLD.reason_code IS DISTINCT FROM NEW.reason_code
                   OR OLD.supersedes_assignment_id IS DISTINCT FROM NEW.supersedes_assignment_id
                   OR OLD.active IS DISTINCT FROM true
                   OR NEW.active IS DISTINCT FROM false
                   OR OLD.ended_at IS NOT NULL
                   OR NEW.ended_at IS NULL THEN
                    RAISE EXCEPTION 'M24C append-only: invalid assignment rewrite';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_report_assignments_m24c_history
            BEFORE UPDATE OR DELETE ON report_assignments
            FOR EACH ROW EXECUTE FUNCTION m24c_guard_assignment_history()
            """
        )


def _fail_closed_downgrade_if_data() -> None:
    bind = op.get_bind()
    checks = {
        "physician_profiles": "SELECT count(*) FROM physician_profiles",
        "report_assignments": "SELECT count(*) FROM report_assignments",
        "report_assignment_events": "SELECT count(*) FROM report_assignment_events",
        "explicit_physician_roles": """
            SELECT count(*) FROM user_roles
            WHERE role_id = (SELECT id FROM roles WHERE name='medico')
        """,
        "m24c_report_documents": """
            SELECT count(*) FROM report_documents
            WHERE origin_type IS NOT NULL
               OR clinical_started_at IS NOT NULL
               OR ready_for_signature_at IS NOT NULL
               OR signed_at IS NOT NULL
               OR correction_reason_code IS NOT NULL
               OR status IN (
                   'atribuido', 'em_elaboracao',
                   'assinatura_pendente', 'assinado'
               )
        """,
        "m24c_report_versions": """
            SELECT count(*) FROM report_document_versions
            WHERE interpretation_text_snapshot IS NOT NULL
               OR physician_profile_id_snapshot IS NOT NULL
               OR footer_template_id IS NOT NULL
        """,
        "m24c_template_revisions": f"""
            SELECT count(*) FROM report_templates
            WHERE (
                status <> 'draft'
                OR clinically_approved = true
                OR supersedes_template_id IS NOT NULL
                OR approved_by_user_id IS NOT NULL
                OR approved_at IS NOT NULL
            )
            OR id IN (
                SELECT id FROM report_templates
                WHERE codigo IN ({
                    ", ".join(repr(row[1]) for row in PROVISIONAL_TEMPLATES)
                })
                  AND (
                    texto_completo <> :warning
                    OR versao <> 1
                    OR status <> 'draft'
                    OR clinically_approved = true
                  )
            )
        """,
    }
    for label, query in checks.items():
        params = {"warning": PROVISIONAL_WARNING} if ":warning" in query else {}
        count = bind.execute(sa.text(query), params).scalar_one()
        if count:
            raise RuntimeError(
                "Downgrade M24C recusado: existem dados clínicos que seriam "
                f"perdidos ({label}={count})."
            )


def downgrade() -> None:
    bind = op.get_bind()
    _fail_closed_downgrade_if_data()

    if _dialect() == "postgresql":
        op.execute(
            "DROP TRIGGER trg_report_assignments_m24c_history ON report_assignments"
        )
        op.execute("DROP FUNCTION m24c_guard_assignment_history()")
        op.execute(
            "DROP TRIGGER trg_report_assignments_m24c_validate ON report_assignments"
        )
        op.execute("DROP FUNCTION m24c_validate_active_assignment()")
        op.execute(
            "DROP TRIGGER trg_physician_profiles_m24c_validate ON physician_profiles"
        )
        op.execute("DROP FUNCTION m24c_validate_physician_profile()")
        for table_name in (
            "report_footer_templates",
            "report_templates",
            "report_assignment_events",
            "report_document_versions",
        ):
            op.execute(
                f"DROP TRIGGER trg_{table_name}_m24c_immutable ON {table_name}"
            )
        op.execute("DROP FUNCTION m24c_reject_immutable_evidence()")

    op.drop_index(
        op.f("ix_report_assignment_events_report_document_id"),
        table_name="report_assignment_events",
    )
    op.drop_table("report_assignment_events")
    op.drop_index(
        "uq_report_assignments_one_active_per_document",
        table_name="report_assignments",
    )
    op.drop_index(
        op.f("ix_report_assignments_physician_profile_id"),
        table_name="report_assignments",
    )
    op.drop_index(
        op.f("ix_report_assignments_report_document_id"),
        table_name="report_assignments",
    )
    op.drop_table("report_assignments")

    with op.batch_alter_table("report_document_versions", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_report_document_versions_footer_snapshot_complete"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f(
                "ck_report_document_versions_physician_origin_snapshot_complete"
            ),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f(
                "ck_report_document_versions_interpretation_snapshot_complete"
            ),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_report_document_versions_kind_valido"), type_="check"
        )
        batch_op.drop_constraint(
            op.f(
                "fk_report_document_versions_footer_template_id_report_footer_templates"
            ),
            type_="foreignkey",
        )
        for column in (
            "issued_at_snapshot",
            "footer_text_sha256",
            "footer_text_snapshot",
            "footer_version_snapshot",
            "footer_code_snapshot",
            "footer_template_id",
            "origin_partner_unit_id_snapshot",
            "origin_label_snapshot",
            "origin_type_snapshot",
            "physician_rqe_snapshot",
            "physician_crm_state_snapshot",
            "physician_crm_number_snapshot",
            "physician_name_snapshot",
            "physician_profile_id_snapshot",
            "interpretation_text_sha256",
            "interpretation_text_snapshot",
        ):
            batch_op.drop_column(column)
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_kind_valido"),
            "kind IN ('original', 'rascunho', 'finalizado')",
        )

    op.drop_table("report_footer_templates")

    provisional_ids = [row[0] for row in PROVISIONAL_TEMPLATES]
    placeholders = ", ".join(f":id{index}" for index in range(len(provisional_ids)))
    bind.execute(
        sa.text(f"DELETE FROM report_templates WHERE id IN ({placeholders})"),
        {f"id{index}": value for index, value in enumerate(provisional_ids)},
    )
    with op.batch_alter_table("report_templates", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_report_templates_clinical_approval_evidence"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_report_templates_status_valid"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_report_templates_version_positive"), type_="check"
        )
        batch_op.drop_constraint(
            "uq_report_template_supersedes_template_id", type_="unique"
        )
        batch_op.drop_constraint(
            "uq_report_template_code_version", type_="unique"
        )
        batch_op.drop_constraint(
            op.f("fk_report_templates_approved_by_user_id_users"),
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            op.f(
                "fk_report_templates_supersedes_template_id_report_templates"
            ),
            type_="foreignkey",
        )
        batch_op.drop_column("approved_at")
        batch_op.drop_column("approved_by_user_id")
        batch_op.drop_column("supersedes_template_id")
        batch_op.drop_column("clinically_approved")
        batch_op.drop_column("status")
        batch_op.alter_column(
            "codigo",
            existing_type=sa.String(length=48),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
        batch_op.create_unique_constraint(
            op.f("uq_report_templates_codigo"), ["codigo"]
        )

    with op.batch_alter_table("report_documents", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_report_documents_clinical_state_coherent"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_report_documents_signed_state_complete"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_report_documents_origin_type_valid"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_report_documents_status_valido"), type_="check"
        )
        batch_op.drop_constraint(
            op.f(
                "fk_report_documents_origin_partner_unit_id_partner_units"
            ),
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            op.f("uq_report_documents_public_code"), type_="unique"
        )
        for column in (
            "correction_reason_code",
            "signed_at",
            "ready_for_signature_at",
            "clinical_started_at",
            "origin_partner_unit_id",
            "origin_label",
            "origin_type",
            "public_code",
        ):
            batch_op.drop_column(column)
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=24),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
        batch_op.create_check_constraint(
            op.f("ck_report_documents_status_valido"),
            "status IN ('rascunho', 'em_revisao', 'finalizado')",
        )

    bind.execute(
        sa.text(
            """
            UPDATE report_signatures
            SET provider=NULL
            WHERE provider='unconfigured'
              AND status='assinatura_pendente'
              AND external_reference IS NULL
              AND completed_at IS NULL
              AND verification_metadata IS NULL
            """
        )
    )
    op.drop_index(
        "uq_physician_profiles_active_crm_uf",
        table_name="physician_profiles",
    )
    op.drop_index(
        op.f("ix_physician_profiles_user_id"),
        table_name="physician_profiles",
    )
    op.drop_table("physician_profiles")
    bind.execute(
        sa.text(
            """
            DELETE FROM user_roles
            WHERE role_id = (SELECT id FROM roles WHERE name='medico')
            """
        )
    )
    bind.execute(sa.text("DELETE FROM roles WHERE name='medico'"))
    bind.execute(sa.text("DELETE FROM code_sequences WHERE prefix='LAU'"))
