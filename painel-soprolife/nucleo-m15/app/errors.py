"""Tratamento consistente de erros + identificador de requisição validado."""

import re
import uuid

from fastapi import FastAPI, HTTPException as FastAPIHTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .field_labels import (
    caminho_normalizado,
    mensagem_de_dominio,
    motivo_do_tipo,
    rotulo_do_campo,
)

# request_id aceito do cliente: curto, formato seguro; qualquer outra coisa
# é substituída por um id gerado (evita log/coluna estourada e injeção).
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
REPORT_UPLOAD_MULTIPART_OVERHEAD_BYTES = 64 * 1024


class ReportDomainError(FastAPIHTTPException):
    """Erro M24A seguro: código de máquina + mensagem humana sempre textual."""

    def __init__(self, status_code: int, codigo: str, mensagem: str):
        self.status_code = status_code
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(status_code=status_code, detail=mensagem)


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
        if request.url.path.startswith("/api/v1/laudos"):
            # Esta borda roda antes do parser multipart. Assim, feature
            # desabilitada e Content-Length obviamente excessivo não fazem o
            # servidor materializar o upload antes de recusá-lo.
            from .config import get_settings

            settings = get_settings()
            if not settings.reports_enabled:
                return _envelope(
                    request,
                    503,
                    "relatorios_desabilitados",
                    "O recurso de laudos está desabilitado.",
                )
            if (
                request.method == "POST"
                and request.url.path == "/api/v1/laudos"
            ):
                max_request = (
                    settings.reports_max_upload_bytes
                    + REPORT_UPLOAD_MULTIPART_OVERHEAD_BYTES
                )
                declared_raw = request.headers.get("content-length")
                if declared_raw is not None:
                    try:
                        declared = int(declared_raw)
                    except ValueError:
                        return _envelope(
                            request,
                            400,
                            "content_length_invalido",
                            "O tamanho declarado do upload é inválido.",
                        )
                    if declared < 0 or declared > max_request:
                        return _envelope(
                            request,
                            413,
                            "pdf_excede_tamanho_maximo",
                            "O PDF excede o tamanho máximo permitido.",
                        )
                # Content-Length pode estar ausente ou mentir. Limita também
                # o stream ASGI antes de Starlette concluir o parser/spool do
                # multipart. O teto inclui overhead fechado para boundary e
                # campos, preservando um arquivo com exatamente o máximo.
                original_receive = request._receive
                received = 0

                async def limited_receive():
                    nonlocal received
                    message = await original_receive()
                    if message.get("type") == "http.request":
                        received += len(message.get("body", b""))
                        if received > max_request:
                            request.state.report_upload_too_large = True
                            raise ReportDomainError(
                                413,
                                "pdf_excede_tamanho_maximo",
                                "O PDF excede o tamanho máximo permitido.",
                            )
                    return message

                request._receive = limited_receive
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ReportDomainError)
    async def report_domain_exc(request: Request, exc: ReportDomainError):
        return _envelope(
            request,
            exc.status_code,
            exc.codigo,
            exc.mensagem,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exc(request: Request, exc: StarletteHTTPException):
        if getattr(request.state, "report_upload_too_large", False):
            return _envelope(
                request,
                413,
                "pdf_excede_tamanho_maximo",
                "O PDF excede o tamanho máximo permitido.",
            )
        # FastAPI converte exceções do receive durante o parse para um 400
        # encadeado. Recupera o limite M24A original sem expor a cadeia.
        cause = exc.__cause__
        while cause is not None:
            if isinstance(cause, ReportDomainError):
                return _envelope(
                    request,
                    cause.status_code,
                    cause.codigo,
                    cause.mensagem,
                )
            cause = cause.__cause__
        return _envelope(request, exc.status_code, f"http_{exc.status_code}", exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_exc(request: Request, exc: RequestValidationError):
        # fail-closed: entrada inválida nunca é aceita parcialmente
        # M22: esta mensagem fixa é de domínio e não contém input/PII. O caso
        # precisa ser explícito para um cliente obsoleto entender por que o
        # bloco financeiro Pastore foi recusado, sem abrir os demais detalhes
        # livres do Pydantic na resposta.
        pastore_message = (
            "Espirometria Pastore não aceita pagamento direto do paciente."
        )
        if any(
            str((e.get("ctx") or {}).get("error", "")) == pastore_message
            for e in exc.errors()
        ):
            return _envelope(
                request,
                422,
                "pagamento_direto_pastore_proibido",
                pastore_message,
            )
        # M25.26 — o 422 passa a dizer O QUE corrigir.
        #
        # Antes desta missão a resposta era `mensagem: "Payload inválido."` com
        # `campos` em caminho técnico (`body.espirometria.data_exame`). A tela
        # lê só `erro.mensagem`, então o operador via a frase opaca e nada
        # mais — foi exatamente o que travou o teste real da Dra. Ana.
        #
        # `campo`/`tipo` continuam iguais (contrato antigo preservado); o que
        # entra é o rótulo do formulário, o motivo em português e a lista
        # separada de faltantes, que é o que a Central usa para destacar campo
        # a campo. Nenhum valor digitado entra na resposta.
        campos = []
        faltantes = []
        regras = []
        invalidos = []
        for e in exc.errors():
            caminho = caminho_normalizado(e["loc"])
            rotulo = rotulo_do_campo(caminho)
            dominio = mensagem_de_dominio(e)
            motivo = dominio or motivo_do_tipo(e["type"])
            campos.append({
                "campo": ".".join(str(p) for p in e["loc"]),
                "tipo": e["type"],
                "caminho": caminho,
                "rotulo": rotulo,
                "motivo": motivo,
            })
            if dominio:
                # Regra de negócio: o texto já é uma frase completa e explica
                # a combinação recusada melhor que um rótulo isolado.
                if dominio not in regras:
                    regras.append(dominio)
            elif e["type"] == "missing":
                faltantes.append({"campo": caminho, "rotulo": rotulo})
            else:
                invalidos.append(f"{rotulo} ({motivo})")

        partes = []
        if faltantes:
            rotulos = ", ".join(dict.fromkeys(f["rotulo"] for f in faltantes))
            partes.append(f"Falta preencher: {rotulos}.")
        if invalidos:
            partes.append("Corrija: " + ", ".join(dict.fromkeys(invalidos)) + ".")
        partes.extend(regras)
        mensagem = " ".join(partes) or "Payload inválido."
        return _envelope(
            request, 422, "validacao", mensagem,
            {"campos": campos, "campos_faltantes": faltantes},
        )

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
