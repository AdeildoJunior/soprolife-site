"""Engine e sessão SQLAlchemy 2. SQLite para dev/teste, PostgreSQL 16 alvo."""

import pathlib

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


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite:///") and ":memory:" not in url:
        path = pathlib.Path(url.removeprefix("sqlite:///"))
        if not path.is_absolute():
            path = pathlib.Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)


def build_engine(url: str | None = None):
    url = url or get_settings().database_url
    _ensure_sqlite_dir(url)
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
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

        @event.listens_for(engine, "begin")
        def _sqlite_begin(conn):  # pragma: no cover - trivial
            conn.exec_driver_sql("BEGIN")
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
