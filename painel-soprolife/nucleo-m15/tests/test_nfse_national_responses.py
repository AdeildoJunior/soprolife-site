"""M27 — classificação de resposta/erro/timeout e mapeamento para Outcome."""
import pytest

from app.services.nfse_national.responses import (
    TransportOutcome,
    classify_issue_response,
    classify_reconcile_response,
    to_provider_outcome,
)
from app.services.nfse_providers import Outcome


def test_http_success_classified_and_mapped_to_simulated():
    classified = classify_issue_response(http_status=201, exc=None, body_valid=True)
    assert classified.transport_outcome == TransportOutcome.HTTP_SUCCESS
    assert to_provider_outcome(classified, operation="issue") == Outcome.SIMULATED


def test_http_success_on_cancel_mapped_to_cancelled():
    classified = classify_issue_response(http_status=200, exc=None, body_valid=True)
    assert to_provider_outcome(classified, operation="cancel") == Outcome.CANCELLED


def test_malformed_2xx_body_is_uncertain_never_success():
    classified = classify_issue_response(http_status=200, exc=None, body_valid=False)
    assert classified.transport_outcome == TransportOutcome.MALFORMED_RESPONSE
    assert to_provider_outcome(classified, operation="issue") == Outcome.UNCERTAIN


def test_client_error_mapped_to_rejected():
    classified = classify_issue_response(http_status=422, exc=None, body_valid=False)
    assert classified.transport_outcome == TransportOutcome.HTTP_CLIENT_ERROR
    assert to_provider_outcome(classified, operation="issue") == Outcome.REJECTED


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_error_is_uncertain_never_rejected_or_success(status):
    classified = classify_issue_response(http_status=status, exc=None, body_valid=False)
    assert classified.transport_outcome == TransportOutcome.HTTP_SERVER_ERROR
    assert to_provider_outcome(classified, operation="issue") == Outcome.UNCERTAIN


def test_timeout_is_uncertain_never_a_known_negative():
    classified = classify_issue_response(http_status=None, exc=TimeoutError("t"), body_valid=False)
    assert classified.transport_outcome == TransportOutcome.TIMEOUT
    assert to_provider_outcome(classified, operation="issue") == Outcome.UNCERTAIN


def test_connection_error_is_uncertain():
    classified = classify_issue_response(http_status=None, exc=ConnectionError("c"), body_valid=False)
    assert classified.transport_outcome == TransportOutcome.CONNECTION_ERROR
    assert to_provider_outcome(classified, operation="issue") == Outcome.UNCERTAIN


def test_no_response_at_all_is_connection_error():
    classified = classify_issue_response(http_status=None, exc=None, body_valid=False)
    assert classified.transport_outcome == TransportOutcome.CONNECTION_ERROR


def test_reconcile_404_is_not_found():
    classified = classify_reconcile_response(http_status=404, exc=None, body_valid=False)
    assert classified.transport_outcome == TransportOutcome.NOT_FOUND
    assert to_provider_outcome(classified, operation="reconcile") == Outcome.NOT_FOUND


def test_reconcile_timeout_is_never_not_found():
    # The central invariant: only a clean, well-formed 404 counts as absence.
    classified = classify_reconcile_response(http_status=None, exc=TimeoutError("t"), body_valid=False)
    assert classified.transport_outcome == TransportOutcome.TIMEOUT
    assert to_provider_outcome(classified, operation="reconcile") == Outcome.UNCERTAIN


def test_reconcile_server_error_is_never_not_found():
    classified = classify_reconcile_response(http_status=500, exc=None, body_valid=False)
    assert classified.transport_outcome == TransportOutcome.HTTP_SERVER_ERROR
    assert to_provider_outcome(classified, operation="reconcile") == Outcome.UNCERTAIN


def test_reconcile_success_maps_to_simulated_evidence():
    classified = classify_reconcile_response(http_status=200, exc=None, body_valid=True)
    assert to_provider_outcome(classified, operation="reconcile") == Outcome.SIMULATED
