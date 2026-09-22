#!/usr/bin/env python3
"""M61 — READ-ONLY scan for facts that could be the first automated production issuance.

Opens one database read-only, runs the SAME eligibility rules the automation
would run (``nfse.evaluate``), and prints a sanitized list. It writes nothing,
creates no document, calls no provider and opens no socket.

PRIVACY

Never prints a patient name, CPF, address, or any clinical content. Each
candidate is identified by a short hash of the exam's public code — enough for
a human to match a row against the panel, not enough to be an identifier on
its own. Pass ``--show-public-code`` if the operator has decided the public
code is safe to display in their own terminal.

EXCLUSIONS

Facts already covered by an externally issued NFS-e (the M61 import records)
are excluded by construction: they carry a production document in state
``issued``, so they are neither eligible nor listed.

Usage:
    M15_DATABASE_URL=postgresql+psycopg://... \\
        python scripts/nfse_m61_production_candidates.py [--environment production]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select                                      # noqa: E402

from app.models import FiscalDocument, Person, SpirometryExam     # noqa: E402
from app.services import nfse                                     # noqa: E402
from app.services.nfse_external_issuance import (                 # noqa: E402
    IMPORT_OPERATION, PRODUCTION_ENVIRONMENT)
from app.services.nfse_national.recipient_identity import (       # noqa: E402
    RecipientIdentityError, assert_production_recipient)
from app.services.nfse_national.service_location import (         # noqa: E402
    ServiceLocationUndetermined, spirometry_service_municipio_ibge)

PERFORMED = {"Realizado", "Laudo Liberado"}


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", default=PRODUCTION_ENVIRONMENT)
    parser.add_argument("--show-public-code", action="store_true",
                        help="print the exam's public code instead of a short hash")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    if not os.environ.get("M15_DATABASE_URL"):
        print("ERRO: M15_DATABASE_URL não definido. O banco tem de ser nomeado "
              "explicitamente.", file=sys.stderr)
        return 2

    from app.db import get_sessionmaker

    eligible, blocked, already_invoiced = [], [], []

    with get_sessionmaker()() as db:
        exams = db.scalars(
            select(SpirometryExam).where(SpirometryExam.status.in_(PERFORMED))
            .order_by(SpirometryExam.data_exame.desc()).limit(args.limit)).all()

        for exam in exams:
            identifier = (exam.public_code if args.show_public_code
                          else short_hash(exam.public_code or exam.id))
            existing = db.scalars(select(FiscalDocument).where(
                FiscalDocument.spirometry_exam_id == exam.id,
                FiscalDocument.environment == args.environment).limit(1)).first()

            if existing is not None:
                imported = db.scalars(select(nfse.FiscalAttempt).where(
                    nfse.FiscalAttempt.document_id == existing.id,
                    nfse.FiscalAttempt.operation == IMPORT_OPERATION).limit(1)).first()
                already_invoiced.append({
                    "exam": identifier,
                    "state": existing.state,
                    "origin": "importada (NFS-e externa)" if imported else "deste sistema",
                    "service_date": str(exam.data_exame),
                })
                continue

            try:
                evaluation = nfse.evaluate(db, exam.id, args.environment)
            except Exception as exc:                     # noqa: BLE001 - report, never crash
                blocked.append({"exam": identifier, "blockers": [f"evaluate_failed:{type(exc).__name__}"]})
                continue

            blockers = list(evaluation.get("blocking_reasons") or [])

            # The M60 production identity contract, reported here so a human
            # sees it BEFORE preparing anything rather than at dispatch.
            #
            # Looked up by id: SpirometryExam has no ``person`` relationship,
            # and getattr(exam, "person", None) silently returned None for
            # every row, making every fact look like it had no recipient.
            person = db.get(Person, exam.person_id) if exam.person_id else None
            try:
                assert_production_recipient(
                    nome=getattr(person, "nome_completo", None),
                    cpf=getattr(person, "cpf", None))
            except RecipientIdentityError as exc:
                blockers.append(exc.code)

            # M62 — the service location (M31), checked here because
            # ``nfse.evaluate`` does NOT check it: it is resolved later, at
            # dispatch, and fails closed there. Without this the scanner
            # reported as "eligible" facts that cannot actually be issued —
            # which is exactly the wrong direction for a readiness report.
            # On the real operational data, 0 of 48 exams carry it.
            try:
                spirometry_service_municipio_ibge(exam.municipio_atendimento_ibge)
            except ServiceLocationUndetermined:
                blockers.append("service_location_undetermined")

            row = {
                "exam": identifier,
                "modalidade": exam.modalidade,
                "service_date": str(exam.data_exame),
                "municipio_ibge": exam.municipio_atendimento_ibge,
                "broncodilatador": bool(exam.broncodilatador),
                "amount": (str(evaluation.get("amount_snapshot"))
                           if evaluation.get("amount_snapshot") is not None else None),
                "blockers": sorted(blockers),
            }
            (eligible if not blockers else blocked).append(row)

    report = {
        "environment": args.environment,
        "exams_scanned": len(exams),
        "eligible_count": len(eligible),
        "eligible": eligible,
        "already_invoiced_count": len(already_invoiced),
        "already_invoiced": already_invoiced,
        "blocked_count": len(blocked),
        "blocked": blocked[:50],
        "read_only": True,
        "wrote_nothing": True,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if len(eligible) == 1:
        print("\nExatamente UM candidato elegível. Pode ser designado "
              "FIRST_PRODUCTION_CANDIDATE.", file=sys.stderr)
    elif len(eligible) > 1:
        print(f"\n{len(eligible)} candidatos elegíveis — a escolha é humana, "
              "não automática.", file=sys.stderr)
    else:
        print("\nNenhum candidato elegível.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
