#!/usr/bin/env python3
"""
SoproLife OS Local Core — Marketing & SEO connector (leitura).

Lê dados agregados do Google Search Console e GA4. Nunca imprime property ID,
URLs privadas, tokens ou credenciais.

M21 — credencial DURÁVEL de leitura (a mudança que faz o timer parar de
depender de um login humano que vence):
  ordem de resolução, primeira que existir ganha —
    1. SOPROLIFE_MARKETING_CREDENTIALS  (caminho explícito de conta de serviço)
    2. /opt/soprolife/secrets/marketing-readonly.json   (padrão de produção)
    3. GOOGLE_APPLICATION_CREDENTIALS   (honrado por google.auth.default)
    4. ADC pessoal do gcloud            (SÓ desenvolvimento)
  Com SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT=1 (o que a unit de produção
  define) o passo 4 é PROIBIDO: sem conta de serviço o conector falha fechado
  com CREDENTIAL_PENDING em vez de voltar a depender de ADC pessoal.
  O tipo da credencial ("service_account" / "personal_adc") entra no snapshot
  como diagnóstico; o e-mail, o project e a chave privada NUNCA entram.

M14.3A.1 — contrato de frescor operacional:
  - snapshot schema v2 com sourceStatus por fonte (freshness-contract.json);
  - falha em uma fonte NUNCA apaga a última versão válida da outra;
  - gravação atômica (tmp + validação + rename);
  - erro de autenticação vira estado authentication_required, nunca
    "sem tráfego";
  - exit codes padronizados (ver core/contracts/freshness-contract.json).

Pré-requisito de produção (uma vez, por humano):
    conta de serviço dedicada com acesso SOMENTE LEITURA concedido em
    Search Console e GA4; chave em /opt/soprolife/secrets/ fora do Git.
Pré-requisito de desenvolvimento (fallback):
    gcloud auth application-default login \\
        --scopes=https://www.googleapis.com/auth/webmasters.readonly,\\
                 https://www.googleapis.com/auth/analytics.readonly

Uso:
    python3 painel-soprolife/scripts/read-marketing-seo-adc.py --dry-run
    python3 painel-soprolife/scripts/read-marketing-seo-adc.py --write
    python3 painel-soprolife/scripts/read-marketing-seo-adc.py --status
    python3 painel-soprolife/scripts/read-marketing-seo-adc.py --check
Flags:
    --max-age-hours N   limite de frescor na avaliação (padrão: 26h)
    --output PATH       snapshot alternativo (testes/fixtures)
    --no-network        proíbe qualquer chamada externa (modos de teste)
    --credential-check  só resolve e classifica a credencial (sem rede)

Exit codes: 0=fresh · 10=stale · 11=autenticação · 12=schema · 13=indisponível
            14=erro · 15=desconhecido · 16=credencial pendente
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Guarda de PII compartilhada (M2) — mesma pasta deste script.
# _scan_for_secrets abaixo permanece como redundância.
import pii_guard
import freshness_contract as fc

# Regras da guarda para o marketing-seo.local.json (mesmo conteúdo vai ao
# arquivo privado e ao público — a guarda protege os dois):
# - query = termos de busca digitados por usuários do Google (agregados,
#   permitidos pelo projeto) e page/siteUrl = URLs do site público —
#   isentos do detector de nome, mas scans de telefone/CPF/segredos valem
#   (um telefone digitado como busca NÃO pode chegar ao painel);
# - warnings = mensagens do catálogo fixo do contrato de frescor;
# - sourceName/errorMessageSafe = textos fixos do contrato ("Search Console",
#   mensagens do catálogo) — isentos só do detector de nome.
_PII_RULES = {
    "campos_pessoa": [],
    "campos_institucionais": ["query", "page", "siteUrl", "warnings", "sources",
                              "sourceName", "errorMessageSafe"],
    "chaves_proibidas_extras": [],
}

_CONFIG_PATH = Path("painel-soprolife/data-private/marketing-seo-config.local.json")
_OUT_PRIVATE = Path("~/.config/soprolife/painel/marketing-seo.json").expanduser()
_OUT_PUBLIC  = Path("painel-soprolife/data/marketing-seo.local.json")

SC_SCOPE  = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

# ── Credencial durável de leitura (M21) ─────────────────────────────────────
# Caminho padrão de produção. Fica FORA do Git por construção (/opt), com
# permissão restrita, e nunca é lido pelo navegador.
_SA_DEFAULT_PATH = Path("/opt/soprolife/secrets/marketing-readonly.json")
_ENV_SA_PATH = "SOPROLIFE_MARKETING_CREDENTIALS"
_ENV_REQUIRE_SA = "SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT"

CRED_SERVICE_ACCOUNT = "service_account"
CRED_PERSONAL_ADC = "personal_adc"
CRED_NONE = "none"


def _service_account_path():
    """Caminho da conta de serviço, se houver. Nunca lê o conteúdo aqui."""
    bruto = os.environ.get(_ENV_SA_PATH, "").strip()
    if bruto:
        p = Path(bruto).expanduser()
        return p if p.is_file() else None
    if _SA_DEFAULT_PATH.is_file():
        return _SA_DEFAULT_PATH
    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if gac:
        p = Path(gac).expanduser()
        return p if p.is_file() else None
    return None


def _requer_service_account() -> bool:
    return os.environ.get(_ENV_REQUIRE_SA, "").strip() in ("1", "true", "yes", "sim")


def _classificar_arquivo_credencial(path):
    """Devolve o campo `type` do JSON — só o tipo, nunca e-mail nem chave."""
    try:
        dados = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(dados, dict):
        return None
    tipo = dados.get("type")
    return tipo if isinstance(tipo, str) else None


def _credencial_tem_permissao_restrita(path) -> bool:
    """Aceita 0600/0640 etc.; qualquer acesso para `other` falha fechado."""
    try:
        return (Path(path).stat().st_mode & 0o007) == 0
    except OSError:
        return False


def resolver_credencial(scopes, google_auth_default):
    """Resolve a credencial de leitura e devolve (credentials, kind, erro).

    Fail-closed: quando a conta de serviço é obrigatória e não existe (ou não
    é de fato service_account), NÃO cai para ADC pessoal — devolve
    CREDENTIAL_PENDING, que a UI mostra como "Credencial/configuração
    pendente" em vez de "Reautenticação necessária".
    """
    sa_path = _service_account_path()
    exige_sa = _requer_service_account()

    if sa_path is not None:
        if not _credencial_tem_permissao_restrita(sa_path):
            return None, CRED_NONE, "CREDENTIAL_PENDING"
        tipo = _classificar_arquivo_credencial(sa_path)
        if tipo != "service_account":
            # Arquivo existe mas não é conta de serviço: nunca usar às cegas.
            return None, CRED_NONE, "CREDENTIAL_PENDING"
        try:
            from google.oauth2 import service_account
        except ImportError:
            return None, CRED_NONE, "DEPENDENCY_MISSING"
        try:
            creds = service_account.Credentials.from_service_account_file(
                str(sa_path), scopes=list(scopes)
            )
        except Exception:
            # Mensagem crua omitida de propósito: pode conter caminho/identidade.
            return None, CRED_NONE, "CREDENTIAL_PENDING"
        return creds, CRED_SERVICE_ACCOUNT, None

    if exige_sa:
        return None, CRED_NONE, "CREDENTIAL_PENDING"

    # Desenvolvimento: ADC pessoal do gcloud. Vence com o tempo — por isso
    # produção define SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT=1.
    try:
        creds, _proj = google_auth_default(scopes=list(scopes))
    except Exception as exc:
        codigo = fc.classificar_erro(exc)
        return None, CRED_PERSONAL_ADC, (
            codigo if codigo != "SYNC_FAILED" else "AUTH_REQUIRED"
        )
    return creds, CRED_PERSONAL_ADC, None

# chave no snapshot, sourceId do contrato, nome de exibição
_SOURCES_DEF = [
    ("searchConsole", "search-console", "Search Console"),
    ("ga4", "ga4", "GA4"),
]

DEFAULT_STALE_HOURS = 26  # sincronização diária + margem

# Retentativa limitada (M21): erro transitório de rede/quota não deve deixar o
# painel "antigo" por 10 minutos inteiros, mas também não pode martelar a API.
# Erro de credencial/permissão NÃO é retentado — não melhora tentando de novo.
RETRY_MAX_TENTATIVAS = 3
RETRY_BACKOFF_SEGUNDOS = (2, 6)      # espera entre tentativas (limitada)
RETRY_NAO_RETENTAR = frozenset({
    "AUTH_REQUIRED", "CREDENTIAL_PENDING", "PERMISSION_DENIED",
    "NOT_CONFIGURED", "DEPENDENCY_MISSING", "NETWORK_BLOCKED",
    "SOURCE_NOT_FOUND",
})

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


def _load_google_libs():
    try:
        from googleapiclient.discovery import build
        from google.auth import default as google_auth_default
        return build, google_auth_default
    except ImportError as exc:
        print(f"AVISO: dependências google-api-python-client não instaladas — {exc}")
        print()
        print(f"Interpretador em uso: {sys.executable}")
        print("O conector precisa rodar no venv dedicado de Marketing, que o")
        print("deploy cria a partir de painel-soprolife/requirements-marketing.lock:")
        print("  /opt/soprolife/venvs/marketing/bin/python")
        print("Em produção, reexecute o deploy — não instale pacotes à mão.")
        return None, None


def _load_ga4_lib():
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            RunReportRequest, Dimension, Metric, DateRange,
        )
        return BetaAnalyticsDataClient, RunReportRequest, Dimension, Metric, DateRange, None
    except ImportError as exc:
        return None, None, None, None, None, str(exc)


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

    GA4Client, RunReportRequest, Dimension, Metric, DateRange, import_err = _load_ga4_lib()
    if GA4Client is None:
        if import_err:
            warnings.append(
                f"GA4: falha ao importar biblioteca — {import_err}. "
                "Rode o conector no venv dedicado de Marketing."
            )
        else:
            warnings.append(
                "GA4: biblioteca 'google-analytics-data' não instalada. "
                "Rode o conector no venv dedicado de Marketing."
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


# ───────────────────────── Snapshot v2 / contrato de frescor ────────────────

def _carregar_snapshot(path):
    """Carrega snapshot existente; None se ausente ou ilegível (nunca apaga)."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("meta"), dict):
        return None
    return data


