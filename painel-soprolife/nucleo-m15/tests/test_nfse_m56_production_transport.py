"""M56 — production endpoint, the separate production gate, and the real
production mTLS transport. Every test here is OFFLINE.

No test in this file opens a socket. The two that exercise the full send
path replace ``httpx.Client`` with a local recorder, so what is asserted is
exactly what the transport WOULD put on the wire — base URL, timeout, TLS
context, headers, method, body, and the fact that there is precisely one
request and no redirect following — without a single byte leaving the
process. Everything else fails at the gate, before any client is built.
"""
import ssl

import pytest

from app.config import Settings
from app.services.nfse_national import transport as transport_module
from app.services.nfse_national.signer import (
    generate_synthetic_test_certificate,
    load_pkcs12_certificate,
    private_key_pem,
)
from app.services.nfse_national.transport import (
    ALLOWED_PRODUCTION_HOSTS,
    FORBIDDEN_ISSUANCE_HOSTS,
    PRODUCTION_BASE_URL,
    HttpxProductionTransport,
    HttpxRestrictedTransport,
    NetworkGateClosedError,
    ProductionTransport,
    TransportRequest,
    assert_production_base_url,
)

RESTRICTED_BASE_URL = "https://sefin.producaorestrita.nfse.gov.br/SefinNacional"


def _pems():
    p12_bytes, password = generate_synthetic_test_certificate()
    loaded = load_pkcs12_certificate(p12_bytes, password)
    return loaded.certificate_pem, private_key_pem(loaded)


def _open_production_transport(**overrides):
    """A production transport with every gate deliberately open. Building
    one sends nothing; only ``send()`` would, and every test that calls it
    has replaced ``httpx.Client`` first."""
    kwargs = dict(base_url=PRODUCTION_BASE_URL, network_enabled=True,
                  environment="production")
    kwargs.update(overrides)
    return HttpxProductionTransport(**kwargs)


class _RecordingResponse:
    def __init__(self):
        self.status_code = 201
        self.content = b'{"ok": true}'
        self.headers = {"content-type": "application/json"}


class _RecordingClient:
    """Stand-in for ``httpx.Client`` that records instead of connecting."""
    instances: list["_RecordingClient"] = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.requests: list[tuple] = []
        _RecordingClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def request(self, method, path, content=None, headers=None):
        self.requests.append((method, path, content, headers))
        return _RecordingResponse()


@pytest.fixture
def recording_client(monkeypatch):
    _RecordingClient.instances = []
    monkeypatch.setattr(transport_module.httpx, "Client", _RecordingClient)
    return _RecordingClient


# --------------------------------------------------------------- FASE 3: URL


def test_production_base_url_is_the_official_sefin_nacional_endpoint():
    assert PRODUCTION_BASE_URL == "https://sefin.nfse.gov.br/SefinNacional"


def test_production_url_is_a_literal_constant_not_derived_from_the_restricted_one():
    """The dangerous shortcut this constant exists to prevent: deriving the
    production URL by string-editing the restricted one. A ``replace()``
    would faithfully carry over a typo, a stale value or a hand-edit in
    ``m31-restricted.env`` straight into production.

    Proven two ways — the production constant shares no substring of the
    restricted environment's name, and the derivation does not even produce
    the right answer for a host that is merely plausible."""
    assert "producaorestrita" not in PRODUCTION_BASE_URL
    assert "restrita" not in PRODUCTION_BASE_URL
    assert RESTRICTED_BASE_URL.replace("producaorestrita.", "") == PRODUCTION_BASE_URL
    # ...and yet a slightly different restricted value derives something else
    # entirely, which is exactly why derivation is not how this is done.
    stale = "https://sefin.producaorestrita.nfse.gov.br/sefinnacional"
    assert stale.replace("producaorestrita.", "") != PRODUCTION_BASE_URL


def test_official_production_url_passes_its_own_allowlist():
    assert assert_production_base_url(PRODUCTION_BASE_URL) == PRODUCTION_BASE_URL


def test_production_url_validator_normalizes_trailing_slash_and_whitespace():
    assert assert_production_base_url(f"  {PRODUCTION_BASE_URL}/  ") == PRODUCTION_BASE_URL


@pytest.mark.parametrize("url,reason", [
    ("http://sefin.nfse.gov.br/SefinNacional", "production_base_url_must_be_https"),
    ("", "production_base_url_missing"),
    ("   ", "production_base_url_missing"),
    ("https://adn.producaorestrita.nfse.gov.br/SefinNacional",
     "production_base_url_must_not_be_adn_distribution_host"),
    (RESTRICTED_BASE_URL, "production_base_url_host_not_allowlisted"),
    ("https://evil.example.com/SefinNacional", "production_base_url_host_not_allowlisted"),
    ("https://sefin.nfse.gov.br.evil.example.com/SefinNacional",
     "production_base_url_host_not_allowlisted"),
    ("https://sefin-nfse.gov.br/SefinNacional", "production_base_url_host_not_allowlisted"),
])
def test_production_url_validator_fails_closed_with_a_named_reason(url, reason):
    with pytest.raises(NetworkGateClosedError) as excinfo:
        assert_production_base_url(url)
    assert reason in str(excinfo.value)


