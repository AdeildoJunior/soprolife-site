"""m25_2_native_report_release

Aditiva sobre M24D. Introduz o laudo médico PRÓPRIO da SoproLife, gerado
nativamente e separado do PDF técnico da MIR:

- dados estruturados do local de realização em ``partner_units`` (o laudo
  nunca fixa endereço no template);
- campos demográficos/clínicos exigidos pelo documento em ``people`` e
  ``spirometry_exams``, todos opcionais e nunca inferidos;
- apresentação humana do CRM e especialidade em ``physician_profiles``;
- estado ``liberado`` + evidência de liberação institucional em
  ``report_documents`` (código de validação único);
- espécies de versão ``laudo_previa``/``laudo_liberado``/``laudo_adendo`` e
  os respectivos snapshots imutáveis em ``report_document_versions``;
- ``physician_signature_assets`` (referência técnica do PNG manuscrito, que
  vive somente na raiz privada, jamais no Git);
- ``report_addenda`` append-only.

Nenhum estado legado é reescrito: todas as colunas nascem nullable e as
CHECKs só ALARGAM os conjuntos permitidos. Nenhuma linha clínica é criada.
O único backfill é o endereço institucional de uma unidade parceira que já
exista — este marco NÃO cria parceiro, unidade, médico, paciente ou laudo.

Revision ID: a3f1d7c25e90
Revises: c657f22bf857
Create Date: 2026-08-04 09:40:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a3f1d7c25e90"
down_revision = "c657f22bf857"
branch_labels = None
depends_on = None


STATUS_VALUES = (
    "atribuido",
    "em_elaboracao",
    "assinatura_pendente",
    "assinado",
    "liberado",
    "rascunho",
    "em_revisao",
    "finalizado",
)
LEGACY_STATUS_VALUES = tuple(
    value for value in STATUS_VALUES if value != "liberado"
)
KIND_VALUES = (
    "original",
    "rascunho",
    "assinatura_pendente",
    "assinado",
    "finalizado",
    "laudo_previa",
    "laudo_liberado",
    "laudo_adendo",
)
LEGACY_KIND_VALUES = tuple(
    value
    for value in KIND_VALUES
    if value not in {"laudo_previa", "laudo_liberado", "laudo_adendo"}
)
SIGNATURE_STATUS_VALUES = (
    "assinatura_pendente",
    "assinada",
    "rejeitada",
    "liberada_institucional",
)
LEGACY_SIGNATURE_STATUS_VALUES = tuple(
    value
    for value in SIGNATURE_STATUS_VALUES
    if value != "liberada_institucional"
)

CLINICAL_STATE_M25 = (
    "("
    "status NOT IN ("
    "'atribuido', 'em_elaboracao', "
    "'assinatura_pendente', 'assinado', 'liberado'"
    ")"
    ") OR ("
    "status = 'liberado' AND "
    "clinical_started_at IS NOT NULL AND "
    "ready_for_signature_at IS NOT NULL AND "
    "released_at IS NOT NULL AND "
    "released_by_user_id IS NOT NULL AND "
    "released_physician_profile_id IS NOT NULL AND "
    "validation_code IS NOT NULL AND "
    "signed_at IS NULL AND "
    "signature_status = 'liberada_institucional'"
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
    ")"
)
CLINICAL_STATE_M24 = (
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
    ")"
)

# Endereço institucional público da unidade Pastore Ipanema. É dado da
# CLÍNICA (não do paciente) e só é aplicado a uma unidade JÁ CADASTRADA —
# a migration nunca cria parceiro ou unidade.
PASTORE_IPANEMA = {
    "logradouro": "Rua Teixeira de Melo, 54",
    "bairro": "Ipanema",
    "cidade": "Rio de Janeiro",
    "uf": "RJ",
    "telefone_central": "(21) 2508-9001",
}


def _dialect() -> str:
    return op.get_bind().dialect.name


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------ local estruturado
    with op.batch_alter_table("partner_units", schema=None) as batch_op:
        batch_op.add_column(sa.Column("logradouro", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("uf", sa.String(length=2), nullable=True))
        batch_op.add_column(sa.Column("cep", sa.String(length=12), nullable=True))
        batch_op.add_column(
            sa.Column("telefone_central", sa.String(length=40), nullable=True)
        )

    # ------------------------------------- dados exigidos pelo documento
    with op.batch_alter_table("people", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sexo", sa.String(length=20), nullable=True))

    with op.batch_alter_table("spirometry_exams", schema=None) as batch_op:
        batch_op.add_column(sa.Column("hora_exame", sa.String(length=5), nullable=True))
        batch_op.add_column(sa.Column("indicacao_clinica", sa.Text(), nullable=True))

    with op.batch_alter_table("physician_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("crm_display", sa.String(length=30), nullable=True))
        batch_op.add_column(
            sa.Column("especialidade", sa.String(length=120), nullable=True)
        )
        batch_op.create_check_constraint(
            op.f("ck_physician_profiles_crm_display_present"),
            "crm_display IS NULL OR length(trim(crm_display)) >= 1",
        )
        batch_op.create_check_constraint(
            op.f("ck_physician_profiles_especialidade_present"),
            "especialidade IS NULL OR length(trim(especialidade)) >= 2",
        )

    # ------------------------------------------ liberação institucional
    with op.batch_alter_table("report_documents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("released_by_user_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "released_physician_profile_id", sa.String(length=36), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column("validation_code", sa.String(length=24), nullable=True)
        )
        batch_op.create_foreign_key(
            op.f("fk_report_documents_released_by_user_id_users"),
            "users",
            ["released_by_user_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            op.f(
                "fk_report_documents_released_physician_profile_id_"
                "physician_profiles"
            ),
            "physician_profiles",
            ["released_physician_profile_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            op.f("uq_report_documents_validation_code"), ["validation_code"]
        )
        batch_op.drop_constraint(
            op.f("ck_report_documents_status_valido"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_report_documents_status_valido"),
            f"status IN {STATUS_VALUES!r}",
        )
        batch_op.drop_constraint(
            op.f("ck_report_documents_clinical_state_coherent"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_report_documents_clinical_state_coherent"),
            CLINICAL_STATE_M25,
        )
        batch_op.create_check_constraint(
            op.f("ck_report_documents_release_evidence_coherent"),
            "status = 'liberado' OR ("
            "released_at IS NULL AND "
            "released_by_user_id IS NULL AND "
            "released_physician_profile_id IS NULL AND "
            "validation_code IS NULL"
            ")",
        )

    # ------------------------------------ snapshots do laudo nativo
    with op.batch_alter_table("report_document_versions", schema=None) as batch_op:
        for column in (
            sa.Column("conclusion_code_snapshot", sa.String(length=40), nullable=True),
            sa.Column("conclusion_text_snapshot", sa.Text(), nullable=True),
            sa.Column(
                "bronchodilator_code_snapshot", sa.String(length=40), nullable=True
            ),
            sa.Column("bronchodilator_text_snapshot", sa.Text(), nullable=True),
            sa.Column("observations_snapshot", sa.Text(), nullable=True),
            sa.Column("exam_has_post_bd_snapshot", sa.Boolean(), nullable=True),
            sa.Column("location_name_snapshot", sa.String(length=240), nullable=True),
            sa.Column(
                "location_address_snapshot", sa.String(length=300), nullable=True
            ),
            sa.Column(
                "location_contact_snapshot", sa.String(length=120), nullable=True
            ),
            sa.Column(
                "location_partner_unit_id_snapshot",
                sa.String(length=36),
                nullable=True,
            ),
            sa.Column("location_source_snapshot", sa.String(length=40), nullable=True),
            sa.Column("validation_code_snapshot", sa.String(length=24), nullable=True),
            sa.Column(
                "released_at_snapshot", sa.DateTime(timezone=True), nullable=True
            ),
            sa.Column(
                "signature_asset_id_snapshot", sa.String(length=36), nullable=True
            ),
            sa.Column(
                "signature_asset_sha256_snapshot", sa.String(length=64), nullable=True
            ),
            sa.Column("addendum_sequence", sa.Integer(), nullable=True),
        ):
            batch_op.add_column(column)

        batch_op.drop_constraint(
            op.f("ck_report_document_versions_kind_valido"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_kind_valido"),
            f"kind IN {KIND_VALUES!r}",
        )
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_conclusion_snapshot_complete"),
            "("
            "conclusion_code_snapshot IS NULL AND "
            "conclusion_text_snapshot IS NULL"
            ") OR ("
            "conclusion_code_snapshot IS NOT NULL AND "
            "conclusion_text_snapshot IS NOT NULL"
            ")",
        )
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_bronchodilator_snapshot_complete"),
            "("
            "bronchodilator_code_snapshot IS NULL AND "
            "bronchodilator_text_snapshot IS NULL"
            ") OR ("
            "bronchodilator_code_snapshot IS NOT NULL AND "
            "bronchodilator_text_snapshot IS NOT NULL"
            ")",
        )
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_location_snapshot_complete"),
            "("
            "location_name_snapshot IS NULL AND "
            "location_source_snapshot IS NULL"
            ") OR ("
            "location_name_snapshot IS NOT NULL AND "
            "location_source_snapshot IS NOT NULL"
            ")",
        )
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_signature_asset_snapshot_complete"),
            "("
            "signature_asset_id_snapshot IS NULL AND "
            "signature_asset_sha256_snapshot IS NULL"
            ") OR ("
            "signature_asset_id_snapshot IS NOT NULL AND "
            "signature_asset_sha256_snapshot IS NOT NULL"
            ")",
        )
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_addendum_sequence_positive"),
            "addendum_sequence IS NULL OR addendum_sequence > 0",
        )
        batch_op.create_check_constraint(
            op.f(
                "ck_report_document_versions_"
                "release_snapshot_only_on_released_kind"
            ),
            "("
            "kind = 'laudo_liberado' OR kind = 'laudo_adendo'"
            ") OR ("
            "released_at_snapshot IS NULL AND "
            "validation_code_snapshot IS NULL"
            ")",
        )

    with op.batch_alter_table("report_signatures", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_report_signatures_status_valido"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_report_signatures_status_valido"),
            f"status IN {SIGNATURE_STATUS_VALUES!r}",
        )

    # -------------------------------------------- ativo de assinatura
    op.create_table(
        "physician_signature_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("physician_profile_id", sa.String(length=36), nullable=False),
        sa.Column("storage_path", sa.String(length=300), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=40), nullable=False),
        sa.Column("image_width", sa.Integer(), nullable=False),
        sa.Column("image_height", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "size_bytes > 0",
            name=op.f("ck_physician_signature_assets_size_bytes_positivo"),
        ),
        sa.CheckConstraint(
            "image_width > 0",
            name=op.f("ck_physician_signature_assets_image_width_positivo"),
        ),
        sa.CheckConstraint(
            "image_height > 0",
            name=op.f("ck_physician_signature_assets_image_height_positivo"),
        ),
        sa.CheckConstraint(
            "mime_type = 'image/png'",
            name=op.f("ck_physician_signature_assets_mime_type_valido"),
        ),
        sa.CheckConstraint(
            "(active = true AND revoked_at IS NULL AND revoked_by_user_id IS NULL) "
            "OR (active = false AND revoked_at IS NOT NULL AND "
            "revoked_by_user_id IS NOT NULL)",
            name=op.f("ck_physician_signature_assets_revocation_coherent"),
        ),
        sa.ForeignKeyConstraint(
            ["physician_profile_id"],
            ["physician_profiles.id"],
            name=op.f(
                "fk_physician_signature_assets_physician_profile_id_"
                "physician_profiles"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_physician_signature_assets_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name=op.f("fk_physician_signature_assets_revoked_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_physician_signature_assets")),
    )
    op.create_index(
        op.f("ix_physician_signature_assets_physician_profile_id"),
        "physician_signature_assets",
        ["physician_profile_id"],
    )
    op.create_index(
        "uq_signature_assets_one_active_per_profile",
        "physician_signature_assets",
        ["physician_profile_id"],
        unique=True,
        sqlite_where=sa.text("active = 1"),
        postgresql_where=sa.text("active"),
    )

    # ------------------------------------------------------- adendos
    op.create_table(
        "report_addenda",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_document_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column("physician_profile_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "report_document_version_id", sa.String(length=36), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sequence > 0", name=op.f("ck_report_addenda_sequence_positive")
        ),
        sa.CheckConstraint(
            "length(trim(body_text)) >= 3",
            name=op.f("ck_report_addenda_body_text_present"),
        ),
        sa.ForeignKeyConstraint(
            ["report_document_id"],
            ["report_documents.id"],
            name=op.f("fk_report_addenda_report_document_id_report_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["physician_profile_id"],
            ["physician_profiles.id"],
            name=op.f(
                "fk_report_addenda_physician_profile_id_physician_profiles"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_report_addenda_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["report_document_version_id"],
            ["report_document_versions.id"],
            name=op.f(
                "fk_report_addenda_report_document_version_id_"
                "report_document_versions"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_addenda")),
        sa.UniqueConstraint(
            "report_document_id",
            "sequence",
            name=op.f("uq_report_addendum_sequence"),
        ),
        sa.UniqueConstraint(
            "report_document_version_id",
            name=op.f("uq_report_addenda_report_document_version_id"),
        ),
    )
    op.create_index(
        op.f("ix_report_addenda_report_document_id"),
        "report_addenda",
        ["report_document_id"],
    )

    if _dialect() == "postgresql":
        # Mesma proteção append-only já usada pelas demais evidências M24C.
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_report_addenda_m25_2_immutable
                BEFORE UPDATE OR DELETE ON report_addenda
                FOR EACH ROW EXECUTE FUNCTION m24c_reject_immutable_evidence()
                """
            )
        )

    # ------------------------------- backfill do endereço institucional
    # Apenas para uma unidade Pastore Ipanema JÁ existente e ainda sem
    # logradouro. Nenhum cadastro é criado e nada preenchido é sobrescrito.
    bind.execute(
        sa.text(
            """
            UPDATE partner_units
            SET logradouro = :logradouro,
                bairro = COALESCE(bairro, :bairro),
                cidade = COALESCE(cidade, :cidade),
                uf = :uf,
                telefone_central = :telefone_central
            WHERE logradouro IS NULL
              AND lower(nome) LIKE '%ipanema%'
              AND partner_id IN (
                  SELECT id FROM partners
                  WHERE lower(trim(nome)) = 'pastore'
              )
            """
        ),
        PASTORE_IPANEMA,
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Fail-closed: nada de M25.2 pode ser descartado silenciosamente.
    blockers = {
        "laudos_liberados": (
            "SELECT COUNT(*) FROM report_documents WHERE status = 'liberado'"
        ),
        "versoes_de_laudo_nativo": (
            "SELECT COUNT(*) FROM report_document_versions "
            "WHERE kind IN ('laudo_previa', 'laudo_liberado', 'laudo_adendo')"
        ),
        "adendos": "SELECT COUNT(*) FROM report_addenda",
        "ativos_de_assinatura": (
            "SELECT COUNT(*) FROM physician_signature_assets"
        ),
        "assinaturas_institucionais": (
            "SELECT COUNT(*) FROM report_signatures "
            "WHERE status = 'liberada_institucional'"
        ),
    }
    found = {
        label: bind.execute(sa.text(query)).scalar_one()
        for label, query in blockers.items()
    }
    pending = {label: count for label, count in found.items() if count}
    if pending:
        raise RuntimeError(
            "Downgrade M25.2 recusado: existe evidência clínica que seria "
            f"perdida ({pending})."
        )

    if _dialect() == "postgresql":
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS trg_report_addenda_m25_2_immutable "
                "ON report_addenda"
            )
        )

    op.drop_index(
        op.f("ix_report_addenda_report_document_id"), table_name="report_addenda"
    )
    op.drop_table("report_addenda")

    op.drop_index(
        "uq_signature_assets_one_active_per_profile",
        table_name="physician_signature_assets",
    )
    op.drop_index(
        op.f("ix_physician_signature_assets_physician_profile_id"),
        table_name="physician_signature_assets",
    )
    op.drop_table("physician_signature_assets")

    with op.batch_alter_table("report_signatures", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_report_signatures_status_valido"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_report_signatures_status_valido"),
            f"status IN {LEGACY_SIGNATURE_STATUS_VALUES!r}",
        )

    with op.batch_alter_table("report_document_versions", schema=None) as batch_op:
        for name in (
            "release_snapshot_only_on_released_kind",
            "addendum_sequence_positive",
            "signature_asset_snapshot_complete",
            "location_snapshot_complete",
            "bronchodilator_snapshot_complete",
            "conclusion_snapshot_complete",
        ):
            batch_op.drop_constraint(
                op.f(f"ck_report_document_versions_{name}"), type_="check"
            )
        batch_op.drop_constraint(
            op.f("ck_report_document_versions_kind_valido"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_report_document_versions_kind_valido"),
            f"kind IN {LEGACY_KIND_VALUES!r}",
        )
        for column in (
            "addendum_sequence",
            "signature_asset_sha256_snapshot",
            "signature_asset_id_snapshot",
            "released_at_snapshot",
            "validation_code_snapshot",
            "location_source_snapshot",
            "location_partner_unit_id_snapshot",
            "location_contact_snapshot",
            "location_address_snapshot",
            "location_name_snapshot",
            "exam_has_post_bd_snapshot",
            "observations_snapshot",
            "bronchodilator_text_snapshot",
            "bronchodilator_code_snapshot",
            "conclusion_text_snapshot",
            "conclusion_code_snapshot",
        ):
            batch_op.drop_column(column)

    with op.batch_alter_table("report_documents", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_report_documents_release_evidence_coherent"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_report_documents_clinical_state_coherent"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_report_documents_clinical_state_coherent"),
            CLINICAL_STATE_M24,
        )
        batch_op.drop_constraint(
            op.f("ck_report_documents_status_valido"), type_="check"
        )
        batch_op.create_check_constraint(
            op.f("ck_report_documents_status_valido"),
            f"status IN {LEGACY_STATUS_VALUES!r}",
        )
        batch_op.drop_constraint(
            op.f("uq_report_documents_validation_code"), type_="unique"
        )
        batch_op.drop_constraint(
            op.f(
                "fk_report_documents_released_physician_profile_id_"
                "physician_profiles"
            ),
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            op.f("fk_report_documents_released_by_user_id_users"),
            type_="foreignkey",
        )
        batch_op.drop_column("validation_code")
        batch_op.drop_column("released_physician_profile_id")
        batch_op.drop_column("released_by_user_id")
        batch_op.drop_column("released_at")

    with op.batch_alter_table("physician_profiles", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_physician_profiles_especialidade_present"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_physician_profiles_crm_display_present"), type_="check"
        )
        batch_op.drop_column("especialidade")
        batch_op.drop_column("crm_display")

    with op.batch_alter_table("spirometry_exams", schema=None) as batch_op:
        batch_op.drop_column("indicacao_clinica")
        batch_op.drop_column("hora_exame")

    with op.batch_alter_table("people", schema=None) as batch_op:
        batch_op.drop_column("sexo")

    with op.batch_alter_table("partner_units", schema=None) as batch_op:
        batch_op.drop_column("telefone_central")
        batch_op.drop_column("cep")
        batch_op.drop_column("uf")
        batch_op.drop_column("logradouro")
