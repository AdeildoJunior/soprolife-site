#!/usr/bin/env python3
"""M15.5B — Gate de go-live controlado do deploy produtivo do Núcleo M15.

Ponte fail-closed usada por deploy-producao-vps.sh (via lib-go-live-gate.sh)
para aceitar um release com enabled=true SOMENTE sob autorização explícita e
validação HTTPS do endereço privado. Nada aqui configura Serve, Funnel,
certificado, firewall ou ACL; nada aqui conhece hostname real de tailnet.

Subcomandos (exit 0 = validado; exit 1 = rejeitado, fail-closed):

  validate-url   <base-url>    valida a forma da URL base HTTPS
  check-source   <repo-root>   checagens estáticas do release alvo (enabled=true)
  check-https-pre  <base-url>  antes de mutação: painel 200 + health 200 status=ok
  check-https-pos  <base-url>  após deploy: pre + enabled=true servido +
                               m15-security.js 200 + ordem dos scripts no HTML

Garantias de rede: somente HTTPS (opener sem handler de http), verificação de
certificado SEMPRE ativa (falha se o contexto SSL não exigir), redirects só
aceitos para HTTPS no mesmo hostname (sem downgrade), timeout de conexão e
prazo total finitos, somente stdlib (nenhum cliente externo, nenhuma flag
insegura de TLS).
"""

import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

API_BASE = "/painel-soprolife/api/m15"
CONNECT_TIMEOUT_S = 10
TOTAL_TIMEOUT_S = 60
MAX_BODY_BYTES = 5 * 1024 * 1024

# Caminhos servidos, relativos à raiz do site (base HTTPS validada).
CAMINHO_PAINEL = "/painel-soprolife/"
CAMINHO_HEALTH = "/painel-soprolife/api/m15/health"
CAMINHO_CONFIG = "/painel-soprolife/data/m15-config.json"
CAMINHO_SECURITY_JS = "/painel-soprolife/js/m15-security.js"

RE_HOSTNAME = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?)*$",
    re.IGNORECASE,
)
RE_SCRIPT_SECURITY = re.compile(r'<script[^>]+src="[^"]*\bm15-security\.js[^"]*"')
RE_SCRIPT_NUCLEO = re.compile(r'<script[^>]+src="[^"]*\bm15-nucleo\.js[^"]*"')


class GateError(Exception):
    """Rejeição do gate — sempre fail-closed."""


def validar_base_url(url):
    """Valida a URL base do go-live e devolve 'https://host[:porta]' normalizado."""
    if not isinstance(url, str) or not url.strip():
        raise GateError("URL base ausente ou vazia")
    if url != url.strip():
        raise GateError("URL base com espaços nas bordas")
    try:
        partes = urlsplit(url)
        porta = partes.port  # acesso valida a porta; inválida levanta ValueError
    except ValueError as exc:
        raise GateError(f"URL base malformada: {exc}") from exc
    if partes.scheme != "https":
        raise GateError("esquema deve ser exatamente https (HTTP é rejeitado)")
    if partes.username is not None or partes.password is not None:
        raise GateError("URL base não pode embutir usuário ou senha")
    if partes.query:
        raise GateError("URL base não pode ter querystring")
    if partes.fragment:
        raise GateError("URL base não pode ter fragmento (#)")
    host = partes.hostname
    if not host or not RE_HOSTNAME.fullmatch(host):
        raise GateError("hostname da URL base ausente ou inválido")
    if partes.path not in ("", "/"):
        raise GateError("URL base deve apontar para a raiz do site (path / ou vazio)")
    autoridade = host if porta is None else f"{host}:{porta}"
    return f"https://{autoridade}"


def criar_contexto_ssl():
    """Contexto TLS padrão; recusa executar se a verificação estiver desligada."""
    contexto = ssl.create_default_context()
    if contexto.verify_mode != ssl.CERT_REQUIRED or not contexto.check_hostname:
        raise GateError(
            "verificação de certificado TLS desativada — go-live proibido"
        )
    return contexto


