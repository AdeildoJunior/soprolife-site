"""Deterministic DPS XML builder for the National NFS-e restricted layout.

Builds exactly the element tree described in ``DPS_v1.01.xsd`` /
``tiposComplexos_v1.01.xsd`` (vendored under ``schemas/restricted/``). Only
elements proven required by that schema — or needed to describe SoproLife's
own DIRECT/HOME spirometry service — are emitted; every optional group this
foundation does not yet support (comExt, obra, atvEvento, IBSCBS, tribFed,
vDescCondIncond, vDedRed, subst, interm) is left out rather than guessed.

Determinism: the caller supplies ``dh_emi`` explicitly (never
``datetime.now()`` inside this module), so the same ``DpsInput`` always
serializes to byte-identical XML — required for reproducible tests and for a
stable pre-signature digest.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from lxml import etree

from .config import NationalDpsConfiguration
from .identifiers import DpsIdComponents, assert_cnpj, assert_cpf, build_dps_id

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
    dh_emi: datetime  # explicit, UTC — never generated inside the builder
    ver_aplic: str
    numero_dps_display: str  # nDPS (TSNumDPS) — the caller's own numbering
    serie_dps_display: str  # serie (TSSerieDPS)
    competencia: object  # datetime.date — service start date (dCompet)
    tomador: Recipient
    descricao_servico: str
    valor_servico: Decimal

    def __post_init__(self):
        if self.dh_emi.tzinfo is None:
            raise DpsBuildError("dh_emi precisa ser timezone-aware (UTC).")
        if not (0 < self.valor_servico < Decimal("10000000000000.00")):
            raise DpsBuildError("Valor do serviço fora da faixa aceitável (TSDec15V2).")
        if not self.descricao_servico or len(self.descricao_servico) > 2000:
            raise DpsBuildError("Descrição do serviço é obrigatória e limitada a 2000 caracteres.")


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
    _el(inf, "dhEmi", data.dh_emi.strftime("%Y-%m-%dT%H:%M:%S") + _utc_offset(data.dh_emi))
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
    _el(prest, "xNome", cfg.issuer_name)
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
    _el(loc_prest, "cLocPrestacao", cfg.municipio_prestacao_ibge)
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
