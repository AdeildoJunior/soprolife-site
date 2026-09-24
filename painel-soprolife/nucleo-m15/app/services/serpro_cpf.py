"""M68 — cliente da Consulta CPF v3 (SERPRO / Receita Federal).

Contrato oficial (OpenAPI `consulta-cpf-df-v3`, apicenter.estaleiro.serpro.gov.br):

- token: ``POST https://gateway.apiserpro.serpro.gov.br/token`` com
  ``Authorization: Basic base64(ConsumerKey:ConsumerSecret)`` e
  ``grant_type=client_credentials``; Bearer válido por ~1 h, renovado quando
  o gateway devolve 401;
- consulta: ``GET https://gateway.apiserpro.serpro.gov.br/consulta-cpf-df/v3/cpf/{ni}/{nasc}``,
  nascimento em ``ddmmaaaa``. CPF e nascimento viajam no PATH por exigência
  do próprio SERPRO — servidor a servidor, nunca registrados aqui;
- retorno: ``ni``, ``nome``, ``situacao{codigo,descricao}``, ``nascimento``,
  ``dataInscricao``, ``nomeSocial``; 206 = conteúdo parcial.

O que este módulo devolve é o MÍNIMO que a tela usa (nome, nome social,
situação). ``ni`` e ``dataInscricao`` são lidos e descartados. Nada do corpo
bruto sai daqui, e nenhuma exceção carrega texto de resposta, URL ou token.

Não há URL configurável: o endereço de produção é constante, e o host é
conferido antes de cada requisição. A versão de demonstração
(``consulta-cpf-df-trial``) não é alcançável por configuração.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable, Literal
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger("soprolife.serpro_cpf")
# O httpx registra "HTTP Request: GET <url>" em INFO — e a URL desta API
# carrega CPF e nascimento. Nenhuma configuração de log do processo pode
# fazer essa linha existir.
for _nome in ("httpx", "httpcore"):
    logging.getLogger(_nome).setLevel(logging.WARNING)

ALLOWED_HOST = "gateway.apiserpro.serpro.gov.br"
TOKEN_URL = f"https://{ALLOWED_HOST}/token"
CONSULTA_BASE_URL = f"https://{ALLOWED_HOST}/consulta-cpf-df/v3"

# O corpo esperado tem poucas centenas de bytes; 16 KiB é folga ampla e
# impede que uma resposta anômala encha a memória do processo.
MAX_RESPONSE_BYTES = 16 * 1024
# Margem antes do vencimento oficial do token, e teto absoluto de cache.
TOKEN_MARGEM_SEGUNDOS = 300
TOKEN_TTL_MAXIMO = 3600
TOKEN_TTL_MINIMO = 60
_MAX_CAMPO = 200

Resultado = Literal[
    "confere",          # CPF + nascimento correspondem; nome oficial devolvido
    "nao_confere",      # 404, ou nascimento devolvido diferente do enviado
    "dados_recusados",  # 400: o SERPRO recusou os dados informados
    "protegido",        # 422/451 (LGPD, menor de idade) — sem dados
    "indisponivel",     # rede, timeout, 401/403 persistentes, 5xx, corpo ruim
]


class SerproCpfError(RuntimeError):
    """Falha técnica sem detalhe: nunca carrega corpo, URL, CPF ou token."""


@dataclass(frozen=True)
class ConsultaCpf:
    resultado: Resultado
    nome: str | None = None
    nome_social: str | None = None
    situacao_codigo: str | None = None
    situacao_descricao: str | None = None
    parcial: bool = False
    # Só o status HTTP (nunca corpo) — para o log técnico e os testes.
    http_status: int | None = None


def nascimento_ddmmaaaa(nascimento: date) -> str:
    """``date(1970, 11, 14)`` → ``"14111970"`` — formato exigido no path."""

    return nascimento.strftime("%d%m%Y")


def _texto(valor, limite: int = _MAX_CAMPO) -> str | None:
    if not isinstance(valor, str):
        return None
    limpo = " ".join(valor.split())
    return limpo[:limite] or None


class SerproCpfClient:
    """Cliente fino: token em memória, sem retry cego, host fixo."""

    def __init__(
        self,
        *,
        consumer_key: str,
        consumer_secret: str,
        timeout_seconds: float = 8.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not consumer_key or not consumer_secret:
            raise SerproCpfError("credenciais ausentes")
        credencial = f"{consumer_key}:{consumer_secret}".encode("utf-8")
        # Guardado já em Basic: nem a chave nem o segredo ficam soltos como
        # atributos com nome óbvio, e nada disto aparece em repr().
        self._basic = "Basic " + base64.b64encode(credencial).decode("ascii")
        self._timeout = timeout_seconds
        # Injetável só para teste; em produção o httpx usa a rede de verdade.
        self._transport = transport
        self._clock = clock
        self._token: str | None = None
        self._token_expira_em = 0.0
        self._lock = threading.Lock()

    def __repr__(self) -> str:  # nunca expor credencial/token por acidente
        return "SerproCpfClient(<credenciais ocultas>)"

    # ------------------------------------------------------------ transporte

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self._timeout,
            transport=self._transport,
            follow_redirects=False,
        )

    @staticmethod
    def _conferir_host(url: str) -> None:
        partes = urlsplit(url)
        if partes.scheme != "https" or partes.hostname != ALLOWED_HOST:
            raise SerproCpfError("host fora da allowlist")

    def _requisitar(self, method: str, url: str, **kwargs) -> tuple[int, bytes]:
        self._conferir_host(url)
        try:
            with self._client() as client:
                with client.stream(method, url, **kwargs) as response:
                    corpo = bytearray()
                    for bloco in response.iter_bytes():
                        corpo.extend(bloco)
                        if len(corpo) > MAX_RESPONSE_BYTES:
                            raise SerproCpfError("resposta acima do limite")
                    return response.status_code, bytes(corpo)
        except httpx.HTTPError:
            # O texto da exceção do httpx costuma conter a URL — que aqui
            # carrega CPF e nascimento. Nunca propagar.
            raise SerproCpfError("falha de comunicação") from None

    # ------------------------------------------------------------ token

    def _obter_token(self, *, renovar: bool = False) -> str:
        with self._lock:
            agora = self._clock()
            if not renovar and self._token and agora < self._token_expira_em:
                return self._token
            status, corpo = self._requisitar(
                "POST",
                TOKEN_URL,
                headers={
                    "Authorization": self._basic,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                content=b"grant_type=client_credentials",
            )
            if status != 200:
                self._token = None
                raise SerproCpfError(f"token recusado (http {status})")
            try:
                dados = json.loads(corpo)
            except ValueError:
                raise SerproCpfError("token malformado") from None
            token = dados.get("access_token") if isinstance(dados, dict) else None
            if not isinstance(token, str) or not token.strip():
                raise SerproCpfError("token ausente")
            try:
                expira = int(dados.get("expires_in", TOKEN_TTL_MAXIMO))
            except (TypeError, ValueError):
                expira = TOKEN_TTL_MAXIMO
            vida = min(expira, TOKEN_TTL_MAXIMO) - TOKEN_MARGEM_SEGUNDOS
            self._token = token.strip()
            self._token_expira_em = agora + max(vida, TOKEN_TTL_MINIMO)
            return self._token

    def _descartar_token(self) -> None:
        with self._lock:
            self._token = None
            self._token_expira_em = 0.0

    # ------------------------------------------------------------ consulta

    def consultar(self, cpf: str, nascimento: date) -> ConsultaCpf:
        """Uma consulta. ``cpf`` já normalizado (11 dígitos) pelo chamador.

        Única repetição: um 401 na consulta descarta o token e tenta UMA vez
        com token novo — é o procedimento documentado de renovação, não um
        retry cego. 5xx, 504 e timeout não são repetidos (a consulta é
        bilhetável e o operador pode seguir no manual).
        """

        if len(cpf) != 11 or not cpf.isdigit():
            raise ValueError("cpf deve chegar normalizado")
        nasc = nascimento_ddmmaaaa(nascimento)
        url = f"{CONSULTA_BASE_URL}/cpf/{cpf}/{nasc}"
        try:
            status, corpo = self._consultar_uma_vez(url, renovar=False)
            if status == 401:
                self._descartar_token()
                status, corpo = self._consultar_uma_vez(url, renovar=True)
        except SerproCpfError:
            logger.warning("serpro_cpf: consulta indisponível (falha técnica)")
            return ConsultaCpf("indisponivel")
        return self._interpretar(status, corpo, nasc)

    def _consultar_uma_vez(self, url: str, *, renovar: bool) -> tuple[int, bytes]:
        token = self._obter_token(renovar=renovar)
        return self._requisitar(
            "GET",
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    @staticmethod
    def _interpretar(status: int, corpo: bytes, nasc_enviado: str) -> ConsultaCpf:
        if status == 404:
            return ConsultaCpf("nao_confere", http_status=status)
        if status == 400:
            return ConsultaCpf("dados_recusados", http_status=status)
        if status in (422, 451):
            return ConsultaCpf("protegido", http_status=status)
        if status not in (200, 206):
            logger.warning("serpro_cpf: consulta indisponível (http %s)", status)
            return ConsultaCpf("indisponivel", http_status=status)
        try:
            dados = json.loads(corpo) if corpo else None
        except ValueError:
            dados = None
        if not isinstance(dados, dict):
            logger.warning("serpro_cpf: corpo ilegível (http %s)", status)
            return ConsultaCpf("indisponivel", http_status=status)
        # Defesa: se o SERPRO devolver um nascimento, ele TEM de ser o
        # enviado. Diferente é não correspondência, nunca "quase".
        nasc_devolvido = dados.get("nascimento")
        if isinstance(nasc_devolvido, str) and nasc_devolvido.strip() \
                and nasc_devolvido.strip() != nasc_enviado:
            return ConsultaCpf("nao_confere", http_status=status)
        nome = _texto(dados.get("nome"))
        if not nome:
            # 206 sem nome não serve para preencher nada.
            return ConsultaCpf("indisponivel", parcial=status == 206, http_status=status)
        situacao = dados.get("situacao") if isinstance(dados.get("situacao"), dict) else {}
        return ConsultaCpf(
            "confere",
            nome=nome,
            nome_social=_texto(dados.get("nomeSocial")),
            situacao_codigo=_texto(situacao.get("codigo"), 10),
            situacao_descricao=_texto(situacao.get("descricao"), 80),
            parcial=status == 206,
            http_status=status,
        )
