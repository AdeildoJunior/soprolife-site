"""M26.32 — a publicação mais recente do Instagram dentro do convite.

A pergunta desta milestone não é "o permalink entra na mensagem?". É o que
acontece nas onze vezes em que ele NÃO entra: sem credencial, com a Meta
fora do ar, com token vencido, com corpo estranho, com conta vazia. Em
todas, o botão da M26.31 tem de continuar fazendo exatamente o que fazia.

Nenhum teste toca a rede: a Graph API é substituída por `httpx.MockTransport`
e o relógio por um instante injetado. Pacientes "TESTE APAGAR", telefones no
prefixo local 0000 (não atribuído no Brasil, nunca discável).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import pathlib
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings, get_settings
from app.services import instagram_feed as feed
from app.services import patient_results as prs

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_JS = (PANEL_ROOT / "js" / "report-workflow.js").read_text()
WORKFLOW_CSS = (PANEL_ROOT / "css" / "report-workflow.css").read_text()
INDEX_HTML = (PANEL_ROOT / "index.html").read_text()

TOKEN_FALSO = "IGQVJTESTEAPAGARtoken-sintetico-que-nunca-existiu-0123456789"
POST = "https://www.instagram.com/p/CsYNTESTE01/"
REEL = "https://www.instagram.com/reel/DPxTESTE02/"
AGORA_UTC = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)


def _carregar(nome: str):
    caminho = pathlib.Path(__file__).with_name(nome)
    spec = importlib.util.spec_from_file_location(f"_{nome[:-3]}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_M26_4 = _carregar("test_m26_4_portal_resultados.py")
_montar_caso = _M26_4._montar_caso
_acesso = _M26_4._acesso


# ------------------------------------------------------------- ambiente


@pytest.fixture(autouse=True)
def ambiente(monkeypatch, tmp_path):
    """O mesmo arranjo da M26.31 — laudos em piloto e portal ligado."""

    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_REPORTS_VALIDATION_BASE_URL",
        "https://painel-teste.soprolife.local/validar",
    )
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "m26-32-painel-administrativo-secret-de-teste-0123456789"
    )
    monkeypatch.setenv("M15_PORTAL_ENABLED", "true")
    monkeypatch.setenv(
        "M15_PORTAL_TOKEN_KEY",
        "m26-32-chave-que-deriva-o-link-do-paciente-9876543210abcdef",
    )
    monkeypatch.setenv(
        "M15_PORTAL_SESSION_SECRET",
        "m26-32-segredo-do-cookie-do-portal-publico-fedcba9876543210",
    )
    monkeypatch.setenv(
        "M15_PORTAL_PUBLIC_BASE_URL", "https://soprolife.com.br/resultados"
    )
    get_settings.cache_clear()
    feed.reset_cache()
    yield
    get_settings.cache_clear()
    feed.reset_cache()


@pytest.fixture()
def caso(client, auth, db):
    return _montar_caso(
        client, auth, db, nome="TESTE APAGAR Paciente Publicacao", suffix="82"
    )


def _convite(client, auth, document_id, papel="operacional"):
    return client.get(
        f"/api/v1/laudos/{document_id}/convite-instagram", headers=auth(papel)
    )


def _ultima_fonte(db) -> str | None:
    """O `fonte_instagram` da trilha mais recente.

    O estado da integração saiu da resposta HTTP (ninguém na tela o usava).
    A auditoria continua sendo quem responde "o paciente recebeu a
    publicação do dia ou o perfil?" — e é lá que o teste vai buscar.
    """

    from app.models import AuditLog

    trilha = db.execute(
        select(AuditLog)
        .where(AuditLog.acao == "convite_instagram_exibido")
        .order_by(AuditLog.id.desc())
    ).scalars().first()
    fonte = (trilha.detalhes or {}).get("fonte_instagram")
    # Libera a transação de leitura: no SQLite dos testes, uma sessão com
    # SELECT em aberto bloqueia a escrita da requisição seguinte
    # ("database is locked") quando se lê a trilha ENTRE dois cliques.
    db.rollback()
    return fonte


def _settings(**extra) -> Settings:
    base = {
        "instagram_feed_enabled": True,
        "instagram_access_token": TOKEN_FALSO,
    }
    base.update(extra)
    return Settings(**base)


def _midia(*itens) -> dict:
    return {"data": list(itens), "paging": {"cursors": {}}}


def _transporte(responder):
    """`httpx.MockTransport` que também guarda o que foi enviado."""

    registro: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        registro.append(request)
        return responder(request)

    return httpx.MockTransport(_handler), registro


def _responde(corpo, status: int = 200):
    texto = corpo if isinstance(corpo, str) else json.dumps(corpo)
    return lambda _req: httpx.Response(
        status, content=texto.encode(), headers={"content-type": "application/json"}
    )


# ======================================================================
# 1–3. A publicação mais recente, em qualquer formato
# ======================================================================


def test_publicacao_mais_recente_entra_na_mensagem():
    transporte, enviados = _transporte(
        _responde(
            _midia(
                {"permalink": POST, "timestamp": "2026-10-13T12:00:00+0000",
                 "media_type": "IMAGE"},
            )
        )
    )
    pub = feed.ultima_publicacao(_settings(), transport=transporte, agora=1000.0)
    assert pub.permalink == POST
    assert pub.publicado_em == "2026-10-13T12:00:00+0000"
    assert pub.fonte == "meta"

    # A chamada foi à API oficial, no host oficial, com o token no CABEÇALHO.
    (pedido,) = enviados
    assert pedido.url.host == feed.ALLOWED_HOST
    assert pedido.url.path == f"/{feed.API_VERSION}/me/media"
    assert "permalink" in pedido.url.params["fields"]
    assert TOKEN_FALSO not in str(pedido.url)
    assert pedido.headers["authorization"] == f"Bearer {TOKEN_FALSO}"


def test_permalink_de_post_vale():
    transporte, _ = _transporte(
        _responde(_midia({"permalink": POST, "timestamp": "2026-10-10T09:00:00+0000"}))
    )
    assert feed.ultima_publicacao(_settings(), transport=transporte).permalink == POST


def test_permalink_de_reel_vale_igual():
    """Reel não é formato preferido nem preterido: é publicação."""

    transporte, _ = _transporte(
        _responde(_midia({"permalink": REEL, "timestamp": "2026-10-10T09:00:00+0000",
                          "media_type": "VIDEO"}))
    )
    assert feed.ultima_publicacao(_settings(), transport=transporte).permalink == REEL


def test_escolhe_por_data_e_nao_por_formato_nem_por_ordem():
    """O Reel do dia 13 ganha do carrossel do dia 14? Não — ganha o dia 14.

    A lista chega deliberadamente FORA de ordem: se o módulo confiasse na
    ordem da Meta, este teste pegaria o primeiro item.
    """

    carrossel = "https://www.instagram.com/p/CsYNTESTE03/"
    transporte, _ = _transporte(
        _responde(
            _midia(
                {"permalink": REEL, "timestamp": "2026-10-13T23:59:00+0000",
                 "media_type": "VIDEO"},
                {"permalink": carrossel, "timestamp": "2026-10-14T08:00:00+0000",
                 "media_type": "CAROUSEL_ALBUM"},
                {"permalink": POST, "timestamp": "2026-10-01T08:00:00+0000",
                 "media_type": "IMAGE"},
            )
        )
    )
    assert feed.ultima_publicacao(_settings(), transport=transporte).permalink == carrossel


# ======================================================================
# 4–7. Quando a Meta não ajuda
# ======================================================================


def test_meta_indisponivel_nao_vira_erro():
    def _cai(_req):
        raise httpx.ConnectError("sem rede")

    transporte, _ = _transporte(_cai)
    pub = feed.ultima_publicacao(_settings(), transport=transporte)
    assert pub.permalink is None
    assert pub.fonte == "indisponivel"


def test_timeout_nao_vira_erro():
    def _demora(_req):
        raise httpx.ReadTimeout("estourou")

    transporte, _ = _transporte(_demora)
    pub = feed.ultima_publicacao(_settings(), transport=transporte)
    assert pub.permalink is None
    assert pub.fonte == "indisponivel"


@pytest.mark.parametrize(
    "corpo,status",
    [
        ("isto não é json", 200),
        ({"data": "uma string"}, 200),
        ({"erro": "forma inesperada"}, 200),
        (_midia({"permalink": "https://exemplo.invalido/p/XYZ/"}), 200),
        (_midia({"permalink": "https://www.instagram.com/soprolife"}), 200),
        (_midia({"permalink": "javascript:alert(1)"}), 200),
        ({"error": {"message": "token"}}, 400),
        ({"error": {"message": "token"}}, 401),
        ({"error": {"message": "limite"}}, 429),
        ({"error": {"message": "meta"}}, 500),
    ],
)
def test_resposta_invalida_cai_para_o_perfil(corpo, status):
    transporte, _ = _transporte(_responde(corpo, status))
    assert feed.ultima_publicacao(_settings(), transport=transporte).permalink is None


def test_token_nao_configurado_nao_toca_a_rede():
    """Sem credencial não há chamada — nem uma, nem uma que falha."""

    def _nao_deveria(_req):  # pragma: no cover - o teste falha se rodar
        raise AssertionError("chamou a Meta sem credencial configurada")

    transporte, enviados = _transporte(_nao_deveria)
    for s in (
        Settings(),                                         # tudo desligado
        _settings(instagram_feed_enabled=False),            # token sem flag
        Settings(instagram_feed_enabled=True),              # flag sem token
        _settings(instagram_access_token="   "),            # token em branco
    ):
        pub = feed.ultima_publicacao(s, transport=transporte)
        assert pub.permalink is None
        assert pub.fonte == "desligado"
    assert enviados == []


def test_corpo_malformado_preserva_o_cache():
    """`data` com forma errada é falha de chamada, não conta vazia.

    A diferença importa: só uma lista VAZIA de verdade significa "não há
    mais publicação" e autoriza descartar o permalink guardado. Um corpo
    que ninguém entendeu não pode custar o último link bom que tínhamos.
    """

    bom, _ = _transporte(
        _responde(_midia({"permalink": POST, "timestamp": "2026-10-13T12:00:00+0000"}))
    )
    s = _settings()
    assert feed.ultima_publicacao(s, transport=bom, agora=0.0).fonte == "meta"

    ttl = s.instagram_feed_cache_minutes * 60
    corpos = ({"data": "uma string"}, {"erro": "sem data"}, {"data": {"a": 1}})
    for n, corpo in enumerate(corpos):
        # O relógio avança além da contenção a cada volta: sem isso, só a
        # primeira volta chamaria de verdade e as outras duas passariam de
        # graça pelo caminho do backoff.
        agora = ttl + 1 + n * (feed.BACKOFF_SEGUNDOS + 1)
        torto, enviados = _transporte(_responde(corpo))
        pub = feed.ultima_publicacao(s, transport=torto, agora=agora)
        assert len(enviados) == 1, corpo
        assert pub.permalink == POST, corpo
        assert pub.fonte == "cache_vencido", corpo
        assert feed.estado_do_cache()["tem_publicacao"] is True, corpo


def test_conta_sem_publicacao_descarta_o_cache():
    """Publicação apagada: o convite volta ao perfil em vez de link morto."""

    transporte, _ = _transporte(
        _responde(_midia({"permalink": POST, "timestamp": "2026-10-13T12:00:00+0000"}))
    )
    assert feed.ultima_publicacao(_settings(), transport=transporte, agora=0.0).fonte == "meta"

    vazio, _ = _transporte(_responde({"data": []}))
    pub = feed.ultima_publicacao(_settings(), transport=vazio, agora=10_000.0)
    assert pub.permalink is None
    assert pub.fonte == "sem_publicacao"
    assert feed.estado_do_cache() == {"tem_publicacao": False}


# ======================================================================
# 8–11. Cache e a ordem dos fallbacks
# ======================================================================


def test_cache_valido_nao_consulta_a_meta_de_novo():
    transporte, enviados = _transporte(
        _responde(_midia({"permalink": POST, "timestamp": "2026-10-13T12:00:00+0000"}))
    )
    s = _settings()
    primeira = feed.ultima_publicacao(s, transport=transporte, agora=0.0)
    assert primeira.fonte == "meta"

    ttl = s.instagram_feed_cache_minutes * 60
    for instante in (1.0, ttl / 2, ttl - 1):
        repetida = feed.ultima_publicacao(s, transport=transporte, agora=instante)
        assert repetida.permalink == POST
        assert repetida.fonte == "cache"
    assert len(enviados) == 1, "o cache não segurou os cliques seguintes"


def test_cache_expirado_consulta_de_novo_e_atualiza():
    novo = "https://www.instagram.com/reel/DPxTESTE09/"
    respostas = [
        _midia({"permalink": POST, "timestamp": "2026-10-13T12:00:00+0000"}),
        _midia({"permalink": novo, "timestamp": "2026-10-14T12:00:00+0000"}),
    ]

    def _responder(_req):
        corpo = respostas.pop(0) if respostas else respostas
        return httpx.Response(200, json=corpo)

    transporte, enviados = _transporte(_responder)
    s = _settings()
    ttl = s.instagram_feed_cache_minutes * 60
    assert feed.ultima_publicacao(s, transport=transporte, agora=0.0).permalink == POST
    depois = feed.ultima_publicacao(s, transport=transporte, agora=ttl + 1)
    assert depois.permalink == novo
    assert depois.fonte == "meta"
    assert len(enviados) == 2


def test_fallback_para_o_ultimo_post_conhecido():
    """Meta fora do ar com cache vencido: vale o permalink que já tínhamos."""

    bom, _ = _transporte(
        _responde(_midia({"permalink": POST, "timestamp": "2026-10-13T12:00:00+0000"}))
    )
    s = _settings()
    feed.ultima_publicacao(s, transport=bom, agora=0.0)

    def _cai(_req):
        raise httpx.ConnectError("sem rede")

    ruim, _ = _transporte(_cai)
    ttl = s.instagram_feed_cache_minutes * 60
    pub = feed.ultima_publicacao(s, transport=ruim, agora=ttl + 1)
    assert pub.permalink == POST
    assert pub.fonte == "cache_vencido"


def test_reserva_expira_em_sete_dias_sem_confirmacao():
    """A reserva vale enquanto houver confirmação recente — não para sempre.

    Cenário real: o token vence e ninguém renova. Sem teto, o convite
    serviria o mesmo permalink por meses, afirmando "nossa publicação mais
    recente" sobre algo que já não se consegue confirmar. Com teto, depois
    de 7 dias sem UMA consulta bem sucedida o convite volta ao perfil, que
    não tem como ficar desatualizado.
    """

    bom, _ = _transporte(
        _responde(_midia({"permalink": POST, "timestamp": "2026-10-13T12:00:00+0000"}))
    )
    s = _settings()
    assert feed.ultima_publicacao(s, transport=bom, agora=0.0).fonte == "meta"

    def _cai(_req):
        raise httpx.ConnectError("token vencido, sem resposta")

    teto = feed.MAX_CONTINGENCIA_SEGUNDOS
    # Dentro do teto, a reserva serve. Cada volta anda além da contenção,
    # senão só a primeira chamaria de verdade.
    for instante in (teto / 2, teto - feed.BACKOFF_SEGUNDOS - 2, teto):
        ruim, enviados = _transporte(_cai)
        pub = feed.ultima_publicacao(s, transport=ruim, agora=instante)
        assert len(enviados) == 1, instante
        assert pub.permalink == POST, instante
        assert pub.fonte == "cache_vencido", instante

    # Um segundo além do teto, acabou: perfil.
    ruim, _ = _transporte(_cai)
    pub = feed.ultima_publicacao(
        s, transport=ruim, agora=teto + feed.BACKOFF_SEGUNDOS + 2
    )
    assert pub.permalink is None
    assert pub.fonte == "indisponivel"


def test_o_teto_e_da_confirmacao_nao_da_idade_da_publicacao():
    """Publicação velha é legítima; confirmação velha é que não é.

    Uma conta que não posta há três meses tem, sim, uma "publicação mais
    recente" — e divulgá-la está certo. O que o teto mede é há quanto tempo
    NÓS não conseguimos falar com a Meta. Este teste existe para impedir que
    alguém, lendo a regra dos 7 dias, passe a descartar posts antigos.
    """

    antigo = "2026-01-02T08:00:00+0000"   # mais de 9 meses antes de hoje
    transporte, _ = _transporte(
        _responde(_midia({"permalink": POST, "timestamp": antigo}))
    )
    pub = feed.ultima_publicacao(_settings(), transport=transporte, agora=0.0)
    assert pub.permalink == POST
    assert pub.publicado_em == antigo
    assert pub.fonte == "meta"


def test_uma_confirmacao_bem_sucedida_reinicia_o_teto():
    """O prazo conta desde a última obtenção BEM-SUCEDIDA, não desde sempre."""

    s = _settings()
    teto = feed.MAX_CONTINGENCIA_SEGUNDOS
    bom = _responde(_midia({"permalink": POST, "timestamp": "2026-10-13T12:00:00+0000"}))

    t1, _ = _transporte(bom)
    assert feed.ultima_publicacao(s, transport=t1, agora=0.0).fonte == "meta"

    # Quase no limite, uma consulta dá certo: o relógio do teto zera.
    t2, _ = _transporte(bom)
    assert feed.ultima_publicacao(s, transport=t2, agora=teto - 10).fonte == "meta"

    def _cai(_req):
        raise httpx.ConnectError("sem rede")

    # Bem depois do que seriam "7 dias contados do início" — e além do TTL,
    # para que a resposta tenha de vir mesmo da reserva — ela ainda vale,
    # porque o prazo conta da confirmação de `teto - 10`.
    ttl = s.instagram_feed_cache_minutes * 60
    t3, enviados = _transporte(_cai)
    pub = feed.ultima_publicacao(s, transport=t3, agora=teto + ttl + 100)
    assert len(enviados) == 1
    assert pub.permalink == POST
    assert pub.fonte == "cache_vencido"


def test_item_sem_timestamp_nao_e_a_mais_recente():
    """Pedimos `timestamp` à API: sem ele não há prova de ser o mais novo.

    Um item sem data (ou com data ilegível) não concorre. Se restar outro
    com data, é ele; se não restar nenhum, é "não há publicação" e a ordem
    normal de fallback assume — nunca um chute.
    """

    sem_data = "https://www.instagram.com/p/CsYNTESTE77/"
    datado = "https://www.instagram.com/reel/DPxTESTE88/"

    # (a) o sem data vem primeiro e seria escolhido por ordem — não é.
    transporte, _ = _transporte(
        _responde(
            _midia(
                {"permalink": sem_data, "media_type": "IMAGE"},
                {"permalink": datado, "timestamp": "2026-10-01T08:00:00+0000"},
            )
        )
    )
    pub = feed.ultima_publicacao(_settings(), transport=transporte, agora=0.0)
    assert pub.permalink == datado
    assert pub.fonte == "meta"

    # (b) datas ilegíveis contam como ausentes.
    for ruim in ("", "   ", "ontem", "0000-99-99T99:99:99+0000", 20261014, None):
        feed.reset_cache()
        transporte, _ = _transporte(
            _responde(_midia({"permalink": sem_data, "timestamp": ruim}))
        )
        pub = feed.ultima_publicacao(_settings(), transport=transporte, agora=0.0)
        assert pub.permalink is None, ruim
        assert pub.fonte == "sem_publicacao", ruim


def test_o_relogio_do_cache_e_monotonico():
    """TTL e teto medem tempo com `time.monotonic`, não com hora de parede.

    Hora de parede anda para trás: ajuste de NTP, horário de verão, um
    `date -s` do operador. Qualquer um deles faria um cache vencido parecer
    novo — ou aposentaria um cache bom antes da hora. O relógio da entrada
    é monotônico; a hora de parede fica guardada à parte, só para o humano
    ler "confirmado às 14h22".
    """

    import inspect

    fonte = inspect.getsource(feed)
    # A fachada usa o monotônico como padrão.
    assert "agora = time.monotonic() if agora is None else agora" in fonte
    # E a hora de parede NÃO entra em conta de validade.
    for linha in fonte.splitlines():
        if "obtido_em" in linha and ("-" in linha or "<" in linha or ">" in linha):
            assert "now(" not in linha and "time.time" not in linha, linha

    transporte, _ = _transporte(
        _responde(_midia({"permalink": POST, "timestamp": "2026-10-13T12:00:00+0000"}))
    )
    antes = datetime.now(timezone.utc)
    pub = feed.ultima_publicacao(_settings(), transport=transporte)
    depois = datetime.now(timezone.utc)

    # O que o chamador recebe como "confirmado em" é hora de parede real...
    assert antes <= pub.confirmado_em <= depois
    assert pub.confirmado_em.tzinfo is not None
    # ...e o estado do cache a expõe em ISO, para diagnóstico.
    estado = feed.estado_do_cache()
    assert estado["confirmado_em"] == pub.confirmado_em.isoformat()


def test_timeout_padrao_e_curto():
    """3 s: a Meta é enriquecimento de UX, não dependência do fluxo.

    O operador está parado na frente da tela para mandar uma mensagem de
    relacionamento. A faixa 1–20 continua configurável para uma rede ruim,
    mas o padrão não pode custar 6 s de espera por um convite.
    """

    assert Settings().instagram_feed_timeout_seconds == 3.0
    assert _settings(instagram_feed_timeout_seconds=12.0)
    with pytest.raises(Exception):
        _settings(instagram_feed_timeout_seconds=0.5)
    with pytest.raises(Exception):
        _settings(instagram_feed_timeout_seconds=30.0)

    # E o valor configurado chega mesmo ao cliente HTTP.
    visto = {}

    class _Transporte(httpx.MockTransport):
        def handle_request(self, request):
            visto["timeout"] = request.extensions.get("timeout", {}).get("read")
            return httpx.Response(
                200,
                json=_midia(
                    {"permalink": POST, "timestamp": "2026-10-13T12:00:00+0000"}
                ),
            )

    feed.ultima_publicacao(
        _settings(), transport=_Transporte(lambda r: httpx.Response(200))
    )
    assert visto["timeout"] == 3.0


def test_falha_sem_cache_nao_vira_martelada_na_meta():
    chamadas = {"n": 0}

    def _cai(_req):
        chamadas["n"] += 1
        raise httpx.ConnectError("sem rede")

    transporte, _ = _transporte(_cai)
    s = _settings()
    for instante in (0.0, 1.0, 30.0, feed.BACKOFF_SEGUNDOS - 1):
        assert feed.ultima_publicacao(s, transport=transporte, agora=instante).fonte == "indisponivel"
    assert chamadas["n"] == 1
    # Passada a contenção, tenta de novo.
    feed.ultima_publicacao(s, transport=transporte, agora=feed.BACKOFF_SEGUNDOS + 1)
    assert chamadas["n"] == 2


def test_a_ordem_dos_tres_fallbacks_na_mensagem(client, auth, db, caso, monkeypatch):
    """O teste de ponta a ponta da ordem exigida: Meta → cache → perfil."""

    estados = iter(
        [
            feed.Publicacao(POST, "2026-10-14T12:00:00+0000", AGORA_UTC, "meta"),
            feed.Publicacao(POST, "2026-10-14T12:00:00+0000", AGORA_UTC, "cache_vencido"),
            feed.Publicacao(None, None, None, "indisponivel"),
        ]
    )
    monkeypatch.setattr(
        "app.routers.patient_results.instagram_feed.ultima_publicacao",
        lambda *a, **k: next(estados),
    )

    # O estado da fonte NÃO viaja na resposta: ele é lido na trilha.
    primeira = _convite(client, auth, caso["document_id"]).json()
    assert POST in primeira["mensagem"]
    assert "Veja nossa publicação mais recente:" in primeira["mensagem"]
    assert _ultima_fonte(db) == "meta"

    segunda = _convite(client, auth, caso["document_id"]).json()
    assert POST in segunda["mensagem"]
    assert _ultima_fonte(db) == "cache_vencido"

    terceira = _convite(client, auth, caso["document_id"]).json()
    assert "/p/" not in terceira["mensagem"]
    assert "/reel/" not in terceira["mensagem"]
    assert prs.INSTAGRAM_URL in terceira["mensagem"]
    assert _ultima_fonte(db) == "indisponivel"


# ======================================================================
# 12–15. A M26.31 continua inteira
# ======================================================================


def _com_publicacao(monkeypatch, permalink=POST):
    monkeypatch.setattr(
        "app.routers.patient_results.instagram_feed.ultima_publicacao",
        lambda *a, **k: feed.Publicacao(
            permalink, "2026-10-14T12:00:00+0000", AGORA_UTC, "meta"
        ),
    )


def _meta_proibida(monkeypatch) -> list:
    """Dublê que EXPLODE se a Meta for consultada.

    Usado nos casos de recusa: a prova de que o provider não é tocado não
    pode ser "o permalink não apareceu na mensagem" — isso também seria
    verdade se a chamada saísse e falhasse. Tem de ser "a função não foi
    chamada".
    """

    chamadas: list = []

    def _explode(*a, **k):  # pragma: no cover - o teste falha se rodar
        chamadas.append(1)
        raise AssertionError(
            "a Meta foi consultada para um convite já recusado"
        )

    monkeypatch.setattr(
        "app.routers.patient_results.instagram_feed.ultima_publicacao", _explode
    )
    return chamadas


def test_a_mensagem_com_publicacao_e_a_do_enunciado(client, auth, caso, monkeypatch):
    _com_publicacao(monkeypatch)
    corpo = _convite(client, auth, caso["document_id"]).json()
    assert corpo["mensagem"] == (
        "Olá, TESTE.\n\n"
        "Acompanhe a SoproLife diariamente no Instagram para receber "
        "informações, orientações e conteúdos sobre saúde respiratória.\n\n"
        "Veja nossa publicação mais recente:\n"
        f"{POST}\n\n"
        "Siga @soprolife para acompanhar as próximas publicações.\n\n"
        "SoproLife Diagnósticos e Soluções em Saúde"
    )
    # O permalink numa linha só: é a forma que favorece a prévia do
    # WhatsApp (quem decide renderizar é ele, não nós).
    assert f"\n{POST}\n" in corpo["mensagem"]
    # Uma URL só na mensagem — se houver prévia, que seja da publicação.
    assert corpo["mensagem"].count("https://") == 1


# ----------------------------------------------------------------------
# As recusas vêm ANTES da Meta
#
# Regra desta etapa: primeiro se decide se o paciente PODE ser contatado;
# só depois se procura o que mandar. Quem recusou não gera tráfego para um
# serviço de terceiro — e nenhum dado de paciente participa da consulta,
# que é sobre a CONTA da clínica. Manter esta ordem é o que impede que um
# dia alguém "personalize" o feed por paciente sem perceber.
# ----------------------------------------------------------------------


def test_telefone_ausente_nao_consulta_a_meta(client, auth, db, caso, monkeypatch):
    from app.models import PersonContact

    chamadas = _meta_proibida(monkeypatch)
    contato = db.execute(
        select(PersonContact).where(PersonContact.person_id == caso["pessoa"]["id"])
    ).scalars().first()
    contato.nao_discavel = True
    db.commit()

    corpo = _convite(client, auth, caso["document_id"]).json()
    assert corpo["whatsapp"]["disponivel"] is False
    assert corpo["whatsapp"]["motivo"] == "Telefone não cadastrado"
    assert chamadas == []
    # A mensagem continua pronta para copiar — a do perfil, porque sem
    # destino não se foi buscar publicação nenhuma.
    assert prs.INSTAGRAM_URL in corpo["mensagem"]
    assert _ultima_fonte(db) == "nao_consultado"


def test_opt_out_nao_consulta_a_meta(client, auth, db, caso, monkeypatch):
    from app.models import Person

    chamadas = _meta_proibida(monkeypatch)
    pessoa = db.get(Person, caso["pessoa"]["id"])
    pessoa.nao_contatar = True
    db.commit()

    corpo = _convite(client, auth, caso["document_id"]).json()
    assert corpo["whatsapp"] == {
        "disponivel": False,
        "motivo": "Paciente pediu para não ser contatado",
    }
    assert chamadas == []
    assert _ultima_fonte(db) == "nao_consultado"


def test_consentimento_revogado_nao_consulta_a_meta(
    client, auth, db, caso, monkeypatch
):
    from app.models import Consent

    chamadas = _meta_proibida(monkeypatch)
    db.add(
        Consent(
            person_id=caso["pessoa"]["id"],
            canal="whatsapp",
            status="revogado",
            origem="m26-32-teste",
        )
    )
    db.commit()

    corpo = _convite(client, auth, caso["document_id"]).json()
    assert corpo["whatsapp"] == {
        "disponivel": False,
        "motivo": "Consentimento de WhatsApp revogado",
    }
    assert chamadas == []
    assert _ultima_fonte(db) == "nao_consultado"


def test_paciente_arquivado_nao_consulta_a_meta(client, auth, db, caso, monkeypatch):
    """A recusa mais antiga de todas: cadastro arquivado nem chega à rota."""

    from app.models import Person

    chamadas = _meta_proibida(monkeypatch)
    pessoa = db.get(Person, caso["pessoa"]["id"])
    # O arquivamento exige evidência (CHECK `arquivamento_com_evidencia`):
    # a data e o motivo não são opcionais.
    pessoa.arquivado = True
    pessoa.arquivado_em = AGORA_UTC
    pessoa.arquivado_motivo = "TESTE APAGAR — cadastro sintético da M26.32"
    db.commit()

    resposta = _convite(client, auth, caso["document_id"])
    assert resposta.status_code == 409
    assert chamadas == []


def test_o_botao_anterior_continua_intacto(client, auth, db, caso, monkeypatch):
    """A publicação não muda nada do que a M26.31 prometeu não tocar."""

    _com_publicacao(monkeypatch)
    antes = _acesso(db, caso["document_id"])
    status, enviado, geracao = antes.status, antes.sent_at, antes.generation

    corpo = _convite(client, auth, caso["document_id"]).json()
    assert corpo["instagram_url"] == prs.INSTAGRAM_URL
    zap = corpo["whatsapp"]
    partes = urlsplit(zap["url"])
    assert partes.scheme == "https" and partes.netloc == "wa.me"
    assert parse_qs(partes.query)["text"][0] == corpo["mensagem"]

    depois = _acesso(db, caso["document_id"])
    assert (depois.status, depois.sent_at, depois.generation) == (
        status, enviado, geracao
    )

    # Sem laudo clínico, sem token, sem código interno — a regra da M26.31.
    texto = corpo["mensagem"]
    for termo in ("LAU-", "ESP-", "PES-", "CPF", "nascimento", "resultado", "laudo"):
        assert termo.lower() not in texto.lower(), termo

    # E a trilha registra a ORIGEM, não a credencial.
    from app.models import AuditLog

    trilha = db.execute(
        __import__("sqlalchemy").select(AuditLog)
        .where(AuditLog.acao == "convite_instagram_exibido")
        .order_by(AuditLog.id.desc())
    ).scalars().first()
    assert trilha.detalhes["fonte_instagram"] == "meta"
    assert TOKEN_FALSO not in json.dumps(trilha.detalhes)


# ======================================================================
# 16–18. Segredo, log e tela
# ======================================================================


def test_nenhum_segredo_chega_ao_frontend(client, auth, caso, monkeypatch):
    _com_publicacao(monkeypatch)
    corpo = _convite(client, auth, caso["document_id"]).json()
    # O diagnóstico interno saiu da resposta: a tela não o usava.
    assert "publicacao_fonte" not in corpo
    assert "publicacao_url" not in corpo
    assert set(corpo) == {"instagram_url", "mensagem", "whatsapp"}
    bruto = json.dumps(corpo, ensure_ascii=False)
    for proibido in (TOKEN_FALSO, "Bearer", "access_token", "graph.instagram.com"):
        assert proibido not in bruto, proibido

    # E o painel não conhece nem a API nem a credencial.
    for arquivo in (WORKFLOW_JS, WORKFLOW_CSS, INDEX_HTML):
        for proibido in (
            "graph.instagram.com", "access_token", "instagram_access_token",
            "M15_INSTAGRAM", "Bearer ", "instagram.com",
        ):
            assert proibido not in arquivo, proibido


def test_nenhum_token_nos_logs(monkeypatch):
    """Falha de rede, HTTP de erro e corpo ruim: nada disso imprime o token.

    O captador substitui `feed.logger` por um dublê que guarda o que o
    módulo MANDA registrar, já com a interpolação preguiçosa resolvida.
    Não é preciosismo: as duas tentativas anteriores mediram o nada.

      - `caplog` depende da propagação até a raiz e do estado global de
        `logging`, que o resto da suíte mexe;
      - um handler pendurado em `feed.logger` depende de o logger estar
        habilitado e no nível certo quando o teste roda.

    Nos dois casos o arquivo passava sozinho e, na suíte inteira, a lista
    vinha VAZIA — e `assert TOKEN not in ""` passa sempre. Um teste de
    segurança que passa sem medir nada é pior do que não ter o teste. O
    dublê mede a chamada, não o encanamento do `logging`, então não há
    configuração de processo que o faça passar por vacuidade.

    A contagem de tentativas no fim fecha a mesma porta pelo outro lado:
    se o provider parar de chamar a Meta (uma guarda nova, um curto-circuito
    de cache), o teste denuncia em vez de aprovar um silêncio.
    """

    registros: list[str] = []
    tentativas: list[httpx.Request] = []

    class _Duble:
        """Só o que este módulo usa. Qualquer outro nível vira erro."""

        def _guardar(self, msg, *args):
            registros.append(str(msg) % args if args else str(msg))

        warning = _guardar
        error = _guardar

    monkeypatch.setattr(feed, "logger", _Duble())

    casos = [
        lambda _r: (_ for _ in ()).throw(httpx.ConnectError("sem rede")),
        _responde({"error": {"message": "Session has expired"}}, 401),
        _responde("isto não é json", 200),
        _responde({"data": "forma errada"}, 200),
    ]
    s = _settings()
    for i, responder in enumerate(casos):
        feed.reset_cache()
        transporte, enviados = _transporte(responder)
        feed.ultima_publicacao(s, transport=transporte, agora=float(i))
        tentativas.extend(enviados)

    # O provider REALMENTE tentou as quatro chamadas — sem isto, um
    # curto-circuito silencioso deixaria todas as asserções abaixo
    # verdadeiras por vacuidade.
    assert len(tentativas) == 4, (
        f"o provider só tentou {len(tentativas)} de 4 chamadas"
    )
    assert len(registros) == 4, f"faltou registrar falha: {registros}"

    registrado = "\n".join(registros)
    assert TOKEN_FALSO not in registrado
    assert "Bearer" not in registrado
    assert "access_token" not in registrado
    # Nem corpo de resposta da Meta.
    assert "Session has expired" not in registrado
    # E nem a URL, que num descuido futuro poderia voltar a levar o token.
    assert "graph.instagram.com" not in registrado
    # Mas o diagnóstico existe: status e classe do erro.
    assert "HTTP 401" in registrado
    assert "ConnectError" in registrado


def test_a_mensagem_longa_nao_estoura_o_cartao():
    """Responsividade: o permalink é uma palavra só de 43 caracteres.

    Sem `overflow-wrap`, a 360px (Android antigo) a URL empurra a fila e a
    página ganha rolagem horizontal — medido com Chrome headless em
    1920/1366/1024/430/390/360. Este é o teste de contrato da regra; a
    bancada visual roda à parte.
    """

    bloco = WORKFLOW_CSS.split(".report-instagram-message {")[1].split("}")[0]
    assert "overflow-wrap: anywhere" in bloco
    assert "white-space: pre-line" in bloco  # as quebras da mensagem


# ======================================================================
# A validação do permalink, isolada
# ======================================================================


@pytest.mark.parametrize(
    "valor",
    [
        "https://www.instagram.com/p/CsYNTESTE01/",
        "https://www.instagram.com/reel/DPxTESTE02/",
        "https://www.instagram.com/reels/DPxTESTE02/",
        "https://www.instagram.com/tv/DPxTESTE02/",
        "https://instagram.com/p/CsYNTESTE01/",
    ],
)
def test_permalinks_aceitos(valor):
    assert feed.permalink_valido(valor) == valor


@pytest.mark.parametrize(
    "valor",
    [
        None,
        "",
        "   ",
        123,
        "http://www.instagram.com/p/CsYNTESTE01/",        # sem TLS
        "https://instagram.com.exemplo.invalido/p/X/",     # host parecido
        "https://www.instagram.com/soprolife",             # perfil, não post
        "https://www.instagram.com/stories/soprolife/1/",  # story
        "https://www.instagram.com/p/",                    # sem código
        "https://www.instagram.com/p/X/?utm_source=a",     # querystring
        "https://www.instagram.com/p/X/#frag",             # fragmento
        "https://user:senha@www.instagram.com/p/X/",       # credencial na URL
        "javascript:alert(1)",
        "https://www.instagram.com/p/" + "A" * 400 + "/",  # tamanho absurdo
    ],
)
def test_permalinks_recusados(valor):
    assert feed.permalink_valido(valor) is None


def test_configuracao_fora_de_faixa_e_recusada():
    """O TTL vive na faixa 30–60 e o timeout é finito — por validação."""

    for campo, valor in (
        ("instagram_feed_cache_minutes", 5),
        ("instagram_feed_cache_minutes", 600),
        ("instagram_feed_timeout_seconds", 0.1),
        ("instagram_feed_timeout_seconds", 120.0),
        ("instagram_user_id", "me OR 1=1"),
        ("instagram_user_id", "../../admin"),
        ("instagram_user_id", "17841\u00b2"),      # dígito Unicode não é dígito
        ("instagram_user_id", "1" * 33),       # tamanho absurdo
    ):
        with pytest.raises(Exception):
            _settings(**{campo: valor})

    # E o id numérico opcional entra no caminho da URL quando existe.
    s = _settings(instagram_user_id="17841400000000000")
    transporte, enviados = _transporte(
        _responde(_midia({"permalink": POST, "timestamp": "2026-10-14T12:00:00+0000"}))
    )
    feed.ultima_publicacao(s, transport=transporte)
    assert enviados[0].url.path == f"/{feed.API_VERSION}/17841400000000000/media"
