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


# M26.21 — o que o painel devolve a um cliente ANÔNIMO desde a M25.23: a tela
# de login, no mesmo endereço, sem nada do Command Center.
LOGIN_HTML = (
    "<!doctype html><html><body>"
    '<form id="loginForm">'
    '<input id="email" type="email" /><input id="password" type="password" />'
    "</form>"
    '<script src="./js/m15-security.js"></script>'
    "</body></html>"
)
# A resposta real de command-center-local-server.py::_deny para protected_data.
CORPO_401 = rb'{"ok": false, "error": "Sess\u00e3o necess\u00e1ria."}'
GUARDA_JS = (
    '(function(){ "use strict";\n'
    "// bloqueia HTTP remoto: só https: ou loopback (localhost/127.x)\n"
    'function classify(loc){ if (loc.protocol === "https:") return "https";\n'
    '  if (loc.hostname === "localhost" || /^127\\./.test(loc.hostname))'
    ' return "localdev"; return "blocked"; }\n'
    "window.SoproM15Security = { classify: classify };\n"
    "})();"
)
VERSAO_RELEASE = "9.9.9"
INDEX_ADMIN = (
    "<html><body>"
    '<section id="laudos-espirometria"></section>'
    + SCRIPTS_ORDENADOS
    + '<script src="./js/report-workflow.js?v=1" defer></script>'
    "</body></html>"
)


def arquivos_do_release(**mudancas):
    """Árvore mínima de um release enabled=true, já no formato do checkout."""
    arquivos = {
        "painel-soprolife/data/m15-config.json": json.dumps(
            {"enabled": True, "api_base": "/painel-soprolife/api/m15"}
        ),
        "painel-soprolife/js/m15-security.js": GUARDA_JS,
        "painel-soprolife/js/m15-nucleo.js":
            "(function(){ var token = null; /* só em memória */ })();",
        "painel-soprolife/index.html": INDEX_ADMIN,
        "painel-soprolife/login.html": LOGIN_HTML,
        "painel-soprolife/scripts/test-m15-go-live.js":
            "// 63 casos de segurança do go-live\nprocess.exit(0);",
        "painel-soprolife/nucleo-m15/app/__init__.py":
            f'"""sintético."""\n\n__version__ = "{VERSAO_RELEASE}"\n',
    }
    arquivos.update(mudancas)
    return arquivos


def montar_release(raiz: pathlib.Path, **mudancas) -> str:
    for relativo, conteudo in arquivos_do_release(**mudancas).items():
        if conteudo is None:
            continue
        caminho = raiz / relativo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(conteudo, encoding="utf-8")
    return str(raiz)


def respostas_validas(**mudancas):
    """O que a VPS responde HOJE a um cliente sem sessão, com tudo correto."""
    respostas = {
        BASE + gate.CAMINHO_PAINEL: (200, LOGIN_HTML.encode("utf-8")),
        BASE + gate.CAMINHO_HEALTH: (
            200,
            json.dumps(
                {
                    "status": "ok",
                    "versao": VERSAO_RELEASE,
                    "ambiente": "prod",
                    "banco": "ok",
                }
            ).encode("utf-8"),
        ),
        BASE + gate.CAMINHO_CONFIG: (401, CORPO_401),
        BASE + gate.CAMINHO_SECURITY_JS: (200, GUARDA_JS.encode("utf-8")),
    }
    respostas.update(
        {BASE + caminho: valor for caminho, valor in mudancas.items()}
    )
    return respostas


