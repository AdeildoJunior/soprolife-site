"""Healthcheck — único endpoint sem autenticação além de /auth/token."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..db import get_db
from ..serializers import to_local

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    now = datetime.now(timezone.utc)
    return {
        "status": "ok" if db_ok else "degradado",
        "versao": __version__,
        "ambiente": get_settings().env,
        "banco": "ok" if db_ok else "erro",
        "agora_utc": now.isoformat(),
        "agora_local": to_local(now),
    }
