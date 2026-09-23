"""M65 — the script that makes the first real production POST, exercised offline.

The same synthetic operational database as M64 (ESP-000050 prepared and
preflighted, five protected manual notes, the production profile), a
throwaway certificate, and an ``httpx.MockTransport`` standing where SEFIN
would be. What must hold, for every outcome SEFIN could give:

- exactly one POST, to https://sefin.nfse.gov.br/SefinNacional/nfse;
- the DPS sent is the preflight DPS in everything but dhEmi;
- the signed bytes are on disk before the request leaves;
- a success is ``issued`` with ``fiscal_validity``; a rejection is ``failed``
  with no GET; an unknown outcome is reconciled with GETs only;
- a second run sends nothing at all.
"""
import base64
import gzip
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from tests.test_nfse_m64_preflight_script import world  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nfse_m65_first_production_issue_esp000050.py"
REAL_HTTPX_CLIENT = httpx.Client
ACCESS_KEY = "NFS" "3304557" "2" "2" "63544026000110" "0000000000001" "2609" "428247657" "6"
PROCESSED_AT = "2026-09-23T10:00:00.1234567-03:00"


def _envelope(key=ACCESS_KEY, tp_amb=1):
    nfse_xml = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">'
                f'<infNFSe Id="{key}"></infNFSe></NFSe>').encode()
    return json.dumps({"tipoAmbiente": tp_amb, "versaoAplicativo": "SefinNacional_1.6.0",
                       "dataHoraProcessamento": PROCESSED_AT, "chaveAcesso": key[3:],
                       "nfseXmlGZipB64": base64.b64encode(gzip.compress(nfse_xml)).decode()
                       }).encode()


