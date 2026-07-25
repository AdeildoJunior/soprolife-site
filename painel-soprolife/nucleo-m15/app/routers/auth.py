"""Emissão de credencial para usuários internos.

- Usuário inexistente verifica um hash dummy (custo temporal equivalente);
- backoff local por identificador após tentativas falhas;
- auditoria de tentativas sem PII (nunca registra e-mail ou senha).

M21 — sessão persistente segura:
  POST /auth/token   entra e recebe o cookie de sessão (HttpOnly/Secure/
                     SameSite=Strict/Path restrito) + o csrf no corpo;
  GET  /auth/sessao  restaura a sessão ao recarregar a página e devolve um
                     csrf novo — é o que faz o F5 não derrubar ninguém;
  POST /auth/logout  revoga a sessão no servidor E limpa o cookie.
O token bearer continua sendo devolvido para a CLI e integrações; o
navegador não precisa dele e não o guarda em storage algum.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..models import User
from ..schemas import TokenRequest
from ..security import (
    REVOKE_LOGOUT,
    clear_session_cookie,
    create_session,
    get_current_user,
    issue_csrf,
    issue_token,
    load_session,
    login_rate_limiter,
    revoke_session,
    session_duration,
    set_session_cookie,
    user_effective_roles,
    verify_password_or_dummy,
)
from ..config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _identidade(user: User) -> dict:
    return {
        "id": user.id,
        "nome": user.nome,
        "papeis": sorted(r.name for r in user.roles),
        "papeis_efetivos": sorted(user_effective_roles(user)),
    }


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    """Identidade da credencial atual — permite à UI ocultar ações fora do papel.

    A autorização REAL continua no servidor (require_role); isto é só UX.
    """
    return _identidade(user)


@router.post("/token")
def token(
    payload: TokenRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
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

    persistente = bool(payload.manter_conectado)
    cookie_value, csrf, sessao = create_session(db, user, persistente=persistente)
    set_session_cookie(response, cookie_value, persistente)
    audit(db, "auth.token_emitido", entidade="users", entidade_id=user.id,
          user_id=user.id, request_id=request.state.request_id,
          detalhes={"sessao": "criada", "persistente": persistente})
    db.commit()
    return {
        # bearer preservado para CLI/integrações; o navegador ignora este campo
        "token": issue_token(user.id, user.password_hash),
        "csrf": csrf,
        "sessao": {
            "persistente": persistente,
            "expira_em": sessao.expires_at.isoformat(),
            "duracao_segundos": int(session_duration(persistente).total_seconds()),
        },
        "usuario": {"id": user.id, "nome": user.nome, "papeis": [r.name for r in user.roles]},
    }


@router.get("/sessao")
def sessao_atual(request: Request, db: Session = Depends(get_db)):
    """Restaura a sessão ao carregar a página. 401 quando não há sessão viva.

    Não aceita bearer de propósito: este endpoint existe para responder
    "quem está logado NESTE navegador", e a resposta traz um csrf novo.
    """
    settings = get_settings()
    raw_cookie = request.cookies.get(settings.session_cookie_name)
    if not raw_cookie:
        raise HTTPException(status_code=401, detail="Nenhuma sessão neste navegador.")
    carregada = load_session(db, raw_cookie)
    if carregada is None:
        raise HTTPException(
            status_code=401, detail="Sessão expirada ou encerrada. Entre novamente."
        )
    sessao, user = carregada
    csrf = issue_csrf(db, sessao)
    sessao.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "csrf": csrf,
        "sessao": {
            "persistente": sessao.persistente,
            "expira_em": sessao.expires_at.isoformat(),
        },
        "usuario": _identidade(user),
    }


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _authenticated_user: User = Depends(get_current_user),
):
    """Encerra a sessão deste navegador: revoga a linha e apaga o cookie.

    M21: por ser POST que muda estado, o caminho de cookie passa pela mesma
    validação CSRF de toda escrita. Bearer explícito continua compatível.
    """
    settings = get_settings()
    raw_cookie = request.cookies.get(settings.session_cookie_name)
    revogada = False
    if raw_cookie:
        carregada = load_session(db, raw_cookie)
        if carregada is not None:
            sessao, user = carregada
            revoke_session(db, sessao, REVOKE_LOGOUT)
            audit(db, "auth.logout", entidade="users", entidade_id=user.id,
                  user_id=user.id, request_id=request.state.request_id,
                  detalhes={"sessao": "revogada"})
            revogada = True
    db.commit()
    clear_session_cookie(response)
    return {"ok": True, "sessao_revogada": revogada}
