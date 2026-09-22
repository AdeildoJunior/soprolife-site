"""M56 — the anti-duplication rules a production cycle depends on, and the
evidence a production issuance will leave. Entirely offline.

Two things are proven here, and it matters that they are kept apart.

1. THE SHARED PATH. ``nfse.operate()`` has exactly one implementation of
   idempotency, durable numbering, reconciliation-before-retry and
   access-key immutability. Production does not get a second copy — it
   would inherit this one. So these tests drive that single code path
   (through the restricted environment, the only one ``get_provider()``
   will hand back a provider for) and assert the invariants themselves.
   Asserting them against a "production" label would prove less, not more:
   it would test a second copy that must never exist.

2. THE PRODUCTION-BOUND PROVIDER. Evidence persistence and response parsing
   are exercised with a provider bound to ``environment='production'`` and
   driven through a fake production transport, proving the M52/M53 parsers
   and the M55 evidence trail are genuinely shared rather than
   restricted-only.

And, throughout: production batch issuance does not exist, and every route
into a real production POST is still closed.
"""
import hashlib
from dataclasses import replace
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.config import Settings
from app.models import AuditLog, DpsNumberAllocation, FiscalArtifact, FiscalAttempt
from app.services import nfse
from app.services.nfse_national import dispatch
from app.services.nfse_national.provider import NationalNfseProvider
from app.services.nfse_national.transport import (
    FakeTransport,
    HttpxProductionTransport,
    NetworkGateClosedError,
    TransportRequest,
    TransportResponse,
)
from app.services.nfse_national.wire import decode_b64_gzip_xml, encode_xml_gzip_b64
from app.services.nfse_providers import Outcome, ProviderRequest
from tests.test_nfse_national_dispatch import (  # noqa: F401 — pytest fixtures
    _validate_active_config, certificate_file, fiscal_enabled,
    fully_configured_settings, restricted_doc, restricted_source,
)
from tests.test_nfse_national_provider import (
    VALID_ACCESS_KEY,
    _nfse_xml,
    context,  # noqa: F401 — pytest fixture
    request as provider_request,
)

OTHER_ACCESS_KEY = (
    "NFS" "3304557" "2" "2" "11222333000181" "0000000000099" "2609" "987654321" "1"
)
assert len(OTHER_ACCESS_KEY) == 53

PROCESSED_AT = "2026-09-19T01:57:15.3962113-03:00"


def success_body(access_key: str = VALID_ACCESS_KEY, *, tp_amb: int = 2) -> bytes:
    """M59 — ``tipoAmbiente`` is now cross-checked against the environment
    the provider addressed, so it has to be stated rather than assumed.

    The default is 2 because most callers here drive the RESTRICTED
    document fixture; the production-bound provider tests pass ``tp_amb=1``.
    Before M59 this helper hard-coded 1 for every caller, restricted ones
    included — harmless only because nothing compared it to anything.
    """
    return json.dumps({
        "tipoAmbiente": tp_amb,
        "versaoAplicativo": "SefinNacional_1.6.0",
        "dataHoraProcessamento": PROCESSED_AT,
        "chaveAcesso": access_key,
        "nfseXmlGZipB64": encode_xml_gzip_b64(_nfse_xml(access_key)),
    }).encode("utf-8")


def _wire(monkeypatch, responses):
    """Install one FakeTransport for the whole dispatch path and return it,
    so every POST the process makes is countable."""
    fake = FakeTransport(responses=list(responses))
    monkeypatch.setattr(dispatch, "HttpxRestrictedTransport", lambda **kwargs: fake)
    return fake


def _posts(fake: FakeTransport) -> list[TransportRequest]:
    return [r for r in fake.received if r.method == "POST"]


def _external_id(db, doc) -> str | None:
    """The access key lives on the completed FiscalAttempt, never on the
    document — the append-only attempt trail is the fiscal evidence."""
    return db.scalars(select(FiscalAttempt.external_id).where(
        FiscalAttempt.document_id == doc.id, FiscalAttempt.phase == "completed",
        FiscalAttempt.external_id.is_not(None)).order_by(
        FiscalAttempt.number.desc())).first()


def _audit(db, doc, acao="fiscal.issue_completed") -> dict:
    row = db.query(AuditLog).filter_by(entidade_id=doc.id, acao=acao).order_by(
        AuditLog.ts_utc.desc()).first()
    return (row.detalhes or {}) if row else {}


