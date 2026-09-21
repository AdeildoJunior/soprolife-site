"""M55 — evidence of a SUCCESS, which the codebase never kept.

Until now only 4xx/5xx bodies were ever summarized. A 2xx recorded nothing:
not the bytes we submitted, not the document the government returned, not its
processing timestamp. That is exactly why `provider_malformed_response:http_201`
on a genuinely successful DPS #10 issuance was undiagnosable from the audit
trail and needed four missions plus a live re-query to explain.

These tests pin the closure of that gap. None of them performs I/O against a
network: every transport is a FakeTransport.
"""
import hashlib
import json

import pytest

from app.models import AuditLog, FiscalArtifact
from app.services import nfse
from app.services.nfse_national import dispatch
from app.services.nfse_national.transport import FakeTransport, TransportResponse
from app.services.nfse_national.wire import extract_processing_timestamp
from tests.test_nfse_national_dispatch import (  # noqa: F401 — pytest fixtures
    _validate_active_config, certificate_file, fiscal_enabled,
    fully_configured_settings, restricted_doc, restricted_source,
)
from tests.test_nfse_national_provider import VALID_ACCESS_KEY, _nfse_xml, _success_body

PROCESSED_AT = "2026-09-19T01:57:15.3962113-03:00"


def success_body_with_timestamp(access_key: str = VALID_ACCESS_KEY) -> bytes:
    from app.services.nfse_national.wire import encode_xml_gzip_b64
    return json.dumps({
        "tipoAmbiente": 2,
        "versaoAplicativo": "SefinNacional_1.6.0",
        "dataHoraProcessamento": PROCESSED_AT,
        "chaveAcesso": access_key,
        "nfseXmlGZipB64": encode_xml_gzip_b64(_nfse_xml(access_key)),
    }).encode("utf-8")


def issue(db, users, doc, settings, monkeypatch, response):
    _validate_active_config(db, users)
    fake = FakeTransport(responses=[response])
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)
    return nfse.operate(db, doc.id, "issue", "m55-issue", settings, users["gestor"].id), fake


def audit_detail(db, doc):
    row = db.query(AuditLog).filter_by(
        entidade_id=doc.id, acao="fiscal.issue_completed").order_by(
        AuditLog.ts_utc.desc()).first()
    return row.detalhes or {}


def artifacts(db, doc, kind=None):
    q = db.query(FiscalArtifact).filter_by(document_id=doc.id)
    if kind:
        q = q.filter_by(kind=kind)
    return q.all()


# ------------------------------------------------- dataHoraProcessamento ----
def test_processing_timestamp_is_extracted_from_the_real_shape():
    assert extract_processing_timestamp(success_body_with_timestamp()) == PROCESSED_AT


@pytest.mark.parametrize("body", [
    None, b"", b"<xml/>", b"nao json", b"{}", b"[]",
    json.dumps({"dataHoraProcessamento": 12345}).encode(),
    json.dumps({"dataHoraProcessamento": "   "}).encode(),
    json.dumps({"dataHoraProcessamento": "x" * 65}).encode(),
])
def test_processing_timestamp_is_total_and_never_raises(body):
    assert extract_processing_timestamp(body) is None


