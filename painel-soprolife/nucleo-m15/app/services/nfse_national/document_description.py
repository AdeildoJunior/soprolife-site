"""M26.34 — a descrição de serviço de UM documento fiscal, single ou agrupado.

Mora aqui, e não em `services/nfse.py`, por uma razão de dependências: tanto
`dispatch` quanto `preflight` precisam dela, e ambos são importados POR
`nfse` — pedir a eles que importassem `nfse` de volta fecharia o ciclo. Este
módulo depende apenas dos modelos e das sentenças oficiais.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import FiscalDocument, FiscalDocumentItem, SpirometryExam
from .service_description import (grouped_spirometry_service_description,
                                  spirometry_service_description)


def document_service_description(db: Session, doc: FiscalDocument) -> str:
    """A sentença oficial que vai no `xDescServ` deste documento.

    Fail-closed em ambos os ramos, pela mesma razão de sempre: a variante
    (com/sem broncodilatador) vem só do atributo estruturado do exame, nunca
    de texto livre, e um grupo com exames de variantes diferentes não tem
    descrição verdadeira — as duas possíveis mentiriam sobre metade dele.
    """
    if getattr(doc, "kind", "single") == "grouped":
        exam_ids = list(db.scalars(select(FiscalDocumentItem.spirometry_exam_id).where(
            FiscalDocumentItem.document_id == doc.id
        ).order_by(FiscalDocumentItem.position)).all())
        exams = db.scalars(select(SpirometryExam).where(
            SpirometryExam.id.in_(exam_ids))).all() if exam_ids else []
        variantes = {exam.broncodilatador for exam in exams}
        unica = variantes.pop() if len(variantes) == 1 else None
        return grouped_spirometry_service_description(len(exam_ids), unica)
    exam = db.get(SpirometryExam, doc.spirometry_exam_id)
    return spirometry_service_description(exam.broncodilatador if exam else None)
