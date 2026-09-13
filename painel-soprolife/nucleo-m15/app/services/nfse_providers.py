"""Provider boundary. No HTTP client, endpoint, certificate reader or DPS payload.

A future adapter must prove query absence before returning NOT_FOUND. HTTP
404/timeout alone is not such proof. The mock IDs have no fiscal validity.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from fastapi import HTTPException

from ..config import Settings


class Outcome(str, Enum):
    SIMULATED = 'simulated'
    CANCELLED = 'cancelled'
    UNCERTAIN = 'uncertain'
    NOT_FOUND = 'not_found'  # authoritative non-issuance evidence only
    REJECTED = 'rejected'  # known rejection before issuance


@dataclass(frozen=True)
class ProviderResult:
    outcome: Outcome
    external_id: str | None = None


@dataclass(frozen=True)
class ProviderRequest:
    document_id: str
    operation_id: str
    preparation_id: str
    amount: str
    competence: str
    description: str


class NfseProvider(Protocol):
    name: str
    environment: str

    def issue(self, request: ProviderRequest) -> ProviderResult: ...
    def query(self, request: ProviderRequest, operation: str) -> ProviderResult: ...
    def cancel(self, request: ProviderRequest) -> ProviderResult: ...


class MockNfseProvider:
    """Deterministic local simulation; query emulates the completed operation.

    Adverse scenarios are supplied by a test provider, never by HTTP input.
    No personal document number or clinical record enters this interface.
    """
    name = 'mock'
    environment = 'mock'

    def issue(self, request):
        return ProviderResult(Outcome.SIMULATED, 'MOCK-' + request.document_id)

    def query(self, request, operation):
        return self.cancel(request) if operation == 'cancel' else self.issue(request)

    def cancel(self, request):
        return ProviderResult(Outcome.CANCELLED, 'MOCK-' + request.document_id)


class RestrictedProviderPending:
    """M29 — returned by ``get_provider()`` ONLY when every STRUCTURAL
    (settings-only, no database) gate for the restricted environment is
    satisfied. This is deliberately not a working provider: it carries no
    transport, no certificate and no document context. The actual
    ``RestrictedNfseProvider`` is built per-document by
    ``app.services.nfse_national.dispatch.resolve_restricted_provider()``,
    called from ``nfse.operate()`` right before dispatch — which re-checks
    every gate again (including the per-document ones this class cannot see:
    active national tax configuration, recipient identity, fiscal policy)
    and never trusts this marker alone. If ``issue``/``query``/``cancel`` is
    ever called directly on this object, that is itself a bug — it raises
    rather than silently doing nothing.

    This module intentionally never imports ``RestrictedNfseProvider``,
    ``HttpxRestrictedTransport`` or anything from ``nfse_national`` — the
    provider boundary stays exactly as pure as before M29 (no HTTP client,
    no certificate reader, no DPS payload); only ``nfse.operate()`` and
    ``nfse_national.dispatch`` know how to turn this marker into a real call.
    """
    name = 'restricted'
    environment = 'restricted'

    def _unresolved(self):
        raise RuntimeError(
            'RestrictedProviderPending nunca deve ser invocado diretamente — '
            'nfse.operate() precisa resolvê-lo via nfse_national.dispatch antes de usar.'
        )

    issue = query = cancel = _unresolved


def get_provider(settings: Settings) -> NfseProvider:
    if not settings.nfse_enabled:
        raise HTTPException(503, detail={'codigo': 'nfse_disabled'})
    if settings.nfse_environment == 'mock':
        return MockNfseProvider()
    if settings.nfse_environment == 'production':
        # No transport implementation exists for production anywhere in this
        # codebase (structural absence, not a flag) — always unavailable.
        raise HTTPException(503, detail={'codigo': 'production_provider_not_implemented'})
    # environment == 'restricted': STRUCTURAL gates only (settings alone, no
    # database access here) — every one of these must ALSO hold at the
    # per-document resolution step; this is the first of two independent
    # fail-closed checks, never a bypass of the second.
    if not settings.nfse_real_enabled:
        raise HTTPException(503, detail={'codigo': 'real_provider_disabled'})
    if not settings.nfse_restricted_network_enabled:
        raise HTTPException(503, detail={'codigo': 'restricted_network_gate_disabled'})
    if not settings.nfse_restricted_base_url:
        raise HTTPException(503, detail={'codigo': 'restricted_base_url_missing'})
    if settings.nfse_restricted_certificate_path is None:
        raise HTTPException(503, detail={'codigo': 'restricted_certificate_path_missing'})
    if settings.nfse_restricted_certificate_password is None:
        raise HTTPException(503, detail={'codigo': 'restricted_certificate_password_missing'})
    return RestrictedProviderPending()