def _source_status_do_snapshot(snap):
    """Bloco sourceStatus do snapshot; sintetiza a partir do formato legado v1."""
    if not snap:
        return {}
    meta = snap.get("meta", {})
    ss = meta.get("sourceStatus")
    if isinstance(ss, dict) and ss:
        return ss

    # Legado (schema v1): deriva o contrato de sources booleanos + warnings.
    out = {}
    sources = meta.get("sources") or {}
    warnings = snap.get("warnings") or []
    for key, sid, nome in _SOURCES_DEF:
        ok = bool(sources.get(key)) and isinstance(snap.get(key), dict)
        err = None
        if not ok:
            if meta.get("configured") is not True:
                err = "NOT_CONFIGURED"
            else:
                relevantes = [w for w in warnings if nome.lower() in str(w).lower()]
                err = fc.classificar_erro(" ".join(relevantes or [str(w) for w in warnings])) \
                    if (relevantes or warnings) else "SYNC_FAILED"
        out[key] = fc.status_fonte(
            sid, nome,
            last_success_at=meta.get("generatedAt") if ok else None,
            last_attempt_at=meta.get("generatedAt"),
            source_data_through=meta.get("periodEnd") if ok else None,
            error_code=err,
        )
    return out


def _fontes_configuradas(cfg):
    """Quais fontes têm configuração local (independentes uma da outra)."""
    if cfg is None:
        return {key: False for key, _, _ in _SOURCES_DEF}
    return {
        "searchConsole": bool(str(cfg.get("searchConsoleSiteUrl", "")).strip()),
        "ga4": bool(str(cfg.get("ga4PropertyId", "")).strip()),
    }


