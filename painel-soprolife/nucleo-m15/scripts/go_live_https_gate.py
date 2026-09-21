#!/usr/bin/env python3
"""M15.5B — Gate de go-live controlado do deploy produtivo do Núcleo M15.

Ponte fail-closed usada por deploy-producao-vps.sh (via lib-go-live-gate.sh)
para aceitar um release com enabled=true SOMENTE sob autorização explícita e
validação HTTPS do endereço privado. Nada aqui configura Serve, Funnel,
certificado, firewall ou ACL; nada aqui conhece hostname real de tailnet.

Subcomandos (exit 0 = validado; exit 1 = rejeitado, fail-closed):

  validate-url   <base-url>    valida a forma da URL base HTTPS
  check-source   <repo-root>   checagens estáticas do release alvo (enabled=true)
  check-https-pre  <base-url>  antes de mutação: superfície ANÔNIMA correta —
                               /painel-soprolife/ 200 com a TELA DE LOGIN (e sem
                               nenhuma marcação do Command Center), health 200
                               status=ok e m15-config.json protegido (401)
  check-https-pos  <base-url> <repo-root>
                               após deploy: pre + release implantado aprovado no
                               check-source (fonte local versionada) + health do
                               serviço em execução igual à versão do release +
                               bytes servidos dos artefatos PÚBLICOS idênticos
                               aos do release implantado

M26.21 — por que o postflight não faz mais GET anônimo de m15-config.json nem
do index.html administrativo: desde a M25.23 a camada estática do painel exige
sessão (scripts/panel_access_gate.py). Um GET anônimo de /painel-soprolife/
recebe login.html e um GET anônimo de data/m15-config.json recebe 401 — como
deve ser. Provar o release por HTTPS anônimo exigiria tornar esses artefatos
públicos de novo, ou embutir credencial administrativa no deploy: as duas
coisas são proibidas. A prova passou a ser feita em duas metades que, juntas,
são MAIS fortes do que a anterior: (a) o que é público prova-se por HTTPS,
inclusive negativamente (o que NÃO pode vazar); (b) o que é administrativo
prova-se na fonte local versionada do próprio host, mais o casamento entre
serviço em execução e release implantado.

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
RE_VERSAO_APP = re.compile(r'^__version__\s*=\s*"([^"]+)"\s*$', re.MULTILINE)

# M26.21 — o que o GET ANÔNIMO de /painel-soprolife/ tem de conter: a tela de
# login servida no MESMO endereço (login.html), com a guarda de contexto
# seguro. Não é decoração: é a prova de que o gate estático está ligado.
MARCADORES_TELA_LOGIN = (
    'id="loginForm"',
    'id="password"',
    "js/m15-security.js",
)

# …e o que ele NÃO pode conter. Antes da M25.23 o Command Center inteiro saía
# aqui sem sessão; a ausência destes marcadores é a prova NEGATIVA de que o
# vazamento não voltou. `m15-nucleo.js` é a casca administrativa;
# `laudos-espirometria`/`report-workflow.js` são a bancada médica.
MARCADORES_ADMIN_PROIBIDOS = (
    "m15-nucleo.js",
    'id="laudos-espirometria"',
    "report-workflow.js",
)

# Fragmentos do manifesto de boot que uma resposta 401 jamais pode carregar.
FRAGMENTOS_CONFIG_PROIBIDOS = ('"api_base"', '"enabled"', '"reports_mode"')

# Artefatos PÚBLICOS por desenho (panel_access_gate.PANEL_PUBLIC_FILES e a
# entrada do painel sem sessão). Comparar os bytes servidos com os do release
# implantado prova que o HTTPS serve ESTE checkout — sem expor nada novo.
ARTEFATOS_PUBLICOS_SERVIDOS = (
    (CAMINHO_PAINEL, "painel-soprolife/login.html"),
    (CAMINHO_SECURITY_JS, "painel-soprolife/js/m15-security.js"),
)
CAMINHO_VERSAO_APP = "painel-soprolife/nucleo-m15/app/__init__.py"

# Códigos estáveis das rejeições da superfície anônima. O gate de laudos
# traduz por ELES, nunca pelo texto da mensagem.
CODIGO_PAINEL_NAO_200 = "painel_nao_200"
CODIGO_PAINEL_VAZOU_ADMIN = "painel_vazou_admin"
CODIGO_PAINEL_SEM_LOGIN = "painel_sem_login"
CODIGO_CONFIG_DESPROTEGIDO = "config_desprotegido"


class GateError(Exception):
    """Rejeição do gate — sempre fail-closed.

    ``codigo`` é opcional e existe para que OUTRO gate (o de laudos) possa
    traduzir a rejeição para o vocabulário dele sem depender do texto da
    mensagem em português, que é para o operador ler e pode mudar.
    """

    def __init__(self, mensagem, *, codigo=None):
        super().__init__(mensagem)
        self.codigo = codigo


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


def _fazer_getter(opener=None, http_get_fn=None):
    """Adapta os dois formatos de GET usados no projeto para `get(url, prazo)`.

    O gate de laudos injeta um getter de dois argumentos; o deploy usa o
    `http_get` deste módulo (três argumentos, com opener). A resolução de
    `http_get` fica DENTRO do lambda de propósito: é isso que mantém os testes
    existentes, que trocam `gate.http_get`, funcionando sem mudança.
    """
    if http_get_fn is not None:
        return http_get_fn
    return lambda url, prazo: http_get(url, prazo, opener)


def checar_entrada_anonima(base, prazo_final, *, get):
    """GET ANÔNIMO de /painel-soprolife/: 200 com a TELA DE LOGIN, e só ela.

    Desde a M25.23 esta é a resposta correta do painel para quem não tem
    sessão. A checagem é dupla — positiva (a tela de login está lá) e negativa
    (nenhuma marcação do Command Center saiu junto). Devolve os bytes servidos.
    """
    status, corpo = get(base + CAMINHO_PAINEL, prazo_final)
    if status != 200:
        raise GateError(
            f"painel via HTTPS respondeu HTTP {status} (exigido 200)",
            codigo=CODIGO_PAINEL_NAO_200,
        )
    html = corpo.decode("utf-8", errors="replace")
    # O vazamento vem PRIMEIRO de propósito: é o achado mais grave, e é o que
    # o operador precisa ler no topo do erro se a regressão da M25.23 voltar.
    for marcador in MARCADORES_ADMIN_PROIBIDOS:
        if marcador in html:
            raise GateError(
                "GET anônimo de /painel-soprolife/ vazou marcação "
                f"administrativa do Command Center: {marcador}",
                codigo=CODIGO_PAINEL_VAZOU_ADMIN,
            )
    for marcador in MARCADORES_TELA_LOGIN:
        if marcador not in html:
            raise GateError(
                "GET anônimo de /painel-soprolife/ não devolveu a tela de "
                f"login esperada (marcador ausente: {marcador})",
                codigo=CODIGO_PAINEL_SEM_LOGIN,
            )
    return corpo


def checar_protegidos_nao_vazam(base, prazo_final, *, get):
    """GET ANÔNIMO do manifesto de boot: tem de ser 401, e sem corpo parcial."""
    status, corpo = get(base + CAMINHO_CONFIG, prazo_final)
    if status == 200:
        raise GateError(
            "m15-config.json respondeu 200 a um GET anônimo — o manifesto de "
            "boot voltou a ser público",
            codigo=CODIGO_CONFIG_DESPROTEGIDO,
        )
    if status != 401:
        raise GateError(
            f"m15-config.json anônimo respondeu HTTP {status} (exigido 401)",
            codigo=CODIGO_CONFIG_DESPROTEGIDO,
        )
    texto = corpo.decode("utf-8", errors="replace")
    for fragmento in FRAGMENTOS_CONFIG_PROIBIDOS:
        if fragmento in texto:
            raise GateError(
                "a recusa 401 de m15-config.json carrega conteúdo do manifesto",
                codigo=CODIGO_CONFIG_DESPROTEGIDO,
            )


def _checar_health(base, prazo_final, *, get):
    """Health M15 200 com status exatamente "ok". Devolve o objeto lido."""
    status, corpo = get(base + CAMINHO_HEALTH, prazo_final)
    _exigir_200("health M15 via HTTPS (mesma origem)", status)
    try:
        saude = json.loads(corpo.decode("utf-8"))
    except Exception as exc:
        raise GateError(f"health M15 não devolveu JSON válido: {exc}") from exc
    if not isinstance(saude, dict) or saude.get("status") != "ok":
        raise GateError('health M15 sem status exatamente "ok"')
    return saude


def _versao_do_release(repo_root):
    """Versão declarada no checkout implantado, lida sem importar o pacote."""
    texto = _ler_arquivo(repo_root, CAMINHO_VERSAO_APP)
    achado = RE_VERSAO_APP.search(texto)
    if not achado:
        raise GateError("release implantado sem __version__ legível em app/__init__.py")
    return achado.group(1)


def checar_servico_corresponde_ao_release(base, repo_root, prazo_final, *, get):
    """O processo em execução é o do release implantado (e o banco responde).

    Substitui a antiga prova por `enabled=true` servido em m15-config.json,
    que hoje exigiria tornar o manifesto público. O health é anônimo por
    contrato (é o único endpoint de leitura sem autenticação na API) e não
    revela nada operacional: versão, ambiente e saúde do banco.
    """
    saude = _checar_health(base, prazo_final, get=get)
    esperada = _versao_do_release(repo_root)
    if saude.get("versao") != esperada:
        raise GateError(
            "a API em execução não é a do release implantado "
            f"(health informa versão diferente de {esperada})"
        )
    if saude.get("ambiente") != "prod":
        raise GateError('API em execução não está com ambiente "prod"')
    if saude.get("banco") != "ok":
        raise GateError("API em execução sem banco saudável após o deploy")


def checar_servido_corresponde_ao_release(base, repo_root, prazo_final, *, get):
    """Bytes servidos == bytes do release, só nos artefatos JÁ públicos.

    login.html é o que o painel entrega sem sessão; m15-security.js é a guarda
    de contexto seguro que essa própria tela carrega. Nenhum dos dois é
    administrativo, então compará-los não abre nada — e a igualdade byte a byte
    prova que o HTTPS está servindo ESTE checkout, não um release anterior.
    """
    for caminho_http, relativo in ARTEFATOS_PUBLICOS_SERVIDOS:
        status, corpo = get(base + caminho_http, prazo_final)
        _exigir_200(f"{relativo} servido", status)
        if not corpo.strip():
            raise GateError(f"{relativo} servido está vazio")
        with open(f"{repo_root}/{relativo}", "rb") as fh:
            esperado = fh.read(MAX_BODY_BYTES)
        if corpo != esperado:
            raise GateError(
                f"o conteúdo servido em {caminho_http} não é o do release "
                f"implantado ({relativo})"
            )


def checar_https_pre(base_url, opener=None, http_get_fn=None):
    """Antes de qualquer mutação: a superfície ANÔNIMA está correta.

    Painel 200 com a tela de login (e nada do Command Center), health M15 200
    com status=ok, e o manifesto de boot recusado com 401 a quem não tem
    sessão. Nenhuma credencial administrativa participa desta validação.
    """
    base = validar_base_url(base_url)
    prazo_final = time.monotonic() + TOTAL_TIMEOUT_S
    get = _fazer_getter(opener, http_get_fn)
    checar_entrada_anonima(base, prazo_final, get=get)
    _checar_health(base, prazo_final, get=get)
    checar_protegidos_nao_vazam(base, prazo_final, get=get)
    print(
        f"OK: HTTPS pré-validado em {base} (login anônimo 200 sem Command "
        "Center; health 200 status=ok; m15-config.json 401)."
    )


def checar_https_pos(base_url, repo_root, opener=None, http_get_fn=None):
    """Após o deploy: pre + release implantado + serviço e bytes servidos.

    O release alvo (index.html administrativo, m15-config.json, ordem dos
    scripts, ausência de script externo, não persistência de token) é validado
    na FONTE LOCAL versionada do próprio host — os mesmos artefatos que o
    check-source já examinava antes da mutação, agora reexaminados depois dela,
    sem precisar que nada disso saia por HTTPS.
    """
    checar_https_pre(base_url, opener, http_get_fn)
    base = validar_base_url(base_url)
    prazo_final = time.monotonic() + TOTAL_TIMEOUT_S
    get = _fazer_getter(opener, http_get_fn)
    checar_fonte_alvo(repo_root)
    checar_servico_corresponde_ao_release(base, repo_root, prazo_final, get=get)
    checar_servido_corresponde_ao_release(base, repo_root, prazo_final, get=get)
    print(
        f"OK: HTTPS pós-validado em {base} (release implantado aprovado no "
        "check-source; serviço e bytes servidos conferem com o release)."
    )


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


# check-https-pos precisa do repo root do release implantado para validar os
# artefatos administrativos na fonte local; os demais subcomandos continuam com
# um argumento só. A aridade é por subcomando para que um argumento a mais (ou
# a menos) seja erro de uso, nunca argumento silenciosamente ignorado.
ARIDADE = {
    "validate-url": 1,
    "check-source": 1,
    "check-https-pre": 1,
    "check-https-pos": 2,
}


def main(argv):
    comando = argv[1] if len(argv) >= 2 else None
    if comando not in ARIDADE or len(argv) != 2 + ARIDADE[comando]:
        print(__doc__, file=sys.stderr)
        return 2
    alvo = argv[2]
    try:
        if comando == "validate-url":
            base = validar_base_url(alvo)
            print(f"OK: URL base válida ({base}).")
        elif comando == "check-source":
            checar_fonte_alvo(alvo)
        elif comando == "check-https-pre":
            checar_https_pre(alvo)
        else:
            checar_https_pos(alvo, argv[3])
    except GateError as exc:
        print(f"ERRO GO-LIVE (fail-closed): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
