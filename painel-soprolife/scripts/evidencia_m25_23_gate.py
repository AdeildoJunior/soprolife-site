#!/usr/bin/env python3
"""
SoproLife — Evidência de navegador do gate de autenticação (M25.23).

Dirige o Chrome por CDP em contexto LIMPO (perfil descartável = equivalente a
janela privativa) e prova, no navegador real:

  A) sem sessão, /painel-soprolife/ renderiza SOMENTE a tela de login;
  B) o DOM sem sessão não contém os textos nem a estrutura das áreas restritas;
  C) antes da autenticação, nenhuma requisição de dado operacional sai;
  D) sessão médica monta somente a bancada clínica — os nós das áreas
     administrativas são REMOVIDOS, não escondidos;
  F) sessão de gestor mantém o painel completo.

Os papéis vêm de credenciais SINTÉTICAS passadas por ambiente; nenhum dado real
é usado, e as capturas ficam fora do repositório.

Uso:
    python3 painel-soprolife/scripts/evidencia_m25_23_gate.py \
        --base http://127.0.0.1:8765 --saida /caminho/fora/do/git
Exit: 0 = todas as provas passaram | 1 = alguma falhou.
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import websocket  # websocket-client

# Vocabulário que NÃO pode existir no DOM antes do login. A lista mistura
# rótulo de módulo e rótulo de indicador de propósito: o pedido da M25.23 cita
# ambos, e um valor sem rótulo é tão revelador quanto o rótulo sozinho.
TEXTOS_PROIBIDOS = (
    "Financeiro", "Marketing", "CRM", "Receita recebida", "Ticket médio",
    "Central de Cadastros", "Parcerias", "Custos", "Painel Geral",
    "Leads", "Laudos", "Command Center", "Documentos", "Tarefas",
)

# Estrutura: se qualquer um destes seletores existir, o painel foi montado.
SELETORES_PROIBIDOS = (
    ".sidebar", ".nav-item[data-section]", "section.section", "canvas",
    "#financeStats", "#crmView", "#mktKpiStrip", "#navHub", ".app-shell",
)

# Caminhos de dado operacional. Nenhum pode ser pedido antes da autenticação.
REDE_PROIBIDA = (
    "/data/", "financeiro", "crm", "marketing", "pacientes", "parcerias",
    "leads", "laudos", "lancamentos", "espirometria",
)
REDE_PERMITIDA_SUBSTR = ("/auth/", "login.html", "m15-security.js", "login.js",
                         "/css/", "/assets/", "favicon")

FALHAS = []


def caso(nome, cond, detalhe=""):
    if cond:
        print(f"  PASS: {nome}")
    else:
        FALHAS.append(nome)
        print(f"  FALHA: {nome}" + (f" — {detalhe}" if detalhe else ""))


class Chrome:
    def __init__(self):
        self.perfil = tempfile.mkdtemp(prefix="m25-23-chrome-")
        self.porta = 9222
        self.proc = subprocess.Popen(
            [
                "google-chrome", "--headless=new",
                f"--remote-debugging-port={self.porta}",
                f"--user-data-dir={self.perfil}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-gpu", "--hide-scrollbars",
                # Chrome ≥ 111 recusa o handshake do CDP sem isto.
                "--remote-allow-origins=*",
                "--disable-features=DefaultPassthroughCommandDecoder",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._esperar()

    def _esperar(self):
        for _ in range(60):
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{self.porta}/json/version", timeout=1
                ).read()
                return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("Chrome não subiu")

    def aba(self, url="about:blank"):
        # Chrome ≥ 111 exige PUT em /json/new; GET responde 405.
        pedido = urllib.request.Request(
            f"http://127.0.0.1:{self.porta}/json/new?{urllib.parse.quote(url)}",
            method="PUT",
        )
        bruto = urllib.request.urlopen(pedido, timeout=10).read()
        return Aba(json.loads(bruto)["webSocketDebuggerUrl"])

    def fechar(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.perfil, ignore_errors=True)


class Aba:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self._id = 0
        self.eventos = []

    def cmd(self, metodo, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": metodo, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{metodo}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.eventos.append(msg)

    def drenar(self, segundos):
        fim = time.time() + segundos
        self.ws.settimeout(0.4)
        while time.time() < fim:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                continue
            if "method" in msg:
                self.eventos.append(msg)
        self.ws.settimeout(30)

    def urls_pedidas(self):
        return [
            e["params"]["request"]["url"]
            for e in self.eventos
            if e.get("method") == "Network.requestWillBeSent"
        ]

    def html(self):
        return self.cmd(
            "Runtime.evaluate",
            expression="document.documentElement.outerHTML",
            returnByValue=True,
        )["result"]["value"]

    def existe(self, seletor):
        return self.cmd(
            "Runtime.evaluate",
            expression=f"!!document.querySelector({json.dumps(seletor)})",
            returnByValue=True,
        )["result"]["value"]

    def texto_visivel(self):
        return self.cmd(
            "Runtime.evaluate",
            expression="document.body ? document.body.innerText : ''",
            returnByValue=True,
        )["result"]["value"]

    def captura(self, caminho, largura, altura):
        self.cmd(
            "Emulation.setDeviceMetricsOverride",
            width=largura, height=altura, deviceScaleFactor=1,
            mobile=largura < 700,
        )
        dados = self.cmd("Page.captureScreenshot", format="png")["data"]
        with open(caminho, "wb") as destino:
            destino.write(base64.b64decode(dados))

    def fechar(self):
        try:
            self.ws.close()
        except Exception:
            pass


def abrir(chrome, url, cookie=None, largura=1920, altura=1080, espera=3.0):
    aba = chrome.aba()
    aba.cmd("Page.enable")
    aba.cmd("Network.enable")
    aba.cmd(
        "Emulation.setDeviceMetricsOverride",
        width=largura, height=altura, deviceScaleFactor=1, mobile=largura < 700,
    )
    if cookie:
        aba.cmd("Network.setCookie", **cookie)
    aba.cmd("Page.navigate", url=url)
    aba.drenar(espera)
    return aba


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8765")
    p.add_argument("--saida", required=True)
    p.add_argument("--cookie-nome", default="soprolife_m15_sessao")
    args = p.parse_args()

    painel = args.base.rstrip("/") + "/painel-soprolife/"
    os.makedirs(args.saida, exist_ok=True)

    ck_medica = os.environ.get("M25_23_COOKIE_MEDICA", "")
    ck_gestor = os.environ.get("M25_23_COOKIE_GESTOR", "")
    dominio = urllib.parse.urlsplit(args.base).hostname

    def cookie(valor):
        if not valor:
            return None
        return {
            "name": args.cookie_nome, "value": valor,
            "domain": dominio, "path": "/painel-soprolife",
            "httpOnly": True, "sameSite": "Strict",
        }

    chrome = Chrome()
    try:
        # ── A/B/C — sem sessão, contexto limpo ────────────────────────────
        print("── A/B/C — janela limpa, SEM sessão (1920) ──")
        aba = abrir(chrome, painel, largura=1920, altura=1080)
        html = aba.html()
        texto = aba.texto_visivel()

        caso("a tela de login está presente", "loginForm" in html)
        caso("campo de e-mail presente", aba.existe("#email"))
        caso("campo de senha presente", aba.existe("#password"))

        for termo in TEXTOS_PROIBIDOS:
            caso(f"DOM sem o texto {termo!r}", termo.lower() not in html.lower(),
                 "encontrado no HTML")
        for seletor in SELETORES_PROIBIDOS:
            caso(f"DOM sem a estrutura {seletor!r}", not aba.existe(seletor))

        caso("nenhum R$ visível na tela", "R$" not in texto)

        pedidas = aba.urls_pedidas()
        vazamentos = [
            u for u in pedidas
            if any(t in u.lower() for t in REDE_PROIBIDA)
            and not any(ok in u.lower() for ok in REDE_PERMITIDA_SUBSTR)
        ]
        caso("nenhuma requisição de dado operacional antes do login",
             not vazamentos, "; ".join(vazamentos[:4]))
        print(f"    requisições feitas: {len(pedidas)}")
        for u in pedidas:
            print(f"      · {u}")

        aba.captura(os.path.join(args.saida, "01-sem-login-1920.png"), 1920, 1080)
        aba.fechar()

        print("── A — janela limpa, SEM sessão (430 mobile) ──")
        aba = abrir(chrome, painel, largura=430, altura=932)
        caso("mobile: login presente", aba.existe("#loginForm"))
        caso("mobile: sem barra lateral", not aba.existe(".sidebar"))
        aba.captura(os.path.join(args.saida, "02-sem-login-430.png"), 430, 932)
        aba.fechar()

        # ── D — sessão médica sintética ───────────────────────────────────
        if ck_medica:
            print("── D — sessão MÉDICA sintética ──")
            aba = abrir(chrome, painel, cookie=cookie(ck_medica), espera=5.0)
            caso("médica: bancada de laudos existe",
                 aba.existe("#laudos-espirometria"))
            for proibida in ("#financeiro", "#crm", "#marketing", "#overview",
                             "#central-cadastros", "#parcerias-pastore",
                             "#custos-investimentos", "#documentos", "#leads"):
                caso(f"médica: seção {proibida} REMOVIDA do DOM",
                     not aba.existe(proibida))
            for nav in ('.nav-item[data-section="financeiro"]',
                        '.nav-item[data-section="crm"]',
                        '.nav-item[data-section="marketing"]',
                        '.nav-item[data-section="overview"]'):
                caso(f"médica: menu {nav} REMOVIDO", not aba.existe(nav))
            texto_medica = aba.texto_visivel()
            caso("médica: nenhum rótulo financeiro na tela",
                 "Ticket médio" not in texto_medica
                 and "Receita recebida" not in texto_medica)
            aba.captura(os.path.join(args.saida, "03-medica-1920.png"), 1920, 1080)
            aba.fechar()
        else:
            print("  (sem M25_23_COOKIE_MEDICA — etapa D pulada)")

        # ── F — sessão de gestor sintética ────────────────────────────────
        if ck_gestor:
            print("── F — sessão GESTOR sintética ──")
            aba = abrir(chrome, painel, cookie=cookie(ck_gestor), espera=5.0)
            for secao in ("#overview", "#financeiro", "#crm", "#marketing",
                          "#central-cadastros"):
                caso(f"gestor: seção {secao} presente", aba.existe(secao))
            caso("gestor: barra lateral presente", aba.existe(".sidebar"))
            aba.captura(os.path.join(args.saida, "04-gestor-1920.png"), 1920, 1080)
            aba.fechar()
        else:
            print("  (sem M25_23_COOKIE_GESTOR — etapa F pulada)")

    finally:
        chrome.fechar()

    print()
    if FALHAS:
        print(f"RESULTADO: {len(FALHAS)} prova(s) falharam.")
        for f in FALHAS:
            print(f"  - {f}")
        return 1
    print("RESULTADO: gate provado no navegador.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
