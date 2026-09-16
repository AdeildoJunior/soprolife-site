"""Offline automation foundation — safety-gate checklist (mission section I).

Each test below maps to exactly one required guarantee. Several gates were
already proven by M26/M27 unit tests at a lower level (cited in each
docstring); this file re-asserts them at the boundary a reviewer actually
cares about, in one place, so the checklist is auditable without hunting
across a dozen files.
"""
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.config import Settings
from app.models import FinancialEntry, Person, SpirometryExam
from app.services import nfse
from app.services.nfse_national.config import NationalDpsConfiguration
from app.services.nfse_national.dps_builder import Recipient
from app.services.nfse_national.identifiers import DpsIdComponents
from app.services.nfse_national.provider import RestrictedIssueContext, RestrictedNfseProvider
from app.services.nfse_national.signer import generate_synthetic_test_certificate, load_pkcs12_certificate
from app.services.nfse_national.transport import (
    FakeTransport,
    HttpxRestrictedTransport,
    NetworkGateClosedError,
    ProductionTransport,
    TransportRequest,
)
from app.services.nfse_providers import ProviderRequest, get_provider
from tests.test_nfse_foundation import policy_payload


@pytest.fixture
def national_config():
    return NationalDpsConfiguration(
        version='SYNTH-GATE-v1', layout_version='restricted-v1.01-20260727',
        issuer_cnpj='11222333000181', issuer_name='SOPROLIFE SAUDE LTDA (SINTETICO)',
        issuer_municipio_ibge='3304557', issuer_op_simp_nac=3,
        issuer_reg_ap_trib_sn=1, issuer_reg_esp_trib=0,
        codigo_tributacao_nacional='140501',
        trib_issqn=1, tp_ret_issqn=1,
        amount_basis='financial_entry.valor', competence_rule='service_date',
        own_revenue_confirmed=True, validation_reference='SYNTHETIC-ONLY',
        p_tot_trib_sn=Decimal('6.00'),
    )


def _context(national_config):
    p12_bytes, password = generate_synthetic_test_certificate()
    certificate = load_pkcs12_certificate(p12_bytes, password)
    dps_id = DpsIdComponents(codigo_municipio='3304557', tipo_inscricao_federal=2,
                             inscricao_federal='11222333000181', serie_dps='00001',
                             numero_dps='000000000000001')
    return RestrictedIssueContext(config=national_config, dps_id=dps_id,
                                  recipient=Recipient(nome='Paciente Um', sem_nif_motivo=1),
                                  ver_aplic='sl-gate-0.1', numero_dps_display='1',
                                  serie_dps_display='1', certificate=certificate,
                                  municipio_prestacao_ibge='3304557')


# 1. Default cannot send: nfse_enabled=False refuses even the mock path.
def test_default_disabled_cannot_send(db):
    with pytest.raises(Exception):
        get_provider(Settings())


# 2. Missing credential cannot send: no certificate/password configured ->
# get_provider() for any non-mock environment stays 503 (M26, unchanged),
# and the restricted transport itself has no notion of a credential-less call.
def test_missing_credential_cannot_send():
    from fastapi import HTTPException
    settings = Settings(nfse_enabled=True, nfse_environment='restricted')
    with pytest.raises(HTTPException) as exc:
        get_provider(settings)
    assert exc.value.detail['codigo'] == 'real_provider_disabled'


# 3. Missing fiscal policy cannot send: evaluate() blocks with policy_missing,
# and nfse.operate() refuses to issue a document that isn't pending+eligible.
def test_missing_fiscal_policy_cannot_send(db, users):
    settings = Settings(nfse_enabled=True, nfse_environment='mock')
    p = Person(public_code='PES-GATE1', nome_completo='Pessoa Gate', nome_normalizado='pessoa gate')
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code='ESP-GATE1', person_id=p.id, status='Realizado',
                       data_exame=date(2026, 8, 10), data_exame_precisao='dia',
                       modalidade='residencial', broncodilatador=True)
    db.add(e)
    db.commit()
    doc = nfse.prepare(db, e.id, settings, users['gestor'].id)
    assert doc.eligibility == 'blocked'
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        nfse.operate(db, doc.id, 'issue', 'gate-key-1', settings, users['gestor'].id)
    assert exc.value.detail['codigo'] == 'document_not_pending_eligible'


