"""Catálogo estrutural M24C — somente placeholders explicitamente inseguros.

As migrations são a fonte de produção. ``ensure_m24c_catalog`` mantém bancos
SQLite criados diretamente por ``Base.metadata.create_all`` equivalentes nos
testes e no desenvolvimento, sem alterar uma revisão já existente.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ReportFooterTemplate, ReportTemplate

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
PROVISIONAL_CODES = frozenset(item[1] for item in PROVISIONAL_TEMPLATES)

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


def ensure_m24c_catalog(db: Session) -> None:
    """Insere somente seeds ausentes; divergência de id/código falha fechado."""

    existing_templates = {
        template.codigo: template
        for template in db.execute(
            select(ReportTemplate).where(
                ReportTemplate.codigo.in_(PROVISIONAL_CODES),
                ReportTemplate.versao == 1,
            )
        ).scalars()
    }
    for template_id, code, title in PROVISIONAL_TEMPLATES:
        existing = existing_templates.get(code)
        if existing is not None:
            if (
                existing.id != template_id
                or existing.texto_completo != PROVISIONAL_WARNING
                or existing.status != "draft"
                or existing.clinically_approved
            ):
                raise RuntimeError("Catálogo provisório M24C divergente.")
            continue
        db.add(
            ReportTemplate(
                id=template_id,
                codigo=code,
                titulo=title,
                texto_tooltip="PROVISÓRIO — bloqueado para uso clínico",
                texto_completo=PROVISIONAL_WARNING,
                versao=1,
                ativo=True,
                status="draft",
                clinically_approved=False,
            )
        )

    footer = db.get(ReportFooterTemplate, TEST_FOOTER_ID)
    if footer is None:
        db.add(
            ReportFooterTemplate(
                id=TEST_FOOTER_ID,
                code=TEST_FOOTER_CODE,
                version=1,
                body_template=TEST_FOOTER_BODY,
                status="test",
                production_approved=False,
                active=True,
            )
        )
    elif (
        footer.code != TEST_FOOTER_CODE
        or footer.version != 1
        or footer.body_template != TEST_FOOTER_BODY
        or footer.status != "test"
        or footer.production_approved
    ):
        raise RuntimeError("Rodapé TESTE M24C divergente.")
    db.flush()
