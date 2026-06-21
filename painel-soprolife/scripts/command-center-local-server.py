#!/usr/bin/env python3
"""
SoproLife Command Center — Servidor proxy local.

Serve os arquivos estáticos do painel e atua como proxy seguro para o
Apps Script, adicionando o token de autenticação server-side.

O token e a URL do Apps Script nunca saem do servidor — o browser só
vê a resposta final (ok/erro + id gerado).

Endpoints:
  GET  /painel-soprolife/api/command-center/status  → {"configured": bool}
  POST /painel-soprolife/api/command-center          → proxy para Apps Script

Logs mostram apenas: action, ok/erro, status HTTP.
Nunca imprime: token, URL, telefone, nomes ou dados pessoais.

Uso:
    cd ~/soprolife-site
    python3 painel-soprolife/scripts/command-center-local-server.py
"""

import http.server
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8765

_CONFIG_PATH = Path("painel-soprolife/data-private/command-center-config.local.json")
_API_PATH    = "/painel-soprolife/api/command-center"
_STATUS_PATH = "/painel-soprolife/api/command-center/status"


# ── Handler ───────────────────────────────────────────────────────────────────

class _Handler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        if self.path == _STATUS_PATH:
            self._handle_status()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == _API_PATH:
            self._handle_proxy()
        else:
            self._json({"ok": False, "error": "Endpoint não encontrado."}, 404)

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

    server = http.server.HTTPServer((HOST, PORT), _Handler)

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
