"""M27 — classificação de resposta/erro/timeout e mapeamento para Outcome."""
import pytest

from app.services.nfse_national.responses import (
    TransportOutcome,
    classify_issue_response,
    classify_reconcile_response,
    safe_diagnostic_code,
    to_provider_outcome,
)
from app.services.nfse_providers import Outcome


def test_http_success_classified_and_mapped_to_issued():
    # M57 — this module only ever speaks for a real tax authority, so a clean
    # 2xx is a real issuance: ISSUED, never SIMULATED (which means the mock
    # invented an identifier and nothing exists anywhere).
    classified = classify_issue_response(http_status=201, exc=None, body_valid=True)
    assert classified.transport_outcome == TransportOutcome.HTTP_SUCCESS
    assert to_provider_outcome(classified, operation="issue") == Outcome.ISSUED
    assert to_provider_outcome(classified, operation="issue") != Outcome.SIMULATED


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


def test_reconcile_success_maps_to_issued_evidence():
    classified = classify_reconcile_response(http_status=200, exc=None, body_valid=True)
    assert to_provider_outcome(classified, operation="reconcile") == Outcome.ISSUED


# --------------------------------------------------------- M35 — safe HTTP-status diagnostic


@pytest.mark.parametrize("status,expected", [
    (400, "provider_rejected:http_400"),
    (401, "provider_rejected:http_401"),
    (403, "provider_rejected:http_403"),
    (409, "provider_rejected:http_409"),
    (422, "provider_rejected:http_422"),
])
def test_safe_diagnostic_code_client_error(status, expected):
    classified = classify_issue_response(http_status=status, exc=None, body_valid=False)
    assert safe_diagnostic_code(classified) == expected


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_safe_diagnostic_code_server_error(status):
    classified = classify_issue_response(http_status=status, exc=None, body_valid=False)
    assert safe_diagnostic_code(classified) == f"provider_server_error:http_{status}"


def test_safe_diagnostic_code_malformed_2xx():
    classified = classify_issue_response(http_status=200, exc=None, body_valid=False)
    assert safe_diagnostic_code(classified) == "provider_malformed_response:http_200"


def test_safe_diagnostic_code_timeout():
    classified = classify_issue_response(http_status=None, exc=TimeoutError("t"), body_valid=False)
    assert safe_diagnostic_code(classified) == "provider_timeout"


def test_safe_diagnostic_code_connection_error():
    classified = classify_issue_response(http_status=None, exc=ConnectionError("c"), body_valid=False)
    assert safe_diagnostic_code(classified) == "provider_connection_error"


def test_safe_diagnostic_code_reconcile_not_found():
    classified = classify_reconcile_response(http_status=404, exc=None, body_valid=False)
    assert safe_diagnostic_code(classified) == "provider_confirmed_not_found:http_404"


def test_safe_diagnostic_code_success_is_none():
    """A clean success needs no diagnostic label at all."""
    classified = classify_issue_response(http_status=201, exc=None, body_valid=True)
    assert safe_diagnostic_code(classified) is None


def test_safe_diagnostic_code_never_touches_response_body():
    """Structural guarantee, not just a behavioral one: the function only
    ever receives a ClassifiedResponse (transport_outcome + http_status +
    a fixed internal `detail` label) — there is no body/header parameter
    for it to leak, by construction of ClassifiedResponse itself."""
    import dataclasses
    fields = {f.name for f in dataclasses.fields(
        classify_issue_response(http_status=400, exc=None, body_valid=False))}
    assert fields == {"transport_outcome", "http_status", "detail"}
