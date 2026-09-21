"""Transport boundary for the National NFS-e restricted (homologação) API.

Endpoint paths below come from the current official contributor manual
(``manual-contribuintes-apis-adn.pdf`` / ``manual-contribuintes-emissor-publico-v1-2-out2025.pdf``,
see OFFICIAL_SOURCES_USED.md) — nothing here is a community guess.

Fail-closed contract, enforced in code rather than only by configuration:
- BOTH real transports re-check their own gate on every single call, even if
  the caller already checked it — a stale settings object or a future call
  site that forgets the check still cannot reach the network.
- The two gates are INDEPENDENT (M56). ``HttpxRestrictedTransport`` accepts
  only ``environment='restricted'`` and only the restricted network flag;
  ``HttpxProductionTransport`` accepts only ``environment='production'`` and
  only the production network flag. Neither flag can ever satisfy the other
  transport, so enabling Produção Restrita cannot enable production and vice
  versa — proven by test, not only by naming.
- ``ProductionTransport`` is constructed CLOSED: every one of its gate
  arguments defaults to the refusing value, so ``ProductionTransport()``
  with no arguments still raises on ``send()``, exactly as it did when the
  class had no implementation at all. Opening it requires naming the
  production environment, the production flag and an allowlisted URL
  together, explicitly, at the call site.
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
    """Raised whenever an operational call is attempted without that
    transport's own explicit network gate enabled, or from an environment
    that transport does not serve. Never bypassable by constructor arguments
    alone: each transport re-raises it from inside ``send()``, per call."""


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

# M56 — the official Sefin Nacional PRODUCTION base URL. A literal constant,
# written out in full, deliberately NOT derived from
# ``M15_NFSE_RESTRICTED_BASE_URL`` by any string operation: a ``replace()``
# of "producaorestrita" would silently follow a typo, a stale value or an
# operator's hand-edit straight into production. There is no environment
# variable for this URL at all, so there is no arbitrary-URL surface to
# validate in the first place — the only way to send somewhere else is to
# edit this line, in a reviewed commit.
PRODUCTION_BASE_URL = "https://sefin.nfse.gov.br/SefinNacional"

# The ONLY host a production issuance may ever reach. An allowlist, not a
# denylist: an unknown host fails closed. The ADN distribution host is
# additionally refused by FORBIDDEN_ISSUANCE_HOSTS above, so it is barred
# twice over — once for not being on this list, once for being a known
# non-issuance service.
ALLOWED_PRODUCTION_HOSTS = frozenset({"sefin.nfse.gov.br"})


def assert_production_base_url(base_url: str) -> str:
    """Return ``base_url`` normalized, or raise ``NetworkGateClosedError``.

    Four independent refusals, each with its own reason code: not a string
    with content, not HTTPS, host not on the production allowlist, host on
    the forbidden-issuance list. Called from the production transport's
    per-call gate, never once at construction — a mutated attribute is
    caught on the next send, not trusted from the last one.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise NetworkGateClosedError("production_base_url_missing")
    normalized = base_url.strip().rstrip("/")
    if not normalized.startswith("https://"):
        raise NetworkGateClosedError("production_base_url_must_be_https")
    host = httpx.URL(normalized).host
    if host in FORBIDDEN_ISSUANCE_HOSTS:
        raise NetworkGateClosedError("production_base_url_must_not_be_adn_distribution_host")
    if host not in ALLOWED_PRODUCTION_HOSTS:
        raise NetworkGateClosedError("production_base_url_host_not_allowlisted")
    return normalized


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


