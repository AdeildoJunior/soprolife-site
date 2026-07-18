"""Paginação padrão para listagens."""

from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session


class PageParams:
    def __init__(
        self,
        pagina: int = Query(1, ge=1),
        tamanho: int = Query(25, ge=1, le=100),
    ):
        self.pagina = pagina
        self.tamanho = tamanho

    @property
    def offset(self) -> int:
        return (self.pagina - 1) * self.tamanho


def paginate(db: Session, stmt, params: PageParams, serializer) -> dict:
    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = db.execute(stmt.offset(params.offset).limit(params.tamanho)).scalars().all()
    return {
        "itens": [serializer(r) for r in rows],
        "pagina": params.pagina,
        "tamanho": params.tamanho,
        "total": total,
    }
