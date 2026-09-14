"""M31 — separa o local da PRESTAÇÃO do serviço da incidência do ISSQN
(mission section H — testes sintéticos golden).

Motivação (ver o relatório M31 completo): três DANFSe REAIS da SoproLife,
revisados por um humano, provam que o local de prestação varia (Rio de
Janeiro OU Niterói) enquanto a incidência do ISSQN nos dois casos permanece
Rio de Janeiro — ou seja, são conceitos fiscais DISTINTOS. O XSD oficial
confirma: `cLocPrestacao` (TCLocPrest, dentro da própria DPS que este
builder emite) é o que o EMISSOR declara; `cLocIncid` (TCInfNFSe) só existe
na NFS-e DEVOLVIDA pelo governo e é "determinado automaticamente pelo
sistema, conforme regras do aspecto espacial da LC 116/03" — nunca algo que
esta fundação calcula ou envia. Por isso nenhum teste abaixo afirma um valor
de incidência dentro do XML que construímos: o que se prova é que
`cLocPrestacao` nunca é confundido com incidência, e que `cLocIncid` nunca é
inventado do nosso lado (ele simplesmente não existe no schema da DPS).

Nenhum dado de paciente real é usado: paciente/CPF/exame/lançamento
sintéticos, certificado sintético, ZERO chamada de rede (FakeTransport nunca
é usado aqui — apenas ``run_offline_preflight``, que não tem import de
transporte algum).
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

RIO_IBGE = "3304557"          # Rio de Janeiro/RJ
NITEROI_IBGE = "3303302"      # Niterói/RJ — confirmado via IBGE (cidades.ibge.gov.br/brasil/rj/niteroi)
SOPROLIFE_CNPJ = "63544026000110"


def real_soprolife_national_configuration(**overrides) -> NationalDpsConfiguration:
    data = dict(
        version="SOPROLIFE-M31-v1", layout_version="restricted-v1.01-20260727",
        issuer_cnpj=SOPROLIFE_CNPJ,
        issuer_name="SoproLife Diagnósticos e Soluções em Saúde LTDA",
        issuer_municipio_ibge=RIO_IBGE,
        issuer_op_simp_nac=3, issuer_reg_ap_trib_sn=1, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional="040201", codigo_tributacao_municipal="001",
        codigo_nbs="123019900",
        trib_issqn=1, tp_ret_issqn=1,
        p_tot_trib_sn=Decimal("6.00"),
        amount_basis="financial_entry.valor", competence_rule="service_date",
        own_revenue_confirmed=True, validation_reference="SOPROLIFE-M31-GOLDEN",
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


def _make_document(db, users, settings, *, code, flow, modalidade, broncodilatador,
                   municipio_atendimento_ibge, valor, cpf="52998224725", create_policy=True):
    p = Person(public_code=f"PES-{code}", nome_completo=f"Paciente Sintético {code}",
              nome_normalizado=f"paciente sintetico {code}".lower(), cpf=cpf)
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code=f"ESP-{code}", person_id=p.id, status="Realizado",
                       data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                       modalidade=modalidade, broncodilatador=broncodilatador,
                       municipio_atendimento_ibge=municipio_atendimento_ibge)
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code=f"LAN-{code}", tipo="receita", categoria="Espirometria",
                       valor=Decimal(valor), status="Recebido", spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    if create_policy:
        # Only ONE validated policy per (environment, flow) may exist for an
        # overlapping date range — a second call with the same `flow` would
        # make evaluate() see two candidates and block on `policy_ambiguous`.
        nfse.create_policy(db, policy_payload(version=f"SYNTH-M31-{flow}", environment="restricted",
                                              flow=flow), users["admin"].id)
    return nfse.prepare(db, e.id, settings, users["gestor"].id), p, e, f


def _activate_real_config(db, users, *, version="SOPROLIFE-M31-ACTIVE"):
    fiscal_config.create_version(
        db, environment="restricted", effective_from=date(2026, 1, 1), validation_state="validated",
        configuration=real_soprolife_national_configuration(version=version), actor=users["admin"].id,
    )


# --------------------------------------------------------------------- golden


def test_direct_rio_reaches_ready_to_send_with_correct_service_location(
        db, users, restricted_settings, synthetic_certificate):
    """Caso A da missão: exame DIRECT (cowork), sem broncodilatador, prestado
    no Rio de Janeiro, valor sintético 230.00."""
    _activate_real_config(db, users, version="SOPROLIFE-M31-A")
    doc, person, exam, entry = _make_document(
        db, users, restricted_settings, code="M31A", flow="DIRECT", modalidade="cowork",
        broncodilatador=False, municipio_atendimento_ibge=RIO_IBGE, valor="230.00")
    assert doc.eligibility == "eligible"

    result = run_offline_preflight(db, doc.id, restricted_settings, users["gestor"].id,
                                   certificate=synthetic_certificate)
    assert result.status == "ready_to_send"
    assert result.stage_reached == Stage.READY
    assert result.blockers == []

    stored_dir = restricted_settings.resolved_fiscal_artifacts_storage_dir()
    unsigned = next(a for a in result.staged_artifacts if a.kind == "dps_unsigned_xml")
    signed = next(a for a in result.staged_artifacts if a.kind == "dps_signed_xml")
    unsigned_xml = (stored_dir / unsigned.relative_path).read_bytes()
    signed_xml = (stored_dir / signed.relative_path).read_bytes()

    for xml in (unsigned_xml, signed_xml):
        assert b"<cTribNac>040201</cTribNac>" in xml
        assert b"<cTribMun>001</cTribMun>" in xml
        assert b"<cNBS>123019900</cNBS>" in xml
        assert b"<pTotTribSN>6.00</pTotTribSN>" in xml
        assert b"<pAliq>" not in xml  # aliquota_percentual never set -> pAliq never emitted, never 6.00
        assert f"<cLocPrestacao>{RIO_IBGE}</cLocPrestacao>".encode() in xml
        assert b"Espirometria sem broncodilatador" in xml
        # cLocIncid (TCInfNFSe) is a RESPONSE-only element the government
        # computes automatically (LC 116/03) — never part of a DPS this
        # builder emits, so it must never appear here.
        assert b"cLocIncid" not in xml

    row = db.query(FiscalArtifact).filter_by(document_id=doc.id).all()
    assert len(row) == 2


def test_home_niteroi_reaches_ready_to_send_with_correct_service_location(
        db, users, restricted_settings, synthetic_certificate):
    """Caso B da missão: exame HOME (residencial), com broncodilatador,
    prestado em Niterói, valor sintético 295.00 — a incidência do ISSQN
    dos exemplos reais permanece Rio, mas isso é calculado pelo GOVERNO a
    partir do serviço, nunca por este builder (ver docstring do módulo)."""
    _activate_real_config(db, users, version="SOPROLIFE-M31-B")
    doc, person, exam, entry = _make_document(
        db, users, restricted_settings, code="M31B", flow="HOME", modalidade="residencial",
        broncodilatador=True, municipio_atendimento_ibge=NITEROI_IBGE, valor="295.00")
    assert doc.eligibility == "eligible"

    result = run_offline_preflight(db, doc.id, restricted_settings, users["gestor"].id,
                                   certificate=synthetic_certificate)
    assert result.status == "ready_to_send"
    assert result.blockers == []

    stored_dir = restricted_settings.resolved_fiscal_artifacts_storage_dir()
    signed = next(a for a in result.staged_artifacts if a.kind == "dps_signed_xml")
    signed_xml = (stored_dir / signed.relative_path).read_bytes()

    assert b"<cTribNac>040201</cTribNac>" in signed_xml
    assert b"<cTribMun>001</cTribMun>" in signed_xml
    assert b"<cNBS>123019900</cNBS>" in signed_xml
    assert b"<pTotTribSN>6.00</pTotTribSN>" in signed_xml
    assert b"<pAliq>" not in signed_xml
    assert b"Espirometria com broncodilatador" in signed_xml
    assert f"<cLocPrestacao>{NITEROI_IBGE}</cLocPrestacao>".encode() in signed_xml
    # The Niterói service must NEVER silently become a Rio one.
    assert f"<cLocPrestacao>{RIO_IBGE}</cLocPrestacao>".encode() not in signed_xml
    assert b"cLocIncid" not in signed_xml
    # amount comes only from FinancialEntry.valor, never a constant.
    assert b"<vServ>295.00</vServ>" in signed_xml
    # competence comes only from the service date, never the issuance clock.
    assert b"<dCompet>2026-08-10</dCompet>" in signed_xml


def test_niteroi_and_rio_produce_genuinely_different_xml(db, users, restricted_settings,
                                                          synthetic_certificate):
    _activate_real_config(db, users, version="SOPROLIFE-M31-DIFF")
    doc_rio, *_ = _make_document(db, users, restricted_settings, code="M31DIFFR", flow="HOME",
                                 modalidade="residencial", broncodilatador=True,
                                 municipio_atendimento_ibge=RIO_IBGE, valor="220.00")
    doc_nit, *_ = _make_document(db, users, restricted_settings, code="M31DIFFN", flow="HOME",
                                 modalidade="residencial", broncodilatador=True,
                                 municipio_atendimento_ibge=NITEROI_IBGE, valor="220.00",
                                 cpf="12345678901")
    result_rio = run_offline_preflight(db, doc_rio.id, restricted_settings, users["gestor"].id,
                                       certificate=synthetic_certificate)
    result_nit = run_offline_preflight(db, doc_nit.id, restricted_settings, users["gestor"].id,
                                       certificate=synthetic_certificate)
    assert result_rio.status == result_nit.status == "ready_to_send"
    stored_dir = restricted_settings.resolved_fiscal_artifacts_storage_dir()
    xml_rio = (stored_dir / next(a for a in result_rio.staged_artifacts
                                if a.kind == "dps_signed_xml").relative_path).read_bytes()
    xml_nit = (stored_dir / next(a for a in result_nit.staged_artifacts
                                if a.kind == "dps_signed_xml").relative_path).read_bytes()
    assert xml_rio != xml_nit
    assert f"<cLocPrestacao>{RIO_IBGE}</cLocPrestacao>".encode() in xml_rio
    assert f"<cLocPrestacao>{NITEROI_IBGE}</cLocPrestacao>".encode() in xml_nit


# ------------------------------------------------------- fail-closed / stale


def test_missing_service_location_blocks_preflight(db, users, restricted_settings,
                                                    synthetic_certificate):
    _activate_real_config(db, users, version="SOPROLIFE-M31-MISSING")
    doc, person, exam, entry = _make_document(
        db, users, restricted_settings, code="M31MISS", flow="HOME", modalidade="residencial",
        broncodilatador=True, municipio_atendimento_ibge=None, valor="220.00")
    # Structural proof there is nothing to fall back to even if the code
    # tried: Person carries no address/city field at all in this domain.
    assert not any(name in {"endereco", "cidade", "municipio", "logradouro", "cep"}
                   for name in Person.__table__.columns.keys())

    result = run_offline_preflight(db, doc.id, restricted_settings, users["gestor"].id,
                                   certificate=synthetic_certificate)
    assert result.status == "blocked"
    assert result.stage_reached == Stage.DPS_BUILD
    assert result.blockers == ["service_location_missing"]


def test_unsupported_service_location_blocks_preflight(db, users, restricted_settings,
                                                        synthetic_certificate):
    """Um código IBGE sintaticamente válido (7 dígitos) mas sem evidência
    real/oficial para esta fundação nunca deve ser aceito silenciosamente."""
    _activate_real_config(db, users, version="SOPROLIFE-M31-UNSUP")
    doc, *_ = _make_document(
        db, users, restricted_settings, code="M31UNSUP", flow="HOME", modalidade="residencial",
        broncodilatador=True, municipio_atendimento_ibge="9999999", valor="220.00")

    result = run_offline_preflight(db, doc.id, restricted_settings, users["gestor"].id,
                                   certificate=synthetic_certificate)
    assert result.status == "blocked"
    assert result.stage_reached == Stage.DPS_BUILD
    assert result.blockers == ["service_location_unsupported"]


def test_service_location_change_after_preparation_is_stale(db, users, restricted_settings,
                                                             synthetic_certificate):
    _activate_real_config(db, users, version="SOPROLIFE-M31-STALE")
    doc, person, exam, entry = _make_document(
        db, users, restricted_settings, code="M31STALE", flow="HOME", modalidade="residencial",
        broncodilatador=True, municipio_atendimento_ibge=RIO_IBGE, valor="220.00")

    first = run_offline_preflight(db, doc.id, restricted_settings, users["gestor"].id,
                                  certificate=synthetic_certificate)
    assert first.status == "ready_to_send"

    # The exam is corrected/edited after the fact (e.g. the intake team
    # realizes the exam actually happened in Niterói) — the ALREADY BUILT
    # preparation snapshot must never be silently reused as if nothing
    # changed.
    exam.municipio_atendimento_ibge = NITEROI_IBGE
    db.commit()

    second = run_offline_preflight(db, doc.id, restricted_settings, users["gestor"].id,
                                   certificate=synthetic_certificate)
    assert second.status == "blocked"
    assert second.stage_reached == Stage.PREPARATION
    assert "preparation_stale" in second.blockers

    # Re-preparing picks up the new, correct municipality and reaches READY
    # again — proving the fix is "re-prepare", never "silently keep going".
    nfse.prepare(db, exam.id, restricted_settings, users["gestor"].id)
    third = run_offline_preflight(db, doc.id, restricted_settings, users["gestor"].id,
                                  certificate=synthetic_certificate)
    assert third.status == "ready_to_send"
    stored_dir = restricted_settings.resolved_fiscal_artifacts_storage_dir()
    signed = next(a for a in third.staged_artifacts if a.kind == "dps_signed_xml")
    signed_xml = (stored_dir / signed.relative_path).read_bytes()
    assert f"<cLocPrestacao>{NITEROI_IBGE}</cLocPrestacao>".encode() in signed_xml


def test_partner_flow_stays_blocked_regardless_of_service_location(db, users, restricted_settings):
    """Pastore/SPLIT permanece bloqueado por modelo de parceria mesmo com um
    município de atendimento estruturado e válido — a M31 não abre uma porta
    nova para esse fluxo."""
    p = Person(public_code="PES-M31PASTORE", nome_completo="Paciente Pastore M31",
              nome_normalizado="paciente pastore m31", cpf="52998224725")
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code="ESP-M31PASTORE", person_id=p.id, status="Realizado",
                       data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                       modalidade="clinica_parceira", broncodilatador=True,
                       municipio_atendimento_ibge=RIO_IBGE)
    db.add(e)
    db.commit()
    doc = nfse.prepare(db, e.id, restricted_settings, users["gestor"].id)
    assert doc.eligibility == "blocked"
    assert "commercial_flow_unsupported" in doc.blocking_reasons


def test_mock_environment_never_blocked_by_service_location(db, users):
    """O gate de local de prestação é um requisito de DOCUMENTO REAL
    (restricted), nunca do fluxo mock genérico — mesmo padrão já usado por
    `service_description.py`/`broncodilatador`, que também só é exigido no
    momento de montar a DPS real, nunca em `evaluate()`."""
    settings = Settings(nfse_enabled=True, nfse_environment="mock")
    p = Person(public_code="PES-M31MOCK", nome_completo="Paciente Mock M31",
              nome_normalizado="paciente mock m31")
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code="ESP-M31MOCK", person_id=p.id, status="Realizado",
                       data_exame=date(2026, 8, 10), data_exame_precisao="dia",
                       modalidade="residencial", broncodilatador=True,
                       municipio_atendimento_ibge=None)
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code="LAN-M31MOCK", tipo="receita", categoria="Espirometria",
                       valor=Decimal("220.00"), status="Recebido", spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    nfse.create_policy(db, policy_payload(version="SYNTH-M31-MOCK", environment="mock", flow="HOME"),
                       users["admin"].id)
    doc = nfse.prepare(db, e.id, settings, users["gestor"].id)
    assert doc.eligibility == "eligible"
    assert doc.blocking_reasons == []
