#!/usr/bin/env python3
"""M26.21 — Compatibilidade entre o gate de autenticação e os gates de go-live.

Por que este arquivo existe
---------------------------
O deploy do commit f9c0761 passou por tudo (URL HTTPS, checagens estáticas,
painel 200, health status=ok, backup verificado) e abortou ANTES de qualquer
mutação com::

    ERRO REPORTS GO-LIVE (fail-closed): reports_https_workspace_markup_missing

O workspace de laudos ESTAVA no release. O que aconteceu foi outra coisa: os
gates de go-live nasceram na M15.5B (2026-07) e na M24B, quando o painel
inteiro ainda era servido sem autenticação, e faziam GET ANÔNIMO pedindo o
index.html administrativo e o m15-config.json. A M25.23 (2026-08) fechou esse
vazamento — hoje o anônimo recebe login.html e 401 —, e ninguém reconciliou os
gates. Este teste prova as DUAS metades ao mesmo tempo, para que elas não
voltem a divergir em silêncio:

  A. o servidor continua fechado para quem não tem sessão;
  B. os gates provam o release SEM depender de nada disso estar aberto.

Tudo offline: nenhuma porta, nenhum socket, nenhuma credencial.

Uso: python3 painel-soprolife/nucleo-m15/scripts/test_m26_21_go_live_auth_compat.py
"""

import email.message
import importlib.util
import io
import json
import os
import pathlib
import sys
import types
import unittest
from unittest import mock

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(SCRIPT_DIR))

import go_live_https_gate as go_live  # noqa: E402
import reports_go_live_gate as reports  # noqa: E402

PANEL_SCRIPTS = REPO_ROOT / "painel-soprolife/scripts"
sys.path.insert(0, str(PANEL_SCRIPTS))
import panel_access_gate as access_gate  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "command_center_local_server", PANEL_SCRIPTS / "command-center-local-server.py"
)
servidor = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(servidor)

BASE = "https://painel-privado.exemplo.ts.net"
LOGIN_BYTES = (REPO_ROOT / "painel-soprolife/login.html").read_bytes()
GUARDA_BYTES = (REPO_ROOT / "painel-soprolife/js/m15-security.js").read_bytes()
INDEX_TEXTO = (REPO_ROOT / "painel-soprolife/index.html").read_text(encoding="utf-8")


class Requisicao(servidor._Handler):
    """Uma requisição HTTP sem socket — mesmo handler, respostas capturadas."""

    def __init__(self, path, method="GET", headers=None):
        self.path = path
        self.command = method
        self.directory = os.getcwd()
        self.headers = email.message.Message()
        for nome, valor in (headers or {}).items():
            self.headers[nome] = valor
        self.rfile = io.BytesIO(b"")
        self.wfile = io.BytesIO()
        self.statuses = []

    def send_response(self, code, message=None):
        self.statuses.append(code)

    def send_header(self, name, value):
        pass

    def end_headers(self):
        pass

    def send_error(self, code, message=None, explain=None):
        self.statuses.append(code)

    @property
    def status(self):
        return self.statuses[-1] if self.statuses else None

    @property
    def corpo(self):
        return self.wfile.getvalue()


def anonimo(path, method="GET"):
    """Executa a requisição SEM sessão válida e devolve o handler."""
    requisicao = Requisicao(path, method)
    with mock.patch.object(servidor, "_session_identity", return_value=None):
        with mock.patch.object(
            servidor.http.server.SimpleHTTPRequestHandler,
            "do_" + method,
            autospec=True,
        ) as delegado:
            getattr(requisicao, "do_" + method)()
    requisicao.delegou = delegado.called
    return requisicao


