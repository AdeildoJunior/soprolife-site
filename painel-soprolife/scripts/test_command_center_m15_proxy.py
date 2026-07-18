#!/usr/bin/env python3
"""Testes unitários do proxy M15, sem abrir portas nem acessar rede."""

import contextlib
import email.message
import importlib.util
import io
import json
import os
import socket
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
SERVER_PATH = SCRIPT_DIR / "command-center-local-server.py"
SPEC = importlib.util.spec_from_file_location("command_center_local_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server)


class FakeResponse:
    def __init__(self, status=200, body=b'{"status":"ok"}', headers=None):
        self.status = status
        self._body = body
        self._headers = {k.lower(): v for k, v in (headers or {
            "Content-Type": "application/json",
            "X-Request-ID": "api-rid-1",
        }).items()}

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def getheader(self, name, default=None):
        return self._headers.get(name.lower(), default)


class FakeSocket:
    def __init__(self):
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value


class FakeConnection:
    response = FakeResponse()
    connect_error = None
    instances = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = FakeSocket()
        self.request_args = None
        type(self).instances.append(self)

    def connect(self):
        if type(self).connect_error:
            raise type(self).connect_error

    def request(self, method, target, body=None, headers=None):
        self.request_args = (method, target, body, dict(headers or {}))

    def getresponse(self):
        return type(self).response

    def close(self):
        pass


class Harness(server._Handler):
    def __init__(self, path, headers=None, body=b""):
        self.path = path
        self.command = "GET"
        self.directory = os.getcwd()
        self.headers = email.message.Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.statuses = []
        self.output_headers = []

    def send_response(self, code, message=None):
        self.statuses.append(code)

    def send_header(self, name, value):
        self.output_headers.append((name, value))

    def end_headers(self):
        pass

    def send_error(self, code, message=None, explain=None):
        self.statuses.append(code)

    @property
    def response_headers(self):
        return {name.lower(): value for name, value in self.output_headers}


class ProxyM15Tests(unittest.TestCase):
    def setUp(self):
        FakeConnection.response = FakeResponse()
        FakeConnection.connect_error = None
        FakeConnection.instances = []
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("SOPROLIFE_M15_UPSTREAM", None)

    def tearDown(self):
        self.env.stop()

    def run_m15(self, method="GET", path="/painel-soprolife/api/m15/health",
                headers=None, body=b""):
        headers = dict(headers or {})
        if body and "Content-Length" not in headers:
            headers["Content-Length"] = str(len(body))
        handler = Harness(path, headers, body)
        handler.command = method
        with mock.patch.object(server.http.client, "HTTPConnection", FakeConnection):
            getattr(handler, "do_" + method)()
        return handler

    def test_health_get_e_traducao_fixa(self):
        handler = self.run_m15(path="/painel-soprolife/api/m15/health?probe=1")
        self.assertEqual(handler.statuses[-1], 200)
        conn = FakeConnection.instances[-1]
        self.assertEqual((conn.host, conn.port), ("127.0.0.1", 8015))
        self.assertEqual(conn.request_args[:2], ("GET", "/api/v1/health?probe=1"))
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"status": "ok"})

    def test_get_autorizado_e_allowlist_de_headers(self):
        handler = self.run_m15(headers={
            "Authorization": "Bearer token-sintetico",
            "Accept": "application/json",
            "Cookie": "sessao=nao-encaminhar",
            "Host": "cliente.example",
            "Connection": "keep-alive",
            "Proxy-Authorization": "nao",
            "X-Outro": "nao",
        })
        self.assertEqual(handler.statuses[-1], 200)
        forwarded = FakeConnection.instances[-1].request_args[3]
        self.assertEqual(forwarded["Authorization"], "Bearer token-sintetico")
        self.assertEqual(forwarded["Accept"], "application/json")
        for forbidden in ("Cookie", "Host", "Connection", "Proxy-Authorization", "X-Outro"):
            self.assertNotIn(forbidden, forwarded)

    def test_post_json_preserva_idempotencia_e_request_id(self):
        body = b'{"q":"Pessoa Sintetica"}'
        handler = self.run_m15(
            "POST", "/painel-soprolife/api/m15/pessoas/busca",
            {
                "Content-Type": "application/json",
                "Idempotency-Key": "idem-001",
                "X-Request-ID": "req-001",
            }, body,
        )
        self.assertEqual(handler.statuses[-1], 200)
        method, target, sent_body, sent_headers = FakeConnection.instances[-1].request_args
        self.assertEqual((method, target, sent_body),
                         ("POST", "/api/v1/pessoas/busca", body))
        self.assertEqual(sent_headers["Idempotency-Key"], "idem-001")
        self.assertEqual(sent_headers["X-Request-ID"], "req-001")
        self.assertEqual(handler.response_headers["x-request-id"], "api-rid-1")

    def test_patch_e_metodo_proibido(self):
        self.assertEqual(self.run_m15(
            "PATCH", "/painel-soprolife/api/m15/leads/LEA-000001",
            {"Content-Type": "application/json"}, b'{"etapa":"novo"}',
        ).statuses[-1], 200)
        denied = self.run_m15("PUT", "/painel-soprolife/api/m15/health")
        self.assertEqual(denied.statuses[-1], 405)
        self.assertEqual(denied.response_headers["allow"], "GET, POST, PATCH")
        head = self.run_m15("HEAD", "/painel-soprolife/api/m15/health")
        self.assertEqual(head.statuses[-1], 405)

    def test_request_id_longo_nao_e_encaminhado(self):
        self.run_m15(headers={"X-Request-ID": "x" * 500})
        forwarded = FakeConnection.instances[-1].request_args[3]
        self.assertNotIn("X-Request-ID", forwarded)

    def test_cliente_nao_controla_upstream(self):
        self.run_m15(
            path="/painel-soprolife/api/m15/health?upstream=http://externo.example",
            headers={"X-Upstream": "http://externo.example"},
        )
        conn = FakeConnection.instances[-1]
        self.assertEqual((conn.host, conn.port), ("127.0.0.1", 8015))
        self.assertNotIn("X-Upstream", conn.request_args[3])

    def test_prefixo_incorreto_post_retorna_404(self):
        handler = Harness("/painel-soprolife/api/m15-externo/health")
        handler.command = "POST"
        handler.do_POST()
        self.assertEqual(handler.statuses[-1], 404)
        handler = Harness("/painel-soprolife/api/m15-externo/health")
        handler.do_GET()
        self.assertEqual(handler.statuses[-1], 404)

    def test_arquivo_estatico_continua_delegado(self):
        handler = Harness("/painel-soprolife/index.html")
        with mock.patch.object(
            server.http.server.SimpleHTTPRequestHandler, "do_GET", autospec=True
        ) as inherited:
            handler.do_GET()
        inherited.assert_called_once_with(handler)

    def test_path_traversal_rejeitado(self):
        for path in (
            "/painel-soprolife/api/m15/../command-center/status",
            "/painel-soprolife/api/m15/%2e%2e/health",
            "/painel-soprolife/api/m15//health",
            "/painel-soprolife/api/m15/..%2fhealth",
        ):
            handler = Harness(path)
            handler.do_GET()
            self.assertEqual(handler.statuses[-1], 400, path)

    def test_timeout_e_conexao_recusada(self):
        FakeConnection.connect_error = socket.timeout()
        self.assertEqual(self.run_m15().statuses[-1], 504)
        FakeConnection.connect_error = ConnectionRefusedError()
        self.assertEqual(self.run_m15().statuses[-1], 502)

    def test_status_da_api_preservado(self):
        for status in (401, 403, 409, 422):
            FakeConnection.response = FakeResponse(
                status=status, body=json.dumps({"status": status}).encode()
            )
            self.assertEqual(self.run_m15().statuses[-1], status)

    def test_limite_de_corpo_sem_contatar_upstream(self):
        handler = Harness(
            "/painel-soprolife/api/m15/pessoas",
            {"Content-Length": str(server._M15_MAX_REQUEST_BODY + 1)},
        )
        handler.command = "POST"
        with mock.patch.object(server.http.client, "HTTPConnection", FakeConnection):
            handler.do_POST()
        self.assertEqual(handler.statuses[-1], 413)
        self.assertEqual(FakeConnection.instances, [])

    def test_resposta_invalida_e_excedente(self):
        FakeConnection.response = FakeResponse(headers={"Content-Type": "text/html"})
        self.assertEqual(self.run_m15().statuses[-1], 502)
        FakeConnection.response = FakeResponse(body=b"x" * (server._M15_MAX_RESPONSE_BODY + 1))
        self.assertEqual(self.run_m15().statuses[-1], 502)

    def test_log_nao_contem_token_body_pii_query_ou_url(self):
        secret = "Bearer SEGREDO-QUE-NAO-PODE-VAZAR"
        body = b'{"nome":"Pessoa Privada","telefone":"21999999999","cpf":"123"}'
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.run_m15(
                "POST",
                "/painel-soprolife/api/m15/pessoas/busca?q=PessoaPrivada",
                {"Authorization": secret, "Content-Type": "application/json"},
                body,
            )
        logs = stream.getvalue()
        for forbidden in (
            secret, "SEGREDO", "Pessoa Privada", "PessoaPrivada", "21999999999",
            "cpf", "127.0.0.1", "/api/v1", "?q=",
        ):
            self.assertNotIn(forbidden, logs)
        self.assertIn("operation=proxy method=POST status=200", logs)

    def test_upstream_nao_loopback_rejeitado(self):
        invalid = (
            "http://100.87.98.100:8015/api/v1",
            "http://0.0.0.0:8015/api/v1",
            "http://localhost:8015/api/v1",
            "https://127.0.0.1:8015/api/v1",
            "http://127.0.0.1:8015/outra-api",
            "http://127.0.0.1:8015/api/v1?next=http://externo",
        )
        for value in invalid:
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {"SOPROLIFE_M15_UPSTREAM": value}
            ):
                with self.assertRaises(ValueError):
                    server._m15_upstream()

    def test_apps_script_existente_continua_funcionando(self):
        class AppsResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok":true,"id":"REG-001"}'

        with tempfile.TemporaryDirectory() as temp_dir:
            config = Path(temp_dir) / "config.json"
            config.write_text(json.dumps({
                "webAppUrl": "https://script.example/exec",
                "apiToken": "token-server-side",
                "instanceName": "instancia-sintetica",
                "operatorName": "operador-sintetico",
            }), encoding="utf-8")
            payload = b'{"action":"registrar","data":{"campo":"valor"}}'
            handler = Harness(
                server._API_PATH,
                {"Content-Length": str(len(payload)), "Content-Type": "application/json"},
                payload,
            )
            handler.command = "POST"
            with mock.patch.object(server, "_CONFIG_PATH", config), mock.patch.object(
                server.urllib.request, "urlopen", return_value=AppsResponse()
            ) as urlopen:
                handler.do_POST()
            self.assertEqual(handler.statuses[-1], 200)
            forwarded = json.loads(urlopen.call_args.args[0].data)
            self.assertEqual(forwarded["token"], "token-server-side")
            self.assertEqual(forwarded["action"], "registrar")