def _avaliar_snapshot(snap, max_age_hours=None, agora=None):
    """Avalia frescor por fonte + estado agregado. Relógio injetável (testes)."""
    agora = agora or fc.agora_utc()
    if snap is None:
        return {"overall": fc.UNKNOWN, "exit": fc.EXIT_UNKNOWN, "fontes": {},
                "staleAfterHours": max_age_hours or DEFAULT_STALE_HOURS}

    meta = snap.get("meta", {})
    stale_h = max_age_hours or meta.get("staleAfterHours") or DEFAULT_STALE_HOURS
    status = _source_status_do_snapshot(snap)

    fontes = {}
    estados = []
    for key, _sid, _nome in _SOURCES_DEF:
        bloco = status.get(key)
        if bloco is None:
            continue
        aval = fc.avaliar_frescor(bloco, stale_h, agora)
        fontes[key] = {**bloco, **aval}
        # Fonte deliberadamente não configurada não rebaixa o agregado quando
        # existe outra fonte configurada.
        if bloco.get("errorCode") != "NOT_CONFIGURED":
            estados.append(aval["freshnessStatus"])

    if not estados:
        overall = fc.UNAVAILABLE if fontes else fc.UNKNOWN
    else:
        overall = fc.pior_estado(estados)
    return {"overall": overall, "exit": fc.exit_code_para(overall),
            "fontes": fontes, "staleAfterHours": stale_h}


