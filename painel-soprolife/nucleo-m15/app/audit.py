"""Trilha de auditoria append-only, sem PII nos detalhes.

- Sanitização RECURSIVA por allowlist: só chaves aprovadas sobrevivem, em
  qualquer nível; valores são escalares curtos ou listas pequenas deles.
- Append-only imposto em duas camadas: trigger no PostgreSQL (migration) e
  guarda de sessão SQLAlchemy (vale também para SQLite de teste).
"""

from sqlalchemy import event
from sqlalchemy.orm import Session

from .models import AuditLog

# Chaves permitidas nos detalhes — tudo fora daqui é descartado.
ALLOWED_KEYS = {
    "public_code", "campos", "status", "tipo", "canal", "motivo", "followup",
    "candidatos_identidade", "nova_data", "resultado", "modo", "source_type",
    "total", "validas", "rejeitadas", "ambiguas", "sha256", "batch_id",
    "decisao", "contexto", "campo", "marcador", "codigo", "fila", "papel",
    "ativo",
}

_MAX_STR = 120
_MAX_LIST = 20


def _sanitize_value(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_STR]
    if isinstance(value, (list, tuple)):
        clean = [_sanitize_value(v) for v in value[:_MAX_LIST]
                 if isinstance(v, (str, int, float, bool)) or v is None]
        return clean
    if isinstance(value, dict):
        return sanitize_details(value)
    return None


def sanitize_details(details: dict | None) -> dict | None:
    if details is None:
        return None
    clean: dict = {}
    for key, value in details.items():
        if key not in ALLOWED_KEYS:
            continue
        clean[key] = _sanitize_value(value)
    return clean


def audit(
    db: Session,
    acao: str,
    entidade: str | None = None,
    entidade_id: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
    detalhes: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            acao=acao,
            entidade=entidade,
            entidade_id=entidade_id,
            user_id=user_id,
            request_id=request_id,
            detalhes=sanitize_details(detalhes),
        )
    )


class AuditAppendOnlyError(RuntimeError):
    pass


@event.listens_for(Session, "before_flush")
def _audit_append_only_guard(session, _flush_context, _instances):
    """Nenhuma sessão da aplicação pode alterar ou apagar audit_logs."""
    for obj in session.dirty:
        if isinstance(obj, AuditLog) and session.is_modified(obj):
            raise AuditAppendOnlyError("audit_logs é append-only (update bloqueado).")
    for obj in session.deleted:
        if isinstance(obj, AuditLog):
            raise AuditAppendOnlyError("audit_logs é append-only (delete bloqueado).")
