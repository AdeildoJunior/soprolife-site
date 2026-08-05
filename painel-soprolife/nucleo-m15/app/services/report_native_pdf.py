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

HEADER_HEIGHT = 50.0
PAGE_FOOTER_RESERVE = 26.0

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
RELEASE_STATEMENT = (
    "Liberado eletronicamente pela médica acima, com autenticação "
    "individual e ação consciente registradas. Integridade verificável "
    "pelo código e pelo hash SHA-256 deste laudo. Esta liberação não "
    "constitui, por si só, assinatura digital qualificada ICP-Brasil."
)

PREVIEW_WATERMARK = "PRÉVIA — DOCUMENTO NÃO LIBERADO"

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
        c = self.canvas
        top = PAGE_HEIGHT - MARGIN_TOP
        logo_drawn = self._draw_logo(top)
        if not logo_drawn:
            c.setFont(FONT_BOLD, 16)
            c.setFillColor(NAVY)
            c.drawString(MARGIN_X, top - 16, "SoproLife")

        c.setFont(FONT, 7.5)
        c.setFillColor(MUTED)
        c.drawRightString(
            PAGE_WIDTH - MARGIN_X, top - 10, pdf_safe(INSTITUTION_NAME)
        )
        c.drawRightString(
            PAGE_WIDTH - MARGIN_X,
            top - 20,
            pdf_safe(
                f"Laudo {self.content.document_code} "
                f"• versão {self.content.version_number}"
            ),
        )

        # A base do logo fica em `top - LOGO_MAX_HEIGHT` (top - 34). A régua
        # precisa passar ABAIXO disso, senão ela corta a tagline
        # "DIAGNÓSTICOS E SOLUÇÕES EM SAÚDE" impressa no rodapé da marca.
        rule_y = top - HEADER_HEIGHT + 10
        c.setStrokeColor(NAVY)
        c.setLineWidth(1.4)
        c.line(MARGIN_X, rule_y, PAGE_WIDTH - MARGIN_X, rule_y)
        c.setStrokeColor(TEAL)
        c.setLineWidth(1.4)
        c.line(MARGIN_X, rule_y, MARGIN_X + 92, rule_y)

    def _draw_logo(self, top: float) -> bool:
        reader = _load_logo_reader(self.content.logo_path or DEFAULT_LOGO_PATH)
        if reader is None:
            return False
        try:
            src_w, src_h = reader.getSize()
        except Exception:
            return False
        if src_w <= 0 or src_h <= 0:
            return False
        scale = min(LOGO_MAX_WIDTH / src_w, LOGO_MAX_HEIGHT / src_h)
        width = src_w * scale
        height = src_h * scale
        self.canvas.drawImage(
            reader,
            MARGIN_X,
            top - height,
            width=width,
            height=height,
            mask="auto",
            preserveAspectRatio=True,
            anchor="sw",
        )
        return True

    # ------------------------------------------------------------ rodapé

    def _draw_page_footer(self) -> None:
        c = self.canvas
        y = MARGIN_BOTTOM + 14
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(MARGIN_X, y, PAGE_WIDTH - MARGIN_X, y)
        c.setFont(FONT, 6.8)
        c.setFillColor(MUTED)
        left = (
            f"{INSTITUTION_NAME} • Laudo {self.content.document_code} "
            f"• versão {self.content.version_number}"
        )
        c.drawString(MARGIN_X, y - 10, pdf_safe(left))
        code = self.content.validation_code
        right = f"Verificação {code}" if code else "PRÉVIA — sem código"
        c.drawRightString(PAGE_WIDTH - MARGIN_X, y - 10, pdf_safe(right))

    # ---------------------------------------------------------- primitivas

    def space(self, height: float) -> None:
        self.y -= height

    def draw_title(self) -> None:
        c = self.canvas
        self.ensure(46)
        c.setFont(FONT_BOLD, 19)
        c.setFillColor(NAVY)
        c.drawString(MARGIN_X, self.y - 19, pdf_safe(DOCUMENT_TITLE))
        self.y -= 22

        status = (
            "DOCUMENTO LIBERADO"
            if self.content.released
            else PREVIEW_WATERMARK
        )
        c.setFont(FONT_BOLD, 8)
        c.setFillColor(TEAL if self.content.released else WARN_INK)
        c.drawString(MARGIN_X, self.y - 8, pdf_safe(status))
        self.y -= 12

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

    def draw_section_heading(self, title: str) -> None:
        self.ensure(18)
        c = self.canvas
        c.setFont(FONT_BOLD, 9.5)
        c.setFillColor(NAVY)
        c.drawString(MARGIN_X, self.y - 10, pdf_safe(title.upper()))
        c.setStrokeColor(TEAL)
        c.setLineWidth(1.0)
        c.line(MARGIN_X, self.y - 15, MARGIN_X + 26, self.y - 15)
        self.y -= 16

    def draw_data_card(self, title: str, fields: list[tuple[str, str]]) -> None:
        """Cartão de dados com o título EMBUTIDO (M25.4).

        Antes cada bloco gastava um cabeçalho solto acima do cartão. Somando
        paciente, exame, conclusão e observações, eram quatro títulos
        flutuando entre quatro caixas — ruído puro. O título agora vive
        dentro do próprio cartão, e o documento perde uma camada visual
        sem perder nenhuma informação.
        """

        self.draw_field_grid(fields, title=title)

    def draw_field_grid(
        self, fields: list[tuple[str, str]], *, title: str | None = None
    ) -> None:
        """Grade de dois campos por linha dentro de um cartão claro."""

        label_size = 7.0
        value_size = 9.0
        column_width = (CONTENT_WIDTH - 32) / 2
        rows: list[list[tuple[str, list[str]]]] = []
        current: list[tuple[str, list[str]]] = []
        for label, value in fields:
            wrapped = wrap_text(
                value or "—",
                font=FONT,
                size=value_size,
                max_width=column_width,
            )
            current.append((label, wrapped))
            if len(current) == 2:
                rows.append(current)
                current = []
        if current:
            rows.append(current)

        row_heights = [
            max(len(cell[1]) for cell in row) * 11 + 10 for row in rows
        ]
        title_height = 15.0 if title else 0.0
        height = sum(row_heights) + 8 + title_height
        self.ensure(height + 6)

        c = self.canvas
        top = self.y
        c.setFillColor(CARD_BG)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.rect(
            MARGIN_X, top - height, CONTENT_WIDTH, height, stroke=1, fill=1
        )
        c.setFillColor(TEAL)
        c.rect(MARGIN_X, top - height, 2.6, height, stroke=0, fill=1)

        if title:
            c.setFont(FONT_BOLD, 8)
            c.setFillColor(NAVY)
            c.drawString(MARGIN_X + 16, top - 13, pdf_safe(title.upper()))

        row_top = top - 8 - title_height
        for row, row_height in zip(rows, row_heights):
            for index, (label, wrapped) in enumerate(row):
                x = MARGIN_X + 16 + index * column_width
                c.setFont(FONT_BOLD, label_size)
                c.setFillColor(MUTED)
                c.drawString(x, row_top - 8, pdf_safe(label.upper()))
                c.setFont(FONT, value_size)
                c.setFillColor(INK)
                text_y = row_top - 19
                for line in wrapped:
                    c.drawString(x, text_y, line)
                    text_y -= 11
            row_top -= row_height
        self.y = top - height - 7

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

    def _signature_block_height(self) -> float:
        physician = self.content.physician
        statement_lines = wrap_text(
            RELEASE_STATEMENT if self.content.released else
            "Prévia sem validade: a identificação e a assinatura médica só "
            "são aplicadas após a ação \"Assinar e liberar laudo\".",
            font=FONT_ITALIC,
            size=7.2,
            max_width=CONTENT_WIDTH - 24,
        )
        lines_count = 3 + (1 if physician.specialty else 0)
        return (
            8  # respiro superior
            + 42  # área reservada da assinatura manuscrita
            + 6
            + 1  # linha
            + 12 * lines_count
            + 6
            + len(statement_lines) * 8.6
            + 4
        )

    def draw_physician_signature_block(self) -> None:
        """Área EXCLUSIVA de identificação e assinatura — nada mais aqui."""

        height = self._signature_block_height()
        # Bloco atômico: nunca é dividido entre páginas.
        self.ensure(height)
        c = self.canvas
        top = self.y
        physician = self.content.physician

        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(MARGIN_X, top, PAGE_WIDTH - MARGIN_X, top)

        # Faixa reservada da assinatura manuscrita. Fica vazia quando não há
        # ativo autorizado — nada é desenhado por cima em nenhum caso.
        signature_area_top = top - 8
        signature_area_height = 42.0
        line_y = signature_area_top - signature_area_height - 6
        line_width = 240.0
        line_x = MARGIN_X + (CONTENT_WIDTH - line_width) / 2

        image = self.content.signature_image if self.content.released else None
        if image is not None:
            self._draw_signature_image(
                image,
                center_x=line_x + line_width / 2,
                baseline_y=line_y + 3,
                max_width=line_width - 20,
                max_height=signature_area_height,
            )

        c.setStrokeColor(NAVY)
        c.setLineWidth(0.8)
        c.line(line_x, line_y, line_x + line_width, line_y)

        # Selo institucional à esquerda, na faixa livre ao lado da assinatura.
        # Fica FORA da área reservada da assinatura manuscrita — nunca é
        # desenhado por cima dela.
        self.draw_verification_seal(
            center_x=MARGIN_X + 46,
            center_y=line_y + signature_area_height / 2 - 2,
        )

        text_y = line_y - 13
        c.setFont(FONT_BOLD, 10.5)
        c.setFillColor(NAVY)
        c.drawCentredString(
            MARGIN_X + CONTENT_WIDTH / 2,
            text_y,
            pdf_safe(physician.professional_name),
        )
        text_y -= 12
        if physician.specialty:
            c.setFont(FONT, 9)
            c.setFillColor(INK)
            c.drawCentredString(
                MARGIN_X + CONTENT_WIDTH / 2,
                text_y,
                pdf_safe(physician.specialty),
            )
            text_y -= 12
        c.setFont(FONT, 9)
        c.setFillColor(INK)
        registry = f"CRM-{physician.crm_state} {physician.crm_display}"
        if physician.rqe:
            registry = f"{registry}   •   RQE {physician.rqe}"
        c.drawCentredString(
            MARGIN_X + CONTENT_WIDTH / 2, text_y, pdf_safe(registry)
        )
        text_y -= 10

        statement = (
            RELEASE_STATEMENT
            if self.content.released
            else (
                "Prévia sem validade: a identificação e a assinatura médica "
                "só são aplicadas após a ação \"Assinar e liberar laudo\"."
            )
        )
        lines = wrap_text(
            statement, font=FONT_ITALIC, size=7.2, max_width=CONTENT_WIDTH - 24
        )
        c.setFont(FONT_ITALIC, 7.2)
        c.setFillColor(MUTED)
        text_y -= 6
        for line in lines:
            c.drawCentredString(MARGIN_X + CONTENT_WIDTH / 2, text_y, line)
            text_y -= 8.6

        self.y = top - height

    def draw_verification_seal(
        self, *, center_x: float, center_y: float, radius: float = 34.0
    ) -> None:
        """Selo circular de verificação — identidade PRÓPRIA da SoproLife.

        M25.4. Inspirado apenas na ORGANIZAÇÃO de um laudo profissional
        (um selo fecha o documento), sem copiar marca, arte ou texto de
        nenhum concorrente. É um elemento gráfico institucional: dois anéis,
        um "visto" e o texto do estado. NÃO é, e não sugere ser, certificado
        digital — o próprio selo diz "liberação institucional".

        Só é desenhado em documento LIBERADO: numa prévia, um selo de
        verificação seria mentira visual.
        """

        if not self.content.released:
            return
        c = self.canvas
        c.saveState()

        # Anel externo e interno.
        c.setStrokeColor(TEAL)
        c.setLineWidth(1.6)
        c.circle(center_x, center_y, radius, stroke=1, fill=0)
        c.setLineWidth(0.5)
        c.circle(center_x, center_y, radius - 4.2, stroke=1, fill=0)

        # "Visto" central, desenhado como duas retas (sem fonte simbólica,
        # que o WinAnsi não representa).
        c.setStrokeColor(NAVY)
        c.setLineWidth(2.2)
        c.setLineCap(1)
        tick = radius * 0.30
        c.line(center_x - tick, center_y + tick * 0.15,
               center_x - tick * 0.25, center_y - tick * 0.55)
        c.line(center_x - tick * 0.25, center_y - tick * 0.55,
               center_x + tick * 1.05, center_y + tick * 0.85)

        # Texto do selo. As posições verticais são conferidas contra a corda
        # do anel interno: num círculo, quanto mais longe do centro, menos
        # largura disponível — foi assim que "INSTITUCIONAL" vazava para fora
        # do anel na primeira versão.
        inner = radius - 4.2
        c.setFillColor(NAVY)
        c.setFont(FONT_BOLD, 5.8)
        c.drawCentredString(
            center_x, center_y + inner - 9.0, pdf_safe("SOPROLIFE")
        )
        c.setFillColor(TEAL)
        c.setFont(FONT_BOLD, 5.2)
        c.drawCentredString(
            center_x, center_y - inner + 13.0, pdf_safe("LIBERAÇÃO")
        )
        c.setFont(FONT_BOLD, 4.6)
        c.drawCentredString(
            center_x, center_y - inner + 7.4, pdf_safe("INSTITUCIONAL")
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

    # --------------------------------------------------- validação + MIR

    def draw_validation_block(self) -> None:
        content = self.content
        # M25.4 — "Documento" e "Versão" saíram daqui: os dois já aparecem no
        # cabeçalho (canto superior direito) E no rodapé de toda página.
        # Repeti-los uma terceira vez era só ruído.
        info_lines: list[str] = []
        if content.validation_code:
            info_lines.append(f"Código de verificação: {content.validation_code}")
        if content.released_at_local:
            info_lines.append(
                f"Liberado em: {format_datetime(content.released_at_local)} "
                f"({content.timezone_label})"
            )
        else:
            info_lines.append(
                f"Prévia gerada em: {format_datetime(content.issued_at_local)} "
                f"({content.timezone_label})"
            )
        if content.validation_url:
            info_lines.extend(
                wrap_text(
                    f"Validação: {content.validation_url}",
                    font=FONT,
                    size=7.5,
                    max_width=CONTENT_WIDTH - 130,
                )
            )

        qr_size = 66.0 if content.validation_url else 0.0
        text_height = len(info_lines) * 10 + 22
        height = max(text_height, qr_size + 22)
        self.ensure(height + 8)

        c = self.canvas
        top = self.y
        c.setFillColor(CARD_BG)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.rect(
            MARGIN_X, top - height, CONTENT_WIDTH, height, stroke=1, fill=1
        )

        c.setFont(FONT_BOLD, 7.5)
        c.setFillColor(NAVY)
        c.drawString(
            MARGIN_X + 12, top - 14, pdf_safe("IDENTIFICAÇÃO E VALIDAÇÃO")
        )
        c.setFont(FONT, 7.5)
        c.setFillColor(INK)
        text_y = top - 26
        for line in info_lines:
            c.drawString(MARGIN_X + 12, text_y, pdf_safe(line))
            text_y -= 10

        if content.validation_url and qr_size:
            self._draw_qr(
                content.validation_url,
                x=PAGE_WIDTH - MARGIN_X - qr_size - 12,
                y=top - height + 11,
                size=qr_size,
            )
        self.y = top - height - 7

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
    if has_post_bd is True:
        return "Sim — exame com fase pós-broncodilatador"
    if has_post_bd is False:
        return "Não — exame sem fase pós-broncodilatador"
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
    # Rótulos NÃO repetem o título do cartão ("Paciente"/"Exame"): com o
    # título embutido, "PACIENTE › PACIENTE" virava eco visual (M25.4).
    patient_fields: list[tuple[str, str]] = [
        ("Nome", patient.full_name),
        ("Data de nascimento", format_date(patient.birth_date)),
    ]
    age = format_age(patient.birth_date, reference)
    patient_fields.append(("Idade", age or "não informada"))
    patient_fields.append(("Sexo", format_sex(patient.sex)))
    if patient.public_code:
        patient_fields.append(("Registro", patient.public_code))
    composer.draw_data_card("Paciente", patient_fields)

    exam = content.exam
    exam_date = format_date(exam.exam_date, exam.date_precision)
    if exam.exam_time:
        exam_date = f"{exam_date} às {exam.exam_time}"
    location = content.location
    location_text = location.name
    if location.address_line:
        location_text = f"{location_text}\n{location.address_line}"
    if location.contact_line:
        location_text = f"{location_text}\n{location.contact_line}"
    exam_fields: list[tuple[str, str]] = [
        ("Código", exam.public_code),
        ("Data e hora", exam_date),
        ("Pós-broncodilatador", _post_bd_label(exam.has_post_bd)),
        ("Indicação clínica", exam.clinical_indication or "não informada"),
        ("Local de realização", location_text),
    ]
    composer.draw_data_card("Exame", exam_fields)

    composer.draw_section_heading("Conclusão")
    composer.draw_paragraph(
        content.conclusion_text,
        size=11,
        leading=15,
        font=FONT_BOLD,
        color=NAVY,
        emphasis_box=True,
    )

    if content.observations:
        composer.draw_section_heading("Observações complementares")
        composer.draw_paragraph(content.observations, size=10, leading=13.5)

    for addendum in content.addenda:
        composer.draw_section_heading(
            f"Adendo {addendum.sequence} — "
            f"{format_datetime(addendum.created_at)}"
        )
        composer.draw_paragraph(addendum.body_text, size=10, leading=13.5)

    composer.space(2)
    composer.draw_mir_notice()
    composer.draw_validation_block()
    # A área de identificação e assinatura fecha o documento: é o último
    # bloco e nunca compartilha espaço com nenhum outro elemento.
    composer.draw_physician_signature_block()
    return composer.finish()
