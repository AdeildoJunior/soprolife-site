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
        issuer_municipio_ibge="3304557", issuer_op_simp_nac=3,
        issuer_reg_ap_trib_sn=1, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional="140501",
        trib_issqn=1, tp_ret_issqn=1,
        amount_basis="financial_entry.valor", competence_rule="service_date",
        own_revenue_confirmed=True, validation_reference="SYNTHETIC-ONLY",
        p_tot_trib_sn=Decimal("6.00"),
    )
    data.update(changes)
    return NationalDpsConfiguration.model_validate(data)


def synthetic_dps_input(**changes) -> DpsInput:
    dps_id = DpsIdComponents(codigo_municipio="3304557", tipo_inscricao_federal=2,
                             inscricao_federal="11222333000181", serie_dps="00001",
                             numero_dps="000000000000001")
    data = dict(
        config=synthetic_config(), environment='restricted', dps_id=dps_id,
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


# ------------------------------------------------- M59 — tpAmb por ambiente


def test_tp_amb_is_derived_from_the_explicit_environment():
    """The source of truth is the environment, not the stored configuration."""
    from app.services.nfse_national.config import tp_amb_for_environment
    assert tp_amb_for_environment("restricted") == 2
    assert tp_amb_for_environment("production") == 1


@pytest.mark.parametrize("unknown", ["mock", "", "Production", "RESTRICTED", None, 1, 2])
def test_an_unrecognized_environment_fails_closed(unknown):
    """'mock' is in this list on purpose: the mock provider never enters the
    national builder, so a mock environment reaching it is a bug — and must
    not quietly become homologation."""
    from app.services.nfse_national.config import (UnknownFiscalEnvironmentError,
                                                   tp_amb_for_environment)
    with pytest.raises(UnknownFiscalEnvironmentError):
        tp_amb_for_environment(unknown)


def test_restricted_environment_emits_exactly_tp_amb_2():
    xml = serialize_dps(build_dps_element(synthetic_dps_input(environment="restricted")))
    assert b"<tpAmb>2</tpAmb>" in xml


def test_production_environment_emits_exactly_tp_amb_1():
    """A production configuration (tp_amb=1) under the production
    environment is now buildable — which is the whole point of M59."""
    xml = serialize_dps(build_dps_element(synthetic_dps_input(
        environment="production", config=synthetic_config(tp_amb=1))))
    assert b"<tpAmb>1</tpAmb>" in xml
    # And the official schema really accepts it — the builder's refusal was
    # a self-imposed guard, never an XSD limitation.
    validate_dps_xml(xml)


def test_a_restricted_configuration_cannot_build_a_production_dps():
    """The failure M59 exists to prevent: a homologation tax profile used to
    emit under tpAmb=1 would be a real issuance wearing the wrong label."""
    with pytest.raises(DpsBuildError, match="exige tpAmb=1"):
        build_dps_element(synthetic_dps_input(environment="production"))  # config says 2


def test_a_production_configuration_cannot_build_a_restricted_dps():
    """The symmetric half: a production tax profile must not be quietly used
    for a homologation run either."""
    with pytest.raises(DpsBuildError, match="exige tpAmb=2"):
        build_dps_element(synthetic_dps_input(environment="restricted",
                                              config=synthetic_config(tp_amb=1)))


@pytest.mark.parametrize("unknown", ["mock", "", None])
def test_the_builder_refuses_an_unrecognized_environment(unknown):
    with pytest.raises(DpsBuildError):
        build_dps_element(synthetic_dps_input(environment=unknown))


def test_environment_has_no_default_so_it_cannot_be_omitted():
    """A caller that does not say which environment it means gets a
    TypeError, never a silent homologation DPS."""
    import dataclasses
    field = next(f for f in dataclasses.fields(DpsInput) if f.name == "environment")
    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING


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
