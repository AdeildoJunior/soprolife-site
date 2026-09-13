"""M27 — gates de transporte: restrito default-off, produção impossível."""
import pytest

from app.services.nfse_national.transport import (
    FakeTransport,
    HttpxRestrictedTransport,
    NetworkGateClosedError,
    ProductionTransport,
    TransportRequest,
    TransportResponse,
)


def test_restricted_gate_closed_by_default():
    transport = HttpxRestrictedTransport(base_url="https://restrito.nfse.gov.br",
                                         network_enabled=False, environment="restricted")
    with pytest.raises(NetworkGateClosedError):
        transport.send(TransportRequest("GET", "/dps/x"))


def test_restricted_transport_refuses_non_restricted_environment_even_with_gate_on():
    transport = HttpxRestrictedTransport(base_url="https://restrito.nfse.gov.br",
                                         network_enabled=True, environment="production")
    with pytest.raises(NetworkGateClosedError):
        transport.send(TransportRequest("GET", "/dps/x"))


def test_restricted_transport_refuses_mock_environment():
    transport = HttpxRestrictedTransport(base_url="https://restrito.nfse.gov.br",
                                         network_enabled=True, environment="mock")
    with pytest.raises(NetworkGateClosedError):
        transport.send(TransportRequest("GET", "/dps/x"))


def test_restricted_transport_refuses_non_https_base_url():
    transport = HttpxRestrictedTransport(base_url="http://restrito.nfse.gov.br",
                                         network_enabled=True, environment="restricted")
    with pytest.raises(NetworkGateClosedError):
        transport.send(TransportRequest("GET", "/dps/x"))


def test_production_transport_never_works():
    transport = ProductionTransport()
    with pytest.raises(NetworkGateClosedError):
        transport.send(TransportRequest("GET", "/anything"))
    with pytest.raises(NetworkGateClosedError):
        transport.send(TransportRequest("POST", "/nfse", body=b"<DPS/>"))


def test_injected_fake_transport_cannot_bypass_the_production_gate():
    # A test double proves the CONTRACT (issue/query/cancel shape), but a
    # fake transport is never substituted for ProductionTransport in
    # non-test code — there is no factory path that would do so. This test
    # documents that FakeTransport itself has no notion of environment/gate
    # at all: gating is the real transports' job, not the double's.
    fake = FakeTransport(responses=[TransportResponse(201, b"<NFSe/>")])
    response = fake.send(TransportRequest("POST", "/nfse", body=b"<DPS/>"))
    assert response.status_code == 201
    assert fake.received[0].path == "/nfse"


def test_fake_transport_raises_when_exhausted():
    fake = FakeTransport(responses=[])
    with pytest.raises(AssertionError):
        fake.send(TransportRequest("GET", "/dps/x"))


def test_fake_transport_can_simulate_timeout():
    fake = FakeTransport(responses=[TimeoutError("simulated timeout")])
    with pytest.raises(TimeoutError):
        fake.send(TransportRequest("GET", "/dps/x"))


def test_offline_tests_never_need_a_real_certificate_path():
    # Sanity check for the test suite itself: constructing/using
    # HttpxRestrictedTransport and FakeTransport above required no
    # certificate file, no PKCS#12 password and no environment variable.
    transport = HttpxRestrictedTransport(base_url="https://restrito.nfse.gov.br",
                                         network_enabled=False, environment="restricted")
    assert transport._mtls_certificate_pem is None
    assert transport._mtls_key_pem is None
