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

import httpx
import pytest

from app.services.nfse_national import transport as transport_module
from app.services.nfse_national.signer import (
    generate_synthetic_test_certificate,
    load_pkcs12_certificate,
    private_key_pem,
)
from app.services.nfse_national.transport import (
    PATH_GET_DPS,
    PATH_ISSUE_NFSE,
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


# ------------------------------------------------------------ M37 — Sefin Nacional base + ADN guard
#
# 2026-09-15 live read-only route sweep (never a POST — HEAD/GET/OPTIONS
# only) proved:
#   - https://adn.producaorestrita.nfse.gov.br/nfse            -> 404, opaque
#     (no Allow header, no JSON body) on every method: the route simply
#     isn't there.
#   - https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse
#     -> 405 on every method, `Allow: POST`, JSON body: the route EXISTS
#     and accepts exactly POST. The same host's docs page
#     (.../API/SefinNacional/docs/index) itself loads its own "try it"
#     scripts from this exact `/SefinNacional/...` base, confirming it's
#     the real runtime base, not just a doc-only alias.
# The tests below (a) fail-closed-prove ADN can never be the issuance
# target for this transport, no matter how `m31-restricted.env` is
# misconfigured, and (b) prove — through REAL httpx URL-join logic
# (httpx.MockTransport: no socket, no DNS, but genuine httpx.Client
# base_url+path resolution) — that the confirmed Sefin Nacional base
# resolves issuance/lookup requests to the EXACT URLs the live sweep
# confirmed exist.

SEFIN_NACIONAL_BASE_URL = "https://sefin.producaorestrita.nfse.gov.br/SefinNacional"


def test_send_refuses_adn_restricted_host_even_with_gate_open():
    """Even a fully-open gate (restricted environment, network enabled,
    https) can never send a request to the ADN distribution host through
    this transport — the ONLY transport capable of a real POST /nfse. This
    makes "issuance accidentally targets ADN" impossible by construction,
    not dependent on the config file being edited correctly."""
    transport = HttpxRestrictedTransport(
        base_url="https://adn.producaorestrita.nfse.gov.br",
        network_enabled=True, environment="restricted")
    with pytest.raises(NetworkGateClosedError, match="adn_distribution_host"):
        transport.send(TransportRequest("POST", PATH_ISSUE_NFSE, body=b"<DPS/>"))


def test_send_refuses_adn_restricted_host_with_contribuintes_prefix_too():
    """Same guard, for the /contribuintes-prefixed candidate the live sweep
    also ruled out (candidate B) — the host alone is enough to refuse,
    regardless of whatever path prefix a future misconfiguration adds."""
    transport = HttpxRestrictedTransport(
        base_url="https://adn.producaorestrita.nfse.gov.br/contribuintes",
        network_enabled=True, environment="restricted")
    with pytest.raises(NetworkGateClosedError, match="adn_distribution_host"):
        transport.send(TransportRequest("POST", PATH_ISSUE_NFSE, body=b"<DPS/>"))


def test_sefin_nacional_host_is_not_blocked_by_the_adn_guard():
    """Sanity check for the guard itself: it must be host-specific, never so
    broad it also blocks the now-confirmed-correct Sefin Nacional host."""
    transport = HttpxRestrictedTransport(base_url=SEFIN_NACIONAL_BASE_URL,
                                         network_enabled=False, environment="restricted")
    with pytest.raises(NetworkGateClosedError, match="restricted_network_gate_disabled"):
        transport.send(TransportRequest("GET", "/dps/x"))  # gate-closed, not ADN-guard


class _UrlCapturingTransport(httpx.BaseTransport):
    """A REAL httpx transport backend (so httpx's OWN base_url+path join
    logic runs, unmodified) that never opens a socket — it answers every
    request in-process. Captures the exact outgoing request for assertion."""

    def __init__(self):
        self.last_request: httpx.Request | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        return httpx.Response(200, content=b'{"ok": true}')


_REAL_HTTPX_CLIENT = httpx.Client  # captured at import time, before any monkeypatch can touch it


def _real_httpx_client_factory(capture: _UrlCapturingTransport):
    """Stands in for transport_module.httpx.Client: builds a REAL
    httpx.Client (genuine URL-join behavior) but backed by the in-memory
    capturing transport above instead of a real network transport. `verify`
    is accepted and discarded — MockTransport-style backends never perform
    a TLS handshake, so it has nothing to apply to.

    Uses the ``_REAL_HTTPX_CLIENT`` reference captured above, never the
    module-global ``httpx.Client`` — monkeypatching that global to install
    THIS factory would otherwise make the factory call itself (the
    ``httpx`` module object is shared between this test file and
    ``transport_module``, so patching one patches both)."""
    def factory(*, base_url, timeout, verify=None):  # noqa: ARG001 — verify unused, matches real call shape
        return _REAL_HTTPX_CLIENT(base_url=base_url, timeout=timeout, transport=capture)

    return factory


def test_issue_request_resolves_to_exact_confirmed_sefin_nfse_url(monkeypatch):
    capture = _UrlCapturingTransport()
    monkeypatch.setattr(transport_module.httpx, "Client", _real_httpx_client_factory(capture))
    transport = HttpxRestrictedTransport(base_url=SEFIN_NACIONAL_BASE_URL,
                                         network_enabled=True, environment="restricted")
    transport.send(TransportRequest("POST", PATH_ISSUE_NFSE, body=b"<DPS/>"))
    assert str(capture.last_request.url) == (
        "https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse"
    )
    assert capture.last_request.method == "POST"


def test_dps_lookup_request_resolves_to_exact_confirmed_sefin_dps_url(monkeypatch):
    capture = _UrlCapturingTransport()
    monkeypatch.setattr(transport_module.httpx, "Client", _real_httpx_client_factory(capture))
    transport = HttpxRestrictedTransport(base_url=SEFIN_NACIONAL_BASE_URL,
                                         network_enabled=True, environment="restricted")
    dps_id = "DPS330455726354402600011000001000000000000002"  # shape only, not a real lookup
    transport.send(TransportRequest("GET", PATH_GET_DPS.format(dps_id=dps_id)))
    assert str(capture.last_request.url) == (
        f"https://sefin.producaorestrita.nfse.gov.br/SefinNacional/dps/{dps_id}"
    )
    assert capture.last_request.method == "GET"


def test_issue_request_never_resolves_under_the_adn_host():
    """Belt-and-suspenders: even if the ADN-host guard above were ever
    removed by mistake, this proves the URL httpx would build for the ADN
    base is a DIFFERENT string than the confirmed Sefin Nacional issuance
    URL — the two are not somehow the same endpoint under different names.
    No monkeypatch here: this bypasses the ADN guard deliberately, only to
    inspect the URL httpx would have built — _assert_gate_open() is NOT
    called, this is a pure URL-join check, not a call through send()."""
    capture = _UrlCapturingTransport()
    with _REAL_HTTPX_CLIENT(base_url="https://adn.producaorestrita.nfse.gov.br",
                            transport=capture) as client:
        client.request("POST", PATH_ISSUE_NFSE)
    assert str(capture.last_request.url) == "https://adn.producaorestrita.nfse.gov.br/nfse"
    assert str(capture.last_request.url) != (
        "https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse"
    )