def _warnings_seguros(raw_warnings, nome_fonte):
    """Converte avisos técnicos crus em mensagens fixas do catálogo (dedup)."""
    vistos = []
    for w in raw_warnings:
        msg = f"{nome_fonte}: {fc.mensagem_segura(fc.classificar_erro(w))}"
        if msg not in vistos:
            vistos.append(msg)
    return vistos


def _montar_snapshot(cfg, prev, resultados, periodo, agora_iso, stale_h,
                     cred_kind=CRED_NONE):
    """Monta o snapshot v2 preservando a última versão válida por fonte.

    resultados: {key: {"ok": bool, "data": dict, "raw_warnings": [...],
                       "error_code": str|None}}
    """
    prev_status = _source_status_do_snapshot(prev)
    configuradas = _fontes_configuradas(cfg)

    meta = {
        "configured": cfg is not None,
        "schemaVersion": 2,
        "generatedAt": agora_iso,
        "lastAttemptAt": agora_iso,
        # Diagnóstico de credencial (M21): só o TIPO, nunca e-mail/chave/projeto.
        "credentialKind": cred_kind,
        "staleAfterHours": stale_h,
        "periodStart": periodo[0],
        "periodEnd": periodo[1],
        "lookbackDays": periodo[2],
        "sources": {},
        "sourceStatus": {},
        "safeToDisplay": True,
        "containsPersonalData": False,
    }
    payload = {"meta": meta, "warnings": []}

    for key, sid, nome in _SOURCES_DEF:
        res = resultados.get(key) or {"ok": False, "data": {}, "raw_warnings": [],
                                      "error_code": "NOT_CONFIGURED"}
        avisos = _warnings_seguros(res.get("raw_warnings", []), nome)
        meta["sources"][key] = bool(res["ok"])

        if res["ok"]:
            meta["sourceStatus"][key] = fc.status_fonte(
                sid, nome,
                last_success_at=agora_iso, last_attempt_at=agora_iso,
                source_data_through=periodo[1], error_code=None,
                warnings=avisos,
            )
            payload[key] = res["data"]
        else:
            err = res.get("error_code") or "SYNC_FAILED"
            if not configuradas.get(key):
                err = "NOT_CONFIGURED"
            # M21 — com credencial durável, 403/permissão significa "acesso
            # ainda não concedido à conta de serviço", não "login vencido".
            # Assim "Reautenticação necessária" deixa de aparecer em operação
            # normal, como o contrato desta etapa exige.
            elif (cred_kind == CRED_SERVICE_ACCOUNT
                  and err in ("PERMISSION_DENIED", "AUTH_REQUIRED")):
                err = "CREDENTIAL_PENDING"
            pblock = prev_status.get(key) or {}
            meta["sourceStatus"][key] = fc.status_fonte(
                sid, nome,
                last_success_at=pblock.get("lastSuccessAt"),
                last_attempt_at=agora_iso,
                source_data_through=pblock.get("sourceDataThrough"),
                error_code=err, warnings=avisos,
            )
            # Preserva a última versão válida — falha nunca apaga snapshot.
            if prev and isinstance(prev.get(key), dict):
                payload[key] = prev[key]
        payload["warnings"].extend(avisos)

    return payload


