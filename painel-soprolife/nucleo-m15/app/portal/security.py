"""Cabeçalhos, cookie e limitador do portal público.

Tudo aqui existe para uma pergunta: o que este processo entrega a um
navegador hostil? A resposta tem de caber em poucas linhas revisáveis.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time

from fastapi import Request, Response

from ..config import get_settings

COOKIE_PATH = "/p/v1"

# Política de conteúdo para RESPOSTAS DE API. A página do paciente é
# estática e mora noutro domínio (GitHub Pages); aqui só saem JSON e PDF, e
# nenhum dos dois precisa de script, estilo, fonte ou frame.
CABECALHOS_SEGUROS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Resultado de exame não se indexa. O cabeçalho vale inclusive para os
    # PDFs, onde uma meta tag de HTML não teria como existir.
    "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'none'"
    ),
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    "Cross-Origin-Resource-Policy": "same-site",
}


def aplicar_cabecalhos(response: Response) -> Response:
    for chave, valor in CABECALHOS_SEGUROS.items():
        response.headers[chave] = valor
    return response


def origem_autorizada(request: Request) -> str | None:
    """A origem do pedido, SE ela estiver na lista explícita — senão, nada.

    Comparação exata contra `M15_PORTAL_CORS_ORIGINS`. Não há prefixo, não há
    sufixo, não há expressão regular: `https://soprolife.com.br.atacante.tld`
    e `https://soprolife.com.br:8443` são origens diferentes e nenhuma das
    duas passa. Refletir a origem recebida sem conferir é o erro clássico
    aqui — vale exatamente tanto quanto `*`, e ainda funciona com credenciais.
    """

    origem = request.headers.get("origin")
    if not origem:
        return None
    return origem if origem in get_settings().portal_cors_origins else None


def aplicar_cors(request: Request, response: Response) -> Response:
    """CORS em resposta que NÃO passou pelo `CORSMiddleware`.

    M26.7. O `CORSMiddleware` é o middleware mais interno do portal: ele só
    vê o que sai pelo caminho feliz das rotas. Uma resposta nascida acima
    dele — o 404 da fronteira pública, o 500 de uma exceção não tratada —
    saía sem `Access-Control-Allow-Origin`. Para o navegador isso não é um
    erro HTTP: é uma falha de rede. O `fetch` rejeita, o `catch` do
    frontend dispara, e o paciente lê "verifique sua internet" enquanto o
    servidor respondeu 500 — foi exatamente o que aconteceu no teste real
    da M26.6, e escondeu o defeito de permissão por trás de um diagnóstico
    falso.

    Idempotente: se o `CORSMiddleware` já resolveu, nada é sobrescrito.
    """

    vary = response.headers.get("Vary")
    if not vary:
        response.headers["Vary"] = "Origin"
    elif "origin" not in vary.lower():
        response.headers["Vary"] = f"{vary}, Origin"

    origem = origem_autorizada(request)
    if origem is None:
        # Origem ausente ou não autorizada: a resposta sai SEM cabeçalho de
        # CORS nenhum. O navegador de quem tentou é quem barra a leitura.
        return response
    response.headers.setdefault("Access-Control-Allow-Origin", origem)
    response.headers.setdefault("Access-Control-Allow-Credentials", "true")
    return response


def resposta_publica(request: Request, response: Response) -> Response:
    """Todo byte que sai do portal passa por aqui: segurança + CORS."""

    return aplicar_cors(request, aplicar_cabecalhos(response))


def _assinar(payload: str) -> str:
    import base64

    segredo = get_settings().resolved_portal_session_secret().encode()
    assinatura = hmac.new(segredo, payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(assinatura).decode().rstrip("=")


def montar_cookie(sessao_id: str, segredo: str) -> str:
    payload = f"{sessao_id}.{segredo}"
    return f"{payload}.{_assinar(payload)}"


def ler_cookie(bruto: str | None) -> tuple[str, str] | None:
    """Valida a assinatura ANTES de tocar no banco.

    A assinatura usa `M15_PORTAL_SESSION_SECRET`, que é obrigatoriamente
    diferente de `M15_AUTH_SECRET`. Por isso um cookie do painel
    administrativo não vira atalho aqui nem por acidente nem de propósito: a
    assinatura simplesmente não fecha.
    """

    partes = (bruto or "").split(".")
    if len(partes) != 3:
        return None
    sessao_id, segredo, assinatura = partes
    if not sessao_id or not segredo:
        return None
    if not hmac.compare_digest(assinatura, _assinar(f"{sessao_id}.{segredo}")):
        return None
    return sessao_id, segredo


def definir_cookie(response: Response, valor: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.portal_cookie_name,
        value=valor,
        max_age=settings.portal_session_ttl_minutes * 60,
        path=COOKIE_PATH,
        secure=settings.portal_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def limpar_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.portal_cookie_name,
        path=COOKIE_PATH,
        secure=settings.portal_cookie_secure,
        httponly=True,
        samesite="strict",
    )


class LimitadorPorOrigem:
    """Freio contra varredura de token, por origem de rede.

    O contador por ACESSO (na tabela) protege a data de nascimento de quem
    já tem um link válido. Este protege o caso anterior: alguém chutando
    tokens, para quem não existe linha nenhuma a incrementar.

    Guarda `sha256(ip)[:16]`, nunca o endereço. Um IP em memória de processo
    é dado pessoal de baixa utilidade e alto incômodo; o hash serve
    igualmente para contar.
    """

    def __init__(self, maximo: int = 20, janela_segundos: int = 300):
        self.maximo = maximo
        self.janela = janela_segundos
        self._tentativas: dict[str, list[float]] = {}
        self._trava = threading.Lock()

    @staticmethod
    def _chave(origem: str) -> str:
        return hashlib.sha256(origem.encode()).hexdigest()[:16]

    def bloqueado(self, origem: str) -> bool:
        chave = self._chave(origem)
        agora = time.time()
        with self._trava:
            recentes = [
                t for t in self._tentativas.get(chave, []) if agora - t < self.janela
            ]
            self._tentativas[chave] = recentes
            return len(recentes) >= self.maximo

    def registrar_falha(self, origem: str) -> None:
        chave = self._chave(origem)
        with self._trava:
            self._tentativas.setdefault(chave, []).append(time.time())

    def limpar(self) -> None:
        with self._trava:
            self._tentativas.clear()


limitador = LimitadorPorOrigem()


def origem_da_requisicao(request: Request) -> str:
    """Endereço de rede para fins de limite, nunca persistido.

    O processo escuta só em loopback e recebe tráfego exclusivamente do
    nginx local, então o primeiro salto de `X-Forwarded-For` é confiável
    aqui — e sem ele todo mundo viraria "127.0.0.1" e o limite não valeria
    nada.
    """

    encaminhado = request.headers.get("X-Forwarded-For", "")
    if encaminhado:
        primeiro = encaminhado.split(",")[0].strip()
        if primeiro:
            return primeiro[:64]
    cliente = request.client
    return cliente.host if cliente else "desconhecido"
