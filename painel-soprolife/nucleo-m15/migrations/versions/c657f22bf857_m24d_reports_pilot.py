"""m24d_reports_pilot

Aditiva sobre M24C, para o piloto controlado de laudos:
- referência técnica segura de verificação profissional
  (physician_profiles.verification_reference), exigida junto com
  verification_status='verified';
- bloqueio de autoverificação a nível de banco
  (verified_by_user_id != user_id);
- novo par motivo/evento para recuperação admin-only de laudo preso em
  elaboração clínica quando o médico atribuído fica indisponível DEPOIS do
  primeiro rascunho ("physician_unavailable_after_draft" /
  "recovered_after_draft");
- rodapé PILOTO INTERNO dedicado (paralelo ao rodapé TESTE existente),
  nunca aprovado para produção.

Nenhuma linha legada é reescrita: perfis já verificados sem referência
técnica recebem backfill determinístico ("LEGADO_PRE_M24D") ANTES da nova
CHECK entrar em vigor, preservando o estado clínico anterior (mesmo padrão
já usado para report_templates/report_documents em M24C).

Revision ID: c657f22bf857
Revises: 4c9e2f7a6b31
Create Date: 2026-07-27 13:57:53.729999

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c657f22bf857"
down_revision = "4c9e2f7a6b31"
branch_labels = None
depends_on = None

ASSIGNMENT_REASON_VALUES = (
    "initial_assignment",
    "assignment_correction",
    "physician_unavailable",
    "profile_suspended",
    "operational_redistribution",
    "corrective_document",
    "physician_unavailable_after_draft",
)
ASSIGNMENT_EVENT_VALUES = (
    "assigned",
    "reassigned",
    "corrective_assigned",
    "recovered_after_draft",
)
LEGACY_VERIFICATION_REFERENCE = "LEGADO_PRE_M24D"

PILOT_FOOTER_ID = "24c00000-0000-4000-8000-000000000101"
PILOT_FOOTER_CODE = "PILOTO_INTERNO_NAO_ASSINADO"
PILOT_FOOTER_BODY = """SoproLife Diagnósticos e Soluções em Saúde
Laudo de espirometria — piloto interno controlado
Médico: {physician_name}
CRM/{crm_state}: {crm_number}
RQE: {rqe}
Exame: {exam_code}
Origem: {origin}
Emissão: {issued_at}
Laudo/versão: {report_code}/v{version_number}
Estado da assinatura: NÃO ASSINADO — PREPARAÇÃO PENDENTE
PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE"""


def upgrade() -> None:
    bind = op.get_bind()

    # Bloco 1: adiciona a coluna nullable (sem server_default — nada a
    # remover depois, então não há o risco F1 de recriar a tabela com
    # NOT NULL sem default no mesmo bloco).
    with op.batch_alter_table("physician_profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("verification_reference", sa.String(length=64), nullable=True)
        )

    # Backfill determinístico ANTES de apertar a CHECK: qualquer perfil
    # legado já verificado (M24C, sem referência) recebe um marcador
    # técnico fixo em vez de ficar impossível de migrar.
    bind.execute(
        sa.text(
            """
            UPDATE physician_profiles
            SET verification_reference = :ref
            WHERE verification_status = 'verified'
              AND verification_reference IS NULL
            """
        ),
        {"ref": LEGACY_VERIFICATION_REFERENCE},
    )

    # Bloco 2: substitui a CHECK de evidência de verificação (agora exige
    # referência) e acrescenta o bloqueio de autoverificação.
    with op.batch_alter_table("physician_profiles", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_physician_profiles_verification_evidence_complete"),
            type_="check",
        )
        batch_op.create_check_constraint(
            op.f("ck_physician_profiles_verification_evidence_complete"),
            "("
            "verification_status = 'verified' AND "
            "verified_at IS NOT NULL AND verified_by_user_id IS NOT NULL AND "
            "verification_reference IS NOT NULL AND "
            "length(trim(verification_reference)) >= 4"
            ") OR ("
            "verification_status <> 'verified' AND "
            "verified_at IS NULL AND verified_by_user_id IS NULL AND "
            "verification_reference IS NULL"
            ")",
        )
        batch_op.create_check_constraint(
            op.f("ck_physician_profiles_verification_not_self"),
            "verified_by_user_id IS NULL OR verified_by_user_id <> user_id",
        )

    # Motivo/evento novos: apenas alargam o conjunto permitido — linhas
    # legadas já satisfazem a lista ampliada, sem necessidade de backfill.
    with op.batch_alter_table("report_assignments", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_report_assignments_reason_code_valid"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_report_assignments_reason_code_valid"),
            f"reason_code IN {ASSIGNMENT_REASON_VALUES!r}",
        )

    with op.batch_alter_table("report_assignment_events", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_report_assignment_events_event_type_valid"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_report_assignment_events_event_type_valid"),
            f"event_type IN {ASSIGNMENT_EVENT_VALUES!r}",
        )
        batch_op.drop_constraint(op.f("ck_report_assignment_events_reason_code_valid"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_report_assignment_events_reason_code_valid"),
            f"reason_code IN {ASSIGNMENT_REASON_VALUES!r}",
        )

    # Rodapé PILOTO INTERNO — paralelo ao rodapé TESTE, nunca aprovado para
    # produção; o aviso exato fica congelado em cada snapshot de versão.
    # Checagem de existência em Python (não SQL "WHERE NOT EXISTS"): em
    # PostgreSQL, repetir o mesmo bind parameter em posições de tipo
    # diferente (lista SELECT vs. comparação WHERE) causa
    # "AmbiguousParameter" — o mesmo `:id` seria inferido como `text` num
    # lugar e `character varying` no outro.
    already_seeded = bind.execute(
        sa.text("SELECT 1 FROM report_footer_templates WHERE id = :id"),
        {"id": PILOT_FOOTER_ID},
    ).first()
    if already_seeded is None:
        bind.execute(
            sa.text(
                """
                INSERT INTO report_footer_templates (
                    id, code, version, body_template, status,
                    production_approved, active, created_at, updated_at
                ) VALUES (
                    :id, :code, 1, :body, 'test', false, true,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": PILOT_FOOTER_ID,
                "code": PILOT_FOOTER_CODE,
                "body": PILOT_FOOTER_BODY,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()

    existing = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM physician_profiles "
            "WHERE verification_reference IS NOT NULL "
            "AND verification_reference <> :legacy"
        ),
        {"legacy": LEGACY_VERIFICATION_REFERENCE},
    ).scalar_one()
    if existing:
        raise RuntimeError(
            "Downgrade M24D recusado: existem referências de verificação "
            f"reais que seriam perdidas (physician_profiles={existing})."
        )

    # O rodapé PILOTO INTERNO NÃO é removido: report_footer_templates é
    # append-only (trigger PostgreSQL m24c_reject_immutable_evidence recusa
    # DELETE/UPDATE, igual ao rodapé TESTE do M24C). A linha permanece como
    # evidência mesmo após o downgrade deste passo; só é destruída se um
    # downgrade mais profundo derrubar a tabela inteira (M24C), o que já
    # falha fechado sempre que existir qualquer physician_profile.

    with op.batch_alter_table("report_assignment_events", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_report_assignment_events_reason_code_valid"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_report_assignment_events_reason_code_valid"),
            "reason_code IN "
            "('initial_assignment', 'assignment_correction', "
            "'physician_unavailable', 'profile_suspended', "
            "'operational_redistribution', 'corrective_document')",
        )
        batch_op.drop_constraint(op.f("ck_report_assignment_events_event_type_valid"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_report_assignment_events_event_type_valid"),
            "event_type IN ('assigned', 'reassigned', 'corrective_assigned')",
        )

    with op.batch_alter_table("report_assignments", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_report_assignments_reason_code_valid"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_report_assignments_reason_code_valid"),
            "reason_code IN "
            "('initial_assignment', 'assignment_correction', "
            "'physician_unavailable', 'profile_suspended', "
            "'operational_redistribution', 'corrective_document')",
        )

    with op.batch_alter_table("physician_profiles", schema=None) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_physician_profiles_verification_not_self"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_physician_profiles_verification_evidence_complete"),
            type_="check",
        )
        batch_op.create_check_constraint(
            op.f("ck_physician_profiles_verification_evidence_complete"),
            "("
            "verification_status = 'verified' AND "
            "verified_at IS NOT NULL AND verified_by_user_id IS NOT NULL"
            ") OR ("
            "verification_status <> 'verified' AND "
            "verified_at IS NULL AND verified_by_user_id IS NULL"
            ")",
        )

    with op.batch_alter_table("physician_profiles", schema=None) as batch_op:
        batch_op.drop_column("verification_reference")
