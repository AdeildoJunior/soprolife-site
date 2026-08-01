"""Ações manuais seguras de Marketing & SEO (M21).

O navegador nunca executa o conector nem acessa a credencial Google. Um
operador autenticado apenas grava um pedido mínimo numa fila local; o timer
systemd existente consome essa fila e executa a atualização sob a identidade
de serviço dedicada.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from ..config import get_settings
from ..models import User
from ..security import ROLE_LEITURA, ROLE_OPERACIONAL, require_role

router = APIRouter(prefix="/marketing", tags=["marketing"])

def _queue_path() -> Path:
    return get_settings().marketing_refresh_queue


def _read_request() -> dict | None:
    try:
        value = json.loads(_queue_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_request(now: datetime) -> dict:
    """Grava timestamp/origem de forma atômica e com permissão 0600."""
    target = _queue_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "requestId": str(uuid.uuid4()),
        "requestedAt": now.isoformat(timespec="seconds"),
        "origin": "painel-autenticado",
        "state": "pending",
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
    if previous is not None and previous.get("state", "pending") == "pending":
        return {
            "ok": True,
            "queued": False,
            "pending": True,
            "reason": "Já existe uma atualização em andamento.",
            "requestId": previous.get("requestId"),
            "requestedAt": previous.get("requestedAt"),
        }
    payload = _write_request(now)
    return {
        "ok": True,
        "queued": True,
        "pending": True,
        "requestId": payload["requestId"],
        "requestedAt": payload["requestedAt"],
    }


@router.get("/refresh-status")
def refresh_status(
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Informa andamento e resultado seguro da consulta solicitada."""
    request = _read_request()
    if request is None:
        return {"ok": True, "pending": False, "state": "idle"}
    state = request.get("state", "pending")
    return {
        "ok": True,
        "pending": state == "pending",
        "state": state,
        "requestId": request.get("requestId"),
        "requestedAt": request.get("requestedAt"),
        "completedAt": request.get("completedAt"),
        "success": request.get("success") if state == "completed" else None,
        "degraded": request.get("degraded") if state == "completed" else None,
        "snapshotGeneratedAt": request.get("snapshotGeneratedAt"),
        "errorMessageSafe": request.get("errorMessageSafe") if state == "completed" else None,
    }
