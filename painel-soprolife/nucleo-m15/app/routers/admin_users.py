"""Administração de usuários internos — exclusiva do papel admin (M15.3A).

Menor privilégio, fail-closed:
- gestor/operacional/leitura NÃO acessam nada aqui (403);
- senha só trafega em corpo POST (nunca em URL, log, Git ou auditoria);
- redefinir senha muda o fingerprint e revoga na hora os tokens antigos;
- usuário inativo não autentica nem segue operando (barrado no get_current_user);
- nunca é possível se auto-inativar, rebaixar o próprio papel ou deixar o
  sistema sem nenhum admin ativo (anti-lockout);
- nenhuma conta é criada automaticamente: só POST deliberado de um admin
  (o bootstrap inicial continua sendo a CLI local criar-usuario);
- auditoria técnica sem PII: registra papéis/estado, nunca e-mail ou senha.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..models import Role, User, UserRole
from ..pagination import PageParams, paginate
from ..schemas import AdminPasswordReset, AdminUserCreate, AdminUserUpdate
from ..security import ROLE_ADMIN, get_role, hash_password, require_role
from ..serializers import ser_user

router = APIRouter(prefix="/admin/usuarios", tags=["administracao"])


def _user_or_404(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    return user


def _other_active_admins(db: Session, exclude_user_id: str) -> int:
    """Quantos OUTROS usuários ativos têm papel admin (proteção anti-lockout)."""
    return db.execute(
        select(func.count(func.distinct(User.id)))
        .select_from(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(User.ativo == True, Role.name == ROLE_ADMIN, User.id != exclude_user_id)  # noqa: E712
    ).scalar_one()


def _set_single_role(db: Session, user: User, papel: str) -> None:
    user.roles.clear()
    db.flush()
    user.roles.append(get_role(db, papel))
    db.flush()


@router.get("")
def list_users(
    ativo: bool | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(ROLE_ADMIN)),
):
    stmt = select(User).order_by(User.created_at.asc())
    if ativo is not None:
        stmt = stmt.where(User.ativo == ativo)
    return paginate(db, stmt, params, ser_user)


@router.post("", status_code=201)
def create_user(
    payload: AdminUserCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ROLE_ADMIN)),
):
    existing = db.execute(
        select(User).where(User.email == payload.email)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Já existe usuário com este e-mail.")
    user = User(
        email=payload.email,
        nome=payload.nome,
        password_hash=hash_password(payload.senha),
    )
    user.roles.append(get_role(db, payload.papel))
    db.add(user)
    db.flush()
    audit(db, "usuario.criado", "users", user.id, admin.id,
          request.state.request_id, {"papel": payload.papel})
    db.commit()
    return ser_user(user)


@router.get("/{user_id}")
def get_user(
    user_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(ROLE_ADMIN)),
):
    return ser_user(_user_or_404(db, user_id))


@router.patch("/{user_id}")
def update_user(
    user_id: str,
    payload: AdminUserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ROLE_ADMIN)),
):
    user = _user_or_404(db, user_id)
    changed: list[str] = []

    if payload.papel is not None:
        papeis_atuais = {r.name for r in user.roles}
        if payload.papel not in papeis_atuais:
            if user.id == admin.id:
                raise HTTPException(
                    status_code=409,
                    detail="Não é possível alterar o próprio papel — peça a outro admin.",
                )
            if ROLE_ADMIN in papeis_atuais and _other_active_admins(db, user.id) == 0:
                raise HTTPException(
                    status_code=409,
                    detail="Não é possível rebaixar o último admin ativo.",
                )
            _set_single_role(db, user, payload.papel)
            changed.append("papel")

    if payload.ativo is not None and payload.ativo != user.ativo:
        if not payload.ativo:
            if user.id == admin.id:
                raise HTTPException(
                    status_code=409,
                    detail="Não é possível desativar a própria conta.",
                )
            if (
                ROLE_ADMIN in {r.name for r in user.roles}
                and _other_active_admins(db, user.id) == 0
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Não é possível desativar o último admin ativo.",
                )
        user.ativo = payload.ativo
        changed.append("ativo")

    if changed:
        acao = "usuario.atualizado"
        if "ativo" in changed:
            acao = "usuario.reativado" if user.ativo else "usuario.desativado"
        audit(db, acao, "users", user.id, admin.id, request.state.request_id,
              {"campos": changed, "papel": payload.papel, "ativo": user.ativo})
        db.commit()
    return ser_user(user)


@router.post("/{user_id}/redefinir-senha")
def reset_password(
    user_id: str,
    payload: AdminPasswordReset,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ROLE_ADMIN)),
):
    """Nova senha por corpo POST. Tokens antigos caem imediatamente
    (fingerprint da credencial muda) — sem tabela de sessões."""
    user = _user_or_404(db, user_id)
    user.password_hash = hash_password(payload.senha)
    audit(db, "usuario.senha_redefinida", "users", user.id, admin.id,
          request.state.request_id)
    db.commit()
    return {"id": user.id, "tokens_revogados": True}