class TestSuperficieAnonimaDoServidor(unittest.TestCase):
    """A. O que o servidor real entrega a quem não tem sessão."""

    @classmethod
    def setUpClass(cls):
        cls._cwd = os.getcwd()
        os.chdir(REPO_ROOT)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls._cwd)

    def test_entrada_do_painel_devolve_a_tela_de_login_e_nunca_o_command_center(self):
        for alvo in ("/painel-soprolife/", "/painel-soprolife/index.html"):
            requisicao = anonimo(alvo)
            self.assertEqual(requisicao.status, 200, alvo)
            self.assertFalse(requisicao.delegou, alvo)
            self.assertEqual(requisicao.corpo, LOGIN_BYTES, alvo)

    def test_a_tela_de_login_servida_nao_carrega_marcacao_administrativa(self):
        html = LOGIN_BYTES.decode("utf-8")
        for marcador in go_live.MARCADORES_ADMIN_PROIBIDOS:
            self.assertNotIn(marcador, html)
        for marcador in go_live.MARCADORES_TELA_LOGIN:
            self.assertIn(marcador, html)

    def test_manifesto_de_boot_exige_sessao(self):
        requisicao = anonimo("/painel-soprolife/data/m15-config.json")
        self.assertEqual(requisicao.status, 401)
        self.assertFalse(requisicao.delegou)
        corpo = requisicao.corpo.decode("utf-8")
        for fragmento in go_live.FRAGMENTOS_CONFIG_PROIBIDOS:
            self.assertNotIn(fragmento, corpo)

    def test_dado_operacional_e_assets_administrativos_exigem_sessao(self):
        for alvo in (
            "/painel-soprolife/data/financeiro-summary.local.json",
            "/painel-soprolife/js/m15-nucleo.js",
            "/painel-soprolife/js/report-workflow.js",
        ):
            requisicao = anonimo(alvo)
            self.assertEqual(requisicao.status, 401, alvo)
            self.assertFalse(requisicao.delegou, alvo)

    def test_fonte_privada_codigo_e_repositorio_nao_existem_por_http(self):
        for alvo in (
            "/painel-soprolife/data-private/leads.json",
            "/painel-soprolife/nucleo-m15/app/config.py",
            "/painel-soprolife/scripts/command-center-local-server.py",
            "/.git/config",
        ):
            requisicao = anonimo(alvo)
            self.assertEqual(requisicao.status, 404, alvo)
            self.assertFalse(requisicao.delegou, alvo)

    def test_head_passa_pelo_mesmo_gate(self):
        requisicao = anonimo("/painel-soprolife/data/m15-config.json", method="HEAD")
        self.assertEqual(requisicao.status, 401)

    def test_a_guarda_de_contexto_seguro_continua_publica_de_proposito(self):
        """m15-security.js é o único JS que a tela de login carrega."""
        self.assertEqual(
            access_gate.classify("/painel-soprolife/js/m15-security.js"),
            access_gate.PUBLIC,
        )
        requisicao = anonimo("/painel-soprolife/js/m15-security.js")
        self.assertTrue(requisicao.delegou)


class TestReleaseRealSatisfazOsGates(unittest.TestCase):
    """B1. O release deste checkout é um alvo válido, provado localmente."""

    def test_check_source_aprova_o_release_deste_checkout(self):
        go_live.checar_fonte_alvo(str(REPO_ROOT))

    def test_workspace_de_laudos_existe_no_release(self):
        enabled, mode = reports.check_release_workspace(REPO_ROOT)
        self.assertIsInstance(enabled, bool)
        self.assertIn(mode, reports.REPORTS_MODES)
        for marcador in reports.REPORTS_WORKFLOW_MARKERS:
            self.assertIn(marcador, INDEX_TEXTO)

    def test_o_index_administrativo_tem_exatamente_o_que_nao_pode_vazar(self):
        """Fecha o laço: o que o gate proíbe no anônimo é o que o index tem."""
        for marcador in go_live.MARCADORES_ADMIN_PROIBIDOS:
            self.assertIn(marcador, INDEX_TEXTO)


def respostas(*, efetivo="disabled", **mudancas):
    """A superfície anônima REAL da VPS, com o backend no estado indicado."""
    api = {
        "pilot": (401, {"erro": {"codigo": "http_401"}}),
        "disabled": (503, {"erro": {"codigo": "relatorios_desabilitados"}}),
        "production_blocked": (
            503,
            {"erro": {"codigo": "relatorios_producao_bloqueada"}},
        ),
    }[efetivo]
    versao = go_live._versao_do_release(str(REPO_ROOT))
    tabela = {
        BASE + go_live.CAMINHO_PAINEL: (200, LOGIN_BYTES),
        BASE + go_live.CAMINHO_HEALTH: (
            200,
            json.dumps(
                {
                    "status": "ok",
                    "versao": versao,
                    "ambiente": "prod",
                    "banco": "ok",
                }
            ).encode("utf-8"),
        ),
        BASE + go_live.CAMINHO_CONFIG: (
            401,
            b'{"ok": false, "error": "Sessao necessaria."}',
        ),
        BASE + go_live.CAMINHO_SECURITY_JS: (200, GUARDA_BYTES),
        BASE + reports.REPORTS_API_PATH: (api[0], json.dumps(api[1]).encode()),
    }
    tabela.update({BASE + caminho: valor for caminho, valor in mudancas.items()})
    return tabela


