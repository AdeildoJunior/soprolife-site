"""Emissão de token para usuários internos.

- Usuário inexistente verifica um hash dummy (custo temporal equivalente);
- backoff local por identificador após tentativas falhas;
- auditoria de tentativas sem PII (nunca registra e-mail ou senha).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..models import User
from ..schemas import TokenRequest
from ..security import issue_token, login_rate_limiter, verify_password_or_dummy

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token")
def token(payload: TokenRequest, request: Request, db: Session = Depends(get_db)):
    email = payload.email.lower()
    if login_rate_limiter.is_blocked(email):
        audit(db, "auth.bloqueado_rate_limit", request_id=request.state.request_id)
        db.commit()
        raise HTTPException(
            status_code=429,
            detail="Muitas tentativas de login. Aguarde alguns minutos.",
        )
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    stored_hash = user.password_hash if (user and user.ativo) else None
    # verificação SEMPRE roda (hash dummy p/ inexistente) — anti-enumeração
    if not verify_password_or_dummy(payload.password, stored_hash):
        login_rate_limiter.register_failure(email)
        audit(db, "auth.falha", request_id=request.state.request_id)
        db.commit()
        raise HTTPException(status_code=401, detail="Credenciais inválidas.")
    login_rate_limiter.reset(email)
    audit(db, "auth.token_emitido", entidade="users", entidade_id=user.id,
          user_id=user.id, request_id=request.state.request_id)
    db.commit()
    return {
        "token": issue_token(user.id),
        "usuario": {"id": user.id, "nome": user.nome, "papeis": [r.name for r in user.roles]},
    }
