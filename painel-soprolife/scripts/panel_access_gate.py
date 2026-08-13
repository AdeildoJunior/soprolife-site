"""Classificação de acesso da camada ESTÁTICA do painel (M25.23).

Motivo desta existir
--------------------
Até a M25.22 o painel era servido por ``SimpleHTTPRequestHandler`` a partir da
raiz do repositório, sem autenticação e com listagem de diretórios ligada.
Consequência medida em produção, sem nenhuma sessão:

- ``/painel-soprolife/`` devolvia o Command Center inteiro (sidebar, Financeiro,
  CRM, Marketing) — o DOM administrativo chegava ao navegador antes do login;
- ``data/financeiro-summary.local.json`` devolvia receita e ticket médio reais;
- ``data-private/`` devolvia LISTAGEM e download de PII de paciente (nome +
  telefone) e de um ``apiToken`` vivo;
- ``.git/`` era clonável;
- ``nucleo-m15/app/config.py`` e os próprios scripts eram legíveis.

Este módulo é a decisão pura: dado um caminho HTTP, o que ele é. Zero rede,
zero DOM, zero estado — para poder ser testado sem subir servidor.

Contrato de classificação
-------------------------
``"forbidden"``      nunca sai por HTTP, nem autenticado. Não é dado do painel:
                     é fonte privada, código, repositório ou arquivo de sistema.
                     Responde 404 (não 403) para não confirmar existência.
``"protected_page"`` a casca do Command Center. Sem sessão, o servidor entrega
                     a tela de login NO MESMO endereço; com sessão, o painel.
``"protected_data"`` dado operacional. Sem sessão, 401 — nunca corpo parcial.
``"public"``         site institucional, CSS, imagens e o JS da própria tela de
                     login. Nada aqui carrega dado operacional.

Regra de ouro: na dúvida, NÃO é público. Qualquer caminho sob
``/painel-soprolife/`` que não esteja explicitamente liberado cai em
``protected_page`` — fail-closed.
"""

import posixpath
import urllib.parse

PUBLIC = "public"
PROTECTED_PAGE = "protected_page"
PROTECTED_DATA = "protected_data"
FORBIDDEN = "forbidden"

PANEL_PREFIX = "/painel-soprolife"

# Diretórios que NUNCA são conteúdo web. `data-private` é a fonte privada que
# alimenta os summaries; `nucleo-m15` é o backend inteiro (inclusive config e
# migrations); `scripts` é este próprio servidor. Nenhum deles tem motivo para
# existir numa resposta HTTP.
FORBIDDEN_DIRS = (
    "painel-soprolife/data-private",
    "painel-soprolife/nucleo-m15",
    "painel-soprolife/scripts",
    "painel-soprolife/apps-script",
    "painel-soprolife/systemd",
    "painel-soprolife/config-examples",
    "painel-soprolife/modelos-planilhas",
    "audits",
    "_schema-pendente",
)

# Extensões que denunciam código-fonte, banco ou credencial. A lista vale para
# o repositório inteiro, inclusive o site institucional.
FORBIDDEN_SUFFIXES = (
    ".py", ".pyc", ".db", ".sqlite", ".sqlite3", ".sql",
    ".env", ".pem", ".key", ".crt", ".p12", ".pfx",
    ".log", ".lock", ".bak", ".swp", ".ini", ".cfg", ".toml",
    ".service", ".timer", ".gs",
    # Documentação interna. Os RELATORIO_*.md da raiz descrevem arquitetura,
    # caminhos internos, números operacionais e — no caso da própria M25.23 —
    # o desenho do gate. Nenhum é conteúdo do site: verificado que nenhuma
    # página pública referencia um .md. Ficam versionados e legíveis no
    # repositório, nunca por HTTP.
    #
    # `.txt` NÃO entra nesta lista: robots.txt é conteúdo público obrigatório
    # do site. O único .txt sensível (data-private/README.local.txt) já está
    # coberto pela proibição da pasta inteira.
    ".md",
)

# Subárvores do painel liberadas sem sessão. São exclusivamente apresentação:
# folha de estilo, imagem, fonte e o JS da própria tela de login. Nenhuma
# delas lê dado operacional.
PANEL_PUBLIC_DIRS = (
    "painel-soprolife/css",
    "painel-soprolife/assets",
    "painel-soprolife/img",
)

# Arquivos avulsos do painel liberados sem sessão.
PANEL_PUBLIC_FILES = (
    "painel-soprolife/login.html",
    "painel-soprolife/js/login.js",
    "painel-soprolife/js/m15-security.js",
)

# A casca do Command Center. Sem sessão vira a tela de login.
PANEL_PAGE_FILES = (
    "painel-soprolife/",
    "painel-soprolife/index.html",
)

