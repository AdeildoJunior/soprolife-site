"""Smoke visual na PÁGINA REAL de produção, com dados fictícios.

Carrega https://.../painel-soprolife/index.html — o CSS e o JS que a médica
vai baixar — e só então substitui o cliente autenticado por um dublê que
devolve payloads inventados, com os envelopes reais da API.

Nenhum laudo real é lido, aberto, concluído ou alterado: o dublê intercepta
`api()` antes de qualquer requisição, e o roteiro é só leitura.
"""

import asyncio
import base64
import json
import pathlib
import sys

import websockets

URL = sys.argv[1]
SAIDA = pathlib.Path(sys.argv[2])
SAIDA.mkdir(parents=True, exist_ok=True)
HARNESS = pathlib.Path(sys.argv[3]).read_text()

# Reaproveita o dublê do harness: o bloco <script> inline, sem o HTML.
STUB = HARNESS.split("<script>", 1)[1].split("</script>", 1)[0]

TROCAR_CLIENTE = STUB + """
// A sessão real já foi consultada; o dublê assume a partir daqui e o clique
// na aba dispara `loadAuthenticatedData` contra ele.
document.body.classList.add('report-physician-only');
const aba = document.querySelector('.nav-item[data-section="laudos-espirometria"]');
if (aba) aba.click();
"Ok";
"""

MEDIR = r"""
(() => {
  const resumo = document.querySelector('.report-physician-summary');
  const bancada = document.querySelector('.report-physician-workbench');
  const mir = document.querySelector('.report-pdf-frame');
  const chips = document.querySelector('.report-chip-grid');
  const selo = document.querySelector('.report-signature-count');
  const titulo = document.querySelector('#signatureCenterTitle');
  const raiz = document.querySelector('.report-workflow-root');
  return {
    largura_bancada: bancada
      ? Math.round(bancada.getBoundingClientRect().width) : 0,
    largura_raiz: raiz ? Math.round(raiz.getBoundingClientRect().width) : 0,
    bancada_dentro_do_resumo: Boolean(
      bancada && resumo && resumo.contains(bancada)),
    altura_mir: mir ? Math.round(mir.getBoundingClientRect().height) : 0,
    colunas_conclusoes: chips
      ? new Set([...chips.children].map(
          (c) => Math.round(c.getBoundingClientRect().left))).size : 0,
    itens_na_fila: document.querySelectorAll('.report-queue-item').length,
    itens_na_central: document.querySelectorAll('.report-signature-item').length,
    selo: selo ? selo.textContent.trim() : null,
    titulo: titulo ? titulo.textContent.trim() : null,
    documentos_largura: document.querySelector('.report-documents-panel')
      ? Math.round(document.querySelector('.report-documents-panel')
          .getBoundingClientRect().width) : 0,
    contexto_cards: document.querySelectorAll('.report-context-card').length,
    overflow_horizontal: document.documentElement.scrollWidth
      > document.documentElement.clientWidth,
    tem_undefined: /\bundefined\b/.test(document.body.innerText),
    tem_nan: /\bNaN\b/.test(document.body.innerText),
  };
})()
"""


class CDP:
    def __init__(self, ws):
        self.ws, self.id = ws, 0

    async def send(self, metodo, params=None, sessao=None):
        self.id += 1
        pedido = {"id": self.id, "method": metodo, "params": params or {}}
        if sessao:
            pedido["sessionId"] = sessao
        await self.ws.send(json.dumps(pedido))
        while True:
            r = json.loads(await self.ws.recv())
            if r.get("id") == self.id:
                if "error" in r:
                    raise RuntimeError(r["error"])
                return r.get("result", {})


async def main():
    endpoint = json.loads(
        pathlib.Path(sys.argv[4]).read_text())["webSocketDebuggerUrl"]
    saida = {}
    async with websockets.connect(endpoint, max_size=200 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        alvo = await cdp.send("Target.createTarget", {"url": "about:blank"})
        anexo = await cdp.send("Target.attachToTarget",
                               {"targetId": alvo["targetId"], "flatten": True})
        s = anexo["sessionId"]
        await cdp.send("Page.enable", {}, s)
        await cdp.send("Runtime.enable", {}, s)

        for nome, largura, altura, abrir in [
            ("prod-lista-1920", 1920, 1080, None),
            ("prod-paciente-1920", 1920, 1080, "doc-1"),
            ("prod-paciente-1366", 1366, 768, "doc-1"),
            ("prod-mobile-430", 430, 932, None),
        ]:
            await cdp.send("Emulation.setDeviceMetricsOverride", {
                "width": largura, "height": altura, "deviceScaleFactor": 1,
                "mobile": largura <= 500}, s)
            await cdp.send("Page.navigate", {"url": URL}, s)
            await asyncio.sleep(4.0)
            r = await cdp.send("Runtime.evaluate", {
                "expression": TROCAR_CLIENTE, "returnByValue": True}, s)
            if r.get("exceptionDetails"):
                print(nome, "ERRO ao instalar o dublê:", r["exceptionDetails"])
            await asyncio.sleep(2.5)
            if abrir:
                await cdp.send("Runtime.evaluate", {
                    "expression": "const b=document.querySelector"
                                  f"('[data-report-open=\"{abrir}\"]');"
                                  "b && b.click();"}, s)
                await asyncio.sleep(2.5)
            m = await cdp.send("Runtime.evaluate", {
                "expression": MEDIR, "returnByValue": True}, s)
            saida[nome] = m["result"]["value"]
            print(nome, json.dumps(saida[nome], ensure_ascii=False))
            tiro = await cdp.send("Page.captureScreenshot",
                                  {"format": "png"}, s)
            (SAIDA / f"{nome}.png").write_bytes(
                base64.b64decode(tiro["data"]))

    (SAIDA / "prod.json").write_text(
        json.dumps(saida, indent=2, ensure_ascii=False))


asyncio.run(main())
