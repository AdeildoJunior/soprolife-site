"""M54 — reconciling an `uncertain` document from official proof, offline.

DPS #10 was really issued (HTTP 201) but read as malformed, so it sat in
`uncertain` with no external_id. These tests pin the transition that closes
that gap using the domain's OWN reconcile operation — never a raw UPDATE,
never a new state, never a second issuance.

Every transport here is a FakeTransport: no test in this file performs I/O.
"""
import json

import pytest
from fastapi import HTTPException

from app.models import FiscalAttempt
from app.services import nfse
from app.services.nfse_national import dispatch
from app.services.nfse_national.transport import (FakeTransport, PATH_GET_DPS,
                                                  TransportResponse)
from app.services.nfse_national.wire import encode_xml_gzip_b64
from tests.test_nfse_national_dispatch import (  # noqa: F401 — pytest fixtures
    _validate_active_config, certificate_file, fiscal_enabled,
    fully_configured_settings, restricted_doc, restricted_source,
)
from tests.test_nfse_national_dispatch import _sent_dps_xml
from tests.test_nfse_national_provider import VALID_ACCESS_KEY, _nfse_xml

BARE_KEY = VALID_ACCESS_KEY[3:]
OTHER_KEY = VALID_ACCESS_KEY[:-1] + ("0" if VALID_ACCESS_KEY[-1] != "0" else "1")


def dps_query_body(key: str = BARE_KEY) -> bytes:
    """The real GET /dps/{idDPS} shape: the key, no document."""
    return json.dumps({
        "tipoAmbiente": 2,
        "versaoAplicativo": "SefinNacional_1.6.0",
        "dataHoraProcessamento": "2026-09-19T01:13:12.5527997-03:00",
        "chaveAcesso": key,
    }).encode("utf-8")


def envelope_body(declared_key: str, xml_key: str) -> bytes:
    """A GET body carrying a full NFS-e; the two channels may disagree."""
    return json.dumps({
        "chaveAcesso": declared_key,
        "nfseXmlGZipB64": encode_xml_gzip_b64(_nfse_xml(xml_key)),
    }).encode("utf-8")


def _issue_then(db, users, doc, settings, monkeypatch, responses):
    """Drive one issue that lands UNCERTAIN, then queue `responses`."""
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[TransportResponse(status_code=500, body=b"")] + responses)
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)
    doc = nfse.operate(db, doc.id, "issue", "m54-issue", settings, users["gestor"].id)
    assert doc.state == "uncertain", doc.state
    return doc, fake


def completed(db, doc):
    return [a for a in db.query(FiscalAttempt).filter_by(
        document_id=doc.id, phase="completed").order_by(FiscalAttempt.number).all()]


def issue_operations(db, doc):
    return {a.operation_id for a in db.query(FiscalAttempt).filter_by(
        document_id=doc.id, operation="issue", phase="started").all()}


