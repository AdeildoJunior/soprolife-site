"""Aplicação FastAPI do Núcleo Operacional Nativo M15.

Execução local oficial (nunca expor publicamente):
  python -m app.serve
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import get_settings
from .errors import install_error_handling
from .routers import (
    admin_users,
    attendances,
    auth,
    crm,
    finance,
    followups,
    health,
    imports_audit,
    marketing,
    operations,
    partners,
    people,
)


def create_app() -> FastAPI:
    settings = get_settings()
    # fail-closed: valida o segredo já na subida em prod
    settings.resolved_auth_secret()
    app = FastAPI(
        title="SoproLife Núcleo Operacional M15",
        version=__version__,
        docs_url="/docs" if settings.env == "dev" else None,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    install_error_handling(app)
    prefix = "/api/v1"
    app.include_router(health.router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(people.router, prefix=prefix)
    app.include_router(operations.router, prefix=prefix)
    app.include_router(attendances.router, prefix=prefix)
    app.include_router(partners.router, prefix=prefix)
    app.include_router(followups.router, prefix=prefix)
    app.include_router(crm.router, prefix=prefix)
    app.include_router(marketing.router, prefix=prefix)
    app.include_router(finance.router, prefix=prefix)
    app.include_router(imports_audit.router, prefix=prefix)
    app.include_router(admin_users.router, prefix=prefix)
    return app


app = create_app()
