#!/usr/bin/env python3
"""M61 — link the two manually issued production NFS-e to their facts.

Two real NFS-e were issued by hand in production on 2026-09-15. The exams that
generated them look, to the automation, like ordinary uninvoiced facts — so
without this, the first automated run could invoice one of them a SECOND time.
A duplicate NFS-e is a real fiscal document that has to be cancelled, so the
duplicate has to be made impossible rather than fixed afterwards.

WHAT IT DOES

Finds, for each note, the single fact matching the value, the service date and
the bronchodilator flag, and records an ``import`` attempt against it (see
``app.services.nfse_external_issuance``). It does not issue, does not build a
DPS, does not call a provider, and opens no socket.

AMBIGUITY IS A STOP, NOT A GUESS

If a note matches zero facts, or more than one, the script refuses that note
and reports it. Attaching a real invoice to the wrong service is as damaging as
issuing a duplicate, and neither is worth a guess. Both notes must resolve
unambiguously or nothing is written for the one that did not.

DRY RUN BY DEFAULT

With no flags it only reports what it WOULD link. ``--apply`` writes, and needs
an explicitly named database and an actor.

Usage:
    M15_DATABASE_URL=postgresql+psycopg://... \\
        python scripts/nfse_m61_import_manual_nfse.py [--apply --actor <user-id>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select                                      # noqa: E402

from app.models import FinancialEntry, FiscalDocument, SpirometryExam   # noqa: E402
from app.services.nfse_external_issuance import (                 # noqa: E402
    ExternalIssuance, PRODUCTION_ENVIRONMENT, already_imported,
    normalize_access_key, register_external_issuance)

PERFORMED = {"Realizado", "Laudo Liberado"}

# The facts as the operator reported them. Access keys are fiscal identifiers,
# not personal data, and are exactly what anti-duplication needs.
MANUAL_NOTES = [
    {
        "label": "A",
        "access_key": "33045572263544026000110000000000000626096136136469",
        "amount": Decimal("279.00"),
        "service_date": date(2026, 9, 15),
        "broncodilatador": True,
    },
    {
        "label": "B",
        "access_key": "33045572263544026000110000000000000726091190747856",
        "amount": Decimal("219.00"),
        "service_date": date(2026, 9, 15),
        "broncodilatador": True,
    },
    # M64 — three more notes issued by hand in production, reported by the
    # operator WITH the exam each one belongs to. The public code identifies
    # the fact; value and service date must still agree with the database, or
    # the note is refused rather than attached.
    {
        "label": "C",
        "public_code": "ESP-000039",
        "access_key": "33045572263544026000110000000000000426099130462655",
        "amount": Decimal("230.00"),
        "service_date": date(2026, 8, 28),
        "broncodilatador": None,     # not reported; not checked
    },
    {
        "label": "D",
        "public_code": "ESP-000046",
        "access_key": "33045572263544026000110000000000000526092817158327",
        "amount": Decimal("290.00"),
        "service_date": date(2026, 9, 9),
        "broncodilatador": None,
    },
    {
        "label": "E",
        "public_code": "ESP-000053",
        "access_key": "33045572263544026000110000000000000826099736433520",
        "amount": Decimal("279.00"),
        "service_date": date(2026, 9, 19),
        "broncodilatador": None,
    },
]


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def find_candidates(db, note) -> list[SpirometryExam]:
    """Facts matching this note on every stated attribute.

    Deliberately conjunctive and deliberately narrow: value, service date and
    bronchodilator together. Anything looser would start matching facts the
    operator did not mean.

    M64 — when the operator names the exam (``public_code``), that exam is the
    only candidate, and it must STILL agree on service date and value. The
    code says which fact the operator means; the database has to confirm it.
    ``broncodilatador`` is checked only when it was reported.
    """
    criteria = [SpirometryExam.status.in_(PERFORMED),
                SpirometryExam.data_exame == note["service_date"]]
    if note.get("public_code"):
        criteria.append(SpirometryExam.public_code == note["public_code"])
    if note.get("broncodilatador") is not None:
        criteria.append(SpirometryExam.broncodilatador.is_(bool(note["broncodilatador"])))
    exams = db.scalars(select(SpirometryExam).where(*criteria)).all()

    matched = []
    for exam in exams:
        entries = db.scalars(select(FinancialEntry).where(
            FinancialEntry.spirometry_exam_id == exam.id,
            FinancialEntry.tipo == "receita",
            FinancialEntry.status == "Recebido",
        )).all()
        if len(entries) != 1:
            continue
        if Decimal(str(entries[0].valor)) != note["amount"]:
            continue
        matched.append(exam)
    return matched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor", default=None)
    args = parser.parse_args()

    if not os.environ.get("M15_DATABASE_URL"):
        print("ERRO: M15_DATABASE_URL não definido. O banco tem de ser nomeado "
              "explicitamente.", file=sys.stderr)
        return 2
    if args.apply and not args.actor:
        print("ERRO: --actor é obrigatório com --apply.", file=sys.stderr)
        return 2

    from app.db import get_sessionmaker

    results = []
    ambiguous = False

    with get_sessionmaker()() as db:
        for note in MANUAL_NOTES:
            key = normalize_access_key(note["access_key"])
            entry = {
                "label": note["label"],
                "access_key": key,
                "amount": str(note["amount"]),
                "service_date": str(note["service_date"]),
                "broncodilatador": note["broncodilatador"],
                "public_code": note.get("public_code"),
            }

            existing = already_imported(db, key)
            if existing is not None:
                entry.update(status="already_imported", document_id=existing.document_id)
                results.append(entry)
                continue

            matches = find_candidates(db, note)
            entry["matches"] = len(matches)
            if len(matches) != 1:
                entry["status"] = "ambiguous" if matches else "not_found"
                entry["candidates"] = [short_hash(e.public_code or e.id) for e in matches]
                ambiguous = True
                results.append(entry)
                continue

            exam = matches[0]
            entry["exam"] = short_hash(exam.public_code or exam.id)
            blocking = db.scalars(select(FiscalDocument).where(
                FiscalDocument.spirometry_exam_id == exam.id,
                FiscalDocument.environment == PRODUCTION_ENVIRONMENT).limit(1)).first()
            if blocking is not None:
                entry.update(status="exam_already_has_production_document",
                             state=blocking.state)
                ambiguous = True
                results.append(entry)
                continue

            if not args.apply:
                entry["status"] = "would_link"
                results.append(entry)
                continue

            document = register_external_issuance(
                db, ExternalIssuance(spirometry_exam_id=exam.id, access_key=key,
                                     issued_on=str(note["service_date"])),
                args.actor)
            entry.update(status="linked", document_id=document.id, state=document.state)
            results.append(entry)

    print(json.dumps({
        "applied": bool(args.apply),
        "notes": results,
        "ambiguous_or_incomplete": ambiguous,
        "issued_anything": False,
        "called_provider": False,
    }, indent=2, ensure_ascii=False))

    if not args.apply:
        print("\nDRY RUN — nada foi escrito. Use --apply --actor <user-id>.",
              file=sys.stderr)
    if ambiguous:
        print("\nATENÇÃO: pelo menos uma nota não resolveu de forma inequívoca. "
              "Nada foi escrito para essa nota — a ligação tem de ser decidida "
              "por um humano.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