def getter(tabela):
    def falso(url, prazo, opener=None):
        if url not in tabela:
            raise go_live.GateError(f"URL inesperada no dublê: {url}")
        return tabela[url]

    return falso


class TestGatesContraASuperficieAnonimaReal(unittest.TestCase):
    """B2. Os probes rodam contra o corpo REAL que a VPS devolve hoje."""

    def test_preflight_geral_aceita_a_superficie_anonima_atual(self):
        with mock.patch.object(go_live, "http_get", side_effect=getter(respostas())):
            go_live.checar_https_pre(BASE + "/")

    def test_postflight_geral_verifica_o_release_sem_vazamento_anonimo(self):
        with mock.patch.object(go_live, "http_get", side_effect=getter(respostas())):
            go_live.checar_https_pos(BASE + "/", str(REPO_ROOT))

    def test_preflight_de_laudos_aceita_o_backend_ainda_desabilitado(self):
        """O cenário exato do incidente: checkout já no release alvo, serviços
        ainda no anterior. Antes isto abortava em workspace_markup_missing."""
        reports.check_https_workspace(
            BASE,
            repo_root=REPO_ROOT,
            expected_enabled=None,
            expected_mode=None,
            http_get=getter(respostas(efetivo="disabled")),
        )

    def test_postflight_de_laudos_exige_piloto_efetivo(self):
        resultado = reports.check_pilot_postflight(
            repo_root=REPO_ROOT,
            mode_value="pilot",
            backend_flag="true",
            https_base_url=BASE,
            http_get=getter(respostas(efetivo="pilot")),
        )
        self.assertIs(resultado, True)

    def test_postflight_de_laudos_recusa_backend_ainda_desabilitado(self):
        with self.assertRaises(reports.ReportsGateError) as capturado:
            reports.check_pilot_postflight(
                repo_root=REPO_ROOT,
                mode_value="pilot",
                backend_flag="true",
                https_base_url=BASE,
                http_get=getter(respostas(efetivo="disabled")),
            )
        self.assertEqual(
            str(capturado.exception), "reports_https_target_flag_mismatch"
        )

    def test_qualquer_vazamento_anonimo_derruba_os_dois_gates(self):
        vazando = {go_live.CAMINHO_PAINEL: (200, INDEX_TEXTO.encode("utf-8"))}
        with mock.patch.object(
            go_live, "http_get", side_effect=getter(respostas(**vazando))
        ):
            with self.assertRaises(go_live.GateError):
                go_live.checar_https_pre(BASE + "/")
        with self.assertRaises(reports.ReportsGateError) as capturado:
            reports.check_https_workspace(
                BASE,
                repo_root=REPO_ROOT,
                expected_enabled=None,
                http_get=getter(respostas(**vazando)),
            )
        self.assertEqual(
            str(capturado.exception), "reports_https_workspace_markup_leaked"
        )

    def test_manifesto_publico_derruba_os_dois_gates(self):
        publico = {
            go_live.CAMINHO_CONFIG: (
                200,
                b'{"enabled": true, "api_base": "/painel-soprolife/api/m15"}',
            )
        }
        with mock.patch.object(
            go_live, "http_get", side_effect=getter(respostas(**publico))
        ):
            with self.assertRaises(go_live.GateError):
                go_live.checar_https_pre(BASE + "/")
        with self.assertRaises(reports.ReportsGateError) as capturado:
            reports.check_https_workspace(
                BASE,
                repo_root=REPO_ROOT,
                expected_enabled=None,
                http_get=getter(respostas(**publico)),
            )
        self.assertEqual(
            str(capturado.exception), "reports_https_config_not_protected"
        )

    def test_resposta_de_laudos_irreconhecivel_falha_fechado(self):
        for status, corpo in (
            (200, b"{}"),
            (503, b'{"erro":{"codigo":"outra_coisa"}}'),
            (401, b'{"erro":{"codigo":"relatorios_desabilitados"}}'),
            (500, b"nao e json"),
        ):
            with self.assertRaises(reports.ReportsGateError) as capturado:
                reports.check_https_workspace(
                    BASE,
                    repo_root=REPO_ROOT,
                    expected_enabled=None,
                    http_get=getter(
                        respostas(**{reports.REPORTS_API_PATH: (status, corpo)})
                    ),
                )
            self.assertEqual(
                str(capturado.exception), "reports_https_api_response_invalid"
            )


