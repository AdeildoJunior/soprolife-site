#!/usr/bin/env python3
"""
SoproLife OS Local Core — Marketing & SEO connector (ADC).

Lê dados agregados do Google Search Console e GA4 usando
Application Default Credentials (gcloud). Nunca imprime
property ID, URLs privadas, tokens ou credenciais.

Pré-requisito:
    gcloud auth application-default login \\
        --scopes=https://www.googleapis.com/auth/webmasters.readonly,\\
                 https://www.googleapis.com/auth/analytics.readonly

Uso:
    python3 painel-soprolife/scripts/read-marketing-seo-adc.py
    python3 painel-soprolife/scripts/read-marketing-seo-adc.py --dry-run
    python3 painel-soprolife/scripts/read-marketing-seo-adc.py --write
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_CONFIG_PATH = Path("painel-soprolife/data-private/marketing-seo-config.local.json")
_OUT_PRIVATE = Path("~/.config/soprolife/painel/marketing-seo.json").expanduser()
_OUT_PUBLIC  = Path("painel-soprolife/data/marketing-seo.local.json")

SC_SCOPE  = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

FORBIDDEN_OUTPUT = [
    "cpf", "refresh_token", "access_token", "client_secret",
    "private_key", "api_key", "apikey",
]


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_config():
    if not _CONFIG_PATH.exists():
        return None
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"AVISO: configuração JSON inválida — {exc}")
        return None
    if not isinstance(cfg, dict):
        print("AVISO: configuração deve ser um objeto JSON.")
        return None
    return cfg


def _not_configured_output():
    return {
        "meta": {
            "configured": False,
            "generatedAt": _now_iso(),
            "safeToDisplay": True,
            "containsPersonalData": False,
        }
    }


def _load_google_libs():
    try:
        from googleapiclient.discovery import build
        from google.auth import default as google_auth_default
        return build, google_auth_default
    except ImportError as exc:
        print(f"AVISO: dependências google-api-python-client não instaladas — {exc}")
        print()
        print("Instale com:")
        print("  pip install -r painel-soprolife/requirements-google.txt")
        return None, None


def _load_ga4_lib():
    try:
        from google.analytics.data.v1beta import BetaAnalyticsDataClient
        from google.analytics.data.v1beta.types import (
            RunReportRequest, Dimension, Metric, DateRange,
        )
        return BetaAnalyticsDataClient, RunReportRequest, Dimension, Metric, DateRange
    except ImportError:
        return None, None, None, None, None


def _fetch_search_console(build, credentials, site_url, start_date, end_date, top_limit):
    warnings = []
    result = {}

    try:
        service = build("webmasters", "v3", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        warnings.append(f"Search Console: falha ao inicializar cliente — {exc}")
        return result, warnings

    def sc_query(dimensions, row_limit=top_limit):
        try:
            resp = service.searchanalytics().query(
                siteUrl=site_url,
                body={
                    "startDate": start_date,
                    "endDate": end_date,
                    "dimensions": dimensions,
                    "rowLimit": row_limit,
                }
            ).execute()
            return resp.get("rows", [])
        except Exception as exc:
            msg = str(exc)
            if "403" in msg or "PERMISSION_DENIED" in msg:
                warnings.append("Search Console: acesso negado. Verifique permissão.")
            elif "404" in msg:
                warnings.append("Search Console: site não encontrado. Verifique searchConsoleSiteUrl.")
            elif "400" in msg:
                warnings.append(f"Search Console: requisição inválida — {exc}")
            else:
                warnings.append(f"Search Console: {exc}")
            return None

    # Totais (sem dimensões)
    try:
        resp = service.searchanalytics().query(
            siteUrl=site_url,
            body={"startDate": start_date, "endDate": end_date, "dimensions": [], "rowLimit": 1}
        ).execute()
        rows = resp.get("rows", [])
        if rows:
            r = rows[0]
            result["totals"] = {
                "impressions": int(r.get("impressions", 0)),
                "clicks": int(r.get("clicks", 0)),
                "ctr": round(float(r.get("ctr", 0)), 4),
                "avgPosition": round(float(r.get("position", 0)), 1),
            }
    except Exception as exc:
        warnings.append(f"Search Console totais: {exc}")

    # Top consultas
    rows = sc_query(["query"])
    if rows is not None:
        result["topQueries"] = [
            {
                "query": r["keys"][0],
                "impressions": int(r.get("impressions", 0)),
                "clicks": int(r.get("clicks", 0)),
                "ctr": round(float(r.get("ctr", 0)), 4),
                "avgPosition": round(float(r.get("position", 0)), 1),
            }
            for r in rows
        ]

    # Top páginas
    rows = sc_query(["page"])
    if rows is not None:
        result["topPages"] = [
            {
                "page": r["keys"][0],
                "impressions": int(r.get("impressions", 0)),
                "clicks": int(r.get("clicks", 0)),
                "ctr": round(float(r.get("ctr", 0)), 4),
                "avgPosition": round(float(r.get("position", 0)), 1),
            }
            for r in rows
        ]

    # Evolução por data
    rows = sc_query(["date"], row_limit=90)
    if rows is not None:
        result["byDate"] = [
            {
                "date": r["keys"][0],
                "impressions": int(r.get("impressions", 0)),
                "clicks": int(r.get("clicks", 0)),
            }
            for r in rows
        ]

    return result, warnings


def _fetch_ga4(credentials, property_id, start_date, end_date, top_limit):
    warnings = []
    result = {}

    GA4Client, RunReportRequest, Dimension, Metric, DateRange = _load_ga4_lib()
    if GA4Client is None:
        warnings.append(
            "GA4: biblioteca 'google-analytics-data' não instalada. "
            "Execute: pip install google-analytics-data"
        )
        return result, warnings

    try:
        client = GA4Client(credentials=credentials)
    except Exception as exc:
        warnings.append(f"GA4: falha ao inicializar cliente — {exc}")
        return result, warnings

    def ga4_query(dimensions, metrics, limit=top_limit):
        try:
            req = RunReportRequest(
                property=f"properties/{property_id}",
                date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                dimensions=[Dimension(name=d) for d in dimensions],
                metrics=[Metric(name=m) for m in metrics],
                limit=limit,
            )
            return client.run_report(request=req)
        except Exception as exc:
            msg = str(exc)
            if "403" in msg or "PERMISSION_DENIED" in msg:
                warnings.append("GA4: acesso negado. Verifique permissão ou property ID.")
            elif "404" in msg:
                warnings.append("GA4: propriedade não encontrada. Verifique ga4PropertyId.")
            elif "enable" in msg.lower() and "api" in msg.lower():
                warnings.append("GA4: API não habilitada. Acesse Google Cloud Console.")
            else:
                warnings.append(f"GA4: {exc}")
            return None

    # Totais
    resp = ga4_query([], ["activeUsers", "sessions", "screenPageViews"])
    if resp is not None and resp.rows:
        vals = resp.rows[0].metric_values
        result["totals"] = {
            "users":     int(vals[0].value) if len(vals) > 0 else 0,
            "sessions":  int(vals[1].value) if len(vals) > 1 else 0,
            "pageviews": int(vals[2].value) if len(vals) > 2 else 0,
        }

    # Top páginas
    resp = ga4_query(["pagePath"], ["screenPageViews", "activeUsers"])
    if resp is not None:
        result["topPages"] = [
            {
                "page":     row.dimension_values[0].value,
                "pageviews": int(row.metric_values[0].value),
                "users":    int(row.metric_values[1].value),
            }
            for row in resp.rows
        ]

    # Fontes de tráfego
    resp = ga4_query(["sessionSource", "sessionMedium"], ["sessions"])
    if resp is not None:
        result["trafficSources"] = [
            {
                "source":  row.dimension_values[0].value,
                "medium":  row.dimension_values[1].value,
                "sessions": int(row.metric_values[0].value),
            }
            for row in resp.rows
        ]

    # Eventos (funil)
    resp = ga4_query(["eventName"], ["eventCount"], limit=20)
    if resp is not None:
        result["events"] = [
            {
                "event": row.dimension_values[0].value,
                "count": int(row.metric_values[0].value),
            }
            for row in resp.rows
        ]

    return result, warnings


def _scan_for_secrets(output):
    text = json.dumps(output, ensure_ascii=False).lower()
    for pattern in FORBIDDEN_OUTPUT:
        if pattern in text:
            return pattern
    return None


def _write_output(output):
    found = _scan_for_secrets(output)
    if found:
        print(f"ERRO CRÍTICO: padrão proibido '{found}' detectado no JSON de saída.")
        print("  Abortando gravação para proteger dados sensíveis.")
        sys.exit(1)

    text = json.dumps(output, ensure_ascii=False, indent=2) + "\n"

    _OUT_PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PRIVATE.write_text(text, encoding="utf-8")
    _OUT_PRIVATE.chmod(0o600)
    print(f"Gravado em: {_OUT_PRIVATE}")

    _OUT_PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PUBLIC.write_text(text, encoding="utf-8")
    _OUT_PUBLIC.chmod(0o600)
    print(f"Sincronizado em: {_OUT_PUBLIC}")
    print()
    print("Execute a seguir para verificar segurança:")
    print("  bash painel-soprolife/scripts/check-access.sh")


def main():
    parser = argparse.ArgumentParser(
        description="Marketing & SEO connector ADC — SoproLife (sem chave privada)"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Valida sem gravar (padrão)")
    group.add_argument("--write",   action="store_true", help="Grava JSON de saída")
    args = parser.parse_args()
    mode = "write" if args.write else "dry-run"

    print("SoproLife OS Local Core — Marketing & SEO connector (ADC)")
    print(f"mode: {mode}")
    print("auth: Application Default Credentials")
    print()

    cfg = _load_config()
    if cfg is None:
        print("INFO: configuração não encontrada.")
        print(f"  Esperado em: {_CONFIG_PATH}")
        print("  Copie painel-soprolife/config-examples/marketing-seo.local.example.json")
        print("  para esse caminho e preencha os valores reais.")
        print()
        if mode == "write":
            print("Gravando JSON de fallback (configured=false)...")
            _write_output(_not_configured_output())
        return 0

    ga4_prop  = str(cfg.get("ga4PropertyId", "")).strip()
    sc_url    = str(cfg.get("searchConsoleSiteUrl", "")).strip()
    lookback  = max(1, int(cfg.get("lookbackDays", 28)))
    top_limit = max(1, int(cfg.get("topLimit", 20)))

    if not sc_url and not ga4_prop:
        print("AVISO: ga4PropertyId e searchConsoleSiteUrl estão vazios — nada a consultar.")
        if mode == "write":
            _write_output(_not_configured_output())
        return 0

    today      = date.today()
    end_date   = (today - timedelta(days=1)).isoformat()
    start_date = (today - timedelta(days=lookback)).isoformat()

    print(f"Período: {start_date} → {end_date} ({lookback} dias)")
    print()

    build, google_auth_default = _load_google_libs()
    if build is None:
        if mode == "write":
            _write_output(_not_configured_output())
        return 0

    scopes = []
    if sc_url:
        scopes.append(SC_SCOPE)
    if ga4_prop:
        scopes.append(GA4_SCOPE)

    try:
        credentials, _ = google_auth_default(scopes=scopes)
    except Exception as exc:
        print(f"AVISO: falha na autenticação ADC — {exc}")
        print()
        print("Execute para autenticar com os escopos corretos:")
        for s in scopes:
            print(f"    {s}")
        print("  gcloud auth application-default login --scopes=<escopos acima>")
        if mode == "write":
            _write_output(_not_configured_output())
        return 0

    all_warnings = []
    sc_data  = {}
    ga4_data = {}

    if sc_url:
        print("Consultando Search Console...")
        sc_data, sc_warn = _fetch_search_console(
            build, credentials, sc_url, start_date, end_date, top_limit
        )
        all_warnings.extend(sc_warn)
        if sc_warn:
            for w in sc_warn:
                print(f"  AVISO: {w}")
        else:
            totals = sc_data.get("totals", {})
            print(f"  OK — impressões: {totals.get('impressions', 0)}, "
                  f"cliques: {totals.get('clicks', 0)}")
        print()

    if ga4_prop:
        print("Consultando GA4...")
        ga4_data, ga4_warn = _fetch_ga4(
            credentials, ga4_prop, start_date, end_date, top_limit
        )
        all_warnings.extend(ga4_warn)
        if ga4_warn:
            for w in ga4_warn:
                print(f"  AVISO: {w}")
        else:
            totals = ga4_data.get("totals", {})
            print(f"  OK — usuários: {totals.get('users', 0)}, "
                  f"sessões: {totals.get('sessions', 0)}")
        print()

    sources_ok = {
        "searchConsole": bool(sc_data),
        "ga4": bool(ga4_data),
    }

    output = {
        "meta": {
            "configured": True,
            "generatedAt": _now_iso(),
            "periodStart": start_date,
            "periodEnd": end_date,
            "lookbackDays": lookback,
            "sources": sources_ok,
            "safeToDisplay": True,
            "containsPersonalData": False,
        },
        "warnings": all_warnings,
    }
    if sc_data:
        output["searchConsole"] = sc_data
    if ga4_data:
        output["ga4"] = ga4_data

    print("Validação concluída. Nenhum dado pessoal exportado.")
    print(f"  Search Console: {'OK' if sources_ok['searchConsole'] else 'indisponível'}")
    print(f"  GA4:            {'OK' if sources_ok['ga4'] else 'indisponível'}")

    if mode == "dry-run":
        print()
        print("next_step: use --write para gravar os dados.")
        return 0

    print()
    _write_output(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
