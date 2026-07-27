"""Autenticação interna (usuários + papéis) com tokens assinados HMAC.

- Senhas: PBKDF2-HMAC-SHA256 (sem dependência externa).
- Tokens bearer: user_id.expiração.fingerprint_senha.assinatura, validade
  limitada. O fingerprint (derivado do hash da senha, nunca da senha) amarra
  o token à credencial vigente: redefinir a senha revoga todos os tokens
  antigos. Continua sendo o caminho da CLI e das integrações.
- Sessão de navegador (M21): cookie assinado "id.segredo.assinatura",
  HttpOnly + Secure + SameSite=Strict + Path restrito. O servidor guarda
  apenas sha256(segredo) na tabela auth_sessions, então recarregar a página
  restaura a sessão sem que nenhum token viva em localStorage/sessionStorage.
  Logout revoga a linha; redefinir senha e desativar usuário continuam
  derrubando tudo na hora.
- CSRF: requisições que mudam estado autenticadas POR COOKIE exigem o
  cabeçalho X-CSRF-Token. O bearer é imune por construção (o navegador não
  o envia sozinho), então integrações e a CLI não mudam.
- Papéis administrativos: admin > gestor > operacional > leitura.
- Papel clínico ``medico``: isolado e nunca herdado pela hierarquia
  administrativa. Autoria clínica exige esse papel de forma explícita,
  além do perfil profissional e da atribuição validados no fluxo de laudos.
- Fail-closed: sem credencial válida, nada responde além de /health e
  /auth/token.
"""

import base64
import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AuthSession, Role, User

PBKDF2_ITERATIONS = 210_000

# Limite local de tentativas de login (backoff simples em memória).
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60

ROLE_ADMIN = "admin"
ROLE_GESTOR = "gestor"
ROLE_OPERACIONAL = "operacional"
ROLE_LEITURA = "leitura"
ROLE_MEDICO = "medico"
ADMINISTRATIVE_ROLES = [
    ROLE_ADMIN,
    ROLE_GESTOR,
    ROLE_OPERACIONAL,
    ROLE_LEITURA,
]
ALL_ROLES = [*ADMINISTRATIVE_ROLES, ROLE_MEDICO]

