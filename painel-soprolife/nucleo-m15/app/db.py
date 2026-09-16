"""Engine e sessão SQLAlchemy 2. SQLite para dev/teste, PostgreSQL 16 alvo."""

import pathlib
import sqlite3

from sqlalchemy import MetaData, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

# Toda constraint nasce com nome explícito — exigência para FKs criadas
# via op.create_foreign_key e para downgrades determinísticos no PostgreSQL.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@event.listens_for(Session, "before_flush")
def _protect_m24c_immutable_evidence(session, _flush_context, _instances):
    """Defesa ORM para SQLite/dev e para qualquer escrita fora da API.

    PostgreSQL recebe a mesma proteção por triggers na migration M24C. A
    importação local usa ``Base.metadata.create_all`` e, portanto, precisa
    desta camada para não ter semântica mais fraca que produção.
    """

    from .models import (
        ReportAddendum,
        ReportAssignment,
        ReportAssignmentEvent,
        ReportDocumentVersion,
        ReportFooterTemplate,
        ReportTemplate,
    )

    immutable_types = (
        ReportAssignmentEvent,
        ReportDocumentVersion,
        ReportFooterTemplate,
        ReportTemplate,
        # M25.2 — adendo publicado é evidência clínica append-only: uma
        # correção posterior entra como NOVO adendo ou documento corretivo,
        # nunca reescrevendo o anterior.
        ReportAddendum,
    )
    for obj in session.deleted:
        if isinstance(obj, (*immutable_types, ReportAssignment)):
            raise ValueError("Evidência clínica append-only não pode ser removida.")
    for obj in session.dirty:
        if isinstance(obj, immutable_types) and inspect(obj).persistent:
            raise ValueError("Evidência clínica imutável não pode ser alterada.")
        if isinstance(obj, ReportAssignment) and inspect(obj).persistent:
            state = inspect(obj)
            changed = {
                attr.key
                for attr in state.attrs
                if attr.history.has_changes()
            }
            if not changed.issubset({"active", "ended_at"}):
                raise ValueError("Atribuição histórica não pode ser reescrita.")
            active_history = state.attrs.active.history
            if (
                "active" in changed
                and not (
                    active_history.deleted == [True]
                    and active_history.added == [False]
                )
            ):
                raise ValueError("Atribuição só pode transicionar de ativa para encerrada.")


@event.listens_for(Session, "before_flush")
def _protect_fiscal_evidence(session, _flush_context, _instances):
    from .models import FiscalPolicy, FiscalPreparation, FiscalAttempt, FiscalDocument
    immutable = (FiscalPolicy, FiscalPreparation, FiscalAttempt)
    for obj in session.deleted:
        if isinstance(obj, (*immutable, FiscalDocument)):
            raise ValueError("Evidência fiscal não pode ser removida.")
    for obj in session.dirty:
        if isinstance(obj, immutable) and session.is_modified(obj):
            raise ValueError("Evidência fiscal imutável.")
        if isinstance(obj, FiscalDocument):
            state = inspect(obj)
            if any(state.attrs[key].history.has_changes() for key in (
                "spirometry_exam_id", "environment", "created_by",
                "idempotency_key", "idempotency_fingerprint",
            )):
                raise ValueError("Identidade fiscal imutável.")


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite:///") and ":memory:" not in url:
        path = pathlib.Path(url.removeprefix("sqlite:///"))
        if not path.is_absolute():
            path = pathlib.Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)


# Meia hora. Menor que o `idle_session_timeout` que um PostgreSQL costuma
# receber e MUITO menor que a janela em que um firewall com estado (a VPS tem
# ufw) esquece um fluxo TCP parado. Reciclar por idade é barato; descobrir a
# conexão morta no meio da requisição de um paciente, não.
POOL_RECYCLE_SEGUNDOS = 1800

