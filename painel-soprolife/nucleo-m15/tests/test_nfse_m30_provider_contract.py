"""M30 — provider-contract fixes above the provider boundary.

Two documented M29 limitations are closed here:

1. ``nfse._normalized()`` only ever accepted the mock provider's
   ``MOCK-<uuid>`` external-id shape — a genuine, schema-valid NFS-e access
   key (TSIdNFSe) coming back from the restricted provider was silently
   downgraded to UNCERTAIN. It is now provider-aware.
2. ``nfse.operate()`` never protected the access key as immutable evidence:
   a later call returning a DIFFERENT key for the same document must fail
   closed, never silently replace the one already on record.

These tests exercise ``nfse.operate()``/``nfse._normalized()`` directly with
an injected provider double (the same seam ``test_injected_provider_bypasses_
dispatch_entirely`` in ``test_nfse_national_dispatch.py`` uses) — never a real
transport. The end-to-end dispatch→provider→FakeTransport path for the fixed
response contract is covered separately in
``test_nfse_national_dispatch.py::test_full_wiring_with_real_nfse_response_converges_to_issued``.
"""
import pytest
from fastapi import HTTPException

from app.services import nfse
from app.services.nfse_providers import Outcome, ProviderResult
from tests.test_nfse_foundation import events
from tests.test_nfse_national_provider import VALID_ACCESS_KEY
import tests.test_nfse_national_dispatch as _dispatch

fiscal_enabled = _dispatch.fiscal_enabled  # pytest fixture reuse
certificate_file = _dispatch.certificate_file
fully_configured_settings = _dispatch.fully_configured_settings
restricted_source = _dispatch.restricted_source
restricted_doc = _dispatch.restricted_doc

OTHER_ACCESS_KEY = (
    "NFS" "3304557" "2" "2" "11222333000181" "0000000000002" "2609" "123456789" "1"
)
assert len(OTHER_ACCESS_KEY) == 53
assert OTHER_ACCESS_KEY != VALID_ACCESS_KEY


# --------------------------------------------------------- _normalized() unit tests


def test_normalized_accepts_real_access_key_for_restricted_provider():
    result = ProviderResult(Outcome.ISSUED, VALID_ACCESS_KEY)
    normalized = nfse._normalized(result, "issue", "doc-1", "restricted")
    assert normalized.outcome == Outcome.ISSUED
    assert normalized.external_id == VALID_ACCESS_KEY


def test_normalized_rejects_malformed_access_key_for_restricted_provider():
    result = ProviderResult(Outcome.ISSUED, "NOT-A-REAL-KEY")
    normalized = nfse._normalized(result, "issue", "doc-1", "restricted")
    assert normalized.outcome == Outcome.UNCERTAIN
    assert normalized.external_id is None


_DOC_ID = "11111111-1111-1111-1111-111111111111"  # 36 chars, matches MOCK-<uuid> shape
_MOCK_ID = "MOCK-" + _DOC_ID


def test_normalized_rejects_mock_shaped_id_for_restricted_provider():
    # A MOCK-<uuid> id would only ever indicate a bug — never accepted just
    # because it happens to arrive tagged as the 'restricted' provider.
    result = ProviderResult(Outcome.ISSUED, _MOCK_ID)
    normalized = nfse._normalized(result, "issue", _DOC_ID, "restricted")
    assert normalized.outcome == Outcome.UNCERTAIN


def test_normalized_still_enforces_mock_shape_for_mock_provider():
    # Symmetric guarantee: a real NFS-e access key from the mock provider is
    # equally nonsensical and must not be accepted either.
    result = ProviderResult(Outcome.SIMULATED, VALID_ACCESS_KEY)
    normalized = nfse._normalized(result, "issue", _DOC_ID, "mock")
    assert normalized.outcome == Outcome.UNCERTAIN


