"""M49 — dhEmi must be written in the issuer's civil time, not in UTC.

The real DPS #9 was rejected by Sefin Produção Restrita with:

    E0008 — "A data de emissão da DPS não pode ser posterior à data do seu
             processamento."

even though, as an INSTANT, its dhEmi was 1.4 seconds BEFORE Sefin's own
processing. That is only possible if the rule compares civil wall-clock
readings rather than instants. Confirmed against the two real, successfully
issued production NFS-e, where Sefin's OWN `dhProc` is written with a
`-03:00` offset and matches the accepted `dhEmi` to the second.

We were emitting the UTC wall clock with a `+00:00` offset — schema-valid
(`TSDateTimeUTC`'s pattern accepts every offset from -11:00 to +12:00), but
between 21:00 and 23:59 in Brasília the UTC wall clock is already on the NEXT
DAY and reads ~3h higher. DPS #9 was emitted at 23:34:37-03:00 and serialized
as `2026-09-17T02:34:37+00:00`.

The fix converts the instant to `America/Sao_Paulo` at serialization time.
It never subtracts hours by hand, never fabricates a date, and never uses the
competence/service date as dhEmi — `astimezone` preserves the exact moment and
changes only how it is written.

Every test here is offline: no network, no Sefin, no certificate, no real
patient data.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from lxml import etree

from app.services.nfse_national.dps_builder import (
    EMISSION_TIMEZONE,
    NFSE_NS,
    DpsBuildError,
    build_dps_element,
    serialize_dps,
)
from app.services.nfse_national.xsd_validation import validate_dps_xml
from tests.test_nfse_national_dps_builder import synthetic_dps_input

SP = ZoneInfo("America/Sao_Paulo")

# The exact instant DPS #9 was emitted at, from the real attempt's
# started_at/completed_at window (2026-09-17T02:34:37Z == 2026-09-16
# 23:34:37 in Brasília). This is the dangerous boundary: the Brasília civil
# date and the UTC date differ.
DPS9_INSTANT_UTC = datetime(2026, 9, 17, 2, 34, 37, tzinfo=timezone.utc)


def _dh_emi_text(dh_emi) -> str:
    root = build_dps_element(synthetic_dps_input(dh_emi=dh_emi))
    node = root.find(f".//{{{NFSE_NS}}}dhEmi")
    assert node is not None, "dhEmi element missing from the built DPS"
    return node.text


# ------------------------------------------------ the exact DPS #9 boundary


def test_dps9_boundary_instant_is_written_in_brasilia_civil_time():
    """The precise instant that produced E0008, written correctly."""
    assert _dh_emi_text(DPS9_INSTANT_UTC) == "2026-09-16T23:34:37-03:00"


def test_dps9_boundary_never_reproduces_the_rejected_utc_form():
    text = _dh_emi_text(DPS9_INSTANT_UTC)
    assert text != "2026-09-17T02:34:37+00:00", "the exact form Sefin rejected with E0008"
    assert not text.endswith("+00:00")
    assert "2026-09-17" not in text, "Brasília civil date was still 2026-09-16"


def test_dps9_boundary_is_never_future_shifted():
    """The classic corruption: keeping the UTC wall clock but stamping it
    -03:00, which moves the instant 3h into the future."""
    text = _dh_emi_text(DPS9_INSTANT_UTC)
    assert text != "2026-09-17T02:34:37-03:00"
    assert datetime.fromisoformat(text) == DPS9_INSTANT_UTC, "instant must be preserved exactly"


def test_dps9_boundary_would_not_be_after_sefin_processing():
    """Sefin compares civil wall-clock readings. With the fix, our dhEmi's
    wall clock is no longer ahead of Sefin's own (-03:00) processing clock.

    Sefin's processing time for DPS #9 is bounded above by the attempt's
    recorded completed_at (the moment we already held the response).
    """
    dh_proc = datetime(2026, 9, 17, 2, 34, 38, 438006, tzinfo=timezone.utc)
    dh_emi = datetime.fromisoformat(_dh_emi_text(DPS9_INSTANT_UTC))

    # As instants: emission precedes processing.
    assert dh_emi <= dh_proc

    # As the civil wall-clock readings Sefin actually compares.
    emi_wall = dh_emi.replace(tzinfo=None)
    proc_wall = dh_proc.astimezone(SP).replace(tzinfo=None, microsecond=0)
    assert emi_wall <= proc_wall, (
        f"dhEmi wall clock {emi_wall} must not read later than Sefin's {proc_wall}")

    # And prove the OLD behaviour would have failed this very assertion, by
    # ~3h — i.e. this test would not have passed before the fix.
    old_wall = DPS9_INSTANT_UTC.replace(tzinfo=None)
    assert old_wall - proc_wall > timedelta(hours=2, minutes=59)


# ------------------------------------------------ boundary sweep


@pytest.mark.parametrize("local_text, expected", [
    # Every hour of the window where UTC has already rolled to the next day
    # while Brasília has not — the whole class of dates DPS #9 fell into.
    ("2026-09-16T21:00:00-03:00", "2026-09-16T21:00:00-03:00"),
    ("2026-09-16T22:30:00-03:00", "2026-09-16T22:30:00-03:00"),
    ("2026-09-16T23:34:00-03:00", "2026-09-16T23:34:00-03:00"),
    ("2026-09-16T23:59:59-03:00", "2026-09-16T23:59:59-03:00"),
    # Local midnight — the Brasília civil date rolls over.
    ("2026-09-17T00:00:00-03:00", "2026-09-17T00:00:00-03:00"),
    ("2026-09-17T00:00:01-03:00", "2026-09-17T00:00:01-03:00"),
    # UTC midnight, which is 21:00 the previous day in Brasília.
    ("2026-09-16T21:00:00-03:00", "2026-09-16T21:00:00-03:00"),
    # Ordinary daytime emission — must be untouched in substance.
    ("2026-09-16T10:15:30-03:00", "2026-09-16T10:15:30-03:00"),
    ("2026-09-16T14:00:00-03:00", "2026-09-16T14:00:00-03:00"),
])
def test_instant_round_trips_through_any_input_zone(local_text, expected):
    """Same instant, supplied as UTC, as Brasília, and as a third unrelated
    zone, must always serialize to the same Brasília civil reading."""
    instant = datetime.fromisoformat(local_text)
    for tz in (timezone.utc, SP, ZoneInfo("Asia/Tokyo"), timezone(timedelta(hours=5, minutes=30))):
        text = _dh_emi_text(instant.astimezone(tz))
        assert text == expected, f"input zone {tz} changed the written dhEmi"
        assert datetime.fromisoformat(text) == instant


def test_utc_midnight_maps_to_previous_brasilia_day():
    """00:00Z is 21:00 the day before in Brasília — the date must roll back,
    not forward."""
    text = _dh_emi_text(datetime(2026, 9, 17, 0, 0, 0, tzinfo=timezone.utc))
    assert text == "2026-09-16T21:00:00-03:00"


def test_local_midnight_keeps_its_own_civil_date():
    text = _dh_emi_text(datetime(2026, 9, 17, 0, 0, 0, tzinfo=SP))
    assert text == "2026-09-17T00:00:00-03:00"


# ------------------------------------------------ format and precision


def test_offset_is_explicit_and_second_precision():
    text = _dh_emi_text(DPS9_INSTANT_UTC)
    assert len(text) == len("2026-09-16T23:34:37-03:00")
    assert text[-6] in "+-", "an explicit offset designator is mandatory"
    assert "." not in text, "no fractional seconds — TSDateTimeUTC has second precision"


def test_sub_second_is_truncated_not_rounded_up():
    """Rounding up could push dhEmi past the processing instant; truncation
    can only ever move it earlier, which the rule always accepts."""
    instant = DPS9_INSTANT_UTC.replace(microsecond=999999)
    assert _dh_emi_text(instant) == "2026-09-16T23:34:37-03:00"


def test_matches_the_format_of_the_real_production_golden_nfse():
    """The two real successfully issued production NFS-e carry
    `dhEmi`/`dhProc` as `YYYY-MM-DDThh:mm:ss-03:00` (no PII involved — this
    asserts the shape only)."""
    import re
    text = _dh_emi_text(DPS9_INSTANT_UTC)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}-03:00", text)


def test_naive_datetime_is_still_refused():
    with pytest.raises(DpsBuildError, match="timezone-aware"):
        synthetic_dps_input(dh_emi=datetime(2026, 9, 16, 23, 34, 37))


def test_emission_timezone_is_the_issuer_civil_zone():
    assert EMISSION_TIMEZONE == ZoneInfo("America/Sao_Paulo")


# ------------------------------------------------ schema / signature / scope


def test_boundary_dps_is_still_xsd_valid():
    root = build_dps_element(synthetic_dps_input(dh_emi=DPS9_INSTANT_UTC))
    validate_dps_xml(serialize_dps(root))  # raises on any violation


def test_boundary_dps_signs_and_verifies():
    """A -03:00 dhEmi must not disturb canonicalization or the signature."""
    from app.services.nfse_national.signer import (  # noqa: PLC0415
        generate_synthetic_test_certificate,
        load_pkcs12_certificate,
        sign_dps,
        verify_dps_signature,
    )

    p12_bytes, password = generate_synthetic_test_certificate()
    cert = load_pkcs12_certificate(p12_bytes, password)
    root = build_dps_element(synthetic_dps_input(dh_emi=DPS9_INSTANT_UTC))
    signed = sign_dps(root, cert)
    verify_dps_signature(signed, cert.certificate_pem)
    assert b"-03:00" in serialize_dps(signed)


def test_builder_stays_deterministic_for_a_fixed_instant():
    a = serialize_dps(build_dps_element(synthetic_dps_input(dh_emi=DPS9_INSTANT_UTC)))
    b = serialize_dps(build_dps_element(synthetic_dps_input(dh_emi=DPS9_INSTANT_UTC)))
    assert a == b


def test_competence_is_independent_of_dhemi():
    """dCompet is the fiscal/service competence and must never follow dhEmi's
    zone conversion — DPS #9's competence was 2026-09-15 while it was emitted
    on 2026-09-16."""
    root = build_dps_element(synthetic_dps_input(
        dh_emi=DPS9_INSTANT_UTC, competencia=date(2026, 9, 15)))
    assert root.find(f".//{{{NFSE_NS}}}dCompet").text == "2026-09-15"
    assert root.find(f".//{{{NFSE_NS}}}dhEmi").text.startswith("2026-09-16")
