"""M36 — durable, globally-unique numero_dps allocation.

Root-cause regression: before this, numero_dps was derived from
FiscalAttempt.number (scoped PER document), so every document's first real
dispatch attempt minted numero_dps=1 — two different documents, same
issuer/série/município, could produce an identical TSIdDPS. These tests
exercise app.services.nfse_national.dps_numbering directly, against real
FiscalDocument rows (to satisfy the FK on DpsNumberAllocation) created via
the cheap mock-environment nfse.prepare() path — no certificate, no
transport, no restricted settings needed at this layer.

True concurrent-race safety (two allocations racing for the SAME scope at
the SAME instant) is only genuinely provable against real row locking —
see tests/test_nfse_postgres.py (same convention as every other durable
sequence in this codebase: SQLite is single-writer and used here only for
deterministic, sequential-call correctness, exactly like
ids.allocate_public_code/CodeSequence — see app/db.py and M23).
"""
from datetime import date
from decimal import Decimal

import pytest

from app.config import Settings
from app.models import DpsNumberSequence, FinancialEntry, Person, SpirometryExam
from app.services import nfse
from app.services.nfse_national.dps_numbering import allocate_dps_number, scope_key_for
from app.services.nfse_national.identifiers import InvalidIdentifierError

MOCK_SETTINGS = Settings(nfse_enabled=True, nfse_environment='mock')


def _new_document(db, users, suffix: str):
    """Cheapest possible real FiscalDocument — mock environment, no
    certificate/transport needed; only used here to satisfy
    DpsNumberAllocation's FK, never to exercise the restricted provider."""
    p = Person(public_code=f'PES-DPSNUM-{suffix}', nome_completo=f'Pessoa Sintética DPS {suffix}',
              nome_normalizado=f'pessoa sintetica dps {suffix}')
    db.add(p)
    db.flush()
    e = SpirometryExam(public_code=f'ESP-DPSNUM-{suffix}', person_id=p.id, status='Realizado',
                       data_exame=date(2026, 8, 10), data_exame_precisao='dia',
                       modalidade='residencial', broncodilatador=True)
    db.add(e)
    db.flush()
    f = FinancialEntry(public_code=f'LAN-DPSNUM-{suffix}', tipo='receita', categoria='Espirometria',
                       valor=Decimal('100.00'), status='Recebido', spirometry_exam_id=e.id,
                       data_competencia=date(2026, 9, 1))
    db.add(f)
    db.commit()
    from tests.test_nfse_foundation import policy_payload
    nfse.create_policy(db, policy_payload(version=f'SYNTH-DPSNUM-{suffix}'), users['admin'].id)
    return nfse.prepare(db, e.id, MOCK_SETTINGS, users['gestor'].id)


SCOPE_A = dict(codigo_municipio='3304557', tipo_inscricao_federal=2,
              inscricao_federal='63544026000110', serie_dps='00001')
SCOPE_B = dict(codigo_municipio='3550308', tipo_inscricao_federal=2,  # different município -> different scope
              inscricao_federal='63544026000110', serie_dps='00001')


# --------------------------------------------------------------- scope_key_for


def test_scope_key_deterministic_and_matches_tsiddps_components():
    key = scope_key_for(**SCOPE_A)
    assert key == '3304557:2:63544026000110:00001'


def test_scope_key_pads_cpf_issuer_to_fourteen_chars():
    key = scope_key_for(codigo_municipio='3304557', tipo_inscricao_federal=1,
                        inscricao_federal='52998224725', serie_dps='1')
    assert key == '3304557:1:00052998224725:00001'  # CPF padded, série padded too


def test_scope_key_rejects_invalid_municipio():
    with pytest.raises(InvalidIdentifierError):
        scope_key_for(codigo_municipio='330455', tipo_inscricao_federal=2,
                      inscricao_federal='63544026000110', serie_dps='1')


def test_scope_key_rejects_invalid_cnpj():
    with pytest.raises(InvalidIdentifierError):
        scope_key_for(codigo_municipio='3304557', tipo_inscricao_federal=2,
                      inscricao_federal='not-a-cnpj', serie_dps='1')


def test_scope_key_rejects_invalid_tipo_inscricao():
    with pytest.raises(InvalidIdentifierError):
        scope_key_for(codigo_municipio='3304557', tipo_inscricao_federal=3,
                      inscricao_federal='63544026000110', serie_dps='1')


# --------------------------------------------------------------- allocate_dps_number


def test_document_a_gets_dps_1_document_b_gets_dps_2(db, users):
    doc_a = _new_document(db, users, 'A')
    doc_b = _new_document(db, users, 'B')
    number_a = allocate_dps_number(db, document_id=doc_a.id, **SCOPE_A)
    number_b = allocate_dps_number(db, document_id=doc_b.id, **SCOPE_A)
    db.commit()
    assert number_a == 1
    assert number_b == 2
    assert number_a != number_b