def test_normalized_unchanged_behavior_when_provider_name_omitted():
    # Existing (M26-era) callers that never pass provider_name keep the
    # original mock-only contract — no silent behavior change.
    result = ProviderResult(Outcome.SIMULATED, _MOCK_ID)
    assert nfse._normalized(result, "issue", _DOC_ID).outcome == Outcome.SIMULATED


# ------------------------------------- M57 — the two success words are not interchangeable


def test_normalized_refuses_simulated_from_a_real_provider():
    """M57 — SIMULATED means "nothing was issued anywhere". A provider bound to
    a real tax authority saying that about a run carrying a genuine access key
    is self-contradictory: it is a malfunctioning provider, not a success. It
    fails closed to UNCERTAIN (reconcile it) rather than being recorded as a
    success under the wrong word — which is exactly the defect M57 closes."""
    for provider_name in ("restricted", "production"):
        result = ProviderResult(Outcome.SIMULATED, VALID_ACCESS_KEY)
        normalized = nfse._normalized(result, "issue", _DOC_ID, provider_name)
        assert normalized.outcome == Outcome.UNCERTAIN
        assert normalized.external_id is None


def test_normalized_refuses_issued_from_the_mock_provider():
    """The symmetric half, and the one that actually protects the books: the
    mock issues nothing anywhere, so it can never promote a run to ISSUED — not
    with a mock id, and not with a real-looking access key either."""
    for external_id in (_MOCK_ID, VALID_ACCESS_KEY):
        result = ProviderResult(Outcome.ISSUED, external_id)
        assert nfse._normalized(result, "issue", _DOC_ID, "mock").outcome == Outcome.UNCERTAIN


def test_normalized_refuses_issued_when_provider_name_is_omitted():
    """A caller that does not say which provider spoke gets the conservative
    (mock-only) contract, so an omitted provider_name can never be a way to
    smuggle a real issuance past the per-provider check above."""
    result = ProviderResult(Outcome.ISSUED, _MOCK_ID)
    assert nfse._normalized(result, "issue", _DOC_ID).outcome == Outcome.UNCERTAIN


def test_success_state_is_derived_from_the_environment_not_the_label():
    """``nfse.success_state()`` is the single place the terminal success state
    is decided, and it reads the document's OWN environment — never a
    provider-supplied word."""
    assert nfse.success_state("mock") == "simulated"
    assert nfse.success_state("restricted") == "issued"
    assert nfse.success_state("production") == "issued"
    # Both are terminal successes for the state machine's purposes.
    assert nfse.SUCCESS_STATES == {"issued", "simulated"}


# --------------------------------------------------------- operate()-level fixtures


class _ScriptedRestrictedProvider:
    name = "restricted"
    environment = "restricted"

    def __init__(self, result):
        self.result = result
        self.issue_calls = 0
        self.query_calls = 0
        self.cancel_calls = 0

    def issue(self, request):
        self.issue_calls += 1
        return self.result

    def query(self, request, operation):
        self.query_calls += 1
        return self.result

    def cancel(self, request):
        self.cancel_calls += 1
        return self.result


# --------------------------------------------------------- access-key conflict


def test_conflicting_access_key_fails_closed(db, users, restricted_doc, fully_configured_settings):
    """First attempt tentatively records one access key while still
    UNCERTAIN; a later reconcile that reports a DIFFERENT key must never
    overwrite it — the document stays 'uncertain' and the original key is
    the only one ever persisted.
    """
    first = _ScriptedRestrictedProvider(ProviderResult(Outcome.UNCERTAIN, VALID_ACCESS_KEY))
    doc = nfse.operate(db, restricted_doc.id, "issue", "m30-conflict-1", fully_configured_settings,
                       users["gestor"].id, provider=first)
    assert doc.state == "uncertain"
    completed = [a for a in events(db, doc) if a.phase == "completed"]
    assert completed[-1].external_id == VALID_ACCESS_KEY

    second = _ScriptedRestrictedProvider(ProviderResult(Outcome.ISSUED, OTHER_ACCESS_KEY))
    doc = nfse.operate(db, restricted_doc.id, "reconcile", "m30-conflict-2", fully_configured_settings,
                       users["gestor"].id, provider=second)
    assert second.query_calls == 1
    assert doc.state == "uncertain"  # never silently converged to 'issued' on a conflicting key

    completed = [a for a in events(db, doc) if a.phase == "completed"]
    assert completed[-1].outcome == "uncertain"
    assert completed[-1].external_id is None  # the conflicting key was never persisted
    assert completed[-1].error_code == "access_key_conflict"
    # The original key remains the only one ever recorded for this document.
    recorded_ids = {a.external_id for a in completed if a.external_id}
    assert recorded_ids == {VALID_ACCESS_KEY}


