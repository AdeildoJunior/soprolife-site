#!/usr/bin/env python3
"""
SoproLife Command Center — Servidor proxy local.

Serve os arquivos estáticos do painel e publica a API M15 de loopback sob uma
rota de mesma origem.

M23 — o proxy para o Apps Script foi DESATIVADO. Até o M22 este servidor
guardava a URL e o token do Web App e encaminhava escritas do painel para o
Google Sheets; com o PostgreSQL como fonte operacional única, esse caminho
deixou de existir. A rota antiga permanece apenas para responder 410 a um
cliente em cache, e nunca lê configuração com token nem contata a rede.

Endpoints:
  GET  /painel-soprolife/api/command-center/status  → {"configured": false, ...}
  POST /painel-soprolife/api/command-center          → 410, rota desativada
  GET|POST|PATCH /painel-soprolife/api/m15/...       → http://127.0.0.1:8015/api/v1/...

M24A: o upload multipart e a entrega PDF continuam na mesma rota autenticada.
Somente os endpoints exatos de laudo recebem limites binários próprios; todas
as demais respostas do proxy permanecem JSON e com os limites anteriores.

Logs M15 mostram somente operação genérica, método, status, request_id
sanitizado e duração.
Nunca imprime: token, URL, querystring, corpo, telefone, nomes ou CPF.

Uso:
    cd ~/soprolife-site
    python3 painel-soprolife/scripts/command-center-local-server.py
"""

import http.client
import http.server
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.parse
from pathlib import Path

HOST = os.environ.get("SOPROLIFE_PANEL_HOST", "127.0.0.1")
PORT = int(os.environ.get("SOPROLIFE_PANEL_PORT", "8765"))

_API_PATH    = "/painel-soprolife/api/command-center"
_STATUS_PATH = "/painel-soprolife/api/command-center/status"
_M15_PREFIX = "/painel-soprolife/api/m15"
_M15_DEFAULT_UPSTREAM = "http://127.0.0.1:8015/api/v1"
# M25.4 — DELETE entrou para a revogação do ativo de assinatura médica
# (DELETE /laudos/admin/medicos/{id}/assinatura). Sem ele, o botão "Revogar"
# do painel recebia 405 do próprio proxy e a única saída era chamar a API à
# mão. PUT e HEAD seguem bloqueados de propósito: nenhuma rota do M15 os usa,
# e a fronteira de autorização continua sendo o RBAC da API (admin-only),
# não esta lista.
_M15_METHODS = frozenset({"GET", "POST", "PATCH", "DELETE"})
_M15_ALLOW_HEADER = "GET, POST, PATCH, DELETE"
_M15_FORWARD_HEADERS = {
    "authorization": "Authorization",
    "content-type": "Content-Type",
    "idempotency-key": "Idempotency-Key",
    "x-request-id": "X-Request-ID",
    "accept": "Accept",
    # M21 — cabeçalho anti-CSRF da sessão de navegador. Vai junto do cookie.
    "x-csrf-token": "X-CSRF-Token",
}

# M21 — sessão persistente. O cookie precisa atravessar este proxy nos dois
# sentidos, mas SÓ o cookie do painel: qualquer outro cookie que exista na
# mesma origem é descartado antes de chegar à API, e qualquer Set-Cookie com
# nome fora da allowlist é descartado antes de chegar ao navegador.
_M15_COOKIE_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
_M15_DEFAULT_COOKIE = "soprolife_m15_sessao"

def _m15_cookie_names() -> frozenset:
    """Nome do cookie de sessão; env inválido cai no padrão (fail-safe)."""
    nome = os.environ.get("SOPROLIFE_M15_SESSION_COOKIE", "").strip()
    if not nome or not _M15_COOKIE_NAME_RE.fullmatch(nome):
        nome = _M15_DEFAULT_COOKIE
    return frozenset({nome})


_M15_COOKIE_NAMES = _m15_cookie_names()
_M15_MAX_COOKIE_HEADER = 4096


def _filter_request_cookie(raw: str | None) -> str | None:
    """Devolve só os pares cookie cujo nome está na allowlist do painel."""
    if not raw or len(raw) > _M15_MAX_COOKIE_HEADER:
        return None
    mantidos = []
    for par in raw.split(";"):
        nome, sep, valor = par.strip().partition("=")
        if not sep:
            continue
        nome = nome.strip()
        if nome in _M15_COOKIE_NAMES and _M15_COOKIE_NAME_RE.fullmatch(nome):
            mantidos.append(f"{nome}={valor.strip()}")
    return "; ".join(mantidos) or None


