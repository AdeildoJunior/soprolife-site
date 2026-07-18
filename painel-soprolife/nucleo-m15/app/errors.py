"""Tratamento consistente de erros + identificador de requisição validado."""

import re
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

# request_id aceito do cliente: curto, formato seguro; qualquer outra coisa
# é substituída por um id gerado (evita log/coluna estourada e injeção).
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def resolve_request_id(raw: str | None) -> str:
    if raw and REQUEST_ID_RE.fullmatch(raw):
        return raw
    return _new_request_id()


def _envelope(request: Request, status: int, codigo: str, mensagem, extra: dict | None = None):
    body = {
        "erro": {
            "codigo": codigo,
            "mensagem": mensagem,
            "request_id": getattr(request.state, "request_id", None),
        }
    }
    if extra:
        body["erro"].update(extra)
    return JSONResponse(status_code=status, content=body)


def install_error_handling(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = resolve_request_id(request.headers.get("X-Request-ID"))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        return _envelope(request, exc.status_code, f"http_{exc.status_code}", exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_exc(request: Request, exc: RequestValidationError):
        # fail-closed: entrada inválida nunca é aceita parcialmente
        erros = [
            {"campo": ".".join(str(p) for p in e["loc"]), "tipo": e["type"]}
            for e in exc.errors()
        ]
        return _envelope(request, 422, "validacao", "Payload inválido.", {"campos": erros})

    @app.exception_handler(IntegrityError)
    async def integrity_exc(request: Request, exc: IntegrityError):
        # conflito de unicidade/constraint conhecido nunca vira 500
        return _envelope(
            request, 409, "conflito",
            "Conflito de unicidade ou integridade — a operação não foi aplicada.",
        )

    @app.exception_handler(Exception)
    async def unhandled_exc(request: Request, exc: Exception):
        # nunca vazar detalhes internos (nem dados, nem stack trace) na resposta
        return _envelope(request, 500, "interno", "Erro interno. Consulte os logs pelo request_id.")