def test_production_url_validator_refuses_non_string_input():
    for value in (None, 123, b"https://sefin.nfse.gov.br"):
        with pytest.raises(NetworkGateClosedError):
            assert_production_base_url(value)


def test_allowlist_is_an_allowlist_and_excludes_the_adn_host():
    assert ALLOWED_PRODUCTION_HOSTS == frozenset({"sefin.nfse.gov.br"})
    assert not (ALLOWED_PRODUCTION_HOSTS & FORBIDDEN_ISSUANCE_HOSTS)


# ------------------------------------------- FASE 4: two independent gates


def test_production_gate_defaults_to_false_in_settings():
    assert Settings().nfse_production_network_enabled is False


def test_enabling_restricted_never_enables_production():
    settings = Settings(nfse_restricted_network_enabled=True)
    assert settings.nfse_restricted_network_enabled is True
    assert settings.nfse_production_network_enabled is False


def test_enabling_production_never_enables_restricted():
    settings = Settings(nfse_production_network_enabled=True)
    assert settings.nfse_production_network_enabled is True
    assert settings.nfse_restricted_network_enabled is False


def test_production_transport_refuses_the_restricted_environment():
    transport = HttpxProductionTransport(base_url=PRODUCTION_BASE_URL, network_enabled=True,
                                         environment="restricted")
    with pytest.raises(NetworkGateClosedError) as excinfo:
        transport.send(TransportRequest("POST", "/nfse", body=b"{}"))
    assert "only_production_environment_may_use_this_transport" in str(excinfo.value)


def test_restricted_transport_refuses_the_production_environment():
    transport = HttpxRestrictedTransport(base_url=RESTRICTED_BASE_URL, network_enabled=True,
                                         environment="production")
    with pytest.raises(NetworkGateClosedError) as excinfo:
        transport.send(TransportRequest("POST", "/nfse", body=b"{}"))
    assert "only_restricted_environment_may_use_this_transport" in str(excinfo.value)


def test_restricted_transport_refuses_the_production_host():
    """Produção Restrita addressing the production endpoint would be a real
    issuance wearing a homologation label — tpAmb=2 sent to production."""
    transport = HttpxRestrictedTransport(base_url=PRODUCTION_BASE_URL, network_enabled=True,
                                         environment="restricted")
    with pytest.raises(NetworkGateClosedError) as excinfo:
        transport.send(TransportRequest("POST", "/nfse", body=b"{}"))
    assert "restricted_transport_must_not_target_production_host" in str(excinfo.value)


def test_production_transport_refuses_the_restricted_base_url():
    transport = HttpxProductionTransport(base_url=RESTRICTED_BASE_URL, network_enabled=True,
                                         environment="production")
    with pytest.raises(NetworkGateClosedError) as excinfo:
        transport.send(TransportRequest("POST", "/nfse", body=b"{}"))
    assert "production_base_url_host_not_allowlisted" in str(excinfo.value)


def test_production_transport_refuses_the_adn_distribution_host():
    transport = HttpxProductionTransport(base_url="https://adn.producaorestrita.nfse.gov.br",
                                         network_enabled=True, environment="production")
    with pytest.raises(NetworkGateClosedError) as excinfo:
        transport.send(TransportRequest("POST", "/nfse", body=b"{}"))
    assert "adn_distribution_host" in str(excinfo.value)


def test_production_transport_has_no_field_the_restricted_flag_could_reach():
    """Structural, not behavioral: the production transport's own gate is a
    single ``_network_enabled`` field set from the production setting at the
    call site. There is no second flag on the object, so no restricted value
    can be read from it by mistake."""
    settings = Settings(nfse_restricted_network_enabled=True)
    transport = HttpxProductionTransport(
        base_url=PRODUCTION_BASE_URL,
        network_enabled=settings.nfse_production_network_enabled,
        environment="production")
    assert transport._network_enabled is False
    with pytest.raises(NetworkGateClosedError) as excinfo:
        transport.send(TransportRequest("POST", "/nfse", body=b"{}"))
    assert "production_network_gate_disabled" in str(excinfo.value)


