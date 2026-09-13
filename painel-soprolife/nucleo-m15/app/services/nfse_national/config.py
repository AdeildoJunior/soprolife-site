"""Versioned, explicit configuration contract for the National NFS-e restricted layout.

Distinct from ``app.fiscal_schemas.TaxConfiguration`` (the abstract mock-era
policy): this contract names the CONCRETE official fields the restricted DPS
(Annex I v1.01-20260727) requires, so a missing field is reported by its real
name instead of a generic bucket. Nothing here is invented — every field maps
to one documented element/attribute in DPS_v1.01.xsd / tiposComplexos_v1.01.xsd,
cited in each docstring. No production tax setting is hard-coded: every value
must be supplied by the caller, and incompleteness fails closed via
``missing_fields()`` exactly like the M26 contract.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .identifiers import assert_cnpj, assert_municipio_ibge

# Single layout supported by this foundation. Recorded as evidence in
# OFFICIAL_SOURCES_USED.md: restricted documentation page (updated 2026-07),
# esquemas-nfse-rtc-prodrest-v1-01-20260727.zip. NT 009/RTC fields are
# deliberately NOT modeled: the RTC page stated its changes were not yet
# available in Produção Restrita as of the research date.
LayoutVersion = Literal["restricted-v1.01-20260727"]

# TCTribMunicipal/tribISSQN (tiposComplexos_v1.01.xsd)
TribISSQN = Literal[1, 2, 3, 4]
# TCTribMunicipal/tpRetISSQN
TpRetISSQN = Literal[1, 2, 3]
# TCRegTrib/opSimpNac
OpSimpNac = Literal[1, 2, 3]
# TCRegTrib/regEspTrib
RegEspTrib = Literal[0, 1, 2, 3, 4, 5, 6, 9]


class NationalDpsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NationalDpsConfiguration(NationalDpsInput):
    """Everything the DPS builder needs beyond the per-service preparation.

    ``version`` makes this an explicit, versioned contract like FiscalPolicy:
    a change to any field here is a new version, never a silent mutation of
    values already used to build a signed DPS.
    """
    version: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_.-]+$")
    layout_version: LayoutVersion

    # tpAmb (TCInfDPS/tpAmb): 1=Produção, 2=Homologação. This foundation only
    # ever targets restricted/homologation; production tpAmb=1 is refused by
    # the builder regardless of what is configured here (see dps_builder.py).
    tp_amb: Literal[2] = 2

    # TCInfoPrestador — issuer (SoproLife). CNPJ kept textual (TSCNPJ).
    issuer_cnpj: str
    issuer_name: str = Field(min_length=1, max_length=300)
    issuer_municipio_ibge: str  # cLocEmi (TSCodMunIBGE)
    issuer_inscricao_municipal: str | None = Field(None, max_length=15)
    issuer_op_simp_nac: OpSimpNac
    issuer_reg_esp_trib: RegEspTrib

    # TCCServ — service classification (LC 116/2003).
    codigo_tributacao_nacional: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
    codigo_tributacao_municipal: str | None = Field(None, max_length=20)
    codigo_nbs: str | None = Field(None, max_length=9)

    # TCLocPrest — where the service was rendered (domestic only; comExt unsupported).
    municipio_prestacao_ibge: str

    # TCTribMunicipal
    trib_issqn: TribISSQN
    tp_ret_issqn: TpRetISSQN
    aliquota_percentual: Decimal | None = Field(None, ge=0, le=100, max_digits=6, decimal_places=2)

    # TCTribTotal — this foundation only supports indTotTrib=0 ("não
    # informado"), the schema's explicit "no estimate" choice (Decreto
    # 8.264/2014 opt-out), never an invented total-tax estimate.
    ind_tot_trib: Literal[0] = 0

    # Explicit policy decisions carried over from the M26 contract shape.
    amount_basis: Literal["financial_entry.valor"]
    competence_rule: Literal["service_date"]
    own_revenue_confirmed: Literal[True]
    validation_reference: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")

    @field_validator("issuer_cnpj")
    @classmethod
    def _cnpj_shape(cls, value: str) -> str:
        return assert_cnpj(value)

    @field_validator("issuer_municipio_ibge", "municipio_prestacao_ibge")
    @classmethod
    def _municipio_shape(cls, value: str) -> str:
        return assert_municipio_ibge(value)

    def missing_fields(self) -> list[str]:
        """Required-but-empty optional fields.

        Every field above is already non-optional to Pydantic; this exists so
        callers that build the configuration incrementally (e.g. a future
        admin form) can fail closed the same way ``TaxConfiguration`` does,
        by checking a partial ``model_dump()`` before validation succeeds.
        """
        return sorted(key for key, value in self.model_dump().items() if value is None
                       and key not in {"codigo_tributacao_municipal", "codigo_nbs",
                                       "issuer_inscricao_municipal", "aliquota_percentual"})
