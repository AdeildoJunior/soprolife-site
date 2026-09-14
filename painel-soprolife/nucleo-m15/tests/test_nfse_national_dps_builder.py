"""M27 — DPS builder: determinismo e conformidade contra o XSD oficial restrito."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from lxml import etree

from app.services.nfse_national.config import NationalDpsConfiguration
from app.services.nfse_national.dps_builder import (
    DpsBuildError,
    DpsInput,
    Recipient,
    build_dps_element,
    serialize_dps,
)
from app.services.nfse_national.identifiers import DpsIdComponents
from app.services.nfse_national.xsd_validation import XsdValidationError, validate_dps_xml


def synthetic_config(**changes) -> NationalDpsConfiguration:
    data = dict(
        version="SYNTH-RESTRICTED-v1", layout_version="restricted-v1.01-20260727",
        issuer_cnpj="11222333000181", issuer_name="SOPROLIFE SAUDE LTDA (SINTETICO)",
        issuer_municipio_ibge="3304557", issuer_op_simp_nac=3, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional="140501",
        trib_issqn=1, tp_ret_issqn=1,
        amount_basis="financial_entry.valor", competence_rule="service_date",
        own_revenue_confirmed=True, validation_reference="SYNTHETIC-ONLY",
    )
    data.update(changes)
    return NationalDpsConfiguration.model_validate(data)


def synthetic_dps_input(**changes) -> DpsInput:
    dps_id = DpsIdComponents(codigo_municipio="3304557", tipo_inscricao_federal=2,
                             inscricao_federal="11222333000181", serie_dps="00001",
                             numero_dps="000000000000001")
    data = dict(
        config=synthetic_config(), dps_id=dps_id,
        dh_emi=datetime(2026, 9, 13, 10, 0, 0, tzinfo=timezone.utc),
        ver_aplic="soprolife-m27-0.1", numero_dps_display="1", serie_dps_display="1",
        competencia=date(2026, 8, 10),
        tomador=Recipient(nome="Paciente Sintético Um", sem_nif_motivo=1),
        descricao_servico="Realização de exame de espirometria sem broncodilatador em 10/08/2026.",
        valor_servico=Decimal("220.00"),
        municipio_prestacao_ibge="3304557",
    )
    data.update(changes)
    return DpsInput(**data)


def test_builder_output_is_schema_valid():
    root = build_dps_element(synthetic_dps_input())
    xml = serialize_dps(root)
    validate_dps_xml(xml)  # raises on any violation


def test_builder_is_deterministic():
    xml1 = serialize_dps(build_dps_element(synthetic_dps_input()))
    xml2 = serialize_dps(build_dps_element(synthetic_dps_input()))
    assert xml1 == xml2


def test_different_input_changes_output():
    xml1 = serialize_dps(build_dps_element(synthetic_dps_input()))
    xml2 = serialize_dps(build_dps_element(synthetic_dps_input(valor_servico=Decimal("999.00"))))
    assert xml1 != xml2


def test_root_element_and_namespace():
    root = build_dps_element(synthetic_dps_input())
    assert root.tag == "{http://www.sped.fazenda.gov.br/nfse}DPS"
    assert root.get("versao") == "1.01"
    inf = root.find("{http://www.sped.fazenda.gov.br/nfse}infDPS")
    assert inf is not None
    assert inf.get("Id") == "DPS330455721122233300018100001000000000000001"


def test_naive_datetime_rejected():
    with pytest.raises(DpsBuildError):
        synthetic_dps_input(dh_emi=datetime(2026, 9, 13, 10, 0, 0))


def test_empty_description_rejected():
    with pytest.raises(DpsBuildError):
        synthetic_dps_input(descricao_servico="")


def test_zero_amount_rejected():
    with pytest.raises(DpsBuildError):
        synthetic_dps_input(valor_servico=Decimal("0"))


def test_production_ambient_rejected_by_configuration():
    # tp_amb is typed Literal[2]: the configuration contract itself refuses
    # anything else, before the builder's own runtime check ever runs.
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        synthetic_config(tp_amb=1)


def test_recipient_requires_exactly_one_identity():
    from app.services.nfse_national.dps_builder import DpsBuildError as Err
    with pytest.raises(Err):
        Recipient(nome="X")
    with pytest.raises(Err):
        Recipient(nome="X", cpf="12345678901", sem_nif_motivo=1)


def test_recipient_with_cpf_serializes_and_validates():
    data = synthetic_dps_input(tomador=Recipient(nome="Paciente Com CPF", cpf="12345678901"))
    xml = serialize_dps(build_dps_element(data))
    validate_dps_xml(xml)
    root = etree.fromstring(xml)
    cpf_el = root.find(".//{http://www.sped.fazenda.gov.br/nfse}toma/{http://www.sped.fazenda.gov.br/nfse}CPF")
    assert cpf_el is not None and cpf_el.text == "12345678901"


def test_malformed_xml_reported_by_validator():
    with pytest.raises(XsdValidationError):
        validate_dps_xml(b"<not-xml")


def test_schema_invalid_document_reported_with_errors():
    with pytest.raises(XsdValidationError) as excinfo:
        validate_dps_xml(b'<DPS xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.01"/>')
    assert excinfo.value.errors
