"""Transport boundary for the National NFS-e restricted (homologação) API.

Endpoint paths below come from the current official contributor manual
(``manual-contribuintes-apis-adn.pdf`` / ``manual-contribuintes-emissor-publico-v1-2-out2025.pdf``,
see OFFICIAL_SOURCES_USED.md) — nothing here is a community guess.

Fail-closed contract, enforced in code rather than only by configuration:
- ``ProductionTransport`` has no working implementation. It exists only so a
  caller can name it; every method unconditionally raises. There is no
  configuration value that makes it functional — production network access
  is impossible by construction, not by a flag that could be flipped.
- ``HttpxRestrictedTransport`` re-checks the network gate on every single
  call, even if the caller already checked it — a stale settings object or a
  future call site that forgets the check still cannot reach the network.
- ``FakeTransport`` is for tests only; it never touches a socket.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

import httpx

# Paths as documented by the current official contributor manual. Kept as
# plain format strings so a layout/version bump only touches this file.
PATH_MUNICIPAL_PARAMETERS = "/parametros_municipais/{codigo_municipio}/convenio"
PATH_ISSUE_NFSE = "/nfse"
PATH_GET_NFSE = "/nfse/{chave_acesso}"
PATH_GET_DPS = "/dps/{dps_id}"
PATH_REGISTER_EVENT = "/nfse/{chave_acesso}/eventos"
PATH_LIST_EVENTS = "/nfse/{chave_acesso}/eventos"


class NetworkGateClosedError(RuntimeError):
    """Raised whenever an operational call is attempted without the explicit
    restricted network gate enabled, or against any environment other than
    'restricted'. Never bypassable by constructor arguments alone."""


@dataclass(frozen=True)
class TransportRequest:
    method: Literal["GET", "POST", "HEAD"]
    path: str
    body: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TransportResponse:
    status_code: int
    body: bytes


class RestrictedTransport(Protocol):
    def send(self, request: TransportRequest) -> TransportResponse: ...


class ProductionTransport:
    """Deliberately non-functional. Production network access does not
    exist in this codebase — not "disabled", ABSENT."""

    def send(self, request: TransportRequest) -> TransportResponse:
        raise NetworkGateClosedError("production_transport_not_implemented")


class HttpxRestrictedTransport:
    """Real HTTP transport for Produção Restrita, gated on every call.

    ``mtls_certificate``/``mtls_key`` are the PEM bytes derived from the
    loaded PKCS#12 (see ``signer.load_pkcs12_certificate``) — this class
    never reads a certificate file itself and never sees a password.
    """

    def __init__(self, *, base_url: str, network_enabled: bool, environment: str,
                 timeout_seconds: float = 20.0,
                 mtls_certificate_pem: bytes | None = None,
                 mtls_key_pem: bytes | None = None):
        self._base_url = base_url.rstrip("/")
        self._network_enabled = network_enabled
        self._environment = environment
        self._timeout_seconds = timeout_seconds
        self._mtls_certificate_pem = mtls_certificate_pem
        self._mtls_key_pem = mtls_key_pem

    def _assert_gate_open(self) -> None:
        if self._environment != "restricted":
            raise NetworkGateClosedError("only_restricted_environment_may_use_this_transport")
        if not self._network_enabled:
            raise NetworkGateClosedError("restricted_network_gate_disabled")
        if not self._base_url.startswith("https://"):
            raise NetworkGateClosedError("restricted_base_url_must_be_https")

    def send(self, request: TransportRequest) -> TransportResponse:
        self._assert_gate_open()
        cert = None
        if self._mtls_certificate_pem and self._mtls_key_pem:
            # httpx accepts (cert_path, key_path) or in-memory via ssl context;
            # a real deployment supplies file paths sourced from the same
            # fail-closed secret boundary as the PKCS#12 password.
            cert = (self._mtls_certificate_pem, self._mtls_key_pem)
        with httpx.Client(base_url=self._base_url, timeout=self._timeout_seconds, cert=cert) as client:
            response = client.request(request.method, request.path, content=request.body,
                                       headers=request.headers)
        return TransportResponse(status_code=response.status_code, body=response.content)


@dataclass
class FakeTransport:
    """Test double. Returns queued responses/exceptions in order; records
    every request it received for assertions. Never performs I/O."""
    responses: list[TransportResponse | Exception] = field(default_factory=list)
    received: list[TransportRequest] = field(default_factory=list)

    def send(self, request: TransportRequest) -> TransportResponse:
        self.received.append(request)
        if not self.responses:
            raise AssertionError("FakeTransport exhausted: no queued response left.")
        next_item = self.responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item