class _HttpxMtlsTransport:
    """Shared mTLS HTTP machinery for both real environments.

    Everything below is environment-agnostic: building the verified
    ``SSLContext`` from in-memory PEM, an explicit timeout, the single
    request, and narrowing the response down to status/body/content-type.
    What differs between Produção Restrita and Produção is EXACTLY one
    method — ``_assert_gate_open`` — which each subclass implements against
    its own environment name, its own network flag and its own URL rule.

    The M56 production transport therefore inherits the same discipline that
    was proven end to end against the real SEFIN in the restricted cycle
    (DPS #10): the same context construction, the same server verification,
    the same ephemeral key handling, the same single non-retrying call. It
    is not a second implementation that merely resembles the first.

    ``mtls_certificate_pem``/``mtls_key_pem`` are the PEM bytes derived from
    the loaded PKCS#12 (see ``signer.load_pkcs12_certificate``) — no
    subclass ever reads a certificate file itself and none ever sees a
    password.
    """

    def __init__(self, *, base_url: str, network_enabled: bool, environment: str,
                 timeout_seconds: float = 20.0,
                 mtls_certificate_pem: bytes | None = None,
                 mtls_key_pem: bytes | None = None):
        self._base_url = base_url.rstrip("/") if isinstance(base_url, str) else base_url
        self._network_enabled = network_enabled
        self._environment = environment
        self._timeout_seconds = timeout_seconds
        self._mtls_certificate_pem = mtls_certificate_pem
        self._mtls_key_pem = mtls_key_pem

    def _assert_gate_open(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def send(self, request: TransportRequest) -> TransportResponse:
        # Re-checked here, on EVERY call, never at construction: the gate a
        # caller passed in five minutes ago is not evidence about this call.
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
        # `follow_redirects` is left at httpx's default of False on purpose:
        # a 3xx is returned to the caller as a 3xx and classified as a
        # non-success, never silently chased to whatever Location names —
        # which is how a redirect would otherwise escape the host allowlist
        # this class just enforced. There is no retry here either: exactly
        # one request leaves this method, and a failure raises.
        with httpx.Client(base_url=self._base_url, timeout=self._timeout_seconds, verify=verify) as client:
            response = client.request(request.method, request.path, content=request.body,
                                       headers=request.headers)
        return TransportResponse(status_code=response.status_code, body=response.content,
                                 content_type=response.headers.get("content-type"))


class HttpxRestrictedTransport(_HttpxMtlsTransport):
    """Real HTTP transport for Produção Restrita, gated on every call.

    Accepts ONLY ``environment='restricted'`` and ONLY the restricted
    network flag. ``M15_NFSE_PRODUCTION_NETWORK_ENABLED`` is not read here
    and has no field on this object: turning production on cannot turn this
    transport on, and this transport can never be pointed at production by
    an environment value it refuses outright.
    """

    def _assert_gate_open(self) -> None:
        if self._environment != "restricted":
            raise NetworkGateClosedError("only_restricted_environment_may_use_this_transport")
        if not self._network_enabled:
            raise NetworkGateClosedError("restricted_network_gate_disabled")
        if not self._base_url.startswith("https://"):
            raise NetworkGateClosedError("restricted_base_url_must_be_https")
        if httpx.URL(self._base_url).host in FORBIDDEN_ISSUANCE_HOSTS:
            raise NetworkGateClosedError("restricted_base_url_must_not_be_adn_distribution_host")
        # M56 — Produção Restrita may never address the production host,
        # however the URL got here. The restricted gate being open says
        # nothing about production, and a restricted run that reached
        # sefin.nfse.gov.br would be a real issuance wearing a homologation
        # label: tpAmb=2 sent to the production endpoint.
        if httpx.URL(self._base_url).host in ALLOWED_PRODUCTION_HOSTS:
            raise NetworkGateClosedError("restricted_transport_must_not_target_production_host")


class HttpxProductionTransport(_HttpxMtlsTransport):
    """Real HTTP transport for PRODUÇÃO, gated on every call.

    Constructed CLOSED. Every gate argument defaults to the refusing value —
    ``network_enabled=False``, ``environment=""`` — so the no-argument form
    ``HttpxProductionTransport()`` raises on ``send()`` just as the old
    unimplemented stub did. Opening it takes three explicit, simultaneous
    decisions at the call site: name the production environment, pass the
    production network flag as True, and supply an allowlisted URL.

    The default ``base_url`` is the official constant, so the common case
    never has an opportunity to mistype it; the allowlist is still enforced
    per call, so a caller that overrides it gains nothing.

    Note what this class does NOT do. It has no retry, no fallback to
    another host, no redirect following, and no notion of "try restricted
    instead". A failed production call fails — the caller reconciles (see
    ``provider.query`` / ``nfse.operate``'s uncertain path) rather than
    sending a second DPS.
    """

    def __init__(self, *, base_url: str = PRODUCTION_BASE_URL, network_enabled: bool = False,
                 environment: str = "", timeout_seconds: float = 20.0,
                 mtls_certificate_pem: bytes | None = None,
                 mtls_key_pem: bytes | None = None):
        super().__init__(base_url=base_url, network_enabled=network_enabled,
                         environment=environment, timeout_seconds=timeout_seconds,
                         mtls_certificate_pem=mtls_certificate_pem,
                         mtls_key_pem=mtls_key_pem)

    def _assert_gate_open(self) -> None:
        if self._environment != "production":
            raise NetworkGateClosedError("only_production_environment_may_use_this_transport")
        if not self._network_enabled:
            # The M56 gate, independent of M15_NFSE_RESTRICTED_NETWORK_ENABLED:
            # this object has no field that the restricted flag could ever
            # reach, so no amount of restricted configuration opens it.
            raise NetworkGateClosedError("production_network_gate_disabled")
        # HTTPS, allowlisted host and not-the-ADN-host, each with its own
        # reason code. Re-validated per call, never cached from __init__.
        assert_production_base_url(self._base_url)


# Kept under its original, widely-referenced name. Before M56 this class had
# no implementation at all and raised unconditionally; it now carries the
# real production transport, but its DEFAULTS still refuse, so
# ``ProductionTransport().send(...)`` raises ``NetworkGateClosedError``
# exactly as before — the pre-M56 safety tests continue to pass unchanged.
ProductionTransport = HttpxProductionTransport


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
