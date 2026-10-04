#!/usr/bin/env python3
"""Janela móvel de Marketing (Search Console + GA4), sem rede e sem credenciais.

Prova: 60 datas por padrão, encerradas ontem em America/Sao_Paulo, viradas de
mês/ano, `lookbackDays` da configuração respeitado e validado, Search Console e
GA4 consultados com o MESMO período, meta coerente e snapshot anterior
preservado em falha.
"""

import argparse
import importlib.util
import json
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "read_marketing_seo_adc", SCRIPTS / "read-marketing-seo-adc.py"
)
mkt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mkt)

FALHAS = []


def check(cond, msg):
    print(("  PASS: " if cond else "  FAIL: ") + msg)
    if not cond:
        FALHAS.append(msg)


def dias(start, end):
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


print("── Janela canônica ──")
check(mkt.SC_LOOKBACK_DAYS == 60, "padrão canônico = 60 dias")

s, e, n = mkt.canonical_search_console_window(today=date(2026, 10, 4))
check((s, e, n) == ("2026-08-05", "2026-10-03", 60), f"04/10 → 05/08..03/10, 60 datas ({s}..{e})")
check(dias(s, e) == 60, "exatamente 60 datas inclusivas")
check(e == "2026-10-03", "termina ontem; o dia corrente fica fora")

s, e, n = mkt.canonical_search_console_window(today=date(2026, 3, 1))
check((s, e, n) == ("2025-12-31", "2026-02-28", 60), f"virada de mês e de ano ({s}..{e})")

s, e, _ = mkt.canonical_search_console_window(today=date(2027, 1, 1))
check((s, e) == ("2026-11-02", "2026-12-31"), f"virada de ano em 01/01 ({s}..{e})")

# 02:30 UTC de 04/10 ainda é 03/10 (23:30) em São Paulo → termina em 02/10.
real_datetime = mkt.datetime


class FakeDatetime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        base = real_datetime(2026, 10, 4, 2, 30, tzinfo=timezone.utc)
        return base.astimezone(tz) if tz else base


mkt.datetime = FakeDatetime
try:
    s, e, _ = mkt.canonical_search_console_window(timezone_name="America/Sao_Paulo")
finally:
    mkt.datetime = real_datetime
check(e == "2026-10-02", f"timezone America/Sao_Paulo decide 'ontem' ({e})")

print("── lookbackDays da configuração ──")
check(mkt.resolve_lookback_days({}) == 60, "ausente → 60")
check(mkt.resolve_lookback_days({"lookbackDays": 60}) == 60, "60 respeitado")
check(mkt.resolve_lookback_days({"lookbackDays": 90}) == 90, "outro valor válido respeitado")
for ruim in (0, 6, 481, "60", 60.0, True, -1):
    try:
        mkt.resolve_lookback_days({"lookbackDays": ruim})
        check(False, f"recusa lookbackDays={ruim!r}")
    except ValueError:
        check(True, f"recusa lookbackDays={ruim!r}")
try:
    mkt.canonical_search_console_window(today=date(2026, 10, 4), lookback_days=1000)
    check(False, "janela recusa faixa absurda")
except ValueError:
    check(True, "janela recusa faixa absurda")

example = json.loads((ROOT / "config-examples/marketing-seo.local.example.json").read_text())
check(example.get("lookbackDays") == 60, "config de exemplo diz 60, igual ao código")

print("── Search Console e GA4 no mesmo período ──")
chamadas = {}


def fake_sc(build, credentials, site_url, start_date, end_date, top_limit):
    chamadas["sc"] = (start_date, end_date)
    by_date = []
    d = date.fromisoformat(start_date)
    while d <= date.fromisoformat(end_date):
        by_date.append({"date": d.isoformat(), "impressions": 10, "clicks": 1})
        d += timedelta(days=1)
    return ({"totals": {"impressions": 10 * len(by_date), "clicks": len(by_date),
                        "ctr": 0.1, "avgPosition": 9.0},
             "byDate": by_date,
             "request": {"siteUrl": site_url, "startDate": start_date,
                         "endDate": end_date, "type": "web", "dataState": "all"}}, [])


