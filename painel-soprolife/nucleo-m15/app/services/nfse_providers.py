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


class RealProviderUnavailable:
    """Restricted/production scaffold: deliberately no transport implementation."""
    name = 'unavailable'

    def __init__(self, environment):
        if environment not in {'restricted', 'production'}:
            raise ValueError('unsupported_real_environment')
        self.environment = environment

    def issue(self, request):
        raise RuntimeError('real_provider_not_implemented')

    def query(self, request, operation):
        return self.issue(request)

    cancel = issue


def get_provider(settings: Settings) -> NfseProvider:
    if not settings.nfse_enabled:
        raise HTTPException(503, detail={'codigo': 'nfse_disabled'})
    if settings.nfse_environment != 'mock':
        if not settings.nfse_real_enabled:
            reason = 'real_provider_disabled'
        elif settings.nfse_credentials_path is None:
            reason = 'external_credentials_required'
        else:
            # Even with all flags/path set, no file is read and no call is possible.
            reason = 'real_provider_not_implemented'
        raise HTTPException(503, detail={'codigo': reason})
    return MockNfseProvider()
