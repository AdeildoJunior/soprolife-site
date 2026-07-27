"""Database/storage publication contract for immutable report versions."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from sqlalchemy.orm import Session

from .report_storage import (
    PublishedReportFile,
    atomic_write_new_file,
    cleanup_published_file,
)

logger = logging.getLogger(__name__)


def _safe_technical_log(event: str, error: BaseException) -> None:
    """Never interpolate exception text: it may contain a storage path."""

    logger.error(
        event,
        extra={
            "event": event,
            "error_type": type(error).__name__,
        },
    )


@dataclass
class ReportPublicationTransaction:
    """Tracks exact new files until the corresponding DB commit is durable."""

    db: Session
    _publications: list[PublishedReportFile] = field(default_factory=list)
    _finished: bool = False

    def register(self, publication: PublishedReportFile) -> None:
        if self._finished:
            raise RuntimeError("Publicação de laudo já encerrada.")
        if publication in self._publications:
            raise RuntimeError("Publicação de laudo duplicada.")
        self._publications.append(publication)

    def publish(self, path: Path, data: bytes, *, root: Path) -> PublishedReportFile:
        """Atomically create and immediately track one exact new file."""

        publication = atomic_write_new_file(path, data, root=root)
        try:
            self.register(publication)
        except BaseException:
            try:
                cleanup_published_file(publication)
            except BaseException as error:
                _safe_technical_log("report_publication_cleanup_failed", error)
            raise
        return publication

    def rollback_and_cleanup(self) -> None:
        if self._finished:
            return
        try:
            self.db.rollback()
        except BaseException as error:
            _safe_technical_log("report_transaction_rollback_failed", error)

        for publication in reversed(self._publications):
            try:
                cleanup_published_file(publication)
            except BaseException as error:
                _safe_technical_log("report_publication_cleanup_failed", error)
        self._publications.clear()
        self._finished = True

    def commit(self) -> None:
        if self._finished:
            raise RuntimeError("Publicação de laudo já encerrada.")
        try:
            self.db.commit()
        except BaseException:
            self.rollback_and_cleanup()
            raise
        self._publications.clear()
        self._finished = True


@contextmanager
def report_publication_transaction(
    db: Session,
) -> Iterator[ReportPublicationTransaction]:
    """Rollback DB and exact new files on every pre-commit failure."""

    transaction = ReportPublicationTransaction(db)
    try:
        yield transaction
    except BaseException:
        transaction.rollback_and_cleanup()
        raise
    if not transaction._finished:
        transaction.rollback_and_cleanup()
        raise RuntimeError("Transação de publicação terminou sem commit explícito.")