def test_distinct_tsiddps_values_for_two_documents(db, users):
    from app.services.nfse_national.identifiers import DpsIdComponents, build_dps_id

    doc_a = _new_document(db, users, 'TSA')
    doc_b = _new_document(db, users, 'TSB')
    number_a = allocate_dps_number(db, document_id=doc_a.id, **SCOPE_A)
    number_b = allocate_dps_number(db, document_id=doc_b.id, **SCOPE_A)
    db.commit()
    id_a = build_dps_id(DpsIdComponents(codigo_municipio=SCOPE_A['codigo_municipio'],
                                        tipo_inscricao_federal=SCOPE_A['tipo_inscricao_federal'],
                                        inscricao_federal=SCOPE_A['inscricao_federal'],
                                        serie_dps=SCOPE_A['serie_dps'],
                                        numero_dps=str(number_a).rjust(15, '0')))
    id_b = build_dps_id(DpsIdComponents(codigo_municipio=SCOPE_A['codigo_municipio'],
                                        tipo_inscricao_federal=SCOPE_A['tipo_inscricao_federal'],
                                        inscricao_federal=SCOPE_A['inscricao_federal'],
                                        serie_dps=SCOPE_A['serie_dps'],
                                        numero_dps=str(number_b).rjust(15, '0')))
    assert id_a != id_b


def test_different_scope_starts_its_own_sequence(db, users):
    """A different issuer municipality is a DIFFERENT scope — its own
    sequence starting at 1, never continuing SCOPE_A's counter."""
    doc_a = _new_document(db, users, 'SCOPEA')
    doc_c = _new_document(db, users, 'SCOPEC')
    allocate_dps_number(db, document_id=doc_a.id, **SCOPE_A)
    number_c = allocate_dps_number(db, document_id=doc_c.id, **SCOPE_B)
    db.commit()
    assert number_c == 1  # SCOPE_B's own first number, not SCOPE_A's second


def test_idempotent_retry_same_document_keeps_original_number(db, users):
    doc = _new_document(db, users, 'RETRY')
    first = allocate_dps_number(db, document_id=doc.id, **SCOPE_A)
    second = allocate_dps_number(db, document_id=doc.id, **SCOPE_A)
    third = allocate_dps_number(db, document_id=doc.id, **SCOPE_A)
    db.commit()
    assert first == second == third
    # And the sequence was consumed exactly once for this document, not three times.
    seq = db.get(DpsNumberSequence, scope_key_for(**SCOPE_A))
    assert seq.next_value == first + 1


def test_rejected_or_uncertain_document_number_is_never_reused(db, users):
    """allocate_dps_number has no notion of fiscal outcome — proving the
    invariant at this layer: whatever happened to document A afterwards
    (rejected, uncertain, anything), calling allocate for a DIFFERENT
    document B must never receive A's already-allocated number."""
    doc_a = _new_document(db, users, 'REJ')
    doc_b = _new_document(db, users, 'UNC')
    number_a = allocate_dps_number(db, document_id=doc_a.id, **SCOPE_A)
    db.commit()
    # No code path exists to free/roll back number_a — simulate the passage
    # of time/outcome by simply doing nothing to it, then allocate for B.
    number_b = allocate_dps_number(db, document_id=doc_b.id, **SCOPE_A)
    db.commit()
    assert number_b != number_a
    assert number_b == number_a + 1


def test_restart_semantics_new_session_same_values(db, users, engine):
    """Durability: a fresh Session against the SAME database sees exactly
    the same allocation — the guarantee does not depend on any in-memory
    state, only on what is committed. This is the process-restart contract:
    a real restart only ever loses in-memory state, never committed rows."""
    from sqlalchemy.orm import Session

    doc = _new_document(db, users, 'RESTART')
    number = allocate_dps_number(db, document_id=doc.id, **SCOPE_A)
    db.commit()

    with Session(engine, expire_on_commit=False) as fresh_session:
        again = allocate_dps_number(fresh_session, document_id=doc.id, **SCOPE_A)
        fresh_session.commit()
    assert again == number


# dps_number_allocations' database-level immutability (UPDATE/DELETE both
# refused by a trigger) is proven in tests/test_nfse_m36_migration.py — that
# trigger is installed only by the real alembic migration, never by
# Base.metadata.create_all() (what the generic `db`/`engine` fixtures use
# here), so it cannot be exercised through this file's fixtures.


def test_municipality_series_issuer_scoping_matches_official_pattern(db, users):
    """The scope key composition matches exactly the components the
    official TSIdDPS pattern (identifiers.DPS_ID_PATTERN) defines besides
    the number itself — never invented, never an arbitrary hash."""
    from app.services.nfse_national.identifiers import DPS_ID_PATTERN, DpsIdComponents, build_dps_id

    doc = _new_document(db, users, 'SCOPEMATCH')
    number = allocate_dps_number(db, document_id=doc.id, **SCOPE_A)
    db.commit()
    dps_id = build_dps_id(DpsIdComponents(
        codigo_municipio=SCOPE_A['codigo_municipio'], tipo_inscricao_federal=SCOPE_A['tipo_inscricao_federal'],
        inscricao_federal=SCOPE_A['inscricao_federal'], serie_dps=SCOPE_A['serie_dps'],
        numero_dps=str(number).rjust(15, '0'),
    ))
    assert DPS_ID_PATTERN.fullmatch(dps_id)