@pytest.fixture
def m65(world, tmp_path, monkeypatch, capsys):  # noqa: F811
    """ESP-000050 preflighted by the real M64 script, then the M65 module
    pointed at the same synthetic database and a mock SEFIN."""
    m64, sessionmaker = world
    monkeypatch.setattr("sys.argv", ["m64", "--dps-series-checked"])
    assert m64.main() == 0
    capsys.readouterr()

    from sqlalchemy import select
    from app.models import FiscalArtifact, FiscalDocument
    from app.services.nfse_national import dispatch
    with sessionmaker()() as db:
        doc = db.scalars(select(FiscalDocument).where(FiscalDocument.state == "pending")).one()
        signed = db.scalars(select(FiscalArtifact).where(
            FiscalArtifact.document_id == doc.id, FiscalArtifact.kind == "dps_signed_xml")).one()
        document_id, preflight_sha = doc.id, signed.sha256

    monkeypatch.setattr(dispatch, "HttpxProductionTransport", dispatch.HttpxProductionTransport)
    spec = importlib.util.spec_from_file_location("m65_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.DOCUMENT_ID = document_id
    module.PREFLIGHT_SIGNED_SHA256 = preflight_sha
    module.CERTIFICATE = m64.CERTIFICATE
    module.ARTIFACTS = m64.ARTIFACTS
    module.RUNTIME = tmp_path / "runtime"
    module._password = m64._password
    monkeypatch.setenv("M15_NFSE_PRODUCTION_NETWORK_ENABLED", "true")

    sefin = {"queue": [], "requests": []}

    def handler(request: httpx.Request):
        body = request.content
        sefin["requests"].append((request.method, str(request.url), body))
        item = sefin["queue"].pop(0)
        if isinstance(item, Exception):
            raise item
        status, payload = item
        return httpx.Response(status, content=payload,
                              headers={"content-type": "application/json"})

    def mock_client(*args, **kwargs):
        return REAL_HTTPX_CLIENT(base_url=kwargs["base_url"], timeout=kwargs.get("timeout"),
                                 transport=httpx.MockTransport(handler))
    module._ORIGINAL_HTTPX_CLIENT = mock_client

    def run(*argv):
        monkeypatch.setattr("sys.argv", ["m65", *argv])
        code = module.main()
        out = capsys.readouterr().out
        start, end = out.find("{"), out.rfind("}")
        report = json.loads(out[start:end + 1]) if start >= 0 else None
        return code, report, out
    return module, sessionmaker, sefin, run


def _decoded_dps(body):
    return gzip.decompress(base64.b64decode(json.loads(body)["dpsXmlGZipB64"]))


def test_success_issues_once_and_is_fiscally_valid(m65):
    module, sessionmaker, sefin, run = m65
    sefin["queue"] = [(201, _envelope())]
    code, report, out = run("--confirm", "ESP-000050")
    assert code == 0, out
    assert [(m, u) for m, u, _ in sefin["requests"]] == [
        ("POST", "https://sefin.nfse.gov.br/SefinNacional/nfse")]
    final = report["final"]
    assert final["state"] == "issued"
    assert final["fiscal_validity"] is True
    assert final["external_id"] == ACCESS_KEY
    assert final["reconciliation_required"] is False
    assert final["dps_allocations_total"] == 1 and final["dps_number_max"] == 1
    assert {a["kind"] for a in final["artifacts"]} >= {"dps_signed_xml", "nfse_xml"}
    assert report["ledger"]["provider_post_count"] == 1
    assert report["ledger"]["provider_get_count"] == 0
    assert "FIRST REAL PRODUCTION NFSE ISSUED SUCCESSFULLY" in out

    # The bytes on disk before the send are the bytes that went out, and the
    # DPS is the preflight one in everything but dhEmi.
    sent = _decoded_dps(sefin["requests"][0][2])
    assert (module.RUNTIME / "submitted-dps-signed.xml").read_bytes() == sent
    summary = module.dps_summary(sent)
    assert (summary["tpAmb"], summary["serie"], summary["nDPS"], summary["vServ"],
            summary["dCompet"], summary["cLocPrestacao"]) == ("1", "1", "1", "220.00",
                                                              "2026-09-16", "3304557")
    evidence = next(a for a in final["artifacts"]
                    if a["kind"] == "dps_signed_xml" and a["attempt"])
    import hashlib
    assert evidence["sha256"] == hashlib.sha256(sent).hexdigest()


def test_a_second_run_sends_nothing(m65):
    module, _, sefin, run = m65
    sefin["queue"] = [(201, _envelope())]
    run("--confirm", "ESP-000050")
    sefin["requests"].clear()
    code, _, _ = run("--confirm", "ESP-000050")
    assert code == 3
    assert sefin["requests"] == []


def test_timeout_is_reconciled_with_a_get_and_never_a_second_post(m65):
    module, _, sefin, run = m65
    sefin["queue"] = [httpx.ReadTimeout("synthetic"), (200, _envelope())]
    code, report, out = run("--confirm", "ESP-000050")
    methods = [m for m, _, _ in sefin["requests"]]
    assert methods == ["POST", "GET"]
    assert "/SefinNacional/dps/DPS3304557263544026000110000010" in sefin["requests"][1][1]
    assert report["after_issue"]["state"] == "uncertain"
    assert report["final"]["state"] == "issued"
    assert report["final"]["fiscal_validity"] is True
    assert report["ledger"]["provider_post_count"] == 1
    assert "FIRST REAL PRODUCTION NFSE ISSUED SUCCESSFULLY" in out


def test_inconclusive_reconciliation_stays_uncertain(m65):
    _, _, sefin, run = m65
    sefin["queue"] = [httpx.ReadTimeout("synthetic"), (500, b"{}")]
    code, report, out = run("--confirm", "ESP-000050")
    assert [m for m, _, _ in sefin["requests"]] == ["POST", "GET"]
    assert report["final"]["state"] == "uncertain"
    assert report["final"]["reconciliation_required"] is True
    assert report["ledger"]["provider_post_count"] == 1
    assert "FIRST REAL PRODUCTION NFSE UNCERTAIN — DO NOT RETRY POST" in out


def test_rejection_is_final_and_queries_nothing(m65):
    _, _, sefin, run = m65
    sefin["queue"] = [(400, json.dumps({"erros": [{"Codigo": "E0014",
                                                   "Descricao": "DPS já existe"}]}).encode())]
    code, report, out = run("--confirm", "ESP-000050")
    assert [m for m, _, _ in sefin["requests"]] == ["POST"]
    assert report["final"]["state"] == "failed"
    assert report["final"]["dps_number_max"] == 1
    assert "FIRST REAL PRODUCTION NFSE REJECTED — NO RETRY PERFORMED" in out


def test_without_the_process_gate_nothing_happens(m65, monkeypatch):
    module, _, sefin, run = m65
    monkeypatch.delenv("M15_NFSE_PRODUCTION_NETWORK_ENABLED")
    code, _, _ = run("--confirm", "ESP-000050")
    assert code == 2
    assert sefin["requests"] == []
    assert not (module.RUNTIME / module.FIRE_MARKER_NAME).exists()


def test_a_changed_fact_stops_before_the_marker_and_the_post(m65):
    module, sessionmaker, sefin, run = m65
    from sqlalchemy import select
    from app.models import FinancialEntry, FiscalAttempt, SpirometryExam
    with sessionmaker()() as db:
        exam = db.scalars(select(SpirometryExam).where(
            SpirometryExam.public_code == "ESP-000050")).one()
        entry = db.scalars(select(FinancialEntry).where(
            FinancialEntry.spirometry_exam_id == exam.id)).one()
        entry.valor = Decimal("230.00")
        db.commit()
    code, report, out = run("--confirm", "ESP-000050")
    assert code == 1
    assert "preparation_not_stale" in report["guard_failures"]
    assert sefin["requests"] == []
    assert not (module.RUNTIME / module.FIRE_MARKER_NAME).exists()
    with sessionmaker()() as db:
        assert db.query(FiscalAttempt).filter_by(operation="issue").count() == 0


def test_the_transport_refuses_a_second_post(m65):
    module, _, sefin, _ = m65
    from app.services.nfse_national.transport import TransportRequest
    module.LEDGER.post_count = 1
    transport = module.M65OneShotProductionTransport(network_enabled=True,
                                                     environment="production")
    with pytest.raises(module.OneShotViolation, match="SEGUNDO POST"):
        transport.send(TransportRequest(method="POST", path="/nfse", body=b"{}"))
    assert module.LEDGER.second_post_attempted is True
    assert sefin["requests"] == []
