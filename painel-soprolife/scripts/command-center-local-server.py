#!/usr/bin/env python3
"""
SoproLife Command Center — Servidor proxy local.

Serve os arquivos estáticos do painel e atua como proxy seguro para o
Apps Script, adicionando o token de autenticação server-side. Também publica
a API M15 de loopback sob uma rota de mesma origem.

O token e a URL do Apps Script nunca saem do servidor — o browser só
vê a resposta final (ok/erro + id gerado).

Endpoints:
  GET  /painel-soprolife/api/command-center/status  → {"configured": bool}
  POST /painel-soprolife/api/command-center          → proxy para Apps Script
  GET|POST|PATCH /painel-soprolife/api/m15/...       → http://127.0.0.1:8015/api/v1/...

Logs do Apps Script mantêm action/resultado/status. Logs M15 mostram somente
operação genérica, método, status, request_id sanitizado e duração.
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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOST = os.environ.get("SOPROLIFE_PANEL_HOST", "127.0.0.1")
PORT = int(os.environ.get("SOPROLIFE_PANEL_PORT", "8765"))

_CONFIG_PATH = Path("painel-soprolife/data-private/command-center-config.local.json")
_API_PATH    = "/painel-soprolife/api/command-center"
_STATUS_PATH = "/painel-soprolife/api/command-center/status"
_M15_PREFIX = "/painel-soprolife/api/m15"
_M15_DEFAULT_UPSTREAM = "http://127.0.0.1:8015/api/v1"
_M15_METHODS = frozenset({"GET", "POST", "PATCH"})
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
_M15_CONNECT_TIMEOUT = 3.0
_M15_RESPONSE_TIMEOUT = 15.0


def _audit_meta(value, fallback: str) -> str:
    """Sanitiza metadado de auditoria: string curta, sem quebras, com fallback."""
    v = " ".join(str(value or "").split())[:40]
    return v or fallback


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
            self._m15_error(405, "Método não permitido.", {"Allow": "GET, POST, PATCH"})
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
            self._m15_error(405, "Método não permitido.", {"Allow": "GET, POST, PATCH"})
            return
        self._handle_m15(method, m15)

    # ── Status ─────────────────────────────────────────────────────────────

    def _handle_status(self):
        configured = False
        if _CONFIG_PATH.exists():
            try:
                cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
                configured = bool(
                    cfg.get("webAppUrl",  "").strip() and
                    cfg.get("apiToken",   "").strip()
                )
            except Exception:
                configured = False
        self._json({"configured": configured})

    # ── Proxy ──────────────────────────────────────────────────────────────

    def _handle_proxy(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        try:
            payload = json.loads(raw)
        except Exception:
            self._json({"ok": False, "error": "JSON inválido na requisição."}, 400)
            return

        action = str(payload.get("action", "")).strip()
        data   = payload.get("data", {})

        if not action:
            self._json({"ok": False, "error": "Campo 'action' ausente."}, 400)
            return

        # Lê configuração server-side — token nunca vai ao browser
        if not _CONFIG_PATH.exists():
            self._json({"ok": False, "error": "Configuração não encontrada no servidor."}, 503)
            print(f"[CC] ERRO  action={action!r}  config ausente", flush=True)
            return

        try:
            cfg       = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            web_url   = cfg.get("webAppUrl",  "").strip()
            api_token = cfg.get("apiToken",   "").strip()
        except Exception:
            self._json({"ok": False, "error": "Erro ao ler configuração."}, 500)
            return

        if not web_url or not api_token:
            self._json({"ok": False, "error": "Configuração incompleta."}, 503)
            return

        # Identidade de auditoria (M1 Etapa 4) — atributo do SERVIDOR, como o
        # token: sobrescreve qualquer audit_* vindo do browser (nunca confiar
        # no cliente). Lida da config local: instanceName → audit_origem
        # (fallback: hostname), operatorName → audit_operador. Campos usados
        # pelo Apps Script apenas na aba Log Auditoria — nunca contêm dado
        # pessoal, só o nome da instância e do operador declarado na config.
        if isinstance(data, dict):
            try:
                hostname = socket.gethostname()
            except Exception:
                hostname = ""
            data["audit_origem"]   = _audit_meta(cfg.get("instanceName"), _audit_meta(hostname, "desconhecida"))
            data["audit_operador"] = _audit_meta(cfg.get("operatorName"), "desconhecido")

        # Adiciona token server-side e encaminha para o Apps Script
        forward = json.dumps({
            "token":  api_token,   # adicionado aqui, nunca no browser
            "action": action,
            "data":   data,
        }).encode("utf-8")

        result_raw = b""
        result_status = 0
        try:
            req = urllib.request.Request(
                web_url,
                data=forward,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result_raw    = resp.read()
                result_status = resp.status

        except urllib.error.HTTPError as exc:
            # Apps Script pode retornar 4xx/5xx — tenta ler o corpo mesmo assim
            try:
                result_raw    = exc.read()
                result_status = exc.code
            except Exception:
                self._json({"ok": False, "error": f"Erro HTTP {exc.code} do Apps Script."}, 502)
                print(f"[CC] ERRO  action={action!r}  HTTP={exc.code}", flush=True)
                return

        except urllib.error.URLError as exc:
            reason = type(exc.reason).__name__ if exc.reason else "URLError"
            self._json({"ok": False, "error": "Não foi possível contatar o Apps Script."}, 502)
            print(f"[CC] ERRO  action={action!r}  {reason}", flush=True)
            return

        except Exception as exc:
            self._json({"ok": False, "error": "Erro interno do proxy."}, 500)
            print(f"[CC] ERRO  action={action!r}  {type(exc).__name__}", flush=True)
            return

        # Log seguro: apenas action e resultado — sem token, URL, dados pessoais
        try:
            result_obj = json.loads(result_raw)
            ok = result_obj.get("ok", False)
            rid = result_obj.get("id", "")
            print(f"[CC] ok={ok}  action={action!r}  id={rid!r}  http={result_status}", flush=True)
        except Exception:
            print(f"[CC] ERRO  action={action!r}  resposta não-JSON  http={result_status}", flush=True)

        # Devolve a resposta do Apps Script ao browser sem modificação
        self.send_response(200)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(result_raw)))
        self.end_headers()
        self.wfile.write(result_raw)

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
        if length > _M15_MAX_REQUEST_BODY:
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
            result_raw = response.read(_M15_MAX_RESPONSE_BODY + 1)
            if len(result_raw) > _M15_MAX_RESPONSE_BODY:
                self._m15_error(502, "Resposta da API excede o limite.")
                status = 502
                return
            content_type = response.getheader("Content-Type") or ""
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

    cc_ok = _CONFIG_PATH.exists()
    try:
        if cc_ok:
            cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            cc_ok = bool(cfg.get("webAppUrl", "").strip() and cfg.get("apiToken", "").strip())
    except Exception:
        cc_ok = False

    print("SoproLife — Servidor proxy local")
    print(f"Acesso:  http://{HOST}:{PORT}/painel-soprolife/")
    print(f"API:     http://{HOST}:{PORT}{_API_PATH}")
    print(f"Status:  http://{HOST}:{PORT}{_STATUS_PATH}")
    print(f"M15:     mesma origem em {_M15_PREFIX}/... (upstream loopback validado)")
    print()
    print(f"Command Center: {'CONFIGURADO — escrita ativa' if cc_ok else 'SEM CONFIG — modo leitura'}")
    print()
    print("Pressione Ctrl+C para desligar.")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")


if __name__ == "__main__":
    main()
