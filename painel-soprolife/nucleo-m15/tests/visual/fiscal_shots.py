"""Minimal Chrome-headless CDP screenshot check for the Fiscal Command Center
tab, using 100% fictitious data (see fiscal_harness.html). Same mechanism as
shots.py (M25.21), trimmed to this module's own measurements.
"""
import asyncio
import base64
import json
import pathlib
import sys

import websockets

BASE = sys.argv[1]
SAIDA = pathlib.Path(sys.argv[2])
SAIDA.mkdir(parents=True, exist_ok=True)

VIEWPORTS = [(1440, 900), (430, 932)]

MEDIR = r"""
(() => {
  const root = document.getElementById('fiscalRoot');
  const texto = document.body.innerText;
  return {
    largura_janela: window.innerWidth,
    tem_readiness_panel: !!document.querySelector('.fiscal-readiness-panel'),
    tem_summary_cards: document.querySelectorAll('.fiscal-summary-card').length,
    linhas_fila: document.querySelectorAll('.fiscal-table tbody tr').length,
    checkboxes: document.querySelectorAll('[data-fiscal-select]').length,
    overflow_horizontal: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    tem_undefined: /\bundefined\b/.test(texto),
    tem_nan: /\bNaN\b/.test(texto),
    tem_null_visivel: /\bnull\b/.test(texto),
  };
})()
"""


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.id = 0

    async def send(self, metodo, params=None, sessao=None):
        self.id += 1
        pedido = {"id": self.id, "method": metodo, "params": params or {}}
        if sessao:
            pedido["sessionId"] = sessao
        await self.ws.send(json.dumps(pedido))
        while True:
            resposta = json.loads(await self.ws.recv())
            if resposta.get("id") == self.id:
                if "error" in resposta:
                    raise RuntimeError(resposta["error"])
                return resposta.get("result", {})


async def capturar(cdp, sessao, nome, largura, altura):
    await cdp.send("Emulation.setDeviceMetricsOverride", {
        "width": largura, "height": altura, "deviceScaleFactor": 1,
        "mobile": largura <= 500,
    }, sessao)
    await asyncio.sleep(0.6)
    tiro = await cdp.send("Page.captureScreenshot", {"format": "png"}, sessao)
    (SAIDA / f"{nome}.png").write_bytes(base64.b64decode(tiro["data"]))


async def main():
    endpoint = json.loads(pathlib.Path(sys.argv[3]).read_text())["webSocketDebuggerUrl"]
    relatorio = {}
    async with websockets.connect(endpoint, max_size=200 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        alvo = await cdp.send("Target.createTarget", {"url": "about:blank"})
        anexo = await cdp.send("Target.attachToTarget", {
            "targetId": alvo["targetId"], "flatten": True})
        sessao = anexo["sessionId"]
        await cdp.send("Page.enable", {}, sessao)
        await cdp.send("Runtime.enable", {}, sessao)

        cenarios = [
            ("a-fila-mista", "?cenario=mista", []),
            ("b-selecionar-eleg", "?cenario=mista", ["[data-fiscal-select-eligible]"]),
            ("c-emitir-lote", "?cenario=mista",
             ["[data-fiscal-select-eligible]", "[data-fiscal-emit]"]),
            ("d-filtro-bloqueadas", "?cenario=mista", []),
            ("e-vazio", "?cenario=vazio", []),
        ]

        for nome_cenario, query, cliques in cenarios:
            for largura, altura in VIEWPORTS:
                await cdp.send("Emulation.setDeviceMetricsOverride", {
                    "width": largura, "height": altura, "deviceScaleFactor": 1,
                    "mobile": largura <= 500,
                }, sessao)
                await cdp.send("Page.navigate", {"url": BASE + query}, sessao)
                await asyncio.sleep(1.6)
                if nome_cenario == "d-filtro-bloqueadas":
                    await cdp.send("Runtime.evaluate", {
                        "expression": "document.querySelector('[data-fiscal-filter-select]').value='blocked';"
                                      "document.querySelector('[data-fiscal-filter-select]')"
                                      ".dispatchEvent(new Event('change', {bubbles:true}))",
                    }, sessao)
                    await asyncio.sleep(1.0)
                for seletor in cliques:
                    await cdp.send("Runtime.evaluate", {
                        "expression": f"(document.querySelector('{seletor}')||{{}}).click?.()",
                    }, sessao)
                    await asyncio.sleep(1.2)
                medida = await cdp.send("Runtime.evaluate", {
                    "expression": MEDIR, "returnByValue": True,
                }, sessao)
                chave = f"{nome_cenario}-{largura}"
                relatorio[chave] = medida["result"]["value"]
                await capturar(cdp, sessao, chave, largura, altura)
                print(chave, json.dumps(relatorio[chave], ensure_ascii=False))

    (SAIDA / "medidas.json").write_text(json.dumps(relatorio, indent=2, ensure_ascii=False))


asyncio.run(main())
