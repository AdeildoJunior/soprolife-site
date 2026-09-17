"""Deterministic DPS XML builder for the National NFS-e restricted layout.

Builds exactly the element tree described in ``DPS_v1.01.xsd`` /
``tiposComplexos_v1.01.xsd`` (vendored under ``schemas/restricted/``). Only
elements proven required by that schema — or needed to describe SoproLife's
own DIRECT/HOME spirometry service — are emitted; every optional group this
foundation does not yet support (comExt, obra, atvEvento, IBSCBS,
vDescCondIncond, vDedRed, subst, interm) is left out rather than guessed.
``tribFed/piscofins`` (CST/tpRetPisCofins only) is the one exception (M43):
emitted when ``NationalDpsConfiguration.pis_cofins_cst`` is set, proven from
two real successfully-issued production NFS-e for this exact CNPJ/Simples
Nacional profile — never guessed, never emitted for a CPF-identified issuer
(E0675 forbids that combination).

Determinism: the caller supplies ``dh_emi`` explicitly (never
``datetime.now()`` inside this module), so the same ``DpsInput`` always
serializes to byte-identical XML — required for reproducible tests and for a
stable pre-signature digest.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from lxml import etree

from .config import NationalDpsConfiguration
from .identifiers import DpsIdComponents, assert_cnpj, assert_cpf, assert_municipio_ibge, build_dps_id

NFSE_NS = "http://www.sped.fazenda.gov.br/nfse"
NSMAP = {None: NFSE_NS}


class DpsBuildError(ValueError):
    """A required field for a schema-valid DPS was missing or malformed."""


@dataclass(frozen=True)
class Recipient:
    """The tomador (service recipient). Textual identifiers only.

    Exactly one of ``cpf``/``cnpj`` may be set; if neither is known the
    recipient is declared via ``sem_nif_motivo`` (``cNaoNIF``), matching
    Person.cpf being optional (M25.18) — never fabricated.
    """
    nome: str
    cpf: str | None = None
    cnpj: str | None = None
    sem_nif_motivo: int | None = None  # 0/1/2 per TSCodNaoNIF, only if cpf/cnpj absent

    def __post_init__(self):
        present = [v for v in (self.cpf, self.cnpj, self.sem_nif_motivo) if v is not None]
        if len(present) != 1:
            raise DpsBuildError("Tomador exige exatamente um de cpf/cnpj/sem_nif_motivo.")
        if self.cpf is not None:
            assert_cpf(self.cpf)
        if self.cnpj is not None:
            assert_cnpj(self.cnpj)
        if not self.nome or len(self.nome) > 300:
            raise DpsBuildError("Nome do tomador é obrigatório (TSNomeRazaoSocial).")


@dataclass(frozen=True)
class DpsInput:
    config: NationalDpsConfiguration
    dps_id: DpsIdComponents
    # Explicit, timezone-AWARE — never generated inside the builder. Any zone
    # is accepted (UTC is the natural way to capture "now"); M49's
    # EMISSION_TIMEZONE conversion at serialization time makes the written
    # form correct regardless, without altering the instant.
    dh_emi: datetime
    ver_aplic: str
    numero_dps_display: str  # nDPS (TSNumDPS) — the caller's own numbering
    serie_dps_display: str  # serie (TSSerieDPS)
    competencia: object  # datetime.date — service start date (dCompet)
    tomador: Recipient
    descricao_servico: str
    valor_servico: Decimal
    # TCLocPrest/cLocPrestacao (M31) — where THIS service was actually
    # rendered. Deliberately per-document, never part of ``config``: the
    # same NationalDpsConfiguration (a company-wide, versioned tax profile)
    # can back documents rendered in different municipalities (e.g. a HOME
    # exam in Rio vs. one in Niterói). Callers must resolve/validate this via
    # ``service_location.spirometry_service_municipio_ibge`` before
    # constructing ``DpsInput`` — this dataclass only checks shape.
    municipio_prestacao_ibge: str

    def __post_init__(self):
        if self.dh_emi.tzinfo is None:
            raise DpsBuildError("dh_emi precisa ser timezone-aware.")
        if not (0 < self.valor_servico < Decimal("10000000000000.00")):
            raise DpsBuildError("Valor do serviço fora da faixa aceitável (TSDec15V2).")
        if not self.descricao_servico or len(self.descricao_servico) > 2000:
            raise DpsBuildError("Descrição do serviço é obrigatória e limitada a 2000 caracteres.")
        assert_municipio_ibge(self.municipio_prestacao_ibge)


def _el(parent, tag, text=None, **attrs):
    node = etree.SubElement(parent, f"{{{NFSE_NS}}}{tag}")
    if text is not None:
        node.text = str(text)
    for key, value in attrs.items():
        node.set(key, str(value))
    return node


def _decimal_text(value: Decimal) -> str:
    """TSDec15V2/TSDec3V2 etc.: fixed two-decimal textual representation."""
    return f"{value.quantize(Decimal('0.01'))}"


# M49 — the civil time zone dhEmi is EXPRESSED in. Not a conversion of the
# instant: `astimezone` below preserves the exact moment and only changes how
# it is written down.
#
# Why this is required, proven from the real DPS #9 rejection (2026-09-16):
# `dhEmi` is typed `TSDateTimeUTC` in tiposSimples_v1.01.xsd, but that name
# means "carries a UTC offset designator", not "must be expressed in UTC" —
# the pattern accepts any offset from -11:00 to +12:00, `+00:00` included.
# We emitted the UTC wall clock with `+00:00`, which is schema-valid and, as
# an INSTANT, was 1.4s BEFORE Sefin's own processing. Sefin rejected it with
# E0008 ("a data de emissão da DPS não pode ser posterior à data do seu
# processamento") anyway — which is only possible if the rule compares civil
# wall-clock readings, not instants. Confirmed by the two real successfully
# issued production NFS-e, where Sefin's OWN `dhProc` is written `-03:00` and
# equals the accepted `dhEmi` to the second.
#
# Consequence of the old behaviour: between 21:00 and 23:59 in Brasília the
# UTC wall clock is already on the NEXT DAY and reads ~3h higher, so every
# DPS emitted in that window looked ~3h in the future to Sefin. DPS #9 was
# emitted at 23:34:37-03:00 and serialized as 2026-09-17T02:34:37+00:00.
EMISSION_TIMEZONE = ZoneInfo("America/Sao_Paulo")


def build_dps_element(data: DpsInput) -> etree._Element:
    """Return the unsigned ``<DPS>`` root element (``TCDPS``), ready for XSD
    validation and, separately, for XMLDSig signing over ``infDPS``."""
    cfg = data.config
    if cfg.tp_amb != 2:
        raise DpsBuildError("Este builder só emite tpAmb=2 (Homologação/Restrita).")

    dps_id_value = build_dps_id(data.dps_id)

    root = etree.Element(f"{{{NFSE_NS}}}DPS", nsmap=NSMAP)
    root.set("versao", "1.01")

    inf = etree.SubElement(root, f"{{{NFSE_NS}}}infDPS")
    inf.set("Id", dps_id_value)

    _el(inf, "tpAmb", cfg.tp_amb)
    # M49 — expressed in the issuer's civil time (see EMISSION_TIMEZONE).
    # `astimezone` keeps the instant identical; only the written form changes.
    # Callers may pass any aware datetime (UTC is the natural way to capture
    # "now") and still get a correct, Sefin-comparable dhEmi.
    dh_emi_local = data.dh_emi.astimezone(EMISSION_TIMEZONE)
    _el(inf, "dhEmi", dh_emi_local.strftime("%Y-%m-%dT%H:%M:%S") + _utc_offset(dh_emi_local))
    _el(inf, "verAplic", data.ver_aplic)
    _el(inf, "serie", data.serie_dps_display)
    _el(inf, "nDPS", data.numero_dps_display)
    _el(inf, "dCompet", data.competencia.strftime("%Y-%m-%d"))
    _el(inf, "tpEmit", 1)  # 1 = Prestador (SoproLife always issues its own DPS)
    _el(inf, "cLocEmi", cfg.issuer_municipio_ibge)

    prest = _el(inf, "prest")
    _el(prest, "CNPJ", cfg.issuer_cnpj)
    if cfg.issuer_inscricao_municipal:
        _el(prest, "IM", cfg.issuer_inscricao_municipal)
    # M41 — Anexo I (business rules), rule row 202, error E0121: "Se o
    # emitente da DPS for o prestador de serviço (tpEmit for igual a 1),
    # então o nome ou razão social não deve ser informado." This builder
    # ALWAYS emits tpEmit=1 (SoproLife always issues its own DPS as the
    # service provider — see the tpEmit element above), so prest/xNome must
    # NEVER be emitted here. Proven, real rejection code, not a guess:
    # confirmed via the official ANEXO_I-SEFIN_ADN-DPS_NFSe-SNNFSe-PRODREST
    # -v1.01-20260209.xlsx business-rule sheet. `cfg.issuer_name` is kept on
    # the configuration model regardless — it remains useful metadata (e.g.
    # for reports/audit) even though the DPS itself must never carry it.
    reg_trib = _el(prest, "regTrib")
    _el(reg_trib, "opSimpNac", cfg.issuer_op_simp_nac)
    if cfg.issuer_reg_ap_trib_sn is not None:
        _el(reg_trib, "regApTribSN", cfg.issuer_reg_ap_trib_sn)
    _el(reg_trib, "regEspTrib", cfg.issuer_reg_esp_trib)

    toma = _el(inf, "toma")
    if data.tomador.cnpj:
        _el(toma, "CNPJ", data.tomador.cnpj)
    elif data.tomador.cpf:
        _el(toma, "CPF", data.tomador.cpf)
    else:
        _el(toma, "cNaoNIF", data.tomador.sem_nif_motivo)
    _el(toma, "xNome", data.tomador.nome)

    serv = _el(inf, "serv")
    loc_prest = _el(serv, "locPrest")
    _el(loc_prest, "cLocPrestacao", data.municipio_prestacao_ibge)
    c_serv = _el(serv, "cServ")
    _el(c_serv, "cTribNac", cfg.codigo_tributacao_nacional)
    if cfg.codigo_tributacao_municipal:
        _el(c_serv, "cTribMun", cfg.codigo_tributacao_municipal)
    _el(c_serv, "xDescServ", data.descricao_servico)
    if cfg.codigo_nbs:
        _el(c_serv, "cNBS", cfg.codigo_nbs)

    valores = _el(inf, "valores")
    v_serv_prest = _el(valores, "vServPrest")
    _el(v_serv_prest, "vServ", _decimal_text(data.valor_servico))
    trib = _el(valores, "trib")
    trib_mun = _el(trib, "tribMun")
    _el(trib_mun, "tribISSQN", cfg.trib_issqn)
    _el(trib_mun, "tpRetISSQN", cfg.tp_ret_issqn)
    if cfg.aliquota_percentual is not None:
        _el(trib_mun, "pAliq", f"{cfg.aliquota_percentual.quantize(Decimal('0.01'))}")
    # M43 — tribFed/piscofins, between tribMun and totTrib per the schema's
    # TCInfoTributacao sequence order. Proven from two real, successfully
    # issued production NFS-e for this exact CNPJ/Simples-Nacional profile:
    # both embedded DPS carry CST + tpRetPisCofins here (nothing else in
    # the group) — see NationalDpsConfiguration.pis_cofins_cst docstring.
    # E0675 forbids tribFed only for a CPF-identified issuer; SoproLife is
    # CNPJ-identified, so this is permitted. Only emitted when configured
    # (``None`` keeps prior byte-identical behavior).
    if cfg.pis_cofins_cst is not None:
        trib_fed = _el(trib, "tribFed")
        piscofins = _el(trib_fed, "piscofins")
        _el(piscofins, "CST", cfg.pis_cofins_cst)
        if cfg.pis_cofins_tp_ret is not None:
            _el(piscofins, "tpRetPisCofins", cfg.pis_cofins_tp_ret)
    tot_trib = _el(trib, "totTrib")
    if cfg.p_tot_trib_sn is not None:
        _el(tot_trib, "pTotTribSN", f"{cfg.p_tot_trib_sn.quantize(Decimal('0.01'))}")
    else:
        _el(tot_trib, "indTotTrib", cfg.ind_tot_trib)

    return root


def _utc_offset(dt: datetime) -> str:
    offset = dt.utcoffset()
    if offset is None or offset.total_seconds() == 0:
        return "+00:00"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def serialize_dps(root: etree._Element) -> bytes:
    """Deterministic byte serialization (fixed declaration, no pretty-print
    whitespace that would change the canonical digest)."""
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=False)
