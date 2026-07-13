#!/usr/bin/env python3
"""SoproLife — M14.3A.2 — testes offline do kit de paridade Apps Script.

Cobre: determinismo do gerador, --check, hashes/linhas, extração de
símbolos, dependências, todas as fixtures do comparador, exit codes,
JSON report, --strict, --no-write, pacote (recusado em BLOCKED, produzido
em READY, SHA256SUMS, allowlist, ausência de segredo).

100% offline. Escreve SOMENTE em diretórios temporários (/tmp via
tempfile). Nunca modifica o repositório.

USO: python3 painel-soprolife/scripts/test-apps-script-parity.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, "..", ".."))
APPS = os.path.join(RAIZ, "painel-soprolife", "apps-script")
FIXTURES = os.path.join(AQUI, "fixtures", "apps-script-parity")
GERADOR = os.path.join(AQUI, "generate-apps-script-publication-manifest.py")
COMPARADOR = os.path.join(AQUI, "compare-apps-script-export.py")
PACOTE = os.path.join(AQUI, "prepare-apps-script-publication-pack.sh")
MANIFESTO = os.path.join(
    RAIZ, "painel-soprolife", "core", "contracts",
    "apps-script-publication-manifest.json",
)

sys.dont_write_bytecode = True  # nenhum bytecode no repositório
sys.path.insert(0, AQUI)
import apps_script_parity as parity  # noqa: E402

AMBIENTE = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

# Contagens de globals por arquivo validadas contra o relatório Codex
# 20260712-221016 (seção B: 145 globals).
GLOBAIS_ESPERADOS = {
    "command-center-api.gs": 84,
    "contratos-canonicos.gs": 20,
    "pastore-staging.gs": 4,
    "converter-lead-em-paciente.gs": 4,
    "sync-crm-pacientes.gs": 2,
    "organizar-leads-operacionais.gs": 2,
    "soprolife-sheets-template.gs": 8,
    "manual-das-abas.gs": 5,
    "limpar-leads-e-manual-abas.gs": 16,
}

# Conteúdo permitido do pacote (allowlist EXATA, além de arquivos/*.gs).
PACOTE_ALLOWLIST = {
    "apps-script-publication-manifest.json",
    "SHA256SUMS",
    "INVENTARIO-SIMBOLOS.txt",
    "ORDEM-DE-COPIA.txt",
    "CHECKLIST-PUBLICACAO.md",
    "ROLLBACK.md",
    "evidencia-comparacao.json",
    "evidencia-comparacao.txt",
}


def rodar(cmd, **kw):
    return subprocess.run(
        cmd, capture_output=True, text=True, env=AMBIENTE, cwd=RAIZ, **kw
    )


def compor_remoto(fixture_dir, destino):
    """Monta o diretório remoto sintético a partir de fixture.json."""
    with open(os.path.join(fixture_dir, "fixture.json"), encoding="utf-8") as f:
        spec = json.load(f)
    os.makedirs(destino, exist_ok=True)
    if spec.get("base") == "canonico":
        for nome in parity.ARQUIVOS_CANONICOS:
            shutil.copy2(os.path.join(APPS, nome), os.path.join(destino, nome))
    for nome in spec.get("remover", []):
        os.remove(os.path.join(destino, nome))
    for velho, novo in spec.get("renomear", {}).items():
        os.rename(os.path.join(destino, velho), os.path.join(destino, novo))
    for nome in sorted(os.listdir(fixture_dir)):
        if nome == "fixture.json":
            continue
        shutil.copy2(os.path.join(fixture_dir, nome),
                     os.path.join(destino, nome))
    return spec


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="soprolife-parity-test.")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class TestGerador(Base):
    def test_determinismo(self):
        a = os.path.join(self.tmp, "a.json")
        b = os.path.join(self.tmp, "b.json")
        for saida in (a, b):
            r = rodar([sys.executable, GERADOR, "--write", "--output", saida])
            self.assertEqual(r.returncode, 0, r.stderr)
        with open(a, "rb") as fa, open(b, "rb") as fb:
            self.assertEqual(fa.read(), fb.read(),
                             "duas gerações produziram bytes diferentes")

    def test_check_manifesto_commitado_em_dia(self):
        r = rodar([sys.executable, GERADOR, "--check"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_check_falha_com_manifesto_divergente(self):
        alvo = os.path.join(self.tmp, "velho.json")
        with open(MANIFESTO, encoding="utf-8") as f:
            dados = json.load(f)
        dados["total_globais"] = 999
        with open(alvo, "w", encoding="utf-8") as f:
            json.dump(dados, f)
        r = rodar([sys.executable, GERADOR, "--check", "--output", alvo])
        self.assertNotEqual(r.returncode, 0)

    def test_hashes_linhas_bytes_do_manifesto(self):
        with open(MANIFESTO, encoding="utf-8") as f:
            manifesto = json.load(f)
        self.assertEqual(manifesto["total_arquivos"], 9)
        self.assertEqual(manifesto["total_globais"], 145)
        for arq in manifesto["arquivos"]:
            caminho = os.path.join(RAIZ, arq["caminho_local"])
            with open(caminho, "rb") as f:
                dados = f.read()
            self.assertEqual(arq["sha256"], parity.sha256_hex(dados),
                             arq["nome_apps_script"])
            self.assertEqual(arq["bytes"], len(dados), arq["nome_apps_script"])
            texto = dados.decode("utf-8")
            linhas = texto.count("\n") + (0 if texto.endswith("\n") else 1)
            self.assertEqual(arq["linhas"], linhas, arq["nome_apps_script"])

    def test_extracao_de_simbolos_por_arquivo(self):
        with open(MANIFESTO, encoding="utf-8") as f:
            manifesto = json.load(f)
        por_nome = {a["nome_apps_script"]: a for a in manifesto["arquivos"]}
        for nome, esperado in GLOBAIS_ESPERADOS.items():
            self.assertEqual(len(por_nome[nome]["globais"]), esperado, nome)

    def test_entrypoints(self):
        with open(MANIFESTO, encoding="utf-8") as f:
            manifesto = json.load(f)
        por_nome = {a["nome_apps_script"]: a for a in manifesto["arquivos"]}
        api = {e["nome"]: e["tipo"]
               for e in por_nome["command-center-api.gs"]["entrypoints"]}
        self.assertEqual(api.get("doPost"), "web_app")
        template = {e["nome"]: e["tipo"]
                    for e in por_nome["soprolife-sheets-template.gs"]["entrypoints"]}
        self.assertEqual(template.get("onOpen"), "trigger_simples")
        self.assertEqual(template.get("onEdit"), "trigger_simples")
        manual = {e["nome"]: e["tipo"]
                  for e in por_nome["manual-das-abas.gs"]["entrypoints"]}
        self.assertEqual(manual.get("onOpen_manualAbas"), "trigger_nomeado")

    def test_dependencias(self):
        with open(MANIFESTO, encoding="utf-8") as f:
            manifesto = json.load(f)
        por_nome = {a["nome_apps_script"]: a for a in manifesto["arquivos"]}
        deps_api = por_nome["command-center-api.gs"]["dependencias"]
        self.assertIn("contratos-canonicos.gs", deps_api)
        self.assertIn("_cvConverterLeadCore",
                      deps_api["converter-lead-em-paciente.gs"])
        self.assertIn("_registrarAtendimentoPastore",
                      deps_api["pastore-staging.gs"])
        deps_pastore = por_nome["pastore-staging.gs"]["dependencias"]
        self.assertIn("ctValidarDataComPrecisao",
                      deps_pastore["contratos-canonicos.gs"])
        deps_limpar = por_nome["limpar-leads-e-manual-abas.gs"]["dependencias"]
        self.assertIn("atualizarManualDasAbasSoproLife",
                      deps_limpar["manual-das-abas.gs"])

    def test_falha_com_canonico_ausente(self):
        alt = os.path.join(self.tmp, "apps-script")
        os.makedirs(alt)
        for nome in parity.ARQUIVOS_CANONICOS[:-1]:
            shutil.copy2(os.path.join(APPS, nome), os.path.join(alt, nome))
        r = rodar([sys.executable, GERADOR, "--print-summary",
                   "--apps-script-dir", alt])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ausente", r.stderr)

    def test_falha_com_global_duplicado(self):
        alt = os.path.join(self.tmp, "apps-script")
        os.makedirs(alt)
        for nome in parity.ARQUIVOS_CANONICOS:
            shutil.copy2(os.path.join(APPS, nome), os.path.join(alt, nome))
        with open(os.path.join(alt, "organizar-leads-operacionais.gs"),
                  "a", encoding="utf-8") as f:
            f.write("\nvar doPost = null;\n")
        r = rodar([sys.executable, GERADOR, "--print-summary",
                   "--apps-script-dir", alt])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("duplicado", r.stderr)


class TestComparadorFixtures(Base):
    def _rodar_fixture(self, caso, extra_args=()):
        fixture_dir = os.path.join(FIXTURES, caso)
        remoto = os.path.join(self.tmp, "remoto-" + caso)
        spec = compor_remoto(fixture_dir, remoto)
        json_report = os.path.join(self.tmp, f"rel-{caso}.json")
        cmd = [sys.executable, COMPARADOR, "--remote-dir", remoto,
               "--json-report", json_report] + list(extra_args)
        r = rodar(cmd)
        relatorio = None
        if os.path.isfile(json_report):
            with open(json_report, encoding="utf-8") as f:
                relatorio = json.load(f)
        return spec, r, relatorio

    def test_todas_as_fixtures(self):
        casos = sorted(
            d for d in os.listdir(FIXTURES)
            if os.path.isdir(os.path.join(FIXTURES, d))
        )
        self.assertEqual(len(casos), 17, "esperava 17 fixtures")
        for caso in casos:
            with self.subTest(caso=caso):
                spec, r, relatorio = self._rodar_fixture(caso)
                self.assertIsNotNone(relatorio, r.stdout + r.stderr)
                self.assertEqual(relatorio["resultado"], spec["esperado"],
                                 f"{caso}: {r.stdout}")
                if spec["esperado"] == "BLOCKED":
                    self.assertEqual(r.returncode, 2, caso)
                else:
                    self.assertEqual(r.returncode, 0, caso)
                codigos = set(a["codigo"] for a in relatorio["achados"])
                for esperado in spec["achados_esperados"]:
                    self.assertIn(esperado, codigos, caso)

    def test_allow_extra_torna_ready(self):
        spec, r, relatorio = self._rodar_fixture(
            "04-extra-remoto",
            extra_args=[arg for nome in
                        json.load(open(os.path.join(
                            FIXTURES, "04-extra-remoto", "fixture.json")))
                        ["allow_extra"]
                        for arg in ("--allow-extra", nome)],
        )
        self.assertTrue(spec["allow_extra_torna_ready"])
        self.assertEqual(relatorio["resultado"], "READY", r.stdout)
        self.assertEqual(r.returncode, 0)
        codigos = set(a["codigo"] for a in relatorio["achados"])
        self.assertIn("EXTRA_PERMITIDO", codigos)

    def test_regex_com_texto_de_segredo_nao_e_falso_positivo(self):
        """Correção final M14.3A.2: texto com cara de segredo DENTRO de um
        literal regex (ex.: /apiToken="x"/) nunca é uma atribuição real —
        só o arquivo extra em si pode bloquear, nunca SEGREDO_SUSPEITO."""
        caso = "17-regex-com-texto-de-segredo"
        spec, r, relatorio = self._rodar_fixture(caso)
        self.assertEqual(relatorio["resultado"], "BLOCKED", r.stdout)
        codigos = set(a["codigo"] for a in relatorio["achados"])
        self.assertIn("EXTRA_NAO_PERMITIDO", codigos)
        self.assertNotIn("SEGREDO_SUSPEITO", codigos)

        spec, r, relatorio = self._rodar_fixture(
            caso, extra_args=["--allow-extra", "anotacoes-regex-sinteticas.gs"],
        )
        self.assertTrue(spec["allow_extra_torna_ready"])
        self.assertEqual(relatorio["resultado"], "READY", r.stdout)
        self.assertEqual(r.returncode, 0)
        codigos = set(a["codigo"] for a in relatorio["achados"])
        self.assertNotIn("SEGREDO_SUSPEITO", codigos)

    def test_segredo_real_bloqueia_mesmo_com_allow_extra(self):
        """Um extra autorizado com uma atribuição REAL de segredo (fora de
        regex) continua BLOCKED — --allow-extra tolera o arquivo existir,
        nunca o conteúdo dele conter credencial."""
        spec, r, relatorio = self._rodar_fixture(
            "16-segredo-sintetico",
            extra_args=["--allow-extra", "configuracao-privada-sintetica.gs"],
        )
        self.assertEqual(relatorio["resultado"], "BLOCKED", r.stdout)
        self.assertEqual(r.returncode, 2)
        codigos = set(a["codigo"] for a in relatorio["achados"])
        self.assertIn("SEGREDO_SUSPEITO", codigos)

    def test_strict_falha_com_aviso(self):
        _, r, relatorio = self._rodar_fixture(
            "04-extra-remoto",
            extra_args=["--allow-extra", "anotacoes-operacionais-sinteticas.gs",
                        "--strict"],
        )
        self.assertEqual(relatorio["resultado"], "READY")
        self.assertEqual(r.returncode, 3, "strict deve falhar com avisos")

    def test_strict_passa_sem_divergencia(self):
        _, r, relatorio = self._rodar_fixture("01-remoto-identico",
                                              extra_args=["--strict"])
        self.assertEqual(relatorio["resultado"], "READY")
        self.assertEqual(r.returncode, 0)

    def test_no_write_nao_grava_relatorios(self):
        remoto = os.path.join(self.tmp, "remoto-nw")
        compor_remoto(os.path.join(FIXTURES, "01-remoto-identico"), remoto)
        rel_txt = os.path.join(self.tmp, "nw.txt")
        rel_json = os.path.join(self.tmp, "nw.json")
        r = rodar([sys.executable, COMPARADOR, "--remote-dir", remoto,
                   "--report", rel_txt, "--json-report", rel_json,
                   "--no-write"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(rel_txt))
        self.assertFalse(os.path.exists(rel_json))
        self.assertIn("RESULTADO: READY", r.stdout)

    def test_estrutura_do_json_report(self):
        _, r, relatorio = self._rodar_fixture("01-remoto-identico")
        for chave in ("relatorio", "resultado", "risco_coexistencia",
                      "contagens", "identicos", "ausentes", "divergentes",
                      "extras", "globais_remotos", "entrypoints_remotos",
                      "achados"):
            self.assertIn(chave, relatorio)
        self.assertEqual(relatorio["contagens"]["identicos"], 9)
        self.assertEqual(
            sum(len(v) for v in relatorio["globais_remotos"].values()), 145)


class TestPacote(Base):
    def _remoto_ready(self):
        remoto = os.path.join(self.tmp, "remoto-ok")
        compor_remoto(os.path.join(FIXTURES, "01-remoto-identico"), remoto)
        return remoto

    def test_dry_run_nao_escreve(self):
        saida = os.path.join(self.tmp, "pack-dry")
        r = rodar(["bash", PACOTE, "--output", saida,
                   "--remote-dir", self._remoto_ready()])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("DRY-RUN", r.stdout)
        self.assertFalse(os.path.exists(saida), "dry-run não pode escrever")

    def test_execute_exige_remote_dir(self):
        saida = os.path.join(self.tmp, "pack-sem-remoto")
        r = rodar(["bash", PACOTE, "--output", saida, "--execute"])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(saida))

    def test_recusa_output_dentro_do_repo(self):
        r = rodar(["bash", PACOTE, "--output",
                   os.path.join(RAIZ, "painel-soprolife", "pack-proibido")])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DENTRO do repositório", r.stderr)
        self.assertFalse(os.path.exists(
            os.path.join(RAIZ, "painel-soprolife", "pack-proibido")))

    def test_pacote_recusado_em_blocked(self):
        remoto = os.path.join(self.tmp, "remoto-blk")
        compor_remoto(os.path.join(FIXTURES, "02-canonico-ausente"), remoto)
        saida = os.path.join(self.tmp, "pack-blk")
        r = rodar(["bash", PACOTE, "--output", saida,
                   "--remote-dir", remoto, "--execute"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("recusado", r.stderr + r.stdout)
        self.assertFalse(os.path.exists(saida),
                         "pacote BLOCKED não pode deixar rastros")

    def test_pacote_produzido_em_ready(self):
        saida = os.path.join(self.tmp, "pack-ok")
        r = rodar(["bash", PACOTE, "--output", saida,
                   "--remote-dir", self._remoto_ready(), "--execute"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # Allowlist exata: nada além do esperado.
        raiz_itens = set(os.listdir(saida))
        self.assertEqual(raiz_itens, PACOTE_ALLOWLIST | {"arquivos"})
        gs = set(os.listdir(os.path.join(saida, "arquivos")))
        self.assertEqual(gs, set(parity.ARQUIVOS_CANONICOS),
                         "arquivos/ deve ter exatamente os 9 canônicos")

        # SHA256SUMS confere byte a byte.
        v = subprocess.run(["sha256sum", "-c", "../SHA256SUMS"],
                           cwd=os.path.join(saida, "arquivos"),
                           capture_output=True, text=True)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)

        # Conteúdo dos .gs idêntico ao Git.
        for nome in parity.ARQUIVOS_CANONICOS:
            with open(os.path.join(APPS, nome), "rb") as f1, \
                 open(os.path.join(saida, "arquivos", nome), "rb") as f2:
                self.assertEqual(f1.read(), f2.read(), nome)

        # Nenhum segredo no pacote.
        for base, _dirs, arquivos in os.walk(saida):
            for nome in arquivos:
                with open(os.path.join(base, nome), encoding="utf-8",
                          errors="replace") as f:
                    self.assertEqual(parity.varrer_segredos(f.read()), [],
                                     f"segredo suspeito em {nome}")

        # Evidência do comparador embarcada e READY.
        with open(os.path.join(saida, "evidencia-comparacao.json"),
                  encoding="utf-8") as f:
            self.assertEqual(json.load(f)["resultado"], "READY")

    def test_pacote_com_regex_texto_de_segredo_extra_autorizado_fica_ready(self):
        remoto = os.path.join(self.tmp, "remoto-regex-extra")
        compor_remoto(
            os.path.join(FIXTURES, "17-regex-com-texto-de-segredo"), remoto)
        saida = os.path.join(self.tmp, "pack-regex-extra")
        r = rodar(["bash", PACOTE, "--output", saida, "--remote-dir", remoto,
                   "--allow-extra", "anotacoes-regex-sinteticas.gs",
                   "--execute"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(saida, "evidencia-comparacao.json"),
                  encoding="utf-8") as f:
            self.assertEqual(json.load(f)["resultado"], "READY")

    def test_pacote_recusado_com_segredo_real_mesmo_com_allow_extra(self):
        remoto = os.path.join(self.tmp, "remoto-segredo-real")
        compor_remoto(os.path.join(FIXTURES, "16-segredo-sintetico"), remoto)
        saida = os.path.join(self.tmp, "pack-segredo-real")
        r = rodar(["bash", PACOTE, "--output", saida, "--remote-dir", remoto,
                   "--allow-extra", "configuracao-privada-sintetica.gs",
                   "--execute"])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(saida))


class TestTokenizadorRegex(unittest.TestCase):
    """Regressão do bug: '/' após palavra-chave (return/throw/case/yield/
    await) era tratado como divisão, deixando o conteúdo do regex exposto
    como código — o que fazia símbolos proibidos e "declarações" de função
    dentro de literais regex vazarem para a análise estática."""

    def _globais_e_ids(self, codigo):
        limpo = parity.limpar_codigo(codigo)
        return parity.extrair_globais(limpo), parity.extrair_identificadores(limpo)

    def test_regex_apos_return_nao_e_declaracao_nem_simbolo(self):
        globais, ids = self._globais_e_ids("function f(){ return /_nextId/; }")
        nomes = {g["nome"] for g in globais}
        self.assertEqual(nomes, {"f"})
        self.assertNotIn("_nextId", ids)

    def test_regex_apos_return_nao_e_falsa_declaracao_de_funcao(self):
        globais, ids = self._globais_e_ids(
            "function f(){ return /function fake\\(\\)/; }"
        )
        nomes = {g["nome"] for g in globais}
        self.assertEqual(nomes, {"f"})
        self.assertNotIn("fake", ids)

    def test_regex_apos_throw_case_yield_await(self):
        for trecho in (
            "throw /x/;",
            "switch(a){case /x/: break;}",
            "function* g(){ yield /x/; }",
            "async function h(){ await /x/; }",
        ):
            with self.subTest(trecho=trecho):
                _, ids = self._globais_e_ids(trecho)
                self.assertNotIn("x", ids)

    def test_regex_atribuido_com_simbolo_proibido_dentro(self):
        _, ids = self._globais_e_ids("const rx = /clearContents\\(\\)/gi;")
        self.assertIn("rx", ids)
        self.assertNotIn("clearContents", ids)

    def test_regex_com_barra_escapada(self):
        limpo = parity.limpar_codigo("var r = /a\\/b_nextId/;")
        self.assertNotIn("_nextId", parity.extrair_identificadores(limpo))
        self.assertIn("r", parity.extrair_identificadores(limpo))

    def test_regex_com_classe_de_caracteres(self):
        limpo = parity.limpar_codigo("var r = /[/]_nextId/;")
        self.assertNotIn("_nextId", parity.extrair_identificadores(limpo))

    def test_divisao_simples_nao_vira_regex(self):
        limpo = parity.limpar_codigo("var resultado = a / b;")
        self.assertEqual(limpo.strip(), "var resultado = a / b;")

    def test_divisao_encadeada_nao_vira_regex(self):
        limpo = parity.limpar_codigo("var x = 10 / 2 / 5;")
        self.assertEqual(limpo.strip(), "var x = 10 / 2 / 5;")

    def test_divisao_seguida_de_regex_em_outra_expressao(self):
        limpo = parity.limpar_codigo(
            "var d = a / b; function f(){ return /_nextId/; }"
        )
        ids = parity.extrair_identificadores(limpo)
        self.assertIn("a", ids)
        self.assertIn("b", ids)
        self.assertNotIn("_nextId", ids)

    def test_comentarios_strings_templates_continuam_ignorados(self):
        codigo = (
            "// return /_nextId/;\n"
            "/* return /_nextId/; */\n"
            "var s = \"return /_nextId/;\";\n"
            "var t = `return /_nextId/;`;\n"
            "function real(){ return /_nextId/; }\n"
        )
        globais, ids = self._globais_e_ids(codigo)
        nomes = {g["nome"] for g in globais}
        self.assertEqual(nomes, {"s", "t", "real"})
        self.assertNotIn("_nextId", ids)


class TestMascararLiteraisRegex(unittest.TestCase):
    """Correção final M14.3A.2: o scanner de segredos não pode analisar o
    conteúdo bruto de um literal regex — texto com cara de segredo dentro
    de /.../ não é uma atribuição real. mascarar_literais_regex isola
    exatamente esse miolo sem apagar comentários/strings/código normal
    (ao contrário de limpar_codigo, que apaga tudo isso)."""

    def test_nao_detecta_segredo_dentro_de_regex(self):
        casos = [
            'const rx = /apiToken="segredo-sintetico-123"/;',
            'return /clientSecret="xxxxxxxxxxxx"/;',
            'throw /privateKey="xxxxxxxxxxxx"/;',
            "const outro = /password:'xxxxxxxxxxxx'/gi;",
        ]
        for codigo in casos:
            with self.subTest(codigo=codigo):
                mascarado = parity.mascarar_literais_regex(codigo)
                self.assertEqual(parity.varrer_segredos(mascarado), [],
                                 f"falso positivo em: {codigo}")

    def test_continua_detectando_segredo_real(self):
        casos = [
            'const apiToken = "segredo-sintetico-123";',
            'const clientSecret = "segredo-sintetico-456";',
            'const privateKey = "segredo-sintetico-789";',
            'const password = "segredo-sintetico-000";',
        ]
        for codigo in casos:
            with self.subTest(codigo=codigo):
                mascarado = parity.mascarar_literais_regex(codigo)
                self.assertNotEqual(parity.varrer_segredos(mascarado), [],
                                    f"segredo real deixou de ser detectado: {codigo}")

    def test_preserva_tamanho_e_quebras_de_linha(self):
        codigo = ('const rx = /apiToken="segredo-sintetico-123"/;\n'
                   'const y = 1;\n')
        mascarado = parity.mascarar_literais_regex(codigo)
        self.assertEqual(len(mascarado), len(codigo))
        self.assertEqual(mascarado.count("\n"), codigo.count("\n"))

    def test_nao_confunde_divisao_com_regex(self):
        codigo = 'var resultado = a / b; var rx = /segredo="x"/;'
        mascarado = parity.mascarar_literais_regex(codigo)
        self.assertIn("var resultado = a / b;", mascarado)
        self.assertNotIn("segredo", mascarado)

    def test_preserva_comentarios_strings_e_templates_intactos(self):
        codigo = (
            "// comentario com /segredo/ dentro\n"
            "var s = 'literal com /segredo/ dentro';\n"
            "var t = `template com /segredo/ dentro`;\n"
            "var rx = /segredo/;\n"
        )
        mascarado = parity.mascarar_literais_regex(codigo)
        self.assertIn("// comentario com /segredo/ dentro", mascarado)
        self.assertIn("'literal com /segredo/ dentro'", mascarado)
        self.assertIn("`template com /segredo/ dentro`", mascarado)
        ultima_linha = mascarado.splitlines()[-1]
        self.assertIn("var rx = /", ultima_linha)
        self.assertNotIn("segredo", ultima_linha)

    def test_preserva_barra_escapada_e_classe_de_caracteres(self):
        mascarado = parity.mascarar_literais_regex(
            'var a = /a\\/b_segredo/; var c = /[/]_segredo/;'
        )
        self.assertNotIn("_segredo", mascarado)
        self.assertIn("var a = /", mascarado)
        self.assertIn("var c = /", mascarado)


class TestValidadorSchemaLocal(unittest.TestCase):
    """Validador determinístico usado para fechar o contrato do inventário
    remoto sem depender obrigatoriamente do pacote 'jsonschema'."""

    def test_additional_properties_false_rejeita_campo_extra(self):
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"a": {"type": "string"}}}
        erros = parity.validar_schema({"a": "x", "b": "y"}, schema)
        self.assertTrue(any("b" in e for e in erros))

    def test_required_ausente_e_erro(self):
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"a": {"type": "string"}}, "required": ["a"]}
        erros = parity.validar_schema({}, schema)
        self.assertTrue(any("a" in e for e in erros))

    def test_tipo_invalido_e_erro(self):
        schema = {"type": "array"}
        erros = parity.validar_schema("nao-e-lista", schema, "$")
        self.assertTrue(any("tipo inválido" in e for e in erros))

    def test_mensagem_de_erro_nunca_reproduz_o_valor(self):
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {}}
        erros = parity.validar_schema({"token": "segredo-super-secreto-123"},
                                       schema)
        texto = " ".join(erros)
        self.assertNotIn("segredo-super-secreto-123", texto)

    def test_schema_valido_sem_erros(self):
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {"a": {"type": "integer"}}}
        self.assertEqual(parity.validar_schema({"a": 1}, schema), [])


class TestRemoteInventoryValidacao(Base):
    """Defeito 2/3: o schema fecha additionalProperties e o comparador
    valida remote-inventory.json contra ele ANTES de qualquer comparação —
    inventário inválido nunca pode virar READY nem BLOCKED, só exit 1."""

    def setUp(self):
        super().setUp()
        self.remoto = os.path.join(self.tmp, "remoto")
        compor_remoto(os.path.join(FIXTURES, "01-remoto-identico"), self.remoto)

    def _rodar(self, inventario):
        with open(os.path.join(self.remoto, "remote-inventory.json"),
                  "w", encoding="utf-8") as f:
            json.dump(inventario, f)
        return rodar([sys.executable, COMPARADOR, "--remote-dir", self.remoto,
                      "--no-write"])

    def test_inventario_valido_minimo(self):
        r = self._rodar({"coletado_em": "x", "arquivos_visiveis_no_editor": []})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULTADO: READY", r.stdout)

    def test_inventario_valido_completo(self):
        r = self._rodar({
            "coletado_em": "2026-07-12 21:00 America/Sao_Paulo",
            "arquivos_visiveis_no_editor": ["command-center-api.gs"],
            "triggers": [{"funcao": "f", "evento": "ao editar",
                          "origem": "planilha", "ativo": True,
                          "observacoes": "instalado manualmente"}],
            "propriedades_do_script_nomes": ["API_TOKEN", "BUILD_VERSION"],
            "implantacao": {"versao_ativa": 3, "descricao_da_versao": "v3",
                             "executar_como": "USER_DEPLOYING",
                             "quem_pode_acessar": "ANYONE"},
            "observacoes": "tudo certo",
        })
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULTADO: READY", r.stdout)

    def test_property_names_validos(self):
        r = self._rodar({"coletado_em": "x", "arquivos_visiveis_no_editor": [],
                          "propriedades_do_script_nomes": ["API_TOKEN"]})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_property_values_proibido(self):
        r = self._rodar({"coletado_em": "x", "arquivos_visiveis_no_editor": [],
                          "propertyValues": {"API_TOKEN": "valor-secreto"}})
        self.assertEqual(r.returncode, 1)
        self.assertIn("propertyValues", r.stderr)
        self.assertNotIn("valor-secreto", r.stdout + r.stderr)

    def test_deployment_id_proibido(self):
        r = self._rodar({"coletado_em": "x", "arquivos_visiveis_no_editor": [],
                          "deploymentId": "AKfycbXXXXXXXXXXXXXXXXXXXXXXXXXXX"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("deploymentId", r.stderr)

    def test_deployment_url_proibido(self):
        r = self._rodar({"coletado_em": "x", "arquivos_visiveis_no_editor": [],
                          "deploymentUrl": "https://script.google.com/macros/s/x/exec"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("deploymentUrl", r.stderr)

    def test_token_proibido(self):
        # Montado em tempo de execução: mantém o teste do scanner,
        # sem gravar no Git um padrão completo de Google API Key.
        segredo_sintetico = (
            "AI"
            + "zaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234567"
        )
        r = self._rodar({
            "coletado_em": "x",
            "arquivos_visiveis_no_editor": [],
            "observacoes": segredo_sintetico,
        })
        self.assertEqual(r.returncode, 1)
        self.assertIn("segredo", r.stderr.lower())
        self.assertNotIn(
            segredo_sintetico,
            r.stdout + r.stderr,
        )

    def test_campo_arbitrario_proibido(self):
        r = self._rodar({"coletado_em": "x", "arquivos_visiveis_no_editor": [],
                          "campoQualquer": "valor"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("campoQualquer", r.stderr)

    def test_tipo_invalido(self):
        r = self._rodar({"coletado_em": "x", "arquivos_visiveis_no_editor": "x"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("tipo inválido", r.stderr)

    def test_trigger_com_campo_extra(self):
        r = self._rodar({"coletado_em": "x", "arquivos_visiveis_no_editor": [],
                          "triggers": [{"funcao": "f", "evento": "e",
                                       "idDoTrigger": "abc123"}]})
        self.assertEqual(r.returncode, 1)
        self.assertIn("idDoTrigger", r.stderr)

    def test_json_malformado(self):
        with open(os.path.join(self.remoto, "remote-inventory.json"),
                  "w", encoding="utf-8") as f:
            f.write("{ nao e json valido")
        r = rodar([sys.executable, COMPARADOR, "--remote-dir", self.remoto,
                   "--no-write"])
        self.assertEqual(r.returncode, 1)

    def test_comparador_nunca_retorna_ready_com_inventario_invalido(self):
        for payload in (
            {"coletado_em": "x", "arquivos_visiveis_no_editor": [], "scriptId": "abc"},
            {"coletado_em": "x", "arquivos_visiveis_no_editor": [], "token": "x" * 20},
        ):
            with self.subTest(payload=payload):
                r = self._rodar(payload)
                self.assertNotIn("RESULTADO: READY", r.stdout)
                self.assertNotEqual(r.returncode, 0)


class TestExitCodesUso(Base):
    """Defeito 3: exit codes do comparador não podem colidir — 2 é
    reservado para BLOCKED; erro de uso/entrada/validação é sempre 1."""

    def test_sem_argumentos(self):
        r = rodar([sys.executable, COMPARADOR])
        self.assertEqual(r.returncode, 1)

    def test_argumento_invalido(self):
        r = rodar([sys.executable, COMPARADOR, "--remote-dir", self.tmp,
                   "--flag-que-nao-existe"])
        self.assertEqual(r.returncode, 1)

    def test_remote_dir_inexistente(self):
        r = rodar([sys.executable, COMPARADOR, "--remote-dir",
                   os.path.join(self.tmp, "nao-existe")])
        self.assertEqual(r.returncode, 1)

    def test_manifest_inexistente(self):
        r = rodar([sys.executable, COMPARADOR, "--remote-dir", self.tmp,
                   "--manifest", os.path.join(self.tmp, "nao-existe.json")])
        self.assertEqual(r.returncode, 1)


class TestSegurancaDasFixtures(Base):
    PADROES_REAIS = [
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"AIza[0-9A-Za-z_\-]{30,}",
        r"ya29\.[0-9A-Za-z_\-]{20,}",
        r"script\.google\.com/macros/s/[A-Za-z0-9_\-]{20,}",
    ]

    def test_fixtures_sem_segredo_real_nem_pii(self):
        for base, _dirs, arquivos in os.walk(FIXTURES):
            for nome in arquivos:
                caminho = os.path.join(base, nome)
                with open(caminho, encoding="utf-8") as f:
                    texto = f.read()
                for padrao in self.PADROES_REAIS:
                    self.assertIsNone(
                        re.search(padrao, texto),
                        f"padrão de segredo real em {caminho}")

    def test_segredo_sintetico_marcado_como_falso(self):
        caminho = os.path.join(FIXTURES, "16-segredo-sintetico",
                               "configuracao-privada-sintetica.gs")
        with open(caminho, encoding="utf-8") as f:
            texto = f.read()
        self.assertIn("SINTETICO", texto)
        self.assertNotEqual(parity.varrer_segredos(texto), [],
                            "a credencial falsa PRECISA disparar o scanner")


if __name__ == "__main__":
    unittest.main(verbosity=2)
