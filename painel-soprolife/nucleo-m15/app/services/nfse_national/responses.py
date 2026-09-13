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
        return Outcome.SIMULATED if operation != "cancel" else Outcome.CANCELLED
    if t is TransportOutcome.HTTP_CLIENT_ERROR:
        return Outcome.REJECTED
    # server error, timeout, connection error, malformed response: never a
    # known negative — always uncertain, always requiring reconciliation.
    return Outcome.UNCERTAIN
