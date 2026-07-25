"""Ações manuais seguras de Marketing & SEO (M21).

O navegador nunca executa o conector nem acessa a credencial Google. Um
operador autenticado apenas grava um pedido mínimo numa fila local; o timer
systemd existente consome essa fila e executa a atualização sob a identidade
de serviço dedicada.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from ..config import get_settings
from ..models import User
from ..security import ROLE_LEITURA, ROLE_OPERACIONAL, require_role

router = APIRouter(prefix="/marketing", tags=["marketing"])

_MIN_INTERVAL = timedelta(seconds=60)


def _queue_path() -> Path:
    return get_settings().marketing_refresh_queue


def _read_request() -> dict | None:
    try:
        value = json.loads(_queue_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _parse_requested_at(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _write_request(now: datetime) -> dict:
    """Grava timestamp/origem de forma atômica e com permissão 0600."""
    target = _queue_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "requestedAt": now.isoformat(timespec="seconds"),
        "origin": "painel-autenticado",
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        # Só sobra se write/chmod/replace falhar. Nunca remove o pedido válido.
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
    return payload


@router.post("/refresh")
def request_refresh(
    _user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Enfileira uma atualização; sessão por cookie exige CSRF via dependency."""
    now = datetime.now(timezone.utc)
    previous = _read_request()
    requested_at = _parse_requested_at((previous or {}).get("requestedAt"))
    if requested_at is not None and now - requested_at < _MIN_INTERVAL:
        return {
            "ok": True,
            "queued": False,
            "reason": "Já existe um pedido recente na fila.",
            "requestedAt": previous.get("requestedAt"),
        }
    payload = _write_request(now)
    return {"ok": True, "queued": True, "requestedAt": payload["requestedAt"]}


@router.get("/refresh-status")
def refresh_status(
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Informa somente se o pedido ainda aguarda consumo pelo timer."""
    request = _read_request()
    return {
        "ok": True,
        "pending": request is not None,
        "requestedAt": (request or {}).get("requestedAt"),
    }