# Dado sob `data/` que QUALQUER sessão válida lê, independente do papel
# (M25.27).
#
# `data/` é operacional por padrão — e continua sendo. Mas `m15-config.json`
# não é dado operacional: é o manifesto de BOOT do painel (flag `enabled`,
# `reports_enabled`, `api_base`). Quem o lê não descobre nada sobre a
# operação; quem NÃO o lê não consegue montar tela nenhuma.
#
# Foi exatamente esse o defeito da M25.27: classificado como dado operacional,
# ele respondia 403 para a sessão exclusivamente clínica. As duas telas que
# dependem dele desistiam em silêncio — o núcleo nunca revelava o "Sair" e a
# bancada médica ficava eternamente em "Carregando o fluxo seguro de laudos…".
#
# A isenção é NOMINAL e mínima por desenho: um arquivo, não um diretório. Um
# `data/*.json` novo continua nascendo administrativo, como manda a regra de
# ouro do módulo.
PANEL_SHARED_SESSION_DATA = (
    "painel-soprolife/data/m15-config.json",
)


class InvalidPath(ValueError):
    """Caminho malformado ou com tentativa de travessia."""


def normalize(raw_target: str) -> str:
    """Devolve o caminho decodificado e normalizado, ou levanta InvalidPath.

    Recusa (em vez de tentar consertar) tudo que serve para confundir um
    comparador de prefixo: barra invertida, NUL, ``..``, percent-encoding
    inválido. Consertar silenciosamente é como travessia costuma passar.
    """
    if not isinstance(raw_target, str) or not raw_target:
        raise InvalidPath("caminho vazio")
    path = urllib.parse.urlsplit(raw_target).path
    try:
        decoded = urllib.parse.unquote(path, errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidPath("percent-encoding inválido") from exc
    if "\\" in decoded or "\x00" in decoded:
        raise InvalidPath("caractere proibido no caminho")
    if not decoded.startswith("/"):
        raise InvalidPath("caminho relativo")
    segments = decoded.split("/")
    if any(segment in (".", "..") for segment in segments):
        raise InvalidPath("travessia de diretório")
    # posixpath.normpath colapsa "//" e resolve o resto; como já recusamos
    # ".." acima, aqui ele só limpa barras repetidas.
    limpo = posixpath.normpath(decoded)
    if decoded.endswith("/") and not limpo.endswith("/"):
        limpo += "/"
    return limpo


def _relative(path: str) -> str:
    """Caminho sem a barra inicial, em minúsculas, para comparação."""
    return path.lstrip("/").lower()


def _under(rel: str, base: str) -> bool:
    """True quando `rel` é o próprio `base` ou está dentro dele."""
    base = base.lower().rstrip("/")
    return rel == base or rel.startswith(base + "/")


def _has_dot_segment(rel: str) -> bool:
    """Qualquer segmento iniciado por ponto: .git, .env, .gitignore, .ssh…"""
    return any(seg.startswith(".") for seg in rel.split("/") if seg)


def classify(raw_target: str) -> str:
    """Classifica um alvo HTTP. Levanta InvalidPath em caminho malformado."""
    path = normalize(raw_target)
    rel = _relative(path)

    # 1. Arquivos e diretórios ocultos — .git em primeiro lugar.
    if _has_dot_segment(rel):
        return FORBIDDEN

    # 2. Subárvores que não são conteúdo web.
    for proibido in FORBIDDEN_DIRS:
        if _under(rel, proibido):
            return FORBIDDEN

    # 3. Extensões de código/banco/credencial, em qualquer lugar do repo.
    if rel.endswith(FORBIDDEN_SUFFIXES):
        return FORBIDDEN

    # 4. Fora do painel: site institucional, público por natureza.
    if not _under(rel, "painel-soprolife"):
        return PUBLIC

    # 5. Dentro do painel — allowlist explícita primeiro.
    if rel in (f.lower() for f in PANEL_PUBLIC_FILES):
        return PUBLIC
    for publico in PANEL_PUBLIC_DIRS:
        if _under(rel, publico):
            return PUBLIC

    # 6. Dado operacional do painel.
    if _under(rel, "painel-soprolife/data"):
        return PROTECTED_DATA

    # 7. A casca do painel e QUALQUER outro caminho não previsto sob
    #    /painel-soprolife/ — fail-closed. Um arquivo novo largado na pasta
    #    amanhã nasce protegido, não público.
    return PROTECTED_PAGE


def is_shared_session_data(raw_target: str) -> bool:
    """True para o dado de `data/` que toda sessão válida lê, sem olhar papel.

    Não afrouxa a exigência de sessão: `classify` continua devolvendo
    ``protected_data`` e quem não tem cookie continua levando 401. O que esta
    função responde é a pergunta SEGUINTE — "já que há sessão, o papel importa
    para ESTE arquivo?" — e ela só diz "não" para a allowlist nominal acima.
    """
    try:
        path = normalize(raw_target)
    except InvalidPath:
        return False
    rel = _relative(path)
    return rel in (p.lower() for p in PANEL_SHARED_SESSION_DATA)


def is_panel_entry(raw_target: str) -> bool:
    """True para os alvos que devem virar a TELA DE LOGIN quando não há sessão.

    Só a porta de entrada troca de conteúdo. Qualquer outro caminho protegido
    recusa com status, em vez de devolver HTML de login — devolver uma página
    onde o cliente pediu um .json só produziria erro de parse confuso.
    """
    try:
        path = normalize(raw_target)
    except InvalidPath:
        return False
    rel = _relative(path)
    return rel in (p.lower() for p in PANEL_PAGE_FILES)
