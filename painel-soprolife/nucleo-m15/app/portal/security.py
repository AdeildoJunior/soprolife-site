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
