"""M25.2 — geração nativa do PDF de laudo médico da SoproLife.

Este é um documento PRÓPRIO, criado do zero. Ele NÃO é composto sobre o PDF
técnico da MIR e não contém nenhuma página, imagem ou miniatura do exame: o
PDF do equipamento permanece intacto, armazenado separadamente e baixável
por conta própria.

Regras de layout que este módulo garante:

- área de identificação e assinatura médica é EXCLUSIVA e limpa: nada é
  desenhado dentro dela além do bloco médico;
- nenhum elemento se sobrepõe a outro — o cursor de composição reserva
  altura antes de desenhar e quebra página quando o bloco não cabe;
- o rodapé institucional de cada página tem faixa reservada própria;
- a assinatura manuscrita só aparece depois da liberação, quando existir
  ativo autorizado cadastrado; sem ele o laudo sai apenas com o bloco
  identificador da médica e continua plenamente funcional.

O módulo não interpreta números de espirometria, não calcula grau e não
sugere conclusão: recebe texto já decidido pela médica.
"""

from __future__ import annotations

import io
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

# ------------------------------------------------------------------ marca

NAVY = HexColor("#0B2C4D")
TEAL = HexColor("#1B9C93")
INK = HexColor("#16232F")
MUTED = HexColor("#5A6B7C")
RULE = HexColor("#D6E1EA")
CARD_BG = HexColor("#F3F7FA")
WARN_BG = HexColor("#FFF6E8")
WARN_INK = HexColor("#8A5A10")

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_ITALIC = "Helvetica-Oblique"

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN_X = 44.0
MARGIN_TOP = 34.0
MARGIN_BOTTOM = 38.0
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN_X

# M25.5 — cabeçalho em moldura de três células (marca | local | validação),
# no lugar da faixa solta com régua. A moldura fecha o topo do documento e
# concentra ali a validação, que antes ocupava um cartão inteiro no corpo.
HEADER_HEIGHT = 84.0
HEADER_LOGO_CELL = 158.0
HEADER_CODE_CELL = 104.0
PAGE_FOOTER_RESERVE = 40.0

# Selos da faixa de assinatura. Duas colunas laterais de largura fixa
# emolduram a assinatura; o miolo recebe o que sobra.
SEAL_CELL_WIDTH = 116.0
SEAL_RADIUS = 32.0
SIGNATURE_AREA_HEIGHT = 62.0

LOGO_MAX_HEIGHT = 34.0
LOGO_MAX_WIDTH = 150.0

INSTITUTION_NAME = "SoproLife Diagnósticos e Soluções em Saúde"
DOCUMENT_TITLE = "Laudo de Espirometria"

# M25.4 — nota enxuta. A versão anterior gastava três linhas em caixa
# própria para dizer o que cabe em uma: o essencial é que o traçado está em
# OUTRO documento, intacto.
MIR_SEPARATE_NOTICE = (
    "Traçado e medidas originais constam do PDF técnico do equipamento "
    "(MIR) — documento SEPARADO deste laudo, inalterado, com download "
    "próprio."
)

# Declaração honesta da natureza da liberação. NÃO afirma ICP-Brasil.
# M25.4 — encurtada sem perder nenhuma das três afirmações que precisam
# constar: quem liberou e como, o que prova a integridade, e o que esta
# liberação NÃO é.
# M25.18 — o texto do rodapé descreve o que este arquivo É.
#
# A frase anterior começava com "Liberado eletronicamente", o que numa
# leitura rápida soa como assinatura eletrônica aplicada. O documento que
# sai daqui é o laudo concluído pela médica no sistema, destinado à
# assinatura qualificada que ela aplica FORA da SoproLife, com o próprio
# certificado. Quem receber o arquivo precisa saber onde conferir a
# assinatura: no arquivo assinado, não neste.
#
# A negativa explícita sobre ICP-Brasil continua, porque é ela que impede a
# leitura errada enquanto não houver prova criptográfica.
RELEASE_STATEMENT = (
    "Documento concluído pela médica responsável no sistema SoproLife, com "
    "autenticação individual e ação consciente registradas. Integridade "
    "verificável pelo código e pelo hash SHA-256 deste laudo. A "
    "autenticidade da assinatura digital deve ser verificada no arquivo "
    "eletronicamente assinado; esta conclusão não constitui, por si só, "
    "assinatura digital qualificada ICP-Brasil."
)

PREVIEW_WATERMARK = "PRÉVIA — DOCUMENTO NÃO CONCLUÍDO"

DEFAULT_LOGO_PATH = (
    Path(__file__).resolve().parents[3] / "assets" / "soprolife-logo.png"
)
# O PNG versionado tem 828px de largura (~325 KB). Reamostrar uma única vez
# para a resolução realmente usada mantém a nitidez e evita carregar
# centenas de KB em cada laudo emitido.
_LOGO_RENDER_WIDTH_PX = 420
_logo_cache: dict[str, "ImageReader | None"] = {}


def _load_logo_reader(path: Path) -> "ImageReader | None":
    """ImageReader reamostrado e memorizado por caminho.

    Reutilizar o MESMO objeto em todas as páginas faz o reportlab embutir a
    imagem uma única vez no PDF final.
    """

    key = str(path)
    if key in _logo_cache:
        return _logo_cache[key]
    reader: ImageReader | None = None
    try:
        if path.is_file():
            from PIL import Image

            with Image.open(path) as source:
                source.load()
                image = source.convert("RGBA")
                if image.width > _LOGO_RENDER_WIDTH_PX:
                    ratio = _LOGO_RENDER_WIDTH_PX / image.width
                    image = image.resize(
                        (
                            _LOGO_RENDER_WIDTH_PX,
                            max(1, round(image.height * ratio)),
                        ),
                        Image.LANCZOS,
                    )
                buffer = io.BytesIO()
                image.save(buffer, format="PNG", optimize=True)
            buffer.seek(0)
            reader = ImageReader(buffer)
            reader.getSize()
    except Exception:
        # Logo ausente ou ilegível nunca impede a emissão do laudo.
        reader = None
    _logo_cache[key] = reader
    return reader


class NativeReportPdfError(ValueError):
    """Erro de geração com `codigo` estável para a resposta 422/500."""

    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