def test_processing_timestamp_reaches_the_audit_trail(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    doc, _ = issue(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                   TransportResponse(201, success_body_with_timestamp()))
    assert doc.state == "issued"
    assert audit_detail(db, doc)["sefin_data_hora_processamento"] == PROCESSED_AT


# ----------------------------------------------- shape of a 2xx response ----
def test_a_success_response_now_records_its_shape(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """The exact blind spot behind the DPS #10 saga: a 2xx recorded no
    content_type, no sha256 and no top-level key names."""
    body = success_body_with_timestamp()
    doc, _ = issue(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                   TransportResponse(201, body))
    detail = audit_detail(db, doc)
    assert detail["sefin_resposta_sha256"] == hashlib.sha256(body).hexdigest()
    assert detail["sefin_resposta_tamanho"] == len(body)
    assert "chaveAcesso" in detail["sefin_resposta_chaves_json"]
    assert "nfseXmlGZipB64" in detail["sefin_resposta_chaves_json"]


def test_shape_is_recorded_even_when_a_2xx_body_cannot_be_decoded(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """A 201 we cannot parse must still leave a forensic trail — this is the
    case that produced provider_malformed_response:http_201 with nothing to
    go on."""
    body = json.dumps({"chaveAcesso": "nao-e-uma-chave"}).encode()
    doc, _ = issue(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                   TransportResponse(201, body))
    assert doc.state == "uncertain"
    detail = audit_detail(db, doc)
    assert detail["sefin_resposta_sha256"] == hashlib.sha256(body).hexdigest()
    assert detail["sefin_resposta_chaves_json"] == ["chaveAcesso"]


def test_error_response_shape_is_unchanged(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    body = json.dumps({"erros": [{"codigo": "E0001", "descricao": "x"}]}).encode()
    doc, _ = issue(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                   TransportResponse(400, body))
    assert doc.state == "failed"
    detail = audit_detail(db, doc)
    assert detail["sefin_resposta_sha256"] == hashlib.sha256(body).hexdigest()
    assert detail["sefin_erro_codigos"] == ["E0001"]


# --------------------------------------------------- persisted documents ----
def test_success_persists_the_submitted_dps_and_the_returned_nfse(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    doc, fake = issue(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                      TransportResponse(201, success_body_with_timestamp()))
    assert doc.state == "issued"
    kinds = {a.kind for a in artifacts(db, doc)}
    assert {"dps_signed_xml", "nfse_xml"} <= kinds

    signed = artifacts(db, doc, "dps_signed_xml")[-1]
    root = fully_configured_settings.resolved_fiscal_artifacts_storage_dir()
    on_disk = (root / signed.storage_relative_path).read_bytes()
    assert hashlib.sha256(on_disk).hexdigest() == signed.sha256
    # It is the EXACT payload that went out, recoverable from the artifact.
    sent = json.loads(fake.received[0].body)["dpsXmlGZipB64"]
    from app.services.nfse_national.wire import decode_b64_gzip_xml
    assert decode_b64_gzip_xml(sent) == on_disk

    returned = artifacts(db, doc, "nfse_xml")[-1]
    nfse_on_disk = (root / returned.storage_relative_path).read_bytes()
    assert VALID_ACCESS_KEY.encode() in nfse_on_disk
    assert hashlib.sha256(nfse_on_disk).hexdigest() == returned.sha256


def test_audit_records_only_digests_never_the_documents(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    doc, _ = issue(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                   TransportResponse(201, success_body_with_timestamp()))
    detail = audit_detail(db, doc)
    signed = artifacts(db, doc, "dps_signed_xml")[-1]
    returned = artifacts(db, doc, "nfse_xml")[-1]
    assert detail["evidencia_dps_enviado_sha256"] == signed.sha256
    assert detail["evidencia_nfse_recebida_sha256"] == returned.sha256
    blob = json.dumps(detail)
    # Field NAMES are allowed and intentional (M41 records top-level keys);
    # what must never appear is CONTENT: XML, the gzip/Base64 payload, or any
    # unbounded value.
    assert "<NFSe" not in blob
    assert "<infDPS" not in blob
    assert "H4sI" not in blob                      # gzip magic in Base64
    assert max(len(str(v)) for v in detail.values()) <= 128
    assert detail["sefin_resposta_chaves_json"] == [
        "tipoAmbiente", "versaoAplicativo", "dataHoraProcessamento",
        "chaveAcesso", "nfseXmlGZipB64"]           # names only, never values


def test_artifacts_are_linked_to_the_completed_attempt(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    doc, _ = issue(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                   TransportResponse(201, success_body_with_timestamp()))
    from app.models import FiscalAttempt
    completed = db.query(FiscalAttempt).filter_by(
        document_id=doc.id, phase="completed").one()
    for art in artifacts(db, doc):
        if art.kind in {"dps_signed_xml", "nfse_xml"} and art.attempt_id is not None:
            assert art.attempt_id == completed.id


def test_a_rejection_persists_the_submitted_dps_but_no_nfse(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """Even a rejected issuance must leave the exact bytes we sent — that is
    what a fiscal dispute needs."""
    doc, _ = issue(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                   TransportResponse(400, json.dumps({"erros": []}).encode()))
    assert doc.state == "failed"
    kinds = {a.kind for a in artifacts(db, doc)}
    assert "dps_signed_xml" in kinds
    assert not artifacts(db, doc, "nfse_xml")


# ------------------------------------------------------- fail-safe ---------
def test_evidence_failure_never_fails_the_fiscal_operation(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """A full disk must not turn a recorded success into a phantom."""
    from app.services.nfse_national import artifacts as artifact_storage

    def boom(*a, **k):
        raise OSError("disco cheio")

    monkeypatch.setattr(artifact_storage, "write_artifact", boom)
    doc, _ = issue(db, users, restricted_doc, fully_configured_settings, monkeypatch,
                   TransportResponse(201, success_body_with_timestamp()))
    assert doc.state == "issued"             # the fiscal outcome stands
    detail = audit_detail(db, doc)
    assert detail["evidencia_persistencia_falhou"] is True
    assert detail["sefin_data_hora_processamento"] == PROCESSED_AT


def test_mock_provider_is_unaffected(db, users, fiscal_enabled):
    """The mock sets none of the new fields; its path must not change."""
    from app.services.nfse_providers import MockNfseProvider, ProviderRequest
    result = MockNfseProvider().issue(ProviderRequest(
        document_id="d", operation_id="o", preparation_id="p",
        amount="1.00", competence="2026-09-15", description="x"))
    assert result.submitted_document is None
    assert result.returned_document is None
    assert result.provider_processed_at is None