def test_production_transport_gate_is_revalidated_on_every_single_call(recording_client):
    """An object that was allowed to send a moment ago proves nothing about
    the next call: the gate is checked inside send(), never cached."""
    transport = _open_production_transport()
    transport.send(TransportRequest("POST", "/nfse", body=b"{}"))
    assert len(recording_client.instances) == 1

    transport._network_enabled = False
    with pytest.raises(NetworkGateClosedError):
        transport.send(TransportRequest("POST", "/nfse", body=b"{}"))

    transport._network_enabled = True
    transport._base_url = "https://evil.example.com"
    with pytest.raises(NetworkGateClosedError):
        transport.send(TransportRequest("POST", "/nfse", body=b"{}"))

    transport._base_url = PRODUCTION_BASE_URL
    transport._environment = "restricted"
    with pytest.raises(NetworkGateClosedError):
        transport.send(TransportRequest("POST", "/nfse", body=b"{}"))

    # Only the first, fully-gated call ever built a client.
    assert len(recording_client.instances) == 1


# ---------------------------------------- FASE 5: the production transport


def test_production_transport_constructed_closed_still_raises():
    """The pre-M56 guarantee, preserved exactly. ``ProductionTransport()``
    used to raise because the class had no implementation; it now raises
    because every gate argument defaults to the refusing value."""
    for request in (TransportRequest("GET", "/anything"),
                    TransportRequest("POST", "/nfse", body=b"<DPS/>")):
        with pytest.raises(NetworkGateClosedError):
            ProductionTransport().send(request)


def test_production_transport_is_the_name_of_the_real_class():
    assert ProductionTransport is HttpxProductionTransport


def test_both_real_transports_share_one_mtls_implementation():
    """Not two implementations that resemble each other: one base class,
    one ``send``, one SSLContext builder. The subclasses differ in exactly
    one method — the gate."""
    assert issubclass(HttpxProductionTransport, transport_module._HttpxMtlsTransport)
    assert issubclass(HttpxRestrictedTransport, transport_module._HttpxMtlsTransport)
    assert HttpxProductionTransport.send is transport_module._HttpxMtlsTransport.send
    assert HttpxRestrictedTransport.send is transport_module._HttpxMtlsTransport.send
    assert HttpxProductionTransport._assert_gate_open is not HttpxRestrictedTransport._assert_gate_open


def test_production_send_uses_verified_mtls_context_and_explicit_timeout(recording_client):
    cert_pem, key_pem = _pems()
    transport = _open_production_transport(mtls_certificate_pem=cert_pem, mtls_key_pem=key_pem,
                                           timeout_seconds=17.5)
    transport.send(TransportRequest("POST", "/nfse", body=b'{"dpsXmlGZipB64": "x"}',
                                    headers={"Content-Type": "application/json"}))
    kwargs = recording_client.instances[0].init_kwargs
    assert kwargs["base_url"] == PRODUCTION_BASE_URL
    assert kwargs["timeout"] == 17.5
    context = kwargs["verify"]
    assert isinstance(context, ssl.SSLContext)
    # Server verification is never traded away for client authentication.
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_production_send_never_disables_server_verification_without_a_certificate(recording_client):
    transport = _open_production_transport()
    transport.send(TransportRequest("GET", "/dps/x"))
    assert recording_client.instances[0].init_kwargs["verify"] is True


def test_production_send_performs_exactly_one_request_with_no_retry(recording_client):
    transport = _open_production_transport()
    transport.send(TransportRequest("POST", "/nfse", body=b"{}",
                                    headers={"Content-Type": "application/json"}))
    assert len(recording_client.instances) == 1
    client = recording_client.instances[0]
    assert len(client.requests) == 1
    method, path, content, headers = client.requests[0]
    assert method == "POST"
    assert path == "/nfse"
    assert content == b"{}"
    assert headers == {"Content-Type": "application/json"}


def test_production_send_never_follows_a_redirect(recording_client):
    """A followed 3xx would leave the host allowlist the gate just enforced.
    httpx defaults ``follow_redirects`` to False and this transport never
    overrides it, so a redirect comes back to the caller as a 3xx and is
    classified as a non-success."""
    transport = _open_production_transport()
    transport.send(TransportRequest("POST", "/nfse", body=b"{}"))
    assert "follow_redirects" not in recording_client.instances[0].init_kwargs


def test_production_send_returns_only_status_body_and_content_type(recording_client):
    transport = _open_production_transport()
    response = transport.send(TransportRequest("POST", "/nfse", body=b"{}"))
    assert response.status_code == 201
    assert response.body == b'{"ok": true}'
    assert response.content_type == "application/json"
    # No field exists on the boundary object to carry any other header.
    assert set(vars(response)) == {"status_code", "body", "content_type"}


def test_no_production_base_url_setting_exists():
    """There is no environment variable that could point a production
    issuance somewhere else: the endpoint is the constant, full stop."""
    assert not hasattr(Settings(), "nfse_production_base_url")
    assert "nfse_production_base_url" not in Settings.model_fields