def fake_ga4(credentials, property_id, start_date, end_date, top_limit):
    chamadas["ga4"] = (start_date, end_date)
    return ({"totals": {"users": 5, "sessions": 6, "pageviews": 7}}, [])


def fake_fail_sc(*a, **k):
    return ({}, ["503 service unavailable"])


def fake_fail_ga4(*a, **k):
    return ({}, ["503 service unavailable"])


cfg_atual = {"searchConsoleSiteUrl": "https://exemplo.invalid/", "ga4PropertyId": "000",
             "timezone": "America/Sao_Paulo"}
mkt._load_config = lambda: dict(cfg_atual)
mkt._load_google_libs = lambda: (object(), object())
mkt.resolver_credencial = lambda scopes, gad: (object(), mkt.CRED_SERVICE_ACCOUNT, None)
mkt._com_retentativa = lambda nome, consulta, dormir=None: consulta()


def rodar(out):
    args = argparse.Namespace(output=str(out), max_age_hours=None, no_network=False,
                              refresh_request=None)
    mkt.cmd_sync(args, "write")
    return json.loads(Path(out).read_text(encoding="utf-8"))


ontem = (datetime.now(mkt.ZoneInfo("America/Sao_Paulo")).date() - timedelta(days=1)).isoformat()
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "marketing-seo.local.json"
    mkt._fetch_search_console, mkt._fetch_ga4 = fake_sc, fake_ga4
    snap = rodar(out)
    meta = snap["meta"]
    check(chamadas["sc"] == chamadas["ga4"], f"SC e GA4 com o mesmo período {chamadas['sc']}")
    check(meta["lookbackDays"] == 60, "meta.lookbackDays = 60")
    check((meta["periodStart"], meta["periodEnd"]) == chamadas["sc"], "meta.periodStart/End = período consultado")
    check(meta["periodEnd"] == ontem and dias(meta["periodStart"], meta["periodEnd"]) == 60,
          "período real: 60 datas encerradas ontem")
    req = snap["searchConsole"]["request"]
    check(dias(req["startDate"], req["endDate"]) == 60, "request do SC (base do subtítulo) cobre 60 dias")
    check(len(snap["searchConsole"]["byDate"]) == 60, "byDate diário com 60 pontos")
    check(meta["containsPersonalData"] is False and meta["safeToDisplay"] is True,
          "contrato de PII/exibição mantido")

    print("── Falha preserva o snapshot anterior ──")
    anterior = json.dumps(snap["searchConsole"], sort_keys=True)
    mkt._fetch_search_console, mkt._fetch_ga4 = fake_fail_sc, fake_fail_ga4
    snap2 = rodar(out)
    check(json.dumps(snap2["searchConsole"], sort_keys=True) == anterior,
          "falha upstream preserva os dados do SC anterior")
    check("ga4" in snap2 and snap2["ga4"]["totals"]["users"] == 5, "falha preserva GA4 anterior")

    cfg_atual["lookbackDays"] = 3
    mkt._fetch_search_console, mkt._fetch_ga4 = fake_sc, fake_ga4
    chamadas.clear()
    snap3 = rodar(out)
    check(not chamadas, "configuração inválida não consulta nada")
    check(json.dumps(snap3["searchConsole"], sort_keys=True) == anterior,
          "configuração inválida preserva o snapshot anterior")
    check(snap3["meta"]["sourceStatus"]["searchConsole"]["errorCode"] == "SYNC_FAILED",
          "configuração inválida aparece como falha, não como 'não configurada'")

print()
if FALHAS:
    print(f"RESULTADO: {len(FALHAS)} falha(s).")
    sys.exit(1)
print("RESULTADO: janela de Marketing íntegra.")
