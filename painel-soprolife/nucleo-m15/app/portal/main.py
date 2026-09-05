"""A aplicação PÚBLICA. Separada do Command Center por construção.

Não é o mesmo processo com uma rota a mais: é outro app ASGI, outro serviço
systemd, outra porta, outro cookie e outro segredo de assinatura. A razão é
simples de enunciar e cara de esquecer — *o que não está montado não pode
vazar*. Um `include_router` errado no app do painel exporia CRM e financeiro
na internet; aqui não existe router administrativo para incluir por engano.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from ..config import get_settings
from ..errors import install_error_handling
from .routes import PREFIXO_PUBLICO, router
from .security import resposta_publica

logger = logging.getLogger(__name__)

# O que o paciente lê quando o servidor falha. Nenhum detalhe interno, nenhum
# convite a "consultar os logs" — quem está do outro lado é uma pessoa
# esperando o resultado de um exame, não um operador.
MENSAGEM_ERRO_INTERNO = (
    "Não foi possível acessar o resultado agora. Tente novamente em alguns "
    "instantes."
)


def erro_interno(request: Request) -> JSONResponse:
    """O 500 do portal: mesmo envelope dos outros erros, conteúdo genérico.

    O `request_id` vai junto de propósito. Ele não diz nada a quem recebe e
    diz tudo a quem investiga: é a chave que liga esta resposta à linha do
    journal com o traceback completo.
    """

    return JSONResponse(
        status_code=500,
        content={
            "erro": {
                "codigo": "interno",
                "mensagem": MENSAGEM_ERRO_INTERNO,
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


def create_portal_app() -> FastAPI:
    settings = get_settings()
    # Fail-closed na subida: um portal habilitado sem segredo de sessão ou
    # sem endereço público não sobe pela metade — não sobe.
    if settings.portal_enabled:
        settings.resolved_portal_session_secret()
        if not settings.portal_public_base_url:
            raise ValueError(
                "M15_PORTAL_PUBLIC_BASE_URL é obrigatório com o portal "
                "habilitado."
            )

    app = FastAPI(
        title="SoproLife — Resultados de exames",
        version=__version__,
        # Nunca, em ambiente nenhum. Um /docs público é um índice do que
        # existe para tentar; e o que existe aqui já cabe em cinco linhas de
        # documentação escrita à mão.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # A ORDEM importa, e não é a intuitiva: no Starlette o middleware
    # adicionado por ÚLTIMO é o MAIS EXTERNO. Por isso o CORS entra
    # primeiro, o tratamento de erro depois, e a fronteira pública por
    # último — ela precisa envolver todo o resto.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.portal_cors_origins,
        # O cookie do paciente precisa atravessar a chamada do site
        # institucional para a API. `allow_origins` é explícito e sem '*',
        # que é o que torna credenciais aceitáveis aqui.
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        max_age=600,
    )
    install_error_handling(app)

    @app.middleware("http")
    async def fronteira_publica(request, call_next):
        """Duas garantias que só valem se esta camada for a mais externa.

        **1. Fora de `/p/v1/` não existe nada.** O `install_error_handling`
        é compartilhado com o Command Center e responde cedo, antes das
        rotas, a qualquer caminho começado em `/api/v1/laudos` — em
        produção o portal devolvia ali um `relatorios_desabilitados` 503.
        A resposta era inofensiva em conteúdo e péssima em forma: um
        caminho respondendo DIFERENTE dos outros conta a quem varre que
        esta máquina tem algo a ver com um sistema de laudos. Agora tudo
        que não começa em `/p/v1/` é o mesmo 404 seco.

        **2. Todo byte que sai leva os cabeçalhos de segurança.** Enquanto
        esta camada era a mais interna, qualquer resposta produzida por uma
        camada acima saía sem `Cache-Control: no-store`, sem
        `X-Robots-Tag`, sem CSP — foi exatamente o que aconteceu com aquele
        503. Cabeçalho de segurança que depende de a resposta ter vindo
        pelo caminho feliz não é garantia, é coincidência.
        """

        if not request.url.path.startswith(PREFIXO_PUBLICO):
            return resposta_publica(
                request,
                JSONResponse({"erro": {"codigo": "nao_encontrado"}}, status_code=404),
            )
        try:
            resposta = await call_next(request)
        except Exception:
            # M26.7 — a exceção PRECISA morrer aqui dentro.
            #
            # Deixá-la subir entrega o 500 ao `ServerErrorMiddleware`, que no
            # Starlette é sempre a camada mais externa: acima do CORS, acima
            # desta. A resposta dele sai sem `Access-Control-Allow-Origin` e
            # sem cabeçalho de segurança nenhum — e o navegador do paciente
            # classifica isso como falha de rede, não como erro do servidor.
            #
            # O traceback continua indo para o journal, e é ele que permitiu
            # descobrir o defeito de permissão na M26.6. `logger.exception`
            # aqui não é decoração: sem ele, engolir a exceção trocaria um
            # diagnóstico ruim por nenhum diagnóstico.
            logger.exception(
                "erro interno no portal (request_id=%s, caminho=%s)",
                getattr(request.state, "request_id", None),
                request.url.path,
            )
            resposta = erro_interno(request)
        return resposta_publica(request, resposta)

    # Rede de segurança para o que escapar da fronteira — uma exceção na
    # própria fronteira, por exemplo. Sobrepõe o handler genérico que o
    # `install_error_handling` registra para o Command Center, cuja mensagem
    # ("consulte os logs pelo request_id") é escrita para um operador.
    async def erro_nao_tratado(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "erro interno escapou da fronteira do portal (request_id=%s)",
            getattr(request.state, "request_id", None),
        )
        return resposta_publica(request, erro_interno(request))

    app.add_exception_handler(Exception, erro_nao_tratado)

    app.include_router(router)
    return app


app = create_portal_app()
