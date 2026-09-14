"""Structured service-LOCATION resolution for the national DPS ``cLocPrestacao``
field (``TCLocPrest``/``serv/locPrest``, ``tiposComplexos_v1.01.xsd`` linha 1314).

Distinct from ISS INCIDENCE (``cLocIncid``, ``TCInfNFSe``, linha 35 do mesmo
XSD): the schema's own documentation states the incidence municipality "é
determinado automaticamente pelo sistema, conforme regras do aspecto espacial
da lei complementar federal (LC 116/03)" and is returned by the government
ONLY inside the issued NFS-e — never something this DPS builder submits. This
module therefore only ever resolves and validates SERVICE LOCATION (where the
exam was physically performed), never incidence.

The value comes ONLY from ``SpirometryExam.municipio_atendimento_ibge`` — a
structured, exam-level column (M31) — never inferred from a partner/clinic
name, a patient address, or any free-text field (``local_atendimento``,
``observacao``). Mirrors ``service_description.py``'s fail-closed contract:
missing or unsupported raises rather than guessing or defaulting to Rio.
"""
from __future__ import annotations

from .identifiers import InvalidIdentifierError, assert_municipio_ibge

# Only municipalities this foundation has evidence for (mission M31, section
# "CRITICAL GOLDEN DIFFERENCE" — two real, manually-issued DANFSe examples).
# Adding a new one requires the same kind of confirmed evidence (a real
# document or an official IBGE source), never a guess — see the M31 report.
SUPPORTED_SERVICE_MUNICIPALITIES: dict[str, str] = {
    "3304557": "Rio de Janeiro/RJ",
    "3303302": "Niterói/RJ",  # confirmed via IBGE (cidades.ibge.gov.br/brasil/rj/niteroi)
}


class ServiceLocationUndetermined(ValueError):
    """Raised instead of guessing when the exam has no structured service
    municipality on file. Never carries patient-identifying data."""


class ServiceLocationUnsupported(ValueError):
    """Raised when the structured municipality is syntactically valid but
    not one this foundation has evidence/support for yet. Fail closed rather
    than submit an unverified ``cLocPrestacao`` to the government."""

    def __init__(self, municipio_ibge: str):
        self.municipio_ibge = municipio_ibge
        super().__init__(
            f"Município de prestação {municipio_ibge!r} não é suportado por "
            "esta fundação (fail closed, nunca adivinhado)."
        )


def spirometry_service_municipio_ibge(municipio_atendimento_ibge: str | None) -> str:
    """Return the validated, supported IBGE code for ``cLocPrestacao``.

    ``municipio_atendimento_ibge`` must be the exact structured value from
    ``SpirometryExam.municipio_atendimento_ibge`` — never a partner address,
    patient address, or any derived/free-text value.
    """
    if not municipio_atendimento_ibge:
        raise ServiceLocationUndetermined(
            "exam.municipio_atendimento_ibge não está definido; o local da "
            "prestação do serviço não pode ser inferido (fail closed)."
        )
    try:
        code = assert_municipio_ibge(municipio_atendimento_ibge)
    except InvalidIdentifierError as exc:
        raise ServiceLocationUndetermined(str(exc)) from exc
    if code not in SUPPORTED_SERVICE_MUNICIPALITIES:
        raise ServiceLocationUnsupported(code)
    return code