def test_matching_access_key_on_reconcile_converges_normally(
        db, users, restricted_doc, fully_configured_settings):
    """Sanity check for the conflict test above: the SAME key on reconcile is
    not a conflict and converges the document to 'issued' as expected."""
    first = _ScriptedRestrictedProvider(ProviderResult(Outcome.UNCERTAIN, VALID_ACCESS_KEY))
    doc = nfse.operate(db, restricted_doc.id, "issue", "m30-match-1", fully_configured_settings,
                       users["gestor"].id, provider=first)
    assert doc.state == "uncertain"

    second = _ScriptedRestrictedProvider(ProviderResult(Outcome.ISSUED, VALID_ACCESS_KEY))
    doc = nfse.operate(db, restricted_doc.id, "reconcile", "m30-match-2", fully_configured_settings,
                       users["gestor"].id, provider=second)
    assert doc.state == "issued"
    completed = [a for a in events(db, doc) if a.phase == "completed"]
    assert completed[-1].external_id == VALID_ACCESS_KEY
    assert completed[-1].error_code is None


# --------------------------------------------------------- duplicate / idempotency


def test_duplicate_response_does_not_create_duplicate_issuance(
        db, users, restricted_doc, fully_configured_settings):
    provider = _ScriptedRestrictedProvider(ProviderResult(Outcome.ISSUED, VALID_ACCESS_KEY))
    doc1 = nfse.operate(db, restricted_doc.id, "issue", "m30-dup-1", fully_configured_settings,
                        users["gestor"].id, provider=provider)
    doc2 = nfse.operate(db, restricted_doc.id, "issue", "m30-dup-1", fully_configured_settings,
                        users["gestor"].id, provider=provider)
    assert doc1.id == doc2.id and doc2.state == "issued"
    assert provider.issue_calls == 1  # the replayed call never reached the provider again
    completed = [a for a in events(db, doc2) if a.phase == "completed"]
    assert len(completed) == 1
    assert completed[0].external_id == VALID_ACCESS_KEY


# --------------------------------------------------------- no blind retry


def test_uncertain_document_blocks_plain_reissue_until_reconciled(
        db, users, restricted_doc, fully_configured_settings):
    """Once a document is 'uncertain', the ONLY legal next step is an
    explicit 'reconcile' — a plain re-issue must be refused before the
    provider is ever invoked again (never a blind retry)."""
    first = _ScriptedRestrictedProvider(ProviderResult(Outcome.UNCERTAIN))
    doc = nfse.operate(db, restricted_doc.id, "issue", "m30-noretry-1", fully_configured_settings,
                       users["gestor"].id, provider=first)
    assert doc.state == "uncertain"

    retry_provider = _ScriptedRestrictedProvider(ProviderResult(Outcome.ISSUED, VALID_ACCESS_KEY))
    with pytest.raises(HTTPException) as error:
        nfse.operate(db, restricted_doc.id, "issue", "m30-noretry-2", fully_configured_settings,
                     users["gestor"].id, provider=retry_provider)
    assert error.value.detail["codigo"] == "reconciliation_required"
    assert retry_provider.issue_calls == 0  # never even reached
