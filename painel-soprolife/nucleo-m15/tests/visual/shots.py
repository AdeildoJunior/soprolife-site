"""Screenshots automatizados da conta médica com dados 100% fictícios.

Chrome headless + CDP (sem puppeteer/playwright). Mede também, na própria
página, o que os olhos deveriam checar:

  * a bancada clínica é FILHA do shell, não da faixa de resumo;
  * a largura da bancada em relação à largura útil da página;
  * se há overflow horizontal;
  * quantas colunas a grade de conclusões formou;
  * se a string "undefined" aparece em qualquer lugar do texto renderizado.
"""

import asyncio
import base64
import json
import pathlib
import sys

import websockets

BASE = sys.argv[1]          # http://127.0.0.1:PORTA/harness.html
SAIDA = pathlib.Path(sys.argv[2])
SAIDA.mkdir(parents=True, exist_ok=True)

VIEWPORTS = [(1920, 1080), (1440, 900), (1366, 768), (1024, 768),
              (768, 1024), (430, 932)]

MEDIR = r"""
(() => {
  const shell = document.querySelector('.report-physician-shell');
  const resumo = document.querySelector('.report-physician-summary');
  const bancada = document.querySelector('.report-physician-workbench');
  const painel = document.querySelector('.report-clinical-panel');
  const mir = document.querySelector('.report-pdf-frame');
  const chips = document.querySelector('.report-chip-grid');
  const raiz = document.querySelector('.report-workflow-root');
  const titulo = document.querySelector('#signatureCenterTitle');
  const selo = document.querySelector('.report-signature-count');
  const colunas = chips
    ? new Set([...chips.children].map((c) => Math.round(
        c.getBoundingClientRect().left))).size
    : 0;
  const texto = document.body.innerText;
  return {
    largura_janela: window.innerWidth,
    largura_raiz: raiz ? Math.round(raiz.getBoundingClientRect().width) : 0,
    largura_resumo: resumo ? Math.round(resumo.getBoundingClientRect().width) : 0,
    largura_col_assinatura: resumo && resumo.children[0]
      ? Math.round(resumo.children[0].getBoundingClientRect().width) : 0,
    largura_col_meus_laudos: resumo && resumo.children[1]
      ? Math.round(resumo.children[1].getBoundingClientRect().width) : 0,
    itens_na_fila: document.querySelectorAll('.report-queue-item').length,
    itens_na_central: document.querySelectorAll('.report-signature-item').length,
    largura_source_pane: document.querySelector('.report-source-pane')
      ? Math.round(document.querySelector('.report-source-pane').getBoundingClientRect().width) : 0,
    largura_work_pane: document.querySelector('.report-work-pane')
      ? Math.round(document.querySelector('.report-work-pane').getBoundingClientRect().width) : 0,
    largura_cartao_paciente: document.querySelector('.report-context-card')
      ? Math.round(document.querySelector('.report-context-card').getBoundingClientRect().width) : 0,
    altura_cartao_paciente: document.querySelector('.report-context-card')
      ? Math.round(document.querySelector('.report-context-card').getBoundingClientRect().height) : 0,
    largura_bancada: bancada
      ? Math.round(bancada.getBoundingClientRect().width) : 0,
    largura_painel_clinico: painel
      ? Math.round(painel.getBoundingClientRect().width) : 0,
    altura_mir: mir ? Math.round(mir.getBoundingClientRect().height) : 0,
    largura_mir: mir ? Math.round(mir.getBoundingClientRect().width) : 0,
    colunas_conclusoes: colunas,
    bancada_dentro_do_resumo: Boolean(
      bancada && resumo && resumo.contains(bancada)
    ),
    bancada_filha_do_shell: Boolean(
      bancada && shell && bancada.parentElement === shell
    ),
    overflow_horizontal:
      document.documentElement.scrollWidth > document.documentElement.clientWidth,
    titulo_assinatura: titulo ? titulo.textContent.trim() : null,
    selo_assinatura: selo ? selo.textContent.trim() : null,
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
    """Duas capturas por cenário.

    `-viewport` é a tela REAL (1920x1080, 430x932, ...): é nela que `vh`
    significa o que significa para quem usa. A de página inteira serve só
    para conferir a página toda de uma vez, e nela as alturas em `vh` ficam
    infladas de propósito pelo redimensionamento."""

    await cdp.send("Emulation.setDeviceMetricsOverride", {
        "width": largura, "height": altura, "deviceScaleFactor": 1,
        "mobile": largura <= 500,
    }, sessao)
    await asyncio.sleep(0.8)
    tiro = await cdp.send("Page.captureScreenshot", {"format": "png"}, sessao)
    (SAIDA / f"{nome}-viewport.png").write_bytes(base64.b64decode(tiro["data"]))
    await asyncio.sleep(0.2)
    total = await cdp.send("Runtime.evaluate", {
        "expression": "document.documentElement.scrollHeight",
        "returnByValue": True,
    }, sessao)
    cheia = min(int(total["result"]["value"]) + 20, 8000)
    await cdp.send("Emulation.setDeviceMetricsOverride", {
        "width": largura, "height": cheia, "deviceScaleFactor": 1,
        "mobile": largura <= 500,
    }, sessao)
    await asyncio.sleep(0.8)
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
            ("a-lista-medica", "?cenario=lista", None),
            ("b-paciente-aberto", "?cenario=lista", "doc-1"),
            ("c-central-vazia", "?cenario=vazio", None),
        ]

        for nome_cenario, query, abrir in cenarios:
            for largura, altura_viewport in VIEWPORTS:
                await cdp.send("Emulation.setDeviceMetricsOverride", {
                    "width": largura, "height": altura_viewport,
                    "deviceScaleFactor": 1,
                    "mobile": largura <= 500,
                }, sessao)
                await cdp.send("Page.navigate", {"url": BASE + query}, sessao)
                await asyncio.sleep(2.4)
                if abrir:
                    await cdp.send("Runtime.evaluate", {
                        "expression": (
                            "document.querySelector"
                            f"('[data-report-open=\"{abrir}\"]').click()"
                        ),
                    }, sessao)
                    await asyncio.sleep(2.0)
                medida = await cdp.send("Runtime.evaluate", {
                    "expression": MEDIR, "returnByValue": True,
                }, sessao)
                chave = f"{nome_cenario}-{largura}"
                relatorio[chave] = medida["result"]["value"]
                await capturar(cdp, sessao, chave, largura,
                               altura_viewport)
                print(chave, json.dumps(relatorio[chave], ensure_ascii=False))

    (SAIDA / "medidas.json").write_text(
        json.dumps(relatorio, indent=2, ensure_ascii=False)
    )


asyncio.run(main())
