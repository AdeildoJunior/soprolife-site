"""Textual identifiers for the National NFS-e restricted layout (Annex I v1.01).

Every identifier here is a TEXTUAL type in the official schema, never numeric.
CNPJ is validated against ``TSCNPJ`` (``[0-9A-Z]{14}``, restricted schema,
``tiposSimples_v1.01.xsd``): the national platform accepted alphanumeric CNPJ
as of August 2026 (see OFFICIAL_NORMATIVE_BRIEF.md), so no regex here may be
narrowed to digits-only or ever pass through ``int()``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CNPJ_PATTERN = re.compile(r"^[0-9A-Z]{14}$")
CPF_PATTERN = re.compile(r"^[0-9]{11}$")
MUNICIPIO_IBGE_PATTERN = re.compile(r"^[0-9]{7}$")
# TSIdDPS (tiposSimples_v1.01.xsd): "DPS" + cMun(7) + tipoInsc(1) + inscricao(14) + serie(5) + numero(15)
DPS_ID_PATTERN = re.compile(r"^DPS[0-9]{7}(1[0-9]{14}|2[0-9A-Z]{14})[0-9]{20}$")


class InvalidIdentifierError(ValueError):
    """A textual fiscal identifier failed its official-schema pattern."""


def assert_cnpj(value: str) -> str:
    """Validate transport/storage shape only (syntactic), never business rules."""
    if not isinstance(value, str) or not CNPJ_PATTERN.fullmatch(value):
        raise InvalidIdentifierError("CNPJ deve ter 14 caracteres alfanuméricos (TSCNPJ).")
    return value


def assert_cpf(value: str) -> str:
    if not isinstance(value, str) or not CPF_PATTERN.fullmatch(value):
        raise InvalidIdentifierError("CPF deve ter 11 dígitos (TSCPF).")
    return value


def assert_municipio_ibge(value: str) -> str:
    if not isinstance(value, str) or not MUNICIPIO_IBGE_PATTERN.fullmatch(value):
        raise InvalidIdentifierError("Código de município IBGE deve ter 7 dígitos.")
    return value


@dataclass(frozen=True)
class DpsIdComponents:
    """Every component of ``TSIdDPS``, kept explicit instead of a single blob.

    ``inscricao_federal`` is the ISSUER's CNPJ (14 chars) or CPF (11 digits,
    left-padded with zeros to 14 per the schema note). Both remain textual.
    """
    codigo_municipio: str
    tipo_inscricao_federal: int  # 1 = CPF, 2 = CNPJ (schema note on TSIdDPS)
    inscricao_federal: str
    serie_dps: str
    numero_dps: str

    def __post_init__(self):
        assert_municipio_ibge(self.codigo_municipio)
        if self.tipo_inscricao_federal not in (1, 2):
            raise InvalidIdentifierError("Tipo de inscrição federal deve ser 1 (CPF) ou 2 (CNPJ).")
        if self.tipo_inscricao_federal == 1:
            assert_cpf(self.inscricao_federal)  # exactly 11 digits; padding happens only in build_dps_id
        else:
            assert_cnpj(self.inscricao_federal)
        if not re.fullmatch(r"[0-9]{5}", self.serie_dps):
            raise InvalidIdentifierError("Série da DPS deve ter 5 dígitos.")
        if not re.fullmatch(r"[0-9]{15}", self.numero_dps):
            raise InvalidIdentifierError("Número da DPS deve ter 15 dígitos.")

    @property
    def inscricao_federal_padded(self) -> str:
        """14-char field as TSIdDPS requires: CNPJ as-is, CPF left-padded with zeros."""
        if self.tipo_inscricao_federal == 1:
            return self.inscricao_federal.rjust(14, "0")
        return self.inscricao_federal


def build_dps_id(components: DpsIdComponents) -> str:
    """Deterministic ``Id`` attribute for ``infDPS``, per ``TSIdDPS``."""
    dps_id = (
        "DPS"
        + components.codigo_municipio
        + str(components.tipo_inscricao_federal)
        + components.inscricao_federal_padded
        + components.serie_dps
        + components.numero_dps
    )
    if not DPS_ID_PATTERN.fullmatch(dps_id):
        raise InvalidIdentifierError(f"Id de DPS construído não confere com TSIdDPS: {dps_id!r}")
    return dps_id