def _imprimir_status(aval, out_path):
    print(f"Snapshot: {out_path}")
    print(f"Limite de frescor: {aval['staleAfterHours']}h")
    for key, sid, nome in _SOURCES_DEF:
        f = aval["fontes"].get(key)
        if f is None:
            print(f"  {nome}: (sem registro)")
            continue
        idade = f.get("ageSeconds")
        idade_txt = f"{idade / 3600:.1f}h" if idade is not None else "—"
        detalhe = f.get("errorMessageSafe") or ""
        print(f"  {nome}: {f['freshnessStatus']}"
              f" — última sincronização OK: {f.get('lastSuccessAt') or 'nunca'}"
              f" (idade {idade_txt})"
              f" — dados até: {f.get('sourceDataThrough') or '—'}"
              f"{' — ' + detalhe if detalhe else ''}")
    print(f"Estado agregado: {aval['overall']} (exit {aval['exit']})")


# ───────────────────────── Escrita segura (atômica) ─────────────────────────

def _scan_for_secrets(output):
    text = json.dumps(output, ensure_ascii=False).lower()
    for pattern in FORBIDDEN_OUTPUT:
        if pattern in text:
            return pattern
    return None


def _write_output(output, out_public=None):
    found = _scan_for_secrets(output)
    if found:
        print(f"ERRO CRÍTICO: padrão proibido '{found}' detectado no JSON de saída.")
        print("  Abortando gravação para proteger dados sensíveis.")
        sys.exit(1)

    # 2ª validação: guarda de PII compartilhada (M2) — aborta com exit 1 se
    # encontrar violação; nunca imprime o valor sensível.
    pii_guard.ensure_summary_safe(output, rules=_PII_RULES, context="marketing-seo")

    destino_publico = Path(out_public) if out_public else _OUT_PUBLIC

    # Escrita atômica com validação de contrato: arquivo inválido nunca
    # substitui snapshot válido (freshness-contract.json, regra 3/4).
    # Com --output (testes/fixtures), NÃO toca no arquivo privado real.
    if destino_publico == _OUT_PUBLIC:
        fc.escrever_json_atomico(_OUT_PRIVATE, output,
                                 validador=fc.validar_snapshot_marketing)
        print(f"Gravado em: {_OUT_PRIVATE}")

    fc.escrever_json_atomico(destino_publico, output,
                             validador=fc.validar_snapshot_marketing)
    print(f"Sincronizado em: {destino_publico}")
    print()
    print("Execute a seguir para verificar segurança:")
    print("  bash painel-soprolife/scripts/check-access.sh")