# ------------------------------------------------------------------ dados


@dataclass(frozen=True)
class PatientBlock:
    full_name: str
    birth_date: date | None
    sex: str | None
    public_code: str | None
    # M25.18 — já formatado por quem monta o conteúdo. `None` quando o
    # cadastro não tem CPF: o laudo simplesmente não imprime a linha, em vez
    # de imprimir "não informado" no lugar de um documento de identidade.
    cpf: str | None = None


@dataclass(frozen=True)
class ExamBlock:
    public_code: str
    exam_date: date | None
    exam_time: str | None
    date_precision: str | None
    has_post_bd: bool | None
    clinical_indication: str | None


@dataclass(frozen=True)
class LocationBlock:
    name: str
    address_line: str | None
    contact_line: str | None


@dataclass(frozen=True)
class PhysicianBlock:
    professional_name: str
    specialty: str | None
    crm_display: str
    crm_state: str
    rqe: str | None


# Tipos de assinatura que o selo lateral pode declarar. O valor NUNCA é
# escolhido pelo desenhista: ele chega pronto de quem sabe o que de fato
# aconteceu (o router, a partir da evidência gravada). É assim que o selo
# continua correspondendo ao tipo real de assinatura mesmo quando um
# provedor ICP-Brasil entrar no ar.
SIGNATURE_KIND_INSTITUTIONAL = "institutional"
SIGNATURE_KIND_QUALIFIED_ICP = "qualified_icp"


@dataclass(frozen=True)
class SignatureImage:
    """Bytes já revalidados do ativo manuscrito autorizado."""

    data: bytes
    width: int
    height: int


@dataclass(frozen=True)
class AddendumBlock:
    sequence: int
    body_text: str
    created_at: datetime


@dataclass(frozen=True)
class NativeReportContent:
    """Tudo o que entra no laudo — nada é buscado pelo gerador."""

    document_code: str
    version_number: int
    patient: PatientBlock
    exam: ExamBlock
    location: LocationBlock
    physician: PhysicianBlock
    conclusion_text: str
    observations: str | None
    issued_at_local: datetime
    released: bool
    released_at_local: datetime | None = None
    validation_code: str | None = None
    validation_url: str | None = None
    signature_image: SignatureImage | None = None
    # Fail-closed: sem informação em contrário, o selo declara a liberação
    # institucional. Nunca assume ICP-Brasil por omissão.
    signature_kind: str = SIGNATURE_KIND_INSTITUTIONAL
    addenda: tuple[AddendumBlock, ...] = field(default_factory=tuple)
    pilot_warning: str | None = None
    logo_path: Path | None = None
    # Rótulo do fuso usado nas datas apresentadas (settings.display_timezone).
    timezone_label: str = "America/Sao_Paulo"


# ------------------------------------------------------------- utilidades

# Helvetica usa WinAnsi: caracteres fora dele viram caixa preta. O texto
# clínico é preservado, mas pontuação tipográfica é rebaixada para um
# equivalente representável em vez de corromper o documento.
_CHAR_FALLBACK = {
    # Travessão, aspas tipográficas, reticências e bullet EXISTEM em
    # WinAnsi e são preservados. Só mapeamos o que de fato não é
    # representável: o sinal de menos U+2212 (rótulo curto "RBD−") e os
    # espaços especiais.
    "\u2212": "-",
    "\u00a0": " ",
    "\u202f": " ",
    "\u2009": " ",
    "\u200b": "",
    "\t": "    ",
}


def pdf_safe(text: str) -> str:
    """Texto representável em WinAnsi, sem descartar conteúdo silenciosamente."""

    normalized = unicodedata.normalize("NFC", text)
    out: list[str] = []
    for char in normalized:
        replacement = _CHAR_FALLBACK.get(char)
        if replacement is not None:
            out.append(replacement)
            continue
        try:
            char.encode("cp1252")
        except UnicodeEncodeError:
            # Última tentativa: decompor o acento (ex.: caracteres latinos
            # combinados) antes de recorrer a "?".
            decomposed = unicodedata.normalize("NFKD", char)
            ascii_form = "".join(
                part for part in decomposed if not unicodedata.combining(part)
            )
            try:
                ascii_form.encode("cp1252")
            except UnicodeEncodeError:
                out.append("?")
            else:
                out.append(ascii_form or "?")
            continue
        out.append(char)
    return "".join(out)


def _width(text: str, font: str, size: float) -> float:
    return float(pdfmetrics.stringWidth(text, font, size))


def wrap_text(
    text: str, *, font: str, size: float, max_width: float
) -> list[str]:
    """Quebra por largura renderizada, preservando quebras explícitas."""

    lines: list[str] = []
    for paragraph in pdf_safe(text).split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}".strip()
            if not current or _width(candidate, font, size) <= max_width:
                current = candidate
                continue
            lines.append(current)
            current = word
            # Palavra isolada mais larga que a coluna: parte por caractere
            # em vez de estourar a margem.
            while _width(current, font, size) > max_width and len(current) > 1:
                cut = len(current) - 1
                while cut > 1 and _width(current[:cut], font, size) > max_width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        lines.append(current)
    return lines or [""]


def format_date(value: date | None, precision: str | None = None) -> str:
    if value is None:
        return "não informada"
    if precision == "ano":
        return str(value.year)
    if precision == "mes":
        return f"{value.month:02d}/{value.year}"
    return f"{value.day:02d}/{value.month:02d}/{value.year}"


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "não informada"
    return (
        f"{value.day:02d}/{value.month:02d}/{value.year} "
        f"às {value.hour:02d}:{value.minute:02d}"
    )


def format_age(birth: date | None, reference: date) -> str | None:
    if birth is None or birth > reference:
        return None
    years = reference.year - birth.year
    if (reference.month, reference.day) < (birth.month, birth.day):
        years -= 1
    if years < 0:
        return None
    return f"{years} anos"


SEX_LABELS = {
    "feminino": "Feminino",
    "masculino": "Masculino",
    "f": "Feminino",
    "m": "Masculino",
}


def format_sex(value: str | None) -> str:
    if not value:
        return "não informado"
    return SEX_LABELS.get(value.strip().casefold(), value.strip())


