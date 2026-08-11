"""PDF fictício com a APARÊNCIA de um laudo de espirometria de equipamento.

Nenhum dado real: nomes, valores e curvas são inventados. Serve apenas para
que o <iframe> do harness mostre algo com densidade parecida com a de um
exame de verdade, e a legibilidade do visualizador possa ser julgada.
"""

import sys

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

LARGURA, ALTURA = A4


def desenhar(c: canvas.Canvas, pagina: int) -> None:
    c.setFont("Helvetica-Bold", 13)
    c.drawString(20 * mm, ALTURA - 20 * mm, "MIR SPIROLAB — RELATORIO (FICTICIO)")
    c.setFont("Helvetica", 8)
    c.drawString(20 * mm, ALTURA - 26 * mm,
                 "Paciente Exemplo Alpha da Silva Sauro   Nasc. 17/04/1979   "
                 "ID PAC-000777   Exame ESP-000401")
    c.line(20 * mm, ALTURA - 29 * mm, LARGURA - 20 * mm, ALTURA - 29 * mm)

    # Tabela de parâmetros — números inventados.
    linhas = [
        ("Parametro", "Pre", "%Prev", "Pos", "%Prev", "Delta"),
        ("CVF (L)", "3.92", "88", "4.11", "92", "+4.8%"),
        ("VEF1 (L)", "2.41", "68", "2.67", "75", "+10.8%"),
        ("VEF1/CVF (%)", "61.5", "-", "64.9", "-", "+3.4"),
        ("PFE (L/s)", "6.10", "72", "6.88", "81", "+12.8%"),
        ("FEF25-75 (L/s)", "1.42", "41", "1.78", "51", "+25.3%"),
        ("VEF6 (L)", "3.80", "86", "4.02", "91", "+5.8%"),
        ("TEMPO EXP (s)", "8.4", "-", "8.9", "-", "-"),
    ]
    y = ALTURA - 40 * mm
    for indice, linha in enumerate(linhas):
        c.setFont("Helvetica-Bold" if indice == 0 else "Helvetica", 8)
        for coluna, valor in enumerate(linha):
            c.drawString(20 * mm + coluna * 27 * mm, y, valor)
        y -= 6 * mm

    # "Curva" fluxo-volume inventada.
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y - 6 * mm, "Curva fluxo-volume (ilustrativa)")
    base_x, base_y = 22 * mm, y - 70 * mm
    c.rect(base_x, base_y, 75 * mm, 55 * mm)
    c.setLineWidth(1.1)
    caminho = c.beginPath()
    caminho.moveTo(base_x + 2 * mm, base_y + 3 * mm)
    caminho.curveTo(base_x + 8 * mm, base_y + 48 * mm,
                    base_x + 14 * mm, base_y + 50 * mm,
                    base_x + 22 * mm, base_y + 38 * mm)
    caminho.curveTo(base_x + 40 * mm, base_y + 22 * mm,
                    base_x + 58 * mm, base_y + 10 * mm,
                    base_x + 72 * mm, base_y + 3 * mm)
    c.drawPath(caminho)

    c.rect(base_x + 85 * mm, base_y, 75 * mm, 55 * mm)
    c.setFont("Helvetica", 8)
    c.drawString(base_x + 88 * mm, base_y + 50 * mm, "Volume-tempo (ilustrativa)")
    caminho2 = c.beginPath()
    caminho2.moveTo(base_x + 87 * mm, base_y + 4 * mm)
    caminho2.curveTo(base_x + 100 * mm, base_y + 38 * mm,
                     base_x + 125 * mm, base_y + 44 * mm,
                     base_x + 158 * mm, base_y + 45 * mm)
    c.drawPath(caminho2)

    c.setFont("Helvetica", 7)
    c.drawString(20 * mm, 16 * mm,
                 f"Pagina {pagina} de 2 — DOCUMENTO FICTICIO PARA TESTE DE "
                 "LAYOUT. Nenhum dado de paciente real.")


def main(destino: str) -> None:
    c = canvas.Canvas(destino, pagesize=A4)
    for pagina in (1, 2):
        desenhar(c, pagina)
        c.showPage()
    c.save()


if __name__ == "__main__":
    main(sys.argv[1])
