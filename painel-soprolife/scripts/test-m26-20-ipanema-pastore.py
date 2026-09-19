#!/usr/bin/env python3
"""
SoproLife — M26.20: bloco "Pastore Ipanema" em Marketing & SEO.

Cobre o contrato do bloco que mede a demanda que a SoproLife gera para a
unidade Pastore Ipanema, nos dois lados:

  - Python: a agregação pura (montar_bloco_pastore_ipanema) e a leitura
    filtrada do GA4 (_fetch_ga4_pastore_ipanema) com cliente simulado;
  - Estático: index.html / app.js / style.css realmente renderizam o bloco e
    não escondem número demonstrativo dentro dele.

O ponto sensível deste contrato é a diferença entre TRÊS coisas que um painel
desonesto costuma confundir:
  ausência de dado (N/D)  ≠  zero real (0)  ≠  divisão indefinida (N/D, não 0%).

100% offline: sem rede, sem credencial, sem data-private, sem VPS.

Uso:  python3 painel-soprolife/scripts/test-m26-20-ipanema-pastore.py
Exit: 0 = todos os casos passaram | 1 = houve falha.
"""

import importlib.util
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent          # painel-soprolife/
SCRIPTS = RAIZ / "scripts"
CONECTOR = SCRIPTS / "read-marketing-seo-adc.py"
INDEX_HTML = RAIZ / "index.html"
APP_JS = RAIZ / "js" / "app.js"
STYLE_CSS = RAIZ / "css" / "style.css"

FALHAS = 0


def caso(nome, cond, detalhe=""):
    global FALHAS
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS += 1
        print(f"  FAIL: {nome}{' — ' + detalhe if detalhe else ''}")


