"""Fail-closed geometry for the effective visible area of a PDF page.

The effective box is the strict intersection of MediaBox and each explicitly
present CropBox/TrimBox.  Raw arrays are inspected directly: pypdf's rectangle
properties intentionally provide fallbacks and may normalize malformed input,
which is not acceptable for report composition.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pypdf.errors import PdfReadError
from pypdf.generic import (
    ArrayObject,
    FloatObject,
    IndirectObject,
    NullObject,
    NumberObject,
)


class PdfPageGeometryError(ValueError):
    """Malformed or unsafe page geometry with a stable domain code."""

    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class EffectivePageBox:
    """Effective visible rectangle in PDF user space plus viewing rotation."""

    left: float
    bottom: float
    right: float
    top: float
    rotation: int

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom

    @property
    def visible_width(self) -> float:
        return self.height if self.rotation in {90, 270} else self.width

    @property
    def visible_height(self) -> float:
        return self.width if self.rotation in {90, 270} else self.height

    @property
    def overlay_to_user_matrix(
        self,
    ) -> tuple[float, float, float, float, float, float]:
        """Map upright visible coordinates to the page's unrotated user space."""

        if self.rotation == 0:
            return (1.0, 0.0, 0.0, 1.0, self.left, self.bottom)
        if self.rotation == 90:
            return (0.0, -1.0, 1.0, 0.0, self.left, self.top)
        if self.rotation == 180:
            return (-1.0, 0.0, 0.0, -1.0, self.right, self.top)
        return (0.0, 1.0, -1.0, 0.0, self.right, self.bottom)

    def user_to_visible(self, x: float, y: float) -> tuple[float, float]:
        """Inverse of :attr:`overlay_to_user_matrix`."""

        if self.rotation == 0:
            return x - self.left, y - self.bottom
        if self.rotation == 90:
            return self.top - y, x - self.left
        if self.rotation == 180:
            return self.right - x, self.top - y
        return y - self.bottom, self.right - x


def _resolve(value):
    seen: set[tuple[int, int]] = set()
    while isinstance(value, IndirectObject):
        key = (value.idnum, value.generation)
        if key in seen:
            raise PdfPageGeometryError(
                "pdf_caixa_pagina_malformada",
                "PDF contém referência cíclica em uma caixa de página.",
            )
        seen.add(key)
        try:
            value = value.get_object()
        except (PdfReadError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise PdfPageGeometryError(
                "pdf_caixa_pagina_malformada",
                "PDF contém caixa de página malformada.",
            ) from exc
    return value


def _coordinate(value) -> float:
    value = _resolve(value)
    if not isinstance(value, (NumberObject, FloatObject, int, float)) or isinstance(
        value, bool
    ):
        raise PdfPageGeometryError(
            "pdf_caixa_pagina_malformada",
            "PDF contém coordenada inválida em uma caixa de página.",
        )
    coordinate = float(value)
    if not math.isfinite(coordinate):
        raise PdfPageGeometryError(
            "pdf_caixa_pagina_malformada",
            "PDF contém coordenada não finita em uma caixa de página.",
        )
    return coordinate


def _rectangle(value, *, box_name: str) -> tuple[float, float, float, float]:
    value = _resolve(value)
    if not isinstance(value, (ArrayObject, list, tuple)) or len(value) != 4:
        raise PdfPageGeometryError(
            "pdf_caixa_pagina_malformada",
            f"PDF contém {box_name} malformada.",
        )
    left, bottom, right, top = (_coordinate(item) for item in value)
    if left >= right or bottom >= top:
        raise PdfPageGeometryError(
            "pdf_caixa_pagina_malformada",
            f"PDF contém {box_name} vazia ou invertida.",
        )
    return left, bottom, right, top


def _raw_optional_box(page: object, key: str):
    try:
        if key not in page:  # type: ignore[operator]
            return None
        value = page.get(key)  # type: ignore[attr-defined]
    except (PdfReadError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise PdfPageGeometryError(
            "pdf_caixa_pagina_malformada",
            "PDF contém caixa de página ilegível.",
        ) from exc
    value = _resolve(value)
    if value is None or isinstance(value, NullObject):
        raise PdfPageGeometryError(
            "pdf_caixa_pagina_malformada",
            "PDF contém caixa de página nula.",
        )
    return value


def _page_rotation(page: object) -> int:
    try:
        raw = page.get("/Rotate", 0)  # type: ignore[attr-defined]
    except (PdfReadError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise PdfPageGeometryError(
            "pdf_rotacao_pagina_invalida",
            "PDF contém rotação de página inválida.",
        ) from exc
    raw = _resolve(raw)
    if not isinstance(raw, (NumberObject, int)) or isinstance(raw, bool):
        raise PdfPageGeometryError(
            "pdf_rotacao_pagina_invalida",
            "PDF contém rotação de página inválida.",
        )
    rotation = int(raw)
    if rotation not in {0, 90, 180, 270}:
        raise PdfPageGeometryError(
            "pdf_rotacao_pagina_invalida",
            "A rotação da página deve ser 0, 90, 180 ou 270 graus.",
        )
    return rotation


def effective_page_box(page: object) -> EffectivePageBox:
    """Return the strict visible intersection without mutating the page."""

    media_value = _raw_optional_box(page, "/MediaBox")
    if media_value is None:
        raise PdfPageGeometryError(
            "pdf_caixa_pagina_malformada",
            "PDF não possui MediaBox válida.",
        )
    left, bottom, right, top = _rectangle(media_value, box_name="MediaBox")

    for key, label in (("/CropBox", "CropBox"), ("/TrimBox", "TrimBox")):
        raw = _raw_optional_box(page, key)
        if raw is None:
            continue
        box_left, box_bottom, box_right, box_top = _rectangle(raw, box_name=label)
        left = max(left, box_left)
        bottom = max(bottom, box_bottom)
        right = min(right, box_right)
        top = min(top, box_top)
        if left >= right or bottom >= top:
            raise PdfPageGeometryError(
                "pdf_caixas_pagina_sem_intersecao",
                "As caixas visíveis da página não possuem interseção válida.",
            )

    return EffectivePageBox(
        left=left,
        bottom=bottom,
        right=right,
        top=top,
        rotation=_page_rotation(page),
    )