# ───────────────────────── Modos de execução ────────────────────────────────

def cmd_status(args, modo):
    """--status / --check: avaliação offline do snapshot (sem rede)."""
    out_path = Path(args.output) if args.output else _OUT_PUBLIC
    snap = _carregar_snapshot(out_path)

    if snap is not None and modo == "check":
        try:
            fc.validar_snapshot_marketing(snap)
        except ValueError as exc:
            print(f"SCHEMA INVÁLIDO: {exc}")
            return fc.EXIT_SCHEMA_INVALID

    aval = _avaliar_snapshot(snap, max_age_hours=args.max_age_hours)
    if snap is None:
        print(f"Snapshot inexistente ou ilegível: {out_path}")
        print("Estado: unknown — nunca sincronizado neste ambiente.")
        return fc.EXIT_UNKNOWN

    _imprimir_status(aval, out_path)
    return aval["exit"]


def _com_retentativa(nome_fonte, consulta, dormir=None):
    """Executa `consulta()` com backoff limitado. Devolve (dados, warnings).

    Só retenta o que faz sentido retentar: falha de credencial, permissão ou
    configuração é definitiva nesta execução. `dormir` é injetável para teste.
    """
    dormir = dormir or time.sleep
    dados, warnings = {}, []
    for tentativa in range(1, RETRY_MAX_TENTATIVAS + 1):
        dados, warnings = consulta()
        if dados:
            if tentativa > 1:
                print(f"  {nome_fonte}: sucesso na tentativa {tentativa}.")
            return dados, warnings
        codigo = fc.classificar_erro(" ".join(str(w) for w in warnings))
        if codigo in RETRY_NAO_RETENTAR or tentativa == RETRY_MAX_TENTATIVAS:
            return dados, warnings
        espera = RETRY_BACKOFF_SEGUNDOS[min(tentativa - 1,
                                            len(RETRY_BACKOFF_SEGUNDOS) - 1)]
        print(f"  {nome_fonte}: falha transitória — nova tentativa em {espera}s "
              f"({tentativa}/{RETRY_MAX_TENTATIVAS}).")
        dormir(espera)
    return dados, warnings


def cmd_credential_check():
    """Diagnóstico offline da credencial: existe? é conta de serviço? é exigida?

    Não faz nenhuma chamada de rede e não imprime caminho completo, e-mail,
    projeto nem qualquer parte da chave privada.
    """
    print("SoproLife — diagnóstico de credencial de Marketing (offline)")
    sa_path = _service_account_path()
    exige = _requer_service_account()
    print(f"conta de serviço obrigatória: {'sim' if exige else 'não (dev)'}")
    if sa_path is None:
        print("arquivo de credencial: ausente")
        if exige:
            print("estado: credential_pending — configure a conta de serviço "
                  "de leitura antes de esperar dados novos.")
            return fc.EXIT_CREDENTIAL_PENDING
        print("estado: usará ADC pessoal do gcloud (apenas desenvolvimento).")
        return fc.EXIT_FRESH
    tipo = _classificar_arquivo_credencial(sa_path)
    print(f"arquivo de credencial: presente (tipo {tipo or 'ilegível'})")
    if tipo != "service_account":
        print("estado: credential_pending — o arquivo não é uma conta de serviço.")
        return fc.EXIT_CREDENTIAL_PENDING
    try:
        modo = oct(sa_path.stat().st_mode & 0o777)
    except OSError:
        modo = "?"
    print(f"permissão do arquivo: {modo} (não deve haver acesso 'other')")
    if not _credencial_tem_permissao_restrita(sa_path):
        print("estado: credential_pending — restrinja o arquivo para 0640 ou 0600.")
        return fc.EXIT_CREDENTIAL_PENDING
    print("estado: conta de serviço de leitura configurada.")
    return fc.EXIT_FRESH


