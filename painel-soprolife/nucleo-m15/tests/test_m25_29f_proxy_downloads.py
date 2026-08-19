"""M25.29F — o proxy do painel trocava PDF bom por 502.

Incidente real: com a API respondendo `HTTP 200 application/pdf` em
`127.0.0.1:8015`, o navegador recebia *"Resposta inválida da API.
(http_502)"* e nenhum download começava. A operação parou.

A causa está numa lista, não numa exceção. O proxy só relata conteúdo
binário quando a rota está numa allowlist — a intenção é certa, e é o que
impede o proxy de repassar qualquer coisa que venha do upstream. O defeito
era o conteúdo da lista: ela conhecia apenas o download por versão

    /laudos/<uuid>/versoes/<uuid>/conteudo

e ignorava as outras três rotas que devolvem binário:

    GET  /laudos/<uuid>/exame-tecnico/conteudo     (PDF)
    GET  /laudos/<uuid>/assinado/conteudo          (PDF)
    POST /laudos/assinatura-externa/baixar         (PDF ou ZIP)
    POST /laudos/lote/baixar                       (ZIP)

Sem casar, `application/pdf` caía no ramo que exige `application/json`, e o
proxy substituía a resposta boa por 502.

Isso explica os três sintomas de uma vez:

* o `conteúdo 5.jsold` — a âncora crua salvava o corpo do 502 como arquivo;
* o "Resposta inválida da API (http_502)" que apareceu depois da M25.29E,
  quando o erro deixou de virar arquivo e passou a virar mensagem;
* os lotes de auditoria repetidos — cada tentativa frustrada abria um.

Estes testes sobem o proxy DE VERDADE contra um upstream falso e conferem o
que chega do outro lado, que é o caminho do navegador.

Nenhum dado real: o "PDF" é um `%PDF` sintético de poucos bytes.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

import pytest

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]

UUID_A = "0191c2de-3f4a-4d55-9c33-2b1f7a884411"
UUID_B = "0191c2de-3f4a-4d55-9c33-2b1f7a884412"

PDF_SINTETICO = b"%PDF-1.4\n% TESTE APAGAR\n%%EOF\n"
ZIP_SINTETICO = b"PK\x03\x04TESTE-APAGAR"
NOME_PDF = 'attachment; filename="TESTE APAGAR - Assinado.pdf"'


def _proxy_module():
    """Carrega o proxy do painel como módulo, sem subir servidor."""

    caminho = PANEL_ROOT / "scripts" / "command-center-local-server.py"
    spec = importlib.util.spec_from_file_location("_ccls_m2529f", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ------------------------------------------------------- upstream falso


class _UpstreamFalso(BaseHTTPRequestHandler):
    """Responde como a API M15 real responde nas rotas de download."""

    protocol_version = "HTTP/1.1"

    def _responder(self, corpo: bytes, tipo: str, *, disposicao=None):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        if disposicao:
            self.send_header("Content-Disposition", disposicao)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):  # noqa: N802 - assinatura da stdlib
        if self.path.endswith("/conteudo"):
            self._responder(
                PDF_SINTETICO, "application/pdf", disposicao=NOME_PDF
            )
            return
        corpo = json.dumps({"ok": True}).encode()
        self._responder(corpo, "application/json")

    def do_POST(self):  # noqa: N802 - assinatura da stdlib
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho:
            self.rfile.read(tamanho)
        if self.path.endswith("/lote/baixar"):
            self._responder(ZIP_SINTETICO, "application/zip")
            return
        if self.path.endswith("/baixar"):
            self._responder(
                PDF_SINTETICO, "application/pdf", disposicao=NOME_PDF
            )
            return
        self._responder(json.dumps({"ok": True}).encode(), "application/json")

    def log_message(self, *_args):  # silencia o log da stdlib
        return


@pytest.fixture()
def ambiente(monkeypatch, tmp_path):
    """Proxy real de pé, apontando para o upstream falso."""

    upstream = HTTPServer(("127.0.0.1", 0), _UpstreamFalso)
    porta_upstream = upstream.server_address[1]
    thread_upstream = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread_upstream.start()

    # O upstream é resolvido por variável de ambiente a cada requisição.
    monkeypatch.setenv(
        "SOPROLIFE_M15_UPSTREAM", f"http://127.0.0.1:{porta_upstream}/api/v1"
    )
    modulo = _proxy_module()

    # O proxy serve arquivos a partir do diretório corrente; um temporário
    # evita que o teste dependa da árvore do repositório.
    monkeypatch.chdir(tmp_path)

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), modulo._Handler)
    porta_proxy = proxy.server_address[1]
    thread_proxy = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread_proxy.start()

    yield {"porta": porta_proxy, "modulo": modulo}

    proxy.shutdown()
    upstream.shutdown()


def _pedir(porta: int, metodo: str, caminho: str, corpo: bytes | None = None):
    conexao = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    cabecalhos = {"Cookie": "sl_session=teste-sintetico"}
    if corpo is not None:
        cabecalhos["Content-Type"] = "application/json"
    conexao.request(metodo, caminho, body=corpo, headers=cabecalhos)
    resposta = conexao.getresponse()
    dados = resposta.read()
    resultado = (resposta.status, dict(resposta.getheaders()), dados)
    conexao.close()
    return resultado


PREFIXO = "/painel-soprolife/api/m15"


# =====================================================================
# O gate de tipos — quem pode devolver binário
# =====================================================================


@pytest.mark.parametrize(
    "metodo, caminho, tipo, esperado",
    [
        ("GET", f"/laudos/{UUID_A}/versoes/{UUID_B}/conteudo", "application/pdf", True),
        ("GET", f"/laudos/{UUID_A}/exame-tecnico/conteudo", "application/pdf", True),
        ("GET", f"/laudos/{UUID_A}/assinado/conteudo", "application/pdf", True),
        ("POST", "/laudos/assinatura-externa/baixar", "application/pdf", True),
        ("POST", "/laudos/lote/baixar", "application/zip", True),
        # E o que NÃO pode: o gate continua fechado onde sempre esteve.
        ("GET", f"/laudos/{UUID_A}/assinado/conteudo", "text/html", False),
        ("GET", "/pessoas", "application/pdf", False),
        ("GET", "/laudos", "application/zip", False),
        ("POST", "/laudos", "application/pdf", False),
    ],
)
def test_gate_de_tipos_binarios(metodo, caminho, tipo, esperado):
    modulo = _proxy_module()
    tipos = modulo._tipos_binarios_esperados(metodo, caminho)
    aceito = any(tipo.startswith(item) for item in tipos)
    assert aceito is esperado


# =====================================================================
# O caminho do navegador, de ponta a ponta
# =====================================================================


@pytest.mark.parametrize(
    "caminho",
    [
        f"/laudos/{UUID_A}/exame-tecnico/conteudo",
        f"/laudos/{UUID_A}/assinado/conteudo",
    ],
)
def test_download_administrativo_atravessa_o_proxy(ambiente, caminho):
    """O 502 do incidente. Antes desta correção, isto devolvia 502."""

    status, cabecalhos, corpo = _pedir(ambiente["porta"], "GET", PREFIXO + caminho)

    assert status == 200, corpo[:200]
    assert cabecalhos["Content-Type"] == "application/pdf"
    assert corpo[:4] == b"%PDF"
    assert corpo == PDF_SINTETICO
    assert ".pdf" in cabecalhos["Content-Disposition"]
    assert cabecalhos["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in cabecalhos["Cache-Control"]


def test_download_por_versao_continua_funcionando(ambiente):
    """A rota que já funcionava não pode ter sido quebrada pela correção."""

    status, cabecalhos, corpo = _pedir(
        ambiente["porta"],
        "GET",
        f"{PREFIXO}/laudos/{UUID_A}/versoes/{UUID_B}/conteudo",
    )
    assert status == 200
    assert cabecalhos["Content-Type"] == "application/pdf"
    assert corpo[:4] == b"%PDF"


def test_lote_em_pdf_atravessa_o_proxy(ambiente):
    status, cabecalhos, corpo = _pedir(
        ambiente["porta"],
        "POST",
        f"{PREFIXO}/laudos/assinatura-externa/baixar",
        corpo=json.dumps({"document_ids": [UUID_A]}).encode(),
    )
    assert status == 200, corpo[:200]
    assert cabecalhos["Content-Type"] == "application/pdf"
    assert corpo[:4] == b"%PDF"


def test_lote_em_zip_atravessa_o_proxy(ambiente):
    status, cabecalhos, corpo = _pedir(
        ambiente["porta"],
        "POST",
        f"{PREFIXO}/laudos/lote/baixar",
        corpo=json.dumps({"document_ids": [UUID_A, UUID_B]}).encode(),
    )
    assert status == 200, corpo[:200]
    assert cabecalhos["Content-Type"] == "application/zip"
    assert corpo.startswith(b"PK")


def test_json_comum_continua_json(ambiente):
    """O caminho de sempre não pode ter mudado."""

    status, cabecalhos, corpo = _pedir(
        ambiente["porta"], "GET", f"{PREFIXO}/laudos"
    )
    assert status == 200
    assert cabecalhos["Content-Type"].startswith("application/json")
    assert json.loads(corpo) == {"ok": True}