# hierarquia: papel -> papéis que ele engloba
ROLE_IMPLIES = {
    # ``admin`` deliberadamente NÃO implica ``medico``. Uma conta
    # administrativa só ganha autoria clínica se também possuir a linha
    # explícita de papel médico e passar pelas demais guardas clínicas.
    ROLE_ADMIN: set(ADMINISTRATIVE_ROLES),
    ROLE_GESTOR: {ROLE_GESTOR, ROLE_OPERACIONAL, ROLE_LEITURA},
    ROLE_OPERACIONAL: {ROLE_OPERACIONAL, ROLE_LEITURA},
    ROLE_LEITURA: {ROLE_LEITURA},
    ROLE_MEDICO: {ROLE_MEDICO},
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


def password_fingerprint(password_hash: str) -> str:
    """Fingerprint curto do HASH da senha (nunca da senha em claro).

    Entra no token para amarrá-lo à credencial vigente: trocar a senha muda
    o fingerprint e invalida imediatamente todos os tokens anteriores.
    """
    return hashlib.sha256(password_hash.encode()).hexdigest()[:12]


def issue_token(user_id: str, password_hash: str, ttl_minutes: int | None = None) -> str:
    settings = get_settings()
    ttl = ttl_minutes or settings.token_ttl_minutes
    exp = int(time.time()) + ttl * 60
    payload = f"{user_id}.{exp}.{password_fingerprint(password_hash)}"
    return f"{payload}.{_sign(payload, settings.resolved_auth_secret())}"


def parse_token(token: str) -> tuple[str, str] | None:
    """Retorna (user_id, fingerprint) se o token é válido e não expirou."""
    settings = get_settings()
    parts = token.split(".")
    if len(parts) != 4:
        return None
    user_id, exp_str, fingerprint, sig = parts
    payload = f"{user_id}.{exp_str}.{fingerprint}"
    if not hmac.compare_digest(sig, _sign(payload, settings.resolved_auth_secret())):
        return None
    try:
        if int(exp_str) < time.time():
            return None
    except ValueError:
        return None
    return user_id, fingerprint


# --------------------------------------------------- sessão de navegador (M21)

# Métodos que mudam estado e por isso exigem CSRF quando a credencial é o
# cookie (ambiente ambiente: o navegador envia cookie sozinho, o bearer não).
CSRF_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
CSRF_HEADER = "X-CSRF-Token"

# Motivos de revogação registrados na linha (texto fixo, nunca PII).
REVOKE_LOGOUT = "logout"
REVOKE_SENHA = "senha_redefinida"
REVOKE_DESATIVADO = "usuario_desativado"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite devolve datetime naïve; comparar sem tzinfo explode em produção."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def session_duration(persistente: bool) -> timedelta:
    """Duração ajustável por configuração; o teto de 7 dias é validado lá."""
    settings = get_settings()
    if persistente:
        return timedelta(days=settings.session_persistent_days)
    return timedelta(minutes=settings.session_ttl_minutes)


def create_session(
    db: Session, user: User, *, persistente: bool = False
) -> tuple[str, str, AuthSession]:
    """Cria a sessão e devolve (valor_do_cookie, csrf_token, linha).

    O segredo e o csrf existem em claro apenas nesta resposta; o banco fica
    só com os hashes.
    """
    settings = get_settings()
    segredo = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    agora = _utcnow()
    sessao = AuthSession(
        user_id=user.id,
        token_hash=_digest(segredo),
        csrf_hash=_digest(csrf),
        password_fingerprint=password_fingerprint(user.password_hash),
        persistente=persistente,
        created_at=agora,
        last_seen_at=agora,
        expires_at=agora + session_duration(persistente),
    )
    db.add(sessao)
    db.flush()
    payload = f"{sessao.id}.{segredo}"
    cookie_value = f"{payload}.{_sign(payload, settings.resolved_auth_secret())}"
    return cookie_value, csrf, sessao


def parse_session_cookie(raw: str) -> tuple[str, str] | None:
    """Valida a assinatura e devolve (id, segredo) — sem tocar no banco."""
    settings = get_settings()
    parts = (raw or "").split(".")
    if len(parts) != 3:
        return None
    sessao_id, segredo, sig = parts
    if not sessao_id or not segredo:
        return None
    payload = f"{sessao_id}.{segredo}"
    if not hmac.compare_digest(sig, _sign(payload, settings.resolved_auth_secret())):
        return None
    return sessao_id, segredo


def load_session(db: Session, raw_cookie: str) -> tuple[AuthSession, User] | None:
    """Resolve o cookie para (sessão, usuário) ou None — sempre fail-closed."""
    parsed = parse_session_cookie(raw_cookie)
    if not parsed:
        return None
    sessao_id, segredo = parsed
    sessao = db.get(AuthSession, sessao_id)
    if sessao is None:
        return None
    if not hmac.compare_digest(sessao.token_hash, _digest(segredo)):
        return None
    if sessao.revoked_at is not None:
        return None
    expira = _as_utc(sessao.expires_at)
    if expira is None or expira <= _utcnow():
        return None
    user = db.get(User, sessao.user_id)
    if user is None or not user.ativo:
        return None
    # senha redefinida -> fingerprint muda -> sessões antigas caem na hora
    if not hmac.compare_digest(
        sessao.password_fingerprint, password_fingerprint(user.password_hash)
    ):
        return None
    return sessao, user


def revoke_session(db: Session, sessao: AuthSession, motivo: str) -> None:
    if sessao.revoked_at is None:
        sessao.revoked_at = _utcnow()
        sessao.revoked_motivo = motivo


def revoke_user_sessions(db: Session, user_id: str, motivo: str) -> int:
    """Derruba todas as sessões vivas de um usuário. Devolve quantas caíram."""
    vivas = db.execute(
        select(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
        )
    ).scalars().all()
    for sessao in vivas:
        revoke_session(db, sessao, motivo)
    return len(vivas)


def issue_csrf(db: Session, sessao: AuthSession) -> str:
    """Novo csrf para a sessão existente (usado ao restaurar a sessão)."""
    csrf = secrets.token_urlsafe(32)
    sessao.csrf_hash = _digest(csrf)
    return csrf


def set_session_cookie(response: Response, cookie_value: str, persistente: bool) -> None:
    """HttpOnly + Secure + SameSite=Strict + Path restrito, sempre.

    Sem "manter conectado" o cookie NÃO recebe max_age: morre com a sessão do
    navegador. Com "manter conectado" recebe o teto configurado (<=7 dias).
    """
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie_value,
        max_age=int(session_duration(True).total_seconds()) if persistente else None,
        path=settings.session_cookie_path,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.session_cookie_name,
        path=settings.session_cookie_path,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _check_csrf(request: Request, sessao: AuthSession) -> None:
    if request.method.upper() not in CSRF_METHODS:
        return
    enviado = request.headers.get(CSRF_HEADER, "")
    if not enviado or not hmac.compare_digest(sessao.csrf_hash, _digest(enviado)):
        raise HTTPException(
            status_code=403,
            detail="Verificação CSRF falhou. Recarregue a página e tente de novo.",
        )


# --------------------------------------------------------------- dependências


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Aceita bearer (CLI/integrações) OU cookie de sessão (navegador).

    O bearer tem precedência quando presente, para não mudar o comportamento
    de nada que já existia. O caminho por cookie exige CSRF nos métodos que
    mudam estado.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        parsed = parse_token(auth.removeprefix("Bearer ").strip())
        if not parsed:
            raise HTTPException(status_code=401, detail="Token inválido ou expirado.")
        user_id, fingerprint = parsed
        user = db.get(User, user_id)
        if not user or not user.ativo:
            raise HTTPException(status_code=401, detail="Usuário inexistente ou inativo.")
        # senha redefinida -> fingerprint muda -> tokens antigos caem na hora
        if not hmac.compare_digest(fingerprint, password_fingerprint(user.password_hash)):
            raise HTTPException(status_code=401, detail="Token inválido ou expirado.")
        request.state.user_id = user.id
        request.state.auth_kind = "bearer"
        return user

    settings = get_settings()
    raw_cookie = request.cookies.get(settings.session_cookie_name)
    if not raw_cookie:
        raise HTTPException(status_code=401, detail="Token ausente.")
    carregada = load_session(db, raw_cookie)
    if carregada is None:
        raise HTTPException(
            status_code=401, detail="Sessão expirada ou encerrada. Entre novamente."
        )
    sessao, user = carregada
    _check_csrf(request, sessao)
    # Não escreve `last_seen_at` em toda leitura. O CRM abre vários GETs em
    # paralelo; escrever a mesma sessão em cada dependency provoca contenção
    # desnecessária (e trava o SQLite). A restauração explícita /auth/sessao
    # atualiza esse diagnóstico uma vez por carregamento da página.
    request.state.user_id = user.id
    request.state.auth_kind = "cookie"
    request.state.session_id = sessao.id
    return user


def user_effective_roles(user: User) -> set[str]:
    effective: set[str] = set()
    for role in user.roles:
        effective |= ROLE_IMPLIES.get(role.name, {role.name})
    return effective


def user_has_explicit_role(user: User, role_name: str) -> bool:
    """Verdadeiro somente para vínculo direto em ``user_roles``.

    Essa distinção é crítica no M24C: nem ``admin`` nem qualquer outro papel
    hierárquico pode virar autor médico por implicação.
    """

    return any(role.name == role_name for role in user.roles)


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