def cmd_sync(args, mode):
    """--dry-run / --write: consulta as fontes e (no write) grava o snapshot."""
    print("SoproLife OS Local Core — Marketing & SEO connector (leitura)")
    print(f"mode: {mode}")
    print()

    # Tipo de credencial resolvido; preenchido antes de qualquer chamada e
    # gravado no snapshot como diagnóstico (sem identidade nem chave).
    ctx = {"credencial": CRED_NONE}

    out_path = Path(args.output) if args.output else _OUT_PUBLIC
    prev = _carregar_snapshot(out_path)
    if prev is None and out_path == _OUT_PUBLIC:
        prev = _carregar_snapshot(_OUT_PRIVATE)
    cfg = _load_config()
    agora_iso = _now_iso()

    stale_h = args.max_age_hours or DEFAULT_STALE_HOURS
    if cfg and cfg.get("staleAfterHours"):
        stale_h = args.max_age_hours or float(cfg["staleAfterHours"])

    def _finalizar(resultados, periodo):
        payload = _montar_snapshot(cfg, prev, resultados, periodo, agora_iso,
                                   stale_h, ctx["credencial"])
        aval = _avaliar_snapshot(payload, max_age_hours=args.max_age_hours)
        print()
        if mode == "write":
            _write_output(payload, out_public=out_path)
        else:
            print("next_step: use --write para gravar os dados.")
        _imprimir_status(aval, out_path)
        return aval["exit"]

    periodo_vazio = (None, None, None)

    if cfg is None:
        print("INFO: configuração não encontrada.")
        print(f"  Esperado em: {_CONFIG_PATH}")
        print("  Copie painel-soprolife/config-examples/marketing-seo.local.example.json")
        print("  para esse caminho e preencha os valores reais.")
        if prev is not None:
            print("  Snapshot anterior será preservado.")
        return _finalizar({}, periodo_vazio)

    configuradas = _fontes_configuradas(cfg)
    if not any(configuradas.values()):
        print("AVISO: ga4PropertyId e searchConsoleSiteUrl estão vazios — nada a consultar.")
        return _finalizar({}, periodo_vazio)

    lookback  = max(1, int(cfg.get("lookbackDays", 28)))
    top_limit = max(1, int(cfg.get("topLimit", 20)))
    today      = date.today()
    end_date   = (today - timedelta(days=1)).isoformat()
    start_date = (today - timedelta(days=lookback)).isoformat()
    periodo = (start_date, end_date, lookback)

    print(f"Período: {start_date} → {end_date} ({lookback} dias)")
    print()

    def _falha_total(error_code, raw=None):
        resultados = {}
        for key, _sid, _nome in _SOURCES_DEF:
            if configuradas.get(key):
                resultados[key] = {"ok": False, "data": {},
                                   "raw_warnings": [raw] if raw else [],
                                   "error_code": error_code}
        return _finalizar(resultados, periodo)

    if args.no_network:
        print("INFO: --no-network — nenhuma chamada externa será feita.")
        return _falha_total("NETWORK_BLOCKED")

    build, google_auth_default = _load_google_libs()
    if build is None:
        return _falha_total("DEPENDENCY_MISSING")

    scopes = []
    if configuradas["searchConsole"]:
        scopes.append(SC_SCOPE)
    if configuradas["ga4"]:
        scopes.append(GA4_SCOPE)

    credentials, cred_kind, cred_erro = resolver_credencial(scopes, google_auth_default)
    ctx["credencial"] = cred_kind
    print(f"credencial: {cred_kind}"
          + (" (leitura, durável)" if cred_kind == CRED_SERVICE_ACCOUNT else ""))
    if credentials is None:
        if cred_erro == "CREDENTIAL_PENDING":
            print("AVISO: sem credencial de serviço utilizável.")
            print("  Esperado: conta de serviço dedicada, somente leitura, em")
            print(f"  {_SA_DEFAULT_PATH} (ou {_ENV_SA_PATH}).")
            print("  Verifique criação/instalação da identidade e as concessões "
                  "de leitura nas propriedades.")
        else:
            print("AVISO: falha ao obter credencial de leitura.")
            print("  Escopos necessários (somente leitura):")
            for s in scopes:
                print(f"    {s}")
        return _falha_total(cred_erro or "AUTH_REQUIRED")

    resultados = {}

    if configuradas["searchConsole"]:
        print("Consultando Search Console...")
        sc_data, sc_warn = _com_retentativa(
            "Search Console",
            lambda: _fetch_search_console(
                build, credentials, str(cfg.get("searchConsoleSiteUrl", "")).strip(),
                start_date, end_date, top_limit
            ),
        )
        ok = bool(sc_data)
        resultados["searchConsole"] = {
            "ok": ok, "data": sc_data, "raw_warnings": sc_warn,
            "error_code": None if ok else fc.classificar_erro(" ".join(sc_warn)),
        }
        if sc_warn:
            print(f"  AVISO: {len(sc_warn)} problema(s) na consulta — detalhes seguros no snapshot.")
        if ok:
            totals = sc_data.get("totals", {})
            print(f"  OK — impressões: {totals.get('impressions', 0)}, "
                  f"cliques: {totals.get('clicks', 0)}")
        print()

    if configuradas["ga4"]:
        print("Consultando GA4...")
        ga4_data, ga4_warn = _com_retentativa(
            "GA4",
            lambda: _fetch_ga4(
                credentials, str(cfg.get("ga4PropertyId", "")).strip(),
                start_date, end_date, top_limit
            ),
        )
        ok = bool(ga4_data)
        resultados["ga4"] = {
            "ok": ok, "data": ga4_data, "raw_warnings": ga4_warn,
            "error_code": None if ok else fc.classificar_erro(" ".join(ga4_warn)),
        }
        if ga4_warn:
            print(f"  AVISO: {len(ga4_warn)} problema(s) na consulta — detalhes seguros no snapshot.")
        if ok:
            totals = ga4_data.get("totals", {})
            print(f"  OK — usuários: {totals.get('users', 0)}, "
                  f"sessões: {totals.get('sessions', 0)}")
        print()

    print("Validação concluída. Nenhum dado pessoal exportado.")
    for key, _sid, nome in _SOURCES_DEF:
        if key in resultados:
            print(f"  {nome}: {'OK' if resultados[key]['ok'] else 'falhou (snapshot preservado)'}")

    return _finalizar(resultados, periodo)


