"""M29 — real-profile pre-live test (mission section H).

Proves that the REAL SoproLife fiscal-profile STRUCTURE (mission section A:
Simples Nacional, Rio de Janeiro/RJ, código de tributação nacional 04.02.01,
código municipal 001, NBS 123019900, alíquota atual do Simples 6,00%) feeds
``run_offline_preflight()`` end to end, with ONLY synthetic recipient/exam
data and a synthetic certificate, reaching ``READY_TO_SEND`` with ZERO
network access — for both spirometry variants (mission section B: with and
without bronchodilator).

Nothing here uses the real PFX, the real password, or any real patient
data. The values below are the accountant-supplied CURRENT operational
values named in the M29 mission brief, not invented.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.config import Settings
from app.models import FinancialEntry, FiscalArtifact, Person, SpirometryExam
from app.services import nfse
from app.services.nfse_national import fiscal_config
from app.services.nfse_national.config import NationalDpsConfiguration
from app.services.nfse_national.preflight import Stage, run_offline_preflight
from app.services.nfse_national.signer import generate_synthetic_test_certificate, load_pkcs12_certificate
from tests.test_nfse_foundation import policy_payload

RIO_DE_JANEIRO_IBGE = "3304557"
SOPROLIFE_CNPJ = "63544026000110"  # from HUMAN_INPUTS.env / the validated real certificate subject


def real_soprolife_national_configuration(**overrides) -> NationalDpsConfiguration:
    data = dict(
        version="SOPROLIFE-REAL-v1", layout_version="restricted-v1.01-20260727",
        issuer_cnpj=SOPROLIFE_CNPJ,
        issuer_name="SoproLife Diagnósticos e Soluções em Saúde LTDA",
        issuer_municipio_ibge=RIO_DE_JANEIRO_IBGE,
        issuer_op_simp_nac=3,               # Optante - ME/EPP (Simples Nacional)
        issuer_reg_ap_trib_sn=1,            # "Regime de apuração ... pelo Simples Nacional"
        issuer_reg_esp_trib=0,              # Nenhum
        codigo_tributacao_nacional="040201",  # 04.02.01 — Análises clínicas e congêneres
        codigo_tributacao_municipal="001",    # Análises clínicas, patologia ou congênere
        codigo_nbs="123019900",
        trib_issqn=1,                       # Operação Tributável
        tp_ret_issqn=1,                     # Não Retido
        p_tot_trib_sn=Decimal("6.00"),      # CURRENT Simples Nacional percentage (accountant-supplied)
        amount_basis="financial_entry.valor", competence_rule="service_date",
        own_revenue_confirmed=True, validation_reference="SOPROLIFE-M29-REAL-PROFILE",
    )
    data.update(overrides)
    return NationalDpsConfiguration.model_validate(data)


@pytest.fixture
def restricted_settings(tmp_path):
    return Settings(nfse_enabled=True, nfse_environment="restricted",
                    nfse_fiscal_artifacts_dir=tmp_path / "fiscal-artifacts")


@pytest.fixture
def synthetic_certificate():
    p12_bytes, password = generate_synthetic_test_certificate()
    return load_pkcs12_certificate(p12_bytes, password)


def _make_ready_document(db, users, settings, *, broncodilatador, code,
                         municipio_atendimento_ibge=RIO_DE_JANEIRO_IBGE):
    p = Person(public_code=f"PES-{code}", nome_completo=f"Paciente Sintético {code}",
              nome_normalizado=f"paciente sintetico {code}".lower(), cpf="52998224725")
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code=f"ESP-{code}", person_id=p.id, status="Realizado",
                       data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                       modalidade="residencial", broncodilatador=broncodilatador,
                       municipio_atendimento_ibge=municipio_atendimento_ibge)
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code=f"LAN-{code}", tipo="receita", categoria="Espirometria",
                       valor=Decimal("220.00"), status="Recebido", spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    nfse.create_policy(db, policy_payload(version=f"SYNTH-M29-REAL-{code}", environment="restricted",
                                          flow="HOME"), users["admin"].id)
    return nfse.prepare(db, e.id, settings, users["gestor"].id)


@pytest.mark.parametrize("broncodilatador,expected_fragment", [
    (True, b"Espirometria com broncodilatador"),
    (False, b"Espirometria sem broncodilatador"),
])
def test_real_profile_reaches_ready_to_send_for_both_variants(
        db, users, restricted_settings, synthetic_certificate, broncodilatador, expected_fragment):
    code = "REALBD" if broncodilatador else "REALNOBD"
    doc = _make_ready_document(db, users, restricted_settings, broncodilatador=broncodilatador, code=code)

    result = run_offline_preflight(db, doc.id, restricted_settings, users["gestor"].id,
                                   national_config=real_soprolife_national_configuration(
                                       version=f"SOPROLIFE-REAL-{code}"),
                                   certificate=synthetic_certificate)
    assert result.status == "ready_to_send"
    assert result.stage_reached == Stage.READY
    assert result.blockers == []
    assert result.request_fingerprint is not None

    stored_dir = restricted_settings.resolved_fiscal_artifacts_storage_dir()
    signed = next(a for a in result.staged_artifacts if a.kind == "dps_signed_xml")
    signed_xml = (stored_dir / signed.relative_path).read_bytes()
    assert expected_fragment in signed_xml
    assert b"SoproLife Diagn" in signed_xml  # issuer legal name present
    assert SOPROLIFE_CNPJ.encode() in signed_xml
    assert b"<cTribNac>040201</cTribNac>" in signed_xml
    assert b"<cTribMun>001</cTribMun>" in signed_xml
    assert b"<cNBS>123019900</cNBS>" in signed_xml
    assert b"<pTotTribSN>6.00</pTotTribSN>" in signed_xml


def test_real_profile_auto_resolves_from_versioned_configuration_table(
        db, users, restricted_settings, synthetic_certificate):
    """The full path a real admin-created version actually takes: no
    national_config/recipient passed explicitly — run_offline_preflight()
    must resolve BOTH from the database on its own (fiscal_config.py +
    Person.cpf), exactly as the live `/fiscal/documentos/{id}/preflight`
    endpoint does for a real document."""
    fiscal_config.create_version(
        db, environment="restricted", effective_from=date(2026, 1, 1), validation_state="validated",
        configuration=real_soprolife_national_configuration(version="SOPROLIFE-REAL-AUTO"),
        actor=users["admin"].id,
    )
    doc = _make_ready_document(db, users, restricted_settings, broncodilatador=True, code="REALAUTO")

    result = run_offline_preflight(db, doc.id, restricted_settings, users["gestor"].id,
                                   certificate=synthetic_certificate)
    assert result.status == "ready_to_send"
    assert result.blockers == []
    rows = db.query(FiscalArtifact).filter_by(document_id=doc.id).all()
    assert len(rows) == 2


def test_real_profile_blocks_closed_when_no_validated_version_exists(
        db, users, restricted_settings, synthetic_certificate):
    doc = _make_ready_document(db, users, restricted_settings, broncodilatador=True, code="REALNONE")
    result = run_offline_preflight(db, doc.id, restricted_settings, users["gestor"].id,
                                   certificate=synthetic_certificate)
    assert result.status == "blocked"
    assert result.stage_reached == Stage.NATIONAL_CONFIG
    assert result.blockers == ["national_dps_configuration_not_defined_for_any_real_document"]
