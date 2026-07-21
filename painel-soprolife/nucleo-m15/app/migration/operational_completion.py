"""M15.7: suplementos privados e reconstrução financeira somente-preview.

Nenhuma função deste módulo grava banco ou executa migração operacional.
Artefatos contêm dados privados e, por isso, só podem existir no diretório
aprovado com modo 0600, sem symlink/hardlink e sem sobrescrita.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import stat
from datetime import date, datetime, timezone

from .manifest import import_private_dir, safe_name

ADMIN_SUPPLEMENT_SCHEMA = "m15.admin-supplement.1"
FINANCIAL_RECONSTRUCTION_SCHEMA = "m15.financial-reconstruction.1"
REQUIRED_LEGACY_EXCLUSIONS = ("FIN-0001", "FIN-0002", "FIN-0003")
ADMIN_TOTAL_CENTS = 304_479
FIXED_ENTRY_CENTS = 22_000
REMAINING_ENTRY_CENTS = (23_680,) * 10 + (23_679,)
EXPECTED_EXAMINATIONS = 13
MAX_PRIVATE_BYTES = 1024 * 1024


class OperationalCompletionError(ValueError):
    pass


def _canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _fingerprint(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def deterministic_admin_technical_id(record: dict) -> str:
    """ID técnico derivado dos fatos administrativos, nunca ID de fonte."""
    facts = {
        key: record.get(key)
        for key in (
            "name",
            "procedure",
            "location",
            "examination_date",
            "received_amount_cents",
            "payment_method",
            "phone",
        )
    }
    return f"admin-m15-{_fingerprint(facts)[:24]}"


def build_admin_supplement(*, version: str, records: list[dict]) -> dict:
    if not version or len(version) > 40 or not records:
        raise OperationalCompletionError("admin_supplement_invalid")
    normalized = []
    seen: set[str] = set()
    for raw in records:
        if set(raw) != {
            "name",
            "procedure",
            "location",
            "examination_date",
            "received_amount_cents",
            "payment_method",
            "phone",
        }:
            raise OperationalCompletionError("admin_record_schema_invalid")
        try:
            date.fromisoformat(raw["examination_date"])
        except (TypeError, ValueError) as exc:
            raise OperationalCompletionError("admin_record_date_invalid") from exc
        if any(
            not isinstance(raw[field], str) or not raw[field].strip()
            for field in ("name", "procedure", "location", "payment_method")
        ):
            raise OperationalCompletionError("admin_record_text_invalid")
        if (
            not isinstance(raw["received_amount_cents"], int)
            or isinstance(raw["received_amount_cents"], bool)
            or raw["received_amount_cents"] <= 0
        ):
            raise OperationalCompletionError("admin_record_amount_invalid")
        if raw["phone"] is not None:
            raise OperationalCompletionError("admin_record_unknown_phone_must_be_null")
        technical_id = deterministic_admin_technical_id(raw)
        if technical_id in seen:
            raise OperationalCompletionError("admin_record_duplicate")
        seen.add(technical_id)
        normalized.append({
            **raw,
            "technical_id": technical_id,
            "source": "administrator_provided",
        })
    document = {
        "schema_version": ADMIN_SUPPLEMENT_SCHEMA,
        "version": version,
        "administrator_provided": True,
        "records": sorted(normalized, key=lambda item: item["technical_id"]),
    }
    return {**document, "document_fingerprint": _fingerprint(document)}


def preview_admin_supplement(document: dict) -> dict:
    validated = validate_admin_supplement(document)
    return {
        "ok": True,
        "mode": "preview",
        "schema_version": validated["schema_version"],
        "version": validated["version"],
        "record_count": len(validated["records"]),
        "administrator_provided": True,
        "operational_writes": 0,
        "execution_allowed": False,
    }


def validate_admin_supplement(document: dict) -> dict:
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "version",
        "administrator_provided",
        "records",
        "document_fingerprint",
    }:
        raise OperationalCompletionError("admin_supplement_schema_invalid")
    if document["schema_version"] != ADMIN_SUPPLEMENT_SCHEMA:
        raise OperationalCompletionError("admin_supplement_version_invalid")
    if document["administrator_provided"] is not True:
        raise OperationalCompletionError("admin_supplement_origin_invalid")
    unsigned = {key: value for key, value in document.items()
                if key != "document_fingerprint"}
    if _fingerprint(unsigned) != document["document_fingerprint"]:
        raise OperationalCompletionError("admin_supplement_fingerprint_mismatch")
    if not isinstance(document["records"], list) or not document["records"]:
        raise OperationalCompletionError("admin_record_schema_invalid")
    expected_fields = {
        "name", "procedure", "location", "examination_date",
        "received_amount_cents", "payment_method", "phone",
        "technical_id", "source",
    }
    facts_list = []
    for item in document["records"]:
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise OperationalCompletionError("admin_record_schema_invalid")
        facts = {key: value for key, value in item.items()
                 if key not in ("technical_id", "source")}
        facts_list.append(facts)
        if (
            item["source"] != "administrator_provided"
            or item["technical_id"] != deterministic_admin_technical_id(facts)
            or item["phone"] is not None
        ):
            raise OperationalCompletionError("admin_record_identity_invalid")
    ids = [item.get("technical_id") for item in document["records"]]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise OperationalCompletionError("admin_record_duplicate")
    try:
        rebuilt = build_admin_supplement(
            version=document["version"], records=facts_list
        )
    except OperationalCompletionError as exc:
        raise OperationalCompletionError("admin_record_schema_invalid") from exc
    if rebuilt != document:
        raise OperationalCompletionError("admin_supplement_canonical_mismatch")
    return document


def build_financial_reconstruction(
    *,
    version: str,
    fixed_exam_mappings: list[dict],
    remaining_exam_mappings: list[dict],
    pastore_exam_ids: list[str] | None = None,
) -> dict:
    """Deriva valores exclusivamente de IDs técnicos e datas explícitas."""
    if not version or len(version) > 40:
        raise OperationalCompletionError("financial_version_invalid")
    pastore = sorted(set(pastore_exam_ids or []))
    if len(pastore) != len(pastore_exam_ids or []):
        raise OperationalCompletionError("pastore_mapping_duplicate")
    fixed_ids = [item.get("examination_id") for item in fixed_exam_mappings]
    remaining_ids = [item.get("examination_id") for item in remaining_exam_mappings]
    all_ids = fixed_ids + remaining_ids
    if any(not isinstance(item, str) or not item for item in all_ids):
        raise OperationalCompletionError("technical_exam_mapping_required")
    if len(all_ids) != len(set(all_ids)):
        raise OperationalCompletionError("financial_mapping_duplicate")
    if set(all_ids) & set(pastore):
        raise OperationalCompletionError("pastore_mixed_into_reconstruction")
    fixed_valid = (
        len(fixed_exam_mappings) == 2
        and all(
            set(item) == {"examination_id", "amount_cents"}
            and item["amount_cents"] == FIXED_ENTRY_CENTS
            for item in fixed_exam_mappings
        )
    )
    dated = []
    for item in remaining_exam_mappings:
        if set(item) != {"examination_id", "examination_date"}:
            raise OperationalCompletionError("remaining_mapping_schema_invalid")
        try:
            parsed = date.fromisoformat(item["examination_date"])
        except (TypeError, ValueError) as exc:
            raise OperationalCompletionError("remaining_mapping_date_invalid") from exc
        dated.append((parsed, item["examination_id"]))
    exact_count = len(all_ids) == EXPECTED_EXAMINATIONS
    ready = fixed_valid and len(dated) == 11 and exact_count
    assignments = []
    if ready:
        assignments.extend({
            "examination_id": item["examination_id"],
            "amount_cents": FIXED_ENTRY_CENTS,
            "rule": "fixed_administrative_entry",
        } for item in sorted(fixed_exam_mappings,
                             key=lambda value: value["examination_id"]))
        for (exam_date, exam_id), cents in zip(
            sorted(dated, key=lambda value: (value[0], value[1])),
            REMAINING_ENTRY_CENTS,
            strict=True,
        ):
            assignments.append({
                "examination_id": exam_id,
                "examination_date": exam_date.isoformat(),
                "amount_cents": cents,
                "rule": "ordered_remaining_entry",
            })
    assigned_total = sum(item["amount_cents"] for item in assignments)
    if ready and assigned_total != ADMIN_TOTAL_CENTS:
        raise OperationalCompletionError("financial_total_mismatch")
    body = {
        "schema_version": FINANCIAL_RECONSTRUCTION_SCHEMA,
        "version": version,
        "legacy_exclusion_decisions": [
            {"legacy_id": item, "decision": "excluded"}
            for item in REQUIRED_LEGACY_EXCLUSIONS
        ],
        "assignments": assignments,
        "pastore_adjustment_queue": [
            {"examination_id": item, "status": "evidence_required"}
            for item in pastore
        ],
        "state": "ready_preview" if ready else "blocked_missing_mappings",
        "missing_mapping_slots": max(0, EXPECTED_EXAMINATIONS - len(all_ids)),
        "assigned_total_cents": assigned_total,
        "expected_total_cents": ADMIN_TOTAL_CENTS,
        "operational_writes": 0,
    }
    return {**body, "document_fingerprint": _fingerprint(body)}


def preview_financial_reconstruction(document: dict) -> dict:
    document = validate_financial_reconstruction(document)
    return {
        "ok": document["state"] == "ready_preview",
        "mode": "preview",
        "state": document["state"],
        "assignment_count": len(document["assignments"]),
        "legacy_exclusion_count": len(document["legacy_exclusion_decisions"]),
        "pastore_adjustment_count": len(document["pastore_adjustment_queue"]),
        "missing_mapping_slots": document["missing_mapping_slots"],
        "assigned_total_cents": document["assigned_total_cents"],
        "expected_total_cents": document["expected_total_cents"],
        "execution_allowed": False,
        "operational_writes": 0,
    }


def validate_financial_reconstruction(document: dict) -> dict:
    expected = {
        "schema_version", "version", "legacy_exclusion_decisions",
        "assignments", "pastore_adjustment_queue", "state",
        "missing_mapping_slots", "assigned_total_cents",
        "expected_total_cents", "operational_writes", "document_fingerprint",
    }
    if not isinstance(document, dict) or set(document) != expected:
        raise OperationalCompletionError("financial_document_schema_invalid")
    unsigned = {key: value for key, value in document.items()
                if key != "document_fingerprint"}
    if _fingerprint(unsigned) != document["document_fingerprint"]:
        raise OperationalCompletionError("financial_document_fingerprint_mismatch")
    if (
        document["schema_version"] != FINANCIAL_RECONSTRUCTION_SCHEMA
        or document["operational_writes"] != 0
        or document["expected_total_cents"] != ADMIN_TOTAL_CENTS
        or document["state"] not in (
            "ready_preview", "blocked_missing_mappings"
        )
    ):
        raise OperationalCompletionError("financial_document_contract_invalid")
    exclusions = document["legacy_exclusion_decisions"]
    if exclusions != [
        {"legacy_id": item, "decision": "excluded"}
        for item in REQUIRED_LEGACY_EXCLUSIONS
    ]:
        raise OperationalCompletionError("financial_exclusions_invalid")
    assignments = document["assignments"]
    if not isinstance(assignments, list):
        raise OperationalCompletionError("financial_assignments_invalid")
    ids = [item.get("examination_id") for item in assignments
           if isinstance(item, dict)]
    if len(ids) != len(assignments):
        raise OperationalCompletionError("financial_assignments_invalid")
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise OperationalCompletionError("financial_mapping_duplicate")
    pastore = document["pastore_adjustment_queue"]
    if not isinstance(pastore, list) or any(
        not isinstance(item, dict)
        or set(item) != {"examination_id", "status"}
        or not isinstance(item["examination_id"], str)
        or not item["examination_id"]
        or item["status"] != "evidence_required"
        for item in pastore
    ):
        raise OperationalCompletionError("pastore_queue_invalid")
    pastore_ids = [item["examination_id"] for item in pastore]
    if (
        len(pastore_ids) != len(set(pastore_ids))
        or pastore_ids != sorted(pastore_ids)
        or set(ids) & set(pastore_ids)
    ):
        raise OperationalCompletionError("pastore_queue_invalid")
    if any(
        not isinstance(item.get("amount_cents"), int)
        or isinstance(item.get("amount_cents"), bool)
        for item in assignments
    ):
        raise OperationalCompletionError("financial_amount_invalid")
    total = sum(item["amount_cents"] for item in assignments)
    if total != document["assigned_total_cents"]:
        raise OperationalCompletionError("financial_total_mismatch")
    if document["state"] == "ready_preview":
        if (
            len(ids) != EXPECTED_EXAMINATIONS
            or total != ADMIN_TOTAL_CENTS
            or document["missing_mapping_slots"] != 0
        ):
            raise OperationalCompletionError("financial_ready_state_invalid")
        fixed = assignments[:2]
        remaining = assignments[2:]
        if any(
            set(item) != {"examination_id", "amount_cents", "rule"}
            or item["amount_cents"] != FIXED_ENTRY_CENTS
            or item["rule"] != "fixed_administrative_entry"
            for item in fixed
        ):
            raise OperationalCompletionError("financial_fixed_rules_invalid")
        dated = []
        for item in remaining:
            if (
                set(item) != {
                    "examination_id", "examination_date", "amount_cents", "rule"
                }
                or item["rule"] != "ordered_remaining_entry"
            ):
                raise OperationalCompletionError("financial_ordered_rules_invalid")
            try:
                parsed = date.fromisoformat(item["examination_date"])
            except (TypeError, ValueError) as exc:
                raise OperationalCompletionError(
                    "financial_ordered_rules_invalid"
                ) from exc
            dated.append((parsed, item["examination_id"], item["amount_cents"]))
        if (
            len(remaining) != 11
            or dated != sorted(dated, key=lambda value: (value[0], value[1]))
            or [item[2] for item in dated] != list(REMAINING_ENTRY_CENTS)
        ):
            raise OperationalCompletionError("financial_ordered_rules_invalid")
    elif (
        assignments
        or total != 0
        or not isinstance(document["missing_mapping_slots"], int)
        or isinstance(document["missing_mapping_slots"], bool)
        or not 0 <= document["missing_mapping_slots"] <= EXPECTED_EXAMINATIONS
    ):
        raise OperationalCompletionError("financial_blocked_state_invalid")
    return document


def write_immutable_private_json(filename: str, document: dict) -> pathlib.Path:
    """Cria uma vez com 0600; nunca substitui artefato existente."""
    if safe_name(filename) is None:
        raise OperationalCompletionError("private_filename_invalid")
    base = import_private_dir()
    base.mkdir(parents=True, exist_ok=True)
    base = base.resolve(strict=True)
    target = base / filename
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError as exc:
        raise OperationalCompletionError("immutable_private_file_exists") from exc
    try:
        payload = _canonical(document) + b"\n"
        if len(payload) > MAX_PRIVATE_BYTES:
            raise OperationalCompletionError("private_file_too_large")
        written = 0
        while written < len(payload):
            written += os.write(fd, payload[written:])
        os.fsync(fd)
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
        ):
            raise OperationalCompletionError("private_file_security_failed")
    except Exception:
        os.close(fd)
        target.unlink(missing_ok=True)
        raise
    os.close(fd)
    return target


def load_immutable_private_json(filename: str) -> dict:
    """Lê artefato privado regular 0600, do dono e com um único link."""
    if safe_name(filename) is None:
        raise OperationalCompletionError("private_filename_invalid")
    base = import_private_dir().resolve(strict=True)
    target = base / filename
    before = target.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or before.st_size > MAX_PRIVATE_BYTES
    ):
        raise OperationalCompletionError("private_file_security_failed")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(target, flags)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise OperationalCompletionError("private_file_changed_during_read")
        chunks = []
        remaining = MAX_PRIVATE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    if len(raw) > MAX_PRIVATE_BYTES:
        raise OperationalCompletionError("private_file_too_large")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationalCompletionError("private_file_json_invalid") from exc
    if not isinstance(document, dict):
        raise OperationalCompletionError("private_file_json_invalid")
    return document


def utc_version(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}"
