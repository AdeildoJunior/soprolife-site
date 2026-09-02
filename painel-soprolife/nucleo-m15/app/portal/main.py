"""A aplicação PÚBLICA. Separada do Command Center por construção.

Não é o mesmo processo com uma rota a mais: é outro app ASGI, outro serviço
systemd, outra porta, outro cookie e outro segredo de assinatura. A razão é
simples de enunciar e cara de esquecer — *o que não está montado não pode
vazar*. Um `include_router` errado no app do painel exporia CRM e financeiro
na internet; aqui não existe router administrativo para incluir por engano.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from ..config import get_settings
from ..errors import install_error_handling
from .routes import router
from .security import aplicar_cabecalhos


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

    @app.middleware("http")
    async def cabecalhos_de_seguranca(request, call_next):
        return aplicar_cabecalhos(await call_next(request))

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
    app.include_router(router)
    return app


app = create_portal_app()
