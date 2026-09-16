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

import contextlib
import os
import ssl
import stat
import tempfile
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


# M37 — the ADN (Ambiente de Dados Nacional) restricted host is a
# distribution/query service, never an issuance one: the official
# contributor manuals confirm it documents GET /DFe/{NSU} and NFS-e/event
# consultation, not DPS issuance, and a live read-only route sweep
# (2026-09-15) proved it directly — HEAD/GET/OPTIONS on this host's
# candidate ``/nfse`` paths all 404 with no ``Allow`` header (opaque,
# route-not-found), while the confirmed Sefin Nacional issuance host
# answers those same methods with 405 + ``Allow: POST``. This transport is
# the ONLY thing that ever sends a real POST /nfse, so this check alone is
# enough to make "issuance accidentally targets ADN" structurally
# impossible — never a guess, never dependent on `m31-restricted.env`
# being edited correctly by hand.
FORBIDDEN_ISSUANCE_HOSTS = frozenset({"adn.producaorestrita.nfse.gov.br"})


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
    # M41 — the ONLY response header ever carried past this boundary (see
    # response_diagnostics.py). Every other header (any cookie, trace id,
    # server banner, etc.) is never read here and never exists on this
    # object — there is no field to carry it, by construction.
    content_type: str | None = None


class RestrictedTransport(Protocol):
    def send(self, request: TransportRequest) -> TransportResponse: ...


class ProductionTransport:
    """Deliberately non-functional. Production network access does not
    exist in this codebase — not "disabled", ABSENT."""

    def send(self, request: TransportRequest) -> TransportResponse:
        raise NetworkGateClosedError("production_transport_not_implemented")


def _write_private_file(path: str, data: bytes) -> None:
    """Create ``path`` with content ``data``, permissions 0600, refusing to
    follow/overwrite an existing file at that path."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(path)
        raise


def _build_client_ssl_context(certificate_pem: bytes, key_pem: bytes) -> ssl.SSLContext:
    """Build a verified ``ssl.SSLContext`` with the client (mTLS) certificate
    loaded from in-memory PEM bytes.

    ``ssl.SSLContext.load_cert_chain`` (stdlib) only accepts file paths, not
    PEM content, so the bytes are written to an ephemeral, tightly-permissioned
    temporary directory (0700) as 0600 files, loaded, and removed immediately
    afterwards — on both success and failure. Nothing here is ever persisted,
    logged, or returned; only the resulting ``SSLContext`` object leaves this
    function. Server certificate verification is preserved by construction:
    ``ssl.create_default_context()`` defaults to ``CERT_REQUIRED`` with
    hostname checking and the system CA trust store, same as httpx's own
    default when no custom context is supplied.
    """
    context = ssl.create_default_context()
    tmp_dir = tempfile.mkdtemp(prefix="soprolife-nfse-mtls-")
    os.chmod(tmp_dir, stat.S_IRWXU)
    cert_path = os.path.join(tmp_dir, "client-cert.pem")
    key_path = os.path.join(tmp_dir, "client-key.pem")
    try:
        _write_private_file(cert_path, certificate_pem)
        _write_private_file(key_path, key_pem)
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    finally:
        for path in (cert_path, key_path):
            with contextlib.suppress(OSError):
                os.remove(path)
        with contextlib.suppress(OSError):
            os.rmdir(tmp_dir)
    return context


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
        if httpx.URL(self._base_url).host in FORBIDDEN_ISSUANCE_HOSTS:
            raise NetworkGateClosedError("restricted_base_url_must_not_be_adn_distribution_host")

    def send(self, request: TransportRequest) -> TransportResponse:
        self._assert_gate_open()
        # httpx's `cert=` boundary hands its value straight to
        # ssl.SSLContext.load_cert_chain(), which requires file PATHS, not
        # PEM content — passing PEM bytes there fails before any socket is
        # ever touched (see test_nfse_national_transport.py). The verified
        # SSLContext built by _build_client_ssl_context() is instead supplied
        # through `verify=`, httpx's supported way to hand it a fully custom,
        # already-verifying context (server verification stays on; this is
        # not `verify=False`).
        verify: bool | ssl.SSLContext = True
        if self._mtls_certificate_pem and self._mtls_key_pem:
            verify = _build_client_ssl_context(self._mtls_certificate_pem, self._mtls_key_pem)
        with httpx.Client(base_url=self._base_url, timeout=self._timeout_seconds, verify=verify) as client:
            response = client.request(request.method, request.path, content=request.body,
                                       headers=request.headers)
        return TransportResponse(status_code=response.status_code, body=response.content,
                                 content_type=response.headers.get("content-type"))


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
