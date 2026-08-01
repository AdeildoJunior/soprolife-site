#!/usr/bin/env python3
"""Regressões do snapshot Search Console, sem rede e sem credenciais reais."""

import importlib.util
import json
import sys
import tempfile
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "read_marketing_seo_adc", SCRIPTS / "read-marketing-seo-adc.py"
)
mkt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mkt)

SITE_URL = "https://soprolife.com.br/"
START = "2026-07-04"
END = "2026-07-31"


class FakeExecute:
    def __init__(self, response):
        self.response = response

    def execute(self):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeSearchAnalytics:
    def __init__(self, calls, responses):
        self.calls = calls
        self.responses = responses

    def query(self, *, siteUrl, body):
        self.calls.append({"siteUrl": siteUrl, "body": dict(body)})
        dimensions = tuple(body.get("dimensions", ()))
        return FakeExecute(self.responses[dimensions])


class FakeService:
    def __init__(self, calls, responses):
        self.analytics = FakeSearchAnalytics(calls, responses)

    def searchanalytics(self):
        return self.analytics


def fake_build(calls, responses):
    def build(*_args, **_kwargs):
        return FakeService(calls, responses)

    return build


def fixture_responses():
    # Linhas dimensionais deliberadamente não fecham com o agregado.
    return {
        (): {"rows": [{
            "clicks": 71,
            "impressions": 2160,
            "ctr": 0.033,
            "position": 6.1,
        }]},
        ("query",): {"rows": [
            {"keys": ["espirometria onde fazer rj"], "clicks": 2,
             "impressions": 47, "ctr": 2 / 47, "position": 4.0},
            {"keys": ["demais consultas"], "clicks": 59,
             "impressions": 1942, "ctr": 59 / 1942, "position": 6.4},
        ]},
        ("page",): {"rows": [
            {"keys": [SITE_URL + "espirometria/"], "clicks": 50,
             "impressions": 1500, "ctr": 1 / 30, "position": 5.8},
        ]},
        ("date",): {"rows": [
            {"keys": [START], "clicks": 1, "impressions": 30},
            {"keys": [END], "clicks": 4, "impressions": 90},
        ]},
    }


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(f"{message}: esperado {expected!r}, obtido {actual!r}")


def main():
    calls = []
    data, warnings = mkt._fetch_search_console(
        fake_build(calls, fixture_responses()), object(), SITE_URL,
        START, END, top_limit=20,
    )
    assert_equal(warnings, [], "consulta mockada sem avisos")
    assert_equal(data["totals"], {
        "impressions": 2160,
        "clicks": 71,
        "ctr": 0.033,
        "avgPosition": 6.1,
    }, "KPIs usam a linha agregada")
    assert_equal(
        sum(row["clicks"] for row in data["topQueries"]), 61,
        "fixture de consultas soma cliques divergentes",
    )
    assert_equal(
        sum(row["impressions"] for row in data["topQueries"]), 1989,
        "fixture de consultas soma impressões divergentes",
    )
    assert_equal(data["topQueries"][0]["query"],
                 "espirometria onde fazer rj", "ranking de query independente")
    assert_equal(data["topPages"][0]["page"], SITE_URL + "espirometria/",
                 "ranking de página independente")

    common = {
        "startDate": START,
        "endDate": END,
        "type": "web",
        "dataState": "all",
    }
    assert_equal(calls[0], {"siteUrl": SITE_URL, "body": common},
                 "corpo agregado não possui dimensões")
    assert_equal(calls[1]["body"], {
        **common, "dimensions": ["query"], "rowLimit": 25000,
    }, "consulta de queries independente")
    assert_equal(calls[2]["body"], {
        **common, "dimensions": ["page"], "rowLimit": 25000,
    }, "consulta de páginas independente")
    assert_equal(calls[3]["body"], {
        **common, "dimensions": ["date"],
    }, "tendência independente")
    for call in calls:
        assert_equal(call["siteUrl"], SITE_URL, "propriedade URL-prefix consistente")
        for field in ("startDate", "endDate", "type", "dataState"):
            assert_equal(call["body"].get(field), common[field],
                         f"campo comum {field}")
        if "filters" in call["body"] or "dimensionFilterGroups" in call["body"]:
            raise AssertionError("requisição não pode conter filtros adicionais")

    failed_data, failed_warnings = mkt._fetch_search_console(
        fake_build([], {(): RuntimeError("503 service unavailable")}),
        object(), SITE_URL, START, END, top_limit=20,
    )
    assert_equal(failed_data, {}, "falha upstream não fabrica KPIs zerados")
    if not failed_warnings:
        raise AssertionError("falha upstream precisa ser reportada")

    start, end, inclusive = mkt.canonical_search_console_window(
        today=date(2026, 8, 1), timezone_name="America/Sao_Paulo"
    )
    assert_equal((start, end, inclusive), (START, END, 28),
                 "janela canônica tem 28 datas e termina ontem")

    cfg = {"searchConsoleSiteUrl": SITE_URL, "ga4PropertyId": ""}
    success_result = {
        "searchConsole": {
            "ok": True, "data": data, "raw_warnings": [], "error_code": None,
        }
    }
    snapshot = mkt._montar_snapshot(
        cfg, None, success_result, (START, END, 28),
        "2026-08-01T12:00:00+00:00", 26,
    )
    assert_equal(snapshot["searchConsole"]["totals"], data["totals"],
                 "snapshot persiste KPIs agregados")
    assert_equal(snapshot["searchConsole"]["request"], {
        "siteUrl": SITE_URL, "startDate": START, "endDate": END,
        "type": "web", "dataState": "all",
    }, "snapshot persiste a requisição upstream")
    assert_equal(snapshot["searchConsole"]["totals"]["ctr"], 0.033,
                 "CTR permanece fração no snapshot")

    failed_snapshot = mkt._montar_snapshot(
        cfg, snapshot,
        {"searchConsole": {"ok": False, "data": {}, "raw_warnings": ["503"],
                           "error_code": "SYNC_FAILED"}},
        ("2026-07-05", "2026-08-01", 28),
        "2026-08-02T12:00:00+00:00", 26,
    )
    assert_equal(failed_snapshot["searchConsole"], snapshot["searchConsole"],
                 "falha preserva último Search Console válido")
    assert_equal(failed_snapshot["searchConsole"]["totals"]["clicks"], 71,
                 "falha nunca grava zeros")
    if failed_snapshot["meta"]["sourceStatus"]["searchConsole"]["errorCode"] is None:
        raise AssertionError("falha precisa ficar visível no sourceStatus")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        request_path = tmp_path / "refresh.json"
        snapshot_path = tmp_path / "snapshot.json"
        request_path.write_text(json.dumps({
            "requestId": "req-1", "requestedAt": "2026-08-01T11:59:00+00:00",
            "origin": "painel-autenticado", "state": "pending",
        }), encoding="utf-8")
        snapshot_path.write_text(json.dumps(failed_snapshot), encoding="utf-8")
        mkt._complete_refresh_request(request_path, snapshot_path, mkt.fc.EXIT_ERROR)
        result = json.loads(request_path.read_text(encoding="utf-8"))
        assert_equal(result["state"], "completed", "refresh conclui no backend")
        assert_equal(result["success"], False, "refresh falho não anuncia sucesso")
        assert_equal(result["degraded"], True, "refresh falho informa snapshot retido")

    print("OK: reconciliação Search Console (agregado, detalhes, janela e falha)")


if __name__ == "__main__":
    main()