# 4. Invalid schema cannot send: a schema-invalid DPS never reaches signing
# in the restricted provider — proven at the builder/XSD unit level already
# (test_nfse_national_dps_builder.py); here we confirm the provider's own
# _build_signed_dps re-validates before ever calling the transport.
def test_invalid_schema_never_reaches_transport(national_config):
    ctx = _context(national_config)
    transport = FakeTransport(responses=[])  # would raise AssertionError if ever called
    provider = RestrictedNfseProvider(transport=transport, context=ctx)
    bad_request = ProviderRequest(document_id='x', operation_id='op', preparation_id='p',
                                  amount='-1', competence='2026-08-10', description='desc')
    with pytest.raises(Exception):
        provider.issue(bad_request)
    assert transport.received == []  # never sent


# 5. HTTP / non-HTTPS cannot send.
def test_http_transport_refused():
    transport = HttpxRestrictedTransport(base_url='http://restrito.example.gov.br',
                                         network_enabled=True, environment='restricted')
    with pytest.raises(NetworkGateClosedError):
        transport.send(TransportRequest(method='GET', path='/nfse/x'))


# 6. Production cannot send — structurally, not via a flag.
def test_production_transport_has_no_implementation():
    with pytest.raises(NetworkGateClosedError):
        ProductionTransport().send(TransportRequest(method='POST', path='/nfse'))


# 7. FakeTransport cannot accidentally enable production, and the low-level
# provider boundary module stays exactly as pure as before M29 wiring: no
# HTTP client, no certificate reader, no DPS payload, and no import of the
# concrete RestrictedNfseProvider class. (M29 wires the RESTRICTED provider
# into nfse.operate() via a separate module — app.services.nfse_national.dispatch
# — never into this one; see test_nfse_national_dispatch.py for the wiring's
# own fail-closed proof, and the module docstring of `dispatch.py`.)
def test_fake_transport_never_wired_into_live_dispatch(db, users):
    settings = Settings(nfse_enabled=True, nfse_environment='mock')
    provider = get_provider(settings)
    assert provider.name == 'mock'
    assert provider.environment == 'mock'
    # The only way a FakeTransport-backed provider is ever constructed is by
    # a test explicitly building a RestrictedNfseProvider itself — never by
    # any application code path reachable from an HTTP request.
    import ast
    import inspect

    import app.services.nfse_providers as providers_module
    tree = ast.parse(inspect.getsource(providers_module))
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert 'FakeTransport' not in imported_names
    assert 'RestrictedNfseProvider' not in imported_names
    assert 'HttpxRestrictedTransport' not in imported_names
    assert not any('nfse_national' in (node.module or '')
                   for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))


# 8. Uncertain result cannot blind retry — the mock dispatcher's own state
# machine (M26) requires an explicit reconciliation, never a silent reissue.
def test_uncertain_requires_explicit_reconciliation_never_auto_retried(db, users):
    from app.services.nfse_providers import MockNfseProvider
    from fastapi import HTTPException

    class TimeoutProvider(MockNfseProvider):
        def issue(self, request):
            raise httpx.TimeoutException('synthetic timeout')

    settings = Settings(nfse_enabled=True, nfse_environment='mock')
    p = Person(public_code='PES-GATE2', nome_completo='Pessoa Gate2', nome_normalizado='pessoa gate2')
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code='ESP-GATE2', person_id=p.id, status='Realizado',
                       data_exame=date(2026, 8, 10), data_exame_precisao='dia',
                       modalidade='residencial', broncodilatador=True)
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code='LAN-GATE2', tipo='receita', categoria='Espirometria',
                       valor=Decimal('220.00'), status='Recebido', spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    nfse.create_policy(db, policy_payload(version='SYNTH-GATE2', environment='mock', flow='HOME'),
                       users['admin'].id)
    doc = nfse.prepare(db, e.id, settings, users['gestor'].id)
    doc = nfse.operate(db, doc.id, 'issue', 'gate-key-2', settings, users['gestor'].id,
                       provider=TimeoutProvider())
    assert doc.state == 'uncertain'
    # A second plain "issue" attempt must be refused, not silently resent.
    with pytest.raises(HTTPException) as exc:
        nfse.operate(db, doc.id, 'issue', 'gate-key-2-retry', settings, users['gestor'].id)
    assert exc.value.detail['codigo'] == 'reconciliation_required'
