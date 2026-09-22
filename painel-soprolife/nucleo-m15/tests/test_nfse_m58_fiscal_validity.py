"""M58 — the ``fiscal_validity`` contract.

The field answers a third question, separate from the two the queue already
answers ("was it really emitted?" and "in which environment?"):

    may the company treat THIS document as a fiscally valid NFS-e for its
    real operation and accounting?

The whole point of these tests is that the answer is NOT ``state == 'issued'``.
DPS #10 is a genuine NFS-e, really issued by SEFIN, and it must remain
fiscally invalid, because it was issued under ``tpAmb=2`` in Produção
Restrita — homologation. An homologation document is a real document in a
practice environment: it proves the pipeline works and nothing about the books.

Entirely offline and entirely synthetic. No real patient, no real issuance, no
network. Production documents cannot be created by the application at all
(``get_provider()`` refuses production; the DPS builder refuses tpAmb=1), so
the production fixtures here are built by inserting rows directly — which is
the only way to test a contract for a path that does not exist yet, and the
reason it is worth testing now rather than on the day it does.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models import (FinancialEntry, FiscalArtifact, FiscalAttempt, FiscalDocument,
                        FiscalPreparation, Person, SpirometryExam, utcnow)
from app.services import nfse
from app.services.nfse_validity import fiscal_validity

from tests.test_nfse_foundation import (events, issue, policy_payload,  # noqa: F401
                                        ready, settings, source)

# A real government NFS-e access key shape (TSIdNFSe).
VALID_KEY = 'NFS33045572263544026000110000000000000126094282476576'
OTHER_KEY = 'NFS33045572263544026000110000000000000126094282476500'
assert len(VALID_KEY) == len(OTHER_KEY) == 53 and VALID_KEY != OTHER_KEY


# --------------------------------------------------------- synthetic fixtures


def _document(db, users, *, environment, state, slug):
    """A fiscal document in any environment, including ones the application
    itself cannot produce."""
    person = Person(public_code=f'PES-M58{slug}'[:20],
                    nome_completo='Pessoa Sintética M58',
                    nome_normalizado='pessoa sintetica m58')
    db.add(person)
    db.flush()
    exam = SpirometryExam(public_code=f'ESP-M58{slug}'[:20], person_id=person.id,
                          status='Realizado', data_exame=date(2026, 8, 10),
                          data_exame_precisao='dia', modalidade='residencial',
                          broncodilatador=True)
    db.add(exam)
    db.flush()
    db.add(FinancialEntry(public_code=f'LAN-M58{slug}'[:20], tipo='receita',
                          categoria='Espirometria', valor=Decimal('123.45'),
                          status='Recebido', spirometry_exam_id=exam.id,
                          data_competencia=date(2026, 9, 1)))
    document = FiscalDocument(spirometry_exam_id=exam.id, environment=environment,
                              state=state, eligibility='eligible', blocking_reasons=[],
                              created_by=users['gestor'].id,
                              idempotency_key=f'm58-key-{slug}',
                              idempotency_fingerprint=f'm58-fp-{slug}')
    db.add(document)
    db.flush()
    db.add(FiscalPreparation(document_id=document.id, flow='HOME', blocking_reasons=[],
                             fingerprint=f'm58-prep-{slug}', created_by=users['gestor'].id))
    db.flush()
    return document


def _attempt(db, users, document, *, number=1, operation='issue', phase='completed',
             provider='production', environment='production', outcome='issued',
             external_id=VALID_KEY, reconciliation_required=False):
    preparation = nfse.latest_preparation(db, document.id)
    attempt = FiscalAttempt(
        document_id=document.id, preparation_id=preparation.id,
        operation_id=f'm58-op-{document.id[:8]}-{number}', operation=operation,
        phase=phase, number=number, provider=provider, environment=environment,
        outcome=outcome, external_id=external_id,
        reconciliation_required=reconciliation_required, actor_id=users['gestor'].id,
        started_at=utcnow(), completed_at=utcnow() if phase == 'completed' else None,
        idempotency_key=f'm58-att-{document.id[:8]}-{number}-{phase}',
        idempotency_fingerprint=f'm58-attfp-{document.id[:8]}-{number}-{phase}')
    db.add(attempt)
    db.flush()
    return attempt


def _nfse_xml_artifact(db, users, document):
    db.add(FiscalArtifact(document_id=document.id, attempt_id=None, kind='nfse_xml',
                          storage_relative_path=f'fiscal/{document.id}/nfse.xml',
                          sha256='a' * 64, size_bytes=9756,
                          created_by=users['gestor'].id))
    db.flush()


def _fully_proven_production(db, users, slug='ok', **attempt_overrides):
    """The control fixture: production, issued, complete and coherent evidence.

    Every negative test below is this same fixture with exactly ONE thing
    taken away.
    """
    document = _document(db, users, environment='production', state='issued', slug=slug)
    _attempt(db, users, document, **attempt_overrides)
    _nfse_xml_artifact(db, users, document)
    db.commit()
    return document


# --------------------------------------------------------- Phase 4 — mock


def test_mock_simulated_is_never_fiscally_valid(db, users, settings, ready):
    """A mock run invents its identifier locally. Nothing exists anywhere."""
    document = issue(db, users, settings, ready)
    assert document.state == 'simulated'
    assert fiscal_validity(db, document) is False
    assert nfse.serialize_document(db, document)['fiscal_validity'] is False


def test_no_mock_fixture_can_satisfy_the_contract_by_accident(db, users, settings, ready):
    """Even handed the full shape of a real proof — a genuine-looking access
    key and an nfse_xml artifact — a mock document stays invalid, because the
    environment gate is checked first and cannot be talked around."""
    document = issue(db, users, settings, ready)
    _attempt(db, users, document, number=99, provider='production',
             environment='production', outcome='issued', external_id=VALID_KEY)
    _nfse_xml_artifact(db, users, document)
    db.commit()
    assert document.environment == 'mock'
    assert fiscal_validity(db, document) is False


# --------------------------------------------------------- Phase 3 — restricted


def test_restricted_issued_is_not_fiscally_valid(db, users):
    """THE regression this mission exists to prevent.

    A real NFS-e, really issued by SEFIN, with a real access key and the
    returned document stored — and still fiscally invalid, because tpAmb=2 is
    homologation. This is exactly DPS #10's situation.
    """
    document = _document(db, users, environment='restricted', state='issued', slug='restr')
    _attempt(db, users, document, provider='restricted', environment='restricted')
    _nfse_xml_artifact(db, users, document)
    db.commit()
    assert document.state == 'issued'          # M57's verdict stands
    assert fiscal_validity(db, document) is False   # and is not the same question
    assert nfse.serialize_document(db, document)['fiscal_validity'] is False


def test_restricted_issued_stays_invalid_even_with_production_shaped_evidence(db, users):
    """Belt and braces: an attempt row mislabelled 'production' on a
    restricted document is incoherent evidence, not strong evidence."""
    document = _document(db, users, environment='restricted', state='issued', slug='restr2')
    _attempt(db, users, document, provider='production', environment='production')
    _nfse_xml_artifact(db, users, document)
    db.commit()
    assert fiscal_validity(db, document) is False


# --------------------------------------------------------- Phase 5 — production


def test_fully_proven_production_issuance_is_fiscally_valid(db, users):
    """The control. Everything the contract asks for is present and coherent."""
    document = _fully_proven_production(db, users)
    assert fiscal_validity(db, document) is True
    assert nfse.serialize_document(db, document)['fiscal_validity'] is True


@pytest.mark.parametrize('state', ['pending', 'blocked', 'issuing', 'failed',
                                   'uncertain', 'reconciling', 'cancelled', 'simulated'])
def test_production_in_any_non_issued_state_is_invalid(db, users, state):
    """Only the real terminal success state qualifies. 'cancelled' is in this
    list on purpose: a cancelled NFS-e is not a valid one."""
    document = _document(db, users, environment='production', state=state, slug=state[:5])
    _attempt(db, users, document)
    _nfse_xml_artifact(db, users, document)
    db.commit()
    assert fiscal_validity(db, document) is False


def test_production_issued_without_external_id_is_invalid(db, users):
    document = _fully_proven_production(db, users, slug='noext', external_id=None)
    assert fiscal_validity(db, document) is False


@pytest.mark.parametrize('bad_key', [
    'MOCK-11111111-1111-1111-1111-111111111111',  # a mock identifier
    'NFS-NOT-A-REAL-KEY',                         # not the shape at all
    'XYZ' + '0' * 50,                             # right length, wrong shape
    VALID_KEY[:-1],                               # one character short
    VALID_KEY + '0',                              # one character long
])
def test_production_issued_with_a_malformed_access_key_is_invalid(db, users, bad_key):
    document = _fully_proven_production(db, users, slug='badk', external_id=bad_key)
    assert fiscal_validity(db, document) is False


def test_production_issued_without_the_nfse_xml_artifact_is_invalid(db, users):
    """Without the document the authority returned we hold a claim about an
    NFS-e, not the NFS-e."""
    document = _document(db, users, environment='production', state='issued', slug='noart')
    _attempt(db, users, document)
    db.commit()
    assert fiscal_validity(db, document) is False


def test_the_dps_we_sent_is_not_a_substitute_for_the_nfse_returned(db, users):
    """Our own signed DPS proves what we submitted, never what came back."""
    document = _document(db, users, environment='production', state='issued', slug='dpsart')
    _attempt(db, users, document)
    for kind in ('dps_unsigned_xml', 'dps_signed_xml'):
        db.add(FiscalArtifact(document_id=document.id, attempt_id=None, kind=kind,
                              storage_relative_path=f'fiscal/{document.id}/{kind}.xml',
                              sha256='b' * 64, size_bytes=4984,
                              created_by=users['gestor'].id))
    db.commit()
    assert fiscal_validity(db, document) is False


def test_production_issued_still_requiring_reconciliation_is_invalid(db, users):
    document = _fully_proven_production(db, users, slug='recon',
                                        reconciliation_required=True)
    assert fiscal_validity(db, document) is False


def test_production_issued_with_no_completed_attempt_is_invalid(db, users):
    document = _document(db, users, environment='production', state='issued', slug='noatt')
    _nfse_xml_artifact(db, users, document)
    db.commit()
    assert fiscal_validity(db, document) is False


def test_production_issued_with_only_a_started_attempt_is_invalid(db, users):
    document = _fully_proven_production(db, users, slug='start', phase='started')
    assert fiscal_validity(db, document) is False


@pytest.mark.parametrize('provider,environment', [
    ('mock', 'production'),        # the mock cannot speak for production
    ('restricted', 'production'),  # nor can the homologation provider
    ('production', 'restricted'),  # nor a production provider in homologation
    ('production', 'mock'),
    ('restricted', 'restricted'),
])
def test_production_issued_with_an_incoherent_provider_is_invalid(db, users, provider,
                                                                  environment):
    """Provider and environment must both be production and must agree with
    the document. Disagreement is incoherent evidence, not weak evidence."""
    document = _fully_proven_production(db, users, slug='incoh', provider=provider,
                                        environment=environment)
    assert fiscal_validity(db, document) is False


def test_production_issued_with_the_pre_m57_success_word_is_invalid(db, users):
    """A production attempt can only ever have been written after M57, so
    'simulated' there is not legacy vocabulary — it is a contradiction."""
    document = _fully_proven_production(db, users, slug='oldwd', outcome='simulated')
    assert fiscal_validity(db, document) is False


def test_production_issued_with_a_cancellation_as_its_only_evidence_is_invalid(db, users):
    document = _fully_proven_production(db, users, slug='cancl', operation='cancel')
    assert fiscal_validity(db, document) is False


def test_production_issued_with_divergent_access_keys_is_invalid(db, users):
    """A document whose recorded key is disputed has no settled fiscal
    identity, so nothing may be asserted about it — even though the newest
    attempt on its own looks perfect."""
    document = _document(db, users, environment='production', state='issued', slug='confl')
    _attempt(db, users, document, number=1, external_id=OTHER_KEY)
    _attempt(db, users, document, number=2, external_id=VALID_KEY)
    _nfse_xml_artifact(db, users, document)
    db.commit()
    assert fiscal_validity(db, document) is False


def test_reconcile_may_establish_the_issuance(db, users):
    """A conclusive reconcile is as good as a conclusive issue — that is how
    a timed-out POST is legitimately settled."""
    document = _fully_proven_production(db, users, slug='recop', operation='reconcile')
    assert fiscal_validity(db, document) is True


# --------------------------------------------------------- single source of truth


def test_serialize_document_is_the_only_producer_of_the_field(db, users):
    """Nothing may compute its own answer: two answers to this question is
    worse than either of them. This pins that the serializer delegates rather
    than deciding, by checking the two agree on a case where the answer is
    not the old constant."""
    document = _fully_proven_production(db, users, slug='src')
    assert nfse.serialize_document(db, document)['fiscal_validity'] is fiscal_validity(
        db, document) is True


def test_the_field_is_derived_never_persisted(db, users):
    """``fiscal_validity`` is not a column. It is recomputed from evidence on
    every serialization, so it can never drift out of date with the evidence
    — and needs no migration."""
    assert not hasattr(FiscalDocument, 'fiscal_validity')
    assert 'fiscal_validity' not in {c.name for c in FiscalDocument.__table__.columns}


def test_validity_does_not_depend_on_runtime_settings(db, users, settings, monkeypatch):
    """A document legitimately issued in production cannot stop being valid
    because somebody toggled a gate today. The contract reads evidence, never
    configuration — this is what keeps it separate from production_gates.py."""
    document = _fully_proven_production(db, users, slug='setng')
    assert fiscal_validity(db, document) is True
    for flag in ('nfse_enabled', 'nfse_real_enabled', 'nfse_production_network_enabled'):
        if hasattr(settings, flag):
            monkeypatch.setattr(settings, flag, False, raising=False)
    assert fiscal_validity(db, document) is True


# --------------------------------------------------------- Phase 9 — production stays off


def test_the_contract_does_not_open_any_production_path(settings, monkeypatch):
    """Defining when a production document WOULD be valid must not make one
    reachable. get_provider() still refuses production outright."""
    from fastapi import HTTPException
    from app.services.nfse_providers import get_provider
    monkeypatch.setattr(settings, 'nfse_environment', 'production', raising=False)
    monkeypatch.setattr(settings, 'nfse_enabled', True, raising=False)
    with pytest.raises(HTTPException) as error:
        get_provider(settings)
    assert error.value.detail['codigo'] == 'production_provider_not_implemented'
