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
    # M57 — the two success outcomes are deliberately DIFFERENT words, because
    # they are different facts about the world. ISSUED means a real provider
    # confirmed a real NFS-e exists at a tax authority (restricted included:
    # DPS #10 produced an official document under tpAmb=2). SIMULATED means
    # nothing was issued anywhere — the mock invented an identifier locally.
    # Until M57 both shared the name 'simulated', so a genuine NFS-e was
    # recorded with a word that reads as "nothing happened". Which of the two
    # a provider may return is enforced per provider kind in
    # ``nfse._normalized`` — a mock can never report ISSUED, and a real
    # provider can never report SIMULATED.
    ISSUED = 'issued'
    SIMULATED = 'simulated'
    CANCELLED = 'cancelled'
    UNCERTAIN = 'uncertain'
    NOT_FOUND = 'not_found'  # authoritative non-issuance evidence only
    REJECTED = 'rejected'  # known rejection before issuance


@dataclass(frozen=True)
class ProviderResult:
    outcome: Outcome
    external_id: str | None = None
    # M35 — stable, privacy-safe transport diagnostic (e.g. "provider_rejected:http_400"),
    # never response body/headers. See nfse_national.responses.safe_diagnostic_code().
    # Optional and provider-specific: MockNfseProvider never sets it, so every
    # existing call site (positional or keyword, 1-2 args) is unaffected.
    diagnostic_code: str | None = None
    # M40/M41/M44 — already-sanitized structured validation detail from a
    # 4xx/5xx body's documented shape (``NFSePostResponseErro.erros[]``
    # items — full ``MensagemProcessamento``, M44 — or the flatter
    # ``ResponseErro``; see nfse_national.error_sanitizer and
    # nfse_national.response_diagnostics), as plain dicts with only the
    # keys "codigo"/"descricao"/"complemento"/"mensagem"/"erro"/
    # "parametros" — kept provider-agnostic (no nfse_national import here)
    # the same way diagnostic_code is. Never the raw response body.
    # Optional: every existing call site is unaffected.
    validation_errors: tuple[dict, ...] | None = None
    # M41 — a bounded SHAPE summary of the same 4xx/5xx response, present
    # even when validation_errors is empty (DPS #5's exact case: HTTP 400
    # with no usable erros[]): content_type/content_length/sha256/body_kind/
    # top_level_keys only — never body content. See
    # nfse_national.response_diagnostics.summarize_response_shape().
    response_shape: dict | None = None
    # M55 — SUCCESS-side evidence, the gap that made a real HTTP 201 take four
    # missions to explain. Until now nothing about a 2xx was ever kept: not the
    # bytes we submitted, not the document the government returned, not its
    # processing timestamp. Only 4xx/5xx bodies were ever summarized, so a
    # successful issuance left no forensic trail at all.
    #
    # All three are optional and provider-specific (the mock never sets them),
    # so every existing call site is unaffected. They are EVIDENCE ONLY: the
    # state machine never reads them, exactly like diagnostic_code and
    # response_shape.
    submitted_document: bytes | None = None   # the exact signed DPS bytes sent
    returned_document: bytes | None = None    # the NFS-e XML the API returned
    provider_processed_at: str | None = None  # Sefin `dataHoraProcessamento`


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