class TestProbesHttps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.release = montar_release(pathlib.Path(self.tmp.name))

    def com_respostas(self, respostas):
        def falso_http_get(url, prazo_final, opener=None):
            if url not in respostas:
                raise gate.GateError(f"URL inesperada no dublê: {url}")
            return respostas[url]

        return mock.patch.object(gate, "http_get", side_effect=falso_http_get)

    def pre_rejeita(self, respostas, trecho=None):
        with self.com_respostas(respostas):
            with self.assertRaises(gate.GateError) as capturado:
                gate.checar_https_pre(BASE + "/")
        if trecho:
            self.assertIn(trecho, str(capturado.exception))

    def pos_rejeita(self, respostas, release=None, trecho=None):
        with self.com_respostas(respostas):
            with self.assertRaises(gate.GateError) as capturado:
                gate.checar_https_pos(BASE + "/", release or self.release)
        if trecho:
            self.assertIn(trecho, str(capturado.exception))

    # ── superfície anônima correta ───────────────────────────────────────

    def test_pre_valido_aceito_com_rede_mockada(self):
        with self.com_respostas(respostas_validas()):
            gate.checar_https_pre(BASE + "/")

    def test_pos_valido_aceito_com_rede_mockada(self):
        with self.com_respostas(respostas_validas()):
            gate.checar_https_pos(BASE + "/", self.release)

    def test_painel_nao_200_rejeitado(self):
        self.pre_rejeita(
            respostas_validas(**{gate.CAMINHO_PAINEL: (503, b"manutencao")})
        )

    def test_health_nao_200_rejeitado(self):
        self.pre_rejeita(
            respostas_validas(
                **{gate.CAMINHO_HEALTH: (500, b'{"status": "ok"}')}
            )
        )

    def test_health_sem_status_ok_rejeitado(self):
        for corpo in (b'{"status": "iniciando"}', b'{"ok": true}', b"[]",
                      b"ok", b'"ok"', b"{}"):
            self.pre_rejeita(
                respostas_validas(**{gate.CAMINHO_HEALTH: (200, corpo)})
            )

    # ── o Command Center não pode voltar a vazar (prova NEGATIVA) ────────

    def test_painel_anonimo_servindo_o_command_center_e_rejeitado(self):
        """A regressão da M25.23: o painel administrativo antes do login."""
        self.pre_rejeita(
            respostas_validas(
                **{gate.CAMINHO_PAINEL: (200, INDEX_ADMIN.encode("utf-8"))}
            ),
            trecho="vazou marcação",
        )

    def test_painel_anonimo_vazando_so_a_bancada_de_laudos_e_rejeitado(self):
        vazado = LOGIN_HTML.replace(
            "</body>",
            '<section id="laudos-espirometria"></section></body>',
        )
        self.pre_rejeita(
            respostas_validas(
                **{gate.CAMINHO_PAINEL: (200, vazado.encode("utf-8"))}
            ),
            trecho="vazou marcação",
        )

    def test_painel_anonimo_sem_a_tela_de_login_e_rejeitado(self):
        self.pre_rejeita(
            respostas_validas(
                **{gate.CAMINHO_PAINEL: (200, b"<html><body>ok</body></html>")}
            ),
            trecho="tela de login",
        )

    def test_config_publico_anonimo_e_rejeitado(self):
        """Tornar o manifesto público de novo faria o go-live abortar."""
        self.pre_rejeita(
            respostas_validas(
                **{
                    gate.CAMINHO_CONFIG: (
                        200,
                        b'{"enabled": true, '
                        b'"api_base": "/painel-soprolife/api/m15"}',
                    )
                }
            ),
            trecho="voltou a ser público",
        )

    def test_config_com_status_inesperado_e_rejeitado(self):
        for status in (403, 404, 500, 302):
            self.pre_rejeita(
                respostas_validas(
                    **{gate.CAMINHO_CONFIG: (status, CORPO_401)}
                ),
                trecho="exigido 401",
            )

    def test_401_que_vaza_conteudo_do_manifesto_e_rejeitado(self):
        self.pre_rejeita(
            respostas_validas(
                **{
                    gate.CAMINHO_CONFIG: (
                        401,
                        b'{"ok": false, "enabled": true}',
                    )
                }
            ),
            trecho="carrega conteúdo do manifesto",
        )

    # ── postflight: serviço e bytes servidos conferem com o release ──────

    def test_pos_health_de_outra_versao_rejeitado(self):
        corpo = json.dumps(
            {
                "status": "ok",
                "versao": "0.0.1",
                "ambiente": "prod",
                "banco": "ok",
            }
        ).encode("utf-8")
        self.pos_rejeita(
            respostas_validas(**{gate.CAMINHO_HEALTH: (200, corpo)}),
            trecho="não é a do release implantado",
        )

    def test_pos_health_sem_ambiente_prod_rejeitado(self):
        corpo = json.dumps(
            {
                "status": "ok",
                "versao": VERSAO_RELEASE,
                "ambiente": "dev",
                "banco": "ok",
            }
        ).encode("utf-8")
        self.pos_rejeita(
            respostas_validas(**{gate.CAMINHO_HEALTH: (200, corpo)}),
            trecho='ambiente "prod"',
        )

    def test_pos_health_com_banco_degradado_rejeitado(self):
        corpo = json.dumps(
            {
                "status": "ok",
                "versao": VERSAO_RELEASE,
                "ambiente": "prod",
                "banco": "erro",
            }
        ).encode("utf-8")
        self.pos_rejeita(
            respostas_validas(**{gate.CAMINHO_HEALTH: (200, corpo)}),
            trecho="banco saudável",
        )

    def test_pos_m15_security_nao_200_rejeitado(self):
        self.pos_rejeita(
            respostas_validas(**{gate.CAMINHO_SECURITY_JS: (404, b"")})
        )

    def test_pos_m15_security_de_outro_release_rejeitado(self):
        self.pos_rejeita(
            respostas_validas(
                **{gate.CAMINHO_SECURITY_JS: (200, b"/* release antigo */")}
            ),
            trecho="não é o do release implantado",
        )

    def test_pos_tela_de_login_de_outro_release_rejeitada(self):
        antiga = LOGIN_HTML.replace("</body>", "<!-- release antigo --></body>")
        self.pos_rejeita(
            respostas_validas(
                **{gate.CAMINHO_PAINEL: (200, antiga.encode("utf-8"))}
            ),
            trecho="não é o do release implantado",
        )

    def test_pos_release_reprovado_no_check_source_rejeitado(self):
        """O postflight reexamina os artefatos administrativos na fonte local."""
        outro = tempfile.TemporaryDirectory()
        self.addCleanup(outro.cleanup)
        release = montar_release(
            pathlib.Path(outro.name),
            **{
                "painel-soprolife/index.html":
                    '<script src="./js/m15-nucleo.js?v=1" defer></script>'
                    '<script src="./js/m15-security.js?v=1" defer></script>'
            },
        )
        self.pos_rejeita(respostas_validas(), release=release)

    def test_pos_release_sem_versao_legivel_rejeitado(self):
        outro = tempfile.TemporaryDirectory()
        self.addCleanup(outro.cleanup)
        release = montar_release(
            pathlib.Path(outro.name),
            **{"painel-soprolife/nucleo-m15/app/__init__.py": '"""sem versão."""\n'},
        )
        self.pos_rejeita(respostas_validas(), release=release, trecho="__version__")


class TestAridadeDaCli(unittest.TestCase):
    """check-https-pos exige o repo root; um argumento a mais/menos é erro."""

    def test_pos_sem_repo_root_e_erro_de_uso(self):
        self.assertEqual(gate.main(["gate", "check-https-pos", BASE]), 2)

    def test_pre_com_argumento_extra_e_erro_de_uso(self):
        self.assertEqual(
            gate.main(["gate", "check-https-pre", BASE, "/tmp"]), 2
        )

    def test_subcomando_desconhecido_e_erro_de_uso(self):
        self.assertEqual(gate.main(["gate", "check-https-durante", BASE]), 2)


class TestChecagensEstaticas(unittest.TestCase):
    def montar_alvo(self, **mudancas):
        return montar_release(pathlib.Path(self.tmp.name), **mudancas)

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
