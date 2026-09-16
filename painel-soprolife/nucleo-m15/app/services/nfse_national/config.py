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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
# TCTribOutrosPisCofins/CST (tiposSimples_v1.01.xsd TSTipoCST) — full enum.
PisCofinsCst = Literal[
    "00", "01", "02", "03", "04", "05", "06", "07", "08", "09",
    "49", "50", "51", "52", "53", "54", "55", "56",
    "60", "61", "62", "63", "64", "65", "66", "67",
    "70", "71", "72", "73", "74", "75", "98", "99",
]
# TCTribOutrosPisCofins/tpRetPisCofins (TSTipoRetPISCofins)
TpRetPisCofins = Literal[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]


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

    # TCLocPrest (serv/locPrest/cLocPrestacao) — where the service was
    # actually rendered — is DELIBERATELY NOT modeled here (M31). It varies
    # per attendance (a HOME exam may be in Rio or Niterói; DIRECT is not
    # guaranteed fixed either), so it cannot be a versioned COMPANY-wide
    # constant like every other field in this contract. It is resolved per
    # document from the immutable ``FiscalPreparation.service_municipio_ibge``
    # snapshot (itself sourced from the structured, exam-level
    # ``SpirometryExam.municipio_atendimento_ibge``) and passed directly to
    # ``DpsInput``/``RestrictedIssueContext`` — see
    # ``services/nfse_national/service_location.py``. A prior version of this
    # foundation (M27-M29) incorrectly modeled it here, which silently forced
    # every document to whatever single municipality happened to be
    # configured — the exact bug M31 fixes. See the M31 report for the full
    # rationale and the DPS_v1.01.xsd/tiposComplexos_v1.01.xsd evidence.

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

    # TCTribFederal/piscofins (valores/trib/tribFed/piscofins) — M43. Proven
    # from TWO real, successfully-issued production NFS-e for this exact
    # CNPJ/municipality/Simples-Nacional profile (access keys ending
    # ...136469 and ...747856, fetched 2026-09-16): both embedded DPS
    # documents carry CST=00 ("Nenhum") and tpRetPisCofins=0 ("PIS/COFINS/
    # CSLL Não Retidos") inside tribFed/piscofins — nothing else in that
    # group (no vBCPisCofins/aliquotas/vPis/vCofins in either real note).
    # E0675 forbids tribFed only when the DPS issuer is CPF-identified;
    # SoproLife is CNPJ-identified, so this is permitted. Both fields
    # optional here (``None`` omits the whole group, byte-identical to
    # pre-M43 behavior) — only set when the same profile is proven to need
    # them; never fabricated for a different issuer identity.
    pis_cofins_cst: PisCofinsCst | None = None
    pis_cofins_tp_ret: TpRetPisCofins | None = None

    # Explicit policy decisions carried over from the M26 contract shape.
    amount_basis: Literal["financial_entry.valor"]
    competence_rule: Literal["service_date"]
    own_revenue_confirmed: Literal[True]
    validation_reference: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")

    @field_validator("issuer_cnpj")
    @classmethod
    def _cnpj_shape(cls, value: str) -> str:
        return assert_cnpj(value)

    @field_validator("issuer_municipio_ibge")
    @classmethod
    def _municipio_shape(cls, value: str) -> str:
        return assert_municipio_ibge(value)

    # M41 — Simples Nacional (ME/EPP) cross-field invariants, cited from the
    # official ANEXO_I-SEFIN_ADN-DPS_NFSe-SNNFSe-PRODREST-v1.01-20260209.xlsx
    # business-rule sheet (rows 226-227 and 538-539). Enforced HERE, at
    # configuration-validation time, rather than only in the builder: a
    # config that violates one of these can never be created/versioned in
    # the first place, so no DPS can ever be built from it.
    @model_validator(mode="after")
    def _simples_nacional_invariants(self) -> "NationalDpsConfiguration":
        if self.issuer_op_simp_nac == 3:
            # E0166 — "É obrigatorio o preenchimento do campo de regime de
            # apuração dos tributos do SN para o optante do Simples Nacional
            # ME/EPP." (regApTribSN mandatory when opSimpNac=3.)
            if self.issuer_reg_ap_trib_sn is None:
                raise ValueError(
                    "issuer_reg_ap_trib_sn é obrigatório quando issuer_op_simp_nac=3 "
                    "(regra SEFIN E0166)."
                )
            # E0712 — "Se a situação do emitente da DPS perante o Simples
            # Nacional [...] for ME/EPP, o choice indTotTrib nunca poderá
            # ser informado." The builder chooses pTotTribSN over indTotTrib
            # only when p_tot_trib_sn is set (see dps_builder.py); requiring
            # it here for every opSimpNac=3 config makes the forbidden
            # indTotTrib branch unreachable for this profile.
            if self.p_tot_trib_sn is None:
                raise ValueError(
                    "p_tot_trib_sn é obrigatório quando issuer_op_simp_nac=3 — o "
                    "choice indTotTrib nunca pode ser usado para ME/EPP (regra SEFIN E0712)."
                )
            # E0175 — "quando o prestador optante pelo Simples Nacional tiver
            # o regime de apuração dos tributos ocorrendo também pelo
            # Simples Nacional [regApTribSN=1], o regime especial de
            # tributação do ISSQN deve ser 'Nenhum' (regEspTrib = 0)."
            if self.issuer_reg_ap_trib_sn == 1 and self.issuer_reg_esp_trib != 0:
                raise ValueError(
                    "issuer_reg_esp_trib deve ser 0 (Nenhum) quando issuer_reg_ap_trib_sn=1 "
                    "(regra SEFIN E0175)."
                )
        else:
            # E0162 — "Não é permitido ao não optante do Simples Nacional e
            # o MEI preencherem o campo de indicação do regime de apuração
            # dos tributos apurados." (regApTribSN forbidden when
            # opSimpNac is 1 - não optante - or 2 - MEI.)
            if self.issuer_reg_ap_trib_sn is not None:
                raise ValueError(
                    "issuer_reg_ap_trib_sn não pode ser preenchido quando "
                    "issuer_op_simp_nac != 3 (regra SEFIN E0162)."
                )
            # E0174 — "Quando o prestador da NFS-e é MEI (opSimpNac = 2) o "
            # regime especial de tributação deve ser 'Nenhum' (regEspTrib = 0)."
            if self.issuer_op_simp_nac == 2 and self.issuer_reg_esp_trib != 0:
                raise ValueError(
                    "issuer_reg_esp_trib deve ser 0 (Nenhum) quando issuer_op_simp_nac=2 "
                    "(MEI) (regra SEFIN E0174)."
                )
        return self

    # M43 — TCTribOutrosPisCofins/CST is a required child (no minOccurs="0"
    # in tiposComplexos_v1.01.xsd) of the OPTIONAL piscofins group: the
    # group itself may be entirely absent, but once tpRetPisCofins is
    # supplied (meaning the builder WILL emit the piscofins group), CST
    # must accompany it, or the resulting DPS would be schema-invalid.
    @model_validator(mode="after")
    def _pis_cofins_invariant(self) -> "NationalDpsConfiguration":
        if self.pis_cofins_tp_ret is not None and self.pis_cofins_cst is None:
            raise ValueError(
                "pis_cofins_cst é obrigatório sempre que pis_cofins_tp_ret é informado "
                "(CST é elemento obrigatório dentro do grupo opcional piscofins)."
            )
        return self

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
                                       "issuer_reg_ap_trib_sn", "p_tot_trib_sn",
                                       "pis_cofins_cst", "pis_cofins_tp_ret"})


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