# ---------------------------------------------------------------- desenho


class _Composer:
    """Cursor de composição top-down com quebra de página explícita."""

    def __init__(self, content: NativeReportContent):
        self.content = content
        self.buffer = io.BytesIO()
        self.canvas = canvas.Canvas(self.buffer, pagesize=A4)
        self.canvas.setTitle(pdf_safe(DOCUMENT_TITLE))
        self.canvas.setAuthor(pdf_safe(INSTITUTION_NAME))
        self.canvas.setSubject(
            pdf_safe(f"Laudo {content.document_code} v{content.version_number}")
        )
        # Sem Creator/Producer personalizados com dado de paciente.
        self.canvas.setCreator(pdf_safe(INSTITUTION_NAME))
        self.page_index = 0
        self.y = 0.0
        self._start_page()

    # ---------------------------------------------------------- estrutura

    @property
    def bottom_limit(self) -> float:
        return MARGIN_BOTTOM + PAGE_FOOTER_RESERVE

    def _start_page(self) -> None:
        self.page_index += 1
        self._draw_header()
        self.y = PAGE_HEIGHT - MARGIN_TOP - HEADER_HEIGHT

    def new_page(self) -> None:
        self._draw_page_footer()
        self.canvas.showPage()
        self._start_page()

    def ensure(self, height: float) -> None:
        """Garante espaço contínuo; quebra a página quando não houver."""

        if self.y - height < self.bottom_limit:
            self.new_page()

    def finish(self) -> bytes:
        self._draw_page_footer()
        self.canvas.save()
        return self.buffer.getvalue()

    # ------------------------------------------------------------ header

    def _draw_header(self) -> None:
        """Moldura de topo: marca | local de realização | validação.

        O local vem SEMPRE de `content.location`, resolvido a partir da
        unidade vinculada ao exame. Nenhum endereço é escrito no template:
        trocar a clínica troca o cabeçalho, sem tocar neste arquivo.
        """

        c = self.canvas
        top = PAGE_HEIGHT - MARGIN_TOP
        bottom = top - HEADER_HEIGHT
        right = PAGE_WIDTH - MARGIN_X
        divider_left = MARGIN_X + HEADER_LOGO_CELL
        divider_right = right - HEADER_CODE_CELL

        c.setStrokeColor(RULE)
        c.setLineWidth(0.8)
        c.rect(MARGIN_X, bottom, CONTENT_WIDTH, HEADER_HEIGHT, stroke=1, fill=0)
        c.line(divider_left, bottom, divider_left, top)
        c.line(divider_right, bottom, divider_right, top)

        self._draw_logo_cell(
            left=MARGIN_X, right=divider_left, top=top, bottom=bottom
        )
        self._draw_location_cell(
            left=divider_left, right=divider_right, top=top, bottom=bottom
        )
        self._draw_validation_cell(
            left=divider_right, right=right, top=top, bottom=bottom
        )

    def _draw_logo_cell(
        self, *, left: float, right: float, top: float, bottom: float
    ) -> None:
        center_x = (left + right) / 2
        center_y = (top + bottom) / 2
        reader = _load_logo_reader(self.content.logo_path or DEFAULT_LOGO_PATH)
        size = self._logo_size(reader)
        if size is None:
            c = self.canvas
            c.setFont(FONT_BOLD, 15)
            c.setFillColor(NAVY)
            c.drawCentredString(center_x, center_y - 5, "SoproLife")
            return
        width, height = size
        self.canvas.drawImage(
            reader,
            center_x - width / 2,
            center_y - height / 2,
            width=width,
            height=height,
            mask="auto",
            preserveAspectRatio=True,
            anchor="c",
        )

    def _logo_size(self, reader) -> tuple[float, float] | None:
        if reader is None:
            return None
        try:
            src_w, src_h = reader.getSize()
        except Exception:
            return None
        if src_w <= 0 or src_h <= 0:
            return None
        scale = min(LOGO_MAX_WIDTH / src_w, LOGO_MAX_HEIGHT / src_h)
        return src_w * scale, src_h * scale

    def _draw_location_cell(
        self, *, left: float, right: float, top: float, bottom: float
    ) -> None:
        location = self.content.location
        width = right - left - 16
        center_x = (left + right) / 2

        lines: list[tuple[str, str, float]] = []
        for line in wrap_text(
            location.name, font=FONT_BOLD, size=8.6, max_width=width
        ):
            lines.append((line, FONT_BOLD, 8.6))
        for raw in (location.address_line, location.contact_line):
            if not raw:
                continue
            for line in wrap_text(raw, font=FONT, size=7.6, max_width=width):
                lines.append((line, FONT, 7.6))

        total = sum(size + 2.6 for _text, _font, size in lines)
        text_y = (top + bottom) / 2 + total / 2 - 8
        c = self.canvas
        for text, font, size in lines:
            c.setFont(font, size)
            c.setFillColor(NAVY if font == FONT_BOLD else MUTED)
            c.drawCentredString(center_x, text_y, text)
            text_y -= size + 2.6

    def _draw_validation_cell(
        self, *, left: float, right: float, top: float, bottom: float
    ) -> None:
        """Célula de validação: QR e código de verificação.

        M25.5 — antes isto era um cartão inteiro no meio do laudo. Como é
        metadado de conferência, e não conteúdo clínico, subiu para o
        cabeçalho e liberou uma faixa inteira do corpo.
        """

        content = self.content
        center_x = (left + right) / 2
        code = content.validation_code

        c = self.canvas
        if content.validation_url:
            size = 50.0
            self._draw_qr(
                content.validation_url,
                x=center_x - size / 2,
                y=bottom + (HEADER_HEIGHT - size) / 2 + 5,
                size=size,
            )
            # Com QR, a legenda fica no pé da célula, abaixo do código.
            caption_y = bottom + 7
        else:
            # Sem URL de validação não há QR, e ancorar a legenda no pé
            # deixava dois terços da célula em branco. O código então ocupa
            # o centro, como qualquer texto sozinho numa caixa.
            caption_y = (top + bottom) / 2 - 4
            if not content.released:
                c.setFont(FONT_BOLD, 7.0)
                c.setFillColor(WARN_INK)
                c.drawCentredString(
                    center_x, caption_y + 20, pdf_safe("PRÉVIA")
                )

        c.setFont(FONT, 5.6)
        c.setFillColor(MUTED)
        c.drawCentredString(
            center_x, caption_y + 8, pdf_safe("Código de verificação")
        )
        c.setFont(FONT_BOLD, 7.2)
        c.setFillColor(NAVY)
        c.drawCentredString(center_x, caption_y, pdf_safe(code or "—"))

    # ------------------------------------------------------------ rodapé

    def _draw_page_footer(self) -> None:
        c = self.canvas
        content = self.content
        y = MARGIN_BOTTOM + 24

        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(MARGIN_X, y, PAGE_WIDTH - MARGIN_X, y)
        c.setFont(FONT, 6.8)
        c.setFillColor(MUTED)
        left = (
            f"{INSTITUTION_NAME} • Laudo {content.document_code} "
            f"• versão {content.version_number}"
        )
        c.drawString(MARGIN_X, y - 10, pdf_safe(left))
        c.drawRightString(
            PAGE_WIDTH - MARGIN_X,
            y - 10,
            pdf_safe(f"Página {self.page_index}"),
        )

        # Faixa de validação: fecha a página do jeito que um laudo emitido
        # fecha — dizendo onde conferir e com qual código. Só existe quando
        # há URL configurada; sem ela, o código já está no cabeçalho.
        if not (content.validation_url and content.validation_code):
            return
        strip_height = 14.0
        strip_y = MARGIN_BOTTOM - 2
        c.setFillColor(CARD_BG)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.rect(
            MARGIN_X, strip_y, CONTENT_WIDTH, strip_height, stroke=1, fill=1
        )
        # A URL de validação normalmente já termina com o próprio código.
        # Quando termina, pedir "e informe o código" logo depois imprime o
        # mesmo dado duas vezes na mesma linha.
        aviso = f"Para validar este documento acesse {content.validation_url}"
        if not content.validation_url.rstrip("/").endswith(
            content.validation_code
        ):
            aviso = (
                f"{aviso} e informe o código de verificação "
                f"{content.validation_code}"
            )
        c.setFont(FONT, 6.6)
        c.setFillColor(INK)
        c.drawCentredString(
            MARGIN_X + CONTENT_WIDTH / 2, strip_y + 4.6, pdf_safe(aviso)
        )

    # ---------------------------------------------------------- primitivas

    def space(self, height: float) -> None:
        self.y -= height

    def draw_title(self) -> None:
        """Barra de título: o documento se nomeia em uma linha só.

        M25.5 — antes eram um título de 19pt e uma linha de estado logo
        abaixo, ocupando 34pt de altura para dizer duas coisas curtas. A
        barra diz as mesmas duas coisas em 20pt.
        """

        height = 20.0
        self.ensure(height + 8)
        c = self.canvas
        top = self.y
        c.setFillColor(CARD_BG)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.8)
        c.rect(MARGIN_X, top - height, CONTENT_WIDTH, height, stroke=1, fill=1)

        c.setFont(FONT_BOLD, 11.5)
        c.setFillColor(NAVY)
        c.drawCentredString(
            MARGIN_X + CONTENT_WIDTH / 2, top - 14, pdf_safe(DOCUMENT_TITLE)
        )

        status = (
            "DOCUMENTO LIBERADO"
            if self.content.released
            else PREVIEW_WATERMARK
        )
        c.setFont(FONT_BOLD, 6.4)
        c.setFillColor(TEAL if self.content.released else WARN_INK)
        c.drawRightString(
            PAGE_WIDTH - MARGIN_X - 8, top - 13, pdf_safe(status)
        )
        self.y = top - height - 8

    def draw_banner(self, text: str) -> None:
        """Faixa de aviso (prévia/piloto) — nunca sobre outro conteúdo."""

        lines = wrap_text(
            text, font=FONT_BOLD, size=8.5, max_width=CONTENT_WIDTH - 20
        )
        height = 12 + len(lines) * 11
        self.ensure(height + 8)
        c = self.canvas
        top = self.y
        c.setFillColor(WARN_BG)
        c.setStrokeColor(WARN_INK)
        c.setLineWidth(0.7)
        c.rect(
            MARGIN_X, top - height, CONTENT_WIDTH, height, stroke=1, fill=1
        )
        c.setFont(FONT_BOLD, 8.5)
        c.setFillColor(WARN_INK)
        text_y = top - 16
        for line in lines:
            c.drawString(MARGIN_X + 10, text_y, line)
            text_y -= 11
        self.y = top - height - 7

    def draw_section_heading(self, title: str, *, space_before: float = 0.0) -> None:
        """Título de seção, com respiro configurável acima.

        M25.6 — `space_before` existe porque "OBSERVAÇÕES COMPLEMENTARES"
        colava na última linha da conclusão: as duas seções pareciam um
        bloco só. O respiro é do CHAMADOR porque ele sabe o que veio antes.
        """

        # A régua fica 8pt abaixo da linha de base, não 5: com 5 ela passava
        # dentro da cedilha de "CONCLUSÕES" e parecia um risco no texto.
        self.ensure(22 + space_before)
        self.y -= space_before
        c = self.canvas
        c.setFont(FONT_BOLD, 9.6)
        c.setFillColor(NAVY)
        c.drawString(MARGIN_X, self.y - 10, pdf_safe(title.upper()))
        c.setStrokeColor(TEAL)
        c.setLineWidth(1.0)
        c.line(MARGIN_X, self.y - 18, MARGIN_X + 22, self.y - 18)
        self.y -= 22

    def draw_identification_table(
        self,
        left_fields: list[tuple[str, str]],
        right_fields: list[tuple[str, str]],
    ) -> None:
        """Bloco único de identificação, em duas colunas com rótulo em linha.

        M25.5 — antes eram dois cartões empilhados (paciente e exame), cada
        um com título próprio e cada campo gastando duas linhas: o rótulo em
        caixa alta acima e o valor abaixo. Onze campos consumiam mais de um
        terço da página. No formato "Rótulo: valor" o mesmo conteúdo cabe em
        uma moldura só, e nenhum dado foi removido.
        """

        label_size = 8.4
        value_size = 8.4
        padding = 10.0
        column_width = (CONTENT_WIDTH - 3 * padding) / 2

        def layout(fields: list[tuple[str, str]]) -> list[tuple[str, str, bool]]:
            """Quebra cada campo em linhas prontas para desenhar.

            A primeira linha carrega o rótulo; as continuações entram
            recuadas, sem repetir o rótulo.
            """

            rendered: list[tuple[str, str, bool]] = []
            for label, value in fields:
                prefix = f"{label}: "
                prefix_width = _width(pdf_safe(prefix), FONT_BOLD, label_size)
                wrapped = wrap_text(
                    value or "—",
                    font=FONT,
                    size=value_size,
                    max_width=column_width - prefix_width,
                )
                rendered.append((prefix, wrapped[0], True))
                for extra in wrapped[1:]:
                    rendered.append(("", extra, False))
            return rendered

        left = layout(left_fields)
        right = layout(right_fields)
        line_height = 12.4
        rows = max(len(left), len(right))
        height = rows * line_height + 2 * padding
        self.ensure(height + 8)

        c = self.canvas
        top = self.y
        c.setStrokeColor(RULE)
        c.setLineWidth(0.8)
        c.rect(MARGIN_X, top - height, CONTENT_WIDTH, height, stroke=1, fill=0)

        for index, column in enumerate((left, right)):
            x = MARGIN_X + padding + index * (column_width + padding)
            text_y = top - padding - value_size
            for prefix, value, is_first in column:
                if is_first and prefix:
                    c.setFont(FONT_BOLD, label_size)
                    c.setFillColor(NAVY)
                    c.drawString(x, text_y, pdf_safe(prefix))
                    offset = _width(pdf_safe(prefix), FONT_BOLD, label_size)
                else:
                    offset = 8.0
                c.setFont(FONT, value_size)
                c.setFillColor(INK)
                c.drawString(x + offset, text_y, pdf_safe(value))
                text_y -= line_height
        self.y = top - height - 9

    def draw_paragraph(
        self,
        text: str,
        *,
        size: float = 10.5,
        leading: float = 14.0,
        font: str = FONT,
        color: Color = INK,
        emphasis_box: bool = False,
    ) -> None:
        """Parágrafo que pode fluir por várias páginas sem sobreposição."""

        padding = 12.0 if emphasis_box else 0.0
        max_width = CONTENT_WIDTH - 2 * padding - (6 if emphasis_box else 0)
        lines = wrap_text(text, font=font, size=size, max_width=max_width)
        index = 0
        while index < len(lines):
            available = self.y - self.bottom_limit - 2 * padding
            if available < leading:
                self.new_page()
                available = self.y - self.bottom_limit - 2 * padding
            capacity = max(1, int(available // leading))
            chunk = lines[index : index + capacity]
            block_height = len(chunk) * leading + 2 * padding
            c = self.canvas
            top = self.y
            if emphasis_box:
                c.setFillColor(CARD_BG)
                c.setStrokeColor(RULE)
                c.setLineWidth(0.6)
                c.rect(
                    MARGIN_X,
                    top - block_height,
                    CONTENT_WIDTH,
                    block_height,
                    stroke=1,
                    fill=1,
                )
                c.setFillColor(TEAL)
                c.rect(
                    MARGIN_X,
                    top - block_height,
                    2.6,
                    block_height,
                    stroke=0,
                    fill=1,
                )
            c.setFont(font, size)
            c.setFillColor(color)
            text_y = top - padding - size
            for line in chunk:
                c.drawString(MARGIN_X + padding + (6 if emphasis_box else 0), text_y, line)
                text_y -= leading
            self.y = top - block_height - (6 if emphasis_box else 2)
            index += capacity

    # ------------------------------------------- bloco médico + assinatura

    def _statement_text(self) -> str:
        return (
            RELEASE_STATEMENT
            if self.content.released
            else (
                "Prévia sem validade: a identificação e a rubrica da médica "
                "só são aplicadas após a ação \"Concluir laudo\"."
            )
        )

    def _signature_block_height(self) -> float:
        """Altura exata da faixa.

        Especialidade, CRM e RQE moram na MESMA linha da identificação, então
        a contagem é fixa: nome e credenciais. Antes cada um ocupava uma
        linha própria e a especialidade fazia a faixa crescer.
        """

        statement_lines = wrap_text(
            self._statement_text(),
            font=FONT_ITALIC,
            size=7.2,
            max_width=CONTENT_WIDTH - 24,
        )
        return (
            23  # respiro superior + linha da data de liberação
            + SIGNATURE_AREA_HEIGHT
            + 6
            + 1  # linha de assinatura
            + 13  # nome
            + 12  # especialidade, CRM e RQE
            + 8
            + len(statement_lines) * 8.6
            + 6
        )

    def draw_physician_signature_block(self) -> None:
        """Faixa EXCLUSIVA de assinatura, em três colunas.

        M25.5 — o selo do TIPO de assinatura fica à esquerda, a assinatura
        manuscrita e a identificação da médica no centro, o selo institucional
        da SoproLife à direita. Os selos ocupam colunas próprias e nunca são
        desenhados sobre a área reservada da assinatura.
        """

        height = self._signature_block_height()
        # Bloco atômico: nunca é dividido entre páginas.
        self.ensure(height)
        # A faixa segue o fluxo do texto e NÃO é ancorada no pé da página.
        # Ancorar foi tentado e piorou: num laudo curto abria um vão morto
        # entre a nota do MIR e a assinatura, e o vão no meio chama mais
        # atenção que a mesma sobra no fim da folha.
        c = self.canvas
        top = self.y
        physician = self.content.physician

        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(MARGIN_X, top, PAGE_WIDTH - MARGIN_X, top)

        center_x = MARGIN_X + CONTENT_WIDTH / 2
        # A régua de assinatura não ocupa a coluna central inteira: uma linha
        # de 250pt sob um traço estreito parecia um campo vazio a preencher.
        line_width = 200.0
        line_x = center_x - line_width / 2

        # Data de liberação acima da assinatura, como num laudo emitido.
        moment = self.content.released_at_local or self.content.issued_at_local
        c.setFont(FONT, 7.6)
        c.setFillColor(MUTED)
        # M25.18 — "Liberado" sugeria documento pronto para entrega; ele
        # ainda vai ser assinado fora do sistema. A data é a da conclusão
        # clínica, e o rótulo passou a dizer isso.
        prefix = (
            "Concluído em" if self.content.released else "Prévia gerada em"
        )
        c.drawCentredString(
            center_x,
            top - 14,
            pdf_safe(
                f"{prefix} {format_datetime(moment)} "
                f"({self.content.timezone_label})"
            ),
        )

        signature_area_top = top - 23
        line_y = signature_area_top - SIGNATURE_AREA_HEIGHT - 6

        # Área reservada da assinatura manuscrita. Fica vazia quando não há
        # ativo autorizado — nada é desenhado por cima em nenhum caso.
        image = self.content.signature_image if self.content.released else None
        if image is not None:
            self._draw_signature_image(
                image,
                center_x=center_x,
                baseline_y=line_y + 3,
                max_width=line_width - 20,
                max_height=SIGNATURE_AREA_HEIGHT,
            )

        c.setStrokeColor(NAVY)
        c.setLineWidth(0.8)
        c.line(line_x, line_y, line_x + line_width, line_y)

        seal_center_y = line_y + SIGNATURE_AREA_HEIGHT / 2 - 4
        self.draw_signature_type_seal(
            center_x=MARGIN_X + SEAL_CELL_WIDTH / 2,
            center_y=seal_center_y,
        )
        self.draw_institutional_seal(
            center_x=PAGE_WIDTH - MARGIN_X - SEAL_CELL_WIDTH / 2,
            center_y=seal_center_y,
        )

        text_y = line_y - 13
        c.setFont(FONT_BOLD, 10.5)
        c.setFillColor(NAVY)
        c.drawCentredString(
            center_x, text_y, pdf_safe(physician.professional_name)
        )
        text_y -= 12
        registry = f"CRM-{physician.crm_state} {physician.crm_display}"
        if physician.rqe:
            registry = f"{registry}   •   RQE {physician.rqe}"
        if physician.specialty:
            registry = f"{physician.specialty}   •   {registry}"
        c.setFont(FONT, 8.6)
        c.setFillColor(INK)
        c.drawCentredString(center_x, text_y, pdf_safe(registry))
        text_y -= 10

        lines = wrap_text(
            self._statement_text(),
            font=FONT_ITALIC,
            size=7.2,
            max_width=CONTENT_WIDTH - 24,
        )
        c.setFont(FONT_ITALIC, 7.2)
        c.setFillColor(MUTED)
        text_y -= 8
        for line in lines:
            c.drawCentredString(center_x, text_y, line)
            text_y -= 8.6

        self.y = top - height

    def _draw_seal_rings(
        self, *, center_x: float, center_y: float, radius: float, color: Color
    ) -> None:
        c = self.canvas
        c.setStrokeColor(color)
        c.setLineWidth(1.6)
        c.circle(center_x, center_y, radius, stroke=1, fill=0)
        c.setLineWidth(0.5)
        c.circle(center_x, center_y, radius - 4.2, stroke=1, fill=0)

    def _draw_seal_text(
        self,
        text: str,
        *,
        center_x: float,
        center_y: float,
        dy: float,
        inner: float,
        size: float,
        color: Color,
    ) -> None:
        """Escreve dentro do anel, encolhendo até caber na corda daquela altura.

        Num círculo a largura útil encolhe conforme o texto se afasta do
        centro. Estimar isso à mão foi o que fez "INSTITUCIONAL" e
        "E SOLUÇÕES EM SAÚDE" vazarem para fora do anel. Agora a largura vem
        da corda real e a fonte cede até caber — nenhuma linha de selo pode
        mais escapar do círculo, qualquer que seja o texto.
        """

        safe = pdf_safe(text)
        # A corda é medida na BORDA SUPERIOR da caixa de texto (linha de base
        # mais a altura de caixa alta), não na linha de base: é o topo das
        # letras que encosta no anel primeiro.
        extreme = abs(dy) + size * 0.78
        half_chord = max(inner**2 - extreme**2, 1.0) ** 0.5
        available = half_chord * 2 - 5.0
        while size > 2.6 and _width(safe, FONT_BOLD, size) > available:
            size -= 0.1
        c = self.canvas
        c.setFont(FONT_BOLD, size)
        c.setFillColor(color)
        c.drawCentredString(center_x, center_y + dy, safe)

    def draw_signature_type_seal(
        self, *, center_x: float, center_y: float, radius: float = SEAL_RADIUS
    ) -> None:
        """Selo do TIPO de assinatura aplicada — nunca do tipo desejado.

        M25.5. O texto vem de `content.signature_kind`, que por sua vez vem
        da evidência realmente gravada. Enquanto não houver provedor
        ICP-Brasil conectado, este selo diz "liberação institucional", que é
        o que de fato aconteceu. No dia em que a assinatura qualificada
        entrar, o MESMO selo passa a declarar ICP-Brasil sem que nenhuma
        outra parte do laudo precise mudar.

        Não é desenhado em prévia: um selo de assinatura num documento não
        assinado seria mentira visual.
        """

        if not self.content.released:
            return
        qualified = self.content.signature_kind == SIGNATURE_KIND_QUALIFIED_ICP
        c = self.canvas
        c.saveState()
        self._draw_seal_rings(
            center_x=center_x,
            center_y=center_y,
            radius=radius,
            color=NAVY if qualified else TEAL,
        )

        inner = radius - 4.2
        # M25.18 — o selo dizia "ASSINADO ELETRONICAMENTE / LIBERAÇÃO
        # INSTITUCIONAL". "Assinado" num carimbo redondo é lido como
        # assinatura, e a distinção fina entre "eletronicamente" e
        # "digitalmente" não sobrevive à leitura de quem recebe o papel.
        #
        # Antes da assinatura externa o selo passa a dizer o que de fato
        # aconteceu: a médica CONCLUIU o laudo. Quando houver prova
        # criptográfica gravada, o mesmo selo volta a declarar assinatura —
        # o texto continua saindo de `signature_kind`, nunca de intenção.
        #
        # M25.21 — o selo pré-assinatura perdeu as duas linhas de baixo
        # ("AGUARDANDO / ASSINATURA").
        #
        # Elas eram VERDADEIRAS no instante em que o PDF era gerado e
        # FALSAS logo depois: a médica baixa exatamente este arquivo, aplica
        # a assinatura qualificada nele por fora (VIDaaS) e devolve o mesmo
        # PDF assinado. A assinatura entra na camada PDF; o desenho do selo
        # continua impresso do jeito que saiu daqui. O documento assinado
        # ficaria carimbado "AGUARDANDO ASSINATURA" para sempre — e quem o
        # recebesse leria a negativa mais forte que o próprio arquivo.
        #
        # "CONCLUÍDO PELA MÉDICA" não tem esse problema: é um fato sobre o
        # ato clínico, permanece verdadeiro antes e depois da assinatura, e
        # não afirma nada sobre ICP-Brasil. A negativa explícita sobre a
        # assinatura qualificada continua no rodapé (`RELEASE_STATEMENT`),
        # que é texto do documento e não carimbo.
        #
        # O ESTADO OPERACIONAL não mudou. "Aguardando assinatura
        # qualificada", "Assinado recebido — validação pendente", "Pronto
        # para entrega" e "Entregue" continuam onde sempre estiveram: no
        # Centro de Comando, que sabe a hora certa de cada um. Um carimbo
        # impresso não sabe.
        #
        # O ramo qualificado segue idêntico: quando houver prova
        # criptográfica gravada, o selo volta a ter quatro linhas e a
        # declarar ICP-Brasil / PADRÃO PAdES.
        if qualified:
            self._draw_seal_text(
                "ASSINADO",
                center_x=center_x, center_y=center_y, dy=16.0,
                inner=inner, size=5.8, color=NAVY,
            )
            self._draw_seal_text(
                "DIGITALMENTE",
                center_x=center_x, center_y=center_y, dy=9.4,
                inner=inner, size=5.0, color=NAVY,
            )

            c.setStrokeColor(RULE)
            c.setLineWidth(0.5)
            c.line(center_x - inner * 0.62, center_y + 3.4,
                   center_x + inner * 0.62, center_y + 3.4)

            self._draw_seal_text(
                "ICP-BRASIL",
                center_x=center_x, center_y=center_y, dy=-6.0,
                inner=inner, size=6.0, color=NAVY,
            )
            self._draw_seal_text(
                "PADRÃO PAdES",
                center_x=center_x, center_y=center_y, dy=-14.0,
                inner=inner, size=4.8, color=NAVY,
            )
        else:
            # Duas linhas só: elas se centralizam no anel em vez de ficarem
            # empurradas para o topo, deixando meio selo vazio. A régua
            # divisória sai junto — ela separava duas afirmações, e agora há
            # uma só.
            self._draw_seal_text(
                "CONCLUÍDO",
                center_x=center_x, center_y=center_y, dy=4.6,
                inner=inner, size=6.6, color=NAVY,
            )
            self._draw_seal_text(
                "PELA MÉDICA",
                center_x=center_x, center_y=center_y, dy=-5.4,
                inner=inner, size=5.6, color=NAVY,
            )
        c.restoreState()

    def draw_institutional_seal(
        self, *, center_x: float, center_y: float, radius: float = SEAL_RADIUS
    ) -> None:
        """Selo institucional da SoproLife — identidade PRÓPRIA.

        Inspirado apenas na ORGANIZAÇÃO de um laudo profissional (um selo
        fecha o documento), sem copiar marca, arte ou texto de nenhum
        concorrente. O motivo central são as ondas da própria marca,
        desenhadas em curva — não é certificado nem sugere ser.
        """

        if not self.content.released:
            return
        c = self.canvas
        c.saveState()
        self._draw_seal_rings(
            center_x=center_x, center_y=center_y, radius=radius, color=TEAL
        )

        # Ondas da marca, como no "≈" do logotipo. Duas curvas de Bézier
        # espelhadas, sem depender de fonte simbólica (o WinAnsi não
        # representa o caractere).
        c.setStrokeColor(NAVY)
        c.setLineWidth(1.5)
        c.setLineCap(1)
        span = radius * 0.52
        for index, offset in enumerate((3.0, -2.6)):
            path = c.beginPath()
            path.moveTo(center_x - span, center_y + offset)
            path.curveTo(
                center_x - span * 0.45, center_y + offset + 4.4,
                center_x - span * 0.1, center_y + offset - 4.4,
                center_x + span * 0.35, center_y + offset,
            )
            path.curveTo(
                center_x + span * 0.6, center_y + offset + 2.6,
                center_x + span * 0.8, center_y + offset + 2.6,
                center_x + span, center_y + offset + 1.2,
            )
            c.setStrokeColor(NAVY if index == 0 else TEAL)
            c.drawPath(path, stroke=1, fill=0)

        inner = radius - 4.2
        self._draw_seal_text(
            "SOPROLIFE",
            center_x=center_x, center_y=center_y, dy=15.0,
            inner=inner, size=6.4, color=NAVY,
        )
        self._draw_seal_text(
            "DIAGNÓSTICOS",
            center_x=center_x, center_y=center_y, dy=-12.4,
            inner=inner, size=4.8, color=TEAL,
        )
        self._draw_seal_text(
            "EM SAÚDE",
            center_x=center_x, center_y=center_y, dy=-19.0,
            inner=inner, size=4.8, color=TEAL,
        )
        c.restoreState()

    def _draw_signature_image(
        self,
        image: SignatureImage,
        *,
        center_x: float,
        baseline_y: float,
        max_width: float,
        max_height: float,
    ) -> None:
        try:
            reader = ImageReader(io.BytesIO(image.data))
            src_w, src_h = reader.getSize()
        except Exception as exc:
            raise NativeReportPdfError(
                "assinatura_ilegivel",
                "O ativo de assinatura não pôde ser desenhado no laudo.",
            ) from exc
        if src_w <= 0 or src_h <= 0:
            raise NativeReportPdfError(
                "assinatura_ilegivel",
                "O ativo de assinatura não pôde ser desenhado no laudo.",
            )
        scale = min(max_width / src_w, max_height / src_h)
        width = src_w * scale
        height = src_h * scale
        self.canvas.drawImage(
            reader,
            center_x - width / 2,
            baseline_y,
            width=width,
            height=height,
            mask="auto",
            preserveAspectRatio=True,
            anchor="sw",
        )

    # ------------------------------------------------ QR + nota MIR

    def _draw_qr(self, value: str, *, x: float, y: float, size: float) -> None:
        try:
            widget = qr.QrCodeWidget(pdf_safe(value))
            bounds = widget.getBounds()
            span_x = bounds[2] - bounds[0]
            span_y = bounds[3] - bounds[1]
            if span_x <= 0 or span_y <= 0:
                return
            drawing = Drawing(
                size, size, transform=[size / span_x, 0, 0, size / span_y, 0, 0]
            )
            drawing.add(widget)
            renderPDF.draw(drawing, self.canvas, x, y)
        except Exception:
            # QR é redundante: o código textual de verificação já consta do
            # bloco. Falha de renderização nunca impede a emissão do laudo.
            return

    def draw_mir_notice(self) -> None:
        """Nota discreta, sem caixa própria (M25.4).

        A informação continua obrigatória — o laudo precisa dizer que o
        traçado está em documento separado —, mas ela é uma nota de rodapé,
        não um bloco de mesmo peso visual que a conclusão médica. A caixa
        anterior competia com o conteúdo clínico.
        """

        lines = wrap_text(
            MIR_SEPARATE_NOTICE,
            font=FONT_ITALIC,
            size=7.2,
            max_width=CONTENT_WIDTH - 12,
        )
        height = len(lines) * 9.4 + 6
        self.ensure(height + 4)
        c = self.canvas
        top = self.y
        text_y = top - 8
        c.setFont(FONT_ITALIC, 7.2)
        c.setFillColor(MUTED)
        for line in lines:
            c.drawString(MARGIN_X + 6, text_y, line)
            text_y -= 9.4
        self.y = top - height - 4


# ------------------------------------------------------------------ API


def _post_bd_label(has_post_bd: bool | None) -> str:
    # Rótulo curto: o campo se chama "Pós-BD", então repetir
    # "exame com fase pós-broncodilatador" no valor era eco do próprio rótulo.
    if has_post_bd is True:
        return "realizado"
    if has_post_bd is False:
        return "não realizado"
    return "não informado"


def build_native_report_pdf(content: NativeReportContent) -> bytes:
    """Gera os bytes do laudo próprio da SoproLife.

    Nunca embute o PDF técnico da MIR e nunca desenha imagem do exame.
    """

    if not content.document_code or content.version_number < 1:
        raise NativeReportPdfError(
            "identificacao_laudo_invalida",
            "O laudo precisa de código e versão válidos.",
        )
    if content.released and not content.validation_code:
        raise NativeReportPdfError(
            "codigo_validacao_ausente",
            "Um laudo liberado exige código de verificação.",
        )
    if content.released and content.released_at_local is None:
        raise NativeReportPdfError(
            "data_liberacao_ausente",
            "Um laudo liberado exige data e hora de liberação.",
        )
    if not content.conclusion_text.strip():
        raise NativeReportPdfError(
            "conclusao_ausente", "O laudo exige conclusão médica."
        )

    composer = _Composer(content)
    composer.draw_title()

    if content.pilot_warning:
        composer.draw_banner(content.pilot_warning)
    elif not content.released:
        composer.draw_banner(
            f"{PREVIEW_WATERMARK} — conferência da médica antes da assinatura."
        )

    reference = (
        content.released_at_local or content.issued_at_local
    ).date()
    patient = content.patient
    age = format_age(patient.birth_date, reference)
    birth = format_date(patient.birth_date)
    if age:
        birth = f"{birth} ({age})"
    patient_fields: list[tuple[str, str]] = [
        ("Nome", patient.full_name),
        ("Nascimento", birth),
        ("Sexo", format_sex(patient.sex)),
    ]
    # CPF é exigido pela CFM 2.381/2024 "quando houver": presente, entra logo
    # abaixo da identificação; ausente, a linha não existe.
    if patient.cpf:
        patient_fields.append(("CPF", patient.cpf))
    if patient.public_code:
        patient_fields.append(("Registro", patient.public_code))

    exam = content.exam
    exam_date = format_date(exam.exam_date, exam.date_precision)
    if exam.exam_time:
        exam_date = f"{exam_date} às {exam.exam_time}"
    # O LOCAL de realização não entra aqui: ele encabeça o documento, na
    # célula central do cabeçalho, como num laudo impresso em papel timbrado
    # da unidade. Repeti-lo no corpo era a mesma informação duas vezes.
    exam_fields: list[tuple[str, str]] = [
        ("Exame", exam.public_code),
        ("Data", exam_date),
        ("Pós-BD", _post_bd_label(exam.has_post_bd)),
        ("Indicação", exam.clinical_indication or "não informada"),
    ]
    composer.draw_identification_table(patient_fields, exam_fields)

    # Hierarquia tipográfica (M25.6). Antes tudo vivia entre 8.2 e 10pt, e
    # sem contraste de corpo a conclusão — o que a médica assina — pesava o
    # mesmo que uma observação de rodapé. A conclusão agora é o maior texto
    # do documento, com folga sobre título de seção, observação e tabela.
    composer.draw_section_heading("Conclusões")
    composer.draw_paragraph(content.conclusion_text, size=11.6, leading=16.5)

    if content.observations:
        composer.draw_section_heading(
            "Observações complementares", space_before=12
        )
        composer.draw_paragraph(content.observations, size=9.2, leading=13.2)

    for addendum in content.addenda:
        composer.draw_section_heading(
            f"Adendo {addendum.sequence} — "
            f"{format_datetime(addendum.created_at)}",
            space_before=12,
        )
        composer.draw_paragraph(addendum.body_text, size=9.2, leading=13.2)

    composer.space(2)
    composer.draw_mir_notice()
    # A faixa de identificação e assinatura fecha o documento: é o último
    # bloco e nunca compartilha espaço com nenhum outro elemento.
    composer.draw_physician_signature_block()
    return composer.finish()
