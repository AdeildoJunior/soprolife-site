"""Revisão multiaba em lote: arquivo privado, preview e escrita atômica.

Este módulo grava somente ``MigrationDecision`` e ``AuditLog`` append-only.
Ele nunca chama o executor operacional. O preview não retém lock; somente a
transação posterior à confirmação TTY trava o lote até o commit. Qualquer
falha nessa aplicação preserva rollback integral.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..models import ImportBatch, MigrationDecision
from .manifest import import_private_dir
from .multisheet import (
    EXECUTABLE_REVIEW_DECISIONS_BY_CATEGORY,
    MULTI_SHEET_REVIEW_DECISION,
    REVIEW_DECISIONS,
    MultiSheetError,
    _decisions_by_token,
    _get_multi_batch,
    multi_sheet_review_queue,
)

REVIEW_BATCH_SCHEMA_VERSION = "m15.review-batch.1"
REVIEW_BATCH_COVERAGE_MODE = "all_actionable"
REVIEW_BATCH_CONFIRMATION_TEMPLATE = (
    "GRAVAR REVISOES MULTIABA {batch_id} {fingerprint_short}"
)
MAX_REVIEW_BATCH_BYTES = 1024 * 1024
FINGERPRINT_SHORT_LENGTH = 12

_TOP_LEVEL_FIELDS = frozenset(
    (
        "schema_version",
        "coverage_mode",
        "batch_id",
        "snapshot_sha256",
        "mapping_version",
        "queue_fingerprint",
        "items",
    )
)
_ITEM_FIELDS = frozenset(
    (
        "token",
        "category",
        "decision",
        "expected_current_decision",
    )
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_TOKEN_RE = re.compile(r"^priv-[0-9a-f]{32}$")

_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


@dataclass(frozen=True)
class LoadedReviewBatch:
    document: dict
    fingerprint: str
    path: pathlib.Path
    device: int | None = None
    inode: int | None = None


def _json_object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MultiSheetError("arquivo_revisao_chave_repetida")
        result[key] = value
    return result


def _canonical_fingerprint(document: dict) -> str:
    encoded = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_uuid(value) -> str:
    if not isinstance(value, str):
        raise MultiSheetError("arquivo_revisao_batch_id_invalido")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise MultiSheetError("arquivo_revisao_batch_id_invalido") from exc
    if str(parsed) != value:
        raise MultiSheetError("arquivo_revisao_batch_id_invalido")
    return value


def _validate_document(document) -> dict:
    if not isinstance(document, dict) or set(document) != _TOP_LEVEL_FIELDS:
        raise MultiSheetError("arquivo_revisao_schema_fechado_invalido")
    if document["schema_version"] != REVIEW_BATCH_SCHEMA_VERSION:
        raise MultiSheetError("arquivo_revisao_schema_version_invalida")
    if document["coverage_mode"] != REVIEW_BATCH_COVERAGE_MODE:
        raise MultiSheetError("arquivo_revisao_coverage_mode_invalido")
    _validate_uuid(document["batch_id"])
    if not isinstance(document["snapshot_sha256"], str) or not _SHA256_RE.fullmatch(
        document["snapshot_sha256"]
    ):
        raise MultiSheetError("arquivo_revisao_snapshot_sha256_invalido")
    if (
        not isinstance(document["mapping_version"], str)
        or not document["mapping_version"]
        or len(document["mapping_version"]) > 40
    ):
        raise MultiSheetError("arquivo_revisao_mapping_version_invalida")
    if not isinstance(document["queue_fingerprint"], str) or not _SHA256_RE.fullmatch(
        document["queue_fingerprint"]
    ):
        raise MultiSheetError("arquivo_revisao_fingerprint_invalido")
    if not isinstance(document["items"], list) or not document["items"]:
        raise MultiSheetError("arquivo_revisao_itens_invalidos")

    seen: set[str] = set()
    for item in document["items"]:
        if not isinstance(item, dict) or set(item) != _ITEM_FIELDS:
            raise MultiSheetError("arquivo_revisao_item_schema_invalido")
        token = item["token"]
        if not isinstance(token, str) or not _PRIVATE_TOKEN_RE.fullmatch(token):
            raise MultiSheetError("arquivo_revisao_token_invalido")
        if token in seen:
            raise MultiSheetError("arquivo_revisao_token_repetido")
        seen.add(token)
        if not isinstance(item["category"], str) or not item["category"]:
            raise MultiSheetError("arquivo_revisao_categoria_invalida")
        desired = item["decision"]
        expected = item["expected_current_decision"]
        if desired is not None and desired not in REVIEW_DECISIONS:
            raise MultiSheetError("arquivo_revisao_decisao_invalida")
        if expected is not None and expected not in REVIEW_DECISIONS:
            raise MultiSheetError("arquivo_revisao_estado_esperado_invalido")
        if desired is None and expected is not None:
            # Decisões são append-only: o lote não pode transformar uma
            # decisão existente em ausência por omissão ou DELETE lógico.
            raise MultiSheetError("arquivo_revisao_remocao_nao_suportada")
    return document


def _resolve_review_file(filename: str) -> tuple[pathlib.Path, pathlib.Path]:
    if not isinstance(filename, str) or not filename or "\x00" in filename:
        raise MultiSheetError("arquivo_revisao_caminho_invalido")
    try:
        base = import_private_dir().resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise MultiSheetError("diretorio_privado_indisponivel") from exc
    supplied = pathlib.Path(filename)
    candidate = supplied if supplied.is_absolute() else base / supplied
    try:
        if candidate.parent.resolve(strict=True) != base:
            raise MultiSheetError("arquivo_revisao_fora_diretorio_privado")
    except (FileNotFoundError, OSError) as exc:
        raise MultiSheetError("arquivo_revisao_fora_diretorio_privado") from exc
    if not supplied.is_absolute() and supplied.name != filename:
        raise MultiSheetError("arquivo_revisao_fora_diretorio_privado")
    return base, candidate


def _read_secure_private_file(
    filename: str,
) -> tuple[pathlib.Path, bytes, int, int]:
    _base, candidate = _resolve_review_file(filename)
    try:
        before = candidate.lstat()
    except (FileNotFoundError, OSError) as exc:
        raise MultiSheetError("arquivo_revisao_indisponivel") from exc
    if stat.S_ISLNK(before.st_mode):
        raise MultiSheetError("arquivo_revisao_symlink_recusado")
    if not stat.S_ISREG(before.st_mode):
        raise MultiSheetError("arquivo_revisao_nao_regular")
    mode = stat.S_IMODE(before.st_mode)
    if mode & ~0o600 or not mode & stat.S_IRUSR:
        raise MultiSheetError("arquivo_revisao_permissoes_inseguras")
    if before.st_nlink != 1:
        raise MultiSheetError("arquivo_revisao_hardlink_recusado")
    if before.st_uid != os.geteuid():
        raise MultiSheetError("arquivo_revisao_propriedade_insegura")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(candidate, flags)
    except OSError as exc:
        raise MultiSheetError("arquivo_revisao_abertura_segura_falhou") from exc
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise MultiSheetError("arquivo_revisao_modificado_durante_leitura")
        if opened.st_size > MAX_REVIEW_BATCH_BYTES:
            raise MultiSheetError("arquivo_revisao_excede_limite")
        chunks = []
        remaining = MAX_REVIEW_BATCH_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > MAX_REVIEW_BATCH_BYTES:
            raise MultiSheetError("arquivo_revisao_excede_limite")
    finally:
        os.close(fd)
    return candidate, content, opened.st_dev, opened.st_ino


def load_private_review_batch(filename: str) -> LoadedReviewBatch:
    """Carrega JSON confinado sem seguir symlink e valida contrato fechado."""
    path, content, device, inode = _read_secure_private_file(filename)
    try:
        text = content.decode("utf-8")
        document = json.loads(text, object_pairs_hook=_json_object_without_duplicates)
    except UnicodeDecodeError as exc:
        raise MultiSheetError("arquivo_revisao_utf8_invalido") from exc
    except json.JSONDecodeError as exc:
        raise MultiSheetError("arquivo_revisao_json_invalido") from exc
    validated = _validate_document(document)
    return LoadedReviewBatch(
        document=validated,
        fingerprint=_canonical_fingerprint(validated),
        path=path,
        device=device,
        inode=inode,
    )


def confirmation_phrase(batch_id: str, fingerprint: str) -> str:
    return REVIEW_BATCH_CONFIRMATION_TEMPLATE.format(
        batch_id=batch_id,
        fingerprint_short=fingerprint[:FINGERPRINT_SHORT_LENGTH],
    )


@contextmanager
def serialize_review_batch(batch_id: str) -> Iterator[None]:
    """Serializa também no processo; PostgreSQL recebe FOR UPDATE abaixo."""
    with _LOCKS_GUARD:
        lock = _PROCESS_LOCKS.setdefault(batch_id, threading.RLock())
    with lock:
        yield


def _locked_batch(db: Session, batch_id: str) -> ImportBatch:
    return _get_multi_batch(db, batch_id, for_update=True)


def _live_actionable_state(db: Session, batch: ImportBatch) -> tuple[dict, dict]:
    report = batch.params["multi_sheet_report"]
    public_queue = multi_sheet_review_queue(db, batch.id)
    decisions = _decisions_by_token(db, batch.id)
    items = {
        item["private_reference_token"]: {
            **item,
            "current_decision": (
                decisions.get(item["private_reference_token"]) or {}
            ).get("decision"),
        }
        for item in report["review_queue"]
        if item["status"] == "pendente"
    }
    return public_queue, items


def _compatible(category: str, decision: str | None) -> bool:
    if decision is None or decision in ("resolvido", "adiado"):
        return True
    return decision in EXECUTABLE_REVIEW_DECISIONS_BY_CATEGORY.get(
        category, frozenset()
    )


def _decision_history_lookup(
    db: Session, batch_id: str
) -> set[tuple[str, str, str | None, str]]:
    """Carrega uma vez o histórico relevante para validar replay exato."""
    decisions = db.execute(
        select(MigrationDecision).where(
            MigrationDecision.tipo == MULTI_SHEET_REVIEW_DECISION
        )
    ).scalars().all()
    lookup: set[tuple[str, str, str | None, str]] = set()
    for decision in decisions:
        details = decision.detalhes or {}
        token = details.get("private_reference_token")
        fingerprint = details.get("review_batch_fingerprint")
        if details.get("batch_id") == batch_id and token and fingerprint:
            lookup.add((
                token,
                decision.decisao,
                details.get("previous_decision"),
                fingerprint,
            ))
    return lookup


def _is_exact_replay(
    document: dict,
    fingerprint: str,
    live: dict,
    history: set[tuple[str, str, str | None, str]],
) -> bool:
    transitioned = False
    for entry in document["items"]:
        current = live[entry["token"]]["current_decision"]
        desired = entry["decision"]
        expected = entry["expected_current_decision"]
        if current != desired:
            return False
        if expected != desired:
            transitioned = True
            if (entry["token"], desired, expected, fingerprint) not in history:
                return False
    return transitioned


def _pending_count(live: dict, desired_by_token: dict[str, str | None]) -> int:
    return sum(
        desired_by_token[token]
        not in EXECUTABLE_REVIEW_DECISIONS_BY_CATEGORY.get(
            item["category"], frozenset()
        )
        for token, item in live.items()
    )


def prepare_review_batch(
    db: Session,
    loaded: LoadedReviewBatch,
    user_id: str | None,
    *,
    lock_batch: bool = False,
) -> dict:
    """Reconstitui a fila e produz preview; lock é opt-in pós-confirmação."""
    if user_id is None:
        raise MultiSheetError("revisor_autenticado_obrigatorio")
    document = loaded.document
    batch = (
        _locked_batch(db, document["batch_id"])
        if lock_batch
        else _get_multi_batch(db, document["batch_id"])
    )
    report = batch.params["multi_sheet_report"]
    if batch.sha256 != document["snapshot_sha256"]:
        raise MultiSheetError("snapshot_sha256_nao_confere")
    if report["mapping_version"] != document["mapping_version"]:
        raise MultiSheetError("mapping_version_nao_confere")

    queue, live = _live_actionable_state(db, batch)
    entries = {item["token"]: item for item in document["items"]}
    all_queue_tokens = {
        item["private_reference_token"] for item in report["review_queue"]
    }
    unknown = set(entries) - all_queue_tokens
    if unknown:
        raise MultiSheetError("arquivo_revisao_token_desconhecido")
    extra = set(entries) - set(live)
    if extra:
        raise MultiSheetError("arquivo_revisao_token_extra")
    missing = set(live) - set(entries)
    if missing:
        raise MultiSheetError("arquivo_revisao_token_ausente")

    for token, entry in entries.items():
        item = live[token]
        if entry["category"] != item["category"]:
            raise MultiSheetError("arquivo_revisao_categoria_modificada")
        if not _compatible(item["category"], entry["decision"]):
            raise MultiSheetError("decisao_incompativel_com_categoria")

    replay = False
    if queue["queue_fingerprint"] != document["queue_fingerprint"]:
        history = _decision_history_lookup(db, document["batch_id"])
        replay = _is_exact_replay(
            document, loaded.fingerprint, live, history
        )
        if not replay:
            raise MultiSheetError("fingerprint_fila_divergente")

    if not replay:
        for token, entry in entries.items():
            if live[token]["current_decision"] != entry["expected_current_decision"]:
                raise MultiSheetError("estado_atual_modificado")

    counts = {
        "preserved": 0,
        "new": 0,
        "replacements": 0,
        "excluded": 0,
        "delayed": 0,
    }
    desired_by_token: dict[str, str | None] = {}
    for token, entry in entries.items():
        current = live[token]["current_decision"]
        desired = entry["decision"]
        desired_by_token[token] = desired
        if current == desired:
            counts["preserved"] += 1
        elif current is None:
            counts["new"] += 1
        else:
            counts["replacements"] += 1
        if desired == "excluido":
            counts["excluded"] += 1
        if desired == "adiado":
            counts["delayed"] += 1

    before_desired = {
        token: item["current_decision"] for token, item in live.items()
    }
    pending_before = _pending_count(live, before_desired)
    pending_after = _pending_count(live, desired_by_token)
    changed = counts["new"] + counts["replacements"]
    return {
        "ok": True,
        "mode": "preview",
        "batch_id": batch.id,
        "review_batch_fingerprint": loaded.fingerprint,
        "fingerprint_short": loaded.fingerprint[:FINGERPRINT_SHORT_LENGTH],
        "counts": {
            **counts,
            "pending": pending_after,
            "changed": changed,
            "actionable_total": len(live),
        },
        "preflight_effect": {
            "pending_reviews_before": pending_before,
            "pending_reviews_after": pending_after,
            "review_gate_would_pass": pending_after == 0,
        },
        "replay": replay,
        "would_write": changed > 0 and not replay,
        "execution_allowed": False,
        "operational_migration_executed": False,
    }


def _append_batch_decision(
    db: Session,
    *,
    batch: ImportBatch,
    entry: dict,
    previous: str | None,
    fingerprint: str,
    queue_fingerprint: str,
    user_id: str,
) -> MigrationDecision:
    decision = MigrationDecision(
        tipo=MULTI_SHEET_REVIEW_DECISION,
        decisao=entry["decision"],
        decidido_por=user_id,
        detalhes={
            "batch_id": batch.id,
            "private_reference_token": entry["token"],
            "mapping_version": batch.params["multi_sheet_report"][
                "mapping_version"
            ],
            "category": entry["category"],
            "rule": "confirmed_atomic_multi_sheet_review_batch",
            "review_batch_schema_version": REVIEW_BATCH_SCHEMA_VERSION,
            "review_batch_fingerprint": fingerprint,
            "queue_fingerprint_before": queue_fingerprint,
            "previous_decision": previous,
        },
    )
    db.add(decision)
    db.flush()
    audit(
        db,
        "migracao.multiaba_revisao_lote_item",
        "migration_decisions",
        decision.id,
        user_id,
        detalhes={
            "batch_id": batch.id,
            "decisao": entry["decision"],
            "sha256": fingerprint,
        },
    )
    return decision


def apply_review_batch(
    db: Session,
    loaded: LoadedReviewBatch,
    user_id: str | None,
    *,
    batch_already_locked: bool = False,
) -> dict:
    """Revalida sob lock e acrescenta decisões+auditoria; nunca faz commit."""
    preview = prepare_review_batch(
        db, loaded, user_id, lock_batch=not batch_already_locked
    )
    if preview["replay"] or not preview["would_write"]:
        return {
            "ok": True,
            "status": "no_op",
            "batch_id": loaded.document["batch_id"],
            "review_batch_fingerprint": loaded.fingerprint,
            "counts": preview["counts"],
            "decision_ids": [],
            "audit_events_created": 0,
            "operational_migration_executed": False,
        }

    document = loaded.document
    batch = _locked_batch(db, document["batch_id"])
    current = _decisions_by_token(db, batch.id)
    created: list[str] = []
    for entry in sorted(document["items"], key=lambda item: item["token"]):
        previous = (current.get(entry["token"]) or {}).get("decision")
        if previous == entry["decision"]:
            continue
        decision = _append_batch_decision(
            db,
            batch=batch,
            entry=entry,
            previous=previous,
            fingerprint=loaded.fingerprint,
            queue_fingerprint=document["queue_fingerprint"],
            user_id=user_id,
        )
        created.append(decision.id)

    audit(
        db,
        "migracao.multiaba_revisao_lote",
        "import_batches",
        batch.id,
        user_id,
        detalhes={
            "batch_id": batch.id,
            "sha256": loaded.fingerprint,
            "total": len(created),
            "status": "gravado",
        },
    )
    db.flush()
    return {
        "ok": True,
        "status": "applied",
        "batch_id": batch.id,
        "review_batch_fingerprint": loaded.fingerprint,
        "counts": preview["counts"],
        "decision_ids": created,
        "audit_events_created": len(created) + 1,
        "operational_migration_executed": False,
    }