class RedirecionadorSeguro(urllib.request.HTTPRedirectHandler):
    """Só segue redirect para HTTPS no MESMO hostname; qualquer downgrade aborta."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        destino = urlsplit(newurl)
        origem = urlsplit(req.full_url)
        if destino.scheme != "https":
            raise GateError(
                f"redirect para esquema '{destino.scheme}' rejeitado (downgrade)"
            )
        if destino.hostname != origem.hostname:
            raise GateError("redirect para outro hostname rejeitado")
        if destino.username is not None or destino.password is not None:
            raise GateError("redirect com credenciais embutidas rejeitado")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def criar_opener():
    """Opener só-HTTPS: sem handler de http://, com redirect seguro e TLS estrito."""
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPSHandler(context=criar_contexto_ssl()),
        RedirecionadorSeguro(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.UnknownHandler(),
    ):
        opener.add_handler(handler)
    return opener


def http_get(url, prazo_final, opener=None):
    """GET com timeout de conexão e prazo total finitos. Retorna (status, corpo)."""
    if urlsplit(url).scheme != "https":
        raise GateError(f"tentativa de acesso não-HTTPS bloqueada: {url}")
    restante = prazo_final - time.monotonic()
    if restante <= 0:
        raise GateError("prazo total de validação HTTPS esgotado")
    timeout = min(CONNECT_TIMEOUT_S, restante)
    if opener is None:
        opener = criar_opener()
    try:
        with opener.open(url, timeout=timeout) as resposta:
            return resposta.status, resposta.read(MAX_BODY_BYTES)
    except urllib.error.HTTPError as exc:
        corpo = exc.read(MAX_BODY_BYTES) if exc.fp is not None else b""
        return exc.code, corpo
    except GateError:
        raise
    except Exception as exc:  # DNS, TLS, conexão, timeout: tudo rejeita
        raise GateError(f"falha de rede/TLS ao acessar {url}: {exc}") from exc


def _exigir_200(descricao, status):
    if status != 200:
        raise GateError(f"{descricao} respondeu HTTP {status} (exigido 200)")


def checar_https_pre(base_url, opener=None):
    """Antes de qualquer mutação: painel 200 e health M15 200 com status=ok."""
    base = validar_base_url(base_url)
    prazo_final = time.monotonic() + TOTAL_TIMEOUT_S
    status, _ = http_get(base + CAMINHO_PAINEL, prazo_final, opener)
    _exigir_200("painel via HTTPS", status)
    status, corpo = http_get(base + CAMINHO_HEALTH, prazo_final, opener)
    _exigir_200("health M15 via HTTPS (mesma origem)", status)
    try:
        saude = json.loads(corpo.decode("utf-8"))
    except Exception as exc:
        raise GateError(f"health M15 não devolveu JSON válido: {exc}") from exc
    if not isinstance(saude, dict) or saude.get("status") != "ok":
        raise GateError('health M15 sem status exatamente "ok"')
    print(f"OK: HTTPS pré-validado em {base} (painel 200; health 200 status=ok).")


def checar_https_pos(base_url, opener=None):
    """Após o deploy: pre + enabled=true servido + guarda servida + ordem no HTML."""
    checar_https_pre(base_url, opener)
    base = validar_base_url(base_url)
    prazo_final = time.monotonic() + TOTAL_TIMEOUT_S

    status, corpo = http_get(base + CAMINHO_CONFIG, prazo_final, opener)
    _exigir_200("m15-config.json servido", status)
    try:
        cfg = json.loads(corpo.decode("utf-8"))
    except Exception as exc:
        raise GateError(f"m15-config.json servido não é JSON válido: {exc}") from exc
    if cfg.get("enabled") is not True:
        raise GateError("config servida não tem enabled=true após o deploy")
    if cfg.get("api_base") != API_BASE:
        raise GateError("config servida mudou o api_base de mesma origem")

    status, corpo = http_get(base + CAMINHO_SECURITY_JS, prazo_final, opener)
    _exigir_200("m15-security.js servido", status)
    if not corpo.strip():
        raise GateError("m15-security.js servido está vazio")

    status, corpo = http_get(base + CAMINHO_PAINEL, prazo_final, opener)
    _exigir_200("index.html servido", status)
    html = corpo.decode("utf-8", errors="replace")
    seguranca = RE_SCRIPT_SECURITY.search(html)
    nucleo = RE_SCRIPT_NUCLEO.search(html)
    if not seguranca or not nucleo:
        raise GateError("index.html servido não carrega m15-security.js e m15-nucleo.js")
    if seguranca.start() >= nucleo.start():
        raise GateError("m15-security.js deve carregar ANTES de m15-nucleo.js no HTML servido")
    print(f"OK: HTTPS pós-validado em {base} (enabled=true; guarda servida; ordem correta).")


