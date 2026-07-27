#!/usr/bin/env python3
"""M15.5B — Testes determinísticos do gate de go-live (sem rede real).

Toda chamada de rede é substituída por dublês (monkeypatch de http_get ou
opener falso); nenhum teste abre socket. Cobrem: validação da URL base,
verificação de certificado inviolável, timeouts finitos, redirect sem
downgrade, probes HTTPS pré/pós e checagens estáticas do release alvo.

Uso: python3 painel-soprolife/nucleo-m15/scripts/test_go_live_https_gate.py
"""

import json
import pathlib
import re
import ssl
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import go_live_https_gate as gate

BASE = "https://painel-privado.exemplo.ts.net"
SCRIPTS_ORDENADOS = (
    '<script src="./js/m15-security.js?v=1" defer></script>\n'
    '<script src="./js/m15-datepicker.js?v=1" defer></script>\n'
    '<script src="./js/m15-nucleo.js?v=1" defer></script>'
)


class TestValidarBaseUrl(unittest.TestCase):
    def test_https_valida_aceita(self):
        self.assertEqual(gate.validar_base_url(BASE + "/"), BASE)
        self.assertEqual(gate.validar_base_url(BASE), BASE)

    def test_https_com_porta_explicita_aceita(self):
        self.assertEqual(gate.validar_base_url(BASE + ":8443/"), BASE + ":8443")

    def rejeita(self, url):
        with self.assertRaises(gate.GateError):
            gate.validar_base_url(url)

    def test_http_rejeitado(self):
        self.rejeita("http://painel-privado.exemplo.ts.net/")

    def test_esquemas_nao_https_rejeitados(self):
        for url in ("ftp://host/", "file:///etc/passwd", "//host/", "host/"):
            self.rejeita(url)

    def test_credenciais_embutidas_rejeitadas(self):
        self.rejeita("https://usuario:senha@painel-privado.exemplo.ts.net/")
        self.rejeita("https://usuario@painel-privado.exemplo.ts.net/")

    def test_querystring_rejeitada(self):
        self.rejeita(BASE + "/?token=x")

    def test_fragmento_rejeitado(self):
        self.rejeita(BASE + "/#admin")

    def test_malformadas_rejeitadas(self):
        for url in ("", "   ", "https://", "https:///", "https://host:porta/",
                    "https://ho st/", "https://exemplo..ts..net//painel",
                    None, 42, " https://host/ "):
            self.rejeita(url)

    def test_path_fora_da_raiz_rejeitado(self):
        self.rejeita(BASE + "/painel-soprolife/")


