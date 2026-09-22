"""M27 — identificadores textuais (CNPJ alfanumérico, CPF, Id de DPS)."""
import pytest

from app.services.nfse_national.identifiers import (
    DpsIdComponents,
    InvalidIdentifierError,
    assert_cnpj,
    assert_cpf,
    assert_municipio_ibge,
    build_dps_id,
)


def test_cnpj_accepts_alphanumeric_shape():
    # National platform CNPJ alphanumeric support (2026-08) — never digits-only.
    assert assert_cnpj("12ABC34501DE35") == "12ABC34501DE35"
    assert assert_cnpj("11222333000181") == "11222333000181"


@pytest.mark.parametrize("value", ["1122233300018", "112223330001811", "1122233300018a", "", None, 11222333000181])
def test_cnpj_rejects_wrong_shape(value):
    with pytest.raises(InvalidIdentifierError):
        assert_cnpj(value)


def test_cnpj_never_becomes_an_integer():
    # Regression guard: nothing in this module may call int()/float() on a
    # CNPJ — an alphanumeric CNPJ would raise or silently corrupt otherwise.
    value = assert_cnpj("12ABC34501DE35")
    with pytest.raises(ValueError):
        int(value)


def test_cpf_shape():
    assert assert_cpf("12345678901") == "12345678901"
    with pytest.raises(InvalidIdentifierError):
        assert_cpf("1234567890")


def test_municipio_ibge_shape():
    assert assert_municipio_ibge("3304557") == "3304557"
    with pytest.raises(InvalidIdentifierError):
        assert_municipio_ibge("330455")


def test_build_dps_id_cnpj_issuer():
    components = DpsIdComponents(codigo_municipio="3304557", tipo_inscricao_federal=2,
                                  inscricao_federal="11222333000181", serie_dps="00001",
                                  numero_dps="000000000000001")
    dps_id = build_dps_id(components)
    assert dps_id == "DPS330455721122233300018100001000000000000001"
    assert len(dps_id) == 45


def test_build_dps_id_cpf_issuer_padded():
    components = DpsIdComponents(codigo_municipio="3304557", tipo_inscricao_federal=1,
                                  inscricao_federal="12345678901", serie_dps="00001",
                                  numero_dps="000000000000001")
    dps_id = build_dps_id(components)
    assert dps_id.startswith("DPS3304557" + "1" + "000" + "12345678901")


def test_dps_id_components_reject_bad_tipo_inscricao():
    with pytest.raises(InvalidIdentifierError):
        DpsIdComponents(codigo_municipio="3304557", tipo_inscricao_federal=3,
                        inscricao_federal="11222333000181", serie_dps="00001",
                        numero_dps="000000000000001")


def test_dps_id_components_reject_bad_serie_or_numero():
    with pytest.raises(InvalidIdentifierError):
        DpsIdComponents(codigo_municipio="3304557", tipo_inscricao_federal=2,
                        inscricao_federal="11222333000181", serie_dps="1",
                        numero_dps="000000000000001")
    with pytest.raises(InvalidIdentifierError):
        DpsIdComponents(codigo_municipio="3304557", tipo_inscricao_federal=2,
                        inscricao_federal="11222333000181", serie_dps="00001",
                        numero_dps="1")