def main():
    parser = argparse.ArgumentParser(
        description="Marketing & SEO connector ADC — SoproLife (sem chave privada)"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="Consulta sem gravar (padrão)")
    group.add_argument("--write",   action="store_true", help="Grava snapshot v2")
    group.add_argument("--status",  action="store_true",
                       help="Mostra frescor do snapshot local (sem rede)")
    group.add_argument("--check",   action="store_true",
                       help="Valida contrato + frescor (sem rede; exit codes)")
    group.add_argument("--credential-check", action="store_true",
                       help="Diagnostica a credencial de leitura (sem rede)")
    parser.add_argument("--max-age-hours", type=float, default=None,
                        help=f"Limite de frescor em horas (padrão {DEFAULT_STALE_HOURS})")
    parser.add_argument("--output", default=None,
                        help="Caminho alternativo do snapshot (testes)")
    parser.add_argument("--no-network", action="store_true",
                        help="Proíbe chamadas externas (modos de teste)")
    args = parser.parse_args()

    if args.credential_check:
        return cmd_credential_check()
    if args.status:
        return cmd_status(args, "status")
    if args.check:
        return cmd_status(args, "check")
    return cmd_sync(args, "write" if args.write else "dry-run")


if __name__ == "__main__":
    sys.exit(main())
