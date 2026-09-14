"""M27 — gates de transporte: restrito default-off, produção impossível.

M34 adds regression coverage for the mTLS transport boundary itself: the
root cause of the "became UNCERTAIN almost immediately" real-restricted
attempts was passing PEM *content* bytes through httpx's ``cert=`` kwarg,
which stdlib's ``ssl.SSLContext.load_cert_chain()`` (what httpx calls
underneath) requires to be file *paths* — see ``_build_client_ssl_context``
in ``transport.py`` for the fix (a verified ``ssl.SSLContext`` built from
ephemeral, tightly-permissioned temp files, supplied via ``verify=``).
"""
import ssl

import pytest

from app.services.nfse_national import transport as transport_module
from app.services.nfse_national.signer import (
    generate_synthetic_test_certificate,
    load_pkcs12_certificate,
    private_key_pem,
)
from app.services.nfse_national.transport import (
    FakeTransport,
    HttpxRestrictedTransport,
    NetworkGateClosedError,
    ProductionTransport,
    TransportRequest,
    TransportResponse,
    _build_client_ssl_context,
)


def _synthetic_pems(*, common_name: str = "SoproLife M34 Synthetic Test") -> tuple[bytes, bytes]:
    """Only ever a throwaway, explicitly-synthetic cert/key — never the real
    SoproLife certificate, never touches a socket or the PKCS#12 password
    beyond this one local call."""
    p12_bytes, password = generate_synthetic_test_certificate(common_name=common_name)
    certificate = load_pkcs12_certificate(p12_bytes, password)
    return certificate.certificate_pem, private_key_pem(certificate)


class _CapturingClient:
    """Stand-in for httpx.Client: records constructor kwargs, never opens a
    socket. Used to prove what HttpxRestrictedTransport.send() hands to
    httpx without ever letting a real httpx.Client run."""
    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        _CapturingClient.last_kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def request(self, method, path, content=None, headers=None):
        class _Response:
            status_code = 200
            content = b"<synthetic-ok/>"
        return _Response()


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


# ------------------------------------------------------------ M34 — mTLS SSLContext boundary


def test_old_pem_bytes_via_httpx_cert_kwarg_reproduces_root_cause():
    """Documents the M34 root cause: httpx's ``cert=`` kwarg forwards straight
    to ssl.SSLContext.load_cert_chain(), which requires file paths. Handing
    it PEM *content* bytes (the pre-M34 behavior) fails immediately at
    httpx.Client construction — before any socket activity — which is
    exactly why the real restricted attempt went UNCERTAIN almost
    immediately (app/services/nfse.py's blanket ``except Exception`` around
    the provider call). No network is touched: construction itself raises."""
    import httpx

    cert_pem, key_pem = _synthetic_pems()
    with pytest.raises((FileNotFoundError, OSError, ssl.SSLError)):
        httpx.Client(base_url="https://restrito.nfse.gov.br", cert=(cert_pem, key_pem))


def test_build_client_ssl_context_loads_synthetic_certificate():
    cert_pem, key_pem = _synthetic_pems()
    context = _build_client_ssl_context(cert_pem, key_pem)
    assert isinstance(context, ssl.SSLContext)


def test_build_client_ssl_context_preserves_server_verification():
    cert_pem, key_pem = _synthetic_pems()
    context = _build_client_ssl_context(cert_pem, key_pem)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_build_client_ssl_context_removes_temp_material_on_success(monkeypatch, tmp_path):
    tmp_dir = tmp_path / "mtls-success"
    monkeypatch.setattr(transport_module.tempfile, "mkdtemp", lambda prefix="": str(tmp_dir.mkdir() or tmp_dir))
    cert_pem, key_pem = _synthetic_pems()
    _build_client_ssl_context(cert_pem, key_pem)
    assert not (tmp_dir / "client-cert.pem").exists()
    assert not (tmp_dir / "client-key.pem").exists()
    assert not tmp_dir.exists()  # ephemeral directory itself is also removed


def test_build_client_ssl_context_removes_temp_material_on_exception(monkeypatch, tmp_path):
    tmp_dir = tmp_path / "mtls-failure"
    monkeypatch.setattr(transport_module.tempfile, "mkdtemp", lambda prefix="": str(tmp_dir.mkdir() or tmp_dir))
    cert_pem, _matching_key_pem = _synthetic_pems()
    _mismatched_cert_pem, mismatched_key_pem = _synthetic_pems(common_name="SoproLife M34 Mismatched Key")
    with pytest.raises(ssl.SSLError):
        # A key that does not belong to the certificate: OpenSSL's own
        # SSL_CTX_check_private_key rejects this inside load_cert_chain(),
        # giving us a real failure path with no crafted/garbage PEM needed.
        _build_client_ssl_context(cert_pem, mismatched_key_pem)
    assert not (tmp_dir / "client-cert.pem").exists()
    assert not (tmp_dir / "client-key.pem").exists()
    assert not tmp_dir.exists()


def test_build_client_ssl_context_failure_never_leaks_key_material():
    cert_pem, _matching_key_pem = _synthetic_pems()
    _mismatched_cert_pem, mismatched_key_pem = _synthetic_pems(common_name="SoproLife M34 Mismatched Key")
    try:
        _build_client_ssl_context(cert_pem, mismatched_key_pem)
        pytest.fail("expected ssl.SSLError for mismatched cert/key")
    except ssl.SSLError as exc:
        message = str(exc)
        assert mismatched_key_pem.decode("ascii") not in message
        assert cert_pem.decode("ascii") not in message


def test_send_supplies_ssl_context_via_verify_not_cert(monkeypatch):
    """The httpx.Client actually used by send() is stubbed out entirely — no
    socket, no DNS lookup — so this only proves what HttpxRestrictedTransport
    hands to httpx, not a real round trip."""
    monkeypatch.setattr(transport_module.httpx, "Client", _CapturingClient)
    cert_pem, key_pem = _synthetic_pems()
    transport = HttpxRestrictedTransport(base_url="https://restrito.nfse.gov.br",
                                         network_enabled=True, environment="restricted",
                                         mtls_certificate_pem=cert_pem, mtls_key_pem=key_pem)
    response = transport.send(TransportRequest("GET", "/dps/x"))
    assert response.status_code == 200
    assert "cert" not in _CapturingClient.last_kwargs
    verify = _CapturingClient.last_kwargs["verify"]
    assert isinstance(verify, ssl.SSLContext)
    assert verify.verify_mode == ssl.CERT_REQUIRED
    assert verify.check_hostname is True


def test_send_without_client_certificate_still_verifies_server(monkeypatch):
    monkeypatch.setattr(transport_module.httpx, "Client", _CapturingClient)
    transport = HttpxRestrictedTransport(base_url="https://restrito.nfse.gov.br",
                                         network_enabled=True, environment="restricted")
    transport.send(TransportRequest("GET", "/dps/x"))
    assert "cert" not in _CapturingClient.last_kwargs
    assert _CapturingClient.last_kwargs["verify"] is True  # never verify=False


def test_send_never_passes_verify_false(monkeypatch):
    monkeypatch.setattr(transport_module.httpx, "Client", _CapturingClient)
    cert_pem, key_pem = _synthetic_pems()
    transport = HttpxRestrictedTransport(base_url="https://restrito.nfse.gov.br",
                                         network_enabled=True, environment="restricted",
                                         mtls_certificate_pem=cert_pem, mtls_key_pem=key_pem)
    transport.send(TransportRequest("GET", "/dps/x"))
    assert _CapturingClient.last_kwargs.get("verify") is not False
