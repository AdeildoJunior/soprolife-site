#!/usr/bin/env python3
"""M25.12 — evidência VISUAL do fluxo de laudos, no navegador de verdade.

Dirige o painel real com Playwright/Chromium e captura as telas A–J pedidas
no marco. Não simula HTML: abre a página, faz login, clica nos botões e
fotografa o que a tela realmente monta.

Aponta por padrão para o painel LOCAL (`http://127.0.0.1:8765`), servido pelo
mesmo proxy e pelos mesmos arquivos estáticos da produção. Para apontar para
outro ambiente use `--base-url`; as credenciais NUNCA ficam neste arquivo:
vêm de `--email`/`--senha` ou das variáveis `M25_12_EMAIL_*`/`M25_12_SENHA_*`.

Uso (cenário local da M25.3 + M25.12):
    .venv/bin/python scripts/evidencia_m25_12_navegador.py \
        --saida /tmp/m25-12-evidencias --exame ESP-000003
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE_PADRAO = "http://127.0.0.1:8765"
PAINEL = "/painel-soprolife/"

# Códigos institucionais FICTÍCIOS do cenário local. Nenhum dado real.
CODIGO_INEXISTENTE = "ESP-TF0001"

_passos: list[str] = []


def _shot(page, saida: Path, nome: str, descricao: str) -> None:
    caminho = saida / f"{nome}.png"
    page.screenshot(path=str(caminho), full_page=True)
    _passos.append(f"{nome}: {descricao} -> {caminho}")
    print(f"  [{nome}] {descricao}\n        {caminho}")


def _login(page, base: str, email: str, senha: str) -> None:
    if not page.url.startswith(base):
        page.goto(f"{base}{PAINEL}", wait_until="domcontentloaded")
    # O formulário vive na seção "Núcleo administrativo", que só fica visível
    # depois de navegar até ela.
    page.wait_for_selector('[data-section="m15-nucleo"]', timeout=20000)
    page.click('[data-section="m15-nucleo"]')
    page.wait_for_selector("#m15LoginForm", timeout=20000)
    page.fill("#m15Email", email)
    page.fill("#m15Senha", senha)
    page.click("#m15Entrar")
    page.wait_for_selector("#m15LoginForm", state="detached", timeout=20000)


def _abrir_laudos(page) -> None:
    page.click('[data-section="laudos-espirometria"]')
    page.wait_for_selector("#laudos-espirometria", state="visible", timeout=20000)
    page.wait_for_timeout(1200)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE_PADRAO)
    parser.add_argument("--saida", default="/tmp/m25-12-evidencias")
    parser.add_argument(
        "--exame",
        required=True,
        help="código institucional FICTÍCIO sem laudo (ex.: ESP-000003)",
    )
    parser.add_argument("--pdf", default="", help="PDF técnico fictício a anexar")
    parser.add_argument(
        "--email-operacional",
        default=os.environ.get("M25_12_EMAIL_OPERACIONAL", ""),
    )
    parser.add_argument(
        "--senha-operacional",
        default=os.environ.get("M25_12_SENHA_OPERACIONAL", ""),
    )
    parser.add_argument(
        "--email-medica", default=os.environ.get("M25_12_EMAIL_MEDICA", "")
    )
    parser.add_argument(
        "--senha-medica", default=os.environ.get("M25_12_SENHA_MEDICA", "")
    )
    args = parser.parse_args()

    if not (args.email_operacional and args.senha_operacional
            and args.email_medica and args.senha_medica):
        raise SystemExit(
            "Faltam credenciais. Informe --email-*/--senha-* ou as variáveis "
            "M25_12_EMAIL_*/M25_12_SENHA_*. Este script não guarda senha."
        )

    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip("/")

    pdf = Path(args.pdf) if args.pdf else saida / "exame-tecnico-ficticio.pdf"
    if not pdf.exists():
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from scripts.seed_m25_3_laudo_demo import (  # noqa: E402
            _mir_pdf_ficticio,
        )

        pdf.write_bytes(_mir_pdf_ficticio())
        print(f"  PDF técnico FICTÍCIO gerado: {pdf}")

    with sync_playwright() as pw:
        navegador = pw.chromium.launch()
        # 1600x1000: a largura em que a bancada clínica abre em duas colunas.
        contexto = navegador.new_context(viewport={"width": 1600, "height": 1000})
        page = contexto.new_page()

        # ---------------------------------------------- recepção (operacional)
        print("\n== Recepção: localizar exame e anexar o PDF técnico ==")
        _login(page, base, args.email_operacional, args.senha_operacional)
        _abrir_laudos(page)

        # A0 — a falha relatada, agora EXPLICADA em vez de silenciosa.
        page.fill("#reportExamCode", CODIGO_INEXISTENTE)
        page.click("#reportLocateExamForm button[type=submit]")
        page.wait_for_selector(".report-locate-feedback.is-erro", timeout=10000)
        expect(page.locator(".report-locate-feedback")).to_contain_text(
            "Formato não reconhecido"
        )
        _shot(page, saida, "A0-codigo-invalido-explicado",
              f"{CODIGO_INEXISTENTE} recusado COM explicação fixa na tela")

        # A — exame fictício localizado pelo código institucional.
        page.fill("#reportExamCode", args.exame)
        page.click("#reportLocateExamForm button[type=submit]")
        page.wait_for_selector(".report-locate-feedback.is-ok", timeout=15000)
        _shot(page, saida, "A-exame-localizado",
              f"{args.exame} localizado pelo código institucional")

        # B — o formulário de anexar o PDF aparece.
        page.wait_for_selector("#reportUploadForm", timeout=10000)
        page.select_option("#reportPhysician", index=1)
        page.select_option("#reportOriginType", value="clinica_parceira")
        page.select_option("#reportPartnerUnit", index=1)
        page.set_input_files("#reportPdfFile", str(pdf))
        _shot(page, saida, "B-upload-habilitado",
              "campo de PDF técnico habilitado e atribuição preenchida")

        # C — exame atribuído.
        page.click("#reportUploadForm button[type=submit]")
        page.wait_for_selector(".report-locate-feedback.is-ok", timeout=30000)
        expect(page.locator(".report-locate-feedback")).to_contain_text(
            "recebido e atribuído"
        )
        page.wait_for_timeout(1500)
        _shot(page, saida, "C-exame-atribuido",
              "PDF armazenado, laudo criado e atribuído à médica")

        page.click("[data-report-logout]")
        page.wait_for_timeout(1500)

        # ------------------------------------------------------------ médica
        print("\n== Médica: fila, bancada clínica, siglas, prévia ==")
        _login(page, base, args.email_medica, args.senha_medica)
        _abrir_laudos(page)

        # A fila por unidade (M25.6) pede a escolha antes de listar, quando há
        # mais de uma unidade com laudo.
        escolha_unidade = page.locator('[data-report-unit="__todas"]')
        if escolha_unidade.count():
            escolha_unidade.first.click()
            page.wait_for_timeout(1200)

        # D — item na fila.
        page.wait_for_selector(".report-queue-item", timeout=20000)
        _shot(page, saida, "D-fila-medica",
              "“Meus laudos” com o documento fictício atribuído")

        # E — bancada: PDF técnico à esquerda, laudo/conclusão à direita.
        page.locator(".report-queue-item").first.click()
        page.wait_for_selector(".report-clinical-split", timeout=20000)
        page.wait_for_selector(".report-conclusion-chip", timeout=20000)
        page.wait_for_timeout(2500)
        _shot(page, saida, "E-bancada-lado-a-lado",
              "exame técnico (MIR) à esquerda e laudo/conclusão à direita")

        # F — conjunto completo das siglas.
        conclusoes = page.locator(".report-conclusion-chip")
        complementos = page.locator(".report-bd-chip")
        n_conclusoes = conclusoes.count()
        n_complementos = complementos.count()
        print(f"  conclusões na tela: {n_conclusoes} | complementos: {n_complementos}")
        assert n_conclusoes == 18, f"esperado 17 + PERSONALIZADO, veio {n_conclusoes}"
        assert n_complementos == 5, f"esperados 5 complementos, vieram {n_complementos}"
        page.locator(".report-conclusion-picker").first.scroll_into_view_if_needed()
        _shot(page, saida, "F-siglas-conclusoes",
              f"{n_conclusoes} conclusões (17 + PERSONALIZADO) e "
              f"{n_complementos} complementos pós-BD")

        # G — DVO Leve gera o texto por extenso, imediatamente.
        page.click('[data-report-conclusion="DVO_LEVE"]')
        page.wait_for_timeout(400)
        texto = page.input_value("#reportFinalText")
        print(f"  texto após DVO Leve: {texto!r}")
        assert texto.strip() == "Distúrbio ventilatório obstrutivo leve.", texto
        _shot(page, saida, "G-dvo-leve-por-extenso",
              "DVO Leve selecionado → “Distúrbio ventilatório obstrutivo leve.”")

        # H — RBD+ acrescenta o complemento por extenso.
        page.click('[data-report-bd="RBD_POSITIVO"]')
        page.wait_for_timeout(400)
        texto = page.input_value("#reportFinalText")
        print(f"  texto após RBD+: {texto!r}")
        assert "Com resposta significativa ao broncodilatador." in texto, texto
        _shot(page, saida, "H-rbd-mais-por-extenso",
              "RBD+ selecionado → “Com resposta significativa ao broncodilatador.”")

        # Edição manual pela médica — o texto é sempre dela.
        editado = texto + "\nRevisado manualmente pela médica (teste fictício)."
        page.fill("#reportFinalText", editado)
        page.wait_for_timeout(200)

        # I — prévia do laudo.
        page.click("#reportNativeForm button[type=submit]")
        page.wait_for_timeout(6000)
        _shot(page, saida, "I-previa-do-laudo",
              "prévia gerada e exibida ao lado do exame técnico")

        # J — finalização e downloads separados.
        page.locator(".report-release-cta").first.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        _shot(page, saida, "J1-finalizacao-disponivel",
              "assinatura/liberação e downloads separados disponíveis")

        page.click("[data-report-release-open]")
        page.wait_for_selector(".report-release-confirm", timeout=10000)
        _shot(page, saida, "J2-confirmacao-consciente",
              "confirmação consciente antes de assinar e liberar")

        page.click("[data-report-release-confirm]")
        page.wait_for_timeout(8000)
        _shot(page, saida, "J3-laudo-liberado",
              "laudo liberado, com os dois documentos para download separado")

        contexto.close()
        navegador.close()

    print("\n== Evidências ==")
    for linha in _passos:
        print("  " + linha)
    print(f"\n  Pasta: {saida}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
