"""Entrypoint oficial da API: impõe M15_API_HOST/M15_API_PORT e logs sem PII.

Uso:  python -m app.serve

- host/port vêm SEMPRE das Settings (nunca de argumento solto de uvicorn);
- em prod, Settings já rejeita bind não-loopback sem consentimento explícito;
- access log do uvicorn fica DESLIGADO: a request line conteria querystrings
  e cabeçalhos — logs de acesso não podem carregar PII nem tokens.
"""

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()  # valida env/segredo/host/CORS (fail-closed)
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        access_log=False,
        log_level="warning" if settings.env == "prod" else "info",
    )


if __name__ == "__main__":
    main()