def _ler_arquivo(repo_root, relativo):
    caminho = f"{repo_root}/{relativo}"
    try:
        with open(caminho, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise GateError(f"arquivo obrigatório ausente no release alvo: {relativo} ({exc})") from exc


def checar_fonte_alvo(repo_root):
    """Checagens estáticas do checkout alvo para um release enabled=true."""
    cfg_texto = _ler_arquivo(repo_root, "painel-soprolife/data/m15-config.json")
    try:
        cfg = json.loads(cfg_texto)
    except Exception as exc:
        raise GateError(f"m15-config.json inválido: {exc}") from exc
    if cfg.get("enabled") is not True:
        raise GateError("check-source só se aplica a release com enabled=true")
    if cfg.get("api_base") != API_BASE:
        raise GateError("api_base de mesma origem foi alterado no release alvo")

    guarda = _ler_arquivo(repo_root, "painel-soprolife/js/m15-security.js")
    if not guarda.strip():
        raise GateError("m15-security.js está vazio no release alvo")
    # Bloqueio de contexto inseguro (HTTP remoto): marcadores mínimos da guarda.
    for marcador in ('"blocked"', "classify", "localhost", "127.", "https:"):
        if marcador not in guarda:
            raise GateError(
                f"m15-security.js sem o marcador de bloqueio esperado: {marcador}"
            )

    html = _ler_arquivo(repo_root, "painel-soprolife/index.html")
    seguranca = RE_SCRIPT_SECURITY.search(html)
    nucleo = RE_SCRIPT_NUCLEO.search(html)
    if not seguranca:
        raise GateError("index.html do release alvo não carrega m15-security.js")
    if not nucleo:
        raise GateError("index.html do release alvo não carrega m15-nucleo.js")
    if seguranca.start() >= nucleo.start():
        raise GateError("m15-security.js deve vir ANTES de m15-nucleo.js no index.html")
    if re.search(r'<script[^>]+src="https?://', html):
        raise GateError("index.html do release alvo carrega script externo")

    nucleo_js = _ler_arquivo(repo_root, "painel-soprolife/js/m15-nucleo.js")
    for nome, texto in (("m15-nucleo.js", nucleo_js), ("m15-security.js", guarda)):
        if ".setItem(" in texto:
            raise GateError(f"{nome} persiste dados no navegador (.setItem) — proibido para token")
        if "http://" in texto or "https://" in texto:
            raise GateError(f"{nome} referencia URL absoluta externa — dependência proibida")

    testes = _ler_arquivo(repo_root, "painel-soprolife/scripts/test-m15-go-live.js")
    if not testes.strip():
        raise GateError("testes globais de segurança do go-live ausentes/vazios")
    print("OK: release alvo passou nas checagens estáticas do go-live.")


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    comando, alvo = argv[1], argv[2]
    try:
        if comando == "validate-url":
            base = validar_base_url(alvo)
            print(f"OK: URL base válida ({base}).")
        elif comando == "check-source":
            checar_fonte_alvo(alvo)
        elif comando == "check-https-pre":
            checar_https_pre(alvo)
        elif comando == "check-https-pos":
            checar_https_pos(alvo)
        else:
            print(f"ERRO GO-LIVE: subcomando desconhecido: {comando}", file=sys.stderr)
            return 2
    except GateError as exc:
        print(f"ERRO GO-LIVE (fail-closed): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