def _upstream_set_cookies(response) -> list:
    """Set-Cookie da API, tolerante à interface (múltiplos ou único)."""
    headers = getattr(response, "headers", None)
    if headers is not None and hasattr(headers, "get_all"):
        return list(headers.get_all("Set-Cookie") or [])
    unico = response.getheader("Set-Cookie")
    return [unico] if unico else []


def _allowed_set_cookie(raw: str) -> bool:
    """Aceita Set-Cookie da API somente para o cookie de sessão do painel."""
    if not raw or len(raw) > _M15_MAX_COOKIE_HEADER:
        return False
    if "\n" in raw or "\r" in raw:
        return False
    nome = raw.split("=", 1)[0].strip()
    return nome in _M15_COOKIE_NAMES and bool(_M15_COOKIE_NAME_RE.fullmatch(nome))
_M15_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_M15_MAX_REQUEST_BODY = 1024 * 1024
_M15_MAX_RESPONSE_BODY = 4 * 1024 * 1024
# O backend aceita PDFs de até 25 MiB. Um MiB adicional cobre o envelope
# multipart; só POST /laudos recebe esse teto, nunca as rotas JSON comuns.
_M15_MAX_REPORT_REQUEST_BODY = 26 * 1024 * 1024
_M15_MAX_REPORT_RESPONSE_BODY = 26 * 1024 * 1024
_M15_CONNECT_TIMEOUT = 3.0
_M15_RESPONSE_TIMEOUT = 15.0
_M15_REPORT_CONTENT_RE = re.compile(
    r"^/laudos/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/versoes/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/conteudo$"
)
_M15_SAFE_DISPOSITION_RE = re.compile(
    r'^(?:inline|attachment); filename="[A-Za-z0-9._-]{1,180}"$'
)


def _m15_upstream() -> tuple[str, int, str]:
    """Retorna host/porta/base validados; aceita somente HTTP em IP loopback."""
    raw = os.environ.get("SOPROLIFE_M15_UPSTREAM", _M15_DEFAULT_UPSTREAM).strip()
    parsed = urllib.parse.urlsplit(raw)
    try:
        host = parsed.hostname or ""
        address = ipaddress.ip_address(host)
        port = parsed.port or 80
    except ValueError as exc:
        raise ValueError("upstream M15 inválido") from exc
    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not (1 <= port <= 65535)
    ):
        raise ValueError("upstream M15 deve usar HTTP em loopback")
    base_path = parsed.path.rstrip("/")
    if base_path != "/api/v1":
        raise ValueError("caminho-base do upstream M15 inválido")
    return host, port, base_path


def _m15_public_path(raw_target: str) -> tuple[str, str] | None:
    """Valida o alvo público e devolve (sufixo, query), sem normalização ambígua."""
    parsed = urllib.parse.urlsplit(raw_target)
    path = parsed.path
    if path != _M15_PREFIX and not path.startswith(_M15_PREFIX + "/"):
        return None
    try:
        decoded = urllib.parse.unquote(path, errors="strict")
    except (UnicodeDecodeError, ValueError):
        raise ValueError("caminho M15 inválido")
    suffix = decoded[len(_M15_PREFIX):]
    segments = suffix.split("/")
    if (
        "\\" in decoded
        or "\x00" in decoded
        or any(segment in (".", "..") for segment in segments)
        or "//" in suffix
        or decoded != path
    ):
        # Percent-encoding no path é desnecessário para os endpoints atuais e
        # rejeitá-lo elimina traversal/normalizações divergentes no upstream.
        raise ValueError("caminho M15 inválido")
    return suffix, parsed.query


def _safe_request_id(value: str | None) -> str:
    value = (value or "").strip()
    return value if _M15_REQUEST_ID_RE.fullmatch(value) else "-"


def _is_report_upload(method: str, suffix: str, content_type: str | None) -> bool:
    return (
        method == "POST"
        and suffix == "/laudos"
        and (content_type or "").lower().startswith("multipart/form-data;")
    )


def _is_report_content(method: str, suffix: str) -> bool:
    return method == "GET" and bool(_M15_REPORT_CONTENT_RE.fullmatch(suffix))


def _safe_content_disposition(value: str | None) -> str | None:
    if not value or len(value) > 220 or "\r" in value or "\n" in value:
        return None
    return value if _M15_SAFE_DISPOSITION_RE.fullmatch(value) else None


# ── Handler ───────────────────────────────────────────────────────────────────