class TestCertificadoETimeouts(unittest.TestCase):
    def test_contexto_padrao_verifica_certificado(self):
        contexto = gate.criar_contexto_ssl()
        self.assertEqual(contexto.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(contexto.check_hostname)

    def test_verificacao_desligada_impede_execucao(self):
        inseguro = ssl.create_default_context()
        inseguro.check_hostname = False
        inseguro.verify_mode = ssl.CERT_NONE
        with mock.patch.object(ssl, "create_default_context", return_value=inseguro):
            with self.assertRaises(gate.GateError):
                gate.criar_contexto_ssl()
            with self.assertRaises(gate.GateError):
                gate.criar_opener()

    def test_sem_curl_e_sem_flags_inseguras_nos_scripts_de_deploy(self):
        raiz = pathlib.Path(__file__).resolve().parent
        alvos = [
            raiz / "deploy-producao-vps.sh",
            raiz / "lib-deploy-hardening.sh",
            raiz / "lib-go-live-gate.sh",
            raiz / "go_live_https_gate.py",
            raiz / "lib-reports-go-live-gate.sh",
            raiz / "reports_go_live_gate.py",
        ]
        for alvo in alvos:
            texto = alvo.read_text(encoding="utf-8")
            self.assertNotIn("--insecure", texto, alvo.name)
            self.assertIsNone(re.search(r"\bcurl\b", texto), alvo.name)
            self.assertNotIn("_create_unverified_context", texto, alvo.name)

    def test_timeouts_finitos(self):
        self.assertGreater(gate.CONNECT_TIMEOUT_S, 0)
        self.assertLessEqual(gate.CONNECT_TIMEOUT_S, 60)
        self.assertGreater(gate.TOTAL_TIMEOUT_S, 0)
        self.assertLessEqual(gate.TOTAL_TIMEOUT_S, 600)

    def test_http_get_passa_timeout_finito_ao_opener(self):
        capturado = {}

        class OpenerFalso:
            def open(self, url, timeout=None):
                capturado["timeout"] = timeout

                class Resposta:
                    status = 200

                    def read(self, n):
                        return b"{}"

                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                return Resposta()

        gate.http_get(BASE + "/x", time.monotonic() + 999, opener=OpenerFalso())
        self.assertIsNotNone(capturado["timeout"])
        self.assertLessEqual(capturado["timeout"], gate.CONNECT_TIMEOUT_S)

    def test_prazo_total_esgotado_rejeita(self):
        with self.assertRaises(gate.GateError):
            gate.http_get(BASE + "/x", time.monotonic() - 1)

    def test_http_get_recusa_url_nao_https(self):
        with self.assertRaises(gate.GateError):
            gate.http_get("http://127.0.0.1/x", time.monotonic() + 10)


class TestRedirecionadorSeguro(unittest.TestCase):
    def redirect(self, novo_destino):
        handler = gate.RedirecionadorSeguro()
        req = urllib.request.Request(BASE + "/painel-soprolife/")
        return handler.redirect_request(
            req, None, 302, "Found", {"location": novo_destino}, novo_destino
        )

    def test_downgrade_para_http_rejeitado(self):
        with self.assertRaises(gate.GateError):
            self.redirect("http://painel-privado.exemplo.ts.net/outro/")

    def test_redirect_para_outro_hostname_rejeitado(self):
        with self.assertRaises(gate.GateError):
            self.redirect("https://atacante.exemplo.com/")

    def test_redirect_com_credenciais_rejeitado(self):
        with self.assertRaises(gate.GateError):
            self.redirect("https://u:p@painel-privado.exemplo.ts.net/")

    def test_redirect_https_mesmo_hostname_aceito(self):
        novo = self.redirect(BASE + "/painel-soprolife/index.html")
        self.assertIsInstance(novo, urllib.request.Request)


def respostas_pos_validas():
    return {
        BASE + gate.CAMINHO_PAINEL: (200, SCRIPTS_ORDENADOS.encode("utf-8")),
        BASE + gate.CAMINHO_HEALTH: (200, b'{"status": "ok"}'),
        BASE + gate.CAMINHO_CONFIG: (
            200,
            json.dumps(
                {"enabled": True, "api_base": "/painel-soprolife/api/m15"}
            ).encode("utf-8"),
        ),
        BASE + gate.CAMINHO_SECURITY_JS: (200, b'/* guarda */ var x = "blocked";'),
    }


class TestProbesHttps(unittest.TestCase):
    def com_respostas(self, respostas):
        def falso_http_get(url, prazo_final, opener=None):
            if url not in respostas:
                raise gate.GateError(f"URL inesperada no dublê: {url}")
            return respostas[url]

        return mock.patch.object(gate, "http_get", side_effect=falso_http_get)

    def test_pre_valido_aceito_com_rede_mockada(self):
        with self.com_respostas(respostas_pos_validas()):
            gate.checar_https_pre(BASE + "/")

    def test_pos_valido_aceito_com_rede_mockada(self):
        with self.com_respostas(respostas_pos_validas()):
            gate.checar_https_pos(BASE + "/")

    def pos_rejeita(self, respostas):
        with self.com_respostas(respostas):
            with self.assertRaises(gate.GateError):
                gate.checar_https_pos(BASE + "/")

    def test_painel_nao_200_rejeitado(self):
        r = respostas_pos_validas()
        r[BASE + gate.CAMINHO_PAINEL] = (503, b"manutencao")
        with self.com_respostas(r):
            with self.assertRaises(gate.GateError):
                gate.checar_https_pre(BASE + "/")

    def test_health_nao_200_rejeitado(self):
        r = respostas_pos_validas()
        r[BASE + gate.CAMINHO_HEALTH] = (500, b'{"status": "ok"}')
        with self.com_respostas(r):
            with self.assertRaises(gate.GateError):
                gate.checar_https_pre(BASE + "/")

    def test_health_sem_status_ok_rejeitado(self):
        for corpo in (b'{"status": "iniciando"}', b'{"ok": true}', b"[]",
                      b"ok", b'"ok"', b"{}"):
            r = respostas_pos_validas()
            r[BASE + gate.CAMINHO_HEALTH] = (200, corpo)
            with self.com_respostas(r):
                with self.assertRaises(gate.GateError):
                    gate.checar_https_pre(BASE + "/")

    def test_pos_config_servida_sem_enabled_true_rejeitada(self):
        r = respostas_pos_validas()
        r[BASE + gate.CAMINHO_CONFIG] = (
            200, b'{"enabled": false, "api_base": "/painel-soprolife/api/m15"}'
        )
        self.pos_rejeita(r)

    def test_pos_config_servida_com_api_base_alterado_rejeitada(self):
        r = respostas_pos_validas()
        r[BASE + gate.CAMINHO_CONFIG] = (
            200, b'{"enabled": true, "api_base": "https://outro.exemplo/api"}'
        )
        self.pos_rejeita(r)

    def test_pos_m15_security_nao_200_rejeitado(self):
        r = respostas_pos_validas()
        r[BASE + gate.CAMINHO_SECURITY_JS] = (404, b"")
        self.pos_rejeita(r)

    def test_pos_ordem_de_scripts_invertida_rejeitada(self):
        r = respostas_pos_validas()
        invertido = (
            '<script src="./js/m15-nucleo.js?v=1" defer></script>\n'
            '<script src="./js/m15-security.js?v=1" defer></script>'
        )
        r[BASE + gate.CAMINHO_PAINEL] = (200, invertido.encode("utf-8"))
        self.pos_rejeita(r)

    def test_pos_html_sem_guarda_rejeitado(self):
        r = respostas_pos_validas()
        r[BASE + gate.CAMINHO_PAINEL] = (
            200, b'<script src="./js/m15-nucleo.js?v=1" defer></script>'
        )
        self.pos_rejeita(r)


class TestChecagensEstaticas(unittest.TestCase):
    def montar_alvo(self, **mudancas):
        raiz = pathlib.Path(self.tmp.name)
        arquivos = {
            "painel-soprolife/data/m15-config.json": json.dumps(
                {"enabled": True, "api_base": "/painel-soprolife/api/m15"}
            ),
            "painel-soprolife/js/m15-security.js":
                '(function(){ "use strict";\n'
                '// bloqueia HTTP remoto: só https: ou loopback (localhost/127.x)\n'
                'function classify(loc){ if (loc.protocol === "https:") return "https";\n'
                '  if (loc.hostname === "localhost" || /^127\\./.test(loc.hostname))'
                ' return "localdev"; return "blocked"; }\n'
                'window.SoproM15Security = { classify: classify };\n'
                "})();",
            "painel-soprolife/js/m15-nucleo.js":
                "(function(){ var token = null; /* só em memória */ })();",
            "painel-soprolife/index.html":
                "<html><body>" + SCRIPTS_ORDENADOS + "</body></html>",
            "painel-soprolife/scripts/test-m15-go-live.js":
                "// 63 casos de segurança do go-live\nprocess.exit(0);",
        }
        arquivos.update(mudancas)
        for relativo, conteudo in arquivos.items():
            if conteudo is None:
                continue
            caminho = raiz / relativo
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text(conteudo, encoding="utf-8")
        return str(raiz)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_alvo_valido_aceito(self):
        gate.checar_fonte_alvo(self.montar_alvo())

    def rejeita(self, **mudancas):
        with self.assertRaises(gate.GateError):
            gate.checar_fonte_alvo(self.montar_alvo(**mudancas))

    def test_sem_m15_security_rejeitado(self):
        self.rejeita(**{"painel-soprolife/js/m15-security.js": None})

    def test_guarda_sem_bloqueio_de_http_remoto_rejeitada(self):
        self.rejeita(**{"painel-soprolife/js/m15-security.js":
                        "window.SoproM15Security = {};"})

    def test_ordem_de_scripts_invertida_rejeitada(self):
        invertido = (
            '<script src="./js/m15-nucleo.js?v=1" defer></script>'
            '<script src="./js/m15-security.js?v=1" defer></script>'
        )
        self.rejeita(**{"painel-soprolife/index.html": invertido})

    def test_index_sem_guarda_rejeitado(self):
        self.rejeita(**{"painel-soprolife/index.html":
                        '<script src="./js/m15-nucleo.js?v=1" defer></script>'})

    def test_script_externo_no_index_rejeitado(self):
        self.rejeita(**{"painel-soprolife/index.html":
                        '<script src="https://cdn.exemplo.com/auth.js"></script>'
                        + SCRIPTS_ORDENADOS})

    def test_api_base_alterado_rejeitado(self):
        self.rejeita(**{"painel-soprolife/data/m15-config.json":
                        '{"enabled": true, "api_base": "/outra/api"}'})

    def test_enabled_false_nao_e_alvo_de_go_live(self):
        self.rejeita(**{"painel-soprolife/data/m15-config.json":
                        '{"enabled": false, "api_base": "/painel-soprolife/api/m15"}'})

    def test_persistencia_de_token_rejeitada(self):
        self.rejeita(**{"painel-soprolife/js/m15-nucleo.js":
                        'localStorage.setItem("soproM15Token", t);'})
        self.rejeita(**{"painel-soprolife/js/m15-nucleo.js":
                        'sessionStorage.setItem("t", t);'})

    def test_dependencia_externa_de_autenticacao_rejeitada(self):
        self.rejeita(**{"painel-soprolife/js/m15-nucleo.js":
                        'fetch("https://auth.exemplo.com/login");'})

    def test_sem_testes_globais_de_go_live_rejeitado(self):
        self.rejeita(**{"painel-soprolife/scripts/test-m15-go-live.js": None})


class TestConfigDoReleaseIntegrado(unittest.TestCase):
    # M15.5C: o release integrado (ponte M15.5B + go-live M15.5A) tem
    # enabled=true; a ponte segue fail-closed no deploy (exige as variáveis
    # exatas), e o próprio repositório precisa passar no check-source.
    def test_release_integrado_tem_enabled_true_e_api_base_intacto(self):
        raiz_repo = pathlib.Path(__file__).resolve().parents[3]
        cfg = json.loads(
            (raiz_repo / "painel-soprolife/data/m15-config.json")
            .read_text(encoding="utf-8")
        )
        self.assertIs(cfg["enabled"], True)
        self.assertEqual(cfg["api_base"], "/painel-soprolife/api/m15")

    def test_release_integrado_passa_no_check_source(self):
        raiz_repo = pathlib.Path(__file__).resolve().parents[3]
        gate.checar_fonte_alvo(str(raiz_repo))


if __name__ == "__main__":
    unittest.main(verbosity=2)