# M46/M47 — a transaction whose COMMIT needs to escalate to an EXCLUSIVE lock
# while another connection still holds even a SHARED one fails with "database
# is locked" once the wait exceeds this timeout.
#
# This value deliberately EQUALS Python's own ``sqlite3`` default
# (``connect(timeout=5.0)``, applied regardless of any PRAGMA). It is restated
# here so the bound is visible and pinned at the SQLAlchemy layer rather than
# inherited invisibly from the driver — NOT as a fix for anything.
#
# M47 — do NOT raise this to make a "database is locked" go away. That was
# tried (5000 -> 30000) for the real DPS #9 failure and proved to be the wrong
# lever: the holder there was the CALLER's own long-lived session, kept open
# across the HTTP POST it was simultaneously blocked waiting for. That is a
# circular wait, so the writer simply waited the full bound and failed
# identically — measured at 30.050s with a 30000ms timeout, vs 3.008s with
# 3000ms. Legitimate contention in this app is between request-scoped sessions
# that hold locks for milliseconds; a lock that outlasts seconds is structural,
# and the fix belongs in whoever holds it, not in a longer wait here. Keeping
# the bound short also keeps such a bug loud and fast instead of turning it
# into a half-minute hang.
SQLITE_BUSY_TIMEOUT_MS = 5000


def build_engine(url: str | None = None):
    url = url or get_settings().database_url
    _ensure_sqlite_dir(url)
    kwargs: dict = {
        "future": True,
        # M26.5 — o pool devolve conexões que podem ter morrido enquanto
        # estavam paradas: o PostgreSQL derrubou por timeout, o ufw esqueceu
        # o fluxo, a máquina hibernou. Sem `pool_pre_ping` isso vira um
        # OperationalError na PRIMEIRA requisição depois do silêncio — e a
        # primeira requisição depois do silêncio é exatamente o caso do
        # portal público, que passa a madrugada inteira ocioso até um
        # paciente abrir o link. Com o pre-ping, o SQLAlchemy emite um
        # "SELECT 1" barato antes de entregar a conexão e, se ela estiver
        # morta, descarta o pool inteiro daquela geração e reconecta — de
        # forma transparente para quem chamou.
        "pool_pre_ping": True,
    }
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_recycle"] = POOL_RECYCLE_SEGUNDOS
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        # Receita oficial SQLAlchemy p/ pysqlite: desliga o gerenciamento
        # implícito de transações do driver e emite BEGIN explícito — sem
        # isso, SAVEPOINT (begin_nested) escapa da transação externa e o
        # rollback do lote de importação não desfaz tudo.
        @event.listens_for(engine, "connect")
        def _sqlite_connect(dbapi_conn, _record):  # pragma: no cover - trivial
            dbapi_conn.isolation_level = None
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            # M46 — wait for a transient lock to clear instead of failing
            # instantly (see SQLITE_BUSY_TIMEOUT_MS above).
            dbapi_conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")

        @event.listens_for(engine, "begin")
        def _sqlite_begin(conn):  # pragma: no cover - trivial
            conn.exec_driver_sql("BEGIN")

        @event.listens_for(engine, "handle_error")
        def _sqlite_discard_connection_on_lock_conflict(context):
            # M46 — a genuine SQLITE_BUSY (contention that outlasted even
            # busy_timeout) leaves the pysqlite connection's own transaction
            # bookkeeping inconsistent: the next checkout of that SAME pooled
            # connection then fails with "cannot start a transaction within a
            # transaction" on its very next BEGIN — a healthy-looking session
            # poisoned by a PRIOR, unrelated request's failure (reproduced in
            # tests/test_db_sqlite_concurrency.py; this is exactly what turned
            # one local DPS #9 500 into a second, unrelated-looking one on the
            # very next GET during the M45 mission). Marking the error as a
            # disconnect tells SQLAlchemy's pool to discard this ONE
            # connection object outright rather than return it for reuse —
            # every other pooled connection is untouched, and the next
            # checkout simply opens a fresh one.
            original = context.original_exception
            if isinstance(original, sqlite3.OperationalError) and (
                "database is locked" in str(original)
                or "cannot start a transaction within a transaction" in str(original)
            ):
                context.is_disconnect = True
    return engine


_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def get_db():
    """Dependency FastAPI: uma sessão por requisição."""
    session: Session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