def carregar_conector():
    """Importa o conector (nome com hífen) sem executá-lo como programa."""
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("read_marketing_seo_adc", CONECTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mkt = carregar_conector()


# ── Stubs dos tipos do GA4 ──────────────────────────────────────────────────
# Reproduzem só a FORMA que o conector usa (construtores nomeados e enum de
# match). Nenhuma chamada de rede acontece aqui.

class _StringFilter:
    class MatchType:
        BEGINS_WITH = "BEGINS_WITH"

    def __init__(self, match_type=None, value=None, case_sensitive=None):
        self.match_type = match_type
        self.value = value
        self.case_sensitive = case_sensitive


class _InListFilter:
    def __init__(self, values=None):
        self.values = values


class _Filter:
    StringFilter = _StringFilter
    InListFilter = _InListFilter

    def __init__(self, field_name=None, string_filter=None, in_list_filter=None):
        self.field_name = field_name
        self.string_filter = string_filter
        self.in_list_filter = in_list_filter


class _FilterExpression:
    def __init__(self, filter=None):  # noqa: A002 — nome do SDK
        self.filter = filter


TIPOS_STUB = {
    "Filter": _Filter,
    "FilterExpression": _FilterExpression,
}


class _Val:
    def __init__(self, value):
        self.value = str(value)


class _Row:
    def __init__(self, dims, metrics):
        self.dimension_values = [_Val(d) for d in dims]
        self.metric_values = [_Val(m) for m in metrics]


class _Resp:
    def __init__(self, rows):
        self.rows = rows


def fake_query(respostas):
    """ga4_query simulado: devolve resposta por dimensão consultada.

    `respostas` mapeia a 1ª dimensão da consulta para _Resp ou None (None =
    a consulta falhou, exatamente como o conector real sinaliza).
    """
    chamadas = []

    def _q(dimensions, metrics, limit=None, dimension_filter=None):
        chamadas.append({
            "dimensions": list(dimensions), "metrics": list(metrics),
            "limit": limit, "dimension_filter": dimension_filter,
        })
        return respostas.get(dimensions[0])

    return _q, chamadas


# ── 1. Constantes espelham a instrumentação real do site ────────────────────
print("── Constantes do bloco ──")
caso("prefixo da landing é /espirometria-ipanema",
     mkt.IPANEMA_PAGE_PATH_PREFIX == "/espirometria-ipanema",
     mkt.IPANEMA_PAGE_PATH_PREFIX)
caso("os 3 eventos são exatamente os instrumentados no site",
     mkt.IPANEMA_EVENTS == ("click_agendar_pastore", "click_whatsapp_ipanema",
                            "click_rota_pastore_ipanema"),
     str(mkt.IPANEMA_EVENTS))
caso("UTM de saída documentada (soprolife/referral/espirometria_ipanema)",
     mkt.IPANEMA_OUTBOUND_UTM == {"source": "soprolife", "medium": "referral",
                                  "campaign": "espirometria_ipanema"})

# ── 2. Agregação pura ───────────────────────────────────────────────────────
print("── Agregação: fórmula da conversão ──")
b = mkt.montar_bloco_pastore_ipanema(
    {"pageviews": 200, "users": 150, "sessions": 180},
    {"click_agendar_pastore": {"count": 10, "users": 9},
     "click_whatsapp_ipanema": {"count": 6, "users": 6},
     "click_rota_pastore_ipanema": {"count": 4, "users": 4}},
)
caso("10 cliques / 200 visitas = 5.0%", b["conversion"]["rate"] == 5.0,
     str(b["conversion"]["rate"]))
caso("numerador é eventCount de click_agendar_pastore",
     b["conversion"]["numerator"] == 10
     and b["conversion"]["numeratorMetric"] == "eventCount"
     and b["conversion"]["numeratorEvent"] == "click_agendar_pastore")
caso("denominador é screenPageViews (não usuários, não sessões)",
     b["conversion"]["denominator"] == 200
     and b["conversion"]["denominatorMetric"] == "screenPageViews")
caso("fórmula fica gravada no snapshot (auditável)",
     "eventCount" in b["conversion"]["formula"]
     and "screenPageViews" in b["conversion"]["formula"])

print("── Agregação: interações de intenção ──")
caso("soma dos 3 eventos = 20", b["intentInteractions"]["interactions"] == 20,
     str(b["intentInteractions"]["interactions"]))
caso("declarado como eventCount, não como pessoas",
     b["intentInteractions"]["metric"] == "eventCount")
caso("aviso explícito de que é interação, não pessoa única",
     "não" in b["intentInteractions"]["note"].lower()
     and "pessoas" in b["intentInteractions"]["note"].lower(),
     b["intentInteractions"]["note"])

print("── Agregação: zero real ≠ ausência de dado ──")
b_zero = mkt.montar_bloco_pastore_ipanema(
    {"pageviews": 40, "users": 30, "sessions": 35},
    {"click_whatsapp_ipanema": {"count": 3, "users": 3}},
)
def ev(bloco, nome):
    """Lê um evento da LISTA events (formato ga4.events)."""
    for linha in bloco["events"] or []:
        if linha["event"] == nome:
            return linha
    return None


caso("events é lista {event,count,users}, como ga4.events",
     isinstance(b["events"], list)
     and [e["event"] for e in b["events"]] == list(mkt.IPANEMA_EVENTS))
caso("evento sem linha numa consulta OK vira 0, não N/D",
     ev(b_zero, "click_agendar_pastore")["count"] == 0
     and ev(b_zero, "click_rota_pastore_ipanema")["count"] == 0)
caso("evento presente mantém o valor lido",
     ev(b_zero, "click_whatsapp_ipanema")["count"] == 3)
caso("conversão com 0 agendamentos é 0.0%, não N/D",
     b_zero["conversion"]["rate"] == 0.0, str(b_zero["conversion"]["rate"]))

print("── Agregação: ausência de dado vira None (painel mostra N/D) ──")
b_sem_ev = mkt.montar_bloco_pastore_ipanema({"pageviews": 40, "users": 30, "sessions": 35}, None)
caso("consulta de eventos falhou → events None",
     b_sem_ev["events"] is None)
caso("sem eventos não há interações de intenção",
     b_sem_ev["intentInteractions"] is None)
caso("sem eventos não há conversão (nunca 0% por omissão)",
     b_sem_ev["conversion"] is None)
caso("página continua disponível mesmo sem eventos",
     b_sem_ev["page"] == {"pageviews": 40, "users": 30, "sessions": 35})

b_sem_pag = mkt.montar_bloco_pastore_ipanema(
    None, {"click_agendar_pastore": {"count": 5, "users": 5}})
caso("consulta de página falhou → page None", b_sem_pag["page"] is None)
caso("sem página não há conversão", b_sem_pag["conversion"] is None)
caso("eventos continuam disponíveis mesmo sem página",
     ev(b_sem_pag, "click_agendar_pastore")["count"] == 5)

print("── Agregação: divisão por zero é indefinida, não 0% ──")
b_div0 = mkt.montar_bloco_pastore_ipanema(
    {"pageviews": 0, "users": 0, "sessions": 0},
    {"click_agendar_pastore": {"count": 0, "users": 0}},
)
caso("0 visitas → rate None (N/D no painel)", b_div0["conversion"]["rate"] is None)
caso("denominador zero fica visível para auditoria",
     b_div0["conversion"]["denominator"] == 0)

# ── 3. Leitura filtrada do GA4 (cliente simulado) ───────────────────────────
print("── Consulta GA4: filtros e agregação de linhas ──")
q, chamadas = fake_query({
    "pagePath": _Resp([
        _Row(["/espirometria-ipanema/"], [120, 90, 100]),
        _Row(["/espirometria-ipanema"], [30, 25, 28]),
    ]),
    "eventName": _Resp([
        _Row(["click_agendar_pastore"], [12, 11]),
        _Row(["click_whatsapp_ipanema"], [8, 7]),
    ]),
})
bloco = mkt._fetch_ga4_pastore_ipanema(q, TIPOS_STUB)
caso("pageviews somam as linhas do prefixo (120 + 30)",
     bloco["page"]["pageviews"] == 150, str(bloco["page"]["pageviews"]))
caso("sessions somam as linhas (100 + 28)",
     bloco["page"]["sessions"] == 128, str(bloco["page"]["sessions"]))
caso("usuários NÃO são somados entre linhas (máximo, não 90+25)",
     bloco["page"]["users"] == 90, str(bloco["page"]["users"]))
caso("evento sem linha vira 0 (rota não veio na resposta)",
     ev(bloco, "click_rota_pastore_ipanema")["count"] == 0)
caso("totalUsers do evento é lido separado do eventCount",
     ev(bloco, "click_agendar_pastore") == {"event": "click_agendar_pastore",
                                            "count": 12, "users": 11})
caso("conversão usa 12 / 150 = 8.0%", bloco["conversion"]["rate"] == 8.0,
     str(bloco["conversion"]["rate"]))

caso("consulta de página filtra por pagePath BEGINS_WITH o prefixo",
     chamadas[0]["dimension_filter"].filter.field_name == "pagePath"
     and chamadas[0]["dimension_filter"].filter.string_filter.value
         == mkt.IPANEMA_PAGE_PATH_PREFIX
     and chamadas[0]["dimension_filter"].filter.string_filter.match_type
         == "BEGINS_WITH")
caso("consulta de página pede screenPageViews/activeUsers/sessions",
     chamadas[0]["metrics"] == ["screenPageViews", "activeUsers", "sessions"])
caso("consulta de eventos filtra eventName pelos 3 eventos da parceria",
     chamadas[1]["dimension_filter"].filter.field_name == "eventName"
     and chamadas[1]["dimension_filter"].filter.in_list_filter.values
         == list(mkt.IPANEMA_EVENTS))
caso("consulta de eventos não depende de topLimit (não pode truncar)",
     chamadas[1]["limit"] == len(mkt.IPANEMA_EVENTS), str(chamadas[1]["limit"]))

print("── Consulta GA4: falha parcial preserva o que deu certo ──")
q_falha, _ = fake_query({
    "pagePath": None,   # consulta falhou
    "eventName": _Resp([_Row(["click_agendar_pastore"], [4, 4])]),
})
bloco_falha = mkt._fetch_ga4_pastore_ipanema(q_falha, TIPOS_STUB)
caso("página indisponível → None", bloco_falha["page"] is None)
caso("eventos lidos continuam no bloco",
     ev(bloco_falha, "click_agendar_pastore")["count"] == 4)
caso("conversão não é inventada a partir de meia leitura",
     bloco_falha["conversion"] is None)

# ── 4. O bloco passa pelas guardas de segurança do snapshot ─────────────────
print("── Guardas de segurança (PII + contrato de frescor) ──")
snapshot = {
    "meta": {
        "configured": True, "schemaVersion": 2,
        "generatedAt": "2026-09-19T12:00:00+00:00",
        "safeToDisplay": True, "containsPersonalData": False,
        "periodStart": "2026-08-22", "periodEnd": "2026-09-18", "lookbackDays": 28,
        "sources": {"searchConsole": False, "ga4": True},
    },
    "warnings": [],
    "ga4": {"pastoreIpanema": bloco},
}
try:
    mkt.pii_guard.ensure_summary_safe(snapshot, rules=mkt._PII_RULES,
                                      context="marketing-seo")
    caso("bloco Ipanema passa na guarda de PII", True)
except SystemExit as exc:
    caso("bloco Ipanema passa na guarda de PII", False, f"exit {exc.code}")
except Exception as exc:  # noqa: BLE001
    caso("bloco Ipanema passa na guarda de PII", False, str(exc))

try:
    mkt.fc.validar_snapshot_marketing(snapshot)
    caso("snapshot com o bloco passa no contrato de frescor", True)
except Exception as exc:  # noqa: BLE001
    caso("snapshot com o bloco passa no contrato de frescor", False, str(exc))

caso("nenhum padrão proibido no bloco", mkt._scan_for_secrets(snapshot) is None,
     str(mkt._scan_for_secrets(snapshot)))

# ── 5. Guardas estáticas do front ───────────────────────────────────────────
print("── Front: o bloco é realmente renderizado ──")
html = INDEX_HTML.read_text(encoding="utf-8")
js = APP_JS.read_text(encoding="utf-8")
css = STYLE_CSS.read_text(encoding="utf-8")

caso("index.html tem o painel com o título pedido",
     'id="mktIpanemaPanel"' in html
     and "Pastore Ipanema — tráfego gerado pela SoproLife" in html)
caso("painel nasce oculto (só aparece com snapshot real)",
     'id="mktIpanemaPanel" hidden' in html)
caso("index.html tem os contêineres de KPIs e da nota",
     'id="mktIpanemaKpis"' in html and 'id="mktIpanemaNote"' in html)
caso("painel reusa a faixa de KPIs padrão do Marketing",
     'id="mktIpanemaKpis" class="mkt-kpi-strip"' in html)

caso("app.js define renderMktIpanema", "function renderMktIpanema()" in js)
caso("renderMarketingSection chama renderMktIpanema no ramo real",
     "renderMktIpanema();" in js)
caso("renderer lê o bloco real do snapshot GA4",
     "state.marketingSeo?.ga4?.pastoreIpanema" in js)
caso("renderer usa o período do meta do snapshot (sem data fixa)",
     "meta?.periodStart" in js and "meta?.periodEnd" in js)
caso("ausência vira N/D", 'MKT_IPANEMA_ND = "N/D"' in js)
caso("renderer lê events como lista (find por e.event)",
     "e.event === nome" in js)
caso("nomes dos 3 eventos aparecem no renderer",
     all(ev in js for ev in mkt.IPANEMA_EVENTS))
caso("rótulo não promete exame realizado nem paciente convertido",
     "não exames concluídos nem pacientes convertidos" in js)
caso("nota deixa claro que a soma é de interações",
     "são interações, não pessoas únicas" in js)
caso("CSS tem o espaçamento do bloco e o estilo da nota",
     ".mkt-ipanema-panel .mkt-kpi-strip" in css and ".mkt-ipanema-note {" in css)
# A régua única do M17 desliga ::before de .mkt-kpi-card; uma listra só para
# Ipanema seria regra morta — e destoaria do resto da tela se um dia voltasse.
caso("bloco não cria listra própria (seguiria regra morta e destoaria)",
     ".kpi-ipanema::before" not in css)

# ── 6. Nenhum número fictício no caminho de produção ────────────────────────
print("── Sem dado mockado em produção ──")
trecho_ini = js.find("function renderMktIpanema()")
trecho = js[trecho_ini:js.find("function renderMktAlerts()", trecho_ini)]
caso("renderer não contém literal numérico de demonstração",
     not any(f": {n}," in trecho for n in ("12", "34", "42", "120", "150")),
     "há número fixo dentro de renderMktIpanema")
caso("demo esconde o painel em vez de preenchê-lo",
     "if (ipanemaPanel) ipanemaPanel.hidden = true;" in js)

print()
if FALHAS:
    print(f"RESULTADO: {FALHAS} falha(s).")
    sys.exit(1)
print("RESULTADO: todos os casos passaram.")
sys.exit(0)
