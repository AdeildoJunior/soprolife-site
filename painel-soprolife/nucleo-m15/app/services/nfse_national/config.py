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

from datetime import date
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
    # TCRegTrib/regApTribSN — optional per the schema (only mandatory when
    # opSimpNac=3 AND the taxpayer has exceeded a Simples Nacional sublimit),
    # but the SoproLife accountant's real portal configuration explicitly
    # names one of these three regimes ("Regime de apuração dos tributos
    # federais e municipal pelo Simples Nacional" = 1), so it is modeled
    # explicitly rather than silently dropped. ``None`` when not applicable.
    issuer_reg_ap_trib_sn: Literal[1, 2, 3] | None = None
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

    # TCTribTotal is an xs:choice of exactly one alternative: vTotTrib
    # (monetary breakdown), pTotTrib (percentage breakdown), indTotTrib=0
    # ("não informado" — Decreto 8.264/2014 opt-out) or pTotTribSN (the
    # approximate total-tax percentage taken directly from the Simples
    # Nacional bracket). This foundation models only the two evidenced,
    # unambiguous alternatives: the M27 "no estimate" default (indTotTrib=0)
    # and, additively, pTotTribSN — the exact field the official schema
    # documents as "Valor percentual aproximado do total dos tributos da
    # alíquota do Simples Nacional (%)", which is what the real SoproLife
    # portal configuration means by "Informar alíquota do Simples Nacional".
    # vTotTrib/pTotTrib (itemized breakdowns) remain unmodeled — no evidence
    # this foundation needs them, and building them would be invention.
    #
    # When ``p_tot_trib_sn`` is set it takes precedence in the builder and
    # ``ind_tot_trib`` is not emitted; when it is ``None`` behavior is
    # byte-for-byte identical to the original M27 foundation.
    ind_tot_trib: Literal[0] = 0
    p_tot_trib_sn: Decimal | None = Field(None, ge=0, le=100, max_digits=6, decimal_places=2)

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
                                       "issuer_inscricao_municipal", "aliquota_percentual",
                                       "issuer_reg_ap_trib_sn", "p_tot_trib_sn"})


class NationalDpsConfigurationVersionCreate(NationalDpsInput):
    """M29 — admin API payload for creating one new effective configuration
    version (``POST /fiscal/configuracao-nacional``). Wraps the same
    versioning envelope ``FiscalPolicy``/``PolicyCreate`` already use
    (environment, effective date, draft/validated) around the concrete
    ``NationalDpsConfiguration`` contract above — never a partial/loose dict.
    """
    environment: Literal["restricted", "production"]
    effective_from: date
    validation_state: Literal["draft", "validated"] = "draft"
    configuration: NationalDpsConfiguration