class _Handler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        try:
            m15 = _m15_public_path(self.path)
        except ValueError:
            self._m15_error(400, "Caminho M15 inválido.")
            return
        if m15 is not None:
            self._handle_m15("GET", m15)
        elif self.path == _STATUS_PATH:
            self._handle_status()
        else:
            super().do_GET()

    def do_POST(self):
        try:
            m15 = _m15_public_path(self.path)
        except ValueError:
            self._m15_error(400, "Caminho M15 inválido.")
            return
        if m15 is not None:
            self._handle_m15("POST", m15)
        elif self.path == _API_PATH:
            self._handle_proxy()
        else:
            self._json({"ok": False, "error": "Endpoint não encontrado."}, 404)

    def do_PATCH(self):
        self._dispatch_m15_only("PATCH")

    def do_HEAD(self):
        try:
            m15 = _m15_public_path(self.path)
        except ValueError:
            self._m15_error(400, "Caminho M15 inválido.")
            return
        if m15 is not None:
            self._m15_error(405, "Método não permitido.", {"Allow": _M15_ALLOW_HEADER})
        else:
            super().do_HEAD()

    def do_PUT(self):
        self._dispatch_m15_only("PUT")

    def do_DELETE(self):
        self._dispatch_m15_only("DELETE")

    def do_OPTIONS(self):
        self._dispatch_m15_only("OPTIONS")

    def _dispatch_m15_only(self, method: str) -> None:
        try:
            m15 = _m15_public_path(self.path)
        except ValueError:
            self._m15_error(400, "Caminho M15 inválido.")
            return
        if m15 is None:
            self.send_error(501, "Unsupported method")
            return
        if method not in _M15_METHODS:
            self._m15_error(405, "Método não permitido.", {"Allow": _M15_ALLOW_HEADER})
            return
        self._handle_m15(method, m15)

    # ── Rota legada do Apps Script (M23 — desativada) ──────────────────────
    #
    # Até o M22 este servidor guardava a URL e o token do Apps Script e
    # encaminhava escritas do painel para o Google Sheets. O M23 tornou o
    # PostgreSQL a única fonte operacional: a rota continua existindo apenas
    # para responder com honestidade a um cliente antigo em cache, e NUNCA
    # contata o Apps Script nem lê a configuração com token.
    #
    # Toda escrita do Command Center passa pelo proxy M15 abaixo.

    def _handle_status(self):
        self._json({
            "configured": False,
            "decommissioned": True,
            "canonical_source": "postgresql",
            "detail": "Escrita via Apps Script foi desativada no M23. "
                      "A fonte operacional é o PostgreSQL, pela API do Núcleo M15.",
        })

    def _handle_proxy(self):
        # Drena o corpo para não deixar a conexão pendurada, mas NÃO o
        # interpreta: não há destino para onde encaminhar.
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > 0:
            try:
                self.rfile.read(length)
            except Exception:
                pass
        print("[CC] rota legada do Apps Script recusada (M23)", flush=True)
        self._json({
            "ok": False,
            "error": "Escrita via Google Sheets/Apps Script foi desativada no M23. "
                     "Use a Central de Cadastros ou o CRM — ambos gravam no "
                     "PostgreSQL pela API do Núcleo M15.",
            "canonical_source": "postgresql",
        }, 410)

    # ── Proxy M15 de mesma origem ────────────────────────────────────────

    def _handle_m15(self, method: str, route: tuple[str, str]) -> None:
        started = time.monotonic()
        request_id = _safe_request_id(self.headers.get("X-Request-ID"))
        status = 502
        connection = None
        try:
            host, port, base_path = _m15_upstream()
        except ValueError:
            self._m15_error(503, "Proxy M15 indisponível por configuração inválida.")
            self._m15_log(method, 503, request_id, started)
            return

        suffix, query = route
        upstream_target = base_path + suffix
        if query:
            upstream_target += "?" + query

        transfer_encoding = self.headers.get("Transfer-Encoding")
        raw_length = self.headers.get("Content-Length")
        if transfer_encoding:
            self._m15_error(400, "Transfer-Encoding não aceito.")
            self._m15_log(method, 400, request_id, started)
            return
        try:
            length = int(raw_length or "0")
        except ValueError:
            length = -1
        if length < 0:
            self._m15_error(400, "Content-Length inválido.")
            self._m15_log(method, 400, request_id, started)
            return
        report_upload = _is_report_upload(
            method, suffix, self.headers.get("Content-Type")
        )
        request_limit = (
            _M15_MAX_REPORT_REQUEST_BODY if report_upload else _M15_MAX_REQUEST_BODY
        )
        if length > request_limit:
            self._m15_error(413, "Corpo da requisição excede o limite.")
            self._m15_log(method, 413, request_id, started)
            return
        body = self.rfile.read(length) if length else None

        headers = {}
        for source, target in _M15_FORWARD_HEADERS.items():
            value = self.headers.get(source)
            if value is None:
                continue
            if source == "x-request-id":
                value = request_id
                if value == "-":
                    continue
            headers[target] = value

        # M21 — repassa apenas o cookie de sessão do painel (allowlist).
        cookie = _filter_request_cookie(self.headers.get("cookie"))
        if cookie:
            headers["Cookie"] = cookie

        try:
            connection = http.client.HTTPConnection(host, port, timeout=_M15_CONNECT_TIMEOUT)
            connection.connect()
            if connection.sock is not None:
                connection.sock.settimeout(_M15_RESPONSE_TIMEOUT)
            connection.request(method, upstream_target, body=body, headers=headers)
            response = connection.getresponse()
            status = response.status
            report_content = _is_report_content(method, suffix)
            response_limit = (
                _M15_MAX_REPORT_RESPONSE_BODY
                if report_content
                else _M15_MAX_RESPONSE_BODY
            )
            result_raw = response.read(response_limit + 1)
            if len(result_raw) > response_limit:
                self._m15_error(502, "Resposta da API excede o limite.")
                status = 502
                return
            content_type = response.getheader("Content-Type") or ""
            is_pdf = (
                report_content
                and 200 <= status < 300
                and content_type.lower().startswith("application/pdf")
            )
            if not is_pdf:
                if not content_type.lower().startswith("application/json"):
                    self._m15_error(502, "Resposta inválida da API.")
                    status = 502
                    return
                try:
                    json.loads(result_raw)
                except (TypeError, ValueError):
                    self._m15_error(502, "Resposta inválida da API.")
                    status = 502
                    return

            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if is_pdf:
                disposition = _safe_content_disposition(
                    response.getheader("Content-Disposition")
                )
                if disposition:
                    self.send_header("Content-Disposition", disposition)
                # Laudo clínico autenticado nunca deve virar cache público ou
                # ser interpretado como outro tipo de conteúdo.
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
            upstream_request_id = _safe_request_id(response.getheader("X-Request-ID"))
            if upstream_request_id != "-":
                self.send_header("X-Request-ID", upstream_request_id)
            # M21 — devolve ao navegador só o Set-Cookie da sessão do painel,
            # exatamente como a API o emitiu (HttpOnly/Secure/SameSite/Path
            # são decididos lá; o proxy não reescreve atributo nenhum).
            for cookie_header in _upstream_set_cookies(response):
                if _allowed_set_cookie(cookie_header):
                    self.send_header("Set-Cookie", cookie_header)
            self.send_header("Content-Length", str(len(result_raw)))
            self.end_headers()
            self.wfile.write(result_raw)
        except (socket.timeout, TimeoutError):
            status = 504
            self._m15_error(504, "Tempo limite excedido ao contatar a API.")
        except (ConnectionRefusedError, ConnectionError, OSError, http.client.HTTPException):
            status = 502
            self._m15_error(502, "API M15 indisponível.")
        except Exception:
            status = 502
            self._m15_error(502, "Resposta inválida da API.")
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            self._m15_log(method, status, request_id, started)

    def _m15_error(self, status: int, message: str, headers: dict | None = None) -> None:
        body = json.dumps({"ok": False, "error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if headers:
            for name, value in headers.items():
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _m15_log(method: str, status: int, request_id: str, started: float) -> None:
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        print(
            f"[M15] operation=proxy method={method} status={status} "
            f"request_id={request_id} duration_ms={duration_ms}",
            flush=True,
        )

    # ── Utilitário ─────────────────────────────────────────────────────────

    def _json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Suprime logs de requisições de arquivos estáticos para não poluir
        # o terminal; a API imprime seus próprios logs via print()
        path = str(args[0]) if args else ""
        if "/api/command-center" in path:
            super().log_message(fmt, *args)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # Garante execução a partir da raiz do repositório
    repo_root = Path(__file__).resolve().parent.parent.parent
    os.chdir(repo_root)

    # Falha antes de abrir a porta se o upstream server-side for inseguro.
    _m15_upstream()
    server = http.server.ThreadingHTTPServer((HOST, PORT), _Handler)

    print("SoproLife — Servidor proxy local")
    print(f"Acesso:  http://{HOST}:{PORT}/painel-soprolife/")
    print(f"M15:     mesma origem em {_M15_PREFIX}/... (upstream loopback validado)")
    print()
    print("Fonte operacional: PostgreSQL, pela API do Núcleo M15.")
    print(f"Rota legada do Apps Script ({_API_PATH}): DESATIVADA no M23.")
    print()
    print("Pressione Ctrl+C para desligar.")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")


if __name__ == "__main__":
    main()
