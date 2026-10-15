"""M26.32 — a publicação mais recente do Instagram, por API oficial da Meta.

Para quê: o convite que a M26.31 manda ao paciente leva hoje o endereço do
perfil. Levando o PERMALINK da última publicação, o WhatsApp tem como montar
a prévia (imagem ou Reel) e o convite deixa de ser um link cru. Quem decide
se renderiza a prévia é o WhatsApp — isto aqui só entrega o endereço.

Contrato oficial — **Instagram API with Instagram Login**
(`developers.facebook.com/docs/instagram-platform`):

- host: ``graph.instagram.com`` (o caminho com Login do Facebook, em
  ``graph.facebook.com``, exigiria Página do Facebook vinculada e o escopo
  ``instagram_basic``; não é o nosso caso);
- lista: ``GET /{VERSAO}/{IG_ID|me}/media?fields=permalink,timestamp,media_type``
  com ``Authorization: Bearer <token>``;
- credencial: token de usuário Instagram de longa duração (60 dias,
  renovável em ``/refresh_access_token``), escopo ``instagram_business_basic``,
  conta profissional (Business ou Creator);
- ``permalink`` é a URL permanente da publicação; ``timestamp`` é ISO 8601
  em UTC.

O que este módulo NÃO faz, de propósito:

- **não raspa o Instagram.** Nenhum HTML de instagram.com é lido aqui. A
  única origem é a Graph API, e o host é conferido antes de cada chamada;
- não aceita URL nem versão por configuração — apontar este cliente para
  outro servidor teria de ser uma mudança de código revisada;
- não põe o token na querystring. Ele vai no cabeçalho ``Authorization``,
  então não existe URL com segredo para vazar em log, traceback ou métrica;
- **não renova o token.** Renovação é operação humana documentada: um
  processo web que escreve no próprio arquivo de segredos é superfície que
  esta missão não abre. Se um dia for automatizada, será unidade
  operacional separada — timer, escrita atômica, permissão e rollback;
- não escolhe por formato. Imagem, vídeo, carrossel e Reel valem igual: o
  critério é só o ``timestamp`` mais recente, e a ordenação é refeita aqui
  em vez de confiar na ordem que a Meta devolveu;
- não levanta exceção para quem chamou. Falha da Meta, token vencido,
  resposta estranha e timeout têm todos a mesma saída: nenhuma publicação,
  e o convite segue com o perfil. O operador não vê erro de integração.

O cache é de processo, em memória: a resposta é pública (um permalink), tem
dezenas de bytes e um TTL de minutos. Postgres ou Redis para isso seria
infraestrutura nova para guardar um dado que o Instagram já publica.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger("soprolife.instagram_feed")
# O httpx registra "HTTP Request: GET <url>" em INFO. Aqui a URL não carrega
# segredo (o token vai no cabeçalho), mas a regra do projeto é a mesma do
# SERPRO: nenhuma configuração de log do processo transforma uma chamada
# externa em linha de log não revisada.
for _nome in ("httpx", "httpcore"):
    logging.getLogger(_nome).setLevel(logging.WARNING)

ALLOWED_HOST = "graph.instagram.com"
# Versão fixada. v25.0 (18/02/2026) tem disponibilidade garantida pela Meta
# até 29/07/2028 e é a versão dos exemplos oficiais do Instagram Platform.
# Subir de versão é mudança de código, com a suíte rodando junto.
API_VERSION = "v25.0"
CAMPOS = "permalink,timestamp,media_type"
# Uma página curta basta: a mais recente está nela, e pedir mais seria
# baixar histórico que não vamos usar.
LIMITE_MEDIA = 12
# O corpo esperado tem poucos KiB; 64 KiB é folga ampla e impede que uma
# resposta anômala encha a memória do processo.
MAX_RESPONSE_BYTES = 64 * 1024

# O permalink devolvido pela Meta é conferido antes de entrar na mensagem do
# paciente. Não é desconfiança da Meta: é que esta string vai para o WhatsApp
# de uma pessoa, e o que vai para o paciente sai daqui com forma conhecida.
PERMALINK_HOSTS = frozenset({"www.instagram.com", "instagram.com"})
PERMALINK_PREFIXOS = ("/p/", "/reel/", "/reels/", "/tv/")
MAX_PERMALINK = 300

# Depois de uma falha sem nada em cache, segurar a mão antes de tentar de
# novo: sem isso, cada clique do operador viraria uma chamada que já se sabe
# que vai falhar (token vencido é o caso típico — falha por dias).
BACKOFF_SEGUNDOS = 300

# Teto de contingência: por quanto tempo o último permalink conhecido pode
# continuar sendo servido enquanto a Meta NÃO confirma nada.
#
# Isto NÃO é a idade da publicação — um post de três meses pode legitimamente
# ser o mais recente da conta, e divulgá-lo está correto. É há quanto tempo
# estamos sem conseguir CONFIRMAR o feed. Passados 7 dias sem uma única
# consulta bem sucedida (token vencido e não renovado é o caso real), não há
# mais base para afirmar o que a conta publica hoje, e o convite volta ao
# perfil — que não tem como ficar desatualizado.
MAX_CONTINGENCIA_SEGUNDOS = 7 * 24 * 3600

Fonte = Literal[
    "meta",             # veio agora da API oficial
    "cache",            # cache dentro do TTL
    "cache_vencido",    # Meta falhou; reserva ainda dentro do teto
    "desligado",        # sem credencial configurada — nunca houve chamada
    "nao_consultado",   # o convite já estava recusado; a Meta não foi tocada
    "indisponivel",     # Meta falhou e não há reserva utilizável
    "sem_publicacao",   # a conta respondeu, mas não há publicação válida
]


@dataclass(frozen=True)
class Publicacao:
    """O que a mensagem precisa saber. Nada além disso sai deste módulo."""

    permalink: str | None
    # `timestamp` da publicação, como a Meta informou (ISO 8601, UTC).
    publicado_em: str | None
    # Quando a Meta confirmou esta informação, em hora de parede — serve a
    # diagnóstico humano, NUNCA ao cálculo de validade (ver `_Entrada`).
    confirmado_em: datetime | None
    fonte: Fonte


SEM_PUBLICACAO = Publicacao(None, None, None, "desligado")
NAO_CONSULTADO = Publicacao(None, None, None, "nao_consultado")


# --------------------------------------------------------------- cache


@dataclass
class _Entrada:
    permalink: str
    publicado_em: str
    # Relógio MONOTÔNICO. Toda a aritmética de validade (TTL, contenção,
    # teto de contingência) usa este campo, nunca a hora de parede: um
    # ajuste de NTP, o horário de verão ou um `date -s` do operador podem
    # mover a hora de parede para trás e fariam um cache vencido parecer
    # novo — ou aposentariam um cache bom antes da hora. O monotônico só
    # anda para a frente e não tem epoch externo, que é exatamente o que um
    # cache de processo precisa.
    obtido_em: float
    # Hora de parede, só para o diagnóstico humano ("confirmado às 14h22").
    confirmado_em: datetime


_lock = threading.Lock()
_cache: _Entrada | None = None
_proxima_tentativa: float = 0.0


def reset_cache() -> None:
    """Zera o estado de processo. Usado pelos testes e pelo arranque."""

    global _cache, _proxima_tentativa
    with _lock:
        _cache = None
        _proxima_tentativa = 0.0


def estado_do_cache() -> dict[str, Any]:
    """Retrato do cache para diagnóstico. Sem token, sem cabeçalho, sem URL."""

    with _lock:
        if _cache is None:
            return {"tem_publicacao": False}
        return {
            "tem_publicacao": True,
            "permalink": _cache.permalink,
            "publicado_em": _cache.publicado_em,
            "confirmado_em": _cache.confirmado_em.isoformat(),
        }


# ------------------------------------------------------------ validação


def permalink_valido(valor: Any) -> str | None:
    """A URL da publicação, ou ``None``. Forma conhecida ou nada.

    Aceita ``/p/`` (imagem, vídeo, carrossel), ``/reel/`` e ``/tv/``. Não há
    preferência por formato: quem decide é o ``timestamp``. Esta função só
    decide se a string PODE ir para o WhatsApp de um paciente.
    """

    if not isinstance(valor, str):
        return None
    texto = valor.strip()
    if not texto or len(texto) > MAX_PERMALINK:
        return None
    partes = urlsplit(texto)
    if partes.scheme != "https" or partes.hostname not in PERMALINK_HOSTS:
        return None
    if partes.username or partes.password or partes.port:
        return None
    if partes.query or partes.fragment:
        return None
    caminho = partes.path
    if not caminho.startswith(PERMALINK_PREFIXOS):
        return None
    # O código curto da publicação: só o alfabeto que o Instagram usa.
    codigo = caminho.split("/")[2] if len(caminho.split("/")) > 2 else ""
    if not codigo or not all(c.isalnum() or c in "-_" for c in codigo):
        return None
    return texto


def timestamp_valido(valor: Any) -> datetime | None:
    """O ``timestamp`` da publicação como data, ou ``None``.

    A Meta devolve ISO 8601 em UTC, na grafia ``2026-10-13T12:00:00+0000``.
    `fromisoformat` dá conta dela no Python moderno; o `strptime` fica como
    rede de segurança caso a biblioteca mude de humor.
    """

    if not isinstance(valor, str):
        return None
    texto = valor.strip()
    if not texto:
        return None
    for tentativa in (
        datetime.fromisoformat,
        lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z"),
    ):
        try:
            data = tentativa(texto)
        except (ValueError, TypeError):
            continue
        # Sem fuso declarado, assume-se UTC: é o que a API promete devolver,
        # e uma data ingênua tornaria a comparação entre itens impossível.
        return data if data.tzinfo else data.replace(tzinfo=timezone.utc)
    return None


def _mais_recente(itens: list) -> tuple[str, str] | None:
    """A publicação cronologicamente mais recente — e só com prova disso.

    Duas regras, ambas deliberadas:

    - **sem `timestamp` legível, o item não concorre.** Pedimos `timestamp`
      explicitamente à API: um item que volta sem ele (ou com uma data que
      não se consegue ler) não oferece evidência de ser "o mais recente", e
      chamá-lo assim seria afirmar o que não se sabe. Se NENHUM item tiver
      data legível, o resultado é "não há publicação" e a ordem normal de
      fallback assume;
    - **a ordenação é refeita aqui.** A Meta devolve em ordem reversa de
      criação, mas "devolve hoje" não é contrato; ordenar pela data que ela
      mesma informou custa nada e torna a escolha verificável. Comparar
      `datetime` em vez de texto também resolve fusos diferentes entre itens.
    """

    candidatos: list[tuple[datetime, str, str]] = []
    for item in itens[:LIMITE_MEDIA]:
        if not isinstance(item, dict):
            continue
        permalink = permalink_valido(item.get("permalink"))
        if permalink is None:
            continue
        bruto = item.get("timestamp")
        quando = timestamp_valido(bruto)
        if quando is None:
            continue
        candidatos.append((quando, permalink, bruto.strip()))
    if not candidatos:
        return None
    _, permalink, publicado_em = max(candidatos, key=lambda c: c[0])
    return permalink, publicado_em


# -------------------------------------------------------------- rede


def _buscar(
    settings, transport: httpx.BaseTransport | None
) -> tuple[bool, tuple[str, str] | None]:
    """Uma chamada à Graph API: ``(a conta respondeu, a publicação)``.

    As duas informações são distintas e levam a desfechos diferentes. "A
    Meta falhou" preserva o último permalink conhecido; "a conta respondeu
    e não tem publicação utilizável" o DESCARTA — insistir num permalink
    que a conta não lista mais é o caminho para mandar ao paciente o link
    de uma publicação apagada.

    Nenhuma exceção escapa: este módulo é fail-safe por contrato.
    """

    alvo = settings.instagram_user_id or "me"
    url = f"https://{ALLOWED_HOST}/{API_VERSION}/{alvo}/media"
    # Defesa em profundidade: o host é constante, mas a conferência garante
    # que nenhuma edição futura mande o token para outro servidor.
    if urlsplit(url).hostname != ALLOWED_HOST:
        logger.error("instagram_feed: host inesperado; chamada abortada")
        return False, None
    token = settings.instagram_access_token.get_secret_value().strip()
    try:
        with httpx.Client(
            timeout=settings.instagram_feed_timeout_seconds,
            transport=transport,
            follow_redirects=False,
        ) as cliente:
            resposta = cliente.get(
                url,
                params={"fields": CAMPOS, "limit": LIMITE_MEDIA},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
    except httpx.HTTPError as erro:
        # Só a CLASSE do erro. A mensagem do httpx pode trazer a URL, e a
        # regra do projeto é que linha de log de integração não carregue
        # nada que não tenha sido escolhido para estar ali.
        logger.warning("instagram_feed: falha de rede (%s)", type(erro).__name__)
        return False, None
    if resposta.status_code != 200:
        # Só o status. 401/403 = token vencido ou escopo removido; 429 =
        # limite de uso. Nenhum corpo de resposta entra no log.
        logger.warning("instagram_feed: HTTP %s", resposta.status_code)
        return False, None
    conteudo = resposta.content[: MAX_RESPONSE_BYTES + 1]
    if len(conteudo) > MAX_RESPONSE_BYTES:
        logger.warning("instagram_feed: resposta acima do limite")
        return False, None
    try:
        corpo = resposta.json()
    except ValueError:
        logger.warning("instagram_feed: corpo não é JSON")
        return False, None
    if not isinstance(corpo, dict):
        logger.warning("instagram_feed: corpo com forma inesperada")
        return False, None
    dados = corpo.get("data")
    if not isinstance(dados, list):
        # Resposta bem formada o bastante para virar dict, mas sem a lista
        # que o contrato promete. Isso é "a chamada NÃO funcionou", não "a
        # conta não tem publicação": devolver `respondeu=True` aqui faria o
        # desfecho `sem_publicacao` apagar o cache e trocar um permalink bom
        # por um corpo que ninguém entendeu. Só uma lista VAZIA de verdade
        # descarta o cache.
        logger.warning("instagram_feed: campo `data` ausente ou com forma inesperada")
        return False, None
    return True, _mais_recente(dados)


# ------------------------------------------------------------- fachada


def ultima_publicacao(
    settings,
    *,
    agora: float | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Publicacao:
    """A publicação mais recente — ou nada, e nunca um erro.

    `agora` é um instante do relógio MONOTÔNICO (`time.monotonic`), não hora
    de parede: toda a validade do cache é medida nele. Os testes injetam
    esse valor para andar com o relógio sem dormir.

    A ordem, na prática:

    1. sem credencial → ``desligado``, sem tocar a rede;
    2. cache dentro do TTL → ``cache``;
    3. consulta à Meta → ``meta`` (e o cache passa a valer);
    4. Meta falhou e há reserva com menos de 7 dias → ``cache_vencido``;
    5. Meta falhou e não há reserva utilizável → ``indisponivel``.

    Em 1, 5 e ``sem_publicacao`` o ``permalink`` é ``None``, e quem chamou
    monta a mensagem do perfil. Essa é a única decisão que o chamador toma.
    """

    global _cache, _proxima_tentativa

    if not settings.instagram_feed_ready():
        return SEM_PUBLICACAO

    agora = time.monotonic() if agora is None else agora
    ttl = settings.instagram_feed_cache_minutes * 60

    with _lock:
        entrada = _cache
        espera = _proxima_tentativa
    if entrada is not None and agora - entrada.obtido_em < ttl:
        return Publicacao(
            entrada.permalink, entrada.publicado_em, entrada.confirmado_em, "cache"
        )
    if agora < espera:
        # Ainda no intervalo de contenção de uma falha recente.
        return _apos_falha(entrada, agora)

    # A chamada sai FORA do lock, de propósito. Segurá-lo durante uma
    # requisição de alguns segundos serializaria todos os cliques do painel
    # atrás de uma rede lenta. O preço é que dois cliques simultâneos com o
    # cache frio podem gerar duas chamadas — um GET idempotente a mais, uma
    # vez a cada 45 minutos no pior caso.
    respondeu, achado = _buscar(settings, transport)
    if not respondeu:
        with _lock:
            _proxima_tentativa = agora + BACKOFF_SEGUNDOS
        return _apos_falha(entrada, agora)
    if achado is None:
        # A conta respondeu e não tem publicação utilizável. O cache sai de
        # cena: ele guardava um permalink que esta resposta não confirma.
        with _lock:
            _cache = None
            _proxima_tentativa = agora + BACKOFF_SEGUNDOS
        return Publicacao(None, None, None, "sem_publicacao")

    permalink, publicado_em = achado
    confirmado_em = datetime.now(timezone.utc)
    nova = _Entrada(permalink, publicado_em, agora, confirmado_em)
    with _lock:
        _cache = nova
        _proxima_tentativa = 0.0
    return Publicacao(permalink, publicado_em, confirmado_em, "meta")


def _apos_falha(entrada: _Entrada | None, agora: float) -> Publicacao:
    """Degradação com prazo: a reserva vale, mas não para sempre.

    Um permalink do Instagram não caduca — a publicação continua lá —, então
    servir o último conhecido depois de a API falhar é melhor que cair no
    perfil. Por isso o cache vencido não é descartado ao expirar: ele deixa
    de ser "fresco" e vira "reserva".

    O que tem prazo é a nossa CONFIRMAÇÃO. Passados
    `MAX_CONTINGENCIA_SEGUNDOS` sem uma única consulta bem sucedida, não há
    mais base para afirmar que aquela é a publicação mais recente da conta,
    e o convite volta ao perfil — que não tem como ficar desatualizado.
    """

    if entrada is not None and agora - entrada.obtido_em <= MAX_CONTINGENCIA_SEGUNDOS:
        return Publicacao(
            entrada.permalink,
            entrada.publicado_em,
            entrada.confirmado_em,
            "cache_vencido",
        )
    return Publicacao(None, None, None, "indisponivel")
