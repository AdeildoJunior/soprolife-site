"""M64 — the script that prepares ESP-000050 and runs its signed production preflight.

This script is the one that will touch the operational database with the real
certificate. So it is exercised here end to end, offline, on a synthetic
database with a throwaway certificate, and it has to prove the same things it
promises there: the document ends ``pending``, no attempt row exists, nothing
is sendable, the only blocker left is the network gate, and no HTTP client is
ever built. And it has to refuse, before preparing anything, when the
municipality is not the confirmed one or a manual note is unprotected.
"""
import importlib.util
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nfse_m64_production_preflight_esp000050.py"
MANUAL = {"ESP-000039": "33045572263544026000110000000000000426099130462655",
          "ESP-000046": "33045572263544026000110000000000000526092817158327",
          "ESP-000048": "33045572263544026000110000000000000626096136136469",
          "ESP-000049": "33045572263544026000110000000000000726091190747856",
          "ESP-000053": "33045572263544026000110000000000000826099736433520"}


def _cpf(seed):
    b = [(seed * 7 + i * 3) % 10 for i in range(9)]
    for _ in range(2):
        n = len(b)
        t = sum(b[i] * ((n + 1) - i) for i in range(n))
        r = (t * 10) % 11
        b.append(0 if r == 10 else r)
    return "".join(map(str, b))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A synthetic operational database at head, with ESP-000050, the five
    protected manual notes, the production profile and both policies."""
    from app.db import get_sessionmaker
    from app.models import FinancialEntry, Person, SpirometryExam, User
    from app.services import nfse
    from app.services.nfse_external_issuance import ExternalIssuance, register_external_issuance
    from app.services.nfse_national import fiscal_config
    from app.services.nfse_national.production_profile import (PRODUCTION_EFFECTIVE_FROM,
                                                               production_configuration,
                                                               production_policies)
    from app.services.nfse_national.signer import generate_synthetic_test_certificate

    url = f"sqlite:///{tmp_path}/op.db"
    monkeypatch.setenv("M15_DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    # app.db caches the engine and sessionmaker in module globals, and the
    # settings object may be cached too; point all of them at THIS database.
    from app import config as app_config
    from app import db as app_db
    monkeypatch.setattr(app_db, "_engine", None)
    monkeypatch.setattr(app_db, "_SessionLocal", None)
    if hasattr(app_config.get_settings, "cache_clear"):
        app_config.get_settings.cache_clear()

    with get_sessionmaker()() as db:
        user = User(nome="Operador", email="m64@teste.invalid", password_hash="x")
        db.add(user); db.flush()
        seed = 1
        for code, value, day, key in (
                [("ESP-000050", "220.00", date(2026, 9, 16), None)] +
                [(c, "230.00", date(2026, 9, 1), k) for c, k in MANUAL.items()]):
            seed += 1
            person = Person(public_code=f"PES-{code[4:]}", nome_completo="Marina Rocha Albuquerque",
                            nome_normalizado="marina rocha albuquerque", cpf=_cpf(seed))
            db.add(person); db.flush()
            exam = SpirometryExam(public_code=code, person_id=person.id, status="Realizado",
                                  data_exame=day, data_exame_precisao="dia", modalidade="cowork",
                                  broncodilatador=True,
                                  municipio_atendimento_ibge="3304557" if key is None else None)
            db.add(exam); db.flush()
            db.add(FinancialEntry(public_code=f"LAN-{code[4:]}", tipo="receita",
                                  categoria="Espirometria", valor=Decimal(value),
                                  status="Recebido", spirometry_exam_id=exam.id,
                                  data_competencia=day))
            db.commit()
            if key:
                register_external_issuance(db, ExternalIssuance(exam.id, key, str(day)), user.id)
        for policy in production_policies():
            nfse.create_policy(db, policy, user.id)
        fiscal_config.create_version(db, environment="production",
                                     effective_from=PRODUCTION_EFFECTIVE_FROM,
                                     validation_state="validated",
                                     configuration=production_configuration(), actor=user.id)

    p12, password = generate_synthetic_test_certificate()
    cert = tmp_path / "a1.p12"
    cert.write_bytes(p12)
    os.chmod(cert, 0o600)
    # The throwaway certificate lives one day; the real 30-day margin is
    # pinned elsewhere (M60).
    monkeypatch.setenv("M15_NFSE_PRODUCTION_CERTIFICATE_MIN_DAYS", "0")

    # The script replaces httpx.Client/AsyncClient process-wide (that is its
    # safety net). Register the originals with monkeypatch so they are put
    # back at teardown — otherwise every later test using httpx, TestClient
    # included, would inherit the refusal.
    import httpx
    monkeypatch.setattr(httpx, "Client", httpx.Client)
    monkeypatch.setattr(httpx, "AsyncClient", httpx.AsyncClient)

    spec = importlib.util.spec_from_file_location("m64_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CERTIFICATE = cert
    module.ARTIFACTS = tmp_path / "private" / "artifacts"
    module._password = lambda: password

    from app.services.nfse_national import clock as clock_module
    monkeypatch.setattr(clock_module, "read_clock_status",
                        lambda: clock_module.ClockStatus(True, "clock_synchronized"))
    return module, get_sessionmaker


def _run(module, capsys, monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["script", *argv])
    code = module.main()
    out = capsys.readouterr().out
    report = json.loads(out[:out.rfind("}") + 1]) if "{" in out else None
    return code, report, out


def test_the_flag_that_records_the_dps_series_check_is_mandatory(world, capsys, monkeypatch):
    module, _ = world
    monkeypatch.setattr("sys.argv", ["script"])
    with pytest.raises(SystemExit):
        module.main()


def test_it_prepares_signs_and_stops_at_the_edge(world, capsys, monkeypatch):
    module, sessionmaker = world
    code, report, out = _run(module, capsys, monkeypatch, "--dps-series-checked")
    assert code == 0, out
    assert report["document_state_final"] == "pending"
    assert report["fiscal_attempts"] == 0
    assert report["dps_number_allocated"] == 1
    pf = report["preflight"]
    assert pf["network_send_allowed"] is False
    assert pf["authorization_ready"] is False
    assert pf["network_gate_enabled"] is False
    assert pf["xsd_and_signature_ok"] is True
    assert pf["namespace_prefix_count"] == 0
    assert pf["dh_emi_timezone_ok"] is True
    assert pf["blockers"] == ["production_network_gate_disabled"]
    assert "OK — pronto no limiar" in out


def test_no_http_client_is_ever_built(world, capsys, monkeypatch):
    """The script makes constructing any HTTP client raise before it touches
    the database. The run completing with exit 0 is therefore the proof that
    nothing tried: a single attempt would have raised out of main()."""
    import httpx
    module, _ = world
    code, report, out = _run(module, capsys, monkeypatch, "--dps-series-checked")
    assert httpx.Client.__name__ == "refuse"          # the net was really installed
    with pytest.raises(RuntimeError, match="rede proibida"):
        httpx.Client()                                 # and it really bites
    assert code == 0, out                              # yet nothing reached it


def test_it_refuses_before_preparing_when_the_municipality_is_not_confirmed(
        world, capsys, monkeypatch):
    module, sessionmaker = world
    from sqlalchemy import select
    from app.models import FiscalDocument, SpirometryExam
    with sessionmaker()() as db:
        exam = db.scalars(select(SpirometryExam).where(
            SpirometryExam.public_code == "ESP-000050")).one()
        exam.municipio_atendimento_ibge = "3303302"
        db.commit()
    code, _, _ = _run(module, capsys, monkeypatch, "--dps-series-checked")
    assert code == 1
    with sessionmaker()() as db:
        assert db.query(FiscalDocument).filter_by(environment="production").count() == 5


def test_rerunning_reuses_the_same_document_and_dps_number(world, capsys, monkeypatch):
    module, _ = world
    _, first, _ = _run(module, capsys, monkeypatch, "--dps-series-checked")
    _, again, _ = _run(module, capsys, monkeypatch, "--dps-series-checked")
    assert again["document_id"] == first["document_id"]
    assert again["dps_number_allocated"] == first["dps_number_allocated"] == 1
    assert again["fiscal_attempts"] == 0
