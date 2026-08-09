"""M25.2 — local de realização do exame como dado estruturado.

O laudo NUNCA fixa um endereço no template. O local sai sempre da unidade
parceira vinculada ao exame (ou ao documento) e, quando não há unidade,
de um rótulo derivado da origem controlada — porque haverá exames
domiciliares e em outras clínicas.

Ordem de resolução, sempre fail-closed:

1. ``report_documents.origin_partner_unit_id`` — unidade escolhida
   explicitamente pela operação ao receber/atribuir o exame;
2. ``spirometry_exams.partner_unit_id`` — unidade vinculada ao exame;
3. rótulo derivado da origem controlada (domicílio, coworking, empresa…).

Nenhuma etapa inventa endereço: se a unidade não tiver logradouro
cadastrado, o laudo sai com o nome da unidade e sem linha de endereço.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import Partner, PartnerUnit, ReportDocument, SpirometryExam

# Rótulos usados quando o exame não está vinculado a nenhuma unidade
# cadastrada. São descrições de modalidade, não endereços.
ORIGIN_FALLBACK_NAMES = {
    "pastore": "Clínica Pastore",
    "coworking": "Espaço de atendimento SoproLife",
    "residencial": "Atendimento domiciliar",
    "clinica_parceira": "Clínica parceira",
    "empresa_pcmso": "Atendimento em empresa (PCMSO)",
    "outro": "Local de atendimento",
}

SOURCE_DOCUMENT_UNIT = "documento_unidade"
SOURCE_EXAM_UNIT = "exame_unidade"
SOURCE_ORIGIN_FALLBACK = "origem_sem_unidade"


@dataclass(frozen=True)
class ReportLocation:
    """Local congelado no laudo. Apenas dados institucionais da clínica."""

    name: str
    address_line: str | None
    contact_line: str | None
    partner_unit_id: str | None
    source: str

    def as_snapshot(self) -> dict:
        return {
            "location_name_snapshot": self.name,
            "location_address_snapshot": self.address_line,
            "location_contact_snapshot": self.contact_line,
            "location_partner_unit_id_snapshot": self.partner_unit_id,
            "location_source_snapshot": self.source,
        }

    def as_payload(self) -> dict:
        return {
            "nome": self.name,
            "endereco": self.address_line,
            "contato": self.contact_line,
            "partner_unit_id": self.partner_unit_id,
            "origem_do_dado": self.source,
        }


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _unit_display_name(unit: PartnerUnit, partner: Partner | None) -> str:
    """Nome exibido: "Parceiro — Unidade" quando os dois existem."""

    unit_name = _clean(unit.nome) or "Unidade parceira"
    partner_name = _clean(partner.nome) if partner is not None else None
    if not partner_name:
        return unit_name
    if partner_name.casefold() in unit_name.casefold():
        return unit_name
    return f"{partner_name} — {unit_name}"


def _unit_address_line(unit: PartnerUnit) -> str | None:
    """Monta o endereço só com o que estiver realmente cadastrado."""

    street = _clean(getattr(unit, "logradouro", None))
    neighborhood = _clean(unit.bairro)
    city = _clean(unit.cidade)
    uf = _clean(getattr(unit, "uf", None))
    cep = _clean(getattr(unit, "cep", None))

    locality_parts = [part for part in (neighborhood, city) if part]
    locality = ", ".join(locality_parts)
    if locality and uf:
        locality = f"{locality} — {uf}"
    elif uf and not locality:
        locality = uf

    segments = [part for part in (street, locality) if part]
    if not segments:
        return None
    line = " — ".join(segments)
    if cep:
        line = f"{line} — CEP {cep}"
    return line


def _unit_contact_line(unit: PartnerUnit) -> str | None:
    phone = _clean(getattr(unit, "telefone_central", None))
    if not phone:
        return None
    return f"Central: {phone}"


def _from_unit(
    db: Session, unit: PartnerUnit, *, source: str
) -> ReportLocation:
    partner = db.get(Partner, unit.partner_id) if unit.partner_id else None
    return ReportLocation(
        name=_unit_display_name(unit, partner),
        address_line=_unit_address_line(unit),
        contact_line=_unit_contact_line(unit),
        partner_unit_id=unit.id,
        source=source,
    )


def resolve_exam_location_name(db: Session, exam: SpirometryExam) -> str:
    """Nome do local de um exame que ainda NÃO tem laudo.

    M25.15 — serve só ao localizador, para separar homônimos ("qual João da
    Silva? o da Pastore ou o do domicílio?"). Não é o local impresso: esse
    continua saindo de `resolve_report_location`, que só existe depois que a
    operação escolheu a unidade ao atribuir o laudo.

    Por isso aqui não há fallback por origem controlada — a origem é campo
    do DOCUMENTO e ainda não foi decidida. Sem unidade cadastrada, usa-se o
    texto operacional do exame; sem ele, um rótulo neutro. Nada inventado.
    """

    if exam.partner_unit_id:
        unit = db.get(PartnerUnit, exam.partner_unit_id)
        if unit is not None:
            return _from_unit(db, unit, source=SOURCE_EXAM_UNIT).name
    return _clean(exam.local_atendimento) or "Local não informado"


def resolve_report_location(
    db: Session, *, document: ReportDocument, exam: SpirometryExam
) -> ReportLocation:
    """Local estruturado do laudo — nunca um endereço fixo no template."""

    for unit_id, source in (
        (document.origin_partner_unit_id, SOURCE_DOCUMENT_UNIT),
        (exam.partner_unit_id, SOURCE_EXAM_UNIT),
    ):
        if not unit_id:
            continue
        unit = db.get(PartnerUnit, unit_id)
        if unit is not None:
            return _from_unit(db, unit, source=source)

    origin_type = (document.origin_type or "").strip().lower()
    name = ORIGIN_FALLBACK_NAMES.get(origin_type, "Local de atendimento")
    # `local_atendimento` do exame é texto operacional livre; entra apenas
    # como complemento do rótulo, nunca como endereço estruturado.
    detail = _clean(exam.local_atendimento)
    if detail and detail.casefold() != name.casefold():
        name = f"{name} — {detail}"
    return ReportLocation(
        name=name,
        address_line=None,
        contact_line=None,
        partner_unit_id=None,
        source=SOURCE_ORIGIN_FALLBACK,
    )
