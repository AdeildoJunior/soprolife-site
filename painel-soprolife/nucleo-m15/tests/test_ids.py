"""IDs: UUID interno + códigos públicos sequenciais."""

import uuid

from app.ids import PREFIXES, allocate_public_code, format_public_code, new_uuid


def test_new_uuid_valido():
    value = new_uuid()
    assert str(uuid.UUID(value)) == value


def test_format_public_code():
    assert format_public_code("PES", 1) == "PES-000001"
    assert format_public_code("ESP", 123456) == "ESP-123456"


def test_prefixos_canonicos():
    assert PREFIXES["people"] == "PES"
    assert PREFIXES["spirometry_exams"] == "ESP"
    assert PREFIXES["consultations"] == "CON"
    assert PREFIXES["partners"] == "CLI"
    assert PREFIXES["partner_units"] == "UNI"
    assert PREFIXES["partner_contacts"] == "CTT"
    assert PREFIXES["partnerships"] == "PAR"
    assert PREFIXES["partner_referrals"] == "ENC"
    assert PREFIXES["interactions"] == "INT"
    assert PREFIXES["followups"] == "FUP"
    assert PREFIXES["financial_entries"] == "LAN"


def test_alocacao_sequencial(db):
    first = allocate_public_code(db, "people")
    second = allocate_public_code(db, "people")
    other = allocate_public_code(db, "spirometry_exams")
    db.commit()
    assert first == "PES-000001"
    assert second == "PES-000002"
    assert other == "ESP-000001"
