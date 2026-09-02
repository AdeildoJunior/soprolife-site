"""Entrypoint do portal público:  python -m app.portal_serve

Mesmas regras do `app.serve`: host e porta vêm das Settings (em prod só
loopback é aceito) e o access log do uvicorn fica DESLIGADO. Aqui isso é
ainda mais literal do que no painel — a linha de request do portal contém o
caminho do documento, e um log de acesso é exatamente o lugar onde um token
não pode acabar. O token, aliás, viaja no fragmento da URL e nem chega ao
servidor; desligar o log fecha o resto.
"""

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.portal.main:app",
        host=settings.portal_api_host,
        port=settings.portal_api_port,
        access_log=False,
        log_level="warning" if settings.env == "prod" else "info",
        # O portal está na internet: uma requisição que não termina é uma
        # conexão presa, e um cabeçalho gigante é um vetor barato.
        timeout_keep_alive=15,
        h11_max_incomplete_event_size=16 * 1024,
    )


if __name__ == "__main__":
    main()