class TestDeploySemCredencialEAuditavel(unittest.TestCase):
    """C. O deploy prova o estado real sem virar portador de credencial."""

    ARQUIVOS = (
        "deploy-producao-vps.sh",
        "lib-go-live-gate.sh",
        "lib-reports-go-live-gate.sh",
        "go_live_https_gate.py",
        "reports_go_live_gate.py",
        "activate-reports-pilot-vps.sh",
    )

    def textos(self):
        return {nome: (SCRIPT_DIR / nome).read_text(encoding="utf-8")
                for nome in self.ARQUIVOS}

    def test_nenhuma_credencial_administrativa_entra_no_deploy(self):
        """Nenhum script de deploy/gate se autentica no painel.

        `M15_AUTH_SECRET` NÃO entra nesta lista de propósito: o deploy gera e
        instala o segredo do PRÓPRIO serviço no EnvironmentFile, que é outra
        coisa — ele nunca o usa para forjar sessão nem para provar nada.
        """
        proibidos = (
            "Cookie",
            "Authorization",
            "Bearer",
            "/auth/token",
            "/auth/login",
            "/auth/me",
        )
        for nome, texto in self.textos().items():
            for termo in proibidos:
                # `in` cru numa string de 20 KB produziria um diff ilegível no
                # relatório de falha; aqui só o veredito importa.
                self.assertTrue(
                    termo not in texto,
                    f"{nome} menciona {termo} (credencial no deploy)",
                )
            self.assertTrue(
                "AUTH_SECRET" not in texto.split("TEMP_ENV")[0],
                f"{nome} lê o segredo de sessão antes de montar o EnvironmentFile",
            )

    def test_os_probes_continuam_anonimos_por_construcao(self):
        """http_get não tem parâmetro de cabeçalho: não há como autenticar."""
        import inspect

        assinatura = inspect.signature(go_live.http_get)
        self.assertEqual(
            list(assinatura.parameters), ["url", "prazo_final", "opener"]
        )

    def test_deploy_continua_interativo_e_confirmado_a_mao(self):
        deploy = (SCRIPT_DIR / "deploy-producao-vps.sh").read_text(encoding="utf-8")
        self.assertIn("[[ -t 0 && -t 1 ]]", deploy)
        self.assertIn("IMPLANTAR M15", deploy)
        self.assertIn("git bundle create", deploy)
        self.assertIn("pg_dump --format=custom", deploy)

    def test_postflight_geral_recebe_o_repo_root_do_release_implantado(self):
        deploy = (SCRIPT_DIR / "deploy-producao-vps.sh").read_text(encoding="utf-8")
        self.assertIn(
            'soprolife_go_live_validar_https pos "${SOPROLIFE_M15_HTTPS_BASE_URL-}" \\\n'
            '    "$REPO_ROOT"',
            deploy,
        )

    def test_gates_continuam_antes_de_prompt_sudo_e_backup(self):
        deploy = (SCRIPT_DIR / "deploy-producao-vps.sh").read_text(encoding="utf-8")
        gate_laudos = deploy.index("soprolife_reports_go_live_read_target_mode")
        for marcador in (
            "Digite exatamente 'IMPLANTAR M15'",
            "\nsudo -v\n",
            'sudo install -d -o root -g root -m 0700 "$BACKUP_DIR"',
        ):
            self.assertLess(gate_laudos, deploy.index(marcador), marcador)


if __name__ == "__main__":
    unittest.main(verbosity=2)