class FrontendConfigTests(unittest.TestCase):
    def test_config_mesma_origem_flag_desligada(self):
        config_path = SCRIPT_DIR.parent / "data" / "m15-config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertIs(config["enabled"], False)
        self.assertEqual(config["api_base"], "/painel-soprolife/api/m15")
        self.assertNotIn("://", config["api_base"])

    def test_token_somente_memoria_e_limpeza_legada(self):
        js = (SCRIPT_DIR.parent / "js" / "m15-nucleo.js").read_text(encoding="utf-8")
        self.assertIn('localStorage.removeItem("soproM15Token")', js)
        self.assertNotRegex(js, r'(localStorage|sessionStorage)\.setItem\(["\']soproM15Token')
        self.assertNotIn("http://127.0.0.1:8015", js)
        self.assertNotIn("http://localhost:8015", js)
        self.assertIn('apiBase: "/painel-soprolife/api/m15"', js)
        self.assertIn(
            'config.api_base === "/painel-soprolife/api/m15"', js
        )


class DeploymentKitTests(unittest.TestCase):
    def test_unit_tem_bind_usuario_ambiente_e_hardening(self):
        unit = (SCRIPT_DIR.parent / "systemd" / "soprolife-m15-api.service").read_text(
            encoding="utf-8"
        )
        required = (
            "User=soprolife", "Group=soprolife",
            "WorkingDirectory=/opt/soprolife/soprolife-site/painel-soprolife/nucleo-m15",
            "EnvironmentFile=/opt/soprolife/secrets/m15.env",
            "Environment=M15_API_HOST=127.0.0.1", "Environment=M15_API_PORT=8015",
            "ExecStart=/opt/soprolife/venvs/m15/bin/python -m app.serve",
            "NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectSystem=strict",
            "UMask=0077", "ReadWritePaths=", "Requires=postgresql.service",
        )
        for item in required:
            self.assertIn(item, unit)
        self.assertNotRegex(unit, r"(?im)^M15_(AUTH_SECRET|DATABASE_URL)=")
        self.assertNotIn("0.0.0.0", unit)

    def test_deploy_fail_closed_sem_ativacao_ou_dados(self):
        deploy = (
            SCRIPT_DIR.parent / "nucleo-m15" / "scripts" / "deploy-producao-vps.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("set -Eeuo pipefail", deploy)
        self.assertIn("requirements.lock", deploy)
        self.assertIn("alembic\" upgrade head", deploy)
        self.assertIn("pg_restore --list", deploy)
        self.assertIn("M15_API_HOST=127.0.0.1", deploy)
        self.assertIn('cfg["enabled"] is False', deploy)
        for forbidden in ("seed-demo", "seed-institucional", "podman ", "docker "):
            self.assertNotIn(forbidden, deploy.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
