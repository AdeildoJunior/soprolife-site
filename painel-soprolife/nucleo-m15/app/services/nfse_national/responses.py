"""Response/error/timeout classification, mapped onto the already-approved
uncertain-state model (``app.services.nfse_providers.Outcome``).

The mock foundation's rule stands here too: HTTP evidence alone is never
enough to declare non-issuance. A 404 on ``GET /dps/{id}`` before any issue
attempt succeeded is NOT proof nothing was issued — it is UNCERTAIN unless a
reconciliation query specifically proves absence (see ``classify_reconcile``).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..nfse_providers import Outcome


class TransportOutcome(str, Enum):
    """Finer-grained than ``Outcome``: what actually happened on the wire,
    before any business interpretation."""
    HTTP_SUCCESS = "http_success"          # 2xx with a body that parses/validates
    HTTP_CLIENT_ERROR = "http_client_error"  # 4xx — a definite rejection by the API
    HTTP_SERVER_ERROR = "http_server_error"  # 5xx — provider-side, never proof of anything
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    MALFORMED_RESPONSE = "malformed_response"  # 2xx but body fails XSD/JSON parsing
    NOT_FOUND = "not_found"                # 404 specifically on a reconciliation query


@dataclass(frozen=True)
class ClassifiedResponse:
    transport_outcome: TransportOutcome
    http_status: int | None
    detail: str  # short, stable, safe to log/audit — never raw body/headers


def classify_issue_response(*, http_status: int | None, exc: Exception | None,
                            body_valid: bool) -> ClassifiedResponse:
    """Classify the result of an issuance attempt (POST /nfse)."""
    if exc is not None:
        return _classify_exception(exc)
    if http_status is None:
        return ClassifiedResponse(TransportOutcome.CONNECTION_ERROR, None, "sem_resposta")
    if 200 <= http_status < 300:
        if not body_valid:
            return ClassifiedResponse(TransportOutcome.MALFORMED_RESPONSE, http_status, "corpo_invalido")
        return ClassifiedResponse(TransportOutcome.HTTP_SUCCESS, http_status, "sucesso")
    if 400 <= http_status < 500:
        return ClassifiedResponse(TransportOutcome.HTTP_CLIENT_ERROR, http_status, "rejeicao_cliente")
    return ClassifiedResponse(TransportOutcome.HTTP_SERVER_ERROR, http_status, "erro_servidor")


def classify_reconcile_response(*, http_status: int | None, exc: Exception | None,
                                body_valid: bool) -> ClassifiedResponse:
    """Classify a reconciliation query (GET/HEAD /dps/{id} or GET /nfse/{chave}).

    Only a clean, well-formed 404 on THIS specific query counts as NOT_FOUND —
    every other shape (timeout, 5xx, malformed 200, connection error) is
    UNCERTAIN and must be retried later, never interpreted as absence.
    """
    if exc is not None:
        return _classify_exception(exc)
    if http_status == 404:
        return ClassifiedResponse(TransportOutcome.NOT_FOUND, 404, "confirmadamente_ausente")
    return classify_issue_response(http_status=http_status, exc=exc, body_valid=body_valid)


def _classify_exception(exc: Exception) -> ClassifiedResponse:
    name = type(exc).__name__
    if "Timeout" in name:
        return ClassifiedResponse(TransportOutcome.TIMEOUT, None, "timeout")
    return ClassifiedResponse(TransportOutcome.CONNECTION_ERROR, None, "erro_conexao")


def safe_diagnostic_code(classified: ClassifiedResponse) -> str | None:
    """Stable, privacy-safe diagnostic string for ``FiscalAttempt.error_code``
    — HTTP status only (or a fixed transport-level label when there is no
    status, e.g. a timeout), never response body/header content.

    M34/M35 — the official manuals (``manual-contribuintes-apis-adn.pdf`` /
    ``manual-contribuintes-emissor-publico-v1-2-out2025.pdf``) confirm in
    prose that a rejection returns "a mensagem de erro com o motivo da
    rejeição" (§1.3.2.a) but document no field-level schema for that body —
    no example payload, no error-code table. The only place that schema
    would be defined (the restricted Swagger UI) requires an mTLS client
    certificate even to view (M30 addendum) and was never reached. So this
    function parses NOTHING from the response body — only the already-public,
    already-logged-everywhere HTTP status code, which carries no PII and
    needs no schema to interpret.

    Purely descriptive: never consulted by the state machine, which keys
    off ``Outcome``/``uncertain`` alone (see ``nfse.operate()``) — changing
    this function can never change a document's fiscal state.
    """
    t = classified.transport_outcome
    if t is TransportOutcome.HTTP_CLIENT_ERROR:
        return f"provider_rejected:http_{classified.http_status}"
    if t is TransportOutcome.HTTP_SERVER_ERROR:
        return f"provider_server_error:http_{classified.http_status}"
    if t is TransportOutcome.MALFORMED_RESPONSE:
        return f"provider_malformed_response:http_{classified.http_status}"
    if t is TransportOutcome.NOT_FOUND:
        return "provider_confirmed_not_found:http_404"
    if t is TransportOutcome.TIMEOUT:
        return "provider_timeout"
    if t is TransportOutcome.CONNECTION_ERROR:
        return "provider_connection_error"
    return None  # HTTP_SUCCESS: no diagnostic needed for a clean success


def to_provider_outcome(classified: ClassifiedResponse, *, operation: str) -> Outcome:
    """Map onto the M26 ``Outcome`` enum used by the mock queue's state
    machine, so a future wiring reuses the exact same reconciliation
    contract instead of inventing a parallel one.

    This mapping is intentionally conservative: everything that is not an
    unambiguous, well-formed success or a well-formed reconciliation
    NOT_FOUND collapses to UNCERTAIN.
    """
    t = classified.transport_outcome
    if t is TransportOutcome.NOT_FOUND:
        return Outcome.NOT_FOUND
    if t is TransportOutcome.HTTP_SUCCESS:
        # M57 — this provider only ever talks to a real tax authority
        # (restricted or production), so a clean success here is a real
        # issuance and says so: ISSUED, never SIMULATED. Reporting
        # SIMULATED from this module is now a contract violation that
        # ``nfse._normalized`` downgrades to UNCERTAIN.
        return Outcome.ISSUED if operation != "cancel" else Outcome.CANCELLED
    if t is TransportOutcome.HTTP_CLIENT_ERROR:
        return Outcome.REJECTED
    # server error, timeout, connection error, malformed response: never a
    # known negative — always uncertain, always requiring reconciliation.
    return Outcome.UNCERTAIN
