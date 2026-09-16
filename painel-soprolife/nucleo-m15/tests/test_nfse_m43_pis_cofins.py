"""M43 — tribFed/piscofins (CST/tpRetPisCofins), proven from two REAL
successfully-issued production NFS-e for SoproLife's exact fiscal profile
(CNPJ 63544026000110, Rio de Janeiro, Simples Nacional ME/EPP), fetched
2026-09-16. Both embedded DPS documents carried exactly:

    valores/trib/tribFed/piscofins/CST = "00"           (Nenhum)
    valores/trib/tribFed/piscofins/tpRetPisCofins = 0    (PIS/COFINS/CSLL
                                                           Não Retidos)

and nothing else in that group. No real patient/tomador data is stored
here — only the field-shape finding, using the same synthetic fixtures as
the rest of this suite.
"""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.nfse_national.config import NationalDpsConfiguration
from app.services.nfse_national.dps_builder import DpsInput, Recipient, build_dps_element, serialize_dps
from app.services.nfse_national.identifiers import DpsIdComponents
from app.services.nfse_national.xsd_validation import validate_dps_xml

NFSE_NS = "http://www.sped.fazenda.gov.br/nfse"


def synthetic_config(**changes) -> NationalDpsConfiguration:
    data = dict(
        version="SYNTH-M43-v1", layout_version="restricted-v1.01-20260727",
        issuer_cnpj="63544026000110", issuer_name="SoproLife Diagnósticos e Soluções em Saúde LTDA",
        issuer_municipio_ibge="3304557", issuer_op_simp_nac=3,
        issuer_reg_ap_trib_sn=1, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional="040201", codigo_tributacao_municipal="001",
        codigo_nbs="123019900",
        trib_issqn=1, tp_ret_issqn=1,
        amount_basis="financial_entry.valor", competence_rule="service_date",
        own_revenue_confirmed=True, validation_reference="SOPROLIFE-M43-GOLDEN",
        p_tot_trib_sn=Decimal("6.00"),
    )
    data.update(changes)
    return NationalDpsConfiguration.model_validate(data)


def synthetic_dps_input(**changes) -> DpsInput:
    dps_id = DpsIdComponents(codigo_municipio="3304557", tipo_inscricao_federal=2,
                             inscricao_federal="63544026000110", serie_dps="00001",
                             numero_dps="000000000000007")
    data = dict(
        config=synthetic_config(), dps_id=dps_id,
        dh_emi=datetime(2026, 9, 16, 10, 0, 0, tzinfo=timezone.utc),
        ver_aplic="soprolife-m43-0.1", numero_dps_display="7", serie_dps_display="1",
        competencia=date(2026, 9, 15),
        tomador=Recipient(nome="Paciente Sintetico M43", cpf="11144477735"),
        descricao_servico="Realização de exame de espirometria sem broncodilatador em 15/09/2026.",
        valor_servico=Decimal("380.00"),
        municipio_prestacao_ibge="3304557",
    )
    data.update(changes)
    return DpsInput(**data)


def _q(tag):
    return f"{{{NFSE_NS}}}{tag}"


# ============================================================ config validation


def test_pis_cofins_defaults_to_none_backward_compatible():
    cfg = synthetic_config()
    assert cfg.pis_cofins_cst is None
    assert cfg.pis_cofins_tp_ret is None


def test_cst_alone_without_tp_ret_is_valid():
    cfg = synthetic_config(pis_cofins_cst="00")
    assert cfg.pis_cofins_cst == "00"
    assert cfg.pis_cofins_tp_ret is None


def test_cst_and_tp_ret_together_is_valid():
    cfg = synthetic_config(pis_cofins_cst="00", pis_cofins_tp_ret=0)
    assert cfg.pis_cofins_cst == "00"
    assert cfg.pis_cofins_tp_ret == 0


def test_tp_ret_without_cst_is_rejected():
    """CST is a required child of the piscofins group once it is emitted at
    all — tpRetPisCofins alone would build a schema-invalid DPS."""
    with pytest.raises(ValidationError, match="pis_cofins_cst"):
        synthetic_config(pis_cofins_tp_ret=0)


def test_invalid_cst_value_rejected():
    with pytest.raises(ValidationError):
        synthetic_config(pis_cofins_cst="XX")


# ============================================================ builder output


def test_no_tribFed_when_not_configured_byte_identical_to_pre_m43():
    root = build_dps_element(synthetic_dps_input())
    trib = root.find(_q("infDPS")).find(_q("valores")).find(_q("trib"))
    assert trib.find(_q("tribFed")) is None


def test_tribFed_piscofins_emitted_matching_golden_shape():
    cfg = synthetic_config(pis_cofins_cst="00", pis_cofins_tp_ret=0)
    root = build_dps_element(synthetic_dps_input(config=cfg))
    trib = root.find(_q("infDPS")).find(_q("valores")).find(_q("trib"))
    trib_fed = trib.find(_q("tribFed"))
    assert trib_fed is not None
    piscofins = trib_fed.find(_q("piscofins"))
    assert piscofins is not None
    assert piscofins.find(_q("CST")).text == "00"
    assert piscofins.find(_q("tpRetPisCofins")).text == "0"
    # Matching the golden production DPS exactly: nothing else in the group.
    assert piscofins.find(_q("vBCPisCofins")) is None
    assert piscofins.find(_q("pAliqPis")) is None
    assert piscofins.find(_q("pAliqCofins")) is None
    assert piscofins.find(_q("vPis")) is None
    assert piscofins.find(_q("vCofins")) is None


def test_tribFed_element_order_between_tribMun_and_totTrib():
    """Schema-critical: TCInfoTributacao's sequence is tribMun, tribFed,
    totTrib — violating this order alone would make the DPS schema-invalid
    even with otherwise-correct content."""
    cfg = synthetic_config(pis_cofins_cst="00", pis_cofins_tp_ret=0)
    root = build_dps_element(synthetic_dps_input(config=cfg))
    trib = root.find(_q("infDPS")).find(_q("valores")).find(_q("trib"))
    child_tags = [c.tag for c in trib]
    assert child_tags == [_q("tribMun"), _q("tribFed"), _q("totTrib")]


def test_cst_only_no_tp_ret_still_schema_valid():
    cfg = synthetic_config(pis_cofins_cst="00")
    root = build_dps_element(synthetic_dps_input(config=cfg))
    xml = serialize_dps(root)
    validate_dps_xml(xml)  # raises on any violation


def test_full_shape_with_pis_cofins_is_xsd_valid():
    cfg = synthetic_config(pis_cofins_cst="00", pis_cofins_tp_ret=0)
    root = build_dps_element(synthetic_dps_input(config=cfg))
    xml = serialize_dps(root)
    validate_dps_xml(xml)