# ------------------------------------------------------- the happy path -----
def test_uncertain_plus_official_proof_converges_to_success(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    doc, fake = _issue_then(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                            [TransportResponse(200, dps_query_body())])
    doc = nfse.operate(db, doc.id, "reconcile", "m54-rec", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "issued"             # the domain's success state for an issue
    last = completed(db, doc)[-1]
    assert last.operation == "reconcile"
    assert last.outcome == "issued"
    assert last.external_id == VALID_ACCESS_KEY   # TSIdNFSe storage convention
    assert last.reconciliation_required is False


def test_reconcile_clears_reconciliation_required_in_the_api_contract(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    doc, _ = _issue_then(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                         [TransportResponse(200, dps_query_body())])
    assert nfse.serialize_document(db, doc)["reconciliation_required"] is True
    doc = nfse.operate(db, doc.id, "reconcile", "m54-rec", fully_configured_settings,
                       users["gestor"].id)
    assert nfse.serialize_document(db, doc)["reconciliation_required"] is False


def test_reconcile_queries_and_never_issues_again(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    doc, fake = _issue_then(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                            [TransportResponse(200, dps_query_body())])
    before = issue_operations(db, doc)
    doc = nfse.operate(db, doc.id, "reconcile", "m54-rec", fully_configured_settings,
                       users["gestor"].id)
    assert issue_operations(db, doc) == before      # no second issue operation
    assert len(fake.received) == 2                  # the POST, then one GET
    assert fake.received[1].method == "GET"
    assert fake.received[1].path.startswith("/dps/")
    assert not any(r.method == "POST" for r in fake.received[1:])


def test_reconcile_points_at_the_original_operation(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    doc, _ = _issue_then(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                         [TransportResponse(200, dps_query_body())])
    original = db.query(FiscalAttempt).filter_by(
        document_id=doc.id, operation="issue", phase="started").one().operation_id
    doc = nfse.operate(db, doc.id, "reconcile", "m54-rec", fully_configured_settings,
                       users["gestor"].id)
    rec = [a for a in completed(db, doc) if a.operation == "reconcile"][-1]
    assert rec.reconciles_operation_id == original


def test_reconcile_allocates_no_new_dps_number(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """The reconcile queries the SAME official DPS id the issue submitted,
    and allocates no new number."""
    from lxml import etree

    from app.models import DpsNumberAllocation
    doc, fake = _issue_then(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                            [TransportResponse(200, dps_query_body())])
    before = db.query(DpsNumberAllocation).count()
    submitted_id = etree.fromstring(_sent_dps_xml(fake, 0)).find(
        "{http://www.sped.fazenda.gov.br/nfse}infDPS").get("Id")
    doc = nfse.operate(db, doc.id, "reconcile", "m54-rec", fully_configured_settings,
                       users["gestor"].id)
    assert db.query(DpsNumberAllocation).count() == before
    assert fake.received[1].path == PATH_GET_DPS.format(dps_id=submitted_id)


def test_reconcile_is_idempotent_on_the_same_key(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    doc, fake = _issue_then(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                            [TransportResponse(200, dps_query_body())])
    doc = nfse.operate(db, doc.id, "reconcile", "m54-rec", fully_configured_settings,
                       users["gestor"].id)
    n_attempts = db.query(FiscalAttempt).filter_by(document_id=doc.id).count()
    calls = len(fake.received)
    # Replaying the SAME operation key must not create a second attempt.
    doc = nfse.operate(db, doc.id, "reconcile", "m54-rec", fully_configured_settings,
                       users["gestor"].id)
    assert db.query(FiscalAttempt).filter_by(document_id=doc.id).count() == n_attempts
    assert len(fake.received) == calls
    assert doc.state == "issued"


# ------------------------------------------------------- fail-closed --------
def test_a_reconciled_document_refuses_further_reconciliation(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """Once the document reached its success state, the domain refuses to
    reconcile it again at all — so a later answer offering a DIFFERENT access
    key can never overwrite the recorded one. The guard that fires is the
    state guard, before any provider call; `access_key_conflict` inside
    operate() is defence in depth behind it."""
    doc, fake = _issue_then(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                            [TransportResponse(200, dps_query_body()),
                             TransportResponse(200, dps_query_body(OTHER_KEY[3:]))])
    doc = nfse.operate(db, doc.id, "reconcile", "m54-rec-1", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "issued"
    recorded = completed(db, doc)[-1].external_id
    assert recorded == VALID_ACCESS_KEY
    calls = len(fake.received)

    with pytest.raises(HTTPException) as excinfo:
        nfse.operate(db, doc.id, "reconcile", "m54-rec-2", fully_configured_settings,
                     users["gestor"].id)
    assert excinfo.value.detail["codigo"] == "reconciliation_not_required"
    assert len(fake.received) == calls          # no provider call was made
    assert completed(db, doc)[-1].external_id == recorded   # key unchanged


def test_envelope_whose_xml_belongs_to_another_dps_fails_closed(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """Declared key and the key inside the returned XML must agree."""
    doc, _ = _issue_then(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                         [TransportResponse(200, envelope_body(BARE_KEY, OTHER_KEY))])
    doc = nfse.operate(db, doc.id, "reconcile", "m54-rec", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "uncertain"
    assert completed(db, doc)[-1].external_id is None


@pytest.mark.parametrize("body", [b"", b"{}", b"nao json", b'{"chaveAcesso": "123"}'])
def test_unusable_proof_leaves_the_document_uncertain(
        monkeypatch, db, users, restricted_doc, fully_configured_settings, body):
    doc, _ = _issue_then(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                         [TransportResponse(200, body)])
    doc = nfse.operate(db, doc.id, "reconcile", "m54-rec", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "uncertain"
    assert completed(db, doc)[-1].external_id is None


def test_a_clean_404_is_the_only_proof_of_absence(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    doc, _ = _issue_then(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                         [TransportResponse(404, b"")])
    doc = nfse.operate(db, doc.id, "reconcile", "m54-rec", fully_configured_settings,
                       users["gestor"].id)
    assert doc.state == "failed"
    assert completed(db, doc)[-1].external_id is None