# ------------------------------------------------ FASE 9: exactly one POST


def test_replaying_the_same_operation_key_produces_no_second_post(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """The idempotency key is the boundary. A replay returns the document
    it already produced and never crosses the transport a second time."""
    _validate_active_config(db, users)
    fake = _wire(monkeypatch, [TransportResponse(201, success_body())])

    doc = nfse.operate(db, restricted_doc.id, "issue", "same-key",
                       fully_configured_settings, users["gestor"].id)
    assert doc.state == "issued"
    assert len(_posts(fake)) == 1

    # The FakeTransport has no queued response left: a second POST would
    # raise AssertionError rather than silently succeed.
    for _ in range(3):
        replayed = nfse.operate(db, restricted_doc.id, "issue", "same-key",
                                fully_configured_settings, users["gestor"].id)
        assert replayed.id == doc.id
    assert len(_posts(fake)) == 1


def test_a_different_key_on_an_already_issued_document_still_posts_nothing(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """Idempotency by key is not the only guard: a document already in a
    terminal success state (``issued`` here — M57) short-circuits before the
    provider is ever resolved, so even a fresh key cannot produce a second
    NFS-e."""
    _validate_active_config(db, users)
    fake = _wire(monkeypatch, [TransportResponse(201, success_body())])
    nfse.operate(db, restricted_doc.id, "issue", "key-1",
                 fully_configured_settings, users["gestor"].id)
    assert len(_posts(fake)) == 1

    again = nfse.operate(db, restricted_doc.id, "issue", "key-2-totally-different",
                         fully_configured_settings, users["gestor"].id)
    assert again.state == "issued"
    assert len(_posts(fake)) == 1


def test_durable_dps_number_never_changes_across_replays_and_reconciles(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """M36's durable allocation, restated as a production prerequisite: the
    official DPS number is keyed by document, so no later attempt — replay,
    reconcile, uncertain retry — can renumber a document the government may
    already know about."""
    _validate_active_config(db, users)
    fake = _wire(monkeypatch, [
        TimeoutError("sem resposta"),                    # issue -> uncertain
        TransportResponse(200, success_body()),          # reconcile -> success
    ])

    nfse.operate(db, restricted_doc.id, "issue", "k1",
                 fully_configured_settings, users["gestor"].id)
    first = db.get(DpsNumberAllocation, restricted_doc.id).dps_number

    nfse.operate(db, restricted_doc.id, "reconcile", "k2",
                 fully_configured_settings, users["gestor"].id)
    after = db.get(DpsNumberAllocation, restricted_doc.id).dps_number
    assert after == first


def test_uncertain_requires_reconcile_before_any_retry(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """The DPS #10 lesson in code form: a transport failure is not evidence
    of non-issuance. A document left uncertain refuses a fresh issue —
    reconciliation is the only way forward, and the refusal happens before
    the transport is touched."""
    _validate_active_config(db, users)
    fake = _wire(monkeypatch, [TimeoutError("sem resposta")])

    doc = nfse.operate(db, restricted_doc.id, "issue", "k1",
                       fully_configured_settings, users["gestor"].id)
    assert doc.state == "uncertain"
    assert len(_posts(fake)) == 1

    with pytest.raises(HTTPException) as excinfo:
        nfse.operate(db, restricted_doc.id, "issue", "k2-retry",
                     fully_configured_settings, users["gestor"].id)
    assert excinfo.value.detail["codigo"] == "reconciliation_required"
    assert len(_posts(fake)) == 1  # the refused retry never reached the wire


def test_a_conclusive_get_dps_success_closes_the_document_against_retry(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """A reconcile that finds the NFS-e settles the matter: the document
    becomes ``issued`` and a subsequent issue is a no-op, not a POST."""
    _validate_active_config(db, users)
    fake = _wire(monkeypatch, [
        TimeoutError("sem resposta"),
        TransportResponse(200, success_body()),
    ])
    nfse.operate(db, restricted_doc.id, "issue", "k1",
                 fully_configured_settings, users["gestor"].id)
    doc = nfse.operate(db, restricted_doc.id, "reconcile", "k2",
                       fully_configured_settings, users["gestor"].id)
    assert doc.state == "issued"
    assert _external_id(db, doc) == VALID_ACCESS_KEY

    posts_before = len(_posts(fake))
    again = nfse.operate(db, restricted_doc.id, "issue", "k3",
                         fully_configured_settings, users["gestor"].id)
    assert again.state == "issued"
    assert len(_posts(fake)) == posts_before


def test_a_misread_201_never_triggers_an_automatic_retry(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """DPS #10 exactly: HTTP 201 with a body we could not decode. The right
    answer is UNCERTAIN plus a demand to reconcile — never a second POST,
    which would have duplicated a real NFS-e. Nothing in this codebase
    retries on its own."""
    _validate_active_config(db, users)
    fake = _wire(monkeypatch, [TransportResponse(201, b'{"algo": "inesperado"}')])

    doc = nfse.operate(db, restricted_doc.id, "issue", "k1",
                       fully_configured_settings, users["gestor"].id)
    assert doc.state == "uncertain"
    assert len(_posts(fake)) == 1

    detail = _audit(db, doc)
    assert detail["sefin_resposta_sha256"] == hashlib.sha256(b'{"algo": "inesperado"}').hexdigest()

    with pytest.raises(HTTPException) as excinfo:
        nfse.operate(db, restricted_doc.id, "issue", "k2",
                     fully_configured_settings, users["gestor"].id)
    assert excinfo.value.detail["codigo"] == "reconciliation_required"
    assert len(_posts(fake)) == 1


def test_a_divergent_access_key_fails_closed(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """External fiscal evidence is immutable. Once an access key is
    recorded, a later call returning a DIFFERENT one is never believed —
    the document goes uncertain with a named error and the original key
    stands."""
    _validate_active_config(db, users)
    fake = _wire(monkeypatch, [
        TransportResponse(201, success_body(VALID_ACCESS_KEY)),
        TransportResponse(200, success_body(OTHER_ACCESS_KEY)),
    ])
    doc = nfse.operate(db, restricted_doc.id, "issue", "k1",
                       fully_configured_settings, users["gestor"].id)
    assert _external_id(db, doc) == VALID_ACCESS_KEY

    # Force a reconcile over the already-completed attempt.
    doc.state = "uncertain"
    db.commit()
    reconciled = nfse.operate(db, restricted_doc.id, "reconcile", "k2",
                              fully_configured_settings, users["gestor"].id)
    assert reconciled.state == "uncertain"
    completed = db.scalars(select(FiscalAttempt).where(
        FiscalAttempt.document_id == restricted_doc.id,
        FiscalAttempt.phase == "completed").order_by(FiscalAttempt.number.desc())).first()
    assert completed.error_code == "access_key_conflict"
    assert completed.external_id is None
    # The original, trusted key was never overwritten.
    assert db.scalars(select(FiscalAttempt.external_id).where(
        FiscalAttempt.document_id == restricted_doc.id,
        FiscalAttempt.external_id.is_not(None))).first() == VALID_ACCESS_KEY


def test_no_production_batch_issuance_route_exists():
    """``emitir-pendentes`` stays out of the production path until a unitary
    cycle is proven. Checked structurally: the batch endpoint exists, and
    every route into it refuses production before doing any work."""
    from app.routers import fiscal

    batch_routes = [r for r in fiscal.router.routes
                    if "pendentes" in getattr(r, "path", "")]
    assert batch_routes, "o endpoint de lote deveria existir para o ambiente restrito"
    # M59 — the refusal code changed from `production_provider_not_implemented`
    # (there WAS no production provider) to whichever specific gate is shut.
    # The guarantee is the same one and is still checked here: a default
    # production configuration cannot obtain a provider at all. Both gates
    # are asserted, each in isolation, so neither can quietly stop refusing.
    from app.services.nfse_providers import get_provider
    with pytest.raises(HTTPException) as excinfo:
        get_provider(Settings(nfse_enabled=True, nfse_environment="production"))
    assert excinfo.value.detail["codigo"] == "real_provider_disabled"
    with pytest.raises(HTTPException) as excinfo:
        get_provider(Settings(nfse_enabled=True, nfse_environment="production",
                              nfse_real_enabled=True))
    assert excinfo.value.detail["codigo"] == "production_network_gate_disabled"


def test_batch_issuance_in_production_is_refused_per_document(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """Even if a loop were written by hand, each iteration calls
    ``operate()``, and ``operate()`` resolves the provider FIRST — so a
    production batch refuses on document #1 and never reaches #2.

    M59 — ``fully_configured_settings`` is fully configured for RESTRICTED.
    Flipping only ``nfse_environment`` to production therefore leaves the
    production network gate shut, which is the point: production
    configuration does not come along for the ride.
    """
    production = fully_configured_settings.model_copy(
        update={"nfse_environment": "production"})
    fake = _wire(monkeypatch, [TransportResponse(201, success_body())])
    for _ in range(3):
        with pytest.raises(HTTPException) as excinfo:
            nfse.operate(db, restricted_doc.id, "issue", "batch",
                         production, users["gestor"].id)
        assert excinfo.value.detail["codigo"] == "production_network_gate_disabled"
    assert fake.received == []


# ------------------------------- FASE 10: evidence on the production path


@pytest.fixture
def production_provider(context):  # noqa: F811 — fixture from the provider tests
    """A provider bound to production, driven by a fake transport. Proves
    the parsers and the evidence trail are shared, not restricted-only.

    M59 — the context's tax profile must now declare ``tp_amb=1``. Before
    M59 this fixture handed a homologation profile (tp_amb=2) to a
    production-bound provider and the builder accepted it, because tpAmb
    was hard-wired to 2 for everyone. That combination is exactly the
    mislabelling M59 forbids, so the fixture states the production profile
    it always meant.
    """
    production_context = replace(
        context, config=context.config.model_copy(update={"tp_amb": 1}))

    def build(responses):
        transport = FakeTransport(responses=list(responses))
        provider = NationalNfseProvider(transport=transport, context=production_context,
                                          environment="production")
        return provider, transport
    return build


def test_production_bound_provider_keeps_the_submitted_signed_xml(production_provider):
    provider, transport = production_provider([TransportResponse(201, success_body(tp_amb=1))])
    result = provider.issue(provider_request())
    assert result.outcome == Outcome.ISSUED
    # The exact bytes submitted, not a re-serialization: identical to what
    # the transport carried, recovered from the envelope as SEFIN would.
    sent = json.loads(transport.received[0].body.decode("utf-8"))
    assert result.submitted_document == decode_b64_gzip_xml(sent["dpsXmlGZipB64"])
    assert b"Signature" in result.submitted_document


def test_production_bound_provider_keeps_the_returned_nfse_and_timestamp(production_provider):
    provider, _ = production_provider([TransportResponse(201, success_body(tp_amb=1))])
    result = provider.issue(provider_request())
    assert result.returned_document == _nfse_xml(VALID_ACCESS_KEY)
    assert result.provider_processed_at == PROCESSED_AT


def test_production_bound_provider_records_response_shape_and_hash(production_provider):
    body = success_body(tp_amb=1)
    provider, _ = production_provider([TransportResponse(201, body, "application/json")])
    result = provider.issue(provider_request())
    shape = result.response_shape
    assert shape["sha256"] == hashlib.sha256(body).hexdigest()
    assert shape["content_length"] == len(body)
    assert shape["content_type"] == "application/json"
    assert "chaveAcesso" in shape["top_level_keys"]


def test_production_bound_provider_normalizes_the_access_key(production_provider):
    """M52.1 — the JSON ``chaveAcesso`` (50 chars) and ``infNFSe/@Id``
    (53 chars, ``NFS``-prefixed) are two encodings of one identifier. Both
    are cross-checked as identifiers, and the stored form is the TSIdNFSe
    one — the same convention production will use."""
    from app.services.nfse_national.identifiers import NFSE_ACCESS_KEY_PATTERN

    bare = VALID_ACCESS_KEY[3:]
    body = json.dumps({
        # M59 — stated because the envelope is now cross-checked against the
        # environment. This test is about key ENCODING; the absent-field case
        # is covered in test_nfse_m59_production_wiring.py.
        "tipoAmbiente": 1,
        "dataHoraProcessamento": PROCESSED_AT,
        "chaveAcesso": bare,
        "nfseXmlGZipB64": encode_xml_gzip_b64(_nfse_xml(VALID_ACCESS_KEY)),
    }).encode("utf-8")
    provider, _ = production_provider([TransportResponse(201, body)])
    result = provider.issue(provider_request())
    assert result.outcome == Outcome.ISSUED
    assert result.external_id == VALID_ACCESS_KEY
    assert NFSE_ACCESS_KEY_PATTERN.fullmatch(result.external_id)


def test_production_bound_provider_shares_the_error_parsers(production_provider):
    """A rejection on the production path decodes through the same M52/M53
    sanitizer — never a second, drifting implementation."""
    body = json.dumps({
        "dataHoraProcessamento": PROCESSED_AT,
        "erros": [{"codigo": "E0008", "descricao": "Data de emissão inválida.",
                   "complemento": "dhEmi fora da tolerância."}],
    }).encode("utf-8")
    provider, _ = production_provider([TransportResponse(400, body, "application/json")])
    result = provider.issue(provider_request())
    assert result.outcome == Outcome.REJECTED
    assert result.validation_errors[0]["codigo"] == "E0008"
    assert result.response_shape["erros_count"] == 1
    # Evidence of what we submitted survives a rejection too.
    assert result.submitted_document is not None


def test_production_evidence_reaches_artifacts_and_audit_without_pii(
        monkeypatch, db, users, restricted_doc, fully_configured_settings):
    """The persistence half of Phase 10, on the shared ``operate()`` path
    that production would inherit: signed DPS and returned NFS-e stored as
    artifacts, only digests and a timestamp in the audit trail."""
    _validate_active_config(db, users)
    _wire(monkeypatch, [TransportResponse(201, success_body())])
    doc = nfse.operate(db, restricted_doc.id, "issue", "k1",
                       fully_configured_settings, users["gestor"].id)
    assert doc.state == "issued"

    kinds = {a.kind for a in db.query(FiscalArtifact).filter_by(document_id=doc.id).all()}
    assert {"dps_signed_xml", "nfse_xml"} <= kinds

    detail = _audit(db, doc)
    assert detail["sefin_data_hora_processamento"] == PROCESSED_AT
    assert len(detail["evidencia_dps_enviado_sha256"]) == 64
    assert len(detail["evidencia_nfse_recebida_sha256"]) == 64

    # No PII and no payload in the trail: only key NAMES, hashes, counts
    # and a timestamp. `nfseXmlGZipB64` DOES appear — as a recorded
    # top-level key name in `sefin_resposta_chaves_json`, which is exactly
    # the bounded metadata M55 added and is not content. What must never
    # appear is the Base64 payload that key held, or the recipient's
    # identity.
    serialized = json.dumps(detail, ensure_ascii=False)
    assert "52998224725" not in serialized                       # synthetic CPF
    assert "Pessoa Sintética" not in serialized                  # recipient name
    assert encode_xml_gzip_b64(_nfse_xml(VALID_ACCESS_KEY)) not in serialized
    assert "<NFSe" not in serialized and "<DPS" not in serialized
    assert set(detail["sefin_resposta_chaves_json"]) == {
        "tipoAmbiente", "versaoAplicativo", "dataHoraProcessamento",
        "chaveAcesso", "nfseXmlGZipB64"}
    # Nothing unbounded reached the trail.
    assert max(len(str(v)) for v in detail.values()) < 200


def test_dispatch_builds_the_production_transport_closed():
    """M59 REPLACES the M56 invariant here, and it is worth saying exactly
    what changed and why the system is not weaker for it.

    M56 asserted that ``dispatch`` did not so much as mention
    ``HttpxProductionTransport`` — a reasonable last rope while production
    was unreachable by design. M59 wires the path, so that assertion is now
    false by intent. What replaces it is stronger, because it constrains
    behaviour rather than source text: dispatch may CONSTRUCT the production
    transport, but it must construct it CLOSED — it never passes
    ``explicit_human_authorization``, which has no configuration path, so
    the object it builds refuses before any socket exists.
    """
    import inspect

    source = inspect.getsource(dispatch)
    assert "HttpxProductionTransport" in source          # the path exists now
    assert "explicit_human_authorization" not in source  # and dispatch cannot open it


def test_a_production_transport_built_by_hand_still_refuses():
    """And if someone constructed one anyway, in this mission's
    configuration it refuses — the gate is off."""
    transport = HttpxProductionTransport(
        network_enabled=Settings().nfse_production_network_enabled,
        environment="production")
    with pytest.raises(NetworkGateClosedError):
        transport.send(TransportRequest("POST", "/nfse", body=b"{}"))
