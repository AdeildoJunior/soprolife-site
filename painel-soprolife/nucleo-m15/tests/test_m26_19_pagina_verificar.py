"""M26.19 — a página estática pública `verificar/index.html`.

Mesma postura de `resultados/index.html` (M26.4/M26.7): sem indexação, sem
terceiro observando, e a mesma separação entre falha de rede e falha de
servidor. Provas de TEXTO — a suíte não sobe um navegador real aqui.
"""

from __future__ import annotations

import pathlib

import pytest

NUCLEO = pathlib.Path(__file__).resolve().parents[1]
RAIZ = NUCLEO.parents[1]
PAGINA = RAIZ / "verificar" / "index.html"
ROBOTS = RAIZ / "robots.txt"


@pytest.fixture(scope="module")
def pagina() -> str:
    return PAGINA.read_text(encoding="utf-8")


def test_pagina_existe_na_raiz_do_repositorio():
    assert PAGINA.is_file()


def test_robots_bloqueia_a_area_de_verificacao():
    assert "Disallow: /verificar/" in ROBOTS.read_text(encoding="utf-8")


def test_meta_robots_nao_indexa(pagina):
    assert 'content="noindex, nofollow, noarchive, nosnippet, noimageindex"' in pagina


def test_csp_restringe_a_uma_unica_origem_de_rede(pagina):
    trecho = pagina[pagina.index("Content-Security-Policy") : pagina.index("</head>")]
    assert "connect-src https://resultados-api.soprolife.com.br;" in trecho
    assert "default-src 'none';" in trecho
    assert "frame-ancestors 'none'" in trecho


def test_sem_terceiro_de_analytics_ou_rastreamento(pagina):
    for termo in ("googletagmanager", "google-analytics", "gtag(", "facebook.net",
                  "fbq(", "cdn.", "unpkg.", "jsdelivr"):
        assert termo not in pagina


def test_api_chamada_bate_com_a_origem_da_csp(pagina):
    assert 'var API = "https://resultados-api.soprolife.com.br/p/v1";' in pagina


def test_a_tela_separa_erro_do_servidor_de_queda_de_rede(pagina):
    assert "if (status >= 500) return MSG_SERVIDOR;" in pagina
    assert "var MSG_REDE" in pagina and "var MSG_SERVIDOR" in pagina


def test_codigo_vindo_pelo_link_usa_o_fragmento_nao_um_caminho(pagina):
    """O QR do laudo aponta para `.../verificar/#/CODIGO` — nunca para um
    caminho de servidor, porque o GitHub Pages não tem rota dinâmica."""

    assert "window.location.hash" in pagina
    assert 'API + "/verificar/"' in pagina


def test_formato_do_codigo_e_validado_no_cliente_antes_de_consultar(pagina):
    assert "CODIGO_RE" in pagina
    assert "[A-Z0-9]{8,24}" in pagina


def test_pagina_nao_pede_data_de_nascimento_nem_token(pagina):
    """Diferente do portal pessoal (M26.4): aqui não há segundo fator — o
    próprio código, de alta entropia, já autoriza a consulta."""

    assert "nascimento" not in pagina.lower()
    assert 'name="token"' not in pagina
