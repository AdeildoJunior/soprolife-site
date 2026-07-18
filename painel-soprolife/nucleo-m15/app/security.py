"""Autenticação interna (usuários + papéis) com tokens assinados HMAC.

- Senhas: PBKDF2-HMAC-SHA256 (sem dependência externa).
- Tokens: user_id.expiração.assinatura (base64url), validade limitada.
- Papéis: admin > gestor > operacional > leitura.
- Fail-closed: sem token válido, nada responde além de /health e /auth/token.
"""

import base64
import hashlib
import hmac
import secrets
import threading
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import Role, User

PBKDF2_ITERATIONS = 210_000

# Limite local de tentativas de login (backoff simples em memória).
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60

ROLE_ADMIN = "admin"
ROLE_GESTOR = "gestor"
ROLE_OPERACIONAL = "operacional"
ROLE_LEITURA = "leitura"
ALL_ROLES = [ROLE_ADMIN, ROLE_GESTOR, ROLE_OPERACIONAL, ROLE_LEITURA]

# hierarquia: papel -> papéis que ele engloba
ROLE_IMPLIES = {
    ROLE_ADMIN: set(ALL_ROLES),
    ROLE_GESTOR: {ROLE_GESTOR, ROLE_OPERACIONAL, ROLE_LEITURA},
    ROLE_OPERACIONAL: {ROLE_OPERACIONAL, ROLE_LEITURA},
    ROLE_LEITURA: {ROLE_LEITURA},
}


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iters, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iters)
        ).hex()
        return hmac.compare_digest(candidate, digest)
    except (ValueError, AttributeError):
        return False


# Hash "dummy" verificado quando o usuário não existe: iguala o custo
# temporal de usuário inexistente e senha errada (anti-enumeração).
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def verify_password_or_dummy(password: str, stored: str | None) -> bool:
    if stored is None:
        verify_password(password, _DUMMY_HASH)
        return False
    return verify_password(password, stored)


class LoginRateLimiter:
    """Backoff local por identificador (hash do e-mail) — sem PII em memória
    persistente e sem PII em logs."""

    def __init__(self, max_failures: int = LOGIN_MAX_FAILURES,
                 window_seconds: int = LOGIN_WINDOW_SECONDS):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(identifier: str) -> str:
        return hashlib.sha256(identifier.lower().encode()).hexdigest()[:16]

    def is_blocked(self, identifier: str) -> bool:
        key = self._key(identifier)
        now = time.time()
        with self._lock:
            attempts = [t for t in self._failures.get(key, []) if now - t < self.window]
            self._failures[key] = attempts
            return len(attempts) >= self.max_failures

    def register_failure(self, identifier: str) -> None:
        key = self._key(identifier)
        with self._lock:
            self._failures.setdefault(key, []).append(time.time())

    def reset(self, identifier: str) -> None:
        with self._lock:
            self._failures.pop(self._key(identifier), None)


login_rate_limiter = LoginRateLimiter()


def _sign(payload: str, secret: str) -> str:
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def issue_token(user_id: str, ttl_minutes: int | None = None) -> str:
    settings = get_settings()
    ttl = ttl_minutes or settings.token_ttl_minutes
    exp = int(time.time()) + ttl * 60
    payload = f"{user_id}.{exp}"
    return f"{payload}.{_sign(payload, settings.resolved_auth_secret())}"


def parse_token(token: str) -> str | None:
    """Retorna user_id se o token é válido e não expirou; caso contrário None."""
    settings = get_settings()
    parts = token.split(".")
    if len(parts) != 3:
        return None
    user_id, exp_str, sig = parts
    payload = f"{user_id}.{exp_str}"
    if not hmac.compare_digest(sig, _sign(payload, settings.resolved_auth_secret())):
        return None
    try:
        if int(exp_str) < time.time():
            return None
    except ValueError:
        return None
    return user_id


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token ausente.")
    user_id = parse_token(auth.removeprefix("Bearer ").strip())
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado.")
    user = db.get(User, user_id)
    if not user or not user.ativo:
        raise HTTPException(status_code=401, detail="Usuário inexistente ou inativo.")
    request.state.user_id = user.id
    return user


def user_effective_roles(user: User) -> set[str]:
    effective: set[str] = set()
    for role in user.roles:
        effective |= ROLE_IMPLIES.get(role.name, {role.name})
    return effective


def require_role(role_name: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if role_name not in user_effective_roles(user):
            raise HTTPException(status_code=403, detail="Permissão insuficiente.")
        return user

    return dependency


def ensure_roles_exist(db: Session) -> None:
    existing = {r.name for r in db.execute(select(Role)).scalars()}
    for name in ALL_ROLES:
        if name not in existing:
            db.add(Role(name=name))
    db.flush()


def get_role(db: Session, name: str) -> Role:
    role = db.execute(select(Role).where(Role.name == name)).scalar_one_or_none()
    if role is None:
        role = Role(name=name)
        db.add(role)
        db.flush()
    return role
